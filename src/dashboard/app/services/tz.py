"""时区工具 — 统一 UTC→本地业务时区转换与日界判定

默认日记日界规则仍为 Asia/Hong_Kong 04:00 ~ 次日 04:00。
时区通过 Actanara settings / TARGET_TIMEZONE 解析，旧函数名保留兼容。
"""
from datetime import date, datetime, timedelta
from typing import Tuple

from zoneinfo import ZoneInfo

from data_foundation.time import business_date_for, business_window, parse_timestamp, resolve_timezone


def utc_ts_to_hkt(timestamp: str, tz: ZoneInfo | None = None) -> Tuple[date, int]:
    """
    将 UTC timestamp 字符串转换为 (HKT日期, HKT小时)。
    使用日界规则：HKT 04:00 为分界。

    Args:
        timestamp: UTC 时间字符串，如 "2026-04-13T22:30:02.635Z"

    Returns:
        (hkt_date, hkt_hour): HKT 日期和小时（0-23）

    Examples:
        "2026-04-13T02:30:00Z" → (2026-04-13, 10)  # UTC 02:30 = HKT 10:30
        "2026-04-13T20:00:00Z" → (2026-04-14, 4)   # UTC 20:00 = HKT 04:00 → 次日
        "2026-04-13T15:59:59Z" → (2026-04-13, 23)  # UTC 15:59 = HKT 23:59 → 当天
        "2026-04-13T16:00:00Z" → (2026-04-13, 0)   # UTC 16:00 = HKT 00:00 → 归前一天(04:00前)
        "2026-04-13T19:59:59Z" → (2026-04-13, 3)   # UTC 19:59 = HKT 03:59 → 归前一天(04:00前)
    """
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return None, None

    selected_tz = tz or resolve_timezone()
    local_dt = parsed.astimezone(selected_tz)
    return business_date_for(parsed, tz=selected_tz), local_dt.hour


def hkt_now() -> datetime:
    """返回当前配置时区时间。函数名保留兼容。"""
    return datetime.now(resolve_timezone()).replace(tzinfo=None)


def hkt_today() -> date:
    """返回当前配置时区业务日期（考虑 04:00 日界）。"""
    return (hkt_now() - timedelta(hours=4)).date()


def hkt_cutoff_utc(days: int = 1) -> str:
    """
    返回 UTC cutoff 字符串，用于过滤 JSONL。
    days=1 表示从今天 HKT 04:00 到现在。

    Args:
        days: 回溯天数

    Returns:
        UTC 时间字符串，如 "2026-04-13T20:00:00"
    """
    today = hkt_today()
    start_date = today - timedelta(days=days - 1)
    start_utc, _ = business_window(start_date)
    return start_utc.strftime("%Y-%m-%dT%H:%M:%S")
