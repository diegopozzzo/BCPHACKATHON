"""Saneamiento de replies del coach en modo embudo (anti-alucinación / menos ruido)."""

from __future__ import annotations

import unittest

from app.coach_llm import sanitize_coach_funnel_reply


class CoachFunnelSanitizeTests(unittest.TestCase):
    def test_allows_short_ack(self) -> None:
        self.assertEqual(sanitize_coach_funnel_reply("  Listo, anoté Lima. "), "Listo, anoté Lima.")

    def test_drops_questions(self) -> None:
        self.assertIsNone(sanitize_coach_funnel_reply("¿Seguro que es Lima?"))

    def test_drops_cv_pitch(self) -> None:
        self.assertIsNone(sanitize_coach_funnel_reply("Mandame tu CV en PDF por favor"))

    def test_drops_multiline_extra(self) -> None:
        self.assertEqual(
            sanitize_coach_funnel_reply("Perfecto.\n\nAhora decime si tenés PDF"),
            "Perfecto.",
        )

    def test_empty(self) -> None:
        self.assertIsNone(sanitize_coach_funnel_reply(None))
        self.assertIsNone(sanitize_coach_funnel_reply("   "))


if __name__ == "__main__":
    unittest.main()
