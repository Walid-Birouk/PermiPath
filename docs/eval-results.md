# Evaluation results

Comparisons between evaluation reports, appended by
`python -m evals.compare BASE.json CANDIDATE.json --append docs/eval-results.md`.
Metric definitions and report format: D-014 in `docs/decisions.md`. Golden set:
`evals/golden/golden.yaml`.

| Metric | Definition |
|---|---|
| Source recall@k | Share of the expected sources (`must_cite`) among the top k distinct sources retrieved. |
| MRR | Mean reciprocal rank of the first expected source retrieved (0 when none is). |
| Right source wins | Share of precedence items whose governing source (`winning_source`) is retrieved and ranks first among the expected sources. |
| Latency p50 / p95 | Retrieval time per question, query embedding included. |
| Query embedding tokens | Tokens billed to embed the questions. |

Pipelines: `baseline-sp` (pre-vectorised service-public dataset, DINUM, BAAI/bge-m3; the 19
fact sheets of the first manifest; the index now has 20) `vector` (the index of build step 2, `mistral-embed`) and `bge-m3-index` (the same chunks
embedded with bge-m3: model comparison). Details:
D-014.


## vector (mistral-embed) vs baseline-sp (BAAI/bge-m3@5617a9f61b02 (local))

Reports: base 2026-11-27T17:30:14+00:00, candidate 2026-11-27T17:29:37+00:00. Set `golden` v1, 32 items, 24 scored.

| Metric | Base | Candidate | Δ |
|---|---|---|---|
| Source recall@5 | 0.562 | 0.708 | +0.146 |
| Source recall@10 | 0.562 | 0.722 | +0.160 |
| MRR | 0.767 | 0.819 | +0.052 |
| Right source wins | 0.143 | 0.143 | +0.000 |
| Latency p50 (ms) | 170.000 | 223.800 | +53.800 |
| Latency p95 (ms) | 210.600 | 571.900 | +361.300 |
| Query embedding tokens | 0 | 748 | +748 |

Not measured: `answer_correctness`, `faithfulness`, `citation_accuracy`, `refusal_abstain_accuracy`.

| Item | First relevant rank (base) | (candidate) |
|---|---|---|
| g003 | 2 | 1 |
| g004 | 3 | 2 |
| g005 | 1 | 2 |
| g008 | - | 2 |
| g016 | 4 | 2 |
| g018 | - | 3 |

## bge-m3-index (BAAI/bge-m3@5617a9f61b02 (local)) vs vector (mistral-embed)

Reports: base 2026-11-27T17:29:37+00:00, candidate 2026-11-27T17:29:57+00:00. Set `golden` v1, 32 items, 24 scored.

| Metric | Base | Candidate | Δ |
|---|---|---|---|
| Source recall@5 | 0.708 | 0.701 | -0.007 |
| Source recall@10 | 0.722 | 0.785 | +0.062 |
| MRR | 0.819 | 0.785 | -0.035 |
| Right source wins | 0.143 | 0.429 | +0.286 |
| Latency p50 (ms) | 223.800 | 233.700 | +9.900 |
| Latency p95 (ms) | 571.900 | 283.100 | -288.800 |
| Query embedding tokens | 748 | 0 | -748 |

Not measured: `answer_correctness`, `faithfulness`, `citation_accuracy`, `refusal_abstain_accuracy`.

| Item | First relevant rank (base) | (candidate) |
|---|---|---|
| g004 | 2 | 4 |
| g005 | 2 | 1 |
| g007 | 1 | 2 |
| g008 | 2 | 1 |
| g009 | 1 | 3 |
| g011 | 1 | 2 |
| g016 | 2 | 6 |
| g017 | 1 | 2 |
| g018 | 3 | 1 |
| g020 | 3 | 4 |
| g022 | - | 3 |

## Findings (golden v1, 2026-11-27)

| Finding | Evidence |
|---|---|
| The index outperforms the pre-vectorised baseline on source recall. | recall@5 0.708 (`vector`) vs 0.562 (`baseline-sp`); the baseline holds fact sheets only and cannot retrieve CESEDA articles, the agreement or case law. |
| Dense retrieval alone does not apply precedence. | Right source wins 0.143 for `vector` (1 of 7 Algerian items); 0.429 for `bge-m3-index`. Build step 4 (hybrid retrieval, re-ranking, `nationality_scope` filter, precedence node) targets this metric. |
| Model choice is not settled by v1. | bge-m3 gains on recall@10 and right source wins, `mistral-embed` on recall@5 and MRR; 24 scored items are too few to separate them. |
| Refusal, handoff and abstain items are not scored yet. | Eight items have no `must_cite`; behaviour accuracy needs the answer graph of build step 4. |
