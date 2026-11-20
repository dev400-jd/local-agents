"""Was je Agent gemessen wird: Eingaben, Ausfuehrung und Pruefer.

Je Agent gehoeren dazu:

- die Eingaben eines Falls (``...Inputs``, mit der Umgebung, in der er spielt),
- die Aufgabe, die den Agenten gegen eine frische Datenbank ausfuehrt und den
  Endzustand zurueckgibt (``..._task``),
- der Pruefer, der den Endzustand mit der Erwartung aus der Falldatei
  vergleicht (ein ``Evaluator`` von pydantic-evals).

Das Messprogramm selbst (Faelle laden, wiederholen, berichten) steht in ``evals.py``.
"""

import dataclasses
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_evals import Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from local_agents import ask, feedback, meeting_agent, message_agent, scrum_master, search, status_agent, stories
from local_agents.checks import invented_numbers
from local_agents.meeting_types import BY_ID
from local_agents.profile import CUSTOMER, INTERNAL, Context, Profile, load_profile
from local_agents.store import IN_PROGRESS, NOTE_OPEN, NOTE_REMOVED, OPEN, REJECTED, Store, StoredEvent
from local_agents.stories import STORY_HEADING, Story, load_features

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_SAID_CHARS = 300
# Ein haengender Modellaufruf zaehlt nach so vielen Sekunden als gescheiterter
# Lauf, statt die ganze Messung aufzuhalten.
TASK_TIMEOUT = 120.0


class Strict(BaseModel):
    """Unbekannte Schluessel sind ein Fehler, damit Tippfehler in Falldateien auffallen."""

    model_config = ConfigDict(extra="forbid")


def model_said(messages: list) -> str:
    """Die letzte Antwort des Modells in einer Zeile: Text oder Argumente des Werkzeugaufrufs."""
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            parts = []
            for part in message.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content)
                elif isinstance(part, ToolCallPart):
                    parts.append(part.args if isinstance(part.args, str) else json.dumps(part.args, ensure_ascii=False))
            text = " ".join(" ".join(parts).split())
            return text[:MAX_SAID_CHARS] + ("…" if len(text) > MAX_SAID_CHARS else "")
    return ""


def _known_keys(items: list[dict], allowed: set[str], where: str) -> None:
    for item in items:
        unknown = sorted(set(item) - allowed)
        if unknown:
            raise ValueError(f"{where}: unbekannte Schluessel {unknown}. Erlaubt: {sorted(allowed)}.")


# --- Scrum Master


class StoryItem(Strict):
    kuerzel: str = ""
    titel: str
    feature: str = ""


class ScrumSetup(Strict):
    """Was der Scrum Master im Fall kennt: das Datum und die Stories."""

    heute: date
    stories: list[StoryItem] = []
    stories_dir: str = ""  # statt stories: ein Ordner mit Markdown-Dateien


class ScrumInputs(Strict):
    umgebung: ScrumSetup
    eingabe: str
    vorher: list[str] = []  # fruehere Antworten derselben Runde, ungeprueft


class NoteOut(BaseModel):
    story: str  # Kuerzel, sonst Name; leer = keiner Story zugeordnet
    daily: date | None
    text: str


class ScrumOutput(BaseModel):
    """Endzustand nach der Eingabe."""

    bemerkungen: list[NoteOut] = []
    entfernt: int = 0
    modell: str = ""  # letzte Antwort des Modells, woertlich


def _key(story_name: str) -> str:
    """Kuerzel einer gespeicherten Story, sonst ihr Name."""
    match = STORY_HEADING.match(story_name)
    return match.group(1) if match else story_name


def scrum_task(model) -> Callable[[ScrumInputs], ScrumOutput]:
    def run(inputs: ScrumInputs) -> ScrumOutput:
        setup = inputs.umgebung
        if setup.stories_dir:
            folder = Path(setup.stories_dir).expanduser()
            stories = [("", story) for feature in load_features(folder) for story in feature.stories]
        else:
            stories = [("", Story(item.kuerzel, item.titel, item.feature)) for item in setup.stories]
        today = setup.heute
        store = Store(":memory:")
        try:
            agent = scrum_master.ScrumMaster(
                store, Profile(), model, datetime(today.year, today.month, today.day, 9, 0), stories
            )
            for earlier in inputs.vorher:
                agent.respond(earlier, "messung")
            before = {note.id for note in store.story_notes()}
            with capture_run_messages() as messages:
                agent.respond(inputs.eingabe, "messung")
            new = [note for note in store.story_notes((NOTE_OPEN,)) if note.id not in before]
            return ScrumOutput(
                bemerkungen=[NoteOut(story=_key(n.story), daily=n.daily_date, text=n.text) for n in new],
                entfernt=len(store.story_notes((NOTE_REMOVED,))),
                modell=model_said(messages),
            )
        finally:
            store.close()

    return run


