# Gestão de Clientes

Sistema local para organizar, consultar e atualizar uma base de clientes, com cadastro único por CPF, múltiplos contatos e importação de planilhas.

Desenvolvido em **Python, Flask e MySQL**, com interface em português e acesso pelo navegador.

## Funcionalidades

- **Clientes:** cadastro, edição, pesquisa, filtros e paginação.
- **CPF único:** normalização e validação dos dígitos verificadores para evitar cadastros duplicados. Essa validação não consulta a Receita Federal nem comprova titularidade.
- **Contatos:** múltiplos telefones, e-mails e endereços por cliente.
- **Categorias:** criação, edição e consulta de grupos como CLT e SIAPE; um cliente ou lote pode ter várias categorias.
- **Importação CSV e XLSX:** mapeamento de colunas, revisão de pendências, consolidação por CPF e acompanhamento do processamento.
- **Complementação de cadastros:** importações podem preencher campos vazios e acrescentar contatos, preservando informações já preenchidas. Divergências de nome são separadas para revisão.
- **Origem dos telefones:** lotes associados às categorias Discadora ou Argus podem marcar os telefones válidos importados como verificados, inclusive os já existentes para o mesmo CPF. O status representa a confiança na origem informada.
- **Exportação CSV:** exporta os clientes conforme os filtros aplicados.
- **Lixeira:** exclusão recuperável de clientes e lotes, com exclusão permanente restrita ao administrador.
- **Histórico:** registro de alterações e consulta de logs conforme as permissões.
- **Página inicial:** resumo da base, boas-vindas e atalhos para as principais ações.

## Perfis de acesso

| Perfil | Acesso |
| --- | --- |
| **ADM** | Administração do sistema, usuários, permissões e exclusões permanentes. |
| **TI** | Consulta e manutenção dos cadastros, contatos, categorias e importações. Gestão de Atendimento quando autorizada pelo ADM. |
| **Atendimento** | Consulta, cadastro de clientes e inclusão de telefones, sem editar ou excluir informações existentes. |

As permissões são verificadas pelo servidor.

## Tecnologias

| Camada | Tecnologia |
| --- | --- |
| Aplicação | Python e Flask |
| Banco principal | MySQL 8.4 |
| Interface | HTML, CSS e JavaScript |
| Servidor local | Waitress |
| Leitura de XLSX | openpyxl |
| Demonstração e testes | SQLite com dados fictícios |

## Programas necessários

| Programa | Obrigatório? | Para que serve |
| --- | --- | --- |
| **Windows** | Sim, na instalação principal | Executa os atalhos e protege as credenciais locais com DPAPI. |
| **Python 3.11 ou superior, com pip e venv** | Sim | Executa a aplicação e instala suas bibliotecas. |
| **MySQL Server 8.4** | Sim, para uso com banco principal | Armazena clientes, contatos, usuários e histórico. |
| **MySQL Workbench** | Opcional | Facilita a conexão com o servidor e a execução do arquivo SQL inicial. |
| **Navegador** | Sim | Abre a interface do sistema, por exemplo Chrome, Edge ou Firefox. |
| **Git** | Opcional | Permite clonar e atualizar o código; também é possível baixar o ZIP pelo GitHub. |

**Não é necessário instalar Excel, Node.js, XAMPP ou Microsoft SQL Server.** Os arquivos XLSX são lidos pela biblioteca openpyxl. MySQL Server e Microsoft SQL Server são produtos diferentes; este projeto utiliza MySQL.

O modo de demonstração utiliza SQLite, incluído no Python, e dispensa o MySQL. As bibliotecas Flask, PyMySQL, Waitress e openpyxl são instaladas pelo comando abaixo.

## Instalação passo a passo

### 1. Baixar o projeto

No GitHub, clique em **Code > Download ZIP**, extraia o arquivo e abra a pasta que contém este README e os atalhos `.cmd`. Execute os próximos comandos nessa pasta, fora do ZIP.

### 2. Instalar o Python e as bibliotecas

Instale Python 3.11 ou superior com `pip` e o inicializador `py`. Abra o PowerShell na pasta do projeto e confira a instalação:

```powershell
py -3 --version
```

