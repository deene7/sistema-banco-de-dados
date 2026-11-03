import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash


def create_demo(path):
    if Path(path).exists():
        add_extensions(path)
        return
    conn = sqlite3.connect(path)
    conn.executescript('''
    CREATE TABLE clientes (id INTEGER PRIMARY KEY,cpf TEXT NOT NULL UNIQUE,nome TEXT,
      data_nascimento TEXT,nome_mae TEXT,sexo_origem TEXT,app_versao INTEGER NOT NULL DEFAULT 1,
      criado_em TEXT DEFAULT CURRENT_TIMESTAMP,atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP,excluido_em TEXT);
    CREATE TABLE telefones (id INTEGER PRIMARY KEY,cliente_id INTEGER REFERENCES clientes(id),
      numero TEXT NOT NULL,situacao TEXT DEFAULT 'nao_verificado',criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
      atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP,excluido_em TEXT,UNIQUE(cliente_id,numero));
    CREATE TABLE emails (id INTEGER PRIMARY KEY,cliente_id INTEGER REFERENCES clientes(id),email TEXT NOT NULL,
      situacao TEXT DEFAULT 'nao_verificado',criado_em TEXT DEFAULT CURRENT_TIMESTAMP,excluido_em TEXT,UNIQUE(cliente_id,email));
    CREATE TABLE enderecos (id INTEGER PRIMARY KEY,cliente_id INTEGER REFERENCES clientes(id),tipo TEXT,titulo TEXT,
      logradouro TEXT,numero TEXT,complemento TEXT,bairro TEXT,cidade TEXT,uf TEXT,cep TEXT,
      criado_em TEXT DEFAULT CURRENT_TIMESTAMP,excluido_em TEXT);
    CREATE TABLE app_usuarios (id INTEGER PRIMARY KEY,nome TEXT,login TEXT UNIQUE,senha_hash TEXT,perfil TEXT,
      ativo INTEGER DEFAULT 1,gerenciar_usuarios INTEGER DEFAULT 0,auth_versao INTEGER DEFAULT 1);
    CREATE TABLE app_auditoria (id INTEGER PRIMARY KEY,usuario_id INTEGER,cliente_id INTEGER REFERENCES clientes(id) ON DELETE SET NULL,acao TEXT,
      entidade TEXT,entidade_id INTEGER,antes TEXT,depois TEXT,motivo TEXT,criado_em TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE app_login_tentativas (id INTEGER PRIMARY KEY,login TEXT,origem TEXT,criado_em TEXT DEFAULT CURRENT_TIMESTAMP);
    ''')
    for name, login, role in [('Administradora de demonstração','demo','ADM'),
                               ('Atendimento de demonstração','atendimento','ATENDIMENTO'),
                               ('TI de demonstração','ti','TI')]:
        conn.execute('INSERT INTO app_usuarios (nome,login,senha_hash,perfil) VALUES (?,?,?,?)',
                     (name, login, generate_password_hash('Demonstracao!2026'), role))
    # Identificadores sintéticos exclusivos da demonstração; não são enviados a serviços externos.
    for row in [(1,'11111111111','Cliente de exemplo — Ana','1980-03-12','Nome fictício','F'),
                (2,'22222222222','Cliente de exemplo — Bruno',None,None,None),
                (3,'33333333333','Cliente de exemplo — Carla','1975-11-20',None,'F')]:
        conn.execute('INSERT INTO clientes (id,cpf,nome,data_nascimento,nome_mae,sexo_origem) VALUES (?,?,?,?,?,?)',row)
    conn.execute("INSERT INTO telefones (cliente_id,numero) VALUES (1,'11000000000')")
    conn.execute("INSERT INTO emails (cliente_id,email) VALUES (1,'exemplo@example.invalid')")
    conn.execute("INSERT INTO enderecos (cliente_id,logradouro,numero,cidade,uf,cep) VALUES (1,'Rua de demonstração','100','Cidade fictícia','SP','00000000')")
    conn.commit()
    conn.close()
    add_extensions(path)


def add_extensions(path):
    conn=sqlite3.connect(path)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS app_categorias (id INTEGER PRIMARY KEY,nome TEXT UNIQUE COLLATE NOCASE,descricao TEXT,
      ativo INTEGER DEFAULT 1,versao INTEGER DEFAULT 1,criado_em TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS app_cliente_categorias (cliente_id INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
      categoria_id INTEGER REFERENCES app_categorias(id),criado_em TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(cliente_id,categoria_id));
    CREATE TABLE IF NOT EXISTS app_importacoes (id INTEGER PRIMARY KEY,token TEXT UNIQUE,arquivo TEXT,caminho TEXT,sha256 TEXT,
      categoria_id INTEGER REFERENCES app_categorias(id),usuario_id INTEGER REFERENCES app_usuarios(id),aprovador_id INTEGER,
      estado TEXT DEFAULT 'mapeamento',aba TEXT,mapeamento TEXT,progresso INTEGER DEFAULT 0,total_linhas INTEGER DEFAULT 0,
      resumo TEXT,erro TEXT,excluido_em TEXT,criado_em TEXT DEFAULT CURRENT_TIMESTAMP,atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS app_importacao_itens (id INTEGER PRIMARY KEY,importacao_id INTEGER REFERENCES app_importacoes(id),
      chave TEXT,cpf TEXT,estado TEXT,dados TEXT,originais TEXT,linhas TEXT,motivos TEXT,cliente_versao INTEGER,
      cliente_id_resultado INTEGER REFERENCES clientes(id) ON DELETE SET NULL,
      novo_cliente INTEGER DEFAULT 0,novos_telefones INTEGER DEFAULT 0,novos_emails INTEGER DEFAULT 0,
      novos_enderecos INTEGER DEFAULT 0,nova_categoria INTEGER DEFAULT 0,UNIQUE(importacao_id,chave));
    ''')
    columns={row[1] for row in conn.execute('PRAGMA table_info(app_importacao_itens)')}
    if 'cliente_id_resultado' not in columns:
        conn.execute('ALTER TABLE app_importacao_itens ADD COLUMN cliente_id_resultado INTEGER REFERENCES clientes(id) ON DELETE SET NULL')
    columns={row[1] for row in conn.execute('PRAGMA table_info(app_importacoes)')}
    if 'excluido_em' not in columns:conn.execute('ALTER TABLE app_importacoes ADD COLUMN excluido_em TEXT')
    for name in ('CLT','SIAPE'):
        conn.execute('INSERT OR IGNORE INTO app_categorias (nome) VALUES (?)',(name,))
    conn.commit();conn.close()
