import hashlib
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import import_source


@pytest.fixture
def registered_backup(tmp_path, monkeypatch):
    payload = b"PGDMP\x01\x10\x00synthetic"
    archive = tmp_path / "synthetic.backup"
    archive.write_bytes(payload)
    monkeypatch.setattr(import_source, "ARCHIVE_SIZE", len(payload))
    monkeypatch.setattr(import_source, "ARCHIVE_SHA256", hashlib.sha256(payload).hexdigest())
    return archive


def test_inventory_reads_verified_bytes_and_only_returns_aggregates(registered_backup, monkeypatch):
    run = Mock(
        return_value=Mock(
            stdout=(
                b"; Archive created at arbitrary date\r\n"
                b"1; 1259 200 TABLE public synthetic_table synthetic_owner\r\n"
                b"2; 0 200 TABLE DATA public synthetic_table synthetic_owner\r\n"
                b"3; 0 0 TABLE ATTACH public synthetic_table synthetic_owner\r\n"
            )
        )
    )
    monkeypatch.setattr(subprocess, "run", run)

    assert import_source.audit_source(registered_backup, "example-pg-restore") == {
        "sha256": import_source.ARCHIVE_SHA256,
        "size_bytes": import_source.ARCHIVE_SIZE,
        "archive_version": "1.16-0",
        "listed_toc_entries": 3,
        "tables": 1,
        "table_data_entries": 1,
    }
    run.assert_called_once_with(
        ["example-pg-restore", "--list", "--format=custom"],
        input=registered_backup.read_bytes(),
        capture_output=True,
        check=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("transform", "message"),
    [
        (lambda payload: payload + b"!", "size"),
        (lambda payload: b"X" + payload[1:], "version"),
        (lambda payload: payload[:-1] + b"!", "SHA-256"),
    ],
)
def test_invalid_source_stops_before_running_pg_restore(
    registered_backup: Path, monkeypatch, transform, message
):
    registered_backup.write_bytes(transform(registered_backup.read_bytes()))
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(import_source.SourceAuditError, match=message):
        import_source.audit_source(registered_backup)
    run.assert_not_called()


def test_pg_restore_failure_does_not_expose_process_output(registered_backup, monkeypatch):
    error = subprocess.CalledProcessError(1, "pg_restore", stderr=b"synthetic private output")
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=error))

    with pytest.raises(import_source.SourceAuditError, match=r"exit code 1") as failure:
        import_source.audit_source(registered_backup)
    assert "synthetic private output" not in str(failure.value)
