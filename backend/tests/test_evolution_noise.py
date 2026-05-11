"""Mensajes de protocolo / sin texto de usuario no deben extraer cuerpo."""

from __future__ import annotations

import unittest

from app.evolution_client import extract_inbound_text, is_duplicate_inbound_stanza


class EvolutionNoiseTests(unittest.TestCase):
    def test_protocol_only_no_text(self) -> None:
        payload = {
            "data": [
                {
                    "key": {
                        "remoteJid": "51955111111@s.whatsapp.net",
                        "fromMe": False,
                        "id": "MSGPROTO1",
                    },
                    "message": {"protocolMessage": {"type": 0}},
                }
            ]
        }
        jid, text, from_me, sid = extract_inbound_text(payload)
        self.assertEqual(jid, "51955111111@s.whatsapp.net")
        self.assertIsNone(text)

    def test_stanza_dedupe(self) -> None:
        self.assertFalse(is_duplicate_inbound_stanza("51955111111@s.whatsapp.net", "ABC123"))
        self.assertTrue(is_duplicate_inbound_stanza("51955111111@s.whatsapp.net", "ABC123"))


if __name__ == "__main__":
    unittest.main()
