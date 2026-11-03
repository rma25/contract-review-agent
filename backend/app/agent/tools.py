import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.agent.events import record_agent_event
from app.llm.types import ToolCall, ToolSpec
from app.models import Clause, FlagStatus, RiskFlag, Severity
from app.playbook import Playbook
from app.repositories.clause_repository import ClauseRepository
from app.repositories.risk_flag_repository import RiskFlagRepository

MAX_SEARCH_RESULTS = 5
DEFAULT_SEARCH_RESULTS = 3
# Similar clauses are context, not the subject: keep them short to save tokens.
SIMILAR_CLAUSE_PREVIEW_CHARS = 600
MAX_RATIONALE_CHARS = 2000

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="get_playbook_rules",
        description=(
            "Return the policy playbook rules for a clause type: what a reasonable position "
            "looks like, the risk criteria, and the default severity."
        ),
        parameters={
            "type": "object",
            "properties": {
                "clause_type": {"type": "string", "description": "A clause type, e.g. termination"}
            },
            "required": ["clause_type"],
        },
    ),
    ToolSpec(
        name="search_similar_clauses",
        description=(
            "Full-text search for similar clauses in previously analyzed contracts, with any "
            "risk flags they received and the human review status of those flags."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Clause text or key phrases"},
                "limit": {
                    "type": "integer",
                    "description": f"Maximum matches (1-{MAX_SEARCH_RESULTS})",
                },
            },
            "required": ["text"],
        },
    ),
    ToolSpec(
        name="create_risk_flag",
        description=(
            "Flag a clause that meets a playbook rule's risk criteria. The flag is sent to a "
            "human reviewer; it is never final. Use at most one flag per clause and rule."
        ),
        parameters={
            "type": "object",
            "properties": {
                "clause_id": {"type": "string", "description": "The clause id being flagged"},
                "rule_id": {"type": "string", "description": "The playbook rule id"},
                "severity": {"type": "string", "enum": [s.value for s in Severity]},
                "score": {
                    "type": "number",
                    "description": "Risk score from 0 (no risk) to 1 (maximum risk)",
                },
                "rationale": {
                    "type": "string",
                    "description": "Which risk criteria the clause meets and why, for the reviewer",
                },
            },
            "required": ["clause_id", "rule_id", "severity", "score", "rationale"],
        },
    ),
]


