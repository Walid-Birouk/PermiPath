"""Structure-aware chunking of a section tree into retrievable passages.

A section that fits in `max_chars` becomes one chunk (its sub-sections inlined under their
titles). A larger section emits its own text, then each sub-section separately. Text that is
still too long is packed block by block, then line by line. Each chunk carries the path of
section titles that leads to it.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

MAX_CHUNK_CHARS = 2500
"""Upper bound on a chunk body, in characters (about 600 tokens of French text)."""

PATH_SEPARATOR = " > "
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


class SectionNode(Protocol):
    @property
    def title(self) -> str | None: ...
    @property
    def blocks(self) -> Sequence[str]: ...
    @property
    def children(self) -> Sequence["SectionNode"]: ...


@dataclass(frozen=True)
class TextChunk:
    path: tuple[str, ...]
    body: str

    @property
    def text(self) -> str:
        """Body prefixed with its section path, as indexed."""
        if not self.path:
            return self.body
        return f"{PATH_SEPARATOR.join(self.path)}\n\n{self.body}"


def chunk_tree(root: SectionNode, max_chars: int = MAX_CHUNK_CHARS) -> list[TextChunk]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    return list(_emit(root, (), max_chars))


def _emit(node: SectionNode, path: tuple[str, ...], max_chars: int) -> Iterator[TextChunk]:
    whole = _join(_render(node))
    if len(whole) <= max_chars:
        if whole:
            yield TextChunk(path, whole)
        return
    for piece in pack(node.blocks, max_chars):
        yield TextChunk(path, piece)
    for child in node.children:
        yield from _emit(child, (*path, child.title) if child.title else path, max_chars)


def _render(node: SectionNode) -> list[str]:
    parts = list(node.blocks)
    for child in node.children:
        if child.title:
            parts.append(child.title)
        parts.extend(_render(child))
    return parts


def pack(blocks: Sequence[str], max_chars: int) -> list[str]:
    """Greedy packing of blocks into pieces of at most `max_chars`, splitting oversized blocks
    on line breaks, then sentence ends, then hard boundaries."""
    pieces: list[str] = []
    current: list[str] = []
    for unit in _units(blocks, max_chars):
        if current and len(_join([*current, unit])) > max_chars:
            pieces.append(_join(current))
            current = []
        current.append(unit)
    if current:
        pieces.append(_join(current))
    return pieces


def _units(blocks: Sequence[str], max_chars: int) -> Iterator[str]:
    for block in blocks:
        if len(block) <= max_chars:
            yield block
            continue
        for line in block.split("\n"):
            if len(line) <= max_chars:
                yield line
                continue
            for sentence in _SENTENCE_END.split(line):
                for start in range(0, len(sentence), max_chars):
                    yield sentence[start : start + max_chars]


def _join(parts: Sequence[str]) -> str:
    return "\n\n".join(p for p in parts if p)
