"""SQLite schema, rubric configuration, and transactional run persistence."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from models import Criterion, EvaluationError, ProcurementProfile

DEFAULT_DB_PATH = Path("data") / "procura_ai.sqlite3"

SEED_PROFILE = {
    "procurement_id": "sample-cloud-platform",
    "title": "Managed Cloud Platform Services",
    "category": "Cloud infrastructure and managed services",
    "scope": (
        "Provide a secure, reliable managed cloud platform for a mid-sized "
        "organization. Compare proposals using the active configurable criteria."
    ),
}

SEED_CRITERIA = (
    Criterion("technical_fit", "Technical fit", "Fit to stated functional and technical requirements.", 25, 10, True, "Score only requirements evidenced in the proposal."),
    Criterion("security", "Security and privacy", "Security controls, privacy practices, and risk management.", 20, 10, True, "Distinguish specific controls from general claims."),
    Criterion("delivery", "Delivery plan", "Feasibility and clarity of timeline, milestones, and staffing.", 15, 10, True, "Reward specific milestones and accountable roles."),
    Criterion("support", "Support and service levels", "Support model, availability, escalation, and service commitments.", 15, 10, True, "Use only stated service commitments."),
    Criterion("experience", "Relevant experience", "Relevant delivery experience and the quality of references.", 15, 10, True, "Separate documented references from unsupported claims."),
    Criterion("commercial_value", "Commercial value", "Price transparency, assumptions, and overall commercial value.", 10, 10, True, "Do not infer missing costs; flag unclear assumptions."),
)


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS procurement_profiles (
                procurement_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                scope TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_criteria (
                criterion_id TEXT NOT NULL,
                procurement_id TEXT NOT NULL REFERENCES procurement_profiles(procurement_id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                weight REAL NOT NULL,
                max_score REAL NOT NULL,
                is_active INTEGER NOT NULL,
                evaluation_guidance TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (procurement_id, criterion_id)
            );
            CREATE TABLE IF NOT EXISTS rfp_runs (
                rfp_run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                procurement_title TEXT NOT NULL,
                procurement_category TEXT NOT NULL,
                procurement_scope TEXT NOT NULL,
                criteria_snapshot_json TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                schema_version TEXT NOT NULL DEFAULT '1.0',
                warnings_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS supplier_results (
                rfp_run_id TEXT NOT NULL REFERENCES rfp_runs(rfp_run_id) ON DELETE CASCADE,
                supplier_name TEXT NOT NULL,
                submission_date TEXT NOT NULL,
                experience_rating REAL NOT NULL,
                absolute_score REAL,
                ppi REAL,
                final_rank INTEGER,
                result_json TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (rfp_run_id, supplier_name)
            );
            """
        )
        run_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(rfp_runs)")
        }
        if "schema_version" not in run_columns:
            connection.execute(
                "ALTER TABLE rfp_runs ADD COLUMN schema_version TEXT NOT NULL DEFAULT '1.0'"
            )
        existing = connection.execute(
            "SELECT procurement_id FROM procurement_profiles WHERE procurement_id = ?",
            (SEED_PROFILE["procurement_id"],),
        ).fetchone()
        if existing is None:
            save_profile(
                connection,
                ProcurementProfile(
                    procurement_id=SEED_PROFILE["procurement_id"],
                    title=SEED_PROFILE["title"],
                    category=SEED_PROFILE["category"],
                    scope=SEED_PROFILE["scope"],
                    criteria=SEED_CRITERIA,
                ),
            )


