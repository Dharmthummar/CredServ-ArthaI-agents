# Phase 3: Production Architecture & Auditability
## AI-Powered KYC Extractor + Collections Orchestrator — RBI-Compliant Deployment

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                      API GATEWAY  (Kong / AWS API GW)                │
│              mTLS · Rate Limiting · JWT Auth · WAF                   │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
          ┌─────────────────┴──────────────────┐
          ▼                                     ▼
┌──────────────────┐                  ┌──────────────────────┐
│  KYC Extractor   │                  │ Collections          │
│  Microservice    │                  │ Orchestrator         │
│  (Phase 1)       │                  │ (Phase 2)            │
│  FastAPI + VLM   │                  │ FastAPI + LangGraph  │
└────────┬─────────┘                  └──────────┬───────────┘
         │                                        │
         ▼                                        ▼
┌──────────────────────────────────────────────────────────────┐
│                    SHARED INFRASTRUCTURE                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ PostgreSQL │  │  Redis Cache  │  │   Object Storage     │ │
│  │ (Encrypted)│  │  (Sessions)   │  │  (Raw Docs / S3)     │ │
│  └────────────┘  └──────────────┘  └──────────────────────┘ │
│  ┌───────────────────────┐  ┌─────────────────────────────┐  │
│  │   Proof Log Store     │  │  Ollama VLM Service          │  │
│  │   (Immutable Ledger)  │  │  (llama3.2-vision, local)    │  │
│  └───────────────────────┘  └─────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

**Key design principle:** The Vision-Language Model runs **locally via Ollama** (no external API calls, no data egress). This eliminates a full class of data-leakage risks and removes dependency on third-party SLA.

---

## 1. Proof Logs — Auditability Strategy

RBI's IT Framework and DPDP Act 2023 require that every AI-driven financial decision be fully reproducible and explainable.

### 1.1 What Is Logged (Every Single Request)

| Field | Source | Storage |
|---|---|---|
| `request_id` (UUID v4) | Gateway-generated | Proof Log |
| `triggered_by` | JWT `sub` claim (operator ID) | Proof Log |
| `data_accessed` | Field-level access list | Proof Log |
| `llm_input_hash` | SHA-256 of the exact prompt sent | Proof Log |
| `llm_raw_output` | Full model response | Encrypted cold store |
| `llm_model_version` | Ollama model tag + digest | Proof Log |
| `verification_result` | Deterministic math check output | Proof Log |
| `retry_count` | Number of automatic retries | Proof Log |
| `edge_case_routed_to` | Human analyst ID if escalated | Proof Log |
| `state_transitions[]` | Ordered list of all FSM state changes | Proof Log |
| `contact_history[]` | Channel, timestamp, outcome per touch | Proof Log |

### 1.2 Proof Log Architecture

```
Application Layer
      │  emits structured event
      ▼
┌─────────────────────┐
│  Kafka Topic        │  ← append-only, 7-year retention
│  audit.proof-log    │    (matches RBI record-keeping rules)
└──────┬──────────────┘
       │
       ├──► ClickHouse (hot store, 90 days, SQL queryable for audits)
       │
       └──► AWS S3 Glacier (cold store, encrypted AES-256, WORM policy)
```

**Immutability guarantee:** The Kafka topic uses `min.insync.replicas=3` with `acks=all`. S3 uses Object Lock (WORM) so no record can be deleted or overwritten for 7 years. Every log entry is HMAC-signed with a Hardware Security Module (HSM) key so tampering is detectable.

### 1.3 LLM Reasoning Chain Capture

For Phase 1 (KYC), the prompt + full JSON response is stored alongside the **Ollama model digest** (a pinned hash of the model weights). For compliance queries, an analyst can:
1. Re-run the same prompt hash against the same model digest via Ollama to reproduce the extraction.
2. Compare the deterministic verifier output to confirm the math check.
3. View the retry chain to see exactly why any re-attempt was triggered.

Because the model runs locally, **no PII ever leaves the organisation's network** during inference — a significant compliance advantage over cloud-API approaches.

### 1.4 Human Escalation Routing

Edge-case triggers that route to a human analyst:
- Verification fails after `MAX_RETRIES` (Phase 1)
- Debt dispute detected in D+3 voice call (Phase 2)
- Document confidence score below threshold
- Any `is_disputed = True` flag

On escalation, the system:
1. Creates a CRM ticket with the full `run_id` as the primary key.
2. Attaches the complete proof log snapshot.
3. Notifies the analyst via Slack + email with a deep-link to the audit console.
4. Sets a 4-hour SLA timer; if unresolved, auto-escalates to team lead.

---

## 2. Prompt Injection Prevention & Data Leakage Controls

### 2.1 Prompt Injection Defences

