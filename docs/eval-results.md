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

## Model x corpus grid (2026-11-27)

`python -m evals.grid`: each corpus embedded with each model, same 24 scored items of golden v1.
Index chunks are embedded as `title + text`, dataset chunks as `chunk_text` (each as its
producer embeds it). Embedding the 322 dataset chunks with `mistral-embed` cost 161,457 tokens.

| Metric | service-public x mistral-embed | service-public x bge-m3 | index-sheets x mistral-embed | index-sheets x bge-m3 | index x mistral-embed | index x bge-m3 |
|---|---|---|---|---|---|---|
| Chunks | 322 | 322 | 460 | 460 | 757 | 757 |
| Source recall@5 | 0.549 | 0.528 | 0.569 | 0.604 | 0.708 | 0.701 |
| Source recall@10 | 0.549 | 0.528 | 0.569 | 0.618 | 0.722 | 0.785 |
| MRR | 0.750 | 0.708 | 0.806 | 0.783 | 0.819 | 0.785 |
| Right source wins | 0.143 | 0.143 | 0.143 | 0.143 | 0.143 | 0.429 |
| Latency p50 (ms) | 212.1 | 150.0 | 230.6 | 171.3 | 258.8 | 211.1 |

The two fact-sheet corpora cannot hold the CESEDA, agreement and case-law sources: their source
recall is capped at 0.681 on this set (expected sources that are one of the 19 sheets).

| Effect | Comparison | Result |
|---|---|---|
| Chunking (same 19 sheets) | `index-sheets` vs `service-public` | The index's chunks are better under both models: recall@5 +0.020 (mistral-embed) and +0.076 (bge-m3), MRR +0.056 and +0.075. |
| Corpus breadth | `index` vs `index-sheets` | Largest effect: recall@5 +0.139 (mistral-embed) and +0.097 (bge-m3). |
| Model (same corpus) | `mistral-embed` vs bge-m3 | No consistent winner. On the full index: same recall@5, bge-m3 ahead on recall@10 (+0.063) and right source wins (3 of 7 vs 1 of 7), `mistral-embed` ahead on MRR (+0.034). |
| Stored vs local dataset vectors | `baseline-sp` (stored bge-m3 vectors) vs `service-public x bge-m3` (re-embedded locally) | recall@5 0.562 vs 0.528: local re-embedding of the same texts does not reproduce the stored ranking exactly (minimum cosine 0.980 on the checked chunks). |

Decision input for build step 4: chunking and corpus choices are validated; the embedding model
stays `mistral-embed` (no index rebuild, query cost 748 tokens per run) unless hybrid retrieval
and re-ranking leave the precedence gap that bge-m3 narrows. With 24 scored items, differences
below about 0.05 are within one or two items.

## hybrid (mistral-embed) vs vector (mistral-embed)

Reports: base 2026-11-27T17:29:37+00:00, candidate 2026-11-27T17:58:06+00:00. Set `golden` v1, 32 items, 24 scored.

| Metric | Base | Candidate | Δ |
|---|---|---|---|
| Source recall@5 | 0.708 | 0.743 | +0.035 |
| Source recall@10 | 0.722 | 0.792 | +0.069 |
| MRR | 0.819 | 0.801 | -0.018 |
| Right source wins | 0.143 | 0.286 | +0.143 |
| Latency p50 (ms) | 223.800 | 246.500 | +22.700 |
| Latency p95 (ms) | 571.900 | 572.000 | +0.100 |
| Query embedding tokens | 748 | 748 | +0 |

Not measured: `answer_correctness`, `faithfulness`, `citation_accuracy`, `refusal_abstain_accuracy`.

| Item | First relevant rank (base) | (candidate) |
|---|---|---|
| g002 | 1 | 2 |
| g004 | 2 | 5 |
| g005 | 2 | 1 |
| g006 | 1 | 2 |
| g008 | 2 | 1 |
| g013 | 1 | 2 |
| g016 | 2 | 3 |
| g018 | 3 | 5 |
| g020 | 3 | 1 |

## hybrid-precedence (mistral-embed) vs vector (mistral-embed)

