"""Palabra clave efectiva para listados Computrabajo (URL semilla + fallback)."""

from __future__ import annotations

import unittest

from app.collectors.sources import jobs as jobs_mod


class ComputrabajoKeywordTests(unittest.TestCase):
    def test_query_param_wins_over_slug(self) -> None:
        u = "https://pe.computrabajo.com/trabajo-de-python?q=java+developer&p=1"
        self.assertEqual(jobs_mod._effective_computrabajo_keyword(u, "ignored"), "java developer")

    def test_slug_when_no_q(self) -> None:
        u = "https://pe.computrabajo.com/trabajo-de-desarrollador-web"
        self.assertEqual(jobs_mod._effective_computrabajo_keyword(u, "x"), "desarrollador web")

    def test_home_falls_back_to_query(self) -> None:
        u = "https://www.computrabajo.com/"
        self.assertEqual(jobs_mod._effective_computrabajo_keyword(u, "lima ventas"), "lima ventas")

    def test_none_seed_uses_query(self) -> None:
        self.assertEqual(jobs_mod._effective_computrabajo_keyword(None, "abc"), "abc")


if __name__ == "__main__":
    unittest.main()
