"""Runtime configuration. Values come from the environment / a gitignored `.env`."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _jobpilot(name: str) -> AliasChoices:
    """Accept both `JOBPILOT_FOO` and bare `FOO` for project-specific settings."""
    return AliasChoices(f"JOBPILOT_{name.upper()}", name.upper())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/jobpilot"

    # Credentials. Empty is legal — fixture_mode runs the pipeline without them.
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    # Wires recorded fixtures into the real pipeline in place of live API clients.
    fixture_mode: bool = Field(default=False, validation_alias=_jobpilot("fixture_mode"))

    # Tuning dials. Phase 0 volume governs tailoring throughput, not submissions.
    match_score_threshold: int = Field(
        default=70, validation_alias=_jobpilot("match_score_threshold")
    )
    max_tailored_per_day: int = Field(
        default=12, validation_alias=_jobpilot("max_tailored_per_day")
    )
    embed_top_k: int = Field(default=40, validation_alias=_jobpilot("embed_top_k"))

    tailoring_model: str = "claude-sonnet-5"
    scoring_model: str = "claude-sonnet-5"
    extraction_model: str = "claude-sonnet-5"
    embedding_model: str = "voyage-3"
    embedding_dimensions: int = 1024

    # Claude Sonnet 5 runs adaptive thinking by default and max_tokens caps
    # thinking + output together, so these carry headroom above the JSON payload.
    scoring_max_tokens: int = 8000
    tailoring_max_tokens: int = 16000
    extraction_max_tokens: int = 16000

    max_tailoring_attempts: int = 3


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """Process-wide settings singleton. `refresh=True` re-reads the environment."""
    global _settings
    if _settings is None or refresh:
        _settings = Settings()
    return _settings
