# Evaluation results (tracked)

Reports kept as reference points for later comparisons (`evals.compare`) and for the public
metrics page. `reports/` holds working runs and is git-ignored; a run is copied here when it
closes a build step.

| Folder | Golden set | Index | Content |
|---|---|---|---|
| `step-3/` | v1, 32 items verified 2026-11-27, 24 scored | 757 chunks (20 fact sheets, 245 law sources, 17 treaty sources, 1 decision) | `vector`, `bge-m3-index`, `baseline-sp`, and the six `grid-<corpus>-<model>` cells. Summary and findings: `docs/eval-results.md`. |
| `step-4/` | v1, 32 items verified, 24 scored for retrieval | 757 chunks | `hybrid`, `hybrid-precedence` (retrieval of the agent), `agent` and `agent-run2` (end to end, prompt v3). Summary: `docs/eval-results.md`, D-016 to D-018. |

Compare a later run with the step-3 reference:

    uv run --project backend python -m evals.compare evals/results/step-3/2026-11-27-vector.json \
        reports/<date>-<pipeline>.json --append docs/eval-results.md
