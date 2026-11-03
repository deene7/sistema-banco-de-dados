"""CSV em blocos, com leitura consistente e sem paginação por OFFSET."""
import csv
import io
import json
from array import array
from pymysql.cursors import SSDictCursor
from database import Database


def safe_cell(value):
    value='' if value is None else str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')) else value


def stream_clients(config,where,params,categories=True):
    connection=None
    buffer=io.StringIO(newline='');writer=csv.writer(buffer,delimiter=';',lineterminator='\r\n')
    def row(values):
        buffer.seek(0);buffer.truncate(0);writer.writerow([safe_cell(value) for value in values]);return buffer.getvalue()
    try:
        connection=Database(config)
        connection.execute('BEGIN' if connection.sqlite else 'START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        yield '\ufeff'+row(['ID','CPF','Nome','Data de nascimento','Nome da mãe','Sexo','Telefones','E-mails','Endereços (JSON)','Categorias'])
        selected=None;offset=0
        if where not in ('c.excluido_em IS NULL','c.excluido_em IS NOT NULL'):
            # Avalia filtros complexos uma vez, guardando só IDs compactos (8 bytes cada).
            selected=array('Q')
            query=f'SELECT c.id FROM clientes c WHERE {where} ORDER BY c.id'
            cursor=connection.execute(query,params) if connection.sqlite else connection.conn.cursor(SSDictCursor)
            try:
                if not connection.sqlite:cursor.execute(query,params)
                while True:
                    batch=cursor.fetchmany(5000)
                    if not batch:break
                    selected.extend(item['id'] for item in batch)
            finally:cursor.close()
        last_id=0
        while True:
            fields='SELECT c.id,c.cpf,c.nome,c.data_nascimento,c.nome_mae,c.sexo_origem FROM clientes c '
            if selected is not None:
                subset=list(selected[offset:offset+1000]);offset+=1000
                if not subset:break
                clients=connection.all(fields+'WHERE c.id IN ('+','.join(['%s']*len(subset))+') ORDER BY c.id',subset)
            else:
                clients=connection.all(fields+f'WHERE ({where}) AND c.id>%s ORDER BY c.id LIMIT 1000',list(params)+[last_id])
            if not clients:break
            ids=[client['id'] for client in clients];marks=','.join(['%s']*len(ids))
            phones,emails,addresses,links={},{},{},{}
            for table,column,target in [('telefones','numero',phones),('emails','email',emails)]:
                for item in connection.all(f'SELECT cliente_id,{column} FROM {table} WHERE cliente_id IN ({marks}) AND excluido_em IS NULL ORDER BY cliente_id,id',ids):
                    target.setdefault(item['cliente_id'],[]).append(item[column])
            for item in connection.all(f'SELECT * FROM enderecos WHERE cliente_id IN ({marks}) AND excluido_em IS NULL ORDER BY cliente_id,id',ids):
                address={key:item.get(key) for key in ('tipo','titulo','logradouro','numero','complemento','bairro','cidade','uf','cep') if item.get(key)}
                addresses.setdefault(item['cliente_id'],[]).append(address)
            if categories:
                for item in connection.all(f'SELECT l.cliente_id,c.nome FROM app_cliente_categorias l JOIN app_categorias c ON c.id=l.categoria_id WHERE l.cliente_id IN ({marks}) AND c.ativo=1 ORDER BY l.cliente_id,c.nome',ids):
                    links.setdefault(item['cliente_id'],[]).append(item['nome'])
            chunks=[]
            for client in clients:
                key=client['id'];cpf=client['cpf'] or ''
                if len(cpf)==11:cpf=f'{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}'
                chunks.append(row([key,cpf,client['nome'],client['data_nascimento'],client['nome_mae'],client['sexo_origem'],
                           ' | '.join(phones.get(key,[])),' | '.join(emails.get(key,[])),
                           json.dumps(addresses[key],ensure_ascii=False,default=str) if key in addresses else '',
                           ' | '.join(links.get(key,[]))]))
            yield ''.join(chunks)
            last_id=clients[-1]['id']
    finally:
        buffer.close()
        if connection:
            connection.rollback();connection.close()
