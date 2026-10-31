import json
import sqlite3

import pymupdf as fitz
import pytest

from database import (
    connect,
    create_run_record,
    initialize_database,
    persist_run,
    load_profile,
    save_profile,
)
from models import Criterion, EvaluationError, ProcurementProfile
from services.exporter import assert_no_secret, export_run
from services.pdf_extractor import PDFExtractionError, extract_pdf
from services.ranking import rank_suppliers
from services.scoring import calculate_metrics
from services.validator import normalize_response, validate_criteria


@pytest.fixture
def criteria():
    return [
        Criterion("quality", "Quality", "Quality evidence", 60, 10),
        Criterion("price", "Price", "Commercial value", 40, 10),
    ]


def test_validate_criteria_accepts_total_100(criteria):
    assert validate_criteria(criteria) == criteria


@pytest.mark.parametrize(
    "items",
    [
        [Criterion("q", "Q", "", 101, 10)],
        [Criterion("q", "Q", "", 100, 0)],
        [Criterion("q", "Q", "", 0, 10, False)],
        [Criterion("q", "Q", "", -1, 10), Criterion("p", "P", "", 101, 10)],
    ],
)
def test_validate_criteria_rejects_invalid_rubric(items):
    with pytest.raises(EvaluationError):
        validate_criteria(items)


def test_pdf_extraction_success_and_scanned_handling():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Readable proposal content")
    content = document.tobytes()
    document.close()
    assert extract_pdf("proposal.pdf", content)[0]["text"] == "Readable proposal content"

    blank = fitz.open()
    blank.new_page()
    blank_content = blank.tobytes()
    blank.close()
    with pytest.raises(PDFExtractionError, match="OCR"):
        extract_pdf("blank.pdf", blank_content)


def test_pdf_extraction_rejects_unsupported_and_malformed():
    with pytest.raises(PDFExtractionError, match="only PDF"):
        extract_pdf("proposal.txt", b"not a pdf")
    with pytest.raises(PDFExtractionError, match="not a valid PDF"):
        extract_pdf("proposal.pdf", b"broken")


def test_normalization_missing_duplicate_unknown_and_invalid(criteria):
    raw = {
        "supplier_name": "Supplier",
        "criteria": [
            {"criterion_id": "quality", "score": 5, "evidence": "Proof", "justification": "Good"},
            {"criterion_id": "quality", "score": 8},
            {"criterion_id": "other", "score": 10},
            {"criterion_id": "price", "score": "bad"},
        ]
    }
    result = normalize_response(raw, criteria, "Supplier")
    cards = {item["criterion_id"]: item for item in result["criteria"]}
    assert cards["quality"]["score"] == 0
    assert cards["quality"]["validation_status"] == "normalized_duplicate"
    assert cards["price"]["score"] == 0
    assert cards["price"]["validation_status"] == "normalized_invalid"
    assert any("unknown criterion" in warning.lower() for warning in result["warnings"])


def test_normalization_clips_negative_and_over_max(criteria):
    result = normalize_response(
        {"supplier_name": "Supplier", "criteria": [
            {"criterion_id": "quality", "score": -2, "evidence": "", "justification": ""},
            {"criterion_id": "price", "score": 15, "evidence": "", "justification": ""},
        ]},
        criteria,
        "Supplier",
    )
    assert [card["score"] for card in result["criteria"]] == [0, 10]
    assert all(card["validation_status"] == "normalized_clipped" for card in result["criteria"])


def test_malformed_provider_json(criteria):
    with pytest.raises(EvaluationError, match="malformed JSON"):
        normalize_response("{broken", criteria, "Supplier")


def test_valid_provider_json_response(criteria):
    response = json.dumps({
        "supplier_name": "Supplier",
        "criteria": [
            {"criterion_id": "quality", "score": 8, "evidence": "Quote", "justification": "Supported"},
            {"criterion_id": "price", "score": 6, "evidence": "Price table", "justification": "Clear"},
        ],
        "risks": [],
        "overall_summary": "Summary",
    })
    normalized = normalize_response(response, criteria, "Supplier")
    assert [item["score"] for item in normalized["criteria"]] == [8, 6]
    assert not normalized["warnings"]


def test_missing_criterion_is_explicit(criteria):
    result = normalize_response({"supplier_name": "Supplier", "criteria": []}, criteria, "Supplier")
    assert all(card["validation_status"] == "normalized_missing" for card in result["criteria"])
    assert len(result["warnings"]) == 2


