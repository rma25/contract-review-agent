# Backend

FastAPI service (Python 3.12) for the contract review agent. Dependencies are managed with [uv](https://docs.astral.sh/uv/).

## Layout

```
app/
  api/           FastAPI routers and dependencies (thin: no business logic)
  core/          config (pydantic-settings) and database session setup
  models/        SQLAlchemy 2.0 models
  schemas/       Pydantic request/response models
  repositories/  database access
  services/      business logic (uploads, file storage)
  llm/           provider-agnostic LLM layer (adapters, retries, auditing)
  prompts/       versioned prompt files: <name>/v<N>.md
alembic/         migrations
tests/           pytest suite (runs against a real Postgres test database)
```

A request flows route → service → repository → session.

## Data model

| Table | Purpose |
|---|---|
| `contracts` | an uploaded file: original name, content type, storage key, size, sha256, status |
| `clauses` | clauses extracted from a contract, in order |
| `risk_flags` | a playbook rule violation on a clause, with severity, score (0–1), rationale, and review status |
| `reviews` | a human decision (approve / edit / reject) on a risk flag |
| `audit_events` | an **append-only** log of every agent, human, and system step |

`audit_events` cannot be modified. A Postgres trigger rejects `UPDATE` and `DELETE`, and SQLAlchemy listeners raise `AuditEventImmutableError` before the database is reached. A contract with audit history cannot be deleted (`ON DELETE RESTRICT`).

## Run locally (Windows, PowerShell)

Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop/) running, and uv:

```powershell
winget install --id astral-sh.uv -e
```

From the repo root:

```powershell
# 1. Start Postgres 16 (also creates the contract_review_test database on first run)
docker compose up -d --wait

# 2. Create your env file (fill in API keys later; DB defaults work as-is)
Copy-Item .env.example .env
```

From `backend\`:

```powershell
cd backend

# 3. Install Python 3.12 + dependencies into .venv
uv sync

# 4. Apply migrations
uv run alembic upgrade head

# 5. Start the API with auto-reload
uv run uvicorn app.main:app --reload
```

The API runs at http://localhost:8000, with interactive docs at http://localhost:8000/docs.

Try it:

```powershell
Invoke-RestMethod http://localhost:8000/health
curl.exe -F "file=@C:\path\to\contract.pdf" http://localhost:8000/contracts
Invoke-RestMethod http://localhost:8000/contracts/<id>
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | API and database status. Returns 503 if the database is unreachable. |
| `POST` | `/contracts` | Multipart upload (`file` field) of a `.pdf` or `.docx`. Saves it to `UPLOAD_DIR` and returns 201. Returns 415 for an unsupported or mismatched type, 413 if it is over `MAX_UPLOAD_MB`, and 400 if it is empty. |
| `GET` | `/contracts/{id}` | A contract with its clauses. Returns 404 if not found. |

Uploads are validated by extension *and* file content (PDF header, or a DOCX zip containing `word/document.xml`). They are stored under a server-generated name, never the client's filename.

## LLM layer

All model access goes through one entry point:

```python
from app.llm import Message, ToolSpec, get_llm_provider
from app.prompts import load_prompt

llm = get_llm_provider()  # adapter chosen by LLM_PROVIDER
prompt = load_prompt("smoke")  # latest app/prompts/smoke/vN.md
response = llm.chat(
    [Message.system(prompt.render(word="pong")), Message.user("Go.")],
    tools=None,  # list[ToolSpec] (JSON-schema parameters)
    response_schema=None,  # Pydantic model or JSON schema -> JSON text
    prompt=prompt,  # recorded in the audit event
    contract_id=None,  # links the audit event to a contract
)
response.text, response.tool_calls, response.usage
```

| Provider | `LLM_PROVIDER` | Implementation | Key |
|---|---|---|---|
| Gemini (default) | `gemini` | `google-genai` SDK | free at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| Groq | `groq` | `openai` SDK on Groq's OpenAI-compatible API | free at [console.groq.com/keys](https://console.groq.com/keys) |
| Ollama | `ollama` | local HTTP API (`/api/chat`) | none; [install Ollama](https://ollama.com/download), then `ollama pull llama3.1` |

- **Retries:** 429, 5xx, timeouts and connection errors are retried with exponential backoff and full jitter, honoring `Retry-After` (up to 60 s). Other 4xx errors fail immediately. Each SDK's own retries are turned off, so this is the only retry logic.
- **Auditing:** every call writes an `llm.call.succeeded` or `llm.call.failed` audit event with provider, model, latency, attempts, token counts, finish reason, tool names, and prompt name, version and sha256.
  - Message content, prompts, responses, API keys and raw error messages are **never** logged.
  - Events are committed in their own transaction, so failures are recorded even if the caller rolls back. Commit a contract before making LLM calls that reference it.
- **Prompts:** add a new `vN.md` file instead of editing a released one. Placeholders use `$name`, so JSON braces need no escaping.

## Tests, lint, format

```powershell
uv run pytest
uv run ruff check .
uv run ruff format .
```

The LLM adapter tests use a mock HTTP transport (`httpx2.MockTransport`), so they make no network calls and need no API keys. CI runs exactly this.

To check your real provider configuration, run the opt-in live smoke test. It uses `.env`, makes one small call, and skips if the selected provider has no key or Ollama isn't running:

```powershell
uv run pytest -m live
```

The tests use `TEST_DATABASE_URL` (default `contract_review_test`). At the start of each run they drop and recreate its schema with the real Alembic migrations, and each test runs in a transaction that is rolled back afterwards. Postgres must be running.

If your Postgres volume existed before the test database was added, create it once:

```powershell
docker compose exec postgres createdb -U contract contract_review_test
```

## Migrations

```powershell
uv run alembic revision --autogenerate -m "describe change"   # then review the generated file
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic check   # fails if models and migrations have drifted
```

Never edit a migration that has already been applied. Add a new one.

## Configuration

Settings come from environment variables or the repo-root `.env` (see `.env.example`):

| Variable | Default |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://contract:contract@localhost:5432/contract_review` |
| `TEST_DATABASE_URL` | `postgresql+psycopg://contract:contract@localhost:5432/contract_review_test` |
| `UPLOAD_DIR` | `./uploads` (relative to the working directory) |
| `MAX_UPLOAD_MB` | `25` |
| `LLM_PROVIDER` | `gemini` (`gemini`, `groq`, or `ollama`) |
| `LLM_TEMPERATURE` / `LLM_TIMEOUT_SECONDS` | `0.0` / `60` |
| `LLM_MAX_RETRIES` / `LLM_RETRY_BASE_SECONDS` / `LLM_RETRY_MAX_SECONDS` | `3` / `1.0` / `30.0` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | empty / `gemini-2.5-flash` |
| `GROQ_API_KEY` / `GROQ_MODEL` / `GROQ_BASE_URL` | empty / `llama-3.3-70b-versatile` / Groq's OpenAI endpoint |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `llama3.1` |
