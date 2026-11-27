"""Ingestion CLI.

    cd backend
    uv run python -m ingestion run --all [--dry-run] [--date YYYY-MM-DD]
    uv run python -m ingestion run --source sp|legifrance|treaty [--dry-run]
    uv run python -m ingestion embed [--dry-run] [--limit N]

`run --dry-run` loads and chunks every selected source and prints a summary; it never touches
the database. Without it, chunks are synced to `DATABASE_URL` (versioning + changelog, D-003).
`embed` fills the missing chunk embeddings with `mistral-embed` (`MISTRAL_API_KEY`, billed per
input token); `embed --dry-run` only counts the chunks and characters it would send.

Exit codes: 0 success, 1 at least one source failed (others are still synced), 2 configuration.
"""

import argparse
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import httpx2
from pydantic import ValidationError

from ingestion.legifrance.client import LegifranceClient
from ingestion.legifrance.settings import PisteSettings
from ingestion.manifest import MANIFEST_PATH, load_manifest
from ingestion.pipeline import (
    LoadResult,
    download_feed,
    load_case_law,
    load_fact_sheets,
    load_laws,
    load_treaties,
    read_sheets,
)
from ingestion.service_public.parser import Sheet

EXIT_OK, EXIT_SOURCE_ERROR, EXIT_CONFIG_ERROR = 0, 1, 2
SOURCES = ("sp", "legifrance", "treaty", "case_law", "rules")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "embed":
        return _embed(args.dry_run, args.limit)
    selected = set(SOURCES) if args.all else {args.source}
    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValidationError) as exc:
        print(f"Config error: manifest {args.manifest}: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    results: list[LoadResult] = []
    sheets: list[Sheet] = []
    sheet_errors: list[str] = []
    if selected & {"sp", "legifrance"}:
        try:
            feed = download_feed(manifest.fact_sheets.feed_url)
        except httpx2.HTTPError as exc:
            print(f"FAILED: service-public feed: {exc}", file=sys.stderr)
            return EXIT_SOURCE_ERROR
        sheets, sheet_errors = read_sheets(manifest, feed)
        if "sp" in selected:
            results.append(load_fact_sheets(manifest, sheets, sheet_errors))

    if selected & {"legifrance", "treaty", "case_law"}:
        client = _legifrance_client()
        if client is None:
            return EXIT_CONFIG_ERROR
        with client:
            if "legifrance" in selected:
                laws = load_laws(manifest, sheets, client, args.date)
                if sheet_errors:
                    laws.errors.append("sheet references incomplete (feed errors)")
                results.append(laws)
            if "treaty" in selected:
                results.append(load_treaties(manifest, client, args.manifest.parent))
            if "case_law" in selected:
                results.append(load_case_law(manifest, client))

    for result in results:
        _print_result(result)
    if not args.dry_run and results:
        if manifest.reviewed_by is None:
            print("Note: manifest not reviewed yet (reviewed_by is null).")
        _sync(results)
    status = EXIT_OK if all(r.complete for r in results) else EXIT_SOURCE_ERROR
    if "rules" in selected:
        # After the sources: rules are checked against the index as it now stands.
        status = max(status, _rules(args.dry_run))
    return status


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ingestion")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="load, chunk and sync sources")
    which = run.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="every source")
    which.add_argument("--source", choices=SOURCES)
    run.add_argument("--dry-run", action="store_true", help="no database access")
    run.add_argument(
        "--date",
        type=date.fromisoformat,
        default=datetime.now(UTC).date(),
        help="in-force date for legal texts (default: today, UTC)",
    )
    run.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    embed = sub.add_parser("embed", help="embed stored chunks that have no embedding")
    embed.add_argument("--dry-run", action="store_true", help="count only, no API call")
    embed.add_argument("--limit", type=int, default=None, help="embed at most N chunks")
    return parser


