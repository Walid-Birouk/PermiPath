"""Validates a golden set and prints its mix.

    uv run --project backend python -m evals.validate [--set golden] [--check-sources]
        [--require-verified]

Schema and consistency rules: `evals.golden_set`. `--check-sources` also confirms that every
source in `must_cite` has at least one chunk in `DATABASE_URL`. `--require-verified` fails when
any item is still a draft.

Exit codes: 0 valid, 1 invalid or unknown sources, 2 configuration error.
"""

import argparse
import sys

from pydantic import ValidationError

from evals.golden_set import GoldenSet, load_golden_set, mix, set_path, split_ref

EXIT_OK, EXIT_INVALID, EXIT_CONFIG_ERROR = 0, 1, 2


def unknown_sources(golden: GoldenSet, known: set[tuple[str, str]]) -> list[str]:
    return sorted(ref for ref in golden.source_refs() if split_ref(ref) not in known)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.validate")
    parser.add_argument("--set", default="golden")
    parser.add_argument("--check-sources", action="store_true")
    parser.add_argument("--require-verified", action="store_true")
    args = parser.parse_args(argv)

    path = set_path(args.set)
    try:
        golden = load_golden_set(path)
    except OSError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValidationError as exc:
        print(f"INVALID {path.name}:\n{exc}", file=sys.stderr)
        return EXIT_INVALID

    print(f"{path.name}: version {golden.version}, {len(golden.items)} items")
    for dimension, counts in mix(golden).items():
        print(
            f"  {dimension}: "
            + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none")
        )

    status = EXIT_OK
    drafts = [i.id for i in golden.items if not i.verified]
    if args.require_verified and drafts:
        print(f"INVALID: draft items: {', '.join(drafts)}", file=sys.stderr)
        status = EXIT_INVALID
    if args.check_sources:
        missing = unknown_sources(golden, _known_sources())
        if missing:
            print(f"INVALID: sources with no chunk: {', '.join(missing)}", file=sys.stderr)
            status = EXIT_INVALID
        else:
            print(f"  sources: all {len(golden.source_refs())} found in the index")
    return status


def _known_sources() -> set[tuple[str, str]]:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from db.models import Chunk
    from db.session import make_engine

    engine = make_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            rows = session.execute(select(Chunk.source_type, Chunk.source_id).distinct())
            return {(r.source_type, r.source_id) for r in rows}
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
