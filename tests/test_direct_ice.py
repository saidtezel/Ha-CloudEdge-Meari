"""The camera's direct ICE checks are answered from the moment it answers.

Unanswered through trickle collection and TURN setup, the camera gives up on
the direct pair and streams over its relay. Real loopback sockets: the
"camera" is 127.0.0.1, the TURN server 127.0.0.2.
"""

from __future__ import annotations

import importlib
import socket
import threading
import time
import unittest

from debug_tools.bootstrap import _bootstrap_integration_modules

_bootstrap_integration_modules()
TURN = importlib.import_module("custom_components.cloudplus.turn_client")
NETWORK = importlib.import_module("custom_components.cloudplus.p2p_streamer.network")
SDP = importlib.import_module("custom_components.cloudplus.p2p_streamer.sdp")

TURN_SERVER = "127.0.0.2"


def camera_check() -> bytes:
    return TURN.build_ice_binding_request(
        local_ufrag="cam1", remote_ufrag="ha01", remote_pwd="secret", use_candidate=False
    )


class DirectIceTests(unittest.TestCase):
    def setUp(self):
        self.camera = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.camera.bind(("127.0.0.1", 0))
        self.camera.settimeout(1.0)
        self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server.bind((TURN_SERVER, 0))
        self.server.settimeout(1.0)
        self.turn = TURN.TurnClient(TURN_SERVER, self.server.getsockname()[1], "u", "p")
        self.turn.connect()
        self.turn_addr = ("127.0.0.1", self.turn.local_port)
        self.answered: list[tuple[str, int]] = []

        def answer(request, peer):
            self.answered.append(peer)
            self.turn.send_direct(*peer, NETWORK._build_ice_response(request, "pw", *peer))

        self.answer = answer

    def tearDown(self):
        self.turn.close()
        self.camera.close()
        self.server.close()

    def assert_camera_answered(self, request: bytes) -> None:
        reply = TURN._parse_stun(self.camera.recv(2048))
        self.assertEqual(reply["type"], TURN.BINDING_RESPONSE)
        self.assertEqual(reply["txn_id"], TURN._parse_stun(request)["txn_id"])

    def test_queued_checks_answered_and_other_packets_kept(self):
        first, second = camera_check(), camera_check()
        self.camera.sendto(first, self.turn_addr)
        self.camera.sendto(second, self.turn_addr)
        self.camera.sendto(b"not stun", self.turn_addr)
        time.sleep(0.05)

        NETWORK.answer_queued_ice_checks(self.turn, self.answer)

        self.assert_camera_answered(first)
        self.assert_camera_answered(second)
        self.assertEqual(len(self.answered), 2)
        self.turn.sock.settimeout(1.0)
        self.assertEqual(self.turn.sock.recvfrom(2048)[0], b"not stun")

    def test_checks_answered_while_a_turn_request_waits(self):
        request = camera_check()

        def turn_server():
            data, addr = self.server.recvfrom(2048)
            # The camera's check lands while CreatePermission is in flight.
            self.camera.sendto(request, self.turn_addr)
            time.sleep(0.05)
            txn = TURN._parse_stun(data)["txn_id"]
            self.server.sendto(TURN._build_stun(0x0108, b"", txn)[0], addr)

        thread = threading.Thread(target=turn_server)
        thread.start()
        self.turn.on_ice_request = self.answer
        self.assertTrue(self.turn.create_permission("127.0.0.1"))
        thread.join()

        self.assert_camera_answered(request)


class _Signaling:
    """Signaling stand-in: one trickle message, then silence."""

    def __init__(self):
        self.sock, self._peer = socket.socketpair()

    def send_trickle(self):
        self._peer.send(b"x")

    def recv_webrtc_content(self):
        self.sock.recv(1)
        return {}

    def close(self):
        self.sock.close()
        self._peer.close()


class TrickleTests(unittest.TestCase):
    def test_checks_serviced_while_waiting_for_trickle(self):
        sig = _Signaling()
        self.addCleanup(sig.close)
        sig.send_trickle()
        polls = []
        started = time.monotonic()

        SDP.collect_trickle_candidates(sig, [], idle=lambda: polls.append(1))

        elapsed = time.monotonic() - started
        self.assertGreater(len(polls), 10)
        self.assertGreaterEqual(elapsed, SDP.TRICKLE_QUIET_S)
        self.assertLess(elapsed, SDP.TRICKLE_QUIET_S + 0.4)

    def test_without_idle_waits_the_same(self):
        sig = _Signaling()
        self.addCleanup(sig.close)
        started = time.monotonic()
        SDP.collect_trickle_candidates(sig, [])
        self.assertLess(time.monotonic() - started, SDP.TRICKLE_QUIET_S + 0.3)


if __name__ == "__main__":
    unittest.main()
