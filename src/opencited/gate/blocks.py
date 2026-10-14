"""Blocks of a Markdown answer: paragraphs, list items, table rows and headings (spec 0003).

A result is cited in a block when the block holds its marker, so the gate matches the
numbers of each block against the results that the block cites. A citation must never
reach a number outside its block, so where Markdown is unclear the split errs towards
smaller blocks.

The split reads lines. A blank line, a thematic break or setext underline such as
``---``, a code fence, and a change of block quote depth end a block. A line that
holds a pipe is a table row and a block of its own, and a table's separator row holds
no text. A heading is a block of its own, and so is a list item, with the indented
lines below it. Block quote markers are read past, so a quoted paragraph or list item
is split as if unquoted. Offsets are in the normalised text, as the number reader
gives them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

QUOTE = re.compile(r"(?:[ \t]{0,3}>[ \t]?)+")
LIST_ITEM = re.compile(r"[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]")
HEADING = re.compile(r"[ \t]{0,3}#{1,6}(?:[ \t]|$)")
FENCE = re.compile(r"[ \t]{0,3}(?:```|~~~)")
BREAK = re.compile(r"[ \t]{0,3}([-*_=])(?:[ \t]*\1){2,}[ \t]*")
TABLE_ROW = re.compile(r"(?:^|[^\\])\|")
SEPARATOR_ROW = re.compile(r"[ \t:|-]*-[ \t:|-]*")


@dataclass(frozen=True)
class Block:
    start: int
    end: int


class Splitter:
    """Collects blocks while it reads the lines of a text."""

    def __init__(self) -> None:
        self.found: list[Block] = []
        self.start: int | None = None
        self.in_item = False

    def close(self, end: int) -> None:
        if self.start is not None:
            self.found.append(Block(self.start, end))
        self.start, self.in_item = None, False

    def alone(self, start: int, end: int) -> None:
        self.found.append(Block(start, end))


def blocks(text: str) -> tuple[Block, ...]:
    """Return the blocks of a text in order; every character belongs to at most one."""
    split = Splitter()
    depth = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        line_start, line_end = offset, offset + len(content)
        offset += len(line)
        quote = QUOTE.match(content)
        level = quote[0].count(">") if quote else 0
        inner = content[quote.end() :] if quote else content
        if level != depth:
            split.close(line_start)
            depth = level
        if not inner.strip() or FENCE.match(inner) or BREAK.fullmatch(inner):
            split.close(line_start)
            continue
        if TABLE_ROW.search(inner):
            split.close(line_start)
            if not SEPARATOR_ROW.fullmatch(inner):
                split.alone(line_start, line_end)
            continue
        if HEADING.match(inner):
            split.close(line_start)
            split.alone(line_start, line_end)
            continue
        if LIST_ITEM.match(inner):
            split.close(line_start)
            split.start, split.in_item = line_start, True
            continue
        if split.in_item and not inner.startswith((" ", "\t")):
            # A line that is not indented ends the list item and starts a paragraph.
            split.close(line_start)
        if split.start is None:
            split.start = line_start
    split.close(len(text.rstrip("\r\n")))
    return tuple(block for block in split.found if text[block.start : block.end].strip())
