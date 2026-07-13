"""Weather location resolution and Open-Meteo daily weather fetches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable
import urllib.parse
import urllib.request

from .paths import RuntimePaths, load_paths
from .settings import read_settings
from .time import resolve_timezone_name


GEOLOCATION_URL = "https://ipapi.co/json/"
WEATHER_LOCATION_UNAVAILABLE = "（天气位置未配置）"
WEATHER_FETCH_FAILED = "（天气数据获取失败）"


@dataclass(frozen=True)
class WeatherLocation:
    latitude: float
    longitude: float
    label: str = ""
    timezone_name: str | None = None
    source: str = "settings"


def fetch_weather_for_date(
    target_date_str: str,
    *,
    paths: RuntimePaths | None = None,
    weather_settings: dict[str, Any] | None = None,
    urlopen: Callable[..., Any] | None = None,
    sleep_seconds: float = 0.25,
) -> str:
    selected_paths = paths or load_paths()
    settings = resolve_weather_settings(selected_paths, weather_settings=weather_settings)
    if not settings["enabled"] or settings["locationMode"] == "disabled":
        return WEATHER_LOCATION_UNAVAILABLE
    opener = urlopen or urllib.request.urlopen
    location = resolve_weather_location(selected_paths, settings, urlopen=opener)
    if location is None:
        return WEATHER_LOCATION_UNAVAILABLE

    last_error = None
    for url in weather_urls(target_date_str, location):
        for _ in range(2):
            try:
                with opener(url, timeout=settings["timeoutSeconds"]) as resp:
                    payload = json.loads(resp.read())
                formatted = format_weather_daily(payload.get("daily", {}))
                if formatted:
                    return formatted
            except Exception as exc:
                last_error = exc
                if sleep_seconds:
                    time.sleep(sleep_seconds)
    if last_error:
        print(f"   WARNING: weather fetch failed for {target_date_str}: {last_error}")
    return WEATHER_FETCH_FAILED


def resolve_weather_settings(
    paths: RuntimePaths | None = None,
    *,
    weather_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = weather_settings
    if raw is None:
        settings = read_settings(paths, redact_secrets=False)
        raw = settings.get("weather") if isinstance(settings.get("weather"), dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    enabled = _bool_setting(raw.get("enabled"), True)
    mode = str(raw.get("locationMode") or "auto-ip").strip().lower()
    if mode not in {"auto-ip", "manual", "disabled"}:
        mode = "auto-ip"
    latitude = _optional_float(raw.get("latitude"))
    longitude = _optional_float(raw.get("longitude"))
    return {
        "enabled": enabled,
        "locationMode": "disabled" if not enabled else mode,
        "latitude": latitude,
        "longitude": longitude,
        "label": str(raw.get("label") or "").strip(),
        "timezone": str(raw.get("timezone") or "auto").strip() or "auto",
        "cacheTtlHours": _positive_int(raw.get("cacheTtlHours"), 24),
        "timeoutSeconds": _positive_int(raw.get("timeoutSeconds"), 10),
    }


def resolve_weather_location(
    paths: RuntimePaths,
    settings: dict[str, Any],
    *,
    urlopen: Callable[..., Any] | None = None,
) -> WeatherLocation | None:
    manual = _manual_location(settings)
    if manual is not None:
        return manual
    if settings.get("locationMode") != "auto-ip":
        return None
    cached = _read_cached_location(paths, settings)
    if cached is not None:
        return cached
    return _detect_ip_location(paths, settings, urlopen=urlopen or urllib.request.urlopen)


def weather_urls(target_date_str: str, location: WeatherLocation) -> tuple[str, str]:
    daily = "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code"
    timezone_name = urllib.parse.quote(location.timezone_name or resolve_timezone_name(), safe="")
    query = (
        f"?latitude={location.latitude:.6f}&longitude={location.longitude:.6f}"
        f"&daily={daily}"
        f"&timezone={timezone_name}&start_date={target_date_str}&end_date={target_date_str}"
    )
    return (
        f"https://archive-api.open-meteo.com/v1/archive{query}",
        f"https://api.open-meteo.com/v1/forecast{query}",
    )


def format_weather_daily(daily: dict[str, Any]) -> str | None:
    if not daily.get("time"):
        return None
    idx = 0
    try:
        t_max = daily["temperature_2m_max"][idx]
        t_min = daily["temperature_2m_min"][idx]
        precip = daily["precipitation_sum"][idx]
    except (KeyError, IndexError, TypeError):
        return None
    code_values = daily.get("weather_code") or daily.get("weathercode")
    if not code_values:
        return None
    code = code_values[idx]
    wmo = {
        0: "晴天",
        1: "晴间多云",
        2: "多云",
        3: "阴天",
        45: "雾",
        48: "霜雾",
        51: "小毛毛雨",
        53: "中毛毛雨",
        55: "大毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        80: "小阵雨",
        81: "中阵雨",
        82: "大阵雨",
        95: "雷暴",
        96: "雷暴+冰雹",
        99: "雷暴+大雨+冰雹",
    }
    desc = wmo.get(code, f"天气代码{code}")
    return f"{desc}，最高{t_max}°C，最低{t_min}°C (降水{precip}mm)"


def _manual_location(settings: dict[str, Any]) -> WeatherLocation | None:
    latitude = settings.get("latitude")
    longitude = settings.get("longitude")
    if latitude is None or longitude is None:
        return None
    timezone_name = settings.get("timezone")
    return WeatherLocation(
        latitude=float(latitude),
        longitude=float(longitude),
        label=str(settings.get("label") or "").strip(),
        timezone_name=None if timezone_name in {"", "auto", None} else str(timezone_name),
        source="settings",
    )


def _detect_ip_location(
    paths: RuntimePaths,
    settings: dict[str, Any],
    *,
    urlopen: Callable[..., Any],
) -> WeatherLocation | None:
    try:
        with urlopen(GEOLOCATION_URL, timeout=settings["timeoutSeconds"]) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        print(f"   WARNING: weather location detection failed: {exc}")
        return None
    latitude = _optional_float(payload.get("latitude") if "latitude" in payload else payload.get("lat"))
    longitude = _optional_float(payload.get("longitude") if "longitude" in payload else payload.get("lon"))
    if latitude is None or longitude is None:
        return None
    label = _location_label(payload)
    detected = WeatherLocation(
        latitude=latitude,
        longitude=longitude,
        label=label,
        timezone_name=str(payload.get("timezone") or "").strip() or None,
        source="auto-ip",
    )
    _write_cached_location(paths, detected)
    return detected


def _location_label(payload: dict[str, Any]) -> str:
    parts = [
        str(payload.get(key) or "").strip()
        for key in ("city", "region", "country_name")
        if str(payload.get(key) or "").strip()
    ]
    return ", ".join(parts)


def _cache_path(paths: RuntimePaths) -> Path:
    return paths.state_dir / "cache" / "weather-location.json"


def _read_cached_location(paths: RuntimePaths, settings: dict[str, Any]) -> WeatherLocation | None:
    try:
        payload = json.loads(_cache_path(paths).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    detected_at = _optional_float(payload.get("detectedAtEpoch"))
    if detected_at is None:
        return None
    if time.time() - detected_at > int(settings.get("cacheTtlHours") or 24) * 3600:
        return None
    latitude = _optional_float(payload.get("latitude"))
    longitude = _optional_float(payload.get("longitude"))
    if latitude is None or longitude is None:
        return None
    return WeatherLocation(
        latitude=latitude,
        longitude=longitude,
        label=str(payload.get("label") or ""),
        timezone_name=str(payload.get("timezone") or "").strip() or None,
        source=str(payload.get("source") or "auto-ip"),
    )


def _write_cached_location(paths: RuntimePaths, location: WeatherLocation) -> None:
    path = _cache_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": 1,
        "source": location.source,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "label": location.label,
        "timezone": location.timezone_name,
        "detectedAt": datetime.now(timezone.utc).isoformat(),
        "detectedAtEpoch": time.time(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _bool_setting(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
