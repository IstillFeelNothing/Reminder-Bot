"""Тести валідації введення нагадування: текст і дата/час.

Контракт функції `validate_reminder_text(raw)`:
- обрізає пробільні символи (включно з переносами рядків) лише на краях;
- усередині тексту пробіли й переноси рядків зберігаються без змін;
- довжина рахується після обрізання, у символах Python (code points);
  допустимо 1..MAX_REMINDER_TEXT_LENGTH (1000) включно;
- порожній після обрізання текст → EmptyReminderTextError,
  довший за ліміт → ReminderTextTooLongError (обидва — ReminderValidationError).

Контракт функції `parse_reminder_datetime(raw, tz_name, now_utc)`:
- вхід: рядок "YYYY-MM-DD HH:MM" або "DD.MM.YYYY HH:MM" (пробіли нормалізуються),
  IANA-пояс (case-sensitive за zoneinfo.available_timezones()), aware-момент now_utc;
- вихід: `ParsedDateTime(local_datetime, trigger_at_utc, is_ambiguous)`, де
  local_datetime — наївний datetime (намір користувача), trigger_at_utc — aware datetime у UTC;
  формат зберігання в БД перевіряють тести репозиторію;
- помилки: доменні винятки з domain/exceptions.py, ValueError — для наївного now_utc.
"""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from domain.exceptions import (
    EmptyReminderTextError,
    InvalidCalendarDateError,
    InvalidDateTimeFormatError,
    InvalidTimezoneError,
    NonExistentTimeError,
    PastDateTimeError,
    ReminderTextTooLongError,
    ReminderValidationError,
)
from domain.validators import (
    MAX_REMINDER_TEXT_LENGTH,
    parse_reminder_datetime,
    validate_reminder_text,
)

