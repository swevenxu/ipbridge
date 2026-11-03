#!/usr/bin/env python3
"""Photo Bridge — a tiny photo exchange for a phone that can only do plain HTTP.

Runs on the Python standard library alone. One file of state: the uploads dir.

    PIN=1234 python3 app.py            # http://<host>:8080
"""
import email
import email.policy
import hashlib
import hmac
import html
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("STORE", os.path.join(ROOT, "uploads"))
STATIC = os.path.join(ROOT, "static")
# Panels (Pterodactyl-style) inject SERVER_PORT instead of PORT.
PORT = int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or 8080)


def _pin():
    """Env first; then a file, because panels give you nowhere to put env vars."""
    env = os.environ.get("PIN", "").strip()
    if env:
        return env
    path = os.path.join(ROOT, ".pin")
    if os.path.isfile(path):
        return open(path).read().strip()
    return ""


PIN = _pin()

MAX_BYTES = int(os.environ.get("MAX_MB", "100")) * 1024 * 1024
MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".mov", ".mp4"}
COOKIE = "pb"
DAYS = 30

os.makedirs(STORE, exist_ok=True)


def _secret():
    """Persisted in the store so sessions survive restarts and redeploys."""
    path = os.path.join(STORE, ".secret")
    if os.path.exists(path):
        return open(path, "rb").read()
    data = os.urandom(32)
    open(path, "wb").write(data)
    os.chmod(path, 0o600)
    return data


SECRET = _secret()
ATTEMPTS = {}  # ip -> [count, first_ts]


def token() -> str:
    exp = str(int(time.time()) + DAYS * 86400)
    mac = hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()[:32]
    return exp + "." + mac


def valid_token(raw: str) -> bool:
    try:
        exp, mac = raw.split(".")
    except (ValueError, AttributeError):
        return False
    if not exp.isdigit() or int(exp) < time.time():
        return False
    want = hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(mac, want)


def safe_name(name: str) -> str:
    base = os.path.basename(name.strip())
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80]
    return base


def media_files():
    out = [f for f in os.listdir(STORE) if os.path.splitext(f)[1].lower() in MEDIA]
    out.sort(key=lambda f: os.path.getmtime(os.path.join(STORE, f)), reverse=True)
    return out


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="format-detection" content="telephone=no">
<title>Photo Bridge</title>
<link rel="stylesheet" href="/static/app.css">
<script src="/static/app.js" defer></script>
</head><body>
<header class="bar">
  <h1>Photo Bridge</h1>
  <span class="count">__COUNT__</span>
</header>

<main>
  <section class="uploader">
    <label class="pick" for="picker">
      <span class="pick-plus">+</span>
      <span class="pick-label">Add photos</span>
    </label>
    <input id="picker" type="file" name="photo" accept="image/*,video/*" multiple>
    <form id="fallback" method="post" action="/upload" enctype="multipart/form-data" hidden>
      <input type="file" name="photo">
      <button type="submit">Upload</button>
    </form>
    <p id="status" class="status" role="status"></p>
  </section>

  <section class="gallery">
    __TILES__
  </section>
</main>

<footer class="foot">
  <a href="/logout">Sign out</a>
  <span>__USED__</span>
</footer>
</body></html>"""

LOGIN = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Photo Bridge</title>
<link rel="stylesheet" href="/static/app.css">
</head><body class="centered">
<form class="login" method="post" action="/login">
  <h1>Photo Bridge</h1>
  <p>Enter the PIN.</p>
  <input type="password" name="pin" inputmode="numeric" autocomplete="current-password"
         autofocus aria-label="PIN">
  <button type="submit">Open</button>
  __ERROR__
</form>
</body></html>"""


