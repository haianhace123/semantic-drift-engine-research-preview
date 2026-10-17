"""Source authority is chosen by server and scoped to one organization."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from src.services.lineage_recovery_certificate_source_service import (
    load_current_recovery_source, IssuanceSourceUnavailable,
)

ORG=uuid.uuid4(); SOURCE=uuid.uuid4(); SNAP=uuid.uuid4()

def db_factory(source=None, snapshot=None, *, postgres=True, existing=False):
    db=MagicMock()
    db.in_transaction.return_value=existing
    db.get_bind.return_value.dialect.name='postgresql' if postgres else 'sqlite'
    query1=MagicMock(); query2=MagicMock()
    query1.filter.return_value.one_or_none.return_value=source
    query2.filter.return_value.order_by.return_value.first.return_value=snapshot
    db.query.side_effect=[query1,query2]
    return db

def call(db):
    return load_current_recovery_source(authority_session_factory=lambda:db,
                                        organization_id=ORG,source_id=SOURCE)

def test_source_latest_snapshot_uses_tenant_and_read_only():
    db=db_factory(SimpleNamespace(url='https://good.github.io/policy/'),
                  SimpleNamespace(id=SNAP,content_hash='a'*64))
    out=call(db)
    assert out.url=='https://good.github.io/policy/'
    assert out.snapshot_id==SNAP and out.snapshot_sha256=='a'*64
    assert db.execute.call_args.args[0].text=='SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'
    assert str(ORG) in repr(db.query.call_args_list[0]) or db.query.called
    db.rollback.assert_called_once();db.close.assert_called_once()

def test_missing_source_block():
    db=db_factory(None,None)
    with pytest.raises(IssuanceSourceUnavailable):call(db)
    assert db.rollback.called and db.close.called

def test_missing_snapshot_block():
    db=db_factory(SimpleNamespace(url='https://good.github.io/'),None)
    with pytest.raises(IssuanceSourceUnavailable):call(db)
    assert db.close.called

@pytest.mark.parametrize('digest', ['ABC'*21+'A', 'a'*63, None, 'g'*64])
def test_noncanonical_snapshot_digest_block(digest):
    db=db_factory(SimpleNamespace(url='https://good.github.io/'),SimpleNamespace(id=SNAP,content_hash=digest))
    with pytest.raises(IssuanceSourceUnavailable):call(db)

@pytest.mark.parametrize('postgres,existing',[(False,False),(True,True)])
def test_wrong_transaction_context_block(postgres,existing):
    db=db_factory(postgres=postgres,existing=existing)
    with pytest.raises(IssuanceSourceUnavailable):call(db)
    db.close.assert_called_once()
