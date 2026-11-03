import bootstrap
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pymysql
from security import CONFIG_FILE,restrict_file

BASE=Path(__file__).resolve().parent


def backup(root_password):
    private=BASE/'privado';private.mkdir(exist_ok=True);restrict_file(private)
    options=private/(secrets.token_hex(16)+'.cnf')
    options.touch(exist_ok=False);restrict_file(options)
    escaped=root_password.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n').replace('\r','\\r')
    folder=BASE.parent/'backups';folder.mkdir(exist_ok=True)
    name='wa_clientes_antes_v2_'+datetime.now(timezone(timedelta(hours=-3))).strftime('%Y%m%d_%H%M%S_%f')+'.sql'
    partial=folder/(name+'.parcial');final=folder/name
    try:
        options.write_text('[client]\nuser=root\nhost=127.0.0.1\nport=3306\npassword="'+escaped+'"\n',encoding='utf-8')
        command=[r'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe',
                 '--defaults-extra-file='+str(options),'--single-transaction','--routines','--events','--triggers',
                 '--no-tablespaces','--set-gtid-purged=OFF','--default-character-set=utf8mb4',
                 '--result-file='+str(partial),'--databases','wa_clientes']
        result=subprocess.run(command,capture_output=True,text=True)
        if result.returncode or not partial.exists() or partial.stat().st_size<100:
            raise ValueError('O backup automático falhou. A atualização não foi aplicada. Confira MySQL e espaço em disco.')
        os.replace(partial,final)
        print('Backup criado: '+str(final))
    finally:
        options.unlink(missing_ok=True)


