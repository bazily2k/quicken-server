#!/usr/bin/env python3
"""
Lokal webbserver för Quicken Securities Importer.
Hanterar GET (filer) och POST (/save, /fetch).
"""

import json
import os
import subprocess
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = 8765
BASE_DIR = Path(__file__).parent

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path == '/save':
            self.handle_save()
        elif self.path == '/fetch':
            self.handle_fetch()
        elif self.path == '/test':
            self.handle_test()
        elif self.path == '/history':
            self.handle_history()
        else:
            self.send_error(404)

    def handle_save(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            securities = data.get('securities', [])
            sec_file = BASE_DIR / 'securities.json'
            with open(sec_file, 'w') as f:
                json.dump(securities, f, indent=2, ensure_ascii=False)
            print(f"[server] securities.json uppdaterad med {len(securities)} poster")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True, 'count': len(securities)}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def handle_fetch(self):
        try:
            print("[server] Kör fetch_prices.py...")
            result = subprocess.run(
                ['python3', str(BASE_DIR / 'fetch_prices.py')],
                capture_output=True, text=True, timeout=60
            )
            print(result.stdout)
            # Parse how many prices were fetched from log
            ok_count = result.stdout.count('  OK ')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'ok': ok_count,
                'log': result.stdout[-500:] if result.stdout else ''
            }).encode())
        except subprocess.TimeoutExpired:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Timeout efter 60 sekunder'}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def handle_test(self):
        try:
            import urllib.request
            import urllib.parse
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            sym = body.get('symbol', '').strip()
            api_key = body.get('api_key', '').strip()
            if not sym:
                raise ValueError('Symbol saknas')

            result = {}

            # Try Twelve Data first
            if api_key:
                url = f"https://api.twelvedata.com/price?symbol={urllib.parse.quote(sym)}&apikey={urllib.parse.quote(api_key)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode())
                    if data.get('status') != 'error' and 'price' in data:
                        result = {'price': float(data['price']), 'source': 'Twelve Data', 'symbol': sym}
                except Exception:
                    pass

            # Try Yahoo Finance as fallback
            if not result:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?interval=1d&range=1d"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode())
                    r = data.get('chart', {}).get('result', [None])[0]
                    if r:
                        meta = r.get('meta', {})
                        price = meta.get('regularMarketPrice') or meta.get('previousClose')
                        if price:
                            result = {'price': float(price), 'source': 'Yahoo Finance', 'symbol': sym}
                except Exception:
                    pass

            if not result:
                result = {'error': f'Symbolen {sym} hittades inte i Twelve Data eller Yahoo Finance'}

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def handle_history(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            print(f"[server] Kör fetch_history.py...")
            result = subprocess.run(
                ['python3', str(BASE_DIR / 'fetch_history.py')],
                input=body.decode(),
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or "fetch_history.py misslyckades")
            # Sista raden är JSON-resultatet
            lines = [l for l in result.stdout.strip().split('\n') if l.startswith('{')]
            if not lines:
                raise RuntimeError("Inget JSON-svar från fetch_history.py")
            resp_data = json.loads(lines[-1])
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode())
        except subprocess.TimeoutExpired:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Timeout – för många symboler eller för lång period?'}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def log_message(self, format, *args):
        if any(x in str(args) for x in ['404', '500', 'POST']):
            super().log_message(format, *args)

def main():
    os.chdir(BASE_DIR)
    print(f"Quicken server körs på http://0.0.0.0:{PORT}")
    print(f"Öppna http://192.168.1.138:{PORT}/quicken_importer.html i Edge")
    print("Tryck Ctrl+C för att stoppa.\n")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stoppad.")

if __name__ == "__main__":
    main()
