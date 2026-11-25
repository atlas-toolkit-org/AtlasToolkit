"""
The welcome window: when it shows, its notice, the changelog in headlines, the tips, and the option that turns it off.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from atlas_toolkit import version
from atlas_toolkit.core import prefs
from atlas_toolkit.core.settings import AppSettings

START = """
import sys
from PySide6.QtCore import QTimer
from atlas_toolkit.ui import app, shell
class Window(shell.MainWindow):
    def show(self):
        super().show()
        self.ticks = 0
        self.clock = QTimer(self, interval=100, timeout=self.look)
        self.clock.start()
    def look(self):
        self.ticks += 1                 # counted by the event loop, so a slow machine only takes longer
        up = self.welcome is not None and self.welcome.isVisible()
        if up or self.ticks > 25:
            self.clock.stop()
            print("WELCOME", up, flush=True)
            self.close()
shell.MainWindow = Window
sys.exit(app.run_app(sys.argv[1:]))
"""


class ChangelogTests(unittest.TestCase):
    def test_headlines(self):
        from atlas_toolkit.ui.welcome import highlights
        intro, items = highlights("**The first release.** It has `two` things\nand [a link](x.md).\n\n"
                                  "- **A `new` tool.** It draws lines.\n  Then it does more (since 2.5.0).\n"
                                  "- For tool authors: a field, in v2.5 of the\n  spec. And more.\n- **Alone.**")
        self.assertEqual(intro, "The first release. It has two things and a link.")
        self.assertEqual(items, [("A new tool", "It draws lines."), ("", "For tool authors: a field, in v2.5 of the spec."),
                                 ("Alone", "")])

    def test_the_real_changelog(self):
        from atlas_toolkit.ui.about import changelog_text, releases
        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.ui.welcome import RELEASES_SHOWN, highlights, news_html
        newest = releases(changelog_text())[:RELEASES_SHOWN]
        shown = news_html(Theme("dark"))
        for ver, _date, body in newest:
            self.assertIn(ver, shown)
            self.assertTrue(highlights(body)[1], f"{ver} has no entries")
        self.assertNotIn("**", shown)


class PlateTests(unittest.TestCase):
    def test_every_minor_version_draws_its_own_plate(self):
        """CLAUDE.md: a new X.Y gets a new plate (scripts/dev/welcome_art.py), showing what changed in it."""
        from atlas_toolkit.ui.welcome import plate_info
        info = plate_info()
        series = ".".join(version.__version__.split(".")[:2])
        self.assertEqual(info.get("series"), series,
                         f"the welcome plate was drawn for {info.get('series')}: draw one for {series} with "
                         "scripts/dev/welcome_art.py")
        self.assertTrue(info.get("title") and info.get("caption"))

    def test_the_recipe_is_the_published_plate(self):
        import importlib.util
        script = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "welcome_art.py"
        spec = importlib.util.spec_from_file_location("welcome_art", script)
        art = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(art)
        from atlas_toolkit.ui.welcome import plate_info
        info = plate_info()
        self.assertEqual({k: info.get(k) for k in ("series", "title", "caption")},
                         {k: art.RECIPE[k] for k in ("series", "title", "caption")})
        self.assertEqual(art.RECIPE["steps"][-1][0], "contour-lines")


class StartTests(unittest.TestCase):
    def test_only_when_the_app_is_opened_on_its_own(self):
        from atlas_toolkit.ui.app import _parse, welcomes
        self.assertTrue(welcomes(_parse([])))
        self.assertTrue(welcomes(_parse(["--theme", "light"])))
        for argv in (["head.stl"], ["--tool", "mesh-twist"], ["--page", "home"], ["--workflow", "a.atlasflow"],
                     ["--snapshot", "shot.png"]):
            self.assertFalse(welcomes(_parse(argv)), argv)

    def start(self, *argv, **options) -> str:
        with tempfile.TemporaryDirectory(prefix="atlas_welcome_", ignore_cleanup_errors=True) as home:
            env = {**os.environ, _isolate.VAR: home, "QT_QPA_PLATFORM": "offscreen",
                   "PYTHONPATH": str(Path(version.__file__).resolve().parents[1])}
            if options:
                code = ("from atlas_toolkit.core.settings import AppSettings\ns = AppSettings()\n"
                        f"s.data.update({options!r})\ns.save()")
                subprocess.run([sys.executable, "-c", code], env=env, check=True, timeout=60)
            done = subprocess.run([sys.executable, "-c", START, *argv], env=env, capture_output=True, text=True, timeout=120)
        return next((ln for ln in done.stdout.splitlines() if ln.startswith("WELCOME")), done.stdout + done.stderr)

    def test_the_app_shows_it_until_it_is_turned_off(self):
        self.assertEqual(self.start(), "WELCOME True")
        self.assertEqual(self.start(show_welcome=False), "WELCOME False")
        self.assertEqual(self.start("--page", "activity"), "WELCOME False")


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_welcome_"))
        cls.leave_home = _isolate.fresh_home()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from atlas_toolkit.core.history import History
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.ui.bridge import JobBridge
        from atlas_toolkit.ui.context import AppContext
        from atlas_toolkit.ui.shell import MainWindow
        from atlas_toolkit.ui.theme import load_fonts
        load_fonts()
        cls.ctx = AppContext(registry(), AppSettings(cls.tmp / "cfg"), JobBridge(), History(cls.tmp / "history.jsonl"))
        cls.win = MainWindow(cls.ctx)

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.ctx.bridge.shutdown()
        cls.leave_home()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self):
        d = self.win.show_welcome()
        self.addCleanup(lambda: self.win.welcome and self.win.welcome.close())
        self.app.processEvents()
        return d

    def words(self, d) -> str:
        from PySide6.QtWidgets import QAbstractButton, QLabel
        return " ".join([*(w.text() for w in d.findChildren(QLabel)), *(w.text() for w in d.findChildren(QAbstractButton)),
                         d.news.toPlainText()])

    def test_notice_links_and_one_window(self):
        d = self.open()
        self.assertIs(self.win.show_welcome(), d)
        text = self.words(d)
        self.assertIn(f"Atlas v{version.__version__} is still under development", text)
        for part in ("Unexpected issues", "own risk", "slow", "strange results", "feedback is essential"):
            self.assertIn(part, text)
        for private in (str(Path.home()), Path.home().name, str(self.tmp)):
            self.assertNotIn(private, text)
        with mock.patch("atlas_toolkit.core.paths.open_url") as opened:
            d.btn_feedback.click()
            d.btn_support.click()
        self.assertEqual([c.args[0] for c in opened.call_args_list], [version.FEEDBACK_URL, version.SUPPORT_URL])
        self.assertIs(self.win.welcome, d)                       # a web page leaves it open
        d.ok.click()
        self.assertIsNone(self.win.welcome)

    def test_links_into_the_app_take_over(self):
        self.open().follow("help:changelog")
        self.addCleanup(lambda: self.win.about and self.win.about.close())
        self.assertIsNone(self.win.welcome)
        self.assertIsNotNone(self.win.about)

    def test_every_tip_has_an_icon_and_a_place_to_go(self):
        from atlas_toolkit.ui.about import AboutDialog
        from atlas_toolkit.ui.setup import SetupDialog
        from atlas_toolkit.ui.theme import icon_paths
        from atlas_toolkit.ui.welcome import TIPS, TIPS_SHOWN
        self.assertEqual(len(TIPS) % TIPS_SHOWN, 0)              # no page with a hole
        for glyph, title, text, link, target in TIPS:
            self.assertIn(glyph, icon_paths(), title)
            self.assertTrue(text.endswith("."), title)
            self.assertEqual(bool(link), bool(target), title)
            place, _, tab = target.partition(":")
            if target and not target.startswith("https://"):
                self.assertIn((place, tab), {("workflows", "")} | {("setup", t) for t in SetupDialog.TABS}
                              | {("help", t) for t in AboutDialog.TABS}, title)

    def test_the_tips_turn_and_each_opening_shows_the_next(self):
        self.ctx.settings.data.pop("welcome_tip", None)
        d = self.open()
        self.assertEqual(d.tip_page, 0)
        d.tip_prev.click()
        self.assertEqual(d.tip_page, d.tip_pages - 1)
        d.tip_next.click()
        d.tip_next.click()
        self.assertEqual(d.tip_page, 1)
        d.close()
        self.assertEqual(self.open().tip_page, 2)
        self.win.welcome.close()
        self.ctx.settings.set("welcome_tip", "nonsense")
        self.assertEqual(self.open().tip_page, 0)
        self.assertIn("welcome_tip", prefs.STATE_KEYS)
        self.assertIn("welcome_tip", prefs.LOCAL_KEYS)

    def test_its_box_and_the_setup_option_are_one(self):
        d = self.open()
        setup = self.win.show_setup("general")
        self.addCleanup(lambda: self.win.setup and self.win.setup.close())
        self.assertFalse(d.hide_box.isChecked())
        self.assertTrue(setup.welcome_check.isChecked())
        d.hide_box.setChecked(True)
        self.assertFalse(self.ctx.pref("show_welcome"))
        self.assertFalse(setup.welcome_check.isChecked())
        self.assertIs(AppSettings(self.tmp / "cfg").get("show_welcome"), False)      # saved at once
        setup.welcome_check.setChecked(True)
        self.assertTrue(self.ctx.pref("show_welcome"))
        self.assertFalse(d.hide_box.isChecked())

    def test_theme_art_and_small_windows(self):
        d = self.open()
        page = d.tip_page
        self.addCleanup(lambda: (self.ctx.set_theme("dark"), self.ctx.set_art("paper")))
        self.ctx.set_theme("light")
        self.assertIsNot(self.win.welcome, d)                    # rebuilt for the theme, on the same tips
        d = self.win.welcome
        self.assertEqual(d.tip_page, page)
        d.resize(1084, 720)
        self.app.processEvents()
        paper = d.plate.grab().toImage()
        self.ctx.set_art("blueprint")
        self.app.processEvents()
        self.assertNotEqual(paper.pixelColor(5, 5), d.plate.grab().toImage().pixelColor(5, 5))
        self.assertGreater(len({paper.pixelColor(x, 300).rgb() for x in range(0, paper.width(), 2)}), 2)   # the drawing
        self.assertEqual(d.scroll.verticalScrollBar().maximum(), 0)
        d.setMinimumSize(0, 0)
        d.resize(640, 460)
        self.app.processEvents()
        self.assertTrue(d.plate.isHidden())                      # the text keeps the room
        self.assertLessEqual(d.scroll.widget().width(), d.scroll.viewport().width())
        self.assertLessEqual(d.width(), 640)


if __name__ == "__main__":
    unittest.main()
