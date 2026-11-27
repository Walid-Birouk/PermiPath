# Architecture decision log

Each entry records a decision, its context and its consequences. Entries are append-only;
a superseded decision is marked as such and references the entry that replaces it.

| ID | Title | Status | Date |
|----|-------|--------|------|
| D-001 | Repository layout and Python packaging | Accepted | 2026-11-25 |
| D-002 | Database schema ownership and migrations | Accepted | 2026-11-25 |
| D-003 | Chunk identity and versioning | Accepted | 2026-11-25 |
| D-004 | Lexical search configuration | Accepted | 2026-11-25 |
| D-005 | Query log contents | Accepted | 2026-11-25 |
| D-006 | Local stack composition | Accepted | 2026-11-25 |
| D-007 | Dependency introduction policy | Accepted | 2026-11-25 |
| D-008 | Own thin Légifrance client instead of pylegifrance | Accepted | 2026-11-26 |
| D-009 | Algerian nationals in scope with source precedence | Accepted | 2026-11-26 |
| D-010 | httpx2 as HTTP client; warnings fail the test suite | Accepted | 2026-11-27 |
| D-011 | Ingestion: manifest, chunking, identity and removal rules | Accepted | 2026-11-27 |
| D-012 | Sources without machine-readable official text | Proposed | 2026-11-27 |
| D-013 | Passage embeddings: mistral-embed through an own client | Accepted | 2026-11-27 |
| D-014 | Evaluation harness: golden set, retrieval metrics and baseline | Accepted | 2026-11-27 |

---

## D-001 — Repository layout and Python packaging

**Context.** The backend contains several independent concerns (API, ingestion, RAG, guardrails,
database) that must be testable in isolation and runnable as modules
(for example `python -m ingestion run`).

**Decision.** `backend/` is a single `uv` project (non-packaged, `tool.uv.package = false`) whose
top-level Python packages are `app`, `ingestion`, `rag`, `guardrails` and `db`. Python is pinned
to 3.12 through `.python-version` and `requires-python = ">=3.12,<3.13"`. Tests live in
`backend/tests/`, fixtures in `backend/tests/fixtures/`.

**Consequences.** Commands run from `backend/` (`uv run pytest`, `uv run alembic …`). The
`evals/` harness at the repository root will import backend packages by adding `backend/` to the
path; this is settled in Phase 4.

## D-002 — Database schema ownership and migrations

**Context.** `langchain-postgres` can create and manage its own tables. The project requires a
specific chunk schema (provenance, validity dates, journey stages, soft-versioning).

**Decision.** The schema is owned by the project and managed by Alembic
(`backend/db/migrations/`). Migrations are hand-written and freeze the values they depend on
(enumerations, embedding dimension) rather than importing them from application code.
`backend/db/models.py` mirrors the schema for application use. The backend container applies
`alembic upgrade head` on start.

**Consequences.** Vector retrieval in Phase 3 must work against the `chunks` table: either
through a `langchain-postgres` store configured for a custom table, or a small custom retriever.
The choice is recorded when Phase 3 starts, after checking the current `langchain-postgres` API.

## D-003 — Chunk identity and versioning

**Decision.**

- `chunks.id` is a deterministic text key derived from `(source_type, source_id, chunk_index)`,
  so citations remain stable across re-ingestion.
- `chunks` holds only the current version of each chunk.
- When `content_hash` changes, the previous row is copied into `chunk_versions` (without its
  embedding, to save storage) and a `changelog` row is written with `status = 'pending'`.
- `changelog` entries are shown publicly only once `status = 'reviewed'`.
- `chunk_versions.chunk_id` has no foreign key, so history survives removal of a chunk.

## D-004 — Lexical search configuration

**Decision.** `chunks.tsv` is a stored generated column:
`to_tsvector(<config>, title || ' ' || text)`, where `<config>` is `english` when `lang = 'en'`
and `french` otherwise. It is indexed with GIN.

**Consequences.** The query side must use the same configuration as the chunk language
(`plainto_tsquery('french', …)` for French chunks).

## D-005 — Query log contents

**Context.** CLAUDE.md rule 5 limits logs to timestamp, journey stage, retrieved chunk IDs,
latency, token counts and eval flags.

**Decision.** `query_logs` has exactly those columns, plus a surrogate `id`. Any further
per-request signal (outcome, abstain reason) is stored inside `eval_flags` as a non-free-text
value. A test asserts the column set so that a text column cannot be added unnoticed.

## D-006 — Local stack composition

**Decision.** The stack is defined in `infra/docker-compose.yml`; a root `compose.yaml` includes
it so that `docker compose up` works from the repository root. Services:

| Service | Image | Port | Notes |
|---------|-------|------|-------|
| `db` | `pgvector/pgvector:pg17` | 5432 | Creates `permipath` and `permipath_test` |
| `backend` | `infra/backend.Dockerfile` | 8000 | Runs migrations, then uvicorn |
| `frontend` | `infra/frontend.Dockerfile` | 3000 | `next dev`; `src/` and `public/` bind-mounted |

Production does not use the frontend image (Vercel builds from `frontend/`).

## D-007 — Dependency introduction policy

