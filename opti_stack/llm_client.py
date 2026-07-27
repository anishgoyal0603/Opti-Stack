"""
Thin wrapper around the Gemini API with retry and model fallback.

Pulled out of orchestrator.py so "how we talk to the LLM" is a separate
concern from "what order agents run in" -- either can change without
touching the other.

Retry policy: transient failures (429, 500, 502, 503, 504) are retried on
the same model with exponential backoff and jitter, because they usually
clear on their own. Terminal failures (400, 401, 403, 404) fail immediately
-- a malformed request or a bad key will fail identically on every model, so
walking the whole fallback chain just multiplies latency by the number of
models for no benefit.

The google.genai import is deliberately deferred to __init__/call rather than
done at module scope: orchestrator.py imports this module at the top of the
file, so a module-scope import would make the Gemini SDK a hard dependency of
every unit test that imports orchestrator -- including tests for the security
scanner and synthetic-data helpers that never touch an LLM.
"""

import os
import time
import random

DEFAULT_FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
]

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
TERMINAL_STATUS = {400, 401, 403, 404}

MAX_ATTEMPTS_PER_MODEL = 3
BASE_BACKOFF_SECONDS = 1.0


def _fallback_models():
    """The fallback chain, overridable without a code change via
    OPTISTACK_MODELS="gemini-3.5-flash,gemini-3.1-pro-preview" so a model
    deprecation doesn't require editing source."""
    raw = os.environ.get("OPTISTACK_MODELS")
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(DEFAULT_FALLBACK_MODELS)


class TerminalAPIError(RuntimeError):
    """Raised for errors that will fail the same way on every model, so the
    caller can surface a precise message ('bad API key') instead of the
    generic 'all fallback models exhausted'."""


class GeminiClient:
    """Shared by both the CLI and the Streamlit UI, so they can never
    drift into two different implementations of retry/fallback."""

    def __init__(self, models=None, sleep=time.sleep):
        from google import genai
        self.client = genai.Client()
        self.models = models or _fallback_models()
        self._sleep = sleep  # injectable so tests don't actually wait

    def call(self, role_prompt: str, content: str, on_status=None) -> str:
        from google.genai import errors

        last_error = None
        for model_name in self.models:
            for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
                try:
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=[role_prompt, content],
                    )
                    return response.text
                except errors.APIError as e:
                    last_error = e
                    code = getattr(e, "code", None)

                    if code in TERMINAL_STATUS:
                        raise TerminalAPIError(
                            f"Request rejected by the API ({code}); retrying other "
                            f"models will not help: {e}"
                        ) from e

                    if code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS_PER_MODEL:
                        delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                        delay += random.uniform(0, 0.3 * delay)  # jitter
                        if on_status:
                            on_status(
                                f"Model {model_name} returned {code}; "
                                f"retry {attempt}/{MAX_ATTEMPTS_PER_MODEL - 1} "
                                f"in {delay:.1f}s..."
                            )
                        self._sleep(delay)
                        continue

                    if on_status:
                        on_status(f"Model {model_name} unavailable ({code}). Falling back...")
                    break  # move on to the next model

        raise RuntimeError(f"All fallback models exhausted. Last error: {last_error}")