@dataclass
class Bemerkungen(Evaluator[ScrumInputs, ScrumOutput, Any]):
    """Prueft die neu vermerkten Bemerkungen und die Zahl der zurueckgenommenen.

    Je erwarteter Bemerkung: ``story`` (Kuerzel; "" = keiner zugeordnet),
    ``daily`` (Datum oder false = ohne Daily-Bezug; fehlt = nicht geprueft),
    ``enthaelt`` und ``enthaelt_nicht`` (Textstuecke).
    """

    erwartet: list[dict] = field(default_factory=list)
    entfernt: int = 0

    def __post_init__(self) -> None:
        _known_keys(self.erwartet, {"story", "daily", "enthaelt", "enthaelt_nicht"}, "Bemerkungen.erwartet")

    def evaluate(self, ctx: EvaluatorContext[ScrumInputs, ScrumOutput, Any]) -> EvaluationReason:
        problems = []
        new = list(ctx.output.bemerkungen)
        if len(new) != len(self.erwartet):
            problems.append(f"{len(self.erwartet)} Bemerkungen erwartet, {len(new)} vermerkt")
        if ctx.output.entfernt != self.entfernt:
            problems.append(f"{self.entfernt} entfernt erwartet, {ctx.output.entfernt} entfernt")
        for want in self.erwartet:
            story = want.get("story", "")
            note = next((n for n in new if n.story.casefold() == story.casefold()), None)
            label = story or "ohne Story"
            if note is None:
                got = ", ".join(n.story or "ohne Story" for n in new) or "nichts"
                problems.append(f"Bemerkung zu {label} fehlt (übrig: {got})")
                continue
            new.remove(note)
            if "daily" in want:
                daily = want["daily"] or None
                if note.daily != daily:
                    problems.append(f"{label}: Daily {daily or 'keins'} erwartet, {note.daily or 'keins'} vermerkt")
            text = note.text.casefold()
            for part in want.get("enthaelt", []):
                if part.casefold() not in text:
                    problems.append(f"{label}: „{part}“ fehlt im Text")
            for part in want.get("enthaelt_nicht", []):
                if part.casefold() in text:
                    problems.append(f"{label}: „{part}“ gehört nicht in den Text")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Rueckmeldung auf den Plan


class TaskItem(Strict):
    titel: str
    faellig: date


class FeedbackSetup(Strict):
    """Der Plan im Fall: Die Aufgaben bekommen die Nummern 1, 2, 3 ... in dieser Reihenfolge."""

    heute: date
    aufgaben: list[TaskItem] = []


class FeedbackInputs(Strict):
    umgebung: FeedbackSetup
    eingabe: str


class NewTaskOut(BaseModel):
    titel: str
    faellig: date


class FeedbackOutput(BaseModel):
    """Endzustand nach der Eingabe."""

    raus: list[int] = []
    neu: list[NewTaskOut] = []
    unklar: bool = False
    modell: str = ""


def feedback_task(model) -> Callable[[FeedbackInputs], FeedbackOutput]:
    def run(inputs: FeedbackInputs) -> FeedbackOutput:
        store = Store(":memory:")
        try:
            for item in inputs.umgebung.aufgaben:
                store.add_task("vorbereitung", item.titel, "intern", item.faellig, "messung")
            before = {task.id for task in store.tasks()}
            with capture_run_messages() as messages:
                outcome = feedback.handle(inputs.eingabe, store, Profile(), inputs.umgebung.heute, model)
            tasks = store.tasks()
            return FeedbackOutput(
                raus=sorted(task.id for task in tasks if task.status == REJECTED),
                neu=[
                    NewTaskOut(titel=task.title, faellig=task.due_date)
                    for task in tasks
                    if task.id not in before and task.status in (OPEN, IN_PROGRESS)
                ],
                unklar=any(line.startswith("- nicht verstanden") for line in outcome.lines),
                modell=model_said(messages),
            )
        finally:
            store.close()

    return run


