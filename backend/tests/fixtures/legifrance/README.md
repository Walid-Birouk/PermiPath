# Légifrance API response fixtures

Raw JSON responses recorded from the PISTE **sandbox** Légifrance API (`lf-engine-app`) by
`python -m ingestion.legifrance.check --article L422-1 --date 2026-11-26 --dump DIR`, and used by
`tests/test_legifrance_fixtures.py` to replay the client against real response layouts.

| File | Endpoint | Request | Recorded |
|---|---|---|---|
| `list_code.json` | `POST list/code` | CESEDA title, state `VIGUEUR` | 2026-11-27 |
| `search.json` | `POST search` | fond `CODE_DATE`, `NUM_ARTICLE` = `L422-1`, `DATE_VERSION` = 2026-11-26 | 2026-11-27 |
| `consult_getArticle.json` | `POST consult/getArticle` | `id` = `LEGIARTI000042776689` | 2026-11-27 |
| `consult_legi_tableMatieres.json` | `POST consult/legi/tableMatieres` | `textId` = `LEGITEXT000006070158`, `date` = 2026-11-27, `nature` = `CODE` | 2026-11-27 |
| `consult_jorf_JORFTEXT000000599731.json` | `POST consult/jorf` | Décret n° 2002-1500 (third amendment to the Franco-Algerian agreement) | 2026-11-27 |
| `consult_jorf_JORFTEXT000000551016.json` | `POST consult/jorf` | Décret n° 94-1103 (second amendment) | 2026-11-27 |
| `consult_getArticle_annex10.json` | `POST consult/getArticle` | `id` = `LEGIARTI000049820920` (CESEDA Annexe 10, version in force) | 2026-11-27 |

Files are stored verbatim (no fields removed or edited), with one exception:
`consult_legi_tableMatieres.json` is **trimmed** from the full table of contents (3.9 MB). It
keeps the in-force branch Partie législative > Livre IV > Titre II > Chapitre II > Section 1
(`LEGISCTA000042771652`, articles L422-1 to L422-3) and one repealed branch (Partie législative
ancienne > Livre Ier > Titre Ier). Sibling sections and articles outside these branches were
removed; every remaining entry is unchanged.

`consult_getArticle_annex10.json` is **trimmed** from the full response (about 530 kB):
`texteHtml` keeps the text before the table, the header row, the rows of points 1, 25 and 27,
and the text after the table; `texte` keeps only the text up to the end of the header row.
Kept rows are byte-identical to the recorded ones; all other fields are unchanged.

## Journal officiel scans (text layer)

`jo_scan_*.txt` files are the text layer of Journal officiel PDF issues (`JO_PAT` scans), as
output by `pdftotext` (poppler) for the listed pages, unedited. Légifrance serves these PDFs
only to browsers; they were saved by the maintainer.

| File | Issue | PDF pages | Content |
|---|---|---|---|
| `jo_scan_JORF_19690322_69_p5-7.txt` | JO of 22 March 1969 | 5-7 | Décret n° 69-243 and the agreement of 27 December 1968 |
| `jo_scan_JORF_19860308_57_p14-16.txt` | JO of 8 March 1986 | 14-16 | Décret n° 86-320 and the first amendment |
| `jo_scan_JORF_19941220_294_p23-24.txt` | JO of 20 December 1994 | 23-24 | Décret n° 94-1103 and the second amendment |
| `jo_scan_JORF_20021226_300_p14.txt` | JO of 26 December 2002 | 14 | Décret n° 2002-1500 with footnote (1) on the entry into force of the third amendment, and the start of the amendment |

## Source and licence

Légifrance / DILA, Licence Ouverte 2.0.
Texts: Code de l'entrée et du séjour des étrangers et du droit d'asile (`LEGITEXT000006070158`),
last updated 2026-11-21 according to `list_code.json`; decrees as published in the Journal
officiel: <https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000000599731>,
<https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000000551016>.
Article: <https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000042776689/2026-11-26>

## Refreshing

Re-run the command above with a new `--dump` directory and copy the three files here. Assertions
in `tests/test_legifrance_fixtures.py` reference identifiers and dates from these files and must
be updated with them.
