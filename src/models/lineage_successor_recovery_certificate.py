"""Persisted evidence-only successor certificate; not an admission grant."""
from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from src.core.database import Base


class LineageSuccessorRecoveryCertificateRecord(Base):
    __tablename__ = 'lineage_successor_recovery_certificates'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    source_id = Column(UUID(as_uuid=True), nullable=False)
    snapshot_id = Column(UUID(as_uuid=True), nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    target_source_external_id = Column(String(512), nullable=False)
    target_asset_external_id = Column(String(512), nullable=False)
    target_revision_external_id = Column(String(512), nullable=False)
    chunk_revision_external_id = Column(String(512), nullable=False)
    chunk_content_sha256 = Column(String(64), nullable=False)
    chunk_observation_id = Column(UUID(as_uuid=True), nullable=False)
    index_observation_id = Column(UUID(as_uuid=True), nullable=False)
    binding_revision = Column(BigInteger, nullable=False)
    issued_by_user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    evidence_kind = Column(String(40), nullable=False, server_default='physical_successor_preflight_v1')
    grants_admission = Column(Boolean, nullable=False, server_default='false')
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("snapshot_sha256 ~ '^[0-9a-f]{64}$'", name='ck_successor_cert_snapshot_hash'),
        CheckConstraint("chunk_content_sha256 ~ '^[0-9a-f]{64}$'", name='ck_successor_cert_chunk_hash'),
        CheckConstraint('binding_revision >= 1', name='ck_successor_cert_binding_revision'),
        CheckConstraint("evidence_kind = 'physical_successor_preflight_v1'", name='ck_successor_cert_kind'),
        CheckConstraint('grants_admission IS FALSE', name='ck_successor_cert_not_admission'),
        ForeignKeyConstraint(['organization_id','chunk_observation_id'], ['lineage_observations.organization_id','lineage_observations.id'], name='fk_successor_cert_chunk_observation', ondelete='RESTRICT'),
        ForeignKeyConstraint(['organization_id','index_observation_id'], ['lineage_observations.organization_id','lineage_observations.id'], name='fk_successor_cert_index_observation', ondelete='RESTRICT'),
        UniqueConstraint('organization_id','target_source_external_id','target_asset_external_id','target_revision_external_id', name='uq_successor_cert_exact_revision'),
        Index('ix_successor_cert_tenant_source_snapshot','organization_id','source_id','snapshot_id'),
    )
