"""Solo se procesa el chat contigo mismo; no chats 1:1 con terceros."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.evolution_filter import evolution_is_self_chat, evolution_should_process_message


def _st_one_me() -> SimpleNamespace:
    return SimpleNamespace(
        allowed_jid_set={"51955111111@s.whatsapp.net"},
        evolution_self_chat_mode="allow",
        evolution_allow_from_me=False,
    )


def _st_me_and_other() -> SimpleNamespace:
    return SimpleNamespace(
        allowed_jid_set={
            "51955111111@s.whatsapp.net",
            "51998887766@s.whatsapp.net",
        },
        evolution_self_chat_mode="allow",
        evolution_allow_from_me=False,
    )


class EvolutionSelfOnlyTests(unittest.TestCase):
    def test_friend_pm_u_equals_s_not_self(self) -> None:
        """Mismo dígito en remote y sender (el contacto) no es self-chat."""
        st = _st_me_and_other()
        other = "51998887766@s.whatsapp.net"
        payload = {"sender": "51998887766@s.whatsapp.net"}
        self.assertFalse(evolution_is_self_chat(payload, other, st))

    def test_self_number_chat_detected(self) -> None:
        st = _st_one_me()
        me = "51955111111@s.whatsapp.net"
        payload = {"sender": "51955111111@s.whatsapp.net"}
        self.assertTrue(evolution_is_self_chat(payload, me, st))

    def test_multi_allow_blocks_other_dm(self) -> None:
        """Lista con varios JIDs: chat con el 'otro' permitido no es 'contigo'."""
        st = _st_me_and_other()
        other = "51998887766@s.whatsapp.net"
        ok, reason = evolution_should_process_message(
            {"sender": "51998887766@s.whatsapp.net"},
            remote_jid=other,
            text="hola",
            from_me=False,
            stanza_id="abc",
            settings=st,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "solo_mensajes_contigo")

    def test_self_inbound_processed(self) -> None:
        st = _st_one_me()
        me = "51955111111@s.whatsapp.net"
        ok, reason = evolution_should_process_message(
            {"sender": "51955111111@s.whatsapp.net"},
            remote_jid=me,
            text="hola",
            from_me=False,
            stanza_id="abc",
            settings=st,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_unknown_jid_rejected_before_self(self) -> None:
        st = _st_one_me()
        ok, reason = evolution_should_process_message(
            {"sender": "51900000000@s.whatsapp.net"},
            remote_jid="51900000000@s.whatsapp.net",
            text="hola",
            from_me=False,
            stanza_id="abc",
            settings=st,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "jid_no_permitido_o_grupo")

    def test_lid_friend_sender_is_owner_not_self(self) -> None:
        """@lid con un tercero: aunque `sender` sea tu cuenta, no es 'Mensajes contigo'."""
        st = _st_one_me()
        me = "51955111111@s.whatsapp.net"
        friend = "51998887766@s.whatsapp.net"
        payload = {
            "sender": me,
            "data": {
                "messages": [
                    {
                        "key": {
                            "remoteJid": "ABCDEF0123@lid",
                            "remoteJidAlt": friend,
                            "fromMe": False,
                        }
                    }
                ]
            },
        }
        self.assertFalse(evolution_is_self_chat(payload, "ABCDEF0123@lid", st))
        ok, reason = evolution_should_process_message(
            payload,
            remote_jid="ABCDEF0123@lid",
            text="hola",
            from_me=False,
            stanza_id="x1",
            settings=st,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "solo_mensajes_contigo")

    def test_lid_self_chat_with_alt_detected(self) -> None:
        st = _st_one_me()
        me = "51955111111@s.whatsapp.net"
        payload = {
            "sender": me,
            "data": {
                "messages": [
                    {
                        "key": {
                            "remoteJid": "LIDSELF@lid",
                            "remoteJidAlt": me,
                            "fromMe": False,
                        }
                    }
                ]
            },
        }
        self.assertTrue(evolution_is_self_chat(payload, "LIDSELF@lid", st))


if __name__ == "__main__":
    unittest.main()
