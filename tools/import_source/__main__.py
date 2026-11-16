import argparse
import json
from pathlib import Path

from . import SourceAuditError, audit_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and list the registered organizer backup.")
    parser.add_argument(
        "archive", nargs="?", type=Path, default=Path("data/private/alfa_course_hackathon.backup")
    )
    parser.add_argument(
        "--pg-restore", default="pg_restore", help="path to the pg_restore executable"
    )
    parser.add_argument(
        "--prepare", action="store_true", help="prepare the local consultation corpus"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/derived/corpus.json"),
        help="destination for --prepare (default: data/derived/corpus.json)",
    )
    args = parser.parse_args()
    if args.output != Path("data/derived/corpus.json") and not args.prepare:
        parser.error("--output requires --prepare")

    try:
        if args.prepare:
            from .catalog import prepare_catalog

            summary = prepare_catalog(args.archive, args.output, args.pg_restore)
        else:
            summary = audit_source(args.archive, args.pg_restore)
    except SourceAuditError as error:
        parser.exit(1, f"Source audit failed: {error}\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
