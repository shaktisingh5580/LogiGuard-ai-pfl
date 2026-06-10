"""Abstract Gateway Codec interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.completion.mapper import StatutoryFields


class GatewayCodec(ABC):
    """Abstract base for gateway-specific encoding.

    Each customs filing system (ICEGATE, CBP ACE, EU CHIEF) requires
    a different output format. Implement this to add a new gateway.
    """

    @property
    @abstractmethod
    def gateway_name(self) -> str:
        """Human-readable gateway name."""
        ...

    @abstractmethod
    def encode(
        self,
        hs_code: str,
        description: str,
        statutory_fields: StatutoryFields,
        **kwargs,
    ) -> str:
        """Encode classification + statutory fields into gateway format.

        Returns the formatted payload (XML, JSON, etc.)
        """
        ...

    @abstractmethod
    def validate(self, payload: str) -> tuple[bool, list[str]]:
        """Validate the encoded payload against the gateway's schema.

        Returns (is_valid, list_of_errors).
        """
        ...
