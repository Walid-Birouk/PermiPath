# PermiPath

Bilingual (EN/FR) question-answering service for international students in France about
residence permits, from the student visa to the change of status to an employee permit.
Answers are generated only from official sources (Légifrance, Service-Public.gouv.fr, ANEF)
and always cite them. The service provides general information, not legal advice.

> Status: under construction. See `PLAN.md` for the build plan.

Algerian nationals are covered with explicit source precedence: the Franco-Algerian agreement of
27 December 1968 governs their residence rights; CESEDA and service-public govern procedure.

## Repository layout

| Path | Contents |
|------|----------|
| `backend/` | FastAPI app (`app/`), ingestion, RAG pipeline, guardrails, database (`db/`) |
| `frontend/` | Next.js (App Router, TypeScript, Tailwind) |
| `infra/` | Docker Compose stack and Dockerfiles |
| `evals/` | Golden question set and evaluation harness |
| `docs/` | Architecture, decision log, evaluation results |
| `.github/workflows/` | CI and scheduled jobs |

## Local development

### Prerequisites

- Docker with the Compose plugin
- For running checks outside containers: `uv` and Node.js 24+

### Start the stack

```bash
cp .env.example .env    # optional for the stack; required for API keys later
docker compose up --build
```

| URL | Service |
|-----|---------|
| http://localhost:8000/health | API health check |
| http://localhost:8000/docs | OpenAPI documentation |
| http://localhost:3000 | Frontend |

On start, the backend applies all database migrations.

### Backend checks

```bash
cd backend
uv sync
uv run ruff check . ../evals && uv run ruff format --check . ../evals
uv run mypy
uv run pytest                 # tests marked `db` are skipped without TEST_DATABASE_URL
```

Tests marked `db` apply and roll back migrations. They are destructive and run only when
`TEST_DATABASE_URL` points to a disposable database (the local stack provides
`permipath_test`):

```bash
TEST_DATABASE_URL=postgresql://permipath:permipath@localhost:5432/permipath_test uv run pytest
```

### Légifrance (PISTE) live check

Needs `PISTE_CLIENT_ID` and `PISTE_CLIENT_SECRET` in `.env`:

```bash
cd backend
uv run python -m ingestion.legifrance.check --article L422-1 --date 2026-11-26
```

It fetches a token, locates CESEDA and prints the article version in force on the date.
`--dump DIR` saves the raw responses.

### Ingestion and embedding

