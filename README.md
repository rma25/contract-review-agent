# Contract Review Agent

An AI agent that reviews contracts against a policy playbook, with a human in the loop for every decision.

Upload a contract (PDF or DOCX). The agent extracts its clauses, scores each one for risk against your playbook using tool calls, and sends anything it flags to a reviewer, who approves, edits, or rejects it. Every agent step is written to an audit log, and an eval suite measures how well the agent does.

> **Status:** scaffolding only. No features are implemented yet.

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

Set the LLM provider with `LLM_PROVIDER` (`gemini`, `groq`, or `ollama`). See [`.env.example`](.env.example).

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

Setup instructions will be added once the backend and frontend are scaffolded. For now:

```bash
cp .env.example .env   # then fill in your API keys
```

## License

[MIT](LICENSE)
