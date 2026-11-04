from datetime import UTC, datetime

import pytest

from smart_intent.errors import DomainError
from smart_intent.models import TimeRange
from smart_intent.time_ranges import normalize_time

NOW = datetime(2026, 11, 4, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("expression", "start", "end"),
    [
        ("昨天", "2026-11-02T16:00:00+00:00", "2026-11-03T16:00:00+00:00"),
        ("今天", "2026-11-03T16:00:00+00:00", "2026-11-04T01:00:00+00:00"),
        ("本周", "2026-11-01T16:00:00+00:00", "2026-11-04T01:00:00+00:00"),
        ("上周", "2026-10-25T16:00:00+00:00", "2026-11-01T16:00:00+00:00"),
        ("上月", "2026-09-30T16:00:00+00:00", "2026-10-31T16:00:00+00:00"),
        ("2024年2月", "2024-01-31T16:00:00+00:00", "2024-02-29T16:00:00+00:00"),
        ("2026-12-31", "2026-12-30T16:00:00+00:00", "2026-12-31T16:00:00+00:00"),
    ],
)
def test_ranges(expression, start, end):
    result = normalize_time(TimeRange(expression=expression), "Asia/Shanghai", NOW)
    assert (result["start"], result["end"]) == (start, end)


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ({"expression": "12月"}, "year_required"),
        ({"expression": "某一天"}, "time_expression_unsupported"),
        ({"expression": "2026-02-30"}, "date_invalid"),
        ({"expression": "2026年13月"}, "date_invalid"),
        ({"start": "2026-11-01T00:00:00", "end": "2026-11-02T00:00:00Z"}, "timezone_required"),
        ({"start": "2026-11-03T00:00:00Z", "end": "2026-11-02T00:00:00Z"}, "range_invalid"),
        ({"expression": "今天", "timezone": "Invalid/Zone"}, "timezone_invalid"),
    ],
)
def test_invalid_time(spec, code):
    with pytest.raises(DomainError) as error:
        normalize_time(TimeRange.model_validate(spec), "Asia/Shanghai", NOW)
    assert error.value.code == code


def test_dst_day_uses_calendar_not_24_hours():
    result = normalize_time(TimeRange(expression="2026-11-01"), "America/New_York", NOW)
    hours = (
        datetime.fromisoformat(result["end"]) - datetime.fromisoformat(result["start"])
    ).total_seconds() / 3600
    assert hours == 25


def test_no_time_does_not_default_to_today():
    assert normalize_time(None, "Asia/Shanghai", NOW) is None
