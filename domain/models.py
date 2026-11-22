"""Доменні моделі нагадування й користувача. Залежать лише від стандартної бібліотеки."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class ReminderStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


def _is_utc(dt: datetime) -> bool:
    return dt.utcoffset() == timedelta(0)


@dataclass(frozen=True)
class Reminder:
    id: int | None
    user_id: int
    chat_id: int
    text: str
    local_datetime: datetime
    trigger_at_utc: datetime
    status: ReminderStatus
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Поле text не може бути порожнім.")

        if self.local_datetime.utcoffset() is not None:
            raise ValueError("Поле local_datetime має бути naive (без часового поясу).")

        if not _is_utc(self.trigger_at_utc):
            raise ValueError("Поле trigger_at_utc має бути aware у UTC.")

        if not _is_utc(self.created_at):
            raise ValueError("Поле created_at має бути aware у UTC.")


@dataclass(frozen=True)
class User:
    user_id: int
    timezone: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.timezone.strip():
            raise ValueError("Поле timezone не може бути порожнім.")

        if not _is_utc(self.created_at):
            raise ValueError("Поле created_at має бути aware у UTC.")
