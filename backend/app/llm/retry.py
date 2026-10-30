import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.llm.base import LLMError


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    # Upper bound on how long we honor a server's Retry-After, so one call can't stall forever.
    max_retry_after: float = 60.0

    def backoff(self, attempt: int, rng: Callable[[], float]) -> float:
        """Full-jitter delay before retry number `attempt` (1-based)."""
        ceiling = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        return rng() * ceiling

    def delay_for(self, attempt: int, error: LLMError, rng: Callable[[], float]) -> float:
        delay = self.backoff(attempt, rng)
        if error.retry_after is not None:
            delay = max(delay, min(error.retry_after, self.max_retry_after))
        return delay


@dataclass(frozen=True)
class RetryResult[T]:
    value: T
    attempts: int


def call_with_retry[T](
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> RetryResult[T]:
    """Call `fn`, retrying retryable LLMErrors (429, 5xx, transport) with jittered backoff.

    On final failure the last error is re-raised with `error.attempts` set.
    """
    attempt = 1
    while True:
        try:
            return RetryResult(value=fn(), attempts=attempt)
        except LLMError as error:
            error.attempts = attempt
            if not error.retryable or attempt >= policy.max_attempts:
                raise
            sleep(policy.delay_for(attempt, error, rng))
            attempt += 1
