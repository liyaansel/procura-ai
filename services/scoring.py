"""Deterministic weighted scoring and same-run peer metrics."""

from typing import Any

from models import Criterion
from services.validator import validate_criteria


def calculate_metrics(
    suppliers: list[dict[str, Any]], criteria: list[Criterion]
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    active = validate_criteria(criteria)
    if not suppliers:
        return [], {}
    benchmarks = {
        criterion.criterion_id: max(
            float(next(c for c in supplier["criteria"] if c["criterion_id"] == criterion.criterion_id)["score"])
            for supplier in suppliers
        )
        for criterion in active
    }
    weighted_sum = sum(c.weight for c in active)
    calculated: list[dict[str, Any]] = []
    for supplier in suppliers:
        absolute_score = 0.0
        ppi_sum = 0.0
        scorecards = {entry["criterion_id"]: entry for entry in supplier["criteria"]}
        for criterion in active:
            card = scorecards[criterion.criterion_id]
            score = float(card["score"])
            benchmark = benchmarks[criterion.criterion_id]
            relative = (score / benchmark * 100.0) if benchmark > 0 else 100.0
            gap = score - benchmark
            absolute_score += (score / criterion.max_score) * criterion.weight
            ppi_sum += relative * criterion.weight
            card.update(
                {
                    "benchmark": benchmark,
                    "gap": gap,
                    "relative_percentage": relative,
                    "benchmark_convention": "neutral_zero" if benchmark == 0 else None,
                }
            )
        result = dict(supplier)
        result["criteria"] = [scorecards[c.criterion_id] for c in active]
        result["absolute_score"] = absolute_score
        result["ppi"] = ppi_sum / weighted_sum
        calculated.append(result)
    return calculated, benchmarks
