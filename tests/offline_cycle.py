"""Run the same review-cycle tests over in-memory HTTP when loopback is unavailable."""
import io
import json
import runpy
import sys
import unittest
from email.message import Message
from http.client import HTTPResponse
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from test_review import ROOT, ReviewCycle

sys.path.insert(0, str(ROOT))


class StreamConnection:
    def __init__(self, raw):
        self.input = io.BytesIO(raw)
        self.output = io.BytesIO()

    def makefile(self, *args):
        return self.input

    def sendall(self, raw):
        self.output.write(raw)


class OfflineCycle(ReviewCycle):
    def start(self):
        with patch("sys.argv", ["server.py", "--workspace", str(self.work), "--port", self.port]), \
             patch("http.server.ThreadingHTTPServer"):
            self.service = runpy.run_path(str(ROOT/"server.py"))
        self.addCleanup(self.stop)
        self.session = self.get("data")

    def stop(self):
        self.service["guard"].close()

    def open_request(self, req, timeout=None):
        if isinstance(req, str):
            req = Request(req)
        body = req.data or b""
        headers = Message()
        for name, value in req.header_items():
            headers[name] = value
        headers["Host"] = req.host
        headers["Content-Length"] = str(len(body))
        head = f"{req.get_method()} {req.selector} HTTP/1.0\r\n"
        head += "".join(f"{name}: {value}\r\n" for name, value in headers.items()) + "\r\n"
        connection = StreamConnection(head.encode() + body)
        self.service["Handler"](connection, ("127.0.0.1", 1), None)
        response = HTTPResponse(StreamConnection(connection.output.getvalue()))
        response.begin()
        if response.status >= 400:
            raise HTTPError(req.full_url, response.status, response.reason, response.headers, response)
        return response

    def get(self, route):
        with self.open_request(self.origin + "/api/" + route) as response:
            return json.load(response)

    def post(self, route, payload):
        with patch("test_review.urlopen", self.open_request):
            return super().post(route, payload)

    def cli(self, *args):
        output = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with patch("urllib.request.urlopen", self.open_request), patch("sys.stdout", output), \
             patch("sys.argv", ["agent.py", "--port", self.port, *args]):
            runpy.run_path(str(ROOT/"agent.py"))
            return json.loads(output.buffer.getvalue())


if __name__ == "__main__":
    unittest.main(defaultTest="OfflineCycle", verbosity=2)
