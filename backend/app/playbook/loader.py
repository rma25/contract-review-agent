from functools import lru_cache
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.models.enums import Severity

OTHER_CLAUSE_TYPE = "other"


class PlaybookError(ValueError):
    pass


class PlaybookRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=128)
    clause_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    title: str
    description: str
    risk_criteria: list[str] = Field(min_length=1)
    severity: Severity


class Playbook(BaseModel):
    """Policy rules the agent checks clauses against. Loaded from YAML, never hard-coded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    rules: list[PlaybookRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [rule.id for rule in self.rules]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate rule ids: {', '.join(duplicates)}")
        if OTHER_CLAUSE_TYPE in self.clause_types:
            raise ValueError(f"clause_type {OTHER_CLAUSE_TYPE!r} is reserved")
        return self

    @property
    def clause_types(self) -> list[str]:
        """Distinct clause types covered by rules, in playbook order."""
        return list(dict.fromkeys(rule.clause_type for rule in self.rules))

    @property
    def segmentation_types(self) -> list[str]:
        """The vocabulary for clause segmentation: rule clause types plus 'other'."""
        return [*self.clause_types, OTHER_CLAUSE_TYPE]

    def rules_for(self, clause_type: str) -> list[PlaybookRule]:
        return [rule for rule in self.rules if rule.clause_type == clause_type]

    def rule(self, rule_id: str) -> PlaybookRule | None:
        return next((rule for rule in self.rules if rule.id == rule_id), None)


@lru_cache
def load_playbook(path: Path) -> Playbook:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PlaybookError(f"Cannot read playbook {path.name}: {type(exc).__name__}") from exc
    try:
        return Playbook.model_validate(data)
    except ValidationError as exc:
        raise PlaybookError(f"Invalid playbook {path.name}: {exc}") from exc
