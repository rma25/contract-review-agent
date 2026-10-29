# Backend

FastAPI service (Python 3.12) for the contract review agent. Not scaffolded yet.

It will contain:

- **API:** upload, review, and audit endpoints (FastAPI)
- **Agent:** clause extraction, playbook risk scoring through tool calls, routing flags to human review
- **LLM layer:** provider-agnostic interface with Gemini, Groq, and Ollama adapters, selected by `LLM_PROVIDER`
- **Persistence:** SQLAlchemy models, Alembic migrations, PostgreSQL
- **Audit log:** append-only record of every agent and human step
