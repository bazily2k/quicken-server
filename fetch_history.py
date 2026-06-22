#!/usr/bin/env python3
"""
Quicken Historical Price Fetcher
Hämtar historiska kurser från Yahoo Finance och genererar QIF-fil för Quicken.
"""

import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, date, timedelta
from pathlib import Path

SECURITIES_FILE = Path(__file__).parent / "securities.json"

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def load_json(path, default=None):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default if default is not None else {}

# ── Datumfiltrering ────────────────────────────────────────────────────────────

def filter_dates(all_dates, mode, weekday=None):
    """
    Filtrerar en lista av date-objekt baserat på valt läge.
    mode: 'weekday' | 'last_of_month' | 'first_of_month' | 'all'
    weekday: 0=måndag ... 6=söndag (används vid mode='weekday')
    """
    if mode == "all":
        return all_dates

    if mode == "weekday":
        # Välj närmaste tillgängliga handelsdag för varje vecka
        # Gruppera per veckonummer och plocka den som matchar weekday (eller närmaste)
        by_week = {}
        for d in all_dates:
            key = (d.isocalendar()[0], d.isocalendar()[1])  # (år, vecka)
            by_week.setdefault(key, []).append(d)
        result = []
        for week_dates in by_week.values():
            # Försök hitta exakt rätt veckodag
            match = [d for d in week_dates if d.weekday() == weekday]
            if match:
                result.append(match[0])
            else:
                # Välj närmaste dag i veckan
                best = min(week_dates, key=lambda d: abs(d.weekday() - weekday))
                result.append(best)
        return sorted(result)

    if mode == "last_of_month":
        by_month = {}
        for d in all_dates:
            key = (d.year, d.month)
            by_month.setdefault(key, []).append(d)
        return sorted(max(v) for v in by_month.values())

    if mode == "first_of_month":
        by_month = {}
        for d in all_dates:
            key = (d.year, d.month)
            by_month.setdefault(key, []).append(d)
        return sorted(min(v) for v in by_month.values())

    return all_dates

# ── Yahoo Finance historik ─────────────────────────────────────────────────────

def fetch_yahoo_history(symbol, start_date, end_date):
    """
    Hämtar dagliga stängningspriser från Yahoo Finance.
    Returnerar dict {date_str: price} eller {}.
    """
    start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    end_ts   = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time()).timestamp())

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}"
        f"?interval=1d&period1={start_ts}&period2={end_ts}"
    )
    try:
        data = http_get(url)
        result = data.get("chart", {}).get("result", [None])[0]
        if not result:
            log(f"  ✗ {symbol}: inga data från Yahoo")
            return {}
        timestamps = result.get("timestamp", [])
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        prices = {}
        for ts, price in zip(timestamps, closes):
            if price is None:
                continue
            d = date.fromtimestamp(ts)
            prices[d] = round(float(price), 4)
        log(f"  ✓ {symbol}: {len(prices)} dagar hämtade från Yahoo Finance")
        return prices
    except Exception as e:
        log(f"  ✗ {symbol}: Yahoo-fel: {e}")
        return {}

# ── Valutakurser (historik) ────────────────────────────────────────────────────

def fetch_fx_for_dates(currency, dates):
    """
    Hämtar historiska valutakurser (till SEK) för en valuta.
    Använder Yahoo Finance: SEKXXX=X (t.ex. SEKEUR=X → SEK/EUR).
    Returnerar dict {date: fx_rate} (SEK per 1 enhet av currency).
    """
    if currency == "SEK":
        return {d: 1.0 for d in dates}

    # Yahoo symbol: t.ex. EURSEK=X → pris = antal SEK per 1 EUR
    yahoo_sym = f"{currency}SEK=X"
    if not dates:
        return {}
    start_date = min(dates)
    end_date   = max(dates)

    url_sym = urllib.parse.quote(yahoo_sym)
    start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    end_ts   = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time()).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{url_sym}"
        f"?interval=1d&period1={start_ts}&period2={end_ts}"
    )
    try:
        data = http_get(url)
        result = data.get("chart", {}).get("result", [None])[0]
        if not result:
            return {}
        timestamps = result.get("timestamp", [])
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        fx_by_date = {}
        for ts, price in zip(timestamps, closes):
            if price is None:
                continue
            fx_by_date[date.fromtimestamp(ts)] = round(float(price), 6)
        return fx_by_date
    except Exception:
        return {}

def nearest_fx(fx_by_date, target_date):
    """Hitta närmaste tillgängliga FX-kurs till ett givet datum."""
    if not fx_by_date:
        return 1.0
    if target_date in fx_by_date:
        return fx_by_date[target_date]
    # Sök bakåt upp till 5 dagar
    for delta in range(1, 6):
        d = target_date - timedelta(days=delta)
        if d in fx_by_date:
            return fx_by_date[d]
    # Sök framåt
    for delta in range(1, 6):
        d = target_date + timedelta(days=delta)
        if d in fx_by_date:
            return fx_by_date[d]
    # Fallback: närmaste
    return min(fx_by_date.values(), key=lambda v: v)

