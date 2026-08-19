"""OpenAI-compatible LLM client (NVIDIA NIM, and anything else speaking that API).

Implements the same `LLMClient` Protocol as the Anthropic client, so every stage
and the whitelist gate are unchanged — only the transport differs.

Structure comes from `response_format={"type": "json_schema", strict: true}`,
which is the OpenAI-compatible equivalent of `output_config.format`. The response
is then validated against the Pydantic model regardless, because "the endpoint
said strict" is not evidence that the payload actually conforms.

A model that is *listed* by the provider is not necessarily *served* to your
account — several NVIDIA-hosted models accept the request and then never return a
token. `fallback_models` exists for exactly that: a timeout moves to the next
model rather than failing the night's run.
"""

import json
import logging
import time
from typing import Any, TypeVar

from jobpilot_shared.settings import get_settings
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OpenAICompatError(RuntimeError):
    """Every configured model failed for this request."""


class TruncatedCompletion(RuntimeError):
    """The model ran out of output budget before closing its JSON object."""


def _strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON schema acceptable to strict structured-output mode.

    Strict mode requires every object to forbid extra properties and to list all
    of its properties as required. Pydantic marks fields with defaults as
    optional, so we promote them — a model returning the key explicitly (even as
    an empty list) is easier to validate than one that omits it.
    """
    if not isinstance(schema, dict):
        return schema

    if schema.get("type") == "object" or "properties" in schema:
        schema["additionalProperties"] = False
        properties = schema.get("properties", {})
        if properties:
            schema["required"] = list(properties.keys())
        for value in properties.values():
            _strictify(value)

    for key in ("items", "additionalItems"):
        if key in schema:
            _strictify(schema[key])
    for key in ("$defs", "definitions"):
        for value in schema.get(key, {}).values():
            _strictify(value)
    for key in ("anyOf", "oneOf", "allOf"):
        for value in schema.get(key, []):
            _strictify(value)
    return schema


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    return _strictify(model.model_json_schema())


class OpenAICompatLLMClient:
    """Talks to any OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        fallback_models: list[str] | None = None,
        timeout: float | None = None,
        max_validation_retries: int = 2,
    ) -> None:
        from openai import OpenAI

        settings = get_settings()
        key = api_key if api_key is not None else settings.nvidia_api_key
        if not key:
            raise RuntimeError(
                "NVIDIA_API_KEY is not set. Put it in .env, or run with "
                "JOBPILOT_FIXTURE_MODE=1 to use recorded fixtures instead."
            )
        self._timeout = timeout or settings.llm_timeout_seconds
        self._client = OpenAI(
            api_key=key,
            base_url=base_url or settings.nvidia_base_url,
            timeout=self._timeout,
            max_retries=0,  # we own the retry policy, including model fallback
        )
        self._fallbacks = (
            fallback_models
            if fallback_models is not None
            else (settings.llm_fallback_models_list())
        )
        self._max_validation_retries = max_validation_retries

    def _candidates(self, model: str) -> list[str]:
        ordered = [model, *[m for m in self._fallbacks if m != model]]
        return ordered

    def _call(self, model: str, max_tokens: int, system: str, prompt: str, schema: dict) -> str:
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema, "strict": True},
            },
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        if not content.strip():
            raise OpenAICompatError(f"{model} returned an empty body")
        if getattr(choice, "finish_reason", None) == "length":
            # Measured on nemotron-3-super: on roughly one input in five it stops
            # emitting JSON mid-object and pads with whitespace to the token
            # ceiling — 6,713 characters of it in one capture, burning all 8,000
            # tokens and 58 seconds to return an unparseable body. Naming the
            # cause beats letting it surface as a mystery validation error.
            raise TruncatedCompletion(
                f"{model} hit the {max_tokens}-token ceiling before closing the JSON"
            )
        # Some models pad the body with trailing whitespace after valid JSON.
        return content.strip()

    def parse(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        prompt: str,
        output_format: type[T],
    ) -> T:
        schema = json_schema_for(output_format)
        errors: list[str] = []

        for candidate in self._candidates(model):
            for attempt in range(1, self._max_validation_retries + 1):
                started = time.monotonic()
                try:
                    body = self._call(candidate, max_tokens, system, prompt, schema)
                except TruncatedCompletion as exc:
                    errors.append(f"{candidate}: {exc}")
                    # Debug, not warning: this is a handled, transient condition
                    # that the retry below almost always clears — 20 of them in a
                    # 39-job run, none of which cost a verdict. If every attempt
                    # is exhausted the aggregate error is raised and logged by the
                    # caller, so a real failure is still loud.
                    log.debug("%s: %s (attempt %s)", candidate, exc, attempt)
                    continue  # same model, ask again — this is input-specific
                except Exception as exc:  # timeout, 5xx, 404 for an unserved model
                    elapsed = time.monotonic() - started
                    # Keep our own diagnosis ("returned an empty body"); a bare
                    # type name is enough for a timeout but throws away the
                    # reason when we were the one who raised.
                    detail = f"{exc}" if isinstance(exc, OpenAICompatError) else type(exc).__name__
                    errors.append(f"{candidate}: {detail} after {elapsed:.0f}s")
                    log.warning(
                        "LLM call failed on %s after %.0fs (%s); trying next",
                        candidate,
                        elapsed,
                        type(exc).__name__,
                    )
                    break  # a transport failure is the model's problem, not the prompt's

                try:
                    parsed = output_format.model_validate_json(body)
                except (ValidationError, json.JSONDecodeError) as exc:
                    errors.append(f"{candidate}: invalid payload ({type(exc).__name__})")
                    log.warning(
                        "%s returned a payload that failed %s validation (attempt %s)",
                        candidate,
                        output_format.__name__,
                        attempt,
                    )
                    continue  # same model, ask again — this one is worth a retry

                if candidate != model:
                    log.info("Served by fallback model %s (primary %s)", candidate, model)
                return parsed

        raise OpenAICompatError(
            f"No model could produce a valid {output_format.__name__}. Tried: " + "; ".join(errors)
        )
