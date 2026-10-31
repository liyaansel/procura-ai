# Agentic RFP Evaluation — Copilot Starter Artifacts

This package contains:
- `skill.md`: detailed implementation specification for GitHub Copilot in VS Code.
- `synthetic_rfps/`: six fictional 2–4-page supplier proposal PDFs for the same sample procurement.

## Suggested use with GitHub Copilot
1. Open this folder in VS Code.
2. Open `skill.md` and ask Copilot Chat to implement the specification in phases.
3. Ask it to create and test the complete project, then run `python -m pytest -q`.
4. Do not paste API keys into prompts or source code. The intended application accepts keys in its runtime UI only.

## Important
The PDFs are synthetic educational data. The prices, company names, references and claims are fictional. They are designed to create different evidence profiles for testing and do not endorse real suppliers.

The skill specifies generic criteria configuration: the sample procurement is only a test scenario. A completed application should support other products and services by changing the procurement scope and active evaluation criteria, without changing evaluation engine code.
