"""Tests fuer Asana-Quelle und Asana-Agent, ohne Netz und ohne Ollama."""

import unittest
from datetime import datetime, timedelta, timezone

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.agent import answer_with_notes
from local_agents.asana_agent import MAX_DAYS, AsanaDeps, asana_agent
from local_agents.asana_source import Mention, fetch_mentions, mentions_user, plain_text
from local_agents.profile import Context, Profile

models.ALLOW_MODEL_REQUESTS = False

ME = "111"
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=14)


def mention_html(gid: str, name: str = "Erika") -> str:
    return (
        f'<a href="https://app.asana.com/0/9/list" data-asana-accessible="true" '
        f'data-asana-dynamic="true" data-asana-type="user" data-asana-gid="{gid}">@{name}</a>'
    )


def story(gid: str, created: str, text: str, subtype: str = "comment_added") -> dict:
    return {
        "gid": gid,
        "created_at": created,
        "created_by": {"name": "Anna"},
        "resource_subtype": subtype,
        "html_text": f"<body>{text}</body>",
    }


class FakeAsana:
    """Antwortet wie die Asana-API auf die Pfade, die fetch_mentions abfragt."""

    def __init__(self, tasks: list[dict], stories: dict[str, list[list[dict]]]) -> None:
        self.tasks, self.stories, self.calls = tasks, stories, []

    def __call__(self, path: str, params: dict[str, str]) -> dict:
        self.calls.append((path, params))
        if path == "/users/me":
            return {"data": {"gid": ME, "workspaces": [{"gid": "ws"}]}}
        if path == "/workspaces/ws/tasks/search":
            return {"data": self.tasks}
        pages = self.stories.get(path.split("/")[2], [[]])
        index = int(params.get("offset", "0"))
        more = {"offset": str(index + 1)} if index + 1 < len(pages) else None
        return {"data": pages[index], "next_page": more}


def task(gid: str, name: str, created: str = "2026-01-01T08:00:00.000Z", notes: str = "") -> dict:
    return {
        "gid": gid,
        "name": name,
        "html_notes": f"<body>{notes}</body>",
        "permalink_url": f"https://app.asana.com/0/0/{gid}",
        "projects": [{"name": "Datenplattform"}],
        "modified_at": "2026-11-13T08:00:00.000Z",
        "created_at": created,
        "created_by": {"name": "Bernd"},
        "completed": False,
    }


class TestRichText(unittest.TestCase):
    def test_finds_mention_of_user(self) -> None:
        self.assertTrue(mentions_user(f"Hallo {mention_html(ME)}", ME))

    def test_ignores_mention_of_other_user_and_other_links(self) -> None:
        self.assertFalse(mentions_user(mention_html("222"), ME))
        self.assertFalse(mentions_user(f'<a data-asana-type="task" data-asana-gid="{ME}">x</a>', ME))

    def test_plain_text_strips_tags_and_shortens(self) -> None:
        self.assertEqual(plain_text("<body>Hallo <b>du</b> &amp;\n ich</body>"), "Hallo du & ich")
        self.assertEqual(len(plain_text("x" * 500)), 240)


