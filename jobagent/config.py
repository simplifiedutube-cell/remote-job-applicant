"""Runtime configuration.

Frozen and loaded once. `dry_run` defaults to True and there is deliberately no
`--dry-run` flag anywhere in the CLI -- there is a `--live` flag, and it is not by itself
sufficient. See `jobagent.safety` for why that is two keys rather than one.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["RunConfig", "get_config", "reset_config_cache"]


class RunConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOBAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # ---------------------------------------------------------------- core
    db_dsn: str = "postgresql://jobagent:jobagent@localhost:5433/jobagent"
    test_dsn: str | None = None
    artifact_dir: Path = Path("./artifacts")
    export_dir: Path = Path("./exports")
    log_level: str = "INFO"
    log_json: bool = False

    # ---------------------------------------------------------------- safety
    #: Guardrail 3. True unless BOTH this is flipped and `allow_live` is set.
    dry_run: bool = True
    #: The second key. A `--live` flag alone can never reach a real submit.
    allow_live: bool = False

    # ---------------------------------------------------------------- screener (M3)
    fit_threshold: int = Field(default=75, ge=0, le=100)

    # ---------------------------------------------------------------- llm (M3/M4/M7)
    llm_provider: Literal["claude_code", "anthropic_api"] = "claude_code"
    llm_model: str = "claude-sonnet-5"

    # ---------------------------------------------------------------- embeddings (M2/M4)
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    repost_cosine_threshold: float = Field(default=0.92, ge=0.0, le=1.0)

    # ---------------------------------------------------------------- render (M4)
    soffice_bin: Path = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")

    # ---------------------------------------------------------------- browser (M6)
    storage_state_dir: Path = Path("./secrets/storage_state")
    max_applies_per_portal_per_day: int = Field(default=10, ge=1, le=100)
    pace_min_seconds: float = Field(default=30.0, ge=0.0)
    pace_max_seconds: float = Field(default=120.0, ge=0.0)

    # ---------------------------------------------------------------- notify (M7)
    digest_hour: int = Field(default=20, ge=0, le=23)
    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "JOBAGENT_TELEGRAM_BOT_TOKEN"),
    )
    telegram_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_CHAT_ID", "JOBAGENT_TELEGRAM_CHAT_ID"),
    )
    gmail_credentials_json: Path = Path("./secrets/gmail_client_secret.json")
    gmail_token_json: Path = Path("./secrets/gmail_token.json")
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "JOBAGENT_ANTHROPIC_API_KEY"),
    )

    @property
    def effective_test_dsn(self) -> str | None:
        return self.test_dsn


@lru_cache(maxsize=1)
def get_config() -> RunConfig:
    return RunConfig()


def reset_config_cache() -> None:
    """Only for tests that manipulate the environment."""
    get_config.cache_clear()
