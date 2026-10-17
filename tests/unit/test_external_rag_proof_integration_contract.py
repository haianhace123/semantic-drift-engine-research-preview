"""Ensure the actual executable experiment uses the supported guard.

This statically checks integration wiring; behavioral admission tests live in
``test_external_langchain_rag_admission`` and the guard's own unit suite.
"""
from __future__ import annotations

import ast
from pathlib import Path


def test_external_experiment_calls_supported_admission_guard_not_raw_query():
    file = Path(__file__).resolve().parents[2] / "experiments" / "external_langchain_rag.py"
    tree = ast.parse(file.read_text(encoding="utf-8"))
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "admit_langchain_context" in calls
    assert "query_langchain_context_integrity" not in calls
    assert "_require_context_admission" not in calls


def test_experiment_requires_explicit_diagnostic_unknown_policy_flag():
    file = Path(__file__).resolve().parents[2] / "experiments" / "external_langchain_rag.py"
    tree = ast.parse(file.read_text(encoding="utf-8"))
    selectors = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    flags = {n.value for n in selectors}
    assert "--allow-unknown-for-lineage-proof" in flags
    assert "store_true" in flags
