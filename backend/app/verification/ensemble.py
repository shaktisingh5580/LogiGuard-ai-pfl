"""Adaptive Ensemble — dynamic N-way LLM verification.

Not every item needs 5 LLM calls. Simple commodities get N=1-2,
ambiguous items get N=4-5. Combined with semantic cache, this
reduces LLM cost by 60-70%.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.llm import LLMProvider, get_llm_provider
from app.prompts.templates import ENSEMBLE_CLASSIFICATION_PROMPT
from app.rag.retriever import CandidatePath

logger = logging.getLogger(__name__)


# Known restricted commodities (SCOMET list, dual-use items, etc.)
RESTRICTED_KEYWORDS = {
    "nuclear", "uranium", "plutonium", "centrifuge", "missile",
    "encryption", "cryptographic", "biological agent", "chemical weapon",
    "nerve agent", "explosive", "detonator", "precursor",
}


@dataclass
class SingleVote:
    """A single ensemble member's classification vote."""
    vote_index: int
    predicted_code: str
    confidence: float
    reasoning: str
    model_name: str
    tokens_used: int
    latency_ms: int


@dataclass
class EnsembleResult:
    """Aggregated result from the adaptive ensemble."""
    predicted_code: str
    confidence: float
    votes: list[SingleVote]
    agreement_ratio: float
    ensemble_size: int
    is_cache_hit: bool = False

    @property
    def is_unanimous(self) -> bool:
        return self.agreement_ratio == 1.0

    @property
    def majority_code(self) -> str:
        """Most voted code."""
        from collections import Counter
        codes = [v.predicted_code for v in self.votes]
        if not codes:
            return self.predicted_code
        return Counter(codes).most_common(1)[0][0]


class AdaptiveEnsemble:
    """Runs 1-5 parallel LLM calls depending on classification difficulty.

    The ensemble size is determined by:
    - Cache hit → N=1 (just verify)
    - Common commodity → N=2
    - Default → N=3
    - Ambiguous (multiple chapters) → N=4
    - Restricted goods → N=5 (maximum verification)
    """

    def __init__(self, llm: LLMProvider | None = None):
        self._llm = llm or get_llm_provider()

    def determine_n(
        self,
        description: str,
        candidate_count: int = 0,
        is_cache_hit: bool = False,
        is_restricted: bool = False,
    ) -> int:
        """Dynamically determine ensemble size based on expected difficulty."""
        if is_cache_hit:
            return 1

        if is_restricted or self._is_restricted(description):
            return 5

        if candidate_count >= 4:
            return 4

        if self._is_common_commodity(description):
            return 2

        return 3

    async def verify(
        self,
        description: str,
        candidates: list[CandidatePath],
        n: int,
    ) -> EnsembleResult:
        """Run N parallel LLM classification calls and aggregate votes.

        Args:
            description: Commodity description.
            candidates: Filtered candidates from rule engine.
            n: Ensemble size (1-5).

        Returns:
            EnsembleResult with majority vote and confidence.
        """
        # Build the context for each ensemble member
        candidates_context = "\n\n".join(
            f"--- Candidate {i+1} ---\n{c.to_prompt_context()}"
            for i, c in enumerate(candidates)
        )

        # Run N parallel calls
        tasks = [
            self._single_classify(description, candidates_context, vote_idx=i)
            for i in range(n)
        ]
        votes: list[SingleVote] = await asyncio.gather(*tasks)

        # Aggregate — majority voting
        return self._aggregate(votes, n)

    async def _single_classify(
        self,
        description: str,
        candidates_context: str,
        vote_idx: int,
    ) -> SingleVote:
        """Run a single LLM classification call."""
        messages = [
            {"role": "system", "content": ENSEMBLE_CLASSIFICATION_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Classify this commodity:\n\n"
                    f"DESCRIPTION: {description}\n\n"
                    f"CANDIDATES:\n{candidates_context}\n\n"
                    f"Return your answer as JSON: "
                    f'{{"code": "XXXX.XX.XX", "confidence": 0.XX, "reasoning": "..."}}'
                ),
            },
        ]

        start = time.monotonic()
        try:
            response = await self._llm.complete(
                messages,
                temperature=0.1 + (vote_idx * 0.05),  # Slight temp variation for diversity
                max_tokens=1024,
            )
            latency = int((time.monotonic() - start) * 1000)

            # Parse the JSON response
            parsed = self._parse_response(response.content)
            return SingleVote(
                vote_index=vote_idx,
                predicted_code=parsed.get("code", "UNKNOWN"),
                confidence=float(parsed.get("confidence", 0.5)),
                reasoning=parsed.get("reasoning", ""),
                model_name=response.model,
                tokens_used=response.tokens_used,
                latency_ms=latency,
            )
        except Exception as e:
            logger.error("Ensemble vote %d failed: %s", vote_idx, e)
            return SingleVote(
                vote_index=vote_idx,
                predicted_code="ERROR",
                confidence=0.0,
                reasoning=f"LLM call failed: {e}",
                model_name="error",
                tokens_used=0,
                latency_ms=int((time.monotonic() - start) * 1000),
            )

    def _aggregate(self, votes: list[SingleVote], n: int) -> EnsembleResult:
        """Aggregate votes using majority voting + confidence averaging."""
        from collections import Counter

        valid_votes = [v for v in votes if v.predicted_code != "ERROR"]
        if not valid_votes:
            return EnsembleResult(
                predicted_code="UNKNOWN",
                confidence=0.0,
                votes=votes,
                agreement_ratio=0.0,
                ensemble_size=n,
            )

        # Majority vote
        code_counts = Counter(v.predicted_code for v in valid_votes)
        majority_code, majority_count = code_counts.most_common(1)[0]

        # Agreement ratio = how many agree / total valid votes
        agreement_ratio = majority_count / len(valid_votes)

        # Confidence = average confidence of majority voters
        majority_confidences = [
            v.confidence for v in valid_votes if v.predicted_code == majority_code
        ]
        avg_confidence = sum(majority_confidences) / len(majority_confidences)

        # Adjust confidence by agreement ratio
        # Split votes → lower confidence regardless of individual scores
        adjusted_confidence = avg_confidence * agreement_ratio

        return EnsembleResult(
            predicted_code=majority_code,
            confidence=round(adjusted_confidence, 4),
            votes=votes,
            agreement_ratio=round(agreement_ratio, 4),
            ensemble_size=n,
        )

    @staticmethod
    def _parse_response(content: str) -> dict[str, Any]:
        """Parse LLM JSON response, handling markdown code blocks."""
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            import re
            match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            return {"code": "PARSE_ERROR", "confidence": 0.0, "reasoning": content}

    @staticmethod
    def _is_restricted(description: str) -> bool:
        desc_lower = description.lower()
        return any(kw in desc_lower for kw in RESTRICTED_KEYWORDS)

    @staticmethod
    def _is_common_commodity(description: str) -> bool:
        common = {
            "raw cotton", "white rice", "diesel fuel", "crude oil",
            "wheat", "sugar", "cement", "steel bars", "copper wire",
        }
        desc_lower = description.lower()
        return any(c in desc_lower for c in common)
