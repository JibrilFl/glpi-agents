import unittest

from glpi_installer.output import ProgressOutput


class ProgressOutputTests(unittest.TestCase):
    def test_keeps_install_stages_and_errors_but_summarizes_noise(self):
        output = ProgressOutput(summary_every=3)
        shown = output.feed('[1] Установка пакетов\nReading package lists\nUnpacking a\nUnpacking b\n')
        self.assertIn('[1] Установка пакетов', shown)
        self.assertIn('Пропущено 3 подробные строки', shown)
        self.assertNotIn('Unpacking', shown)
        self.assertIn('ERROR: package failed', output.feed('ERROR: package failed\n'))

    def test_handles_lines_split_across_ssh_chunks(self):
        output = ProgressOutput()
        self.assertEqual(output.feed('Права sudo под'), '')
        self.assertEqual(output.feed('тверждены.\n'), 'Права sudo подтверждены.\n')


if __name__ == '__main__':
    unittest.main()
