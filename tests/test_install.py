import unittest
import threading
from unittest.mock import patch

from glpi_installer.core import SUDO_PROMPT, SUDO_READY, _open_pty_command, _run_command, install


class FakeChannel:
    def __init__(self, output):
        self.output = output.encode('utf-8')
        self.sent = b''

    def set_combine_stderr(self, value):
        pass

    def sendall(self, data):
        self.sent += data

    def recv_ready(self):
        return bool(self.output)

    def recv(self, _size):
        data, self.output = self.output, b''
        return data

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return 0


class PromptChannel(FakeChannel):
    def __init__(self):
        super().__init__(SUDO_PROMPT)

    def sendall(self, data):
        super().sendall(data)
        self.output += f'{SUDO_READY}\r\ninstalled\n'.encode('utf-8')

    def settimeout(self, timeout):
        self.timeout = timeout

    def get_pty(self):
        self.pty_opened = True

    def exec_command(self, command):
        self.command = command

    def close(self):
        pass


class FakeSFTPFile:
    def __init__(self, owner, name):
        self.owner, self.name = owner, name

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, data):
        self.owner.files[self.name] = data


class FakeSFTP:
    def __init__(self, owner):
        self.owner = owner

    def put(self, source, target):
        self.owner.files[target] = source

    def get_channel(self):
        return self

    def settimeout(self, timeout):
        self.timeout = timeout

    def file(self, name, mode):
        return FakeSFTPFile(self.owner, name)

    def remove(self, name):
        self.owner.removed.append(name)

    def rmdir(self, name):
        self.owner.removed.append(name)

    def close(self):
        pass


class FakeSSH:
    def __init__(self, os_release):
        self.os_release = os_release
        self.files = {}
        self.removed = []
        self.commands = []
        self.channels = []
        self.sftp_opens = 0

    def load_system_host_keys(self):
        pass

    def set_missing_host_key_policy(self, _policy):
        pass

    def connect(self, **kwargs):
        self.connection = kwargs

    def exec_command(self, command, **_kwargs):
        self.commands.append(command)
        output = self.os_release if command == 'cat /etc/os-release' else '/tmp/glpi-agent.test123\n' if command.startswith('mktemp') else 'installed\n'
        channel = FakeChannel(output)
        self.channels.append(channel)
        return None, type('Stdout', (), {'channel': channel})(), None

    def get_transport(self):
        return self

    def open_session(self, timeout):
        channel = PromptChannel()
        self.channels.append(channel)
        return channel

    def open_sftp(self):
        self.sftp_opens += 1
        return FakeSFTP(self)

    def close(self):
        pass


