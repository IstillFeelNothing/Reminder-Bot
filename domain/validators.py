"""Валідація та конвертація дати/часу нагадування.

Реалізовано: формати "YYYY-MM-DD HH:MM" і "DD.MM.YYYY HH:MM" (день, місяць і
година можуть бути без ведучого нуля: "5.1.2027 9:00"), нормалізація пробілів,
розрізнення помилок формату та календаря, валідація IANA-поясу (з урахуванням
регістру), весняна прогалина DST (відхилення) та осіннє подвоєння (fold=0),
відхилення моменту, не пізнішого за now_utc, і перевірка, що now_utc aware.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

from domain.exceptions import (
    InvalidCalendarDateError,
    InvalidDateTimeFormatError,
    InvalidTimezoneError,
    NonExistentTimeError,
    PastDateTimeError,
)

# Година й хвилина перевіряються вже на рівні форми: "24:00"/"25:00" — помилка
# формату, а не календаря. День і місяць лише за формою (1–2 цифри): їх
# існування (31.02, 29.02 у невисокосний рік) перевіряє конструктор datetime.
_TIME = r"(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)"
# Порядок не важливий: роздільники дати ("-" проти ".") не перетинаються.
_INPUT_PATTERNS = (
    re.compile(rf"(?P<year>\d{{4}})-(?P<month>\d{{1,2}})-(?P<day>\d{{1,2}}) {_TIME}", re.ASCII),
    re.compile(rf"(?P<day>\d{{1,2}})\.(?P<month>\d{{1,2}})\.(?P<year>\d{{4}}) {_TIME}", re.ASCII),
)


@dataclass(frozen=True, slots=True)
class ParsedDateTime:
    # Наївний локальний час — намір користувача без прив'язки до поясу.
    local_datetime: datetime
    # Aware-момент у UTC. Перетворення в рядок БД — відповідальність репозиторію.
    trigger_at_utc: datetime
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

    Рядок не відповідає жодній формі → InvalidDateTimeFormatError.
    Форма правильна, але дати не існує (29.02.2027, 31.02) → InvalidCalendarDateError.
    """
    for pattern in _INPUT_PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        try:
            # DTZ001: наївний локальний час, пояс застосовує _localize().
            return datetime(  # noqa: DTZ001
                year=int(match["year"]),
                month=int(match["month"]),
                day=int(match["day"]),
                hour=int(match["hour"]),
                minute=int(match["minute"]),
            )
        except ValueError as exc:
            raise InvalidCalendarDateError(f"Дати {text!r} не існує в календарі") from exc
    raise InvalidDateTimeFormatError(
        f"Не вдалося розпізнати дату/час {text!r}. "
        "Очікується формат РРРР-ММ-ДД ГГ:ХХ або ДД.ММ.РРРР ГГ:ХХ."
    )


def parse_reminder_datetime(raw: str, tz_name: str, now_utc: datetime) -> ParsedDateTime:
    # Наївний now_utc — помилка викликаючого коду, а не введення користувача,
    # тому перевіряємо першою і кидаємо ValueError, а не доменний виняток.
    # Приймаємо будь-який aware-час: порівняння aware-значень іде за фізичним
    # моментом, тож пояс, у якому представлено now_utc, не впливає на результат.
    if now_utc.utcoffset() is None:
        raise ValueError("now_utc має бути aware datetime")

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

    # "<=": нагадування на поточну хвилину вже не встигне спрацювати вчасно.
    if trigger_utc <= now_utc:
        raise PastDateTimeError(
            f"Час {local_naive:%Y-%m-%d %H:%M} ({tz.key}) уже минув. Вкажіть майбутній час."
        )

    return ParsedDateTime(
        local_datetime=local_naive,
        trigger_at_utc=trigger_utc,
        is_ambiguous=is_ambiguous,
    )
