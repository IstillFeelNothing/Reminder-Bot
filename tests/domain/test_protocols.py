"""Тести контрактів domain/protocols.py.

Контракт модуля:
- `UserRepositoryProtocol`: async `get`, `add`, `update_timezone`;
- `ReminderRepositoryProtocol`: async `add`, `get`, `list_pending_by_user`, `list_due`,
  `update`, `set_status`, `delete` — усі методи асинхронні;
- `UnitOfWorkProtocol`: async context manager з read-only властивостями `users` і
  `reminders`, async `commit` / `rollback` (rollback при виході без commit
  перевіряють тести реалізацій UnitOfWork);
- `NotificationSenderProtocol`: async `send_reminder`, `send_overdue`;
- усі протоколи — `typing.Protocol`;
- domain/ залежить лише від стандартної бібліотеки та самого domain
  (жодних aiogram / aiosqlite).

Сумісність реалізацій із сигнатурами перевіряє mypy strict на фейках і репозиторіях.
"""

import ast
import inspect
import sys
from pathlib import Path
from typing import Protocol, get_type_hints

import pytest

from domain.protocols import (
    NotificationSenderProtocol,
    ReminderRepositoryProtocol,
    UnitOfWorkProtocol,
    UserRepositoryProtocol,
)

DOMAIN_DIR = Path(__file__).resolve().parents[2] / "domain"


@pytest.mark.parametrize(
    "protocol",
    [
        pytest.param(UserRepositoryProtocol, id="01-user-repository"),
        pytest.param(ReminderRepositoryProtocol, id="02-reminder-repository"),
        pytest.param(UnitOfWorkProtocol, id="03-unit-of-work"),
        pytest.param(NotificationSenderProtocol, id="04-notification-sender"),
    ],
)
def test_contract_is_typing_protocol(protocol: type) -> None:
    assert Protocol in protocol.__mro__


@pytest.mark.parametrize(
    ("protocol", "method"),
    [
        pytest.param(UserRepositoryProtocol, "get", id="05-user-get"),
        pytest.param(UserRepositoryProtocol, "add", id="06-user-add"),
        pytest.param(UserRepositoryProtocol, "update_timezone", id="07-user-update-timezone"),
        pytest.param(ReminderRepositoryProtocol, "add", id="08-reminder-add"),
        pytest.param(ReminderRepositoryProtocol, "get", id="09-reminder-get"),
        pytest.param(
            ReminderRepositoryProtocol, "list_pending_by_user", id="10-reminder-list-pending"
        ),
        pytest.param(ReminderRepositoryProtocol, "list_due", id="11-reminder-list-due"),
        pytest.param(ReminderRepositoryProtocol, "update", id="12-reminder-update"),
        pytest.param(ReminderRepositoryProtocol, "set_status", id="13-reminder-set-status"),
        pytest.param(ReminderRepositoryProtocol, "delete", id="14-reminder-delete"),
        pytest.param(UnitOfWorkProtocol, "__aenter__", id="15-uow-aenter"),
        pytest.param(UnitOfWorkProtocol, "__aexit__", id="16-uow-aexit"),
        pytest.param(UnitOfWorkProtocol, "commit", id="17-uow-commit"),
        pytest.param(UnitOfWorkProtocol, "rollback", id="18-uow-rollback"),
        pytest.param(NotificationSenderProtocol, "send_reminder", id="19-sender-send-reminder"),
        pytest.param(NotificationSenderProtocol, "send_overdue", id="20-sender-send-overdue"),
    ],
)
def test_protocol_method_is_async(protocol: type, method: str) -> None:
    assert inspect.iscoroutinefunction(getattr(protocol, method))


@pytest.mark.parametrize(
    "protocol",
    [
        pytest.param(UserRepositoryProtocol, id="21-user-repository"),
        pytest.param(ReminderRepositoryProtocol, id="22-reminder-repository"),
        pytest.param(NotificationSenderProtocol, id="23-notification-sender"),
    ],
)
def test_all_public_methods_are_async(protocol: type) -> None:
    # Новий метод не може випадково бути синхронним: блокуюче I/O у циклі подій.
    methods = {
        name: member
        for name, member in vars(protocol).items()
        if not name.startswith("_") and callable(member)
    }

    assert methods
    sync = [name for name, member in methods.items() if not inspect.iscoroutinefunction(member)]
    assert sync == []


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        pytest.param("users", UserRepositoryProtocol, id="24-uow-users"),
        pytest.param("reminders", ReminderRepositoryProtocol, id="25-uow-reminders"),
    ],
)
def test_unit_of_work_exposes_read_only_repository(attribute: str, expected: type) -> None:
    member = inspect.getattr_static(UnitOfWorkProtocol, attribute)

    assert isinstance(member, property)
    assert member.fset is None
    assert member.fget is not None
    assert get_type_hints(member.fget)["return"] is expected


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize(
    "path",
    [pytest.param(p, id=f"26-{p.name}") for p in sorted(DOMAIN_DIR.glob("*.py"))],
)
def test_domain_depends_only_on_stdlib(path: Path) -> None:
    foreign = _imported_top_level_modules(path) - set(sys.stdlib_module_names) - {"domain"}

    assert foreign == set()
