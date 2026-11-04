import calendar
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import DomainError
from .models import TimeRange


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainError(
            "timezone_required", "时间必须包含时区偏移。", status="needs_clarification"
        )
    return value.astimezone(UTC)


def local_midnight(day: date, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, time())
    choices = []
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == naive:
            choices.append(local)
    if not choices or len({c.utcoffset() for c in choices}) != 1:
        raise DomainError(
            "ambiguous_local_time",
            "本地日期边界不能唯一定位，请指定偏移。",
            status="needs_clarification",
        )
    return choices[0]


def normalize_time(spec: TimeRange | None, timezone: str, now: datetime) -> dict | None:
    if spec is None:
        return None
    try:
        zone = ZoneInfo(spec.timezone or timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DomainError("timezone_invalid", "无法识别时区。", status="rejected") from exc
    if spec.start is not None or spec.end is not None:
        if spec.start is None or spec.end is None:
            raise DomainError(
                "range_required", "请同时提供起止时间。", status="needs_clarification"
            )
        if spec.reference_at is not None:
            raise DomainError("time_conflict", "绝对范围不能同时指定基准时间。", status="rejected")
        start, end = aware(spec.start), aware(spec.end)
        # expression is retained as provenance on normalized absolute ranges, never re-evaluated.
        expression = spec.expression
        reference = None
    else:
        reference = aware(spec.reference_at or now)
        local = reference.astimezone(zone)
        today = local.date()
        expression = spec.expression
        if expression in ("今天", "昨天"):
            day = today - timedelta(days=expression == "昨天")
            start = local_midnight(day, zone)
            end = local_midnight(day + timedelta(days=1), zone)
            if expression == "今天":
                end = local
        elif expression in ("本周", "上周"):
            monday = today - timedelta(days=today.weekday())
            if expression == "上周":
                start = local_midnight(monday - timedelta(days=7), zone)
                end = local_midnight(monday, zone)
            else:
                start, end = local_midnight(monday, zone), local
        elif expression in ("本月", "上月"):
            first = today.replace(day=1)
            if expression == "上月":
                start = local_midnight((first - timedelta(days=1)).replace(day=1), zone)
                end = local_midnight(first, zone)
            else:
                start, end = local_midnight(first, zone), local
        elif expression and re.fullmatch(r"\d{4}-\d{2}-\d{2}", expression):
            try:
                day = date.fromisoformat(expression)
                start = local_midnight(day, zone)
                end = local_midnight(day + timedelta(days=1), zone)
            except ValueError as exc:
                raise DomainError("date_invalid", "日期无效。", status="rejected") from exc
        elif expression and re.fullmatch(r"\d{4}年\d{1,2}月", expression):
            year, month = map(int, re.findall(r"\d+", expression))
            try:
                first = date(year, month, 1)
                days = calendar.monthrange(year, month)[1]
                start = local_midnight(first, zone)
                end = local_midnight(first + timedelta(days=days), zone)
            except (ValueError, OverflowError) as exc:
                raise DomainError("date_invalid", "月份无效。", status="rejected") from exc
        else:
            code = (
                "year_required"
                if expression and re.fullmatch(r"\d{1,2}月", expression)
                else "time_expression_unsupported"
            )
            raise DomainError(
                code,
                "请提供包含年份的日期或明确起止时间。",
                status="needs_clarification",
                fields={"time": "需要明确时间范围"},
            )
        start, end = aware(start), aware(end)
    if start > end:
        raise DomainError("range_invalid", "结束时间不能早于开始时间。", status="rejected")
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": zone.key,
        "expression": expression,
        "reference_at": reference.isoformat() if reference else None,
        "interval": "[start,end)",
    }
