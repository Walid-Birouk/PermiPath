"""Structure-aware chunker. Synthetic trees use placeholder text; the coverage test runs on a
real sheet (Service-Public.gouv.fr / DILA, Licence Ouverte 2.0)."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ingestion.chunker import MAX_CHUNK_CHARS, TextChunk, chunk_tree, pack
from ingestion.service_public.parser import SheetSection, parse_sheet_file

FIXTURES = Path(__file__).parent / "fixtures" / "service_public"


@dataclass
class Node:
    title: str | None
    blocks: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)


def test_small_tree_is_one_chunk_with_inlined_titles() -> None:
    tree = Node(None, ["intro"], [Node("A", ["a1"]), Node("B", ["b1"])])
    assert chunk_tree(tree, max_chars=100) == [TextChunk((), "intro\n\nA\n\na1\n\nB\n\nb1")]


def test_large_tree_splits_along_children_with_paths() -> None:
    tree = Node(
        None, ["intro"], [Node("A", ["x" * 40], [Node("A1", ["y" * 40])]), Node("B", ["b"])]
    )
    chunks = chunk_tree(tree, max_chars=60)
    assert [(c.path, len(c.body)) for c in chunks] == [
        ((), 5),
        (("A",), 40),
        (("A", "A1"), 40),
        (("B",), 1),
    ]


def test_untitled_child_keeps_parent_path() -> None:
    tree = Node("Root", ["r" * 30], [Node(None, ["c" * 30])])
    assert [c.path for c in chunk_tree(tree, max_chars=40)] == [(), ()]


def test_text_prefixes_path() -> None:
    assert TextChunk(("A", "B"), "body").text == "A > B\n\nbody"
    assert TextChunk((), "body").text == "body"


def test_pack_respects_limit_and_order() -> None:
    blocks = ["a" * 30, "b" * 30, "c\n" + "d" * 50, "e. f. " + "g" * 130]
    pieces = pack(blocks, max_chars=50)
    assert all(len(p) <= 50 for p in pieces)
    assert "".join(pieces).replace("\n", "").replace(" ", "") == "".join(blocks).replace(
        "\n", ""
    ).replace(" ", "")


def test_empty_tree_has_no_chunks() -> None:
    assert chunk_tree(Node(None)) == []


def test_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        chunk_tree(Node(None, ["x"]), max_chars=0)


@pytest.mark.parametrize("sheet_id", ["F2231", "F2733", "F31969"])
def test_real_sheet_chunks_are_bounded_and_lose_no_text(sheet_id: str) -> None:
    sheet = parse_sheet_file(FIXTURES / f"{sheet_id}.xml")
    chunks = chunk_tree(sheet.root)
    assert all(len(c.body) <= MAX_CHUNK_CHARS for c in chunks)

    def blocks(section: SheetSection) -> list[str]:
        return [*section.blocks, *(b for c in section.children for b in blocks(c))]

    corpus = "\n".join(c.body for c in chunks)
    missing = [b for b in blocks(sheet.root) if len(b) <= MAX_CHUNK_CHARS and b not in corpus]
    assert missing == []
