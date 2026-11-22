"""Тести in-memory фейків протоколів (tests/fakes.py).

Фейки — виконувана специфікація контрактів domain/protocols.py для тестів сервісів.
Тести працюють лише через типи протоколів: mypy strict перевіряє, що фейки їх
реалізують, а сам контракт зручний у використанні.

Контракт ReminderRepositoryProtocol:
- add: приймає id=None, повертає копію з новим id; id не перевикористовуються
  (AUTOINCREMENT), навіть після видалення останнього запису; rollback UoW повертає
  і лічильник id (як sqlite_sequence);
- get: запис або None;
- list_pending_by_user: лише pending цього користувача, ORDER BY trigger_at_utc, id;
- list_due(now_utc, *, limit, after): pending з trigger_at_utc <= now_utc, ORDER BY
  trigger_at_utc, id, не більше limit; after — останній елемент попередньої порції,
  повертаються лише (trigger_at_utc, id) > after; limit < 1 → ValueError;
- update: змінює text, local_datetime, trigger_at_utc; status не змінює; повертає
  запис після оновлення або None, якщо запису немає;
- set_status(..., expected_trigger_at_utc): змінює статус лише якщо запис pending і
  trigger_at_utc збігається; інакше False і запис не змінюється;
- delete: True — видалено, False — запису не було.

Контракт UserRepositoryProtocol: get / add; update_timezone для неіснуючого — без змін.

Контракт UnitOfWorkProtocol: вихід із `async with` без винятку — commit;
з винятком — rollback і виняток поширюється далі.
"""

import asyncio
import dataclasses
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from domain.models import Reminder, ReminderStatus, User
from domain.protocols import (
    DueCursor,
    NotificationSenderProtocol,
    ReminderRepositoryProtocol,
    UnitOfWorkProtocol,
    UserRepositoryProtocol,
)
from tests.fakes import (
    FakeNotificationSender,
    FakeUnitOfWork,
    InMemoryReminderRepository,
    InMemoryUserRepository,
)

NOW_UTC = datetime(2026, 11, 14, 17, 35, tzinfo=UTC)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def make_reminder(**overrides: Any) -> Reminder:
    fields: dict[str, Any] = {
        "id": None,
        "user_id": 100,
        "chat_id": 200,
        "text": "Купити хліб",
        "local_datetime": datetime(2026, 11, 14, 19, 0),
        "trigger_at_utc": datetime(2026, 11, 14, 17, 0, tzinfo=UTC),
        "status": ReminderStatus.PENDING,
        "created_at": NOW_UTC - timedelta(days=1),
    }
    fields.update(overrides)
    return Reminder(**fields)


def at(minutes: int) -> datetime:
    """Момент спрацювання відносно NOW_UTC (від'ємне — уже настав)."""
    return NOW_UTC + timedelta(minutes=minutes)


def new_reminders() -> ReminderRepositoryProtocol:
    return InMemoryReminderRepository()


def new_users() -> UserRepositoryProtocol:
    return InMemoryUserRepository()


async def add_all(repo: ReminderRepositoryProtocol, *reminders: Reminder) -> list[Reminder]:
    return [await repo.add(r) for r in reminders]


def ids(reminders: list[Reminder]) -> list[int | None]:
    return [r.id for r in reminders]


# --- ReminderRepositoryProtocol: add / get / delete -----------------------------------


def test_01_add_assigns_increasing_ids() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        draft = make_reminder()

        first = await repo.add(draft)
        second = await repo.add(draft)

        assert draft.id is None
        assert first.id is not None and second.id is not None
        assert second.id > first.id
        assert first == dataclasses.replace(draft, id=first.id)
        assert await repo.get(first.id) == first

    run(scenario())


def test_02_ids_are_not_reused_after_delete() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        last = await repo.add(make_reminder())
        assert last.id is not None

        assert await repo.delete(last.id) is True
        again = await repo.add(make_reminder())

        assert again.id is not None and again.id > last.id

    run(scenario())


def test_03_add_rejects_saved_reminder() -> None:
    with pytest.raises(ValueError):
        run(new_reminders().add(make_reminder(id=1)))


def test_04_get_missing_returns_none() -> None:
    assert run(new_reminders().get(404)) is None


def test_05_delete_reports_whether_record_existed() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        saved = await repo.add(make_reminder())
        assert saved.id is not None

        assert await repo.delete(saved.id) is True
        assert await repo.get(saved.id) is None
        assert await repo.delete(saved.id) is False

    run(scenario())


# --- list_pending_by_user ----------------------------------------------------------


