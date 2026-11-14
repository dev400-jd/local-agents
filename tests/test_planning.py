"""Tests fuer die Verschiebevorschlaege."""

import dataclasses
import unittest
from datetime import datetime

from local_agents.calendar_events import Event
from local_agents.planning import move_suggestions
from local_agents.profile import Context, Profile


def profile(stress: str = "", onsite_days: frozenset[int] = frozenset()) -> Profile:
    return Profile(
        contexts=(
            Context("intern", "intern", "Intern", domains=frozenset({"beispiel.de"})),
            Context(
                "kunde-acme",
                "kunde",
                "Kunde ACME",
                domains=frozenset({"acme.example"}),
                stress=stress,
                onsite_days=onsite_days,
            ),
        )
    )


def meeting(day: int, start: str, end: str, title: str, who: str = "beispiel.de", n: int = 2) -> Event:
    def at(clock: str) -> datetime:
        hour, minute = map(int, clock.split(":"))
        return datetime(2026, 11, day, hour, minute)

    return Event(at(start), at(end), title, organizer_domain=who, attendee_domains=(who,) * n)


# Mo 16.11.: zwei interne Termine am Stueck (2 h), Di 17.11.: ein interner (1 h),
# Mi 18.11.: ein kurzer interner und ein Kundentermin.
WEEK = [
    meeting(16, "09:00", "10:00", "Team"),
    meeting(16, "10:15", "11:00", "Planung"),
    meeting(17, "14:00", "15:00", "Jour fixe"),
    meeting(18, "09:00", "09:30", "Abstimmung"),
    meeting(18, "10:00", "11:00", "Kundenworkshop", who="acme.example"),
]


class TestMoveSuggestions(unittest.TestCase):
    def suggest(self, prof: Profile, events: list[Event] = WEEK) -> list[str]:
        def context_of(event: Event) -> str:
            return prof.classify(event.title, event.organizer_domain, event.attendee_domains)

        return move_suggestions(events, prof, context_of)

    def test_low_stress_without_customer_day_suggests_nothing(self) -> None:
        self.assertEqual(self.suggest(profile()), [])
        self.assertEqual(self.suggest(profile(stress="niedrig")), [])

    def test_customer_day_lists_every_internal_meeting_of_that_day(self) -> None:
        self.assertEqual(
            self.suggest(profile(onsite_days=frozenset({2}))),
            [
                "- Mi 18.11. ist Kundentag (Kunde ACME), 1 interner Termin verschieben:"
                "\n  - 09:00-09:30 Abstimmung"
            ],
        )

    def test_medium_stress_suggests_blocks_of_two_hours(self) -> None:
        self.assertEqual(
            self.suggest(profile(stress="mittel")),
            [
                "- Mo 16.11.: interner Block 09:00-11:00 (2 interne Termine, 120 Min) "
                "verschieben, weil das Stresslevel bei Kunde ACME mittel ist:"
                "\n  - 09:00-10:00 Team\n  - 10:15-11:00 Planung"
            ],
        )

    def test_high_stress_also_suggests_single_meetings_of_an_hour(self) -> None:
        lines = self.suggest(profile(stress="hoch"))
        self.assertEqual(len(lines), 2)
        self.assertIn("Mo 16.11.: interner Block 09:00-11:00", lines[0])
        self.assertIn("Di 17.11.: interner Block 14:00-15:00 (1 interner Termin, 60 Min)", lines[1])

    def test_long_pause_splits_blocks(self) -> None:
        events = [meeting(16, "09:00", "10:00", "Team"), meeting(16, "13:00", "14:00", "Planung")]
        self.assertEqual(self.suggest(profile(stress="mittel"), events), [])

    def test_duplicate_entries_are_named_once(self) -> None:
        events = [meeting(17, "14:00", "15:00", "Jour fixe")] * 2
        lines = self.suggest(profile(stress="hoch"), events)
        self.assertEqual(lines[0].count("Jour fixe"), 1)
        self.assertIn("1 interner Termin", lines[0])

    def test_large_round_is_flagged(self) -> None:
        events = [meeting(18, "12:00", "13:00", "Firmenrunde", n=40)]
        lines = self.suggest(profile(onsite_days=frozenset({2})), events)
        self.assertIn("Firmenrunde (große Runde mit 40 Eingeladenen, eher aussetzen)", lines[0])

    def test_customer_meetings_are_never_suggested(self) -> None:
        lines = self.suggest(profile(stress="hoch", onsite_days=frozenset({2})))
        self.assertNotIn("Kundenworkshop", "\n".join(lines))

    def test_without_customer_no_suggestions(self) -> None:
        only_internal = dataclasses.replace(profile(), contexts=profile().contexts[:1])
        self.assertEqual(self.suggest(only_internal), [])


if __name__ == "__main__":
    unittest.main()
