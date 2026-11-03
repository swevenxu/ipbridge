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


MAX_BYTES = int(os.environ.get("MAX_MB", "300")) * 1024 * 1024
MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".mov", ".mp4"}

os.makedirs(STORE, exist_ok=True)
os.makedirs(THUMBS, exist_ok=True)


def safe_name(name: str) -> str:
    base = os.path.basename(name.strip())
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80]
    return base


def media_files(order="newest"):
    out = [f for f in os.listdir(STORE) if os.path.splitext(f)[1].lower() in MEDIA]
    out.sort(key=lambda f: os.path.getmtime(os.path.join(STORE, f)),
             reverse=(order != "oldest"))
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
<meta name="theme-color" content="#000000">
<title>114s</title>
<link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="stylesheet" href="/static/app.css">
<script src="/static/app.js" defer></script>
</head><body>
<header class="bar">
  <div class="wrap">
    <span class="count">__COUNT__</span>
  </div>
</header>

<main class="wrap">
  <section class="uploader">
    <div class="row">
      <input id="picker" type="file" name="photo" accept="image/*,video/*" multiple>
      <label class="pick" for="picker">
        <span class="pick-plus">+</span>
        <span class="pick-label">Add photos</span>
      </label>
      <a class="sort" href="__SORT_HREF__" title="Switch the order">__SORT_LABEL__</a>
    </div>
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

def tiles(order="newest"):
    files = media_files(order)
    if not files:
        return ('<p class="empty"><strong>Nothing here yet</strong>'
            'Photos and clips you add appear on both phones.</p>')
    out = []
    for f in files:
        src = "/media/" + quote(f)
        n = os.path.getsize(os.path.join(STORE, f))
        if os.path.splitext(f)[1].lower() in {".mov", ".mp4"}:
            # A poster frame needs ffmpeg; a glyph also stops the 4S from
            # pulling metadata for every clip in the grid.
            media = '<span class="play"></span>'
        else:
            media = '<img src="%s" alt="" loading="lazy">' % src
        out.append(
            '<figure class="tile" data-size="%d">'
            '<a class="thumb" href="%s">%s</a>'
            '<button class="del" data-name="%s" aria-label="Delete photo">&times;</button>'
            '<figcaption>%s</figcaption>'
            "</figure>" % (n, src, media, html.escape(f, quote=True), human(n))
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
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            order = "oldest" if (query.get("sort") or [""])[0] == "oldest" else "newest"
            files = media_files(order)
            used = sum(os.path.getsize(os.path.join(STORE, f)) for f in files)
            body = (PAGE.replace("__COUNT__", "%d item%s" % (
                len(files), "" if len(files) == 1 else "s"))
                       .replace("__TILES__", tiles(order))
                       .replace("__USED__", human(used))
                       .replace("__SORT_HREF__",
                                "/?sort=oldest" if order == "newest" else "/")
                       .replace("__SORT_LABEL__",
                                "Newest \u21c5" if order == "newest" else "Oldest \u21c5"))
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
                 ".woff": "font/woff", ".ttf": "font/ttf",
                 ".png": "image/png", ".ico": "image/x-icon"}.get(
                     ext, "application/octet-stream")
        with open(path, "rb") as fh:
            data = fh.read()
        # fonts cache for a day; no-cache on code: a cached app.js silently
        # disables client fixes on old browsers
        cache = "public, max-age=86400" if ext in (".woff", ".ttf", ".png", ".ico") else "no-cache"
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
        self.send_file(path, ctype)

    def send_file(self, path, ctype, cache="private, max-age=86400"):
        """One file, honouring Range.

        iOS will not play video from a server that answers 200 to a byte-range
        request; it probes with Range first and gives up otherwise. Serving a
        range also keeps a 300 MB clip out of memory when only seconds of it
        are wanted.
        """
        size = os.path.getsize(path)
        start, end, partial = 0, max(size - 1, 0), False
        rng = (self.headers.get("Range") or "").strip()
        m = re.match(r"bytes=(\d*)-(\d*)$", rng) if rng and "," not in rng else None
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))       # suffix: last N bytes
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True
        length = end - start + 1 if size else 0
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", cache)
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if self.command == "HEAD" or not length:
            return
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

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
