"""Coleta local das credenciais, exclusivamente pela própria pessoa na janela."""
import re


def validate_setup(values):
    name = values.get('nome', '').strip()
    login = values.get('login', '').strip().lower()
    password = values.get('senha', '')
    if not name or len(name) > 150:
        raise ValueError('Informe seu nome, com até 150 caracteres.')
    if not re.fullmatch(r'[a-z0-9._-]{3,80}', login):
        raise ValueError('O login precisa ter de 3 a 80 caracteres: letras sem acento, números, ponto, hífen ou sublinhado.')
    if not 12 <= len(password) <= 200:
        raise ValueError('A senha do sistema precisa ter entre 12 e 200 caracteres.')
    if password != values.get('confirmacao', ''):
        raise ValueError('As duas senhas do sistema são diferentes. Confira a confirmação.')
    if not values.get('root', ''):
        raise ValueError('Digite a senha root que você já usa no MySQL. Ela não é a nova senha do sistema.')
    return {'nome': name, 'login': login, 'senha': password, 'root': values['root']}


def collect_setup():
    import tkinter as tk
    from tkinter import ttk, messagebox

    window = tk.Tk()
    window.title('WA — Configurar sistema de clientes')
    window.geometry('650x725')
    window.minsize(570, 680)
    window.configure(background='#f4f5f7')
    style = ttk.Style(window)
    style.theme_use('clam')
    style.configure('TFrame', background='#f4f5f7')
    style.configure('TLabel', background='#f4f5f7', foreground='#203146', font=('Segoe UI', 10))
    style.configure('Title.TLabel', font=('Segoe UI', 21, 'bold'))
    style.configure('Hint.TLabel', foreground='#667586', font=('Segoe UI', 9))
    style.configure('TEntry', padding=8, font=('Segoe UI', 11))
    style.configure('Primary.TButton', padding=(20, 11), background='#112338', foreground='white', font=('Segoe UI', 10, 'bold'))
    style.configure('TCheckbutton', background='#f4f5f7', font=('Segoe UI', 9))
    frame = ttk.Frame(window, padding=30)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Crie seu acesso', style='Title.TLabel').pack(anchor='w')
    ttk.Label(frame, text='Tudo é preenchido neste computador. Nenhuma senha precisa ser enviada no chat.',
              style='Hint.TLabel', wraplength=560).pack(anchor='w', pady=(8, 18))
    entries = {}
    specifications = [
        ('nome', 'Seu nome', False),
        ('login', 'Seu login (exemplo: seu.nome)', False),
        ('senha', 'Crie uma senha para o sistema — mínimo de 12 caracteres', True),
        ('confirmacao', 'Repita a senha do sistema', True),
        ('root', 'Senha root do MySQL — a senha que você já criou na instalação', True)]
    for key, label, secret in specifications:
        ttk.Label(frame, text=label, wraplength=560).pack(anchor='w', pady=(9, 4))
        entry = ttk.Entry(frame, show='*' if secret else '')
        entry.pack(fill='x', ipady=1)
        entries[key] = entry
    show = tk.BooleanVar(value=False)

    def toggle():
        for key in ('senha', 'confirmacao', 'root'):
            entries[key].configure(show='' if show.get() else '*')

    ttk.Checkbutton(frame, text='Mostrar as senhas nesta janela', variable=show,
                    command=toggle).pack(anchor='w', pady=(14, 8))
    ttk.Label(frame, text='A nova senha serve para entrar nas telas. A senha root serve somente para preparar a conexão com o MySQL.',
              style='Hint.TLabel', wraplength=560).pack(anchor='w', pady=(0, 14))
    result = {}

    def submit():
        try:
            cleaned = validate_setup({key: entry.get() for key, entry in entries.items()})
        except ValueError as error:
            messagebox.showerror('Confira os campos', str(error), parent=window)
            return
        result.update(cleaned)
        window.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=(6, 0))
    ttk.Button(buttons, text='Cancelar', command=window.destroy).pack(side='left')
    ttk.Button(buttons, text='Configurar sistema', style='Primary.TButton', command=submit).pack(side='right')
    entries['nome'].focus_set()
    window.mainloop()
    if not result:
        raise KeyboardInterrupt()
    return result
