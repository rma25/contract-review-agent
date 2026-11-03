import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Clause

# Short words rarely help ranking and only bloat the query.
_MIN_TERM_LENGTH = 4
_MAX_TERMS = 24
_WORD = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class SimilarClause:
    clause: Clause
    rank: float


class ClauseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_for_contract(self, contract_id: uuid.UUID, clauses: Sequence[Clause]) -> None:
        """Drop any earlier segmentation (and its flags) and store these clauses in order."""
        self.session.execute(delete(Clause).where(Clause.contract_id == contract_id))
        for ordinal, clause in enumerate(clauses, start=1):
            clause.contract_id = contract_id
            clause.ordinal = ordinal
            self.session.add(clause)
        self.session.flush()

    def list_for_contract(self, contract_id: uuid.UUID) -> list[Clause]:
        stmt = select(Clause).where(Clause.contract_id == contract_id).order_by(Clause.ordinal)
        return list(self.session.scalars(stmt))

    def search_similar(
        self, text: str, *, exclude_contract_id: uuid.UUID, limit: int
    ) -> list[SimilarClause]:
        """Rank clauses from other contracts by full-text overlap with `text`.

        The text is reduced to distinct keywords OR-ed together, so a long clause still
        matches clauses that share only part of its vocabulary.
        """
        terms = keyword_terms(text)
        if not terms:
            return []
        query = func.to_tsquery("english", " | ".join(terms))
        rank = func.ts_rank_cd(Clause.search_vector, query).label("rank")
        stmt = (
            select(Clause, rank)
            .where(Clause.contract_id != exclude_contract_id)
            .where(Clause.search_vector.op("@@")(query))
            .order_by(rank.desc(), Clause.id)
            .limit(limit)
            .options(selectinload(Clause.risk_flags))
        )
        return [
            SimilarClause(clause=clause, rank=float(score))
            for clause, score in self.session.execute(stmt)
        ]


def keyword_terms(text: str) -> list[str]:
    """Distinct lowercase alphabetic words, safe to splice into to_tsquery."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(text.lower()):
        if len(word) >= _MIN_TERM_LENGTH:
            seen.setdefault(word, None)
        if len(seen) >= _MAX_TERMS:
            break
    return list(seen)
