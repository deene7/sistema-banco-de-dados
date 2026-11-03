import base64
import ctypes
import json
import os
import re
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / 'config.local.json'


def restrict_file(path):
    """Configuração acessível somente à conta atual do Windows e SYSTEM."""
    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    match = re.search(r'S-1-[0-9-]+', identity)
    if not match:
        raise RuntimeError('Não foi possível identificar a conta do Windows para proteger a configuração.')
    rights = '(OI)(CI)(F)' if Path(path).is_dir() else '(F)'
    subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                    '*' + match.group() + ':' + rights, '*S-1-5-18:' + rights],
                   check=True, capture_output=True, text=True)


def protect(text, decrypt=False):
    """DPAPI vincula a senha do banco ao usuário atual do Windows."""
    if os.name != 'nt':
        raise RuntimeError('A configuração de produção requer Windows e DPAPI.')
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(text) if decrypt else text.encode('utf-8')
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    api = ctypes.windll.crypt32
    # CRYPTPROTECT_UI_FORBIDDEN: nenhuma janela de autenticação é automatizada.
    if decrypt:
        ok = api.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = api.CryptProtectData(ctypes.byref(source), 'WA MySQL', None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise ctypes.WinError()
    try:
        value = ctypes.string_at(result.data, result.size)
        return value.decode('utf-8') if decrypt else base64.b64encode(value).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def read_config():
    data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    data['password'] = protect(data.pop('password_dpapi'), decrypt=True)
    return data
