"""Totais sem travar novas navegações quando o cache precisa ser atualizado."""
import threading
import time
from database import Database


class HomeSummary:
    def __init__(self,config,ttl=30):
        self.config=config;self.ttl=ttl;self.data=None;self.updated=0;self.attempted=0
        self.lock=threading.Lock()

    def load(self):
        connection=Database(self.config)
        try:
            stats=connection.one("SELECT COUNT(*) AS total,COALESCE(SUM(NULLIF(TRIM(nome),'') IS NULL),0) AS sem_nome,"
                "(SELECT COUNT(DISTINCT t.cliente_id) FROM telefones t JOIN clientes p ON p.id=t.cliente_id WHERE t.excluido_em IS NULL AND p.excluido_em IS NULL) AS com_telefone,"
                "(SELECT COUNT(DISTINCT e.cliente_id) FROM emails e JOIN clientes p ON p.id=e.cliente_id WHERE e.excluido_em IS NULL AND p.excluido_em IS NULL) AS com_email "
                'FROM clientes WHERE excluido_em IS NULL')
            stats['sem_telefone']=stats['total']-stats['com_telefone']
            stats['sem_email']=stats['total']-stats['com_email']
            self.data=stats;self.updated=time.monotonic()
        finally:connection.close()

    def background(self):
        if not self.lock.acquire(blocking=False):return
        self.attempted=time.monotonic()
        def run():
            try:self.load()
            except Exception:
                # Preserva o último resumo; uma falha não bloqueia o restante da Home.
                pass
            finally:self.lock.release()
        threading.Thread(target=run,name='resumo-home',daemon=True).start()

    def get(self):
        if self.data is None:
            with self.lock:
                if self.data is None:self.load()
        elif time.monotonic()-max(self.updated,self.attempted)>self.ttl:self.background()
        return dict(self.data)
