# Service-Public.gouv.fr sheet fixtures

Sheets copied verbatim from the "Fiches pratiques et ressources de Service-Public.gouv.fr
Particuliers" XML feed, schema 3.5, used by the service-public parser, chunker and store tests.

| File | Sheet | Layout | Feed date |
|---|---|---|---|
| `F2231.xml` | Étudiant étranger en France : visa de long séjour ou titre de séjour | `Introduction` + `ListeSituations` | 2026-11-26 |
| `F2733.xml` | Autorisation de travail d'un Algérien salarié en France | `Introduction` + `Texte` | 2026-11-26 |
| `F31969.xml` | Que faire en cas de difficulté lors d'une demande de titre de séjour ? | `Texte` + `ListeSituations` | 2026-11-26 |

## Source and licence

Service-Public.gouv.fr / DILA, Licence Ouverte 2.0.
Feed: <https://lecomarquage.service-public.gouv.fr/vdd/3.5/part/zip/vosdroits-latest.zip>
Schema: <https://echanges.dila.gouv.fr/OPENDATA/SERVICE-PUBLIC_DTD/schema_3.5.zip>
Sheet pages: `https://www.service-public.gouv.fr/particuliers/vosdroits/<ID>`

## Refreshing

Download the feed, extract the files above and replace them here. `tests/test_ingestion_store.py`
edits one sentence of `F2231.xml` in memory; if that sentence disappears from the sheet, update
the `ORIGINAL` constant (the test `test_fixture_contains_the_edited_sentence_once` fails first).
