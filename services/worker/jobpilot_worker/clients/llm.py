"""Claude client behind a Protocol.

Structure comes from `output_config.format` via `client.messages.parse()`, which
validates the response against a Pydantic model for us.

Note there is deliberately no `temperature` anywhere in this module: sampling
parameters (`temperature`, `top_p`, `top_k`) are rejected with a 400 on Claude
Sonnet 5. Determinism is bought with a tight schema and a tight prompt instead.

`max_tokens` is sized with headroom because adaptive thinking is on by default on
Sonnet 5 and the cap covers thinking plus visible output together.
"""

from typing import Protocol, TypeVar

from jobpilot_shared.settings import get_settings
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def parse(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        prompt: str,
        output_format: type[T],
    ) -> T:
        """Return a validated instance of `output_format`."""
        ...


class AnthropicLLMClient:
    """Live client. Requires ANTHROPIC_API_KEY."""

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic

        key = api_key if api_key is not None else get_settings().anthropic_api_key
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Set it in .env, or run with "
                "JOBPILOT_FIXTURE_MODE=1 to use recorded fixtures instead."
            )
        self._client = anthropic.Anthropic(api_key=key)

    def parse(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        prompt: str,
        output_format: type[T],
    ) -> T:
        response = self._client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=output_format,
        )
        if response.stop_reason == "refusal":
            raise LLMRefusal(f"Model declined the request: {response.stop_details}")
        parsed = response.parsed_output
        if parsed is None:
            raise LLMParseError(
                f"Model returned no parseable {output_format.__name__} "
                f"(stop_reason={response.stop_reason})."
            )
        return parsed


class LLMRefusal(RuntimeError):
    """Safety classifiers declined the request. Not retryable with the same prompt."""


class LLMParseError(RuntimeError):
    """Response did not validate against the requested schema."""


class FakeLLMClient:
    """Scripted client for tests and fixture mode.

    Responses are queued per output type, so one fake can serve extraction,
    scoring, and tailoring in the same run. Calls are recorded for assertions.
    """

    def __init__(self, responses: dict[type[BaseModel], list[BaseModel]] | None = None) -> None:
        self._responses: dict[type[BaseModel], list[BaseModel]] = {
            k: list(v) for k, v in (responses or {}).items()
        }
        self.calls: list[dict] = []

    def queue(self, *responses: BaseModel) -> None:
        for response in responses:
            self._responses.setdefault(type(response), []).append(response)

    def parse(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        prompt: str,
        output_format: type[T],
    ) -> T:
        self.calls.append(
            {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "prompt": prompt,
                "output_format": output_format,
            }
        )
        queued = self._responses.get(output_format)
        if not queued:
            raise AssertionError(
                f"FakeLLMClient has no queued {output_format.__name__} response "
                f"(call #{len(self.calls)})."
            )
        # The last queued response repeats, so a retry loop can be driven by
        # queueing one bad output followed by one good one.
        return queued.pop(0) if len(queued) > 1 else queued[0]  # type: ignore[return-value]


def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.fixture_mode:
        from ..fixtures import build_fake_llm_client

        return build_fake_llm_client()
    return AnthropicLLMClient()
