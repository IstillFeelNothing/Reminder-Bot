"""Доменні винятки. Залежать лише від стандартної бібліотеки."""


class DomainError(Exception):
    """Базовий клас усіх доменних винятків."""


class ReminderValidationError(DomainError):
    """Некоректне введення користувача при створенні/редагуванні нагадування."""


class InvalidDateTimeFormatError(ReminderValidationError):
    """Рядок не відповідає жодному з підтримуваних форматів дати/часу."""


class InvalidCalendarDateError(ReminderValidationError):
    """Формат правильний, але такої календарної дати не існує (напр. 29.02 у невисокосний рік)."""


class InvalidTimezoneError(ReminderValidationError):
    """Невідомий IANA-пояс (перевірка чутлива до регістру)."""


class NonExistentTimeError(ReminderValidationError):
    """Локальний час потрапляє у весняну прогалину переведення годинника."""


class PastDateTimeError(ReminderValidationError):
    """Момент спрацювання не пізніший за поточний."""


class EmptyReminderTextError(ReminderValidationError):
    """Текст нагадування порожній або складається лише з пробільних символів."""


class ReminderTextTooLongError(ReminderValidationError):
    """Текст нагадування після обрізання країв довший за допустимий ліміт."""


class InvalidReminderFieldError(DomainError):
    """Некоректне значення поля нагадування (наприклад, невідомий статус або дата з БД)."""

    def __init__(self, reminder_id: int, user_id: int, field: str, value: object) -> None:
        self.reminder_id = reminder_id
        self.user_id = user_id
        self.field = field
        self.value = value
        super().__init__(
            f"Некоректне значення поля '{field}' ({value!r}) "
            f"для нагадування id={reminder_id} (user_id={user_id})."
        )
