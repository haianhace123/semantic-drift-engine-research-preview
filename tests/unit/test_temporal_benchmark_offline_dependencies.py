"""Offline benchmarking must not require an embedding provider or its SDK."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from benchmarks.temporal_knowledge_integrity.run_benchmark import (
    DEFAULT_CASES_PATH,
    run_benchmark,
)


def test_standard_library_only_cli_runs_without_provider_sdk():
    repo = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-S", "-m", "benchmarks.temporal_knowledge_integrity.run_benchmark"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert [line.strip() for line in completed.stdout.splitlines()
            if line.startswith("Method:")] == [
        "Method: normalized_text_change",
        "Method: lexical_change",
        "Method: token_change",
    ]
    assert "Embedding:" not in completed.stdout


def test_offline_benchmark_never_imports_embedding_adapter(monkeypatch):
    # A new process with -S above is the definitive regression; this also
    # protects callers that import/run the module in an existing process.
    import builtins

    imported = builtins.__import__

    def forbidden_embedding_import(name, *args, **kwargs):
        if name == "benchmarks.temporal_knowledge_integrity.embedding_adapter":
            raise AssertionError("Offline run loaded an optional embedding dependency")
        return imported(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbidden_embedding_import)
    results = run_benchmark(
        cases_path=DEFAULT_CASES_PATH,
        maximum_false_positive_rate=0.0,
        include_embedding=False,
    )
    assert len(results) == 3
    assert all(r.method != "semantic_magnitude" for r in results)


def test_explicit_embedding_mode_still_loads_provider_adapter(monkeypatch):
    calls = {"constructed": 0, "embedded": 0}

    class FakeAdapter:
        def __init__(self):
            calls["constructed"] += 1
            self.metadata = SimpleNamespace(provider="synthetic", model="fake", dimensions=1)

        def embed_text(self, text):
            calls["embedded"] += 1
            return (float(len(text)),)

        def score_embeddings(self, before, after):
            delta = abs(before[0] - after[0])
            return SimpleNamespace(semantic_magnitude=delta / (1.0 + delta))

    fake_module = ModuleType("benchmarks.temporal_knowledge_integrity.embedding_adapter")
    fake_module.ProductionEmbeddingAdapter = FakeAdapter
    monkeypatch.setitem(sys.modules, fake_module.__name__, fake_module)

    results = run_benchmark(
        cases_path=DEFAULT_CASES_PATH,
        maximum_false_positive_rate=0.0,
        include_embedding=True,
    )
    assert [r.method for r in results] == [
        "normalized_text_change", "lexical_change", "token_change", "semantic_magnitude"
    ]
    assert calls["constructed"] == 1
    assert calls["embedded"] > 0