@dataclass
class Aufgaben(Evaluator[FeedbackInputs, FeedbackOutput, Any]):
    """Prueft, welche Aufgaben herausgenommen und welche ergaenzt wurden.

    ``raus``: genau diese Nummern. Je neuer Aufgabe ``titel_enthaelt`` und
    optional ``faellig``. ``unklar``: ob der Agent nachfragt (fehlt = nicht geprueft).
    """

    raus: list[int] = field(default_factory=list)
    neu: list[dict] = field(default_factory=list)
    unklar: bool | None = None

    def __post_init__(self) -> None:
        _known_keys(self.neu, {"titel_enthaelt", "faellig"}, "Aufgaben.neu")

    def evaluate(self, ctx: EvaluatorContext[FeedbackInputs, FeedbackOutput, Any]) -> EvaluationReason:
        problems = []
        output = ctx.output
        if output.raus != sorted(self.raus):
            problems.append(f"raus erwartet {sorted(self.raus)}, herausgenommen {output.raus}")
        added = list(output.neu)
        if len(added) != len(self.neu):
            got = ", ".join(task.titel for task in added) or "nichts"
            problems.append(f"{len(self.neu)} neue Aufgaben erwartet, {len(added)} angelegt ({got})")
        for want in self.neu:
            part = want.get("titel_enthaelt", "")
            task = next((t for t in added if part.casefold() in t.titel.casefold()), None)
            if task is None:
                problems.append(f"neue Aufgabe mit „{part}“ fehlt")
                continue
            added.remove(task)
            if "faellig" in want and task.faellig != want["faellig"]:
                problems.append(f"„{part}“: fällig {want['faellig']} erwartet, {task.faellig} angelegt")
        if self.unklar is not None and output.unklar != self.unklar:
            problems.append("Rückfrage erwartet, keine gestellt" if self.unklar else "unnötige Rückfrage")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Status-Agent


class StatusTaskItem(Strict):
    titel: str
    faellig: date
    termin: datetime | None = None  # Beginn des Termins hinter der Aufgabe


class StatusSetup(Strict):
    """Zeitpunkt der Abfrage und die offenen Aufgaben (Nummern 1, 2, 3 ... in dieser Reihenfolge)."""

    jetzt: datetime
    aufgaben: list[StatusTaskItem] = []


class StatusInputs(Strict):
    umgebung: StatusSetup
    eingabe: str = status_agent.DEFAULT_QUESTION


class StatusOutput(BaseModel):
    antwort: str  # die Saetze des Modells, ohne die vom Code angehaengten Zeilen
    naechste: int | None  # Nummer der Aufgabe, die der Code als naechste bestimmt hat
    fakten: str  # was das Modell zu sehen bekam
    modell: str = ""


def status_task(model) -> Callable[[StatusInputs], StatusOutput]:
    def run(inputs: StatusInputs) -> StatusOutput:
        store = Store(":memory:")
        try:
            for number, item in enumerate(inputs.umgebung.aufgaben, 1):
                if item.termin is None:
                    store.add_task("manuell", item.titel, INTERNAL, item.faellig, "messung")
                    continue
                event_id = f"messung-{number}"
                store.upsert_event(
                    StoredEvent(event_id, item.titel, item.termin, item.termin + timedelta(minutes=30), INTERNAL)
                )
                store.upsert_task(event_id, "vorbereitung", item.titel, INTERNAL, item.faellig, "messung")
            deps = status_agent.StatusDeps(now=inputs.umgebung.jetzt, store=store)
            with capture_run_messages() as messages:
                answer = status_agent.status_agent.run_sync(inputs.eingabe, model=model, deps=deps).output
            facts = "\n".join([inputs.eingabe, *(f"{head}\n" + "\n".join(lines) for head, lines in deps.notes.items())])
            return StatusOutput(
                antwort=answer.strip(),
                naechste=deps.item.task.id if deps.item else None,
                fakten=facts,
                modell=model_said(messages),
            )
        finally:
            store.close()

    return run


@dataclass
class Status(Evaluator[StatusInputs, StatusOutput, Any]):
    """Prueft die Saetze des Status-Agenten.

    ``naechste``: Nummer der Aufgabe, die dran ist ("keine" = es steht nichts an;
    leer = nicht geprueft). ``nennt`` / ``nennt_nicht``: Textstuecke. ``verboten``:
    Wendungen, die nie vorkommen duerfen. Zahlen der Antwort muessen aus der
    Eingabe stammen (``zahlen_aus_eingabe``).
    """

    naechste: int | str = ""
    nennt: list[str] = field(default_factory=list)
    nennt_nicht: list[str] = field(default_factory=list)
    verboten: list[str] = field(default_factory=lambda: ["du musst", "du solltest"])
    hoechstens_zeichen: int = 400
    zahlen_aus_eingabe: bool = True

    def evaluate(self, ctx: EvaluatorContext[StatusInputs, StatusOutput, Any]) -> EvaluationReason:
        problems = []
        output = ctx.output
        if self.naechste != "":
            wanted = None if self.naechste == "keine" else self.naechste
            if output.naechste != wanted:
                problems.append(f"Aufgabe {wanted or 'keine'} erwartet, der Code wählte {output.naechste or 'keine'}")
        text = output.antwort.casefold()
        if not text:
            problems.append("leere Antwort")
        for part in self.nennt:
            if part.casefold() not in text:
                problems.append(f"„{part}“ fehlt in der Antwort")
        for part in [*self.nennt_nicht, *self.verboten]:
            if part.casefold() in text:
                problems.append(f"„{part}“ gehört nicht in die Antwort")
        if len(output.antwort) > self.hoechstens_zeichen:
            problems.append(f"Antwort zu lang ({len(output.antwort)} Zeichen)")
        if self.zahlen_aus_eingabe:
            invented = invented_numbers(output.antwort, output.fakten)
            if invented:
                problems.append(f"Zahlen nicht aus der Eingabe: {', '.join(invented)}")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Termin-Agent: Terminart


