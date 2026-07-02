"""
Thin wrapper around the Gemini API with model-fallback and retry.

Pulled out of orchestrator.py so "how we talk to the LLM" is a separate
concern from "what order agents run in" -- either can change without
touching the other.
"""

from google import genai
from google.genai import errors

FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview"
]


class GeminiClient:
    """Shared by both the CLI and the Streamlit UI, so they can never
    drift into two different implementations of model fallback/retry."""

    def __init__(self):
        self.client = genai.Client()

    def call(self, role_prompt: str, content: str, on_status=None) -> str:
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
