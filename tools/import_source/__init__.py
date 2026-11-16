"""Local inspection of the registered organizer backup."""

import hashlib
import re
import subprocess
from pathlib import Path

ARCHIVE_SIZE = 19_675_929
ARCHIVE_SHA256 = "c9cd546ed3bfeefb98b162d6683bce9096bdb03ada84fd683431683c5c6dff0e"
ARCHIVE_HEADER = b"PGDMP\x01\x10\x00"
TOC_ENTRY = re.compile(rb"^\d+; \d+ \d+ (.+)$", re.MULTILINE)


class SourceAuditError(Exception):
    """The registered source could not be safely inspected."""


def read_registered_archive(archive: Path) -> bytes:
    """Read a bounded snapshot and verify the registered fingerprint."""
    try:
        with archive.open("rb") as source:
            payload = source.read(ARCHIVE_SIZE + 1)
    except OSError as error:
        raise SourceAuditError("Could not read the backup file.") from error

    if len(payload) != ARCHIVE_SIZE:
        raise SourceAuditError("Backup size does not match the registered source.")
    if not payload.startswith(ARCHIVE_HEADER):
        raise SourceAuditError("Expected a PostgreSQL custom backup with archive version 1.16-0.")
    fingerprint = hashlib.sha256(payload).hexdigest()
    if fingerprint != ARCHIVE_SHA256:
        raise SourceAuditError("Backup SHA-256 does not match the registered source.")

    return payload


def run_pg_restore(pg_restore: str, payload: bytes, *args: str) -> bytes:
    # Use stdin so the child reads the same bytes that were verified.
    try:
        result = subprocess.run(
            [pg_restore, *args, "--format=custom"],
            input=payload,
            capture_output=True,
            check=True,
            timeout=60,
        )
    except FileNotFoundError as error:
        raise SourceAuditError(
            "pg_restore was not found. Pass its executable with --pg-restore."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise SourceAuditError("pg_restore timed out while reading the backup.") from error
    except subprocess.CalledProcessError as error:
        raise SourceAuditError(
            f"pg_restore could not read archive version 1.16-0 (exit code {error.returncode})."
        ) from error
    except OSError as error:
        raise SourceAuditError("Could not start pg_restore.") from error

    return result.stdout


def audit_source(archive: Path, pg_restore: str = "pg_restore") -> dict[str, int | str]:
    """Verify and list the same archive bytes without connecting to PostgreSQL."""
    payload = read_registered_archive(archive)
    entries = TOC_ENTRY.findall(run_pg_restore(pg_restore, payload, "--list"))
    if not entries:
        raise SourceAuditError("pg_restore returned no recognizable inventory entries.")
    return {
        "sha256": ARCHIVE_SHA256,
        "size_bytes": len(payload),
        "archive_version": "1.16-0",
        "listed_toc_entries": len(entries),
        "tables": sum(
            entry.startswith(b"TABLE ") and not entry.startswith((b"TABLE DATA ", b"TABLE ATTACH "))
            for entry in entries
        ),
        "table_data_entries": sum(entry.startswith(b"TABLE DATA ") for entry in entries),
    }
