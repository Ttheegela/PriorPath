# PriorPath — Multi-Agent Prior Authorization Engine

> LangGraph-orchestrated prior authorization system for ophthalmic procedures.
> Automates the PA workflow: eligibility → clinical criteria → determination → letter generation.

---

## Architecture

```
PA Request (CPT + ICD-10 + clinical notes)
        │
        ▼
   ┌─────────┐
   │ Intake  │  → Parse + validate request fields
   └────┬────┘
        │
        ▼
   ┌─────────────┐
   │ Eligibility │  → Check insurance coverage + PA requirement
   └──────┬──────┘
          │
   ┌──────┴────────────────────────┐
   │ PA not required / not covered │  PA required + covered
   ▼                               ▼
   │                    ┌──────────────────┐
   │                    │ Clinical Criteria │ → RAG over payer guidelines (Qdrant)
   │                    └────────┬─────────┘
   │                             │
   │                    ┌────────▼──────┐
   │                    │   Decision    │ → approve / deny / pend / escalate
   │                    └────────┬──────┘
   │                             │
   └──────────────┬──────────────┘
                  │
         ┌────────▼──────────┐
         │   Notification    │ → Provider letter + Patient summary + PostgreSQL audit
         └───────────────────┘
```

## Stack

| Layer | Tool |
|-------|------|
| Agent orchestration | LangGraph (StateGraph) |
| LLM | Claude 3 Haiku (Anthropic API) |
| Clinical RAG | Qdrant + fastembed |
| Payer integration | Simulated eligibility API (swap for Availity / Change Healthcare) |
| API | FastAPI + Pydantic v2 |
| Audit DB | PostgreSQL |
| Observability | LangSmith |
| Deploy | Docker Compose → Railway |

## Quick Start

```bash
cp .env.example .env
# Add ANTHROPIC_API_KEY

docker compose up

# Demo scenarios
curl -X POST http://localhost:8001/demo -H "Content-Type: application/json" \
     -d '{"scenario": 1}'   # Wet AMD anti-VEGF → APPROVED
     -d '{"scenario": 3}'   # DME with sparse notes → PENDED

# Full API docs
open http://localhost:8001/docs
```

## Demo Scenarios

| Scenario | Procedure | Diagnosis | Expected |
|----------|-----------|-----------|----------|
| 1 | Anti-VEGF injection (CPT 67028) | Wet AMD | APPROVED |
| 2 | Cataract surgery (CPT 66984) | Cataract | APPROVED |
| 3 | Anti-VEGF injection (CPT 67028) | DME, sparse notes | PENDED |

## Clinical Guidelines Indexed

- Intravitreal anti-VEGF injection (CPT 67028) — AMD, DME, RVO criteria
- Cataract surgery standard (CPT 66984) — VA and functional criteria
- Cataract surgery complex (CPT 66982) — complicating factor criteria
- Vitrectomy (CPT 67036) — indications
- Trabeculoplasty SLT (CPT 65855) — glaucoma criteria

## Payer Integration

In demo mode, eligibility uses a simulated payer API. For production:
- Replace `tools/payer_simulator.py` with real Availity or Change Healthcare API calls
- EDI 270/271 transactions for real-time eligibility verification
- All agent logic and LangGraph routing remain unchanged
