from pathlib import Path

import pytest

from app.core.config import Settings
from app.models import Severity
from app.playbook import PlaybookError, load_playbook

VALID_RULE = """
  - id: {id}
    clause_type: {clause_type}
    title: T
    description: D
    risk_criteria: [c]
    severity: {severity}
"""


def _write(tmp_path: Path, *rules: dict[str, str], version: int = 1) -> Path:
    body = "".join(VALID_RULE.format(**rule) for rule in rules)
    path = tmp_path / "playbook.yaml"
    path.write_text(f"version: {version}\nrules:{body}", encoding="utf-8")
    return path


def test_default_playbook_loads() -> None:
    playbook = load_playbook(Settings(_env_file=None).playbook_path)

    assert playbook.version == 1
    assert [rule.id for rule in playbook.rules] == [
        "liability-cap",
        "indemnification",
        "auto-renewal",
        "termination",
        "governing-law",
        "confidentiality",
        "payment-terms",
        "ip-ownership",
        "data-protection",
        "non-compete",
    ]
    assert len(playbook.clause_types) == 10
    assert playbook.segmentation_types[-1] == "other"
    assert all(rule.risk_criteria for rule in playbook.rules)
    assert playbook.rule("data-protection").severity == Severity.CRITICAL  # type: ignore[union-attr]
    assert playbook.rules_for("termination")[0].id == "termination"
    assert playbook.rule("nope") is None


def test_duplicate_rule_ids_are_rejected(tmp_path: Path) -> None:
    rule = {"id": "dup", "clause_type": "termination", "severity": "high"}
    with pytest.raises(PlaybookError, match="duplicate rule ids: dup"):
        load_playbook(_write(tmp_path, rule, rule))


def test_other_is_a_reserved_clause_type(tmp_path: Path) -> None:
    with pytest.raises(PlaybookError, match="reserved"):
        load_playbook(_write(tmp_path, {"id": "x", "clause_type": "other", "severity": "low"}))


def test_unknown_severity_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PlaybookError):
        load_playbook(
            _write(tmp_path, {"id": "x", "clause_type": "termination", "severity": "extreme"})
        )


def test_malformed_or_missing_files_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(PlaybookError, match="YAMLError|ParserError|ScannerError"):
        load_playbook(bad)
    with pytest.raises(PlaybookError, match="FileNotFoundError"):
        load_playbook(tmp_path / "missing.yaml")
