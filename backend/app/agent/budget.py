from dataclasses import dataclass, field
from typing import Literal

from app.llm.types import ChatResponse

StopReason = Literal["max_iterations", "token_budget"]


class AgentError(Exception):
    """Base error for the analysis agent. Messages never contain contract text."""


class AgentBudgetExceededError(AgentError):
    def __init__(self, reason: StopReason, *, iterations: int, tokens: int) -> None:
        super().__init__(f"Agent stopped: {reason} (iterations={iterations}, tokens={tokens})")
        self.reason = reason
        self.iterations = iterations
        self.tokens = tokens


@dataclass
class AgentBudget:
    """Per-contract guard on LLM calls and tokens, shared by every stage of one analysis.

    Limits are checked before each call, so the call that crosses the token budget still
    completes; the next one is refused.
    """

    max_iterations: int
    token_budget: int
    iterations: int = field(default=0, init=False)
    tokens: int = field(default=0, init=False)

    def before_call(self) -> None:
        if self.iterations >= self.max_iterations:
            raise AgentBudgetExceededError(
                "max_iterations", iterations=self.iterations, tokens=self.tokens
            )
        if self.tokens >= self.token_budget:
            raise AgentBudgetExceededError(
                "token_budget", iterations=self.iterations, tokens=self.tokens
            )

    def charge(self, response: ChatResponse) -> None:
        self.iterations += 1
        self.tokens += response.usage.total_tokens