class TerminSetup(Strict):
    """Die Rollen des Nutzers, wie sie im Profil stuenden."""

    rolle_intern: str = ""
    rolle_kunde: str = ""
    kunde: str = "Kunde ACME"


class TerminInputs(Strict):
    umgebung: TerminSetup
    titel: str
    beginn: datetime
    minuten: int = 30
    eingeladene: int = 4
    kontext: Literal["intern", "kunde"] = "intern"


class TerminOutput(BaseModel):
    art: str
    begruendung: str
    ergebnisse: list[str]
    modell: str = ""


def termin_task(model) -> Callable[[TerminInputs], TerminOutput]:
    def run(inputs: TerminInputs) -> TerminOutput:
        setup = inputs.umgebung
        profile = Profile(
            contexts=(
                Context(INTERNAL, INTERNAL, "Intern", role=setup.rolle_intern),
                Context(CUSTOMER, CUSTOMER, setup.kunde, role=setup.rolle_kunde),
            )
        )
        store = Store(":memory:")
        try:
            event = StoredEvent(
                "messung",
                inputs.titel,
                inputs.beginn,
                inputs.beginn + timedelta(minutes=inputs.minuten),
                inputs.kontext,
                attendees=inputs.eingeladene,
            )
            store.upsert_event(event)
            store.upsert_task(event.id, "vorbereitung", f"Vorbereitung: {inputs.titel}", inputs.kontext, inputs.beginn.date(), "messung")
            with capture_run_messages() as messages:
                brief = meeting_agent.analyse(store.tasks()[0], event, profile, store, model, fresh=True)
            return TerminOutput(
                art=brief.meeting_type,
                begruendung=brief.reason,
                ergebnisse=list(brief.deliverables),
                modell=model_said(messages),
            )
        finally:
            store.close()

    return run


@dataclass
class Terminart(Evaluator[TerminInputs, TerminOutput, Any]):
    """Prueft die erkannte Terminart.

    ``art``: die richtige id aus ``meeting_types.py`` oder mehrere vertretbare.
    ``nicht``: ids, die falsch waeren, wenn es keine eindeutig richtige gibt
    (etwa bei einem irrefuehrenden Stichwort im Titel). Zusaetzlich: Das Modell
    hat selbst geantwortet (kein Rueckfall auf den Katalog) und nennt Ergebnisse
    in erlaubter Zahl.
    """

    art: str | list[str] = ""
    nicht: list[str] = field(default_factory=list)

    def _allowed(self) -> list[str]:
        return ([self.art] if self.art else []) if isinstance(self.art, str) else list(self.art)

    def __post_init__(self) -> None:
        unknown = [name for name in [*self._allowed(), *self.nicht] if name not in BY_ID]
        if unknown or not (self._allowed() or self.nicht):
            raise ValueError(
                f"Terminart: art oder nicht angeben; unbekannt: {unknown}. Erlaubt: {', '.join(BY_ID)}."
            )

    def evaluate(self, ctx: EvaluatorContext[TerminInputs, TerminOutput, Any]) -> EvaluationReason:
        problems = []
        output = ctx.output
        allowed = self._allowed()
        if allowed and output.art not in allowed:
            problems.append(f"Terminart {' oder '.join(allowed)} erwartet, erkannt {output.art}")
        if output.art in self.nicht:
            problems.append(f"Terminart {output.art} ist hier falsch")
        if output.begruendung.startswith("Das Modell konnte den Termin nicht einordnen"):
            problems.append("Modell lieferte nichts Brauchbares (Rückfall auf den Katalog)")
        count = len(output.ergebnisse)
        if not meeting_agent.MIN_DELIVERABLES <= count <= meeting_agent.MAX_DELIVERABLES:
            problems.append(f"{count} Ergebnisse statt {meeting_agent.MIN_DELIVERABLES} bis {meeting_agent.MAX_DELIVERABLES}")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Story-Erkennung durch das Modell


