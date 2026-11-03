@echo off
setlocal
chcp 65001 >nul
set "MYSQL_DUMP=C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe"
if not exist "%MYSQL_DUMP%" (
    echo Nao foi encontrado o programa de backup do MySQL.
    pause
    exit /b 1
)
set "BACKUP_DIR=%~dp0backups"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
if not exist "%BACKUP_DIR%" (
    echo Nao foi possivel criar a pasta de backups.
    pause
    exit /b 1
)
for /f %%T in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss_fff"') do set "BACKUP_TIME=%%T"
if not defined BACKUP_TIME (
    echo Nao foi possivel gerar o nome do backup.
    pause
    exit /b 1
)
set "BACKUP_FILE=%BACKUP_DIR%\wa_clientes_%BACKUP_TIME%.sql"
echo Backup do banco wa_clientes.
echo Digite a senha do usuario root quando aparecer Enter password.
echo A senha nao sera exibida nem salva neste atalho.
echo Aguarde a mensagem de conclusao.
echo.
"%MYSQL_DUMP%" --host=127.0.0.1 --port=3306 --user=root --password --single-transaction --routines --events --triggers --no-tablespaces --set-gtid-purged=OFF --default-character-set=utf8mb4 --result-file="%BACKUP_FILE%.parcial" --databases wa_clientes
if errorlevel 1 (
    echo.
    echo O BACKUP FALHOU. O arquivo .parcial nao deve ser usado como backup completo.
    echo Leia a mensagem acima e informe o erro, sem informar a senha.
    pause
    exit /b 1
)
move /-Y "%BACKUP_FILE%.parcial" "%BACKUP_FILE%" >nul
if errorlevel 1 (
    echo Nao foi possivel finalizar o nome do arquivo. Verifique a pasta backups.
    pause
    exit /b 1
)
echo.
echo BACKUP CONCLUIDO:
echo %BACKUP_FILE%
echo Copie o arquivo para outro dispositivo de armazenamento.
echo A restauracao ainda precisa ser testada em um banco separado.
pause