# 19:35 за Europe/Kyiv (UTC+2)
NOW_UTC = datetime(2026, 11, 14, 17, 35, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "tz_name", "local_datetime", "trigger_at_utc", "is_ambiguous"),
    [
        pytest.param(
            "2026-11-15 09:00",
            "Europe/Kyiv",
            datetime(2026, 11, 15, 9, 0),
            datetime(2026, 11, 15, 7, 0, tzinfo=UTC),
            False,
            id="01-kyiv-winter-utc+2",
        ),
        pytest.param(
            "2027-06-15 09:00",
            "Europe/Kyiv",
            datetime(2027, 6, 15, 9, 0),
            datetime(2027, 6, 15, 6, 0, tzinfo=UTC),
            False,
            id="02-kyiv-summer-utc+3",
        ),
        pytest.param(
            "  15.11.2026   09:00  ",
            "Europe/Kyiv",
            datetime(2026, 11, 15, 9, 0),
            datetime(2026, 11, 15, 7, 0, tzinfo=UTC),
            False,
            id="03-ua-format-and-extra-spaces",
        ),
        pytest.param(
            "2026-11-15 21:30",
            "America/New_York",
            datetime(2026, 11, 15, 21, 30),
            datetime(2026, 11, 16, 2, 30, tzinfo=UTC),
            False,
            id="05-new-york-utc-date-rolls-forward",
        ),
        pytest.param(
            "2026-11-15 10:00",
            "Asia/Kolkata",
            datetime(2026, 11, 15, 10, 0),
            datetime(2026, 11, 15, 4, 30, tzinfo=UTC),
            False,
            id="06-kolkata-fractional-offset",
        ),
        pytest.param(
            "2027-03-28 04:00",
            "Europe/Kyiv",
            datetime(2027, 3, 28, 4, 0),
            datetime(2027, 3, 28, 1, 0, tzinfo=UTC),
            False,
            id="09-spring-gap-right-boundary",
        ),
        pytest.param(
            "2027-10-31 03:00",
            "Europe/Kyiv",
            datetime(2027, 10, 31, 3, 0),
            datetime(2027, 10, 31, 0, 0, tzinfo=UTC),
            True,
            id="10-autumn-fold-left-boundary",
        ),
        pytest.param(
            "2027-10-31 03:30",
            "Europe/Kyiv",
            datetime(2027, 10, 31, 3, 30),
            datetime(2027, 10, 31, 0, 30, tzinfo=UTC),
            True,
            id="11-autumn-fold-middle",
        ),
        pytest.param(
            "2027-10-31 04:00",
            "Europe/Kyiv",
            datetime(2027, 10, 31, 4, 0),
            datetime(2027, 10, 31, 2, 0, tzinfo=UTC),
            False,
            id="12-autumn-fold-right-boundary",
        ),
        pytest.param(
            "2026-11-14 15:00",
            "America/New_York",
            datetime(2026, 11, 14, 15, 0),
            datetime(2026, 11, 14, 20, 0, tzinfo=UTC),
            False,
            id="15-same-day-future-in-user-tz-not-past",
        ),
        pytest.param(
            "2027-03-28 02:59",
            "Europe/Kyiv",
            datetime(2027, 3, 28, 2, 59),
            datetime(2027, 3, 28, 0, 59, tzinfo=UTC),
            False,
            id="20-last-valid-minute-before-spring-gap",
        ),
        pytest.param(
            "2027-10-31 03:59",
            "Europe/Kyiv",
            datetime(2027, 10, 31, 3, 59),
            datetime(2027, 10, 31, 0, 59, tzinfo=UTC),
            True,
            id="21-autumn-fold-last-ambiguous-minute",
        ),
        pytest.param(
            "5.1.2027 9:00",
            "Europe/Kyiv",
            datetime(2027, 1, 5, 9, 0),
            datetime(2027, 1, 5, 7, 0, tzinfo=UTC),
            False,
            id="29-ua-format-without-leading-zeros",
        ),
        pytest.param(
            "2027-01-05 23:59",
            "Europe/Kyiv",
            datetime(2027, 1, 5, 23, 59),
            datetime(2027, 1, 5, 21, 59, tzinfo=UTC),
            False,
            id="30-max-valid-hour-and-minute",
        ),
        pytest.param(
            "2027-1-5 0:00",
            "Europe/Kyiv",
            datetime(2027, 1, 5, 0, 0),
            datetime(2027, 1, 4, 22, 0, tzinfo=UTC),
            False,
            id="35-iso-format-without-leading-zeros-midnight",
        ),
        pytest.param(
            "2027-01-05 20:00",
            "Europe/Kyiv",
            datetime(2027, 1, 5, 20, 0),
            datetime(2027, 1, 5, 18, 0, tzinfo=UTC),
            False,
            id="36-hour-20-boundary",
        ),
    ],
)
def test_valid_input_is_converted(
    raw: str,
    tz_name: str,
    local_datetime: datetime,
    trigger_at_utc: datetime,
    is_ambiguous: bool,
) -> None:
    result = parse_reminder_datetime(raw, tz_name, NOW_UTC)

    # naive == aware дає False, тож рівність заодно фіксує, що local_datetime наївний.
    assert result.local_datetime == local_datetime
    assert result.trigger_at_utc == trigger_at_utc
    # Рівність aware-значень порівнює лише момент — окремо фіксуємо саме UTC.
    assert result.trigger_at_utc.utcoffset() == timedelta(0)
    assert result.is_ambiguous is is_ambiguous