class StoriesInputs(Strict):
    """Ein Markdown-Dokument: direkt als Text oder als Datei.

    Ein relativer Pfad gilt ab dem Repo. ``ohne_kuerzel`` entfernt Kuerzel wie
    "QG-1:" aus den Ueberschriften, bevor das Modell sie sieht; so laesst sich an
    einer Datei mit Kuerzeln messen, ob das Modell die Stories auch ohne erkennt.
    """

    markdown: str = ""
    datei: str = ""
    ohne_kuerzel: bool = False


class StoriesOutput(BaseModel):
    erkannt: list[str]  # Ueberschriften, die das Modell als Stories markiert hat
    nach_regel: list[str]  # Ueberschriften, die die Regel (Kuerzel) als Stories erkennt
    modell: str = ""


def _plain(heading: str) -> str:
    match = STORY_HEADING.match(heading)
    return match.group(2) if match else heading


def stories_task(model) -> Callable[[StoriesInputs], StoriesOutput]:
    def run(inputs: StoriesInputs) -> StoriesOutput:
        text = inputs.markdown
        if inputs.datei:
            path = Path(inputs.datei).expanduser()
            text = (path if path.is_absolute() else REPO_ROOT / path).read_text(encoding="utf-8")
        sections = stories.outline(text)[1]
        by_rule = sorted(stories.rule_stories(sections))
        shown = sections
        if inputs.ohne_kuerzel:
            shown = [dataclasses.replace(section, heading=_plain(section.heading)) for section in sections]
        with capture_run_messages() as messages:
            marked = stories._without_groups(shown, stories.model_stories(shown, model))
        return StoriesOutput(
            erkannt=[shown[index].heading for index in sorted(marked)],
            nach_regel=[shown[index].heading for index in by_rule],
            modell=model_said(messages),
        )

    return run


@dataclass
class Stories(Evaluator[StoriesInputs, StoriesOutput, Any]):
    """Prueft, welche Ueberschriften das Modell als Stories erkennt.

    ``erwartet``: genau diese Ueberschriften. ``wie_regel``: stattdessen die
    Ueberschriften, die die Regel an den Kuerzeln erkennt (fuer Dateien mit Kuerzeln).
    """

    erwartet: list[str] = field(default_factory=list)
    wie_regel: bool = False

    def evaluate(self, ctx: EvaluatorContext[StoriesInputs, StoriesOutput, Any]) -> EvaluationReason:
        expected = ctx.output.nach_regel if self.wie_regel else self.erwartet
        found = ctx.output.erkannt
        problems = []
        missing = [heading for heading in expected if heading not in found]
        extra = [heading for heading in found if heading not in expected]
        if missing:
            problems.append(f"nicht erkannt ({len(missing)} von {len(expected)}): {'; '.join(missing[:5])}")
        if extra:
            problems.append(f"zu viel erkannt ({len(extra)}): {'; '.join(extra[:5])}")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Nachrichten-Agent


class NachrichtSetup(Strict):
    """Wie die Nachricht klingen soll: Absender und Anrede aus dem Profil."""

    absender: str = "Alex Beispiel"
    anrede: Literal["du", "Sie"] = "du"


class NachrichtInputs(Strict):
    umgebung: NachrichtSetup
    aufgabe: str  # Titel der Verschiebe-Aufgabe, wie der Planungs-Agent ihn schreibt
    titel: str  # Titel des Termins
    beginn: datetime
    organisator: str = ""  # Anzeigename aus dem Kalender
    vorgaben: list[str] = []  # gemerkte Vorgaben des Nutzers


class NachrichtOutput(BaseModel):
    text: str
    quelle: str  # "modell" oder "vorlage"
    maengel: list[str]  # was die Selbstpruefung des Agenten am Text bemaengelt
    modell: str = ""


