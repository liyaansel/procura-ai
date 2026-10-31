"""Streamlit interface for configurable, evidence-backed RFP evaluation."""

from pathlib import Path
import json
import uuid

import pandas as pd
import streamlit as st

from database import (
    DEFAULT_DB_PATH,
    connect,
    fetch_run,
    initialize_database,
    load_profile,
    save_profile,
)
from models import Criterion, EvaluationError, ProcurementProfile
from providers.anthropic_provider import AnthropicProvider
from providers.openai_provider import OpenAIProvider
from services.exporter import export_run
from services.orchestrator import run_evaluation
from services.validator import validate_criteria

st.set_page_config(page_title="Procura AI | Supplier Evaluation", page_icon="📋", layout="wide")
DB_PATH = Path(DEFAULT_DB_PATH)
initialize_database(DB_PATH)


def _load_profile_options() -> list[str]:
    with connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT procurement_id FROM procurement_profiles ORDER BY title"
        ).fetchall()
    return [row["procurement_id"] for row in rows]


def _provider(provider_name: str, model_name: str, api_key: str):
    try:
        if provider_name == "OpenAI":
            return OpenAIProvider(api_key, model_name)
        return AnthropicProvider(api_key, model_name)
    except Exception:
        raise EvaluationError(
            "Could not initialize the provider client. Check the provider package and credentials."
        ) from None


def _reset_model_for_provider() -> None:
    st.session_state["model_name"] = (
        "gpt-4o-mini"
        if st.session_state["provider_name"] == "OpenAI"
        else "claude-3-5-sonnet-latest"
    )


def _render_run_results(run: dict, include_export: bool = False) -> None:
    st.caption(
        f"Run {run['rfp_run_id']} · {run['status']} · "
        f"{run['provider_name']} / {run['model_name']} · {run['created_at']}"
    )
    if run["status"] == "complete":
        st.success(f"Evaluation completed. Run ID: {run['rfp_run_id']}")
    elif run["status"] == "partial":
        st.warning(f"Evaluation partially completed. Run ID: {run['rfp_run_id']}")
    else:
        st.error(f"Evaluation failed. Run ID: {run['rfp_run_id']}")
    for warning in run["warnings"]:
        st.warning(warning)

    if run["suppliers"]:
        leaderboard = [
            {
                "Rank": item["rank"],
                "Supplier": item["supplier_name"],
                "Absolute weighted score": round(item["absolute_score"], 2),
                "PPI": round(item["ppi"], 2),
                "Submission date": item["submission_date"],
                "Experience (0–5)": item["experience_rating"],
            }
            for item in run["suppliers"]
        ]
        st.dataframe(pd.DataFrame(leaderboard), hide_index=True, width="stretch")
        chosen = st.selectbox(
            "Detailed scorecard",
            [item["supplier_name"] for item in run["suppliers"]],
            key=(
                f"scorecard_{run['rfp_run_id']}_"
                f"{'history' if include_export else 'latest'}"
            ),
        )
        supplier = next(item for item in run["suppliers"] if item["supplier_name"] == chosen)
        st.write(supplier["overall_summary"])
        st.write("**Risks**", supplier["risks"] or "None reported.")
        st.dataframe(pd.DataFrame(supplier["criteria"]), hide_index=True, width="stretch")
        st.write(supplier["tie_break_explanation"])
        for warning in supplier["warnings"]:
            st.warning(warning)
    else:
        st.info("No supplier evaluation completed successfully for this run.")

    if include_export:
        export_payload = export_run(
            {
                "rfp_run_id": run["rfp_run_id"],
                "created_at": run["created_at"],
                "status": run["status"],
                "procurement": {
                    "title": run["procurement_title"],
                    "category": run["procurement_category"],
                    "scope": run["procurement_scope"],
                },
                "criteria_snapshot": run["criteria_snapshot"],
                "provider": {"name": run["provider_name"], "model": run["model_name"]},
                "prompt_version": run["prompt_version"],
                "schema_version": run["schema_version"],
                "suppliers": run["suppliers"],
                "benchmarks": {
                    criterion_id: max(
                        (
                            card["benchmark"]
                            for item in run["suppliers"]
                            for card in item["criteria"]
                            if card["criterion_id"] == criterion_id
                        ),
                        default=0,
                    )
                    for criterion_id in [c["criterion_id"] for c in run["criteria_snapshot"]]
                },
                "warnings": run["warnings"],
            },
            st.session_state.get("runtime_api_key"),
        )
        st.download_button(
            "Download complete JSON",
            data=export_payload,
            file_name=f"rfp-run-{run['rfp_run_id']}.json",
            mime="application/json",
            key=f"download_{run['rfp_run_id']}",
        )


