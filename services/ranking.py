"""Stable, fully deterministic ranking and tie-break explanations."""

from datetime import date, datetime
from typing import Any

from models import EvaluationError


def parse_submission_date(value: str) -> date:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
        if value != parsed.isoformat():
            raise ValueError("Submission date must use YYYY-MM-DD format.")
        return parsed
    except (TypeError, ValueError) as exc:
        raise EvaluationError(
            f"Submission date {value!r} must use YYYY-MM-DD format."
        ) from exc


def rank_suppliers(suppliers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for supplier in suppliers:
        rating = supplier.get("experience_rating")
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not 0 <= rating <= 5:
            raise EvaluationError("Experience ratings must be numeric values from 0 to 5.")
        supplier["_parsed_date"] = parse_submission_date(supplier["submission_date"])
    ordered = sorted(
        suppliers,
        key=lambda supplier: (
            -supplier["ppi"],
            supplier["_parsed_date"],
            -supplier["experience_rating"],
            supplier["supplier_name"].casefold(),
            supplier["supplier_name"],
        ),
    )
    ranked: list[dict[str, Any]] = []
    for index, supplier in enumerate(ordered, start=1):
        result = dict(supplier)
        result.pop("_parsed_date", None)
        result["rank"] = index
        if index == 1:
            result["tie_break_explanation"] = "Ranked first by the mandatory PPI/date/experience/name ordering."
        else:
            previous = ordered[index - 2]
            if supplier["ppi"] != previous["ppi"]:
                reason = "higher PPI"
            elif supplier["_parsed_date"] != previous["_parsed_date"]:
                reason = "earlier submission date"
            elif supplier["experience_rating"] != previous["experience_rating"]:
                reason = "higher experience rating"
            else:
                reason = "supplier name ascending (case-insensitive, then original name)"
            result["tie_break_explanation"] = f"Placed after the previous supplier due to {reason}."
        ranked.append(result)
    return ranked
