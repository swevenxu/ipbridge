#!/usr/bin/env python3
"""Minimal checks: filename safety, uploads that stream or refuse, and no login."""
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import uuid
import zipfile
from unittest import mock
from urllib.parse import quote

os.environ.setdefault("STORE", tempfile.mkdtemp(prefix="pb-test-"))
os.environ.setdefault("PORT", "0")

import app  # noqa: E402  (env must be set first)


class Unit(unittest.TestCase):
    def test_safe_name_strips_paths(self):
        self.assertEqual(app.safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(app.safe_name("my photo!.JPG"), "my_photo_.JPG")


class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def test_round_trip(self):
        status, _, page = self.req("GET", "/")
        self.assertEqual(status, 200)                     # gallery, no login
        self.assertIn(b"Add photos", page)

        boundary = uuid.uuid4().hex
        payload = (
            "--%s\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"a.jpg\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n" % boundary
        ).encode() + b"\xff\xd8\xff\xd9" + ("\r\n--%s--\r\n" % boundary).encode()
        status, _, data = self.req(
            "POST", "/upload", payload,
            {"Content-Type": "multipart/form-data; boundary=" + boundary})
        self.assertEqual(status, 200, data)
        self.assertTrue(data.startswith(b'{"ok": true'))

        status, _, page = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/media/a-", page)
        self.assertIn(b'data-size="', page)

        name = app.media_files()[0]
        status, _, _ = self.req("POST", "/delete", "name=" + name,
                                {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200)
        self.assertEqual(app.media_files(), [])

    def multipart(self, payload, boundary, filename="clip.mov"):
        head = ('--%s\r\nContent-Disposition: form-data; name="photo"; '
                'filename="%s"\r\nContent-Type: video/quicktime\r\n\r\n'
                % (boundary, filename)).encode()
        return head + payload + ("\r\n--%s--\r\n" % boundary).encode()

    def test_upload_streams_large_files_unchanged(self):
        boundary = uuid.uuid4().hex
        payload = bytearray(os.urandom(512 * 1024))
        payload[2000:2040] = b"\r\n--" + boundary[:-1].encode() + b"near-miss"
        payload = bytes(payload)                       # a boundary lookalike inside
        status, _, data = self.req(
            "POST", "/upload", self.multipart(payload, boundary),
            {"Content-Type": "multipart/form-data; boundary=" + boundary})
        self.assertEqual(status, 200, data)
        name = json.loads(data.decode())["saved"][0]
        try:
            self.assertEqual(os.path.getsize(os.path.join(app.STORE, name)),
                             len(payload))
            self.assertEqual(self.req("GET", "/media/" + quote(name))[2], payload)
        finally:
            os.remove(os.path.join(app.STORE, name))

    def test_truncated_upload_is_rejected(self):
        boundary = uuid.uuid4().hex
        payload = os.urandom(200000)
        body = self.multipart(payload, boundary)
        body = body[:-len(("\r\n--%s--\r\n" % boundary).encode())]
        before = set(app.media_files())
        status, head, data = self.req(
            "POST", "/upload", body,
            {"Content-Type": "multipart/form-data; boundary=" + boundary})
        self.assertEqual(status, 400)
        self.assertIn(b"incomplete", data)
        self.assertEqual(set(app.media_files()), before)   # no half-length file
        self.assertEqual(head.get("Connection"), "close")
        self.assertEqual(self.req("GET", "/")[0], 200)   # server still serving

    def test_chunked_upload(self):
        # iOS 9 Safari sends chunked, with no Content-Length to check.
        boundary = uuid.uuid4().hex
        payload = os.urandom(300000)
        body = self.multipart(payload, boundary)
        parts = [body[i:i + 65536] for i in range(0, len(body), 65536)]
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/upload", body=iter(parts),
                  headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                           "Transfer-Encoding": "chunked"},
                  encode_chunked=True)
        r = c.getresponse()
        data = r.read()
        c.close()
        self.assertEqual(r.status, 200, data)
        name = json.loads(data.decode())["saved"][0]
        try:
            with open(os.path.join(app.STORE, name), "rb") as fh:
                self.assertEqual(fh.read(), payload)
        finally:
            os.remove(os.path.join(app.STORE, name))

    def test_oversized_upload_is_refused_cleanly(self):
        boundary = uuid.uuid4().hex
        before = set(app.media_files())
        with mock.patch.object(app, "upload_limit", return_value=1024):
            status, head, data = self.req(
                "POST", "/upload", self.multipart(os.urandom(5000), boundary),
                {"Content-Type": "multipart/form-data; boundary=" + boundary})
        self.assertEqual(status, 413, data)
        self.assertEqual(head.get("Connection"), "close")
        self.assertEqual(set(app.media_files()), before)
        self.assertEqual(self.req("GET", "/")[0], 200)

    def test_media_supports_byte_ranges(self):
        # iOS refuses to play video when a range request comes back as 200.
        name = "clip-%s.mp4" % uuid.uuid4().hex[:8]
        data = bytes(range(256)) * 40                      # 10,240 bytes
        with open(os.path.join(app.STORE, name), "wb") as fh:
            fh.write(data)
        try:
            status, head, body = self.req("GET", "/media/" + name,
                                          headers={"Range": "bytes=100-199"})
            self.assertEqual(status, 206)
            self.assertEqual(head.get("Content-Range"),
                             "bytes 100-199/%d" % len(data))
            self.assertEqual(head.get("Accept-Ranges"), "bytes")
            self.assertEqual(body, data[100:200])

            status, _, body = self.req("GET", "/media/" + name,
                                       headers={"Range": "bytes=10200-"})
            self.assertEqual(status, 206)
            self.assertEqual(body, data[10200:])

            self.assertEqual(
                self.req("GET", "/media/" + name,
                         headers={"Range": "bytes=99999-"})[0], 416)

            status, _, body = self.req("GET", "/media/" + name)
            self.assertEqual(status, 200)
            self.assertEqual(body, data)                   # whole file, untouched
        finally:
            os.remove(os.path.join(app.STORE, name))

    def test_sort_toggle(self):
        old = os.path.join(app.STORE, "old-%s.jpg" % uuid.uuid4().hex[:8])
        new = os.path.join(app.STORE, "new-%s.jpg" % uuid.uuid4().hex[:8])
        for path in (old, new):
            with open(path, "wb") as fh:
                fh.write(b"x")
        os.utime(old, (1000, 1000))                     # old is older
        try:
            _, _, page = self.req("GET", "/")
            self.assertLess(page.index(os.path.basename(new).encode()),
                            page.index(os.path.basename(old).encode()))
            self.assertIn(b'href="/?sort=oldest"', page)

            _, _, page = self.req("GET", "/?sort=oldest")
            self.assertLess(page.index(os.path.basename(old).encode()),
                            page.index(os.path.basename(new).encode()))
        finally:
            for path in (old, new):
                os.remove(path)

    def test_download_and_streamed_zip(self):
        name = "dl-%s.jpg" % uuid.uuid4().hex[:8]
        data = b"\xff\xd8\xff\xd9payload-bytes"
        with open(os.path.join(app.STORE, name), "wb") as fh:
            fh.write(data)
        try:
            status, head, body = self.req("GET", "/download/" + name)
            self.assertEqual(status, 200)
            self.assertIn("attachment", head.get("Content-Disposition", ""))
            self.assertEqual(body, data)                   # untouched bytes

            status, head, body = self.req("GET", "/download-all")
            self.assertEqual(status, 200)
            self.assertEqual(head.get("Content-Type"), "application/zip")
            self.assertIn("attachment", head.get("Content-Disposition", ""))
            bundle = zipfile.ZipFile(io.BytesIO(body))
            self.assertIn(name, bundle.namelist())
            self.assertEqual(bundle.read(name), data)
        finally:
            os.remove(os.path.join(app.STORE, name))

    def test_delete_all_needs_the_word(self):
        keep = os.path.join(app.STORE, "keep-%s.jpg" % uuid.uuid4().hex[:8])
        with open(keep, "wb") as fh:
            fh.write(b"x")
        try:
            status, _, _ = self.req(
                "POST", "/delete-all", "confirm=yes",
                {"Content-Type": "application/x-www-form-urlencoded"})
            self.assertEqual(status, 400)
            self.assertTrue(os.path.exists(keep))

            status, _, body = self.req(
                "POST", "/delete-all", "confirm=DELETE",
                {"Content-Type": "application/x-www-form-urlencoded"})
            self.assertEqual(status, 200)
            self.assertIn(b'"deleted"', body)
            self.assertEqual(app.media_files(), [])
        finally:
            if os.path.exists(keep):
                os.remove(keep)

    def test_delete_rejects_traversal(self):
        status, _, _ = self.req(
            "POST", "/delete", "name=../app.py",
            {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 400)

    def test_login_routes_are_gone(self):
        self.assertEqual(self.req("GET", "/login")[0], 404)
        self.assertEqual(self.req("GET", "/logout")[0], 404)
        self.assertEqual(self.req("POST", "/login", "pin=2468",
                                  {"Content-Type": "application/x-www-form-urlencoded"})[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
