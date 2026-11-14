"""Валідація та конвертація дати/часу нагадування.

Реалізовано: формат "YYYY-MM-DD HH:MM", валідація IANA-поясу (з урахуванням
регістру), весняна прогалина DST (відхилення) та осіннє подвоєння (fold=0).
Ще не реалізовано: формат "DD.MM.YYYY", нормалізація пробілів, доменні помилки
формату/календаря, перевірка минулого часу та наївного now_utc.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from domain.exceptions import (
    InvalidCalendarDateError,
    InvalidTimezoneError,
    NonExistentTimeError,
)

_INPUT_FORMAT = "%Y-%m-%d %H:%M"
_LOCAL_FORMAT = "%Y-%m-%dT%H:%M:%S"
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True, slots=True)
class ParsedDateTime:
    local_datetime: str
    trigger_at_utc: str
    is_ambiguous: bool


@cache
def _available_timezones() -> frozenset[str]:
    # available_timezones() сканує всю базу — рахуємо один раз на процес.
    return frozenset(available_timezones())


def get_timezone(tz_name: str) -> ZoneInfo:
    """Повертає ZoneInfo для IANA-поясу або кидає InvalidTimezoneError.

    Перевірка чутлива до регістру: на Windows (регістронезалежна ФС)
    ZoneInfo("europe/kyiv") може успішно завантажитися, тому звіряємося
    з канонічним списком імен.
    """
    if tz_name not in _available_timezones():
        raise InvalidTimezoneError(f"Невідомий часовий пояс: {tz_name!r}")
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezoneError(f"Невідомий часовий пояс: {tz_name!r}") from exc


def _localize(local_naive: datetime, tz: ZoneInfo) -> tuple[datetime, bool]:
    """Прив'язує наївний локальний час до поясу з урахуванням DST.

    Повертає (aware-час із fold=0, is_ambiguous).
    Неіснуючий час (весняна прогалина) → NonExistentTimeError.
    """
    first = local_naive.replace(tzinfo=tz, fold=0)
    second = local_naive.replace(tzinfo=tz, fold=1)

    # Час у прогалині не переживає кругову конвертацію через UTC:
    # 03:30 (Kyiv, весна) → 01:30Z → 04:30 локально.
    round_trip = first.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if round_trip != local_naive:
        raise NonExistentTimeError(
            f"Час {local_naive:%Y-%m-%d %H:%M} не існує в поясі {tz.key} "
            "через перехід на літній час. Вкажіть інший час."
        )

    is_ambiguous = first.utcoffset() != second.utcoffset()
    return first, is_ambiguous


def parse_reminder_datetime(raw: str, tz_name: str, now_utc: datetime) -> ParsedDateTime:
    tz = get_timezone(tz_name)
    # Наївний datetime навмисний: це намір користувача без зсуву.
    local_naive = datetime.strptime(raw, _INPUT_FORMAT)  # noqa: DTZ007
    try:
        local_aware, is_ambiguous = _localize(local_naive, tz)
        trigger_utc = local_aware.astimezone(UTC)
    except OverflowError as exc:
        # Напр. "0001-01-01 00:00" у UTC+2 → рік 0, поза діапазоном datetime.
        raise InvalidCalendarDateError(
            f"Дата {local_naive:%Y-%m-%d} поза підтримуваним діапазоном"
        ) from exc

    return ParsedDateTime(
        local_datetime=local_naive.strftime(_LOCAL_FORMAT),
        trigger_at_utc=trigger_utc.strftime(_UTC_FORMAT),
        is_ambiguous=is_ambiguous,
    )
