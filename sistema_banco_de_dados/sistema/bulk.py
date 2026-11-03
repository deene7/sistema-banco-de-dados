"""Operações em conjuntos limitados, dentro da transação de quem chamou."""
def chunks(values,size=500):
    for start in range(0,len(values),size):yield values[start:start+size]


def client_state(connection,ids,restore=False):
    for batch in chunks(ids):
        connection.execute('UPDATE clientes SET excluido_em='+('NULL' if restore else 'CURRENT_TIMESTAMP')+
                           ',app_versao=app_versao+1 WHERE id IN ('+','.join(['%s']*len(batch))+')',batch)


def remove_clients(connection,ids):
    for batch in chunks(ids,size=100):
        marks=','.join(['%s']*len(batch))
        for table in ('app_cliente_categorias','telefones','emails','enderecos'):
            connection.execute(f'DELETE FROM {table} WHERE cliente_id IN ({marks})',batch)
        connection.execute(f'DELETE FROM clientes WHERE id IN ({marks})',batch)


def bump_clients(connection,ids):
    for batch in chunks(ids):
        connection.execute('UPDATE clientes SET app_versao=app_versao+1,atualizado_em=CURRENT_TIMESTAMP WHERE id IN ('+
                           ','.join(['%s']*len(batch))+')',batch)


def update_client_fields(connection,updates,changed_fields=None):
    fields=('nome','data_nascimento','nome_mae','sexo_origem')
    for batch in chunks(updates):
        assignments=[];params=[]
        for position,field in enumerate(fields):
            if changed_fields is not None and field not in changed_fields:continue
            assignments.append(field+'=CASE id '+' '.join('WHEN %s THEN %s' for _ in batch)+' ELSE '+field+' END')
            for values in batch:params.extend((values[-1],values[position]))
        params.extend(values[-1] for values in batch)
        connection.execute('UPDATE clientes SET '+','.join(assignments)+
                           ',app_versao=app_versao+1,atualizado_em=CURRENT_TIMESTAMP WHERE id IN ('+
                           ','.join(['%s']*len(batch))+')',params)
