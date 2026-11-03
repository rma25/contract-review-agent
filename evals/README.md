# Evals

Eval suite for the contract review agent.

## Fixtures

[`fixtures/`](fixtures) holds three synthetic contracts. Every party is fictitious and none of the text comes from a real company:

| Fixture | Risk |
|---|---|
| `low_risk_saas.md` | low: balanced, mutual terms |
| `medium_risk_services.md` | medium: long auto-renewal, short payment terms, weak confidentiality, vendor-owned deliverables |
| `high_risk_vendor.md` | high: violates every playbook rule |

[`fixtures/expected.yaml`](fixtures/expected.yaml) labels which playbook rules each fixture must, may, or must not trigger, and at what minimum severity. Backend tests render the Markdown to DOCX (`backend/tests/contract_fixtures.py`) and upload it through the API. The opt-in live test (`uv run pytest -m live -k agent`) runs the high-risk fixture end to end.

## Planned

- **Datasets:** sample contracts with labeled clauses and expected risk flags
- **Metrics:** clause extraction precision and recall, risk-flag accuracy, tool-call correctness, and per-provider latency and token usage
- **Runners:** scripts that run the agent against the datasets with any configured provider (`LLM_PROVIDER`), staying within free-tier limits

Run outputs go to `evals/results/`, which git ignores.
