# CLAUDE.md

Project context and conventions for Claude Code working in this repo.

## What this project is

An AI contract review agent. Users upload a contract (PDF/DOCX). The agent:

1. Extracts and classifies clauses.
2. Scores each clause for risk against a policy playbook, using tool calls.
3. Routes flagged clauses to a human, who approves, edits, or rejects them.

Every agent step is audit-logged, and an eval suite measures agent quality.

### Non-negotiables

- **Human in the loop.** The agent proposes; a human decides. The agent never auto-approves a flag or a contract.
- **Audit everything.** Every LLM call (provider, model, prompt version, inputs, outputs, latency, token counts), tool call, agent decision, and human action is written to an append-only audit log. Audit rows are never updated or deleted.
- **$0 cost.** Free tiers only (Gemini free tier, Groq free tier, local Ollama, AWS free tier). Do not add paid services, paid SDK features, or always-on infrastructure.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL
- **Frontend:** React + TypeScript + Vite
- **LLM:** provider-agnostic layer; Gemini is the default, Groq and Ollama are alternates
- **Local dev:** docker-compose runs Postgres
- **Deploy (later):** AWS Lambda + API Gateway + S3

## Repo layout

```
backend/    FastAPI app, agent, LLM provider layer, models, Alembic migrations
frontend/   React review UI
evals/      Eval datasets, metrics, runners
docs/       Architecture notes, ADRs
```

## Conventions

### LLM layer
- All model access goes through one provider interface in the backend. Select the provider with `LLM_PROVIDER=gemini|groq|ollama`.
- Business logic and agent code never import a provider SDK directly.
- Prompts live in versioned files, not inline strings. The prompt version is recorded in the audit log.
- Assume free-tier rate limits: handle 429s with backoff and keep calls small.

### Backend
- Type hints everywhere. Lint and format with ruff; test with pytest against real Postgres (no SQLite).
- Layering: `api` (thin routes) → `services` (logic) → `repositories` (DB access) → models. Every state change that matters writes an audit event in the same transaction, through `AuditRepository.append`.
- Change the schema only through Alembic migrations. Never edit an applied migration.
- Config comes from environment variables (see `.env.example`). Never hard-code secrets or commit `.env`.
- Keep handlers thin; agent and domain logic live outside the FastAPI route layer so they can run in Lambda.

### Playbook
- Policy rules are data (YAML/JSON), not code, so they can change without a deploy.

### Frontend
- TypeScript strict mode, ESLint. API base URL comes from `VITE_API_BASE_URL`.

### Evals
- Changes to agent behavior (prompts, tools, scoring) need eval coverage in `evals/`.
- Evals must run against any configured provider and stay within free-tier limits.

### Git
- Conventional commit prefixes (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`).

## Commands (run from `backend/`)

- `uv sync`: install dependencies
- `uv run pytest`: tests (needs `docker compose up -d` from the repo root)
- `uv run ruff check .` and `uv run ruff format .`: lint and format
- `uv run alembic upgrade head`: apply migrations; `uv run alembic check` detects drift

## Current status

The backend foundation is in place: the schema (contracts, clauses, risk_flags, reviews, append-only audit_events), `GET /health`, `POST /contracts`, and `GET /contracts/{id}`. There is no LLM or agent code yet; don't add any unless asked.
