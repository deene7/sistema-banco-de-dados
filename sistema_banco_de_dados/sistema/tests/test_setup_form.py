import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from setup_form import validate_setup


class SetupFormTest(unittest.TestCase):
    def data(self):
        return {'nome': ' Pessoa fictícia ', 'login': 'Pessoa.TI', 'senha': 'SomenteTeste!2026',
                'confirmacao': 'SomenteTeste!2026', 'root': 'teste-local'}

    def test_valid_fields(self):
        result = validate_setup(self.data())
        self.assertEqual(result['nome'], 'Pessoa fictícia')
        self.assertEqual(result['login'], 'pessoa.ti')

    def test_short_password(self):
        values = self.data()
        values.update(senha='curta', confirmacao='curta')
        with self.assertRaisesRegex(ValueError, '12'):
            validate_setup(values)

    def test_mismatch(self):
        values = self.data()
        values['confirmacao'] = 'OutraSenha!2026'
        with self.assertRaisesRegex(ValueError, 'diferentes'):
            validate_setup(values)

    def test_root_required_and_independent_from_new_password(self):
        values = self.data()
        values['root'] = ''
        with self.assertRaisesRegex(ValueError, 'root'):
            validate_setup(values)
        values['root'] = 'senha-antiga-curta'
        self.assertEqual(validate_setup(values)['root'], 'senha-antiga-curta')

    def test_invalid_login(self):
        values = self.data()
        values['login'] = 'nome com espaços'
        with self.assertRaisesRegex(ValueError, 'login'):
            validate_setup(values)


if __name__ == '__main__':
    unittest.main()
