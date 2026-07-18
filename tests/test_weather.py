import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_foundation import weather
from data_foundation.paths import initialize_home


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class WeatherTests(unittest.TestCase):
    def test_auto_ip_location_is_cached_for_weather_fetches(self):
        weather_payload = {
            "daily": {
                "time": ["2026-06-05"],
                "temperature_2m_max": [21.0],
                "temperature_2m_min": [12.5],
                "precipitation_sum": [0.0],
                "weather_code": [1],
            }
        }
        opened = []

        def fake_urlopen(url, **kwargs):
            del kwargs
            opened.append(url)
            if url == weather.GEOLOCATION_URL:
                return _Response(
                    {
                        "latitude": 37.7749,
                        "longitude": -122.4194,
                        "city": "San Francisco",
                        "region": "California",
                        "country_name": "United States",
                        "timezone": "America/Los_Angeles",
                    }
                )
            return _Response(weather_payload)

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            settings = {"enabled": True, "locationMode": "auto-ip", "cacheTtlHours": 24}
            first = weather.fetch_weather_for_date(
                "2026-06-05",
                paths=paths,
                weather_settings=settings,
                urlopen=fake_urlopen,
                sleep_seconds=0,
            )
            second = weather.fetch_weather_for_date(
                "2026-06-06",
                paths=paths,
                weather_settings=settings,
                urlopen=fake_urlopen,
                sleep_seconds=0,
            )

        self.assertEqual(opened.count(weather.GEOLOCATION_URL), 1)
        self.assertIn("latitude=37.774900", opened[1])
        self.assertIn("longitude=-122.419400", opened[1])
        self.assertIn("timezone=America%2FLos_Angeles", opened[1])
        self.assertEqual(first, "晴间多云，最高21.0°C，最低12.5°C (降水0.0mm)")
        self.assertEqual(second, "晴间多云，最高21.0°C，最低12.5°C (降水0.0mm)")

    def test_auto_ip_failure_does_not_fall_back_to_hong_kong(self):
        opened = []

        def fake_urlopen(url, **kwargs):
            del kwargs
            opened.append(url)
            raise OSError("network unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            result = weather.fetch_weather_for_date(
                "2026-06-05",
                paths=paths,
                weather_settings={"enabled": True, "locationMode": "auto-ip"},
                urlopen=fake_urlopen,
                sleep_seconds=0,
            )

        self.assertEqual(opened, [weather.GEOLOCATION_URL])
        self.assertEqual(result, weather.WEATHER_LOCATION_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
