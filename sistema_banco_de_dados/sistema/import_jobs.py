"""Importações persistentes, com conferência antes da aprovação e retomada por item."""
import hashlib
import json
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from database import Database
from import_data import ADDRESS_FIELDS, FIELDS, normalize, rows, same, key
from bulk import update_client_fields, bump_clients, chunks


def dump(value):
    return json.dumps(value,ensure_ascii=False,default=str)


def category_ids(job):
    spec=json.loads(job['mapeamento']) if job.get('mapeamento') else {}
    values=spec.get('categorias')
    if values is None:values=[job['categoria_id']] if job.get('categoria_id') is not None else []
    return sorted(set(int(value) for value in values))


def check_categories(connection,job,lock=False):
    ids=category_ids(job)
    if ids:
        found=connection.all('SELECT id,ativo FROM app_categorias WHERE id IN ('+','.join(['%s']*len(ids))+') ORDER BY id'+(' FOR UPDATE' if lock else ''),ids)
        if len(found)!=len(ids) or any(not row['ativo'] for row in found):
            raise ValueError('Uma categoria deste lote foi excluída. Restaure-a antes de continuar.')
    return ids


def trusted_phone_sources(connection,job):
    ids=category_ids(job)
    if not ids:return []
    return [row for row in connection.all('SELECT id,nome FROM app_categorias WHERE id IN ('+','.join(['%s']*len(ids))+') ORDER BY id',ids)
            if key(row['nome']) in ('discadora','argus')]