def test_06_list_pending_by_user_filters_and_orders() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        late, early, tie, _other_user, _sent = await add_all(
            repo,
            make_reminder(trigger_at_utc=at(60)),
            make_reminder(trigger_at_utc=at(10)),
            make_reminder(trigger_at_utc=at(10)),
            make_reminder(user_id=101, trigger_at_utc=at(5)),
            make_reminder(trigger_at_utc=at(1), status=ReminderStatus.SENT),
        )

        result = await repo.list_pending_by_user(100)

        assert ids(result) == [early.id, tie.id, late.id]

    run(scenario())


# --- list_due ----------------------------------------------------------------------


def test_07_list_due_selects_pending_up_to_now_inclusive() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        due_now, overdue, _future, _failed = await add_all(
            repo,
            make_reminder(trigger_at_utc=at(0)),
            make_reminder(trigger_at_utc=at(-30)),
            make_reminder(trigger_at_utc=at(1)),
            make_reminder(trigger_at_utc=at(-60), status=ReminderStatus.FAILED),
        )

        result = await repo.list_due(NOW_UTC, limit=10)

        assert ids(result) == [overdue.id, due_now.id]

    run(scenario())


def test_08_list_due_pages_by_cursor_without_gaps_or_duplicates() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        # Однаковий trigger_at_utc: порядок і межі порцій визначає id.
        saved = await add_all(
            repo,
            make_reminder(trigger_at_utc=at(-10)),
            make_reminder(trigger_at_utc=at(-20)),
            make_reminder(trigger_at_utc=at(-10)),
            make_reminder(trigger_at_utc=at(-10)),
            make_reminder(trigger_at_utc=at(-5)),
        )
        expected = sorted(saved, key=lambda r: (r.trigger_at_utc, r.id or 0))

        pages: list[list[Reminder]] = []
        after: DueCursor | None = None
        while page := await repo.list_due(NOW_UTC, limit=2, after=after):
            pages.append(page)
            last = page[-1]
            assert last.id is not None
            after = DueCursor(trigger_at_utc=last.trigger_at_utc, reminder_id=last.id)

        assert [len(p) for p in pages] == [2, 2, 1]
        assert [r for p in pages for r in p] == expected

    run(scenario())


def test_09_list_due_cursor_moves_past_records_left_pending() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        stuck, waiting = await add_all(
            repo,
            make_reminder(trigger_at_utc=at(-20)),
            make_reminder(trigger_at_utc=at(-10)),
        )
        assert stuck.id is not None

        first = await repo.list_due(NOW_UTC, limit=1)
        # Тимчасова помилка доставки: stuck лишився pending.
        cursor = DueCursor(trigger_at_utc=stuck.trigger_at_utc, reminder_id=stuck.id)
        second = await repo.list_due(NOW_UTC, limit=1, after=cursor)

        assert ids(first) == [stuck.id]
        assert ids(second) == [waiting.id]

    run(scenario())


@pytest.mark.parametrize(
    "limit",
    [pytest.param(0, id="10-zero"), pytest.param(-1, id="10-negative")],
)
def test_10_list_due_rejects_non_positive_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        run(new_reminders().list_due(NOW_UTC, limit=limit))


# --- update ------------------------------------------------------------------------


def test_11_update_returns_and_stores_new_text_and_time() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        saved = await repo.add(make_reminder())
        assert saved.id is not None
        edited = dataclasses.replace(
            saved,
            text="Купити молоко",
            local_datetime=datetime(2026, 11, 15, 9, 0),
            trigger_at_utc=datetime(2026, 11, 15, 7, 0, tzinfo=UTC),
        )

        result = await repo.update(edited)

        assert result == edited
        assert await repo.get(saved.id) == edited

    run(scenario())


def test_12_update_does_not_change_status() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        saved = await repo.add(make_reminder())
        assert saved.id is not None

        result = await repo.update(
            dataclasses.replace(saved, text="Нове", status=ReminderStatus.SENT)
        )

        assert result is not None
        assert result.status is ReminderStatus.PENDING
        stored = await repo.get(saved.id)
        assert stored is not None and stored.status is ReminderStatus.PENDING

    run(scenario())


def test_13_update_missing_returns_none() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        saved = await repo.add(make_reminder())
        assert saved.id is not None
        await repo.delete(saved.id)

        assert await repo.update(dataclasses.replace(saved, text="Нове")) is None
        assert await repo.get(saved.id) is None

    run(scenario())


# --- set_status --------------------------------------------------------------------


