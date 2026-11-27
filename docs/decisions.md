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