def test_invalid_page_and_unmatched_evidence_are_flagged(criteria):
    result = normalize_response(
        {
            "supplier_name": "Supplier",
            "criteria": [
                {
                    "criterion_id": "quality",
                    "score": 5,
                    "evidence": "Invented excerpt",
                    "justification": "Example",
                    "page_number": 4,
                }
            ],
        },
        criteria,
        "Supplier",
        [{"page_number": 1, "text": "Actual proposal content"}],
    )
    quality = result["criteria"][0]
    assert quality["page_number"] is None
    assert any("Invalid page number" in warning for warning in result["warnings"])
    assert any("could not be matched in the extracted PDF text" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    ("evidence", "page_text"),
    [
        ("The supplier provides a cost-effective service.", "The supplier provides a cost-\neffective service."),
        ("The supplier offers a reliable service", "The supplier offers a reliable service."),
        ("The supplier provides an efficient service", "The supplier provides an efﬁcient service"),
    ],
)
def test_evidence_matching_ignores_pdf_text_formatting(criteria, evidence, page_text):
    result = normalize_response(
        {
            "supplier_name": "Supplier",
            "criteria": [
                {
                    "criterion_id": "quality",
                    "score": 5,
                    "evidence": evidence,
                    "justification": "Supported",
                    "page_number": 1,
                }
            ],
        },
        criteria,
        "Supplier",
        [{"page_number": 1, "text": page_text}],
    )
    assert not any(
        "could not be matched in the extracted PDF text" in warning
        for warning in result["warnings"]
    )


def _supplier(name, quality, price, date="2026-01-01", experience=3):
    return {
        "supplier_name": name,
        "submission_date": date,
        "experience_rating": experience,
        "criteria": [
            {"criterion_id": "quality", "score": quality},
            {"criterion_id": "price", "score": price},
        ],
        "status": "evaluated",
    }


def test_weighted_score_benchmarks_gap_relative_and_exact_ppi(criteria):
    results, benchmarks = calculate_metrics(
        [_supplier("A", 5, 10), _supplier("B", 10, 5)], criteria
    )
    a = next(item for item in results if item["supplier_name"] == "A")
    assert a["absolute_score"] == pytest.approx(70)
    assert a["ppi"] == pytest.approx((50 * 60 + 100 * 40) / 100)
    assert benchmarks == {"quality": 10, "price": 10}
    assert a["criteria"][0]["gap"] == -5
    assert a["criteria"][0]["relative_percentage"] == 50


def test_zero_benchmark_is_neutral(criteria):
    results, _ = calculate_metrics([_supplier("A", 0, 0), _supplier("B", 0, 0)], criteria)
    assert all(item["ppi"] == 100 for item in results)
    assert all(card["benchmark_convention"] == "neutral_zero" for card in results[0]["criteria"])


def test_ranking_tie_breaks_and_casefold():
    values = [
        {"supplier_name": "zeta", "ppi": 90, "submission_date": "2026-01-01", "experience_rating": 5},
        {"supplier_name": "Beta", "ppi": 90, "submission_date": "2026-01-02", "experience_rating": 5},
        {"supplier_name": "alpha", "ppi": 90, "submission_date": "2026-01-01", "experience_rating": 4},
        {"supplier_name": "Alpha", "ppi": 90, "submission_date": "2026-01-01", "experience_rating": 4},
        {"supplier_name": "Best", "ppi": 91, "submission_date": "2026-01-09", "experience_rating": 0},
    ]
    ordered = rank_suppliers(values)
    assert [item["supplier_name"] for item in ordered] == ["Best", "zeta", "Alpha", "alpha", "Beta"]
    assert [item["rank"] for item in ordered] == [1, 2, 3, 4, 5]


def test_submission_dates_require_iso_format():
    from services.ranking import parse_submission_date

    with pytest.raises(EvaluationError, match="YYYY-MM-DD"):
        parse_submission_date("20260101")
    assert parse_submission_date("2026-01-01").isoformat() == "2026-01-01"


def test_sqlite_persistence_and_transaction_rollback(tmp_path, criteria):
    db = tmp_path / "test.sqlite3"
    initialize_database(db)
    profile = load_profile(db)
    create_run_record(db, "run-1", "2026-01-01T00:00:00Z", profile, "mock", "model", "1")
    with pytest.raises(sqlite3.IntegrityError):
        persist_run(
            str(db),
            "run-1",
            "complete",
            [],
            [
                {"supplier_name": "A", "submission_date": "2026-01-01", "experience_rating": 3, "status": "evaluated"},
                {"supplier_name": "A", "submission_date": "2026-01-01", "experience_rating": 3, "status": "evaluated"},
            ],
        )
    with connect(db) as connection:
        assert connection.execute("SELECT status FROM rfp_runs WHERE rfp_run_id='run-1'").fetchone()["status"] == "running"
        assert connection.execute("SELECT count(*) AS n FROM supplier_results").fetchone()["n"] == 0


def test_procurement_profile_configuration_persists(tmp_path, criteria):
    db = tmp_path / "profiles.sqlite3"
    initialize_database(db)
    updated = ProcurementProfile(
        "custom-category",
        "Custom procurement",
        "Laboratory services",
        "A procurement unrelated to software.",
        tuple(criteria),
    )
    with connect(db) as connection:
        save_profile(connection, updated)
    loaded = load_profile(db, "custom-category")
    assert loaded.category == "Laboratory services"
    assert [item.criterion_id for item in loaded.criteria] == ["price", "quality"]


def test_export_serializes_and_detects_secret(caplog):
    run = {"rfp_run_id": "test", "suppliers": [], "provider": {"name": "mock"}}
    secret = "secret-value"
    exported = export_run(run, secret)
    assert json.loads(exported)["export_version"] == "1.0"
    assert secret not in exported
    assert secret not in caplog.text
    with pytest.raises(ValueError, match="secret"):
        assert_no_secret(exported + secret, secret)