Crie o ambiente do projeto e instale as dependências:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r sistema/requirements.txt
```

Não é necessário ativar o ambiente nem alterar a política de execução do PowerShell. Os atalhos usam diretamente o Python da pasta `.venv`.

### 3. Instalar e preparar o MySQL

Para usar uma base própria, instale **MySQL Server 8.4** e suas ferramentas de linha de comando, incluindo `mysqldump.exe`. Durante a configuração:

- Mantenha a conexão local na porta **3306**.
- Defina e guarde a senha do usuário **root**.
- Configure o servidor como serviço do Windows e deixe-o iniciado durante o uso do sistema.

Instale também o Workbench se preferir preparar o banco pela interface gráfica. No Workbench, crie uma conexão com host `127.0.0.1`, porta `3306` e usuário `root`. Use a senha escolhida na instalação do servidor.

**Instalar somente o Workbench não instala nem inicia o servidor MySQL.**

Se quiser apenas conhecer a interface, pule essa etapa e siga a demonstração. Para usar uma base própria, continue em **Configurar uma base vazia**.

## Executar a demonstração

Abra `VER_DEMONSTRACAO.cmd` e acesse **http://127.0.0.1:8081**.

| Campo | Valor |
| --- | --- |
| Login | `demo` |
| Senha | `Demonstracao!2026` |

A demonstração cria registros fictícios. A senha acima é pública e exclusiva desse modo; crie credenciais próprias na instalação principal.

## Configurar uma base vazia

Use uma instância MySQL separada e vazia. O projeto utiliza o banco `wa_clientes`; executar a configuração no computador de uma instalação existente pode atingir o mesmo banco. Para experimentar nesse computador, utilize a demonstração.

1. No Workbench conectado à instância vazia, use **File > Open SQL Script**, selecione `ESTRUTURA_BANCO_VAZIO.sql` e execute o arquivo inteiro pelo botão de raio. Ele cria o banco `wa_clientes` e suas tabelas iniciais sem registros.
2. Abra `CONFIGURAR_SISTEMA.cmd`. Informe seu nome, escolha um login e crie uma senha de acesso de pelo menos 12 caracteres. Confirme essa senha e informe a senha root do MySQL no campo próprio. Aguarde a mensagem de configuração concluída. A senha root não será guardada pelo configurador; a senha do aplicativo é a que você acabou de criar.
3. Abra `ATUALIZAR_SISTEMA.cmd` para preparar os recursos atuais, incluindo categorias e importações.
4. Abra `INICIAR_SISTEMA.cmd` e acesse **http://127.0.0.1:8080**.

O atualizador e o atalho de backup esperam as ferramentas do MySQL em `C:\Program Files\MySQL\MySQL Server 8.4\bin`. Ajuste esse caminho se a instalação for diferente.

O computador e o servidor local precisam permanecer ligados durante o uso. O acesso padrão fica restrito ao próprio computador; acesso pela rede exige configuração adicional.

## Como usar no dia a dia

1. Abra `INICIAR_SISTEMA.cmd` e mantenha a janela aberta durante o uso. Caso a aplicação já esteja em execução, basta abrir o navegador.
2. Acesse **http://127.0.0.1:8080** e entre com o login e a senha criados na configuração.
3. Em **Cadastros**, pesquise por CPF, nome ou telefone, combine os filtros e abra a ficha desejada.
4. Em **Novo cadastro**, informe os dados do cliente. O sistema formata CPF e telefone e impede a criação de outro cliente com o mesmo CPF.
5. Na ficha, consulte ou mantenha os contatos conforme seu perfil de acesso.
6. Em **Categorias**, organize os grupos e abra uma categoria para consultar seus clientes.
7. Em **Configurações**, altere sua senha e, quando autorizado, administre usuários e consulte o histórico disponível.
8. Use **Exportar CSV**, em Cadastros, para baixar os registros selecionados pelos filtros. Essa função depende da permissão do usuário.

O primeiro usuário criado é ADM. Crie contas individuais para as outras pessoas, atribuindo o perfil adequado.

Para encerrar uma execução aberta no terminal, pressione **Ctrl+C** na janela do servidor. Não encerre o sistema enquanto houver importação, exclusão ou restauração em andamento.

## Backup e atualização

- **Backup:** execute `FAZER_BACKUP.cmd`, informe a senha root quando solicitada e aguarde a confirmação. Os arquivos são criados na pasta `backups` e contêm dados do banco; guarde-os em local protegido.
- **Atualização da estrutura:** execute `ATUALIZAR_SISTEMA.cmd` quando uma atualização do projeto exigir. Ele solicita a senha root, faz um backup antes das mudanças de estrutura e reinicia a aplicação após a conclusão. Não substitui o processo de baixar versões novas do código.
- **Mudança de computador:** preserve os backups. A configuração local protegida pelo Windows não deve ser simplesmente copiada para outro usuário ou computador; a conexão precisa ser preparada no novo ambiente.

## Importação de dados

1. Abra **Importações** e selecione um arquivo CSV ou XLSX.
2. Escolha, opcionalmente, uma ou mais categorias.
3. Confira o mapeamento das colunas e a análise dos registros.
4. Revise as pendências e confirme a aplicação dos registros prontos.

Problemas de CPF e nome podem bloquear um registro. Problemas em campos opcionais, como endereço, geram avisos e não impedem por si só a importação dos demais dados válidos. Cadastros na lixeira e alterações posteriores à análise também possuem proteções próprias.

O formato legado `.xls` não é suportado. Converta-o para `.xlsx` ou CSV antes de importar.

## Problemas comuns

| Situação | Como verificar |
| --- | --- |
| `py` não é reconhecido | Confira a instalação do Python e do inicializador `py`. Feche e reabra o terminal após instalar. |
| O atalho não encontra o Python | Crie a pasta `.venv` com os comandos de instalação, na mesma pasta dos atalhos. |
| `ModuleNotFoundError` | Instale as dependências usando `.\.venv\Scripts\python.exe -m pip install -r sistema/requirements.txt`. |
| Erro de conexão com `localhost:3306` ou `127.0.0.1:3306` | Confira se MySQL Server está instalado, se o serviço está iniciado e se a porta é 3306. |
| A configuração não encontra as tabelas | Execute primeiro `ESTRUTURA_BANCO_VAZIO.sql` na instância MySQL escolhida. |
| Falha no backup automático da atualização | Confira a instalação do `mysqldump.exe` no caminho esperado, a senha root e o espaço em disco. |
| O navegador não abre a página | Verifique a janela do servidor. Use a porta 8080 para a instalação principal ou 8081 para a demonstração. |
| A porta já está em uso | Confira se o sistema já está aberto antes de tentar iniciá-lo novamente. |

A configuração principal foi preparada para uso local no Windows. Hospedagem pública, acesso externo e instalação em outros sistemas operacionais exigem adaptações.

## Estrutura do projeto

```text
├── README.md
├── ESTRUTURA_BANCO_VAZIO.sql
├── CONFIGURAR_SISTEMA.cmd
├── ATUALIZAR_SISTEMA.cmd
├── INICIAR_SISTEMA.cmd
├── VER_DEMONSTRACAO.cmd
├── FAZER_BACKUP.cmd
└── sistema/
    ├── app.py              # Aplicação e rotas principais
    ├── features.py         # Recursos de gestão
    ├── import_data.py      # Leitura e normalização de dados
    ├── import_jobs.py      # Análise e processamento dos lotes
    ├── export_csv.py       # Exportação filtrada
    ├── security.py         # Autenticação e proteção local
    ├── requirements.txt    # Dependências
    ├── schema.sql          # Estrutura de usuários e auditoria
    ├── schema_v2.sql       # Estrutura dos recursos adicionais
    ├── templates/          # Páginas HTML
    ├── static/             # Estilos, scripts e imagens
    └── tests/              # Testes automatizados
```

## Testes

Execute os testes isolados com dados fictícios:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s sistema/tests -p "test_*.py"
```

A cópia foi validada com **61 testes automatizados**. Os scripts de integração dependentes de backups do ambiente original não estão incluídos.

## Dados e credenciais

Este repositório contém código, imagens da marca e estrutura vazia do banco. Não inclui dados de clientes, contas reais, credenciais, arquivos importados, logs ou backups. Os exemplos da demonstração e dos testes são fictícios.

As senhas dos usuários são armazenadas como hash. A configuração de conexão gerada localmente utiliza a proteção DPAPI do Windows. O arquivo `sistema/config.local.json` não deve ser compartilhado nem enviado ao GitHub.

O `.gitignore` exclui configurações locais, planilhas, bancos, backups, logs e dependências. Revise os arquivos selecionados antes de cada publicação: o `.gitignore` não remove arquivos que já tenham sido versionados.
