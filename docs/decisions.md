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