class ImportManager:
    def __init__(self, config):
        self.config=config
        self.running={}
        self.lock=threading.Lock()
        self.work_lock=threading.Lock()
        self.fast_slots=threading.BoundedSemaphore(2)

    def dispatch(self,job_id,phase):
        connection=Database(self.config)
        try:job=connection.one('SELECT caminho,aba FROM app_importacoes WHERE id=%s',(job_id,))
        finally:connection.close()
        small=False
        try:
            if job and Path(job['caminho']).stat().st_size<=65536:
                iterator=rows(job['caminho'],job['aba'])
                try:
                    small=True
                    for count,_ in enumerate(iterator):
                        if count>200:small=False;break
                finally:iterator.close()
        except (OSError,ValueError):
            # O processamento normal registra a falha no recibo do lote.
            small=False
        if small and self.fast_slots.acquire(blocking=False):
            try:
                with self.lock:
                    if job_id in self.running:return False
                    self.running[job_id]=threading.current_thread()
                self.run(job_id,phase,serialized=False)
                return True
            finally:self.fast_slots.release()
        return self.start(job_id,phase)

    def active(self, job_id):
        with self.lock:return job_id in self.running

    def start(self, job_id, phase):
        with self.lock:
            if job_id in self.running:return False
            thread=threading.Thread(target=self.run,args=(job_id,phase),daemon=True,
                                    name=f'importacao-{job_id}')
            self.running[job_id]=thread
            thread.start()
        return True

    def run(self, job_id, phase, serialized=True):
        try:
            with self.work_lock if serialized else nullcontext():
                if phase=='preparar':self.prepare(job_id)
                else:self.apply(job_id)
        except Exception as error:
            connection=Database(self.config)
            try:
                message=str(error)[:900] if isinstance(error,ValueError) else 'Falha de processamento. Nenhuma linha parcialmente gravada foi confirmada. A TI pode retomar este lote.'
                connection.execute('UPDATE app_importacoes SET estado=%s,erro=%s WHERE id=%s',('erro',message,job_id))
                connection.commit()
            finally:connection.close()
        finally:
            with self.lock:self.running.pop(job_id,None)

    @staticmethod
    def authorized(connection, user_id):
        user=connection.one('SELECT ativo,perfil FROM app_usuarios WHERE id=%s',(user_id,))
        if not user or not user['ativo'] or user['perfil'] not in ('ADM','TI'):
            raise ValueError('O responsável não tem mais permissão para executar esta importação.')

    def prepare(self, job_id):
        started=time.perf_counter()
        connection=Database(self.config)
        try:
            job=connection.one('SELECT * FROM app_importacoes WHERE id=%s',(job_id,))
            if not job or job['estado']!='preparando' or job.get('excluido_em'):return
            self.authorized(connection,job['usuario_id'])
            categories=check_categories(connection,job)
            sources=trusted_phone_sources(connection,job)
            path=Path(job['caminho'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=job['sha256']:
                raise ValueError('O arquivo de origem foi modificado após o envio. Envie um novo lote.')
            if connection.one("SELECT COUNT(*) AS n FROM app_importacao_itens WHERE importacao_id=%s AND estado='aplicado'",(job_id,))['n']:
                raise ValueError('Este lote já confirmou registros. Use Retomar importação, sem refazer a preparação.')
            connection.execute('DELETE FROM app_importacao_itens WHERE importacao_id=%s',(job_id,))
            connection.commit()
            spec=json.loads(job['mapeamento'])
            mapping=spec['targets']
            groups={}
            stats={'linhas':0,'linhas_vazias':0,'zeros_recuperados':0,'repeticoes_no_arquivo':0,
                   'novos_clientes':0,'clientes_existentes':0,'novos_telefones':0,'novos_emails':0,
                   'novos_enderecos':0,'novos_vinculos_categoria':0,'pendencias':0,'prontos':0,
                   'avisos_itens':0,'avisos_amostra':[],'verificar_telefones':bool(sources),'fontes_verificacao':sources,
                   'telefones_a_verificar':0,'telefones_verificados':0,
                   'colunas_preservadas':[h for h,m in zip(spec['headers'],mapping) if m=='extra']}
            iterator=rows(path,job['aba'])
            try:
                header=next(iterator,[])
                if header!=spec['headers']:raise ValueError('O cabeçalho mudou após o mapeamento.')
                for line,row in enumerate(iterator,2):
                    if not any(row):stats['linhas_vazias']+=1;continue
                    stats['linhas']+=1
                    if stats['linhas']>300000:raise ValueError('O lote excede 300.000 registros. Divida o arquivo em partes menores.')
                    if any(len(v)>10000 for v in row):raise ValueError('Há uma célula com mais de 10.000 caracteres. Revise o arquivo.')
                    original=list(row)
                    extra_columns=len(row)>len(header) and any(row[len(header):])
                    row=(row+['']*len(header))[:len(header)]
                    data,errors,recovered=normalize(row,mapping)
                    if extra_columns:errors.append('Linha com mais colunas do que o cabeçalho.')
                    stats['zeros_recuperados']+=int(recovered)
                    group_key=data['cpf'] if data['cpf'] and not any('CPF' in e for e in errors) else f'linha:{line}'
                    if group_key not in groups:
                        groups[group_key]={'data':data,'errors':errors,'lines':[line],'originals':[original],'ignored_fields':set()}
                    else:
                        group=groups[group_key]
                        stats['repeticoes_no_arquivo']+=1
                        for field,value in data['fields'].items():
                            if field in group['ignored_fields']:continue
                            if not same(field,group['data']['fields'].get(field),value):
                                message=f'{FIELDS[field]} divergente entre linhas do mesmo CPF.'
                                if field=='nome':group['errors'].append(message)
                                else:
                                    group['data']['warnings'].append(message+' Campo não aproveitado.')
                                    group['data']['fields'].pop(field,None);group['ignored_fields'].add(field)
                            else:group['data']['fields'].setdefault(field,value)
                        group['data']['warnings'].extend(data.get('warnings',[]))
                        for field in ('phones','emails','addresses'):
                            for value in data[field]:
                                if value not in group['data'][field]:group['data'][field].append(value)
                        group['errors'].extend(errors)
                        group['lines'].append(line)
                        group['originals'].append(original)
                    if stats['linhas']%1000==0:
                        connection.execute('UPDATE app_importacoes SET progresso=%s WHERE id=%s',(stats['linhas'],job_id))
                        connection.commit()
            finally:iterator.close()
            # Consulta o banco em lotes; não executa uma busca completa por linha do arquivo.
            existing={}
            cpfs=[key for key in groups if not key.startswith('linha:')]
            for start in range(0,len(cpfs),500):
                subset=cpfs[start:start+500]
                marks=','.join(['%s']*len(subset))
                for client in connection.all(f'SELECT * FROM clientes WHERE cpf IN ({marks})',subset):
                    existing[client['cpf']]=client
            phones,emails,addresses,links={},{},{},set()
            phone_states={}
            ids=[client['id'] for client in existing.values()]
            need_addresses=any(group['data']['addresses'] for group in groups.values())
            need_phones=any(group['data']['phones'] for group in groups.values())
            need_emails=any(group['data']['emails'] for group in groups.values())
            for start in range(0,len(ids),500):
                subset=ids[start:start+500]
                marks=','.join(['%s']*len(subset))
                for table,column,target in [('telefones','numero',phones),('emails','email',emails)]:
                    if (table=='telefones' and not need_phones) or (table=='emails' and not need_emails):continue
                    extra=',situacao,excluido_em' if table=='telefones' and sources else ''
                    for contact in connection.all(f'SELECT cliente_id,{column}{extra} FROM {table} WHERE cliente_id IN ({marks})',subset):
                        target.setdefault(contact['cliente_id'],set()).add(contact[column])
                        if table=='telefones' and sources:phone_states[(contact['cliente_id'],contact['numero'])]=(contact['situacao'],bool(contact['excluido_em']))
                if need_addresses:
                    for address in connection.all(f'SELECT * FROM enderecos WHERE cliente_id IN ({marks})',subset):
                        addresses.setdefault(address['cliente_id'],set()).add(tuple(address[name] or None for name in ADDRESS_FIELDS))
                if categories:
                    cat_marks=','.join(['%s']*len(categories))
                    for link in connection.all(f'SELECT cliente_id,categoria_id FROM app_cliente_categorias WHERE categoria_id IN ({cat_marks}) AND cliente_id IN ({marks})',categories+subset):
                        links.add((link['cliente_id'],link['categoria_id']))
            inserts=[]
            for group_key,group in groups.items():
                data=group['data'];client=existing.get(data['cpf']);errors=group['errors']
                if client:
                    if client['excluido_em']:errors.append('Cliente na lixeira. A importação não restaura cadastros automaticamente.')
                    for field,value in list(data['fields'].items()):
                        if not same(field,client.get(field),value):
                            message=f'{FIELDS[field]} diverge do cadastro existente.'
                            if field=='nome':errors.append(message)
                            else:
                                data['warnings'].append(message+' Valor existente mantido.')
                                data['fields'].pop(field,None)
                data['warnings']=list(dict.fromkeys(data.get('warnings',[])))
                if data['warnings']:
                    stats['avisos_itens']+=1
                    if len(stats['avisos_amostra'])<30:
                        stats['avisos_amostra'].append({'cpf':data['cpf'],'linhas':group['lines'][:10],'avisos':data['warnings']})
                data['cliente_id_snapshot']=client['id'] if client else None
                errors=list(dict.fromkeys(errors))
                if errors:stats['pendencias']+=1
                else:
                    stats['prontos']+=1
                    stats['clientes_existentes' if client else 'novos_clientes']+=1
                    client_id=client['id'] if client else None
                    stats['novos_telefones']+=sum(number not in phones.get(client_id,set()) for number in data['phones'])
                    if sources:
                        for number in data['phones']:
                            current=phone_states.get((client_id,number))
                            if not current or (not current[1] and current[0]!='confirmado'):stats['telefones_a_verificar']+=1
                    stats['novos_emails']+=sum(email not in emails.get(client_id,set()) for email in data['emails'])
                    stats['novos_enderecos']+=sum(tuple(a[name] for name in ADDRESS_FIELDS) not in addresses.get(client_id,set()) for a in data['addresses'])
                    stats['novos_vinculos_categoria']+=sum((client_id,cat) not in links for cat in categories)
                inserts.append((job_id,group_key,data['cpf'],'pendente' if errors else 'pronto',dump(data),
                                dump(group['originals']),dump(group['lines']),dump(errors),client['app_versao'] if client else None))
                if len(inserts)>=500:
                    self.save_items(connection,inserts);inserts=[];connection.commit()
            if inserts:self.save_items(connection,inserts)
            stats['clientes_ja_cadastrados']=len(existing)
            stats['analise_segundos']=round(time.perf_counter()-started,4)
            connection.execute('UPDATE app_importacoes SET estado=%s,resumo=%s,progresso=%s,total_linhas=%s,erro=NULL WHERE id=%s',
                               ('revisao',dump(stats),stats['linhas'],stats['linhas'],job_id))
            connection.commit()
        finally:connection.close()

    @staticmethod
    def save_items(connection, items):
        connection.many('INSERT INTO app_importacao_itens (importacao_id,chave,cpf,estado,dados,originais,linhas,motivos,cliente_versao) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',items)

    def apply(self, job_id):
        started=time.perf_counter()
        connection=Database(self.config)
        try:
            job=connection.one('SELECT * FROM app_importacoes WHERE id=%s',(job_id,))
            if not job or job['estado']!='importando' or job.get('excluido_em'):return
            self.authorized(connection,job['aprovador_id'])
            completed=connection.one("SELECT COUNT(*) AS n FROM app_importacao_itens WHERE importacao_id=%s AND estado='aplicado'",(job_id,))['n']
            connection.commit()
            while True:
                self.authorized(connection,job['aprovador_id'])
                batch=connection.all("SELECT id,dados,cliente_versao FROM app_importacao_itens WHERE importacao_id=%s AND estado='pronto' ORDER BY id LIMIT 500 FOR UPDATE",(job_id,))
                if not batch:break
                completed+=self.apply_batch(connection,job,batch)
                connection.execute('UPDATE app_importacoes SET progresso=%s,resumo=%s WHERE id=%s',(completed,job['resumo'],job_id))
                connection.commit()
            summary=json.loads(job['resumo']) if job['resumo'] else {}
            summary['aplicacao_segundos']=round(time.perf_counter()-started,4)
            connection.execute("UPDATE app_importacoes SET estado='concluida',erro=NULL,resumo=%s WHERE id=%s",(dump(summary),job_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:connection.close()

    @staticmethod
    def update_items(connection, outcomes):
        columns=('estado','motivos','novo_cliente','novos_telefones','novos_emails','novos_enderecos','nova_categoria','cliente_id_resultado')
        groups={}
        for outcome in outcomes:
            key=tuple(outcome.get(column) for column in columns[:-1])
            groups.setdefault(key,[]).append(outcome['id'])
        if len(groups)<=12:
            for values,ids in groups.items():
                connection.execute('UPDATE app_importacao_itens SET '+','.join(column+'=%s' for column in columns[:-1])+
                                   ' WHERE id IN ('+','.join(['%s']*len(ids))+')',list(values)+ids)
            params=[]
            for outcome in outcomes:params.extend((outcome['id'],outcome['cliente_id_resultado']))
            params.extend(outcome['id'] for outcome in outcomes)
            connection.execute('UPDATE app_importacao_itens SET cliente_id_resultado=CASE id '+
                               ' '.join('WHEN %s THEN %s' for _ in outcomes)+' ELSE cliente_id_resultado END WHERE id IN ('+
                               ','.join(['%s']*len(outcomes))+')',params)
            return
        statements=[];params=[]
        for column in columns:
            statements.append(column+'=CASE id '+ ' '.join('WHEN %s THEN %s' for _ in outcomes)+' ELSE '+column+' END')
            for outcome in outcomes:params.extend((outcome['id'],outcome.get(column)))
        params.extend(outcome['id'] for outcome in outcomes)
        connection.execute('UPDATE app_importacao_itens SET '+','.join(statements)+' WHERE id IN ('+','.join(['%s']*len(outcomes))+')',params)

    @classmethod
    def apply_batch(cls,connection,job,items):
        categories=check_categories(connection,job,lock=True)
        summary=json.loads(job['resumo']) if job.get('resumo') else {}
        verify_phones=bool(summary.get('verificar_telefones'))
        payloads={item['id']:json.loads(item['dados']) for item in items}
        cpfs=sorted(data['cpf'] for data in payloads.values())
        marks=','.join(['%s']*len(cpfs))
        clients={row['cpf']:row for row in connection.all(f'SELECT * FROM clientes WHERE cpf IN ({marks}) ORDER BY cpf FOR UPDATE',cpfs)}
        plans=[];outcomes=[]
        fields=('nome','data_nascimento','nome_mae','sexo_origem')
        for item in items:
            data=payloads[item['id']];client=clients.get(data['cpf']);reason=None
            if client and (client['excluido_em'] or item['cliente_versao'] is None or client['app_versao']!=item['cliente_versao'] or data.get('cliente_id_snapshot')!=client['id']):
                reason='O cadastro mudou após a revisão ou está na lixeira. Confira antes de importar.'
            elif not client and item['cliente_versao'] is not None:
                reason='O cliente foi removido após a revisão. Não foi recriado automaticamente.'
            elif client:
                for field,value in data['fields'].items():
                    if not same(field,client.get(field),value):reason=f'{FIELDS[field]} mudou desde a revisão.';break
            outcome={'id':item['id'],'estado':'pendente' if reason else 'aplicado','motivos':dump([reason] if reason else []),
                     'novo_cliente':0,'novos_telefones':0,'novos_emails':0,'novos_enderecos':0,'nova_categoria':0,'cliente_id_resultado':None}
            outcomes.append(outcome)
            if not reason:plans.append({'data':data,'old':client,'outcome':outcome,'filled':[]})
        new=[plan for plan in plans if not plan['old']]
        if new:
            connection.many('INSERT INTO clientes (cpf,nome,data_nascimento,nome_mae,sexo_origem) VALUES (%s,%s,%s,%s,%s)',
                            [(plan['data']['cpf'],)+tuple(plan['data']['fields'].get(field) for field in fields) for plan in new])
            new_cpfs=[plan['data']['cpf'] for plan in new]
            for client in connection.all('SELECT * FROM clientes WHERE cpf IN ('+','.join(['%s']*len(new_cpfs))+')',new_cpfs):clients[client['cpf']]=client
        ids=[clients[plan['data']['cpf']]['id'] for plan in plans]
        phones,emails,addresses,links={},{},{},set()
        phone_states={}
        if ids:
            id_marks=','.join(['%s']*len(ids))
            for table,column,target,field in [('telefones','numero',phones,'phones'),('emails','email',emails,'emails')]:
                if any(plan['data'][field] for plan in plans):
                    extra=',id,situacao,excluido_em' if table=='telefones' and verify_phones else ''
                    for contact in connection.all(f'SELECT cliente_id,{column}{extra} FROM {table} WHERE cliente_id IN ({id_marks})',ids):
                        target.setdefault(contact['cliente_id'],set()).add(contact[column])
                        if table=='telefones' and verify_phones:phone_states[(contact['cliente_id'],contact['numero'])]=contact
            if any(plan['data']['addresses'] for plan in plans):
                for address in connection.all(f'SELECT * FROM enderecos WHERE cliente_id IN ({id_marks})',ids):addresses.setdefault(address['cliente_id'],set()).add(tuple(address[field] or None for field in ADDRESS_FIELDS))
            if categories:
                cat_marks=','.join(['%s']*len(categories))
                links={(row['cliente_id'],row['categoria_id']) for row in connection.all(f'SELECT cliente_id,categoria_id FROM app_cliente_categorias WHERE categoria_id IN ({cat_marks}) AND cliente_id IN ({id_marks})',categories+ids)}
        phone_inserts=[];email_inserts=[];address_inserts=[];link_inserts=[];updates=[];audits=[];version_only=[];changed_fields=set()
        verified_ids=[];phone_audits=[];verified_count=0
        for plan in plans:
            data=plan['data'];old=plan['old'];outcome=plan['outcome'];client_id=clients[data['cpf']]['id']
            outcome['cliente_id_resultado']=client_id;outcome['novo_cliente']=int(old is None)
            if old:
                for field,value in data['fields'].items():
                    if old.get(field) in (None,''):plan['filled'].append(field)
            verified_for_client=0
            for values,current,target,result_key in [(data['phones'],phones,phone_inserts,'novos_telefones'),(data['emails'],emails,email_inserts,'novos_emails')]:
                known=current.setdefault(client_id,set())
                for value in values:
                    if value not in known:
                        target.append((client_id,value,'confirmado' if verify_phones else 'nao_verificado') if result_key=='novos_telefones' else (client_id,value))
                        known.add(value);outcome[result_key]+=1
                        if verify_phones and result_key=='novos_telefones':verified_for_client+=1
            if verify_phones:
                for number in data['phones']:
                    current=phone_states.get((client_id,number))
                    if current and not current['excluido_em'] and current['situacao']!='confirmado':
                        verified_ids.append(current['id']);verified_for_client+=1
                        phone_audits.append((job['aprovador_id'],client_id,'verificar_origem','telefone',current['id'],
                                             dump({'situacao':current['situacao']}),dump({'situacao':'confirmado','lote':job['id'],'fontes':summary.get('fontes_verificacao',[])})))
            verified_count+=verified_for_client
            known_addresses=addresses.setdefault(client_id,set())
            for address in data['addresses']:
                signature=tuple(address[field] for field in ADDRESS_FIELDS)
                if signature not in known_addresses:address_inserts.append((client_id,)+signature);known_addresses.add(signature);outcome['novos_enderecos']+=1
            for cat in categories:
                if (client_id,cat) not in links:
                    link_inserts.append((client_id,cat));links.add((client_id,cat));outcome['nova_categoria']+=1
            changed=bool(verified_for_client or plan['filled'] or any(outcome[key] for key in ('novo_cliente','novos_telefones','novos_emails','novos_enderecos','nova_categoria')))
            if old and changed:
                if plan['filled']:
                    changed_fields.update(plan['filled'])
                    updates.append(tuple(data['fields'].get(field) if field in plan['filled'] else old.get(field) for field in fields)+(client_id,))
                else:version_only.append(client_id)
            if changed:
                result={key:outcome[key] for key in ('novo_cliente','novos_telefones','novos_emails','novos_enderecos','nova_categoria')}
                audits.append((job['aprovador_id'],client_id,'importar','cliente',client_id,dump({'lote':job['id'],'campos_preenchidos':plan['filled'],'telefones_verificados':verified_for_client,**result})))
        if updates:update_client_fields(connection,updates,changed_fields)
        if version_only:bump_clients(connection,version_only)
        if phone_inserts:connection.many('INSERT INTO telefones (cliente_id,numero,situacao) VALUES (%s,%s,%s)',phone_inserts)
        for batch in chunks(verified_ids):
            connection.execute("UPDATE telefones SET situacao='confirmado' WHERE id IN ("+','.join(['%s']*len(batch))+") AND excluido_em IS NULL",batch)
        if phone_audits:connection.many('INSERT INTO app_auditoria (usuario_id,cliente_id,acao,entidade,entidade_id,antes,depois) VALUES (%s,%s,%s,%s,%s,%s,%s)',phone_audits)
        if email_inserts:connection.many('INSERT INTO emails (cliente_id,email) VALUES (%s,%s)',email_inserts)
        if address_inserts:connection.many('INSERT INTO enderecos (cliente_id,'+','.join(ADDRESS_FIELDS)+') VALUES ('+','.join(['%s']*10)+')',address_inserts)
        if link_inserts:connection.many('INSERT INTO app_cliente_categorias (cliente_id,categoria_id) VALUES (%s,%s)',link_inserts)
        if audits:connection.many('INSERT INTO app_auditoria (usuario_id,cliente_id,acao,entidade,entidade_id,depois) VALUES (%s,%s,%s,%s,%s,%s)',audits)
        cls.update_items(connection,outcomes)
        summary['telefones_verificados']=summary.get('telefones_verificados',0)+verified_count
        job['resumo']=dump(summary)
        return len(plans)