**Decision.** Dependencies are added when the phase that uses them starts, at their then-current
version, verified against current documentation. LangChain, LangGraph and the Mistral client
are therefore not installed in Phase 0. Exact versions are pinned by `backend/uv.lock` and
`frontend/package-lock.json`.

## D-008 — Own thin Légifrance client instead of pylegifrance

**Context.** Build step 1 needs a token, a code lookup and one article as in force on a date.
pylegifrance (v1.7.2) covers this but defaults to PROD hosts, reads `LEGIFRANCE_*` variables,
pulls `requests` + `tenacity` + `python-dotenv`, and hides the request shapes we need to test.

**Decision.** `backend/ingestion/legifrance/` is a small httpx client (token caching, one retry on
401, typed errors with no secrets) over three endpoints: `list/code`, `search` (fond
`CODE_DATE`), `consult/getArticle`. `httpx` moves from dev to runtime dependencies. The in-force
check is done locally from `dateDebut`/`dateFin`, falling back to `articleVersions`, so a search
hit on the wrong version cannot be returned silently. pylegifrance is used only as a reference
for request shapes (its models are generated from the Légifrance Swagger).

**Consequences.** Endpoint shapes are unverified against the PISTE portal Swagger until the live
check passes. The base path `/dila/legifrance/lf-engine-app` and the PROD hosts are flagged in
`settings.py`.

**Verification.** Live sandbox check passed on 2026-11-27 without code changes: token
(`scope=openid`), base path, and the `list/code`, `search` and `consult/getArticle` request bodies
are confirmed. Recorded responses are kept in `backend/tests/fixtures/legifrance/`. PROD hosts and
the public article URL pattern remain unverified.

**Amended by D-010** (2026-11-27): the client uses `httpx2` instead of `httpx`.

## D-009 — Algerian nationals in scope with source precedence

**Context.** CLAUDE.md first listed Algerian nationals as out of scope. The project handoff makes
them a deliberate feature.

**Decision.** Algerian cases are in scope. For residence rights, the Franco-Algerian agreement of
27 December 1968 (as amended) prevails over CESEDA; for general procedure, CESEDA and
service-public apply. Chunks carry `nationality_scope` and `authority`; the agent has a
`precedence` node; the golden set has a "right source wins" metric.

**Consequences.** The chunk schema changes in build step 2 (see CLAUDE.md data model).

## D-010 — httpx2 as HTTP client; warnings fail the test suite

**Context.** Starlette 1.10 emits `StarletteDeprecationWarning` when its test client runs on
`httpx` and recommends `httpx2`, the continuation of HTTPX maintained by Pydantic. Alembic also
warned that `alembic.ini` declared no `path_separator`. The project does not keep deprecated
dependencies or configuration.

**Decision.** `httpx` is replaced by `httpx2` (runtime dependency, API-compatible; imports are
`import httpx2`). `alembic.ini` sets `path_separator = os`. pytest runs with
`filterwarnings = ["error"]`, so any warning, deprecations included, fails the suite locally and
in CI.

**Consequences.** A dependency upgrade that introduces a deprecation breaks CI and must be handled
in the same change (fix the call site or, with a written reason, add a targeted
`filterwarnings` ignore). The live PISTE sandbox check was repeated on `httpx2` on 2026-11-27
with the same result.

## D-011 — Ingestion: manifest, chunking, identity and removal rules

**Context.** Build step 2 loads service-public sheets, CESEDA articles and treaty texts into the
`chunks` table, with versioning and a changelog (D-003). Re-runs must be idempotent and a single
edit in an official source must produce a single changelog entry.

**Decision.**

| Topic | Rule |
|---|---|
| Source list | `sources/manifest.yaml`, validated by `ingestion.manifest.Manifest`. Sheets carry `journey_stages` and `nationality_scope`; the maintainer reviews the file before embedding. |
| Legal articles | Not listed in the manifest: the in-force articles cited by the selected sheets (`Reference` elements). Section citations are expanded through `consult/legi/tableMatieres`; cited article ids of superseded versions resolve to the version in force on the ingestion date. Each article inherits the union of the journey stages of the sheets citing it. |
| Sheet chunks | Structure-aware (`ingestion.chunker`): a section under `MAX_CHUNK_CHARS` (2,500) is one chunk; larger sections split along sub-sections, then between blocks. Each chunk text starts with its section path. |
| Article chunks | One article = one chunk, split only above the limit. `source_id` is the article **cid**, so a new version of an article updates the same chunk ids and writes `modified` entries. |
| Annex chunks | Tabular annexes (CESEDA Annexe 10, supporting documents per permit) are excluded as whole articles and indexed row by row for the points listed under `annexes` in the manifest, each with its own journey stages. `source_id` = `{annex cid}-point-{n}`; title "CESEDA, annexe 10, point n". A listed point missing from the version in force is an error. |
| Consolidated treaty chunks | One provision in force of a consolidation file = one chunk, ending with the instruments that set its wording (D-012). |
| Treaty chunks | One clause (centred "Article …" heading in the JORF text) = one chunk; the preamble and the publishing decree's own articles are separate chunks. `source_type = treaty`, `source_id` = JORFTEXT cid. |
| Content hash | SHA-256 of title and text. Metadata changes (stages, scope, tags) update rows without a changelog entry. |
| Removal | Only for sources loaded in full; a source type is complete only when every source of that type loaded without error. A failed fetch never empties the index. |
| Quotas | The Légifrance client retries HTTP 429 up to four times (`Retry-After`, else 1, 2, 4, 8 s). |
| Ministry guidance pages | `source_type = fact_sheet`, `authority = official`. |

