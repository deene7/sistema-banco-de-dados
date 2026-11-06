import csv
import io
import json
import os
import re
import secrets
from functools import wraps
from pathlib import Path

import pymysql
from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash
from import_data import FIELDS, guess, inspect_file, file_digest
from import_jobs import ImportManager, dump, category_ids, check_categories, trusted_phone_sources
from pagination import page_numbers
from bulk import chunks, client_state as bulk_client_state, remove_clients


def register_features(app, config, db, required, audit, locked_client, bump):
    feature=Blueprint('features',__name__)
    manager=ImportManager(config)
    app.import_manager=manager
    page_index={}

    def exclusive_heavy(function):
        @wraps(function)
        def wrapped(*args,**kwargs):
            if request.method!='POST':return function(*args,**kwargs)
            if not manager.work_lock.acquire(blocking=False):
                raise ValueError('Há um processamento grande em andamento. Aguarde a conclusão antes de excluir ou restaurar outro lote. As consultas continuam disponíveis.')
            try:return function(*args,**kwargs)
            finally:manager.work_lock.release()
        return wrapped

    def stage_page_source():
        if db().sqlite:return ' FROM app_importacao_itens x'
        if 'name' not in page_index:
            index=db().one("SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_importacao_itens' AND INDEX_NAME IN ('idx_app_importacao_lote_id','idx_app_importacao_estado') ORDER BY CASE INDEX_NAME WHEN 'idx_app_importacao_lote_id' THEN 0 ELSE 1 END LIMIT 1")
            page_index['name']=index['INDEX_NAME'] if index else None
        name=page_index['name']
        return ' FROM app_importacao_itens x'+(f' FORCE INDEX ({name})' if name else '')

    @feature.before_request
    def ready():
        if not app.config['FEATURES_V2'] or (not app.config['FEATURES_V3'] and request.endpoint!='features.purge'):
            return render_template('error.html',title='Atualização necessária',message=
                                   'Execute ATUALIZAR_SISTEMA.cmd neste computador para ativar importações sem categoria e a exclusão de lotes.'),503

    def run_job(job_id, phase):
        if current_app.config.get('TESTING_SYNC_IMPORTS'):
            (manager.prepare if phase=='preparar' else manager.apply)(job_id)
        else:manager.dispatch(job_id,phase)

    def job_record(job_id,include_excluded=False):
        job=db().one("SELECT i.*,COALESCE(c.nome,'Sem categoria') AS categoria FROM app_importacoes i LEFT JOIN app_categorias c ON c.id=i.categoria_id WHERE i.id=%s",(job_id,))
        if not job or (job['excluido_em'] and not include_excluded):abort(404)
        label_categories([job])
        return job

    def label_categories(jobs):
        ids=sorted({cat for job in jobs for cat in category_ids(job)})
        names={row['id']:row['nome'] for row in db().all('SELECT id,nome FROM app_categorias WHERE id IN ('+','.join(['%s']*len(ids))+')',ids)} if ids else {}
        for job in jobs:job['categoria']=' · '.join(names.get(cat,'Categoria indisponível') for cat in category_ids(job)) or 'Sem categoria'

    def category_condition(alias='i'):
        member=(f"EXISTS (SELECT 1 FROM json_each({alias}.mapeamento,'$.categorias') WHERE CAST(value AS TEXT)=%s)" if db().sqlite else f"JSON_CONTAINS({alias}.mapeamento,%s,'$.categorias')")
        return f'({alias}.categoria_id=%s OR {member})'

    def deletion_reason():
        reason=request.form.get('motivo','').strip()
        if len(reason)>500 or (not reason and g.user['perfil']!='ADM'):
            raise ValueError('Informe um motivo com até 500 caracteres.')
        return reason or None

    def batch_created_clients(job_id):
        # Só cadastros criados pelo lote, intactos e não usados por outro lote ativo.
        job=db().one('SELECT resumo FROM app_importacoes WHERE id=%s',(job_id,))
        summary=json.loads(job['resumo']) if job['resumo'] else {}
        versions={entry['id']:entry['versao'] for entry in summary.get('_clientes_restaurados',[])}
        ids=sorted({row['cliente_id_resultado'] for row in db().all("SELECT cliente_id_resultado FROM app_importacao_itens WHERE importacao_id=%s AND estado='aplicado' AND novo_cliente=1 AND cliente_id_resultado IS NOT NULL",(job_id,))})
        candidates=[]
        for batch in chunks(ids):
            marks=','.join(['%s']*len(batch))
            candidates.extend(db().all(f'SELECT c.id,c.app_versao,c.excluido_em FROM clientes c WHERE c.id IN ({marks}) '
                              'AND c.excluido_em IS NULL AND NOT EXISTS ('
                              'SELECT 1 FROM app_importacao_itens y JOIN app_importacoes j ON j.id=y.importacao_id '
                              "WHERE y.cliente_id_resultado=c.id AND y.estado='aplicado' AND y.importacao_id<>%s AND j.excluido_em IS NULL) "
                              'ORDER BY c.id FOR UPDATE',batch+[job_id]))
        return [c for c in candidates if c['app_versao']==versions.get(c['id'],1)]

    def tracked_clients(job):
        summary=json.loads(job['resumo']) if job['resumo'] else {}
        result=[];versions={entry['id']:entry['versao'] for entry in summary.get('_lixeira_clientes',[])}
        for batch in chunks(sorted(versions)):
            marks=','.join(['%s']*len(batch))
            clients=db().all(f'SELECT c.id,c.app_versao,c.excluido_em FROM clientes c WHERE c.id IN ({marks}) AND c.excluido_em IS NOT NULL '
                             "AND NOT EXISTS (SELECT 1 FROM app_importacao_itens x JOIN app_importacoes j ON j.id=x.importacao_id "
                             "WHERE x.cliente_id_resultado=c.id AND x.importacao_id<>%s AND x.estado='aplicado' AND j.excluido_em IS NULL) ORDER BY c.id FOR UPDATE",batch+[job['id']])
            result.extend(client for client in clients if client['app_versao']==versions[client['id']])
        return summary,result

    @feature.route('/categorias',methods=['GET','POST'])
    @required(edit=True)
    def categories():
        if request.method=='POST':
            name=' '.join(request.form.get('nome','').split())
            description=request.form.get('descricao','').strip()
            if not name or len(name)>100 or len(description)>500:raise ValueError('Informe uma categoria com até 100 caracteres e descrição de até 500.')
            if db().one('SELECT id FROM app_categorias WHERE nome=%s',(name,)):
                raise ValueError('Já existe uma categoria com esse nome, inclusive entre as excluídas.')
            cur=db().execute('INSERT INTO app_categorias (nome,descricao) VALUES (%s,%s)',(name,description or None))
            audit('criar_categoria','categoria',cur.lastrowid,after={'nome':name,'descricao':description})
            db().commit();flash('Categoria criada.','success')
            return redirect(url_for('features.categories'))
        rows=db().all('SELECT c.*,(SELECT COUNT(*) FROM app_cliente_categorias l JOIN clientes x ON x.id=l.cliente_id '
                      'WHERE l.categoria_id=c.id AND x.excluido_em IS NULL) AS clientes FROM app_categorias c ORDER BY ativo DESC,nome')
        return render_template('categories.html',categories=rows)

    @feature.post('/categorias/<int:category_id>/<action>')
    @required(edit=True)
    def category_action(category_id,action):
        if action not in ('editar','excluir','restaurar'):abort(404)
        before=db().one('SELECT * FROM app_categorias WHERE id=%s FOR UPDATE',(category_id,))
        if not before:abort(404)
        if str(before['versao'])!=request.form.get('versao'):raise ValueError('A categoria mudou. Atualize a página antes de salvar.')
        if action=='editar':
            name=' '.join(request.form.get('nome','').split());description=request.form.get('descricao','').strip()
            if not name or len(name)>100 or len(description)>500:raise ValueError('Confira o nome e a descrição da categoria.')
            if db().one('SELECT id FROM app_categorias WHERE nome=%s AND id<>%s',(name,category_id)):
                raise ValueError('Já existe outra categoria com esse nome.')
            db().execute('UPDATE app_categorias SET nome=%s,descricao=%s,versao=versao+1 WHERE id=%s',(name,description or None,category_id))
            after={'nome':name,'descricao':description}
        else:
            active=action=='restaurar'
            if bool(before['ativo'])==active:raise ValueError('A categoria já está nessa situação.')
            if db().one("SELECT i.id FROM app_importacoes i WHERE "+category_condition()+" AND i.excluido_em IS NULL AND i.estado IN ('preparando','importando') LIMIT 1",(category_id,str(category_id))):
                raise ValueError('Aguarde a importação desta categoria terminar antes de excluí-la.')
            db().execute('UPDATE app_categorias SET ativo=%s,versao=versao+1 WHERE id=%s',(int(active),category_id))
            after={'ativo':active}
        audit(action+'_categoria','categoria',category_id,before=before,after=after)
        db().commit();flash('Categoria atualizada. Nenhum cliente foi excluído.','success')
        return redirect(url_for('features.categories'))

    @feature.post('/clientes/<int:client_id>/categoria')
    @required(edit=True)
    def client_category(client_id):
        locked_client(client_id)
        category_id=request.form.get('categoria_id',type=int)
        category=db().one('SELECT * FROM app_categorias WHERE id=%s AND ativo=1',(category_id,))
        if not category:raise ValueError('Escolha uma categoria ativa.')
        exists=db().one('SELECT cliente_id FROM app_cliente_categorias WHERE cliente_id=%s AND categoria_id=%s',(client_id,category_id))
        if request.form.get('acao')=='remover':
            if not exists:raise ValueError('O cliente não está nessa categoria.')
            db().execute('DELETE FROM app_cliente_categorias WHERE cliente_id=%s AND categoria_id=%s',(client_id,category_id))
            action='remover_categoria'
        else:
            if exists:raise ValueError('O cliente já está nessa categoria.')
            db().execute('INSERT INTO app_cliente_categorias (cliente_id,categoria_id) VALUES (%s,%s)',(client_id,category_id))
            action='vincular_categoria'
        bump(client_id);audit(action,'cliente',client_id,after={'categoria':category['nome']},client_id=client_id)
        db().commit();flash('Categorias do cliente atualizadas.','success')
        return redirect(url_for('detail',client_id=client_id))

    @feature.route('/clientes/<int:client_id>/excluir-definitivamente',methods=['GET','POST'])
    @required(edit=True)
    def purge(client_id):
        if g.user['perfil']!='ADM':abort(403)
        client=locked_client(client_id,allow_deleted=True) if request.method=='POST' else db().one('SELECT * FROM clientes WHERE id=%s',(client_id,))
        if not client:abort(404)
        if not client['excluido_em']:raise ValueError('Somente clientes que já estão na lixeira podem ser excluídos permanentemente.')
        counts={table:db().one(f'SELECT COUNT(*) AS n FROM {table} WHERE cliente_id=%s',(client_id,))['n']
                for table in ('telefones','emails','enderecos')}
        if request.method=='POST':
            if request.form.get('confirmacao','').strip()!=client['cpf']:
                raise ValueError('Digite o CPF completo, somente os 11 números, para confirmar a exclusão.')
            reason=deletion_reason()
            user=db().one('SELECT senha_hash FROM app_usuarios WHERE id=%s',(g.user['id'],))
            if not check_password_hash(user['senha_hash'],request.form.get('senha','')):
                raise ValueError('Sua senha do sistema não confere. Nenhum dado foi excluído.')
            # O histórico permanece imutável; a FK atualizada só desvincula cliente_id.
            audit('excluir_permanentemente','cliente',client_id,after={'cadastro_id':client_id,'contatos_removidos':counts},reason=reason)
            try:
                db().execute('DELETE FROM app_cliente_categorias WHERE cliente_id=%s',(client_id,))
                for table in ('telefones','emails','enderecos'):
                    db().execute(f'DELETE FROM {table} WHERE cliente_id=%s',(client_id,))
                db().execute('DELETE FROM clientes WHERE id=%s',(client_id,))
            except pymysql.IntegrityError:
                raise ValueError('Há outro vínculo que impede a exclusão. Nada foi removido; solicite revisão à TI.')
            db().commit();flash('Cadastro e contatos excluídos permanentemente da base operacional.','success')
            return redirect(url_for('index',lixeira=1))
        return render_template('purge.html',client=client,counts=counts)

    @feature.get('/importacoes')
    @required(edit=True)
    def imports():
        search=request.args.get('q','').strip()[:255]
        excluded=request.endpoint=='features.trash_imports' or request.args.get('excluidas')=='1'
        state=request.args.get('estado','')
        if state not in ('mapeamento','preparando','revisao','importando','concluida','erro'):state=''
        category=request.args.get('categoria','todos')
        if category!='sem' and not category.isdigit():category='todos'
        conditions=['i.excluido_em IS '+('NOT NULL' if excluded else 'NULL')];params=[]
        if search:
            conditions.append("i.arquivo LIKE %s ESCAPE '!'")
            params.append('%'+search.replace('!','!!').replace('%','!%').replace('_','!_')+'%')
        if state:conditions.append('i.estado=%s');params.append(state)
        if category=='sem':conditions.append('i.categoria_id IS NULL')
        elif category.isdigit():conditions.append(category_condition());params.extend([int(category),str(int(category))])
        where=' WHERE '+' AND '.join(conditions)
        total=db().one('SELECT COUNT(*) AS n FROM app_importacoes i'+where,params)['n']
        pages=max(1,(total+24)//25);page=min(max(1,request.args.get('pagina',1,type=int)),pages)
        jobs=db().all("SELECT i.id,i.arquivo,i.estado,i.criado_em,i.progresso,i.excluido_em,i.categoria_id,i.mapeamento,COALESCE(c.nome,'Sem categoria') AS categoria "
                      'FROM app_importacoes i LEFT JOIN app_categorias c ON c.id=i.categoria_id'+where+' ORDER BY i.id DESC LIMIT %s OFFSET %s',params+[25,(page-1)*25])
        label_categories(jobs)
        paging={'q':search,'estado':state,'categoria':category,'excluidas':int(excluded)}
        return render_template('imports.html',jobs=jobs,search=search,state=state,category=category,excluded=excluded,
                               total=total,pages=pages,page=page,page_numbers=page_numbers(page,pages),page_size=25,paging=paging,
                               pagination_endpoint='features.trash_imports' if excluded else 'features.imports',result_start=(page-1)*25+1 if total else 0,result_end=min(page*25,total))

    @feature.get('/lixeira/importacoes')
    @required(edit=True)
    def trash_imports():
        return imports()

    @feature.post('/importacoes/enviar')
    @required(edit=True)
    def upload():
        values=[value for value in request.form.getlist('categoria_id') if value.strip()]
        if any(not value.isdigit() for value in values) or len(values)>50:
            raise ValueError('Escolha até 50 categorias válidas ou deixe sem categoria.')
        categories=sorted(set(int(value) for value in values))
        category_id=categories[0] if categories else None
        check_categories(db(),{'mapeamento':dump({'categorias':categories})})
        verify_source=bool(trusted_phone_sources(db(),{'mapeamento':dump({'categorias':categories})}))
        file=request.files.get('arquivo')
        filename=Path((file.filename if file else '') or '').name
        suffix=Path(filename).suffix.lower()
        if not file or suffix not in ('.csv','.xlsx') or len(filename)>255:
            raise ValueError('Envie um arquivo CSV ou Excel .xlsx, com nome de até 255 caracteres.')
        root=Path(config.get('uploads_dir',Path(__file__).with_name('importacoes_privadas'))).resolve()
        if not root.is_dir():raise ValueError('A pasta privada de importações não foi preparada. Execute ATUALIZAR_SISTEMA.cmd.')
        token=secrets.token_hex(16);path=root/(token+suffix)
        file.save(path)
        if path.stat().st_size>50*1024*1024:
            path.unlink();raise ValueError('O arquivo excede 50 MB. Divida em arquivos menores.')
        digest=file_digest(path)
        previous=db().all("SELECT id,categoria_id,mapeamento,resumo FROM app_importacoes WHERE sha256=%s AND (categoria_id=%s OR (categoria_id IS NULL AND %s IS NULL)) AND excluido_em IS NULL AND estado='concluida' ORDER BY id DESC",(digest,category_id,category_id))
        duplicate=next((job for job in previous if category_ids(job)==categories and bool(json.loads(job['resumo'] or '{}').get('verificar_telefones'))==verify_source),None)
        if duplicate:
            path.unlink();flash('Este mesmo arquivo já foi processado nesta categoria. Confira o recibo e as pendências.','info')
            return redirect(url_for('features.import_review',job_id=duplicate['id']))
        try:headers,examples,sheets,sheet=inspect_file(path)
        except Exception:
            path.unlink();raise ValueError('Não foi possível ler o arquivo. Confira o formato, a codificação e o cabeçalho da primeira linha.')
        if len(str(path))>255:path.unlink();raise ValueError('O caminho da pasta de importação é muito longo.')
        cur=db().execute('INSERT INTO app_importacoes (token,arquivo,caminho,sha256,categoria_id,usuario_id,aba) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                         (token,filename,str(path),digest,category_id,g.user['id'],sheet))
        db().execute('UPDATE app_importacoes SET mapeamento=%s WHERE id=%s',(dump({'categorias':categories}),cur.lastrowid))
        audit('enviar_arquivo','importacao',cur.lastrowid,after={'arquivo':filename,'categorias':categories,'sha256':digest})
        db().commit()
        return redirect(url_for('features.import_mapping',job_id=cur.lastrowid))

    @feature.route('/importacoes/<int:job_id>/mapear',methods=['GET','POST'])
    @required(edit=True)
    def import_mapping(job_id):
        job=job_record(job_id)
        if job['estado']!='mapeamento':return redirect(url_for('features.import_review',job_id=job_id))
        sheet=request.values.get('aba') or job['aba']
        headers,examples,sheets,sheet=inspect_file(job['caminho'],sheet)
        if request.method=='POST':
            mapping=[request.form.get('coluna_'+str(i),'extra') for i in range(len(headers))]
            if any(value not in FIELDS for value in mapping) or mapping.count('cpf')!=1:
                raise ValueError('Mapeie exatamente uma coluna de CPF. Campos não reconhecidos devem ser preservados como origem.')
            known=[value for value in mapping if value!='extra']
            if len(known)!=len(set(known)):raise ValueError('Cada campo deve receber apenas uma coluna. Use os campos Telefone 1 a 10 para números diferentes.')
            spec={'headers':headers,'targets':mapping,'categorias':category_ids(job)}
            cur=db().execute("UPDATE app_importacoes SET estado='preparando',aba=%s,mapeamento=%s,progresso=0,erro=NULL WHERE id=%s AND estado='mapeamento' AND excluido_em IS NULL",
                             (sheet,dump(spec),job_id))
            if cur.rowcount!=1:raise ValueError('Este lote já foi iniciado. Atualize a página.')
            audit('revisar_arquivo','importacao',job_id,after={'mapeamento':spec,'aba':sheet})
            db().commit();run_job(job_id,'preparar')
            return redirect(url_for('features.import_review',job_id=job_id))
        return render_template('import_mapping.html',job=job,headers=headers,examples=examples,
                               sheets=sheets,sheet=sheet,guesses=guess(headers),fields=FIELDS)

    @feature.get('/importacoes/<int:job_id>')
    @required(edit=True)
    def import_review(job_id):
        job=job_record(job_id,include_excluded=True)
        if job['estado']=='mapeamento' and not job['excluido_em']:return redirect(url_for('features.import_mapping',job_id=job_id))
        summary=json.loads(job['resumo']) if job['resumo'] else {}
        totals=db().one("SELECT COUNT(*) AS itens,COALESCE(SUM(estado='aplicado'),0) AS aplicados,"
                        "COALESCE(SUM(estado='pendente'),0) AS pendentes,COALESCE(SUM(estado='pronto'),0) AS prontos,"
                        'COALESCE(SUM(novo_cliente),0) AS clientes,COALESCE(SUM(novos_telefones),0) AS telefones,'
                        'COALESCE(SUM(novos_emails),0) AS emails,COALESCE(SUM(novos_enderecos),0) AS enderecos, '
                        'COALESCE(SUM(cliente_versao IS NOT NULL),0) AS ja_cadastrados '
                        'FROM app_importacao_itens WHERE importacao_id=%s',(job_id,))
        pending=db().all("SELECT id,cpf,linhas,motivos FROM app_importacao_itens WHERE importacao_id=%s AND estado='pendente' ORDER BY id LIMIT 30",(job_id,))
        for item in pending:
            item['linhas']=json.loads(item['linhas']);item['motivos']=json.loads(item['motivos'])
        return render_template('import_review.html',job=job,summary=summary,totals=totals,
                               pending=pending,active=manager.active(job_id))

    @feature.post('/importacoes/<int:job_id>/confirmar')
    @required(edit=True)
    def import_confirm(job_id):
        job=job_record(job_id)
        if job['estado']!='revisao':raise ValueError('Este lote não está aguardando confirmação.')
        if request.form.get('confirmacao')!='1':raise ValueError('Confirme que revisou o resumo e o mapeamento.')
        check_categories(db(),job)
        cur=db().execute("UPDATE app_importacoes SET estado='importando',aprovador_id=%s,progresso=0 WHERE id=%s AND estado='revisao' AND excluido_em IS NULL",(g.user['id'],job_id))
        if cur.rowcount!=1:raise ValueError('O lote já foi confirmado.')
        summary=json.loads(job['resumo'] or '{}')
        audit('confirmar_importacao','importacao',job_id,after={'categorias':category_ids(job),'verificar_telefones':summary.get('verificar_telefones',False),'fontes_verificacao':summary.get('fontes_verificacao',[])})
        db().commit();run_job(job_id,'importar')
        return redirect(url_for('features.import_review',job_id=job_id))

    @feature.post('/importacoes/<int:job_id>/retomar')
    @required(edit=True)
    def import_resume(job_id):
        job=job_record(job_id)
        if manager.active(job_id):raise ValueError('Este lote ainda está sendo processado. Atualize a página para acompanhar.')
        if job['estado'] not in ('erro','preparando','importando'):raise ValueError('Este lote não precisa de retomada.')
        phase='importar' if job['aprovador_id'] else 'preparar'
        state='importando' if phase=='importar' else 'preparando'
        if phase=='importar':
            db().execute('UPDATE app_importacoes SET estado=%s,erro=NULL,aprovador_id=%s WHERE id=%s',(state,g.user['id'],job_id))
        else:
            db().execute('UPDATE app_importacoes SET estado=%s,erro=NULL,usuario_id=%s WHERE id=%s',(state,g.user['id'],job_id))
        audit('retomar_importacao','importacao',job_id,after={'fase':phase})
        db().commit();run_job(job_id,phase)
        return redirect(url_for('features.import_review',job_id=job_id))

    @feature.get('/importacoes/<int:job_id>/pendencias.csv')
    @required(edit=True)
    def import_pending(job_id):
        job_record(job_id)
        items=db().all("SELECT cpf,linhas,motivos FROM app_importacao_itens WHERE importacao_id=%s AND estado='pendente' ORDER BY id",(job_id,))
        stream=io.StringIO();writer=csv.writer(stream,delimiter=';')
        writer.writerow(['CPF','Linhas de origem','Motivos'])
        for item in items:
            cells=[item['cpf'] or '',', '.join(map(str,json.loads(item['linhas']))),'; '.join(json.loads(item['motivos']))]
            writer.writerow([("'"+v) if v.startswith(('=','+','-','@','\t','\r')) else v for v in cells])
        return Response('\ufeff'+stream.getvalue(),mimetype='text/csv',
                        headers={'Content-Disposition':f'attachment; filename=pendencias_lote_{job_id}.csv'})

    @feature.post('/importacoes/<int:job_id>/revisar-novamente')
    @required(edit=True)
    def import_recheck(job_id):
        job=job_record(job_id)
        if job['estado'] not in ('concluida','revisao') or manager.active(job_id):raise ValueError('Conclua ou retome o processamento atual antes de fazer uma nova revisão.')
        check_categories(db(),job)
        cur=db().execute('INSERT INTO app_importacoes (token,arquivo,caminho,sha256,categoria_id,usuario_id,estado,aba,mapeamento) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                         (secrets.token_hex(16),job['arquivo'],job['caminho'],job['sha256'],job['categoria_id'],g.user['id'],'preparando',job['aba'],job['mapeamento']))
        new_id=cur.lastrowid
        audit('reavaliar_importacao','importacao',new_id,after={'lote_anterior':job_id})
        db().commit();run_job(new_id,'preparar')
        return redirect(url_for('features.import_review',job_id=new_id))

    @feature.get('/importacoes/<int:job_id>/status')
    @required(edit=True)
    def import_status(job_id):
        job=db().one('SELECT estado,progresso,excluido_em FROM app_importacoes WHERE id=%s',(job_id,))
        if not job or job['excluido_em']:abort(404)
        return jsonify(estado=job['estado'],progresso=job['progresso'],ativo=manager.active(job_id))

    @feature.get('/importacoes/<int:job_id>/clientes-existentes')
    @required(edit=True)
    def existing_clients(job_id):
        job=job_record(job_id,include_excluded=True)
        search=request.args.get('q','').strip()[:255]
        all_clients=request.endpoint=='features.batch_clients'
        where=' WHERE x.importacao_id=%s'+('' if all_clients else ' AND x.cliente_versao IS NOT NULL');params=[job_id]
        if search:
            escaped=search.replace('!','!!').replace('%','!%').replace('_','!_')
            cpf_source='cpf' if db().sqlite else 'CONVERT(cpf USING utf8mb4) COLLATE utf8mb4_0900_ai_ci'
            where+=f" AND (x.dados LIKE %s ESCAPE '!' OR x.cpf=%s OR x.cpf IN (SELECT {cpf_source} FROM clientes WHERE nome LIKE %s ESCAPE '!'))";params.extend(['%'+escaped+'%',re.sub('[^0-9]','',search),'%'+escaped+'%'])
        source=' FROM app_importacao_itens x'
        total=db().one('SELECT COUNT(*) AS n'+source+where,params)['n'];pages=max(1,(total+49)//50)
        page=min(max(1,request.args.get('pagina',1,type=int)),pages)
        item_ids=[row['id'] for row in db().all('SELECT x.id'+stage_page_source()+where+' ORDER BY x.id LIMIT %s OFFSET %s',params+[50,(page-1)*50])]
        items=db().all('SELECT id,cpf,estado,dados FROM app_importacao_itens WHERE id IN ('+','.join(['%s']*len(item_ids))+') ORDER BY id',item_ids) if item_ids else []
        cpfs=sorted({item['cpf'] for item in items if item['cpf']})
        existing={row['cpf']:row for row in db().all('SELECT id,cpf,nome,excluido_em FROM clientes WHERE cpf IN ('+','.join(['%s']*len(cpfs))+')',cpfs)} if cpfs else {}
        clients=[]
        for item in items:
            client=existing.get(item['cpf'],{})
            clients.append({'id':client.get('id'),'cpf':item['cpf'],'estado':item['estado'],'excluido_em':client.get('excluido_em'),
                            'nome':client.get('nome') or json.loads(item['dados']).get('fields',{}).get('nome')})
        return render_template('import_existing.html',all_clients=all_clients,job=job,clients=clients,search=search,total=total,page=page,pages=pages,
                               page_size=50,page_numbers=page_numbers(page,pages),pagination_endpoint='features.batch_clients' if all_clients else 'features.existing_clients',
                               paging={'job_id':job_id,'q':search},result_start=(page-1)*50+1 if total else 0,result_end=min(page*50,total))

    @feature.get('/importacoes/<int:job_id>/clientes')
    @required(edit=True)
    def batch_clients(job_id):
        return existing_clients(job_id)

    @feature.route('/importacoes/<int:job_id>/excluir',methods=['GET','POST'])
    @required(edit=True)
    @exclusive_heavy
    def import_delete(job_id):
        job=job_record(job_id)
        if job['estado'] in ('preparando','importando') or manager.active(job_id):
            raise ValueError('Aguarde o processamento terminar antes de excluir esta importação.')
        if request.method=='POST':
            if request.form.get('confirmacao')!='1':raise ValueError('Confirme a exclusão do lote e dos clientes novos elegíveis.')
            reason=deletion_reason()
            locked=db().one('SELECT estado,excluido_em,resumo FROM app_importacoes WHERE id=%s FOR UPDATE',(job_id,))
            if locked['excluido_em'] or locked['estado'] in ('preparando','importando'):
                raise ValueError('A situação deste lote mudou. Atualize a página antes de excluir.')
            clients=batch_created_clients(job_id)
            summary=json.loads(locked['resumo']) if locked['resumo'] else {}
            summary['_lixeira_clientes']=[{'id':c['id'],'versao':c['app_versao']+1} for c in clients]
            bulk_client_state(db(),[client['id'] for client in clients])
            db().execute('UPDATE app_importacoes SET excluido_em=CURRENT_TIMESTAMP,resumo=%s WHERE id=%s',(dump(summary),job_id))
            audit('excluir_importacao','importacao',job_id,after={'arquivo':job['arquivo'],'clientes_para_lixeira':[c['id'] for c in clients]},reason=reason)
            db().commit();flash(f'Lote e {len(clients)} cliente(s) novo(s) movidos para a lixeira. Cadastros anteriores, alterados ou usados por outro lote foram preservados.','success')
            return redirect(url_for('features.trash_imports'))
        return render_template('import_delete.html',job=job)

    @feature.route('/importacoes/<int:job_id>/excluir-definitivamente',methods=['GET','POST'])
    @required(edit=True)
    @exclusive_heavy
    def import_purge(job_id):
        if g.user['perfil']!='ADM':abort(403)
        job=job_record(job_id,include_excluded=True)
        if not job['excluido_em']:raise ValueError('Mova a importação para a lixeira antes de excluir permanentemente.')
        if manager.active(job_id) or job['estado'] in ('preparando','importando'):
            raise ValueError('O lote ainda está em processamento.')
        if request.method=='POST':
            if not config.get('features_v4',config.get('demo',False)):
                raise ValueError('Execute ATUALIZAR_SISTEMA.cmd para ativar a exclusão permanente de lotes.')
            if request.form.get('confirmacao','').strip()!=str(job_id):raise ValueError('Digite o número do lote para confirmar.')
            reason=deletion_reason()
            user=db().one('SELECT senha_hash FROM app_usuarios WHERE id=%s',(g.user['id'],))
            if not check_password_hash(user['senha_hash'],request.form.get('senha','')):raise ValueError('Sua senha do sistema não confere.')
            locked=db().one('SELECT estado,excluido_em,resumo FROM app_importacoes WHERE id=%s FOR UPDATE',(job_id,))
            if not locked or not locked['excluido_em'] or locked['estado'] in ('preparando','importando'):
                raise ValueError('O lote mudou. Atualize a página antes de continuar.')
            # Lotes excluídos na versão anterior ainda não possuem vínculo de lixeira.
            job.update(locked)
            summary,clients=tracked_clients(job)
            if '_lixeira_clientes' not in summary:clients=batch_created_clients(job_id)
            remove_clients(db(),[client['id'] for client in clients])
            audit('excluir_importacao_permanentemente','importacao',job_id,after={'arquivo':job['arquivo'],'clientes_removidos':[c['id'] for c in clients]},reason=reason)
            db().execute('DELETE FROM app_importacao_itens WHERE importacao_id=%s',(job_id,))
            db().execute('DELETE FROM app_importacoes WHERE id=%s',(job_id,))
            db().commit()
            flash(f'Lote e {len(clients)} cliente(s) removidos permanentemente. Cadastros anteriores, alterados ou usados por outro lote foram preservados.','success')
            return redirect(url_for('features.trash_imports'))
        return render_template('import_purge.html',job=job)

    @feature.post('/importacoes/<int:job_id>/restaurar')
    @required(edit=True)
    @exclusive_heavy
    def import_restore(job_id):
        db().one('SELECT id FROM app_importacoes WHERE id=%s FOR UPDATE',(job_id,))
        job=job_record(job_id,include_excluded=True)
        if not job['excluido_em']:raise ValueError('Esta importação não está excluída.')
        summary,clients=tracked_clients(job)
        bulk_client_state(db(),[client['id'] for client in clients],restore=True)
        summary['_clientes_restaurados']=[{'id':c['id'],'versao':c['app_versao']+1} for c in clients]
        summary.pop('_lixeira_clientes',None)
        db().execute('UPDATE app_importacoes SET resumo=%s WHERE id=%s',(dump(summary),job_id))
        db().execute('UPDATE app_importacoes SET excluido_em=NULL WHERE id=%s',(job_id,))
        audit('restaurar_importacao','importacao',job_id,after={'arquivo':job['arquivo']})
        db().commit();flash('Registro da importação restaurado.','success')
        return redirect(url_for('features.import_review',job_id=job_id))

    app.register_blueprint(feature)
