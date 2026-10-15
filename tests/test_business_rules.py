"""Fast, database-free tests for the core attendance rules.

Run after installing requirements with: python -m unittest discover -s tests
These tests do not replace integration tests against MongoDB.
"""
import unittest
from datetime import datetime, timezone

from app.main import (
    attendance_day, calc_late, calc_derived, from_epoch, epoch,
    IST, UTC,
)


class AttendanceRuleTests(unittest.TestCase):
    def test_late_grace_boundary(self):
        employee = {"shift_start": "09:30", "shift_end": "18:30"}
        exact_grace = datetime.fromisoformat("2026-07-06T09:40:00+05:30").astimezone(UTC)
        one_second_late = datetime.fromisoformat("2026-07-06T09:40:01+05:30").astimezone(UTC)
        later = datetime.fromisoformat("2026-07-06T10:15:59+05:30").astimezone(UTC)
        self.assertEqual(calc_late(exact_grace, employee, "2026-07-06"), 0)
        self.assertEqual(calc_late(one_second_late, employee, "2026-07-06"), 10)
        self.assertEqual(calc_late(later, employee, "2026-07-06"), 45)

    def test_overnight_attendance_day(self):
        employee = {"shift_start": "22:00", "shift_end": "06:00"}
        punch = datetime.fromisoformat("2026-07-07T02:00:00+05:30").astimezone(UTC)
        self.assertEqual(attendance_day(punch, employee), "2026-07-06")

    def test_overnight_overtime_and_work_hours(self):
        employee = {"shift_start": "22:00", "shift_end": "06:00"}
        punch_in = datetime.fromisoformat("2026-07-06T21:55:00+05:30").astimezone(UTC)
        punch_out = datetime.fromisoformat("2026-07-07T06:40:00+05:30").astimezone(UTC)
        values = calc_derived(punch_in, punch_out, employee, "2026-07-06")
        self.assertEqual(values["work_hours"], 8.75)
        self.assertEqual(values["overtime_minutes"], 40)
        self.assertFalse(values["half_day"])

    def test_half_up_rounding(self):
        employee = {"shift_start": "09:00", "shift_end": "18:00"}
        punch_in = datetime(2026, 7, 6, 3, 30, tzinfo=UTC)
        punch_out = datetime(2026, 7, 6, 7, 37, 30, tzinfo=UTC)  # 4.125 hours
        values = calc_derived(punch_in, punch_out, employee, "2026-07-06")
        self.assertEqual(values["work_hours"], 4.13)
        self.assertTrue(values["half_day"])

    def test_epoch_milliseconds_and_second_truncation(self):
        original = datetime(2026, 7, 6, 4, 0, 0, 900000, tzinfo=UTC)
        ms = int(original.timestamp() * 1000)
        parsed = from_epoch(ms)
        self.assertEqual(parsed.microsecond, 0)
        self.assertEqual(epoch(parsed) % 1000, 0)

    def test_rejects_epoch_seconds(self):
        with self.assertRaises(ValueError):
            from_epoch(1_783_300_000)


if __name__ == "__main__":
    unittest.main()
