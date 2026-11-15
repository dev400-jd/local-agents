"""Rueckmeldung des Nutzers auf den Plan: Aufgaben herausnehmen oder ergaenzen.

Nach der Kurzfassung fragt der Planungs-Agent "Passt das so fuer dich?". Die
Antwort ist freier Text. Ein schlichtes Ja erkennt der Code selbst; alles andere
uebersetzt das lokale Modell in eine Liste von Aenderungen (``Feedback``). Der
Code prueft sie, fuehrt sie aus und nennt jede einzeln, damit ein Missverstaendnis
auffaellt. Beides laesst sich mit ``python -m local_agents.tasks`` zuruecknehmen.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext

from local_agents.agent import MODEL_SETTINGS
from local_agents.holidays import WEEKDAYS
from local_agents.profile import Profile
from local_agents.store import IN_PROGRESS, OPEN, REJECTED, Store, Task

AGENT_NAME = "rueckmeldung"
MANUAL = "manuell"
APPROVALS = re.compile(
    r"(ja|jo|jep|ok|okay|passt|gut|super|prima|perfekt|alles klar|in ordnung|einverstanden|"
    r"so|soweit|das|für mich|danke|genau|,|\.|!|\s)*",
    re.IGNORECASE,
)
# So viele Tage voraus bekommt das Modell als Kalender, um Wochentage aufzuloesen.
CALENDAR_DAYS = 14


class NewTask(BaseModel):
    """Eine Aufgabe, die der Nutzer zusaetzlich haben moechte."""

    title: str = Field(description="Kurzer Titel der Aufgabe, in den Worten des Nutzers")
    due: date | None = Field(None, description="Fertig bis, JJJJ-MM-TT; null, wenn nicht genannt")


class Feedback(BaseModel):
    """Was der Nutzer am Plan geaendert haben moechte."""

    remove: list[int] = Field(default_factory=list, description="Nummern der Aufgaben, die raus sollen")
    add: list[NewTask] = Field(default_factory=list, description="Aufgaben, die dazukommen sollen")
    unclear: str = Field("", description="Teil der Antwort, der nicht eindeutig zuzuordnen ist; sonst leer")


@dataclass
class FeedbackDeps:
    today: date
    tasks: list[Task]


@dataclass
class Outcome:
    """Ergebnis einer Rueckmeldung: Zeilen fuers Terminal und ein Satz zum Vorlesen."""

    lines: list[str] = field(default_factory=list)
    spoken: str = "Alles klar, der Plan bleibt so."
    removed: int = 0
    added: int = 0


feedback_agent = Agent(
    deps_type=FeedbackDeps, output_type=Feedback, model_settings=MODEL_SETTINGS, retries=2
)


@feedback_agent.instructions
def instructions(ctx: RunContext[FeedbackDeps]) -> str:
    today = ctx.deps.today
    days = ", ".join(
        f"{WEEKDAYS[day.weekday()]} {day.isoformat()}"
        for day in (today + timedelta(days=n) for n in range(CALENDAR_DAYS))
    )
    tasks = "\n".join(
        f"#{task.id} {task.title} (fertig bis {WEEKDAYS[task.due_date.weekday()]} {task.due_date.isoformat()})"
        for task in ctx.deps.tasks
    )
    return (
        "Der Nutzer hat einen Plan mit Aufgaben gesehen und sagt, was daran nicht passt. "
        "Uebersetze seine Antwort in Aenderungen:\n"
        "- remove: Nummern der Aufgaben aus der Liste unten, die er nicht haben will. "
        "Nimm nur Aufgaben, die er eindeutig meint. Meint er mehrere (etwa 'alle "
        "Vorbereitungen fuer Montag'), nenne alle passenden Nummern.\n"
        "- add: Aufgaben, die er zusaetzlich haben will, mit kurzem Titel. Nennt er "
        "einen Tag ('bis Dienstag', 'morgen', 'am 20.'), setze due auf dieses Datum; "
        "Wochentage loest du mit der Liste der naechsten Tage unten auf. Ohne Tag "
        "bleibt due null.\n"
        "- unclear: was du nicht eindeutig zuordnen kannst, in einem kurzen deutschen "
        "Satz; sonst leer.\n"
        "Aendere nichts, was der Nutzer nicht verlangt.\n"
        f"Heute ist {WEEKDAYS[today.weekday()]} {today.isoformat()}. Die naechsten Tage: {days}.\n"
        f"Offene Aufgaben:\n{tasks or '(keine)'}"
    )


@feedback_agent.output_validator
def known_tasks_only(ctx: RunContext[FeedbackDeps], feedback: Feedback) -> Feedback:
    known = {task.id for task in ctx.deps.tasks}
    unknown = [number for number in feedback.remove if number not in known]
    if unknown:
        raise ModelRetry(f"Diese Nummern stehen nicht in der Liste: {unknown}. Nur Nummern aus der Liste.")
    return feedback


def is_approval(answer: str) -> bool:
    """Ob die Antwort nur Zustimmung ist ("ja passt", "ok", leer)."""
    return APPROVALS.fullmatch(answer.strip()) is not None


def handle(answer: str, store: Store, profile: Profile, today: date, model) -> Outcome:
    """Wertet die Antwort aus, aendert die Aufgaben und protokolliert den Lauf."""
    if is_approval(answer):
        return Outcome()
    run_id = store.start_run(AGENT_NAME)
    try:
        tasks = store.tasks((OPEN, IN_PROGRESS))
        deps = FeedbackDeps(today=today, tasks=tasks)
        feedback = feedback_agent.run_sync(answer, deps=deps, model=model).output
        outcome = apply(feedback, tasks, store, profile, today)
    except BaseException:
        store.finish_run(run_id, "fehler")
        raise
    store.finish_run(run_id, "ok", f"{outcome.removed} herausgenommen, {outcome.added} ergänzt")
    return outcome


def apply(feedback: Feedback, tasks: list[Task], store: Store, profile: Profile, today: date) -> Outcome:
    """Fuehrt die Aenderungen aus und beschreibt jede in einer Zeile."""
    by_id = {task.id: task for task in tasks}
    outcome = Outcome()
    for number in dict.fromkeys(feedback.remove):
        task = by_id.get(number)
        if task and store.set_status(number, REJECTED, "vom Nutzer herausgenommen"):
            outcome.removed += 1
            outcome.lines.append(f"- raus: #{number} {task.title}")
    for new in feedback.add:
        title = " ".join(new.title.split())
        if not title:
            continue
        due = max(new.due or today, today)
        number = store.add_task(MANUAL, title, profile.classify(title), due, AGENT_NAME)
        outcome.added += 1
        outcome.lines.append(
            f"- neu: #{number} {title}, fertig bis {WEEKDAYS[due.weekday()]} {due:%d.%m.%Y}"
        )
    if feedback.unclear.strip():
        outcome.lines.append(f"- nicht verstanden: {feedback.unclear.strip()}")
    outcome.spoken = _spoken(outcome, bool(feedback.unclear.strip()))
    return outcome


def _spoken(outcome: Outcome, unclear: bool) -> str:
    def count(n: int) -> str:
        words = ("", "eine Aufgabe", "zwei Aufgaben", "drei Aufgaben", "vier Aufgaben", "fünf Aufgaben")
        return words[n] if n < len(words) else f"{n} Aufgaben"

    parts = []
    if outcome.removed:
        parts.append(f"{count(outcome.removed)} herausgenommen")
    if outcome.added:
        parts.append(f"{count(outcome.added)} ergänzt")
    hint = " Einen Teil habe ich nicht verstanden, er steht im Terminal." if unclear else ""
    if not parts:
        return "Ich habe nichts geändert." + (hint or " Ich konnte deine Antwort keiner Aufgabe zuordnen.")
    return f"Alles klar, ich habe {' und '.join(parts)}.{hint}"
