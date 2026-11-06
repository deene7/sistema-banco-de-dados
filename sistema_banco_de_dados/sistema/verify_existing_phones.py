"""Confirma pares CPF/telefone existentes, com plano privado, cópia anterior e auditoria."""
import bootstrap
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import uuid

from database import Database
from import_data import cell, file_digest, guess, normalize
from bulk import bump_clients


def open_plan(path):
    plan=sqlite3.connect(path)
    plan.row_factory=sqlite3.Row
    return plan


def init_plan(path, metadata):
    plan=open_plan(path)
    plan.executescript('''
        CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1),dados TEXT NOT NULL);
        CREATE TABLE pares (cpf TEXT NOT NULL,numero TEXT NOT NULL,aba TEXT NOT NULL,linha INTEGER NOT NULL,
          classificacao TEXT,cliente_id INTEGER,telefone_id INTEGER,versao INTEGER,situacao TEXT,
          PRIMARY KEY(cpf,numero));
        CREATE INDEX idx_verificacao_cliente ON pares(classificacao,cliente_id);
        CREATE TABLE copia_telefones (telefone_id INTEGER PRIMARY KEY,cliente_id INTEGER,numero TEXT,situacao TEXT,registro TEXT);
        CREATE TABLE copia_clientes (cliente_id INTEGER PRIMARY KEY,versao INTEGER,atualizado_em TEXT);
    ''')
    plan.execute('INSERT INTO metadata VALUES (1,?)',(json.dumps(metadata,ensure_ascii=False),))
    plan.commit()
    return plan


def metadata_of(plan):
    return json.loads(plan.execute('SELECT dados FROM metadata WHERE id=1').fetchone()[0])


def analyse(config, source, folder):
    from openpyxl import load_workbook
    source=Path(source)
    folder=Path(folder)
    folder.mkdir(exist_ok=False)
    from security import restrict_file
    restrict_file(folder)
    saved=folder/source.name
    shutil.copyfile(source,saved)
    metadata={'operacao':uuid.uuid4().hex,'arquivo':source.name,'sha256':file_digest(saved),'criado_em':datetime.now().isoformat()}
    plan=init_plan(folder/'plano.sqlite3',metadata)
    counts=Counter()
    book=load_workbook(saved,read_only=True,data_only=False,keep_links=False)
    try:
        for sheet in book:
            iterator=sheet.iter_rows(values_only=True)
            headers=[cell(value) for value in next(iterator,())]
            mapping=guess(headers)
            if 'cpf' not in mapping or not any(field.startswith('telefone') for field in mapping):
                raise ValueError('Uma aba não contém as colunas de CPF e telefone esperadas.')
            pending=[]
            for line,row in enumerate(iterator,2):
                if not any(value is not None and str(value).strip() for value in row):
                    continue
                counts['linhas_lidas']+=1
                data,errors,recovered=normalize([cell(value) for value in row],mapping)
                if errors:
                    counts['linhas_cpf_invalido']+=1;continue
                if not data['phones']:
                    counts['linhas_sem_telefone_valido']+=1;continue
                counts['cpfs_com_zeros_recuperados']+=int(recovered)
                pending.extend((data['cpf'],number,sheet.title,line) for number in data['phones'])
                if len(pending)>=1000:
                    counts['pares_lidos']+=len(pending)
                    plan.executemany('INSERT OR IGNORE INTO pares(cpf,numero,aba,linha) VALUES (?,?,?,?)',pending)
                    plan.commit();pending=[]
            if pending:
                counts['pares_lidos']+=len(pending)
                plan.executemany('INSERT OR IGNORE INTO pares(cpf,numero,aba,linha) VALUES (?,?,?,?)',pending)
                plan.commit()
    finally:
        book.close()
    metadata['leitura']=dict(counts)
    metadata['pares_unicos']=plan.execute('SELECT COUNT(*) FROM pares').fetchone()[0]
    metadata['pares_repetidos']=counts['pares_lidos']-metadata['pares_unicos']
    plan.execute('UPDATE metadata SET dados=?',(json.dumps(metadata,ensure_ascii=False),));plan.commit()
    connection=Database(config)
    try:
        last=''
        while True:
            cpfs=[row[0] for row in plan.execute('SELECT DISTINCT cpf FROM pares WHERE cpf>? ORDER BY cpf LIMIT 500',(last,))]
            if not cpfs:break
            last=cpfs[-1];marks=','.join(['%s']*len(cpfs))
            rows=connection.all('SELECT c.id AS cliente_id,c.cpf,c.app_versao,c.excluido_em AS cliente_excluido,'
                't.id AS telefone_id,t.numero,t.situacao,t.excluido_em AS telefone_excluido '
                f'FROM clientes c LEFT JOIN telefones t ON t.cliente_id=c.id WHERE c.cpf IN ({marks})',cpfs)
            clients={row['cpf']:row for row in rows}
            phones={(row['cpf'],row['numero']):row for row in rows if row['telefone_id'] is not None}
            updates=[]
            for pair in plan.execute('SELECT cpf,numero FROM pares WHERE cpf IN ('+','.join(['?']*len(cpfs))+')',cpfs):
                client=clients.get(pair['cpf']);phone=phones.get((pair['cpf'],pair['numero']))
                state=('cliente_ausente' if not client else 'cliente_excluido' if client['cliente_excluido'] else
                       'telefone_ausente' if not phone else 'telefone_excluido' if phone['telefone_excluido'] else
                       'ja_verificado' if phone['situacao']=='confirmado' else 'a_verificar')
                updates.append((state,client['cliente_id'] if client else None,phone['telefone_id'] if phone else None,
                    client['app_versao'] if client else None,phone['situacao'] if phone else None,pair['cpf'],pair['numero']))
            plan.executemany('UPDATE pares SET classificacao=?,cliente_id=?,telefone_id=?,versao=?,situacao=? WHERE cpf=? AND numero=?',updates)
            plan.commit()
    finally:
        connection.close();plan.close()
    return folder/'plano.sqlite3'


