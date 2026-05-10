"""CLI entry point for igms-dynamic-pricing."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timedelta
from typing import TextIO

from rich.console import Console
from rich.table import Table

from .config import EngineConfig
from .config_store import PropertyConfigStore
from .engine import DatePrice, PricingEngine, apply_manual_overrides
from .client import PricingClient
from .wheelhouse_fetcher import WheelhouseFetcher
from .booking_adapter import fetch_bookings_for_window

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


def _fetch_bookings_cached(client, property_uid, from_date, to_date):
    """Fetch and cache bookings for the pricing window.

    Caches in an _bookings_cache dict keyed by property_uid to avoid
    repeated API calls when the same property is visited across commands.
    """
    cache_key = f"{property_uid}:{from_date}:{to_date}"
    if not hasattr(client, "_bookings_cache"):
        client._bookings_cache: dict[str, list[dict]] = {}
    if cache_key not in client._bookings_cache:
        client._bookings_cache[cache_key] = fetch_bookings_for_window(
            client, property_uid, from_date, to_date
        )
    return client._bookings_cache[cache_key]


def _write_prices_csv(
    prices: list[DatePrice],
    from_date: str,
    output_path: str | None = None,
) -> str:
    """Write pricing results to a CSV file.

    Returns the path to the written file.
    Columns: date, day_of_week, base_rate, demand_multiplier, event_factor,
             last_minute_factor, weather_factor, adjusted_rate
    """
    if output_path:
        file_path = output_path
    else:
        safe_date = from_date.replace("-", "")
        file_path = f"frosty-pines-pricing-{safe_date}.csv"

    rows = []
    for dp in prices:
        date_str = dp.date
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dow = dt.strftime("%a")

        demand_factors = dp.all_factors.get("demand", {})
        event_factors = dp.all_factors.get("event", {})
        weather_factors = dp.all_factors.get("weather", {})

        base_rate = demand_factors.get("base_price", 0.0)
        demand_multiplier = demand_factors.get("demand_multiplier", 1.0)
        last_minute_factor = demand_factors.get("last_minute_factor", 1.0)
        event_factor = event_factors.get("seasonal_multiplier", 1.0)
        weather_factor = weather_factors.get("weather_factor", 1.0)

        rows.append({
            "date": date_str,
            "day_of_week": dow,
            "base_rate": round(base_rate, 2),
            "demand_multiplier": round(demand_multiplier, 3),
            "event_factor": round(event_factor, 3),
            "last_minute_factor": round(last_minute_factor, 3),
            "weather_factor": round(weather_factor, 3),
            "adjusted_rate": dp.final_price,
        })

    with open(file_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date", "day_of_week", "base_rate", "demand_multiplier",
                "event_factor", "last_minute_factor", "weather_factor", "adjusted_rate",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    return file_path


# ─── existing commands ──────────────────────────────────────────────────────────

# ─── debug-day command ──────────────────────────────────────────────────────────

def _factor_contribution(base: float, multiplier: float) -> float:
    """How much the multiplier changes the price from base."""
    return base * multiplier


def cmd_debug_day(args: argparse.Namespace) -> None:
    """Print a detailed factor breakdown for a single day across all properties.
    
    Shows price as a chain of stacked multipliers:
      starting_price → occupancy_pacing → booking_velocity → final clamp
    """
    from dashboard.engine_proxy import get_calendar_with_live_prices, get_day_detail

    _setup_logging(args.log_level)
    store = PropertyConfigStore()

    property_uids = args.property or [
        "731418607849470882",   # Frosty Pines (CA)
        "645841896772032198",   # Freedom Place (VA)
    ]

    def _fmt_money(value: float | None) -> str:
        return "n/a" if value is None else f"${value:.2f}"

    def _fmt_delta(delta: float | None) -> str:
        if delta is None:
            return "n/a"
        if delta >= 0:
            return f"+${delta:.2f}"
        return f"-${abs(delta):.2f}"

    for uid in property_uids:
        prop = store.load(uid)
        if not prop:
            console.print(f"[red]No config for {uid}[/red]")
            continue

        name = prop.get("name", uid)
        state = prop.get("state", "??")
        target_dt = datetime.strptime(args.date, "%Y-%m-%d")
        month_key = target_dt.strftime("%m")
        dow_key = target_dt.strftime("%a").lower()

        # Pull live iGMS price for this exact date so debug output can show deltas.
        live_prices = get_calendar_with_live_prices(uid, args.date, args.date)
        current_price = live_prices.get(args.date)

        console.print(f"\n{'='*56}")
        console.print(f"[bold cyan]{name}[/bold cyan]  |  {uid}  |  state={state}")
        console.print(f"[bold]Date: {args.date}[/bold]")
        console.print(f"{'='*56}")

        detail = get_day_detail(uid, args.date, None, None, current_price)
        final_price = float(detail["final_price"])
        confidence = float(detail["confidence"])
        current = detail.get("current_airbnb_price")
        if isinstance(current, (int, float)):
            current = float(current)
        else:
            current = None

        raw_factors = detail.get("raw_factors", {})
        explanation = raw_factors.get("explanation", {}) if isinstance(raw_factors, dict) else {}
        demand_factors = raw_factors.get("demand", {}) if isinstance(raw_factors, dict) else {}
        occ = demand_factors.get("occupancy_pacing", {}) if isinstance(demand_factors, dict) else {}
        vel = demand_factors.get("booking_velocity", {}) if isinstance(demand_factors, dict) else {}
        occ_inputs = occ.get("inputs", {}) if isinstance(occ, dict) else {}
        occ_computed = occ.get("computed", {}) if isinstance(occ, dict) else {}
        vel_inputs = vel.get("inputs", {}) if isinstance(vel, dict) else {}
        vel_computed = vel.get("computed", {}) if isinstance(vel, dict) else {}

        def _fmt_pct_signed(v: float | None) -> str:
            if v is None:
                return "n/a"
            sign = "+" if v >= 0 else "-"
            return f"{sign}{abs(v) * 100:.1f}%"

        def _fmt_num(v: Any, ndigits: int = 3) -> str:
            try:
                return f"{float(v):.{ndigits}f}"
            except Exception:
                return "n/a"

        # ── CONFIG SNAPSHOT ──────────────────────────────────────────────────
        seasonal_cfg = prop.get("seasonal_months", {})
        dow_cfg = prop.get("dow_multipliers", {})
        console.print("\n[bold white on blue]CONFIG[/bold white on blue]")
        console.print(
            "base="
            f"{_fmt_money(prop.get('base_price'))}  "
            "min/max="
            f"{_fmt_money(prop.get('min_price'))}/{_fmt_money(prop.get('max_price'))}  "
            f"seasonal({month_key})={seasonal_cfg.get(month_key, 'default')}  "
            f"dow({dow_key})={dow_cfg.get(dow_key, 1.0)}"
        )

        # ── CURRENT VS RECOMMENDED ───────────────────────────────────────────
        console.print("\n[bold white on green]PRICE COMPARISON[/bold white on green]")
        if current is not None:
            console.print(
                f"current iGMS={_fmt_money(current)}  "
                f"recommended={_fmt_money(final_price)}  "
                f"delta={_fmt_delta(final_price - current)}  "
                f"confidence={confidence:.0%}"
            )
        else:
            console.print(
                f"current iGMS=n/a  recommended={_fmt_money(final_price)}  "
                f"confidence={confidence:.0%}"
            )

        console.print("\n[bold]STARTING PRICE[/bold]")
        console.print(
            f"  base_price={_fmt_money(explanation.get('base_price'))}  "
            f"event_multiplier={_fmt_num(explanation.get('event_multiplier'))}  "
            f"yield_multiplier={_fmt_num(explanation.get('yield_multiplier'))}  "
            f"starting_price={_fmt_money(explanation.get('starting_price'))}"
        )

        console.print("\n[bold]OCCUPANCY PACING CALCULATION[/bold]")
        console.print(
            f"  enabled={occ_inputs.get('enabled', 'n/a')}  "
            f"window_days={occ_inputs.get('window_days', 'n/a')}  "
            f"booked_nights={occ_inputs.get('booked_nights', 'n/a')}  "
            f"available_nights={occ_inputs.get('available_nights', 'n/a')}"
        )
        console.print(
            f"  actual_occupancy={_fmt_pct_signed(occ_computed.get('actual_occupancy'))}  "
            f"target_occupancy={_fmt_pct_signed(occ_inputs.get('target_occupancy'))}  "
            f"delta={_fmt_pct_signed(occ_computed.get('delta'))}"
        )
        console.print(
            f"  sensitivity={_fmt_num(occ_inputs.get('sensitivity'))}  "
            f"raw_adjustment={_fmt_pct_signed(occ_computed.get('raw_adjustment'))}  "
            f"capped_adjustment={_fmt_pct_signed(occ_computed.get('capped_adjustment'))}"
        )
        console.print(
            f"  multiplier={_fmt_num(occ.get('multiplier'))}  "
            f"price_after_occupancy={_fmt_money(explanation.get('price_after_occupancy'))}  "
            f"reason={occ.get('reason', 'n/a')}"
        )

        console.print("\n[bold]BOOKING VELOCITY CALCULATION[/bold]")
        console.print(
            f"  enabled={vel_inputs.get('enabled', 'n/a')}  "
            f"recent_window_days={vel_inputs.get('recent_window_days', 'n/a')}  "
            f"recent_bookings={vel_inputs.get('recent_bookings', 'n/a')}  "
            f"recent_bpd={_fmt_num(vel_computed.get('recent_bpd'))}"
        )
        console.print(
            f"  baseline_window_days={vel_inputs.get('baseline_window_days', 'n/a')}  "
            f"baseline_bookings={vel_inputs.get('baseline_bookings', 'n/a')}  "
            f"baseline_bpd={_fmt_num(vel_computed.get('baseline_bpd'))}"
        )
        console.print(
            f"  velocity_ratio={_fmt_num(vel_computed.get('velocity_ratio'), 2)}x  "
            f"velocity_delta={_fmt_pct_signed(vel_computed.get('velocity_delta'))}  "
            f"sensitivity={_fmt_num(vel_inputs.get('sensitivity'))}"
        )
        console.print(
            f"  raw_adjustment={_fmt_pct_signed(vel_computed.get('raw_adjustment'))}  "
            f"capped_adjustment={_fmt_pct_signed(vel_computed.get('capped_adjustment'))}  "
            f"multiplier={_fmt_num(vel.get('multiplier'))}"
        )
        console.print(
            f"  price_after_velocity={_fmt_money(explanation.get('price_after_velocity'))}  "
            f"reason={vel.get('reason', 'n/a')}"
        )

        console.print("\n[bold]FINAL CLAMP[/bold]")
        console.print(
            f"  raw_adjusted_price={_fmt_money(explanation.get('raw_adjusted_price'))}  "
            f"floor_price={_fmt_money(explanation.get('min_price'))}  "
            f"ceiling_price={_fmt_money(explanation.get('max_price'))}  "
            f"final_price={_fmt_money(explanation.get('final_price'))}"
        )

        console.print()


def cmd_status(args: argparse.Namespace) -> None:
    """Show current iGMS prices vs recommended prices."""
    _setup_logging(args.log_level)
    config = EngineConfig.from_env(args.env)
    client = PricingClient.from_env()
    engine = PricingEngine()

    window = config.pricing_window_days
    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=window)).strftime("%Y-%m-%d")

    store = PropertyConfigStore()
    uids = store.list_properties()
    if not uids:
        console.print("[red]No local property configs found in config/properties/[/red]")
        return

    for uid in uids:
        prop_config = store.load(uid)
        if not prop_config:
            continue
        name = prop_config.get("name", uid)
        console.print(f"\n[bold cyan]{name}[/bold cyan] ({uid})")

        try:
            calendar = client.get_calendar(uid, from_date, to_date)
        except Exception as e:
            console.print(f"  [red]Calendar error: {e}[/red]")
            continue

        bookings = _fetch_bookings_cached(client, uid, from_date, to_date)

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
                bookings_in_window=bookings,
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

    store = PropertyConfigStore()
    uids = store.list_properties()
    results: dict[str, list[DatePrice]] = {}

    for uid in uids:
        prop_config = store.load(uid)
        if not prop_config:
            continue
        name = prop_config.get("name", uid)
        try:
            calendar = client.get_calendar(uid, from_date, to_date)
            bookings = _fetch_bookings_cached(client, uid, from_date, to_date)
            prices = engine.compute_range(
                property_uid=uid,
                from_date=from_date,
                to_date=to_date,
                calendar_data=calendar.get("data", []),
                bookings_in_window=bookings,
                config=config.__dict__,
            )
            results[name] = prices
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

    store = PropertyConfigStore()
    uids = store.list_properties()

    for uid in uids:
        prop_config = store.load(uid)
        if not prop_config:
            continue
        name = prop_config.get("name", uid)
        console.print(f"\n[bold]Pushing prices for {name}...[/bold]")
        try:
            calendar = client.get_calendar(uid, from_date, to_date)
            calendar_entries = calendar.get("data", [])

            by_listing = _build_calendar_by_listing(calendar_entries)

            if not by_listing:
                console.print(f"  [yellow]No calendar entries for {uid}[/yellow]")
                continue

            bookings = _fetch_bookings_cached(client, uid, from_date, to_date)

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
                            bookings_in_window=bookings,
                            config=config.__dict__,
                        )
                        console.print(
                            f"    {entry.get('date')}: current ${entry.get('price', 0)} "
                            f"→ recommended ${rec.final_price}"
                        )
                    continue

                for entry in entries:
                    date = entry.get("date", "")
                    rec = engine.compute_price(
                        property_uid=uid,
                        date=date,
                        calendar_entry=entry,
                        bookings_in_window=bookings,
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

    # CLI --from / --to override default date range
    if getattr(args, "from_date", None):
        from_date = args.from_date
    else:
        from_date = datetime.now().strftime("%Y-%m-%d")

    if getattr(args, "to_date", None):
        to_date = args.to_date
    else:
        to_date = (datetime.now() + timedelta(days=args.days or config.pricing_window_days)).strftime("%Y-%m-%d")

    client = PricingClient.from_env()
    try:
        calendar = client.get_calendar(args.property, from_date, to_date)
    except Exception as e:
        console.print(f"[red]Calendar fetch failed: {e}[/red]")
        calendar = {"data": []}

    bookings = _fetch_bookings_cached(client, args.property, from_date, to_date)

    prices = engine.compute_range(
        property_uid=args.property,
        from_date=from_date,
        to_date=to_date,
        calendar_data=calendar.get("data", []),
        bookings_in_window=bookings,
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

    # CSV export
    export_csv = getattr(args, "export_csv", False)
    if export_csv:
        csv_path = _write_prices_csv(prices, from_date)
        console.print(f"\n[green]CSV exported to {csv_path}[/green]")


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
            bookings_in_window=bookings,
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

    bookings = _fetch_bookings_cached(client, args.property, from_date, to_date)

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
                bookings_in_window=bookings,
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
                bookings_in_window=bookings,
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
    run_cfg.add_argument("--from", dest="from_date", metavar="FROM", help="Start date (YYYY-MM-DD)")
    run_cfg.add_argument("--to", dest="to_date", metavar="TO", help="End date (YYYY-MM-DD)")
    run_cfg.add_argument("--export-csv", action="store_true", help="Export results to CSV")
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

    debug = sub.add_parser("debug-day", help="Factor breakdown for a single day across all properties")
    debug.add_argument("--date", required=True, help="Target date (YYYY-MM-DD)")
    debug.add_argument("--property", action="append", dest="property", help="Property UID (repeatable; defaults to Frosty Pines + Freedom Place)")
    debug.set_defaults(func=cmd_debug_day)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
