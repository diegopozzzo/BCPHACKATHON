"""Reglas de URLs gestionadas por scrapers y validación de filas."""

from __future__ import annotations

import unittest

from app.collectors.managed_urls import is_managed_scrape_url, validate_collected_row, validate_normalized
from app.collectors.schema import NormalizedOpportunity


class ManagedUrlTests(unittest.TestCase):
    def test_computrabajo_managed(self) -> None:
        self.assertTrue(
            is_managed_scrape_url("https://pe.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-xyz")
        )

    def test_example_not_managed(self) -> None:
        self.assertFalse(is_managed_scrape_url("https://example.com/job"))

    def test_validate_ok(self) -> None:
        ok, _ = validate_collected_row(
            title="Dev Python",
            type_="empleo",
            organization="ACME",
            region="Lima",
            requirements="2 años",
            url="https://pe.computrabajo.com/ofertas-de-trabajo/x",
        )
        self.assertTrue(ok)

    def test_validate_bad_url(self) -> None:
        ok, err = validate_collected_row(
            title="X",
            type_="empleo",
            organization="Y",
            region="Z",
            requirements="",
            url="ftp://bad",
        )
        self.assertFalse(ok)
        self.assertEqual(err, "url_invalida")

    def test_validate_normalized(self) -> None:
        it = NormalizedOpportunity(
            title="Curso",
            type="curso",
            organization="Platzi",
            region="Remoto",
            requirements="—",
            url="https://platzi.com/cursos/x",
            source="platzi",
        )
        self.assertTrue(validate_normalized(it)[0])


if __name__ == "__main__":
    unittest.main()
