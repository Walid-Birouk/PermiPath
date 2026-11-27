"""Compares two eval reports and renders a markdown table.

    uv run --project backend python -m evals.compare BASE.json CANDIDATE.json \\
        [--append docs/eval-results.md]

The table lists the summary metrics of both reports and their difference, then the scored items
whose first relevant rank changed. Reports are comparable only on the same set version and the
same items; a mismatch is stated above the table.
"""

import argparse
import json
from pathlib import Path
from typing import Any

METRICS = (
    ("recall@5", "Source recall@5"),
    ("recall@10", "Source recall@10"),
    ("mrr", "MRR"),
    ("right_source_wins", "Right source wins"),
    ("latency_ms_p50", "Latency p50 (ms)"),
    ("latency_ms_p95", "Latency p95 (ms)"),
    ("embedding_tokens", "Query embedding tokens"),
)
"""(summary key, label)."""


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _delta(base: Any, candidate: Any) -> str:
    if not isinstance(base, int | float) or not isinstance(candidate, int | float):
        return "-"
    diff = candidate - base
    return f"{diff:+.3f}" if isinstance(diff, float) else f"{diff:+d}"


def _label(report: dict[str, Any]) -> str:
    pipeline = report["pipeline"]
    return f"{pipeline['name']} ({pipeline.get('embedding_model', 'n/a')})"


def render(base: dict[str, Any], candidate: dict[str, Any]) -> str:
    lines = [
        f"## {_label(candidate)} vs {_label(base)}",
        "",
        f"Reports: base {base['created_at']}, candidate {candidate['created_at']}. "
        f"Set `{candidate['set']['name']}` v{candidate['set']['version']}, "
        f"{candidate['summary']['items']} items, {candidate['summary']['items_scored']} scored.",
        "",
    ]
    base_ids = [i["id"] for i in base["items"]]
    candidate_ids = [i["id"] for i in candidate["items"]]
    if base["set"]["version"] != candidate["set"]["version"] or base_ids != candidate_ids:
        lines += ["**Not comparable:** the reports use different set versions or items.", ""]
    if base["set"]["includes_drafts"] or candidate["set"]["includes_drafts"]:
        lines += ["**Includes draft (unverified) items.**", ""]
    lines += ["| Metric | Base | Candidate | Δ |", "|---|---|---|---|"]
    for key, label in METRICS:
        b, c = base["summary"].get(key), candidate["summary"].get(key)
        lines.append(f"| {label} | {_fmt(b)} | {_fmt(c)} | {_delta(b, c)} |")
    not_measured = candidate["summary"].get("not_measured", [])
    if not_measured:
        lines += ["", "Not measured: " + ", ".join(f"`{m}`" for m in not_measured) + "."]

    base_ranks = {i["id"]: i["first_rank"] for i in base["items"] if i["scored"]}
    changed = [
        (i["id"], base_ranks[i["id"]], i["first_rank"])
        for i in candidate["items"]
        if i["scored"] and i["id"] in base_ranks and base_ranks[i["id"]] != i["first_rank"]
    ]
    if changed:
        lines += ["", "| Item | First relevant rank (base) | (candidate) |", "|---|---|---|"]
        lines += [f"| {item} | {_fmt(b)} | {_fmt(c)} |" for item, b, c in changed]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.compare")
    parser.add_argument("base", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--append", type=Path, default=None, help="markdown file to append to")
    args = parser.parse_args(argv)
    base = json.loads(args.base.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    table = render(base, candidate)
    if args.append is None:
        print(table, end="")
    else:
        with args.append.open("a", encoding="utf-8") as out:
            out.write("\n" + table)
        print(f"Appended to {args.append}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
