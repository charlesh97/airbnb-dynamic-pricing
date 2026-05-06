"""CLI entry point for igms-dynamic-pricing."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

from .config import EngineConfig
from .config_store import PropertyConfigStore
from .engine import DatePrice, PricingEngine, apply_manual_overrides
from .client import PricingClient
from .wheelhouse_fetcher import WheelhouseFetcher

console = Console()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_calendar_by_listing(calendar_data: list[dict]) -> dict[str, list[dict]]:
    """Group calendar entries by listing_uid."""
    by_listing: dict[str, list[dict]] = {}
    for entry in calendar_data:
        lst = entry.get("listing_uid", "")
        if lst:
            by_listing.setdefault(lst, []).append(entry)
    return by_listing


# ─── existing commands ──────────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> None:
    """Show current iGMS prices vs recommended prices."""
    _setup_logging(args.log_level)
    config = EngineConfig.from_env(args.env)
    client = PricingClient.from_env()
    engine = PricingEngine()

    window = config.pricing_window_days
    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=window)).strftime("%Y-%m-%d")

    properties = client.get_all_properties()
    if not properties:
        console.print("[red]No properties found — check IGMS_ACCESS_TOKEN[/red]")
        return

    for prop in properties:
        uid = prop.get("property_uid")
        if not uid:
            continue
        name = prop.get("name", uid)
        console.print(f"\n[bold cyan]{name}[/bold cyan] ({uid})")

        try:
            calendar = client.get_calendar(uid, from_date, to_date)
        except Exception as e:
            console.print(f"  [red]Calendar error: {e}[/red]")
            continue

        engine_local = PricingEngine()

        table = Table(show_header=True)
        table.add_column("Date")
        table.add_column("Listing")
        table.add_column("Current")
        table.add_column("Suggested")
        table.add_column("Delta")
        table.add_column("Confidence")

        for entry in calendar.get("data", []):
            date = entry.get("date", "")
            listing_uid = entry.get("listing_uid", "")
            listing_name = entry.get("listing_name", listing_uid)
            current = entry.get("price", 0)

            rec = engine_local.compute_price(
                property_uid=uid,
                date=date,
                calendar_entry=entry,
                bookings_in_window=[],
                config=config.__dict__,
            )
            delta = rec.final_price - current
            sign = "+" if delta >= 0 else ""
            delta_str = f"{sign}{delta:.2f}"

            color = "green" if abs(delta) < 5 else "yellow"
            table.add_row(
                date,
                listing_name[:30],
                f"${current:.2f}",
                f"${rec.final_price:.2f}",
                f"[{color}]{delta_str}[/{color}]",
                f"{rec.confidence:.0%}",
            )

        console.print(table)


def cmd_run(args: argparse.Namespace) -> None:
    """Compute prices without pushing to iGMS."""
    _setup_logging(args.log_level)
    config = EngineConfig.from_env(args.env)
    engine = PricingEngine()

    client = PricingClient.from_env()
    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=config.pricing_window_days)).strftime("%Y-%m-%d")

    properties = client.get_all_properties()
    results: dict[str, list[DatePrice]] = {}

    for prop in properties:
        uid = prop.get("property_uid")
        if not uid:
            continue
        try:
            calendar = client.get_calendar(uid, from_date, to_date)
            prices = engine.compute_range(
                property_uid=uid,
                from_date=from_date,
                to_date=to_date,
                calendar_data=calendar.get("data", []),
                bookings_in_window=[],
                config=config.__dict__,
            )
            results[prop.get("name", uid)] = prices
        except Exception as e:
            console.print(f"[red]Error for {uid}: {e}[/red]")

    console.print(json.dumps(results, indent=2, default=str))


def cmd_dry_run(args: argparse.Namespace) -> None:
    """Alias for cmd_run."""
    cmd_run(args)


def cmd_push(args: argparse.Namespace) -> None:
    """Push computed prices to iGMS (requires pricing-management scope)."""
    _setup_logging(args.log_level)
    config = EngineConfig.from_env(args.env)
    engine = PricingEngine()
    client = PricingClient.from_env()

    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=config.pricing_window_days)).strftime("%Y-%m-%d")

    properties = client.get_all_properties()

    for prop in properties:
        uid = prop.get("property_uid")
        if not uid:
            continue
        console.print(f"\n[bold]Pushing prices for {prop.get('name','?')}...[/bold]")
        try:
            calendar = client.get_calendar(uid, from_date, to_date)
            calendar_entries = calendar.get("data", [])

            by_listing = _build_calendar_by_listing(calendar_entries)

            if not by_listing:
                console.print(f"  [yellow]No calendar entries for {uid}[/yellow]")
                continue

            for listing_uid, entries in by_listing.items():
                listing_name = entries[0].get("listing_name", listing_uid) if entries else listing_uid
                console.print(f"\n  Listing: {listing_name} ({listing_uid})")

                if args.dry_run:
                    console.print(f"    [yellow]DRY RUN — would update {len(entries)} dates[/yellow]")
                    for entry in entries:
                        rec = engine.compute_price(
                            property_uid=uid,
                            date=entry.get("date", ""),
                            calendar_entry=entry,
                            bookings_in_window=[],
                            config=config.__dict__,
                        )
                        console.print(
                            f"    {entry.get('date')}: current ${entry.get('price', 0)} "
                            f"→ recommended ${rec.final_price}"
                        )
                    continue

                for entry in entries:
                    date = entry.get("date", "")
                    current_price = entry.get("price", 0)

                    rec = engine.compute_price(
                        property_uid=uid,
                        date=date,
                        calendar_entry=entry,
                        bookings_in_window=[],
                        config=config.__dict__,
                    )

                    result = client.update_calendar_price(
                        listing_uid=listing_uid,
                        property_uid=uid,
                        date=date,
                        price=rec.final_price,
                        currency="USD",
                        min_stay=entry.get("min_stay"),
                    )

                    status = getattr(result, 'status_code', 0)
                    payload = getattr(result, 'payload', result)

                    if isinstance(payload, dict) and payload.get("error"):
                        err = payload["error"]
                        console.print(
                            f"    [red]✗ {date}: {err.get('message','?')} "
                            f"(code {err.get('code','?')})[/red]"
                        )
                    elif status in (200, 201, 204):
                        console.print(
                            f"    [green]✓ {date}: ${current_price} → ${rec.final_price}[/green]"
                        )
                    else:
                        console.print(
                            f"    [red]✗ {date}: HTTP {status} — {payload}[/red]"
                        )

        except Exception as e:
            console.print(f"[red]Error for {uid}: {e}[/red]")


# ─── Loop 8: new commands ─────────────────────────────────────────────────────

def cmd_run_config(args: argparse.Namespace) -> None:
    """Load property JSON, run engine, print results."""
    _setup_logging(args.log_level)
    config = EngineConfig.from_env(args.env)
    store = PropertyConfigStore()
    prop_config = store.load(args.property)
    if not prop_config:
        console.print(f"[red]No config found for property {args.property}[/red]")
        return

    merged = store.merge_with_env_defaults(args.property, config.__dict__)
    engine = PricingEngine()

    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=args.days or config.pricing_window_days)).strftime("%Y-%m-%d")

    client = PricingClient.from_env()
    try:
        calendar = client.get_calendar(args.property, from_date, to_date)
    except Exception as e:
        console.print(f"[red]Calendar fetch failed: {e}[/red]")
        calendar = {"data": []}

    prices = engine.compute_range(
        property_uid=args.property,
        from_date=from_date,
        to_date=to_date,
        calendar_data=calendar.get("data", []),
        bookings_in_window=[],
        config=merged,
    )

    table = Table(show_header=True)
    table.add_column("Date")
    table.add_column("Price")
    table.add_column("Confidence")
    table.add_column("Avail")
    table.add_column("Min Stay")
    table.add_column("Blocked")

    for dp in prices:
        avail_sym = "[green]✓[/green]" if dp.is_available else "[red]✗[/red]"
        blocked = dp.blocked_reason or ""
        table.add_row(
            dp.date,
            f"${dp.final_price:.2f}",
            f"{dp.confidence:.0%}",
            avail_sym,
            str(dp.min_stay),
            blocked[:30],
        )

    console.print(f"\n[bold cyan]{args.property}[/bold cyan] ({from_date} → {to_date})")
    console.print(table)


def cmd_availability(args: argparse.Namespace) -> None:
    """Check availability rules for next N days (default 90)."""
    _setup_logging(args.log_level)
    config = EngineConfig.from_env(args.env)
    store = PropertyConfigStore()
    prop_config = store.load(args.property)
    if not prop_config:
        console.print(f"[red]No config found for property {args.property}[/red]")
        return

    merged = store.merge_with_env_defaults(args.property, config.__dict__)
    engine = PricingEngine()

    from_date = datetime.now().strftime("%Y-%m-%d")
    days = args.days or 90
    to_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    client = PricingClient.from_env()
    try:
        calendar = client.get_calendar(args.property, from_date, to_date)
    except Exception:
        calendar = {"data": []}

    table = Table(show_header=True)
    table.add_column("Date")
    table.add_column("Available")
    table.add_column("Min Stay")
    table.add_column("Blocked Reason")

    blocked_dates = []
    for entry in calendar.get("data", []):
        date = entry.get("date", "")
        avail = engine.compute_availability(
            property_uid=args.property,
            date=date,
            calendar_entry=entry,
            bookings_in_window=[],
            config=merged,
        )
        avail_sym = "[green]✓[/green]" if avail.is_available else "[red]✗[/red]"
        if not avail.is_available:
            blocked_dates.append((date, avail.blocked_reason or "unknown"))
        table.add_row(date, avail_sym, str(avail.min_stay), avail.blocked_reason or "")

    console.print(f"\n[bold cyan]{args.property}[/bold cyan] availability ({from_date} → {to_date})")
    console.print(table)

    if blocked_dates:
        console.print(f"\n[yellow]Blocked dates ({len(blocked_dates)}):[/yellow]")
        for d, reason in blocked_dates[:20]:
            console.print(f"  {d}: {reason}")
        if len(blocked_dates) > 20:
            console.print(f"  ... and {len(blocked_dates) - 20} more")


def cmd_wheelhouse_check(args: argparse.Namespace) -> None:
    """Call Wheelhouse coverage check + fetch recommendations for a property."""
    _setup_logging(args.log_level)
    store = PropertyConfigStore()
    prop_config = store.load(args.property)
    if not prop_config:
        console.print(f"[red]No config found for property {args.property}[/red]")
        return

    lat = prop_config.get("latitude")
    lng = prop_config.get("longitude")
    if lat is None or lng is None:
        console.print("[red]Property config missing latitude/longitude[/red]")
        return

    wh = WheelhouseFetcher()

    # Coverage check
    try:
        coverage = wh.check_coverage(
            latitude=lat,
            longitude=lng,
            country=prop_config.get("country", "US"),
            postal_code=prop_config.get("postal_code", ""),
        )
        console.print(f"\n[bold cyan]Wheelhouse coverage for {args.property}[/bold cyan]")
        console.print(f"  In market : {coverage.get('in_market', '?')}")
        console.print(f"  Market   : {coverage.get('market_name', '?')}")
    except Exception as e:
        console.print(f"[red]Coverage check failed: {e}[/red]")
        return

    if not coverage.get("in_market", False):
        console.print("[yellow]Property not in Wheelhouse market — skipping recommendations[/yellow]")
        return

    # Recommendations
    try:
        recs = wh.fetch_recommendations(
            latitude=lat,
            longitude=lng,
            bedrooms=prop_config.get("bedrooms", 2),
            baths=prop_config.get("baths", 1.0),
            sleeps=prop_config.get("sleeps", 4),
            room_type=prop_config.get("room_type", "house"),
            country_code=prop_config.get("country", "US"),
            cleaning_fee=prop_config.get("cleaning_fee"),
            amenities=prop_config.get("amenities"),
        )
    except Exception as e:
        console.print(f"[red]Recommendations fetch failed: {e}[/red]")
        return

    if not recs:
        console.print("[yellow]No recommendations returned[/yellow]")
        return

    from_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=args.days or 90)).strftime("%Y-%m-%d")

    filtered = [
        r for r in recs
        if from_date <= (r.get("date") or r.get("date_iso", ""))[:10] <= to_date
    ]

    table = Table(show_header=True)
    table.add_column("Date")
    table.add_column("Total Price")
    table.add_column("Adr")
    table.add_column("Occupancy %")

    for r in filtered[:60]:
        date_str = (r.get("date") or r.get("date_iso", ""))[:10]
        total = r.get("total_price", 0)
        adr = r.get("adr", 0)
        occ = r.get("occupancy", 0)
        table.add_row(date_str, f"${total:.2f}", f"${adr:.2f}", f"{occ:.0%}" if occ else "—")

    console.print(f"\nRecommendations for {args.property} ({from_date} → {to_date})")
    console.print(table)


def cmd_push_config(args: argparse.Namespace) -> None:
    """Load property JSON, compute prices + availability, push to iGMS."""
    _setup_logging(args.log_level)
    config = EngineConfig.from_env(args.env)
    store = PropertyConfigStore()
    prop_config = store.load(args.property)
    if not prop_config:
        console.print(f"[red]No config found for property {args.property}[/red]")
        return

    merged = store.merge_with_env_defaults(args.property, config.__dict__)
    engine = PricingEngine()
    client = PricingClient.from_env()

    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=config.pricing_window_days)).strftime("%Y-%m-%d")

    try:
        calendar = client.get_calendar(args.property, from_date, to_date)
    except Exception as e:
        console.print(f"[red]Calendar fetch failed: {e}[/red]")
        return

    calendar_entries = calendar.get("data", [])
    by_listing = _build_calendar_by_listing(calendar_entries)

    if not by_listing:
        console.print(f"[yellow]No calendar entries for {args.property}[/yellow]")
        return

    for listing_uid, entries in by_listing.items():
        console.print(f"\n[bold]Listing {listing_uid} — pushing prices[/bold]")

        for entry in entries:
            date = entry.get("date", "")

            # Compute availability
            avail = engine.compute_availability(
                property_uid=args.property,
                date=date,
                calendar_entry=entry,
                bookings_in_window=[],
                config=merged,
            )

            # Skip unavailable dates unless override
            if not avail.is_available and not args.force:
                console.print(f"  [yellow]~ {date}: unavailable ({avail.blocked_reason})[/yellow]")
                continue

            # Compute price
            rec = engine.compute_price(
                property_uid=args.property,
                date=date,
                calendar_entry=entry,
                bookings_in_window=[],
                config=merged,
            )

            # Apply manual overrides
            rec = apply_manual_overrides(rec, args.property, date, merged)

            if args.dry_run:
                console.print(
                    f"  [yellow]DRY RUN[/yellow] {date}: "
                    f"${entry.get('price', 0):.2f} → ${rec.final_price:.2f} "
                    f"(min_stay={avail.min_stay})"
                )
                continue

            try:
                result = client.update_calendar_price(
                    listing_uid=listing_uid,
                    property_uid=args.property,
                    date=date,
                    price=rec.final_price,
                    currency="USD",
                    min_stay=avail.min_stay if avail.is_available else None,
                )
                status = getattr(result, 'status_code', 0)
                if status in (200, 201, 204):
                    console.print(
                        f"  [green]✓[/green] {date}: ${rec.final_price:.2f} "
                        f"(min_stay={avail.min_stay})"
                    )
                else:
                    payload = getattr(result, 'payload', result)
                    console.print(f"  [red]✗ {date}: HTTP {status} — {payload}[/red]")
            except Exception as e:
                console.print(f"  [red]✗ {date}: {e}[/red]")


def main() -> None:
    parser = argparse.ArgumentParser(description="iGMS Dynamic Pricing Engine")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--log-level", default="INFO")

    sub = parser.add_subparsers(required=True)

    # Existing
    sub.add_parser("status", help="Show current vs recommended prices").set_defaults(
        func=cmd_status
    )
    sub.add_parser("run", help="Compute prices (no push)").set_defaults(func=cmd_run)
    sub.add_parser("dry-run", help="Compute prices (no push — alias)").set_defaults(
        func=cmd_dry_run
    )
    push = sub.add_parser("push", help="Push prices to iGMS")
    push.add_argument("--dry-run", action="store_true")
    push.set_defaults(func=cmd_push)

    # Loop 8: new commands
    run_cfg = sub.add_parser("run-config", help="Load property JSON, run engine, print results")
    run_cfg.add_argument("--property", required=True, help="Property UID")
    run_cfg.add_argument("--days", type=int, help="Number of days (default from config)")
    run_cfg.set_defaults(func=cmd_run_config)

    avail = sub.add_parser("availability", help="Check availability rules for next N days")
    avail.add_argument("--property", required=True, help="Property UID")
    avail.add_argument("--days", type=int, default=90, help="Number of days (default 90)")
    avail.set_defaults(func=cmd_availability)

    wh = sub.add_parser("wheelhouse-check", help="Call Wheelhouse coverage + recommendations")
    wh.add_argument("--property", required=True, help="Property UID")
    wh.add_argument("--days", type=int, help="Days for recommendations (default 90)")
    wh.set_defaults(func=cmd_wheelhouse_check)

    push_cfg = sub.add_parser("push-config", help="Load property JSON, compute, push to iGMS")
    push_cfg.add_argument("--property", required=True, help="Property UID")
    push_cfg.add_argument("--dry-run", action="store_true")
    push_cfg.add_argument("--force", action="store_true", help="Push even for blocked dates")
    push_cfg.set_defaults(func=cmd_push_config)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()