import pymupdf as fitz
import pytest

from database import initialize_database, load_profile
from models import Criterion, EvaluationError, ProcurementProfile
from services.orchestrator import run_evaluation


class MockProvider:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on

    def evaluate(self, prompt):
        if self.fail_on and self.fail_on in prompt:
            raise RuntimeError("private provider error must not leak")
        import re
        match = re.search(r"Expected supplier name: (.+)", prompt)
        name = match.group(1).splitlines()[0]
        return {
            "supplier_name": name,
            "criteria": [
                {"criterion_id": "quality", "score": 8, "evidence": "Evidence", "justification": "Fit", "page_number": 1},
                {"criterion_id": "price", "score": 6, "evidence": "Price", "justification": "Cost", "page_number": 1},
            ],
            "risks": [],
            "overall_summary": "Mock evaluation",
        }


def _pdf():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Proposal text")
    data = doc.tobytes()
    doc.close()
    return data


def _profile():
    return ProcurementProfile(
        "test",
        "Test procurement",
        "Testing",
        "Test scope",
        (Criterion("quality", "Quality", "", 50, 10), Criterion("price", "Price", "", 50, 10)),
    )


def _submission(name):
    return {
        "filename": f"{name}.pdf",
        "content": _pdf(),
        "supplier_name": name,
        "submission_date": "2026-01-01",
        "experience_rating": 3,
    }


def test_mocked_successful_run_is_persisted(tmp_path):
    db = tmp_path / "app.sqlite3"
    initialize_database(db)
    result = run_evaluation(
        str(db), _profile(), [_submission("Supplier A")], MockProvider(), "Mock", "mock-1"
    )
    assert result["status"] == "complete"
    assert result["suppliers"][0]["rank"] == 1
    assert result["suppliers"][0]["absolute_score"] == pytest.approx(70)
    assert load_profile(db).title


def test_provider_failure_results_in_partial_or_failed_run(tmp_path):
    db = tmp_path / "app.sqlite3"
    initialize_database(db)
    provider = MockProvider(fail_on="Supplier B")
    result = run_evaluation(
        str(db),
        _profile(),
        [_submission("Supplier A"), _submission("Supplier B")],
        provider,
        "Mock",
        "mock-1",
    )
    assert result["status"] == "partial"
    assert len(result["suppliers"]) == 1
    assert "private provider error" not in str(result)


def test_provider_secret_does_not_reach_run_output_or_logs(tmp_path, caplog):
    secret = "key-that-must-not-leak"

    class SecretFailureProvider:
        def evaluate(self, prompt):
            raise RuntimeError(secret)

    db = tmp_path / "app.sqlite3"
    initialize_database(db)
    result = run_evaluation(
        str(db),
        _profile(),
        [_submission("Supplier A")],
        SecretFailureProvider(),
        "Mock",
        "mock-1",
    )
    assert result["status"] == "failed"
    assert secret not in str(result)
    assert secret not in caplog.text


def test_invalid_metadata_blocks_before_run_creation(tmp_path):
    db = tmp_path / "app.sqlite3"
    initialize_database(db)
    duplicate = [_submission("same"), _submission("SAME")]
    with pytest.raises(EvaluationError, match="unique"):
        run_evaluation(str(db), _profile(), duplicate, MockProvider(), "Mock", "mock-1")


def test_all_provider_failures_are_failed_run(tmp_path):
    db = tmp_path / "app.sqlite3"
    initialize_database(db)
    result = run_evaluation(
        str(db),
        _profile(),
        [_submission("Supplier B")],
        MockProvider(fail_on="Supplier B"),
        "Mock",
        "mock-1",
    )
    assert result["status"] == "failed"
    assert result["suppliers"] == []


def test_same_normalized_inputs_have_identical_metrics_and_order(tmp_path):
    db = tmp_path / "app.sqlite3"
    initialize_database(db)
    first = run_evaluation(
        str(db),
        _profile(),
        [_submission("Supplier B"), _submission("Supplier A")],
        MockProvider(),
        "Mock",
        "mock-1",
    )
    second = run_evaluation(
        str(db),
        _profile(),
        [_submission("Supplier B"), _submission("Supplier A")],
        MockProvider(),
        "Mock",
        "mock-1",
    )
    projection = lambda result: [
        (item["supplier_name"], item["absolute_score"], item["ppi"], item["rank"])
        for item in result["suppliers"]
    ]
    assert projection(first) == projection(second)
