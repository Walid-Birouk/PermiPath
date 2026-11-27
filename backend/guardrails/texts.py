"""Fixed user-facing texts (`texts.yaml`) and the answer language."""

import re
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml

Lang = Literal["fr", "en"]
TEXTS_PATH = Path(__file__).with_name("texts.yaml")

_FRENCH = re.compile(
    r"[àâçéèêëîïôûùüÿœ]|\b(je|j'|mon|ma|mes|le|la|les|un|une|des|du|est|suis|peut|quel|quelle|"
    r"comment|pour|avec|dans|après|titre|séjour|carte)\b",
    re.IGNORECASE,
)
_ENGLISH = re.compile(
    r"\b(i|my|the|a|an|is|am|can|which|what|how|for|with|after|permit|residence|card|do|does)\b",
    re.IGNORECASE,
)


def detect_lang(question: str) -> Lang:
    """French unless English markers clearly outnumber French ones."""
    french, english = len(_FRENCH.findall(question)), len(_ENGLISH.findall(question))
    return "en" if english > french else "fr"


@cache
def texts() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(TEXTS_PATH.read_text(encoding="utf-8"))
    return data


def notice(name: str, lang: Lang) -> str:
    return str(texts()["notices"][name][lang]).strip()


def response(kind: str, lang: Lang, topic: str | None = None) -> str:
    template = str(texts()["responses"][kind][lang]).strip()
    label = texts()["topics"].get(topic or "", {}).get(lang, topic or "")
    return template.format(topic=label)
