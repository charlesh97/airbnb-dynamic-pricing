"""Block ledger — records which blocked dates the ENGINE created.

iGMS has no per-block reason/note field: every availability block created via
`set_calendar_batch` reads back as `unavailable_reason: "Blocked manually"`,
identical to a block Charles creates in the iGMS UI. We therefore keep a local
ledger of every date *this engine* blocked, so we can:

  - auto-unblock a date when the rule that created the block no longer applies
    (e.g. a booking cancelled after we blocked its checkout day), and
  - NEVER touch a date Charles blocked manually (not in the ledger).

The ledger is a JSON file at `.state/engine_blocks.json` (repo-relative,
git-ignored via the existing `.state/` entry).

Schema:
    {
      "<property_uid>": {
        "<YYYY-MM-DD>": {"reason": "<rule name>", "created_at": "<ISO ts>"}
      }
    }
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_LEDGER_PATH = (
    Path(__file__).resolve().parent.parent.parent / ".state" / "engine_blocks.json"
)


def _default_path() -> Path:
    return _DEFAULT_LEDGER_PATH


def load_ledger(path: str | Path | None = None) -> dict[str, Any]:
    """Load the ledger dict. Missing/corrupt file → empty ledger."""
    ledger_path = Path(path) if path else _default_path()
    if not ledger_path.exists():
        return {}
    try:
        data = json.loads(ledger_path.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Block ledger unreadable (%s): %s — starting empty", ledger_path, exc)
    return {}


def save_ledger(ledger: dict[str, Any], path: str | Path | None = None) -> None:
    """Persist the ledger atomically (write tmp, then rename)."""
    ledger_path = Path(path) if path else _default_path()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True))
    tmp.replace(ledger_path)


def record_blocks(
    ledger: dict[str, Any],
    property_uid: str,
    dates: list[str],
    reason: str = "engine_block",
) -> None:
    """Mark dates as engine-owned blocks. Mutates `ledger` in place."""
    prop = ledger.setdefault(property_uid, {})
    ts = datetime.now().isoformat(timespec="seconds")
    for date_str in dates:
        prop[date_str] = {"reason": reason, "created_at": ts}


def remove_blocks(
    ledger: dict[str, Any],
    property_uid: str,
    dates: list[str],
) -> None:
    """Drop dates from the ledger. Mutates `ledger` in place."""
    prop = ledger.get(property_uid)
    if not prop:
        return
    for date_str in dates:
        prop.pop(date_str, None)
    if not prop:
        ledger.pop(property_uid, None)


def is_engine_owned(ledger: dict[str, Any], property_uid: str, date_str: str) -> bool:
    """True if this date was blocked by the engine (not a manual UI block)."""
    return date_str in (ledger.get(property_uid) or {})


def engine_owned_dates(ledger: dict[str, Any], property_uid: str) -> list[str]:
    """Sorted list of dates the engine has blocked for this property."""
    return sorted((ledger.get(property_uid) or {}).keys())


def seed_from_bookings(
    ledger: dict[str, Any],
    property_uid: str,
    bookings: list[dict[str, Any]],
    block_day_after: bool,
) -> int:
    """One-time transition aid: claim checkout-date blocks of accepted bookings.

    Before the ledger existed, the engine blocked checkout days of bookings via
    the pipeline. Those blocks are indistinguishable from manual blocks in iGMS,
    but we *know* they are ours because block_day_after is enabled and the
    booking record still exists. This seeds them into the ledger so a later
    cancellation can be auto-unblocked. Returns the number of dates seeded.
    """
    if not block_day_after:
        return 0
    seeded = 0
    dates: list[str] = []
    for b in bookings:
        if b.get("booking_status") not in ("accepted", "confirmed"):
            continue
        checkout_raw = b.get("checkout") or str(b.get("local_checkout_dttm", ""))[:10]
        if not checkout_raw or len(checkout_raw) < 10:
            continue
        dates.append(checkout_raw[:10])
    if dates:
        record_blocks(ledger, property_uid, dates, reason="day_after_checkout_blocked")
        seeded = len(dates)
    return seeded
