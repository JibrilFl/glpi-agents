"""Compact verbose installer output for the desktop window."""

import re


IMPORTANT = re.compile(
    r'^(?:\[[1-8]\]|Подключение|Обнаружена ОС|Передача |Запуск установщика|'
    r'Открытие удалённого терминала|Удалённый терминал открыт|Команда запущена|'
    r'Права sudo подтверждены|Установка завершена|Ожидание вывода|Не удалось|'
    r'Ошибка|Error|Failed|Fatal|Warning|E:|sudo:)',
    re.IGNORECASE,
)


class ProgressOutput:
    def __init__(self, summary_every=100):
        self.pending = ''
        self.suppressed = 0
        self.summary_every = summary_every

    def _line(self, line):
        if IMPORTANT.search(line):
            return line + '\n'
        self.suppressed += 1
        if self.suppressed >= self.summary_every:
            count = self.suppressed
            self.suppressed = 0
            return self._summary(count)
        return ''

    @staticmethod
    def _summary(count):
        last_two = count % 100
        last_one = count % 10
        phrase = 'подробная строка' if last_one == 1 and last_two != 11 else \
            'подробные строки' if last_one in (2, 3, 4) and last_two not in (12, 13, 14) else 'подробных строк'
        return f'Пропущено {count} {phrase} установки.\n'

    def feed(self, chunk):
        self.pending += chunk.replace('\r\n', '\n').replace('\r', '\n')
        visible = []
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            shown = self._line(line)
            if shown:
                visible.append(shown)
        return ''.join(visible)

    def flush(self):
        visible = self._line(self.pending) if self.pending else ''
        self.pending = ''
        if self.suppressed:
            visible += self._summary(self.suppressed)
            self.suppressed = 0
        return visible
