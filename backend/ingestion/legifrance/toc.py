"""Index over a `consult/legi/tableMatieres` response: resolves cited sections and articles to
the article versions listed as in force (`etat = VIGUEUR`) in the table of contents."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

IN_FORCE = "VIGUEUR"


@dataclass(frozen=True)
class TocArticle:
    id: str
    cid: str
    num: str
    in_force: bool


class TocIndex:
    def __init__(self, toc: dict[str, Any]) -> None:
        self._section_articles: dict[str, list[TocArticle]] = {}
        self._by_id: dict[str, TocArticle] = {}
        for section in toc.get("sections") or []:
            self._index(section, parent_in_force=True)

    def _index(self, section: dict[str, Any], parent_in_force: bool) -> list[TocArticle]:
        in_force = parent_in_force and section.get("etat") == IN_FORCE
        articles = [self._article(a) for a in section.get("articles") or []]
        for child in section.get("sections") or []:
            articles.extend(self._index(child, in_force))
        if in_force and section.get("cid"):
            self._section_articles[str(section["cid"])] = [a for a in articles if a.in_force]
        return articles

    def _article(self, raw: dict[str, Any]) -> TocArticle:
        article = TocArticle(
            id=str(raw["id"]),
            cid=str(raw.get("cid") or raw["id"]),
            num=str(raw.get("num") or ""),
            in_force=raw.get("etat") == IN_FORCE,
        )
        self._by_id[article.id] = article
        return article

    def section_articles(self, section_cid: str) -> list[TocArticle] | None:
        """In-force articles of an in-force section (by cid), in table order; None if the
        section is unknown or not in force."""
        return self._section_articles.get(section_cid)

    def article(self, article_id: str) -> TocArticle | None:
        return self._by_id.get(article_id)


@dataclass(frozen=True)
class Resolution:
    article_ids: tuple[str, ...]
    """Article ids to fetch, in citation order, without duplicates. Ids not listed as in force
    in the table of contents are kept: the version in force is resolved when fetching."""
    unresolved_sections: tuple[str, ...]


def resolve(index: TocIndex, sections: Iterable[str], articles: Iterable[str]) -> Resolution:
    ids: dict[str, None] = {}
    missing: list[str] = []
    for cid in sections:
        found = index.section_articles(cid)
        if found is None:
            missing.append(cid)
        else:
            ids.update(dict.fromkeys(a.id for a in found))
    for article_id in articles:
        ids[article_id] = None
    return Resolution(tuple(_dedupe_by_cid(index, ids)), tuple(missing))


def _dedupe_by_cid(index: TocIndex, ids: Iterable[str]) -> Iterator[str]:
    """Two cited versions of the same article (same cid) are fetched once."""
    seen: set[str] = set()
    for article_id in ids:
        known = index.article(article_id)
        key = known.cid if known else article_id
        if key not in seen:
            seen.add(key)
            yield article_id
