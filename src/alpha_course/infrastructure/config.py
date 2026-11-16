"""Validated configuration; diagnostics must never expose input values."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class ConfigurationError(ValueError):
    """A safe configuration error suitable for process diagnostics."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALFA_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr
    telegram_bot_token: SecretStr | None = None
    yandex_api_key: SecretStr | None = None
    yandex_folder_id: str | None = None
    # One HTTP call to the model; the whole consultation is bounded by answer_deadline_seconds.
    yandex_timeout_seconds: float = Field(default=20.0, gt=0, allow_inf_nan=False)
    # "none" disables hidden reasoning of deepseek-v4.1-flash (the main latency cause);
    # "default" sends nothing and keeps the provider behaviour.
    yandex_reasoning_effort: Literal["none", "low", "medium", "high", "default"] = "none"
    answer_deadline_seconds: float = Field(default=25.0, ge=5, le=300, allow_inf_nan=False)
    corpus_path: Path = Path("data/derived/corpus.json")
    index_dir: Path = Path("data/index")
    admin_ids: str = ""
    webapp_url: str | None = None
    webapp_tunnel_url: str | None = None
    webapp_port: int = Field(default=8080, ge=1, le=65535)
    webapp_dev: bool = False
    ab_paid_offer: bool = True

    @field_validator("webapp_url")
    @classmethod
    def validate_webapp_url(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not value.startswith("https://") or len(value) <= len("https://"):
            raise ValueError("must be a public https URL")
        return value

    def admin_set(self) -> frozenset[int]:
        return frozenset(
            int(item)
            for item in self.admin_ids.replace(";", ",").split(",")
            if item.strip().isdigit()
        )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    worker_poll_interval_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = url.drivername == "postgresql+psycopg" and bool(url.database)
        except Exception:
            valid = False
        if not valid:
            raise ValueError("must be a postgresql+psycopg URL with a database")
        return value

    def require_bot_token(self) -> str:
        """Return the token for bot startup; checks/migrations need no token."""
        if (
            self.telegram_bot_token is None
            or not self.telegram_bot_token.get_secret_value().strip()
        ):
            raise ConfigurationError("Missing configuration field: telegram_bot_token")
        return self.telegram_bot_token.get_secret_value()


def load_settings() -> Settings:
    """Load environment settings and report field names without values or causes."""
    try:
        return Settings()
    except ValidationError as error:
        fields = sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]})
        raise ConfigurationError("Invalid configuration fields: " + ", ".join(fields)) from None