def tiles():
    files = media_files()
    if not files:
        return '<p class="empty">No photos yet.</p>'
    out = []
    for f in files:
        src = "/media/" + quote(f)
        size = human(os.path.getsize(os.path.join(STORE, f)))
        if os.path.splitext(f)[1].lower() in {".mov", ".mp4"}:
            media = '<video src="%s" playsinline muted preload="metadata"></video>' % src
        else:
            media = '<img src="%s" alt="" loading="lazy">' % src
        out.append(
            '<figure class="tile">'
            '<a class="thumb" href="%s">%s</a>'
            '<button class="del" data-name="%s" aria-label="Delete photo">&times;</button>'
            '<figcaption>%s</figcaption>'
            "</figure>" % (src, media, html.escape(f, quote=True), size)
        )
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "photobridge"

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), flush=True)

    # ---- plumbing ----
    def send_body(self, code, ctype, body, extra=None, cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, where):
        self.send_body(303, "text/plain", b"", {"Location": where})

    def cookie_value(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return ""

    def authed(self):
        return valid_token(self.cookie_value())

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return parse_qs(self.rfile.read(min(n, 1 << 20)).decode("utf-8", "replace"))

    # ---- routes ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/static/app.css":
            return self.static("app.css", "text/css; charset=utf-8")
        if path == "/static/app.js":
            return self.static("app.js", "application/javascript; charset=utf-8")
        if path == "/logout":
            return self.send_body(303, "text/plain", b"", {
                "Location": "/", "Set-Cookie": "%s=; Path=/; Max-Age=0" % COOKIE})
        if not self.authed():
            if path in ("/", ""):
                return self.send_body(200, "text/html; charset=utf-8", LOGIN.encode())
            return self.send_body(403, "text/plain", b"auth required")
        if path in ("/", ""):
            used = sum(os.path.getsize(os.path.join(STORE, f)) for f in media_files())
            body = (PAGE.replace("__COUNT__", "%d photo%s" % (
                len(media_files()), "" if len(media_files()) == 1 else "s"))
                       .replace("__TILES__", tiles())
                       .replace("__USED__", human(used)))
            return self.send_body(200, "text/html; charset=utf-8", body.encode())
        if path.startswith("/media/"):
            return self.media(unquote(path[len("/media/"):]))
        return self.send_body(404, "text/plain", b"not found")

    def static(self, name, ctype):
        path = os.path.join(STATIC, name)
        if not os.path.isfile(path):
            return self.send_body(404, "text/plain", b"not found")
        with open(path, "rb") as fh:
            self.send_body(200, ctype, fh.read(), {"Cache-Control": "public, max-age=3600"})

    def media(self, name):
        safe = safe_name(name)
        path = os.path.join(STORE, safe)
        if safe != name or not os.path.isfile(path):
            return self.send_body(404, "text/plain", b"not found")
        ext = os.path.splitext(safe)[1].lower()
        ctype = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".heic": "image/heic", ".mov": "video/quicktime",
            ".mp4": "video/mp4",
        }.get(ext, "application/octet-stream")
        with open(path, "rb") as fh:
            data = fh.read()
        # shortcut: whole-file responses, no Range — fine for phone-sized media
        self.send_body(200, ctype, data, {"Cache-Control": "private, max-age=86400"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/login":
            return self.login()
        if not self.authed():
            # close: an unread POST body would otherwise be parsed as the next
            # request on this keep-alive connection
            self.close_connection = True
            return self.send_body(403, "text/plain", b"auth required",
                                  {"Connection": "close"})
        if path == "/upload":
            return self.upload()
        if path == "/delete":
            return self.delete()
        return self.send_body(404, "text/plain", b"not found")

    def login(self):
        ip = self.address_string()
        count, first = ATTEMPTS.get(ip, (0, time.time()))
        if count >= 6 and time.time() - first < 300:
            return self.send_body(429, "text/plain", b"too many attempts, wait 5 min")
        if not PIN:
            return self.send_body(500, "text/plain", b"PIN not configured on the server")
        pin = (self.form().get("pin") or [""])[0]
        if not hmac.compare_digest(pin, PIN):
            ATTEMPTS[ip] = (count + 1, first)
            body = LOGIN.replace("__ERROR__", '<p class="error">Wrong PIN.</p>')
            return self.send_body(401, "text/html; charset=utf-8", body.encode())
        ATTEMPTS.pop(ip, None)
        cookie = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
            COOKIE, token(), DAYS * 86400)
        self.send_body(303, "text/plain", b"", {"Location": "/", "Set-Cookie": cookie})

    def upload(self):
        ctype = self.headers.get("Content-Type") or ""
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if "multipart/form-data" not in ctype or not m:
            return self.json(400, {"ok": False, "error": "expected multipart"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BYTES:
            return self.json(413, {"ok": False, "error": "file too large"})
        raw = self.rfile.read(length)
        msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
            b"Content-Type: multipart/form-data; boundary=" + m.group(1).encode()
            + b"\r\n\r\n" + raw)
        saved = []
        if not msg.is_multipart():
            return self.json(400, {"ok": False, "error": "bad body"})
        for part in msg.iter_parts():
            name = part.get_filename()
            data = part.get_payload(decode=True) or b""
            if not name or not data:
                continue
            base = safe_name(name)
            stem, ext = os.path.splitext(base)
            if ext.lower() not in MEDIA:
                continue
            final = "%s-%s%s" % (stem or "photo", time.strftime("%Y%m%d-%H%M%S"), ext.lower())
            with open(os.path.join(STORE, final), "wb") as fh:
                fh.write(data)
            saved.append(final)
        return self.json(200, {"ok": True, "saved": saved})

    def delete(self):
        name = (self.form().get("name") or [""])[0].strip()
        if not name or safe_name(name) != name or os.path.splitext(name)[1].lower() not in MEDIA:
            return self.json(400, {"ok": False, "error": "bad name"})
        path = os.path.join(STORE, name)
        if not os.path.isfile(path):
            return self.json(404, {"ok": False, "error": "not found"})
        os.remove(path)
        return self.json(200, {"ok": True})

    def json(self, code, obj):
        import json as _json
        self.send_body(code, "application/json", _json.dumps(obj).encode())


if __name__ == "__main__":
    if not os.path.isdir(STATIC):
        raise SystemExit("missing static/ next to app.py")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("Photo Bridge on http://0.0.0.0:%d — store %s" % (PORT, STORE), flush=True)
    if not PIN:
        print("WARNING: PIN is empty — set PIN=<digits> before exposing this.", flush=True)
    srv.serve_forever()
