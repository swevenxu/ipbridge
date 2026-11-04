#!/usr/bin/env python3
"""Photo Bridge — a tiny photo exchange for a phone that can only do plain HTTP.

Runs on the Python standard library alone. One file of state: the uploads dir.

    python3 app.py                     # http://<host>:8080
"""
import html
import os
import re
import shutil
import tempfile
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("STORE", os.path.join(ROOT, "uploads"))
THUMBS = os.path.join(STORE, "thumbs")   # grid-sized copies; the gallery never loads full files
STATIC = os.path.join(ROOT, "static")
# Panels (Pterodactyl-style) inject SERVER_PORT instead of PORT.
PORT = int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or 8080)


MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".mov", ".mp4"}
DISK_RESERVE = 256 * 1024 * 1024    # never let one upload fill the volume

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


CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".heic": "image/heic", ".mov": "video/quicktime",
    ".mp4": "video/mp4",
}


def content_type(name: str) -> str:
    return CONTENT_TYPES.get(os.path.splitext(name)[1].lower(),
                             "application/octet-stream")


class Chunked:
    """Tiny chunked encoder: a zip can stream without knowing its length."""

    def __init__(self, wfile):
        self.wfile = wfile

    def write(self, data):
        if not data:
            return 0
        self.wfile.write(b"%x\r\n" % len(data))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        return len(data)

    def flush(self):
        self.wfile.flush()


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def upload_limit():
    """Largest single upload, in bytes.

    The free space on the volume, less a reserve, so one clip can never be the
    thing that fills the disk and takes the app down with it. MAX_MB, when set,
    is still a ceiling on top of that.
    """
    try:
        free = shutil.disk_usage(STORE).free
    except OSError:
        free = 0
    room = max(0, free - DISK_RESERVE)
    hard = int(os.environ.get("MAX_MB") or 0) * 1024 * 1024
    return min(room, hard) if hard else room


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
      <input id="picker" type="file" name="photo" accept="image/*,video/*" multiple data-max="__MAX__">
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
    <span class="tools">
      <a class="tool" id="dlall" href="/download-all">Download all</a>
      <button class="tool danger" id="remove-all" type="button">Remove all</button>
    </span>
    <span class="used">__USED__ used</span>
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


class TooLarge(Exception):
    pass


class BadUpload(Exception):
    pass


class RequestBody:
    """The request body as a stream, chunked or not.

    iOS 9 Safari uploads with Transfer-Encoding: chunked; a reader that trusts
    Content-Length alone sees an empty body there. read() returns b"" at the
    end either way, and counts what it handed out.
    """

    CHUNK = 65536

    def __init__(self, rfile, headers):
        self.rfile = rfile
        self.chunked = "chunked" in (headers.get("Transfer-Encoding") or "").lower()
        self.left = None if self.chunked else int(headers.get("Content-Length") or 0)
        self.in_chunk = 0
        self.finished = False
        self.count = 0

    def _next_chunk(self):
        line = self.rfile.readline(1024)
        if not line:
            self.finished = True
            return
        try:
            size = int(line.strip().split(b";")[0] or b"0", 16)
        except ValueError:
            self.finished = True
            return
        if size == 0:
            while True:                              # trailers, then the end
                line = self.rfile.readline(1024)
                if line in (b"\r\n", b"\n", b""):
                    break
            self.finished = True
            return
        self.in_chunk = size

    def read(self, size=CHUNK):
        if self.finished:
            return b""
        if self.chunked:
            if not self.in_chunk:
                self._next_chunk()
                if self.finished:
                    return b""
            data = self.rfile.read(min(size, self.in_chunk))
            if not data:
                self.finished = True
                return b""
            self.in_chunk -= len(data)
            if not self.in_chunk:
                self.rfile.read(2)                   # the CRLF closing the chunk
        else:
            if not self.left:
                self.finished = True
                return b""
            data = self.rfile.read(min(size, self.left))
            if not data:
                self.finished = True
                return b""
            self.left -= len(data)
        self.count += len(data)
        return data

    def drain(self, cap=65536):
        total = 0
        while total < cap:
            data = self.read()
            if not data:
                return
            total += len(data)


