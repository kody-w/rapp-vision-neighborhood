#!/usr/bin/env python3
"""Offline, fixture-driven tests. No network, no clock dependence.

Every test here answers a question that cost something to learn:

  * idempotency - the second run of a day must not grow a second entry. This
    is the property launchd will attack: a double-fire, a retry after a
    timeout, a manual run while the scheduled one is mid-flight.
  * freshness - the sentinel's whole purpose. The check has to fail on a
    channel that serves 200 and stopped posting, which is exactly the case a
    naive "is it up" check passes.
  * transient tolerance - a single 503 must not become a published accusation.
    The first two live runs of the tumbler producer did exactly that, and five
    manual probes immediately after returned 206 every time.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "producers"))

import rvn_common as C          # noqa: E402
import tumbler_producer as TP   # noqa: E402


def iso_ago(hours):
    t = datetime.now(timezone.utc) - timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


class UpsertIdempotency(unittest.TestCase):
    """upsert_video is the whole idempotency story - so hammer it."""

    def test_second_upsert_same_id_does_not_duplicate(self):
        doc = {"videos": []}
        v1 = {"id": "tumble-2026-08-04", "title": "first"}
        self.assertEqual(C.upsert_video(doc, v1), "added")
        self.assertEqual(len(doc["videos"]), 1)

        v2 = {"id": "tumble-2026-08-04", "title": "second"}
        self.assertEqual(C.upsert_video(doc, v2), "updated")
        self.assertEqual(len(doc["videos"]), 1, "a same-day rerun grew a duplicate")
        self.assertEqual(doc["videos"][0]["title"], "second")

    def test_identical_upsert_reports_unchanged(self):
        """An unchanged rerun must be distinguishable from an edit, or the
        head-publish throttle has nothing to throttle on."""
        doc = {"videos": []}
        v = {"id": "guide-2026-08-04", "title": "x"}
        C.upsert_video(doc, v)
        self.assertEqual(C.upsert_video(doc, dict(v)), "unchanged")
        self.assertEqual(len(doc["videos"]), 1)

    def test_different_day_appends_newest_first(self):
        doc = {"videos": []}
        C.upsert_video(doc, {"id": "tumble-2026-08-03"})
        C.upsert_video(doc, {"id": "tumble-2026-08-04"})
        self.assertEqual([v["id"] for v in doc["videos"]],
                         ["tumble-2026-08-04", "tumble-2026-08-03"])

    def test_upsert_survives_a_hundred_reruns(self):
        """The launchd nightmare: the same job firing over and over."""
        doc = {"videos": []}
        for i in range(100):
            C.upsert_video(doc, {"id": "tumble-2026-08-04", "n": i})
        self.assertEqual(len(doc["videos"]), 1)
        self.assertEqual(doc["videos"][0]["n"], 99)


class Freshness(unittest.TestCase):
    """The invariant the sentinel exists for."""

    def test_hours_since_reads_both_forms_we_emit(self):
        self.assertAlmostEqual(C.hours_since(iso_ago(5)), 5, places=1)
        self.assertAlmostEqual(
            C.hours_since((datetime.now(timezone.utc) - timedelta(hours=3))
                          .isoformat().replace("+00:00", "Z")), 3, places=1)

    def test_hours_since_is_none_on_garbage(self):
        for bad in (None, "", "not-a-date", 12345):
            self.assertIsNone(C.hours_since(bad), "accepted %r as a date" % (bad,))

    def test_a_channel_that_serves_but_stopped_posting_is_stale(self):
        """The nineteen-day failure, in one assertion.

        The document is perfectly valid and would return 200 forever. The only
        thing wrong with it is that nothing new has been added, which is
        exactly what an HTTP check cannot see.
        """
        doc = {"videos": [{"id": "old", "published": "2026-01-01"}],
               "_generated": iso_ago(72)}
        age = C.hours_since(doc["_generated"])
        self.assertGreater(age, 48, "a 72h-old channel must read as stale")

    def test_a_fresh_channel_passes(self):
        doc = {"_generated": iso_ago(2)}
        self.assertLess(C.hours_since(doc["_generated"]), 48)


class HashGate(unittest.TestCase):
    """Fire-on-difference. The tumbler must be silent on a quiet day."""

    def _facts(self, apps, media=()):
        return {"utc": iso_ago(0), "channels": 2, "channel_ids": ["a", "b"],
                "unreachable_channels": [], "videos": 4, "live_videos": 2,
                "apps": [{"url": u, "status": 200, "sha": s, "bytes": 100,
                          "driven_by": ["a/v"]} for u, s in apps],
                "media": [{"url": u, "status": st, "content_range": "",
                           "offered_by": ["a/v"]} for u, st in media]}

    def test_no_change_is_not_newsworthy(self):
        state = {"app_sha": {"u1": "aaa"}, "media_status": {"m1": 206}}
        facts = self._facts([("u1", "aaa")], [("m1", 206)])
        d = TP.diff_against_state(facts, state)
        self.assertFalse(TP.is_newsworthy(d, first_run=False),
                         "published on a day nothing moved")

    def test_a_changed_hash_is_newsworthy(self):
        state = {"app_sha": {"u1": "aaa"}, "media_status": {}}
        facts = self._facts([("u1", "bbb")])
        d = TP.diff_against_state(facts, state)
        self.assertEqual(len(d["changed"]), 1)
        self.assertEqual(d["changed"][0]["was"], "aaa")
        self.assertTrue(TP.is_newsworthy(d, first_run=False))

    def test_first_run_publishes_the_baseline(self):
        facts = self._facts([("u1", "aaa")])
        d = TP.diff_against_state(facts, None)
        self.assertTrue(TP.is_newsworthy(d, first_run=True),
                        "a first run has no yesterday and must say so")

    def test_a_dead_app_is_newsworthy_even_with_no_hash_change(self):
        state = {"app_sha": {"u1": "aaa"}, "media_status": {}}
        facts = self._facts([])
        facts["apps"] = [{"url": "u1", "status": 404, "sha": None, "bytes": 0,
                          "driven_by": ["a/v"]}]
        d = TP.diff_against_state(facts, state)
        self.assertEqual(len(d["dead_apps"]), 1)
        self.assertTrue(TP.is_newsworthy(d, first_run=False))

    def test_an_app_leaving_every_scene_is_noticed(self):
        state = {"app_sha": {"u1": "aaa", "u2": "bbb"}, "media_status": {}}
        facts = self._facts([("u1", "aaa")])
        d = TP.diff_against_state(facts, state)
        self.assertEqual(d["vanished"], ["u2"])
        self.assertTrue(TP.is_newsworthy(d, first_run=False))


class TransientTolerance(unittest.TestCase):
    """One sample is not evidence.

    Regression test for a real defect: the first two live runs published
    'this media file will not seek' off a single 503, and five manual probes
    immediately afterwards returned 206 every time.
    """

    def test_transient_statuses_are_classified_as_retryable(self):
        for code in (429, 500, 502, 503, 504, 0):
            self.assertIn(code, C.TRANSIENT, "%d should be retried" % code)

    def test_a_real_answer_is_not_retried(self):
        for code in (200, 206, 404, 403, 301):
            self.assertNotIn(code, C.TRANSIENT,
                             "%d is an answer and must not be retried" % code)


class ChannelShape(unittest.TestCase):
    """Both channel.json files must satisfy the platform's template shape."""

    REQUIRED_TOP = ("schema", "id", "name", "videos")
    REQUIRED_VIDEO = ("id", "title", "description", "published", "duration",
                      "tags", "sources")

    def test_both_channels_match_the_template_shape(self):
        for name in ("tumbler", "fieldguide"):
            p = os.path.join(ROOT, name, "channel.json")
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
            for k in self.REQUIRED_TOP:
                self.assertIn(k, doc, "%s missing %s" % (name, k))
            self.assertEqual(doc["schema"], "rapp-vision-channel/1.0")
            for v in doc["videos"]:
                for k in self.REQUIRED_VIDEO:
                    self.assertIn(k, v, "%s/%s missing %s" % (name, v.get("id"), k))
                # card-only: no media file is claimed anywhere
                self.assertEqual(v["sources"], [],
                                 "%s claims a media file it does not ship" % name)
                self.assertIn("live", v)
                for sc in v["live"]["scenes"]:
                    self.assertIn("card", sc,
                                  "a scene without a card would need an app")
                    self.assertIn("t", sc)
                    self.assertIn("dur", sc)

    def test_scene_clock_is_contiguous_and_matches_duration(self):
        """A scene list whose t offsets drift renders as a video that skips."""
        for name in ("tumbler", "fieldguide"):
            with open(os.path.join(ROOT, name, "channel.json"), encoding="utf-8") as fh:
                doc = json.load(fh)
            for v in doc["videos"]:
                t = 0
                for sc in v["live"]["scenes"]:
                    self.assertEqual(sc["t"], t, "%s/%s scene clock drifted"
                                     % (name, v["id"]))
                    t += sc["dur"]
                self.assertEqual(v["duration"], t,
                                 "%s/%s duration disagrees with its scenes"
                                 % (name, v["id"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
