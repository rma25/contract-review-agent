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
- **Audit everything.** Every LLM call, tool call, agent decision, and human action is written to an append-only audit log. Audit rows are never updated or deleted. LLM call events record metadata only (provider, model, prompt name/version/sha256, latency, attempts, token counts, outcome); **never API keys, prompt text, message content, or raw error messages**.
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

### LLM layer (`backend/app/llm`)
- All model access goes through `app.llm.get_llm_provider()`, which returns the adapter chosen by `LLM_PROVIDER=gemini|groq|ollama`, wrapped with retries and auditing. Never construct an adapter directly in app code.
- Only `app/llm/providers/` may import provider SDKs (`google-genai`, `openai`) or call provider HTTP APIs.
- Retries live only in `app/llm/retry.py` (429, 5xx and transport errors; full-jitter backoff). Keep SDK built-in retries off.
- Adapters map every provider failure to `LLMRateLimitError`, `LLMServerError`, `LLMRequestError` or `LLMResponseError`. Error messages must not include keys or prompt text.
- Pass `prompt=` and `contract_id=` to `chat()` so the audit event records them. Commit a contract before making LLM calls that reference it (audit events are written in their own transaction).
- Prompts live in `app/prompts/<name>/v<N>.md` and load via `load_prompt()`. Never edit a released version; add `v<N+1>.md`. Placeholders use `$var`.
- Assume free-tier rate limits: keep calls small.

### Testing LLM code
- Unit tests must never hit the network. Inject an `httpx2.Client` backed by `MockTransport` (see `tests/llm/mock_http.py`). respx does not work here: the SDKs use httpx2.
- Real-provider tests are marked `@pytest.mark.live`, deselected by default, and run with `uv run pytest -m live`. CI has no API keys.
- Build `Settings(_env_file=None, ...)` in unit tests, so a developer's `.env` can't change results.

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
- `uv run pytest -m live`: opt-in smoke test against the real provider in `.env`
- `uv run ruff check .` and `uv run ruff format .`: lint and format
- `uv run alembic upgrade head`: apply migrations; `uv run alembic check` detects drift

## Current status

- **Done:**
  - Backend foundation: the schema (contracts, clauses, risk_flags, reviews, append-only audit_events), `GET /health`, `POST /contracts`, and `GET /contracts/{id}`.
  - CI: GitHub Actions runs ruff and pytest against Postgres 16 on pushes to main and on PRs.
  - The provider-agnostic LLM layer with Gemini, Groq and Ollama adapters.
- **Not started:** agent logic (clause extraction, playbook scoring, review routing), frontend, evals. Don't add agent logic unless asked.