st.title("Procura AI")
st.caption("Evidence-backed supplier evaluation with configurable criteria and deterministic rankings.")
profile_ids = _load_profile_options()
selected_id = st.sidebar.selectbox("Procurement profile", profile_ids)
profile = load_profile(DB_PATH, selected_id)

if "provider_name" not in st.session_state:
    st.session_state["provider_name"] = "OpenAI"
if "model_name" not in st.session_state:
    st.session_state["model_name"] = "gpt-4o-mini"

st.sidebar.subheader("Provider setup")
provider_name = st.sidebar.selectbox(
    "Provider",
    ["OpenAI", "Anthropic"],
    key="provider_name",
    on_change=_reset_model_for_provider,
)
model_name = st.sidebar.text_input(
    "Model",
    key="model_name",
    help="Enter a model supported by your provider account.",
)
api_key = st.sidebar.text_input(
    "API key",
    type="password",
    key="runtime_api_key",
    help="Used only in this Streamlit session; never written to files or SQLite.",
)
col_test, col_clear = st.sidebar.columns(2)
with col_test:
    if st.button("Test connection"):
        if not api_key or not model_name.strip():
            st.sidebar.error("Enter an API key and model before testing.")
        else:
            try:
                _provider(provider_name, model_name.strip(), api_key).test_connection()
                st.sidebar.success("Provider connection succeeded.")
            except Exception as exc:
                st.sidebar.error(str(exc))
with col_clear:
    if st.button("Clear key"):
        st.session_state.pop("runtime_api_key", None)
        st.rerun()
st.sidebar.caption(
    "The key is held in this session only. Clearing it does not guarantee immediate erasure from every process-memory location."
)

tabs = st.tabs(["Procurement setup", "Supplier input & evaluation", "Leaderboard & export"])

with tabs[0]:
    st.subheader("Procurement setup")
    st.write("The sample rubric is editable. Active criterion weights must total 100%.")
    title = st.text_input("Procurement title", profile.title)
    category = st.text_input("Procurement category", profile.category)
    scope = st.text_area("Scope / buyer requirements", profile.scope, height=100)
    criteria_frame = pd.DataFrame([criterion.as_dict() for criterion in profile.criteria])
    edited = st.data_editor(
        criteria_frame,
        key="criteria_editor",
        num_rows="dynamic",
        width="stretch",
        column_config={
            "criterion_id": st.column_config.TextColumn("Stable criterion ID", required=True),
            "name": st.column_config.TextColumn(required=True),
            "description": st.column_config.TextColumn(required=True),
            "weight": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=1.0),
            "max_score": st.column_config.NumberColumn(min_value=0.01, step=1.0),
            "is_active": st.column_config.CheckboxColumn(),
            "evaluation_guidance": st.column_config.TextColumn(),
        },
    )
    if st.button("Save procurement and criteria"):
        try:
            criteria = tuple(
                Criterion(
                    criterion_id=str(row["criterion_id"]).strip(),
                    name=str(row["name"]).strip(),
                    description=str(row["description"]).strip(),
                    weight=float(row["weight"]),
                    max_score=float(row["max_score"]),
                    is_active=bool(row["is_active"]),
                    evaluation_guidance=str(row.get("evaluation_guidance", "")),
                )
                for row in edited.to_dict("records")
            )
            if not title.strip() or not category.strip() or not scope.strip():
                raise EvaluationError("Title, category, and procurement scope are required.")
            identifiers = [criterion.criterion_id for criterion in criteria]
            if any(not item for item in identifiers) or len(identifiers) != len(set(identifiers)):
                raise EvaluationError("Criterion IDs must be nonempty and unique.")
            with connect(DB_PATH) as connection:
                save_profile(
                    connection,
                    ProcurementProfile(selected_id, title.strip(), category.strip(), scope.strip(), criteria),
                )
            st.success("Procurement profile saved.")
            st.rerun()
        except (ValueError, KeyError, EvaluationError) as exc:
            st.error(str(exc))
    with st.expander("Create another procurement profile"):
        with st.form("create_profile_form"):
            new_title = st.text_input("New procurement title")
            new_category = st.text_input("New procurement category")
            new_scope = st.text_area("New procurement scope")
            create_profile = st.form_submit_button("Create profile using this rubric")
        if create_profile:
            if not new_title.strip() or not new_category.strip() or not new_scope.strip():
                st.error("Enter a title, category, and scope for the new profile.")
            else:
                profile_id = f"procurement-{uuid.uuid4()}"
                with connect(DB_PATH) as connection:
                    save_profile(
                        connection,
                        ProcurementProfile(
                            profile_id,
                            new_title.strip(),
                            new_category.strip(),
                            new_scope.strip(),
                            profile.criteria,
                        ),
                    )
                st.success("New procurement profile created with a copy of the current rubric. Select it from the sidebar to edit its criteria.")
                st.rerun()