**Consequences.** A full run (16 sheets) yields about 680 chunks (425 sheet, 227 article, 25
treaty) from about 260 API calls. `procedure_tags` stay empty until a tag vocabulary is defined.
Code du travail and Code pénal articles cited by the sheets are not ingested yet.

## D-012 — Sources without machine-readable official text

**Status.** Agreement: accepted (2026-11-27), consolidation pending maintainer verification. Ministry page: open.

**Context.**

| Source | Finding (2026-11-27) |
|---|---|
| Franco-Algerian agreement of 27 December 1968 (Décret n° 69-243, `JORFTEXT000000694290`) | `consult/jorf` returns metadata only. The official text exists only as a scan of the full JO issue (`JORF_19690322_69.pdf`, 15 MB). No consolidated (LODA) version exists. |
| First amendment (Décret n° 86-320, `JORFTEXT000000333424`) | Same: scan only (`JORF_19860308_57.pdf`, 28 MB). |
| Second and third amendments (Décrets n° 94-1103 and 2002-1500) | Full text available; ingested. |
| Ministry page on bilateral agreements (immigration.interieur.gouv.fr) | Automated requests receive an HTTP 403 JavaScript challenge. Not fetched; the challenge is not bypassed. |

**Options.**

1. Agreement: OCR the official scans (pages 5 and 14), with a manual check of the text against
   the PDF; `authority = official`.
2. Agreement: index a consolidated text from GISTI with `authority = secondary` (never outranks
   an official chunk).
3. Agreement: index only the official machine-readable texts (second and third amendments) and
   the service-public Algerian sheets; answer "no official source" for provisions only in the
   original text.
4. Ministry page: a maintainer-saved copy read from a local file (freshness checked by hand), or
   no ministry source.

**Decision (agreement).** A consolidated text is maintained in
`sources/franco-algerian-agreement.yaml` and loaded by `ingestion.consolidation`
(manifest: `treaties.consolidations`).

| Topic | Rule |
|---|---|
| Base texts | 1968 agreement and first amendment: text layer of the Journal officiel scans (JO of 22 March 1969 and 8 March 1986), saved by the maintainer because Légifrance refuses automated downloads (HTTP 403) and the PISTE API (Swagger 2.4.2) has no PDF endpoint; cross-read with the UN Treaty Series reprint (vol. 677 and 1485), whose typography differs. Second and third amendments: Légifrance `consult/jorf`. |
| Consolidation | One entry per provision in force (Accord articles, Protocol titles). Amended passages use only the words quoted by the amending instrument; every change is recorded in `history`. Repealed provisions and obsolete exchanges of letters carry no text. Editorial restorations (missing closing quotation marks in the Légifrance text) are listed in `editorial_notes`. |
| Checks | `tests/test_consolidation.py`: every alinéa replaced or inserted by the 1994 and 2001 amendments matches the recorded JORF texts; every 1968 and 1985 passage outside the 2001 edits is found in the JO scan text layer; each quoted 2001 phrase is checked on both sides; replaced wording is checked absent. Mistral OCR (`mistral-ocr-latest`) of the JO pages was run once as a second reading, on the maintainer's Mistral Pro subscription, and agrees with the consolidation; it is not part of the pipeline. |
| Entry into force | Recorded per instrument (`in_force_from`, `in_force_note`). First and second amendments: on signature (22 December 1985, 28 September 1994), per their own clauses. Third amendment: 1 January 2003, per footnote (1) to the title of Décret n° 2002-1500 in the JO of 26 December 2002 (p. 21614); the Légifrance API text omits the footnote, so the date is checked against the scan's text layer. |
| Indexing | Only with `status: verified`, `verified_by` and `verified_at` set. Chunks: `source_type = treaty`, `authority = official`, `nationality_scope = algerian`, `source_id = accord-franco-algerien-1968:{provision}`; URL = official publication of the instrument that last set the wording; `in_force_from` = latest entry-into-force date of those instruments. |

## D-013 — Passage embeddings: mistral-embed through an own client

**Context.** Build step 2 ends with embedding the stored chunks (PLAN.md). CLAUDE.md fixes the
model (`mistral-embed`, 1024 dimensions, the `chunks.embedding` column) and requires a thin
provider interface. The project already calls Légifrance through its own `httpx2` client (D-008,
D-010); LangChain and the Mistral SDK are not installed (D-007).

**Decision.**

