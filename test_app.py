#!/usr/bin/env python3
"""Minimal checks: auth token, filename safety, and one live HTTP round trip."""
import http.client
import os
import tempfile
import threading
import unittest
import uuid

os.environ.setdefault("PIN", "2468")
os.environ.setdefault("STORE", tempfile.mkdtemp(prefix="pb-test-"))
os.environ.setdefault("PORT", "0")

import app  # noqa: E402  (env must be set first)


class Unit(unittest.TestCase):
    def test_token_roundtrip(self):
        self.assertTrue(app.valid_token(app.token()))

    def test_token_tamper_rejected(self):
        exp, mac = app.token().split(".")
        self.assertFalse(app.valid_token(exp + "." + "0" * 32))
        self.assertFalse(app.valid_token("not-a-token"))

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
        status, _, _ = self.req("GET", "/")
        self.assertEqual(status, 200)                     # login page
        status, _, _ = self.req("POST", "/upload", b"x")
        self.assertEqual(status, 403)                     # no cookie

        status, headers, _ = self.req(
            "POST", "/login", "pin=2468",
            {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"].split(";")[0]

        boundary = uuid.uuid4().hex
        payload = (
            "--%s\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"a.jpg\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n" % boundary
        ).encode() + b"\xff\xd8\xff\xd9" + ("\r\n--%s--\r\n" % boundary).encode()
        status, _, data = self.req(
            "POST", "/upload", payload,
            {"Content-Type": "multipart/form-data; boundary=" + boundary,
             "Cookie": cookie})
        self.assertEqual(status, 200, data)
        self.assertTrue(data.startswith(b'{"ok": true'))

        status, _, page = self.req("GET", "/", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"/media/a-", page)

        name = app.media_files()[0]
        status, _, _ = self.req("POST", "/delete", "name=" + name,
                                {"Content-Type": "application/x-www-form-urlencoded",
                                 "Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(app.media_files(), [])

    def test_delete_rejects_traversal(self):
        status, _, _ = self.req(
            "POST", "/delete", "name=../app.py",
            {"Content-Type": "application/x-www-form-urlencoded",
             "Cookie": app.COOKIE + "=" + app.token()})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
