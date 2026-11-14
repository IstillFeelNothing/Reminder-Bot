"""Тести валідації та конвертації дати/часу нагадування.

Контракт функції `parse_reminder_datetime(raw, tz_name, now_utc)`:
- вхід: рядок "YYYY-MM-DD HH:MM" або "DD.MM.YYYY HH:MM" (пробіли нормалізуються),
  IANA-пояс (case-sensitive за zoneinfo.available_timezones()), aware-момент now_utc;
- вихід: `ParsedDateTime(local_datetime, trigger_at_utc, is_ambiguous)`;
- помилки: доменні винятки з domain/exceptions.py, ValueError — для наївного now_utc.
"""

import re
from datetime import datetime, timezone

import pytest

from domain.exceptions import (
    InvalidCalendarDateError,
    InvalidDateTimeFormatError,
    InvalidTimezoneError,
    NonExistentTimeError,
    PastDateTimeError,
)
from domain.validators import parse_reminder_datetime

# 19:35 за Europe/Kyiv (UTC+2)
NOW_UTC = datetime(2026, 11, 14, 17, 35, tzinfo=timezone.utc)

TRIGGER_AT_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.mark.parametrize(
    ("raw", "tz_name", "local_datetime", "trigger_at_utc", "is_ambiguous"),
    [
        pytest.param(
            "2026-11-15 09:00", "Europe/Kyiv",
            "2026-11-15T09:00:00", "2026-11-15T07:00:00Z", False,
            id="01-kyiv-winter-utc+2",
        ),
        pytest.param(
            "2027-06-15 09:00", "Europe/Kyiv",
            "2027-06-15T09:00:00", "2027-06-15T06:00:00Z", False,
            id="02-kyiv-summer-utc+3",
        ),
        pytest.param(
            "  15.11.2026   09:00  ", "Europe/Kyiv",
            "2026-11-15T09:00:00", "2026-11-15T07:00:00Z", False,
            id="03-ua-format-and-extra-spaces",
        ),
        pytest.param(
            "2026-11-15 21:30", "America/New_York",
            "2026-11-15T21:30:00", "2026-11-16T02:30:00Z", False,
            id="05-new-york-utc-date-rolls-forward",
        ),
        pytest.param(
            "2026-11-15 10:00", "Asia/Kolkata",
            "2026-11-15T10:00:00", "2026-11-15T04:30:00Z", False,
            id="06-kolkata-fractional-offset",
        ),
        pytest.param(
            "2027-03-28 04:00", "Europe/Kyiv",
            "2027-03-28T04:00:00", "2027-03-28T01:00:00Z", False,
            id="09-spring-gap-right-boundary",
        ),
        pytest.param(
            "2027-10-31 03:00", "Europe/Kyiv",
            "2027-10-31T03:00:00", "2027-10-31T00:00:00Z", True,
            id="10-autumn-fold-left-boundary",
        ),
        pytest.param(
            "2027-10-31 03:30", "Europe/Kyiv",
            "2027-10-31T03:30:00", "2027-10-31T00:30:00Z", True,
            id="11-autumn-fold-middle",
        ),
        pytest.param(
            "2027-10-31 04:00", "Europe/Kyiv",
            "2027-10-31T04:00:00", "2027-10-31T02:00:00Z", False,
            id="12-autumn-fold-right-boundary",
        ),
        pytest.param(
            "2026-11-14 15:00", "America/New_York",
            "2026-11-14T15:00:00", "2026-11-14T20:00:00Z", False,
            id="15-same-day-future-in-user-tz-not-past",
        ),
        pytest.param(
            "2027-03-28 02:59", "Europe/Kyiv",
            "2027-03-28T02:59:00", "2027-03-28T00:59:00Z", False,
            id="20-last-valid-minute-before-spring-gap",
        ),
        pytest.param(
            "2027-10-31 03:59", "Europe/Kyiv",
            "2027-10-31T03:59:00", "2027-10-31T00:59:00Z", True,
            id="21-autumn-fold-last-ambiguous-minute",
        ),
    ],
)
def test_valid_input_is_converted(raw, tz_name, local_datetime, trigger_at_utc, is_ambiguous):
    result = parse_reminder_datetime(raw, tz_name, NOW_UTC)

    assert result.local_datetime == local_datetime
    assert result.trigger_at_utc == trigger_at_utc
    assert result.is_ambiguous is is_ambiguous


def test_04_trigger_at_utc_output_format_contract():
    result = parse_reminder_datetime("2026-11-15 09:00", "Europe/Kyiv", NOW_UTC)

    assert len(result.trigger_at_utc) == 20
    assert result.trigger_at_utc.endswith("Z")
    assert "+00:00" not in result.trigger_at_utc
    assert "." not in result.trigger_at_utc  # без мікросекунд
    assert TRIGGER_AT_UTC_RE.fullmatch(result.trigger_at_utc)


@pytest.mark.parametrize(
    ("raw", "tz_name", "expected_error"),
    [
        pytest.param("2027-03-28 03:30", "Europe/Kyiv", NonExistentTimeError,
                     id="07-spring-gap-middle"),
        pytest.param("2027-03-28 03:00", "Europe/Kyiv", NonExistentTimeError,
                     id="08-spring-gap-left-boundary"),
        pytest.param("2026-11-14 19:34", "Europe/Kyiv", PastDateTimeError,
                     id="13-one-minute-in-past"),
        pytest.param("2026-11-14 19:35", "Europe/Kyiv", PastDateTimeError,
                     id="14-equals-now"),
        pytest.param("2027-02-29 12:00", "Europe/Kyiv", InvalidCalendarDateError,
                     id="16-feb-29-non-leap-year"),
        pytest.param("завтра о дев'ятій", "Europe/Kyiv", InvalidDateTimeFormatError,
                     id="17-unparseable-format"),
        pytest.param("2026-11-15 09:00", "europe/kyiv", InvalidTimezoneError,
                     id="18-timezone-wrong-case"),
        pytest.param("31.02.2026 12:00", "Europe/Kyiv", InvalidCalendarDateError,
                     id="22-ua-format-non-existent-date"),
        pytest.param("2026-11-15 25:00", "Europe/Kyiv", InvalidDateTimeFormatError,
                     id="23-hour-25-out-of-range"),
        pytest.param("2026-11-15 24:00", "Europe/Kyiv", InvalidDateTimeFormatError,
                     id="24-hour-24-out-of-range"),
        pytest.param("2026-11-15 09:00", "Mars/Base", InvalidTimezoneError,
                     id="25-unknown-timezone"),
        pytest.param("", "Europe/Kyiv", InvalidDateTimeFormatError,
                     id="26-empty-string"),
        pytest.param("   ", "Europe/Kyiv", InvalidDateTimeFormatError,
                     id="27-whitespace-only-string"),
    ],
)
def test_invalid_input_raises_domain_error(raw, tz_name, expected_error):
    with pytest.raises(expected_error) as exc_info:
        parse_reminder_datetime(raw, tz_name, NOW_UTC)

    # Точний тип: напр. 25:00 — помилка формату, а не календарна дата,
    # навіть якщо винятки успадковуються один від одного.
    assert exc_info.type is expected_error


def test_19_naive_now_utc_is_contract_violation():
    naive_now = datetime(2026, 11, 14, 17, 35)

    with pytest.raises(ValueError) as exc_info:
        parse_reminder_datetime("2026-11-15 09:00", "Europe/Kyiv", naive_now)

    # Саме помилка контракту коду, а не доменний виняток валідації введення.
    assert exc_info.type is ValueError
