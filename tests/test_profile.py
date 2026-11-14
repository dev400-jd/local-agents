"""Tests fuer das Profil: Laden und Zuordnung zu Arbeitskontexten."""

import tempfile
import unittest
from pathlib import Path

from local_agents.profile import (
    EXTERNAL_UNKNOWN,
    OTHER,
    Context,
    Profile,
    ProfileError,
    load_profile,
)

PROFILE = Profile(
    name="Erika",
    employer="Beispiel GmbH",
    role="Teamleitung",
    contexts=(
        Context("intern", "intern", "Intern", domains=frozenset({"beispiel.de"})),
        Context(
            "kunde-acme",
            "kunde",
            "Kunde ACME",
            description="Projekt Datenplattform",
            domains=frozenset({"acme.example"}),
            keywords=("acme", "datenplattform"),
        ),
    ),
)

TOML = """
[person]
name = "Erika"
employer = "Beispiel GmbH"
role = "Teamleitung"

[[contexts]]
id = "intern"
kind = "intern"
label = "Intern"
domains = ["Beispiel.de"]

[[contexts]]
id = "kunde-acme"
kind = "kunde"
label = "Kunde ACME"
description = "Projekt Datenplattform"
domains = ["acme.example"]
keywords = ["ACME", "Datenplattform"]
"""


class TestClassify(unittest.TestCase):
    def test_all_internal_is_internal(self) -> None:
        self.assertEqual(PROFILE.classify("Team Meeting", "beispiel.de", ["beispiel.de"] * 3), "intern")

    def test_customer_organizer_is_customer(self) -> None:
        attendees = ["beispiel.de"] * 5 + ["acme.example"]
        self.assertEqual(PROFILE.classify("Abstimmung", "acme.example", attendees), "kunde-acme")

    def test_customer_keyword_wins_without_customer_domain(self) -> None:
        self.assertEqual(
            PROFILE.classify("Datenplattform Daily", "beispiel.de", ["beispiel.de"]), "kunde-acme"
        )

    def test_keyword_ignores_case(self) -> None:
        self.assertEqual(PROFILE.classify("WG: acme Status"), "kunde-acme")

    def test_few_customer_addresses_in_internal_round_stay_internal(self) -> None:
        attendees = ["beispiel.de"] * 15 + ["acme.example"] * 2 + ["fremd.example"]
        self.assertEqual(PROFILE.classify("Team Meeting", "beispiel.de", attendees), "intern")

    def test_internal_invite_to_mostly_customer_people_is_customer(self) -> None:
        attendees = ["beispiel.de"] * 2 + ["acme.example"] * 3
        self.assertEqual(PROFILE.classify("Abstimmung", "beispiel.de", attendees), "kunde-acme")

    def test_unknown_foreign_organizer_is_external(self) -> None:
        self.assertEqual(PROFILE.classify("Buchung", "fremd.example", ["beispiel.de"]), EXTERNAL_UNKNOWN)

    def test_mostly_unknown_foreign_attendees_is_external(self) -> None:
        attendees = ["beispiel.de"] + ["fremd.example"] * 3
        self.assertEqual(PROFILE.classify("Termin", "beispiel.de", attendees), EXTERNAL_UNKNOWN)

    def test_ignored_domains_do_not_count(self) -> None:
        profile = Profile(contexts=PROFILE.contexts, ignored_domains=frozenset({"teams.example"}))
        attendees = ["beispiel.de"] + ["Teams.example"] * 3
        self.assertEqual(profile.classify("Termin", "beispiel.de", attendees), "intern")

    def test_no_participants_is_other(self) -> None:
        self.assertEqual(PROFILE.classify("Feiertag"), OTHER)

    def test_empty_profile(self) -> None:
        self.assertEqual(Profile().classify("Termin", "beispiel.de"), EXTERNAL_UNKNOWN)
        self.assertEqual(Profile().describe(), "")


class TestDescribe(unittest.TestCase):
    def test_names_person_and_context_ids(self) -> None:
        text = PROFILE.describe()
        self.assertIn("Erika, Teamleitung, Beispiel GmbH", text)
        self.assertIn("- kunde-acme: Kunde ACME - Projekt Datenplattform", text)
        self.assertIn("- extern:", text)


class TestLoadProfile(unittest.TestCase):
    def write(self, text: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "profile.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_and_normalises_case(self) -> None:
        self.assertEqual(load_profile(self.write(TOML)), PROFILE)

    def test_missing_file_gives_empty_profile(self) -> None:
        self.assertEqual(load_profile(Path("/nicht/vorhanden.toml")), Profile())

    def test_loads_state(self) -> None:
        profile = load_profile(self.write('[person]\nstate = "he"\n'))
        self.assertEqual(profile.state, "HE")
        self.assertIn("Arbeitsort: Hessen", profile.describe())

    def test_rejects_unknown_state(self) -> None:
        with self.assertRaises(ProfileError):
            load_profile(self.write('[person]\nstate = "Hessen"\n'))

    def test_loads_stress_and_customer_days(self) -> None:
        text = '[[contexts]]\nid = "k"\nkind = "kunde"\nstress = "Hoch"\nonsite_days = ["Mi", "freitag"]\n'
        context = load_profile(self.write(text)).contexts[0]
        self.assertEqual((context.stress, context.onsite_days), ("hoch", frozenset({2, 4})))
        self.assertIn("Stresslevel hoch; Kundentag Mi, Fr", load_profile(self.write(text)).describe())

    def test_rejects_unknown_stress_and_weekday(self) -> None:
        for field in ('stress = "extrem"', 'onsite_days = ["Xy"]'):
            with self.assertRaises(ProfileError):
                load_profile(self.write(f'[[contexts]]\nid = "k"\nkind = "kunde"\n{field}\n'))

    def test_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ProfileError):
            load_profile(self.write('[[contexts]]\nid = "x"\nkind = "privat"\n'))

    def test_rejects_reserved_id(self) -> None:
        with self.assertRaises(ProfileError):
            load_profile(self.write('[[contexts]]\nid = "extern"\nkind = "kunde"\n'))

    def test_rejects_missing_field(self) -> None:
        with self.assertRaises(ProfileError):
            load_profile(self.write('[[contexts]]\nid = "x"\n'))

    def test_rejects_broken_toml(self) -> None:
        with self.assertRaises(ProfileError):
            load_profile(self.write("[person"))


if __name__ == "__main__":
    unittest.main()
