"""Reinicia exclusivamente a aplicação desta pasta. Não interrompe o MySQL."""
import os
import subprocess
import sys
from pathlib import Path


def restart():
    base=Path(__file__).resolve().parent
    (base/'logs').mkdir(exist_ok=True)
    env=os.environ.copy()
    env['WA_APP_SCRIPT']=str(base/'run.py')
    env['WA_APP_ROOT']=str(base)
    env['WA_APP_PYTHON']=sys.executable
    command=r'''
    $appScript=$env:WA_APP_SCRIPT
    $appRoot=$env:WA_APP_ROOT
    $processes=@(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
      $_.CommandLine -and $_.CommandLine.Contains($appScript) -and $_.CommandLine -notlike '*--demo*'
    })
    foreach ($app in $processes) { Stop-Process -Id $app.ProcessId -ErrorAction Stop }
    foreach ($app in $processes) { Wait-Process -Id $app.ProcessId -Timeout 10 -ErrorAction SilentlyContinue }
    $new=Start-Process -FilePath $env:WA_APP_PYTHON -ArgumentList @('-X','utf8',('"'+$appScript+'"')) -WorkingDirectory $appRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $appRoot 'logs\servidor.log') -RedirectStandardError (Join-Path $appRoot 'logs\erros.log') -PassThru
    Write-Output ('Sistema iniciado: '+$new.Id)
    '''
    subprocess.run(['powershell.exe','-NoProfile','-Command',command],env=env,check=True)


if __name__=='__main__':restart()
