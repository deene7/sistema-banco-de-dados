import bootstrap
import argparse
import os
from pathlib import Path
from waitress import serve
from app import create_app
from security import CONFIG_FILE, read_config, restrict_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    if args.demo:
        from demo import create_demo
        path = Path(__file__).with_name('demonstracao.sqlite3')
        create_demo(path)
        uploads=Path(__file__).with_name('demonstracao_importacoes')
        uploads.mkdir(exist_ok=True)
        if os.name=='nt':restrict_file(uploads)
        config = {'sqlite_path': str(path), 'secret_key': 'apenas-demonstracao-local-sem-dados-reais',
                  'demo': True, 'bind': '127.0.0.1', 'web_port': 8081,'uploads_dir':str(uploads)}
        print('DEMONSTRAÇÃO com dados fictícios: http://127.0.0.1:8081')
        print('Login: demo | Senha: Demonstracao!2026')
    else:
        if not CONFIG_FILE.exists():
            print('Execute CONFIGURAR_SISTEMA.cmd antes de iniciar o sistema.')
            return
        config = read_config()
        if config.get('bind', '127.0.0.1') != '127.0.0.1':
            raise ValueError('O acesso pela rede deve ser configurado pela TI em uma etapa separada.')
        print('SISTEMA LOCAL: http://127.0.0.1:8080')
        print('Deixe esta janela aberta enquanto estiver usando. Para encerrar, pressione Ctrl+C.')
    app = create_app(config)
    # Uma segunda cópia no Windows pode disputar a mesma porta com SO_REUSEADDR.
    # O bloqueio do arquivo permanece ativo durante toda a execução do servidor.
    lock = None
    if os.name == 'nt':
        import msvcrt
        lock_path = Path(__file__).with_name('demonstracao.lock' if args.demo else 'servidor.lock')
        lock = lock_path.open('a+b')
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock.close()
            print('O sistema já está em execução. Abra o endereço no navegador; não é necessário iniciar novamente.')
            return
    try:
        serve(app, host=config.get('bind', '127.0.0.1'), port=config.get('web_port', 8080),
              threads=4, channel_timeout=60, clear_untrusted_proxy_headers=True)
    finally:
        if lock:
            lock.close()


if __name__ == '__main__':
    main()
