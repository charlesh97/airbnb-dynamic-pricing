#!/usr/bin/env python3
"""Debug booking velocity input using a broad booking-window fetch.

This script fetches bookings for a property across its configured booking
window, then computes recent/baseline booking counts based on created_dttm.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pricing_engine.booking_adapter import fetch_bookings_for_window
from pricing_engine.client import PricingClient
from pricing_engine.config import EngineConfig
from pricing_engine.strategies.demand import get_pricing_adjustments_config


@dataclass
class VelocityWindows:
    recent_days: int
    baseline_days: int
    min_recent_bookings: int
    min_baseline_bookings: int


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    if v.endswith("Z"):
        v = v[:-1]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _load_property_config(property_uid: str) -> dict[str, Any]:
    cfg_path = REPO_ROOT / "config" / "properties" / f"{property_uid}.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Missing config: {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _velocity_windows_from_config(config: dict[str, Any]) -> VelocityWindows:
    adj = get_pricing_adjustments_config(config)
    vel = adj["booking_velocity"]
    return VelocityWindows(
        recent_days=max(1, int(vel["recent_window_days"])),
        baseline_days=max(1, int(vel["baseline_window_days"])),
        min_recent_bookings=max(0, int(vel["min_recent_bookings"])),
        min_baseline_bookings=max(0, int(vel["min_baseline_bookings"])),
    )


def _booking_window_days(config: dict[str, Any]) -> int:
    availability = config.get("availability", {}) or {}
    return max(1, int(availability.get("booking_window_days", 120)))


def _build_client() -> PricingClient:
    env_cfg = EngineConfig.from_env(REPO_ROOT / ".env")
    client = PricingClient()
    if hasattr(client, "set_access_token"):
        client.set_access_token(env_cfg.igms_access_token)
    else:
        client.access_token = env_cfg.igms_access_token
    return client


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "n/a"


def _extract_data_list(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        data = response.get("data", response.get("bookings", []))
        return data if isinstance(data, list) else []
    if isinstance(response, list):
        return [r for r in response if isinstance(r, dict)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug velocity booking fetch path.")
    parser.add_argument("--property", required=True, help="Property UID")
    parser.add_argument(
        "--anchor-date",
        default=None,
        help="Anchor date for recent/baseline windows (YYYY-MM-DD). Uses 23:59:59 local time.",
    )
    parser.add_argument(
        "--anchor-datetime",
        default=None,
        help="Anchor datetime for windows (YYYY-MM-DDTHH:MM:SS). Overrides --anchor-date.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=25,
        help="Max bookings to print in each sample section.",
    )
    args = parser.parse_args()

    property_uid = str(args.property).strip()
    config = _load_property_config(property_uid)
    velocity = _velocity_windows_from_config(config)
    booking_window_days = _booking_window_days(config)

    anchor = datetime.now()
    if args.anchor_datetime:
        anchor = datetime.strptime(args.anchor_datetime, "%Y-%m-%dT%H:%M:%S")
    elif args.anchor_date:
        d = datetime.strptime(args.anchor_date, "%Y-%m-%d")
        anchor = d.replace(hour=23, minute=59, second=59, microsecond=0)

    fetch_from_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    fetch_to_dt = fetch_from_dt + timedelta(days=booking_window_days)
    fetch_from = fetch_from_dt.strftime("%Y-%m-%d")
    fetch_to = fetch_to_dt.strftime("%Y-%m-%d")

    recent_start = anchor - timedelta(days=velocity.recent_days)
    baseline_start = anchor - timedelta(days=velocity.baseline_days)

    client = _build_client()
    raw_response = client.get_bookings(
        page=1,
        property_uid=property_uid,
        from_date=fetch_from,
        to_date=fetch_to,
    )
    raw_rows = _extract_data_list(raw_response)

    raw_status_counts: dict[str, int] = {}
    raw_property_counts: dict[str, int] = {}
    raw_target_property_rows: list[dict[str, Any]] = []
    raw_missing_checkin = 0
    raw_missing_checkout = 0
    for r in raw_rows:
        status = str(r.get("booking_status", "") or "").lower() or "(empty)"
        raw_status_counts[status] = raw_status_counts.get(status, 0) + 1

        p = str(r.get("property_uid", "") or "(empty)")
        raw_property_counts[p] = raw_property_counts.get(p, 0) + 1
        if p == property_uid:
            raw_target_property_rows.append(r)

        if not r.get("checkin") and not r.get("local_checkin_dttm"):
            raw_missing_checkin += 1
        if not r.get("checkout") and not r.get("local_checkout_dttm"):
            raw_missing_checkout += 1

    bookings = fetch_bookings_for_window(client, property_uid, fetch_from, fetch_to)

    recent: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    created_today: list[dict[str, Any]] = []
    today_key = datetime.now().strftime("%Y-%m-%d")

    for b in bookings:
        created = _parse_dt(b.get("created_dttm"))
        if not created:
            continue
        b["_created_dt"] = created
        if baseline_start <= created < anchor:
            baseline.append(b)
        if recent_start <= created < anchor:
            recent.append(b)
        if created.strftime("%Y-%m-%d") == today_key:
            created_today.append(b)

    def sort_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=lambda x: x.get("_created_dt", datetime.min), reverse=True)

    recent = sort_desc(recent)
    baseline = sort_desc(baseline)
    created_today = sort_desc(created_today)

    print("")
    print("=== Velocity Fetch Debug ===")
    print(f"property_uid:            {property_uid}")
    print(f"booking_window_days:     {booking_window_days}")
    print(f"fetch window (stay):     {fetch_from} -> {fetch_to}")
    print(f"anchor date:             {anchor.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"recent window days:      {velocity.recent_days}")
    print(f"baseline window days:    {velocity.baseline_days}")
    print(f"recent start:            {_fmt_dt(recent_start)}")
    print(f"baseline start:          {_fmt_dt(baseline_start)}")
    print("")
    print(f"fetched bookings total:  {len(bookings)}")
    print(f"baseline bookings:       {len(baseline)} (min required {velocity.min_baseline_bookings})")
    print(f"recent bookings:         {len(recent)} (min required {velocity.min_recent_bookings})")
    print(f"created today:           {len(created_today)}")
    print("")
    print("Raw API diagnostics (pre-adapter filtering):")
    print(f"raw rows (page 1):       {len(raw_rows)}")
    print(f"raw status counts:       {raw_status_counts}")
    print(f"raw property counts:     {raw_property_counts}")
    print(f"missing checkin fields:  {raw_missing_checkin}")
    print(f"missing checkout fields: {raw_missing_checkout}")

    print("")
    print(
        f"Raw rows for target property (page 1, latest {min(args.sample_limit, len(raw_target_property_rows))}):"
    )
    if not raw_target_property_rows:
        print("  (none)")
    else:
        rows = sorted(
            raw_target_property_rows,
            key=lambda r: _parse_dt(str(r.get('created_dttm', '') or "")) or datetime.min,
            reverse=True,
        )
        for r in rows[: args.sample_limit]:
            print(
                "  "
                f"status={str(r.get('booking_status') or '').lower() or '-'}  "
                f"created={str(r.get('created_dttm') or '-'):<19}  "
                f"checkin={str(r.get('checkin') or r.get('local_checkin_dttm') or '-'):<10}  "
                f"checkout={str(r.get('checkout') or r.get('local_checkout_dttm') or '-'):<10}  "
                f"reservation={str(r.get('reservation_code') or '-')}"
            )

    def print_rows(title: str, rows: list[dict[str, Any]], limit: int) -> None:
        print("")
        print(title)
        if not rows:
            print("  (none)")
            return
        for b in rows[:limit]:
            created = _fmt_dt(b.get("_created_dt"))
            checkin = b.get("checkin", "")
            checkout = b.get("checkout", "")
            code = b.get("reservation_code", "")
            bid = b.get("booking_id", "")
            print(
                f"  created={created}  stay={checkin}->{checkout}  "
                f"reservation={code or '-'}  booking_id={bid or '-'}"
            )

    print_rows(
        f"Recent bookings sample (latest {min(args.sample_limit, len(recent))})",
        recent,
        args.sample_limit,
    )
    print_rows(
        f"Created-today sample (latest {min(args.sample_limit, len(created_today))})",
        created_today,
        args.sample_limit,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