def _legifrance_client() -> LegifranceClient | None:
    try:
        settings = PisteSettings()
    except ValidationError:
        print("Config error: check PISTE_CLIENT_ID, PISTE_CLIENT_SECRET in .env", file=sys.stderr)
        return None
    return LegifranceClient(
        settings.endpoints(), settings.client_id, settings.client_secret.get_secret_value()
    )


def _print_result(result: LoadResult) -> None:
    chars = sum(len(d.text) for d in result.drafts)
    status = "complete" if result.complete else "INCOMPLETE"
    print(
        f"[{result.source_type}] {len(result.sources)} sources, {len(result.drafts)} chunks, "
        f"{chars} chars - {status}"
    )
    for note in result.notes:
        print(f"  note: {note}")
    for error in result.errors:
        print(f"  error: {error}", file=sys.stderr)


def _sync(results: list[LoadResult]) -> None:
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from db.session import make_engine
    from ingestion.store import sync

    engine = make_engine(get_settings().database_url)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session, session.begin():
            for result in results:
                plan = sync(
                    session,
                    result.drafts,
                    result.sources,
                    now,
                    complete_source_types=[result.source_type] if result.complete else [],
                )
                counts = Counter(c.change_type for c in plan.changes)
                print(f"[{result.source_type}] synced: {dict(counts) or 'no changes'}")
    finally:
        engine.dispose()


def _rules(dry_run: bool) -> int:
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from db.session import make_engine
    from ingestion.rules import check_rules, indexed_sources, load_rules_file, sync_rules

    try:
        rules = load_rules_file()
    except (OSError, ValidationError) as exc:
        print(f"Config error: rules file: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    engine = make_engine(get_settings().database_url)
    try:
        with Session(engine) as session, session.begin():
            if dry_run:
                sources = indexed_sources(session, {r.source for r in rules.rules})
                loaded, errors = 0, check_rules(rules, sources)
            else:
                loaded, errors = sync_rules(session, rules, datetime.now(UTC))
    finally:
        engine.dispose()
    verified = sum(1 for r in rules.rules if r.verified_at is not None)
    for error in errors:
        print(f"  error: {error}", file=sys.stderr)
    state = "checked" if dry_run else f"{loaded} loaded"
    print(
        f"[rules] {len(rules.rules)} rows, {verified} verified, {state}"
        f"{' - FAILED' if errors else ''}"
    )
    return EXIT_SOURCE_ERROR if errors else EXIT_OK


def _embed(dry_run: bool, limit: int | None) -> int:
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from db.session import make_engine
    from ingestion.embed import EmbedReport, batches, embed_pending, pending_chunks
    from rag.embeddings import EmbeddingError, MistralEmbedder

    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        if dry_run:
            with Session(engine) as session:
                chunks = pending_chunks(session, limit)
            chars = sum(len(c.input) for c in chunks)
            requests = sum(1 for _ in batches(chunks))
            print(f"[embed] dry run: {len(chunks)} chunks, {chars} chars, {requests} requests")
            return EXIT_OK
        if settings.mistral_api_key is None:
            print("Config error: MISTRAL_API_KEY is not set in .env", file=sys.stderr)
            return EXIT_CONFIG_ERROR

        def progress(report: EmbedReport) -> None:
            print(f"  {report.chunks} chunks embedded, {report.prompt_tokens} tokens", flush=True)

        with MistralEmbedder(settings.mistral_api_key.get_secret_value()) as embedder:
            try:
                report = embed_pending(engine, embedder, limit, on_batch=progress)
            except EmbeddingError as exc:
                print(f"FAILED: {exc}", file=sys.stderr)
                return EXIT_SOURCE_ERROR
        print(
            f"[embed] {embedder.model}: {report.chunks} chunks, {report.requests} requests, "
            f"{report.prompt_tokens} prompt tokens, {report.skipped} skipped (changed meanwhile)"
        )
        return EXIT_OK
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
