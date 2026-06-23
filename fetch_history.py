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
CONFIG_FILE     = Path(__file__).parent / "config.json"

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

# ── Twelve Data historik ───────────────────────────────────────────────────────

def fetch_twelvedata_history(symbol, start_date, end_date, api_key):
    """
    Hämtar dagliga stängningspriser från Twelve Data time_series endpoint.
    Returnerar dict {date: price} eller {}.
    """
    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol={urllib.parse.quote(symbol)}"
        f"&interval=1day"
        f"&start_date={start_date.isoformat()}"
        f"&end_date={end_date.isoformat()}"
        f"&outputsize=5000"
        f"&apikey={urllib.parse.quote(api_key)}"
    )
    try:
        data = http_get(url)
        if data.get("status") == "error":
            log(f"  ✗ {symbol}: Twelve Data fel: {data.get('message','okänt fel')}")
            return {}
        values = data.get("values", [])
        if not values:
            log(f"  ✗ {symbol}: Twelve Data returnerade inga värden")
            return {}
        prices = {}
        for entry in values:
            try:
                d = date.fromisoformat(entry["datetime"][:10])
                prices[d] = round(float(entry["close"]), 4)
            except Exception:
                continue
        log(f"  ✓ {symbol}: {len(prices)} dagar hämtade från Twelve Data")
        return prices
    except Exception as e:
        log(f"  ✗ {symbol}: Twelve Data nätverksfel: {e}")
        return {}



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

# ── CSV-generering (matchar befintligt Quicken-importformat) ───────────────────

def build_csv(results):
    """
    Bygger CSV i samma format som befintlig export: symbol,pris,datum (MM/DD/YYYY)
    Importeras med samma PowerShell-script som vanlig kurshämtning.
    """
    lines = []
    for r in results:
        d = r["date"]
        date_str = f"{d.month:02d}/{d.day:02d}/{d.year}"
        lines.append(f'{r["sym"]},{r["sek_price"]:.2f},{date_str}')
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

    # Läs Twelve Data API-nyckel
    config = load_json(CONFIG_FILE)
    api_key = config.get("twelvedata_api_key", "")
    if not api_key:
        log("VARNING: Ingen Twelve Data API-nyckel i config.json – hoppar över Twelve Data")

    all_results = []
    fx_cache = {}  # currency -> {date: fx_rate}

    # Steg 1: Twelve Data för alla symboler
    td_data = {}  # ext -> {date: price}
    if api_key:
        log(f"Hämtar historik från Twelve Data för {len(selected)} symboler...")
        for sec in selected:
            ext = sec.get("ext") or sec["sym"]
            result = fetch_twelvedata_history(ext, start_date, end_date, api_key)
            if result:
                td_data[ext] = result
            time.sleep(0.5)  # Twelve Data rate-limit

    # Steg 2: Yahoo Finance för de som misslyckades
    failed = [s for s in selected if (s.get("ext") or s["sym"]) not in td_data]
    if failed:
        log(f"Provar Yahoo Finance för {len(failed)} symboler...")
        for sec in failed:
            ext = sec.get("ext") or sec["sym"]
            result = fetch_yahoo_history(ext, start_date, end_date)
            if result:
                td_data[ext] = result
            else:
                log(f"  ✗ {sec['sym']}: hittades inte på varken Twelve Data eller Yahoo")
            time.sleep(0.3)

    for sec in selected:
        sym      = sec["sym"]
        ext      = sec.get("ext") or sym
        currency = sec.get("currency", "SEK")

        price_by_date = td_data.get(ext, {})
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

    qif = build_csv(all_results)

    log(f"Klar: {len(all_results)} kursposter för {len(selected)} securities")
    print(json.dumps({
        "ok": True,
        "count": len(all_results),
        "securities": len(selected),
        "csv": qif,
    }))

if __name__ == "__main__":
    main()
