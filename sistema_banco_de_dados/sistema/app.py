import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import pymysql
from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import Database
from pagination import page_numbers
from export_csv import stream_clients
from home_summary import HomeSummary

ROLES = {'ADM': 'Administração', 'TI': 'Equipe de TI', 'ATENDIMENTO': 'Atendimento'}
CONTACTS = {
    'telefone': ('telefones', [('numero', 'Telefone', 11), ('situacao', 'Situação', 20)]),
    'email': ('emails', [('email', 'E-mail', 320), ('situacao', 'Situação', 20)]),
    'endereco': ('enderecos', [(x, y, n) for x, y, n in [
        ('tipo', 'Tipo de via', 100), ('titulo', 'Título', 100),
        ('logradouro', 'Logradouro', 255), ('numero', 'Número', 50),
        ('complemento', 'Complemento', 255), ('bairro', 'Bairro', 150),
        ('cidade', 'Cidade', 150), ('uf', 'UF', 2), ('cep', 'CEP', 8)]])}
PHONE_STATUS = ('nao_verificado', 'confirmado', 'invalido', 'inativo')


def cpf_valid(value):
    if not re.fullmatch(r'[0-9]{11}', value) or len(set(value)) == 1:
        return False
    return all(int(value[n]) == (sum(int(value[i]) * (n + 1 - i)
               for i in range(n)) * 10 % 11) % 10 for n in (9, 10))


def digits(value):
    return re.sub(r'[^0-9]', '', value or '')


def normalize_phone(value):
    if any(character.isalpha() for character in value or ''):
        raise ValueError('Informe o telefone com DDD, usando somente números e pontuação.')
    number = digits(value)
    if len(number) in (12, 13) and number.startswith('55'):
        number = number[2:]
    if len(number) not in (10, 11) or number[:2].startswith('0') or len(set(number)) == 1:
        raise ValueError('Informe um telefone com DDD e 10 ou 11 dígitos.')
    return number


def text_field(form, name, maximum, required=False):
    value = ' '.join(form.get(name, '').strip().split())
    if len(value) > maximum:
        raise ValueError(f'O campo {name} excede {maximum} caracteres.')
    if required and not value:
        raise ValueError('Preencha os campos obrigatórios.')
    return value or None


def client_fields(form):
    raw = form.get('cpf', '').strip()
    if any(character.isalpha() for character in raw) or not cpf_valid(digits(raw)):
        raise ValueError('Informe um CPF válido com os 11 dígitos, incluindo os zeros iniciais.')
    birth = form.get('data_nascimento', '').strip() or None
    if birth:
        try:
            parsed = date.fromisoformat(birth)
            if parsed > datetime.now(timezone(timedelta(hours=-3))).date() or parsed.year < 1800:
                raise ValueError()
        except ValueError:
            raise ValueError('Confira a data de nascimento.')
    sex = form.get('sexo_origem', '').strip() or None
    if sex not in (None, 'M', 'F', 'I'):
        raise ValueError('Código de sexo inválido.')
    return {'cpf': digits(raw), 'nome': text_field(form, 'nome', 255, True),
            'data_nascimento': birth, 'nome_mae': text_field(form, 'nome_mae', 255), 'sexo_origem': sex}


