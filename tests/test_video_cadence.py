"""Live video PTS follows the camera's own frame cadence."""

from __future__ import annotations

import importlib
import unittest
from unittest.mock import Mock

from debug_tools.bootstrap import _bootstrap_integration_modules

MODULES = _bootstrap_integration_modules()
COORDINATOR = MODULES["coordinator"].CloudEdgeMeariCoordinator
CADENCE = importlib.import_module(
    "custom_components.cloudplus.coordinator.video_cadence"
)
MUXER = importlib.import_module("custom_components.cloudplus.coordinator.muxer")
CODECS = importlib.import_module("custom_components.cloudplus.p2p_streamer.codecs")

VideoCadence = CADENCE.VideoCadence


def coordinator():
    coord = COORDINATOR(Mock(), "user@example.invalid", "x", {"_category": "ipc"})
    coord._video_codec = CODECS.CodecName.HEVC
    return coord


def camera_timestamps(count, step_ms=100, start=5_000, drop_every=0):
    stamps = []
    for idx in range(count):
        if drop_every and idx % drop_every == drop_every - 1:
            continue
        stamps.append(start + idx * step_ms)
    return stamps


class VideoCadenceTests(unittest.TestCase):
    def test_follows_camera_steps(self):
        cadence = VideoCadence()
        intervals = [cadence.interval_for(ts) for ts in camera_timestamps(10)]
        self.assertIsNone(intervals[0])
        self.assertEqual(intervals[1:], [0.1] * 9)
        self.assertAlmostEqual(cadence.nominal_interval_s, 0.1)

    def test_dropped_frame_keeps_its_slot(self):
        cadence = VideoCadence()
        stamps = camera_timestamps(40, drop_every=4)
        total = sum(cadence.interval_for(ts) or 0.0 for ts in stamps)
        self.assertAlmostEqual(total, (stamps[-1] - stamps[0]) / 1000.0)
        self.assertAlmostEqual(cadence.nominal_interval_s, 0.1)

    def test_wraps_32_bit_timestamps(self):
        cadence = VideoCadence()
        cadence.interval_for(0xFFFFFFFF - 49)
        self.assertAlmostEqual(cadence.interval_for(50), 0.1)

    def test_jumps_use_learned_cadence(self):
        cadence = VideoCadence()
        for ts in camera_timestamps(5):
            cadence.interval_for(ts)
        # Stall, backward jump, repeated stamp, missing stamp.
        self.assertAlmostEqual(cadence.interval_for(60_000), 0.1)
        self.assertAlmostEqual(cadence.interval_for(1_000), 0.1)
        self.assertAlmostEqual(cadence.interval_for(1_000), 0.1)
        self.assertAlmostEqual(cadence.interval_for(None), 0.1)
        self.assertAlmostEqual(cadence.interval_for(1_100), 0.1)
        self.assertAlmostEqual(cadence.nominal_interval_s, 0.1)

    def test_reset_keeps_cadence_clear_forgets_it(self):
        cadence = VideoCadence()
        for ts in camera_timestamps(5):
            cadence.interval_for(ts)
        cadence.reset()
        self.assertAlmostEqual(cadence.interval_for(900_000), 0.1)
        cadence.clear()
        self.assertIsNone(cadence.interval_for(900_100))
        self.assertIsNone(cadence.nominal_interval_s)


class CoordinatorIntervalTests(unittest.TestCase):
    def test_hevc_uses_camera_timestamps_fixed_and_auto(self):
        for quality in (1, None):
            coord = coordinator()
            coord._vvp_quality = quality
            got = [
                coord._next_live_video_interval(0.0, ts)
                for ts in camera_timestamps(4)
            ]
            self.assertEqual(got, [None, 0.1, 0.1, 0.1], quality)

    def test_reconnect_does_not_jump_pts(self):
        coord = coordinator()
        coord._vvp_quality = 1
        for ts in camera_timestamps(5):
            coord._next_live_video_interval(0.0, ts)
        coord._reset_live_mux_timing()
        self.assertAlmostEqual(coord._next_live_video_interval(0.0, 7), 0.1)


class MuxerPtsTests(unittest.TestCase):
    def test_pts_tracks_wall_clock_at_10_fps(self):
        """The crib-camera case: 10 fps HEVC used to be stamped at 15 fps."""
        coord = coordinator()
        coord._vvp_quality = 1
        muxer = MUXER.FfmpegMuxer(lambda _ts: None)
        stamps = camera_timestamps(300, drop_every=5)
        pts = []
        for ts in stamps:
            interval = coord._next_live_video_interval(0.0, ts)
            muxer._queue_video_time(0.0, interval)
            pts.append(muxer._next_video_pts())
            muxer._last_video_pts = muxer._current_video_pts
        span_s = (pts[-1] - pts[0]) / 90000
        self.assertAlmostEqual(span_s, (stamps[-1] - stamps[0]) / 1000.0, places=2)

    def test_pts_stays_monotonic_across_restart(self):
        coord = coordinator()
        coord._vvp_quality = 1
        muxer = MUXER.FfmpegMuxer(lambda _ts: None)
        last = -1
        for session in range(3):
            muxer._last_video_pts = None  # what start() does on reconnect
            coord._reset_live_mux_timing()
            for ts in camera_timestamps(20, start=session * 777_777):
                muxer._queue_video_time(0.0, coord._next_live_video_interval(0.0, ts))
                pts = muxer._next_video_pts()
                muxer._last_video_pts = pts
                self.assertGreater(pts, last)
                last = pts


if __name__ == "__main__":
    unittest.main()
