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
