# Contract Review Agent

[![CI](https://github.com/rma25/contract-review-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/rma25/contract-review-agent/actions/workflows/ci.yml)

An AI agent that reviews contracts against a policy playbook, with a human in the loop for every decision.

Upload a contract (PDF or DOCX). The agent extracts its clauses, scores each one for risk against your playbook using tool calls, and sends anything it flags to a reviewer, who approves, edits, or rejects it. Every agent step is written to an audit log, and an eval suite measures how well the agent does.

> **Status:** the backend foundation (database schema, contract upload API), CI, and a provider-agnostic LLM layer are in place. No agent logic yet.

## How a review works

1. **Upload:** a user uploads a PDF or DOCX contract.
2. **Extract:** the agent splits the document into clauses and classifies each one (indemnity, liability cap, termination, and so on).
3. **Score:** the agent checks each clause against the policy playbook through tool calls and assigns a risk score with a rationale.
4. **Human review:** flagged clauses go to a reviewer, who approves, edits, or rejects each one. The agent never approves on its own.
5. **Audit:** every LLM call, tool call, agent decision, and human action is appended to an audit log.

## Stack

| Layer        | Technology                                                   |
| ------------ | ------------------------------------------------------------ |
| Backend      | Python 3.12, FastAPI, SQLAlchemy, Alembic                    |
| Database     | PostgreSQL (docker-compose for local dev)                    |
| Frontend     | React, TypeScript, Vite                                      |
| LLM          | Provider-agnostic layer: Gemini (default), Groq, Ollama      |
| Deploy (later) | AWS Lambda, API Gateway, S3 (free tier)                    |

## LLM providers

Set the provider with `LLM_PROVIDER` in `.env`. All three options are free:

| `LLM_PROVIDER` | What you need |
|---|---|
| `gemini` (default) | a free API key from [Google AI Studio](https://aistudio.google.com/apikey) in `GEMINI_API_KEY` |
| `groq` | a free API key from the [Groq console](https://console.groq.com/keys) in `GROQ_API_KEY` |
| `ollama` | [Ollama](https://ollama.com/download) running locally, plus `ollama pull llama3.1`; no key |

Every LLM call is retried on rate limits and server errors, and is recorded in the audit log as metadata only (never prompts or keys). See [`backend/README.md`](backend/README.md#llm-layer) for details. To check your setup with one real call, run `uv run pytest -m live` from `backend/`.

## Repo layout

```
backend/    FastAPI service, agent, LLM provider layer, DB models, migrations
frontend/   React + TypeScript + Vite review UI
evals/      Eval suite: labeled contracts, metrics, runners
docs/       Architecture notes and decision records
```

## Cost

This project has to run for $0. It uses only free tiers (Gemini free tier, Groq free tier, local Ollama, AWS free tier) and no always-on paid infrastructure.

## Getting started

```powershell
docker compose up -d --wait        # Postgres 16
Copy-Item .env.example .env        # then fill in your API keys
```

Then follow [`backend/README.md`](backend/README.md) to install dependencies, run migrations, start the API, and run the tests.

## License

[MIT](LICENSE)
