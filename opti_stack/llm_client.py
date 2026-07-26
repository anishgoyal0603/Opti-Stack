"""
Thin wrapper around the Gemini API with model-fallback and retry.

Pulled out of orchestrator.py so "how we talk to the LLM" is a separate
concern from "what order agents run in" -- either can change without
touching the other.

The google.genai import is deliberately deferred to __init__ rather than
done at module scope: orchestrator.py imports this module at the top of the
file, so a module-scope import here would make the Gemini SDK a hard
dependency of every unit test that imports orchestrator -- including tests
for the security scanner and synthetic-data helpers that never touch an LLM.
"""

FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview"
]


class GeminiClient:
    """Shared by both the CLI and the Streamlit UI, so they can never
    drift into two different implementations of model fallback/retry."""

    def __init__(self):
        from google import genai
        self.client = genai.Client()

    def call(self, role_prompt: str, content: str, on_status=None) -> str:
        from google.genai import errors

        last_error = None
        for model_name in FALLBACK_MODELS:
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=[role_prompt, content],
                )
                return response.text
            except errors.APIError as e:
                last_error = e
                if on_status:
                    on_status(f"Model {model_name} unavailable ({e.code}). Trying next fallback...")
                continue
        raise RuntimeError(f"All fallback models exhausted. Last error: {last_error}")