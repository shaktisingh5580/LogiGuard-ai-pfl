<div align="center">

# LogiGuard AI

### Intelligent Customs Tariff Classification Engine

**6-Layer Deterministic AI Pipeline · Human-in-the-Loop · Audit-Grade Traceability**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-1C3C3C?logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph/)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#license)

---

*LogiGuard AI automates the classification of trade goods into Harmonized System (HS) tariff codes using a 6-layer AI pipeline. It combines OCR extraction, semantic RAG search, deterministic rule engines, LLM-based comparative reasoning, and a human-in-the-loop review queue — all producing an immutable audit trail suitable for customs compliance.*

</div>
---

> 📘 **[Read the Full Technical Documentation (20+ Pages) →](https://github.com/shaktisingh5580/LogiGuard-ai-pfl/blob/main/backend/README.md)**
>
> Covers system architecture diagrams, 6-layer pipeline deep-dive, database ER diagrams, Multi-Path RAG design, SSE event system, data ingestion pipelines, and competitive analysis.
---

## Table of Contents

- [Why LogiGuard AI?](#why-logiguard-ai)
- [Architecture Overview](#architecture-overview)
- [The 6-Layer Pipeline](#the-6-layer-pipeline)
- [Tech Stack & Rationale](#tech-stack--rationale)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
- [Frontend Integration Guide](#frontend-integration-guide)
- [LLM Provider Configuration](#llm-provider-configuration)
- [Deployment](#deployment)
- [Testing](#testing)
- [Contributing](#contributing)
- [License](#license)

---

## Why LogiGuard AI?

Customs tariff misclassification costs global trade **$50B+ annually** in penalties, shipment delays, and overpaid duties. Existing solutions are either:

| Problem | LogiGuard's Solution |
|---|---|
| **Pure-LLM approaches** hallucinate HS codes that don't exist | Deterministic rule engine + RAG retrieval from real tariff schedules — LLMs only *choose* from validated candidates, never invent codes |
| **Manual classification** is slow (15-30 min per item) | AI classifies in <2 seconds; humans only review low-confidence items |
| **Black-box AI** can't satisfy customs auditors | Every classification produces a full audit trail with GRI (General Rules of Interpretation) legal reasoning |
| **Single-model systems** have unpredictable accuracy | Ensemble voting across 3 independent strategies (RAG + Rules + LLM) with weighted confidence scoring |

### Design Philosophy

> **"AI recommends, humans legally sign."**

LogiGuard AI is designed around the legal reality that **a human customs broker must sign every declaration**. The AI pipeline produces a *recommendation* with confidence scoring. High-confidence items are auto-approved; low-confidence items are routed to a human review queue. Every decision — human or AI — is recorded in an immutable audit log.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENT (Frontend / API)                      │
│                  POST /api/classify  ·  POST /api/invoices          │
│                  GET  /api/review/queue  ·  GET /health              │
└─────────────┬───────────────────────────────────────┬───────────────┘
              │              REST / JSON               │
              ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FASTAPI APPLICATION (:8000)                     │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ Upload   │  │ Classify │  │  Review  │  │  Audit   │           │
│  │ Router   │  │ Router   │  │  Router  │  │  Router  │           │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘           │
│       │              │              │              │                 │
│       ▼              ▼              ▼              ▼                 │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │               6-LAYER CLASSIFICATION PIPELINE                │   │
│  │     (LangGraph StateGraph — deterministic + AI hybrid)       │   │
│  │                                                              │   │
│  │  L1: Extract ──→ L2: Structure ──→ L3: Classify             │   │
│  │       │               │                │                     │   │
│  │   OCR/VLM         Validator      RAG + Rules + LLM          │   │
│  │   Tesseract       Pydantic       Ensemble Voting            │   │
│  │                                       │                     │   │
│  │                        L4: Verify ◄───┘                     │   │
│  │                            │                                │   │
│  │                  L5: Route (confidence-based)                │   │
│  │                    ╱              ╲                          │   │
│  │            ≥0.85              <0.85                          │   │
│  │         Auto-approve       Human Review                     │   │
│  │               │                │                            │   │
│  │         L6: Complete      HITL Pause                        │   │
│  │          (Gateway Map)    (REST Resume)                      │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
└───────┬─────────────────────┬──────────────────────┬───────────────┘
        │                     │                      │
        ▼                     ▼                      ▼
  ┌───────────┐        ┌───────────┐          ┌───────────┐
  │ PostgreSQL│        │   Redis   │          │   MinIO   │
  │  + pgvec  │        │  (Cache)  │          │  (S3/Docs)│
  │   :5435   │        │   :6379   │          │   :9000   │
  └───────────┘        └───────────┘          └───────────┘
```

---

## The 6-Layer Pipeline

Each layer is a standalone module that can be tested, replaced, or extended independently.

### Layer 1 — Extraction (`app/extraction/`)

| File | Purpose |
|---|---|
| `ocr.py` | Tesseract + Poppler PDF-to-text extraction with confidence scoring per page |
| `vlm.py` | Vision-Language Model extraction for complex/scanned invoices (GPT-4o vision) |
| `siv.py` | **Structured Invoice Validator** — validates OCR output quality, detects when VLM fallback is needed |

**Why this design?** OCR is fast and cheap but fails on handwritten or complex invoices. The SIV layer acts as a quality gate: if OCR confidence is too low, it automatically triggers VLM extraction. This avoids sending every invoice through expensive VLM calls.

### Layer 2 — Structuring (`app/structuring/`)

| File | Purpose |
|---|---|
| `validator.py` | Normalizes extracted line items into canonical fields (description, quantity, unit, price, origin country). Applies Pydantic validation. |

**Why Pydantic validation?** Raw OCR/VLM output is messy. This layer ensures every downstream module receives clean, typed, validated data — no `"five hundred"` in quantity fields, no missing currencies.

### Layer 3 — Classification (`app/rag/` + `app/rules/`)

Classification uses **three independent strategies** that vote on the correct HS code:

| Strategy | Module | How it Works |
|---|---|---|
| **RAG** | `rag/retriever.py` | Semantic vector search against the tariff rules database using pgvector embeddings. Finds the top-K most relevant tariff headings for a product description. |
| **Rules Engine** | `rules/engine.py` | Deterministic keyword-matching and Section/Chapter Note exclusion rules. Catches classifications that violate tariff legal notes. |
| **LLM Reasoning** | `prompts/templates.py` | GPT-4o comparative classification using GRI (General Rules of Interpretation) legal reasoning with structured JSON output. |

**Why three strategies?** No single approach is reliable for customs classification:
- RAG finds semantically similar headings but can't apply legal exclusion rules.
- Rules catch legal violations but can't handle novel product descriptions.
- LLMs reason well but may hallucinate codes that don't exist.

The ensemble voting layer merges all three and produces a weighted confidence score.

### Layer 4 — Verification (`app/verification/`)

| File | Purpose |
|---|---|
| `ensemble.py` | Weighted ensemble voting across the 3 classification strategies. Computes final HS code + confidence. |
| `confidence.py` | Confidence calibration engine. Cross-checks agreement across strategies. Flags disagreements. |

**Why ensemble verification?** If all three strategies agree → high confidence (auto-approve). If they disagree → low confidence (route to human review). This gives the system a quantifiable "uncertainty" signal.

### Layer 5 — Routing (`app/graph/nodes.py` → `route_node`)

The routing layer makes a binary decision based on confidence thresholds:

| Confidence | Action | Reason |
|---|---|---|
| **≥ 0.85** | Auto-approve → Layer 6 | All strategies agree; classification is legally defensible |
| **0.60 – 0.84** | Route to human review queue | Some disagreement; needs expert judgment |
| **< 0.60** | Flag + route to senior reviewer | Genuinely ambiguous; may need tariff ruling research |

**Why 0.85?** Customs penalty risk is asymmetric — a wrong code can cost 4x the duty in fines. The threshold is intentionally conservative.

### Layer 6 — Completion (`app/completion/`)

| File | Purpose |
|---|---|
| `mapper.py` | Maps the final HS code to gateway-specific output fields |
| `codecs/base.py` | Abstract base class for customs gateway format encoders |
| `codecs/icegate.py` | Indian Customs ICEGATE format encoder |

**Why pluggable gateways?** Different countries have different electronic customs filing systems (ICEGATE for India, ACE for USA, CHIEF for UK). The codec pattern lets you add new gateways by subclassing `BaseGatewayCodec` without touching the pipeline.

---

## Tech Stack & Rationale

| Technology | Version | Why We Chose It |
|---|---|---|
| **Python** | 3.10+ | Industry standard for AI/ML. Async support via `asyncio`. Rich ecosystem for OCR, NLP, and LLM integrations. |
| **FastAPI** | 0.115+ | Fastest Python web framework. Auto-generates OpenAPI docs. Native async. Pydantic validation built-in. |
| **PostgreSQL 16** | + pgvector | ACID-compliant relational DB for audit-grade data. pgvector extension enables vector similarity search for RAG without a separate vector database (reduces infrastructure complexity). |
| **SQLAlchemy 2.0** | async | Best Python ORM. Async mode via asyncpg. Mapped columns with type hints. Relationship lazy-loading. |
| **LangGraph** | 0.2+ | LangChain's graph-based agent framework. Gives us deterministic node-to-node execution with conditional edges and human-in-the-loop `interrupt_before` patterns. Falls back to sequential execution if not installed. |
| **Redis** | 7+ | Classification cache + rate limiting + session store. In-memory = fast. |
| **MinIO** | Latest | S3-compatible object storage for invoice PDFs. Runs locally, drops into AWS S3 in production with zero code changes. |
| **Tesseract OCR** | 5+ | Open-source, battle-tested OCR engine. Supports 100+ languages. |
| **OpenAI / OpenRouter** | GPT-4o | State-of-the-art LLM for classification reasoning. OpenRouter support enables free-tier testing. |
| **Pydantic** | 2.0+ | Data validation at every boundary. JSON Schema generation for frontend contracts. |
| **Docker Compose** | Latest | One-command infrastructure setup. Consistent across dev machines. |

### Why NOT these alternatives?

| Rejected | Reason |
|---|---|
| **Pinecone / Weaviate** (vector DB) | pgvector is "good enough" for 100K-1M tariff rules and avoids a separate service. One fewer thing to deploy, monitor, and pay for. |
| **Django** | Too opinionated for an API-only backend. FastAPI is 3-5x faster and generates better docs. |
| **MongoDB** | Customs data is highly relational (invoices → line items → classifications → audit logs). Document stores make joins painful. |
| **LangChain Agents** | Too unpredictable for compliance software. We need deterministic pipelines, not autonomous agents. LangGraph gives us the graph orchestration without the agent chaos. |
| **Celery** | Overkill for our async needs. FastAPI + Redis handles background tasks natively. |

---

## Project Structure

```
logiguard-ai/
├── .env                          # Local environment variables (git-ignored)
├── .env.example                  # Template for environment variables
├── docker-compose.yml            # PostgreSQL + Redis + MinIO infrastructure
├── README.md                     # This file
│
└── backend/
    ├── pyproject.toml             # Python dependencies & build config
    ├── seed.py                    # Database seeding script
    │
    └── app/
        ├── __init__.py
        ├── main.py                # FastAPI app factory + lifespan
        ├── config.py              # Pydantic Settings (env var loader)
        ├── database.py            # SQLAlchemy async engine + session factory
        │
        ├── api/                   # REST API Layer
        │   ├── router.py          # Root router — mounts all sub-routers
        │   ├── health.py          # GET /health
        │   ├── upload.py          # POST /api/invoices
        │   ├── classify.py        # POST /api/classify
        │   ├── review.py          # GET/POST /api/review/*
        │   └── audit.py           # GET /api/audit/{invoice_id}
        │
        ├── schemas/               # Pydantic request/response models
        │   ├── classification.py  # ClassifyRequest, ClassifyResponse, CandidatePath
        │   ├── invoice.py         # InvoiceResponse
        │   └── review.py          # ApproveRequest, ModifyRequest, RejectRequest
        │
        ├── models/                # SQLAlchemy ORM models (14 tables)
        │   ├── tariff.py          # TariffEpoch, TariffRule, DutyRate, HSTariffTree, GatewayFieldMapping
        │   ├── classification.py  # TransactionState, ClassificationLineItem, EnsembleVote, StrategyCandidate
        │   ├── invoice.py         # Invoice, LineItem, Client
        │   ├── audit.py           # AuditLog, ReviewDecision
        │   └── cache.py           # ClassificationCache
        │
        ├── extraction/            # Layer 1: Document Extraction
        │   ├── ocr.py             # Tesseract OCR engine
        │   ├── vlm.py             # Vision-Language Model fallback
        │   └── siv.py             # Structured Invoice Validator (quality gate)
        │
        ├── structuring/           # Layer 2: Data Structuring
        │   └── validator.py       # Line item normalization + Pydantic validation
        │
        ├── rag/                   # Layer 3a: RAG Retrieval
        │   ├── retriever.py       # pgvector semantic search
        │   ├── cache.py           # Embedding cache (Redis-backed)
        │   └── epoch_router.py    # Routes queries to the correct tariff epoch
        │
        ├── rules/                 # Layer 3b: Deterministic Rules Engine
        │   └── engine.py          # Keyword matching + Section Note exclusions
        │
        ├── verification/          # Layer 4: Ensemble Verification
        │   ├── ensemble.py        # Weighted voting across strategies
        │   └── confidence.py      # Confidence calibration + thresholds
        │
        ├── graph/                 # Layer 5: LangGraph Pipeline Orchestrator
        │   ├── state.py           # PipelineState TypedDict (lightweight ~4KB)
        │   ├── builder.py         # StateGraph builder + sequential fallback
        │   └── nodes.py           # All 6 pipeline node functions
        │
        ├── completion/            # Layer 6: Gateway Completion
        │   ├── mapper.py          # Final HS code → gateway field mapping
        │   └── codecs/
        │       ├── base.py        # Abstract BaseGatewayCodec
        │       └── icegate.py     # Indian Customs ICEGATE format
        │
        ├── prompts/               # LLM Prompt Templates
        │   └── templates.py       # GRI-aware classification prompts (3 templates)
        │
        └── core/                  # Shared Infrastructure
            ├── llm.py             # OpenAI / OpenRouter client wrapper
            ├── storage.py         # S3/MinIO + local filesystem abstraction
            └── events.py          # Server-Sent Events publisher for real-time UI updates
```

---

## Prerequisites

| Tool | Version | Purpose | Installation |
|---|---|---|---|
| **Python** | 3.10+ | Runtime | [python.org/downloads](https://www.python.org/downloads/) |
| **Docker Desktop** | Latest | PostgreSQL, Redis, MinIO | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) |
| **Tesseract OCR** | 5+ | Invoice text extraction | [github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki) (Windows) |
| **Poppler** | Latest | PDF → image conversion | [github.com/oschwartz10612/poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases/) (Windows) |
| **Git** | Latest | Version control | [git-scm.com](https://git-scm.com/) |

> **Note:** Tesseract and Poppler are only required if you plan to process invoice PDFs. The `/api/classify` endpoint works without them (it takes text descriptions directly).

---

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/logiguard-ai.git
cd logiguard-ai
```

### 2. Start Infrastructure

```bash
docker-compose up -d
```

This starts three containers:

| Container | Port | Purpose |
|---|---|---|
| `logiguard-postgres` | `5435` | PostgreSQL 16 + pgvector |
| `logiguard-redis` | `6379` | Redis 7 (caching) |
| `logiguard-minio` | `9000` (API) / `9001` (Console) | S3-compatible object storage |

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set your API key:

```env
# For OpenAI:
OPENAI_API_KEY=sk-your-key-here
LLM_MODEL=gpt-4o

# OR for OpenRouter (free tier for testing):
OPENAI_API_KEY=sk-or-v1-your-openrouter-key
OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=meta-llama/llama-4-maverick:free
```

### 4. Install Python Dependencies

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -e ".[dev]"
```

### 5. Seed the Database

```bash
python seed.py
```

This creates all 14 database tables and inserts sample tariff data (HS codes, duty rates, epoch snapshots).

### 6. Start the Server

```bash
uvicorn app.main:app --reload --port 8000
```

### 7. Verify

Open your browser to:

| URL | Purpose |
|---|---|
| [http://localhost:8000/health](http://localhost:8000/health) | Health check — should return `{"status": "healthy"}` |
| [http://localhost:8000/docs](http://localhost:8000/docs) | Swagger UI — interactive API documentation |
| [http://localhost:8000/redoc](http://localhost:8000/redoc) | ReDoc — alternative API documentation |

### 8. Test a Classification

```bash
curl -X POST http://localhost:8000/api/classify \
  -H "Content-Type: application/json" \
  -d '{"description": "Green tea in packets of 3kg", "jurisdiction": "IN"}'
```

---

## Environment Variables

All configuration is loaded from the `.env` file at the project root. Every variable has a sensible default for local development.

| Variable | Default | Description |
|---|---|---|
| **Database** | | |
| `DATABASE_URL` | `postgresql+asyncpg://logiguard:logiguard_dev@127.0.0.1:5435/logiguard` | Async PostgreSQL connection string |
| **Redis** | | |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string for caching |
| **Object Storage** | | |
| `S3_ENDPOINT_URL` | `http://localhost:9000` | MinIO/S3 endpoint |
| `S3_ACCESS_KEY` | `minioadmin` | S3 access key |
| `S3_SECRET_KEY` | `minioadmin` | S3 secret key |
| `S3_BUCKET_NAME` | `logiguard-documents` | Bucket for invoice storage |
| `S3_REGION` | `us-east-1` | S3 region |
| **LLM** | | |
| `OPENAI_API_KEY` | `sk-change-me` | OpenAI or OpenRouter API key |
| `OPENAI_BASE_URL` | `None` | Override for OpenRouter: `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | `gpt-4o` | Model name (e.g., `gpt-4o`, `meta-llama/llama-4-maverick:free`) |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model for RAG vector search |
| `EMBEDDING_DIMENSIONS` | `1536` | Vector dimensions (must match embedding model) |
| **Application** | | |
| `APP_ENV` | `development` | Environment: `development`, `staging`, `production` |
| `APP_DEBUG` | `true` | Enable debug mode |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins (JSON array or comma-separated) |
| **OCR** | | |
| `TESSERACT_CMD` | `tesseract` | Path to Tesseract executable |
| `POPPLER_PATH` | *(empty)* | Path to Poppler bin directory |

---

## Database Schema

LogiGuard AI uses **14 tables** across 5 domains:

### Entity Relationship Diagram

```
┌──────────────────┐       ┌──────────────────┐
│   TariffEpoch    │       │      Client      │
│   (versioned     │       │   (importers)    │
│    schedule)     │       └────────┬─────────┘
└───────┬──────────┘                │
        │ 1:N                      │ 1:N
        ▼                          ▼
┌──────────────────┐       ┌──────────────────┐
│   TariffRule     │       │     Invoice      │
│  (HS rules +     │       │   (uploaded doc) │
│   embeddings)    │       └────────┬─────────┘
└──────────────────┘                │ 1:N
                                   ▼
┌──────────────────┐       ┌──────────────────┐
│    DutyRate      │       │    LineItem      │
│  (rates per HS)  │       │ (invoice lines)  │
└──────────────────┘       └────────┬─────────┘
                                   │ 1:1
┌──────────────────┐               ▼
│  HSTariffTree    │       ┌──────────────────┐
│ (hierarchical    │       │ TransactionState │
│  HS tree)        │       │ (pipeline state) │
└──────────────────┘       └────────┬─────────┘
                                   │ 1:N
┌──────────────────┐               ▼
│GatewayFieldMap   │       ┌──────────────────┐       ┌──────────────────┐
│ (external field  │       │  EnsembleVote    │       │   AuditLog       │
│  mappings)       │       │ (strategy votes) │       │ (immutable trail)│
└──────────────────┘       └──────────────────┘       └──────────────────┘

┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│Classification    │       │StrategyCandidate │       │ ReviewDecision   │
│   Cache          │       │(per-strategy     │       │ (human verdicts) │
│ (SHA256 lookup)  │       │ candidates)      │       │                  │
└──────────────────┘       └──────────────────┘       └──────────────────┘

┌──────────────────┐
│Classification    │
│   LineItem       │
│(item-level class)│
└──────────────────┘
```

### Key Tables

| Table | Purpose | Key Columns |
|---|---|---|
| `tariff_epochs` | Versioned tariff schedule snapshots | `name`, `version`, `effective_date`, `is_active` |
| `tariff_rules` | HS rules + pgvector embeddings for RAG | `hs_code`, `description`, `embedding` (Vector 1536d), `epoch_id` |
| `duty_rates` | Duty percentages per HS code + country | `hs_code`, `duty_type`, `rate_percent`, `country_code` |
| `hs_tariff_tree` | Hierarchical HS code tree (adjacency list) | `code`, `level`, `parent_id`, `path`, `is_leaf` |
| `invoices` | Uploaded invoice documents | `filename`, `storage_key`, `status`, `client_id` |
| `line_items` | Individual invoice line items | `description`, `quantity`, `unit_price`, `invoice_id` |
| `transaction_states` | Pipeline execution state | `status`, `final_hs_code`, `final_confidence`, `needs_review` |
| `ensemble_votes` | Per-strategy classification votes | `strategy_name`, `hs_code`, `confidence`, `reasoning` |
| `classification_cache` | SHA-256 indexed result cache | `description_hash`, `hs_code`, `confidence`, `embedding` |
| `audit_logs` | Immutable decision audit trail | `action`, `actor`, `before_state`, `after_state` |
| `review_decisions` | Human reviewer verdicts | `decision`, `reviewer`, `corrected_hs_code` |
| `clients` | Importer/client registry | `name`, `code`, `country` |
| `gateway_field_mappings` | External system field translations | `gateway_name`, `external_field`, `internal_field` |
| `classification_line_items` | Item-level classification records | `line_item_id`, `hs_code`, `confidence` |

---

## API Reference

**Base URL:** `http://localhost:8000`

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Application health check + backing service status |

**Response:**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "timestamp": "2026-06-10T22:30:00Z",
  "services": {
    "database": "connected",
    "redis": "connected",
    "storage": "connected"
  }
}
```

---

### Classification

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/classify` | Classify a product description into an HS tariff code |

**Request Body:**
```json
{
  "description": "Green tea in packets of 3 kg",
  "jurisdiction": "IN",
  "force_reclassify": false
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | `string` | ✅ | Product / commodity description |
| `jurisdiction` | `string` | ❌ | Country code (ISO 2-letter) |
| `force_reclassify` | `boolean` | ❌ | Skip cache, force full pipeline re-run |

**Response (`200 OK`):**
```json
{
  "transaction_id": "uuid",
  "description": "Green tea in packets of 3 kg",
  "recommended_hs_code": "0902.10.10",
  "confidence": 0.92,
  "needs_review": false,
  "review_reason": null,
  "candidates": [
    {
      "hs_code": "0902.10.10",
      "description": "Green tea (not fermented) in immediate packings",
      "confidence": 0.92,
      "strategy": "ensemble",
      "reasoning": "GRI Rule 1: Heading 0902 covers tea..."
    }
  ],
  "excluded": [],
  "processing_time_ms": 1250,
  "cache_hit": false,
  "created_at": "2026-06-10T22:30:00Z"
}
```

---

### Invoice Upload

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/invoices` | Upload an invoice document (PDF, PNG, JPEG, TIFF) |

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | `file` | ✅ | Invoice document (max 50MB) |
| `client_id` | `string` | ❌ | Client UUID |

**Accepted MIME types:** `application/pdf`, `image/png`, `image/jpeg`, `image/tiff`

**Response (`201 Created`):**
```json
{
  "id": "uuid",
  "client_id": "uuid or null",
  "filename": "invoice_001.pdf",
  "storage_key": "invoices/{id}/invoice_001.pdf",
  "content_type": "application/pdf",
  "file_size_bytes": 245000,
  "status": "uploaded",
  "created_at": "2026-06-10T22:30:00Z"
}
```

---

### Human Review Queue

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/review/queue` | List items pending human review |
| `GET` | `/api/review/{transaction_id}` | Get a single review item |
| `POST` | `/api/review/{transaction_id}/approve` | Approve classification as-is |
| `POST` | `/api/review/{transaction_id}/modify` | Approve with corrected HS code |
| `POST` | `/api/review/{transaction_id}/reject` | Reject + optionally re-queue |

**GET `/api/review/queue` Query Params:**

| Param | Default | Description |
|---|---|---|
| `limit` | `50` | Max items to return |
| `offset` | `0` | Pagination offset |

**POST `/api/review/{id}/approve`:**
```json
{
  "reviewer": "john.doe@company.com",
  "notes": "Classification looks correct"
}
```

**POST `/api/review/{id}/modify`:**
```json
{
  "reviewer": "john.doe@company.com",
  "corrected_hs_code": "0902.20.10",
  "reason": "Should be black tea, not green tea",
  "notes": "Seller description was misleading"
}
```

**POST `/api/review/{id}/reject`:**
```json
{
  "reviewer": "john.doe@company.com",
  "reason": "Insufficient information to classify",
  "request_reclassify": true
}
```

---

### Audit Trail

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/audit/{invoice_id}` | Full audit trail for an invoice |

**Query Params:**

| Param | Default | Description |
|---|---|---|
| `limit` | `100` | Max entries (1-500) |
| `offset` | `0` | Pagination offset |

**Response:** Array of audit entries with `before_state` / `after_state` snapshots.

---

## Frontend Integration Guide

LogiGuard AI is a **headless API backend** designed to be consumed by any frontend framework. Here's everything you need to integrate:

### Connection Details

| Setting | Value |
|---|---|
| **API Base URL** | `http://localhost:8000` |
| **API Docs** | `http://localhost:8000/docs` (Swagger) |
| **CORS** | All origins allowed by default (`CORS_ORIGINS=["*"]`) |
| **Auth** | None (add JWT/OAuth2 middleware for production) |
| **Content-Type** | `application/json` (except file upload: `multipart/form-data`) |

### Recommended Frontend Stack

| Framework | Why |
|---|---|
| **Next.js 14+** or **React + Vite** | Modern, fast, great DX |
| **TanStack Query** | Server state management, caching, polling |
| **Axios** or **fetch** | HTTP client |

### Key Frontend Pages to Build

| Page | API Calls |
|---|---|
| **Dashboard** | `GET /health`, `GET /api/review/queue` (count pending) |
| **Upload Invoice** | `POST /api/invoices` → then `POST /api/classify` |
| **Quick Classify** | `POST /api/classify` (text description, no file) |
| **Review Queue** | `GET /api/review/queue` → click item → `GET /api/review/{id}` |
| **Review Detail** | `POST .../approve` or `POST .../modify` or `POST .../reject` |
| **Audit Trail** | `GET /api/audit/{invoice_id}` |

### Example: React Hook for Classification

```typescript
// hooks/useClassify.ts
import { useMutation } from '@tanstack/react-query';

interface ClassifyRequest {
  description: string;
  jurisdiction?: string;
  force_reclassify?: boolean;
}

export function useClassify() {
  return useMutation({
    mutationFn: async (data: ClassifyRequest) => {
      const res = await fetch('http://localhost:8000/api/classify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error('Classification failed');
      return res.json();
    },
  });
}
```

### Example: File Upload

```typescript
async function uploadInvoice(file: File, clientId?: string) {
  const formData = new FormData();
  formData.append('file', file);
  if (clientId) formData.append('client_id', clientId);

  const res = await fetch('http://localhost:8000/api/invoices', {
    method: 'POST',
    body: formData, // No Content-Type header — browser sets it with boundary
  });
  return res.json();
}
```

### CORS Configuration (Production)

In production, restrict CORS to your frontend domain:

```env
CORS_ORIGINS=["https://app.logiguard.com","https://dashboard.logiguard.com"]
```

---

## LLM Provider Configuration

LogiGuard AI supports any OpenAI-compatible API. Switch providers by changing environment variables:

### OpenAI (Production)

```env
OPENAI_API_KEY=sk-proj-...
LLM_MODEL=gpt-4o
EMBEDDING_MODEL=text-embedding-3-small
```

### OpenRouter (Free Testing)

```env
OPENAI_API_KEY=sk-or-v1-...
OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=meta-llama/llama-4-maverick:free
```

> **Note:** When using OpenRouter with free models, embedding calls may not be available. The classification pipeline will still work via the Rules Engine + LLM, but RAG vector search requires an embedding model.

### Local LLM (Ollama)

```env
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3:latest
OPENAI_API_KEY=not-needed
```

---

## Deployment

### Docker (Recommended for Production)

Create a `Dockerfile` in `backend/`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Production Checklist

- [ ] Set `APP_ENV=production`
- [ ] Set `APP_DEBUG=false`
- [ ] Set `CORS_ORIGINS` to your frontend domain(s)
- [ ] Use a strong `POSTGRES_PASSWORD`
- [ ] Add JWT / OAuth2 authentication middleware
- [ ] Set up database backups
- [ ] Enable HTTPS via reverse proxy (nginx / Caddy)
- [ ] Configure rate limiting
- [ ] Set `LOG_LEVEL=WARNING` to reduce noise

---

## Testing

### Run Tests

```bash
cd backend
pip install -e ".[dev]"
pytest
```

### Manual API Testing

The fastest way to test is through the Swagger UI at `http://localhost:8000/docs`.

You can also use cURL:

```bash
# Health check
curl http://localhost:8000/health

# Classify a product
curl -X POST http://localhost:8000/api/classify \
  -H "Content-Type: application/json" \
  -d '{"description": "Polyester woven labels for garments"}'

# Upload an invoice
curl -X POST http://localhost:8000/api/invoices \
  -F "file=@invoice.pdf"

# List review queue
curl http://localhost:8000/api/review/queue
```

### Lint & Type Check

```bash
ruff check app/          # Linting
mypy app/                # Type checking
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run tests: `pytest`
5. Run linting: `ruff check app/`
6. Commit with a descriptive message: `git commit -m "feat: add ACE gateway codec"`
7. Push and open a Pull Request

### Adding a New Gateway Codec

1. Create `backend/app/completion/codecs/your_gateway.py`
2. Subclass `BaseGatewayCodec` from `base.py`
3. Implement `encode()` and `decode()` methods
4. Register in `mapper.py`

### Adding New Tariff Rules

1. Add rules to `seed.py` or create a data migration
2. Generate embeddings for new rules (via the embedding model)
3. Insert into the `tariff_rules` table with the correct `epoch_id`

---

## License

This project is proprietary software. All rights reserved.

---

<div align="center">

**Built with precision for the customs compliance industry.**

*LogiGuard AI — Where AI meets legal accountability.*

</div>