def save_profile(
    connection: sqlite3.Connection, profile: ProcurementProfile
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """INSERT INTO procurement_profiles
           (procurement_id, title, category, scope, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(procurement_id) DO UPDATE SET title=excluded.title,
             category=excluded.category, scope=excluded.scope, updated_at=excluded.updated_at""",
        (profile.procurement_id, profile.title, profile.category, profile.scope, now),
    )
    connection.execute(
        "DELETE FROM evaluation_criteria WHERE procurement_id = ?",
        (profile.procurement_id,),
    )
    connection.executemany(
        """INSERT INTO evaluation_criteria
           (criterion_id, procurement_id, name, description, weight, max_score,
            is_active, evaluation_guidance, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                item.criterion_id,
                profile.procurement_id,
                item.name,
                item.description,
                item.weight,
                item.max_score,
                int(item.is_active),
                item.evaluation_guidance,
                now,
            )
            for item in profile.criteria
        ],
    )


def load_profile(db_path: str | Path = DEFAULT_DB_PATH, procurement_id: str | None = None) -> ProcurementProfile:
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM procurement_profiles WHERE procurement_id = ?",
            (procurement_id or SEED_PROFILE["procurement_id"],),
        ).fetchone()
        if row is None:
            raise EvaluationError("The selected procurement profile was not found.")
        criteria = connection.execute(
            "SELECT * FROM evaluation_criteria WHERE procurement_id = ? ORDER BY criterion_id",
            (row["procurement_id"],),
        ).fetchall()
    return ProcurementProfile(
        procurement_id=row["procurement_id"],
        title=row["title"],
        category=row["category"],
        scope=row["scope"],
        criteria=tuple(
            Criterion(
                criterion_id=item["criterion_id"],
                name=item["name"],
                description=item["description"],
                weight=item["weight"],
                max_score=item["max_score"],
                is_active=bool(item["is_active"]),
                evaluation_guidance=item["evaluation_guidance"],
            )
            for item in criteria
        ),
    )


def create_run_record(
    db_path: str | Path,
    run_id: str,
    created_at: str,
    profile: ProcurementProfile,
    provider_name: str,
    model_name: str,
    prompt_version: str,
    schema_version: str = "1.0",
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO rfp_runs
               (rfp_run_id, created_at, status, procurement_title,
                procurement_category, procurement_scope, criteria_snapshot_json,
                provider_name, model_name, prompt_version, schema_version, warnings_json)
               VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, '[]')""",
            (
                run_id,
                created_at,
                profile.title,
                profile.category,
                profile.scope,
                json.dumps([c.as_dict() for c in profile.criteria if c.is_active]),
                provider_name,
                model_name,
                prompt_version,
                schema_version,
            ),
        )


def persist_run(
    db_path: str | Path,
    run_id: str,
    status: str,
    warnings: list[str],
    supplier_results: Iterable[dict[str, Any]],
) -> None:
    connection = connect(db_path)
    try:
        connection.execute("BEGIN")
        connection.execute(
            "UPDATE rfp_runs SET status = ?, warnings_json = ? WHERE rfp_run_id = ?",
            (status, json.dumps(warnings, ensure_ascii=False), run_id),
        )
        for result in supplier_results:
            connection.execute(
                """INSERT INTO supplier_results
                   (rfp_run_id, supplier_name, submission_date, experience_rating,
                    absolute_score, ppi, final_rank, result_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    result["supplier_name"],
                    result["submission_date"],
                    result["experience_rating"],
                    result.get("absolute_score"),
                    result.get("ppi"),
                    result.get("rank"),
                    json.dumps(result, ensure_ascii=False),
                    result["status"],
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_run(db_path: str | Path, run_id: str) -> dict[str, Any]:
    with connect(db_path) as connection:
        run = connection.execute(
            "SELECT * FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise EvaluationError("The requested evaluation run was not found.")
        results = connection.execute(
            "SELECT result_json FROM supplier_results WHERE rfp_run_id = ? ORDER BY final_rank",
            (run_id,),
        ).fetchall()
    data = dict(run)
    data["criteria_snapshot"] = json.loads(data.pop("criteria_snapshot_json"))
    data["warnings"] = json.loads(data.pop("warnings_json"))
    data["suppliers"] = [json.loads(row["result_json"]) for row in results]
    return data
