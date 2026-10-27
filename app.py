"""Desktop entry point: python app.py."""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from glpi_installer.core import install, read_env_server, validate_glpi_server, validate_ip
from glpi_installer.output import ProgressOutput


class InstallerApp:
    def __init__(self, root):
        self.root = root
        root.title('Установка GLPI Agent')
        root.minsize(560, 420)
        self.events = queue.Queue()
        self.worker = None
        self.host = tk.StringVar()
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.server = tk.StringVar(value=read_env_server())

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        fields = (
            ('IP-адрес устройства', self.host, False),
            ('SSH-логин', self.username, False),
            ('SSH-пароль / пароль sudo', self.password, True),
            ('URL сервера GLPI', self.server, False),
        )
        for row, (label, variable, hidden) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row * 2, column=0, sticky='w', pady=(0, 3))
            entry = ttk.Entry(frame, textvariable=variable, show='*' if hidden else '')
            entry.grid(row=row * 2 + 1, column=0, sticky='ew', pady=(0, 8))
            if row == 0:
                entry.focus_set()

        self.button = ttk.Button(frame, text='Установить агент', command=self.start)
        self.button.grid(row=8, column=0, sticky='ew', pady=(2, 10))
        ttk.Label(frame, text='Ход установки').grid(row=9, column=0, sticky='w')
        self.output = scrolledtext.ScrolledText(frame, height=10, state='disabled', wrap='word')
        self.output.grid(row=10, column=0, sticky='nsew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(10, weight=1)
        root.after(100, self.process_events)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def append(self, text):
        self.output.configure(state='normal')
        self.output.insert('end', text)
        lines = int(self.output.index('end-1c').split('.')[0])
        if lines > 300:
            self.output.delete('1.0', f'{lines - 300}.0')
        self.output.see('end')
        self.output.configure(state='disabled')

    def start(self):
        try:
            host = validate_ip(self.host.get())
            username = self.username.get().strip()
            password = self.password.get()
            server = validate_glpi_server(self.server.get())
            if not username or not password:
                raise ValueError('Введите SSH-логин и пароль.')
        except ValueError as exc:
            messagebox.showerror('Проверьте данные', str(exc), parent=self.root)
            return
        self.password.set('')
        self.button.configure(state='disabled')
        self.append('Начало установки.\n')

        def confirm_host_key(hostname, fingerprint):
            decision = threading.Event()
            result = {'accepted': False}
            self.events.put(('host_key', hostname, fingerprint, decision, result))
            decision.wait()
            return result['accepted']

        def run():
            compact = ProgressOutput()

            def log(text):
                shown = compact.feed(text)
                if shown:
                    self.events.put(('log', shown))

            try:
                install(host, username, password, server,
                        confirm_host_key=confirm_host_key,
                        log=log)
            except Exception as exc:
                outcome = ('error', str(exc).replace(password, '[скрыто]'))
            else:
                outcome = ('success',)
            finally:
                tail = compact.flush()
                if tail:
                    self.events.put(('log', tail))
                self.events.put(outcome)
                self.events.put(('done',))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def process_events(self):
        processed = 0
        pending_log = []
        while processed < 100:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1
            kind = event[0]
            if kind == 'log':
                pending_log.append(event[1])
                continue
            if pending_log:
                self.append(''.join(pending_log))
                pending_log.clear()
            if kind == 'host_key':
                _, hostname, fingerprint, decision, result = event
                result['accepted'] = messagebox.askyesno(
                    'Новый SSH-узел',
                    f'Подтвердить ключ устройства {hostname}?\n\nОтпечаток: {fingerprint}\n\nСверьте его с отпечатком устройства.',
                    parent=self.root,
                )
                decision.set()
            elif kind == 'error':
                self.append(f'Ошибка: {event[1]}\n')
                messagebox.showerror('Установка не завершена', event[1], parent=self.root)
            elif kind == 'success':
                messagebox.showinfo('Готово', 'Команда установки завершилась успешно.', parent=self.root)
            elif kind == 'done':
                self.button.configure(state='normal')
        if pending_log:
            self.append(''.join(pending_log))
        self.root.after(10 if not self.events.empty() else 100, self.process_events)

    def close(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('Установка выполняется', 'Дождитесь завершения удалённой установки.', parent=self.root)
            return
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    InstallerApp(root)
    root.mainloop()
