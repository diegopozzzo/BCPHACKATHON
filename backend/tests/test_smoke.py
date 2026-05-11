"""Pruebas mínimas de humo (sin pytest: python -m unittest desde la carpeta backend)."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app


class SmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("solo_chat_configurado", data)

    def test_admin_html(self) -> None:
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("content-type", ""))

    def test_setup_evolution_qr_html(self) -> None:
        r = self.client.get("/setup/evolution-qr")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("content-type", ""))

    def test_company_search_validation(self) -> None:
        r = self.client.post("/company/search", json={"prompt": "ab"})
        self.assertEqual(r.status_code, 422)

    def test_evolution_webhook_accepts_list_or_dict(self) -> None:
        r = self.client.post("/webhooks/evolution", json=[])
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
