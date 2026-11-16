"""Тести доменних моделей.

Контракт `domain/models.py`:
- `ReminderStatus(StrEnum)`: `pending`, `sent`, `failed`.
- `Reminder` — `@dataclass(frozen=True)` з полями id (`int | None`, None — ще не збережено),
  user_id, chat_id, text, local_datetime, trigger_at_utc, status, created_at.
- `User` — `@dataclass(frozen=True)` з полями user_id, timezone, created_at.
- `__post_init__` відхиляє лише стан, неконсистентний для системи; ValueError називає поле
  у повідомленні. Бізнес-правила (напр. «час не в минулому») перевіряє валідатор:
  - text порожній після strip → ValueError;
  - local_datetime aware → ValueError (це намір користувача без поясу);
  - trigger_at_utc / created_at наївні або зі зсувом, відмінним від нуля → ValueError;
  - User.timezone порожній після strip → ValueError;
  - status: сирий рядок із відомим значенням нормалізується до ReminderStatus,
    будь-яке інше значення (зокрема не рядок: None, int, bytes) →
    InvalidReminderFieldError(reminder_id, user_id, field, value). Тип поля — ReminderStatus | str,
    після __post_init__ завжди ReminderStatus.
- Модель не змінює дані, лише перевіряє: text зберігається як є (без strip).
- Порядок перевірок не є контрактом: при кількох порушеннях може піднятися будь-яке з них.
"""

import dataclasses
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from domain.exceptions import DomainError, InvalidReminderFieldError, ReminderValidationError
from domain.models import Reminder, ReminderStatus, User

NOW_UTC = datetime(2026, 11, 14, 17, 35, tzinfo=UTC)
KYIV = ZoneInfo("Europe/Kyiv")


def make_reminder(**overrides: Any) -> Reminder:
    """Валідне нагадування; кожен тест змінює лише поле, яке перевіряє."""
    fields: dict[str, Any] = {
        "id": 7,
        "user_id": 100,
        "chat_id": 200,
        "text": "Купити хліб",
        "local_datetime": datetime(2026, 11, 15, 9, 0),
        "trigger_at_utc": datetime(2026, 11, 15, 7, 0, tzinfo=UTC),
        "status": ReminderStatus.PENDING,
        "created_at": NOW_UTC,
    }
    fields.update(overrides)
    return Reminder(**fields)


def make_user(**overrides: Any) -> User:
    fields: dict[str, Any] = {
        "user_id": 100,
        "timezone": "Europe/Kyiv",
        "created_at": NOW_UTC,
    }
    fields.update(overrides)
    return User(**fields)


# --- ReminderStatus ---------------------------------------------------------------


def test_01_status_values_match_db_check_constraint() -> None:
    assert {status.value for status in ReminderStatus} == {"pending", "sent", "failed"}


def test_02_status_is_str_enum() -> None:
    # StrEnum: значення можна писати в БД і порівнювати з рядком без .value.
    assert ReminderStatus.PENDING == "pending"
    assert isinstance(ReminderStatus.SENT, str)


# --- Reminder: валідне створення ---------------------------------------------------


def test_03_valid_reminder_keeps_fields() -> None:
    reminder = make_reminder()

    assert reminder.id == 7
    assert reminder.user_id == 100
    assert reminder.chat_id == 200
    assert reminder.text == "Купити хліб"
    assert reminder.local_datetime == datetime(2026, 11, 15, 9, 0)
    assert reminder.trigger_at_utc == datetime(2026, 11, 15, 7, 0, tzinfo=UTC)
    assert reminder.status is ReminderStatus.PENDING
    assert reminder.created_at == NOW_UTC


def test_04_id_is_none_before_saving() -> None:
    reminder = make_reminder(id=None)

    assert reminder.id is None


def test_05_text_is_kept_as_is() -> None:
    # Обрізання пробілів — справа валідатора тексту; модель дані не змінює.
    reminder = make_reminder(text="  Купити хліб  ")

    assert reminder.text == "  Купити хліб  "


def test_06_reminder_is_frozen() -> None:
    reminder = make_reminder()

    with pytest.raises(dataclasses.FrozenInstanceError):
        reminder.text = "Інший текст"  # type: ignore[misc]


@pytest.mark.parametrize(
    "utc_tz",
    [
        pytest.param(UTC, id="07-datetime-utc"),
        pytest.param(timezone(timedelta(0)), id="07-fixed-zero-offset"),
        pytest.param(ZoneInfo("UTC"), id="07-zoneinfo-utc"),
    ],
)
def test_07_any_zero_offset_tz_is_utc(utc_tz: tzinfo) -> None:
    trigger = datetime(2026, 11, 15, 7, 0, tzinfo=utc_tz)

    reminder = make_reminder(trigger_at_utc=trigger, created_at=NOW_UTC.astimezone(utc_tz))

    assert reminder.trigger_at_utc == datetime(2026, 11, 15, 7, 0, tzinfo=UTC)


