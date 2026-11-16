"""Container entrypoint behavior using synthetic inputs only."""

import json
import sys
from pathlib import Path

import pytest

from tools.import_source import SourceAuditError, run_pg_restore
from tools.import_source.__main__ import main


@pytest.mark.parametrize("output", [None, "/corpus/corpus.json"])
def test_prepare_cli_destination_and_aggregate_output(monkeypatch, capsys, output):
    calls = []

    def prepare(archive, destination, pg_restore):
        calls.append((archive, destination, pg_restore))
        return {"materials": 2, "chunks": 3}

    monkeypatch.setattr("tools.import_source.catalog.prepare_catalog", prepare)
    arguments = ["import_source", "synthetic.backup", "--prepare"]
    if output:
        arguments += ["--output", output]
    monkeypatch.setattr(sys, "argv", arguments)
    main()
    assert calls == [
        (Path("synthetic.backup"), Path(output or "data/derived/corpus.json"), "pg_restore")
    ]
    assert json.loads(capsys.readouterr().out) == {"materials": 2, "chunks": 3}


def test_cli_missing_archive_fails_without_creating_corpus(monkeypatch, capsys, tmp_path):
    destination = tmp_path / "corpus.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["import_source", str(tmp_path / "missing"), "--prepare", "--output", str(destination)],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Source audit failed: Could not read the backup file.\n"
    assert not destination.exists()


def test_cli_bad_archive_fails_closed(monkeypatch, capsys, tmp_path):
    archive = tmp_path / "synthetic.backup"
    archive.write_bytes(b"synthetic private content")
    monkeypatch.setattr(sys, "argv", ["import_source", str(archive), "--prepare"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Backup size does not match" in captured.err
    assert "synthetic private content" not in captured.err


def test_missing_pg_restore_is_safe(monkeypatch, capsys):
    def prepare(archive, destination, pg_restore):
        return run_pg_restore(pg_restore, b"synthetic private content", "--list")

    monkeypatch.setattr("tools.import_source.catalog.prepare_catalog", prepare)
    monkeypatch.setattr(
        sys,
        "argv",
        ["import_source", "--prepare", "--pg-restore", "/missing/pg_restore"],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "pg_restore was not found" in captured.err
    assert "synthetic private content" not in captured.err


def test_pg_restore_stderr_is_not_exposed(monkeypatch):
    import subprocess

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(2, "pg_restore", stderr=b"private source text")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(SourceAuditError, match=r"\(exit code 2\)") as error:
        run_pg_restore("pg_restore", b"synthetic", "--list")
    assert "private source text" not in str(error.value)


def test_output_requires_prepare(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["import_source", "--output", "/corpus/custom.json"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "--output requires --prepare" in capsys.readouterr().err


def test_importer_can_load_without_site_packages():
    import subprocess

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-S", "-c", "import tools.import_source.catalog"],
        cwd=root,
        env={"PYTHONPATH": str(root / "src")},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_importer_and_runtime_share_private_corpus_uid():
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    importer, runtime = dockerfile.split(" AS runtime", 1)
    assert "mkdir -m 0700 /corpus && chown 10001:10001 /corpus" in importer
    assert importer.index("chown 10001:10001 /corpus") < importer.index("USER 10001:10001")
    assert importer.index("USER 10001:10001") < importer.index('CMD ["python"')
    assert "USER 10001:10001" in runtime


def test_prepared_corpus_remains_private_to_shared_runtime_user(monkeypatch, tmp_path):
    from tools.import_source import catalog

    monkeypatch.setattr(catalog, "read_registered_archive", lambda archive: b"synthetic")
    monkeypatch.setattr(catalog, "run_pg_restore", lambda *args: b"synthetic")
    monkeypatch.setattr(catalog, "read_tables", lambda output: {})
    monkeypatch.setattr(
        catalog, "make_catalog", lambda tables: {"report": {"materials": 0}, "materials": []}
    )
    destination = tmp_path / "private" / "corpus.json"
    catalog.prepare_catalog(tmp_path / "synthetic.backup", destination, "pg_restore")
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700
    assert destination.stat().st_uid == destination.parent.stat().st_uid
    assert json.loads(destination.read_text())["materials"] == []


def test_existing_named_corpus_volume_is_repaired_before_nonroot_import():
    compose = (Path(__file__).resolve().parents[2] / "compose.yaml").read_text()
    initializer = compose.split("  corpus-permissions:\n", 1)[1].split("  prepare:\n", 1)[0]
    prepare = compose.split("  prepare:\n", 1)[1].split("  bot:\n", 1)[0]
    assert 'user: "0:0"' in initializer
    assert "network_mode: none" in initializer
    assert 'command: ["chown 10001:10001 /corpus && chmod 0700 /corpus"]' in initializer
    assert initializer.split("    volumes:\n", 1)[1].strip() == "- corpus:/corpus"
    assert "environment:" not in initializer
    assert "type: bind" not in initializer
    assert "corpus-permissions:\n        condition: service_completed_successfully" in prepare
