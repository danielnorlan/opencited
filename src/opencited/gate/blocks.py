"""Blocks of a Markdown answer: paragraphs, list items, table rows and headings (spec 0003).

A result is cited in a block when the block holds its marker, so the gate matches the
numbers of each block against the results that the block cites. The split reads lines:
a blank line ends a paragraph, a line that starts a list item, a table row or a heading
starts a block of its own, and an indented line continues the list item above it.
Offsets are in the normalised text, as the number reader gives them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LIST_ITEM = re.compile(r"[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]")
TABLE_ROW = re.compile(r"[ \t]*\|")
HEADING = re.compile(r"[ \t]{0,3}#{1,6}[ \t]")
# A table's separator row holds no text.
SEPARATOR_ROW = re.compile(r"[ \t]*\|?[ \t:|-]+\|?[ \t]*")


@dataclass(frozen=True)
class Block:
    start: int
    end: int


def blocks(text: str) -> tuple[Block, ...]:
    """Return the blocks of a text in order; every character belongs to at most one."""
    found: list[Block] = []
    start: int | None = None
    in_item = False
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        line_start, line_end = offset, offset + len(content)
        offset += len(line)
        if not content.strip():
            if start is not None:
                found.append(Block(start, line_start))
            start, in_item = None, False
            continue
        if TABLE_ROW.match(content) or HEADING.match(content):
            if start is not None:
                found.append(Block(start, line_start))
            if not SEPARATOR_ROW.fullmatch(content) or not TABLE_ROW.match(content):
                found.append(Block(line_start, line_end))
            start, in_item = None, False
            continue
        if LIST_ITEM.match(content):
            if start is not None:
                found.append(Block(start, line_start))
            start, in_item = line_start, True
            continue
        if in_item and not content.startswith((" ", "\t")):
            # A line that is not indented ends the list item and starts a paragraph.
            if start is not None:
                found.append(Block(start, line_start))
            start, in_item = line_start, False
            continue
        if start is None:
            start = line_start
    if start is not None:
        found.append(Block(start, len(text.rstrip("\r\n"))))
    return tuple(block for block in found if text[block.start : block.end].strip())
