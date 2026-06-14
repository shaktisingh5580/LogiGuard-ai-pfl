"""Abstract LLM interface with pluggable providers.

Swap OpenAI for Gemini, Claude, or local models by changing one config value.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from openai import AsyncOpenAI
from tenacity import before_sleep_log, retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import get_settings

logger = logging.getLogger(__name__)

def is_retryable_error(exception: Exception) -> bool:
    """Check if the API error should be retried (e.g., 429, 503, timeouts)."""
    if hasattr(exception, "code"):
        return exception.code in (429, 503, 500, 502, 504)
    msg = str(exception).lower()
    return "timeout" in msg or "unavailable" in msg or "503" in msg or "429" in msg


class LLMResponse:
    """Standardized response from any LLM provider."""

    __slots__ = ("content", "model", "tokens_used", "finish_reason")

    def __init__(
        self,
        content: str,
        model: str,
        tokens_used: int = 0,
        finish_reason: str = "stop",
    ):
        self.content = content
        self.model = model
        self.tokens_used = tokens_used
        self.finish_reason = finish_reason

    def __repr__(self) -> str:
        return f"LLMResponse(model={self.model!r}, tokens={self.tokens_used})"


class EmbeddingResponse:
    """Standardized embedding response."""

    __slots__ = ("embedding", "model", "tokens_used")

    def __init__(self, embedding: list[float], model: str, tokens_used: int = 0):
        self.embedding = embedding
        self.model = model
        self.tokens_used = tokens_used


class LLMProvider(ABC):
    """Abstract interface for LLM providers.

    Implement this to add a new LLM backend (Gemini, Claude, local, etc.)
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> EmbeddingResponse:
        """Generate an embedding vector for the given text."""
        ...

    @abstractmethod
    async def complete_with_vision(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion with image content."""
        ...


class OpenAIProvider(LLMProvider):
    """OpenAI GPT-4o implementation (also supports OpenRouter/compatibles)."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        self._model = model or settings.llm_model
        self._embedding_model = settings.embedding_model

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat completion to OpenAI."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        try:
            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            return LLMResponse(
                content=choice.message.content or "",
                model=response.model,
                tokens_used=response.usage.total_tokens if response.usage else 0,
                finish_reason=choice.finish_reason or "stop",
            )
        except Exception as e:
            logger.error("OpenAI completion failed: %s", e)
            raise

    async def embed(self, text: str) -> EmbeddingResponse:
        """Generate embedding via OpenAI."""
        try:
            response = await self._client.embeddings.create(
                model=self._embedding_model,
                input=text,
            )
            return EmbeddingResponse(
                embedding=response.data[0].embedding,
                model=response.model,
                tokens_used=response.usage.total_tokens if response.usage else 0,
            )
        except Exception as e:
            logger.error("OpenAI embedding failed: %s", e)
            raise

    async def complete_with_vision(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a vision completion to OpenAI (GPT-4o with images)."""
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choice = response.choices[0]
            return LLMResponse(
                content=choice.message.content or "",
                model=response.model,
                tokens_used=response.usage.total_tokens if response.usage else 0,
                finish_reason=choice.finish_reason or "stop",
            )
        except Exception as e:
            logger.error("OpenAI vision completion failed: %s", e)
            raise


# ── Google Gemini Provider ───────────────────────────────────────────────────

class GeminiProvider(LLMProvider):
    """Google Gemini implementation using the official google-genai SDK.

    Uses gemini-2.5-flash for fast extraction/classification and
    text-embedding-004 for vector embeddings.

    The SDK is imported lazily so the app can still boot without
    google-genai installed when LLM_PROVIDER=openai.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
    ):
        from google import genai  # lazy import

        settings = get_settings()
        key = api_key or settings.gemini_api_key
        if not key:
            raise ValueError(
                "GEMINI_API_KEY is not set. "
                "Add it to your .env file or pass it directly."
            )

        self._client = genai.Client(api_key=key)
        self._model = model or settings.gemini_model
        self._embedding_model = embedding_model or settings.gemini_embedding_model

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        retry=retry_if_exception(is_retryable_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat completion to Gemini.

        Converts OpenAI-style messages to Gemini format:
        - "system" messages → system_instruction config parameter
        - "user"/"assistant" messages → contents list
        """
        from google.genai import types
        from google.genai.errors import APIError

        # ── Convert messages ─────────────────────────────────────
        system_parts: list[str] = []
        contents: list[types.Content] = []

        for msg in messages:
            role = msg["role"]
            text = msg["content"]

            if role == "system":
                system_parts.append(text)
            elif role == "assistant":
                contents.append(
                    types.Content(role="model", parts=[types.Part(text=text)])
                )
            else:  # "user" or anything else
                contents.append(
                    types.Content(role="user", parts=[types.Part(text=text)])
                )

        # ── Build config ─────────────────────────────────────────
        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }

        if system_parts:
            config_kwargs["system_instruction"] = "\n\n".join(system_parts)

        # Native JSON mode — guarantees valid JSON, no markdown fences
        if response_format and response_format.get("type") == "json_object":
            config_kwargs["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_kwargs)

        # ── Call the API (async) ─────────────────────────────────
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )

            usage = response.usage_metadata
            return LLMResponse(
                content=response.text or "",
                model=self._model,
                tokens_used=(
                    usage.total_token_count if usage else 0
                ),
                finish_reason="stop",
            )

        except APIError as e:
            if e.code == 429:
                logger.error(
                    "Gemini rate limit (HTTP 429) on %s. "
                    "Check your quota at https://aistudio.google.com/apikey. "
                    "Details: %s",
                    self._model,
                    e,
                )
            else:
                logger.error(
                    "Gemini API error (HTTP %s) on %s: %s",
                    e.code,
                    self._model,
                    e,
                )
            raise

        except Exception as e:
            logger.error("Gemini completion failed: %s", e)
            raise

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        retry=retry_if_exception(is_retryable_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    async def embed(self, text: str) -> EmbeddingResponse:
        """Generate embedding via Gemini."""
        from google.genai import types
        try:
            config = types.EmbedContentConfig(output_dimensionality=768)
            response = await self._client.aio.models.embed_content(
                model=self._embedding_model,
                contents=text,
                config=config,
            )

            embedding = response.embeddings[0].values
            return EmbeddingResponse(
                embedding=list(embedding),
                model=self._embedding_model,
                tokens_used=0,  # Gemini embed API doesn't report token usage
            )

        except Exception as e:
            logger.error("Gemini embedding failed: %s", e)
            raise

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        retry=retry_if_exception(is_retryable_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    async def complete_with_vision(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a vision completion to Gemini.

        Gemini handles multimodal natively — images in the content parts
        are processed alongside text without needing a separate API.
        """
        from google.genai import types
        from google.genai.errors import APIError

        system_parts: list[str] = []
        contents: list[types.Content] = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "system":
                system_parts.append(content if isinstance(content, str) else str(content))
                continue

            gemini_role = "model" if role == "assistant" else "user"

            # Handle multimodal content (list of text + image parts)
            if isinstance(content, list):
                parts: list[types.Part] = []
                for part in content:
                    if part.get("type") == "text":
                        parts.append(types.Part(text=part["text"]))
                    elif part.get("type") == "image_url":
                        # Extract base64 data URI
                        url = part["image_url"]["url"]
                        if url.startswith("data:"):
                            # data:image/png;base64,<base64data>
                            import base64
                            header, b64data = url.split(",", 1)
                            mime = header.split(":")[1].split(";")[0]
                            raw_bytes = base64.b64decode(b64data)
                            parts.append(
                                types.Part.from_bytes(data=raw_bytes, mime_type=mime)
                            )
                        else:
                            parts.append(
                                types.Part.from_uri(file_uri=url, mime_type="image/png")
                            )
                contents.append(types.Content(role=gemini_role, parts=parts))
            else:
                contents.append(
                    types.Content(
                        role=gemini_role,
                        parts=[types.Part(text=content if isinstance(content, str) else str(content))],
                    )
                )

        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system_parts:
            config_kwargs["system_instruction"] = "\n\n".join(system_parts)

        config = types.GenerateContentConfig(**config_kwargs)

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )

            usage = response.usage_metadata
            return LLMResponse(
                content=response.text or "",
                model=self._model,
                tokens_used=usage.total_token_count if usage else 0,
                finish_reason="stop",
            )

        except APIError as e:
            if e.code == 429:
                logger.error("Gemini vision rate limit (429): %s", e)
            else:
                logger.error("Gemini vision API error (%s): %s", e.code, e)
            raise

        except Exception as e:
            logger.error("Gemini vision completion failed: %s", e)
            raise


# ── Factory ──────────────────────────────────────────────────────────────────

def get_llm_provider() -> LLMProvider:
    """Factory — returns the configured LLM provider."""
    settings = get_settings()
    provider_name = settings.llm_provider.lower()

    if provider_name == "openai":
        return OpenAIProvider()
    elif provider_name == "gemini":
        return GeminiProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}. Supported: openai, gemini")