class InstallTests(unittest.TestCase):
    def test_failed_pty_setup_does_not_open_new_sftp_for_cleanup(self):
        fake = FakeSSH('ID=linuxmint\n')
        log = []
        with patch('paramiko.SSHClient', return_value=fake), \
                patch('glpi_installer.core._open_pty_command', side_effect=RuntimeError('SSH terminal timeout')):
            with self.assertRaisesRegex(RuntimeError, 'SSH terminal timeout'):
                install('192.0.2.10', 'admin', 'secret-test', 'https://glpi.example.test/',
                        confirm_host_key=lambda *_: True, log=log.append)
        self.assertEqual(fake.sftp_opens, 1)
        self.assertTrue(fake.removed)

    def test_pty_request_cannot_wait_forever(self):
        class HangingChannel(PromptChannel):
            def __init__(self):
                super().__init__()
                self.closed_event = threading.Event()

            def get_pty(self):
                self.closed_event.wait()
                raise OSError('Channel closed')

            def close(self):
                self.closed_event.set()

        channel = HangingChannel()

        class Client:
            def get_transport(self):
                return self

            def open_session(self, timeout):
                return channel

        with self.assertRaisesRegex(RuntimeError, 'не ответил'):
            _open_pty_command(Client(), 'sudo true', None, setup_timeout=0.02)
        self.assertTrue(channel.closed_event.is_set())

    def test_pty_setup_uses_bounded_channel_and_reports_stages(self):
        class Channel(PromptChannel):
            def settimeout(self, timeout):
                self.timeout = timeout

            def get_pty(self):
                self.pty_opened = True

            def exec_command(self, command):
                self.command = command

            def close(self):
                pass

        channel = Channel()

        class Transport:
            def open_session(self, timeout):
                self.timeout = timeout
                return channel

        transport = Transport()

        class Client:
            def get_transport(self):
                return transport

            def exec_command(self, *args, **kwargs):
                raise AssertionError('sudo must use a bounded channel setup')

        log = []
        status, _ = _run_command(Client(), 'sudo true', password='secret-test', on_output=log.append, expect_ready=True)
        self.assertEqual(status, 0)
        self.assertTrue(channel.pty_opened)
        self.assertEqual(channel.command, 'sudo true')
        self.assertEqual(transport.timeout, 15)
        self.assertIn('Удалённый терминал открыт', ''.join(log))

    def test_sudo_password_is_sent_only_after_prompt(self):
        class SplitPromptChannel(PromptChannel):
            def __init__(self):
                super().__init__()
                self.output = SUDO_PROMPT[:8].encode('utf-8')
                self.second_half = True

            def recv_ready(self):
                if not self.output and self.second_half:
                    self.output = SUDO_PROMPT[8:].encode('utf-8')
                    self.second_half = False
                return super().recv_ready()

            def sendall(self, data):
                super().sendall(data)
                self.output += f'{SUDO_READY}\r\n[1] Установка пакетов\n'.encode('utf-8')

        channel = SplitPromptChannel()

        class Client:
            def get_transport(self):
                return self

            def open_session(self, timeout):
                return channel

        client = Client()
        log = []
        status, output = _run_command(client, 'sudo true', password='secret-test', on_output=log.append, expect_ready=True)
        self.assertTrue(channel.pty_opened)
        self.assertEqual(status, 0)
        self.assertEqual(channel.sent, b'secret-test\n')
        self.assertIn('[1] Установка пакетов', output)
        self.assertIn('Права sudo подтверждены.', output)
        self.assertNotIn(SUDO_PROMPT, ''.join(log))

    def test_silent_remote_command_times_out(self):
        class SilentChannel(FakeChannel):
            def __init__(self):
                super().__init__('')
                self.closed = False

            def exit_status_ready(self):
                return False

            def close(self):
                self.closed = True

        channel = SilentChannel()

        class Client:
            def exec_command(self, command, **_kwargs):
                return None, type('Stdout', (), {'channel': channel})(), None

        with self.assertRaisesRegex(RuntimeError, 'не выводил сообщений'):
            _run_command(Client(), 'silent-command', idle_timeout=0)
        self.assertTrue(channel.closed)

    def test_fast_closed_install_channel_reports_remote_error(self):
        class ClosedChannel(FakeChannel):
            def sendall(self, _data):
                raise OSError('Socket is closed')

            def recv_exit_status(self):
                return 1

        channel = ClosedChannel('sudo: a terminal is required to read the password\n')

        class Client:
            def exec_command(self, command, **_kwargs):
                return None, type('Stdout', (), {'channel': channel})(), None

        log = []
        status, output = _run_command(Client(), 'sudo -S true', password='secret-test', on_output=log.append)
        self.assertEqual(status, 1)
        self.assertIn('sudo: a terminal is required', output)
        self.assertEqual(''.join(log), output)
        self.assertNotIn('secret-test', output)

    def test_installs_matching_package_and_keeps_password_out_of_log(self):
        for distro_id, package in (('astra', 'glpi-agent-1.16.tar.gz'), ('linuxmint', 'glpi-agent-1.16-x86_64.AppImage')):
            with self.subTest(distro_id=distro_id):
                fake = FakeSSH(f'ID={distro_id}\n')
                log = []
                with patch('paramiko.SSHClient', return_value=fake):
                    install('192.0.2.10', 'admin', 'secret-test', 'https://glpi.example.test/',
                            confirm_host_key=lambda *_: True, log=log.append)
                self.assertTrue(any(name.endswith(package) for name in fake.files))
                self.assertTrue(any(name.endswith('install.sh') for name in fake.files))
                if distro_id == 'astra':
                    config = next(data for name, data in fake.files.items() if name.endswith('agent.cfg'))
                    self.assertIn(b'server = https://glpi.example.test/', config)
                    self.assertNotIn(b'__GLPI_SERVER__', config)
                self.assertEqual(fake.channels[-1].sent, b'secret-test\n')
                self.assertIn('GLPI_SERVER=', fake.channels[-1].command)
                self.assertIn(SUDO_READY, fake.channels[-1].command)
                self.assertNotIn('secret-test', ''.join(log))
                self.assertNotIn(SUDO_READY, ''.join(log))
                self.assertTrue(fake.removed)
                self.assertEqual(fake.sftp_opens, 1)


if __name__ == '__main__':
    unittest.main()
