"""Versioned JSON export with explicit secret-leak checks."""

import json
from typing import Any


def export_run(run: dict[str, Any], secret: str | None = None) -> str:
    document = {
        "export_version": "1.0",
        **run,
    }
    serialized = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)
    assert_no_secret(serialized, secret)
    return serialized


def assert_no_secret(text: str, secret: str | None) -> None:
    if secret and secret in text:
        raise ValueError("A secret was detected in output and export was blocked.")
