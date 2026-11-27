"""End-to-end evaluation of the agent graph (`rag.graph`) on the golden set.

    uv run --project backend python -m evals.agent [--set golden] [--include-drafts]

Calls the real retriever and model (`mistral-embed`, `mistral-small-2603`; billed). Writes
`reports/<date>-agent.json` and prints a summary.

Metrics:

- Behaviour accuracy: share of items whose response kind (answer, refuse, handoff, abstain)
  equals `expected_behaviour`; also per expected behaviour.
- Expected source cited: answered answer items citing at least one `must_cite` source.
- Citation precision: share of cited sources that are in `must_cite`, over answered answer
  items. Strict: a relevant source the maintainer did not list counts as imprecise.
- Governing source cited first: answered Algerian answer items whose first cited source is
  `winning_source`.
- Removed sentences: sentences removed by the citation checker, over generated sentences.
- Key-fact figure recall: answered answer items: share of the figures in `key_facts` (digits)
  that the answer states. A judge-free proxy for getting the figures right.
- Items citing rules: answers citing at least one rules-table row.
- Tokens and latency per question: embedding, prompt and completion tokens; wall time.

`key_facts` coverage and faithfulness need a judge and are not measured here.
"""

import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import REPO_ROOT
from evals.golden_set import GoldenItem, load_golden_set, set_path
from rag.citations import figures

REPORT_SCHEMA = "permipath.agent-eval.v1"


def score(item: GoldenItem, state: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    response = state["response"]
    cited = [s["ref"] for s in response["sources"]] if response["kind"] == "answer" else []
    checked = state.get("checked")
    draft = state.get("draft")
    found = state.get("search")
    must = set(item.must_cite)
    answered = item.expected_behaviour == "answer" and response["kind"] == "answer" and bool(must)
    texts = [s["text"] for s in response["sentences"]] if response["kind"] == "answer" else []
    expected_figures = set().union(set(), *(figures(f) for f in item.key_facts))
    stated = set().union(set(), *(figures(t) for t in texts))
    return {
        "id": item.id,
        "expected": item.expected_behaviour,
        "kind": response["kind"],
        "correct": response["kind"] == item.expected_behaviour,
        "reason": response.get("reason") or response.get("abstain_reason"),
        "nationality": item.nationality,
        "cited": cited,
        "must_cite": list(item.must_cite),
        "winning_source": item.winning_source,
        "expected_source_cited": bool(must & set(cited)) if answered else None,
        "citation_precision": (len([c for c in cited if c in must]) / len(cited))
        if answered and cited
        else None,
        "governing_first": (cited[0] == item.winning_source)
        if answered and item.winning_source and item.nationality == "algerian" and cited
        else None,
        "sentences_kept": len(checked.kept) if checked else 0,
        "sentences_removed": len(checked.dropped) if checked else 0,
        "removed_reasons": [d.reason for d in checked.dropped] if checked else [],
        "answer": texts,
        "key_fact_figures": sorted(expected_figures),
        "key_fact_figures_stated": sorted(expected_figures & stated),
        "key_fact_figure_recall": (len(expected_figures & stated) / len(expected_figures))
        if answered and expected_figures
        else None,
        "rules_offered": [r.id for r in state.get("rules", [])],
        "rules_cited": sorted({s["rule"] for s in response["sources"] if s.get("rule")}),
        "embedding_tokens": found.embedding_tokens if found else 0,
        "prompt_tokens": draft.prompt_tokens if draft else 0,
        "completion_tokens": draft.completion_tokens if draft else 0,
        "latency_ms": round(latency_ms, 1),
    }


def _mean(values: list[Any]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return round(sum(present) / len(present), 3) if present else None


def summarise(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_behaviour: dict[str, list[bool]] = {}
    for item in items:
        by_behaviour.setdefault(item["expected"], []).append(item["correct"])
    kept = sum(i["sentences_kept"] for i in items)
    removed = sum(i["sentences_removed"] for i in items)
    latencies = sorted(i["latency_ms"] for i in items)
    return {
        "items": len(items),
        "behaviour_accuracy": _mean([i["correct"] for i in items]),
        "behaviour_accuracy_by_expected": {
            k: {"accuracy": _mean(v), "items": len(v)} for k, v in sorted(by_behaviour.items())
        },
        "expected_source_cited": _mean([i["expected_source_cited"] for i in items]),
        "key_fact_figure_recall": _mean([i["key_fact_figure_recall"] for i in items]),
        "items_citing_rules": sum(1 for i in items if i["rules_cited"]),
        "citation_precision": _mean([i["citation_precision"] for i in items]),
        "governing_source_cited_first": _mean([i["governing_first"] for i in items]),
        "removed_sentence_rate": round(removed / (kept + removed), 3) if kept + removed else None,
        "tokens": {
            k: sum(i[k] for i in items)
            for k in ("embedding_tokens", "prompt_tokens", "completion_tokens")
        },
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_p95": latencies[max(0, round(0.95 * len(latencies)) - 1)]
        if latencies
        else None,
    }


def main(argv: list[str] | None = None) -> int:
    from app.ask import make_agent
    from app.tracing import OFF, build_tracing
    from rag.answer import ANSWER_MODEL, PROMPT_VERSION

    parser = argparse.ArgumentParser(prog="python -m evals.agent")
    parser.add_argument("--set", default="golden")
    parser.add_argument("--include-drafts", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--trace",
        action="store_true",
        help="send full traces to Langfuse (tag `eval`) with per-item scores (D-021)",
    )
    parser.add_argument(
        "--rules",
        choices=("verified", "all", "none"),
        default="verified",
        help="rules-table rows the agent may use: verified (default), all (drafts too), none",
    )
    args = parser.parse_args(argv)

    golden = load_golden_set(set_path(args.set))
    selected = [i for i in golden.items if args.include_drafts or i.verified]
    tracing = build_tracing("full") if args.trace else OFF
    agent = make_agent(args.rules, tracing, tags=("eval", f"golden-v{golden.version}"))
    today = datetime.now(UTC).date()
    items = []
    for item in selected:
        started = time.perf_counter()
        state = agent({"question": item.question, "on": today})
        items.append(score(item, state, (time.perf_counter() - started) * 1000))
        if trace_id := state.get("trace_id"):
            items[-1]["trace_id"] = trace_id
            tracing.score(trace_id, "behaviour_correct", float(items[-1]["correct"]), item.id)
            if items[-1]["key_fact_figure_recall"] is not None:
                tracing.score(
                    trace_id, "key_fact_figure_recall", items[-1]["key_fact_figure_recall"], item.id
                )
        print(f"{item.id} {item.expected_behaviour:>8} -> {items[-1]['kind']}", flush=True)
    report = {
        "schema": REPORT_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "set": {"name": args.set, "version": golden.version, "items": len(selected)},
        "agent": {
            "answer_model": ANSWER_MODEL,
            "prompt": PROMPT_VERSION,
            "in_force_on": str(today),
            "rules": args.rules,
        },
        "summary": summarise(items),
        "items": items,
    }
    tracing.flush()
    out = args.out or REPO_ROOT / "reports" / f"{today.isoformat()}-agent.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
