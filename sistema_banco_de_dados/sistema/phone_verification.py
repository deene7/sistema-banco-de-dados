"""Consulta a auditoria de todos os telefones da ficha em uma única leitura."""
import json


def audit_object(value):
    try:
        parsed=json.loads(value or '{}')
        return parsed if isinstance(parsed,dict) else {}
    except (TypeError,ValueError):
        return {}


def verification_history(connection, client_id, phones):
    confirmed = {phone['id'] for phone in phones if phone['situacao'] == 'confirmado'}
    if not confirmed:
        return {}
    events = connection.all(
        "SELECT a.entidade_id,a.acao,a.antes,a.depois,a.criado_em,u.nome AS autor "
        "FROM app_auditoria a LEFT JOIN app_usuarios u ON u.id=a.usuario_id "
        "WHERE a.cliente_id=%s AND a.entidade='telefone' "
        "AND a.acao IN ('adicionar','editar','verificar_origem') ORDER BY a.id DESC LIMIT 500", (client_id,))
    result = {}
    for event in events:
        phone_id = event['entidade_id']
        if phone_id not in confirmed or phone_id in result:
            continue
        before = audit_object(event['antes'])
        after = audit_object(event['depois'])
        if after.get('situacao') != 'confirmado':
            continue
        if event['acao'] == 'editar' and before.get('situacao') == 'confirmado' and before.get('numero') == after.get('numero'):
            continue
        job_id=after.get('lote')
        sources=after.get('fontes')
        spreadsheet=after.get('arquivo') if after.get('origem')=='planilha' else None
        result[phone_id] = {'data': event['criado_em'], 'autor': event['autor'],
                            'arquivo': spreadsheet if isinstance(spreadsheet,str) else None,
                            'lote': job_id if event['acao'] == 'verificar_origem' and type(job_id) is int and 0<job_id<2**64 else None,
                            'fontes': ' · '.join(str(source['nome']) for source in (sources if isinstance(sources,list) else []) if isinstance(source, dict) and source.get('nome')),
                            'manual': event['acao'] != 'verificar_origem'}
    return result
