#!/usr/bin/env python3
"""
Quicken Securities Price Fetcher
Hämtar kurser från Twelve Data och Yahoo Finance och sparar till prices.json
"""

import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, date
from pathlib import Path

# ── Konfiguration ────────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "config.json"
PRICES_FILE = Path(__file__).parent / "prices.json"
SECURITIES_FILE = Path(__file__).parent / "securities.json"

# ── Hjälpfunktioner ──────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def http_get(url, timeout=10):
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

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ── Twelve Data ──────────────────────────────────────────────────────────────
def fetch_twelvedata(symbols, api_key):
    """Hämtar priser från Twelve Data med ett batch-anrop."""
    if not symbols:
        return {}
    url = (
        "https://api.twelvedata.com/price"
        f"?symbol={urllib.parse.quote(','.join(symbols))}"
        f"&apikey={urllib.parse.quote(api_key)}"
    )
    try:
        data = http_get(url)
        results = {}
        if len(symbols) == 1:
            sym = symbols[0]
            if data.get("status") == "error" or "price" not in data:
                log(f"  Twelve Data misslyckades för {sym}: {data.get('message','okänt fel')}")
            else:
                results[sym] = float(data["price"])
                log(f"  ✓ {sym}: {results[sym]} (Twelve Data)")
        else:
            for sym in symbols:
                entry = data.get(sym, {})
                if entry.get("status") == "error" or "price" not in entry:
                    log(f"  Twelve Data misslyckades för {sym}: {entry.get('message','okänt fel')}")
                else:
                    results[sym] = float(entry["price"])
                    log(f"  ✓ {sym}: {results[sym]} (Twelve Data)")
        return results
    except Exception as e:
        log(f"  Twelve Data nätverksfel: {e}")
        return {}

# ── Yahoo Finance ────────────────────────────────────────────────────────────
def fetch_yahoo(symbol):
    """Hämtar pris för ett symbol från Yahoo Finance."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?interval=1d&range=1d"
    )
    try:
        data = http_get(url)
        result = data.get("chart", {}).get("result", [None])[0]
        if not result:
            return None
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        return float(price) if price else None
    except Exception as e:
        log(f"  Yahoo misslyckades för {symbol}: {e}")
        return None

# ── Valutakurser ─────────────────────────────────────────────────────────────
def fetch_fx_rates():
    """Hämtar valutakurser från open.er-api.com (SEK som bas)."""
    try:
        data = http_get("https://open.er-api.com/v6/latest/SEK")
        if data.get("result") == "error":
            raise ValueError(data.get("error-type", "okänt fel"))
        rates = {"SEK": 1.0}
        for cur, rate in data.get("rates", {}).items():
            rates[cur] = 1.0 / rate  # konvertera till SEK per 1 enhet
        log(f"  ✓ Valutakurser hämtade ({len(rates)} valutor)")
        return rates
    except Exception as e:
        log(f"  Valutakurser misslyckades: {e}")
        return {}

# ── Huvudlogik ───────────────────────────────────────────────────────────────
def main():
    log("=== Quicken Price Fetcher startar ===")

    # Läs konfiguration
    config = load_json(CONFIG_FILE)
    api_key = config.get("twelvedata_api_key", "")
    if not api_key:
        log("VARNING: Ingen Twelve Data API-nyckel i config.json – hoppar över Twelve Data")

    # Läs securities
    securities = load_json(SECURITIES_FILE, [])
    if not securities:
        log("Inga securities i securities.json – avslutar")
        sys.exit(0)

    today = date.today().isoformat()
    log(f"Datum: {today}, antal securities: {len(securities)}")

    # Läs befintliga priser (för att bevara manuella)
    existing = load_json(PRICES_FILE, {})
    prices = existing.copy()
    prices["_updated"] = datetime.now().isoformat()
    prices["_date"] = today

    # Separera manuella från automatiska
    auto_secs = [s for s in securities if not s.get("manual")]
    manual_secs = [s for s in securities if s.get("manual")]
    log(f"Automatiska: {len(auto_secs)}, manuella (hoppas över): {len(manual_secs)}")

    # Bygg sym → security-mapping (använd ext om det finns)
    sym_map = {}
    for s in auto_secs:
        sym = (s.get("ext") or s.get("sym", "")).strip()
        if sym:
            sym_map[sym] = s

    if not sym_map:
        log("Inga symboler att hämta")
    else:
        # Steg 1: Twelve Data (batch)
        td_results = {}
        if api_key:
            log(f"Hämtar {len(sym_map)} symboler från Twelve Data...")
            td_results = fetch_twelvedata(list(sym_map.keys()), api_key)

        # Steg 2: Yahoo Finance fallback för de som misslyckades
        failed = [sym for sym in sym_map if sym not in td_results]
        if failed:
            log(f"Provar Yahoo Finance för {len(failed)} symboler...")
            for sym in failed:
                price = fetch_yahoo(sym)
                if price is not None:
                    td_results[sym] = price
                    log(f"  ✓ {sym}: {price} (Yahoo Finance)")
                else:
                    log(f"  ✗ {sym}: hittades inte")
                time.sleep(0.3)

        # Hämta valutakurser
        log("Hämtar valutakurser...")
        fx = fetch_fx_rates()

        # Beräkna SEK-priser och spara
        ok = 0
        for sym, sec in sym_map.items():
            raw_price = td_results.get(sym)
            if raw_price is None:
                continue
            currency = sec.get("currency", "SEK")
            fx_rate = fx.get(currency, 1.0) if currency != "SEK" else 1.0
            sek_price = raw_price * fx_rate

            entry = {
                "sym": sec.get("sym", sym),
                "name": sec.get("name", ""),
                "ext": sym,
                "currency": currency,
                "raw_price": round(raw_price, 4),
                "fx_rate": round(fx_rate, 6),
                "sek_price": round(sek_price, 2),
                "date": today,
            }
            prices[sec.get("sym", sym)] = entry
            ok += 1

        prices["_fx"] = fx
        log(f"Klart: {ok} priser uppdaterade, {len(failed) - (len(failed) - len([s for s in failed if sym_map[s].get('sym') in prices]))} misslyckades")

    save_json(PRICES_FILE, prices)
    log(f"Priser sparade till {PRICES_FILE}")
    log("=== Klar ===")

if __name__ == "__main__":
    main()