class TestFetchMentions(unittest.TestCase):
    def test_collects_comment_mentions_newest_first(self) -> None:
        api = FakeAsana(
            [task("t1", "Bericht"), task("t2", "Angebot")],
            {
                "t1": [[story("s1", "2026-11-10T09:00:00.000Z", f"{mention_html(ME)} bitte pruefen")]],
                "t2": [[story("s2", "2026-11-12T09:00:00.000Z", f"Frage an {mention_html(ME)}")]],
            },
        )
        mentions = fetch_mentions(SINCE, api)
        self.assertEqual([m.task for m in mentions], ["Angebot", "Bericht"])
        first = mentions[0]
        self.assertEqual((first.author, first.kind, first.excerpt), ("Anna", "Kommentar", "Frage an @Erika"))
        self.assertEqual(first.projects, ("Datenplattform",))
        self.assertEqual(first.url, "https://app.asana.com/0/0/t2")

    def test_searches_followed_tasks_in_period(self) -> None:
        api = FakeAsana([], {})
        fetch_mentions(SINCE, api)
        params = dict(api.calls)["/workspaces/ws/tasks/search"]
        self.assertEqual(params["followers.any"], ME)
        self.assertTrue(params["modified_at.after"].startswith("2026-10-31T"))

    def test_skips_comments_without_mention_and_system_stories(self) -> None:
        api = FakeAsana(
            [task("t1", "Bericht")],
            {
                "t1": [
                    [
                        story("s1", "2026-11-10T09:00:00.000Z", "nur ein Kommentar"),
                        story("s2", "2026-11-10T09:00:00.000Z", mention_html("222")),
                        story("s3", "2026-11-10T09:00:00.000Z", mention_html(ME), subtype="assigned"),
                    ]
                ]
            },
        )
        self.assertEqual(fetch_mentions(SINCE, api), [])

    def test_follows_story_pages(self) -> None:
        pages = [
            [story("s1", "2026-11-10T09:00:00.000Z", mention_html(ME))],
            [story("s2", "2026-11-11T09:00:00.000Z", mention_html(ME))],
        ]
        self.assertEqual(len(fetch_mentions(SINCE, FakeAsana([task("t1", "Bericht")], {"t1": pages}))), 2)

    def test_description_mention_counts_only_for_new_tasks(self) -> None:
        new = task("t1", "Neu", created="2026-11-05T08:00:00.000Z", notes=f"fuer {mention_html(ME)}")
        old = task("t2", "Alt", created="2026-09-01T08:00:00.000Z", notes=f"fuer {mention_html(ME)}")
        mentions = fetch_mentions(SINCE, FakeAsana([new, old], {}))
        self.assertEqual([(m.task, m.kind, m.author) for m in mentions], [("Neu", "Beschreibung", "Bernd")])


MENTIONS = [
    Mention(
        datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc),
        "Anna",
        "ACME Angebot",
        "Kommentar",
        "Frage an @Erika",
        url="https://app.asana.com/0/0/t2",
        projects=("Vertrieb",),
    ),
    Mention(datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc), "Bernd", "Bericht", "Beschreibung", "fuer @Erika"),
]
PROFILE = Profile(contexts=(Context("kunde-acme", "kunde", "Kunde ACME", keywords=("acme",)),))


def scripted_model(**args: int) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            if part.part_kind == "tool-return":
                label = "FEHLER" if part.outcome == "retried" else "LISTE"
                return ModelResponse(parts=[TextPart(f"{label}: {part.content}")])
        return ModelResponse(parts=[ToolCallPart("list_mentions", args)])

    return FunctionModel(respond)


class TestAsanaAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.since: list[datetime] = []
        self.mentions = MENTIONS

    def fetch(self, since: datetime) -> list[Mention]:
        self.since.append(since)
        return self.mentions

    def run_agent(self, **args: int) -> str:
        deps = AsanaDeps(now=NOW, fetch=self.fetch, profile=PROFILE)
        return answer_with_notes(asana_agent, "Frage", deps, scripted_model(**args))

    def test_default_period_is_14_days(self) -> None:
        self.run_agent()
        self.assertEqual(self.since, [SINCE])

    def test_model_sees_mentions_grouped_by_task_without_links(self) -> None:
        model_part = self.run_agent().split("\n\n**")[0]
        self.assertIn("Aufgabe: ACME Angebot [Kunde ACME] (Projekte: Vertrieb)", model_part)
        self.assertIn("Anna (Kommentar): Frage an @Erika", model_part)
        self.assertIn("Aufgabe: Bericht\n", model_part)
        self.assertNotIn("https://", model_part)

    def test_full_list_with_links_is_appended(self) -> None:
        notes = self.run_agent(days=7).split("\n\n**")[1]
        self.assertTrue(notes.startswith("Erwähnungen der letzten 7 Tage (2)**\n"), notes)
        self.assertIn("· Anna in „ACME Angebot“ [Kunde ACME] (Kommentar): Frage an @Erika", notes)
        self.assertIn("\n  https://app.asana.com/0/0/t2", notes)
        self.assertIn("· Bernd in „Bericht“ (Beschreibung): fuer @Erika", notes)

    def test_no_mentions(self) -> None:
        self.mentions = []
        self.assertEqual(self.run_agent(), "LISTE: Keine Erwaehnungen in den letzten 14 Tagen.")

    def test_rejects_too_long_period(self) -> None:
        output = self.run_agent(days=MAX_DAYS + 1)
        self.assertTrue(output.startswith("FEHLER:"), output)
        self.assertEqual(self.since, [])


if __name__ == "__main__":
    unittest.main()
