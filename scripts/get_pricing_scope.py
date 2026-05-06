#!/usr/bin/env python3
"""
OAuth callback server for requesting the pricing-management scope.

1) Make sure your iGMS app redirect URI is set to:
   http://localhost:8000/callback

2) Run this server:
   python3 igms_callback_server_pricing.py

3) Open the printed auth URL in your browser.

4) iGMS will redirect to localhost:8000/callback with the auth code.

5) The server exchanges the code for a token and prints it.

6) Replace IGMS_ACCESS_TOKEN in your .env with the new token.
"""

from __future__ import annotations

import json
import secrets
import threading
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

CLIENT_ID = "412"
REDIRECT_URI = "http://localhost:8000/callback"
# Full scope including pricing-management
SCOPE = "tasks,messaging,listings,calendar-control,direct-bookings,pricing-management"
AUTH_URL = "https://igms.com/app/auth.html"
TOKEN_URL = "https://igms.com/auth/token"

STATE = secrets.token_urlsafe(24)


def build_auth_url() -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": STATE,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> dict:
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
    }
    url = f"{TOKEN_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [""])[0]
        state = query.get("state", [""])[0]
        error = query.get("error", [""])[0]

        if error:
            body = f"iGMS returned error: {error}\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            print(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if state != STATE:
            body = "State mismatch — possible CSRF attack.\n"
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            self.wfile.write(f"Expected: {STATE}\nReceived: {state}\n".encode())
            print(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if not code:
            body = "No code received.\n"
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            print(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        try:
            token_resp = exchange_code(code)
            access_token = token_resp.get("access_token", "")
            body = "Success! You can close this tab.\n\n" + json.dumps(token_resp, indent=2)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            print("✅ Auth code received.")
            print(json.dumps(token_resp, indent=2))
            print()
            if access_token:
                print("⚠️  Add this to your .env as IGMS_ACCESS_TOKEN:")
                print(f"IGMS_ACCESS_TOKEN={access_token}")
                print()
                print("Then test pricing write with:")
                print(f"curl -s -X POST 'https://www.igms.com/api/v1/calendar?access_token={access_token}' \\")
                print("  -H 'Content-Type: application/json' \\")
                print("  -d '{\"property_uid\":\"6925833560458409984\",\"date\":\"2099-01-01\",\"price\":9999}'")
        except urllib.error.HTTPError as e:
            body = f"Token exchange HTTP error: {e.code} {e.reason}\n{e.read().decode()}\n"
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            print(body)
        except Exception as exc:
            body = f"Token exchange failed: {exc}\n"
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            print(body)
        finally:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format, *args):
        return


def main() -> None:
    print("iGMS OAuth — pricing-management scope")
    print("=" * 45)
    print(f"Client ID: {CLIENT_ID}")
    print(f"Redirect URI: {REDIRECT_URI}")
    print(f"Scope: {SCOPE}")
    print()
    url = build_auth_url()
    print("Open this URL in your browser:")
    print(url)
    print()
    try:
        webbrowser.open(url)
        print("Opened browser automatically.")
    except Exception:
        pass

    server = HTTPServer(("localhost", 8000), Handler)
    print(f"Listening on http://localhost:8000/callback ...")
    print("Waiting for callback...")
    server.serve_forever()


if __name__ == "__main__":
    main()