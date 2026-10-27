"""Validation, OS selection and remote installation."""

import base64
import hashlib
import ipaddress
import re
import shlex
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
SUDO_READY = '__GLPI_SUDO_READY__'
SUDO_PROMPT = '__GLPI_SUDO_PASSWORD__'


class UnsupportedOS(ValueError):
    pass


def read_env_server(path=None):
    """Read only the optional GLPI_SERVER default, without executing .env text."""
    if path is None:
        path = Path(sys.executable).resolve().parent / '.env' if getattr(sys, 'frozen', False) else ROOT / '.env'
    if not path.exists():
        return ''
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('GLPI_SERVER='):
            return line.partition('=')[2].strip().strip('"').strip("'")
    return ''


def validate_ip(value):
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise ValueError('Введите корректный IP-адрес устройства.') from exc


def validate_glpi_server(value):
    value = value.strip()
    if not value or re.search(r'[\s\x00-\x1f\x7f;`$\\"\']', value):
        raise ValueError('Введите корректный URL сервера GLPI без пробелов и управляющих символов.')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('URL сервера GLPI должен начинаться с http:// или https:// и содержать адрес узла.')
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError('Некорректный порт в URL сервера GLPI.') from exc
    return value


def detect_distribution(os_release):
    data = {}
    for line in os_release.splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        try:
            parts = shlex.split(value, comments=False)
        except ValueError:
            continue
        data[key.strip().upper()] = parts[0] if parts else ''
    distro_id = data.get('ID', '').lower()
    name = ' '.join(data.get(key, '') for key in ('NAME', 'PRETTY_NAME')).lower()
    if distro_id in ('astra', 'astralinux') or 'astra linux' in name:
        return 'astra'
    if distro_id in ('linuxmint', 'mint') or 'linux mint' in name:
        return 'mint'
    raise UnsupportedOS('Поддерживаются только Astra Linux и Linux Mint; обнаружена другая ОС.')


def files_for_distribution(distribution):
    if distribution == 'astra':
        folder = ROOT / 'linux_astra'
        required = {'install.sh', 'glpi-agent-1.16.tar.gz', 'agent.cfg'}
    elif distribution == 'mint':
        folder = ROOT / 'linux_mint'
        required = {'install.sh', 'glpi-agent-1.16-x86_64.AppImage'}
    else:
        raise UnsupportedOS('Неизвестный набор файлов для ОС.')
    files = sorted((path for path in folder.iterdir() if path.is_file()), key=lambda path: path.name)
    missing = required - {path.name for path in files}
    if missing:
        raise FileNotFoundError(f'В папке {folder.name} не хватает файлов: {", ".join(sorted(missing))}')
    return files


def render_astra_config(template, server):
    server = validate_glpi_server(server)
    if template.count('__GLPI_SERVER__') != 1:
        raise ValueError('В шаблоне Astra должен быть один маркер сервера GLPI.')
    return template.replace('__GLPI_SERVER__', server)


def build_install_command(distribution, remote_dir, server, use_sudo):
    files_for_distribution(distribution)
    server = validate_glpi_server(server)
    if not re.fullmatch(r'/tmp/glpi-agent\.[A-Za-z0-9]+', remote_dir):
        raise ValueError('Некорректный временный путь на устройстве.')
    script = shlex.quote(f'{remote_dir}/install.sh')
    environment = f'env DEBIAN_FRONTEND=noninteractive GLPI_SERVER={shlex.quote(server)}'
    if use_sudo:
        inner = f"printf '{SUDO_READY}\\n'; exec bash {script}"
        return (f"stty -echo; sudo -S -p '{SUDO_PROMPT}' {environment} "
                f'sh -c {shlex.quote(inner)}')
    return f'{environment} bash {script}'


def _open_pty_command(client, command, on_output, setup_timeout=15):
    transport = client.get_transport()
    if transport is None:
        raise RuntimeError('SSH-соединение потеряно до запуска установщика.')
    if on_output:
        on_output('Открытие удалённого терминала…\n')
    channel = transport.open_session(timeout=setup_timeout)
    channel.settimeout(setup_timeout)
    channel.set_combine_stderr(True)
    timed_out = threading.Event()

    def stop_waiting():
        timed_out.set()
        channel.close()

    watchdog = threading.Timer(setup_timeout, stop_waiting)
    watchdog.daemon = True
    watchdog.start()
    try:
        channel.get_pty()
        if on_output:
            on_output('Удалённый терминал открыт. Запуск команды…\n')
        channel.exec_command(command)
    except Exception as exc:
        channel.close()
        if timed_out.is_set():
            raise RuntimeError(f'SSH-сервер не ответил на запрос терминала или запуск команды за {setup_timeout} секунд.') from exc
        raise RuntimeError('SSH-сервер отклонил запрос терминала или запуск команды.') from exc
    finally:
        watchdog.cancel()
    if on_output:
        on_output('Команда запущена. Ожидание sudo…\n')
    return channel


