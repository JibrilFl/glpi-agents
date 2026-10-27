import unittest
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from glpi_installer.core import (
    UnsupportedOS,
    build_install_command,
    detect_distribution,
    files_for_distribution,
    render_astra_config,
    read_env_server,
    validate_glpi_server,
)


class DistributionTests(unittest.TestCase):
    def test_detects_astra_and_mint(self):
        self.assertEqual(detect_distribution('ID=astra\nNAME="Astra Linux"\n'), 'astra')
        self.assertEqual(detect_distribution('ID=linuxmint\nNAME="Linux Mint"\n'), 'mint')

    def test_rejects_other_system(self):
        with self.assertRaises(UnsupportedOS):
            detect_distribution('ID=ubuntu\nNAME="Ubuntu"\n')

    def test_selects_only_own_package(self):
        astra = [p.name for p in files_for_distribution('astra')]
        mint = [p.name for p in files_for_distribution('mint')]
        self.assertIn('glpi-agent-1.16.tar.gz', astra)
        self.assertIn('agent.cfg', astra)
        self.assertIn('glpi-agent-1.16-x86_64.AppImage', mint)
        self.assertNotIn('glpi-agent-1.16-x86_64.AppImage', astra)
        self.assertNotIn('glpi-agent-1.16.tar.gz', mint)


class ConfigurationTests(unittest.TestCase):
    def test_frozen_app_reads_env_next_to_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / 'GLPI-Agent-Installer.exe'
            (Path(temp) / '.env').write_text('GLPI_SERVER=https://glpi.example.test/\n', encoding='utf-8')
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(executable)):
                self.assertEqual(read_env_server(), 'https://glpi.example.test/')

    def test_url_validation_rejects_shell_control_and_credentials(self):
        self.assertEqual(validate_glpi_server('https://glpi.example.test/'), 'https://glpi.example.test/')
        for bad in ('', 'ftp://glpi.example.test', 'http://user:pass@host', 'http://host\nwhoami', 'http://host;whoami'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_glpi_server(bad)

    def test_astra_config_contains_supplied_server_once(self):
        template = 'server = __GLPI_SERVER__\nfoo = bar\n'
        result = render_astra_config(template, 'https://glpi.example.test/collector')
        self.assertIn('server = https://glpi.example.test/collector', result)
        self.assertNotIn('__GLPI_SERVER__', result)

    def test_command_quotes_remote_values(self):
        command = build_install_command('mint', '/tmp/glpi-agent.test123', 'https://glpi.example.test/a?x=1&y=2', use_sudo=True)
        self.assertIn("'https://glpi.example.test/a?x=1&y=2'", command)
        self.assertIn('sudo -S', command)
        self.assertIn('install.sh', command)


if __name__ == '__main__':
    unittest.main()
