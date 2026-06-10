# LogiGuard AI — Frontend Dashboard Developer Guide

> **Audience:** Frontend developer building the React/Next.js dashboard.
> **Last Updated:** 2026-06-10
> **Backend Version:** 0.1.0

---

## Table of Contents

1. [Quick Reference](#1-quick-reference)
2. [Backend Connection Setup](#2-backend-connection-setup)
3. [Authentication (Current & Future)](#3-authentication-current--future)
4. [Complete API Route Map](#4-complete-api-route-map)
5. [Page-by-Page Implementation Guide](#5-page-by-page-implementation-guide)
   - [Dashboard / Home](#51-dashboard--home)
   - [Quick Classify](#52-quick-classify)
   - [Invoice Upload](#53-invoice-upload)
   - [Review Queue](#54-review-queue)
   - [Review Detail](#55-review-detail--action)
   - [Audit Trail](#56-audit-trail)
6. [Real-Time Events (SSE)](#6-real-time-events-sse)
7. [Full API Reference with TypeScript Types](#7-full-api-reference-with-typescript-types)
8. [Error Handling](#8-error-handling)
9. [State Management Recommendations](#9-state-management-recommendations)
10. [UI/UX Guidelines](#10-uiux-guidelines)
11. [Environment Configuration](#11-environment-configuration)

---

## 1. Quick Reference

| Item | Value |
|---|---|
| **Backend Base URL** | `http://localhost:8000` |
| **Swagger UI (interactive docs)** | `http://localhost:8000/docs` |
| **ReDoc (read-only docs)** | `http://localhost:8000/redoc` |
| **OpenAPI JSON Schema** | `http://localhost:8000/openapi.json` |
| **Default Port** | `8000` |
| **CORS** | All origins allowed (`*`) in dev |
| **Content-Type** | `application/json` (except file uploads: `multipart/form-data`) |
| **Auth** | None currently (see Section 3) |
| **SSE Events Stream** | `GET /api/events` |
| **Max Upload Size** | 50 MB |
| **Accepted File Types** | PDF, PNG, JPEG, TIFF |

---

## 2. Backend Connection Setup

### Axios Setup (Recommended)

```typescript
// lib/api.ts
import axios from 'axios';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000, // 60s — classification can take time with LLM calls
  headers: {
    'Content-Type': 'application/json',
  },
});

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 422) {
      // Validation error — the backend returns detailed field errors
      console.error('Validation Error:', error.response.data.detail);
    }
    return Promise.reject(error);
  }
);
```

### Fetch Setup (Alternative)

```typescript
// lib/api.ts
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `API Error: ${res.status}`);
  }
  return res.json();
}
```

---

## 3. Authentication (Current & Future)

### Current State
**No authentication is required.** All endpoints are publicly accessible. This is intentional for development.

### Future Implementation
When you add auth, the backend is pre-configured with CORS middleware that supports credentials:

```python
# Already in backend/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,   # ← ready for cookies/tokens
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Recommended Auth Flow for Production:**
- Use **JWT Bearer tokens** in the `Authorization` header
- Store the reviewer's email/name in the token claims
- The `reviewer` field in review actions will be extracted from the JWT instead of the request body

---

## 4. Complete API Route Map

Here is every endpoint the backend exposes. Green rows are the ones your dashboard will use most frequently.

| Method | Path | Tag | Purpose | Priority |
|---|---|---|---|---|
| `GET` | `/health` | Health | System health check | 🟢 High |
| `POST` | `/api/classify` | Classification | **Classify a product description** | 🟢 High |
| `POST` | `/api/invoices` | Invoices | **Upload an invoice PDF** | 🟢 High |
| `GET` | `/api/review/queue` | Review | **List pending review items** | 🟢 High |
| `GET` | `/api/review/{transaction_id}` | Review | **Get single review item** | 🟢 High |
| `POST` | `/api/review/{transaction_id}/approve` | Review | **Approve classification** | 🟢 High |
| `POST` | `/api/review/{transaction_id}/modify` | Review | **Approve with correction** | 🟢 High |
| `POST` | `/api/review/{transaction_id}/reject` | Review | **Reject classification** | 🟢 High |
| `GET` | `/api/audit/{invoice_id}` | Audit | Get full audit trail for an invoice | 🟡 Medium |
| `GET` | `/api/events` | Events | **SSE stream for real-time updates** | 🟡 Medium |

---

## 5. Page-by-Page Implementation Guide

### 5.1 Dashboard / Home

**Purpose:** Overview page showing system health and pending review count.

**API Calls:**
```
GET /health
GET /api/review/queue?limit=1   → use total count for badge
```

**UI Elements to Build:**

| Element | Data Source | Notes |
|---|---|---|
| System Status Card | `GET /health` → `services` object | Show green/red indicators for database, redis, storage |
| Pending Reviews Badge | `GET /api/review/queue` → array length | Show as notification badge on sidebar |
| Quick Classify Input | — | Text input + button → navigates to Quick Classify page |
| Upload Button | — | Opens file picker → navigates to Upload page |
| Recent Activity | `GET /api/review/queue?limit=5` | Show last 5 classifications |

**Example Health Response:**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "timestamp": "2026-06-10T17:00:00Z",
  "services": {
    "database": "connected",
    "redis": "connected",
    "storage": "connected"
  }
}
```

---

### 5.2 Quick Classify

**Purpose:** Paste a product description, get an instant HS code classification.

**This is the most important page.** It demonstrates the full AI pipeline.

**API Call:**
```
POST /api/classify
```

**Request Body:**
```typescript
interface ClassifyRequest {
  description: string;          // REQUIRED — min 3 chars, max 4000
  country_of_origin?: string;   // Optional — ISO 2-letter code (e.g., "CN", "IN", "US")
  additional_context?: string;  // Optional — material, intended use, etc.
  client_id?: string;           // Optional — UUID of a registered client
  force_reclassify?: boolean;   // Optional — bypass cache, default false
}
```

**Response Body:**
```typescript
interface ClassifyResponse {
  transaction_id: string;           // UUID — save this for review/audit
  invoice_id: string | null;
  description: string;
  recommended_hs_code: string;      // e.g., "0902.10.10"
  confidence: number;               // 0.0 to 1.0
  needs_review: boolean;            // true if confidence < 0.85
  review_reason: string | null;     // why it needs review
  candidates: CandidatePath[];      // all candidate codes considered
  excluded: ExcludedCandidate[];    // codes rejected by rule engine
  duty_rate_percent: number | null;
  processing_time_ms: number | null;
  cache_hit: boolean;               // true if result came from cache
  metadata: Record<string, any> | null;
  created_at: string;               // ISO timestamp
}

interface CandidatePath {
  hs_code: string;
  description: string;
  confidence: number;
  strategy: string;              // "ensemble_llm", "cache", "rag", etc.
  reasoning: string | null;      // LLM's legal reasoning (GRI rules cited)
  duty_rate_percent: number | null;
  supporting_rules: string[];
}

interface ExcludedCandidate {
  hs_code: string;
  description: string;
  exclusion_reason: string;      // why this code was rejected
  confidence: number;
}
```

**UI Elements to Build:**

| Element | Details |
|---|---|
| **Description Input** | `<textarea>` — min 3 chars. Placeholder: "Enter product description (e.g., Green tea in packets of 3 kg)" |
| **Country Selector** | Dropdown of ISO country codes. Default: "IN" (India) |
| **Additional Context** | Optional textarea for material composition, use case |
| **Classify Button** | Triggers `POST /api/classify`. Show loading spinner (LLM takes 2-5 seconds) |
| **Force Reclassify Toggle** | Checkbox — bypasses cache |
| **Result Card** | Show `recommended_hs_code` prominently with `confidence` as a progress bar |
| **Confidence Badge** | Green (≥85%), Yellow (60-84%), Red (<60%) |
| **Needs Review Banner** | If `needs_review: true`, show warning: "This classification needs human review" |
| **Reasoning Panel** | Expandable panel showing the LLM's legal reasoning text |
| **Candidates Table** | Show all `candidates[]` with code, confidence, strategy |
| **Excluded Table** | Show `excluded[]` with code and exclusion reason (collapsible) |
| **Timing Badge** | Show `processing_time_ms` and `cache_hit` status |

**Key UX Considerations:**
- The LLM call takes **2-8 seconds**. Show a skeleton loader or animated progress bar.
- If `cache_hit: true`, show a "⚡ Cached Result" badge (response will be <100ms).
- The `reasoning` field contains the LLM's legal analysis — this is the most valuable part for customs officers. Display it prominently.

**Example Request:**
```javascript
const response = await api.post('/api/classify', {
  description: 'Green tea in packets of 3 kg',
  country_of_origin: 'IN',
});
```

---

### 5.3 Invoice Upload

**Purpose:** Upload a PDF/image invoice for processing.

**API Call:**
```
POST /api/invoices       (multipart/form-data)
```

**⚠️ IMPORTANT:** Do NOT set `Content-Type` header manually. The browser auto-sets it with the correct multipart boundary.

**Request:**
```typescript
// Use FormData — NOT JSON
const formData = new FormData();
formData.append('file', file);                         // REQUIRED — File object
formData.append('client_id', '550e8400-...');          // OPTIONAL — UUID string
```

**Accepted File Types:**
| MIME Type | Extension |
|---|---|
| `application/pdf` | `.pdf` |
| `image/png` | `.png` |
| `image/jpeg` | `.jpg`, `.jpeg` |
| `image/tiff` | `.tif`, `.tiff` |

**Max Size:** 50 MB

**Response (201 Created):**
```typescript
interface InvoiceResponse {
  id: string;                    // UUID — save this! You need it for everything else
  client_id: string | null;
  filename: string;
  storage_key: string;           // Internal path (e.g., "invoices/{id}/invoice.pdf")
  content_type: string;
  file_size_bytes: number;
  status: string;                // "uploaded"
  raw_text: string | null;
  metadata: Record<string, any> | null;
  created_at: string;
  updated_at: string;
  line_items: LineItemResponse[];
}
```

**Upload Component Example:**
```tsx
function InvoiceUpload() {
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState(null);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Validate on frontend before sending
    const allowedTypes = ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff'];
    if (!allowedTypes.includes(file.type)) {
      alert('Please upload a PDF or image file');
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      alert('File is too large (max 50 MB)');
      return;
    }

    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch('http://localhost:8000/api/invoices', {
        method: 'POST',
        body: formData,  // ← NO Content-Type header!
      });
      const data = await res.json();
      setResult(data);
    } catch (err) {
      console.error('Upload failed:', err);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <input type="file" accept=".pdf,.png,.jpg,.jpeg,.tiff" onChange={handleUpload} />
      {uploading && <p>Uploading...</p>}
      {result && <p>Invoice ID: {result.id}</p>}
    </div>
  );
}
```

**After Upload — What Happens Next?**
The upload endpoint only saves the file and creates a database record. To actually classify the invoice's contents, you have two options:

1. **Manual trigger:** After upload, let the user click "Classify" to run the pipeline
2. **Auto-trigger:** Immediately call `POST /api/classify` with each extracted line item description

Currently, the text classification endpoint (`POST /api/classify`) works with product descriptions. The full invoice-to-classification pipeline (OCR → Structure → Classify) is available via the LangGraph pipeline but should be triggered server-side.

---

### 5.4 Review Queue

**Purpose:** Show all classifications that need human review (confidence < 85%).

**API Call:**
```
GET /api/review/queue?limit=50&offset=0
```

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | `integer` | `50` | Max items to return (1-500) |
| `offset` | `integer` | `0` | Pagination offset |

**Response:** `ReviewQueueItem[]`
```typescript
interface ReviewQueueItem {
  transaction_id: string;        // UUID — use this for all actions
  invoice_id: string | null;
  description: string;           // Product description
  recommended_hs_code: string;   // AI's recommendation
  confidence: number;            // 0.0 to 1.0
  review_reason: string | null;  // Why it needs review
  candidates: CandidatePath[];   // All candidate codes
  status: string;                // "pending" | "pending_review"
  created_at: string;
  client_name: string | null;
}
```

**UI Elements to Build:**

| Element | Details |
|---|---|
| **Queue Table** | Sortable table with columns: Description, AI Code, Confidence, Reason, Created |
| **Confidence Column** | Color-coded: Yellow (60-84%), Red (<60%) |
| **Row Click** | Navigate to Review Detail page for that `transaction_id` |
| **Pagination** | Previous/Next buttons using `limit` and `offset` |
| **Empty State** | Show "No items pending review" with a checkmark icon |
| **Auto-refresh** | Poll every 30 seconds OR use SSE events (see Section 6) |
| **Count Badge** | Show total count in sidebar/navbar |

**Polling Example:**
```typescript
// hooks/useReviewQueue.ts
import { useQuery } from '@tanstack/react-query';

export function useReviewQueue(limit = 50, offset = 0) {
  return useQuery({
    queryKey: ['review-queue', limit, offset],
    queryFn: () => api.get(`/api/review/queue?limit=${limit}&offset=${offset}`).then(r => r.data),
    refetchInterval: 30000, // Auto-refresh every 30 seconds
  });
}
```

---

### 5.5 Review Detail + Action

**Purpose:** View classification details and take action (approve/modify/reject).

**API Calls:**
```
GET  /api/review/{transaction_id}           → get details
POST /api/review/{transaction_id}/approve   → approve as-is
POST /api/review/{transaction_id}/modify    → approve with correction
POST /api/review/{transaction_id}/reject    → reject
```

#### Approve Request
```typescript
interface ApproveRequest {
  reviewer: string;    // REQUIRED — email or name of the reviewer
  notes?: string;      // Optional — up to 2000 chars
}
```

#### Modify Request
```typescript
interface ModifyRequest {
  reviewer: string;            // REQUIRED
  corrected_hs_code: string;   // REQUIRED — the correct HS code (4-16 chars)
  reason: string;              // REQUIRED — why the AI was wrong (3-2000 chars)
  notes?: string;              // Optional
}
```

#### Reject Request
```typescript
interface RejectRequest {
  reviewer: string;            // REQUIRED
  reason: string;              // REQUIRED — why it's being rejected
  request_reclassify?: boolean; // Default: true — re-queue for AI processing
}
```

**All three actions return:** `ReviewQueueItem` (updated state)

**Error Responses:**
| Status | Meaning | When |
|---|---|---|
| `404` | Transaction not found | Invalid `transaction_id` |
| `409` | Conflict | Transaction already reviewed (not in `pending`/`pending_review` status) |

**UI Elements to Build:**

| Element | Details |
|---|---|
| **Product Description** | Large text display of the product description |
| **AI Recommendation Card** | Show `recommended_hs_code` + `confidence` prominently |
| **Confidence Gauge** | Visual gauge/meter showing 0-100% |
| **Review Reason** | Why the AI flagged this for review |
| **Candidates Table** | All `candidates[]` with code, confidence, strategy, reasoning |
| **AI Reasoning Panel** | Expandable — show the LLM's legal reasoning text |
| **Approve Button** | Green button → opens modal for reviewer name + optional notes |
| **Modify Button** | Yellow button → opens modal for corrected code + reason + reviewer |
| **Reject Button** | Red button → opens modal for reason + re-classify toggle + reviewer |
| **HS Code Input (Modify)** | Text input with format hint: `XXXX.XX.XX` |

**Action Flow Example:**
```typescript
async function approveClassification(transactionId: string, reviewer: string) {
  const response = await api.post(`/api/review/${transactionId}/approve`, {
    reviewer: reviewer,
    notes: 'Verified against tariff schedule. Classification is correct.',
  });
  return response.data; // Updated ReviewQueueItem
}

async function modifyClassification(transactionId: string) {
  const response = await api.post(`/api/review/${transactionId}/modify`, {
    reviewer: 'john.doe@company.com',
    corrected_hs_code: '0902.20.10',
    reason: 'Product is black tea, not green tea. Heading 0902.20 is correct.',
    notes: 'Seller used misleading description on invoice.',
  });
  return response.data;
}

async function rejectClassification(transactionId: string) {
  const response = await api.post(`/api/review/${transactionId}/reject`, {
    reviewer: 'john.doe@company.com',
    reason: 'Description is too vague to classify. Need sample or specification sheet.',
    request_reclassify: false, // Don't retry — needs more info
  });
  return response.data;
}
```

---

### 5.6 Audit Trail

**Purpose:** Show the full decision history for an invoice (compliance/legal requirement).

**API Call:**
```
GET /api/audit/{invoice_id}?limit=100&offset=0
```

**Response:** `AuditLogResponse[]`
```typescript
interface AuditLogResponse {
  id: string;                       // UUID
  invoice_id: string | null;
  client_id: string | null;
  action: string;                   // "approve", "modify", "reject"
  actor: string | null;             // Who performed the action
  entity_type: string | null;       // "transaction_state"
  entity_id: string | null;
  before_state: Record<string, any> | null;  // State BEFORE the action
  after_state: Record<string, any> | null;   // State AFTER the action
  details: string | null;           // Additional notes
  ip_address: string | null;
  created_at: string;
}
```

**UI Elements to Build:**

| Element | Details |
|---|---|
| **Timeline View** | Vertical timeline showing each audit event chronologically |
| **Before/After Diff** | Show `before_state` vs `after_state` as a visual diff |
| **Actor Badge** | Show who performed each action (system or human name) |
| **Action Icon** | ✅ Approve (green), ✏️ Modify (yellow), ❌ Reject (red) |
| **Timestamp** | Show relative time ("2 hours ago") + absolute time on hover |
| **Export Button** | Allow exporting audit trail as PDF/CSV for compliance officers |

---

## 6. Real-Time Events (SSE)

The backend publishes real-time events as the AI pipeline processes invoices. Your frontend can subscribe to these for live progress updates.

### How to Connect

```typescript
// hooks/useSSE.ts
import { useEffect, useState } from 'react';

interface PipelineEvent {
  event: string;
  invoice_id: string;
  line_item_id: string | null;
  data: Record<string, any>;
  timestamp: string;
}

export function usePipelineEvents(invoiceId?: string) {
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const url = invoiceId
      ? `http://localhost:8000/api/events?invoice_id=${invoiceId}`
      : 'http://localhost:8000/api/events';

    const es = new EventSource(url);

    es.addEventListener('connected', () => {
      setConnected(true);
    });

    // Listen to all pipeline event types
    const eventTypes = [
      'invoice.uploaded',
      'extraction.started',
      'extraction.completed',
      'siv.violation',
      'classification.started',
      'classification.completed',
      'verification.completed',
      'review.required',
      'review.completed',
      'completion.done',
      'pipeline.error',
    ];

    eventTypes.forEach((type) => {
      es.addEventListener(type, (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        setEvents((prev) => [...prev, data]);
      });
    });

    es.onerror = () => {
      setConnected(false);
    };

    return () => {
      es.close();
    };
  }, [invoiceId]);

  return { events, connected };
}
```

### Event Types Reference

| Event | When It Fires | `data` Payload |
|---|---|---|
| `invoice.uploaded` | File upload complete | `{ filename, size }` |
| `extraction.started` | OCR begins | `{ phase: "OCR + Layout Parsing" }` |
| `extraction.completed` | OCR finished | `{ needs_vlm: boolean }` |
| `siv.violation` | Quality issue detected in OCR | `{ violations: [...] }` |
| `classification.started` | AI classification begins | `{ total_items: number }` |
| `classification.completed` | All items classified | `{ processed: number }` |
| `verification.completed` | Ensemble verification done | `{}` |
| `review.required` | Items routed to human review | `{ paused_items: n, auto_approved: n }` |
| `review.completed` | Human review action taken | `{ action, reviewer }` |
| `completion.done` | Pipeline fully complete | `{}` |
| `pipeline.error` | Something went wrong | `{ error: string }` |

### Progress Bar Mapping

Use the events to build a visual pipeline progress indicator:

```
Step 1: Extracting    ← extraction.started
Step 2: Structuring   ← extraction.completed
Step 3: Classifying   ← classification.started
Step 4: Verifying     ← classification.completed + verification.completed
Step 5: Routing       ← review.required OR completion.done
Step 6: Complete      ← completion.done
```

---

## 7. Full API Reference with TypeScript Types

Copy this entire block into your project as `types/api.ts`:

```typescript
// ═══════════════════════════════════════════════════════════════
// types/api.ts — LogiGuard AI Backend Type Definitions
// Auto-generate from: http://localhost:8000/openapi.json
// ═══════════════════════════════════════════════════════════════

// ── Health ────────────────────────────────────────────────────
export interface HealthResponse {
  status: string;
  version: string;
  timestamp: string;
  services: Record<string, string>;
}

// ── Classification ───────────────────────────────────────────
export interface ClassifyRequest {
  description: string;
  country_of_origin?: string | null;
  additional_context?: string | null;
  client_id?: string | null;
  force_reclassify?: boolean;
}

export interface ClassifyResponse {
  transaction_id: string;
  invoice_id?: string | null;
  description: string;
  recommended_hs_code: string;
  confidence: number;
  needs_review: boolean;
  review_reason?: string | null;
  candidates: CandidatePath[];
  excluded: ExcludedCandidate[];
  duty_rate_percent?: number | null;
  processing_time_ms?: number | null;
  cache_hit: boolean;
  metadata?: Record<string, any> | null;
  created_at: string;
}

export interface CandidatePath {
  hs_code: string;
  description: string;
  confidence: number;
  strategy: string;
  reasoning?: string | null;
  duty_rate_percent?: number | null;
  supporting_rules: string[];
}

export interface ExcludedCandidate {
  hs_code: string;
  description: string;
  exclusion_reason: string;
  confidence: number;
}

// ── Invoice ──────────────────────────────────────────────────
export interface InvoiceResponse {
  id: string;
  client_id?: string | null;
  filename: string;
  storage_key: string;
  content_type: string;
  file_size_bytes: number;
  status: string;
  raw_text?: string | null;
  metadata?: Record<string, any> | null;
  created_at: string;
  updated_at: string;
  line_items?: LineItemResponse[];
}

export interface LineItemResponse {
  id: string;
  invoice_id: string;
  line_number: number;
  description: string;
  quantity?: number | null;
  unit?: string | null;
  unit_price?: number | null;
  total_price?: number | null;
  currency?: string | null;
  country_of_origin?: string | null;
  hs_code?: string | null;
  confidence?: number | null;
  raw_data?: Record<string, any> | null;
  created_at: string;
}

// ── Review Queue ─────────────────────────────────────────────
export interface ReviewQueueItem {
  transaction_id: string;
  invoice_id?: string | null;
  description: string;
  recommended_hs_code: string;
  confidence: number;
  review_reason?: string | null;
  candidates: CandidatePath[];
  status: string;
  created_at: string;
  client_name?: string | null;
}

export interface ApproveRequest {
  reviewer: string;
  notes?: string | null;
}

export interface ModifyRequest {
  reviewer: string;
  corrected_hs_code: string;
  reason: string;
  notes?: string | null;
}

export interface RejectRequest {
  reviewer: string;
  reason: string;
  request_reclassify?: boolean;
}

// ── Audit ────────────────────────────────────────────────────
export interface AuditLogResponse {
  id: string;
  invoice_id?: string | null;
  client_id?: string | null;
  action: string;
  actor?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  before_state?: Record<string, any> | null;
  after_state?: Record<string, any> | null;
  details?: string | null;
  ip_address?: string | null;
  created_at: string;
}

// ── SSE Events ───────────────────────────────────────────────
export type PipelineEventType =
  | 'invoice.uploaded'
  | 'extraction.started'
  | 'extraction.completed'
  | 'siv.violation'
  | 'classification.started'
  | 'classification.completed'
  | 'verification.completed'
  | 'review.required'
  | 'review.completed'
  | 'completion.done'
  | 'pipeline.error';

export interface PipelineEvent {
  event: PipelineEventType;
  invoice_id: string;
  line_item_id?: string | null;
  data: Record<string, any>;
  timestamp: string;
}
```

---

## 8. Error Handling

### HTTP Error Codes the Backend Returns

| Status | Meaning | When | Frontend Action |
|---|---|---|---|
| `200` | Success | Normal response | Display data |
| `201` | Created | File upload success | Show success toast + navigate |
| `404` | Not Found | Invalid UUID | Show "Not Found" page |
| `409` | Conflict | Review action on already-reviewed item | Show "Already reviewed" message, refresh queue |
| `413` | Payload Too Large | File > 50 MB | Show "File too large" error |
| `422` | Validation Error | Bad input | Show field-level errors |
| `500` | Server Error | Backend crash | Show generic error + retry button |

### Validation Error Shape (422)
```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "description"],
      "msg": "String should have at least 3 characters",
      "input": "ab",
      "ctx": {"min_length": 3}
    }
  ]
}
```

**How to display:** Map `loc` array to field names. Show inline error messages under each form field.

---

## 9. State Management Recommendations

### TanStack Query (Recommended)

```typescript
// queries.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from './api';

// Review Queue — auto-refreshes
export function useReviewQueue(limit = 50, offset = 0) {
  return useQuery({
    queryKey: ['review-queue', limit, offset],
    queryFn: () => api.get(`/api/review/queue?limit=${limit}&offset=${offset}`).then(r => r.data),
    refetchInterval: 30000,
  });
}

// Classify — mutation
export function useClassify() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ClassifyRequest) => api.post('/api/classify', data).then(r => r.data),
    onSuccess: () => {
      // Invalidate review queue in case new items were added
      queryClient.invalidateQueries({ queryKey: ['review-queue'] });
    },
  });
}

// Approve — mutation
export function useApprove(transactionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ApproveRequest) =>
      api.post(`/api/review/${transactionId}/approve`, data).then(r => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['review-queue'] });
    },
  });
}
```

### Key Cache Invalidation Rules

| After This Action | Invalidate These Queries |
|---|---|
| `POST /api/classify` | `review-queue` (new item may be added) |
| `POST /api/review/*/approve` | `review-queue` (item removed from queue) |
| `POST /api/review/*/modify` | `review-queue` |
| `POST /api/review/*/reject` | `review-queue` |
| `POST /api/invoices` | Nothing (upload doesn't auto-classify) |

---

## 10. UI/UX Guidelines

### Confidence Visualization

Use consistent color coding across the entire dashboard:

| Confidence Range | Color | Label | Icon |
|---|---|---|---|
| **≥ 0.85** (85%+) | `#10B981` (green) | Auto-Approved | ✅ |
| **0.60 – 0.84** | `#F59E0B` (amber) | Needs Review | ⚠️ |
| **< 0.60** | `#EF4444` (red) | Low Confidence | 🔴 |

### Loading States

| Action | Expected Time | Show |
|---|---|---|
| Health check | <200ms | Nothing (instant) |
| Classification (cached) | <100ms | Brief flash |
| Classification (full pipeline) | 2-8 seconds | Progress bar with pipeline steps |
| File upload (10 MB PDF) | 1-3 seconds | Upload progress bar |
| Review queue fetch | <500ms | Skeleton loader |

### HS Code Display Format

Always display HS codes in dot-separated format: `0902.10.10` (not `09021010`).

Use monospace font for HS codes for readability:
```css
.hs-code {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 1.1rem;
  font-weight: 600;
  letter-spacing: 0.05em;
}
```

### Reasoning Display

The `reasoning` field from the LLM contains legal reasoning citing GRI rules. Display this in a styled panel:

```css
.reasoning-panel {
  background: #F8FAFC;
  border-left: 4px solid #3B82F6;
  padding: 1rem;
  border-radius: 0 0.5rem 0.5rem 0;
  font-size: 0.9rem;
  line-height: 1.6;
  white-space: pre-wrap;
}
```

---

## 11. Environment Configuration

### Frontend `.env` File

```env
# .env.local (Next.js) or .env (Vite)

# Backend API URL
NEXT_PUBLIC_API_URL=http://localhost:8000
# or for Vite:
# VITE_API_URL=http://localhost:8000

# SSE Events URL (same as API in dev, may differ in production)
NEXT_PUBLIC_SSE_URL=http://localhost:8000/api/events
```

### Production CORS

When deploying, update the backend `.env` to restrict CORS:
```env
CORS_ORIGINS=["https://your-dashboard.vercel.app","https://app.logiguard.com"]
```

### Ports Reference

| Service | Port | URL |
|---|---|---|
| FastAPI Backend | `8000` | `http://localhost:8000` |
| PostgreSQL | `5435` | `postgresql://localhost:5435` |
| Redis | `6379` | `redis://localhost:6379` |
| MinIO API | `9000` | `http://localhost:9000` |
| MinIO Console | `9001` | `http://localhost:9001` |
| Your Frontend | `3000` (typical) | `http://localhost:3000` |

---

## Quick Checklist for Frontend Dev

- [ ] Set up API client with base URL `http://localhost:8000`
- [ ] Create TypeScript types (copy from Section 7)
- [ ] Build **Quick Classify** page — most important, demonstrates the AI
- [ ] Build **Review Queue** page — table with pending items
- [ ] Build **Review Detail** page — approve/modify/reject actions
- [ ] Build **Invoice Upload** page — drag-and-drop PDF upload
- [ ] Build **Audit Trail** page — timeline view
- [ ] Build **Dashboard** page — health + stats overview
- [ ] Add SSE connection for real-time updates
- [ ] Add proper error handling for 404, 409, 422 responses
- [ ] Test with Swagger UI first: `http://localhost:8000/docs`

---

> **Tip:** Before writing any frontend code, spend 10 minutes on `http://localhost:8000/docs`. The Swagger UI lets you test every endpoint interactively. This is the fastest way to understand the exact request/response shapes.
