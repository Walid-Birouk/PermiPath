"""Runs a golden set through a retriever and writes a JSON report.

    uv run --project backend python -m evals.run_evals --set golden --pipeline vector \\
        [--depth 20] [--include-drafts] [--out reports/YYYY-MM-DD-vector.json]
    uv run --project backend --group baseline python -m evals.run_evals --pipeline baseline-sp

Pipelines: `vector` (the index of build step 2, `evals.retrievers`) and `baseline-sp` (the
pre-vectorised service-public dataset, `evals.baseline`; run `python -m evals.baseline fetch`
first).

Scope (build step 3): retrieval only. Items expecting an answer and listing `must_cite` are
scored for source recall@k, reciprocal rank and, when `winning_source` is set, whether the
governing source wins. Every item records its top similarity score, the reference data for the
abstain threshold of build step 4. Answer-level metrics (correctness, faithfulness, citation
accuracy, refusal/abstain accuracy) need generated answers and are listed as not measured.

Only verified items run unless `--include-drafts` is given; draft results are flagged in the
report. Exit codes: 0 success, 1 run failure, 2 configuration or empty set.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evals import REPO_ROOT
from evals.golden_set import GoldenItem, GoldenSet, load_golden_set, set_path
from evals.metrics import (
    first_rank,
    mean_of,
    percentile,
    recall_at_k,
    reciprocal_rank,
    right_source_wins,
    source_ranking,
)
from evals.retrievers import Retriever
from rag.embeddings import EmbeddingError

REPORT_SCHEMA = 1
KS = (5, 10)
DEFAULT_DEPTH = 20
RANKING_KEPT = 10
NOT_MEASURED = (
    "answer_correctness",
    "faithfulness",
    "citation_accuracy",
    "refusal_abstain_accuracy",
)
"""Need generated answers (build step 4)."""

EXIT_OK, EXIT_RUN_ERROR, EXIT_CONFIG_ERROR = 0, 1, 2


def score_item(
    item: GoldenItem,
    retriever: Retriever,
    depth: int,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    started = clock()
    retrieval = retriever.retrieve(item.question, depth)
    latency_ms = (clock() - started) * 1000
    ranking = source_ranking(retrieval.hits)
    scored = item.expected_behaviour == "answer" and bool(item.must_cite)
    expected = list(item.must_cite) if scored else []
    result: dict[str, Any] = {
        "id": item.id,
        "verified": item.verified,
        "expected_behaviour": item.expected_behaviour,
        "nationality": item.nationality,
        "lang": item.lang,
        "scored": scored,
        "must_cite": list(item.must_cite),
        "winning_source": item.winning_source,
        "ranking": ranking[:RANKING_KEPT],
        "top_score": retrieval.hits[0].score if retrieval.hits else None,
        "first_rank": first_rank(ranking, expected) if scored else None,
        "reciprocal_rank": reciprocal_rank(ranking, expected),
        "right_source_wins": right_source_wins(
            ranking, expected, item.winning_source if scored else None
        ),
        "latency_ms": round(latency_ms, 1),
        "embedding_tokens": retrieval.embedding_tokens,
    }
    for k in KS:
        result[f"recall@{k}"] = recall_at_k(ranking, expected, k)
    return result


def summarise(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(i["latency_ms"]) for i in items]
    summary: dict[str, Any] = {
        "items": len(items),
        "items_scored": sum(1 for i in items if i["scored"]),
        "precedence_items": sum(1 for i in items if i["right_source_wins"] is not None),
    }
    for k in KS:
        summary[f"recall@{k}"] = mean_of(i[f"recall@{k}"] for i in items)
    summary["mrr"] = mean_of(i["reciprocal_rank"] for i in items)
    summary["right_source_wins"] = mean_of(i["right_source_wins"] for i in items)
    summary["latency_ms_p50"] = percentile(latencies, 50)
    summary["latency_ms_p95"] = percentile(latencies, 95)
    summary["embedding_tokens"] = sum(int(i["embedding_tokens"]) for i in items)
    summary["not_measured"] = list(NOT_MEASURED)
    return summary


def run(
    golden: GoldenSet,
    set_name: str,
    retriever: Retriever,
    depth: int = DEFAULT_DEPTH,
    include_drafts: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    selected = [i for i in golden.items if include_drafts or i.verified]
    items = [score_item(item, retriever, depth) for item in selected]
    return {
        "schema": REPORT_SCHEMA,
        "created_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "set": {
            "name": set_name,
            "version": golden.version,
            "items": len(selected),
            "verified": sum(1 for i in selected if i.verified),
            "includes_drafts": any(not i.verified for i in selected),
        },
        "pipeline": {**retriever.describe(), "depth": depth, "ks": list(KS)},
        "summary": summarise(items),
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        golden = load_golden_set(set_path(args.set))
    except (OSError, ValidationError) as exc:
        print(f"Config error: golden set {args.set}: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if not any(args.include_drafts or i.verified for i in golden.items):
        print(
            f"Config error: no verified item in set {args.set} (use --include-drafts to run "
            "draft items)",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    out = args.out or _default_out(args.pipeline)
    try:
        with ExitStack() as stack:
            retriever = _retriever(args.pipeline, stack)
            report = run(golden, args.set, retriever, args.depth, args.include_drafts)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except EmbeddingError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print_summary(report, out)
    return EXIT_OK


class ConfigError(Exception):
    pass


def _retriever(pipeline: str, stack: ExitStack) -> Retriever:
    """Builds the retriever; resources are closed by `stack`."""
    if pipeline == "baseline-sp":
        from evals.baseline import (
            SNAPSHOT_PATH,
            BaselineRetriever,
            SentenceTransformerEncoder,
            load_snapshot,
        )

        if not SNAPSHOT_PATH.exists():
            raise ConfigError(f"no snapshot at {SNAPSHOT_PATH}; run python -m evals.baseline fetch")
        try:
            encoder = SentenceTransformerEncoder()
        except ImportError as exc:
            raise ConfigError(f"{exc}; run with --group baseline") from exc
        return BaselineRetriever(load_snapshot(SNAPSHOT_PATH), encoder)

    from app.config import get_settings
    from db.session import make_engine
    from evals.retrievers import VectorRetriever
    from rag.embeddings import MistralEmbedder

    settings = get_settings()
    if settings.mistral_api_key is None:
        raise ConfigError("MISTRAL_API_KEY is not set in .env")
    engine = make_engine(settings.database_url)
    stack.callback(engine.dispose)
    embedder = stack.enter_context(MistralEmbedder(settings.mistral_api_key.get_secret_value()))
    return VectorRetriever(engine, embedder)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.run_evals")
    parser.add_argument("--set", default="golden", help="golden set name (evals/golden/NAME.yaml)")
    parser.add_argument("--pipeline", choices=("vector", "baseline-sp"), default="vector")
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH, help="chunks retrieved")
    parser.add_argument("--include-drafts", action="store_true", help="also run draft items")
    parser.add_argument("--out", type=Path, default=None, help="report path (JSON)")
    return parser


def _default_out(pipeline: str) -> Path:
    return REPO_ROOT / "reports" / f"{datetime.now(UTC).date().isoformat()}-{pipeline}.json"


def _print_summary(report: dict[str, Any], out: Path) -> None:
    summary = report["summary"]
    shown = ("items", "items_scored", "recall@5", "recall@10", "mrr", "right_source_wins")
    for key in (*shown, "latency_ms_p50", "embedding_tokens"):
        value = summary[key]
        print(f"{key:>18}: {value:.3f}" if isinstance(value, float) else f"{key:>18}: {value}")
    if report["set"]["includes_drafts"]:
        print("Note: the report includes draft (unverified) items.")
    print(f"Report: {out}")


if __name__ == "__main__":
    raise SystemExit(main())
