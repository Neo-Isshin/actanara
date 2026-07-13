import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.services.dashboard_security import (
    DashboardSessionStore,
    dashboard_security_config,
    is_host_allowed,
    is_origin_allowed,
    is_protected_path,
    is_session_exempt_path,
    should_bootstrap_session,
)
from data_foundation.paths import initialize_home
from data_foundation.settings import resolve_dashboard_settings, validate_operator_settings_update, write_settings


class DashboardSecurityTests(unittest.TestCase):
    def test_default_dashboard_origins_are_loopback_only(self):
        settings = {"host": "127.0.0.1", "port": 3036, "publicBaseUrl": "", "allowedOrigins": []}
        config = dashboard_security_config(settings)

        self.assertNotIn("*", config["allowedOrigins"])
        self.assertIn("http://127.0.0.1:3036", config["allowedOrigins"])
        self.assertIn("http://localhost:3036", config["allowedOrigins"])
        self.assertTrue(is_origin_allowed("http://localhost:3036", settings))
        self.assertFalse(is_origin_allowed("https://evil.example", settings))
        self.assertTrue(is_host_allowed("127.0.0.1:3036", settings))

    def test_public_base_url_and_allowed_origins_are_explicit(self):
        settings = {
            "host": "0.0.0.0",
            "port": 3036,
            "publicBaseUrl": "https://nova.example.test/dashboard",
            "allowedOrigins": ["https://ops.example.test/"],
        }
        config = dashboard_security_config(settings)

        self.assertIn("https://nova.example.test", config["allowedOrigins"])
        self.assertIn("https://ops.example.test", config["allowedOrigins"])
        self.assertTrue(is_origin_allowed("https://nova.example.test", settings))
        self.assertTrue(is_origin_allowed("https://ops.example.test", settings))
        self.assertFalse(is_origin_allowed("https://other.example.test", settings))
        self.assertTrue(is_host_allowed("nova.example.test", settings))

    def test_session_store_requires_matching_csrf_cookie_and_header(self):
        store = DashboardSessionStore()
        session, csrf = store.create()

        self.assertTrue(store.validate(session))
        self.assertTrue(store.validate_csrf(session, csrf, csrf))
        self.assertFalse(store.validate_csrf(session, "wrong", csrf))
        self.assertFalse(store.validate_csrf(session, csrf, "wrong"))
        self.assertFalse(store.validate("missing"))

    def test_security_path_policy_keeps_ui_low_friction(self):
        self.assertTrue(should_bootstrap_session("/dashboard", "GET"))
        self.assertTrue(should_bootstrap_session("/static/index.html", "GET"))
        self.assertFalse(should_bootstrap_session("/api/settings", "GET"))
        self.assertTrue(is_protected_path("/api/settings"))
        self.assertTrue(is_protected_path("/events/tokens"))
        self.assertTrue(is_session_exempt_path("/api/rag/external/search"))
        self.assertFalse(is_session_exempt_path("/api/file-content"))

    def test_dashboard_settings_resolve_public_origin_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            write_settings(
                {
                    "dashboard": {
                        "host": "0.0.0.0",
                        "port": 4040,
                        "publicBaseUrl": "https://nova.example.test",
                        "allowedOrigins": ["https://ops.example.test"],
                    }
                },
                paths,
            )

            resolved = resolve_dashboard_settings(paths)

        self.assertEqual(resolved["publicBaseUrl"], "https://nova.example.test")
        self.assertEqual(resolved["allowedOrigins"], ["https://ops.example.test"])

    def test_dashboard_origin_settings_validate_shape(self):
        validate_operator_settings_update(
            {
                "dashboard": {
                    "publicBaseUrl": "https://nova.example.test",
                    "allowedOrigins": ["https://ops.example.test"],
                }
            }
        )
        with self.assertRaises(ValueError):
            validate_operator_settings_update({"dashboard": {"allowedOrigins": "*"}})
        with self.assertRaises(ValueError):
            validate_operator_settings_update({"dashboard": {"allowedOrigins": ["https://ops.example.test/path"]}})

    def test_main_uses_allowlisted_cors_and_events_only_router(self):
        main = (ROOT / "src" / "dashboard" / "app" / "main.py").read_text(encoding="utf-8")

        self.assertNotIn('allow_origins=["*"]', main)
        self.assertIn('allow_origins=_security_config["allowedOrigins"]', main)
        self.assertIn("DashboardSecurityMiddleware", main)
        self.assertIn("metrics.events_router", main)
        self.assertIn("ai_assets.events_router", main)
        self.assertNotIn('app.include_router(metrics.router, tags=["Events"])', main)
        self.assertNotIn('app.include_router(ai_assets.router, tags=["Events"])', main)

    def test_antiframing_headers_cover_normal_and_auth_failure_responses(self):
        from app import main as dashboard_main
        from starlette.requests import Request
        from starlette.responses import Response

        middleware = dashboard_main.DashboardSecurityMiddleware(dashboard_main.app)

        def request(path: str, *, method: str = "GET", host: str = "127.0.0.1:3036", headers=()):
            raw_headers = [(b"host", host.encode("ascii")), *headers]
            return Request(
                {
                    "type": "http",
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode("ascii"),
                    "query_string": b"",
                    "headers": raw_headers,
                    "client": ("127.0.0.1", 50000),
                    "server": ("127.0.0.1", 3036),
                }
            )

        async def ok_call(_request):
            return Response("ok", status_code=200)

        normal = asyncio.run(middleware.dispatch(request("/health"), ok_call))
        unauthorized = asyncio.run(middleware.dispatch(request("/api/settings"), ok_call))
        bad_origin = asyncio.run(
            middleware.dispatch(
                request("/api/settings", headers=((b"origin", b"https://evil.example"),)),
                ok_call,
            )
        )

        self.assertEqual(normal.status_code, 200)
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(bad_origin.status_code, 403)
        for response in (normal, unauthorized, bad_origin):
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertEqual(response.headers["Content-Security-Policy"], "frame-ancestors 'none'")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertNotIn("default-src", response.headers["Content-Security-Policy"])

    def test_dashboard_health_publishes_frozen_loaded_source_commit(self):
        from app import main as dashboard_main

        commit = "1" * 40
        with patch.object(dashboard_main, "_LOADED_SOURCE_COMMIT", commit):
            payload = asyncio.run(dashboard_main.health())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["sourceCommit"], commit)
        self.assertNotIn(str(ROOT), payload.values())

    def test_main_creates_diary_data_static_directory_before_mount(self):
        main = (ROOT / "src" / "dashboard" / "app" / "main.py").read_text(encoding="utf-8")

        create_dir = "DIARY_DATA_DIR.mkdir(parents=True, exist_ok=True)"
        diary_mount = 'StaticFiles(directory=str(DIARY_DATA_DIR))'
        self.assertIn(create_dir, main)
        self.assertIn(diary_mount, main)
        self.assertLess(main.index(create_dir), main.index(diary_mount))


if __name__ == "__main__":
    unittest.main()
