"""The LLM the AI endpoints talk to, behind one tiny interface.

Owner: Person 3. Only the owner edits this file.

Everything in app/agent/ calls the model through ``LLM``: a function that takes a
system instruction and a user message and returns plain text. Endpoints get it
with FastAPI's ``Depends(get_llm)``, so tests can swap in a fake that returns
canned answers and never touches the network.

The provider and model come from ``.env`` (``LLM_PROVIDER``, ``LLM_API_KEY``,
``LLM_MODEL``). Only Gemini is wired today; adding Ollama later only means
adding a branch in ``_build_chat_model``.
"""

from collections.abc import Callable
from functools import lru_cache

from app.config import get_settings

LLM = Callable[[str, str], str]
TIMEOUT_S = 30


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM is configured or the provider call fails."""


def _text(content) -> str:
    """Gemini may answer with a string or with a list of parts; keep only the text."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
    return "".join(parts)


def _build_chat_model():
    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        # Imported here so the app and its tests start even without the package.
        from langchain_google_genai import ChatGoogleGenerativeAI  # pylint: disable=import-outside-toplevel

        if not settings.llm_api_key.get_secret_value() or not settings.llm_model:
            raise LLMUnavailableError("Set LLM_API_KEY and LLM_MODEL in .env")
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            google_api_key=settings.llm_api_key.get_secret_value(),
            temperature=0,  # same question, same answer: easier to test and to demo
            timeout=TIMEOUT_S,
            max_retries=2,
        )
    raise LLMUnavailableError(f"LLM_PROVIDER '{settings.llm_provider}' is not supported (use 'gemini')")


@lru_cache
def _chat_model():
    return _build_chat_model()


def get_llm() -> LLM:
    """FastAPI dependency: return a function (system, user) -> text."""

    def call(system: str, user: str) -> str:
        try:
            reply = _chat_model().invoke([("system", system), ("human", user)])
        except LLMUnavailableError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Network errors, quota exceeded, bad key... all mean "the LLM is unavailable".
            raise LLMUnavailableError(f"The LLM provider failed: {type(exc).__name__}") from exc
        return _text(reply.content).strip()

    return call
