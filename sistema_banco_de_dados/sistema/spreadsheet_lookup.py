"""Cruzamento de planilhas somente para leitura, com fila e saída Excel em fluxo."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time

from database import Database
from import_data import cell, file_digest, rows, valid_cpf

GROUPS = {
    'verificados':'Com telefone verificado',
    'sem_verificados':'Sem telefone verificado',
    'nao_encontrados':'Não encontrados',
    'fora_filtros':'Fora dos filtros',
    'invalidos':'CPFs inválidos',
    'lixeira':'Clientes na lixeira',
}
CPF_GROUPS={'cadastrados':'Já cadastrados',**{key:value for key,value in GROUPS.items() if key not in ('verificados','sem_verificados')}}

def lookup_groups(filters):
    return CPF_GROUPS if filters.get('tipo')=='cpf' else GROUPS

RETENTION_SECONDS=3*24*60*60


class LookupExpired(ValueError):
    pass


def retention_time(job):
    """Compatibilidade com consultas anteriores à política de retenção."""
    try:
        summary=json.loads(job['resumo'] or '{}')
        date=summary.get('concluido_em') or job['criado_em']
        return datetime.strptime(date,'%d/%m/%Y %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=-3))).timestamp()
    except (ValueError,TypeError,AttributeError):
        return time.time()


def now():
    return datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M:%S')


def normalized_cpf(value):
    raw=cell(value)
    if not re.fullmatch(r'[0-9.\s/-]+',raw):return None
    digits=re.sub('[^0-9]','',raw)
    cpf=digits.zfill(11) if 1<=len(digits)<=11 else ''
    return cpf if valid_cpf(cpf) else None


class LookupStore:
    def __init__(self,root,protect=False):
        self.root=Path(root).resolve();self.path=self.root/'consultas.sqlite3'
        self.protect=protect;self.lock=threading.Lock();self.initialized=False

    def ensure(self):
        with self.lock:
            if self.initialized:return
            self.root.mkdir(parents=True,exist_ok=True)
            if self.protect:
                from security import restrict_file
                restrict_file(self.root)
            with sqlite3.connect(self.path,timeout=10) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('''CREATE TABLE IF NOT EXISTS consultas (
                    token TEXT PRIMARY KEY,usuario_id INTEGER NOT NULL,arquivo TEXT NOT NULL,
                    extensao TEXT NOT NULL,sha256 TEXT NOT NULL,estado TEXT NOT NULL,
                    filtros TEXT NOT NULL,mapeamento TEXT,progresso INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,resumo TEXT,erro TEXT,criado_em TEXT NOT NULL,
                    fase TEXT NOT NULL DEFAULT 'Aguardando configuração')''')
                columns={row[1] for row in db.execute('PRAGMA table_info(consultas)')}
                if 'retencao_desde' not in columns:
                    db.execute('ALTER TABLE consultas ADD COLUMN retencao_desde REAL NOT NULL DEFAULT 0')
                    db.row_factory=sqlite3.Row
                    for job in db.execute('SELECT token,resumo,criado_em FROM consultas WHERE retencao_desde=0').fetchall():
                        db.execute('UPDATE consultas SET retencao_desde=? WHERE token=?',(retention_time(job),job['token']))
                if 'expirado_em' not in columns:db.execute('ALTER TABLE consultas ADD COLUMN expirado_em TEXT')
                db.execute('CREATE INDEX IF NOT EXISTS idx_consultas_usuario ON consultas(usuario_id,criado_em DESC)')
                db.execute('CREATE INDEX IF NOT EXISTS idx_consultas_retencao ON consultas(expirado_em,retencao_desde)')
            db.close()
            self.initialized=True

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback();raise
        finally:db.close()

    def create(self,token,user,filename,suffix,digest,filters):
        self.ensure()
        with self.connection() as db:
            db.execute("INSERT INTO consultas(token,usuario_id,arquivo,extensao,sha256,estado,filtros,criado_em,retencao_desde) VALUES (?,?,?,?,?,'mapeamento',?,?,?)",
                (token,user,filename,suffix,digest,json.dumps(filters,ensure_ascii=False),now(),time.time()))

    def get(self,token):
        if not self.path.exists():return None
        self.ensure()
        with self.connection() as db:
            row=db.execute('SELECT * FROM consultas WHERE token=?',(token,)).fetchone()
            return dict(row) if row else None

    def recent(self,user=None,limit=20):
        if not self.path.exists():return []
        self.ensure()
        with self.connection() as db:
            sql='SELECT * FROM consultas'+(' WHERE usuario_id=?' if user is not None else '')+' ORDER BY rowid DESC LIMIT ?'
            return [dict(row) for row in db.execute(sql,(user,limit) if user is not None else (limit,))]

    def many(self,tokens):
        if not tokens or not self.path.exists():return []
        self.ensure()
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM consultas WHERE token IN ('+','.join(['?']*len(tokens))+')',tokens)]

    def update(self,token,**values):
        allowed={'estado','mapeamento','progresso','total','resumo','erro','fase','retencao_desde','expirado_em'}
        if not values or set(values)-allowed:raise ValueError('Atualização de consulta inválida.')
        if values.get('estado') in ('concluida','erro'):values.setdefault('retencao_desde',time.time())
        with self.connection() as db:
            db.execute('UPDATE consultas SET '+','.join(name+'=?' for name in values)+' WHERE token=?',list(values.values())+[token])

    def prepare(self,token,mapping,states):
        with self.connection() as db:
            cursor=db.execute("UPDATE consultas SET estado='fila',fase='Na fila',mapeamento=?,erro=NULL,progresso=0,total=0,resumo=NULL,retencao_desde=? WHERE token=? AND expirado_em IS NULL AND estado IN ("+','.join(['?']*len(states))+')',[json.dumps(mapping,ensure_ascii=False),time.time(),token]+list(states))
            if cursor.rowcount!=1:raise ValueError('Esta consulta já foi concluída. Envie o arquivo novamente para outra consulta.')


def lookup_batch(connection,cpfs,filters):
    if not cpfs:return {}
    cpf_only=filters.get('tipo')=='cpf'
    marks=','.join(['%s']*len(cpfs))
    clients=connection.all(f'SELECT id,cpf,nome,excluido_em FROM clientes WHERE cpf IN ({marks})',cpfs)
    ids=[client['id'] for client in clients if not client['excluido_em']]
    phones={};categories={};addresses=set()
    if ids:
        id_marks=','.join(['%s']*len(ids))
        for phone in ([] if cpf_only else connection.all(f'SELECT cliente_id,numero,situacao FROM telefones WHERE cliente_id IN ({id_marks}) AND excluido_em IS NULL ORDER BY cliente_id,id',ids)):
            phones.setdefault(phone['cliente_id'],[]).append(phone)
        for category in connection.all(f'SELECT l.cliente_id,c.id,c.nome FROM app_cliente_categorias l JOIN app_categorias c ON c.id=l.categoria_id WHERE l.cliente_id IN ({id_marks}) AND c.ativo=1 ORDER BY c.nome',ids):
            categories.setdefault(category['cliente_id'],[]).append(category)
        if filters.get('uf'):
            addresses={row['cliente_id'] for row in connection.all(f'SELECT DISTINCT cliente_id FROM enderecos WHERE cliente_id IN ({id_marks}) AND excluido_em IS NULL AND uf=%s',ids+[filters['uf']])}
    result={cpf:{'grupo':'nao_encontrados','motivo':'CPF não encontrado no banco.','id':'','nome':'','categorias':'','telefones':0,'verificados':0,'numeros':''} for cpf in cpfs}
    for client in clients:
        info=result[client['cpf']];info.update(id=client['id'],nome=client['nome'] or '')
        if client['excluido_em']:
            info.update(grupo='lixeira',motivo='Cadastro existente na lixeira.');continue
        current=phones.get(client['id'],[])
        verified=[phone['numero'] for phone in current if phone['situacao']=='confirmado']
        links=categories.get(client['id'],[])
        failures=[]
        if filters.get('categorias') and not set(filters['categorias']).intersection(category['id'] for category in links):failures.append('Não pertence às categorias selecionadas.')
        if filters.get('uf') and client['id'] not in addresses:failures.append('Não possui endereço ativo na UF selecionada.')
        if not cpf_only and filters.get('verificacao')=='com' and not verified:failures.append('Não possui telefone verificado ativo.')
        if not cpf_only and filters.get('verificacao')=='sem' and verified:failures.append('Possui telefone verificado ativo.')
        info.update(grupo='fora_filtros' if failures else 'cadastrados' if cpf_only else 'verificados' if verified else 'sem_verificados',
                    motivo=' '.join(failures) or ('CPF já cadastrado no banco.' if cpf_only else 'Possui telefone verificado ativo.' if verified else 'Nenhum telefone verificado ativo.'),
                    categorias=' · '.join(category['nome'] for category in links),telefones=len(current),verificados=len(verified),
                    numeros=' | '.join(verified)[:32000] if not failures else '')
    return result


class SpreadsheetLookupManager:
    def __init__(self,config):
        self.config=config
        base=Path(config['sqlite_path']).parent if config.get('sqlite_path') else Path(__file__).with_name('privado')
        self.store=LookupStore(config.get('lookup_dir',base/'consultas_planilhas'),protect=not bool(config.get('sqlite_path')))
        self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='consulta-planilha')
        self.lock=threading.Lock();self.running=set()
        self.file_users={};self.retention_stop=threading.Event();self.retention_thread=None

    @staticmethod
    def expired_at(job,active=False,at=None):
        return bool(job.get('expirado_em')) or (not active and float(job.get('retencao_desde') or time.time())<= (time.time() if at is None else at)-RETENTION_SECONDS)

    def expired(self,job):
        with self.lock:return self.expired_at(job,job['token'] in self.running)

    @contextmanager
    def file_access(self,token):
        with self.lock:
            job=self.store.get(token)
            if not job or self.expired_at(job,token in self.running):
                raise LookupExpired('Os arquivos desta consulta expiraram após 3 dias. Envie a planilha novamente.')
            self.file_users[token]=self.file_users.get(token,0)+1
        try:yield
        finally:
            with self.lock:
                self.file_users[token]-=1
                if not self.file_users[token]:del self.file_users[token]

    def cleanup(self,at=None,limit=100):
        result={'consultas':0,'arquivos':0,'bytes':0}
        if not self.store.path.exists():return result
        self.store.ensure();instant=time.time() if at is None else at
        with self.store.connection() as db:
            jobs=[dict(row) for row in db.execute('SELECT * FROM consultas WHERE expirado_em IS NULL AND retencao_desde<=? ORDER BY retencao_desde LIMIT ?',(instant-RETENTION_SECONDS,limit))]
        allowed={'entrada.csv','entrada.xlsx','resultado.xlsx','resultado.parcial.xlsx','trabalho.sqlite3','trabalho.sqlite3-wal','trabalho.sqlite3-shm','trabalho.sqlite3-journal'}
        for candidate in jobs:
            token=candidate['token']
            if not re.fullmatch('[a-f0-9]{32}',token):continue
            with self.lock:
                if token in self.running or self.file_users.get(token):continue
                job=self.store.get(token)
                if not job or job.get('expirado_em') or not self.expired_at(job,at=instant):continue
                if job['estado'] in ('fila','processando'):continue
                folder=self.store.root/token
                try:
                    # Somente arquivos conhecidos, diretamente na pasta deste token; nunca segue links.
                    if folder.is_symlink() or folder.is_junction():continue
                    resolved=folder.resolve()
                    if resolved.parent!=self.store.root or resolved.name!=token:continue
                    files=list(folder.iterdir()) if folder.exists() else []
                    if any(item.name not in allowed or not item.is_file() or item.is_symlink() or item.resolve().parent!=resolved for item in files):continue
                    for item in files:
                        size=item.stat().st_size;item.unlink()
                        result['arquivos']+=1;result['bytes']+=size
                    if folder.exists():folder.rmdir()
                    self.store.update(token,expirado_em=now(),fase='Arquivos removidos após 3 dias',mapeamento=None)
                    result['consultas']+=1
                except OSError:
                    logging.getLogger(__name__).warning('Limpeza de consulta adiada: arquivos em uso ou indisponíveis.')
        return result

    def start_retention(self):
        if self.retention_thread is not None:return
        if self.store.path.exists():
            self.store.ensure()
            # Chamado apenas depois do bloqueio exclusivo do servidor: não há fila antiga ativa.
            with self.store.connection() as db:
                db.execute("UPDATE consultas SET estado='interrompida',fase='Interrompida após reinício' WHERE estado IN ('fila','processando') AND expirado_em IS NULL")
        def maintain():
            while not self.retention_stop.is_set():
                try:self.cleanup()
                except Exception:logging.getLogger(__name__).exception('Não foi possível concluir a limpeza das consultas.')
                self.retention_stop.wait(3600)
        self.retention_thread=threading.Thread(target=maintain,name='limpeza-consultas',daemon=True)
        self.retention_thread.start()

    def stop_retention(self):
        self.retention_stop.set()
        if self.retention_thread is not None:self.retention_thread.join(timeout=5)

    def active(self,token):
        with self.lock:return token in self.running

    def queue(self,token,mapping,states=('mapeamento',),sync=False):
        with self.lock:
            if token in self.running:raise ValueError('Esta consulta já está em andamento.')
            job=self.store.get(token)
            if not job or self.expired_at(job):raise LookupExpired('Os arquivos desta consulta expiraram após 3 dias. Envie a planilha novamente.')
            if len(self.running)>=10:raise ValueError('A fila de consultas está cheia. Aguarde uma consulta terminar.')
            self.store.prepare(token,mapping,states)
            self.running.add(token)
            try:
                if not sync:self.worker.submit(self.run,token)
            except Exception:
                self.running.remove(token);raise
        if sync:self.run(token)
        return True

    def source_path(self,job):
        return self.store.root/job['token']/('entrada'+job['extensao'])

    def result_path(self,token):
        return self.store.root/token/'resultado.xlsx'

    def run(self,token):
        try:self.process(token)
        except Exception as error:
            message=str(error)[:300] if isinstance(error,ValueError) else 'Não foi possível concluir a consulta. Tente retomar o processamento.'
            self.store.update(token,estado='erro',erro=message)
        finally:
            with self.lock:self.running.discard(token)

    def process(self,token):
        job=self.store.get(token)
        if not job or job['estado']!='fila':return
        self.store.update(token,estado='processando',fase='Lendo arquivo',progresso=0,erro=None)
        source=self.source_path(job);mapping=json.loads(job['mapeamento']);filters=json.loads(job['filtros'])
        if file_digest(source)!=job['sha256']:raise ValueError('O arquivo enviado foi alterado. Envie a planilha novamente.')
        database=Database(self.config)
        spool=sqlite3.connect(self.store.root/token/'trabalho.sqlite3')
        try:
            user=database.one("SELECT id FROM app_usuarios WHERE id=%s AND ativo=1 AND perfil IN ('ADM','TI')",(job['usuario_id'],))
            if not user:raise ValueError('O responsável não tem mais permissão para consultar a base.')
            database.rollback()
            spool.executescript('DROP TABLE IF EXISTS linhas; DROP TABLE IF EXISTS resultados; CREATE TABLE linhas(n INTEGER PRIMARY KEY,dados TEXT,cpf TEXT); CREATE TABLE resultados(cpf TEXT PRIMARY KEY,dados TEXT);')
            iterator=rows(source,mapping.get('aba'))
            try:
                header=next(iterator,[])
                if header!=mapping['headers']:raise ValueError('As colunas do arquivo mudaram. Envie uma nova consulta.')
                total=0;buffer=[]
                for line,values in enumerate(iterator,2):
                    if not any(values):continue
                    total+=1
                    if total>300000:raise ValueError('Use até 300.000 linhas por consulta.')
                    if len(values)>len(header) and any(values[len(header):]):raise ValueError('Há linhas com mais colunas que o cabeçalho.')
                    if any(len(value)>10000 for value in values):raise ValueError('Há células muito extensas. Use até 10.000 caracteres por célula.')
                    values=(values+['']*len(header))[:len(header)]
                    cpf=normalized_cpf(values[mapping['cpf_col']])
                    buffer.append((line,json.dumps(values,ensure_ascii=False),cpf))
                    if len(buffer)>=1000:
                        spool.executemany('INSERT INTO linhas VALUES (?,?,?)',buffer);spool.commit();buffer=[]
                        self.store.update(token,progresso=total)
                if buffer:spool.executemany('INSERT INTO linhas VALUES (?,?,?)',buffer)
                spool.commit()
            finally:iterator.close()
            spool.execute('CREATE INDEX idx_linhas_cpf ON linhas(cpf)');spool.commit()
            # Só abre o snapshot do banco após ler o arquivo, reduzindo a duração da transação.
            user=database.one("SELECT id FROM app_usuarios WHERE id=%s AND ativo=1 AND perfil IN ('ADM','TI')",(job['usuario_id'],))
            if not user:raise ValueError('O responsável não tem mais permissão para consultar a base.')
            database.rollback()
            database.execute('BEGIN' if database.sqlite else 'START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY').close()
            if filters['categorias']:
                marks=','.join(['%s']*len(filters['categorias']))
                valid=database.all(f'SELECT id FROM app_categorias WHERE id IN ({marks}) AND ativo=1',filters['categorias'])
                if len(valid)!=len(filters['categorias']):raise ValueError('Uma categoria selecionada não está mais disponível. Envie uma nova consulta.')
            self.store.update(token,total=total,progresso=0,fase='Cruzando CPFs com a base')
            last='';looked_up=0
            while True:
                cpfs=[row[0] for row in spool.execute('SELECT DISTINCT cpf FROM linhas WHERE cpf>? ORDER BY cpf LIMIT 500',(last,))]
                if not cpfs:break
                last=cpfs[-1]
                result=lookup_batch(database,cpfs,filters)
                spool.executemany('INSERT INTO resultados VALUES (?,?)',[(cpf,json.dumps(info,ensure_ascii=False)) for cpf,info in result.items()]);spool.commit()
                looked_up+=len(cpfs);self.store.update(token,progresso=looked_up)
            database.rollback()
            self.store.update(token,fase='Gerando planilha',progresso=0)
            summary=self.write_result(token,spool,header,filters,total)
            self.store.update(token,estado='concluida',fase='Concluída',resumo=json.dumps(summary,ensure_ascii=False),progresso=total,erro=None)
        finally:
            database.close();spool.close()
            try:(self.store.root/token/'trabalho.sqlite3').unlink(missing_ok=True)
            except PermissionError:pass

    def write_result(self,token,spool,headers,filters,total):
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font,PatternFill
        from openpyxl.styles import Alignment
        from openpyxl.utils import get_column_letter
        book=Workbook(write_only=True)
        summary_sheet=book.create_sheet('Resumo')
        summary_sheet.column_dimensions['A'].width=40;summary_sheet.column_dimensions['B'].width=100
        cpf_only=filters.get('tipo')=='cpf'
        groups=lookup_groups(filters)
        sheets={key:book.create_sheet(name) for key,name in groups.items()}
        columns=headers+['Consulta | CPF','Consulta | Resultado','Consulta | Motivo','Banco | ID','Banco | Nome','Banco | Categorias','Banco | Telefones cadastrados','Banco | Telefones verificados','Banco | Números verificados']
        if cpf_only:columns=columns[:-3]
        def append(sheet,values,heading=False):
            cells=[]
            for value in values:
                if isinstance(value,str):
                    value=re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f]','',value)[:32767]
                    item=WriteOnlyCell(sheet,value=value);item.data_type='s'
                else:item=WriteOnlyCell(sheet,value=value)
                if heading:
                    item.font=Font(bold=True,color='FFFFFF');item.fill=PatternFill('solid',fgColor='2879DF');item.alignment=Alignment(wrap_text=True,vertical='center')
                cells.append(item)
            sheet.append(cells)
        for sheet in sheets.values():
            sheet.freeze_panes='A2'
            sheet.row_dimensions[1].height=34
            widths=[min(32,max(16,len(str(header))+2)) for header in headers]+[18,30,52,14,35,30,20,20,42]
            if cpf_only:widths=widths[:-3]
            for index,width in enumerate(widths,1):sheet.column_dimensions[get_column_letter(index)].width=width
            append(sheet,columns,True)
        counts={key:0 for key in groups};done=0
        query='SELECT l.dados,l.cpf,r.dados FROM linhas l LEFT JOIN resultados r ON r.cpf=l.cpf ORDER BY l.n'
        for original,cpf,matched in spool.execute(query):
            info=json.loads(matched) if matched else {'grupo':'invalidos','motivo':'CPF vazio, inválido ou não reconhecido.','id':'','nome':'','categorias':'','telefones':0,'verificados':0,'numeros':''}
            group=info['grupo'];counts[group]+=1
            values=[cpf or '',groups[group],info['motivo'],info['id'],info['nome'],info['categorias']]
            if not cpf_only:values += [info['telefones'],info['verificados'],info['numeros']]
            append(sheets[group],json.loads(original)+values)
            done+=1
            if done%1000==0:self.store.update(token,progresso=done)
        append(summary_sheet,['Consulta de planilha na base'],True)
        job=self.store.get(token);mapping=json.loads(job['mapeamento'])
        append(summary_sheet,['Arquivo enviado',job['arquivo']])
        append(summary_sheet,['Aba consultada',mapping.get('aba') or 'CSV'])
        append(summary_sheet,['Coluna do CPF',headers[mapping['cpf_col']]])
        append(summary_sheet,['Gerado em',now()]);append(summary_sheet,['Linhas consultadas',total])
        append(summary_sheet,['Categorias',' · '.join(filters.get('nomes_categorias',[])) or 'Todas'])
        append(summary_sheet,['UF',filters.get('uf') or 'Todas'])
        append(summary_sheet,['Tipo de consulta','CPF na base' if cpf_only else 'Telefones por CPF'])
        if not cpf_only:append(summary_sheet,['Verificação',{'todos':'Todas','com':'Com telefone verificado','sem':'Sem nenhum telefone verificado'}.get(filters.get('verificacao'),'Todas')])
        append(summary_sheet,['Situação','Linhas'],True)
        for key,name in groups.items():append(summary_sheet,[name,counts[key]])
        if sum(counts.values())!=total:raise ValueError('Os totais da consulta não conferem. Tente novamente.')
        for key,sheet in sheets.items():sheet.auto_filter.ref=f'A1:{get_column_letter(len(columns))}{counts[key]+1}'
        append(summary_sheet,['Critério','CPF ativo existente no banco, independentemente de telefones.' if cpf_only else 'Cliente com ao menos um telefone verificado ativo pertence ao grupo com telefone verificado.'])
        append(summary_sheet,['Fora dos filtros','CPF existente que não atende às categorias, UF ou verificação selecionadas.'])
        append(summary_sheet,['Não encontrados','CPF válido que não existe no banco, independentemente dos filtros.'])
        append(summary_sheet,['Preservação','Linhas repetidas são mantidas; fórmulas originais são exportadas como texto. Nenhum cadastro é alterado.'])
        partial=self.result_path(token).with_suffix('.parcial.xlsx')
        try:
            book.save(partial);partial.replace(self.result_path(token))
        finally:book.close()
        return {'total':total,'grupos':counts,'concluido_em':now()}
