# Evals

Eval suite for the contract review agent. Not scaffolded yet.

It will contain:

- **Datasets:** sample contracts with labeled clauses and expected risk flags
- **Metrics:** clause extraction precision and recall, risk-flag accuracy, tool-call correctness, and per-provider latency and token usage
- **Runners:** scripts that run the agent against the datasets with any configured provider (`LLM_PROVIDER`), staying within free-tier limits

Run outputs go to `evals/results/`, which git ignores.