Sources are selected in `sources/manifest.yaml`. Ingestion needs `DATABASE_URL` (the local
stack's database by default) and, for Légifrance and the Franco-Algerian agreement, the PISTE
credentials. Embedding needs `MISTRAL_API_KEY`.

```bash
docker compose up -d db                                  # local Postgres + pgvector
cd backend
uv run alembic upgrade head
uv run python -m ingestion run --all --dry-run          # load and chunk, no database access
uv run python -m ingestion run --all                    # sync chunks, versions, changelog
uv run python -m ingestion embed --dry-run              # chunks, characters, requests to send
uv run python -m ingestion embed                        # fill missing embeddings (mistral-embed)
```

| Command | Effect |
|---------|--------|
| `run --all` / `run --source sp\|legifrance\|treaty\|case_law` | Loads the selected sources and syncs them: new chunks added, changed chunks archived and their embedding reset, removed chunks deleted; one changelog entry per change. Re-running without source changes writes nothing. |
| `run ... --date YYYY-MM-DD` | In-force date for legal texts (default: today, UTC). |
| `embed` | Embeds every chunk whose embedding is empty, in batches of at most 32 chunks and 40,000 characters, one transaction per batch. An interrupted run resumes where it stopped. Billed per input token; the token count is printed. |
| `embed --limit N` | Embeds at most N chunks. |

Exit codes: `0` success, `1` a source or the provider failed, `2` configuration error.

### Evaluation

The golden set is `evals/golden/golden.yaml` (schema and rules: `evals/golden_set.py`). Commands
run from the repository root with the backend environment:

```bash
uv run --project backend python -m evals.validate [--check-sources] [--require-verified]
uv run --project backend python -m evals.draft add --question "..." --lang fr --stage working
uv run --project backend python -m evals.draft sources F2713          # find must_cite references
uv run --project backend python -m evals.run_evals --set golden --pipeline vector
uv run --project backend python -m evals.baseline fetch               # baseline snapshot
uv run --project backend --group baseline python -m evals.run_evals --pipeline baseline-sp
uv run --project backend python -m evals.compare reports/BASE.json reports/NEW.json \
    --append docs/eval-results.md
```

| Command | Effect |
|---------|--------|
| `evals.validate` | Checks the schema and consistency rules and prints the set's mix. `--check-sources` confirms every `must_cite` source exists in the index; `--require-verified` rejects draft items. Runs in CI. |
| `evals.draft add` | Appends a draft item (next id, empty answer fields). Expected answers are written by the maintainer. |
| `evals.draft sources TEXT` | Lists source references whose title, id or URL contains TEXT. |
| `evals.run_evals` | Runs verified items (`--include-drafts` for all) through a retriever and writes `reports/<date>-<pipeline>.json` (git-ignored). Embeds each question with `mistral-embed`. |
| `evals.baseline fetch` | Saves the rows of the manifest's fact sheets from the pre-vectorised service-public dataset (`AgentPublic/service-public`) to `reports/cache/service-public-baseline.json`, with the dataset revision. |
| `run_evals --pipeline baseline-sp` | Runs the baseline: questions embedded locally with BAAI/bge-m3 on CPU (optional dependency group `baseline`, about 2.2 GB of model files on first use), ranked against the dataset's own vectors. |
| `run_evals --pipeline bge-m3-index` | Model comparison: the index's chunks embedded locally with bge-m3 (cached in `reports/cache/`; the first run embeds every chunk on CPU, several minutes), ranked like `vector`. Needs `--group baseline`. |
| `evals.compare` | Renders the summary metrics of two reports and their difference as a markdown table. |

Metrics and report format: D-014 in `docs/decisions.md`; published comparisons:
`docs/eval-results.md`.

### Frontend checks

```bash
cd frontend
npm ci
npm run lint && npm run typecheck && npm run build
```

## Configuration

All configuration is read from environment variables. `.env.example` lists every variable.

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | Postgres URL. `postgresql://` URLs are converted to the psycopg 3 driver. |
| `TEST_DATABASE_URL` | No | Disposable database for `db`-marked tests. |
| `MISTRAL_API_KEY` | Later phases | Mistral API key (generation, embeddings, OCR). The project uses a Mistral Pro subscription; a key without a billing plan is refused with HTTP 402. |
| `PISTE_CLIENT_ID`, `PISTE_CLIENT_SECRET` | For Légifrance | PISTE OAuth credentials for the Légifrance API. |
| `PISTE_ENV` | No | `sandbox` (default) or `prod`. `PISTE_OAUTH_URL` / `PISTE_API_URL` override the hosts. |
| `DAILY_QUESTION_LIMIT` | No | Per-client daily question limit. |
| `MONTHLY_SPEND_CAP_EUR` | No | Monthly LLM spend cap, in euros. |
| `CORS_ORIGINS` | No | JSON list of allowed browser origins. |

## Data sources and licences

Content from Service-Public.gouv.fr and Légifrance is reused under Licence Ouverte 2.0
(credit: Service-Public.gouv.fr / DILA; Légifrance / DILA).

The evaluation baseline uses "Fiches pratiques Service-Public.fr vectorisées" (DINUM,
<https://huggingface.co/datasets/AgentPublic/service-public>), Licence Ouverte / Etalab 2.0,
built from Service-Public.gouv.fr / DILA content. It is never indexed by the application.
