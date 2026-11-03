# Sistema local de gestão de clientes

Aplicação em Python, Flask e MySQL para consulta e manutenção de clientes, contatos, categorias e importações CSV/XLSX. Interface local em português, com perfis ADM, TI e Atendimento, histórico, lixeira e exportação CSV.

Esta distribuição contém somente código, imagens da marca e estrutura vazia do banco. Não contém cadastros, usuários reais, configurações de conexão, senhas, arquivos importados, logs ou backups. Os exemplos nos testes e na demonstração são fictícios. A marca WA foi mantida como parte do visual solicitado.

## Preparar o ambiente (Windows)

Instale Python 3.11 ou superior e MySQL Server 8.4. No terminal desta pasta:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r sistema/requirements.txt
```

## Ver uma demonstração

Execute `VER_DEMONSTRACAO.cmd`. Abra http://127.0.0.1:8081.
Login: `demo`. Senha: `Demonstracao!2026`.
Essa senha é pública, exclusiva da demonstração com dados fictícios. Não a use em produção.

## Criar uma instalação vazia

Use uma instância MySQL própria e vazia. Esta cópia usa o nome de banco `wa_clientes`; para testar no mesmo computador de uma instalação existente, prefira a demonstração ou uma máquina virtual separada. Não execute a configuração sobre um banco de produção.

1. No Workbench da instância vazia, execute `ESTRUTURA_BANCO_VAZIO.sql`. O arquivo cria somente as quatro tabelas de cadastro, sem registros.
2. Execute `CONFIGURAR_SISTEMA.cmd` e crie seu próprio usuário ADM e senha. A senha root é solicitada localmente e não é guardada pelo configurador.
3. Execute `ATUALIZAR_SISTEMA.cmd` para preparar categorias, importações e os recursos atuais. O atualizador espera MySQL 8.4 instalado em `C:\Program Files\MySQL\MySQL Server 8.4`; adapte o caminho da ferramenta de backup se necessário.
4. Execute `INICIAR_SISTEMA.cmd` e acesse http://127.0.0.1:8080.

As credenciais geradas ficam em `sistema/config.local.json`, protegidas pelo Windows e ignoradas pelo Git. Nunca publique esse arquivo.

## Desenvolvimento

HTML: `sistema/templates`. Estilos: `sistema/static/clean.css` e `style.css`. Comportamentos de interface: arquivos JavaScript em `sistema/static`. Regras de importação: `sistema/import_data.py` e `sistema/import_jobs.py`.

Testes isolados com dados fictícios:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s sistema/tests -p "test_*.py"
```

Os scripts de integração dependentes de backups do ambiente original não fazem parte desta cópia.

## Publicar no GitHub

Publique somente o conteúdo desta pasta, nunca a pasta do sistema original. O `.gitignore` bloqueia arquivos locais de configuração, planilhas, bancos, backups e logs. Revise sempre os arquivos selecionados antes de enviar. Nenhum repositório remoto foi criado ou acessado para preparar esta distribuição.