@pytest.mark.parametrize(
    ("raw", "tz_name", "expected_error"),
    [
        pytest.param(
            "2027-03-28 03:30", "Europe/Kyiv", NonExistentTimeError, id="07-spring-gap-middle"
        ),
        pytest.param(
            "2027-03-28 03:00",
            "Europe/Kyiv",
            NonExistentTimeError,
            id="08-spring-gap-left-boundary",
        ),
        pytest.param(
            "2026-11-14 19:34", "Europe/Kyiv", PastDateTimeError, id="13-one-minute-in-past"
        ),
        pytest.param("2026-11-14 19:35", "Europe/Kyiv", PastDateTimeError, id="14-equals-now"),
        pytest.param(
            "2027-02-29 12:00",
            "Europe/Kyiv",
            InvalidCalendarDateError,
            id="16-feb-29-non-leap-year",
        ),
        pytest.param(
            "завтра о дев'ятій",
            "Europe/Kyiv",
            InvalidDateTimeFormatError,
            id="17-unparseable-format",
        ),
        pytest.param(
            "2026-11-15 09:00", "europe/kyiv", InvalidTimezoneError, id="18-timezone-wrong-case"
        ),
        pytest.param(
            "31.02.2026 12:00",
            "Europe/Kyiv",
            InvalidCalendarDateError,
            id="22-ua-format-non-existent-date",
        ),
        pytest.param(
            "2026-11-15 25:00",
            "Europe/Kyiv",
            InvalidDateTimeFormatError,
            id="23-hour-25-out-of-range",
        ),
        pytest.param(
            "2026-11-15 24:00",
            "Europe/Kyiv",
            InvalidDateTimeFormatError,
            id="24-hour-24-out-of-range",
        ),
        pytest.param(
            "2026-11-15 09:00", "Mars/Base", InvalidTimezoneError, id="25-unknown-timezone"
        ),
        pytest.param(
            "0001-01-01 00:00",
            "Europe/Kyiv",
            InvalidCalendarDateError,
            id="28-min-year-utc-conversion-underflows",
        ),
        pytest.param(
            "2026-11-15 09:60",
            "Europe/Kyiv",
            InvalidDateTimeFormatError,
            id="31-minute-60-out-of-range",
        ),
        pytest.param(
            "2026-11-15 9:5",
            "Europe/Kyiv",
            InvalidDateTimeFormatError,
            id="32-single-digit-minute",
        ),
        pytest.param(
            "2026-13-01 12:00",
            "Europe/Kyiv",
            InvalidCalendarDateError,
            id="33-month-13-is-calendar-error",
        ),
        pytest.param(
            "00.01.2027 12:00",
            "Europe/Kyiv",
            InvalidCalendarDateError,
            id="37-day-00-is-calendar-error",
        ),
        pytest.param(
            "32.01.2027 12:00",
            "Europe/Kyiv",
            InvalidCalendarDateError,
            id="38-day-32-is-calendar-error",
        ),
        pytest.param(
            "2027-01-05 000:00",
            "Europe/Kyiv",
            InvalidDateTimeFormatError,
            id="39-three-digit-hour",
        ),
        pytest.param(
            "2027-01-05 00:000",
            "Europe/Kyiv",
            InvalidDateTimeFormatError,
            id="40-three-digit-minute",
        ),
        pytest.param(
            "2026-11-15 ١٢:00",
            "Europe/Kyiv",
            InvalidDateTimeFormatError,
            id="34-non-ascii-digits",
        ),
        pytest.param("", "Europe/Kyiv", InvalidDateTimeFormatError, id="26-empty-string"),
        pytest.param(
            "   ", "Europe/Kyiv", InvalidDateTimeFormatError, id="27-whitespace-only-string"
        ),
    ],
)
def test_invalid_input_raises_domain_error(
    raw: str, tz_name: str, expected_error: type[Exception]
) -> None:
    with pytest.raises(expected_error) as exc_info:
        parse_reminder_datetime(raw, tz_name, NOW_UTC)

    # Точний тип: напр. 25:00 — помилка формату, а не календарна дата,
    # навіть якщо винятки успадковуються один від одного.
    assert exc_info.type is expected_error


def test_19_naive_now_utc_is_contract_violation() -> None:
    naive_now = datetime(2026, 11, 14, 17, 35)

    with pytest.raises(ValueError) as exc_info:
        parse_reminder_datetime("2026-11-15 09:00", "Europe/Kyiv", naive_now)

    # Саме помилка контракту коду, а не доменний виняток валідації введення.
    assert exc_info.type is ValueError


def test_19_naive_now_utc_is_checked_before_input_validation() -> None:
    naive_now = datetime(2026, 11, 14, 17, 35)

    # Навіть за некоректного введення першою спрацьовує перевірка контракту.
    with pytest.raises(ValueError) as exc_info:
        parse_reminder_datetime("сміття", "Mars/Base", naive_now)

    assert exc_info.type is ValueError


# Той самий фізичний момент, що й NOW_UTC, у різних представленнях.
_SAME_INSTANT_AS_NOW = [
    pytest.param(NOW_UTC.astimezone(ZoneInfo("Europe/Kyiv")), id="zoneinfo-kyiv"),
    pytest.param(NOW_UTC.astimezone(ZoneInfo("America/New_York")), id="zoneinfo-new-york"),
    pytest.param(NOW_UTC.astimezone(timezone(timedelta(hours=5, minutes=30))), id="fixed-offset"),
]


