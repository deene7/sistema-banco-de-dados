"""Origem por lote, incluindo vínculos antigos preservados na auditoria."""
import json


def load_origins(connection, client_id, limit=10):
    receipts = connection.all(
        "SELECT x.importacao_id AS lote,x.novo_cliente,x.originais,x.linhas "
        "FROM app_importacao_itens x WHERE x.cliente_id_resultado=%s AND x.estado='aplicado' "
        "ORDER BY x.novo_cliente DESC,x.id DESC LIMIT %s", (client_id, limit+1))
    sources = {row['lote']: row for row in receipts}
    audits = connection.all(
        "SELECT depois FROM app_auditoria WHERE cliente_id=%s AND acao='importar' "
        "AND entidade='cliente' ORDER BY id DESC LIMIT 100", (client_id,))
    for audit in audits:
        try:
            data = json.loads(audit['depois'] or '{}')
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        job_id = data.get('lote')
        if type(job_id) is not int or job_id <= 0:
            continue
        source = sources.setdefault(job_id, {'lote': job_id, 'novo_cliente': False})
        source['novo_cliente'] = bool(source['novo_cliente'] or data.get('novo_cliente'))
    if not sources:
        return [], False
    ids = list(sources)
    jobs = connection.all('SELECT id,arquivo,categoria_id,mapeamento,criado_em,excluido_em '
                          'FROM app_importacoes WHERE id IN ('+','.join(['%s']*len(ids))+')', ids)
    jobs.sort(key=lambda job: (bool(sources[job['id']]['novo_cliente']), job['id']), reverse=True)
    more = len(jobs) > limit
    jobs = jobs[:limit]
    categories = {row['id']: row['nome'] for row in connection.all('SELECT id,nome FROM app_categorias')}
    origins = []
    for job in jobs:
        source = sources[job['id']]
        mapping = json.loads(job['mapeamento'] or '{}')
        category_ids = mapping.get('categorias', [job['categoria_id']] if job['categoria_id'] else [])
        extra = []
        originals = json.loads(source.get('originais') or '[]')
        for index, (header, target) in enumerate(zip(mapping.get('headers', []), mapping.get('targets', []))):
            if target == 'extra':
                values = list(dict.fromkeys(row[index] for row in originals if index < len(row) and row[index]))
                if values:
                    extra.append((header, values))
        origins.append({'lote': job['id'], 'arquivo': job['arquivo'],
                        'categoria': ' · '.join(categories.get(category, 'Categoria indisponível') for category in category_ids) or 'Sem categoria',
                        'criado_em': job['criado_em'], 'excluido_em': job['excluido_em'],
                        'novo_cliente': bool(source['novo_cliente']), 'extra': extra,
                        'linhas': json.loads(source.get('linhas') or '[]')})
    return origins, more