class ToolArgumentError(ValueError):
    """Bad tool arguments. The message goes back to the model, so it never echoes content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AgentTools:
    """The agent's tools for one contract. Every call is validated and audited.

    `active_clause_ids` limits which clauses may be flagged (the current batch).
    """

    def __init__(self, session: Session, contract_id: uuid.UUID, playbook: Playbook) -> None:
        self.session = session
        self.contract_id = contract_id
        self.playbook = playbook
        self.active_clause_ids: set[uuid.UUID] = set()
        self.flags_created = 0
        self._clauses = ClauseRepository(session)
        self._flags = RiskFlagRepository(session)
        self._handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
            "get_playbook_rules": self._get_playbook_rules,
            "search_similar_clauses": self._search_similar_clauses,
            "create_risk_flag": self._create_risk_flag,
        }

    @property
    def specs(self) -> list[ToolSpec]:
        return TOOL_SPECS

    def execute(self, call: ToolCall, *, batch: int) -> dict[str, Any]:
        """Run one tool call, audit it, and commit. Errors are returned, not raised."""
        audit: dict[str, Any] = {"tool": call.name, "tool_call_id": call.id, "batch": batch}
        handler = self._handlers.get(call.name)
        try:
            if handler is None:
                raise ToolArgumentError("unknown_tool", f"Unknown tool {call.name!r}")
            result = handler(call.arguments, audit)
        except ToolArgumentError as error:
            # Handlers validate before writing, so there is nothing to roll back here.
            audit |= {"ok": False, "error_code": error.code}
            result = {"ok": False, "error": str(error)}
        else:
            audit["ok"] = True
            result = {"ok": True, **result}
        record_agent_event(self.session, "agent.tool_call", self.contract_id, audit)
        self.session.commit()
        return result

    def _get_playbook_rules(self, args: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
        clause_type = _require_str(args, "clause_type")
        rules = self.playbook.rules_for(clause_type)
        audit |= {"clause_type": clause_type[:64], "result_count": len(rules)}
        return {
            "playbook_version": self.playbook.version,
            "clause_type": clause_type,
            "rules": [rule.model_dump(mode="json") for rule in rules],
        }

    def _search_similar_clauses(
        self, args: dict[str, Any], audit: dict[str, Any]
    ) -> dict[str, Any]:
        text = _require_str(args, "text")
        limit = args.get("limit", DEFAULT_SEARCH_RESULTS)
        if isinstance(limit, float) and limit.is_integer():
            limit = int(limit)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ToolArgumentError("invalid_limit", "limit must be an integer")
        limit = max(1, min(limit, MAX_SEARCH_RESULTS))

        matches = self._clauses.search_similar(
            text, exclude_contract_id=self.contract_id, limit=limit
        )
        audit |= {"query_chars": len(text), "result_count": len(matches)}
        return {
            "matches": [
                {
                    "clause_type": match.clause.clause_type,
                    "heading": match.clause.heading,
                    "text": _preview(match.clause.text),
                    "rank": round(match.rank, 4),
                    "flags": [
                        {
                            "rule_id": flag.rule_id,
                            "severity": flag.severity.value,
                            "review_status": flag.status.value,
                        }
                        for flag in match.clause.risk_flags
                    ],
                }
                for match in matches
            ]
        }

    def _create_risk_flag(self, args: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
        clause = self._active_clause(_require_str(args, "clause_id"))
        audit["clause_id"] = str(clause.id)

        rule_id = _require_str(args, "rule_id")
        rule = self.playbook.rule(rule_id)
        if rule is None:
            raise ToolArgumentError("unknown_rule", "rule_id is not in the playbook")
        audit["rule_id"] = rule.id

        try:
            severity = Severity(_require_str(args, "severity"))
        except ValueError:
            raise ToolArgumentError(
                "invalid_severity", "severity must be one of: low, medium, high, critical"
            ) from None

        score = args.get("score")
        if isinstance(score, bool) or not isinstance(score, int | float) or not 0 <= score <= 1:
            raise ToolArgumentError("invalid_score", "score must be a number from 0 to 1")

        rationale = _require_str(args, "rationale")[:MAX_RATIONALE_CHARS]

        if self._flags.exists(clause.id, rule.id):
            raise ToolArgumentError(
                "duplicate_flag", "This clause already has a flag for this rule"
            )

        # The agent proposes; a human decides. Flags always start pending review.
        flag = self._flags.add(
            RiskFlag(
                clause_id=clause.id,
                rule_id=rule.id,
                severity=severity,
                score=float(score),
                rationale=rationale,
                status=FlagStatus.PENDING,
            )
        )
        self.flags_created += 1
        record_agent_event(
            self.session,
            "agent.flag_created",
            self.contract_id,
            {
                "risk_flag_id": str(flag.id),
                "clause_id": str(clause.id),
                "rule_id": rule.id,
                "severity": severity.value,
                "score": float(score),
                "status": flag.status.value,
                "playbook_version": self.playbook.version,
            },
        )
        audit |= {"risk_flag_id": str(flag.id), "severity": severity.value}
        return {"risk_flag_id": str(flag.id), "status": flag.status.value}

    def _active_clause(self, raw_id: str) -> Clause:
        try:
            clause_id = uuid.UUID(raw_id)
        except ValueError:
            raise ToolArgumentError("invalid_clause_id", "clause_id is not a valid id") from None
        if clause_id not in self.active_clause_ids:
            raise ToolArgumentError(
                "clause_not_in_batch", "clause_id is not one of the clauses under review"
            )
        clause = self.session.get(Clause, clause_id)
        if clause is None or clause.contract_id != self.contract_id:
            raise ToolArgumentError(
                "clause_not_in_batch", "clause_id is not one of the clauses under review"
            )
        return clause


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError(f"missing_{key}", f"{key} must be a non-empty string")
    return value.strip()


def _preview(text: str) -> str:
    if len(text) <= SIMILAR_CLAUSE_PREVIEW_CHARS:
        return text
    return text[:SIMILAR_CLAUSE_PREVIEW_CHARS].rstrip() + "…"
