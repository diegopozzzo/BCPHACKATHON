from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.whatsapp_jid import build_allowed_dm_set

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent


def _load_dotenv_into_os() -> None:
    """Carga `.env` raíz y luego `backend/.env` (este último pisa al anterior). `override=True` para que el archivo mande sobre variables sueltas del shell."""
    root = _REPO_ROOT / ".env"
    back = _BACKEND_DIR / ".env"
    if root.is_file():
        load_dotenv(root, override=True)
    if back.is_file():
        load_dotenv(back, override=True)


_load_dotenv_into_os()


class Settings(BaseSettings):
    """
    Variables desde el entorno (tras `load_dotenv` al importar este módulo).
    """

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    node_env: str = Field(default="development", validation_alias="NODE_ENV")
    port: int = Field(default=8000, validation_alias="PORT")
    app_base_url: str = Field(
        default="http://localhost:8000",
        validation_alias="APP_BASE_URL",
    )
    log_level: str = Field(default="info", validation_alias="LOG_LEVEL")

    database_url: str = Field(
        default="sqlite:///./data/app.db",
        validation_alias="DATABASE_URL",
    )
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")

    default_locale: str = Field(default="es", validation_alias="DEFAULT_LOCALE")
    default_timezone: str = Field(
        default="America/Lima",
        validation_alias="DEFAULT_TIMEZONE",
    )

    evolution_allowed_jids: str = Field(default="", validation_alias="EVOLUTION_ALLOWED_JIDS")
    evolution_allowed_jid: str = Field(default="", validation_alias="EVOLUTION_ALLOWED_JID")
    evolution_my_phone: str = Field(
        default="",
        validation_alias="EVOLUTION_MY_PHONE",
        description="Solo dígitos con código país; equivale a un único chat permitido.",
    )
    evolution_base_url: str = Field(
        default="http://localhost:8080",
        validation_alias=AliasChoices("EVOLUTION_API_URL", "EVOLUTION_BASE_URL"),
    )
    evolution_api_key: str = Field(default="", validation_alias="EVOLUTION_API_KEY")
    evolution_instance: str = Field(default="", validation_alias="EVOLUTION_INSTANCE")
    webhook_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EVOLUTION_WEBHOOK_TOKEN", "WEBHOOK_SECRET"),
    )
    evolution_self_chat_mode: str = Field(
        default="only",
        validation_alias="EVOLUTION_SELF_CHAT_MODE",
        description="NEKOBOT-style: only=solo Mensajes contigo; allow=contigo+otros; disabled=nunca fromMe.",
    )
    evolution_allow_from_me: bool = Field(
        default=False,
        validation_alias=AliasChoices("EVOLUTION_ALLOW_FROM_ME", "EVOLUTION_PROCESS_SELF_CHAT"),
        description="Legacy: si true y no usas SELF_CHAT_MODE, equivale a allow.",
    )

    whatsapp_verify_token: str | None = Field(default=None, validation_alias="WHATSAPP_VERIFY_TOKEN")
    whatsapp_app_secret: str | None = Field(default=None, validation_alias="WHATSAPP_APP_SECRET")
    whatsapp_access_token: str | None = Field(default=None, validation_alias="WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: str | None = Field(
        default=None,
        validation_alias="WHATSAPP_PHONE_NUMBER_ID",
    )
    whatsapp_api_version: str = Field(default="v21.0", validation_alias="WHATSAPP_API_VERSION")
    whatsapp_send_mode: str = Field(default="off", validation_alias="WHATSAPP_SEND_MODE")

    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    openai_embeddings_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="OPENAI_EMBEDDINGS_MODEL",
    )
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-sonnet-4-20250514",
        validation_alias="ANTHROPIC_MODEL",
    )

    admin_dashboard_token: str | None = Field(
        default=None,
        validation_alias="ADMIN_DASHBOARD_TOKEN",
        description="Si se define, /admin y /admin/api/* requieren ?token= o header X-Admin-Token.",
    )

    @property
    def allowed_jid_set(self) -> set[str]:
        return build_allowed_dm_set(
            csv=self.evolution_allowed_jids,
            single=self.evolution_allowed_jid,
            my_phone=self.evolution_my_phone,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Útil en tests tras cambiar variables de entorno."""
    get_settings.cache_clear()
    return get_settings()
