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
from app import create_app
from demo import create_demo
from import_data import guess,normalize,parse_birth


def fake_cpf(base):
    value=f'{base:09d}'
    for n in (9,10):value+=str((sum(int(value[i])*(n+1-i) for i in range(n))*10%11)%10)
    return value


class FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='teste_v2_',dir=Path(__file__).resolve().parents[1])
        cls.seed=Path(cls.temp.name)/'seed.sqlite3';create_demo(cls.seed)

    @classmethod
    def tearDownClass(cls):
        Path(cls.temp.name).resolve().relative_to(Path(__file__).resolve().parents[1])
        cls.temp.cleanup()

    def setUp(self):
        self.root=Path(self.temp.name)/self.id().split('.')[-1];self.root.mkdir()
        self.path=self.root/'dados.sqlite3';shutil.copyfile(self.seed,self.path)
        uploads=self.root/'uploads';uploads.mkdir()
        self.config={'sqlite_path':str(self.path),'secret_key':'tests','demo':True,'uploads_dir':str(uploads)}
        self.app=create_app(self.config);self.app.config.update(TESTING=True,TESTING_SYNC_IMPORTS=True)
        self.client=self.app.test_client();self.login()

    def login(self,role='demo'):
        self.client.get('/login')
        with self.client.session_transaction() as session:token=session['csrf']
        self.client.post('/login',data={'csrf':token,'login':role,'senha':'Demonstracao!2026'})

    def post(self,path,data=None,**kwargs):
        with self.client.session_transaction() as session:token=session['csrf']
        return self.client.post(path,data={'csrf':token,**(data or {})},**kwargs)

    def sql(self,sql,params=()):
        conn=sqlite3.connect(self.path)
        try:
            rows=conn.execute(sql,params).fetchall();conn.commit();return rows
        finally:conn.close()

    def prepare_file(self,text,category=1):
        value=[str(cat) for cat in category] if isinstance(category,list) else ('' if category is None else str(category))
        response=self.post('/importacoes/enviar',{'categoria_id':value,'arquivo':(io.BytesIO(text.encode('utf-8-sig')),'base.csv')},content_type='multipart/form-data')
        self.assertEqual(response.status_code,302,response.data)
        job_id=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        headers=next(csv.reader(text.splitlines(),delimiter=';'))
        mapping={'coluna_'+str(i):value for i,value in enumerate(guess(headers))}
        result=self.post(f'/importacoes/{job_id}/mapear',mapping)
        self.assertEqual(result.status_code,302,result.data)
        return job_id

    def test_optional_category_fast_import_and_archive(self):
        self.app.config['TESTING_SYNC_IMPORTS']=False
        cpf=fake_cpf(900000001)
        job=self.prepare_file(f'CPF;NOME;TELEFONE\n{cpf};Pessoa fictícia;11988887777\n123;Inválido;\n',None)
        self.app.import_manager.worker.submit(lambda: None).result(timeout=10)
        self.assertEqual(self.sql('SELECT estado,categoria_id FROM app_importacoes WHERE id=?',(job,))[0],('revisao',None))
        review=self.client.get(f'/importacoes/{job}')
        self.assertIn(b'pending-card',review.data)
        self.assertIn(b'href="#pendencias"',review.data)
        self.assertEqual(self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'}).status_code,302)
        self.app.import_manager.worker.submit(lambda: None).result(timeout=10)
        self.assertEqual(self.sql('SELECT estado FROM app_importacoes WHERE id=?',(job,))[0][0],'concluida')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_cliente_categorias')[0][0],0)
        before=self.sql('SELECT COUNT(*) FROM clientes')[0][0]
        self.assertEqual(self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1','motivo':'Teste concluído'}).status_code,302)
        self.assertIsNotNone(self.sql('SELECT excluido_em FROM app_importacoes WHERE id=?',(job,))[0][0])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0],before)
        trash=self.client.get('/lixeira/importacoes')
        self.assertEqual(trash.status_code,200)
        self.assertIn(b'Lotes importados',trash.data)
        self.assertIn(b'base.csv',trash.data)
        self.assertNotIn(b'Nova importa',trash.data)
        self.assertIn(b'trash-tabs',self.client.get('/?lixeira=1').data)
        self.assertIn(b'base.csv',self.client.get('/importacoes?excluidas=1&q=base&categoria=sem').data)
        self.assertEqual(self.client.get(f'/importacoes/{job}').status_code,200)
        self.assertEqual(self.client.get(f'/importacoes/{job}/status').status_code,404)
        self.assertEqual(self.post(f'/importacoes/{job}/restaurar').status_code,302)
        self.assertIsNone(self.sql('SELECT excluido_em FROM app_importacoes WHERE id=?',(job,))[0][0])

    def test_batches_existing_clients_and_category_entry(self):
        text='CPF;NOME;TELEFONE\n'+''.join(f'{fake_cpf(800000000+i)};Pessoa fictícia {i};11988887777\n' for i in range(205))
        job=self.prepare_file(text)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_importacao_itens WHERE importacao_id=? AND estado='aplicado'",(job,))[0][0],205)
        second=self.prepare_file(text+'\n',None)
        self.assertIn(b'205',self.client.get(f'/importacoes/{second}/clientes-existentes').data)
        self.assertEqual(self.client.get(f'/importacoes/{second}/clientes-existentes?q=Pessoa&pagina=2').status_code,200)
        self.post(f'/importacoes/{second}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0],208)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_cliente_categorias')[0][0],205)
        self.assertIn(b'/?categoria=1',self.client.get('/categorias').data)
        self.assertIn(b'Pessoa fict',self.client.get('/?categoria=1').data)

    def test_admin_edits_permissions_and_revokes_old_session(self):
        self.assertIn(b'Editar permiss',self.client.get('/usuarios').data)
        target=self.sql("SELECT id,auth_versao FROM app_usuarios WHERE login='atendimento'")[0]
        other=self.app.test_client();other.get('/login')
        with other.session_transaction() as session:token=session['csrf']
        other.post('/login',data={'csrf':token,'login':'atendimento','senha':'Demonstracao!2026'})
        result=self.post(f'/usuarios/{target[0]}/permissoes',{'versao':str(target[1]),'perfil':'TI','gerenciar_usuarios':'1','motivo':'Mudança de função'})
        self.assertEqual(result.status_code,302)
        self.assertEqual(self.sql('SELECT perfil,gerenciar_usuarios,auth_versao FROM app_usuarios WHERE id=?',(target[0],))[0],('TI',1,target[1]+1))
        self.assertEqual(other.get('/').status_code,302)
        self.assertEqual(self.post(f'/usuarios/{target[0]}/permissoes',{'versao':str(target[1]),'perfil':'ADM','motivo':'Revisão antiga'}).status_code,400)
        admin=self.sql("SELECT id,auth_versao FROM app_usuarios WHERE login='demo'")[0]
        self.assertEqual(self.post(f'/usuarios/{admin[0]}/permissoes',{'versao':str(admin[1]),'perfil':'TI','motivo':'Teste'}).status_code,400)
        self.post('/sair');self.login('ti')
        self.assertEqual(self.post(f'/usuarios/{target[0]}/permissoes',{'versao':str(target[1]+1),'perfil':'ADM','motivo':'Teste'}).status_code,403)

    def test_import_permanent_purge_and_status_colors(self):
        cpf=fake_cpf(900000002)
        job=self.prepare_file(f'CPF;NOME\n{cpf};Teste fictício\n',None)
        html=self.client.get(f'/importacoes/{job}').data
        self.assertIn(b'clear-card',html);self.assertNotIn(b'stat-card pending-card',html)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertIn(b'status-success',self.client.get(f'/importacoes/{job}').data)
        url=f'/importacoes/{job}/excluir-definitivamente'
        self.assertEqual(self.client.get(url).status_code,400)
        self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1','motivo':'Teste'})
        self.assertIn(url.encode(),self.client.get('/lixeira/importacoes').data)
        before=self.sql('SELECT COUNT(*) FROM clientes')[0][0]
        self.assertEqual(self.post(url,{'confirmacao':str(job),'senha':'incorreta','motivo':'Teste'}).status_code,400)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_importacoes')[0][0],1)
        self.post('/sair');self.login('ti');self.assertEqual(self.client.get(url).status_code,403)
        self.post('/sair');self.login()
        self.assertEqual(self.post(url,{'confirmacao':str(job),'senha':'Demonstracao!2026','motivo':'Teste fictício'}).status_code,302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_importacoes')[0][0],0)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_importacao_itens')[0][0],0)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0],before-1)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_auditoria WHERE acao='excluir_importacao_permanentemente'")[0][0],1)

    def test_batch_delete_restore_and_purge_only_its_intact_new_clients(self):
        cpfs=[fake_cpf(700000001+i) for i in range(3)]
        text='CPF;NOME\n'+''.join(f'{cpf};Teste {i}\n' for i,cpf in enumerate(cpfs))
        job=self.prepare_file(text,None);self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.sql('UPDATE clientes SET app_versao=app_versao+1 WHERE cpf=?',(cpfs[1],))
        other=self.prepare_file(f'CPF;NOME\n{cpfs[2]};Teste 2\n',None)
        self.post(f'/importacoes/{other}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1'}).status_code,302)
        states=self.sql('SELECT cpf,excluido_em FROM clientes WHERE cpf IN (?,?,?) ORDER BY cpf',cpfs)
        self.assertIsNotNone(states[0][1]);self.assertIsNone(states[1][1]);self.assertIsNone(states[2][1])
        self.assertEqual(self.post(f'/importacoes/{job}/restaurar').status_code,302)
        self.assertIsNone(self.sql('SELECT excluido_em FROM clientes WHERE cpf=?',(cpfs[0],))[0][0])
        self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1'})
        self.assertIsNotNone(self.sql('SELECT excluido_em FROM clientes WHERE cpf=?',(cpfs[0],))[0][0])
        self.assertEqual(self.post(f'/importacoes/{job}/excluir-definitivamente',{'confirmacao':str(job),'senha':'Demonstracao!2026'}).status_code,302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE cpf=?',(cpfs[0],))[0][0],0)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE cpf IN (?,?)',cpfs[1:])[0][0],2)

    def test_admin_can_delete_without_reason_but_ti_requires_it(self):
        self.assertEqual(self.post('/clientes/1/excluir',{'versao':'1'}).status_code,302)
        self.post('/sair');self.login('ti')
        self.assertEqual(self.post('/clientes/2/excluir',{'versao':'1'}).status_code,400)

    def test_existing_cpf_fills_gaps_from_import_and_manual_create(self):
        cpf=fake_cpf(600000003)
        self.sql('INSERT INTO clientes (cpf) VALUES (?)',(cpf,))
        cid=self.sql('SELECT id FROM clientes WHERE cpf=?',(cpf,))[0][0]
        job=self.prepare_file(f'CPF;NOME;EMAIL\n{cpf};Nome recebido;origem@example.invalid\n',None)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT nome FROM clientes WHERE id=?',(cid,))[0][0],'Nome recebido')
        self.assertEqual(self.sql('SELECT email FROM emails WHERE cliente_id=?',(cid,))[0][0],'origem@example.invalid')
        response=self.client.get(f'/importacoes/{job}/clientes')
        self.assertEqual(response.status_code,200);self.assertIn(b'Nome recebido',response.data)
        self.assertIn(b'Clientes do lote',response.data)
        self.assertNotIn(b'topbar-account',response.data)
        self.sql('UPDATE clientes SET nome=NULL WHERE id=?',(cid,))
        result=self.post('/clientes/novo',{'cpf':cpf,'nome':'Nome manual','email':'novo@example.invalid'})
        self.assertEqual(result.status_code,302)
        self.assertEqual(self.sql('SELECT nome FROM clientes WHERE id=?',(cid,))[0][0],'Nome manual')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE cpf=?',(cpf,))[0][0],1)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM emails WHERE cliente_id=?',(cid,))[0][0],2)
        self.post('/clientes/novo',{'cpf':cpf,'nome':'Não substituir','email':'novo@example.invalid'})
        self.assertEqual(self.sql('SELECT nome FROM clientes WHERE id=?',(cid,))[0][0],'Nome manual')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM emails WHERE cliente_id=?',(cid,))[0][0],2)
        self.post('/sair');self.login('atendimento')
        self.sql('UPDATE clientes SET nome=NULL WHERE id=?',(cid,))
        self.post('/clientes/novo',{'cpf':cpf,'nome':'Não permitido','email':'outro@example.invalid','telefone':'11977776666'})
        self.assertIsNone(self.sql('SELECT nome FROM clientes WHERE id=?',(cid,))[0][0])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM emails WHERE cliente_id=?',(cid,))[0][0],2)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones WHERE cliente_id=?',(cid,))[0][0],1)

    def test_client_card_shows_creation_and_later_imports_with_file_categories_and_extras(self):
        cpf=fake_cpf(610000001)
        job=self.prepare_file(f'CPF;NOME;Observação adicional\n{cpf};Pessoa origem;Informação preservada\n',[1,2])
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        cid=self.sql('SELECT id FROM clientes WHERE cpf=?',(cpf,))[0][0]
        second=self.prepare_file(f'CPF;NOME;EMAIL\n{cpf};Pessoa origem;origemteste@example.invalid\n',None)
        self.post(f'/importacoes/{second}/confirmar',{'confirmacao':'1'})
        response=self.client.get(f'/clientes/{cid}')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Origem do cadastro',response.data)
        self.assertLess(response.data.index(b'Origem do cadastro'),response.data.index(b'<h2>Categorias</h2>'))
        self.assertIn(b'CLT',response.data);self.assertIn(b'SIAPE',response.data)
        self.assertIn(b'base.csv',response.data)
        self.assertIn(b'Criado neste lote',response.data)
        self.assertIn(b'Participou deste lote',response.data)
        self.assertIn('Informação preservada'.encode(),response.data)
        self.assertIn(f'href="/importacoes/{job}"'.encode(),response.data)
        self.assertIn(f'href="/importacoes/{second}"'.encode(),response.data)
        self.assertEqual(response.data.count(f'Lote #{job} ·'.encode()),1)
        self.assertEqual(response.data.count(f'Lote #{second} ·'.encode()),1)

    def test_client_origins_support_legacy_links_audit_and_archived_lots(self):
        cpf=fake_cpf(610000002)
        job=self.prepare_file(f'CPF;NOME\n{cpf};Pessoa histórica\n',None)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        cid=self.sql('SELECT id FROM clientes WHERE cpf=?',(cpf,))[0][0]
        self.sql('UPDATE app_importacao_itens SET cliente_id_resultado=NULL WHERE importacao_id=?',(job,))
        response=self.client.get(f'/clientes/{cid}')
        self.assertIn(f'Lote #{job} ·'.encode(),response.data)
        self.sql('DELETE FROM app_importacao_itens WHERE importacao_id=?',(job,))
        self.sql('UPDATE app_importacoes SET excluido_em=CURRENT_TIMESTAMP WHERE id=?',(job,))
        response=self.client.get(f'/clientes/{cid}')
        self.assertEqual(response.status_code,200)
        self.assertIn(f'Lote #{job} ·'.encode(),response.data)
        self.assertIn(b'Criado neste lote',response.data)
        self.assertIn(b'Este lote est',response.data)
        self.assertEqual(self.client.get(f'/importacoes/{job}').status_code,200)
        self.assertIn(b'Nenhum lote de importa',self.client.get('/clientes/1').data)
        self.post('/sair');self.login('atendimento')
        response=self.client.get(f'/clientes/{cid}')
        self.assertEqual(response.status_code,200)
        self.assertNotIn(b'Origem do cadastro',response.data)
        self.assertNotIn(f'href="/importacoes/{job}"'.encode(),response.data)

    def test_multiple_categories_filter_dedup_and_recheck(self):
        cpf=fake_cpf(600000004);text=f'CPF;NOME\n{cpf};Cliente múltiplas categorias\n'
        job=self.prepare_file(text,[2,1,2])
        self.assertEqual(json.loads(self.sql('SELECT mapeamento FROM app_importacoes WHERE id=?',(job,))[0][0])['categorias'],[1,2])
        self.assertIn(b'SIAPE',self.client.get(f'/importacoes/{job}').data)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT categoria_id FROM app_cliente_categorias ORDER BY categoria_id'),[(1,),(2,)])
        self.assertIn(f'#{job}'.encode(),self.client.get('/importacoes?categoria=2').data)
        # Mesmos bytes com outro conjunto de categorias devem gerar outro lote.
        second=self.prepare_file(text,1);self.assertNotEqual(job,second)
        self.post(f'/importacoes/{second}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_cliente_categorias')[0][0],2)
        response=self.post('/importacoes/enviar',{'categoria_id':['2','1'],'arquivo':(io.BytesIO(text.encode('utf-8-sig')),'base.csv')},content_type='multipart/form-data')
        self.assertTrue(response.location.endswith(f'/importacoes/{job}'))
        self.post(f'/importacoes/{job}/revisar-novamente')
        last=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        self.assertEqual(json.loads(self.sql('SELECT mapeamento FROM app_importacoes WHERE id=?',(last,))[0][0])['categorias'],[1,2])
        self.sql("UPDATE app_importacoes SET estado='preparando' WHERE id=?",(last,))
        self.assertEqual(self.post('/categorias/2/excluir',{'versao':'1'}).status_code,400)

    def test_xlsx_upload_multiple_categories_and_sheet_selection(self):
        from openpyxl import Workbook
        book=Workbook();sheet=book.active;sheet.title='Clientes'
        sheet.append(['CPF','NOME','EMAIL']);sheet.append([fake_cpf(600000005),'Cliente Excel','excel@example.invalid'])
        book.create_sheet('Outra aba')
        stream=io.BytesIO();book.save(stream);book.close();stream.seek(0)
        result=self.post('/importacoes/enviar',{'categoria_id':['1','2'],'arquivo':(stream,'clientes.xlsx')},content_type='multipart/form-data')
        self.assertEqual(result.status_code,302)
        job=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        self.assertEqual(self.client.get(result.location).status_code,200)
        self.post(f'/importacoes/{job}/mapear',{'aba':'Clientes','coluna_0':'cpf','coluna_1':'nome','coluna_2':'email1'})
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT estado FROM app_importacoes WHERE id=?',(job,))[0][0],'concluida')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_cliente_categorias')[0][0],2)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM emails WHERE email='excel@example.invalid'")[0][0],1)

    def test_optional_data_warnings_do_not_block_valid_identity(self):
        cpf=fake_cpf(600000006)
        text=f'CPF;NOME;TELEFONE;EMAIL;NASC;LOGRADOURO;CEP;UF\n{cpf};Cliente válido;abc;email errado;99/99/2000;;xyz;ESTADO\n'
        job=self.prepare_file(text,None)
        self.assertEqual(self.sql('SELECT estado FROM app_importacao_itens WHERE importacao_id=?',(job,))[0][0],'pronto')
        summary=json.loads(self.sql('SELECT resumo FROM app_importacoes WHERE id=?',(job,))[0][0])
        self.assertEqual(summary['pendencias'],0);self.assertEqual(summary['avisos_itens'],1)
        self.assertIn('não impedem a importação'.encode(),self.client.get(f'/importacoes/{job}').data)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        cid=self.sql('SELECT id FROM clientes WHERE cpf=?',(cpf,))[0][0]
        for table in ('telefones','emails','enderecos'):
            self.assertEqual(self.sql(f'SELECT COUNT(*) FROM {table} WHERE cliente_id=?',(cid,))[0][0],0)
        # Apenas os campos ruins são desconsiderados em um endereço aproveitável.
        other=self.prepare_file(f'CPF;NOME;LOGRADOURO;CEP;UF\n{cpf};Cliente válido;Rua fictícia;abc;ESTADO\n',None)
        self.post(f'/importacoes/{other}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT logradouro,cep,uf FROM enderecos WHERE cliente_id=?',(cid,))[0],('Rua fictícia',None,None))
        bad_name=self.prepare_file(f'CPF;NOME\n{cpf};Nome diferente\n',None)
        self.assertEqual(self.sql('SELECT estado FROM app_importacao_itens WHERE importacao_id=?',(bad_name,))[0][0],'pendente')

    def test_optional_conflicts_preserve_existing_values_and_allow_reanalysis(self):
        cpf=fake_cpf(600000007)
        self.sql("INSERT INTO clientes(cpf,nome,data_nascimento,nome_mae) VALUES (?,?,?,?)",(cpf,'Cliente válido','1980-01-01','Mãe anterior'))
        job=self.prepare_file(f'CPF;NOME;NASC;NOME_MAE\n{cpf};Cliente válido;19900101;Mãe diferente\n',None)
        self.assertEqual(self.sql('SELECT estado FROM app_importacao_itens WHERE importacao_id=?',(job,))[0][0],'pronto')
        self.assertEqual(self.post(f'/importacoes/{job}/revisar-novamente').status_code,302)
        new=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        self.post(f'/importacoes/{new}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT data_nascimento,nome_mae FROM clientes WHERE cpf=?',(cpf,))[0],('1980-01-01','Mãe anterior'))

    def test_bulk_lifecycle_crosses_chunks_without_per_client_queries(self):
        from database import Database
        from unittest.mock import patch
        text='CPF;NOME;TELEFONE\n'+''.join(f'{fake_cpf(550000000+i)};Teste em lote {i};11988887777\n' for i in range(1005))
        job=self.prepare_file(text,None)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_importacao_itens WHERE importacao_id=? AND estado='aplicado'",(job,))[0][0],1005)
        original=Database.execute;queries=[]
        writes=[0]
        def interrupt(connection,sql,params=()):
            if sql.startswith('UPDATE clientes SET excluido_em=CURRENT_TIMESTAMP'):
                writes[0]+=1
                if writes[0]==2:raise ValueError('Falha simulada antes do segundo bloco')
            return original(connection,sql,params)
        with patch.object(Database,'execute',interrupt):
            self.assertEqual(self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1'}).status_code,400)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE excluido_em IS NOT NULL')[0][0],0)
        self.assertIsNone(self.sql('SELECT excluido_em FROM app_importacoes WHERE id=?',(job,))[0][0])
        def observe(connection,sql,params=()):
            queries.append(sql);return original(connection,sql,params)
        with patch.object(Database,'execute',observe):
            self.assertEqual(self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1'}).status_code,302)
            self.assertLess(len(queries),80)
            self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE excluido_em IS NOT NULL')[0][0],1005)
            queries.clear()
            self.assertEqual(self.post(f'/importacoes/{job}/restaurar').status_code,302)
            self.assertLess(len(queries),80)
            self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE excluido_em IS NOT NULL')[0][0],0)
            self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1'})
            queries.clear()
            self.assertEqual(self.post(f'/importacoes/{job}/excluir-definitivamente',{'confirmacao':str(job),'senha':'Demonstracao!2026'}).status_code,302)
            self.assertLess(len(queries),100)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0],3)

    def test_bulk_existing_fields_keeps_identity_and_versions(self):
        conn=sqlite3.connect(self.path)
        conn.executemany('INSERT INTO clientes(cpf) VALUES (?)',[(fake_cpf(560000000+i),) for i in range(505)])
        conn.commit();conn.close()
        text='CPF;NOME;EMAIL\n'+''.join(f'{fake_cpf(560000000+i)};Nome fictício {i};teste{i}@example.invalid\n' for i in range(505))
        job=self.prepare_file(text,None);self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql("SELECT COUNT(*) FROM clientes WHERE nome LIKE 'Nome fictício %' AND app_versao=2")[0][0],505)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_importacao_itens WHERE importacao_id=? AND estado='aplicado'",(job,))[0][0],505)

    def test_lost_connection_error_does_not_requery_failed_database(self):
        from database import Database
        from unittest.mock import patch
        import pymysql
        job=self.prepare_file(f'CPF;NOME\n{fake_cpf(580000001)};Cliente fictício\n',None)
        original=Database.execute
        def fail(connection,sql,params=()):
            if getattr(connection,'simulated_failure',False):raise AssertionError('A tela de erro reutilizou a conexão perdida')
            if 'FROM app_importacao_itens x' in sql:
                connection.simulated_failure=True
                def broken_rollback():raise pymysql.err.InterfaceError(0,'')
                def broken_close():
                    connection.conn.close()
                    raise pymysql.err.InterfaceError(0,'')
                connection.rollback=broken_rollback;connection.close=broken_close
                raise pymysql.err.OperationalError(2013,'Falha de conexão simulada')
            return original(connection,sql,params)
        with patch.object(Database,'execute',fail):
            response=self.client.get(f'/importacoes/{job}/clientes')
            self.assertEqual(response.status_code,503)
            self.assertIn('conexão com o banco foi interrompida'.encode(),response.data)
            self.assertEqual(self.client.get('/').status_code,200)
        connection=Database(self.config)
        connection.close();connection.rollback();connection.close()

    def test_heavy_operation_does_not_queue_other_requests(self):
        job=self.prepare_file(f'CPF;NOME\n{fake_cpf(580000002)};Cliente fictício\n',None)
        manager=self.app.import_manager
        manager.work_lock.acquire()
        try:
            response=self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1'})
            self.assertEqual(response.status_code,400)
            self.assertIn('As consultas continuam disponíveis'.encode(),response.data)
            self.assertEqual(self.client.get('/').status_code,200)
            self.assertIsNone(self.sql('SELECT excluido_em FROM app_importacoes WHERE id=?',(job,))[0][0])
        finally:manager.work_lock.release()
        self.assertEqual(self.post(f'/importacoes/{job}/excluir',{'confirmacao':'1'}).status_code,302)

    def test_discadora_verifies_only_uploaded_active_phones_for_same_cpf(self):
        self.check_trusted_category_verification('Discadora')

    def test_manual_phone_verification_displays_author_and_date_without_guessing_legacy_data(self):
        result=self.post('/clientes/1/contatos/telefone/1/editar',{'versao':'1','numero':'11900000000','situacao':'confirmado'})
        self.assertEqual(result.status_code,302)
        response=self.client.get('/clientes/1')
        self.assertEqual(response.status_code,200)
        self.assertIn('Verificação manual'.encode(),response.data)
        self.assertIn('Administradora de demonstração'.encode(),response.data)
        self.assertNotIn('Origem e data de verificação não registradas'.encode(),response.data)
        self.sql("INSERT INTO telefones(cliente_id,numero,situacao) VALUES (1,'11911111111','confirmado')")
        self.assertIn('Origem e data de verificação não registradas'.encode(),self.client.get('/clientes/1').data)

    def test_processing_center_queue_interruption_pagination_and_bounded_api(self):
        import time
        for index in range(28):
            self.sql("INSERT INTO app_importacoes(arquivo,estado,resumo) VALUES (?,?,'{}')",(f'Lote fictício {index}.csv','concluida'))
        self.sql("INSERT INTO app_importacoes(arquivo,estado,resumo) VALUES ('fila.csv','preparando','{}')")
        queued=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        self.sql("INSERT INTO app_importacoes(arquivo,estado,progresso,resumo) VALUES ('ativo.csv','importando',5,?)",(json.dumps({'prontos':10,'avisos_amostra':[{'cpf':'privado'}]}),))
        running=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        self.sql("INSERT INTO app_importacoes(arquivo,estado,resumo) VALUES ('interrompido.csv','preparando','{}')")
        interrupted=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        manager=self.app.import_manager
        manager.running[queued]={'situacao':'fila','fase':'preparar','progresso':0,'total':None}
        manager.running[running]={'situacao':'executando','fase':'importar','progresso':5,'total':10,'inicio':time.monotonic()}
        try:
            response=self.client.get('/processamentos')
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Central de processamentos',response.data)
            self.assertIn(b'pagina=2',response.data)
            response=self.client.get(f'/processamentos/status?id={queued}&id={running}&id={interrupted}')
            self.assertEqual(response.status_code,200)
            jobs={job['id']:job for job in response.json['jobs']}
            self.assertEqual(jobs[queued]['situacao'],'fila')
            self.assertEqual(jobs[queued]['posicao'],1)
            self.assertEqual(jobs[running]['percentual'],50)
            self.assertEqual(jobs[interrupted]['situacao'],'interrompido')
            self.assertTrue(jobs[interrupted]['retomar'])
            self.assertNotIn(b'privado',response.data)
            self.assertNotIn('resumo',jobs[running])
            self.assertEqual(self.post(f'/importacoes/{queued}/retomar').status_code,400)
            self.assertIn(b'fila.csv',self.client.get('/processamentos?estado=fila').data)
            self.assertNotIn(b'ativo.csv',self.client.get('/processamentos?estado=fila').data)
            self.assertIn(b'ativo.csv',self.client.get('/processamentos?estado=importando').data)
            self.assertNotIn(b'fila.csv',self.client.get('/processamentos?estado=preparando').data)
            self.assertNotIn(b'interrompido.csv',self.client.get('/processamentos?estado=preparando').data)
            self.assertEqual(self.client.get('/processamentos/status?id=invalid').status_code,400)
            self.assertEqual(self.client.get('/processamentos/status?'+('&'.join('id=1' for _ in range(26)))).status_code,400)
        finally:
            manager.running.clear()

    def test_quality_totals_exclude_deleted_contacts_and_clients_and_deduplicate_coverage(self):
        self.sql("UPDATE telefones SET situacao='confirmado' WHERE cliente_id=1")
        self.sql("INSERT INTO telefones(cliente_id,numero,situacao) VALUES (1,'11900000000','confirmado'),(2,'11900000001','invalido'),(3,'11900000002','confirmado')")
        self.sql("INSERT INTO telefones(cliente_id,numero,situacao,excluido_em) VALUES (2,'11900000003','confirmado',CURRENT_TIMESTAMP)")
        self.sql('UPDATE clientes SET excluido_em=CURRENT_TIMESTAMP WHERE id=3')
        self.sql('INSERT INTO app_cliente_categorias(cliente_id,categoria_id) VALUES (1,1),(2,1),(3,1),(1,2)')
        self.app.quality_summary.load()
        response=self.client.get('/qualidade/resumo')
        self.assertEqual(response.status_code,200)
        stats=response.json['stats']
        self.assertEqual(stats['total'],2)
        self.assertEqual(stats['telefones'],3)
        self.assertEqual(stats['verificados'],2)
        self.assertEqual(stats['clientes_verificados'],1)
        self.assertEqual(stats['invalidos'],1)
        self.assertEqual(stats['cobertura_verificados'],50)
        categories={category['id']:category for category in stats['categorias']}
        self.assertEqual(categories[1]['clientes'],2)
        self.assertEqual(categories[1]['verificados'],1)
        self.assertEqual(categories[2]['verificados'],1)
        self.assertEqual(self.client.get('/qualidade').status_code,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_quality_loading_does_not_wait_and_operational_pages_require_editor(self):
        from unittest.mock import patch
        with patch.object(self.app.quality_summary,'background') as background:
            self.assertEqual(self.client.get('/qualidade').status_code,200)
            self.assertEqual(self.client.get('/qualidade/resumo').status_code,202)
            self.assertTrue(background.called)
        self.post('/sair');self.login('atendimento')
        for path in ('/processamentos','/processamentos/status?id=1','/qualidade','/qualidade/resumo'):
            self.assertEqual(self.client.get(path).status_code,403)

    def test_nova_vida_verifies_only_uploaded_active_phones_for_same_cpf(self):
        self.check_trusted_category_verification('nova vida')

    def test_fonte_verificada_preserves_trusted_phone_rule(self):
        self.check_trusted_category_verification('Fonte verificada')

    def check_trusted_category_verification(self, name):
        self.post('/categorias',{'nome':name})
        category=self.sql('SELECT id FROM app_categorias WHERE nome=?',(name,))[0][0]
        cpf=fake_cpf(590000001);self.sql('UPDATE clientes SET cpf=? WHERE id=1',(cpf,))
        for number,status,deleted in [('11900000000','nao_verificado',None),('11900000001','inativo',None),('11900000002','invalido','2026-01-01'),('11900000003','nao_verificado',None)]:
            self.sql('INSERT INTO telefones(cliente_id,numero,situacao,excluido_em) VALUES (1,?,?,?)',(number,status,deleted))
        self.sql("INSERT INTO telefones(cliente_id,numero) VALUES (2,'11900000000')")
        text=f'CPF;CEL1;CEL2;CEL3;CEL4;EMAIL\n{cpf};11900000000;11900000001;11900000002;11900000004;origem@example.invalid\n'
        job=self.prepare_file(text,[category,1])
        summary=json.loads(self.sql('SELECT resumo FROM app_importacoes WHERE id=?',(job,))[0][0])
        self.assertTrue(summary['verificar_telefones']);self.assertEqual(summary['telefones_a_verificar'],3)
        self.assertIn('Origem de telefones'.encode(),self.client.get(f'/importacoes/{job}').data)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        for number in ('11900000000','11900000001','11900000004'):
            self.assertEqual(self.sql('SELECT situacao FROM telefones WHERE cliente_id=1 AND numero=?',(number,)),[('confirmado',)])
        self.assertEqual(self.sql("SELECT situacao,excluido_em FROM telefones WHERE cliente_id=1 AND numero='11900000002'")[0],('invalido','2026-01-01'))
        self.assertEqual(self.sql("SELECT situacao FROM telefones WHERE cliente_id=1 AND numero='11900000003'")[0][0],'nao_verificado')
        self.assertEqual(self.sql("SELECT situacao FROM telefones WHERE cliente_id=2 AND numero='11900000000'")[0][0],'nao_verificado')
        self.assertEqual(self.sql("SELECT situacao FROM emails WHERE email='origem@example.invalid'")[0][0],'nao_verificado')
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_auditoria WHERE acao='verificar_origem'")[0][0],3)
        self.assertEqual(json.loads(self.sql('SELECT resumo FROM app_importacoes WHERE id=?',(job,))[0][0])['telefones_verificados'],3)
        self.assertIn(b'Verificado',self.client.get('/clientes/1').data)
        self.assertIn('Verificado por importação'.encode(),self.client.get('/clientes/1').data)
        self.assertIn(name.encode(),self.client.get('/clientes/1').data)
        self.assertIn(f'Lote #{job}'.encode(),self.client.get('/clientes/1').data)
        self.post(f'/importacoes/{job}/revisar-novamente')
        again=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        self.post(f'/importacoes/{again}/confirmar',{'confirmacao':'1'})
        self.assertEqual(json.loads(self.sql('SELECT resumo FROM app_importacoes WHERE id=?',(again,))[0][0])['telefones_verificados'],0)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_auditoria WHERE acao='verificar_origem'")[0][0],3)

    def test_trusted_source_is_snapshot_and_reanalysis_can_verify_existing_phone(self):
        self.post('/categorias',{'nome':'Argo'})
        category=self.sql("SELECT id FROM app_categorias WHERE nome='Argo'")[0][0]
        cpf=fake_cpf(590000002);text=f'CPF;NOME;TELEFONE\n{cpf};Cliente fictício;11900000005\n'
        job=self.prepare_file(text,category)
        self.sql("UPDATE app_categorias SET nome='aRgUs' WHERE id=?",(category,))
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        cid=self.sql('SELECT id FROM clientes WHERE cpf=?',(cpf,))[0][0]
        self.assertEqual(self.sql('SELECT situacao FROM telefones WHERE cliente_id=?',(cid,)),[('nao_verificado',)])
        self.post(f'/importacoes/{job}/revisar-novamente')
        new=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        self.post(f'/importacoes/{new}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT situacao FROM telefones WHERE cliente_id=?',(cid,)),[('confirmado',)])
        # Ter Argus no cliente não torna confiável um lote comum posterior.
        regular=self.prepare_file(f'CPF;CEL1;CEL2\n{cpf};11900000005;11900000006\n',1)
        self.post(f'/importacoes/{regular}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql("SELECT situacao FROM telefones WHERE cliente_id=? AND numero='11900000006'",(cid,)),[('nao_verificado',)])
        self.assertEqual(self.sql("SELECT situacao FROM telefones WHERE cliente_id=? AND numero='11900000005'",(cid,)),[('confirmado',)])

    def test_invalid_identity_does_not_verify_trusted_source_phone(self):
        self.post('/categorias',{'nome':'ARGUS'})
        category=self.sql("SELECT id FROM app_categorias WHERE nome='ARGUS'")[0][0]
        cpf=fake_cpf(590000003);self.sql('UPDATE clientes SET cpf=? WHERE id=1',(cpf,))
        job=self.prepare_file(f'CPF;NOME;TELEFONE\n{cpf};Nome divergente;11000000000\n',category)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT situacao FROM telefones WHERE cliente_id=1'),[('nao_verificado',)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_auditoria WHERE acao='verificar_origem'")[0][0],0)

    def test_pages_and_category_management(self):
        for path in ['/categorias','/importacoes']:
            self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.post('/categorias',{'nome':'INSS','descricao':'Categoria de exemplo'}).status_code,302)
        cat_id=self.sql("SELECT id FROM app_categorias WHERE nome='INSS'")[0][0]
        self.assertEqual(self.post(f'/categorias/{cat_id}/editar',{'versao':'1','nome':'INSS revisado','descricao':''}).status_code,302)
        self.assertEqual(self.post(f'/categorias/{cat_id}/excluir',{'versao':'2'}).status_code,302)
        self.assertEqual(self.sql('SELECT ativo FROM app_categorias WHERE id=?',(cat_id,))[0][0],0)
        self.assertEqual(self.post(f'/categorias/{cat_id}/restaurar',{'versao':'3'}).status_code,302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0],3)

    def test_category_links_filter_and_removal(self):
        self.assertEqual(self.post('/clientes/1/categoria',{'versao':'1','categoria_id':'1'}).status_code,302)
        self.assertEqual(self.post('/clientes/1/categoria',{'versao':'2','categoria_id':'2'}).status_code,302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_cliente_categorias WHERE cliente_id=1')[0][0],2)
        response=self.client.get('/?categoria=1')
        self.assertIn('Cliente de exemplo — Ana'.encode(),response.data)
        self.assertNotIn('Cliente de exemplo — Bruno'.encode(),response.data)
        self.assertEqual(response.data.count('Cliente de exemplo — Ana'.encode()),1)
        response=self.client.get('/?categoria=sem')
        self.assertNotIn('Cliente de exemplo — Ana'.encode(),response.data)
        self.assertEqual(self.post('/clientes/1/categoria',{'versao':'3','categoria_id':'1','acao':'remover'}).status_code,302)

    def test_attendance_cannot_manage_or_import_or_purge(self):
        self.post('/sair');self.login('atendimento')
        for path in ['/categorias','/importacoes','/clientes/1/excluir-definitivamente']:
            self.assertEqual(self.client.get(path).status_code,403,path)
        self.assertEqual(self.post('/clientes/1/categoria',{'versao':'1','categoria_id':'1'}).status_code,403)
        self.assertEqual(self.post('/importacoes/enviar').status_code,403)

    def test_permanent_delete_requires_trash_admin_password_and_confirmation(self):
        self.assertEqual(self.client.get('/clientes/1/excluir-definitivamente').status_code,400)
        self.post('/clientes/1/excluir',{'versao':'1','motivo':'Revisão'})
        self.assertIn(b'Excluir permanentemente',self.client.get('/?lixeira=1').data)
        self.assertEqual(self.client.get('/clientes/1/excluir-definitivamente').status_code,200)
        data={'versao':'2','motivo':'Exclusão de teste','confirmacao':'11111111111','senha':'incorreta'}
        self.assertEqual(self.post('/clientes/1/excluir-definitivamente',data).status_code,400)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE id=1')[0][0],1)
        data['senha']='Demonstracao!2026'
        self.assertEqual(self.post('/clientes/1/excluir-definitivamente',data).status_code,302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE id=1')[0][0],0)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones WHERE cliente_id=1')[0][0],0)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_auditoria WHERE acao='excluir_permanentemente'")[0][0],1)
        self.assertEqual(self.sql("SELECT cliente_id FROM app_auditoria WHERE acao='excluir'")[0][0],None)

    def test_import_review_merge_pending_and_idempotence(self):
        cpf=fake_cpf(1234567)
        text='CPF;Nome;DDDCEL1;CEL1;EMAIL1;NASC;RENDA\n'
        text+=f'{int(cpf)};Pessoa fictícia;11;900000000;teste@example.invalid;19800102;1234\n'
        text+=f'{int(cpf)};Pessoa fictícia;11;900000001;teste@example.invalid;19800102;1234\n'
        text+='123;Outra pessoa;11;900000002;;19800102;999\n'
        job=self.prepare_file(text)
        summary=json.loads(self.sql('SELECT resumo FROM app_importacoes WHERE id=?',(job,))[0][0])
        self.assertEqual(summary['novos_clientes'],1)
        self.assertEqual(summary['novos_telefones'],2)
        self.assertEqual(summary['pendencias'],1)
        self.assertEqual(summary['zeros_recuperados'],2)
        self.assertIn('RENDA',summary['colunas_preservadas'])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0],3)
        self.assertEqual(self.client.get(f'/importacoes/{job}').status_code,200)
        self.assertEqual(self.client.get(f'/importacoes/{job}/pendencias.csv').status_code,200)
        self.assertEqual(self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'}).status_code,302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE cpf=?',(cpf,))[0][0],1)
        client=self.sql('SELECT id FROM clientes WHERE cpf=?',(cpf,))[0][0]
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones WHERE cliente_id=?',(client,))[0][0],2)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_cliente_categorias WHERE cliente_id=?',(client,))[0][0],1)
        self.assertEqual(self.sql('SELECT estado FROM app_importacoes WHERE id=?',(job,))[0][0],'concluida')
        self.assertEqual(self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'}).status_code,400)
        # Enviar novamente o mesmo arquivo devolve o recibo anterior.
        response=self.post('/importacoes/enviar',{'categoria_id':'1','arquivo':(io.BytesIO(text.encode('utf-8-sig')),'base.csv')},content_type='multipart/form-data')
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_importacoes')[0][0],1)

    def test_conflicts_and_deleted_clients_are_not_overwritten(self):
        cpf=fake_cpf(901234560)
        self.sql('UPDATE clientes SET cpf=?,nome=? WHERE id=1',(cpf,'Nome já existente'))
        job=self.prepare_file('CPF;NOME;CEL1\n'+cpf+';Outro nome;11900000000\n')
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_importacao_itens WHERE estado='pendente'")[0][0],1)
        self.assertEqual(self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'}).status_code,302)
        self.assertEqual(self.sql('SELECT nome FROM clientes WHERE id=1')[0][0],'Nome já existente')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones')[0][0],1)

        # Depois de corrigir o cadastro, uma nova revisão reaproveita o arquivo sem repetir o lote anterior.
        self.sql('UPDATE clientes SET nome=?,app_versao=app_versao+1 WHERE id=1',('Outro nome',))
        self.assertEqual(self.post(f'/importacoes/{job}/revisar-novamente').status_code,302)
        new_job=self.sql('SELECT MAX(id) FROM app_importacoes')[0][0]
        self.assertNotEqual(new_job,job)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_importacao_itens WHERE importacao_id=? AND estado='pronto'",(new_job,))[0][0],1)
        self.post(f'/importacoes/{new_job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones')[0][0],2)

    def test_concurrent_change_after_review_goes_to_pending(self):
        cpf=fake_cpf(901234561)
        self.sql('UPDATE clientes SET cpf=? WHERE id=1',(cpf,))
        job=self.prepare_file('CPF;CEL1\n'+cpf+';11900000000\n')
        self.sql('UPDATE clientes SET app_versao=app_versao+1 WHERE id=1')
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones')[0][0],1)
        self.assertEqual(self.sql('SELECT estado FROM app_importacao_itens')[0][0],'pendente')

    def test_partial_resume_does_not_repeat_applied_items(self):
        text='CPF;CEL1\n'+fake_cpf(901234562)+';11900000000\n'+fake_cpf(901234563)+';11900000001\n'
        job=self.prepare_file(text)
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.sql("UPDATE app_importacoes SET estado='erro',erro='Interrupção simulada' WHERE id=?",(job,))
        self.assertEqual(self.post(f'/importacoes/{job}/retomar').status_code,302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0],5)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones')[0][0],3)

    def test_purged_and_recreated_cpf_is_not_modified_by_old_review(self):
        cpf=fake_cpf(901234564)
        self.sql('UPDATE clientes SET cpf=? WHERE id=1',(cpf,))
        job=self.prepare_file('CPF;CEL1\n'+cpf+';11900000000\n')
        self.post('/clientes/1/excluir',{'versao':'1','motivo':'Teste'})
        self.post('/clientes/1/excluir-definitivamente',{'versao':'2','confirmacao':cpf,'motivo':'Teste','senha':'Demonstracao!2026'})
        self.post('/clientes/novo',{'cpf':cpf,'nome':'Cliente recriado fictício'})
        self.post(f'/importacoes/{job}/confirmar',{'confirmacao':'1'})
        self.assertEqual(self.sql('SELECT estado FROM app_importacao_itens WHERE importacao_id=?',(job,))[0][0],'pendente')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones')[0][0],0)