@pytest.mark.parametrize("now", _SAME_INSTANT_AS_NOW)
@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("2026-11-14 19:34", id="41-one-minute-in-past"),
        pytest.param("2026-11-14 19:35", id="41-equals-now"),
    ],
)
def test_41_any_aware_now_rejects_past_by_instant(raw: str, now: datetime) -> None:
    with pytest.raises(PastDateTimeError) as exc_info:
        parse_reminder_datetime(raw, "Europe/Kyiv", now)

    assert exc_info.type is PastDateTimeError


@pytest.mark.parametrize("now", _SAME_INSTANT_AS_NOW)
def test_42_any_aware_now_accepts_future_by_instant(now: datetime) -> None:
    result = parse_reminder_datetime("2026-11-14 19:36", "Europe/Kyiv", now)

    assert result.local_datetime == datetime(2026, 11, 14, 19, 36)
    assert result.trigger_at_utc == datetime(2026, 11, 14, 17, 36, tzinfo=UTC)
    assert result.is_ambiguous is False


# --- validate_reminder_text ---------------------------------------------------


def test_43_max_reminder_text_length_is_1000() -> None:
    assert MAX_REMINDER_TEXT_LENGTH == 1000


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("Купити хліб", "Купити хліб", id="44-plain-text-unchanged"),
        pytest.param("   Купити хліб   ", "Купити хліб", id="45-edge-spaces-stripped"),
        pytest.param("\n\t Купити хліб \t\n", "Купити хліб", id="46-edge-newlines-tabs-stripped"),
        pytest.param(" Купити хліб ", "Купити хліб", id="47-edge-nbsp-stripped"),
        pytest.param("Купити   хліб", "Купити   хліб", id="48-inner-spaces-kept"),
        pytest.param(
            "Список:\n- хліб\n\n- молоко",
            "Список:\n- хліб\n\n- молоко",
            id="49-inner-newlines-kept",
        ),
        pytest.param(
            "\n  Список:\n  - хліб  \n",
            "Список:\n  - хліб",
            id="50-inner-indent-kept-edges-stripped",
        ),
        pytest.param("Подзвонити мамі 📞", "Подзвонити мамі 📞", id="51-emoji-kept"),
        pytest.param("x", "x", id="52-single-char"),
    ],
)
def test_valid_reminder_text_is_normalized(raw: str, expected: str) -> None:
    assert validate_reminder_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("a" * MAX_REMINDER_TEXT_LENGTH, id="53-exactly-limit"),
        pytest.param("  " + "a" * MAX_REMINDER_TEXT_LENGTH + "\n\n", id="54-limit-after-strip"),
        pytest.param("a\n" * 499 + "ab", id="55-limit-with-inner-newlines"),
    ],
)
def test_reminder_text_at_limit_is_accepted(raw: str) -> None:
    result = validate_reminder_text(raw)

    assert len(result) == MAX_REMINDER_TEXT_LENGTH


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("", id="56-empty"),
        pytest.param("   ", id="57-only-spaces"),
        pytest.param("\n\t\r\n  ", id="58-only-whitespace-mixed"),
    ],
)
def test_empty_reminder_text_is_rejected(raw: str) -> None:
    with pytest.raises(EmptyReminderTextError):
        validate_reminder_text(raw)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("a" * (MAX_REMINDER_TEXT_LENGTH + 1), id="59-one-over-limit"),
        pytest.param("a\n" * 500 + "a", id="60-over-limit-counting-newlines"),
        pytest.param("a" * 5000, id="61-far-over-limit"),
    ],
)
def test_too_long_reminder_text_is_rejected(raw: str) -> None:
    with pytest.raises(ReminderTextTooLongError) as exc_info:
        validate_reminder_text(raw)

    assert str(MAX_REMINDER_TEXT_LENGTH) in str(exc_info.value)


def test_62_text_errors_are_validation_errors() -> None:
    # Обробник діалогу ловить ReminderValidationError і показує повідомлення користувачу.
    assert issubclass(EmptyReminderTextError, ReminderValidationError)
    assert issubclass(ReminderTextTooLongError, ReminderValidationError)
