"""Indicadores agregados, compartilhados e atualizados fora das requisições."""
from datetime import datetime, timezone, timedelta
import time
from database import Database
from home_summary import HomeSummary


class QualitySummary(HomeSummary):
    def __init__(self, config):
        super().__init__(config, ttl=120)

    def load(self):
        connection = Database(self.config)
        try:
            if connection.sqlite:
                connection.execute('BEGIN').close()
            clients = connection.one("SELECT COUNT(*) AS total,COALESCE(SUM(NULLIF(TRIM(nome),'') IS NULL),0) AS sem_nome FROM clientes WHERE excluido_em IS NULL")
            phones = connection.one("SELECT COUNT(*) AS telefones,COUNT(DISTINCT t.cliente_id) AS com_telefone,"
                "COUNT(DISTINCT CASE WHEN t.situacao='confirmado' THEN t.cliente_id END) AS clientes_verificados,"
                "COALESCE(SUM(t.situacao='confirmado'),0) AS verificados,COALESCE(SUM(t.situacao='nao_verificado'),0) AS nao_verificados,"
                "COALESCE(SUM(t.situacao='invalido'),0) AS invalidos,COALESCE(SUM(t.situacao='inativo'),0) AS inativos "
                "FROM telefones t JOIN clientes c ON c.id=t.cliente_id WHERE t.excluido_em IS NULL AND c.excluido_em IS NULL")
            emails = connection.one('SELECT COUNT(DISTINCT e.cliente_id) AS com_email FROM emails e JOIN clientes c ON c.id=e.cliente_id WHERE e.excluido_em IS NULL AND c.excluido_em IS NULL')
            stats = {key:int(value) for source in (clients,phones,emails) for key,value in source.items()}
            stats['sem_telefone'] = stats['total']-stats['com_telefone']
            stats['sem_email'] = stats['total']-stats['com_email']
            stats['cobertura_telefone'] = round(100*stats['com_telefone']/stats['total'],1) if stats['total'] else 0
            stats['cobertura_verificados'] = round(100*stats['clientes_verificados']/stats['total'],1) if stats['total'] else 0
            categories = connection.all("SELECT cat.id,cat.nome,COUNT(c.id) AS clientes,COUNT(v.cliente_id) AS verificados "
                "FROM app_categorias cat LEFT JOIN app_cliente_categorias l ON l.categoria_id=cat.id "
                "LEFT JOIN clientes c ON c.id=l.cliente_id AND c.excluido_em IS NULL "
                "LEFT JOIN (SELECT DISTINCT cliente_id FROM telefones WHERE excluido_em IS NULL AND situacao='confirmado') v ON v.cliente_id=c.id "
                "WHERE cat.ativo=1 GROUP BY cat.id,cat.nome ORDER BY cat.nome LIMIT 101")
            stats['mais_categorias'] = len(categories)>100
            stats['categorias'] = categories[:100]
            for category in stats['categorias']:
                category['cobertura'] = round(100*category['verificados']/category['clientes'],1) if category['clientes'] else 0
            stats['atualizado_em'] = datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M:%S')
            self.data = stats
            self.updated = time.monotonic()
        finally:
            connection.close()
