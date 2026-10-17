from src.services import worker_runtime_identity


def test_create_worker_instance_id_contains_process_identity(
    monkeypatch,
):
    monkeypatch.setattr(
        worker_runtime_identity.socket,
        "gethostname",
        lambda: "worker-host",
    )
    monkeypatch.setattr(
        worker_runtime_identity.os,
        "getpid",
        lambda: 1234,
    )

    class FixedUuid:
        hex = "abcdef1234567890"

    monkeypatch.setattr(
        worker_runtime_identity.uuid,
        "uuid4",
        lambda: FixedUuid(),
    )

    assert (
        worker_runtime_identity.create_worker_instance_id()
        == "worker-host-1234-abcdef12"
    )


def test_create_worker_instance_id_changes_between_incarnations(
    monkeypatch,
):
    monkeypatch.setattr(
        worker_runtime_identity.socket,
        "gethostname",
        lambda: "worker-host",
    )
    monkeypatch.setattr(
        worker_runtime_identity.os,
        "getpid",
        lambda: 1234,
    )

    values = iter(
        (
            "aaaaaaaa11111111",
            "bbbbbbbb22222222",
        )
    )

    class GeneratedUuid:
        def __init__(self, value: str):
            self.hex = value

    monkeypatch.setattr(
        worker_runtime_identity.uuid,
        "uuid4",
        lambda: GeneratedUuid(next(values)),
    )

    first = (
        worker_runtime_identity.create_worker_instance_id()
    )
    second = (
        worker_runtime_identity.create_worker_instance_id()
    )

    assert first == "worker-host-1234-aaaaaaaa"
    assert second == "worker-host-1234-bbbbbbbb"
    assert first != second