def nachricht_task(model) -> Callable[[NachrichtInputs], NachrichtOutput]:
    def run(inputs: NachrichtInputs) -> NachrichtOutput:
        profile = Profile(
            name=inputs.umgebung.absender,
            message_form=inputs.umgebung.anrede,
            contexts=(Context(INTERNAL, INTERNAL, "Intern"),),
        )
        store = Store(":memory:")
        try:
            event = StoredEvent(
                "messung",
                inputs.titel,
                inputs.beginn,
                inputs.beginn + timedelta(minutes=60),
                INTERNAL,
                organizer=inputs.organisator,
            )
            store.upsert_event(event)
            store.upsert_task(event.id, message_agent.MOVE, inputs.aufgabe, INTERNAL, inputs.beginn.date(), "messung")
            for item in inputs.vorgaben:
                store.add_preference(message_agent.AGENT_NAME, item)
            task = store.tasks()[0]
            with capture_run_messages() as messages:
                result = message_agent.draft(task, event, profile, store, model)
            request = message_agent.build_request(task, event, profile)
            return NachrichtOutput(
                text=result.text,
                quelle=result.source,
                maengel=message_agent.problems(result.text, request),
                modell=model_said(messages),
            )
        finally:
            store.close()

    return run


@dataclass
class Nachricht(Evaluator[NachrichtInputs, NachrichtOutput, Any]):
    """Prueft den Entwurf einer Nachricht.

    Immer geprueft: Das Modell hat den Entwurf selbst geschrieben (kein
    Rueckfall auf die Vorlage), und die Selbstpruefung des Agenten findet
    nichts (Termin, Datum, Anrede, Grussformel, keine erfundene Zahl, kein
    Platzhalter).
    ``nennt`` / ``nennt_nicht``: Textstuecke. ``eines_von``: Mindestens eines
    dieser Textstuecke muss vorkommen (etwa Wendungen fuer eine Absage).
    """

    nennt: list[str] = field(default_factory=list)
    nennt_nicht: list[str] = field(default_factory=list)
    eines_von: list[str] = field(default_factory=list)

    def evaluate(self, ctx: EvaluatorContext[NachrichtInputs, NachrichtOutput, Any]) -> EvaluationReason:
        output = ctx.output
        problems = list(output.maengel)
        if output.quelle == message_agent.TEMPLATE:
            problems.append("Modell lieferte nichts Brauchbares (Rückfall auf die Vorlage)")
        text = output.text.casefold()
        for part in self.nennt:
            if part.casefold() not in text:
                problems.append(f"„{part}“ fehlt im Entwurf")
        for part in self.nennt_nicht:
            if part.casefold() in text:
                problems.append(f"„{part}“ gehört nicht in den Entwurf")
        if self.eines_von and not any(part.casefold() in text for part in self.eines_von):
            problems.append(f"keines von {', '.join(self.eines_von)} im Entwurf")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Suche (ohne Modell)


class SucheBemerkung(Strict):
    """Eine Bemerkung aus dem Scrum Master, die durchsuchbar sein soll."""

    text: str
    story: str = ""


class SucheInputs(Strict):
    frage: str
    # Ordner mit Markdown-Dateien, relativ zum Repo. Leer: der echte Bestand
    # (Profil und Agenten-Datenbank), fuer Faelle aus dem Alltag.
    ordner: str = ""
    bemerkungen: list[SucheBemerkung] = []
    treffer: int = search.DEFAULT_LIMIT  # so viele Treffer werden geholt


class SucheOutput(BaseModel):
    titel: list[str]  # Titel der Treffer, der beste zuerst
    modell: str = ""  # mit Bedeutungssuche: wodurch und mit welcher Naehe jeder Treffer gefunden wurde


def suche_task(model: object) -> Callable[[SucheInputs], SucheOutput]:
    """Die Aufgabe fuer pydantic-evals: Index aufbauen, suchen, Titel der Treffer zurueckgeben.

    ``model`` ist None (nur Wortsuche) oder der Name eines Embedding-Modells;
    dann laeuft die Bedeutungssuche mit.
    """
    embedder = None
    if isinstance(model, str) and model:
        ask = search.ollama_embedder(model)
        known: dict[str, list[float]] = {}  # dieselben Dokumente in jedem Fall: nur einmal berechnen

        def embedder(texts: list[str]) -> list[list[float]]:
            missing = [text for text in dict.fromkeys(texts) if text not in known]
            if missing:
                known.update(zip(missing, ask(missing)))
            return [known[text] for text in texts]

    def run(inputs: SucheInputs) -> SucheOutput:
        index = search.Index(":memory:")
        try:
            if inputs.ordner:
                store = Store(":memory:")
                for remark in inputs.bemerkungen:
                    store.add_story_note(remark.story, "", remark.text)
                passages = search.story_passages("Beispiel", REPO_ROOT / inputs.ordner)
                passages += search.store_passages(store, Profile())
            else:
                store = Store()
                passages = search.collect(load_profile(), store)
            store.close()
            index.update(passages)
            if embedder is not None:
                index.embed_missing(embedder, str(model))
            hits = index.search(inputs.frage, inputs.treffer, embedder=embedder)
            said = "; ".join(
                f"{hit.passage.title.split(' (')[0]} [{hit.found_by}"
                + (f" {hit.similarity:.2f}]" if hit.similarity is not None else "]")
                for hit in hits
            )
            return SucheOutput(titel=[hit.passage.title for hit in hits], modell=said if embedder else "")
        finally:
            index.close()

    return run