class _Stream:
    """Buffered reader for a multipart body: one chunk plus a boundary at most."""

    def __init__(self, body):
        self.body = body
        self.buf = bytearray()

    def more(self):
        data = self.body.read()
        if not data:
            return False
        self.buf += data
        return True

    def need(self, n):
        while len(self.buf) < n:
            if not self.more():
                raise BadUpload("body ended early")

    def until(self, marker, cap=65536):
        while True:
            i = self.buf.find(marker)
            if i >= 0:
                out = bytes(self.buf[:i])
                del self.buf[:i + len(marker)]
                return out
            if len(self.buf) > cap:
                raise BadUpload("header too long")
            if not self.more():
                raise BadUpload("body ended early")


class _Sink:
    """Where a non-file part goes: nowhere, but the bytes still have to move."""

    written = 0

    def write(self, data):
        self.written += len(data)
        return len(data)

    def close(self):
        pass


def _part_filename(headers):
    m = re.search(r'filename="([^"]*)"', headers, re.I)
    if not m:
        m = re.search(r"filename=([^;\s]+)", headers, re.I)
    return m.group(1) if m else None


def _stored_name(stem, ext):
    base = "%s-%s" % (stem or "photo", time.strftime("%Y%m%d-%H%M%S"))
    name, n = base + ext, 2
    while os.path.exists(os.path.join(STORE, name)):
        name = "%s-%d%s" % (base, n, ext)
        n += 1
    return name


