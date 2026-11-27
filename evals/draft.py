"""Helper for writing golden items. It never fills expected answers.

    uv run --project backend python -m evals.draft add --question "..." --lang fr \\
        --stage working [--nationality algerian] [--behaviour answer] [--origin "..."]
    uv run --project backend python -m evals.draft sources TEXT [--type law] [--limit 20]

`add` appends a draft item (next free id, empty `must_cite`, `key_facts` and verification
fields) to `evals/golden/<set>.yaml`, keeping the file's existing text and comments.
`sources` lists source references whose title, source id or URL contains TEXT, to fill in
`must_cite` and `winning_source` by hand.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import get_args

from evals.golden_set import GoldenItem, GoldenSet, load_golden_set, set_path
from ingestion.manifest import JourneyStage

STAGES = get_args(JourneyStage)


def next_id(golden: GoldenSet) -> str:
    numbers = [int(item.id[1:]) for item in golden.items]
    return f"g{max(numbers, default=0) + 1:03d}"


def item_block(item: GoldenItem) -> str:
    """YAML text of a draft item, indented for the `items:` list. Strings are JSON-quoted
    (valid YAML) so that any punctuation in the question is safe."""
    lines = [
        f"  - id: {item.id}",
        f"    question: {json.dumps(item.question, ensure_ascii=False)}",
        f"    lang: {item.lang}",
        f"    stage: {item.stage if item.stage else 'null'}",
        f"    nationality: {item.nationality}",
        f"    expected_behaviour: {item.expected_behaviour}",
        f"    origin: {json.dumps(item.origin, ensure_ascii=False)}",
        "    must_cite: []",
        "    winning_source: null",
        "    key_facts: []",
        "    verified_by: null",
        "    verified_at: null",
    ]
    return "\n".join(lines) + "\n"


def append_item(path: Path, item: GoldenItem) -> None:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^items:\s*\[\]\s*$", "items:", text)
    if not re.search(r"(?m)^items:\s*$", text):
        raise ValueError(f"{path.name}: no top-level 'items:' list")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + item_block(item), encoding="utf-8")
    load_golden_set(path)  # the file must still be valid


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "add":
        path = set_path(args.set)
        golden = load_golden_set(path)
        item = GoldenItem(
            id=next_id(golden),
            question=args.question,
            lang=args.lang,
            stage=args.stage,
            nationality=args.nationality,
            expected_behaviour=args.behaviour,
            origin=args.origin,
        )
        append_item(path, item)
        print(f"{item.id} added to {path.name} (draft: fill must_cite, key_facts, verified_*)")
        return 0
    for ref, title in _matching_sources(args.text, args.type, args.limit):
        print(f"{ref}\t{title}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.draft")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="append a draft item")
    add.add_argument("--set", default="golden")
    add.add_argument("--question", required=True)
    add.add_argument("--lang", choices=("fr", "en"), required=True)
    add.add_argument("--stage", choices=STAGES, default=None)
    add.add_argument("--nationality", choices=("any", "algerian"), default="any")
    add.add_argument(
        "--behaviour", choices=("answer", "refuse", "handoff", "abstain"), default="answer"
    )
    add.add_argument("--origin", default="")
    sources = sub.add_parser("sources", help="find source references")
    sources.add_argument("text")
    sources.add_argument("--type", choices=("law", "treaty", "fact_sheet", "prefecture"))
    sources.add_argument("--limit", type=int, default=20)
    return parser


def _matching_sources(text: str, source_type: str | None, limit: int) -> list[tuple[str, str]]:
    from sqlalchemy import or_, select
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from db.models import Chunk
    from db.session import make_engine

    pattern = f"%{text}%"
    query = (
        select(Chunk.source_type, Chunk.source_id, Chunk.title)
        .where(
            or_(
                Chunk.title.ilike(pattern),
                Chunk.source_id.ilike(pattern),
                Chunk.url.ilike(pattern),
            )
        )
        .distinct()
        .order_by(Chunk.source_type, Chunk.source_id)
        .limit(limit)
    )
    if source_type is not None:
        query = query.where(Chunk.source_type == source_type)
    engine = make_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            return [(f"{r.source_type}:{r.source_id}", r.title) for r in session.execute(query)]
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
