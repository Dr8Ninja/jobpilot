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
    #: Aggregator search terms. Each runs against India and against remote.
    #: Forward-deployed and AI roles are here because the user asked for them by
    #: name — they are titles an ATS-board sweep alone rarely surfaces.
    aggregator_queries_raw: str = Field(
        default=(
            "software engineer,backend engineer,python developer,"
            "forward deployed engineer,ai engineer,machine learning engineer,"
            "llm engineer,applied ai engineer,solutions engineer"
        ),
        validation_alias=_jobpilot("aggregator_queries"),
    )

    @property
    def aggregator_queries(self) -> tuple[str, ...]:
        return tuple(q.strip() for q in self.aggregator_queries_raw.split(",") if q.strip())

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

    #: Only postings published within this many days reach the queue. 0 disables
    #: the filter. Rows whose provider exposes no date are excluded when it is on
    #: — an undated posting is unknown-age, not fresh.
    max_posting_age_days: int = Field(
        default=30, validation_alias=_jobpilot("max_posting_age_days")
    )
    #: Roles asking for up to this many years still count as a real opportunity.
    #: Above it — and at staff/principal/director titles — the role is dropped.
    #: This does NOT change what the resume claims — canonical_facts.experience_years
    #: remains the hard honesty ceiling the whitelist gate enforces.
    max_years_required: int = Field(default=8, validation_alias=_jobpilot("max_years_required"))
    #: Roles outside India/remote are kept and shown in their own tab, but they do
    #: not consume the daily tailoring budget unless promoted by hand.
    tailor_overseas: bool = Field(default=False, validation_alias=_jobpilot("tailor_overseas"))

    # "nvidia" (OpenAI-compatible NIM) or "anthropic".
    llm_provider: str = Field(default="nvidia", validation_alias=_jobpilot("llm_provider"))
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    # Defaults chosen by measurement, not by reputation: both answer a strict
    # json_schema request in under 3s on this account. GLM-5.2 and DeepSeek V4 Pro
    # are listed by NVIDIA but never return a token on the free tier — set them
    # here if that changes.
    tailoring_model: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b",
        validation_alias=_jobpilot("tailoring_model"),
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
        default="nvidia/nemotron-3-ultra-550b-a55b,openai/gpt-oss-120b",
        validation_alias=_jobpilot("llm_fallback_models"),
    )
    #: 90s cut `openai/gpt-oss-120b` off mid-answer under load — measured at 54s
    #: idle for a full 8-bullet tailoring, slower when the account is busy. A
    #: truncated call costs the whole attempt, so waiting is cheaper than retrying.
    llm_timeout_seconds: float = Field(
        default=180.0, validation_alias=_jobpilot("llm_timeout_seconds")
    )
    #: Seconds to wait before re-attempting a tailoring call, doubling each time.
    #: Measured: hammering NIM three times in immediate succession failed all
    #: three on 4 of 11 cards, while the same cards succeeded when spaced out.
    #: The provider is intermittent, not broken — waiting is the whole fix.
    llm_retry_backoff_seconds: float = Field(
        default=8.0, validation_alias=_jobpilot("llm_retry_backoff_seconds")
    )
    #: Tailoring keeps its own chain. It was emptied when every fallback answered
    #: with a schema-valid but EMPTY `tailored_bullets` list — but that was the
    #: old prompt, which invited the model to omit bullets. With the explicit
    #: per-role budget in place, both models below return a complete 8/8 answer,
    #: so a fallback is worth having again. The completeness check is what makes
    #: it safe: an empty reply is now retried rather than accepted.
    tailoring_fallback_models: str = Field(
        default="nvidia/nemotron-3-ultra-550b-a55b,openai/gpt-oss-120b",
        validation_alias=_jobpilot("tailoring_fallback_models"),
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
    #: Ceiling on *estimated* tokens per embedding input. The provider's hard
    #: limit is 512; the margin absorbs the estimate being approximate, because a
    #: rejection costs the whole row while a few trimmed characters cost nothing.
    #: 440 still let two batches through at an actual 576, so the margin is wider
    #: than the arithmetic suggests it needs to be. The prefilter only ranks —
    #: the LLM scorer reads the full description later.
    embedding_token_budget: int = Field(
        default=380, validation_alias=_jobpilot("embedding_token_budget")
    )
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

    def tailoring_fallback_models_list(self) -> list[str]:
        return [m.strip() for m in self.tailoring_fallback_models.split(",") if m.strip()]

    # Claude Sonnet 5 runs adaptive thinking by default and max_tokens caps
    # thinking + output together, so these carry headroom above the JSON payload.
    #: Real scoring answers measured at 848-2,297 completion tokens. The ceiling
    #: is what a runaway costs, not what a good answer needs: nemotron pads with
    #: whitespace to whatever ceiling it is given, so 8,000 bought nothing but a
    #: 58-second failure. 3,000 leaves comfortable headroom and caps the waste.
    scoring_max_tokens: int = 3000
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
