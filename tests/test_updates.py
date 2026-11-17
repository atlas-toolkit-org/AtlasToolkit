"""
core/updates.py: is a newer public release out? Versions, the site-then-GitHub lookup, the daily schedule, skipping
a version, and that its state never leaves this computer (settings export). No network: every fetch is a stand-in.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from atlas_toolkit.core import prefs, updates
from atlas_toolkit.core.settings import AppSettings


def fake(answers: dict):
    """A fetch that answers by URL; an exception value is raised, as a failed request would."""
    def fetch(url, timeout):
        a = answers.get(url, OSError("offline"))
        if isinstance(a, Exception):
            raise a
        return a
    return fetch


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(updates.parse("2.3.2"), (2, 3, 2))
        self.assertEqual(updates.parse("v10.0.1"), (10, 0, 1))
        for bad in ("", None, "2.3", "2.4.0-rc1", "latest", 3):
            self.assertIsNone(updates.parse(bad), bad)
        self.assertTrue(updates.newer("2.10.0", "2.9.9"))           # numbers, not text
        self.assertFalse(updates.newer("2.3.1", "2.3.2"))           # an early-access build ahead of the public one
        self.assertFalse(updates.newer("2.3.2", "2.3.2"))
        self.assertFalse(updates.newer("nonsense", "2.3.2"))


class LookupTests(unittest.TestCase):
    def test_the_site_first(self):
        f = fake({updates.SITE_URL: {"version": "2.4.0"}, updates.GITHUB_URL: {"tag_name": "v9.9.9"}})
        self.assertEqual(updates.latest(fetch=f), "2.4.0")

    def test_github_when_the_site_does_not_answer(self):
        f = fake({updates.GITHUB_URL: {"tag_name": "v2.4.1"}})
        self.assertEqual(updates.latest(fetch=f), "2.4.1")
        f = fake({updates.SITE_URL: {"version": None}, updates.GITHUB_URL: {"tag_name": "v2.4.1"}})
        self.assertEqual(updates.latest(fetch=f), "2.4.1")          # the site up but with nothing public yet

    def test_nothing_when_offline_or_nonsense(self):
        self.assertIsNone(updates.latest(fetch=fake({})))
        f = fake({updates.SITE_URL: ["not", "a", "dict"], updates.GITHUB_URL: {"tag_name": "nightly"}})
        self.assertIsNone(updates.latest(fetch=f))

    def test_the_request_says_nothing_about_the_person(self):
        seen = {}

        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return json.dumps({"version": "2.4.0"}).encode()

        def urlopen(req, timeout, context):
            seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
            return Answer()

        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(updates.latest(), "2.4.0")
        self.assertEqual(seen["url"], updates.SITE_URL)              # no query beyond format=json
        self.assertEqual(seen["headers"].get("User-agent"), updates.AGENT)
        self.assertNotIn(updates.__version__, " ".join(seen["headers"].values()))


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="atlas_updates_"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.settings = AppSettings(self.folder)
        env = mock.patch.dict(os.environ, {updates.ENV_OFF: ""})
        env.start()
        self.addCleanup(env.stop)

    def test_asks_first_and_checks_only_when_daily(self):
        self.assertEqual(prefs.get(self.settings, "update_check"), "ask")
        self.assertFalse(updates.due(self.settings, now=1e9))      # not before the person says yes
        self.settings.set("update_check", "daily")
        self.assertTrue(updates.due(self.settings, now=1e9))
        updates.record(self.settings, "2.4.0", now=1e9)
        self.assertFalse(updates.due(self.settings, now=1e9 + updates.DAY - 1))
        self.assertTrue(updates.due(self.settings, now=1e9 + updates.DAY))
        self.assertTrue(updates.due(self.settings, now=1e9 - 10))  # a clock set back does not stop it for good
        self.settings.set("update_check", "never")
        self.assertFalse(updates.due(self.settings, now=2e9))
        self.settings.set("update_check", "nonsense")               # a bad value is the default: ask
        self.assertEqual(prefs.get(self.settings, "update_check"), "ask")

    def test_environment_turns_it_off(self):
        self.settings.set("update_check", "daily")
        with mock.patch.dict(os.environ, {updates.ENV_OFF: "1"}):
            self.assertFalse(updates.due(self.settings, now=1e9))

    def test_offer_and_skip(self):
        f = fake({updates.SITE_URL: {"version": "2.9.0"}})
        self.assertEqual(updates.check(self.settings, now=1e9, fetch=f, local="2.3.2"), "2.9.0")
        self.assertEqual(self.settings.get("update_last"), 1e9)
        self.settings.set("update_skip", "2.9.0")
        self.assertIsNone(updates.offer(self.settings, local="2.3.2"))
        f = fake({updates.SITE_URL: {"version": "2.9.1"}})                       # a later one is offered again
        self.assertEqual(updates.check(self.settings, now=2e9, fetch=f, local="2.3.2"), "2.9.1")
        self.assertIsNone(updates.offer(self.settings, local="2.9.1"))          # once installed, nothing
        updates.check(self.settings, now=3e9, fetch=fake({}), local="2.3.2")    # offline: the last answer stays
        self.assertEqual(self.settings.get("update_latest"), "2.9.1")
        self.assertEqual(self.settings.get("update_last"), 3e9)                 # and the next try waits a day

    def test_state_stays_on_this_computer(self):
        for k, v in (("update_check", "daily"), ("update_last", 1e9), ("update_latest", "2.9.0"),
                     ("update_skip", "2.8.0")):
            self.settings.set(k, v)
        out = self.folder / "export.json"
        prefs.export_settings(self.settings, out)
        app = json.loads(out.read_text(encoding="utf-8"))["app"]
        self.assertEqual(app.get("update_check"), "daily")                      # the choice travels
        for k in ("update_last", "update_latest", "update_skip"):               # the state does not
            self.assertNotIn(k, app)


if __name__ == "__main__":
    unittest.main()
