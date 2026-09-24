import http.client
import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import core

# Importing the proxy must not load a developer's real config.env or credentials.
with patch.object(core, "load_dotenv"), patch.dict(os.environ, {}, clear=True):
    import proxy


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.received = (self.path, dict(self.headers), raw)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.server.received = (self.path, dict(self.headers), b"")
        if self.path.endswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            data = b"data: first\n\n"
            self.wfile.write(b"%X\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()
            self.server.release.wait(3)
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass
        else:
            self.send_response(204)
            self.send_header("Connection", "X-Private")
            self.send_header("X-Private", "must-not-forward")
            self.end_headers()

    def do_HEAD(self):
        self.server.received = (self.path, dict(self.headers), b"")
        self.send_response(200)
        self.send_header("Content-Length", "123")
        self.end_headers()


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        self.upstream.release = threading.Event()
        self.upstream.received = None
        self.frontend = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
        for server in (self.upstream, self.frontend):
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
        self.patches = [
            patch.object(proxy, "UPSTREAM", f"http://127.0.0.1:{self.upstream.server_port}/v1"),
            patch.object(proxy, "log"),
            patch.object(proxy, "enhance", return_value=core.EnhanceResult(True, "improved draft")),
        ]
        for item in self.patches:
            item.start()
        self.client = http.client.HTTPConnection("127.0.0.1", self.frontend.server_port, timeout=1)

    def tearDown(self):
        self.upstream.release.set()
        self.client.close()
        for server in (self.frontend, self.upstream):
            server.shutdown()
            server.server_close()
        for item in reversed(self.patches):
            item.stop()

    def test_chat_rewrite_preserves_query_and_one_version_prefix(self):
        body = {"messages": [{"role": "user", "content": "original draft"}]}
        self.client.request("POST", "/v1/chat/completions?trace=1", body=json.dumps(body),
                            headers={"Content-Type": "application/json"})
        response = self.client.getresponse()
        result = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(self.upstream.received[0], "/v1/chat/completions?trace=1")
        self.assertEqual(result["messages"][0]["content"], "improved draft")

    def test_disabled_enhancement_preserves_body_and_drops_private_headers(self):
        original = b'{"input":"original draft"}'
        self.client.request("POST", "/v1/responses", body=original,
                            headers={"X-Enhancer": "off", "Connection": "X-Private", "X-Private": "secret"})
        self.assertEqual(self.client.getresponse().read(), original)
        headers = {key.lower(): value for key, value in self.upstream.received[1].items()}
        self.assertNotIn("x-enhancer", headers)
        self.assertNotIn("x-private", headers)
        proxy.enhance.assert_not_called()

    def test_sse_first_event_arrives_before_upstream_closes(self):
        self.client.request("GET", "/v1/stream")
        response = self.client.getresponse()
        self.assertEqual(response.getheader("Transfer-Encoding"), "chunked")
        self.assertEqual(response.read(len(b"data: first\n\n")), b"data: first\n\n")
        self.upstream.release.set()
        self.assertEqual(response.read(), b"")

    def test_empty_response_does_not_get_chunked_body_or_hop_headers(self):
        self.client.request("GET", "/v1/models")
        response = self.client.getresponse()
        self.assertEqual(response.status, 204)
        self.assertIsNone(response.getheader("Transfer-Encoding"))
        self.assertIsNone(response.getheader("X-Private"))
        self.assertEqual(response.read(), b"")

    def test_head_preserves_content_length_without_body(self):
        self.client.request("HEAD", "/v1/models")
        response = self.client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Length"), "123")
        self.assertEqual(response.read(), b"")

    def test_invalid_length_is_rejected_and_connection_closed(self):
        self.client.request("POST", "/v1/responses", body=None, headers={"Content-Length": "-1"})
        response = self.client.getresponse()
        self.assertEqual(response.status, 400)
        self.assertEqual(response.getheader("Connection"), "close")
        response.read()
        self.assertIsNone(self.upstream.received)

    def test_duplicate_content_length_is_rejected(self):
        with socket.create_connection(("127.0.0.1", self.frontend.server_port), 1) as client:
            client.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n")
            response = http.client.HTTPResponse(client)
            response.begin()
            self.assertEqual(response.status, 400)
            response.read()
        self.assertIsNone(self.upstream.received)

    def test_chunked_request_has_explicit_error_instead_of_empty_forward(self):
        self.client.request("POST", "/v1/responses", body=None, headers={"Transfer-Encoding": "chunked"})
        response = self.client.getresponse()
        self.assertEqual(response.status, 501)
        response.read()
        self.assertIsNone(self.upstream.received)

    def test_too_large_request_is_rejected_before_reading(self):
        self.client.request("POST", "/v1/responses", body=None,
                            headers={"Content-Length": str(proxy.MAX_BODY_BYTES + 1)})
        response = self.client.getresponse()
        self.assertEqual(response.status, 413)
        response.read()

    def test_path_join_handles_custom_gateway_prefix(self):
        self.assertEqual(proxy._target_path("/gateway/v1", "/v1/responses?a=1"), "/gateway/v1/responses?a=1")
        self.assertEqual(proxy._target_path("/v1", "/responses"), "/v1/responses")


if __name__ == "__main__":
    unittest.main()
