"""Модульные тесты для SentenceAccumulator (core/voice/sentence_streamer.py).

> [!NOTE]
> **Назначение файла:** Проверка потокового накопления предложений, фильтрации сокращений,
> дробных чисел, времени и корректной работы сброса (reset) и выталкивания (flush).
"""

from __future__ import annotations

from core.voice.sentence_streamer import SentenceAccumulator


def test_sentence_accumulator_basic_punctuation_split() -> None:
    """Проверка разделения по базовым знакам препинания с пробелом."""
    acc = SentenceAccumulator()
    chunks = ["Привет! ", "Как твои ", "дела? ", "Все отлично."]

    results: list[str] = []
    for chunk in chunks:
        results.extend(acc.feed(chunk))

    assert results == ["Привет!", "Как твои дела?"]

    # Хвост без завершающего пробела возвращается через flush()
    tail = acc.flush()
    assert tail == "Все отлично."


def test_sentence_accumulator_fractional_numbers_protection() -> None:
    """Проверка защиты от ложного разбиения на дробных числах (3.14, 0.5)."""
    acc = SentenceAccumulator()
    text = "Константа пи равна 3.14 примерно. А половина — это 0.5 ровно."

    results = list(acc.feed(text))
    assert results == ["Константа пи равна 3.14 примерно."]

    tail = acc.flush()
    assert tail == "А половина — это 0.5 ровно."


def test_sentence_accumulator_russian_abbreviations_protection() -> None:
    """Проверка защиты от ложного разбиения на русских сокращениях."""
    acc = SentenceAccumulator()
    text = (
        "Мы купили 2.5 кг. яблок по 120 руб. 50 коп. за килограмм на ул. Ленина в г. Москве. "
        "Встречаемся у д. 10, корп. 2, кв. 15. "
        "Размер коробки 20 см. на 15 мм. в ширину. "
        "Это важно, т.е. необходимо проверить все и т.д. "
        "Конец первой части."
    )

    results = list(acc.feed(text))
    assert len(results) == 4
    expected_first = (
        "Мы купили 2.5 кг. яблок по 120 руб. 50 коп. за килограмм на ул. Ленина в г. Москве."
    )
    assert results[0] == expected_first
    assert results[1] == "Встречаемся у д. 10, корп. 2, кв. 15."
    assert results[2] == "Размер коробки 20 см. на 15 мм. в ширину."
    assert results[3] == "Это важно, т.е. необходимо проверить все и т.д."

    tail = acc.flush()
    assert tail == "Конец первой части."


def test_sentence_accumulator_min_sentence_length_threshold() -> None:
    """Проверка минимального порога длины: одиночные знаки не вызывают синтез."""
    acc = SentenceAccumulator(min_sentence_length=3)

    # Одиночные знаки или цифры списков не должны порождать отдельные предложения
    res1 = list(acc.feed("1. "))
    assert res1 == []

    res2 = list(acc.feed("Открыть окно. "))
    # Объединяется с префиксом "1. "
    assert res2 == ["1. Открыть окно."]


def test_sentence_accumulator_newline_split() -> None:
    """Проверка разделения по переводам строк."""
    acc = SentenceAccumulator()
    text = "Пункт первый\nПункт второй\n\nПункт третий\n"

    results = list(acc.feed(text))
    assert results == ["Пункт первый", "Пункт второй", "Пункт третий"]
    assert acc.flush() is None


def test_sentence_accumulator_reset() -> None:
    """Проверка мгновенной очистки внутреннего буфера при вызове reset()."""
    acc = SentenceAccumulator()
    acc.feed("Недописанный фрагмент")
    assert len(acc.buffer) > 0

    acc.reset()
    assert acc.buffer == ""
    assert acc.flush() is None


def test_sentence_accumulator_flush_empty_and_noise() -> None:
    """Проверка flush() на пустом буфере и шуме из знаков препинания."""
    acc = SentenceAccumulator()
    assert acc.flush() is None

    acc.feed("   ...   ")
    assert acc.flush() is None

    acc.feed("   Да   ")
    assert acc.flush() == "Да"


def test_sentence_accumulator_time_and_ellipsis() -> None:
    """Проверка корректного распознавания времени и многоточия."""
    acc = SentenceAccumulator()
    text = "Поезд отходит в 15:00. Подожди... Мы уже бежим!"

    results = list(acc.feed(text))
    assert results == ["Поезд отходит в 15:00.", "Подожди..."]

    tail = acc.flush()
    assert tail == "Мы уже бежим!"


def test_sentence_accumulator_colon_delimiter() -> None:
    """Проверка разделения по знаку двоеточия с последующим пробелом."""
    acc = SentenceAccumulator()
    text = "Инструкция следующая: откройте окно. Затем нажмите кнопку."

    results = list(acc.feed(text))
    assert results == ["Инструкция следующая:", "откройте окно."]

    tail = acc.flush()
    assert tail == "Затем нажмите кнопку."


def test_sentence_accumulator_token_stream_simulation() -> None:
    """Проверка пошаговой подачи мелких токенов из потока LLM."""
    acc = SentenceAccumulator()
    tokens = [
        "Привет",
        ", ",
        "пользова",
        "тель",
        "! ",
        "Я ",
        "го",
        "тов ",
        "помочь",
        ". ",
        "Хвост ",
        "без ",
        "точки",
    ]

    emitted: list[str] = []
    for token in tokens:
        emitted.extend(acc.feed(token))

    assert emitted == ["Привет, пользователь!", "Я готов помочь."]

    tail = acc.flush()
    assert tail == "Хвост без точки"


def test_sentence_accumulator_multiple_punctuations() -> None:
    """Проверка составных знаков препинания (?!, !!, ...)."""
    acc = SentenceAccumulator()
    text = "Неужели это правда?! Да, абсолютно!! Все подтвердилось..."

    results = list(acc.feed(text))
    assert results == ["Неужели это правда?!", "Да, абсолютно!!"]

    tail = acc.flush()
    assert tail == "Все подтвердилось..."


def test_sentence_accumulator_empty_and_whitespace_chunks() -> None:
    """Проверка обработки пустых и чисто пробельных чанков."""
    acc = SentenceAccumulator()
    assert list(acc.feed("")) == []
    assert list(acc.feed("   ")) == []
    assert acc.flush() is None
