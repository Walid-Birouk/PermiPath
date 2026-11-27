# Légifrance API response fixtures

Raw JSON responses recorded from the PISTE **sandbox** Légifrance API (`lf-engine-app`) by
`python -m ingestion.legifrance.check --article L422-1 --date 2026-11-26 --dump DIR`, and used by
`tests/test_legifrance_fixtures.py` to replay the client against real response layouts.

| File | Endpoint | Request | Recorded |
|---|---|---|---|
| `list_code.json` | `POST list/code` | CESEDA title, state `VIGUEUR` | 2026-11-27 |
| `search.json` | `POST search` | fond `CODE_DATE`, `NUM_ARTICLE` = `L422-1`, `DATE_VERSION` = 2026-11-26 | 2026-11-27 |
| `consult_getArticle.json` | `POST consult/getArticle` | `id` = `LEGIARTI000042776689` | 2026-11-27 |

Files are stored verbatim (no fields removed or edited).

## Source and licence

Légifrance / DILA, Licence Ouverte 2.0.
Text: Code de l'entrée et du séjour des étrangers et du droit d'asile (`LEGITEXT000006070158`),
last updated 2026-11-21 according to `list_code.json`.
Article: <https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000042776689/2026-11-26>

## Refreshing

Re-run the command above with a new `--dump` directory and copy the three files here. Assertions
in `tests/test_legifrance_fixtures.py` reference identifiers and dates from these files and must
be updated with them.
