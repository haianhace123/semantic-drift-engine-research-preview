"""Read-only join attribution must recognize both cross-tenant directions."""
from uuid import uuid4

from sqlalchemy import Column, ForeignKey, MetaData, String, Table, create_engine
from sqlalchemy.orm import Session

from scripts.audit_organization_data_lifecycle_readonly import summarize_indirect_ownership


def test_tenant_owned_children_and_cross_tenant_edges_are_distinguished():
    md = MetaData()
    users = Table("users", md, Column("id", String, primary_key=True), Column("organization_id", String))
    consent = Table("consent_logs", md, Column("id", String, primary_key=True), Column("user_id", String, ForeignKey("users.id")))
    sessions = Table("user_sessions", md, Column("id", String, primary_key=True), Column("user_id", String, ForeignKey("users.id")))
    decisions = Table("lineage_invalidation_decisions", md, Column("id", String, primary_key=True), Column("organization_id", String))
    revisions = Table("lineage_invalidation_decision_path_revisions", md, Column("id", String, primary_key=True), Column("decision_id", String, ForeignKey("lineage_invalidation_decisions.id")))
    observations = Table("lineage_observations", md, Column("id", String, primary_key=True), Column("organization_id", String))
    dependencies = Table("lineage_observation_dependencies", md, Column("id", String, primary_key=True), Column("observation_id", String, ForeignKey("lineage_observations.id")))
    snapshots = Table("page_snapshots", md, Column("id", String, primary_key=True), Column("organization_id", String))
    correlations = Table("temporal_lineage_correlations", md, Column("page_snapshot_id", String, ForeignKey("page_snapshots.id")), Column("lineage_observation_id", String, ForeignKey("lineage_observations.id")))
    target = str(uuid4())
    other = str(uuid4())
    engine = create_engine("sqlite+pysqlite:///:memory:")
    md.create_all(engine)
    with Session(engine) as db:
        db.execute(users.insert(), [{"id":"u1", "organization_id":target}, {"id":"u2", "organization_id":other}])
        db.execute(consent.insert(), [{"id":"c1", "user_id":"u1"}, {"id":"c2", "user_id":"u2"}, {"id":"c3", "user_id":None}])
        db.execute(sessions.insert(), [{"id":"sess", "user_id":"u1"}])
        db.execute(decisions.insert(), [{"id":"d1", "organization_id":target}, {"id":"d2", "organization_id":other}])
        db.execute(revisions.insert(), [{"id":"r1", "decision_id":"d1"}])
        db.execute(observations.insert(), [{"id":"o1", "organization_id":target}, {"id":"o2", "organization_id":other}])
        db.execute(dependencies.insert(), [{"id":"dep", "observation_id":"o1"}])
        db.execute(snapshots.insert(), [{"id":"s1", "organization_id":target}, {"id":"s2", "organization_id":other}])
        db.execute(correlations.insert(), [
            {"page_snapshot_id":"s1", "lineage_observation_id":"o1"},
            {"page_snapshot_id":"s1", "lineage_observation_id":"o2"},
            {"page_snapshot_id":"s2", "lineage_observation_id":"o1"},
        ])
        observed = summarize_indirect_ownership(db, md.tables, target)
        assert observed["indirect_table_counts"] == {
            "consent_logs":1, "user_sessions":1,
            "lineage_invalidation_decision_path_revisions":1,
            "lineage_observation_dependencies":1,
            "temporal_lineage_correlations":3,
        }
        assert observed["correlation_integrity"] == {
            "same_tenant":1, "cross_tenant_outgoing":1, "cross_tenant_incoming":1,
        }
        assert observed["unattributed_consent_records_global"] == 1
