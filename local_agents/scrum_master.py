"""Scrum-Master-Agent: fragt, ob es zu einer User Story etwas Neues gibt, und
vermerkt die Bemerkung an der Story, auf Wunsch fuer ein bestimmtes Daily.

Aufruf:
  python -m local_agents.scrum_master              nachfragen, im Terminal
  python -m local_agents.scrum_master --dialog     nachfragen, als macOS-Dialogfenster
  python -m local_agents.scrum_master notizen      offene Bemerkungen anzeigen
  python -m local_agents.scrum_master besprochen 3 Bemerkung #3 abhaken
  python -m local_agents.scrum_master entfernt 3   Bemerkung #3 entfernen
  python -m local_agents.scrum_master offen 3      Bemerkung #3 wiederherstellen

Die Antwort ist freier Text, etwa "P-1 und P-2 wurden am 30.10. abgeschlossen".
Das lokale Modell ordnet sie den Stories zu (aus den Markdown-Dateien,
``stories.py``); der Code prueft das und speichert je Story einen Vermerk in der
Datenbank (Tabelle ``story_notes``), zusammen mit dem unveraenderten Wortlaut.

Ein Vermerk gehoert nur dann zu einem Daily, wenn die Antwort das sagt ("fuers
Daily am Dienstag", "morgen ansprechen"). Sonst ist er ein Vermerk an der Story
ohne Daily-Bezug, und genannte Tage bleiben Teil des Textes. "Entferne den
Vermerk wieder" nimmt die zuletzt vermerkten Bemerkungen zurueck, "entferne #3"
eine bestimmte.

Fragen und Antworten laufen ueber einen Kanal (``Channel``). Terminal und
Dialogfenster gibt es; weitere Kanaele (etwa ein Messenger) brauchen nur
``ask`` und ``tell``.
"""

import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from local_agents.agent import MODEL_SETTINGS, ollama_model
from local_agents.holidays import WEEKDAYS, is_public_holiday
from local_agents.meeting_types import keyword_hint
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import NOTE_DONE, NOTE_OPEN, NOTE_REMOVED, Store, StoryNote
from local_agents.stories import Story, load_features

AGENT_NAME = "scrum_master"
QUESTION = "Gibt es etwas Neues zu einer User Story?"
FOLLOW_UP = "Noch etwas zu einer anderen Story?"
MAX_ROUNDS = 5
CALENDAR_DAYS = 14
DAY_WORDS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")
NOTHING = re.compile(
    r"(nein|nee|nö|nichts|nix|nichts neues|keine|kein|danke|das war'?s|das wars|fertig|"
    r"passt|ok|okay|im moment|momentan|gerade|aktuell|mehr|alles|war alles|,|\.|!|\s)*",
    re.IGNORECASE,
)


# Woerter, die sagen, dass eine Bemerkung in einem Daily zur Sprache kommen soll.
DAILY_CUE = re.compile(
    r"(?<!\w)(daily|dailys|dailies|stand-?up|ansprechen|anzusprechen|besprechen|"
    r"zu besprechen|thematisieren|zur sprache)(?!\w)",
    re.IGNORECASE,
)
REMOVAL = re.compile(
    r"(?<!\w)(entfern\w*|lösch\w*|loesch\w*|streich\w*|rückgängig|zurücknehmen|"
    r"nimm\w*\b.*\b(raus|zurück)|verwerf\w*|verwirf\w*)(?!\w)",
    re.IGNORECASE,
)
NOTE_NUMBER = re.compile(r"#\s?(\d+)|(?:vermerk|bemerkung|notiz|nummer|nr\.?)\s+(\d+)", re.IGNORECASE)
# Angaben, die im Text einer Bemerkung nicht verloren gehen duerfen.
DETAIL = re.compile(r"\d{1,2}\.\d{1,2}\.(?:\d{4})?|\d{1,2}:\d{2}")


