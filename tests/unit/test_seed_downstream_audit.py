"""Conservative downstream reference matching must not over-claim provenance."""

from uuid import UUID, uuid4

from scripts.audit_seed_downstream_readonly import (
    classify_alert_reference,
    parse_event_ids,
)


def test_pattern_json_requires_valid_uuid_strings() -> None:
    id_one, id_two = uuid4(), uuid4()
    assert parse_event_ids([str(id_one), "invalid", str(id_two), 5]) == {id_one, id_two}
    assert parse_event_ids({"ids": [str(id_one)]}) == set()
    assert parse_event_ids(None) == set()
    assert parse_event_ids([UUID(int=1)]) == set()


def test_alert_direct_reference_is_conservative() -> None:
    synthetic = uuid4()
    unrelated = uuid4()
    args = {"seed_events": {synthetic}, "related_patterns": set(), "related_anomalies": set(), "related_risk_assessments": set()}
    assert classify_alert_reference(synthetic, None, None, **args)
    assert not classify_alert_reference(unrelated, None, None, **args)
    assert not classify_alert_reference(None, "risk", unrelated, **args)


def test_alert_polymorphic_sources_do_not_guess_other_types() -> None:
    seed = uuid4()
    pattern_id, anomaly_id = uuid4(), uuid4()
    args = {
        "seed_events": {seed},
        "related_patterns": {pattern_id},
        "related_anomalies": {anomaly_id},
        "related_risk_assessments": {seed},
    }
    assert classify_alert_reference(None, "pattern", pattern_id, **args)
    assert classify_alert_reference(None, "anomaly", anomaly_id, **args)
    assert not classify_alert_reference(None, "pattern", anomaly_id, **args)
    assert classify_alert_reference(None, "risk", seed, **args)
    assert not classify_alert_reference(None, "other", seed, **args)
