"""Validación de re-prompts cuando el usuario no responde lo esperado."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registra tablas en Base
from app.agent import (
    _is_greeting_or_filler,
    _reprompt_region_options,
    _wants_to_send_cv_now,
    process_message,
)
from app.config import Settings
from app.db import Base
from app.models import UserProfile


def _memory_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _test_settings() -> Settings:
    return Settings(
        openai_api_key="",
        evolution_my_phone="51955119999",
        evolution_base_url="http://127.0.0.1:9",
        evolution_api_key="",
        evolution_instance="",
    )


class AgentRepromptHelpers(unittest.TestCase):
    def test_greeting_filler(self) -> None:
        self.assertTrue(_is_greeting_or_filler("hola"))
        self.assertTrue(_is_greeting_or_filler("OK"))
        self.assertTrue(_is_greeting_or_filler("  "))
        self.assertFalse(_is_greeting_or_filler("Lima"))
        self.assertFalse(_is_greeting_or_filler("remoto LATAM"))

    def test_wants_cv(self) -> None:
        self.assertTrue(_wants_to_send_cv_now("sí"))
        self.assertTrue(_wants_to_send_cv_now("dale, te mando el pdf"))
        self.assertFalse(_wants_to_send_cv_now("hola"))
        self.assertFalse(_wants_to_send_cv_now("quizás después"))

    def test_reprompt_region_has_examples(self) -> None:
        body = _reprompt_region_options()
        self.assertIn("Lima", body)
        self.assertIn("remoto", body.lower())


@patch("app.agent._coach_increment_safe", new_callable=AsyncMock)
@patch("app.agent.send_whatsapp_text", new_callable=AsyncMock)
class AgentRepromptFlows(unittest.IsolatedAsyncioTestCase):
    async def test_ask_region_hola_stays_ask_region(self, _mock_send: AsyncMock, mock_coach: AsyncMock) -> None:
        mock_coach.return_value = ({}, None)
        db = _memory_session()
        try:
            jid = "51955119999@s.whatsapp.net"
            u = UserProfile(wa_id=jid, conversation_state="ask_region", role="job_seeker")
            db.add(u)
            db.commit()

            await process_message(db, _test_settings(), jid, "hola", payload={})
            db.refresh(u)
            self.assertEqual(u.conversation_state, "ask_region")
            self.assertFalse((u.region or "").strip())
        finally:
            db.close()

    async def test_ask_has_cv_hola_reprompts(self, _mock_send: AsyncMock, mock_coach: AsyncMock) -> None:
        mock_coach.return_value = ({}, None)
        db = _memory_session()
        try:
            jid = "51955119999@s.whatsapp.net"
            u = UserProfile(wa_id=jid, conversation_state="ask_has_cv", role="job_seeker", goal="empleo")
            db.add(u)
            db.commit()

            await process_message(db, _test_settings(), jid, "hola", payload={})
            db.refresh(u)
            self.assertEqual(u.conversation_state, "ask_has_cv")
        finally:
            db.close()

    async def test_ask_has_cv_si_goes_await_cv(self, _mock_send: AsyncMock, mock_coach: AsyncMock) -> None:
        mock_coach.return_value = ({}, None)
        db = _memory_session()
        try:
            jid = "51955119999@s.whatsapp.net"
            u = UserProfile(wa_id=jid, conversation_state="ask_has_cv", role="job_seeker", goal="empleo")
            db.add(u)
            db.commit()

            await process_message(db, _test_settings(), jid, "sí", payload={})
            db.refresh(u)
            self.assertEqual(u.conversation_state, "await_cv")
        finally:
            db.close()

    async def test_ask_skills_short_reprompts(self, _mock_send: AsyncMock, mock_coach: AsyncMock) -> None:
        mock_coach.return_value = ({}, None)
        db = _memory_session()
        try:
            jid = "51955119999@s.whatsapp.net"
            u = UserProfile(wa_id=jid, conversation_state="ask_skills", role="job_seeker", goal="empleo")
            db.add(u)
            db.commit()

            await process_message(db, _test_settings(), jid, "ok", payload={})
            db.refresh(u)
            self.assertEqual(u.conversation_state, "ask_skills")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
