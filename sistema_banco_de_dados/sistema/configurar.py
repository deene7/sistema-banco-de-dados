import bootstrap
import getpass
import json
import os
import secrets
import sys
from pathlib import Path

import pymysql
from werkzeug.security import generate_password_hash
from security import CONFIG_FILE, protect, restrict_file

BASE = Path(__file__).resolve().parent


def ask_password(label):
    while True:
        password = getpass.getpass(label)
        if not 12 <= len(password) <= 200:
            print('Use entre 12 e 200 caracteres.')
            continue
        if password != getpass.getpass('Repita a senha: '):
            print('As senhas são diferentes. Tente novamente.')
            continue
        return password


def main():
    print('\nCONFIGURAÇÃO DO SISTEMA LOCAL DE CLIENTES\n')
    print('Esta etapa acrescenta contas, histórico e controle de edição ao banco wa_clientes.')
    print('Não importa, exclui ou substitui cadastros existentes.')
    print('As mudanças de estrutura do MySQL são permanentes. Mantenha o backup recente.')
    print('Nenhuma senha precisa ser enviada no chat.\n')
    if CONFIG_FILE.exists():
        print('O sistema já foi configurado. Use INICIAR_SISTEMA.cmd.')
        return
    # Confirma que a proteção da senha funciona antes de alterar o banco.
    probe = secrets.token_urlsafe(16)
    if protect(protect(probe), decrypt=True) != probe:
        raise RuntimeError('Não foi possível proteger a configuração no Windows.')
    if '--janela' in sys.argv:
        from setup_form import collect_setup
        values = collect_setup()
        admin_name, login = values['nome'], values['login']
        app_password, root_password = values['senha'], values['root']
        values.clear()
    else:
        admin_name = input('Seu nome para a primeira conta ADM: ').strip()
        if not admin_name or len(admin_name) > 150:
            raise ValueError('Informe seu nome com até 150 caracteres.')
        import re
        login = input('Seu login (exemplo: seu.nome): ').strip().lower()
        if not re.fullmatch(r'[a-z0-9._-]{3,80}', login):
            raise ValueError('Login inválido: use de 3 a 80 letras sem acentos, números, ponto, hífen ou sublinhado.')
        print('Ao digitar senhas nesta janela de texto, nenhum caractere será mostrado. Digite e pressione Enter.')
        app_password = ask_password('Crie sua senha de acesso ao sistema (mínimo 12 caracteres): ')
        root_password = getpass.getpass('Agora digite a senha root do MySQL: ')
    conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password=root_password,
                           database='wa_clientes', charset='utf8mb4', autocommit=False,
                           cursorclass=pymysql.cursors.DictCursor, connect_timeout=5)
    root_password = None
    service_user = 'wa_app_' + secrets.token_hex(4)
    service_password = secrets.token_urlsafe(40)
    try:
        with conn.cursor() as cur:
            for table in ('clientes', 'telefones', 'emails', 'enderecos'):
                cur.execute('SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=%s AND table_name=%s',
                            ('wa_clientes', table))
                if not cur.fetchone()['n']:
                    raise ValueError(f'A tabela {table} não foi encontrada. Confira o banco antes de configurar.')
            # Uma instalação interrompida não pode criar ADM adicional sem revisão.
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema='wa_clientes' AND table_name='app_usuarios'")
            if cur.fetchone()['n']:
                cur.execute('SELECT COUNT(*) AS n FROM app_usuarios')
                if cur.fetchone()['n']:
                    raise ValueError('Já existem usuários do sistema. A configuração local precisa ser recuperada pela TI; nenhum acesso foi criado.')
            print('\nPreparando a estrutura adicional...')
            for statement in (BASE / 'schema.sql').read_text(encoding='utf-8').split(';'):
                if statement.strip():
                    cur.execute(statement)
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.columns WHERE table_schema='wa_clientes' AND table_name='clientes' AND column_name='app_versao'")
            if not cur.fetchone()['n']:
                cur.execute('ALTER TABLE clientes ADD COLUMN app_versao BIGINT UNSIGNED NOT NULL DEFAULT 1')
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.statistics WHERE table_schema='wa_clientes' AND table_name='clientes' AND index_name='idx_app_nome'")
            if not cur.fetchone()['n']:
                cur.execute('CREATE INDEX idx_app_nome ON clientes (nome)')
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.statistics WHERE table_schema='wa_clientes' AND table_name='clientes' AND index_name='idx_app_clientes_lista'")
            if not cur.fetchone()['n']:
                cur.execute("CREATE INDEX idx_app_clientes_lista ON clientes (excluido_em,((NULLIF(TRIM(nome),'') IS NULL)),nome,id DESC)")
            cur.execute("CREATE USER %s@'127.0.0.1' IDENTIFIED BY %s", (service_user, service_password))
            for table in ('clientes', 'telefones', 'emails', 'enderecos', 'app_usuarios'):
                cur.execute(f"GRANT SELECT,INSERT,UPDATE ON wa_clientes.{table} TO %s@'127.0.0.1'", (service_user,))
            cur.execute("GRANT SELECT,INSERT ON wa_clientes.app_auditoria TO %s@'127.0.0.1'", (service_user,))
            cur.execute("GRANT SELECT,INSERT,DELETE ON wa_clientes.app_login_tentativas TO %s@'127.0.0.1'", (service_user,))
            cur.execute("INSERT INTO app_usuarios (nome,login,senha_hash,perfil) VALUES (%s,%s,%s,'ADM')",
                        (admin_name, login, generate_password_hash(app_password)))
            uid = cur.lastrowid
            cur.execute("INSERT INTO app_auditoria (usuario_id,acao,entidade,entidade_id,depois) VALUES (%s,'configurar_sistema','usuario',%s,%s)",
                        (uid, uid, json.dumps({'nome': admin_name, 'login': login, 'perfil': 'ADM'}, ensure_ascii=False)))
        # Valida a conta da aplicação antes de salvar a configuração.
        conn.commit()
        with pymysql.connect(host='127.0.0.1', port=3306, user=service_user, password=service_password,
                             database='wa_clientes', charset='utf8mb4', connect_timeout=5) as test_conn:
            with test_conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM clientes')
                count = cur.fetchone()[0]
        data = {'host': '127.0.0.1', 'port': 3306, 'database': 'wa_clientes', 'user': service_user,
                'password_dpapi': protect(service_password), 'secret_key': secrets.token_urlsafe(48),
                'bind': '127.0.0.1', 'web_port': 8080}
        temporary = CONFIG_FILE.with_suffix('.tmp')
        temporary.touch(exist_ok=False)
        restrict_file(temporary)
        temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
        os.replace(temporary, CONFIG_FILE)
        print(f'\nCONFIGURAÇÃO CONCLUÍDA. {count:,} clientes disponíveis.'.replace(',', '.'))
        print(f'Seu login é: {login}')
        print('A senha do MySQL foi protegida pelo Windows e a senha do sistema foi armazenada como hash.')
        print('Agora abra INICIAR_SISTEMA.cmd. O endereço é http://127.0.0.1:8080')
        print('Nesta primeira etapa o sistema atende apenas este computador.')
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nConfiguração interrompida. Se já houve alteração de estrutura, preserve os arquivos e consulte a TI.')
        sys.exit(1)
    except pymysql.MySQLError as error:
        print(f'\nNão foi possível concluir. Código MySQL: {error.args[0] if error.args else "desconhecido"}.')
        print('Confira se o MySQL está ligado e se a senha root está correta. Se já iniciou a estrutura, não repita sem revisão.')
        sys.exit(1)
    except Exception as error:
        print(f'\nNão foi possível concluir: {error}')
        sys.exit(1)