# ── QIF-generering ─────────────────────────────────────────────────────────────

def build_qif(results):
    """
    Bygger QIF-strängformat för Quicken Securities Prices.
    results: list of {sym, date, sek_price}
    """
    lines = ["!Type:Prices"]
    for r in results:
        d = r["date"]
        # Quicken QIF datum-format: MM/DD/YYYY
        date_str = f"{d.month}/{d.day}/{d.year}"
        # Format: "SYMBOL",pris,datum
        lines.append(f'"{r["sym"]}",{r["sek_price"]},{date_str}')
        lines.append("^")
    return "\n".join(lines) + "\n"

# ── Huvudfunktion ─────────────────────────────────────────────────────────────

def main():
    """
    Läser parametrar från stdin (JSON) och skriver QIF till stdout.
    Input JSON:
    {
      "symbols": ["SYM1", "SYM2"],   // Quicken-symboler (sym från securities.json)
      "start": "2024-01-01",
      "end":   "2024-12-31",
      "mode":  "weekday" | "last_of_month" | "first_of_month" | "all",
      "weekday": 2                    // 0=mån..6=sön, används vid mode=weekday
    }
    """
    try:
        params = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": f"Ogiltiga parametrar: {e}"}))
        sys.exit(1)

    req_syms  = params.get("symbols", [])
    start_str = params.get("start", "")
    end_str   = params.get("end", "")
    mode      = params.get("mode", "last_of_month")
    weekday   = params.get("weekday", 2)  # onsdag default

    try:
        start_date = date.fromisoformat(start_str)
        end_date   = date.fromisoformat(end_str)
    except Exception:
        print(json.dumps({"error": "Ogiltigt datumformat (använd YYYY-MM-DD)"}))
        sys.exit(1)

    if start_date > end_date:
        print(json.dumps({"error": "Startdatum måste vara före slutdatum"}))
        sys.exit(1)

    # Läs securities
    securities = load_json(SECURITIES_FILE, [])
    sec_by_sym = {s["sym"]: s for s in securities}

    # Filtrera till efterfrågade symboler
    if req_syms:
        selected = [sec_by_sym[s] for s in req_syms if s in sec_by_sym]
    else:
        selected = [s for s in securities if not s.get("manual")]

    if not selected:
        print(json.dumps({"error": "Inga matchande securities hittades"}))
        sys.exit(1)

    log(f"Hämtar historik för {len(selected)} securities: {start_str} → {end_str} ({mode})")

    all_results = []
    fx_cache = {}  # currency -> {date: fx_rate}

    for sec in selected:
        sym     = sec["sym"]
        ext     = sec.get("ext") or sym
        currency = sec.get("currency", "SEK")

        log(f"Hämtar {sym} ({ext})...")
        price_by_date = fetch_yahoo_history(ext, start_date, end_date)

        if not price_by_date:
            log(f"  Hoppar över {sym} (inga data)")
            continue

        # Filtrera datum enligt valt läge
        all_dates = sorted(price_by_date.keys())
        filtered_dates = filter_dates(all_dates, mode, weekday)

        # Hämta FX-kurser om nödvändigt
        if currency != "SEK":
            if currency not in fx_cache:
                log(f"  Hämtar {currency}/SEK valutahistorik...")
                fx_cache[currency] = fetch_fx_for_dates(currency, filtered_dates)
                time.sleep(0.3)

        for d in filtered_dates:
            raw_price = price_by_date.get(d)
            if raw_price is None:
                continue
            if currency != "SEK":
                fx = nearest_fx(fx_cache.get(currency, {}), d)
                sek_price = round(raw_price * fx, 2)
            else:
                sek_price = raw_price

            all_results.append({
                "sym":       sym,
                "date":      d,
                "sek_price": sek_price,
                "currency":  currency,
                "raw_price": raw_price,
            })

        time.sleep(0.4)  # Undvik rate-limiting

    if not all_results:
        print(json.dumps({"error": "Inga priser hämtades. Kontrollera symboler och datumintervall."}))
        sys.exit(1)

    # Sortera: datum, sedan symbol
    all_results.sort(key=lambda r: (r["date"], r["sym"]))

    qif = build_qif(all_results)

    log(f"Klar: {len(all_results)} kursposter för {len(selected)} securities")
    print(json.dumps({
        "ok": True,
        "count": len(all_results),
        "securities": len(selected),
        "qif": qif,
    }))

if __name__ == "__main__":
    main()