def test_14_set_status_when_trigger_matches() -> None:
    async def scenario() -> None:
        repo = new_reminders()
        saved = await repo.add(make_reminder())
        assert saved.id is not None

        changed = await repo.set_status(
            saved.id, ReminderStatus.SENT, expected_trigger_at_utc=saved.trigger_at_utc
        )

        assert changed is True
        assert await repo.get(saved.id) == dataclasses.replace(saved, status=ReminderStatus.SENT)

    run(scenario())


@pytest.mark.parametrize(
    ("stored", "expected_trigger"),
    [
        pytest.param({"trigger_at_utc": at(30)}, at(-1), id="15-rescheduled-during-send"),
        pytest.param({"status": ReminderStatus.SENT}, at(-1), id="15-already-sent"),
        pytest.param({"status": ReminderStatus.FAILED}, at(-1), id="15-already-failed"),
    ],
)
def test_15_set_status_rejects_changed_record(
    stored: dict[str, Any], expected_trigger: datetime
) -> None:
    async def scenario() -> None:
        repo = new_reminders()
        saved = await repo.add(make_reminder(**{"trigger_at_utc": at(-1), **stored}))
        assert saved.id is not None

        changed = await repo.set_status(
            saved.id, ReminderStatus.FAILED, expected_trigger_at_utc=expected_trigger
        )

        assert changed is False
        assert await repo.get(saved.id) == saved

    run(scenario())


def test_16_set_status_missing_returns_false() -> None:
    changed = run(
        new_reminders().set_status(404, ReminderStatus.SENT, expected_trigger_at_utc=at(0))
    )

    assert changed is False


# --- UserRepositoryProtocol ----------------------------------------------------------


def test_17_user_add_get_and_update_timezone() -> None:
    async def scenario() -> None:
        repo = new_users()
        user = User(user_id=100, timezone="Europe/Kyiv", created_at=NOW_UTC)

        assert await repo.get(100) is None
        await repo.add(user)
        await repo.update_timezone(100, "America/New_York")

        assert await repo.get(100) == dataclasses.replace(user, timezone="America/New_York")

    run(scenario())


def test_18_update_timezone_of_missing_user_changes_nothing() -> None:
    async def scenario() -> None:
        repo = new_users()

        await repo.update_timezone(404, "Europe/Kyiv")

        assert await repo.get(404) is None

    run(scenario())


# --- UnitOfWorkProtocol --------------------------------------------------------------


def test_19_uow_commits_on_clean_exit() -> None:
    async def scenario() -> None:
        uow: UnitOfWorkProtocol = FakeUnitOfWork()
        user = User(user_id=100, timezone="Europe/Kyiv", created_at=NOW_UTC)

        async with uow:
            await uow.users.add(user)
            saved = await uow.reminders.add(make_reminder())

        assert saved.id is not None
        async with uow:
            assert await uow.users.get(100) == user
            assert await uow.reminders.get(saved.id) == saved
        assert isinstance(uow, FakeUnitOfWork)
        assert (uow.commits, uow.rollbacks) == (2, 0)

    run(scenario())


def test_20_uow_rolls_back_and_reraises_on_error() -> None:
    class Boom(Exception):
        pass

    async def scenario() -> None:
        uow: UnitOfWorkProtocol = FakeUnitOfWork()
        user = User(user_id=100, timezone="Europe/Kyiv", created_at=NOW_UTC)
        async with uow:
            await uow.users.add(user)
            kept = await uow.reminders.add(make_reminder())
        assert kept.id is not None

        with pytest.raises(Boom):
            async with uow:
                await uow.users.update_timezone(100, "America/New_York")
                await uow.reminders.update(dataclasses.replace(kept, text="Нове"))
                await uow.reminders.add(make_reminder())
                raise Boom

        async with uow:
            assert await uow.users.get(100) == user
            assert await uow.reminders.get(kept.id) == kept
            assert ids(await uow.reminders.list_pending_by_user(100)) == [kept.id]
            # Лічильник id відкочено разом із даними (як sqlite_sequence).
            assert (await uow.reminders.add(make_reminder())).id == kept.id + 1
        assert isinstance(uow, FakeUnitOfWork)
        assert (uow.commits, uow.rollbacks) == (2, 1)

    run(scenario())


# --- NotificationSenderProtocol ------------------------------------------------------


def test_21_sender_records_deliveries() -> None:
    async def scenario() -> None:
        fake = FakeNotificationSender()
        sender: NotificationSenderProtocol = fake
        one = make_reminder(id=1)
        two = make_reminder(id=2)

        await sender.send_reminder(one)
        await sender.send_overdue(200, [one, two])

        assert fake.sent == [one]
        assert fake.overdue == [(200, [one, two])]

    run(scenario())