def summary(path):
    plan=open_plan(path)
    try:
        return {**metadata_of(plan),'resultados':dict(plan.execute('SELECT classificacao,COUNT(*) FROM pares GROUP BY classificacao').fetchall())}
    finally:plan.close()


def apply(config,path,user):
    path=Path(path)
    plan=open_plan(path)
    try:
        metadata=metadata_of(plan)
        if file_digest(path.parent/metadata['arquivo'])!=metadata['sha256']:
            raise ValueError('A cópia da planilha mudou após a análise.')
        if 'registro' not in {row[1] for row in plan.execute('PRAGMA table_info(copia_telefones)')}:
            plan.execute('ALTER TABLE copia_telefones ADD COLUMN registro TEXT');plan.commit()
        connection=Database(config)
    except Exception:
        plan.close()
        raise
    reason=metadata.get('motivo_auditoria') or 'Planilha verificada por solicitação de '+user+'; operação '+metadata['operacao']
    try:
        actors=connection.all("SELECT id,nome,login FROM app_usuarios WHERE ativo=1 AND perfil IN ('ADM','TI') AND (LOWER(nome)=LOWER(%s) OR LOWER(login)=LOWER(%s))",(user,user))
        if len(actors)!=1:
            raise ValueError('Não foi possível identificar um único usuário autorizado para a auditoria.')
        actor=actors[0]
        if metadata.get('usuario_id',actor['id'])!=actor['id']:
            raise ValueError('O responsável pela retomada difere do responsável original.')
        metadata.update(usuario_id=actor['id'],usuario=actor['nome'],motivo_auditoria=reason)
        plan.execute('UPDATE metadata SET dados=?',(json.dumps(metadata,ensure_ascii=False),));plan.commit()
        connection.rollback()
        while True:
            ids=[row[0] for row in plan.execute("SELECT DISTINCT cliente_id FROM pares WHERE classificacao='a_verificar' ORDER BY cliente_id LIMIT 500")]
            if not ids:break
            allowed=connection.one("SELECT id FROM app_usuarios WHERE id=%s AND ativo=1 AND perfil IN ('ADM','TI')",(actor['id'],))
            if not allowed:raise ValueError('O responsável não tem mais permissão para executar a atualização.')
            marks=','.join(['%s']*len(ids))
            clients={row['id']:row for row in connection.all(f'SELECT id,cpf,app_versao,atualizado_em,excluido_em FROM clientes WHERE id IN ({marks}) ORDER BY id FOR UPDATE',ids)}
            phones={row['id']:row for row in connection.all(f'SELECT * FROM telefones WHERE cliente_id IN ({marks}) ORDER BY id FOR UPDATE',ids)}
            pairs=plan.execute("SELECT * FROM pares WHERE classificacao='a_verificar' AND cliente_id IN ("+','.join(['?']*len(ids))+')',ids).fetchall()
            changed=[];client_ids=set();audits=[];outcomes=[]
            for pair in pairs:
                client=clients.get(pair['cliente_id']);phone=phones.get(pair['telefone_id'])
                state='alterado_desde_analise'
                if client and phone and client['cpf']==pair['cpf'] and phone['cliente_id']==client['id'] and phone['numero']==pair['numero']:
                    if client['excluido_em']:state='cliente_excluido'
                    elif phone['excluido_em']:state='telefone_excluido'
                    elif phone['situacao']=='confirmado':state='ja_verificado'
                    elif client['app_versao']==pair['versao'] and phone['situacao']==pair['situacao']:
                        state='atualizado'
                        plan.execute('INSERT OR IGNORE INTO copia_telefones VALUES (?,?,?,?,?)',(phone['id'],client['id'],phone['numero'],phone['situacao'],json.dumps(phone,ensure_ascii=False,default=str)))
                        plan.execute('INSERT OR IGNORE INTO copia_clientes VALUES (?,?,?)',(client['id'],client['app_versao'],str(client['atualizado_em'])))
                        changed.append(phone['id']);client_ids.add(client['id'])
                        before=json.dumps({'situacao':phone['situacao']},ensure_ascii=False)
                        after=json.dumps({'situacao':'confirmado','numero':phone['numero'],'origem':'planilha','arquivo':metadata['arquivo'],
                            'sha256':metadata['sha256'],'operacao':metadata['operacao'],'aba':pair['aba'],'linha':pair['linha'],
                            'fontes':[{'nome':'Planilha fornecida pelo usuário'}]},ensure_ascii=False)
                        audits.append((actor['id'],client['id'],'verificar_origem','telefone',phone['id'],before,after,reason))
                outcomes.append((state,pair['cpf'],pair['numero']))
            # Cópia anterior durável antes de confirmar qualquer alteração no banco real.
            plan.commit()
            if changed:
                marks=','.join(['%s']*len(changed))
                cursor=connection.execute(f"UPDATE telefones SET situacao='confirmado' WHERE id IN ({marks}) AND excluido_em IS NULL",changed)
                if cursor.rowcount!=len(changed):raise ValueError('Uma situação mudou durante a gravação.')
                cursor.close()
                connection.many('INSERT INTO app_auditoria(usuario_id,cliente_id,acao,entidade,entidade_id,antes,depois,motivo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',audits)
                bump_clients(connection,sorted(client_ids))
            connection.commit()
            plan.executemany('UPDATE pares SET classificacao=? WHERE cpf=? AND numero=?',outcomes);plan.commit()
            print('Bloco concluído:',len(changed),'telefones verificados.',flush=True)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close();plan.close()
    return summary(path)