with tabs[1]:
    st.subheader("Supplier input")
    try:
        validate_criteria(list(profile.criteria))
        st.success(f"Active criteria are valid ({sum(c.weight for c in profile.criteria if c.is_active):g}% total weight).")
    except EvaluationError as exc:
        st.error(str(exc))
    uploads = st.file_uploader("Upload supplier proposal PDFs", type=["pdf"], accept_multiple_files=True)
    submissions = []
    if uploads:
        st.info("Enter supplier metadata for each PDF. Experience rating uses a 0–5 scale.")
        for index, uploaded in enumerate(uploads):
            with st.expander(uploaded.name, expanded=True):
                name = st.text_input("Supplier name", value=Path(uploaded.name).stem, key=f"name_{index}")
                submission_date = st.date_input("Submission date", key=f"date_{index}")
                rating = st.number_input("Historical experience rating (0–5)", 0.0, 5.0, 3.0, 0.5, key=f"rating_{index}")
                submissions.append(
                    {
                        "filename": uploaded.name,
                        "content": uploaded.getvalue(),
                        "supplier_name": name,
                        "submission_date": submission_date.isoformat(),
                        "experience_rating": rating,
                    }
                )
    if st.button("Evaluate suppliers", type="primary"):
        if not api_key:
            st.error("Enter an API key in the sidebar before starting evaluation.")
        else:
            try:
                validate_criteria(list(profile.criteria))
                progress = st.progress(0, text="Preparing sequential supplier evaluation…")

                def update_progress(completed, total, supplier_name):
                    progress.progress(
                        completed / total,
                        text=f"Evaluated {completed} of {total} suppliers · {supplier_name}",
                    )

                result = run_evaluation(
                    str(DB_PATH),
                    profile,
                    submissions,
                    _provider(provider_name, model_name.strip(), api_key),
                    provider_name,
                    model_name.strip(),
                    update_progress,
                )
                st.session_state["latest_run_id"] = result["rfp_run_id"]
            except (EvaluationError, ValueError) as exc:
                st.error(str(exc))
            except Exception:
                st.error("Evaluation could not be completed due to a persistence or application error.")

    latest_run_id = st.session_state.get("latest_run_id")
    if latest_run_id:
        st.subheader("Evaluation results")
        try:
            _render_run_results(fetch_run(DB_PATH, latest_run_id))
        except (EvaluationError, ValueError, json.JSONDecodeError) as exc:
            st.error(str(exc))

with tabs[2]:
    st.subheader("Past runs and export")
    run_id = st.text_input("Run ID", value=st.session_state.get("latest_run_id", ""))
    if run_id:
        try:
            run = fetch_run(DB_PATH, run_id)
            _render_run_results(run, include_export=True)
        except (EvaluationError, ValueError, json.JSONDecodeError) as exc:
            st.error(str(exc))
