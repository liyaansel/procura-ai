"""Validate and deterministically normalize provider evaluations."""

import json
import math
import re
import unicodedata
from typing import Any

from models import Criterion, EvaluationError


def _normalize_evidence_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    text = re.sub(
        r"(?<=\w)[\-\u2010-\u2015][ \t]*\r?\n[ \t]*(?=\w)",
        " ",
        text,
    )
    text = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in text.casefold()
    )
    return " ".join(text.split())


def validate_criteria(criteria: list[Criterion]) -> list[Criterion]:
    active = [criterion for criterion in criteria if criterion.is_active]
    if not active:
        raise EvaluationError("Activate at least one evaluation criterion.")
    if any(not math.isfinite(c.weight) or c.weight < 0 for c in criteria):
        raise EvaluationError("Criterion weights must be nonnegative numbers.")
    if any(not math.isfinite(c.max_score) or c.max_score <= 0 for c in active):
        raise EvaluationError("Every active criterion must have a positive maximum score.")
    if not math.isclose(sum(c.weight for c in active), 100.0, abs_tol=1e-6):
        raise EvaluationError("Active criterion weights must sum to exactly 100%.")
    if len({c.criterion_id for c in active}) != len(active):
        raise EvaluationError("Active criterion IDs must be unique.")
    return active


def parse_response(raw_response: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw_response, str):
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise EvaluationError("The provider returned malformed JSON.") from exc
    else:
        parsed = raw_response
    if not isinstance(parsed, dict):
        raise EvaluationError("The provider response must be a JSON object.")
    if not isinstance(parsed.get("supplier_name"), str):
        raise EvaluationError("The provider response must include a supplier_name string.")
    if not isinstance(parsed.get("criteria"), list):
        raise EvaluationError("The provider response must include a criteria list.")
    return parsed


def normalize_response(
    raw_response: str | dict[str, Any],
    criteria: list[Criterion],
    expected_supplier_name: str,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    parsed = parse_response(raw_response)
    active = validate_criteria(criteria)
    by_id = {criterion.criterion_id: criterion for criterion in active}
    entries: dict[str, list[Any]] = {criterion_id: [] for criterion_id in by_id}
    warnings: list[str] = []
    for item in parsed["criteria"]:
        if not isinstance(item, dict):
            warnings.append("Ignored a malformed criterion entry.")
            continue
        criterion_id = item.get("criterion_id")
        if not isinstance(criterion_id, str) or criterion_id not in by_id:
            warnings.append(f"Ignored unknown criterion ID: {criterion_id!r}.")
            continue
        entries[criterion_id].append(item)

    normalized: list[dict[str, Any]] = []
    for criterion in active:
        criterion_entries = entries[criterion.criterion_id]
        status = "valid"
        evidence = ""
        justification = ""
        page_number = None
        if not criterion_entries:
            score = 0.0
            status = "normalized_missing"
            warnings.append(f"Missing criterion {criterion.criterion_id}; normalized to 0.")
        elif len(criterion_entries) > 1:
            score = 0.0
            status = "normalized_duplicate"
            warnings.append(f"Duplicate criterion {criterion.criterion_id}; normalized to 0.")
        else:
            item = criterion_entries[0]
            supplied_score = item.get("score")
            if isinstance(supplied_score, bool) or not isinstance(supplied_score, (int, float)) or not math.isfinite(supplied_score):
                score = 0.0
                status = "normalized_invalid"
                warnings.append(f"Invalid score for {criterion.criterion_id}; normalized to 0.")
            else:
                score = float(supplied_score)
                clipped = min(max(score, 0.0), criterion.max_score)
                if clipped != score:
                    warnings.append(
                        f"Score for {criterion.criterion_id} clipped from {score:g} to {clipped:g}."
                    )
                    score = clipped
                    status = "normalized_clipped"
            evidence_value = item.get("evidence", "")
            justification_value = item.get("justification", "")
            evidence = evidence_value if isinstance(evidence_value, str) else ""
            justification = justification_value if isinstance(justification_value, str) else ""
            if not isinstance(evidence_value, str):
                warning = f"Invalid evidence for {criterion.criterion_id}; omitted."
                warnings.append(warning)
                status = "normalized_invalid"
            elif not evidence.strip():
                warnings.append(f"No supporting evidence was returned for {criterion.criterion_id}.")
            if not isinstance(justification_value, str):
                warning = f"Invalid justification for {criterion.criterion_id}; omitted."
                warnings.append(warning)
                status = "normalized_invalid"
            elif not justification.strip():
                warnings.append(f"No justification was returned for {criterion.criterion_id}.")
            page_value = item.get("page_number")
            if isinstance(page_value, int) and not isinstance(page_value, bool) and page_value > 0:
                if pages is not None and page_value > len(pages):
                    warnings.append(
                        f"Invalid page number for {criterion.criterion_id}; omitted."
                    )
                else:
                    page_number = page_value
            elif page_value is not None:
                warnings.append(f"Invalid page number for {criterion.criterion_id}; omitted.")
            if pages is not None and evidence.strip():
                candidate_pages = (
                    [pages[page_number - 1]]
                    if page_number is not None
                    else pages
                )
                evidence_text = _normalize_evidence_text(evidence)
                found = any(
                    evidence_text in _normalize_evidence_text(page["text"])
                    for page in candidate_pages
                )
                if not found:
                    warnings.append(
                        f"Evidence for {criterion.criterion_id} could not be matched in the extracted PDF text; review it against the PDF."
                    )
        normalized.append(
            {
                "criterion_id": criterion.criterion_id,
                "name": criterion.name,
                "score": score,
                "max_score": criterion.max_score,
                "weight": criterion.weight,
                "justification": justification,
                "evidence": evidence,
                "page_number": page_number,
                "validation_status": status,
                "warnings": [
                    warning for warning in warnings
                    if criterion.criterion_id in warning
                ],
            }
        )
    risks = parsed.get("risks", [])
    if not isinstance(risks, list) or not all(isinstance(item, str) for item in risks):
        risks = []
        warnings.append("Malformed risks list was omitted.")
    summary = parsed.get("overall_summary", "")
    if not isinstance(summary, str):
        summary = ""
        warnings.append("Malformed overall summary was omitted.")
    return {
        "supplier_name": expected_supplier_name,
        "criteria": normalized,
        "risks": risks,
        "overall_summary": summary,
        "warnings": warnings,
    }