def validate(config,path):
    plan=open_plan(path)
    connection=Database(config)
    try:
        metadata=metadata_of(plan)
        plan.execute('CREATE INDEX IF NOT EXISTS idx_verificacao_telefone ON pares(telefone_id)');plan.commit()
        counts=dict(plan.execute('SELECT classificacao,COUNT(*) FROM pares GROUP BY classificacao').fetchall())
        result={'pares_conferidos':0,'divergencias':0,'pendentes':counts.get('a_verificar',0)}
        last=0
        while True:
            pairs=plan.execute("SELECT cpf,numero,cliente_id,telefone_id FROM pares INDEXED BY idx_verificacao_telefone WHERE classificacao IN ('atualizado','ja_verificado') AND telefone_id>? ORDER BY telefone_id LIMIT 500",(last,)).fetchall()
            if not pairs:break
            last=pairs[-1]['telefone_id']
            ids=[row['telefone_id'] for row in pairs];marks=','.join(['%s']*len(ids))
            actual={row['id']:row for row in connection.all(f'SELECT t.id,t.numero,t.situacao,t.cliente_id,t.excluido_em,c.cpf,c.excluido_em AS cliente_excluido FROM telefones t JOIN clientes c ON c.id=t.cliente_id WHERE t.id IN ({marks})',ids)}
            for pair in pairs:
                phone=actual.get(pair['telefone_id'])
                result['pares_conferidos']+=1
                if not phone or phone['numero']!=pair['numero'] or phone['cpf']!=pair['cpf'] or phone['cliente_id']!=pair['cliente_id'] or phone['situacao']!='confirmado' or phone['excluido_em'] or phone['cliente_excluido']:
                    result['divergencias']+=1
        result['telefones_atualizados']=counts.get('atualizado',0)
        result['ja_verificados']=counts.get('ja_verificado',0)
        result['copias_telefones']=plan.execute('SELECT COUNT(*) FROM copia_telefones').fetchone()[0]
        result['clientes_atualizados']=plan.execute('SELECT COUNT(*) FROM copia_clientes').fetchone()[0]
        first=plan.execute("SELECT cliente_id FROM pares WHERE classificacao='atualizado' ORDER BY cliente_id LIMIT 1").fetchone()
        result['auditorias']=0;result['telefones_auditados']=0
        if first:
            floor=connection.one('SELECT MIN(id) AS n FROM app_auditoria WHERE cliente_id=%s AND motivo=%s',(first[0],metadata['motivo_auditoria']))['n']
            if floor is not None:
                audits=connection.one("SELECT COUNT(*) AS total,COUNT(DISTINCT entidade_id) AS telefones FROM app_auditoria WHERE id>=%s AND motivo=%s AND acao='verificar_origem' AND entidade='telefone'",(floor,metadata['motivo_auditoria']))
                result['auditorias']=audits['total'];result['telefones_auditados']=audits['telefones']
        result['consistente']=(result['pendentes']==0 and result['divergencias']==0 and
            result['telefones_atualizados']==result['copias_telefones']==result['auditorias']==result['telefones_auditados'])
        (Path(path).parent/'conferencia_final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:
        connection.close();plan.close()


def main():
    from security import BASE,read_config
    parser=argparse.ArgumentParser()
    parser.add_argument('--arquivo',type=Path)
    parser.add_argument('--aplicar',type=Path)
    parser.add_argument('--conferir',type=Path)
    parser.add_argument('--usuario')
    args=parser.parse_args()
    if args.conferir:
        result=validate(read_config(),args.conferir)
    elif args.aplicar:
        if not args.usuario:parser.error('Informe o usuário responsável.')
        result=apply(read_config(),args.aplicar,args.usuario)
    else:
        if not args.arquivo:parser.error('Informe a planilha de origem.')
        folder=BASE/'privado'/('verificacao_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
        path=analyse(read_config(),args.arquivo,folder)
        print('PLANO:',path,flush=True)
        result=summary(path)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()
