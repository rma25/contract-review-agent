import pytest

from app.llm.base import LLMRateLimitError, LLMRequestError, LLMServerError
from app.llm.retry import RetryPolicy, call_with_retry


class Script:
    """Callable that raises the scripted errors in order, then returns 'ok'."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


@pytest.fixture
def sleeps() -> list[float]:
    return []


def _run(script: Script, sleeps: list[float], policy: RetryPolicy, rng: float = 1.0):
    return call_with_retry(script, policy, sleep=sleeps.append, rng=lambda: rng)


def test_succeeds_after_transient_failures(sleeps: list[float]) -> None:
    script = Script(LLMRateLimitError("429"), LLMServerError("503"))

    result = _run(script, sleeps, RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=30.0))

    assert result.value == "ok"
    assert result.attempts == 3
    assert sleeps == [1.0, 2.0]  # rng=1.0 gives the full exponential ceiling


def test_gives_up_after_max_attempts(sleeps: list[float]) -> None:
    script = Script(*(LLMServerError("503") for _ in range(5)))

    with pytest.raises(LLMServerError) as info:
        _run(script, sleeps, RetryPolicy(max_attempts=3))

    assert script.calls == 3
    assert info.value.attempts == 3
    assert len(sleeps) == 2


def test_does_not_retry_client_errors(sleeps: list[float]) -> None:
    script = Script(LLMRequestError("400"))

    with pytest.raises(LLMRequestError) as info:
        _run(script, sleeps, RetryPolicy(max_attempts=5))

    assert script.calls == 1
    assert info.value.attempts == 1
    assert sleeps == []


def test_backoff_is_capped(sleeps: list[float]) -> None:
    script = Script(*(LLMServerError("503") for _ in range(5)))
    policy = RetryPolicy(max_attempts=6, base_delay=1.0, max_delay=5.0)

    _run(script, sleeps, policy)

    assert sleeps == [1.0, 2.0, 4.0, 5.0, 5.0]


@pytest.mark.parametrize("rng", [0.0, 0.25, 0.999])
def test_full_jitter_stays_within_bounds(rng: float) -> None:
    policy = RetryPolicy(base_delay=2.0, max_delay=30.0)

    for attempt in range(1, 6):
        ceiling = min(30.0, 2.0 * 2 ** (attempt - 1))
        assert 0.0 <= policy.backoff(attempt, lambda: rng) <= ceiling
        assert policy.backoff(attempt, lambda: rng) == pytest.approx(rng * ceiling)


def test_retry_after_sets_a_floor_on_the_delay(sleeps: list[float]) -> None:
    script = Script(LLMRateLimitError("429", retry_after=7.0))

    _run(script, sleeps, RetryPolicy(base_delay=1.0), rng=0.0)

    assert sleeps == [7.0]


def test_retry_after_is_capped(sleeps: list[float]) -> None:
    script = Script(LLMRateLimitError("429", retry_after=3600.0))

    _run(script, sleeps, RetryPolicy(max_retry_after=60.0), rng=0.0)

    assert sleeps == [60.0]