**Input sanitisation (Phase 1 — document content):**
- All document text extracted via controlled PDF rasterisation (PyMuPDF). The raw text is **never** fed into prompts; only the pixel-level image is passed to the VLM.
- This eliminates the primary injection vector: a malicious PDF embedding instructions as hidden text.
- No user-supplied free-text is concatenated into the system prompt. The `EXTRACTION_USER_PROMPT` is a static constant compiled into the application binary.

**Input sanitisation (Phase 2 — borrower responses):**
- Voice transcripts from the call platform are treated as untrusted data.
- The transcript is parsed by a lightweight classifier that maps outcomes to a closed enum: `{paid, disputed, promised, unreachable}`.
- The raw transcript text never influences control flow directly; only the classified enum drives state transitions.
- Keyword matching for dispute detection (e.g. "I never took") is done in pure Python before any LLM call, as a first-line deterministic guard.

**System prompt hardening:**
- System prompts are stored in a secrets manager (AWS Secrets Manager), not in application code.
- Prompts are versioned; any change triggers a new version, a code review, and is recorded in the proof log.
- Output format is enforced: the model is instructed to return only valid JSON. Responses are parsed with `json.loads`; any non-JSON output is rejected and retried, never passed downstream.

### 2.2 Data Leakage Controls

**Network-level isolation (Ollama advantage):**
- The VLM runs inside the cluster network. There is **zero outbound traffic** for inference — no data can leak via API calls to a third-party LLM provider.
- Ollama exposes a local REST endpoint (`localhost:11434`) that is not reachable from outside the pod's network namespace.

**PII Tokenisation:**
- Account numbers, Aadhaar, PAN, phone numbers are tokenised using a Vault transit engine before any data is written to logs.
- The VLM receives the raw image (necessary for extraction) but the extracted PII is immediately tokenised in memory before being stored.
- De-tokenisation is restricted to authorised services via IAM role and logged.

**Network segmentation:**
- Internal services communicate over a private VPC subnet; no database is exposed to the public internet.
- The document storage bucket (S3) is private, server-side encrypted (SSE-KMS), accessible only via pre-signed URLs with a 15-minute expiry.

**Model output containment:**
- The LLM response is parsed and validated against the strict JSON schema before any field is persisted or displayed.
- Numeric fields are cast to `Decimal` (not `float`) to prevent floating-point representation leakage.
- No raw LLM output is ever returned to the end user; only the validated structured data is.

**Access control:**
- Every API endpoint requires a signed JWT with explicit scopes (`kyc:read`, `collections:write`, etc.).
- Data access is logged at the field level; analysts can only access the subset of fields their role permits.
- All inter-service calls use mutual TLS (mTLS) with short-lived certificates rotated every 24 hours.

---

## 3. Deployment Architecture

### 3.1 Container & Orchestration

Both microservices are containerised (Docker, distroless base images) and deployed on Kubernetes (EKS):

```
Kubernetes Cluster
├── Namespace: kyc
│   ├── Deployment: extractor          (2–10 pods, HPA on CPU+queue depth)
│   ├── Deployment: ollama-vlm         (GPU node pool, model served via REST)
│   └── Service: extractor-svc         (ClusterIP)
│
├── Namespace: collections
│   ├── Deployment: orchestrator       (2–6 pods, HPA on active-loans count)
│   └── CronJob: timeline-ticker       (runs every hour; advances D-day states)
│
└── Namespace: infra
    ├── PostgreSQL (RDS, Multi-AZ, encrypted)
    ├── Redis (ElastiCache, in-transit encryption)
    └── Kafka (MSK, 3-broker cluster)
```

### 3.2 CI/CD & Model Version Pinning

- All LLM calls pin to a specific Ollama model digest (e.g. `llama3.2-vision:sha256-abc123…`).
- Model upgrades go through a full regression suite on synthetic documents before promotion.
- Prompt changes require a PR review from both an ML engineer and a compliance officer.
- Blue/green deployment ensures zero-downtime rollouts; the old version stays live until the new one passes health checks.

### 3.3 Observability

| Signal | Tool | Alert Condition |
|---|---|---|
| Extraction success rate | Prometheus + Grafana | < 95% over 5 min |
| Verification retry rate | Prometheus | > 15% over 15 min |
| State machine throughput | Kafka consumer lag | > 500 pending events |
| VLM inference latency (p99) | Prometheus (Ollama metrics) | > 10s |
| Failed escalations | PagerDuty | Any single failure |

---

## 4. Regulatory Compliance Summary

| Requirement | Mechanism |
|---|---|
| RBI IT Framework §4.3 (audit trail) | Immutable Kafka → S3 WORM proof log |
| DPDP Act 2023 (data minimisation) | PII tokenisation; field-level access control |
| TRAI TCCCPR (no-call registry) | DNC check before every outbound contact |
| RBI Collections Guidelines 2022 | State machine enforces call-time windows (8AM–7PM); negative constraints baked into D+3 prompt |
| ISO 27001 | mTLS, HSM key management, VAPT on every release |
| Data residency (RBI circular) | Ollama runs on-premise/in-VPC — **no PII leaves the country's infrastructure** |
