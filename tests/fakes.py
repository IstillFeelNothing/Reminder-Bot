"""In-memory фейки протоколів domain/protocols.py для тестів сервісів.

Поведінка відтворює контракти протоколів (див. tests/test_fakes.py), зокрема
AUTOINCREMENT, порційну вибірку за курсором, умовний set_status і
семантику UnitOfWork: вихід без винятку — commit, з винятком — rollback.
"""

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Self

from domain.models import DueCursor, Reminder, ReminderStatus, User


def _order_key(reminder: Reminder) -> tuple[datetime, int]:
    assert reminder.id is not None
    return (reminder.trigger_at_utc, reminder.id)


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.rows: dict[int, User] = {}

    async def get(self, user_id: int) -> User | None:
        return self.rows.get(user_id)

    async def add(self, user: User) -> None:
        if user.user_id in self.rows:
            raise ValueError(f"Користувач {user.user_id} уже існує")
        self.rows[user.user_id] = user

    async def update_timezone(self, user_id: int, timezone: str) -> None:
        user = self.rows.get(user_id)
        if user is not None:
            self.rows[user_id] = dataclasses.replace(user, timezone=timezone)


class InMemoryReminderRepository:
    def __init__(self) -> None:
        self.rows: dict[int, Reminder] = {}
        # Як sqlite_sequence: id не перевикористовуються після видалення.
        self.last_id = 0

    async def add(self, reminder: Reminder) -> Reminder:
        if reminder.id is not None:
            raise ValueError("Нове нагадування має мати id=None")
        self.last_id += 1
        saved = dataclasses.replace(reminder, id=self.last_id)
        self.rows[self.last_id] = saved
        return saved

    async def get(self, reminder_id: int) -> Reminder | None:
        return self.rows.get(reminder_id)

    async def list_pending_by_user(self, user_id: int) -> list[Reminder]:
        return sorted(
            (
                r
                for r in self.rows.values()
                if r.user_id == user_id and r.status is ReminderStatus.PENDING
            ),
            key=_order_key,
        )

    async def list_due(
        self, now_utc: datetime, *, limit: int, after: DueCursor | None = None
    ) -> list[Reminder]:
        if limit < 1:
            raise ValueError(f"limit має бути >= 1, отримано {limit}")
        due = (
            r
            for r in self.rows.values()
            if r.status is ReminderStatus.PENDING and r.trigger_at_utc <= now_utc
        )
        if after is not None:
            cursor = (after.trigger_at_utc, after.reminder_id)
            due = (r for r in due if _order_key(r) > cursor)
        return sorted(due, key=_order_key)[:limit]

    async def update(self, reminder: Reminder) -> Reminder | None:
        if reminder.id is None:
            raise ValueError("Не можна оновити нагадування без id")
        stored = self.rows.get(reminder.id)
        if stored is None:
            return None
        updated = dataclasses.replace(
            stored,
            text=reminder.text,
            local_datetime=reminder.local_datetime,
            trigger_at_utc=reminder.trigger_at_utc,
        )
        self.rows[reminder.id] = updated
        return updated

    async def set_status(
        self, reminder_id: int, status: ReminderStatus, *, expected_trigger_at_utc: datetime
    ) -> bool:
        stored = self.rows.get(reminder_id)
        if (
            stored is None
            or stored.status is not ReminderStatus.PENDING
            or stored.trigger_at_utc != expected_trigger_at_utc
        ):
            return False
        self.rows[reminder_id] = dataclasses.replace(stored, status=status)
        return True

    async def delete(self, reminder_id: int) -> bool:
        return self.rows.pop(reminder_id, None) is not None


class FakeUnitOfWork:
    """Транзакція через знімок стану: моделі незмінні, тож достатньо копій словників."""

    def __init__(
        self,
        users: InMemoryUserRepository | None = None,
        reminders: InMemoryReminderRepository | None = None,
    ) -> None:
        self._users = users if users is not None else InMemoryUserRepository()
        self._reminders = reminders if reminders is not None else InMemoryReminderRepository()
        self._snapshot: tuple[dict[int, User], dict[int, Reminder], int] | None = None
        self.commits = 0
        self.rollbacks = 0

    @property
    def users(self) -> InMemoryUserRepository:
        return self._users

    @property
    def reminders(self) -> InMemoryReminderRepository:
        return self._reminders

    async def __aenter__(self) -> Self:
        if self._snapshot is not None:
            raise RuntimeError("Вкладені транзакції не підтримуються")
        self._snapshot = (
            dict(self._users.rows),
            dict(self._reminders.rows),
            self._reminders.last_id,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._snapshot is not None
        if exc_type is None:
            self.commits += 1
        else:
            self._users.rows, self._reminders.rows, self._reminders.last_id = self._snapshot
            self.rollbacks += 1
        self._snapshot = None


class FakeNotificationSender:
    def __init__(self) -> None:
        self.sent: list[Reminder] = []
        self.overdue: list[tuple[int, list[Reminder]]] = []

    async def send_reminder(self, reminder: Reminder) -> None:
        self.sent.append(reminder)

    async def send_overdue(self, chat_id: int, reminders: Sequence[Reminder]) -> None:
        self.overdue.append((chat_id, list(reminders)))
