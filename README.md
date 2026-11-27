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
uv run ruff check . && uv run ruff format --check .
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
