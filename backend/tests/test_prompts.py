import hashlib
from pathlib import Path

import pytest

from app.prompts import PromptNotFoundError, PromptRenderError, load_prompt


@pytest.fixture
def prompts_root(tmp_path: Path) -> Path:
    directory = tmp_path / "score_clause"
    directory.mkdir()
    (directory / "v1.md").write_text("old: $clause", encoding="utf-8")
    (directory / "v2.md").write_text("v2: $clause", encoding="utf-8")
    (directory / "v10.md").write_text('Return {"score": 0.5} for: $clause', encoding="utf-8")
    (directory / "notes.txt").write_text("ignored", encoding="utf-8")
    return tmp_path


def test_latest_version_is_numeric_max(prompts_root: Path) -> None:
    prompt = load_prompt("score_clause", root=prompts_root)

    assert prompt.version == 10
    assert prompt.sha256 == hashlib.sha256(prompt.text.encode()).hexdigest()


def test_specific_version(prompts_root: Path) -> None:
    assert load_prompt("score_clause", 2, root=prompts_root).text == "v2: $clause"


def test_render_leaves_json_braces_alone(prompts_root: Path) -> None:
    rendered = load_prompt("score_clause", root=prompts_root).render(clause="Cap is 1x fees")

    assert rendered == 'Return {"score": 0.5} for: Cap is 1x fees'


def test_missing_variable_raises(prompts_root: Path) -> None:
    with pytest.raises(PromptRenderError, match="clause"):
        load_prompt("score_clause", root=prompts_root).render()


def test_missing_prompt_or_version(prompts_root: Path) -> None:
    with pytest.raises(PromptNotFoundError):
        load_prompt("nope", root=prompts_root)
    with pytest.raises(PromptNotFoundError):
        load_prompt("score_clause", 3, root=prompts_root)


def test_bundled_smoke_prompt_loads() -> None:
    prompt = load_prompt("smoke")

    assert prompt.version >= 1
    assert "pong" in prompt.render(word="pong")
