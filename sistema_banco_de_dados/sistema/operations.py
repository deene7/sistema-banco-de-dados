"""Telas administrativas com leituras paginadas e acompanhamento limitado aos lotes visíveis."""
import json
from flask import Blueprint, abort, g, jsonify, render_template, request, url_for
from pagination import page_numbers
from quality_summary import QualitySummary


def register_operations(app, config, db, required):
    pages = Blueprint('operations', __name__)
    quality = QualitySummary(config)
    app.quality_summary = quality

    @pages.before_request
    def ready():
        if not app.config['FEATURES_V2'] or not app.config['FEATURES_V3']:
            abort(404)

    def cached(key, loader):
        if app.config.get('QUERY_CACHE_ENABLED', not app.testing):
            return app.read_cache.get(key, loader, g.read_generation)
        return loader()

    def decorate(rows):
        live = app.import_manager.snapshot()
        for row in rows:
            meta = live.get(row['id'])
            row['ativo'] = meta is not None
            row['posicao'] = meta.get('posicao') if meta else None
            row['decorridos'] = meta.get('decorridos',0) if meta else 0
            row['retomar'] = not meta and row['estado'] in ('erro','preparando','importando')
            row['situacao'] = ('fila' if meta['situacao']=='fila' else row['estado']) if meta else (
                'interrompido' if row['estado'] in ('preparando','importando') else row['estado'])
            summary = json.loads(row.pop('resumo',None) or '{}')
            row['meta'] = int(meta.get('total') or 0) if meta else 0
            if not row['meta'] and row['estado'] in ('importando','concluida','erro'):
                row['meta'] = int(summary.get('prontos') or 0)
            row['percentual'] = min(100,round(100*row['progresso']/row['meta'])) if row['meta'] else None
        return rows

    def processing_totals():
        counts=cached(('processing_totals',), lambda: db().all('SELECT estado,COUNT(*) AS n FROM app_importacoes WHERE excluido_em IS NULL GROUP BY estado'))
        totals={row['estado']:row['n'] for row in counts}
        live=app.import_manager.snapshot()
        totals['fila']=sum(meta['situacao']=='fila' for meta in live.values())
        totals['executando']=sum(meta['situacao']=='executando' for meta in live.values())
        return totals

    @pages.get('/processamentos')
    @required(edit=True)
    def processing():
        state = request.args.get('estado','')
        if state not in ('fila','preparando','importando','revisao','concluida','erro','mapeamento','interrompido'):
            state = ''
        conditions = ['excluido_em IS NULL']; params = []
        live = app.import_manager.snapshot()
        if state=='fila':
            queued = [job_id for job_id,meta in live.items() if meta['situacao']=='fila']
            conditions.append('id IN ('+','.join(['%s']*len(queued))+')' if queued else '1=0')
            params.extend(queued)
        elif state=='interrompido':
            conditions.append("estado IN ('preparando','importando')")
            if live:
                conditions.append('id NOT IN ('+','.join(['%s']*len(live))+')');params.extend(live)
        elif state in ('preparando','importando'):
            conditions.append('estado=%s');params.append(state)
            executing=[job_id for job_id,meta in live.items() if meta['situacao']=='executando']
            conditions.append('id IN ('+','.join(['%s']*len(executing))+')' if executing else '1=0')
            params.extend(executing)
        elif state:
            conditions.append('estado=%s');params.append(state)
        where=' WHERE '+' AND '.join(conditions)
        total=cached(('processing_count',where,tuple(params)), lambda: db().one('SELECT COUNT(*) AS n FROM app_importacoes'+where,params)['n'])
        pages_count=max(1,(total+24)//25)
        page=min(max(1,request.args.get('pagina',1,type=int)),pages_count)
        rows=db().all('SELECT id,arquivo,estado,progresso,total_linhas,criado_em,resumo FROM app_importacoes'+where+' ORDER BY id DESC LIMIT %s OFFSET %s',params+[25,(page-1)*25])
        jobs=decorate(rows)
        totals=processing_totals()
        return render_template('processing.html',jobs=jobs,lookup_jobs=app.lookup_recent(),state=state,totals=totals,total=total,page=page,pages=pages_count,
            page_numbers=page_numbers(page,pages_count),pagination_endpoint='operations.processing',paging={'estado':state},
            result_start=(page-1)*25+1 if total else 0,result_end=min(page*25,total),
            status_url=url_for('operations.processing_status',id=[row['id'] for row in jobs]))

    @pages.get('/processamentos/status')
    @required(edit=True)
    def processing_status():
        values=request.args.getlist('id')
        if len(values)>25 or any(not value.isdigit() or len(value)>20 or not 0<int(value)<2**64 for value in values):
            abort(400)
        ids=sorted({int(value) for value in values})
        rows=db().all('SELECT id,estado,progresso,total_linhas,resumo FROM app_importacoes WHERE excluido_em IS NULL AND id IN ('+','.join(['%s']*len(ids))+') LIMIT 25',ids) if ids else []
        jobs=decorate(rows)
        # A API não devolve recibos, CPFs, linhas originais nem mensagens de erro.
        return jsonify(jobs=jobs,totals=processing_totals())

    @pages.get('/qualidade')
    @required(edit=True)
    def quality_page():
        return render_template('quality.html',stats=quality.get(wait=False))

    @pages.get('/qualidade/resumo')
    @required(edit=True)
    def quality_status():
        stats=quality.get(wait=False)
        return jsonify(stats=stats,refreshing=quality.lock.locked()),200 if stats is not None else 202

    app.register_blueprint(pages)