def _run_command(client, command, *, password=None, on_output=None, expect_ready=False, idle_timeout=30):
    if expect_ready:
        channel = _open_pty_command(client, command, on_output)
    else:
        _, stdout, _ = client.exec_command(command, timeout=20)
        channel = stdout.channel
        channel.set_combine_stderr(True)
    output = []

    def record(chunk):
        chunk = chunk.replace(password, '[скрыто]') if password else chunk
        if chunk:
            output.append(chunk)
            if on_output:
                on_output(chunk)

    if expect_ready:
        pending = ''
        deadline = time.monotonic() + 30
        attempts = 0
        while SUDO_READY not in pending:
            if channel.recv_ready():
                pending += channel.recv(32768).decode('utf-8', errors='replace')
                while SUDO_PROMPT in pending and SUDO_READY not in pending:
                    before, _, pending = pending.partition(SUDO_PROMPT)
                    record(before)
                    attempts += 1
                    if attempts > 3:
                        channel.close()
                        raise RuntimeError('Не удалось подтвердить sudo: проверьте пароль и права учётной записи.')
                    try:
                        channel.sendall((password + '\n').encode('utf-8'))
                    except OSError:
                        # The command may have failed immediately; keep reading
                        # its output so the useful remote error is not lost.
                        pass
                    deadline = time.monotonic() + 30
                if SUDO_READY not in pending:
                    keep = max(len(SUDO_PROMPT), len(SUDO_READY)) - 1
                    if len(pending) > keep:
                        record(pending[:-keep])
                        pending = pending[-keep:]
            elif channel.exit_status_ready():
                record(pending)
                while channel.recv_ready():
                    record(channel.recv(32768).decode('utf-8', errors='replace'))
                return channel.recv_exit_status(), ''.join(output)
            elif time.monotonic() > deadline:
                channel.close()
                raise RuntimeError('Нет ответа от sudo в течение 30 секунд. Проверьте пароль и права учётной записи.')
            else:
                time.sleep(0.1)
        before, _, after = pending.partition(SUDO_READY)
        record(before)
        record('Права sudo подтверждены.\n')

    send_error = None
    if password is not None and not expect_ready:
        try:
            channel.sendall((password + '\n').encode('utf-8'))
        except OSError as exc:
            # A failed remote command can close the channel before the password
            # is sent. Read its stderr to show the actual failure to the user.
            send_error = exc
    if expect_ready:
        record(after.lstrip('\r\n'))
    last_activity = time.monotonic()
    last_notice = last_activity
    while True:
        if channel.recv_ready():
            record(channel.recv(32768).decode('utf-8', errors='replace'))
            last_activity = time.monotonic()
        elif channel.exit_status_ready():
            break
        else:
            now = time.monotonic()
            if now - last_activity > idle_timeout:
                channel.close()
                raise RuntimeError(f'Установщик не выводил сообщений {idle_timeout} секунд. Удалённая команда остановлена.')
            if on_output and now - last_notice > 30:
                on_output('Ожидание вывода удалённой команды…\n')
                last_notice = now
            time.sleep(0.1)
    while channel.recv_ready():
        record(channel.recv(32768).decode('utf-8', errors='replace'))
    status = channel.recv_exit_status()
    if send_error and status == -1 and not output:
        raise RuntimeError('SSH-соединение закрылось до получения ответа удалённой команды.') from send_error
    return status, ''.join(output)


def install(host, username, password, server, *, confirm_host_key, log):
    import paramiko

    host = validate_ip(host)
    if not username.strip() or not password:
        raise ValueError('Введите SSH-логин и пароль.')
    server = validate_glpi_server(server)

    class ConfirmPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode('ascii').rstrip('=')
            if not confirm_host_key(hostname, f'SHA256:{fingerprint}'):
                raise ValueError('Ключ SSH-узла не подтверждён.')

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(ConfirmPolicy())
    remote_dir = None
    uploaded = []
    sftp = None
    try:
        log('Подключение по SSH…\n')
        client.connect(hostname=host, port=22, username=username.strip(), password=password,
                       look_for_keys=False, allow_agent=False, timeout=10, auth_timeout=10, banner_timeout=10)
        status, os_release = _run_command(client, 'cat /etc/os-release')
        if status != 0:
            raise RuntimeError('Не удалось прочитать /etc/os-release на устройстве.')
        distribution = detect_distribution(os_release)
        log(f'Обнаружена ОС: {"Astra Linux" if distribution == "astra" else "Linux Mint"}.\n')
        status, remote_dir = _run_command(client, 'mktemp -d /tmp/glpi-agent.XXXXXXXX')
        remote_dir = remote_dir.strip()
        if status != 0 or not re.fullmatch(r'/tmp/glpi-agent\.[A-Za-z0-9]+', remote_dir):
            raise RuntimeError('Не удалось создать временный каталог на устройстве.')
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(30)
        for path in files_for_distribution(distribution):
            target = f'{remote_dir}/{path.name}'
            log(f'Передача {path.name}…\n')
            if distribution == 'astra' and path.name == 'agent.cfg':
                template = path.read_text(encoding='utf-8')
                with sftp.file(target, 'wb') as remote_file:
                    remote_file.write(render_astra_config(template, server).encode('utf-8'))
            else:
                sftp.put(str(path), target)
            uploaded.append(target)
        log('Запуск установщика…\n')
        command = build_install_command(distribution, remote_dir, server, use_sudo=username.strip() != 'root')
        status, output = _run_command(client, command, password=password if username.strip() != 'root' else None,
                                      on_output=log, expect_ready=username.strip() != 'root',
                                      idle_timeout=180 if distribution == 'mint' else 900)
        if status != 0:
            detail = output.strip().splitlines()[-1] if output.strip() else 'Удалённая команда не вывела подробностей.'
            raise RuntimeError(f'Установщик завершился с кодом {status}: {detail}')
        log('Установка завершена.\n')
        return distribution
    finally:
        if sftp and remote_dir and uploaded:
            try:
                for target in uploaded:
                    sftp.remove(target)
                sftp.rmdir(remote_dir)
            except Exception:
                log('Не удалось удалить временные файлы на устройстве.\n')
        try:
            if sftp:
                sftp.close()
        finally:
            client.close()
