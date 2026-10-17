"""Fingerprint constraints for historical seeder forensics (never cleanup)."""

from scripts.audit_seed_provenance_readonly import (
    SEED_DOMAINS,
    SEED_PAGE_TYPES,
    SEED_TRANSITIONS,
    _hash,
    matches_seed_matrix,
    matches_seed_url,
)


def test_historical_seed_fingerprints_are_exact() -> None:
    assert len(SEED_DOMAINS) == 20
    assert len(SEED_PAGE_TYPES) == 5
    assert len(SEED_TRANSITIONS) == 10
    assert _hash("Data retained for 30 days") == (
        "a2edda61eb732bfd5088c53f73a084852b0ca62113a7c817b25296ffd04c61d1"
    )


def test_a_matching_url_is_only_a_weak_signal() -> None:
    assert matches_seed_url("https://github.com/privacy", "github.com", "privacy")
    assert not matches_seed_url("https://github.com/features", "github.com", "docs")
    assert not matches_seed_url("https://customer.example/privacy", "customer.example", "privacy")


def test_entire_source_matrix_is_required() -> None:
    full = {domain: {"tos", "privacy", "docs"} for domain in SEED_DOMAINS}
    assert matches_seed_matrix(full)
    without_domain = dict(full)
    without_domain.pop("github.com")
    assert not matches_seed_matrix(without_domain)
    wrong_count = dict(full)
    wrong_count["github.com"] = {"tos", "privacy"}
    assert not matches_seed_matrix(wrong_count)
