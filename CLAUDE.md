# CLAUDE.md — PermiPath

## What this project is
A public, bilingual (EN/FR) RAG + agent web app that answers questions from international students
in France about their residence permits, from the first student permit up to the change of status
to salarié (or talent – salarié qualifié). It is grounded ONLY in official sources and cites them
at article level.

It is a portfolio project for an AI engineer job search. Revenue is not a goal. Real users and
published metrics (retrieval quality, citation accuracy, "right source wins", latency, cost) are.
Project language (code, docs, commits) is English; user-facing answers are FR and EN.

## Scope
IN scope (the "journey"):
1. Arriving — VLS-TS student visa, validation on ANEF
2. Renewing — one-year card vs multi-year card
3. Working while studying — hour limits, internships
4. Finishing studies — job-search / business-creation permit (RECE)
5. Job offer — change of status to salarié or talent – salarié qualifié
6. Settling — long-term residence after the job offer (multi-year, resident and 10-year
   Algerian certificates), as information only

**Algerian nationals are IN scope, as a deliberate feature.** Their residence rights are governed
by the Franco-Algerian agreement of 27 December 1968 (amended 1985, 1994, 2001), not by CESEDA.
Precedence rule the agent must apply and test:
- Residence rights of Algerians (which permit, conditions, work rights) → **the agreement wins**
  over CESEDA.
- General procedure (how and where to apply, ANEF, documents, deadlines not set by the agreement)
  → CESEDA / service-public apply.
- Some permits have no equivalent in the agreement (e.g. talent, student-mobility CSP): say so,
  never transpose the CESEDA rule silently.
Never answer an Algerian case with CESEDA residence rules without stating which source governs.

OUT of scope (detect and say so clearly, do not answer):
- Asylum, family reunification, naturalisation, other permit categories
- Appeals, refusals, OQTF, expired permits, anything urgent → handoff response

