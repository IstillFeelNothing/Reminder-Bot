"""Тести доменних винятків.

Контракт `InvalidReminderFieldError` (кидає репозиторій, коли збережений рядок містить
значення, яке не відображається в модель, напр. невідомий status):
- зберігає контекст: reminder_id, user_id, field, value (сире значення з БД);
- це DomainError, але не ValueError і не ReminderValidationError.
"""

from domain.exceptions import DomainError, InvalidReminderFieldError, ReminderValidationError


def test_01_invalid_reminder_field_error_keeps_context() -> None:
    error = InvalidReminderFieldError(reminder_id=7, user_id=100, field="status", value="archived")

    assert error.reminder_id == 7
    assert error.user_id == 100
    assert error.field == "status"
    assert error.value == "archived"


def test_02_invalid_reminder_field_error_message_names_field_and_value() -> None:
    error = InvalidReminderFieldError(reminder_id=7, user_id=100, field="status", value="archived")

    assert "status" in str(error)
    assert "archived" in str(error)


def test_03_invalid_reminder_field_error_hierarchy() -> None:
    # Пошкоджені дані системи — доменна помилка, а не порушення контракту виклику
    # і не помилка введення: обробники введення користувача не мають її ловити.
    assert issubclass(InvalidReminderFieldError, DomainError)
    assert not issubclass(InvalidReminderFieldError, ValueError)
    assert not issubclass(InvalidReminderFieldError, ReminderValidationError)
