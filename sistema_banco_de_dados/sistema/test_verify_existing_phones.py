import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import tempfile
import unittest
from database import Database
from demo import create_demo
from import_data import file_digest
from phone_verification import verification_history
from verify_existing_phones import init_plan,apply,summary,validate


class SpreadsheetVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='teste_verificacao_',dir=Path(__file__).resolve().parents[1])
        self.folder=Path(self.temp.name)
        self.database=self.folder/'demo.sqlite3';create_demo(self.database)
        self.config={'sqlite_path':str(self.database)}
        self.source=self.folder/'origem-ficticia.txt';self.source.write_bytes(b'origem ficticia para teste')
        self.path=self.folder/'plano.sqlite3'
        self.plan=init_plan(self.path,{'operacao':'teste-verificacao','arquivo':self.source.name,'sha256':file_digest(self.source)})

    def tearDown(self):
        self.plan.close()
        self.folder.resolve().relative_to(Path(__file__).resolve().parents[1])
        self.temp.cleanup()

    def pair(self,cpf,number,client,phone,version=1,status='nao_verificado',classification='a_verificar'):
        self.plan.execute('INSERT INTO pares VALUES (?,?,?,?,?,?,?,?,?)',(cpf,number,'Aba fictícia',2,classification,client,phone,version,status))

    def test_exact_pairs_audit_backup_exclusions_concurrency_and_idempotence(self):
        db=Database(self.config)
        try:
            db.execute("INSERT INTO telefones(id,cliente_id,numero,situacao) VALUES (2,1,'11900000000','invalido'),(3,1,'11900000001','nao_verificado'),(4,2,'11900000002','nao_verificado'),(5,2,'11900000003','nao_verificado'),(6,3,'11900000004','nao_verificado'),(7,1,'11900000005','confirmado')")
            db.execute('UPDATE telefones SET excluido_em=CURRENT_TIMESTAMP WHERE id=4')
            db.execute('UPDATE clientes SET excluido_em=CURRENT_TIMESTAMP WHERE id=3')
            db.commit()
        finally:db.close()
        self.pair('11111111111','11000000000',1,1)
        self.pair('11111111111','11900000000',1,2,status='invalido')
        self.pair('22222222222','11900000002',2,4)
        self.pair('22222222222','11900000003',2,5,version=0)
        self.pair('33333333333','11900000004',3,6)
        self.pair('11111111111','11900000005',1,7,status='confirmado',classification='ja_verificado')
        self.plan.commit()
        result=apply(self.config,self.path,'demo')
        self.assertEqual(result['resultados'],{'atualizado':2,'telefone_excluido':1,'alterado_desde_analise':1,'cliente_excluido':1,'ja_verificado':1})
        db=Database(self.config)
        try:
            phones={row['id']:row['situacao'] for row in db.all('SELECT id,situacao FROM telefones')}
            self.assertEqual(phones,{1:'confirmado',2:'confirmado',3:'nao_verificado',4:'nao_verificado',5:'nao_verificado',6:'nao_verificado',7:'confirmado'})
            self.assertEqual(db.one('SELECT app_versao FROM clientes WHERE id=1')['app_versao'],2)
            self.assertEqual(db.one("SELECT COUNT(*) AS n FROM app_auditoria WHERE acao='verificar_origem'")['n'],2)
            info=verification_history(db,1,db.all('SELECT * FROM telefones WHERE cliente_id=1'))
            self.assertEqual(info[1]['arquivo'],self.source.name)
            self.assertFalse(info[1]['manual'])
        finally:db.close()
        self.assertEqual(self.plan.execute('SELECT COUNT(*) FROM copia_telefones').fetchone()[0],2)
        self.assertEqual(self.plan.execute('SELECT COUNT(*) FROM copia_clientes').fetchone()[0],1)
        self.assertEqual(apply(self.config,self.path,'demo')['resultados'],result['resultados'])
        self.assertTrue(validate(self.config,self.path)['consistente'])

    def test_changed_source_and_unknown_actor_do_not_change_data(self):
        self.pair('11111111111','11000000000',1,1);self.plan.commit()
        with self.assertRaises(ValueError):apply(self.config,self.path,'usuario-inexistente')
        self.source.write_bytes(b'arquivo modificado')
        with self.assertRaises(ValueError):apply(self.config,self.path,'demo')
        db=Database(self.config)
        try:self.assertEqual(db.one('SELECT situacao FROM telefones WHERE id=1')['situacao'],'nao_verificado')
        finally:db.close()
