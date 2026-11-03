#!/usr/bin/env python3
"""Photo Bridge — a tiny photo exchange for a phone that can only do plain HTTP.

Runs on the Python standard library alone. One file of state: the uploads dir.

    python3 app.py                     # http://<host>:8080
"""
import email
import email.policy
import html
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("STORE", os.path.join(ROOT, "uploads"))
THUMBS = os.path.join(STORE, "thumbs")   # grid-sized copies; the gallery never loads full files
STATIC = os.path.join(ROOT, "static")
# Panels (Pterodactyl-style) inject SERVER_PORT instead of PORT.
PORT = int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or 8080)


MAX_BYTES = int(os.environ.get("MAX_MB", "100")) * 1024 * 1024
MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".mov", ".mp4"}

os.makedirs(STORE, exist_ok=True)
os.makedirs(THUMBS, exist_ok=True)


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
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="format-detection" content="telephone=no">
<title>114s</title>
<link rel="stylesheet" href="/static/app.css">
<script src="/static/app.js" defer></script>
</head><body>
<header class="bar">
  <div class="wrap">
    <h1>114s</h1>
    <span class="count">__COUNT__</span>
  </div>
</header>

<main class="wrap">
  <section class="uploader">
    <input id="picker" type="file" name="photo" accept="image/*,video/*" multiple>
    <label class="pick" for="picker">
      <span class="pick-plus">+</span>
      <span class="pick-label">Add photos</span>
    </label>
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
  <div class="wrap">
    <span>__USED__ used</span>
  </div>
</footer>
</body></html>"""

def tiles():
    files = media_files()
    if not files:
        return ('<p class="empty"><strong>No photos yet</strong>'
            'Photos you add here appear on both phones.</p>')
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
    def send_body(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return parse_qs(self.rfile.read(min(n, 1 << 20)).decode("utf-8", "replace"))

    # ---- routes ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/static/"):
            return self.static(path[len("/static/"):])
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

    def static(self, name):
        safe = safe_name(name)
        path = os.path.join(STATIC, safe)
        if safe != name or not os.path.isfile(path):
            return self.send_body(404, "text/plain", b"not found")
        ext = os.path.splitext(safe)[1].lower()
        ctype = {".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".woff": "font/woff", ".ttf": "font/ttf"}.get(
                     ext, "application/octet-stream")
        with open(path, "rb") as fh:
            data = fh.read()
        # fonts cache for a day; no-cache on code: a cached app.js silently
        # disables client fixes on old browsers
        cache = "public, max-age=86400" if ext in (".woff", ".ttf") else "no-cache"
        self.send_body(200, ctype, data, {"Cache-Control": cache})

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
        if path == "/upload":
            return self.upload()
        if path == "/delete":
            return self.delete()
        return self.send_body(404, "text/plain", b"not found")

    def body(self):
        """Request body, chunked or not.

        iOS 9 Safari uploads with Transfer-Encoding: chunked. Reading only
        Content-Length there gives an empty body, and answering before the
        client finishes makes the phone see a reset instead of a response.
        """
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            out = []
            total = 0
            while True:
                line = self.rfile.readline(1024)
                if not line:
                    break
                size = int(line.strip().split(b";")[0] or b"0", 16)
                if size == 0:
                    while True:                      # trailers
                        trailer = self.rfile.readline(1024)
                        if trailer in (b"\r\n", b"\n", b""):
                            break
                    break
                total += size
                if total > MAX_BYTES:
                    raise ValueError("too large")
                out.append(self.rfile.read(size))
                self.rfile.read(2)                   # CRLF after each chunk
            return b"".join(out)
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BYTES:
            raise ValueError("too large")
        return self.rfile.read(n) if n > 0 else b""

    def upload(self):
        ctype = self.headers.get("Content-Type") or ""
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if "multipart/form-data" not in ctype or not m:
            return self.json(400, {"ok": False, "error": "expected multipart"})
        try:
            raw = self.body()
        except ValueError:
            return self.json(413, {"ok": False, "error": "file too large"})
        if not raw:
            return self.json(400, {"ok": False, "error": "empty body"})
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
            # Idempotent: a double tap or a stale tile must not look like a failure.
            return self.json(200, {"ok": True, "already_gone": True})
        os.remove(path)
        return self.json(200, {"ok": True})

    def json(self, code, obj):
        import json as _json
        self.send_body(code, "application/json", _json.dumps(obj).encode())


if __name__ == "__main__":
    if not os.path.isdir(STATIC):
        raise SystemExit("missing static/ next to app.py")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("114s on http://0.0.0.0:%d — store %s" % (PORT, STORE), flush=True)
    srv.serve_forever()
