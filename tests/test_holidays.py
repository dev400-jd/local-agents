"""Tests fuer die Feiertagsberechnung je Bundesland."""

import unittest
from datetime import date

from local_agents.holidays import (
    ALL_STATES,
    easter_sunday,
    holiday_note,
    is_public_holiday,
    public_holidays,
)


class TestEaster(unittest.TestCase):
    def test_known_dates(self) -> None:
        self.assertEqual(easter_sunday(2024), date(2024, 3, 31))
        self.assertEqual(easter_sunday(2025), date(2025, 4, 20))
        self.assertEqual(easter_sunday(2026), date(2026, 4, 5))
        self.assertEqual(easter_sunday(2027), date(2027, 3, 28))


class TestPublicHolidays(unittest.TestCase):
    def test_repentance_day_is_wednesday_before_november_23(self) -> None:
        for year, day in ((2025, 19), (2026, 18), (2027, 17), (2028, 22)):
            name, states = public_holidays(year)[date(year, 11, day)]
            self.assertEqual((name, states), ("Buss- und Bettag", frozenset({"SN"})))

    def test_movable_holidays_2026(self) -> None:
        holidays = public_holidays(2026)
        self.assertEqual(holidays[date(2026, 4, 3)][0], "Karfreitag")
        self.assertEqual(holidays[date(2026, 5, 14)][0], "Christi Himmelfahrt")
        self.assertEqual(holidays[date(2026, 5, 25)][0], "Pfingstmontag")
        self.assertEqual(holidays[date(2026, 6, 4)][0], "Fronleichnam")

    def test_nationwide_holidays_apply_everywhere(self) -> None:
        self.assertEqual(public_holidays(2026)[date(2026, 10, 3)][1], ALL_STATES)

    def test_hessen(self) -> None:
        self.assertTrue(is_public_holiday(date(2026, 6, 4), "HE"))  # Fronleichnam
        self.assertFalse(is_public_holiday(date(2026, 11, 18), "HE"))  # Buss- und Bettag
        self.assertFalse(is_public_holiday(date(2026, 10, 31), "HE"))  # Reformationstag
        self.assertFalse(is_public_holiday(date(2026, 11, 22), "HE"))  # Totensonntag
        self.assertEqual(sum("HE" in states for _, states in public_holidays(2026).values()), 10)


class TestHolidayNote(unittest.TestCase):
    def test_does_not_apply_at_location(self) -> None:
        self.assertEqual(
            holiday_note(date(2026, 11, 18), "HE"),
            "KEIN Feiertag in Hessen, normaler Arbeitstag; gilt nur in Sachsen",
        )

    def test_applies_at_location(self) -> None:
        self.assertEqual(
            holiday_note(date(2026, 6, 4), "HE"), "gesetzlicher Feiertag in Hessen, arbeitsfrei"
        )

    def test_not_a_public_holiday(self) -> None:
        self.assertEqual(holiday_note(date(2026, 11, 22), "HE"), "kein gesetzlicher Feiertag, Wochenende")
        self.assertEqual(holiday_note(date(2026, 11, 11), "HE"), "kein gesetzlicher Feiertag")

    def test_regional_holiday_on_weekend(self) -> None:
        self.assertEqual(
            holiday_note(date(2026, 10, 31), "HE")[:36], "KEIN Feiertag in Hessen, Wochenende;"
        )

    def test_without_location(self) -> None:
        self.assertEqual(holiday_note(date(2026, 11, 18)), "gesetzlicher Feiertag nur in Sachsen")
        self.assertEqual(holiday_note(date(2026, 12, 25)), "bundesweiter gesetzlicher Feiertag")


if __name__ == "__main__":
    unittest.main()
