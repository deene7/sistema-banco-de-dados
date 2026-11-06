"""Envio, configuração, acompanhamento e download de consultas sem alteração de cadastros."""
import json
from pathlib import Path
import re
import secrets
import shutil
from flask import Blueprint,abort,flash,g,jsonify,redirect,render_template,request,send_file,url_for
from import_data import file_digest,guess,inspect_file
from spreadsheet_lookup import GROUPS,SpreadsheetLookupManager,LookupExpired,lookup_groups

STATES=('AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO')


def register_lookups(app,config,db,required,audit):
    pages=Blueprint('lookups',__name__)
    manager=SpreadsheetLookupManager(config);app.lookup_manager=manager

    @pages.before_request
    def ready():
        if not app.config['FEATURES_V2']:abort(404)

    @pages.errorhandler(LookupExpired)
    def expired_files(error):
        return render_template('error.html',title='Arquivos da consulta expirados',message=str(error)),410

    def owned(token):
        if not re.fullmatch('[a-f0-9]{32}',token):abort(404)
        job=manager.store.get(token)
        if not job:abort(404)
        if job['usuario_id']!=g.user['id'] and g.user['perfil']!='ADM':abort(403)
        return job

    def state_of(job):
        if manager.expired(job):return 'expirada'
        state=job['estado']
        return 'interrompida' if state in ('fila','processando') and not manager.active(job['token']) else state

    def recent():
        jobs=manager.store.recent(None if g.user['perfil']=='ADM' else g.user['id'])
        for job in jobs:job['situacao']=state_of(job)
        return jobs
    app.lookup_recent=recent

    @pages.get('/consultas-planilha')
    @required(edit=True)
    def index():
        return render_template('lookup_form.html',jobs=recent(),states=STATES)

    @pages.post('/consultas-planilha/enviar')
    @required(edit=True)
    def upload():
        values=request.form.getlist('categoria_id')
        if len(values)>50 or any(not value.isdigit() or len(value)>20 for value in values):raise ValueError('Selecione até 50 categorias válidas.')
        ids=sorted({int(value) for value in values})
        selected=db().all('SELECT id,nome FROM app_categorias WHERE ativo=1 AND id IN ('+','.join(['%s']*len(ids))+')',ids) if ids else []
        if len(selected)!=len(ids):raise ValueError('Uma categoria selecionada não está disponível.')
        uf=request.form.get('uf','').strip().upper()
        kind=request.form.get('tipo','telefones')
        if kind not in ('cpf','telefones'):raise ValueError('Escolha consultar CPF ou telefones.')
        verification='todos' if kind=='cpf' else request.form.get('verificacao','todos')
        if uf and uf not in STATES:raise ValueError('Escolha uma UF válida.')
        if verification not in ('todos','com','sem'):raise ValueError('Escolha uma situação de verificação válida.')
        file=request.files.get('arquivo');filename=Path(file.filename or '').name if file else ''
        suffix=Path(filename).suffix.lower()
        if not file or suffix not in ('.xlsx','.csv') or len(filename)>255:raise ValueError('Envie uma planilha Excel .xlsx ou CSV válida.')
        manager.store.ensure();token=secrets.token_hex(16)
        folder=manager.store.root/token;folder.mkdir()
        source=folder/('entrada'+suffix)
        try:
            file.save(source)
            if not 0<source.stat().st_size<=50*1024*1024:raise ValueError('Use um arquivo entre 1 byte e 50 MB.')
            inspect_file(source)
            filters={'tipo':kind,'categorias':ids,'nomes_categorias':[row['nome'] for row in selected],'uf':uf,'verificacao':verification}
            manager.store.create(token,g.user['id'],filename,suffix,file_digest(source),filters)
        except Exception:
            folder.resolve().relative_to(manager.store.root)
            shutil.rmtree(folder)
            raise ValueError('Não foi possível ler o arquivo. Confira tamanho, formato e cabeçalho da primeira linha.')
        audit('enviar_consulta_planilha','planilha',0,after={'consulta':token,'arquivo':filename,'filtros':filters});db().commit()
        return redirect(url_for('lookups.detail',token=token))

    @pages.route('/consultas-planilha/<token>',methods=['GET','POST'])
    @required(edit=True)
    def detail(token):
        job=owned(token)
        filters=json.loads(job['filtros'])
        if request.method=='POST':
            if job['estado']!='mapeamento':raise ValueError('Esta consulta já foi iniciada.')
            with manager.file_access(token):
                sheet=request.form.get('aba') or None
                headers,examples,sheets,sheet=inspect_file(manager.source_path(job),sheet)
                column=request.form.get('cpf_col','')
                if not column.isdigit() or not 0<=int(column)<len(headers):raise ValueError('Escolha a coluna que contém o CPF.')
                mapping={'headers':headers,'cpf_col':int(column),'aba':sheet}
                audit('consultar_planilha','planilha',0,after={'consulta':token,'filtros':filters,'coluna_cpf':headers[int(column)]});db().commit()
                manager.queue(token,mapping,sync=bool(app.config.get('TESTING_SYNC_LOOKUPS')))
            return redirect(url_for('lookups.detail',token=token))
        headers=[];examples=[];sheets=[];sheet=None;cpf_col=0
        if job['estado']=='mapeamento' and not manager.expired(job):
            with manager.file_access(token):
                headers,examples,sheets,sheet=inspect_file(manager.source_path(job),request.args.get('aba') or None)
            mapping=guess(headers)
            cpf_col=mapping.index('cpf') if 'cpf' in mapping else 0
        return render_template('lookup_detail.html',job=job,filters=filters,headers=headers,examples=examples,sheets=sheets,sheet=sheet,
            cpf_col=cpf_col,summary=json.loads(job['resumo'] or '{}'),state=state_of(job),active=manager.active(token),groups=lookup_groups(filters))

    @pages.post('/consultas-planilha/<token>/retomar')
    @required(edit=True)
    def resume(token):
        job=owned(token)
        if manager.expired(job):raise LookupExpired('Os arquivos desta consulta expiraram após 3 dias. Envie a planilha novamente.')
        if manager.active(token) or job['estado'] not in ('erro','fila','processando','interrompida') or not job['mapeamento']:
            raise ValueError('Esta consulta não pode ser retomada agora.')
        audit('retomar_consulta_planilha','planilha',0,after={'consulta':token});db().commit()
        manager.queue(token,json.loads(job['mapeamento']),states=('erro','fila','processando','interrompida'),sync=bool(app.config.get('TESTING_SYNC_LOOKUPS')))
        return redirect(url_for('lookups.detail',token=token))

    @pages.get('/consultas-planilha/<token>/status')
    @required(edit=True)
    def status(token):
        job=owned(token)
        return jsonify(estado=state_of(job),ativo=manager.active(token),progresso=job['progresso'],total=job['total'],fase=job['fase'])

    @pages.get('/consultas-planilha/status')
    @required(edit=True)
    def batch_status():
        tokens=request.args.getlist('id')
        if len(tokens)>20 or any(not re.fullmatch('[a-f0-9]{32}',token) for token in tokens):abort(400)
        jobs=manager.store.many(sorted(set(tokens)))
        if g.user['perfil']!='ADM' and any(job['usuario_id']!=g.user['id'] for job in jobs):abort(403)
        return jsonify(jobs=[{'token':job['token'],'estado':state_of(job),'fase':job['fase'],'progresso':job['progresso'],'total':job['total']} for job in jobs])

    @pages.get('/consultas-planilha/<token>/resultado.xlsx')
    @required(edit=True)
    def download(token):
        job=owned(token)
        with manager.file_access(token):
            if job['estado']!='concluida' or not manager.result_path(token).is_file():abort(404)
            audit('baixar_consulta_planilha','planilha',0,after={'consulta':token});db().commit()
            response=send_file(manager.result_path(token),as_attachment=True,download_name='consulta_base_'+token[:8]+'.xlsx',mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        return response

    app.register_blueprint(pages)
