# PLAN.md — build plan

Read CLAUDE.md first. Work one step at a time; each step ends when its "Done when" passes, with
tests. Don't start the next step until Walid asks.

Step 0 (setup: monorepo, docker-compose, Alembic schema, `/health`) is done; open items are in
`internal coms/phase-0-review.md` (Docker check, git repo, CI).

---

## Step 1 — Prove the PISTE keys work
Script: get a PISTE sandbox token (client_credentials, creds from `.env`) → locate CESEDA →
fetch one article as in force on a given date.
- `backend/ingestion/legifrance/` client (token caching, typed errors, no secrets in output)
- CLI: `uv run python -m ingestion.legifrance.check --article L422-1 --date YYYY-MM-DD`
- `.env.example` with the PISTE variables
- Tests with mocked HTTP (`httpx.MockTransport`)

**Done when:** tests pass, and Walid's live sandbox run prints the CESEDA text ID and the article's
version in force on the date (ID, dates, text start, Légifrance link).

## Step 2 — Ingest → index
- Migrate chunk metadata to the CLAUDE.md data model (`source_type`, `authority`,
  `nationality_scope`, `prefecture`).
- Loaders: service-public XML (current feed schema; attribution: URL, file name, date), CESEDA
  articles via Légifrance (articles cited by the selected sheets, L and R parts), the
  Franco-Algerian agreement (original + amendment decrees from the JORF; GISTI only as labelled
  secondary), the ministry page on the agreement.
- `sources/manifest.yaml` of selected sources → **Walid reviews it** before embedding.
- Chunking: one article / treaty clause = one chunk (split only if long, keep ID); sheets by section.
- Embed with `mistral-embed`; `tsv` with french config.
- Diff + changelog on re-ingest (`content_hash`); changes marked `needs_review`.
- CLI: `python -m ingestion run --all | --source sp|legifrance|treaty|ministry`, `--dry-run`.

**Done when:** corpus ingested; re-running is idempotent; a modified fixture produces exactly one
changelog entry; parsers and chunker are tested.

## Step 3 — Golden set v1 (30 questions) + eval harness
- Tooling: schema, validator, draft-question helper. **Never fill in answers** — Walid writes and
  verifies them (questions may come from forums; answers only from official sources).
- Item fields: `question`, `lang`, `stage`, `nationality` (any | algerian), `expected_behaviour`
  (answer | refuse | handoff | abstain), `must_cite`, `winning_source` (for precedence cases),
  `key_facts`, `verified_by`, `verified_at`.
- Mix: Algerian precedence cases, out-of-scope, personal/urgent, no-source questions.
- `python -m evals.run_evals --set golden --out reports/<date>.json`; metrics per CLAUDE.md
  (correctness, faithfulness, citation accuracy, right-source-wins, refusal, latency, cost).
- Baseline run against the pre-vectorised service-public dataset.
- Compare two reports → markdown diff table in `docs/eval-results.md`. Run in CI.

**Done when:** a baseline report and a first pipeline report exist and are diffed.

## Step 4 — LangGraph agent
- Nodes: `route` → `clarify` (nationality, diploma, current permit) → `eligibility` → `retrieve`
  (hybrid FTS + pgvector, RRF, re-rank, metadata filters, in force on date) → `precedence` →
  `answer` (structured: sentences with chunk IDs) → `check_citations`; branches to `refuse`,
  `abstain`, `handoff`. "Not legal advice" notice on every answer. FR and EN.
- Unit tests for fusion, abstain gate, precedence, citation checker without LangChain; graph test
  per path with a mocked model. Mermaid diagram into `docs/architecture.md`.
- `POST /ask {question, lang?, stage?}`.

**Done when:** all paths tested; eval report shows the delta vs step 3.

## Step 5 — SQL rules tool
- `rules` table: permit type, nationality scope, required documents, thresholds, deadlines; every
  row has `source_id`, `url`, `verified_at` (Walid verifies values).
- Read-only tool the agent can call instead of doc search; tests + eval delta.

## Step 6 — Docker + deploy + tracing + metrics
- Backend image on a free EU host, frontend on Vercel, DB on Supabase; CORS locked.
- PROD PISTE app subscribed to Légifrance PROD (Walid).
- Tracing (LangSmith or Langfuse, no user text in prod), cost/latency dashboard, public metrics page.
- Rate limit (`DAILY_QUESTION_LIMIT`), spend cap (`MONTHLY_SPEND_CAP_EUR`); nightly GitHub Actions
  ingest + smoke eval.

## Step 7 — Prompt-injection suite, FR/EN, polish
- Injection test suite in CI; input validation; FR/EN UI pass; README with demo GIF, architecture,
  eval table. Soft launch to real students; failures go to the golden set.

## Step 8 — Later: content pipeline
RAG answer → script → ElevenLabs voice → motion template, for a short-video channel driving users
to the tool. Every video shows the source + in-force date. **Only start once v1 answers 20–30
golden questions correctly.**

## Optional later
- Judilibre (case law) via PISTE.
- Cross-encoder reranker upgrade if citation accuracy plateaus.
- Partner referral (lawyer / association) for handoffs.