def receive(body, boundary, limit):
    """Write the file parts straight to disk; return the names stored.

    Nothing bigger than a chunk and a boundary is held in memory, so a 2 GB
    clip costs the disk, not the RAM. A body that ends before the closing
    boundary raises BadUpload: partial bytes are never promoted to a file.
    """
    s = _Stream(body)
    first = b"--" + boundary
    while True:                                      # skip the preamble
        i = s.buf.find(first)
        if i >= 0:
            del s.buf[:i + len(first)]
            break
        if len(s.buf) > 65536 or not s.more():
            raise BadUpload("no boundary")
    mark = b"\r\n--" + boundary
    saved = []
    while True:
        s.need(2)
        after = bytes(s.buf[:2])
        del s.buf[:2]
        if after == b"--":
            break                                    # closing boundary
        if after != b"\r\n":
            raise BadUpload("bad boundary line")
        s.need(2)
        if bytes(s.buf[:2]) == b"\r\n":
            del s.buf[:2]
            headers = ""
        else:
            headers = s.until(b"\r\n\r\n", 16384).decode("latin-1")

        name = _part_filename(headers)
        base = safe_name(name) if name else ""
        stem, ext = os.path.splitext(base)
        target = tmp = None
        if name and ext.lower() in MEDIA:
            target = _stored_name(stem, ext.lower())
            fd, tmp = tempfile.mkstemp(dir=STORE, prefix=".incoming-")
            out = os.fdopen(fd, "wb")
        else:
            out = _Sink()
        written = 0
        try:
            while True:
                i = s.buf.find(mark)
                if i >= 0:
                    if limit and written + i > limit:
                        raise TooLarge()
                    out.write(bytes(s.buf[:i]))
                    written += i
                    del s.buf[:i + len(mark)]
                    break
                keep = len(mark)
                if len(s.buf) > keep:
                    take = len(s.buf) - keep
                    if limit and written + take > limit:
                        raise TooLarge()
                    out.write(bytes(s.buf[:take]))
                    written += take
                    del s.buf[:take]
                if not s.more():
                    raise BadUpload("body ended inside a file")
        except Exception:
            out.close()
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise
        out.close()
        if tmp and written:
            os.replace(tmp, os.path.join(STORE, target))
            saved.append(target)
        elif tmp:
            os.remove(tmp)                           # an empty pick is not a file
    return saved


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
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

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
                                "Newest \u21c5" if order == "newest" else "Oldest \u21c5")
                       .replace("__MAX__", str(upload_limit())))
            return self.send_body(200, "text/html; charset=utf-8", body.encode())
        if path.startswith("/media/"):
            return self.media(unquote(path[len("/media/"):]))
        if path == "/download-all":
            return self.download_all()
        if path.startswith("/download/"):
            return self.download(unquote(path[len("/download/"):]))
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
        self.send_file(path, content_type(safe))

    def download(self, name):
        safe = safe_name(name)
        path = os.path.join(STORE, safe)
        if safe != name or not os.path.isfile(path):
            return self.send_body(404, "text/plain", b"not found")
        # attachment: the laptop saves it, modern iOS puts it in Files. Safari on
        # iOS 9 has no save sheet for video, so there it just plays.
        self.send_file(path, content_type(safe), attachment=safe)

    def download_all(self):
        """One zip, streamed: no temp file, no disk doubling, no compression.

        JPEG and H.264 are already packed, so ZIP_STORED costs almost no CPU;
        the length is unknown up front, so the body goes out chunked.
        """
        files = media_files()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="114s-%s.zip"'
                         % time.strftime("%Y%m%d-%H%M%S"))
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with zipfile.ZipFile(Chunked(self.wfile), "w",
                                 zipfile.ZIP_STORED) as bundle:
                for f in files:
                    bundle.write(os.path.join(STORE, f), arcname=f)
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def send_file(self, path, ctype, cache="private, max-age=86400",
                  attachment=None):
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
        if attachment:
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % attachment)
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
        if path == "/delete-all":
            return self.delete_all()
        return self.send_body(404, "text/plain", b"not found")

    def upload(self):
        ctype = self.headers.get("Content-Type") or ""
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if "multipart/form-data" not in ctype or not m:
            self.close_connection = True
            return self.json(400, {"ok": False, "error": "expected multipart"}, close=True)
        limit = upload_limit()
        started = time.time()
        known = int(self.headers.get("Content-Length") or 0)
        if known and known > limit:
            # Refuse before reading: draining gigabytes just to say no wastes
            # the phone's uplink and its battery. The page checks the same
            # number before it starts, so this is the backstop.
            self.close_connection = True
            self.log_message("upload refused: content-length %d over limit %d",
                             known, limit)
            return self.json(413, {"ok": False, "error": "too large",
                                   "limit": limit}, close=True)
        body = RequestBody(self.rfile, self.headers)
        try:
            saved = receive(body, m.group(1).encode("latin-1", "replace"), limit)
        except TooLarge:
            self.close_connection = True
            self.log_message("upload refused mid-body after %d bytes (limit %d)",
                             body.count, limit)
            return self.json(413, {"ok": False, "error": "too large",
                                   "limit": limit}, close=True)
        except (BadUpload, OSError) as exc:
            # A dropped connection lands here. Nothing partial is kept, and the
            # connection is closed rather than left holding half a request.
            self.close_connection = True
            self.log_message("upload incomplete after %d bytes: %s", body.count, exc)
            return self.json(400, {"ok": False,
                                   "error": "upload incomplete, nothing was saved"},
                             close=True)
        body.drain(65536)                            # trailing CRLF, chunk end
        if not body.finished:
            self.close_connection = True
        self.log_message("upload ok: %d bytes, %s, %.1fs, %d file(s)",
                         body.count, "chunked" if body.chunked else "content-length",
                         time.time() - started, len(saved))
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

    def delete_all(self):
        """Permanent, and gated on typing the word: there is no login here, so
        the only thing between the archive and a stray tap is that prompt."""
        if (self.form().get("confirm") or [""])[0] != "DELETE":
            return self.json(400, {"ok": False, "error": "type DELETE to confirm"})
        gone = 0
        for name in media_files():
            try:
                os.remove(os.path.join(STORE, name))
                gone += 1
            except OSError:
                pass
        return self.json(200, {"ok": True, "deleted": gone})

    def json(self, code, obj, close=False):
        import json as _json
        self.send_body(code, "application/json", _json.dumps(obj).encode(),
                       {"Connection": "close"} if close else None)


if __name__ == "__main__":
    if not os.path.isdir(STATIC):
        raise SystemExit("missing static/ next to app.py")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("114s on http://0.0.0.0:%d — store %s" % (PORT, STORE), flush=True)
    srv.serve_forever()
