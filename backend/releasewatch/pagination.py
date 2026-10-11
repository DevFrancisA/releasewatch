"""Keyset (cursor) pagination.

Lists are ordered by (timestamp DESC, id DESC). A cursor encodes the sort key
of the last row on a page, and the next page is `WHERE (ts, id) < (:ts, :id)`,
which Postgres answers with a single index range scan no matter how deep the
page is. OFFSET pagination, by contrast, reads and discards every skipped row.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, Select, tuple_


def encode_cursor(ts: datetime, row_id: int) -> str:
    raw = json.dumps([ts.isoformat(), row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts, row_id = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(ts), int(row_id)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


def paginate(
    query: Select[Any],
    ts_column: ColumnElement[datetime],
    id_column: ColumnElement[int],
    cursor: str | None,
    limit: int,
) -> Select[Any]:
    """Order newest-first and apply the cursor; fetches limit + 1 rows to detect a next page."""
    if cursor:
        ts, row_id = decode_cursor(cursor)
        query = query.where(tuple_(ts_column, id_column) < tuple_(ts, row_id))
    return query.order_by(ts_column.desc(), id_column.desc()).limit(limit + 1)


def split_page(rows: list[Any], limit: int, key: Any) -> tuple[list[Any], str | None]:
    """Trim the probe row and build the cursor for the next page, if any."""
    if len(rows) <= limit:
        return rows, None
    rows = rows[:limit]
    ts, row_id = key(rows[-1])
    return rows, encode_cursor(ts, row_id)
