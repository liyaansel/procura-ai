"""Sequential end-to-end evaluation workflow."""

from datetime import datetime, timezone
import uuid
from typing import Any, Callable

from database import create_run_record, persist_run
from models import EvaluationError, ProcurementProfile
from providers.base import EvaluationProvider
from services.evaluator import PROMPT_VERSION, RESPONSE_SCHEMA_VERSION, evaluate_supplier
from services.pdf_extractor import PDFExtractionError, extract_pdf
from services.ranking import parse_submission_date, rank_suppliers
from services.scoring import calculate_metrics
from services.validator import normalize_response, validate_criteria


def run_evaluation(
    db_path: str,
    profile: ProcurementProfile,
    submissions: list[dict[str, Any]],
    provider: EvaluationProvider,
    provider_name: str,
    model_name: str,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    criteria = validate_criteria(list(profile.criteria))
    if len(submissions) < 1:
        raise EvaluationError("Upload at least one supplier PDF.")
    names = [item["supplier_name"].strip() for item in submissions]
    if any(not name for name in names) or len({name.casefold() for name in names}) != len(names):
        raise EvaluationError("Supplier names must be nonempty and unique within a batch.")
    for item in submissions:
        rating = item.get("experience_rating")
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not 0 <= rating <= 5:
            raise EvaluationError("Experience ratings must be numeric values from 0 to 5.")
        parse_submission_date(item["submission_date"])
    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    create_run_record(
        db_path,
        run_id,
        created_at,
        profile,
        provider_name,
        model_name,
        PROMPT_VERSION,
        RESPONSE_SCHEMA_VERSION,
    )
    successful: list[dict[str, Any]] = []
    run_warnings: list[str] = []
    failures: list[dict[str, Any]] = []
    for index, item in enumerate(submissions, start=1):
        name = item["supplier_name"].strip()
        try:
            pages = extract_pdf(item["filename"], item["content"])
            raw_response = evaluate_supplier(
                provider,
                profile.title,
                profile.category,
                profile.scope,
                criteria,
                pages,
                name,
            )
            normalized = normalize_response(raw_response, criteria, name, pages)
            successful.append(
                {
                    **normalized,
                    "submission_date": item["submission_date"],
                    "experience_rating": float(item["experience_rating"]),
                    "status": "evaluated",
                }
            )
        except (PDFExtractionError, EvaluationError) as exc:
            failures.append({"supplier_name": name, "message": str(exc)})
        except Exception:
            failures.append(
                {
                    "supplier_name": name,
                    "message": "Provider evaluation failed. Check provider selection, model, credentials, and connectivity.",
                }
            )
        finally:
            if progress_callback is not None:
                progress_callback(index, len(submissions), name)
    if failures:
        run_warnings.extend(
            f"{failure['supplier_name']}: {failure['message']}" for failure in failures
        )
    status = "complete" if not failures else ("partial" if successful else "failed")
    if successful:
        calculated, benchmarks = calculate_metrics(successful, criteria)
        ranked = rank_suppliers(calculated)
        eligible_count = len(ranked)
        for result in ranked:
            result["eligible_supplier_count"] = eligible_count
    else:
        ranked, benchmarks = [], {}
    persist_run(db_path, run_id, status, run_warnings, ranked)
    return {
        "rfp_run_id": run_id,
        "created_at": created_at,
        "status": status,
        "procurement": {
            "title": profile.title,
            "category": profile.category,
            "scope": profile.scope,
        },
        "criteria_snapshot": [criterion.as_dict() for criterion in criteria],
        "provider": {"name": provider_name, "model": model_name},
        "prompt_version": PROMPT_VERSION,
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "suppliers": ranked,
        "benchmarks": benchmarks,
        "warnings": run_warnings,
        "failures": failures,
    }
