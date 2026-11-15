"""Валідація та конвертація дати/часу нагадування.

Реалізовано: формати "YYYY-MM-DD HH:MM" і "DD.MM.YYYY HH:MM", нормалізація
пробілів, валідація IANA-поясу (з урахуванням регістру), весняна прогалина DST
(відхилення) та осіннє подвоєння (fold=0).
Ще не реалізовано: доменні помилки формату/календаря, перевірка минулого часу
та наївного now_utc.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

from domain.exceptions import (
    InvalidCalendarDateError,
    InvalidTimezoneError,
    NonExistentTimeError,
)

# Порядок не важливий: роздільники дати ("-" проти ".") не перетинаються.
_INPUT_FORMATS = ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M")
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
    # Збої середовища (пошкоджений/видалений tzdata) навмисно не ловимо:
    # це інфраструктурна помилка, а не некоректне введення користувача.
    return ZoneInfo(tz_name)


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


def _normalize_whitespace(raw: str) -> str:
    """Обрізає краї та стискає будь-які пробільні послідовності до одного пробілу.

    str.split() без аргументів враховує і Unicode-пробіли (напр. NBSP,
    який клієнти Telegram інколи підставляють при копіюванні).
    """
    return " ".join(raw.split())


def _parse_local_naive(text: str) -> datetime:
    """Парсить нормалізований рядок у наївний локальний datetime.

    Якщо жоден формат не підійшов, прокидає ValueError від останньої спроби
    (перетворення на доменні помилки формату/календаря — окремий крок).
    """
    last_error: ValueError | None = None
    for fmt in _INPUT_FORMATS:
        try:
            # DTZ007 хибний: користувач вводить час без зсуву, пояс і DST
            # застосовує _localize().
            return datetime.strptime(text, fmt)  # noqa: DTZ007
        except ValueError as exc:
            last_error = exc
    assert last_error is not None  # _INPUT_FORMATS не порожній
    raise last_error


def parse_reminder_datetime(raw: str, tz_name: str, now_utc: datetime) -> ParsedDateTime:
    tz = get_timezone(tz_name)
    local_naive = _parse_local_naive(_normalize_whitespace(raw))
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