def create_app(config):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=config['secret_key'], SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
                      MAX_CONTENT_LENGTH=52 * 1024 * 1024, DEMO=config.get('demo', False),
                      FEATURES_V2=config.get('features_v2',config.get('demo',False)),
                      FEATURES_V3=config.get('features_v3',config.get('demo',False)))

    def db():
        if 'db' not in g:
            heavy=request.method=='POST' and request.endpoint in ('features.import_delete','features.import_restore','features.import_purge')
            g.db = Database({**config,'read_timeout':90} if heavy else config)
        return g.db

    def audit(action, entity, entity_id, before=None, after=None, reason=None, client_id=None):
        db().execute('INSERT INTO app_auditoria '
                     '(usuario_id, cliente_id, acao, entidade, entidade_id, antes, depois, motivo) '
                     'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                     (g.user['id'], client_id, action, entity, entity_id,
                      json.dumps(before, ensure_ascii=False, default=str) if before else None,
                      json.dumps(after, ensure_ascii=False, default=str) if after else None, reason))

    def required(edit=False, manage=False):
        def decorate(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                if not g.user:
                    return redirect(url_for('login'))
                if edit and g.user['perfil'] not in ('ADM', 'TI'):
                    abort(403)
                if manage and not (g.user['perfil'] == 'ADM' or
                                   (g.user['perfil'] == 'TI' and g.user['gerenciar_usuarios'])):
                    abort(403)
                return fn(*args, **kwargs)
            return wrapper
        return decorate

    def locked_client(client_id, allow_deleted=False):
        row = db().one('SELECT * FROM clientes WHERE id=%s FOR UPDATE', (client_id,))
        if not row:
            abort(404)
        if row['excluido_em'] and not allow_deleted:
            raise ValueError('Restaure o cliente antes de alterar seus dados.')
        if str(row['app_versao']) != request.form.get('versao'):
            raise ValueError('Outra pessoa atualizou esta ficha. Recarregue a página e confira as alterações antes de salvar.')
        return row

    def bump(client_id):
        db().execute('UPDATE clientes SET app_versao=app_versao+1, atualizado_em=CURRENT_TIMESTAMP '
                     'WHERE id=%s', (client_id,))

    @app.before_request
    def before():
        g.user = None
        if request.endpoint == 'static':
            return
        if request.endpoint != 'features.upload' and request.content_length and request.content_length>64*1024:
            abort(413)
        if request.method == 'POST':
            token = request.form.get('csrf', '')
            if not token or not secrets.compare_digest(token, session.get('csrf', '')):
                abort(400, description='Sua sessão expirou. Recarregue a página antes de tentar novamente.')
        if session.get('uid'):
            user = db().one('SELECT id,nome,login,perfil,ativo,gerenciar_usuarios,auth_versao '
                            'FROM app_usuarios WHERE id=%s', (session['uid'],))
            if user and user['ativo'] and user['auth_versao'] == session.get('auth_versao'):
                g.user = user
            else:
                session.clear()
        session.setdefault('csrf', secrets.token_urlsafe(32))

    @app.teardown_appcontext
    def close(error):
        connection = g.pop('db', None)
        if connection:
            try:connection.rollback()
            except (pymysql.MySQLError,sqlite3.Error):pass
            finally:
                try:connection.close()
                except (pymysql.MySQLError,sqlite3.Error):pass

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.context_processor
    def context():
        editor = bool(g.get('user') and g.user['perfil'] in ('ADM', 'TI'))
        manage = bool(g.get('user') and (g.user['perfil'] == 'ADM' or
                      (g.user['perfil'] == 'TI' and g.user['gerenciar_usuarios'])))
        categories = db().all('SELECT id,nome FROM app_categorias WHERE ativo=1 ORDER BY nome') if app.config['FEATURES_V2'] and g.get('user') and not g.get('database_failed') else []
        return {'user': g.get('user'), 'csrf': session.get('csrf'), 'editor': editor,
                'manage_users': manage, 'roles': ROLES, 'demo': config.get('demo', False),
                'contact_types': CONTACTS, 'phone_status': PHONE_STATUS,
                'features_v2':app.config['FEATURES_V2'],'available_categories':categories}

    @app.template_filter('cpf')
    def cpf_format(value):
        return f'{value[:3]}.{value[3:6]}.{value[6:9]}-{value[9:]}' if value and len(value) == 11 else value

    @app.template_filter('datebr')
    def datebr(value):
        if not value:
            return 'Não informado'
        return str(value)[:10][8:10] + '/' + str(value)[5:7] + '/' + str(value)[:4]

    @app.template_filter('moment')
    def moment(value):
        if not value:
            return 'Não informado'
        if not isinstance(value, datetime):
            try:
                value = datetime.fromisoformat(str(value))
            except ValueError:
                return str(value)
        if config.get('sqlite_path'):
            value -= timedelta(hours=3)
        return value.strftime('%d/%m/%Y %H:%M')

    @app.template_filter('import_status')
    def import_status(value):
        return {'mapeamento':'Conferir colunas','preparando':'Analisando arquivo','revisao':'Aguardando revisão',
                'importando':'Importando','concluida':'Concluída','erro':'Interrompida',
                'pronto':'Pronto para importar','pendente':'Revisar','aplicado':'Processado'}.get(value,value)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if g.user:
            return redirect(url_for('home'))
        if request.method == 'POST':
            login_name = request.form.get('login', '').strip().lower()[:80]
            # Persistência no banco evita perder o limite ao reiniciar o sistema.
            cutoff = "datetime('now','-15 minutes')" if db().sqlite else 'DATE_SUB(CURRENT_TIMESTAMP, INTERVAL 15 MINUTE)'
            attempts = db().one(f'SELECT COUNT(*) AS n FROM app_login_tentativas '
                                f'WHERE criado_em > {cutoff} AND (login=%s OR origem=%s)',
                                (login_name, request.remote_addr or 'local'))['n']
            if attempts >= 10:
                flash('Muitas tentativas. Aguarde 15 minutos para tentar novamente.', 'error')
                return render_template('login.html'), 429
            user = db().one('SELECT * FROM app_usuarios WHERE login=%s', (login_name,))
            valid = check_password_hash(user['senha_hash'] if user else app.config['DUMMY_HASH'],
                                        request.form.get('senha', ''))
            if user and user['ativo'] and valid:
                session.clear()
                session.update(uid=user['id'], auth_versao=user['auth_versao'], csrf=secrets.token_urlsafe(32))
                session.permanent = True
                db().execute('DELETE FROM app_login_tentativas WHERE login=%s', (login_name,))
                db().commit()
                return redirect(url_for('home'))
            db().execute('INSERT INTO app_login_tentativas (login,origem) VALUES (%s,%s)',
                         (login_name, request.remote_addr or 'local'))
            db().commit()
            flash('Login ou senha incorretos.', 'error')
        return render_template('login.html')

    @app.post('/sair')
    @required()
    def logout():
        session.clear()
        return redirect(url_for('login'))

    home_summary=HomeSummary(config)
    if not config.get('sqlite_path'):home_summary.background()

    @app.get('/inicio')
    @required()
    def home():
        stats=home_summary.get()
        jobs=[];activities=[]
        if g.user['perfil'] in ('ADM','TI'):
            if app.config['FEATURES_V2'] and app.config['FEATURES_V3']:
                jobs=db().all("SELECT i.id,i.arquivo,i.estado,i.criado_em,(SELECT COUNT(*) FROM app_importacao_itens x WHERE x.importacao_id=i.id AND x.estado='pendente') AS pendencias FROM app_importacoes i WHERE i.excluido_em IS NULL ORDER BY i.id DESC LIMIT 5")
            activities=db().all('SELECT a.id,a.acao,a.entidade,a.criado_em,u.nome AS autor FROM app_auditoria a '
                                'LEFT JOIN app_usuarios u ON u.id=a.usuario_id ORDER BY a.id DESC LIMIT 5')
        return render_template('home.html',stats=stats,jobs=jobs,activities=activities,
                               today=datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y'))

    @app.get('/')
    @required()
    def index():
        query = request.args.get('q', '').strip()[:255]
        deleted = request.args.get('lixeira') == '1' and g.user['perfil'] in ('ADM', 'TI')
        filters = {key: request.args.get(key, 'todos') for key in ('nome', 'telefone', 'email')}
        filters = {key: value if value in ('todos', 'com', 'sem') else 'todos' for key, value in filters.items()}
        uf = request.args.get('uf', '').strip().upper()
        if not re.fullmatch('[A-Z]{2}', uf):
            uf = ''
        orders = {'nome': "(NULLIF(TRIM(c.nome),'') IS NULL), c.nome, c.id DESC",
                  'recentes': 'c.id DESC', 'antigos': 'c.id ASC'}
        order = request.args.get('ordem', 'nome')
        if order not in orders:
            order = 'nome'
        page = max(1,request.args.get('pagina',1,type=int))
        page_size=request.args.get('por_pagina',25,type=int)
        if page_size not in (25,50,100,200):page_size=25
        base = 'c.excluido_em IS ' + ('NOT NULL' if deleted else 'NULL')
        params = []
        category = request.args.get('categoria','todos') if app.config['FEATURES_V2'] else 'todos'
        if category!='sem' and not category.isdigit():category='todos'
        if category=='sem':
            base += ' AND NOT EXISTS (SELECT 1 FROM app_cliente_categorias l JOIN app_categorias cat ON cat.id=l.categoria_id WHERE l.cliente_id=c.id AND cat.ativo=1)'
        elif category.isdigit():
            base += ' AND EXISTS (SELECT 1 FROM app_cliente_categorias l JOIN app_categorias cat ON cat.id=l.categoria_id WHERE l.cliente_id=c.id AND cat.ativo=1 AND cat.id=%s)'
            params.append(int(category))
        if filters['nome'] != 'todos':
            base += " AND NULLIF(TRIM(c.nome),'') IS " + ('NOT NULL' if filters['nome'] == 'com' else 'NULL')
        for key, table in (('telefone', 'telefones'), ('email', 'emails')):
            if filters[key] != 'todos':
                base += ' AND c.id '+('NOT IN' if filters[key]=='sem' else 'IN')
                base += f' (SELECT contato.cliente_id FROM {table} contato WHERE contato.excluido_em IS NULL AND contato.cliente_id IS NOT NULL)'
        if uf:
            base += ' AND EXISTS (SELECT 1 FROM enderecos e WHERE e.cliente_id=c.id AND e.excluido_em IS NULL AND e.uf=%s)'
            params.append(uf)
        if query:
            numeric = digits(query)
            if numeric and re.fullmatch(r'[0-9+().\s-]+', query):
                numbers = [numeric]
                if len(numeric) in (12, 13) and numeric.startswith('55'):
                    numbers.append(numeric[2:])
                marks = ','.join(['%s'] * len(numbers))
                base += f' AND c.id IN (SELECT match_id FROM (SELECT id AS match_id FROM clientes WHERE cpf=%s UNION '
                base += f'SELECT cliente_id AS match_id FROM telefones WHERE excluido_em IS NULL AND numero IN ({marks})) search_matches)'
                params.extend([numeric] + numbers)
            else:
                # Busca por início do nome evita varredura irrestrita por substring.
                escaped = query.replace('!', '!!').replace('%', '!%').replace('_', '!_')
                base += " AND c.nome LIKE %s ESCAPE '!'"
                params.append(escaped + '%')
        if request.endpoint=='export_clients':
            audit('exportar_csv','clientes',0,after={'filtros':dict(request.args),'ordem':'id'})
            db().commit()
            response=Response(stream_clients(config,base,params,app.config['FEATURES_V2']),content_type='text/csv; charset=utf-8')
            response.headers['Content-Disposition']='attachment; filename="clientes_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv"'
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
            return response
        total = db().one(f'SELECT COUNT(*) AS n FROM clientes c WHERE {base}', params)['n']
        pages = max(1,(total+page_size-1)//page_size)
        page = min(page, pages)
        rows = db().all(f'SELECT c.id,c.cpf,c.nome,c.data_nascimento,c.excluido_em, '
                         '(SELECT COUNT(*) FROM telefones t WHERE t.cliente_id=c.id '
                         'AND t.excluido_em IS NULL) AS telefones '
                         f'FROM clientes c WHERE {base} ORDER BY {orders[order]} LIMIT %s OFFSET %s',
                         params + [page_size,(page-1)*page_size])
        paging = dict(q=query, lixeira=1 if deleted else 0, uf=uf, ordem=order, categoria=category,por_pagina=page_size, **filters)
        active_filters = sum(value != 'todos' for value in filters.values()) + bool(uf) + (category!='todos')
        return render_template('index.html', rows=rows, total=total, query=query,
                               deleted=deleted, page=page, pages=pages, filters=filters,
                               uf=uf, order=order, paging=paging, active_filters=active_filters,category=category,
                               page_size=page_size,page_numbers=page_numbers(page,pages),pagination_endpoint='index',
                               result_start=(page-1)*page_size+1 if total else 0,result_end=min(page*page_size,total))

    @app.get('/clientes/exportar.csv')
    @required(edit=True)
    def export_clients():
        return index()

    @app.get('/configuracoes/logs')
    @required(edit=True)
    def system_logs():
        kind=request.args.get('tipo','atividades')
        if kind not in ('atividades','acessos','tecnico'):kind='atividades'
        page=max(1,request.args.get('pagina',1,type=int))
        search=request.args.get('q','').strip()[:100]
        action=request.args.get('acao','').strip()[:40]
        author=request.args.get('autor',type=int)
        conditions=[];params=[]
        date_filters={}
        for field,operator in (('inicio','>='),('fim','<')):
            value=request.args.get(field,'').strip()
            if not value:date_filters[field]='';continue
            try:
                parsed=date.fromisoformat(value)
                boundary=datetime.combine(parsed,datetime.min.time())+(timedelta(days=1) if field=='fim' else timedelta())
                if db().sqlite:boundary+=timedelta(hours=3)
            except (ValueError,OverflowError):raise ValueError('Confira as datas dos filtros de logs.')
            date_filters[field]=value
            conditions.append(f'a.criado_em {operator} %s');params.append(boundary.strftime('%Y-%m-%d %H:%M:%S'))
        if date_filters['inicio'] and date_filters['fim'] and date_filters['inicio']>date_filters['fim']:
            raise ValueError('A data inicial deve ser anterior ou igual à data final.')
        technical=[];rows=[];authors=[];actions=[]
        if kind=='tecnico':
            from pathlib import Path
            log=Path(__file__).with_name('logs')/('demonstracao-erros.log' if config.get('demo') else 'erros.log')
            if log.exists():
                with log.open('rb') as stream:
                    stream.seek(0,2);length=stream.tell();stream.seek(max(0,length-65536));content=stream.read().decode('utf-8','replace')
                for line in content.splitlines()[-150:]:
                    if re.search(r'password|passwd|senha|secret|token|authorization',line,re.I):
                        line='[Linha omitida por conter referência a credenciais.]'
                    line=re.sub(r'(?i)(mysql://[^:]+:)[^@]+@',r'\1[oculto]@',line)
                    technical.append(line)
            total=len(technical);pages=page=1
        else:
            if kind=='atividades':
                source='app_auditoria a LEFT JOIN app_usuarios u ON u.id=a.usuario_id'
                if search:
                    escaped=search.replace('!','!!').replace('%','!%').replace('_','!_')
                    conditions.append("(u.nome LIKE %s ESCAPE '!' OR a.entidade LIKE %s ESCAPE '!' OR a.motivo LIKE %s ESCAPE '!')")
                    params.extend(['%'+escaped+'%']*3)
                if action:conditions.append('a.acao=%s');params.append(action)
                if author:conditions.append('a.usuario_id=%s');params.append(author)
                fields='a.id,a.cliente_id,a.acao,a.entidade,a.entidade_id,a.motivo,a.criado_em,u.nome AS autor'
                authors=db().all('SELECT id,nome FROM app_usuarios ORDER BY nome')
                actions=db().all('SELECT DISTINCT acao FROM app_auditoria ORDER BY acao')
            else:
                source='app_login_tentativas a'
                if search:
                    escaped=search.replace('!','!!').replace('%','!%').replace('_','!_')
                    conditions.append("a.login LIKE %s ESCAPE '!'");params.append('%'+escaped+'%')
                fields='a.id,a.login,a.origem,a.criado_em'
            where=' WHERE '+' AND '.join(conditions) if conditions else ''
            total=db().one(f'SELECT COUNT(*) AS n FROM {source}{where}',params)['n']
            pages=max(1,(total+49)//50);page=min(page,pages)
            rows=db().all(f'SELECT {fields} FROM {source}{where} ORDER BY a.id DESC LIMIT %s OFFSET %s',params+[50,(page-1)*50])
        paging={'tipo':kind,'q':search,'acao':action,'autor':author or '',**date_filters}
        return render_template('logs.html',kind=kind,rows=rows,technical=technical,total=total,page=page,pages=pages,
                               page_size=50,page_numbers=page_numbers(page,pages),paging=paging,pagination_endpoint='system_logs',
                               result_start=(page-1)*50+1 if total else 0,result_end=min(page*50,total),
                               search=search,action=action,author=author,authors=authors,actions=actions,date_filters=date_filters)

    @app.route('/clientes/novo', methods=['GET', 'POST'])
    @required()
    def new_client():
        if request.method == 'POST':
            data = client_fields(request.form)
            number = request.form.get('telefone', '').strip()
            number = normalize_phone(number) if number else None
            email=text_field(request.form,'email',320)
            if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise ValueError('Confira o formato do e-mail.')
            existing = db().one('SELECT * FROM clientes WHERE cpf=%s FOR UPDATE', (data['cpf'],))
            if existing:
                if existing['excluido_em'] and g.user['perfil'] == 'ATENDIMENTO':
                    raise ValueError('CPF reservado por um cadastro excluído. Solicite a revisão à ADM ou TI.')
                if existing['excluido_em']:
                    flash('CPF na lixeira. Restaure o cadastro antes de atualizar.','info')
                    return redirect(url_for('detail',client_id=existing['id']))
                changes={key:value for key,value in data.items() if key!='cpf' and value and not existing.get(key)} if g.user['perfil'] in ('ADM','TI') else {}
                for key,value in changes.items():
                    db().execute(f'UPDATE clientes SET {key}=%s WHERE id=%s',(value,existing['id']))
                added=[]
                for table,column,value in [('telefones','numero',number),('emails','email',email if g.user['perfil'] in ('ADM','TI') else None)]:
                    if value and not db().one(f'SELECT id FROM {table} WHERE cliente_id=%s AND {column}=%s',(existing['id'],value)):
                        cur=db().execute(f'INSERT INTO {table} (cliente_id,{column}) VALUES (%s,%s)',(existing['id'],value))
                        audit('adicionar','telefone' if table=='telefones' else 'email',cur.lastrowid,after={column:value},client_id=existing['id']);added.append(column)
                if changes or added:
                    bump(existing['id'])
                    audit('completar_cadastro','cliente',existing['id'],before={key:existing[key] for key in changes},after=changes,client_id=existing['id'])
                    db().commit()
                flash('CPF já cadastrado: informações vazias permitidas e contatos novos foram aproveitados. Dados existentes não foram substituídos.' if g.user['perfil'] in ('ADM','TI') else 'CPF já cadastrado. Apenas telefone novo pode ser acrescentado por Atendimento; solicite à ADM/TI o preenchimento dos outros campos.', 'info')
                return redirect(url_for('detail', client_id=existing['id']))
            columns = list(data)
            cur = db().execute(f"INSERT INTO clientes ({','.join(columns)}) VALUES ({','.join(['%s']*len(columns))})", tuple(data.values()))
            client_id = cur.lastrowid
            audit('cadastrar', 'cliente', client_id, after=data, client_id=client_id)
            category_id=request.form.get('categoria_id',type=int) if app.config['FEATURES_V2'] else None
            if category_id:
                if not db().one('SELECT id FROM app_categorias WHERE id=%s AND ativo=1',(category_id,)):
                    raise ValueError('Escolha uma categoria ativa.')
                db().execute('INSERT INTO app_cliente_categorias (cliente_id,categoria_id) VALUES (%s,%s)',(client_id,category_id))
                audit('vincular_categoria','cliente',client_id,after={'categoria_id':category_id},client_id=client_id)
            if number:
                cur = db().execute('INSERT INTO telefones (cliente_id,numero) VALUES (%s,%s)', (client_id, number))
                audit('adicionar', 'telefone', cur.lastrowid, after={'numero': number}, client_id=client_id)
            if email:
                cur=db().execute('INSERT INTO emails (cliente_id,email) VALUES (%s,%s)',(client_id,email))
                audit('adicionar','email',cur.lastrowid,after={'email':email},client_id=client_id)
            db().commit()
            flash('Cliente cadastrado.', 'success')
            return redirect(url_for('detail', client_id=client_id))
        return render_template('client_form.html', client={}, creating=True)

    @app.get('/clientes/<int:client_id>')
    @required()
    def detail(client_id):
        client = db().one('SELECT * FROM clientes WHERE id=%s', (client_id,))
        if not client or (client['excluido_em'] and g.user['perfil'] == 'ATENDIMENTO'):
            abort(404)
        contacts = {}
        for kind, (table, fields) in CONTACTS.items():
            contacts[kind] = db().all(f'SELECT * FROM {table} WHERE cliente_id=%s ORDER BY id', (client_id,))
        history = db().all('SELECT a.id,a.acao,a.entidade,a.entidade_id,a.criado_em,a.motivo,u.nome AS autor '
                           'FROM app_auditoria a LEFT JOIN app_usuarios u ON u.id=a.usuario_id '
                           'WHERE a.cliente_id=%s ORDER BY a.id DESC LIMIT 30', (client_id,)) if g.user['perfil'] in ('ADM', 'TI') else []
        categories=db().all('SELECT cat.id,cat.nome FROM app_categorias cat JOIN app_cliente_categorias l ON l.categoria_id=cat.id WHERE l.cliente_id=%s AND cat.ativo=1 ORDER BY cat.nome',(client_id,)) if app.config['FEATURES_V2'] else []
        origins=[]
        if app.config['FEATURES_V2'] and g.user['perfil'] in ('ADM','TI'):
            receipts=db().all("SELECT i.id AS lote,i.arquivo,i.mapeamento,cat.nome AS categoria,x.originais,x.linhas "
                              "FROM app_importacao_itens x JOIN app_importacoes i ON i.id=x.importacao_id "
                              "LEFT JOIN app_categorias cat ON cat.id=i.categoria_id WHERE x.cliente_id_resultado=%s AND x.estado='aplicado' ORDER BY x.id DESC LIMIT 10",(client_id,))
            for receipt in receipts:
                mapping=json.loads(receipt['mapeamento']);source=json.loads(receipt['originais']);lines=json.loads(receipt['linhas'])
                extra=[]
                for index,(header,target) in enumerate(zip(mapping['headers'],mapping['targets'])):
                    if target=='extra':
                        values=list(dict.fromkeys(row[index] for row in source if index<len(row) and row[index]))
                        if values:extra.append((header,values))
                origins.append({'lote':receipt['lote'],'arquivo':receipt['arquivo'],'categoria':receipt['categoria'] or 'Sem categoria','extra':extra,'linhas':lines})
        return render_template('detail.html', client=client, contacts=contacts, history=history,client_categories=categories,origins=origins)

    @app.route('/clientes/<int:client_id>/editar', methods=['GET', 'POST'])
    @required(edit=True)
    def edit_client(client_id):
        if request.method == 'POST':
            before = locked_client(client_id)
            data = client_fields(request.form)
            db().execute('UPDATE clientes SET cpf=%s,nome=%s,data_nascimento=%s,nome_mae=%s,'
                         'sexo_origem=%s,app_versao=app_versao+1 WHERE id=%s',
                         tuple(data.values()) + (client_id,))
            audit('atualizar', 'cliente', client_id, before={k: before[k] for k in data},
                  after=data, client_id=client_id)
            db().commit()
            flash('Cadastro atualizado.', 'success')
            return redirect(url_for('detail', client_id=client_id))
        client = db().one('SELECT * FROM clientes WHERE id=%s AND excluido_em IS NULL', (client_id,))
        if not client:
            abort(404)
        return render_template('client_form.html', client=client, creating=False)

    @app.post('/clientes/<int:client_id>/<action>')
    @required(edit=True)
    def client_state(client_id, action):
        if action not in ('excluir', 'restaurar'):
            abort(404)
        before = locked_client(client_id, allow_deleted=True)
        if (action == 'excluir') == bool(before['excluido_em']):
            raise ValueError('Este cadastro já está nessa situação. Atualize a página.')
        reason = text_field(request.form, 'motivo', 500, g.user['perfil']!='ADM')
        value = 'CURRENT_TIMESTAMP' if action == 'excluir' else 'NULL'
        db().execute(f'UPDATE clientes SET excluido_em={value},app_versao=app_versao+1 WHERE id=%s', (client_id,))
        audit(action, 'cliente', client_id, before={'excluido_em': before['excluido_em']},
              after={'excluido': action == 'excluir'}, reason=reason, client_id=client_id)
        db().commit()
        flash('Cliente movido para a lixeira.' if action == 'excluir' else 'Cliente restaurado.', 'success')
        return redirect(url_for('detail', client_id=client_id))

    @app.route('/clientes/<int:client_id>/contatos/<kind>/<action>', methods=['GET', 'POST'])
    @app.route('/clientes/<int:client_id>/contatos/<kind>/<int:item_id>/<action>', methods=['GET', 'POST'])
    @required()
    def contact(client_id, kind, action, item_id=None):
        if kind not in CONTACTS or action not in ('adicionar', 'editar', 'excluir', 'restaurar'):
            abort(404)
        if g.user['perfil'] == 'ATENDIMENTO' and not (kind == 'telefone' and action == 'adicionar'):
            abort(403)
        if (action == 'adicionar') != (item_id is None):
            abort(404)
        if request.method == 'GET' and action in ('excluir', 'restaurar'):
            abort(405)
        table, fields = CONTACTS[kind]
        client = locked_client(client_id) if request.method == 'POST' else db().one(
            'SELECT * FROM clientes WHERE id=%s AND excluido_em IS NULL', (client_id,))
        if not client:
            abort(404)
        item = db().one(f'SELECT * FROM {table} WHERE id=%s AND cliente_id=%s', (item_id, client_id)) if item_id else {}
        if item_id and not item:
            abort(404)
        if request.method == 'POST':
            reason = None
            data = None
            if action in ('excluir', 'restaurar'):
                if (action == 'excluir') == bool(item['excluido_em']):
                    raise ValueError('O contato já está nessa situação. Atualize a página.')
                reason = text_field(request.form, 'motivo', 500, g.user['perfil']!='ADM')
                value = 'CURRENT_TIMESTAMP' if action == 'excluir' else 'NULL'
                db().execute(f'UPDATE {table} SET excluido_em={value} WHERE id=%s AND cliente_id=%s', (item_id, client_id))
                data = {'excluido': action == 'excluir'}
            else:
                if item and item['excluido_em']:
                    raise ValueError('Restaure o contato antes de editá-lo.')
                data = {key: text_field(request.form, key,
                        40 if kind == 'telefone' and key == 'numero' else maximum)
                        for key, label, maximum in fields}
                if kind == 'telefone':
                    data['numero'] = normalize_phone(request.form.get('numero', ''))
                    if g.user['perfil'] == 'ATENDIMENTO' and request.form.get('situacao', 'nao_verificado') != 'nao_verificado':
                        abort(403)
                    data['situacao'] = 'nao_verificado' if g.user['perfil'] == 'ATENDIMENTO' else data['situacao'] or 'nao_verificado'
                    if data['situacao'] not in PHONE_STATUS:
                        raise ValueError('Situação de telefone inválida.')
                    shared = db().one('SELECT id FROM telefones WHERE numero=%s AND cliente_id<>%s AND excluido_em IS NULL LIMIT 1',
                                      (data['numero'], client_id))
                    if shared:
                        flash('Atenção: esse número também está associado a outro cliente. Confira a titularidade.', 'info')
                elif kind == 'email':
                    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', data['email'] or ''):
                        raise ValueError('Confira o formato do e-mail.')
                    if data['situacao'] not in PHONE_STATUS:
                        raise ValueError('Situação de e-mail inválida.')
                else:
                    if not data['logradouro']:
                        raise ValueError('Informe o logradouro.')
                    if data['uf'] and not re.fullmatch('[A-Za-z]{2}', data['uf']):
                        raise ValueError('Informe a UF com duas letras.')
                    if data['uf']:
                        data['uf'] = data['uf'].upper()
                    if data['cep'] and not re.fullmatch('[0-9]{8}', data['cep']):
                        raise ValueError('Informe o CEP com oito dígitos, sem pontuação.')
                if action == 'adicionar':
                    keys = list(data)
                    cur = db().execute(f"INSERT INTO {table} (cliente_id,{','.join(keys)}) VALUES ({','.join(['%s']*(len(keys)+1))})",
                                       (client_id,) + tuple(data.values()))
                    item_id = cur.lastrowid
                else:
                    db().execute(f"UPDATE {table} SET {','.join(k+'=%s' for k in data)} WHERE id=%s AND cliente_id=%s",
                                 tuple(data.values()) + (item_id, client_id))
            audit(action, kind, item_id, before=item or None, after=data, reason=reason, client_id=client_id)
            bump(client_id)
            db().commit()
            flash('Contato atualizado.' if action == 'editar' else 'Operação concluída.', 'success')
            return redirect(url_for('detail', client_id=client_id))
        return render_template('contact_form.html', client=client, item=item, kind=kind, action=action, fields=fields)

    @app.route('/usuarios', methods=['GET', 'POST'])
    @required(manage=True)
    def users():
        if request.method == 'POST':
            name = text_field(request.form, 'nome', 150, True)
            login_name = request.form.get('login', '').strip().lower()
            role = request.form.get('perfil', '')
            password = request.form.get('senha', '')
            if not re.fullmatch(r'[a-z0-9._-]{3,80}', login_name) or role not in ROLES:
                raise ValueError('Use um login de 3 a 80 letras sem acento, números, ponto, hífen ou sublinhado e escolha um perfil.')
            if len(password) < 12 or len(password) > 200:
                raise ValueError('A senha precisa ter entre 12 e 200 caracteres.')
            # Só ADM pode conceder administração de usuários a outro TI ou criar ADM.
            if g.user['perfil'] != 'ADM' and role != 'ATENDIMENTO':
                abort(403)
            allow_manage = int(g.user['perfil'] == 'ADM' and role == 'TI' and request.form.get('gerenciar_usuarios') == '1')
            cur = db().execute('INSERT INTO app_usuarios (nome,login,senha_hash,perfil,gerenciar_usuarios) VALUES (%s,%s,%s,%s,%s)',
                               (name, login_name, generate_password_hash(password), role, allow_manage))
            audit('criar_usuario', 'usuario', cur.lastrowid, after={'nome': name, 'login': login_name, 'perfil': role, 'gerenciar_usuarios': allow_manage})
            db().commit()
            flash('Usuário criado. Entregue a senha diretamente à pessoa.', 'success')
            return redirect(url_for('users'))
        rows = db().all('SELECT id,nome,login,perfil,ativo,gerenciar_usuarios,auth_versao FROM app_usuarios ORDER BY nome')
        return render_template('users.html', users=rows)

    @app.post('/usuarios/<int:user_id>/permissoes')
    @required(manage=True)
    def user_permissions(user_id):
        if g.user['perfil']!='ADM':abort(403)
        admins=db().all("SELECT id FROM app_usuarios WHERE perfil='ADM' AND ativo=1 ORDER BY id FOR UPDATE")
        row=db().one('SELECT id,perfil,gerenciar_usuarios,auth_versao,ativo FROM app_usuarios WHERE id=%s FOR UPDATE',(user_id,))
        if not row:abort(404)
        if request.form.get('versao',type=int)!=row['auth_versao']:
            raise ValueError('As permissões mudaram. Atualize a página antes de salvar.')
        role=request.form.get('perfil','')
        if role not in ROLES:raise ValueError('Escolha um perfil válido.')
        if row['perfil']=='ADM' and row['ativo'] and role!='ADM' and len(admins)<=1:
            raise ValueError('É necessário manter pelo menos uma conta ADM ativa.')
        reason=text_field(request.form,'motivo',500,True)
        manage=int(role=='TI' and request.form.get('gerenciar_usuarios')=='1')
        db().execute('UPDATE app_usuarios SET perfil=%s,gerenciar_usuarios=%s,auth_versao=auth_versao+1 WHERE id=%s',(role,manage,user_id))
        audit('alterar_permissoes','usuario',user_id,before=row,after={'perfil':role,'gerenciar_usuarios':manage},reason=reason)
        db().commit()
        if user_id==g.user['id']:
            session.clear();flash('Permissões atualizadas. Entre novamente.','success');return redirect(url_for('login'))
        flash('Permissões atualizadas. A pessoa precisará entrar novamente.','success')
        return redirect(url_for('users'))

    @app.post('/usuarios/<int:user_id>/situacao')
    @required(manage=True)
    def user_state(user_id):
        if user_id == g.user['id']:
            raise ValueError('Você não pode desativar sua própria conta.')
        # Bloqueia alterações concorrentes nas contas administrativas.
        db().all("SELECT id FROM app_usuarios WHERE perfil='ADM' AND ativo=1 FOR UPDATE")
        row = db().one('SELECT id,nome,login,perfil,ativo FROM app_usuarios WHERE id=%s FOR UPDATE', (user_id,))
        if not row:
            abort(404)
        if g.user['perfil'] != 'ADM' and row['perfil'] != 'ATENDIMENTO':
            abort(403)
        if row['perfil'] == 'ADM' and row['ativo']:
            admins = db().one("SELECT COUNT(*) AS n FROM app_usuarios WHERE perfil='ADM' AND ativo=1")['n']
            if admins <= 1:
                raise ValueError('É necessário manter pelo menos uma conta ADM ativa.')
        reason = text_field(request.form, 'motivo', 500, True)
        active = 0 if row['ativo'] else 1
        db().execute('UPDATE app_usuarios SET ativo=%s,auth_versao=auth_versao+1 WHERE id=%s', (active, user_id))
        audit('alterar_acesso', 'usuario', user_id, before=row, after={'ativo': active}, reason=reason)
        db().commit()
        flash('Acesso atualizado.', 'success')
        return redirect(url_for('users'))

    @app.route('/minha-senha', methods=['GET', 'POST'])
    @required()
    def password():
        if request.method == 'POST':
            row = db().one('SELECT senha_hash FROM app_usuarios WHERE id=%s FOR UPDATE', (g.user['id'],))
            new = request.form.get('nova', '')
            if not check_password_hash(row['senha_hash'], request.form.get('atual', '')):
                raise ValueError('A senha atual não confere.')
            if len(new) < 12 or len(new) > 200 or new != request.form.get('confirmacao'):
                raise ValueError('Use entre 12 e 200 caracteres e repita a mesma senha na confirmação.')
            db().execute('UPDATE app_usuarios SET senha_hash=%s,auth_versao=auth_versao+1 WHERE id=%s',
                         (generate_password_hash(new), g.user['id']))
            audit('trocar_senha', 'usuario', g.user['id'])
            db().commit()
            session.clear()
            flash('Senha alterada. Entre novamente.', 'success')
            return redirect(url_for('login'))
        return render_template('password.html')

    @app.get('/configuracoes')
    @required()
    def settings():
        return render_template('settings.html')

    @app.get('/historico/<int:audit_id>')
    @required(edit=True)
    def history(audit_id):
        row = db().one('SELECT a.*,u.nome AS autor FROM app_auditoria a LEFT JOIN app_usuarios u '
                       'ON u.id=a.usuario_id WHERE a.id=%s', (audit_id,))
        if not row:
            abort(404)
        for key in ('antes', 'depois'):
            row[key] = json.loads(row[key]) if row[key] else {}
        return render_template('history.html', entry=row)

    @app.errorhandler(ValueError)
    def validation(error):
        if 'db' in g:
            db().rollback()
        return render_template('error.html', title='Confira os dados', message=str(error)), 400

    @app.errorhandler(pymysql.IntegrityError)
    @app.errorhandler(sqlite3.IntegrityError)
    def duplicate(error):
        if 'db' in g:
            db().rollback()
        return render_template('error.html', title='Cadastro já existente', message=
                               'O CPF, contato, login ou categoria já existe, inclusive entre os excluídos. Confira o registro antes de tentar novamente.'), 409

    @app.errorhandler(pymysql.MySQLError)
    def database_error(error):
        app.logger.error('Falha MySQL (código %s).', error.args[0] if error.args else 'desconhecido')
        g.database_failed=True
        close(error)
        if error.args and error.args[0] in (1205,1213):
            return render_template('error.html',title='Dados em uso por outra operação',message=
                                   'Outra operação está alterando estes registros. Aguarde a conclusão e confira a situação antes de tentar novamente.'),409
        return render_template('error.html', title='Não foi possível acessar o banco', message=
                               'A conexão com o banco foi interrompida. Se você estava salvando ou excluindo, confira a situação do registro antes de repetir a ação.'), 503

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    def http_error(error):
        messages = {400: 'Recarregue a página e tente novamente.', 403: 'Seu perfil não permite essa operação.',
                    404: 'O registro ou a página não foi encontrado.', 405: 'Essa operação precisa ser feita pelo formulário.',
                    413: 'O formulário enviado é muito grande.'}
        return render_template('error.html', title=f'Operação indisponível ({error.code})',
                               message=messages[error.code]), error.code

    @app.errorhandler(500)
    def unexpected(error):
        return render_template('error.html', title='Não foi possível concluir', message=
                               'Recarregue a ficha antes de tentar novamente. Se o erro persistir, procure a TI.'), 500

    app.config['DUMMY_HASH'] = generate_password_hash(secrets.token_urlsafe(32))
    from features import register_features
    register_features(app,config,db,required,audit,locked_client,bump)
    return app
