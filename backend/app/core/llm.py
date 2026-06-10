"""Abstract LLM interface with pluggable providers.

Swap OpenAI for Gemini, Claude, or local models by changing one config value.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


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


def get_llm_provider() -> LLMProvider:
    """Factory — returns the configured LLM provider."""
    settings = get_settings()
    provider_name = settings.llm_provider.lower()

    if provider_name == "openai":
        return OpenAIProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}. Supported: openai")
