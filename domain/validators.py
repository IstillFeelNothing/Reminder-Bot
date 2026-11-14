"""Валідація та конвертація дати/часу нагадування.

Мінімальна реалізація (TDD): підтримує лише формат "YYYY-MM-DD HH:MM"
без перевірок DST, минулого часу та регістру поясу.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_INPUT_FORMAT = "%Y-%m-%d %H:%M"
_LOCAL_FORMAT = "%Y-%m-%dT%H:%M:%S"
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True, slots=True)
class ParsedDateTime:
    local_datetime: str
    trigger_at_utc: str
    is_ambiguous: bool


def parse_reminder_datetime(raw: str, tz_name: str, now_utc: datetime) -> ParsedDateTime:
    # Наївний datetime навмисний: це намір користувача без зсуву.
    local_naive = datetime.strptime(raw, _INPUT_FORMAT)  # noqa: DTZ007
    local_aware = local_naive.replace(tzinfo=ZoneInfo(tz_name))
    trigger_utc = local_aware.astimezone(UTC)

    return ParsedDateTime(
        local_datetime=local_naive.strftime(_LOCAL_FORMAT),
        trigger_at_utc=trigger_utc.strftime(_UTC_FORMAT),
        is_ambiguous=False,
    )
