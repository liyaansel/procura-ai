"""Typed data structures shared by the RFP evaluation services."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    name: str
    description: str
    weight: float
    max_score: float
    is_active: bool = True
    evaluation_guidance: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "name": self.name,
            "description": self.description,
            "weight": self.weight,
            "max_score": self.max_score,
            "is_active": self.is_active,
            "evaluation_guidance": self.evaluation_guidance,
        }


@dataclass(frozen=True)
class ProcurementProfile:
    procurement_id: str
    title: str
    category: str
    scope: str
    criteria: tuple[Criterion, ...]


class EvaluationError(Exception):
    """An actionable error that is safe to show in the UI."""
