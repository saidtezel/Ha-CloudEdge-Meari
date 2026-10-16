"""A camera that has gone completely silent is reconnected within seconds.

Captured on a mains-powered HEVC camera: ~120 s into every session it stops
sending everything (media, audio, ICE replies) and never resumes, so waiting
out the 40 s source-idle budget only lengthens the freeze.
"""

from __future__ import annotations

import importlib
import unittest

from debug_tools.bootstrap import _bootstrap_integration_modules

_bootstrap_integration_modules()
SUPPORT = importlib.import_module(
    "custom_components.cloudplus.p2p_streamer.session_support"
)


class PeerGoneTests(unittest.TestCase):
    def test_total_silence_after_video_is_a_dead_peer(self):
        silent = SUPPORT.PEER_SILENT_RECONNECT_S
        self.assertTrue(SUPPORT.peer_gone(100.0 + silent, 100.0, 99.0, snap=False))
        self.assertFalse(
            SUPPORT.peer_gone(100.0 + silent - 0.5, 100.0, 99.0, snap=False)
        )

    def test_dead_peer_reconnect_beats_the_source_idle_budget(self):
        self.assertLess(
            SUPPORT.PEER_SILENT_RECONNECT_S, SUPPORT.DIRECT_SOURCE_IDLE_RECONNECT_S
        )

    def test_not_before_first_video_or_on_snap_cameras(self):
        self.assertFalse(SUPPORT.peer_gone(200.0, 100.0, 0.0, snap=False))
        self.assertFalse(SUPPORT.peer_gone(200.0, 0.0, 99.0, snap=False))
        self.assertFalse(SUPPORT.peer_gone(200.0, 100.0, 99.0, snap=True))


if __name__ == "__main__":
    unittest.main()
