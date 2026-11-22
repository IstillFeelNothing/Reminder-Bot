"""Контракти між шарами (Dependency Inversion). Залежать лише від stdlib і domain.

Реалізації: storage/ (репозиторії, UnitOfWork поверх aiosqlite), bot/sender.py
(NotificationSender поверх aiogram), а в тестах — in-memory фейки.
Усі методи асинхронні: реалізації виконують I/O у циклі подій.
"""

from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from domain.models import Reminder, ReminderStatus, User


class UserRepositoryProtocol(Protocol):
    async def get(self, user_id: int) -> User | None:
        """Повертає користувача або None, якщо він ще не зареєстрований."""
        ...

    async def add(self, user: User) -> None:
        """Створює запис користувача (після підтвердження поясу в /start)."""
        ...

    async def update_timezone(self, user_id: int, timezone: str) -> None:
        """Змінює IANA-пояс користувача.

        Перерахунок trigger_at_utc активних нагадувань виконує сервіс у тому ж UnitOfWork.
        Користувача немає → нічого не змінюється.
        """
        ...


class ReminderRepositoryProtocol(Protocol):
    async def add(self, reminder: Reminder) -> Reminder:
        """Зберігає нове нагадування (id=None) і повертає його копію з присвоєним id."""
        ...

    async def get(self, reminder_id: int) -> Reminder | None:
        """Повертає нагадування за id або None.

        Права власника (user_id) перевіряє сервіс, а не репозиторій.
        Невідоме значення поля в БД → InvalidReminderFieldError.
        """
        ...

    async def list_pending_by_user(self, user_id: int) -> list[Reminder]:
        """Активні (pending) нагадування користувача, впорядковані за trigger_at_utc."""
        ...

    async def list_due(self, now_utc: datetime) -> list[Reminder]:
        """Нагадування зі status = pending і trigger_at_utc <= now_utc, за trigger_at_utc.

        now_utc — aware datetime; перетворення в рядок БД — відповідальність репозиторію.
        """
        ...

    async def update(self, reminder: Reminder) -> None:
        """Оновлює text, local_datetime і trigger_at_utc запису з reminder.id.

        Статус змінюється лише через set_status. Запису немає → нічого не змінюється.
        """
        ...

    async def set_status(self, reminder_id: int, status: ReminderStatus) -> None:
        """Змінює статус (планувальник: після відправки → sent, після 403 → failed).

        Запису немає (видалено під час відправки) → нічого не змінюється.
        """
        ...

    async def delete(self, reminder_id: int) -> bool:
        """Фізично видаляє запис. False — запису вже немає (напр. стара інлайн-кнопка)."""
        ...


class UnitOfWorkProtocol(Protocol):
    """Транзакція над одним з'єднанням.

    Використання: `async with uow: ...; await uow.commit()`.
    Вихід із контексту без commit (зокрема через виняток) — rollback.
    """

    @property
    def users(self) -> UserRepositoryProtocol: ...

    @property
    def reminders(self) -> ReminderRepositoryProtocol: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class NotificationSenderProtocol(Protocol):
    """Доставка в Telegram. Знає про форматування й ліміт 4096 символів.

    Помилки — лише доменні: RecipientUnavailableError (403),
    RateLimitExceededError (429), TemporaryDeliveryError (5xx, таймаути).
    """

    async def send_reminder(self, reminder: Reminder) -> None:
        """Надсилає одне нагадування в reminder.chat_id."""
        ...

    async def send_overdue(self, chat_id: int, reminders: Sequence[Reminder]) -> None:
        """Надсилає прострочені нагадування одним списком (за потреби — частинами)."""
        ...