| Topic | Rule |
|---|---|
| Client | `rag.embeddings.MistralEmbedder`, an `httpx2` client for `POST https://api.mistral.ai/v1/embeddings` (`model`, `input` list; response `data[].embedding`, `data[].index`, `usage.prompt_tokens`), per the official documentation read on 2026-11-27. It implements the `Embedder` protocol, which query-time retrieval reuses so that queries and passages share one model. No SDK dependency. |
| Input | `title + "\n\n" + text`: the fields covered by `content_hash` and by the lexical index (`tsv`). A chunk's embedding is reset exactly when its input changes (D-003). |
| Batching | At most 32 chunks and 40,000 characters per request. The documentation states no per-request or rate limit (UNVERIFIED); HTTP 429 and 5xx are retried with backoff, honouring `Retry-After`. |
| Writes | One transaction per batch; a vector is written only if the chunk's `content_hash` is unchanged since it was read. A failed or interrupted run keeps finished batches; the next `embed` run continues with the remaining chunks. |
| Validation | The response must return one 1024-dimensional vector per input, indexes `0..n-1`; anything else fails the run. |
| Model change | The model is a constant (`EMBEDDING_MODEL`). Changing it requires re-embedding every chunk (set `embedding` to NULL) and a new evaluation run. |

**Consequences.** First run on 2026-11-27 (local Postgres, Mistral Pro subscription): 737
chunks (460 fact-sheet, 256 law, 21 treaty), 858,135 input characters, 25 requests,
286,736 prompt tokens, about 20 s; all vectors have unit norm; a second run sends nothing. No
retrieval quality figure exists until the golden set (build step 3).

## D-014 — Evaluation harness: golden set, retrieval metrics and baseline

**Context.** Build step 3 requires a golden set, an evaluation CLI and a comparison between a
baseline and the pipeline (PLAN.md). Expected answers are written and verified by the
maintainer only (CLAUDE.md). No answer generation exists before build step 4.

**Decision.**

| Topic | Rule |
|---|---|
| Location | `evals/` at the repository root, run with the backend environment (`uv run --project backend python -m evals.<command>`); `evals/__init__.py` puts `backend/` on the import path (D-001). Tests in `backend/tests/test_evals_*.py` (pytest `pythonpath = [".."]`); ruff and mypy cover `../evals`. |
| Golden item | `id`, `question`, `lang`, `stage`, `nationality` (any, algerian), `expected_behaviour` (answer, refuse, handoff, abstain), `must_cite`, `winning_source`, `key_facts`, `origin`, `notes`, `verified_by`, `verified_at`. Sources are referenced as `{source_type}:{source_id}`, the identity used in `chunks`. |
| Consistency | Verification fields are set together. A verified answer item has `must_cite` and `key_facts`; a verified Algerian answer item also has `winning_source`, which must be one of `must_cite`. An answer item has a journey stage. |
| Drafts | Items without verification are drafts. `run_evals` scores verified items only unless `--include-drafts` is given, and the report then says so. |
| Retrieval metrics | Computed at source level (a source counts as retrieved when any of its chunks is): recall@5 and @10 of `must_cite`, reciprocal rank of the first expected source (MRR), and right-source-wins (the governing source is retrieved and ranks first among the expected sources). Applied to answer items with `must_cite`. |
| Other measures | Per item: latency of retrieval (query embedding + search; p50, p95 in the summary), query embedding tokens, top similarity score (reference data for the abstain threshold). |
| Not measured yet | Answer correctness, faithfulness, citation accuracy, refusal/abstain accuracy: they need generated answers (build step 4). Reports list them under `not_measured`. |
| Reports | JSON in `reports/` (git-ignored); `evals.compare` appends the markdown comparison to `docs/eval-results.md`. Reports on different set versions or items are marked not comparable. |
| First pipeline | `vector`: pgvector cosine over the embedded chunks, top 20 chunks, no lexical search, fusion, filters or re-ranking. |
| Baseline | `baseline-sp`: the pre-vectorised service-public dataset (DINUM, `AgentPublic/service-public`, config `latest`), restricted to the fact sheets of `sources/manifest.yaml` so that both retrievers draw on the same documents. Rows are fetched per sheet through the Hugging Face dataset viewer `/filter` endpoint into a git-ignored snapshot that records the dataset revision. Questions are embedded locally with BAAI/bge-m3 (pinned revision) through sentence-transformers on CPU, and ranked by cosine against the dataset's stored vectors, top 20 chunks. |
| Baseline dependencies | Optional uv group `baseline` (`sentence-transformers`, `torch` from the PyTorch CPU index). Not installed by default, in CI or in the backend image. |
| Model comparison | `bge-m3-index`: the index's own chunks (all source types) embedded locally with BAAI/bge-m3, same input text and ranking as `vector`; only the model differs. Vectors are cached in `reports/cache/bge-m3-index.json` by chunk id and `content_hash`, never stored in the database. Not a baseline: it isolates the embedding model (maintainer decision, 2026-11-27). |
| Baseline compatibility | The dataset card does not document query embedding. Each baseline report records `vector_check`: the minimum cosine between stored vectors and the local re-embedding of their chunk texts. |

**Options considered for the baseline.** Re-embedding the dataset's chunk texts with
`mistral-embed` (measures chunking only, not the published vectors); DINUM's Albert API for
bge-m3 query embeddings (requires an account). Local bge-m3 was chosen by the maintainer
(2026-11-27).

