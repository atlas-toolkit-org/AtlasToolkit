"""
The number fields (ui/widgets/spin.py), offscreen: the triangles, steps that fit each parameter, forgiving typing,
and that every numeric parameter of every tool gets them.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QStyle, QStyleOptionSpinBox

from atlas_toolkit.core.params import Float, Int, Vec3
from atlas_toolkit.core.registry import registry
from atlas_toolkit.ui.theme import Theme
from atlas_toolkit.ui.theme.qss import stylesheet
from atlas_toolkit.ui.widgets.param_form import ParamForm
from atlas_toolkit.ui.widgets.spin import DoubleSpin, IntSpin, step_for, stepped


class StepTests(unittest.TestCase):
    def test_declared_steps_stay_when_they_suit_the_range(self):
        self.assertEqual(step_for(40, 0, 100, 5), 5)
        self.assertEqual(step_for(0.5, 0, 1, 0.05), 0.05)
        self.assertEqual(step_for(800, 0, 1000, 1), 1)
        self.assertEqual(step_for(0, 0, 1e6, 0.5), 0.5)                      # nothing to follow at zero

    def test_wide_ranges_follow_the_value(self):
        self.assertEqual(step_for(150000, 5000, 5000000, 1), 2000)
        self.assertEqual(stepped(150000, 1, 5000, 5000000, 1, 1), 152000)
        self.assertEqual(stepped(150000, -1, 5000, 5000000, 1, 1), 148000)
        self.assertEqual(stepped(151234, 1, 5000, 5000000, 1, 1), 152000)    # lands on a round number
        self.assertEqual(stepped(151234, -1, 5000, 5000000, 1, 1), 150000)
        self.assertEqual(step_for(50, float("-inf"), float("inf"), 0.1), 1)  # unbounded
        self.assertEqual(step_for(3, 0, 100000, 0.5), 0.5)                   # never finer than declared

    def test_small_values_with_a_positive_minimum_step_by_part_of_themselves(self):
        self.assertEqual(step_for(0.02, 0.0001, 1e9, 0.1), 0.005)
        self.assertEqual(step_for(1e-5, 1e-8, 0.01, 1e-6), 1e-6)
        self.assertEqual(step_for(2, 0, 100, 5), 5)                          # zero is in range: declared

    def test_limits_resolution_and_modifiers(self):
        self.assertEqual(stepped(98, 1, 0, 100, 5, 1), 100)
        self.assertEqual(stepped(2, -1, 0, 100, 5, 1), 0)
        self.assertAlmostEqual(stepped(12.3, 1, 0, 100, 5, 0.1), 17.3)       # the declared step keeps the offset
        self.assertEqual(stepped(10, 1, 0, 100, 5, 1, 10.0), 60)
        self.assertEqual(stepped(10, 1, 0, 100, 5, 1, 0.1), 11)              # a tenth, rounded up to the resolution
        self.assertAlmostEqual(stepped(0.5, 1, 0, 1, 0.05, 0.001, 0.1), 0.505)
        self.assertEqual(stepped(10, 1, 0, 100, 0.5, 1), 11)                 # never below the resolution


class FieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(stylesheet(Theme("dark")))

    def spin(self, cls=DoubleSpin, lo=0.0, hi=100.0, step=5.0, value=40.0, decimals=2, suffix=" mm"):
        sb = cls()
        if cls is DoubleSpin:
            sb.setDecimals(decimals)
        sb.setRange(lo, hi)
        sb.setSingleStep(step)
        sb.setSuffix(suffix)
        sb.setValue(value)
        sb.resize(120, 30)
        sb.show()
        self.addCleanup(sb.close)
        QTest.qWaitForWindowExposed(sb)
        return sb

    def control(self, sb, which) -> QPoint:
        opt = QStyleOptionSpinBox()
        sb.initStyleOption(opt)
        r = sb.style().subControlRect(QStyle.CC_SpinBox, opt, which, sb)
        self.assertGreaterEqual(r.width(), 16)
        self.assertGreaterEqual(r.height(), 10)
        return r.center()

    def type_in(self, sb, text):
        sb.setFocus()
        sb.selectAll()
        QTest.keyClicks(sb.lineEdit(), text)
        QTest.keyClick(sb.lineEdit(), Qt.Key_Return)

    def test_clicking_the_triangles_steps(self):
        sb = self.spin()
        seen = []
        sb.valueChanged.connect(seen.append)
        QTest.mouseClick(sb, Qt.LeftButton, pos=self.control(sb, QStyle.SC_SpinBoxUp))
        self.assertEqual(sb.value(), 45)
        QTest.mouseClick(sb, Qt.LeftButton, pos=self.control(sb, QStyle.SC_SpinBoxDown))
        QTest.mouseClick(sb, Qt.LeftButton, pos=self.control(sb, QStyle.SC_SpinBoxDown))
        self.assertEqual(sb.value(), 35)
        self.assertEqual(seen, [45, 40, 35])

    def test_triangles_keep_the_text_clear_and_work_on_an_int(self):
        sb = self.spin(IntSpin, 0, 1000, 10, 100, suffix="")
        QTest.mouseClick(sb, Qt.LeftButton, pos=self.control(sb, QStyle.SC_SpinBoxUp))
        self.assertEqual(sb.value(), 110)
        QTest.keyClick(sb, Qt.Key_Down)
        self.assertEqual(sb.value(), 100)
        self.assertLess(sb.lineEdit().geometry().right(), self.control(sb, QStyle.SC_SpinBoxUp).x())

    def test_typing_more_decimals_commas_and_out_of_range(self):
        sb = self.spin()
        self.type_in(sb, "12.345678")
        self.assertEqual(sb.value(), 12.35)                                  # rounded when committed
        self.assertEqual(sb.text(), "12.35 mm")
        self.type_in(sb, "7,5")
        self.assertEqual(sb.value(), 7.5)
        self.type_in(sb, "9999")
        self.assertEqual(sb.value(), 100)                                    # clamped, not refused
        self.type_in(sb, "abc")
        self.assertEqual(sb.value(), 100)                                    # letters never enter
        sb.setFocus()
        sb.selectAll()
        QTest.keyClick(sb.lineEdit(), Qt.Key_Delete)
        QTest.keyClick(sb.lineEdit(), Qt.Key_Return)
        sb.clearFocus()
        self.assertEqual(sb.value(), 100)                                    # empty goes back to the value
        self.assertEqual(sb.text(), "100.00 mm")

    def test_typing_on_an_int_and_small_numbers(self):
        sb = self.spin(IntSpin, 0, 1000, 10, 100, suffix="")
        self.type_in(sb, "250")
        self.assertEqual(sb.value(), 250)
        tol = self.spin(lo=1e-8, hi=0.01, step=1e-6, value=1e-5, decimals=8, suffix="")
        self.type_in(tol, "2e-5")
        self.assertAlmostEqual(tol.value(), 2e-5)

    def test_step_starts_from_a_number_just_typed(self):
        sb = self.spin()
        sb.setFocus()
        sb.selectAll()
        QTest.keyClicks(sb.lineEdit(), "20")
        QTest.mouseClick(sb, Qt.LeftButton, pos=self.control(sb, QStyle.SC_SpinBoxUp))
        self.assertEqual(sb.value(), 25)

    def test_a_field_without_focus_ignores_the_wheel(self):
        sb = self.spin()
        sb.clearFocus()
        e = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
        QApplication.sendEvent(sb, e)
        self.assertEqual(sb.value(), 40)

    def test_every_numeric_parameter_of_every_tool_has_them_and_can_step(self):
        n = 0
        for tool in registry().ordered():
            form = ParamForm(tool, Theme("dark"))
            spins = form.findChildren(QAbstractSpinBox)
            self.assertTrue(all(isinstance(s, (IntSpin, DoubleSpin)) for s in spins), tool.id)
            want = sum(len(p.axes) if isinstance(p, Vec3) else 1 for p in tool.param_list
                       if isinstance(p, (Float, Int, Vec3)))
            self.assertEqual(len(spins), want, tool.id)
            for s in spins:
                n += 1
                was = s.value()
                s.setValue(s.minimum())
                if s.minimum() < s.maximum():
                    s.stepBy(1)
                    self.assertGreater(s.value(), s.minimum(), (tool.id, s.prefix(), s.singleStep()))
                s.setValue(was)
            form.deleteLater()
        self.assertGreater(n, 500)

    def test_no_plain_spin_box_anywhere_in_the_app(self):
        root = Path(__file__).resolve().parents[1] / "src" / "atlas_toolkit"
        plain = re.compile(r"\bQ(Double)?SpinBox\(\)")
        bad = [str(f) for f in root.rglob("*.py") if plain.search(f.read_text(encoding="utf-8"))]
        self.assertEqual(bad, [], "use IntSpin / DoubleSpin (ui/widgets/spin.py)")


if __name__ == "__main__":
    unittest.main()
