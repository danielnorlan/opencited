"""The provenance record: where a result came from, so that a reader can check it.

[Spec 0001](../../docs/specs/0001-ssb-mcp-server.md) defines the fields. The record
is built from the exchange that produced a result, so the request, the retrieval time
and the hash of the response body, after content decoding, are the ones that the client
sent and received.
"""

from __future__ import annotations

import hashlib

from pydantic import AwareDatetime

from opencited.http import Exchange
from opencited.model import Frozen, Sha256Hex, TableId, UtcDatetime


class RequestRecord(Frozen):
    """The request that produced a result: its method, its full URL and its body."""

    method: str
    url: str
    body: str | None


class ProvenanceRecord(Frozen):
    """Where one result came from.

    ``license`` is an SPDX identifier, spelled as the field name in spec 0001.
    """

    source_id: str
    publisher: str
    license: str
    table_id: TableId
    table_title: str
    table_url: str
    table_updated: AwareDatetime
    request: RequestRecord
    retrieved_at: UtcDatetime
    response_sha256: Sha256Hex


def sha256_hex(content: bytes) -> str:
    """Return the hexadecimal SHA-256 of a response body after content decoding."""
    return hashlib.sha256(content).hexdigest()


def record_of(
    exchange: Exchange,
    *,
    source_id: str,
    publisher: str,
    license: str,  # noqa: A002 - the field name that spec 0001 gives
    table_id: str,
    table_title: str,
    table_url: str,
    table_updated: AwareDatetime,
) -> ProvenanceRecord:
    """Return the provenance record of a result that came from ``exchange``."""
    return ProvenanceRecord(
        source_id=source_id,
        publisher=publisher,
        license=license,
        table_id=table_id,
        table_title=table_title,
        table_url=table_url,
        table_updated=table_updated,
        request=RequestRecord(
            method=exchange.method,
            url=exchange.url,
            body=exchange.body.decode("utf-8") if exchange.body is not None else None,
        ),
        retrieved_at=exchange.retrieved_at,
        response_sha256=sha256_hex(exchange.content),
    )