def migrate(connection,service_user):
    with connection.cursor() as cur:
        for statement in (BASE/'schema_v2.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():cur.execute(statement)
        cur.execute("SELECT IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_importacoes' AND COLUMN_NAME='categoria_id'")
        if cur.fetchone()[0]!='YES':cur.execute('ALTER TABLE app_importacoes MODIFY COLUMN categoria_id BIGINT UNSIGNED NULL')
        cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_importacoes' AND COLUMN_NAME='excluido_em'")
        if not cur.fetchone()[0]:cur.execute('ALTER TABLE app_importacoes ADD COLUMN excluido_em DATETIME NULL')
        cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_importacao_itens' AND COLUMN_NAME='cliente_id_resultado'")
        if not cur.fetchone()[0]:
            cur.execute('ALTER TABLE app_importacao_itens ADD COLUMN cliente_id_resultado BIGINT UNSIGNED NULL, ADD CONSTRAINT fk_app_importacao_item_cliente FOREIGN KEY(cliente_id_resultado) REFERENCES clientes(id) ON DELETE SET NULL')
        cur.execute("SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_importacao_itens' AND INDEX_NAME='idx_app_importacao_cpf'")
        if not cur.fetchone()[0]:cur.execute('CREATE INDEX idx_app_importacao_cpf ON app_importacao_itens(cpf,estado,id)')
        cur.execute("SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='app_auditoria' AND CONSTRAINT_NAME='fk_app_auditoria_cliente'")
        fk=cur.fetchone()
        if not fk or fk[0]!='SET NULL':
            if fk:cur.execute('ALTER TABLE app_auditoria DROP FOREIGN KEY fk_app_auditoria_cliente')
            cur.execute('ALTER TABLE app_auditoria ADD CONSTRAINT fk_app_auditoria_cliente FOREIGN KEY(cliente_id) REFERENCES clientes(id) ON DELETE SET NULL')
        for name in ('CLT','SIAPE'):
            cur.execute('INSERT INTO app_categorias (nome) SELECT %s WHERE NOT EXISTS (SELECT 1 FROM app_categorias WHERE nome=%s)',(name,name))
        for table,name,columns in [
                ('clientes','idx_app_clientes_ativos','excluido_em,id'),
                ('telefones','idx_app_tel_ativos','excluido_em,cliente_id,numero'),
                ('emails','idx_app_email_ativos','excluido_em,cliente_id,email'),
                ('app_importacao_itens','idx_app_importacao_lote_id','importacao_id,id'),
                ('enderecos','idx_app_endereco_uf','excluido_em,uf,cliente_id')]:
            cur.execute('SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s',(table,name))
            if not cur.fetchone()[0]:cur.execute(f'CREATE INDEX {name} ON {table} ({columns})')
        for table in ('app_categorias','app_importacoes'):
            cur.execute(f"GRANT SELECT,INSERT,UPDATE ON wa_clientes.{table} TO %s@'127.0.0.1'",(service_user,))
        cur.execute("GRANT DELETE ON wa_clientes.app_importacoes TO %s@'127.0.0.1'",(service_user,))
        for table in ('app_cliente_categorias','app_importacao_itens','clientes','telefones','emails','enderecos'):
            cur.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON wa_clientes.{table} TO %s@'127.0.0.1'",(service_user,))
    connection.commit()


def main():
    print('\nATUALIZAÇÃO: CATEGORIAS, IMPORTAÇÕES E LIXEIRA\n')
    if not CONFIG_FILE.exists():raise ValueError('Configure o sistema primeiro.')
    raw=json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    print('Será criado um backup antes das mudanças de estrutura e permissões.')
    print('Nenhum cadastro será excluído por esta atualização.\n')
    import tkinter as tk
    from tkinter import simpledialog
    window=tk.Tk();window.withdraw()
    password=simpledialog.askstring('Atualizar sistema de clientes',
                                   'Digite a senha ROOT do MySQL. Ela será usada somente para o backup e a atualização local.',show='*',parent=window)
    window.destroy()
    if password is None:print('Atualização cancelada.');return
    conn=pymysql.connect(host='127.0.0.1',port=3306,user='root',password=password,database='wa_clientes',charset='utf8mb4')
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_importacoes'")
            if cur.fetchone()[0]:
                cur.execute("SELECT COUNT(*) FROM app_importacoes WHERE estado IN ('preparando','importando') AND excluido_em IS NULL")
                if cur.fetchone()[0]:raise ValueError('Aguarde as importações em processamento terminarem antes de atualizar.')
            cur.execute("SELECT COUNT(*) FROM information_schema.INNODB_TRX t JOIN information_schema.PROCESSLIST p ON p.ID=t.trx_mysql_thread_id WHERE p.DB='wa_clientes' AND t.trx_rows_modified>0")
            if cur.fetchone()[0]:raise ValueError('Há gravações em andamento. Aguarde a conclusão antes de atualizar.')
        print('Criando backup, aguarde...');backup(password);password=None
        print('Preparando estrutura e índices de desempenho...');migrate(conn,raw['user'])
        uploads=BASE/'importacoes_privadas';uploads.mkdir(exist_ok=True);restrict_file(uploads)
        raw['features_v2']=True
        raw['features_v3']=True
        raw['features_v4']=True
        temporary=BASE/('config.atualizacao_'+secrets.token_hex(8)+'.tmp')
        temporary.touch(exist_ok=False);restrict_file(temporary)
        temporary.write_text(json.dumps(raw,indent=2),encoding='utf-8');os.replace(temporary,CONFIG_FILE)
    finally:conn.close()
    print('Atualização aplicada. Reiniciando somente o sistema de telas...')
    from reiniciar import restart
    restart()
    print('\nATUALIZAÇÃO CONCLUÍDA. Abra http://127.0.0.1:8080 e pressione Ctrl+F5.')


if __name__=='__main__':
    try:main()
    except pymysql.MySQLError as error:
        print('Falha MySQL, código: '+str(error.args[0])+'. Não tente excluir tabelas. Informe este código à TI.')
        sys.exit(1)
    except Exception as error:
        print('Não foi possível concluir: '+str(error));sys.exit(1)