@dataclass
class Treffer(Evaluator[SucheInputs, SucheOutput, Any]):
    """Prueft die Rangfolge der Suche an den Titeln der Treffer.

    ``erwartet``: Jedes dieser Textstuecke muss im Titel eines Treffers unter
    den ersten ``unter_den_ersten`` stehen. ``zuerst``: steht im Titel des
    besten Treffers. ``nicht``: steht in keinem Titel. ``leer``: Es darf keinen
    Treffer geben.
    """

    erwartet: list[str] = field(default_factory=list)
    unter_den_ersten: int = 3
    zuerst: str = ""
    nicht: list[str] = field(default_factory=list)
    leer: bool = False

    def evaluate(self, ctx: EvaluatorContext[SucheInputs, SucheOutput, Any]) -> EvaluationReason:
        titles = ctx.output.titel
        top = [title.casefold() for title in titles[: self.unter_den_ersten]]
        problems = []
        if self.leer and titles:
            problems.append("es sollte keinen Treffer geben")
        for part in self.erwartet:
            if not any(part.casefold() in title for title in top):
                problems.append(f"„{part}“ nicht unter den ersten {self.unter_den_ersten}")
        if self.zuerst and (not titles or self.zuerst.casefold() not in titles[0].casefold()):
            problems.append(f"„{self.zuerst}“ ist nicht der beste Treffer")
        for part in self.nicht:
            if any(part.casefold() in title.casefold() for title in titles):
                problems.append(f"„{part}“ gehört nicht zu den Treffern")
        if problems:
            found = "; ".join(f"{rank}. {title}" for rank, title in enumerate(titles, start=1)) or "nichts"
            problems.append(f"gefunden: {found}")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Gespraechs-Agent


class FragenInputs(Strict):
    frage: str
    # Ordner mit Markdown-Dateien, relativ zum Repo. Leer: der echte Bestand.
    ordner: str = ""
    bemerkungen: list[SucheBemerkung] = []
    thema: str = ""  # Kuerzel der Story aus der vorigen Antwort, fuer Anschlussfragen
    vorher: list[str] = []  # voriger Wortwechsel: Frage, Antwort, Frage, Antwort ...


class FragenOutput(BaseModel):
    antwort: str
    quelle: str  # "modell", "code" (nichts gefunden) oder "fundstellen" (Modell lieferte nichts Belegtes)
    gewaehlt: list[str]  # Titel der Eintraege, die das Modell bekam
    wie: str  # wie der Code ausgewaehlt hat
    maengel: list[str]  # was die Selbstpruefung an der Antwort findet
    modell: str = ""


def fragen_task(model: object) -> Callable[[FragenInputs], FragenOutput]:
    """Die Aufgabe fuer pydantic-evals: Eintraege auswaehlen, antworten lassen, Antwort pruefen."""

    def run(inputs: FragenInputs) -> FragenOutput:
        index = search.Index(":memory:")
        try:
            if inputs.ordner:
                store = Store(":memory:")
                for remark in inputs.bemerkungen:
                    store.add_story_note(remark.story, "", remark.text)
                passages = search.story_passages("Beispiel", REPO_ROOT / inputs.ordner)
                passages += search.store_passages(store, Profile())
            else:
                store = Store()
                passages = search.collect(load_profile(), store)
            store.close()
            index.update(passages)
            talk = ask.Conversation(index, model)
            if inputs.thema:
                stories_found = index.by_key(inputs.thema, (search.STORY,))
                talk.focus = (stories_found[0].group or stories_found[0].ref) if stories_found else ""
            talk.history = list(zip(inputs.vorher[::2], inputs.vorher[1::2]))
            with capture_run_messages() as messages:
                answer = talk.ask(inputs.frage)
            deps = ask.AskDeps(answer.selection, inputs.frage, talk.history[:-1], index.key_prefixes())
            return FragenOutput(
                antwort=answer.text,
                quelle=answer.source,
                gewaehlt=answer.selection.titles(),
                wie=answer.selection.how,
                maengel=ask.problems(answer.text, deps) if answer.source == ask.MODEL else [],
                modell=model_said(messages),
            )
        finally:
            index.close()

    return run