## Non-negotiable rules
1. **Information, not advice.** French law (art. 54, loi du 31 décembre 1971) reserves personalised
   legal consultation to lawyers. The app explains general rules and procedures. It never tells a
   user "in your case you should…". Every answer carries a "not legal advice" notice.
   Personal-situation questions get a general answer + handoff (lawyer, student associations, the
   school's international office).
2. **Every factual sentence must be backed by a retrieved chunk or a sourced rules-table row.**
   If nothing supports it, the sentence is removed. If retrieval confidence is low, answer
   "I don't have an official source for this" and point to the right official page.
3. **Every answer shows sources** (article / sheet / treaty clause, with link), the in-force date
   used and the date the source was last checked.
4. **Official sources only** in the index (see Sources). Forums and Facebook groups are NEVER
   indexed: they are used only to collect real questions for the eval set. Secondary sources
   (e.g. GISTI consolidation of the agreement) must be labelled `secondary` and never outrank an
   official one.
5. **Privacy by design.** No accounts. Do not persist user messages that contain personal details.
   Logs store only: timestamp, journey stage, retrieved chunk IDs, latency, token counts, eval flags.
   Immigration status is sensitive data under GDPR — treat all user input as sensitive.
6. **Never invent legal figures** (hour limits, salary thresholds, fees, deadlines) in code, prompts,
   fixtures or UI copy. They come from retrieved sources, or from the SQL rules table where every
   row carries its source URL/article ID and a verified-on date. Test fixtures use obviously fake
   values or real API responses captured verbatim.
7. **Data licences:** service-public and Légifrance data are under Licence Ouverte 2.0. Credit
   "Service-Public.gouv.fr / DILA" or "Légifrance / DILA" with the full source URL, file name and
   update date. Respect PISTE API terms and quotas.
8. **Secrets only in `.env`** (git-ignored). Never in code, tests, fixtures, logs, commits or chat.
   `.env.example` lists every variable with no real values and is kept current.
9. **Prefer official documentation; flag anything unverified** in code comments and replies
   (e.g. an endpoint shape taken from a third-party client rather than the PISTE Swagger).
10. **Security:** no write actions anywhere in the agent; validate input; prompt-injection test
    suite in CI (build step 7).

## Sources
| # | Source | Access | Notes |
|---|--------|--------|-------|
| 1 | Légifrance API via PISTE | OAuth2 client_credentials | CESEDA articles as in force on a date, code search, JORF (decrees publishing the agreement's amendments), circulars. Sandbox for dev; PROD app needed for deployment. Bulk alternative: LEGI dumps (echanges.dila.gouv.fr/OPENDATA/). |
| 2 | Service-public.fr fact sheets | XML on data.gouv.fr, daily, Licence Ouverte 2.0 | Use the **current** feed schema (v3.3 was retired May 2026). The pre-vectorised data.gouv version is ONLY an eval baseline to beat, never the pipeline. |
| 3 | Ministry page on the Franco-Algerian agreement | immigration.interieur.gouv.fr | Official guidance on Algerian-specific rules. |
| 4 | Agreement text | JORF decrees (original + amendments, e.g. Decree 2002-1500 for the 3rd) | Official = original + amendments as published. GISTI consolidation = secondary, labelled; or rebuild the consolidation ourselves. |
| 5 | Prefecture document lists (PDFs) | per prefecture | Start with 2–3 (Paris, Seine-Saint-Denis). `prefecture` is metadata. |
| 5b | Case law (Conseil d'État, Cour de cassation) | Légifrance API `consult/juri` | Only decisions that settle how indexed texts apply (e.g. CESEDA titles do not apply to Algerians). Listed in the manifest (D-015). |
| 6 | Forums / Facebook groups | — | NOT indexed. Eval questions only. |

## Stack
- Backend: Python 3.12, FastAPI, Pydantic v2, uv. HTTP client: httpx2 (D-010).
- DB: Postgres + pgvector on **Supabase free tier** (EU region); Postgres `tsvector` (french config)
  for BM25-like lexical retrieval. Keep the corpus lean.
- LLM + embeddings: **Mistral** (`mistral-small-*` generation, `mistral-embed`), behind a thin
  provider interface.
- Orchestration: **LangGraph** `StateGraph` with typed state; **LangChain** components where they
  fit (`langchain-mistralai`, `langchain-postgres` or a custom retriever). Pin versions; check
  current docs before using a class, don't rely on memory.
- Project-specific logic stays in our own modules, unit-tested without LangChain: fusion,
  re-ranking glue, abstain threshold, precedence, citation checking, guardrails.
- Frontend: Next.js (App Router) + TypeScript + Tailwind, i18n EN/FR, **Vercel free tier**.
- Backend host: free tier with an EU region (pick at deploy time after checking limits). Expect
  cold starts; `/health` stays cheap.
- Jobs: GitHub Actions scheduled workflow (nightly ingestion + diff + smoke eval; keeps free tiers awake).
- Rate limiting and spend tracking in Postgres (no Redis).
- Docker / docker-compose locally and for the backend image. **No Kubernetes.**
- Observability: LangSmith or Langfuse tracing in dev and eval runs; on production traffic only
  without user text (rule 5). Cost/latency dashboard and a public metrics page.
- Tests: pytest; evals in `evals/` via a CLI, run in CI.

## Target architecture
- **Ingestion:** per-source loaders → chunking → metadata on every chunk.
- **Retrieval:** hybrid (Postgres full-text + pgvector) fused with RRF, then re-ranking, with
  metadata filters (nationality_scope, source_type, in-force date, prefecture).
- **SQL rules table:** permit type, required documents, salary thresholds, deadlines — each row
  sourced and dated. The agent chooses between doc search (RAG) and a read-only SQL tool.
- **LangGraph agent:** `route` → `clarify` (nationality, diploma, current permit) → `eligibility`
  (rules tool) → `retrieve` → `precedence` (which source wins) → `answer` with article-level
  citations, or `refuse` / `abstain` / `handoff`. Always the "not legal advice" notice.

## Repo layout (target)
```
backend/
  app/            FastAPI app (api routes)
  ingestion/      source clients (legifrance/, service_public/, …), parsers, chunker, diff + changelog
  rag/            LangGraph graph, nodes/, retrievers, fusion, precedence, citation checker, prompts/
  guardrails/     scope + personal-situation classifier, injection checks, handoff texts
  db/             schema, migrations (alembic)
evals/
  golden/         golden Q&A set (YAML), written and verified by Walid
  run_evals.py    metrics + report JSON for the public metrics page
frontend/         Next.js app
infra/            docker-compose (local), Dockerfiles, deploy notes
.github/workflows/ CI, nightly ingestion, eval run
docs/             architecture.md, decisions.md, eval-results.md
```

## Data model essentials
Each chunk stores: `id`, `source_type` (law | treaty | fact_sheet | prefecture | case_law; migrations 0002, 0004), `authority`
(official | secondary), `nationality_scope` (all | algerian), `source_id` (article ID / sheet ID /
treaty clause), `prefecture` (nullable), `title`, `url`, `text`, `lang`, `journey_stages[]`,
`procedure_tags[]`, `in_force_from`, `in_force_to`, `source_updated_at`, `fetched_at`,
`content_hash`, `embedding`, `tsv`. Keep previous versions (soft-versioning by `content_hash`).

## Evaluation (the CV differentiator)
- Golden set: v1 = 30 questions (build step 3), target 100–150 real questions with verified
  answers + source links, written by Walid from official sources.
- Metrics: answer correctness, faithfulness, citation accuracy, "right source wins" for Algerian
  cases, refusal/abstain accuracy, latency, cost per answer. Run in CI.
- Baseline: the pre-vectorised service-public dataset.

## Conventions
- Small, typed functions; no hidden global state; config via env vars (`.env.example` kept current).
- One build step at a time (see PLAN.md), each with tests. Don't start the next step unasked.
- Every pipeline stage is testable in isolation with fixtures in `backend/tests/fixtures/`.
  External HTTP is mocked in tests (`httpx2.MockTransport`); no test needs real credentials.
- Prompts live in versioned files under `backend/rag/prompts/`, never inline strings.
- When changing retrieval, chunking or prompts: run evals and paste the metric diff in the PR/commit.
- Write decisions (and why) in `docs/decisions.md`.
- Be direct: challenge weak ideas, no padding.

## Things only Walid does (ask, don't fake)
- PISTE account / Légifrance apps (sandbox and PROD), Mistral API key, any credential
- Running anything that needs his `.env` (live API calls)
- Writing and verifying golden answers in `evals/golden/`
- Final wording of disclaimers and handoff texts
- Anything touching deployment credentials or DNS
