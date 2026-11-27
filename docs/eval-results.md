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

Pipelines: `baseline-sp` (pre-vectorised service-public dataset, DINUM, BAAI/bge-m3; same 19
fact sheets as the index) and `vector` (the index of build step 2, `mistral-embed`). Details:
D-014.

No comparison has been recorded yet: the golden set has no verified item.