Reports: base 2026-11-27T17:29:37+00:00, candidate 2026-11-27T18:03:08+00:00. Set `golden` v1, 32 items, 24 scored.

| Metric | Base | Candidate | Δ |
|---|---|---|---|
| Source recall@5 | 0.708 | 0.764 | +0.056 |
| Source recall@10 | 0.722 | 0.799 | +0.076 |
| MRR | 0.819 | 0.849 | +0.029 |
| Right source wins | 0.143 | 1.000 | +0.857 |
| Latency p50 (ms) | 223.800 | 259.300 | +35.500 |
| Latency p95 (ms) | 571.900 | 756.100 | +184.200 |
| Query embedding tokens | 748 | 748 | +0 |

Not measured: `answer_correctness`, `faithfulness`, `citation_accuracy`, `refusal_abstain_accuracy`.

| Item | First relevant rank (base) | (candidate) |
|---|---|---|
| g002 | 1 | 2 |
| g004 | 2 | 5 |
| g005 | 2 | 1 |
| g006 | 1 | 2 |
| g008 | 2 | 1 |
| g013 | 1 | 2 |
| g016 | 2 | 3 |
| g018 | 3 | 1 |
| g020 | 3 | 1 |
| g022 | - | 3 |

## Agent, end to end (build step 4, 2026-11-27)

`python -m evals.agent`: the graph of `rag.graph` with `mistral-embed` and `mistral-small-2603`
on the 32 verified items of golden v1. Prompt versions in D-018; v1 and v2 runs are kept to show
what each change fixed.

| Metric | Prompt v1 (function calling) | v1 (json_schema) | v2 | v3 | v3, second run |
|---|---|---|---|---|---|
| Behaviour accuracy | 0.344 | 0.906 | 0.969 | 1.000 | 1.000 |
| Answer items answered | 3/24 | 21/24 | 24/24 | 24/24 | 24/24 |
| Refuse / handoff / abstain correct | 3/3, 3/3, 2/2 | 3/3, 3/3, 2/2 | 3/3, 3/3, 1/2 | 3/3, 3/3, 2/2 | 3/3, 3/3, 2/2 |
| Expected source cited | 1.000 (3 items) | 0.952 | 0.917 | 0.958 | 0.958 |
| Citation precision (strict) | 0.333 | 0.591 | 0.539 | 0.574 | 0.629 |
| Governing source cited first | 0.667 | 0.833 | 0.857 | 0.857 | 0.857 |
| Sentences removed by the checker | 11.1 % | 6.7 % | 2.8 % | 0.6 % | 0.6 % |
| Latency p50 (ms) | 2,040 | 1,872 | 1,988 | 2,089 | 1,769 |

What each step fixed: `json_schema` output (sentence objects instead of lists); source labels
`S1`..`S10` (no more shortened chunk ids); number words and titles in the figure check
("douze mois", the agreement's date); v3 declines when no source states what is asked (the
average processing time of one prefecture had been answered with a related deadline).

Retrieval of the agent (`hybrid-precedence`) against the step-3 reference is in the comparison
above (recall@5 0.708 to 0.764, right source wins 0.143 to 1.000).

## Rules table (build step 5, 2026-11-27)

`python -m evals.agent --rules none|all`: same code, same day, prompt `answer.v4`, 32 items.
`--rules all` uses the 27 draft rows (not yet verified).

| Metric | No rules | Draft rules |
|---|---|---|
| Behaviour accuracy | 1.000 | 1.000 |
| Expected source cited | 0.958 | 0.958 |
| Key-fact figure recall | 0.480 | 0.522 |
| Answers citing a rule | 0 | 2 |
| Citation precision (strict) | 0.617 | 0.608 |
| Governing source cited first | 0.857 | 0.857 |
| Sentences removed by the checker | 0.0 % | 0.0 % |
| Latency p50 (ms) | 1,786 | 1,746 |

Key-fact figure recall: share of the numbers in `key_facts` that the answer states (judge-free
proxy, see D-019 for its limits). Figures added with rules: internship stipend threshold (g008),
6-month document limit (g006), diploma within 12 months (g010), 4-year limit after leaving
France (g012).
