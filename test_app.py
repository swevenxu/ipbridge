#!/usr/bin/env python3
"""Minimal checks: filename safety, the upload round trip, and no login."""
import http.client
import os
import tempfile
import threading
import unittest
import uuid

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
