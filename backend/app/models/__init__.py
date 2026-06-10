"""ORM model registry — import all models so Alembic and Base.metadata see them."""

from app.models.audit import AuditLog, Client
from app.models.cache import ClassificationCache
from app.models.classification import (
    EnsembleVote,
    ExtractionResult,
    RAGResult,
    TransactionState,
)
from app.models.invoice import Invoice, LineItem
from app.models.tariff import (
    DutyRate,
    GatewayFieldMapping,
    HSTariffTree,
    TariffEpoch,
    TariffRule,
)

__all__ = [
    # Invoice
    "Invoice",
    "LineItem",
    # Classification
    "TransactionState",
    "EnsembleVote",
    "RAGResult",
    "ExtractionResult",
    # Tariff
    "TariffEpoch",
    "TariffRule",
    "DutyRate",
    "HSTariffTree",
    "GatewayFieldMapping",
    # Cache
    "ClassificationCache",
    # Audit
    "AuditLog",
    "Client",
]
