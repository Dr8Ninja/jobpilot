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

    # "nvidia" (OpenAI-compatible NIM) or "anthropic".
    llm_provider: str = Field(default="nvidia", validation_alias=_jobpilot("llm_provider"))
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    # Defaults chosen by measurement, not by reputation: both answer a strict
    # json_schema request in under 3s on this account. GLM-5.2 and DeepSeek V4 Pro
    # are listed by NVIDIA but never return a token on the free tier — set them
    # here if that changes.
    tailoring_model: str = Field(
        default="openai/gpt-oss-120b", validation_alias=_jobpilot("tailoring_model")
    )
    scoring_model: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b",
        validation_alias=_jobpilot("scoring_model"),
    )
    extraction_model: str = Field(
        default="openai/gpt-oss-120b", validation_alias=_jobpilot("extraction_model")
    )
    #: Tried in order when the requested model times out or is not served.
    llm_fallback_models: str = Field(
        default="openai/gpt-oss-120b,nvidia/nemotron-3-super-120b-a12b",
        validation_alias=_jobpilot("llm_fallback_models"),
    )
    llm_timeout_seconds: float = Field(
        default=90.0, validation_alias=_jobpilot("llm_timeout_seconds")
    )

    # "nvidia" reuses the chat key; "voyage" needs a separate pa-... key.
    embedding_provider: str = Field(
        default="nvidia", validation_alias=_jobpilot("embedding_provider")
    )
    embedding_model: str = Field(
        default="nvidia/nv-embedqa-e5-v5", validation_alias=_jobpilot("embedding_model")
    )
    #: nv-embedqa-e5-v5 and voyage-3 are both 1024-wide, so the two providers are
    #: interchangeable without a migration or a reindex.
    embedding_dimensions: int = 1024
    #: nv-embedqa-e5-v5 hard-caps at 512 tokens; ~1600 chars keeps a safety margin.
    embedding_char_budget: int = Field(
        default=1600, validation_alias=_jobpilot("embedding_char_budget")
    )

    #: Keyless public remote boards pulled every run.
    remote_boards: str = Field(
        default="remotive,arbeitnow,remoteok", validation_alias=_jobpilot("remote_boards")
    )

    def remote_boards_list(self) -> list[str]:
        return [b.strip() for b in self.remote_boards.split(",") if b.strip()]

    def llm_fallback_models_list(self) -> list[str]:
        return [m.strip() for m in self.llm_fallback_models.split(",") if m.strip()]

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
