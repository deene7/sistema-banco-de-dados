import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
import json
import sqlite3
import tempfile
import unittest
from app import create_app, cpf_valid, normalize_phone
from demo import create_demo


def synthetic_cpf(base):
    value = f'{base:09d}'
    for size in (9, 10):
        value += str((sum(int(value[i]) * (size + 1 - i) for i in range(size)) * 10 % 11) % 10)
    return value


class SystemTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='teste_', dir=Path(__file__).resolve().parents[1])
        cls.seed = Path(cls.temp.name) / 'seed.sqlite3'
        create_demo(cls.seed)

    @classmethod
    def tearDownClass(cls):
        Path(cls.temp.name).resolve().relative_to(Path(__file__).resolve().parents[1])
        cls.temp.cleanup()

    def setUp(self):
        import shutil
        self.path = Path(self.temp.name) / (self.id().split('.')[-1] + '.sqlite3')
        shutil.copyfile(self.seed, self.path)
        self.app = create_app({'sqlite_path': str(self.path), 'secret_key': 'test-only', 'demo': True})
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def login(self, role='demo', client=None):
        client = client or self.client
        client.get('/login')
        with client.session_transaction() as session:
            csrf = session['csrf']
        response = client.post('/login', data={'csrf': csrf, 'login': role, 'senha': 'Demonstracao!2026'})
        self.assertEqual(response.status_code, 302)

    def post(self, path, data=None, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            token = session['csrf']
        return client.post(path, data={'csrf': token, **(data or {})})

    def sql(self, query, params=()):
        conn = sqlite3.connect(self.path)
        try:
            rows = conn.execute(query, params).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def test_csv_export_filters_contacts_formula_protection_and_permission(self):
        import csv,io
        self.login()
        self.sql("UPDATE clientes SET nome='=formula fictícia' WHERE id=1")
        response=self.client.get('/clientes/exportar.csv?telefone=com')
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['Content-Disposition'])
        rows=list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),1)
        self.assertEqual(next(row for row in rows if row['ID']=='1')['Nome'],"'=formula fictícia")
        self.assertTrue(next(row for row in rows if row['ID']=='1')['Telefones'])
        self.assertTrue(next(row for row in rows if row['ID']=='1')['CPF'].count('.')==2)
        filtered=self.client.get('/clientes/exportar.csv?q=Cliente de exemplo — Bruno')
        parsed=list(csv.DictReader(io.StringIO(filtered.data.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(parsed),1)
        other=self.app.test_client();self.login('atendimento',other)
        self.assertEqual(other.get('/clientes/exportar.csv').status_code,403)
        self.assertNotIn(b'Exportar CSV',other.get('/').data)

    def test_csv_export_crosses_batch_boundary_without_duplicates(self):
        import csv,io
        self.login()
        conn=sqlite3.connect(self.path)
        conn.executemany('INSERT INTO clientes(cpf,nome) VALUES (?,?)',[(synthetic_cpf(500000000+i),'Exportação fictícia') for i in range(1005)])
        conn.commit();conn.close()
        response=self.client.get('/clientes/exportar.csv?q=Exporta&por_pagina=25&pagina=9')
        rows=list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),1005)
        self.assertEqual(len({row['ID'] for row in rows}),1005)

    def test_home_counts_links_and_role_visibility(self):
        self.assertEqual(self.client.get('/inicio').status_code,302)
        for role in ('demo','ti','atendimento'):
            client=self.app.test_client();self.login(role,client)
            response=client.get('/inicio')
            self.assertEqual(response.status_code,200)
            for text in ('Visão geral','Sem nome informado','Sem telefone','Sem e-mail','Novo cliente'):
                self.assertIn(text.encode(),response.data)
            self.assertIn(b'nome=sem',response.data)
            self.assertIn(b'telefone=sem',response.data)
            if role=='atendimento':
                self.assertNotIn('Importações recentes'.encode(),response.data)
                self.assertNotIn('Atividades recentes'.encode(),response.data)
                self.assertNotIn(b'/configuracoes/logs',response.data)
            else:
                self.assertIn('Importações recentes'.encode(),response.data)
                self.assertIn('Atividades recentes'.encode(),response.data)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0],3)

    def test_pages_render_all_roles(self):
        for role in ('demo', 'ti', 'atendimento'):
            client = self.app.test_client()
            self.login(role, client)
            paths = ['/', '/?q=Cliente', '/?q=11111111111', '/clientes/novo', '/clientes/1',
                     '/clientes/1/contatos/telefone/adicionar', '/minha-senha']
            if role != 'atendimento':
                paths += ['/clientes/1/editar', '/clientes/1/contatos/email/adicionar',
                          '/clientes/1/contatos/endereco/adicionar', '/?lixeira=1']
            if role == 'demo':
                paths.append('/usuarios')
            for path in paths:
                self.assertEqual(client.get(path).status_code, 200, (role, path))

    def test_authentication_and_csrf(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.login()
        self.assertEqual(self.client.post('/clientes/1/excluir', data={'versao': 1}).status_code, 400)
        self.assertIsNone(self.sql('SELECT excluido_em FROM clientes WHERE id=1')[0][0])

    def test_attendance_cannot_edit_delete_restore_or_manage(self):
        self.login('atendimento')
        blocked = ['/clientes/1/editar', '/clientes/1/excluir', '/clientes/1/restaurar',
                   '/clientes/1/contatos/telefone/1/editar', '/clientes/1/contatos/telefone/1/excluir',
                   '/clientes/1/contatos/telefone/1/restaurar', '/clientes/1/contatos/email/adicionar',
                   '/clientes/1/contatos/endereco/adicionar', '/usuarios', '/usuarios/2/situacao']
        for path in blocked:
            self.assertEqual(self.post(path, {'versao': '1', 'motivo': 'teste'}).status_code, 403, path)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_auditoria')[0][0], 0)
        self.assertEqual(self.client.get('/historico/1').status_code, 403)

    def test_attendance_add_phone_audit_and_duplicate(self):
        self.login('atendimento')
        path = '/clientes/1/contatos/telefone/adicionar'
        response = self.post(path, {'versao': '1', 'numero': '+55 (11) 90000-0000'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.sql("SELECT numero,situacao FROM telefones WHERE id=2")[0], ('11900000000', 'nao_verificado'))
        self.assertEqual(self.sql('SELECT app_versao FROM clientes WHERE id=1')[0][0], 2)
        audit = self.sql('SELECT usuario_id,acao,entidade,depois FROM app_auditoria')[0]
        self.assertEqual(audit[:3], (2, 'adicionar', 'telefone'))
        self.assertEqual(json.loads(audit[3])['numero'], '11900000000')
        self.assertEqual(self.client.get('/clientes/1').status_code, 200)
        duplicate = self.post(path, {'versao': '2', 'numero': '11900000000'})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM app_auditoria')[0][0], 1)

    def test_attendance_cannot_set_confirmed_status(self):
        self.login('atendimento')
        response = self.post('/clientes/1/contatos/telefone/adicionar',
                             {'versao': '1', 'numero': '11900000000', 'situacao': 'confirmado'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones')[0][0], 1)

    def test_concurrent_edits_reject_stale_version(self):
        self.login()
        other = self.app.test_client()
        self.login('ti', other)
        data = {'versao': '1', 'cpf': synthetic_cpf(901234560), 'nome': 'Nome atualizado'}
        self.assertEqual(self.post('/clientes/1/editar', data).status_code, 302)
        data['nome'] = 'Tentativa antiga'
        result = self.post('/clientes/1/editar', data, other)
        self.assertEqual(result.status_code, 400)
        self.assertIn('Outra pessoa'.encode(), result.data)
        self.assertEqual(self.sql('SELECT nome FROM clientes WHERE id=1')[0][0], 'Nome atualizado')

    def test_soft_delete_restore_and_contact_protection(self):
        self.login()
        self.assertEqual(self.post('/clientes/1/excluir', {'versao': '1', 'motivo': 'Revisão'}).status_code, 302)
        attendance = self.app.test_client()
        self.login('atendimento', attendance)
        self.assertEqual(attendance.get('/clientes/1').status_code, 404)
        self.assertEqual(self.post('/clientes/1/contatos/telefone/adicionar',
                                   {'versao': '2', 'numero': '11900000000'}).status_code, 400)
        self.assertEqual(self.client.get('/?lixeira=1').status_code, 200)
        self.assertEqual(self.post('/clientes/1/restaurar', {'versao': '2', 'motivo': 'Retorno'}).status_code, 302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM telefones WHERE cliente_id=1')[0][0], 1)
        self.assertIsNone(self.sql('SELECT excluido_em FROM clientes WHERE id=1')[0][0])
        self.assertEqual(self.client.get('/historico/1').status_code, 200)

    def test_contact_edit_exclude_restore(self):
        self.login('ti')
        base = '/clientes/1/contatos/telefone/1/'
        self.assertEqual(self.post(base+'editar', {'versao':'1','numero':'11900000000','situacao':'confirmado'}).status_code, 302)
        self.assertEqual(self.post(base+'excluir', {'versao':'2','motivo':'Número antigo'}).status_code, 302)
        self.assertEqual(self.post(base+'restaurar', {'versao':'3','motivo':'Correção'}).status_code, 302)
        row = self.sql('SELECT numero,situacao,excluido_em FROM telefones WHERE id=1')[0]
        self.assertEqual(row, ('11900000000', 'confirmado', None))

    def test_new_client_atomic_and_unique_cpf(self):
        self.login('atendimento')
        cpf = synthetic_cpf(901234561)
        data = {'cpf': cpf, 'nome': 'Novo exemplo', 'telefone': '(11) 90000-0000'}
        self.assertEqual(self.post('/clientes/novo', data).status_code, 302)
        self.assertEqual(self.post('/clientes/novo', data).status_code, 302)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes WHERE cpf=?', (cpf,))[0][0], 1)
        before = self.sql('SELECT COUNT(*) FROM clientes')[0][0]
        data.update(cpf=synthetic_cpf(901234562), telefone='123')
        self.assertEqual(self.post('/clientes/novo', data).status_code, 400)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM clientes')[0][0], before)

    def test_user_revocation_and_no_self_lockout(self):
        self.login()
        attendance = self.app.test_client()
        self.login('atendimento', attendance)
        self.assertEqual(self.post('/usuarios/1/situacao', {'motivo':'teste'}).status_code, 400)
        self.assertEqual(self.post('/usuarios/2/situacao', {'motivo':'Desligamento'}).status_code, 302)
        self.assertEqual(attendance.get('/').status_code, 302)
        self.assertEqual(self.sql('SELECT ativo FROM app_usuarios WHERE id=1')[0][0], 1)

    def test_new_user_and_password_change(self):
        self.login()
        data = {'nome':'Pessoa exemplo','login':'nova.pessoa','perfil':'TI','senha':'SenhaFicticia!2026'}
        self.assertEqual(self.post('/usuarios', data).status_code, 302)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM app_usuarios WHERE login='nova.pessoa'")[0][0], 1)
        self.assertEqual(self.post('/minha-senha', {'atual':'Demonstracao!2026','nova':'OutraFicticia!2026',
                                                   'confirmacao':'OutraFicticia!2026'}).status_code, 302)
        self.assertEqual(self.client.get('/').status_code, 302)
        audit = self.sql("SELECT antes,depois FROM app_auditoria WHERE acao='trocar_senha'")[0]
        self.assertEqual(audit, (None,None))

    def test_login_rate_limit(self):
        self.client.get('/login')
        for _ in range(10):
            self.assertEqual(self.post('/login', {'login':'naoexiste','senha':'incorreta'}).status_code, 200)
        self.assertEqual(self.post('/login', {'login':'naoexiste','senha':'incorreta'}).status_code, 429)

    def test_search_escaping_and_html_safety(self):
        self.login()
        self.assertEqual(self.client.get('/?q=%25').status_code, 200)
        self.assertNotIn(b'Cliente de exemplo', self.client.get('/?q=%25').data)
        self.sql("UPDATE clientes SET nome='<script>alert(1)</script>' WHERE id=1")
        response = self.client.get('/clientes/1')
        self.assertNotIn(b'<script>alert(1)</script>', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    def test_email_address_addition_and_history(self):
        self.login()
        self.assertEqual(self.post('/clientes/1/contatos/email/adicionar',
                                   {'versao':'1','email':'outro@example.invalid','situacao':'nao_verificado'}).status_code, 302)
        self.assertEqual(self.post('/clientes/1/contatos/endereco/adicionar',
                                   {'versao':'2','logradouro':'Rua exemplo','numero':'10A','uf':'sp','cep':'00123456'}).status_code, 302)
        self.assertEqual(self.sql('SELECT uf,cep FROM enderecos WHERE id=2')[0], ('SP','00123456'))
        self.assertEqual(self.client.get('/historico/2').status_code, 200)

    def test_name_filters_and_default_order(self):
        self.sql('UPDATE clientes SET nome=NULL WHERE id=2')
        self.login()
        response = self.client.get('/?nome=com')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'Nome n\xc3\xa3o informado', response.data)
        response = self.client.get('/?nome=sem')
        self.assertIn(b'Nome n\xc3\xa3o informado', response.data)
        self.assertNotIn('Cliente de exemplo — Ana'.encode(), response.data)
        response = self.client.get('/')
        self.assertLess(response.data.index('Cliente de exemplo — Ana'.encode()),
                        response.data.index('Nome não informado'.encode()))

    def test_combined_contact_and_state_filters(self):
        self.login('atendimento')
        response = self.client.get('/?telefone=com&email=com&uf=SP&nome=com')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Cliente de exemplo — Ana'.encode(), response.data)
        self.assertNotIn('Cliente de exemplo — Bruno'.encode(), response.data)
        # Vários endereços para o mesmo cliente não podem duplicá-lo na lista.
        self.sql("INSERT INTO enderecos (cliente_id,logradouro,uf) VALUES (1,'Outra rua fictícia','SP')")
        response = self.client.get('/?uf=SP')
        self.assertEqual(response.data.count('Cliente de exemplo — Ana'.encode()), 1)
        self.sql("UPDATE telefones SET excluido_em=CURRENT_TIMESTAMP WHERE cliente_id=1")
        response = self.client.get('/?telefone=com')
        self.assertNotIn('Cliente de exemplo — Ana'.encode(), response.data)
        response = self.client.get('/?telefone=sem&email=com')
        self.assertIn('Cliente de exemplo — Ana'.encode(), response.data)

    def test_pagination_keeps_filters(self):
        for i in range(100, 130):
            self.sql('INSERT INTO clientes (id,cpf) VALUES (?,?)', (i,'9'+f'{i:010d}'))
        self.login()
        response = self.client.get('/?nome=sem&telefone=sem&email=sem&ordem=recentes')
        self.assertIn(b'pagina=2', response.data)
        for field in (b'nome=sem',b'telefone=sem',b'email=sem',b'ordem=recentes'):
            self.assertIn(field,response.data)
        response = self.client.get('/?pagina=2&nome=sem&telefone=sem&email=sem&ordem=recentes')
        self.assertEqual(response.data.count(b'Nome n\xc3\xa3o informado'),5)

    def test_filter_values_are_allowlisted(self):
        self.login()
        response = self.client.get('/?nome=INVALIDO&ordem=INVALIDO&uf=XX%27+OR+1%3D1')
        self.assertEqual(response.status_code,200)
        self.assertIn('Cliente de exemplo — Ana'.encode(),response.data)

    def test_settings_for_each_role(self):
        for role in ('demo','ti','atendimento'):
            client = self.app.test_client()
            self.login(role, client)
            response = client.get('/configuracoes')
            self.assertEqual(response.status_code,200)
            self.assertIn('Alterar minha senha'.encode(),response.data)
            self.assertEqual(b'Gerenciar equipe' in response.data, role == 'demo')
            navigation = response.data.split(b'<nav>')[1].split(b'</nav>')[0]
            self.assertIn('Configurações'.encode(),navigation)
            self.assertNotIn(b'Minha senha',navigation)
            self.assertEqual(client.get('/minha-senha').status_code,200)

    def test_cpf_formatting_and_server_limits(self):
        self.login()
        cpf = synthetic_cpf(1234567)
        formatted = '('+cpf[:3]+') '+cpf[3:6]+'/'+cpf[6:9]+'-'+cpf[9:]
        result = self.post('/clientes/novo',{'cpf':formatted,'nome':'Exemplo de máscara',
                                           'telefone':'(11) 90000-0000'})
        self.assertEqual(result.status_code,302)
        self.assertEqual(self.sql('SELECT cpf FROM clientes WHERE cpf=?',(cpf,))[0][0],cpf)
        for bad in (cpf+'1','11111111111'):
            result = self.post('/clientes/novo',{'cpf':bad,'nome':'Teste inválido'})
            self.assertEqual(result.status_code,400)
        result = self.post('/clientes/novo',{'cpf':synthetic_cpf(1234568),'nome':'Exemplo de limite',
                                           'telefone':'119000000001'})
        self.assertEqual(result.status_code,400)

    def test_direct_pagination_and_page_size(self):
        for i in range(100,171):
            self.sql('INSERT INTO clientes (id,cpf,nome) VALUES (?,?,?)',(i,'8'+f'{i:010d}',f'Página fictícia {i}'))
        self.login()
        response=self.client.get('/?por_pagina=50&pagina=2')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data.count(b'class="client-name'),24)
        self.assertIn(b'por_pagina=50',response.data)
        self.assertIn('Última página'.encode(),self.client.get('/').data)
        self.assertIn(b'name="pagina"',response.data)
        self.assertIn(b'Clientes por p',response.data)
        response=self.client.get('/?por_pagina=99999&pagina=999999')
        self.assertEqual(response.status_code,200)
        self.assertIn('Página 3 de 3'.encode(),response.data)

    def test_logs_permissions_filters_and_details(self):
        self.login()
        self.post('/clientes/1/contatos/telefone/adicionar',{'versao':'1','numero':'11900000000'})
        response=self.client.get('/configuracoes/logs')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'adicionar',response.data)
        response=self.client.get('/configuracoes/logs?acao=INEXISTENTE')
        self.assertNotIn(b'Ver detalhes',response.data)
        for kind in ('atividades','acessos','tecnico'):
            self.assertEqual(self.client.get('/configuracoes/logs?tipo='+kind).status_code,200)
        self.assertEqual(self.client.get('/configuracoes/logs?inicio=2026-11-01&fim=2026-10-01').status_code,400)
        self.post('/sair');self.login('ti')
        self.assertEqual(self.client.get('/configuracoes/logs').status_code,200)
        self.post('/sair');self.login('atendimento')
        self.assertEqual(self.client.get('/configuracoes/logs').status_code,403)

    def test_page_number_window(self):
        from pagination import page_numbers
        self.assertEqual(page_numbers(1,1),[1])
        self.assertEqual(page_numbers(50,100),[1,None,48,49,50,51,52,None,100])
        self.assertEqual(page_numbers(4,7),[1,2,3,4,5,6,7])


class ValidatorsTest(unittest.TestCase):
    def test_cpf_and_phone_boundaries(self):
        self.assertTrue(cpf_valid(synthetic_cpf(1234567)))
        for bad in ('', '00000000000', '11111111111', '12345678900', '123'):
            self.assertFalse(cpf_valid(bad))
        self.assertEqual(normalize_phone('+55 (11) 90000-0000'), '11900000000')
        for bad in ('111', '1190000000e1', '00000000000'):
            with self.assertRaises(ValueError):
                normalize_phone(bad)


if __name__ == '__main__':
    unittest.main(verbosity=2)
