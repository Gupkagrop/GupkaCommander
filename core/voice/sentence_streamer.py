"""Модуль потокового разделения предложений на базе SentenceAccumulator.

> [!NOTE]
> **Назначение файла:** Потоковое накопление текстовых токенов и разделение на законченные
> предложения для синтеза речи (TTS) с защитой от ложного разбиения на дробных числах
> и русских сокращениях.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final

# Одиночные сокращения русского языка, за которыми следует точка
SINGLE_WORD_ABBREVIATIONS: Final[frozenset[str]] = frozenset(
    {
        "г",  # город / год
        "гг",  # годы
        "д",  # дом / деревня
        "ул",  # улица
        "руб",  # рубли
        "коп",  # копейки
        "корп",  # корпус
        "кв",  # квартира
        "см",  # сантиметры
        "мм",  # миллиметры
        "м",  # метры
        "км",  # километры
        "кг",  # килограммы
        "мг",  # миллиграммы
        "мл",  # миллилитры
        "л",  # литры
        "стр",  # страница
        "др",  # другие
        "пр",  # прочие / проспект
        "в",  # век
        "вв",  # века
        "напр",  # например
        "рис",  # рисунок
        "табл",  # таблица
        "п",  # пункт
        "ст",  # статья / станция
        "вып",  # выпуск
        "разд",  # раздел
        "т",  # том / тысяча / то (в т.е.)
    }
)

# Адресно-номенклатурные указатели, которые никогда не завершают предложение,
# даже если следующее слово с заглавной буквы ("г. Москва", "ул. Ленина")
SPECIFIER_ABBREVIATIONS: Final[frozenset[str]] = frozenset(
    {"г", "ул", "д", "корп", "кв", "стр", "рис", "табл", "п", "ст"}
)

# Составные сокращения с точками
COMPOUND_ABBREVIATIONS: Final[frozenset[str]] = frozenset(
    {
        "т.е",  # то есть
        "т.д",  # так далее
        "т.п",  # тому подобное
        "т.к",  # так как
        "т.о",  # таким образом
        "н.э",  # нашей эры
        "и.о",  # исполняющий обязанности
    }
)

# Регулярное выражение для поиска границ предложений:
# 1) Знаки [.!?:] с закрывающими кавычками/скобками, за которыми идет пробел или \n
# 2) Либо явный перевод строки \n+
SPLIT_REGEX: Final[re.Pattern[str]] = re.compile(
    r'(?P<punct>[.!?:]+)(?P<quotes>["\'»”\)\]]*)(?P<ws>\s+)|(?P<newline>\n+)'
)


class SentenceAccumulator:
    """Накопитель текстовых токенов для потокового выделения законченных предложений."""

    def __init__(self, min_sentence_length: int = 3) -> None:
        """Инициализация аккумулятора предложений.

        Args:
            min_sentence_length: Минимальная длина предложения в символах для отсева
                одиночных знаков препинания и маркеров списков.
        """
        self._min_length: int = min_sentence_length
        self._buffer: str = ""

    @property
    def buffer(self) -> str:
        """Текущее содержимое внутреннего буфера."""
        return self._buffer

    def feed(self, chunk: str) -> Iterator[str]:
        """Накапливает входящие текстовые токены и выдает законченные предложения.

        Args:
            chunk: Фрагмент текста (токен или группа токенов).

        Returns:
            Итератор законченных предложений, готовых для передачи в TTS-синтез.
        """
        if not chunk:
            return iter(())

        self._buffer += chunk
        completed_sentences: list[str] = []
        search_pos = 0

        while search_pos < len(self._buffer):
            match = SPLIT_REGEX.search(self._buffer, search_pos)
            if not match:
                break

            punct = match.group("punct") or ""
            quotes = match.group("quotes") or ""
            match_end = match.end()
            sentence_end = match.start() + len(punct) + len(quotes) if punct else match.start()
            prefix = self._buffer[: match.start()]
            suffix = self._buffer[match_end:]

            # Защита 1: проверка на русские сокращения
            if punct and self._is_abbreviation(prefix, punct, suffix):
                search_pos = match.start() + 1
                continue

            # Защита 2: проверка на дробные числа (например, 3.14 или 0.5)
            if self._is_fractional_number(prefix, punct, suffix):
                search_pos = match.start() + 1
                continue

            # Защита 3: проверка на время или соотношения с двоеточием (например, 15:00)
            if self._is_time_pattern(prefix, punct, suffix):
                search_pos = match.start() + 1
                continue

            candidate = self._buffer[:sentence_end].strip()

            # Защита 4: минимальный порог длины и наличие буквенно-цифровых символов
            if len(candidate) < self._min_length or not any(c.isalnum() for c in candidate):
                search_pos = match.start() + 1
                continue

            completed_sentences.append(candidate)
            self._buffer = self._buffer[match_end:]
            search_pos = 0

        return iter(completed_sentences)

    def flush(self) -> str | None:
        """Возвращает оставшийся хвост текста в конце потока.

        Returns:
            Оставшийся текст, если он содержит полезную информацию, либо None.
        """
        tail = self._buffer.strip()
        self._buffer = ""
        if not tail:
            return None

        # Отсекаем остатки, состоящие только из знаков препинания или спецсимволов
        if not any(char.isalnum() for char in tail):
            return None

        return tail

    def reset(self) -> None:
        """Мгновенная очистка внутреннего буфера."""
        self._buffer = ""

    def _is_abbreviation(self, prefix: str, punct: str, suffix: str) -> bool:
        """Проверяет, завершается ли префикс перед точкой сокращением."""
        if "." not in punct:
            return False

        token_match = re.search(r"([а-яёa-z0-9_.]+)$", prefix, re.IGNORECASE)
        if not token_match:
            return False

        token = token_match.group(1).lower()
        if token not in SINGLE_WORD_ABBREVIATIONS and token not in COMPOUND_ABBREVIATIONS:
            return False

        # Адресно-номенклатурные указатели ("г.", "ул.", "д.") никогда не завершают предложение
        if token in SPECIFIER_ABBREVIATIONS:
            return True

        # "т.е.", "т.к.", "т.о." вводят пояснение и не завершают предложение
        if token in {"т.е", "т.к", "т.о"}:
            return True

        # Для единиц измерения и "т.д." / "т.п.":
        # Если следующее слово начинается с заглавной буквы — это конец предложения
        next_word_match = re.match(r"^\s*([^\s]+)", suffix)
        if next_word_match:
            first_char = next_word_match.group(1)[0]
            if first_char.isupper():
                return False

        return True

    @staticmethod
    def _is_fractional_number(prefix: str, punct: str, suffix: str) -> bool:
        """Проверяет, является ли точка разделителем в дробном числе."""
        if punct != ".":
            return False
        # Точка между цифрами: префикс кончается цифрой, суффикс начинается с цифры
        return bool(re.search(r"\d$", prefix) and re.match(r"^\d", suffix))

    @staticmethod
    def _is_time_pattern(prefix: str, punct: str, suffix: str) -> bool:
        """Проверяет, является ли двоеточие разделителем времени или пропорции."""
        if punct != ":":
            return False
        return bool(re.search(r"\d$", prefix) and re.match(r"^\d", suffix))
