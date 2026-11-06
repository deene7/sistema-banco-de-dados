import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import csv
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from openpyxl import load_workbook
from app import create_app
from demo import create_demo
from spreadsheet_lookup import normalized_cpf,lookup_batch


def fake_cpf(base):
    value=f'{base:09d}'
    for n in (9,10):value+=str((sum(int(value[i])*(n+1-i) for i in range(n))*10%11)%10)
    return value


class SpreadsheetLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='teste_consultas_',dir=Path(__file__).resolve().parents[1])
        cls.seed=Path(cls.temp.name)/'seed.sqlite3';create_demo(cls.seed)

    @classmethod
    def tearDownClass(cls):
        Path(cls.temp.name).resolve().relative_to(Path(__file__).resolve().parents[1]);cls.temp.cleanup()

    def setUp(self):
        self.root=Path(self.temp.name)/self.id().split('.')[-1];self.root.mkdir()
        self.path=self.root/'dados.sqlite3';shutil.copyfile(self.seed,self.path)
        self.cpfs=[fake_cpf(12000001+i) for i in range(6)]
        self.sql('UPDATE clientes SET cpf=? WHERE id=1',(self.cpfs[0],))
        self.sql('UPDATE clientes SET cpf=? WHERE id=2',(self.cpfs[1],))
        self.sql('UPDATE clientes SET cpf=? WHERE id=3',(self.cpfs[2],))
        self.sql("UPDATE telefones SET situacao='confirmado' WHERE cliente_id=1")
        self.sql("INSERT INTO telefones(cliente_id,numero,situacao) VALUES (1,'11911111111','nao_verificado'),(2,'11922222222','nao_verificado')")
        self.sql('INSERT INTO app_cliente_categorias(cliente_id,categoria_id) VALUES (1,1),(2,2)')
        self.app=create_app({'sqlite_path':str(self.path),'secret_key':'consulta-teste','demo':True})
        self.app.config.update(TESTING=True,TESTING_SYNC_LOOKUPS=True)
        self.client=self.app.test_client();self.login()

    def sql(self,query,params=()):
        connection=sqlite3.connect(self.path)
        try:
            result=connection.execute(query,params).fetchall();connection.commit();return result
        finally:connection.close()

    def login(self,role='demo'):
        self.client.get('/login')
        with self.client.session_transaction() as session:token=session['csrf']
        response=self.client.post('/login',data={'csrf':token,'login':role,'senha':'Demonstracao!2026'})
        self.assertEqual(response.status_code,302)

    def post(self,url,data=None,**kwargs):
        with self.client.session_transaction() as session:token=session['csrf']
        return self.client.post(url,data={'csrf':token,**(data or {})},**kwargs)

    def upload(self,records,**filters):
        stream=io.StringIO();writer=csv.writer(stream,delimiter=';')
        writer.writerow(['CPF','Nome enviado','Observação']);writer.writerows(records)
        response=self.post('/consultas-planilha/enviar',{'arquivo':(io.BytesIO(stream.getvalue().encode('utf-8-sig')),'prospectos.csv'),**filters},content_type='multipart/form-data')
        self.assertEqual(response.status_code,302,response.data)
        return response.location.rsplit('/',1)[-1]

    def execute(self,token):
        response=self.post('/consultas-planilha/'+token,{'cpf_col':'0'})
        self.assertEqual(response.status_code,302,response.data)
        job=self.app.lookup_manager.store.get(token)
        self.assertEqual(job['estado'],'concluida',job.get('erro'))
        return json.loads(job['resumo'])

    def workbook_rows(self,token):
        response=self.client.get(f'/consultas-planilha/{token}/resultado.xlsx')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        data=response.data;response.close()
        book=load_workbook(io.BytesIO(data),read_only=True,data_only=False)
        try:return {sheet.title:list(sheet.iter_rows(values_only=True)) for sheet in book}
        finally:book.close()

    def test_cpf_mode_groups_existing_without_phone_lookup_or_mutations(self):
        from database import Database
        self.sql('UPDATE clientes SET excluido_em=CURRENT_TIMESTAMP WHERE id=3')
        self.sql('DELETE FROM telefones WHERE cliente_id=2')
        before=self.sql('SELECT * FROM clientes')
        connection=Database({'sqlite_path':str(self.path)})
        try:
            with patch.object(connection,'all',wraps=connection.all) as queries:
                result=lookup_batch(connection,self.cpfs,{'tipo':'cpf','verificacao':'com'})
                self.assertEqual(result[self.cpfs[1]]['grupo'],'cadastrados')
                self.assertFalse(any('FROM telefones' in call.args[0] for call in queries.call_args_list))
        finally:connection.close()
        records=[[cpf,'Contato',''] for cpf in [self.cpfs[0],self.cpfs[1],self.cpfs[2],self.cpfs[3],self.cpfs[0],'123']]
        token=self.upload(records,tipo='cpf',verificacao='com')
        summary=self.execute(token)
        self.assertEqual(summary['grupos'],{'cadastrados':3,'nao_encontrados':1,'fora_filtros':0,'invalidos':1,'lixeira':1})
        sheets=self.workbook_rows(token)
        self.assertIn('Já cadastrados',sheets)
        self.assertNotIn('Com telefone verificado',sheets)
        self.assertFalse(any('Telefone' in str(value) for value in sheets['Já cadastrados'][0]))
        self.assertIn('CPF na base',self.client.get('/consultas-planilha/'+token).data.decode())
        self.assertEqual(before,self.sql('SELECT * FROM clientes'))
        token=self.upload(records,tipo='cpf',categoria_id='1')
        self.assertEqual(self.execute(token)['grupos']['fora_filtros'],1)

    def test_categories_keep_new_outside_invalid_and_trash_separate_without_mutations(self):
        self.sql('UPDATE clientes SET excluido_em=CURRENT_TIMESTAMP WHERE id=3')
        self.sql('INSERT INTO clientes(id,cpf,nome) VALUES (5,?,?)',(self.cpfs[4],'Cliente outra base'))
        self.sql("INSERT INTO telefones(cliente_id,numero,situacao) VALUES (5,'11955555555','confirmado')")
        self.sql('INSERT INTO app_cliente_categorias(cliente_id,categoria_id) VALUES (5,2)')
        before={table:self.sql('SELECT * FROM '+table) for table in ('clientes','telefones','app_cliente_categorias')}
        records=[[int(self.cpfs[0]),'Original','=1+1'],[self.cpfs[1],'Outro',''],[self.cpfs[2],'Lixeira',''],[self.cpfs[3],'Novo',''],[self.cpfs[4],'Outra categoria',''],['123','Inválido',''],[self.cpfs[0],'Repetição','']]
        token=self.upload(records,categoria_id='1')
        summary=self.execute(token)
        self.assertEqual(summary['total'],7)
        self.assertEqual(summary['grupos'],{'verificados':2,'sem_verificados':0,'nao_encontrados':1,'fora_filtros':2,'invalidos':1,'lixeira':1})
        sheets=self.workbook_rows(token)
        self.assertEqual(len(sheets['Com telefone verificado']),3)
        first=sheets['Com telefone verificado'][1]
        self.assertEqual(first[:3],(str(int(self.cpfs[0])),'Original','=1+1'))
        self.assertEqual(first[3],self.cpfs[0])
        self.assertEqual(first[-1],'11000000000')
        self.assertIn(sheets['Fora dos filtros'][1][-1],('',None))
        for table,rows_before in before.items():self.assertEqual(self.sql('SELECT * FROM '+table),rows_before)
        self.assertFalse((self.app.lookup_manager.store.root/token/'trabalho.sqlite3').exists())

    def test_multiple_categories_uf_and_no_verified_mean_none_even_for_mixed_clients(self):
        self.sql("INSERT INTO enderecos(cliente_id,uf) VALUES (2,'SP')")
        token=self.upload([[self.cpfs[0],'Misto',''],[self.cpfs[1],'Sem verificado',''],[self.cpfs[2],'Sem telefone','']],categoria_id=['1','2'],uf='SP',verificacao='sem')
        result=self.execute(token)['grupos']
        self.assertEqual(result['sem_verificados'],1)
        self.assertEqual(result['fora_filtros'],2)
        rows=self.workbook_rows(token)['Sem telefone verificado']
        self.assertEqual(rows[1][3],self.cpfs[1])
        self.assertEqual(rows[1][-2],0)

    def test_no_filters_and_duplicates_use_bounded_unique_cpf_batches(self):
        records=[[fake_cpf(610000000+i),'Prospecto','x'*100] for i in range(1005)]
        records.append(records[0])
        batches=[]
        def read(connection,cpfs,filters):
            batches.append(len(cpfs));return lookup_batch(connection,cpfs,filters)
        token=self.upload(records)
        with patch('spreadsheet_lookup.lookup_batch',read):summary=self.execute(token)
        self.assertEqual(batches,[500,500,5])
        self.assertEqual(summary['grupos']['nao_encontrados'],1006)
        self.assertEqual(len(self.workbook_rows(token)['Não encontrados']),1007)

    def test_permissions_tokens_csrf_and_double_submission(self):
        token=self.upload([[self.cpfs[0],'Nome','']]);self.execute(token)
        self.assertEqual(self.post('/consultas-planilha/'+token,{'cpf_col':'0'}).status_code,400)
        self.post('/sair');self.login('ti')
        self.assertEqual(self.client.get('/consultas-planilha/'+token).status_code,403)
        self.assertEqual(self.client.get('/consultas-planilha/'+token+'/resultado.xlsx').status_code,403)
        self.assertEqual(self.client.get('/consultas-planilha/not-a-token').status_code,404)
        self.post('/sair');self.login('atendimento')
        self.assertEqual(self.client.get('/consultas-planilha').status_code,403)
        self.assertEqual(self.client.get('/consultas-planilha/'+token+'/status').status_code,403)
        self.assertEqual(self.client.post('/consultas-planilha/enviar',data={'csrf':'incorreto'}).status_code,400)

    def test_invalid_filters_column_and_restart_recovery(self):
        token=self.upload([[self.cpfs[0],'Nome','']])
        self.assertEqual(self.post('/consultas-planilha/'+token,{'cpf_col':'999'}).status_code,400)
        job=self.app.lookup_manager.store.get(token)
        self.app.lookup_manager.store.prepare(token,{'headers':['CPF','Nome enviado','Observação'],'cpf_col':0,'aba':None},('mapeamento',))
        status=self.client.get('/consultas-planilha/'+token+'/status')
        self.assertEqual(status.json['estado'],'interrompida')
        self.assertEqual(self.post('/consultas-planilha/'+token+'/retomar').status_code,302)
        self.assertEqual(self.app.lookup_manager.store.get(token)['estado'],'concluida')
        self.assertEqual(normalized_cpf('=1+1'),None)
        self.assertEqual(normalized_cpf(str(int(self.cpfs[0]))),self.cpfs[0])

    def test_formula_cells_are_strings_in_output(self):
        token=self.upload([[self.cpfs[0],'=1+1','+comando']]);self.execute(token)
        book=load_workbook(self.app.lookup_manager.result_path(token),read_only=True,data_only=False)
        try:
            row=next(book['Com telefone verificado'].iter_rows(min_row=2,max_row=2))
            self.assertEqual(row[1].value,'=1+1');self.assertEqual(row[1].data_type,'s')
        finally:book.close()

    def test_excel_sheet_selection_and_manual_cpf_column(self):
        from openpyxl import Workbook
        book=Workbook();book.active.title='Primeira';book.active.append(['CPF']);book.active.append([self.cpfs[0]])
        other=book.create_sheet('Lista SIAPE');other.append(['Nome','Documento do cliente']);other.append(['Cliente informado',int(self.cpfs[1])])
        stream=io.BytesIO();book.save(stream);book.close();stream.seek(0)
        response=self.post('/consultas-planilha/enviar',{'arquivo':(stream,'duas_abas.xlsx'),'categoria_id':'2'},content_type='multipart/form-data')
        self.assertEqual(response.status_code,302)
        token=response.location.rsplit('/',1)[-1]
        self.assertEqual(self.client.get(response.location+'?aba=Lista+SIAPE').status_code,200)
        self.assertEqual(self.post(response.location,{'aba':'Lista SIAPE','cpf_col':'1'}).status_code,302)
        job=self.app.lookup_manager.store.get(token)
        self.assertEqual(job['estado'],'concluida',job['erro'])
        output=self.workbook_rows(token)
        self.assertEqual(len(output['Sem telefone verificado']),2)
        self.assertEqual(output['Sem telefone verificado'][1][2],self.cpfs[1])
        self.assertEqual(self.client.get('/consultas-planilha/status?id='+token).json['jobs'][0]['estado'],'concluida')
        self.assertEqual(self.client.get('/consultas-planilha/status?'+('&'.join('id='+token for _ in range(21)))).status_code,400)

    def test_background_queue_returns_without_waiting_and_blocks_double_start(self):
        import threading
        token=self.upload([[self.cpfs[0],'Cliente','']])
        manager=self.app.lookup_manager
        started=threading.Event();release=threading.Event();original=manager.process
        def process(value):
            started.set();release.wait(5);original(value)
        self.app.config['TESTING_SYNC_LOOKUPS']=False
        try:
            with patch.object(manager,'process',process):
                response=self.post('/consultas-planilha/'+token,{'cpf_col':'0'})
                self.assertEqual(response.status_code,302)
                self.assertTrue(started.wait(5))
                self.assertTrue(manager.active(token))
                self.assertEqual(self.post('/consultas-planilha/'+token,{'cpf_col':'0'}).status_code,400)
                release.set()
                manager.worker.shutdown(wait=True)
        finally:
            release.set();manager.worker.shutdown(wait=True)
        self.assertEqual(manager.store.get(token)['estado'],'concluida')

    def test_retention_removes_only_expired_files_and_preserves_summary_and_audit(self):
        import time
        from spreadsheet_lookup import RETENTION_SECONDS
        token=self.upload([[self.cpfs[0],'Cliente','']]);self.execute(token)
        fresh=self.upload([[self.cpfs[1],'Recente','']])
        manager=self.app.lookup_manager
        before=manager.store.get(token)
        audit_before=self.sql('SELECT COUNT(*) FROM app_auditoria')[0][0]
        instant=time.time()
        manager.store.update(token,retencao_desde=instant-RETENTION_SECONDS-1)
        result=manager.cleanup(at=instant)
        self.assertEqual(result['consultas'],1)
        self.assertEqual(result['arquivos'],2)
        self.assertGreater(result['bytes'],0)
        self.assertFalse((manager.store.root/token).exists())
        self.assertTrue((manager.store.root/fresh).exists())
        after=manager.store.get(token)
        self.assertEqual(after['resumo'],before['resumo'])
        self.assertEqual(after['filtros'],before['filtros'])
        self.assertEqual(after['total'],before['total'])
        self.assertIsNotNone(after['expirado_em'])
        self.assertEqual(audit_before,self.sql('SELECT COUNT(*) FROM app_auditoria')[0][0])
        self.assertEqual(self.client.get('/consultas-planilha/'+token).status_code,200)
        self.assertEqual(self.client.get('/consultas-planilha/'+token+'/status').json['estado'],'expirada')
        self.assertEqual(self.client.get('/consultas-planilha/'+token+'/resultado.xlsx').status_code,410)
        self.assertEqual(self.post('/consultas-planilha/'+token+'/retomar').status_code,410)
        self.assertEqual(manager.cleanup(at=instant)['arquivos'],0)

    def test_retention_deadline_is_exact_and_does_not_extend_when_viewed(self):
        import time
        from spreadsheet_lookup import RETENTION_SECONDS
        token=self.upload([[self.cpfs[0],'Cliente','']]);self.execute(token)
        manager=self.app.lookup_manager;instant=time.time()
        manager.store.update(token,retencao_desde=instant-RETENTION_SECONDS+1)
        self.assertEqual(manager.cleanup(at=instant)['consultas'],0)
        self.client.get('/consultas-planilha/'+token)
        self.assertEqual(manager.cleanup(at=instant+1)['consultas'],1)

    def test_retention_preserves_running_and_leased_files(self):
        import time
        from spreadsheet_lookup import RETENTION_SECONDS
        token=self.upload([[self.cpfs[0],'Cliente','']]);self.execute(token)
        manager=self.app.lookup_manager;instant=time.time()
        with manager.file_access(token):
            manager.store.update(token,retencao_desde=instant-RETENTION_SECONDS-1)
            self.assertEqual(manager.cleanup(at=instant)['consultas'],0)
        manager.running.add(token)
        try:
            self.assertEqual(manager.cleanup(at=instant)['consultas'],0)
            self.assertTrue(manager.result_path(token).exists())
        finally:manager.running.remove(token)
        manager.store.update(token,estado='processando',retencao_desde=instant-RETENTION_SECONDS-1)
        self.assertEqual(manager.cleanup(at=instant)['consultas'],0)
        manager.store.update(token,estado='interrompida')
        self.assertEqual(manager.cleanup(at=instant)['consultas'],1)

    def test_retention_refuses_unexpected_files_and_preserves_upload_and_other_folders(self):
        import time
        from spreadsheet_lookup import RETENTION_SECONDS
        token=self.upload([[self.cpfs[0],'Cliente','']])
        manager=self.app.lookup_manager
        unrelated=manager.store.root/'fora_da_consulta';unrelated.mkdir()
        (unrelated/'entrada.csv').write_text('Preservar',encoding='utf-8')
        note=manager.store.root/token/'anotacao.txt';note.write_text('Preservar',encoding='utf-8')
        manager.store.update(token,retencao_desde=time.time()-RETENTION_SECONDS-1)
        self.assertEqual(manager.cleanup()['consultas'],0)
        self.assertTrue(note.exists())
        self.assertTrue(manager.source_path(manager.store.get(token)).exists())
        self.assertTrue((unrelated/'entrada.csv').exists())

    def test_retention_migrates_old_metadata_using_completion_time(self):
        from spreadsheet_lookup import LookupStore,RETENTION_SECONDS
        from datetime import datetime,timedelta,timezone
        import time
        root=self.root/'metadata_antiga';root.mkdir()
        token='d'*32
        instant=time.time();completed=instant-RETENTION_SECONDS-30
        local=lambda value:datetime.fromtimestamp(value,tz=timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M:%S')
        old=sqlite3.connect(root/'consultas.sqlite3')
        try:
            old.execute('CREATE TABLE consultas(token TEXT PRIMARY KEY,usuario_id INTEGER,arquivo TEXT,estado TEXT,resumo TEXT,criado_em TEXT)')
            old.execute('INSERT INTO consultas VALUES (?,?,?,?,?,?)',(token,1,'antiga.csv','concluida',json.dumps({'concluido_em':local(completed)}),local(instant-10*86400)))
            old.commit()
        finally:old.close()
        store=LookupStore(root);job=store.get(token)
        self.assertAlmostEqual(job['retencao_desde'],completed,delta=1.1)
        self.assertIsNone(job['expirado_em'])

    def test_retention_retries_files_in_use_without_losing_history(self):
        import time
        from spreadsheet_lookup import RETENTION_SECONDS
        token=self.upload([[self.cpfs[0],'Cliente','']]);self.execute(token)
        manager=self.app.lookup_manager
        manager.store.update(token,retencao_desde=time.time()-RETENTION_SECONDS-1)
        with patch('pathlib.Path.unlink',side_effect=PermissionError('arquivo em uso')):
            self.assertEqual(manager.cleanup()['consultas'],0)
        self.assertIsNone(manager.store.get(token)['expirado_em'])
        self.assertTrue(manager.result_path(token).exists())
        self.assertEqual(manager.cleanup()['consultas'],1)

    def test_retention_worker_cleans_on_start_and_stops_cleanly(self):
        import time
        from spreadsheet_lookup import RETENTION_SECONDS
        token=self.upload([[self.cpfs[0],'Cliente','']])
        manager=self.app.lookup_manager
        manager.store.update(token,estado='fila',retencao_desde=time.time()-RETENTION_SECONDS-1)
        try:
            manager.start_retention()
            first_thread=manager.retention_thread
            manager.start_retention()
            self.assertIs(manager.retention_thread,first_thread)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and not manager.store.get(token)['expirado_em']:
                time.sleep(.02)
            self.assertIsNotNone(manager.store.get(token)['expirado_em'])
        finally:manager.stop_retention()
        self.assertFalse(manager.retention_thread.is_alive())
