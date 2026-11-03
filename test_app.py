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

        name = app.media_files()[0]
        status, _, _ = self.req("POST", "/delete", "name=" + name,
                                {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200)
        self.assertEqual(app.media_files(), [])

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