**Consequences.** Before step 4, the reports measure retrieval only. The baseline holds fact
sheets only, so it cannot retrieve CESEDA articles or the agreement: its right-source-wins on
Algerian items whose governing source is the agreement is 0 by construction. Baseline latency
(local CPU embedding) and pipeline latency (API embedding) are not comparable and are reported
for information. First snapshot (2026-11-27, dataset revision `1087b4b9191f`): 322 chunks from
the 19 manifest sheets; local bge-m3 reproduced stored vectors with a minimum cosine of 0.980 over 14 checked
chunks; about 130 ms per question on CPU. On the 32 draft questions, `mistral-embed` top
similarities stay high for out-of-scope and no-source questions (0.82 to 0.83) where bge-m3
drops to 0.44 to 0.58: a raw similarity threshold is a weak abstain signal on the index.


## D-015 — Sources added for golden-set coverage: included articles and case law

**Context.** Four golden items (g003, g008, g020, g022) had no supporting chunk in the index.
The answer contract (CLAUDE.md, rule 2) allows `abstain` only when no official source exists;
for these questions official sources exist but were not ingested:

| Item | Question | Missing source |
|---|---|---|
| g003 | Consequence of not validating the VLS-TS in time | CESEDA R431-16 to R431-18 (no selected sheet cites them) |
| g008 | Internship under a student permit | Service-public sheet F16734 |
| g020 | Job-search permit for Algerian graduates | Ruling that the CESEDA residence titles do not apply to Algerians |
| g022 | Talent card for Algerians | Same ruling |

The ministry page on the Franco-Algerian agreement (Sources, row 3) refuses automated requests
(HTTP 403) and is still not ingested.

**Decision.**

| Topic | Rule |
|---|---|
| Included articles | `laws.codes[].include_articles` lists articles indexed although no sheet cites them: `article_id` (any version), `label`, `journey_stages`, `note`. An article both cited and included is indexed once; its journey stages are the union. |
| Case law | New source type `case_law` (migration 0004). `case_law.decisions` in the manifest lists decisions by Légifrance id (`CETATEXT…` or `JURITEXT…`) with `journey_stages` and `nationality_scope`. Text from `consult/juri` (`texteHtml`, pseudonymised by Légifrance), split on paragraphs and packed into chunks keyed by the decision id. Title is the Légifrance title with the ECLI; `in_force_from` is the decision date. Licence: Légifrance / DILA, Licence Ouverte 2.0. |
| Admission rule | A decision is indexed only when it settles how indexed texts apply (for example which source governs). It is not a source of figures. |
| First entries | CESEDA R431-16, R431-17, R431-18 (stage arriving; R431-18 also renewing); sheet F16734 (working); Conseil d'État, 2 March 2026, n° 500835 (`CETATEXT000053612496`, Lebon tables), nationality scope `algerian`, stages finishing, job_offer, settling. |

`consult/juri` request and response shape come from a live sandbox call recorded as
`backend/tests/fixtures/legifrance/consult_juri_CETATEXT000053612496.json`; the PISTE Swagger
was not checked for optional fields.