@dataclass
class Antwort(Evaluator[FragenInputs, FragenOutput, Any]):
    """Prueft Auswahl und Antwort des Gespraechs-Agenten.

    Immer geprueft: Die Selbstpruefung findet nichts, und das Modell hat selbst
    geantwortet (ausser bei ``ohne_modell``). ``waehlt``: Jedes Textstueck steht
    im Titel eines ausgewaehlten Eintrags; ``zuerst`` im Titel des ersten.
    ``nennt`` / ``nennt_nicht`` / ``eines_von``: Textstuecke der Antwort.
    ``saetze_hoechstens``: Laenge der Antwort. ``ohne_modell``: Der Code
    antwortet selbst, weil nichts gefunden wurde.
    """

    waehlt: list[str] = field(default_factory=list)
    zuerst: str = ""
    nennt: list[str] = field(default_factory=list)
    nennt_nicht: list[str] = field(default_factory=list)
    eines_von: list[str] = field(default_factory=list)
    saetze_hoechstens: int = 0
    ohne_modell: bool = False

    def evaluate(self, ctx: EvaluatorContext[FragenInputs, FragenOutput, Any]) -> EvaluationReason:
        output = ctx.output
        problems = list(output.maengel)
        if self.ohne_modell != (output.quelle == ask.CODE):
            problems.append(
                "es sollte nichts gefunden werden" if self.ohne_modell else "nichts gefunden, das Modell wurde nicht gefragt"
            )
        if output.quelle == ask.FALLBACK:
            problems.append("Modell lieferte keine belegte Antwort (Fundstellen statt Antwort)")
        chosen = [title.casefold() for title in output.gewaehlt]
        for part in self.waehlt:
            if not any(part.casefold() in title for title in chosen):
                problems.append(f"„{part}“ nicht ausgewählt")
        if self.zuerst and (not chosen or self.zuerst.casefold() not in chosen[0]):
            problems.append(f"„{self.zuerst}“ ist nicht der erste Eintrag")
        if (self.waehlt or self.zuerst) and problems and output.gewaehlt:
            problems.append(f"ausgewählt ({output.wie}): {'; '.join(output.gewaehlt)}")
        text = output.antwort.casefold()
        for part in self.nennt:
            if part.casefold() not in text:
                problems.append(f"„{part}“ fehlt in der Antwort")
        for part in self.nennt_nicht:
            if part.casefold() in text:
                problems.append(f"„{part}“ gehört nicht in die Antwort")
        if self.eines_von and not any(part.casefold() in text for part in self.eines_von):
            problems.append(f"keines von {', '.join(self.eines_von)} in der Antwort")
        if self.saetze_hoechstens:
            sentences = [piece for piece in re.split(r"(?<=[.!?])\s+", output.antwort.strip()) if piece]
            if len(sentences) > self.saetze_hoechstens:
                problems.append(f"{len(sentences)} Sätze, erlaubt sind {self.saetze_hoechstens}")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Agenten und ihre Falldateien


@dataclass(frozen=True)
class Suite:
    """Was zur Messung eines Agenten gehoert."""

    dataset_type: Any  # Dataset[Eingabe, Ausgabe, Any]
    evaluators: tuple[type, ...]
    task: Callable[[object], Callable]  # Modell -> Aufgabe fuer pydantic-evals
    timeout: float = TASK_TIMEOUT  # Sekunden je Lauf
    uses_model: bool = True  # False: rechnet ohne Modell, ein Lauf je Fall genuegt


AGENTS: dict[str, Suite] = {
    scrum_master.AGENT_NAME: Suite(Dataset[ScrumInputs, ScrumOutput, Any], (Bemerkungen,), scrum_task),
    feedback.AGENT_NAME: Suite(Dataset[FeedbackInputs, FeedbackOutput, Any], (Aufgaben,), feedback_task),
    status_agent.AGENT_NAME: Suite(Dataset[StatusInputs, StatusOutput, Any], (Status,), status_task),
    # Der Termin-Agent denkt mit mittlerem Aufwand nach und braucht je Lauf deutlich laenger.
    meeting_agent.AGENT_NAME: Suite(Dataset[TerminInputs, TerminOutput, Any], (Terminart,), termin_task, timeout=300.0),
    stories.AGENT_NAME: Suite(Dataset[StoriesInputs, StoriesOutput, Any], (Stories,), stories_task),
    message_agent.AGENT_NAME: Suite(Dataset[NachrichtInputs, NachrichtOutput, Any], (Nachricht,), nachricht_task),
    search.AGENT_NAME: Suite(Dataset[SucheInputs, SucheOutput, Any], (Treffer,), suche_task, uses_model=False),
    ask.AGENT_NAME: Suite(Dataset[FragenInputs, FragenOutput, Any], (Antwort,), fragen_task),
}