# --- Reminder: статус ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("pending", ReminderStatus.PENDING, id="08-raw-pending"),
        pytest.param("sent", ReminderStatus.SENT, id="08-raw-sent"),
        pytest.param("failed", ReminderStatus.FAILED, id="08-raw-failed"),
    ],
)
def test_08_known_raw_status_is_normalized_to_enum(raw: str, expected: ReminderStatus) -> None:
    reminder = make_reminder(status=raw)

    assert reminder.status is expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("archived", id="09-unknown-value"),
        pytest.param("PENDING", id="09-wrong-case"),
        pytest.param(" pending", id="09-leading-space"),
        pytest.param("", id="09-empty"),
        pytest.param(None, id="09-none"),
        pytest.param(5, id="09-int"),
        pytest.param(b"pending", id="09-bytes"),
    ],
)
def test_09_unknown_status_raises_contextual_error(raw: object) -> None:
    with pytest.raises(InvalidReminderFieldError) as exc_info:
        make_reminder(id=7, user_id=100, status=raw)

    assert exc_info.value.reminder_id == 7
    assert exc_info.value.user_id == 100
    assert exc_info.value.field == "status"
    assert exc_info.value.value == raw


def test_10_unknown_status_context_for_unsaved_reminder() -> None:
    with pytest.raises(InvalidReminderFieldError) as exc_info:
        make_reminder(id=None, status="archived")

    assert exc_info.value.reminder_id is None


def test_11_unknown_status_error_is_domain_error_not_value_error() -> None:
    # Пошкоджені дані системи — доменна помилка, а не порушення контракту виклику
    # і не помилка введення: обробники введення користувача не мають її ловити.
    assert issubclass(InvalidReminderFieldError, DomainError)
    assert not issubclass(InvalidReminderFieldError, ValueError)
    assert not issubclass(InvalidReminderFieldError, ReminderValidationError)


# --- Reminder: інваріанти консистентності ------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"text": ""}, "text", id="12-empty-text"),
        pytest.param({"text": "   \n\t"}, "text", id="12-whitespace-only-text"),
        pytest.param(
            {"local_datetime": datetime(2026, 11, 15, 9, 0, tzinfo=KYIV)},
            "local_datetime",
            id="12-aware-local-datetime",
        ),
        pytest.param(
            {"trigger_at_utc": datetime(2026, 11, 15, 7, 0)},
            "trigger_at_utc",
            id="12-naive-trigger-at-utc",
        ),
        pytest.param(
            {"trigger_at_utc": datetime(2026, 11, 15, 9, 0, tzinfo=KYIV)},
            "trigger_at_utc",
            id="12-trigger-at-utc-in-local-zoneinfo",
        ),
        pytest.param(
            {"trigger_at_utc": datetime(2026, 11, 15, 9, 0, tzinfo=timezone(timedelta(hours=2)))},
            "trigger_at_utc",
            id="12-trigger-at-utc-fixed-nonzero-offset",
        ),
        pytest.param(
            {"created_at": datetime(2026, 11, 14, 17, 35)},
            "created_at",
            id="12-naive-created-at",
        ),
        pytest.param(
            {"created_at": NOW_UTC.astimezone(KYIV)},
            "created_at",
            id="12-created-at-in-local-zoneinfo",
        ),
    ],
)
def test_12_inconsistent_reminder_raises_value_error(overrides: dict[str, Any], field: str) -> None:
    # Повідомлення має називати поле (окремим словом) — інакше кейс пройшов би
    # від будь-якого ValueError.
    with pytest.raises(ValueError, match=rf"\b{field}\b") as exc_info:
        make_reminder(**overrides)

    # Саме ValueError, не підклас: InvalidReminderFieldError свідомо не ValueError.
    assert exc_info.type is ValueError


# --- User --------------------------------------------------------------------------


def test_13_valid_user_keeps_fields() -> None:
    user = make_user()

    assert user.user_id == 100
    assert user.timezone == "Europe/Kyiv"
    assert user.created_at == NOW_UTC


def test_14_user_is_frozen() -> None:
    user = make_user()

    with pytest.raises(dataclasses.FrozenInstanceError):
        user.timezone = "Europe/London"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"timezone": ""}, "timezone", id="15-empty-timezone"),
        pytest.param({"timezone": "   "}, "timezone", id="15-whitespace-only-timezone"),
        pytest.param(
            {"created_at": datetime(2026, 11, 14, 17, 35)}, "created_at", id="15-naive-created-at"
        ),
        pytest.param(
            {"created_at": NOW_UTC.astimezone(KYIV)}, "created_at", id="15-created-at-not-utc"
        ),
    ],
)
def test_15_inconsistent_user_raises_value_error(overrides: dict[str, Any], field: str) -> None:
    # Повідомлення має називати поле (окремим словом) — інакше кейс пройшов би
    # від будь-якого ValueError.
    with pytest.raises(ValueError, match=rf"\b{field}\b") as exc_info:
        make_user(**overrides)

    # Саме ValueError, не підклас: InvalidReminderFieldError свідомо не ValueError.
    assert exc_info.type is ValueError
