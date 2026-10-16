"""The live session repeats the webrtcsvr `hello`, like the official app.

Captured on an Arenti HEVC camera: the official app re-sends `hello` on its
MsgSvr socket every ~21 s and streams past 120 s; a client that sends it only
once at setup loses the whole session 120 s after the SDP answer.
"""

from __future__ import annotations

import base64
import importlib
import json
import socket
import unittest

from debug_tools.bootstrap import _bootstrap_integration_modules

_bootstrap_integration_modules()
SIGNALING = importlib.import_module("custom_components.cloudplus.meari_signaling")
CODEC = importlib.import_module("custom_components.cloudplus.msgsvr_codec")
SUPPORT = importlib.import_module(
    "custom_components.cloudplus.p2p_streamer.session_support"
)


class WebrtcHelloKeepaliveTests(unittest.TestCase):
    def setUp(self):
        self.client_sock, self.server = socket.socketpair()
        self.server.settimeout(1.0)
        self.client = SIGNALING.MsgSvrClient("127.0.0.1")
        self.client.sock = self.client_sock
        self.client.uuid = "92d52b59cc17a22b"

    def tearDown(self):
        self.client_sock.close()
        self.server.close()

    def test_hello_matches_the_setup_hello_and_consumes_its_reply(self):
        reply = json.dumps(
            {"content": base64.b64encode(b'{"cmd":"hello","tag":"5510aa81"}').decode()}
        )
        self.server.sendall(
            CODEC.build_frame(CODEC.NODE_CLIENT, CODEC.METHOD_DIRECT, CODEC.CMD_WEBRTC, reply)
        )

        self.client.send_webrtc_hello(read_response=True)

        frame = CODEC.recv_frame(self.server)
        self.assertEqual(frame["cmd"], CODEC.CMD_WEBRTC)
        self.assertEqual(frame["method"], CODEC.METHOD_DIRECT)
        self.assertEqual(frame["json"]["node"], "webrtcsvr")
        self.assertEqual(frame["json"]["from"]["domain"], self.client.uuid)
        content = json.loads(base64.b64decode(frame["json"]["content"]))
        self.assertEqual(content, {"cmd": "hello"})
        self.client_sock.setblocking(False)
        with self.assertRaises(BlockingIOError):
            self.client_sock.recv(1)

    def test_hello_is_skipped_before_register(self):
        self.client.uuid = None
        self.client.send_webrtc_hello()
        self.server.setblocking(False)
        with self.assertRaises(BlockingIOError):
            self.server.recv(1)

    def test_interval_matches_the_official_app_and_beats_the_120s_cutoff(self):
        self.assertLessEqual(SUPPORT.WEBRTC_HELLO_S, 25.0)
        self.assertLess(SUPPORT.WEBRTC_HELLO_S * 2, 120.0)


if __name__ == "__main__":
    unittest.main()