**Consequences.** The four items are answerable from indexed official sources: g003 from
R431-17 and R431-18, g008 from F16734, g020 and g022 from point 3 of the decision ("régit
d'une manière complète…", the CESEDA provisions on residence titles "ne sont pas applicables
aux ressortissants algériens") read with article 7 of the agreement. Index after ingestion on
2026-11-27: 20 fact sheets (470 chunks), 245 law sources (261 chunks), 17 treaty sources
(21 chunks), 1 decision (5 chunks).

## D-016 — Hybrid retrieval: full-text and vector rankings fused with RRF

**Context.** Build step 4 replaces the vector-only retriever of step 3 with hybrid retrieval and
metadata filters (PLAN.md). On golden v1 the vector retriever finds the expected sources
(recall@5 0.708) but rarely ranks the governing source first for Algerian questions (right
source wins 0.143).

**Decision.**

| Topic | Rule |
|---|---|
| Rankings | Lexical: `ts_rank_cd` over `chunks.tsv`, query parsed with the `french` configuration, stemmed terms joined with OR (an AND of every term of a question almost never matches). Vector: cosine distance with `mistral-embed`. 50 candidates each. |
| Fusion | Reciprocal Rank Fusion, k = 60, weights lexical 0.5 and vector 1.0 (`rag.fusion`, `rag.retrieval`). A ranking with weight 0 adds no documents. |
| Filters | Applied to both rankings: `nationality_scope` (`all`, plus `algerian` when the question names an Algerian national, `guardrails.nationality`), in force on the question date, prefecture (chunks of another prefecture excluded). |
| Re-ranking | Not part of this change; measured separately. |

Ablation on the 24 scored items of golden v1 (2026-11-27, filters on, same cached query vectors):

| Variant | recall@5 | recall@10 | MRR | Right source wins |
|---|---|---|---|---|
| Vector only | 0.729 | 0.743 | 0.840 | 0.143 |
| Lexical only | 0.472 | 0.667 | 0.417 | 0.429 |
| RRF 1:1 | 0.646 | 0.722 | 0.798 | 0.286 |
| RRF 1:1, length normalisation 1 | 0.646 | 0.743 | 0.777 | 0.286 |
| RRF 1:1, normalisation 32 | 0.646 | 0.722 | 0.798 | 0.286 |
| **RRF 0.5:1** (chosen) | 0.743 | 0.792 | 0.801 | 0.286 |
| RRF 0.5:1, normalisation 1 | 0.701 | 0.771 | 0.802 | 0.286 |

**Consequences.** The weights were chosen among seven variants on the same 24 items, so the
chosen row is an optimistic estimate; the golden set needs held-out items before further
tuning. The filters alone raise vector-only results (recall@5 0.708 to 0.729, MRR 0.819 to
0.840). The lexical ranking is weak alone but doubles right source wins in fusion; the
remaining gap is the job of the precedence node. `mistral-embed` query vectors vary slightly
between calls (MRR 0.777 and 0.798 for the same configuration on two runs): differences of a
few hundredths between runs are noise at this set size.

## D-017 — Decision logic outside the graph: scope, precedence, citations, abstain

**Context.** Build step 4 requires the guardrails, precedence, citation checking and abstain
gate to be project modules unit-tested without LangChain (CLAUDE.md, Stack). Retrieval scores do
not separate answerable from unanswerable questions on golden v1: the two abstain items have a
top cosine of 0.738 and 0.833, answer items 0.765 to 0.923, and every question has lexical
matches.

**Decision.**

| Module | Rule |
|---|---|
| `guardrails.scope` | Keyword patterns (FR, EN), in order: handoff (OQTF, refusal and appeal, expired permit or irregular stay, detention), then out of scope (asylum, family reunification, naturalisation), else in scope. `personal` flags "in my case" questions, which get a general answer plus the handoff notice. |
| `guardrails.nationality` | Algerian only on an explicit mention (nationality, Algeria, "certificat de résidence"); otherwise unknown, and `clarify` asks. |
| `rag.precedence` | Algerian question: topic `procedure` on an explicit procedural marker (where, online, ANEF, documents, fees, appointment), else `rights`. Rights: the agreement governs; the best 3 chunks of the agreement and case law are retrieved separately and merged; order agreement and case law, then Algerian sheets, then the rest. Procedure: Algerian sheets, general sheets, CESEDA, then the agreement. Other questions keep the retrieval order. |
| `rag.citations` | A sentence is kept only if it cites retrieved chunk ids and every figure it states appears in a cited chunk (spaces inside numbers and decimal comma normalised). Dropped sentences are reported with the reason and never shown. |
| `rag.abstain` | Abstain when retrieval returns nothing, the model declares the sources insufficient, or no sentence survives the citation checker. No similarity threshold. |

Retrieval with precedence (`hybrid-precedence`, 24 scored items, 2026-11-27): recall@5 0.764,
recall@10 0.799, MRR 0.849, right source wins 1.000 (7 of 7), against 0.743 / 0.792 / 0.801 /
0.286 for `hybrid` and 0.708 / 0.722 / 0.819 / 0.143 for the step-3 reference.

**Consequences.** The scope patterns and the procedural markers were checked against the golden
questions (and further cases in the tests), so their accuracy on new questions is unmeasured:
golden v2 needs held-out refuse, handoff and Algerian items. The figure check makes rule 6
mechanical for generated text: a sentence whose number is not in its cited chunk is removed,
even if the number is correct elsewhere. Behaviour accuracy (answer, refuse, handoff, abstain)
is measured once the graph runs (part 3).

## D-018 — Agent graph, answer model and structured output

**Context.** Build step 4 assembles the agent: LangGraph orchestration, answer generation with
article-level citations, and the `POST /ask` endpoint (PLAN.md). Decision logic stays in project
modules (D-017).

**Decision.**

| Topic | Rule |
|---|---|
| Orchestration | `langgraph` `StateGraph` with a `TypedDict` state (`rag.graph`): `route`, `clarify`, `retrieve`, `answer`, `check_citations`, `respond`, `refuse`, `handoff`, `abstain`. Retriever and model injected, so every path is tested with fakes. |
| Answer model | `mistral-small-2603` (Mistral Small 4), pinned by dated id; listed by the API `/v1/models` on 2026-11-27 with no deprecation. Temperature 0. |
| Structured output | `langchain_mistralai.ChatMistralAI.with_structured_output(..., method="json_schema")`. With `function_calling` the model returned sentences as `[text, "[id]"]` lists that failed validation (21 of 24 answer items abstained on golden v1). |
| Source labels | Sources are labelled `S1`..`S10` in the prompt and mapped back; with full chunk ids the model shortened them (`fact_sheet:F17319:4` to `fact_sheet:F4`). |
| Prompts | `rag/prompts/answer.v<N>.md`, every version kept; the report records the version. v1 first version; v2 labels and partial answers; v3 "a related rule is not an answer". |
| Citation figures | Titles and texts of cited chunks are checked; French number words in sources count (D-017), "un"/"une" only before a unit. |
| Language | Requested language, else detected from the question (French unless English markers dominate). |
| API | `POST /ask {question (5 to 1000 characters), lang?, stage?, nationality?}`. One `query_logs` row per request, metadata only. A failed log write does not fail the answer. |
| Dependencies | `langgraph`, `langchain-mistralai`, `langchain-core`. `langchain-mistralai` depends on `httpx`; project code uses `httpx2` (D-010) and the suite runs with warnings as errors. |
| Fixed texts | `guardrails/texts.yaml`; wording approved by the maintainer on 2026-11-27 (`status: approved`). |

**Consequences.** Results on golden v1 (`evals.agent`, prompt v3, 32 items, 2026-11-27): behaviour
accuracy 1.000 (answer 24/24, refuse 3/3, handoff 3/3, abstain 2/2); expected source cited
0.958; governing source cited first 6 of 7 (the seventh cites article 7 bis before article 7);
sentences removed by the checker 0.6 %; p50 latency about 2 s; 3,813 prompt and 381 completion tokens
per model call on average (26 calls: the 24 answer and 2 abstain items). A second identical run gave the same figures except
citation precision (0.574 and 0.629). Prompts v2 and v3 were revised after reading golden v1
failures, so these figures are optimistic; golden v2 needs held-out items. `key_facts`
coverage and faithfulness need a judge and are not measured yet. Rate limiting and the spend
cap (`DAILY_QUESTION_LIMIT`, `MONTHLY_SPEND_CAP_EUR`) are not enforced by `/ask` yet.

## D-019 — Rules table and the read-only rules tool

**Context.** Build step 5 adds a `rules` table of permit facts (thresholds, deadlines, fees,
durations, documents), each row sourced and dated, and a read-only tool the agent can use
alongside document search (PLAN.md; CLAUDE.md, rule 6: figures come from retrieved sources or a
sourced rules-table row).

**Decision.**

| Topic | Rule |
|---|---|
| Source of truth | `sources/rules.yaml` (reviewed in git), loaded by `python -m ingestion run --source rules` into `rules` (migration 0005); `--all` loads it after the sources. The table is replaced as a whole in one transaction. |
| Row | `id`, `permit`, `nationality_scope`, `topic`, `stage`, `label` (fr, en), `value`, `quote`, `source`, `verified_by`, `verified_at`. `url`, `source_title` and `source_checked_at` come from the indexed chunks. |
| Grounding checks | At load: the source has chunks in the index; the quote appears in the source's title or text (after normalising spaces and apostrophes); every figure of the value appears in the quote, French number words included. Any failure loads nothing. |
| Verification | Rows are drafted from the sources and verified by the maintainer (`verified_by`, `verified_at`). The agent uses verified rows only; `evals.agent --rules all` evaluates drafts. |
| Tool | `rag.rules.RulesTool.lookup`: one SELECT. French and English patterns detect the asked topic and the named permits; a row is returned when its topic is asked and, if permits are named, its permit is one of them; Algerian rows for an Algerian national, general rows otherwise; at most 5 rows. |
| Agent | Node `eligibility` between `clarify` and `retrieve`. Rows are given to the model as `R1`..`R5` (prompt `answer.v4`), cited like sources, checked by the citation checker against their label, value and quote, and shown as the official source they quote (`rule` field of the source). |

**Consequences.** First load (2026-11-27): 27 rows, all grounding checks passed, 0 verified.
On golden v1 the tool offers rows for 9 of the 24 answer items. With the draft rows
(`--rules all`) against no rules (`--rules none`), same code and day: behaviour accuracy 1.000 in
both; key-fact figure recall 0.480 to 0.522; 2 answers cite a rule. The gain is small because
the same figures are usually in the retrieved chunks; the rows fill figures retrieval missed
(internship stipend threshold, job-search card deadlines). The figure-recall metric counts
every number in `key_facts`, including article and decision numbers an answer need not repeat,
so its absolute value understates figure accuracy; it is used for comparisons only. Thresholds
that change by decree (salary thresholds, fees) now have one row each to update.

## D-020 — Rate limit and spend cap

**Context.** The public `/ask` endpoint has no accounts and calls billed APIs. CLAUDE.md requires
rate limiting and spend tracking in Postgres (no Redis) and no stored personal details.

**Decision.**

| Topic | Rule |
|---|---|
| Visitor | `HMAC(HMAC(IP_HASH_SECRET, day), client IP)`, hex. The raw IP is never stored; the hash changes every day; rows of past days are deleted on every write (table `usage_counters`, migration 0006). Behind a proxy, `TRUST_FORWARDED_FOR=true` reads the first `X-Forwarded-For` entry. |
| Daily limit | `DAILY_QUESTION_LIMIT` questions per visitor per UTC day; one atomic `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`. Over the limit: HTTP 429 `rate_limited`, `Retry-After` until midnight UTC. |
| Spend | Month-to-date tokens in `query_logs` (prompt, completion, query embedding) times the configured prices. Mistral list prices in USD per million tokens, read on 2026-11-27 from docs.mistral.ai/inference/pricing and the model cards: Mistral Small 4 input 0.15, output 0.6; Mistral Embed 0.1. Counted 1:1 as EUR, which overestimates while 1 USD < 1 EUR. |
| Spend cap | Estimated spend ≥ `MONTHLY_SPEND_CAP_EUR`: HTTP 503 `spend_cap`, `Retry-After` until the next month. The check runs before the counter, so a paused service counts no question. |
| Order | Guard first; a refused request runs no model and writes no log. |

**Consequences.** The spend is an estimate at list prices: the project's Mistral key is on a Pro
subscription (see `.env.example`), whose billing may differ. Evaluation runs (`evals.agent`) call
the graph directly and are not counted. Prices are settings (`PRICE_*`), to update when Mistral
changes them. The user-facing wording for 429 and 503 belongs to the frontend (build step 7).

## D-021 — Tracing with Langfuse, without user text in production

**Context.** Build step 6 adds tracing for development, evaluation runs and production
(PLAN.md). CLAUDE.md, rule 5: production traces carry no user text. Langfuse was chosen by the
maintainer (EU cloud region); the Langfuse agent skill (github.com/langfuse/skills, commit
`d3b7123`) and the Langfuse docs read on 2026-11-27 (best practices, Python SDK v5,
observation types, masking) were followed.

**Decision.**

| Topic | Rule |
|---|---|
| SDK | `langfuse` 5.2.0 (Python SDK v5), client built from settings (`LANGFUSE_*`), environment from `LANGFUSE_ENVIRONMENT`. |
| Modes | `TRACING_MODE=off` (default), `full` (development, `evals.agent --trace`), `metadata` (production). |
| Instrumentation | Manual, one typed observation per graph node: root `answer-question` (agent), `classify-scope` (guardrail), `lookup-rules` (tool), `retrieve-sources` (retriever) with `embed-question` (embedding) nested, `generate-answer` (generation: model, usage, role-labelled input, structured output, prompt version), `check-citations` (guardrail). The LangChain `CallbackHandler` is not used: it records every LangGraph node as a generic span whose input and output are the whole graph state, and it sends prompt text that `metadata` mode must not export. |
| No text in production | Text goes through `rag.tracing.text()` and is never passed to the SDK when capture is off; at export, `metadata_only_mask` (`mask_otel_spans`) deletes every attribute not on an allowlist (observation type, model, usage, cost, level, trace name and tags, environment, named safe metadata keys). An attribute added by a later integration is dropped by default. |
| Cost | Langfuse had no price for `mistral-small-2603` and `mistral-embed` (empty cost on 2026-11-27); `cost_details` are sent from the configured list prices (`PRICE_*`, D-020). |
| Evaluation | `evals.agent --trace` sends full traces tagged `eval` and `golden-v1`, with scores `behaviour_correct` and `key_fact_figure_recall` per item (comment: golden item id). |
| Identity | No `user_id` or `session_id`: there are no accounts and no conversation, and a per-visitor id would be personal data. |
| Shutdown | The API flushes buffered traces at shutdown (FastAPI lifespan); `evals.agent` flushes at the end. |
| Reading traces | Observations API v2 (`@langfuse/cli api observations list --trace-id`); the `traces` endpoints return HTTP 410. |

**Consequences.** Verified against Langfuse on 2026-11-27 by sending the same question in both
modes and reading the traces back: in `full` mode the tree, model, usage, cost and inputs and
outputs are present; in `metadata` mode the same tree, model, usage, cost and metadata are
present and the question text appears nowhere in the exported data. Cost per answered question
at list prices: about $0.0007.

## D-022 — Production database on Supabase, closed to the Data API

**Context.** Production uses Supabase Postgres (EU region, eu-central-1) on the free plan. The
application connects to Postgres directly and never uses the Supabase Data API (REST/GraphQL),
supabase-js, Auth or the API keys. Tables in `public` can be reachable through the Data API when
the client roles hold grants; the project still had default privileges granting `anon`,
`authenticated` and `service_role` TRUNCATE, REFERENCES, TRIGGER and MAINTAIN on new tables.
Sources: supabase.com/docs/guides/api/securing-your-api, .../database/postgres/row-level-security
and the changelog entry of 2026-04-28 on tables no longer exposed automatically (read
2026-11-27, following the Supabase agent skill).

**Decision.**

| Topic | Rule |
|---|---|
| Connection | Session pooler (IPv4-compatible, works from IPv6 networks too; one URL for local loads, the backend host and GitHub Actions), with `sslmode=require`. |
| Data API | Turned off in the dashboard (Integrations > Data API: "Enable Data API" off), done by the maintainer. |
| Grants | Migration 0007 revokes every grant of `anon`, `authenticated` and `service_role` on tables and sequences in `public`, and their default privileges on future tables, sequences and functions (only where these roles exist). |
| RLS | Enabled on every table, with no policy (defense in depth; the owner and `postgres` are unaffected). A migration test fails if a table in `public` lacks RLS. |
| Initial data | Copied from the evaluated local database (chunks with embeddings, chunk versions, changelog, rules), not re-ingested: identical chunk ids and content hashes. Request logs and usage counters are per environment and not copied. Later updates come from the nightly ingestion. |

**Consequences.** Verified on 2026-11-27: no table without RLS, no grant left to the client
roles, `anon` / `authenticated` / `service_role` refused on read and write, rules grounding
check passing against Supabase, and the agent answering from Supabase (1.5 to 3.6 s from the
maintainer's machine). The two databases sort ids slightly differently (collation libraries),
which can reorder exact ties only.
