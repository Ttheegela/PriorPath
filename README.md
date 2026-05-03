# PriorPath — Multi-Agent Prior Authorization Engine

> LangGraph-orchestrated prior authorization system for ophthalmic procedures.  
> **Live demo:** https://priorpath-8prs.onrender.com

---

## Overview

PriorPath automates the prior authorization (PA) workflow using a multi-agent AI pipeline. A 5-node LangGraph graph processes each PA request — verifying eligibility, retrieving relevant CMS clinical guidelines via RAG, evaluating criteria, generating a determination, and producing formal provider and patient letters.

Built as a sister project to [AlignWell](https://github.com/Ttheegela/AlignWell), a clinical triage engine, both sharing the same architecture pattern.

---

## Architecture

```
POST /authorize/stream
        │
        ▼
┌───────────────────────────────────────────────────┐
│                  LangGraph Graph                  │
│                                                   │
│  intake → eligibility ──┬──► clinical → decision ─┤
│                         │                         │
│                         └──► (skip if not covered)│
│                                        │           │
│                                        ▼           │
│                                  notification      │
└───────────────────────────────────────────────────┘
        │
        ├── Qdrant Cloud  (RAG over CMS guideline docs)
        ├── Claude Haiku  (LLM reasoning at each node)
        └── Supabase      (audit log → prior_auth_audit)
```

**Node responsibilities:**

| Node | Responsibility |
|---|---|
| `intake` | Extract procedure, diagnosis, and flag missing fields |
| `eligibility` | Simulate payer coverage check and PA requirement |
| `clinical` | RAG against CMS guidelines, evaluate criteria |
| `decision` | Produce approved / pended / denied determination |
| `notification` | Generate provider letter + plain-language patient summary |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph 1.x |
| LLM | Claude Haiku (Anthropic) |
| Vector DB | Qdrant Cloud (fastembed BAAI/bge-small-en-v1.5) |
| Audit DB | Supabase (PostgreSQL) |
| API | FastAPI + uvicorn |
| Frontend | Vanilla HTML/CSS/JS (served by FastAPI) |
| Hosting | Render (Docker, free tier) |
| CI/CD | GitHub Actions (auto-deploy on push, keep-warm cron) |

---

## Features

- **Streaming progress UI** — watch each agent node complete in real time via SSE
- **3 demo scenarios** — Wet AMD (APPROVED), Cataract surgery (APPROVED), DME thin notes (PENDED)
- **History tab** — full audit log of past decisions via Supabase
- **RAG grounding** — decisions cited against real CMS LCD guideline documents
- **Formal outputs** — provider authorization letter + plain-language patient summary

---

## Local Development

### Prerequisites
- Docker + Docker Compose
- Anthropic API key

### Setup

```bash
git clone https://github.com/Ttheegela/PriorPath.git
cd PriorPath
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY, QDRANT_URL, QDRANT_API_KEY
```

### Run

```bash
docker compose up --build
```

App runs at **http://localhost:8001**

### Ingest guidelines into Qdrant

```bash
docker compose run --rm app python scripts/ingest_guidelines.py
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Frontend UI |
| `GET` | `/health` | Health check |
| `POST` | `/authorize` | Run PA workflow, returns full result |
| `POST` | `/authorize/stream` | SSE streaming PA workflow with node progress |
| `POST` | `/demo` | Run pre-seeded scenario (`{"scenario": 1\|2\|3}`) |
| `GET` | `/history` | Last 50 PA decisions from Supabase |
| `GET` | `/history/{patient_id}` | PA history for a specific patient |

Interactive docs: https://priorpath-8prs.onrender.com/docs

---

## Project Structure

```
PriorPath/
├── agents/          # LangGraph node functions (intake, eligibility, clinical, decision, notification)
├── api/             # FastAPI app (main.py)
├── data/
│   └── cms_guidelines/  # CMS LCD guideline .txt files per CPT code
├── frontend/        # Single-page frontend (index.html)
├── graph/           # LangGraph graph assembly (workflow.py)
├── models/          # State schema (PriorAuthState)
├── scripts/
│   └── ingest_guidelines.py  # Load guideline docs into Qdrant
├── tools/
│   ├── audit_log.py          # Supabase write/read
│   ├── payer_simulator.py    # Mock payer eligibility
│   └── qdrant_search.py      # Qdrant vector search
├── tests/           # End-to-end pytest scenarios
├── Dockerfile
├── docker-compose.yml
├── railway.toml     # Railway deploy config
└── render.yaml      # Render deploy config
```

---

## Related

- [AlignWell](https://github.com/Ttheegela/AlignWell) — LangGraph clinical triage engine (sister project)