class Channel(Protocol):
    """Weg, auf dem der Agent den Nutzer erreicht."""

    name: str

    def ask(self, question: str) -> str | None:
        """Stellt eine Frage; None, wenn der Nutzer nicht antwortet oder abbricht."""

    def tell(self, text: str) -> None:
        """Teilt etwas mit, ohne eine Antwort zu erwarten."""


class TerminalChannel:
    name = "terminal"

    def ask(self, question: str) -> str | None:
        try:
            return input(f"{question}\n> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    def tell(self, text: str) -> None:
        print(text, flush=True)


class DialogChannel:
    """macOS-Dialogfenster; eignet sich fuer Laeufe ohne Terminal."""

    name = "dialog"
    SCRIPT = (
        "on run argv",
        'set r to display dialog (item 1 of argv) default answer "" with title "Scrum Master" '
        'buttons {"Nichts Neues", "OK"} default button "OK" giving up after 900',
        'if gave up of r or button returned of r is "Nichts Neues" then return ""',
        "return text returned of r",
        "end run",
    )

    def __init__(self, runner=None) -> None:
        self.runner = runner or self._run

    @staticmethod
    def _run(command: list[str]) -> tuple[int, str]:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result.returncode, result.stdout

    def _script(self, lines: tuple[str, ...], text: str) -> tuple[int, str]:
        command = ["osascript"]
        for line in lines:
            command += ["-e", line]
        return self.runner([*command, text])

    def ask(self, question: str) -> str | None:
        try:
            code, output = self._script(self.SCRIPT, question)
        except OSError:
            return None
        return output.strip() if code == 0 else None

    def tell(self, text: str) -> None:
        lines = (
            "on run argv",
            'display dialog (item 1 of argv) with title "Scrum Master" buttons {"OK"} '
            'default button "OK" giving up after 60',
            "end run",
        )
        try:
            self._script(lines, text)
        except OSError:
            pass


class Remark(BaseModel):
    """Eine Bemerkung des Nutzers zu einer Story."""

    story: str | None = Field(
        None, description="Kuerzel oder Name der Story, genau wie in der Liste; null, wenn keine passt"
    )
    text: str = Field(
        description="Die Bemerkung in den Worten des Nutzers, vollstaendig, mit allen genannten "
        "Datums- und Zahlenangaben"
    )
    daily: date | None = Field(
        None,
        description="Nur wenn der Nutzer sagt, in welchem Daily das zur Sprache kommen soll: "
        "dessen Datum, JJJJ-MM-TT; sonst null",
    )


class Remarks(BaseModel):
    remarks: list[Remark] = Field(default_factory=list)


@dataclass
class RemarkDeps:
    today: date
    stories: list[Story]
    answer: str = ""


remark_agent = Agent(
    deps_type=RemarkDeps, output_type=Remarks, model_settings=MODEL_SETTINGS, retries=2
)


@remark_agent.instructions
def instructions(ctx: RunContext[RemarkDeps]) -> str:
    today = ctx.deps.today
    days = ", ".join(
        f"{WEEKDAYS[day.weekday()]} {day.isoformat()}"
        for day in (today + timedelta(days=n) for n in range(CALENDAR_DAYS))
    )
    stories = "\n".join(f"- {story.name} (Feature: {story.feature})" for story in ctx.deps.stories)
    return (
        "Der Nutzer berichtet, was es Neues zu seinen User Stories gibt. Zerlege "
        "seine Antwort in Bemerkungen, je Story eine:\n"
        "- story: die Story aus der Liste unten, die er meint. Er nennt das Kuerzel "
        "(etwa QG-3), den Namen oder ein Stichwort daraus. Gib das Kuerzel zurueck, "
        "bei Stories ohne Kuerzel den Namen, genau wie in der Liste. Passt keine "
        "eindeutig, bleibt story null.\n"
        "- text: was er zu dieser Story sagt, in seinen Worten, vollstaendig und ohne "
        "etwas hinzuzufuegen. Jede Datums-, Zeit- und Zahlenangabe aus seiner Antwort "
        "bleibt im Text ('am 30.10.2026 abgeschlossen'). Weglassen darfst du nur die "
        "Bitte selbst ('schreib bitte rein, dass') und den Namen der Story.\n"
        "- daily: Nur wenn er ausdruecklich sagt, dass es in einem Daily zur Sprache "
        "kommen soll ('fuers Daily am Dienstag', 'morgen ansprechen'): das Datum "
        "dieses Dailys; Wochentage loest du mit der Liste der naechsten Tage auf. In "
        "allen anderen Faellen null, auch wenn im Text ein Datum vorkommt.\n"
        f"Heute ist {WEEKDAYS[today.weekday()]} {today.isoformat()}. Die naechsten Tage: {days}.\n"
        f"Stories:\n{stories or '(keine)'}"
    )


@remark_agent.output_validator
def known_stories_only(ctx: RunContext[RemarkDeps], remarks: Remarks) -> Remarks:
    unknown = [
        r.story for r in remarks.remarks if r.story and find_story(r.story, ctx.deps.stories) is None
    ]
    if unknown:
        raise ModelRetry(
            f"Diese Stories stehen nicht eindeutig in der Liste: {unknown}. "
            "Kuerzel oder Namen genau uebernehmen, sonst null."
        )
    texts = " ".join(remark.text for remark in remarks.remarks)
    lost = [detail for detail in DETAIL.findall(ctx.deps.answer) if detail not in texts]
    if lost and not DAILY_CUE.search(ctx.deps.answer):
        raise ModelRetry(f"Diese Angaben fehlen in text: {lost}. Uebernimm sie woertlich.")
    return remarks


def find_story(reference: str, stories: list[Story]) -> int | None:
    """Position der Story mit diesem Kuerzel oder Namen; None, wenn nicht eindeutig."""
    wanted = reference.strip().casefold()
    if not wanted:
        return None
    for matches in (
        lambda story: story.key.casefold() == wanted,
        lambda story: story.name.casefold() == wanted or story.title.casefold() == wanted,
        lambda story: wanted in story.name.casefold(),
    ):
        found = [index for index, story in enumerate(stories) if matches(story)]
        if len(found) == 1:
            return found[0]
        if found:
            return None
    return None


def mentioned_stories(text: str, stories: list[Story]) -> list[int]:
    """Stories, deren Kuerzel woertlich im Text steht, in der Reihenfolge der Liste."""
    return [
        index
        for index, story in enumerate(stories)
        if story.key and re.search(rf"(?<![\w-]){re.escape(story.key)}(?![\w-])", text, re.IGNORECASE)
    ]


def mentioned_dates(text: str, today: date) -> list[date]:
    """Tage, die der Text nennt: heute, morgen, Wochentage, "20.11." oder "am 20."."""
    lowered = text.casefold()
    found = []
    for word, days in (("übermorgen", 2), ("morgen", 1), ("heute", 0)):
        if re.search(rf"(?<!\w){word}(?!\w)", lowered):
            found.append(today + timedelta(days=days))
            lowered = lowered.replace(word, " ")
    for number, name in enumerate(DAY_WORDS):
        if re.search(rf"(?<!\w){name}s?(?!\w)", lowered):
            # Der naechste solche Tag; der heutige Wochentag meint die kommende Woche.
            found.append(today + timedelta(days=(number - today.weekday()) % 7 or 7))
    for match in re.finditer(r"(?<!\d)(\d{1,2})\.(?:\s?(\d{1,2})\.(?:(\d{4}))?)?(?!\d)", lowered):
        day, month, year = match.group(1), match.group(2), match.group(3)
        if month is None and not re.search(r"\bam\s*$", lowered[: match.start()]):
            continue  # eine nackte Zahl mit Punkt ist kein Datum
        try:
            candidate = date(int(year or today.year), int(month or today.month), int(day))
        except ValueError:
            continue
        if candidate < today and year is None:
            # Ohne Jahr ist der naechste solche Tag gemeint.
            if month is None:
                next_month = today.replace(day=28) + timedelta(days=4)
                try:
                    candidate = date(next_month.year, next_month.month, int(day))
                except ValueError:
                    continue
            else:
                candidate = candidate.replace(year=today.year + 1)
        found.append(candidate)
    return sorted(set(found))


def is_nothing(answer: str) -> bool:
    """Ob die Antwort nur "nichts Neues" bedeutet (auch leer)."""
    return NOTHING.fullmatch(answer.strip()) is not None


def all_stories(profile: Profile) -> list[tuple[str, Story]]:
    """(Kontext-id, Story) fuer alle Stories aus den Ordnern im Profil."""
    return [
        (context.id, story)
        for context in profile.contexts
        if context.stories_dir
        for feature in load_features(Path(context.stories_dir))
        for story in feature.stories
    ]


def next_daily(store: Store, now: datetime, context: str = "", state: str = "") -> date:
    """Tag des naechsten Dailys: aus den geplanten Terminen, sonst der naechste Werktag."""
    for event in store.events(now.date(), now.date() + timedelta(days=CALENDAR_DAYS)):
        hint = keyword_hint(event.title)
        if event.start > now and hint and hint.id == "daily" and context in ("", event.context):
            return event.start.date()
    day = now.date() + timedelta(days=1)
    while day.weekday() >= 5 or (state and is_public_holiday(day, state)):
        day += timedelta(days=1)
    return day


@dataclass
class ScrumMaster:
    """Eine Nachfrage-Runde: fragen, Antwort einordnen, Bemerkungen speichern."""

    store: Store
    profile: Profile
    model: object
    now: datetime
    stories: list[tuple[str, Story]] = field(default_factory=list)

    def record(self, answer: str, source: str) -> list[StoryNote]:
        """Ordnet die Antwort ein und speichert die Bemerkungen."""
        stories = [story for _, story in self.stories]
        today = self.now.date()
        deps = RemarkDeps(today=today, stories=stories, answer=answer)
        # Was woertlich in der Antwort steht, geht vor: Kuerzel und Tage liest der Code selbst.
        keys_in_answer = mentioned_stories(answer, stories)
        try:
            remarks = remark_agent.run_sync(answer, deps=deps, model=self.model).output.remarks
        except UnexpectedModelBehavior:
            remarks = []
        if not remarks:
            # Nichts Verwertbares vom Modell: den Wortlaut trotzdem festhalten,
            # je genanntem Kuerzel einmal.
            remarks = [Remark(story=stories[index].key, text=answer) for index in keys_in_answer]
            remarks = remarks or [Remark(text=answer)]
        # Ein Daily-Bezug entsteht nur, wenn die Antwort ihn ausspricht.
        for_daily = bool(DAILY_CUE.search(answer))
        dates_in_answer = [day for day in mentioned_dates(answer, today) if day >= today]
        notes = []
        for remark in remarks:
            text = " ".join(remark.text.split()) or " ".join(answer.split())
            keys_in_text = mentioned_stories(text, stories)
            if len(keys_in_text) == 1:
                position = keys_in_text[0]
            elif len(keys_in_answer) == 1 and len(remarks) == 1:
                position = keys_in_answer[0]
            else:
                position = find_story(remark.story, stories) if remark.story else None
            context, story = self.stories[position] if position is not None else ("", None)
            if not for_daily:
                daily = None
            elif len(dates_in_answer) == 1:
                daily = dates_in_answer[0]
            elif remark.daily in dates_in_answer:
                daily = remark.daily
            else:
                daily = next_daily(self.store, self.now, context, self.profile.state)
            notes.append(
                self.store.add_story_note(
                    story.name if story else "",
                    story.feature if story else "",
                    text,
                    daily,
                    answer.strip(),
                    source,
                )
            )
        return notes

    def remove(self, answer: str, last: list[StoryNote]) -> str:
        """Nimmt Vermerke zurueck: die genannten Nummern, sonst die zuletzt vermerkten."""
        numbers = [int(a or b) for a, b in NOTE_NUMBER.findall(answer)]
        if not numbers:
            numbers = [note.id for note in last]
        if not numbers:
            newest = [note for note in self.store.story_notes((NOTE_OPEN,))]
            numbers = [max(newest, key=lambda note: note.id).id] if newest else []
        removed = [number for number in numbers if self.store.set_note_status(number, NOTE_REMOVED)]
        if not removed:
            return "Es gibt keinen Vermerk, den ich entfernen könnte."
        return "Entfernt: " + ", ".join(f"#{number}" for number in removed)

    def check_in(self, channel: Channel) -> list[StoryNote]:
        """Fragt nach, bis nichts mehr kommt; gibt die neuen Bemerkungen zurueck."""
        run_id = self.store.start_run(AGENT_NAME)
        saved: list[StoryNote] = []
        last: list[StoryNote] = []
        try:
            question = QUESTION
            for _ in range(MAX_ROUNDS):
                answer = channel.ask(question)
                if answer is None or is_nothing(answer):
                    break
                if is_removal(answer):
                    channel.tell(self.remove(answer, last))
                    gone = {note.id for note in self.store.story_notes((NOTE_REMOVED,))}
                    saved = [note for note in saved if note.id not in gone]
                    last = []
                else:
                    last = self.record(answer, channel.name)
                    saved.extend(last)
                    channel.tell("\n".join(["Vermerkt:", *(note_line(note) for note in last)]))
                question = FOLLOW_UP
        except BaseException:
            self.store.finish_run(run_id, "fehler")
            raise
        self.store.finish_run(run_id, "ok", f"{len(saved)} Bemerkungen über {channel.name}")
        return saved


def is_removal(answer: str) -> bool:
    """Ob die Antwort verlangt, einen Vermerk zurueckzunehmen."""
    return REMOVAL.search(answer) is not None


def _day(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m.%Y}"


def note_line(note: StoryNote) -> str:
    where = note.story or "ohne Story (nicht zugeordnet)"
    tags = [f"Daily {_day(note.daily_date)}"] if note.daily_date else []
    if note.status != NOTE_OPEN:
        tags.append(note.status)
    return f"- #{note.id} {where}: {note.text}" + (f" [{', '.join(tags)}]" if tags else "")


def format_notes(notes: list[StoryNote]) -> str:
    """Bemerkungen gruppiert: zuerst die ohne Daily-Bezug, dann je Daily."""
    if not notes:
        return "Keine Bemerkungen."
    lines = []
    current: object = ""
    for note in notes:
        if note.daily_date != current:
            current = note.daily_date
            lines.append(f"Daily {_day(current)}" if current else "Vermerke ohne Daily-Bezug")
        where = note.story or "ohne Story (nicht zugeordnet)"
        status = "" if note.status == NOTE_OPEN else f" [{note.status}]"
        lines.append(f"  #{note.id} {where}: {note.text}{status}")
    return "\n".join(lines)


def main(argv: list[str], store: Store | None = None) -> int:
    args = argv[1:]
    store = Store() if store is None else store
    if args[:1] == ["notizen"] and len(args) <= 2:
        statuses = None if args[1:] == ["alle"] else (NOTE_OPEN,)
        print(format_notes(store.story_notes(statuses)))
        return 0
    if len(args) == 2 and args[0] in (NOTE_DONE, NOTE_REMOVED, NOTE_OPEN) and args[1].lstrip("#").isdigit():
        number = int(args[1].lstrip("#"))
        if store.set_note_status(number, args[0]):
            print(f"Bemerkung #{number}: {args[0]}.")
            return 0
        print(f"Bemerkung #{number} gibt es nicht.", file=sys.stderr)
        return 1
    if args not in ([], ["--dialog"]):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    stories = all_stories(profile)
    if not stories:
        print("Keine Stories gefunden. stories_dir im Profil setzen, siehe README.", file=sys.stderr)
        return 1
    agent = ScrumMaster(
        store, profile, ollama_model(profile.model_for(AGENT_NAME)), datetime.now(), stories
    )
    agent.check_in(DialogChannel() if args else TerminalChannel())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