class ImportDataTests(unittest.TestCase):
    def test_common_headers_and_phone_slots(self):
        headers=['CPF','NOME','DDDCEL1','CEL1','DDDCEL2','CEL2','DDD1','FONE1','DT_NASC','EMAIL','OUTRO']
        self.assertEqual(guess(headers),['cpf','nome','ddd1','telefone1','ddd2','telefone2','ddd3','telefone3','data_nascimento','email1','extra'])
        self.assertEqual(guess(['CPF','TELEFONE1','DDD1','TELEFONE4','DDD4','EMAIL4']),
                         ['cpf','telefone1','ddd1','telefone4','ddd4','email4'])

    def test_normalize_dates_formulas_zero_cpf_and_unknown_fields(self):
        cpf=fake_cpf(1234567)
        data,errors,recovered=normalize([str(int(cpf)),'Exemplo','11','900000000','01/02/1980'],['cpf','nome','ddd1','telefone1','data_nascimento'])
        self.assertFalse(errors);self.assertTrue(recovered)
        self.assertEqual(data['cpf'],cpf);self.assertEqual(data['phones'],['11900000000'])
        self.assertEqual(data['fields']['data_nascimento'],'1980-02-01')
        _,errors,_=normalize([cpf,'=A1'],['cpf','nome'])
        self.assertTrue(any('Fórmula' in value for value in errors))
        with self.assertRaises(ValueError):parse_birth('31/02/1980')


if __name__=='__main__':unittest.main(verbosity=2)
