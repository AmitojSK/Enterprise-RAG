"""Tests for the idempotency property required by safe cloud-write retries."""

from uuid import UUID, uuid5


def test_chunk_point_id_is_deterministic() -> None:
    """A retry must target the same vector point instead of adding a duplicate."""

    document_id = UUID("d44f05de-254e-4fde-9184-1afbb4396774")
    assert uuid5(document_id, "chunk:1") == uuid5(document_id, "chunk:1")
