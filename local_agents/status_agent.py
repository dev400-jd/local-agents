"""Status-Agent: kennt Datum und Uhrzeit und sagt, welche eine Aufgabe als
Allernaechstes dran ist und wie dringend sie ist.

Aufruf: python -m local_agents.status_agent ["Frage"]

Er ist der kleinste Baustein der Orchestrierung: Andere Agenten holen sich mit
``next_task`` die dringendste offene Aufgabe (oder mit ``next_tasks`` alle in
der Reihenfolge ihrer Dringlichkeit). Die Dringlichkeit rechnet der Code aus
Fertig-bis-Datum und Terminbeginn; das Modell formuliert nur ein, zwei Saetze
dazu. Darunter druckt der Code die eine Aufgabe, nicht den ganzen Plan; den
zeigt ``python -m local_agents.tasks``.
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic_ai import Agent, RunContext

from local_agents import sync
from local_agents.agent import MODEL_SETTINGS, answer_with_notes, ollama_model
from local_agents.holidays import WEEKDAYS
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import IN_PROGRESS, OPEN, Store, StoredEvent, Task

AGENT_NAME = "status"
DEFAULT_QUESTION = "Was steht als Naechstes an?"
# Laeufe, nach denen der Plan als vom Nutzer abgesegnet gilt.
APPROVAL_AGENTS = ("freigabe", "rueckmeldung")

OVERDUE, TODAY, TOMORROW, LATER, OBSOLETE = "überfällig", "heute", "morgen", "später", "hinfällig"
# Reihenfolge der Dringlichkeit; hinfaellige Aufgaben (Termin vorbei) zuletzt.
LEVELS = (OVERDUE, TODAY, TOMORROW, LATER, OBSOLETE)
HEADINGS = {
    OVERDUE: "überfällig",
    TODAY: "heute fällig",
    TOMORROW: "morgen fällig",
    LATER: "später fällig",
    OBSOLETE: "hinfällig, Termin hat schon begonnen",
}


@dataclass(frozen=True)
class StatusItem:
    """Eine offene Aufgabe mit ihrer Dringlichkeit zum Zeitpunkt der Abfrage."""

    task: Task
    level: str  # eine der LEVELS
    event: StoredEvent | None = None
    until_event: timedelta | None = None  # Zeit bis zum Terminbeginn

    def note(self) -> str:
        """Kurzer Zusatz zur Dringlichkeit, etwa "Termin in 3 Std"."""
        if self.until_event is None:
            return ""
        if self.level == OBSOLETE:
            return "Termin hat begonnen"
        return f"Termin in {_duration(self.until_event)}"


def next_tasks(store: Store, now: datetime) -> list[StatusItem]:
    """Alle offenen Aufgaben, die dringendsten zuerst."""
    tasks = store.tasks((OPEN, IN_PROGRESS))
    if not tasks:
        return []
    first = min(task.due_date for task in tasks)
    last = max(task.due_date for task in tasks) + timedelta(days=60)
    events = {event.id: event for event in store.events(first - timedelta(days=60), last)}
    today = now.date()
    items = []
    for task in tasks:
        event = events.get(task.event_id or "")
        until = event.start - now if event else None
        if until is not None and until <= timedelta(0):
            level = OBSOLETE
        elif task.due_date < today:
            level = OVERDUE
        elif task.due_date == today:
            level = TODAY
        elif task.due_date == today + timedelta(days=1):
            level = TOMORROW
        else:
            level = LATER
        items.append(StatusItem(task, level, event, until))
    far = datetime.max
    return sorted(
        items,
        key=lambda item: (
            LEVELS.index(item.level),
            item.task.due_date,
            item.event.start if item.event else far,
            item.task.id,
        ),
    )


def next_task(store: Store, now: datetime) -> StatusItem | None:
    """Die eine Aufgabe, die jetzt als Naechstes dran ist; None, wenn nichts ansteht.

    Hinfaellige Aufgaben (Termin hat schon begonnen) zaehlen nicht.
    """
    return next((item for item in next_tasks(store, now) if item.level != OBSOLETE), None)


def item_line(item: StatusItem, profile: Profile) -> str:
    """Eine Aufgabe mit Dringlichkeit in einer Zeile."""
    task = item.task
    due = f"fertig bis {WEEKDAYS[task.due_date.weekday()]} {task.due_date:%d.%m.}"
    extra = "; ".join(part for part in (HEADINGS[item.level], due, item.note()) if part)
    return f"#{task.id} {task.title} [{_label(profile, task.context)}; {extra}]"


def remaining(items: list[StatusItem]) -> str:
    """Wie viel nach der naechsten Aufgabe noch offen ist, in einem Satz."""
    rest = [item for item in items if item.level != OBSOLETE][1:]
    if not rest:
        return "Danach ist nichts mehr offen."
    counts = [(sum(item.level == level for item in rest), HEADINGS[level]) for level in LEVELS[:4]]
    parts = ", ".join(f"{count} {name}" for count, name in counts if count)
    noun = "weitere Aufgabe" if len(rest) == 1 else "weitere Aufgaben"
    return f"Danach noch {len(rest)} {noun}: {parts}."


def approved_at(store: Store) -> datetime | None:
    """Wann der Nutzer den Plan zuletzt abgesegnet oder angepasst hat."""
    for run in store.runs(limit=200):
        if run["agent"] in APPROVAL_AGENTS and run["status"] == "ok" and run["finished_at"]:
            return datetime.fromisoformat(run["finished_at"])
    return None


NO_PLAN = object()


def status_heading(now: datetime, approved: datetime | None | object = NO_PLAN) -> str:
    """Zeitpunkt der Abfrage; mit ``approved`` (auch None) samt Stand der Freigabe."""
    stand = f"Stand {WEEKDAYS[now.weekday()]} {now:%d.%m.%Y, %H:%M} Uhr"
    if approved is NO_PLAN:
        return stand
    plan = f"Plan abgesegnet am {approved:%d.%m. %H:%M}" if approved else "Plan noch nicht abgesegnet"
    return f"{stand} ({plan})"


def _label(profile: Profile, context: str) -> str:
    try:
        return profile.label(context)
    except KeyError:
        return context


def _duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} Min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} Std {rest} Min" if rest else f"{hours} Std"
    days, hours = divmod(hours, 24)
    unit = "Tag" if days == 1 else "Tagen"
    return f"{days} {unit} {hours} Std" if hours else f"{days} {unit}"


@dataclass
class StatusDeps:
    """Laufzeitabhaengigkeiten: Zeitpunkt, Datenbank und Profil."""

    now: datetime
    store: Store
    profile: Profile = field(default_factory=Profile)
    notes: dict[str, list[str]] = field(default_factory=dict)
    # Die naechste Aufgabe, sobald der Agent gelaufen ist: die Uebergabe an den naechsten Agenten.
    item: StatusItem | None = None


status_agent = Agent(deps_type=StatusDeps, model_settings=MODEL_SETTINGS)


@status_agent.instructions
def instructions(ctx: RunContext[StatusDeps]) -> str:
    deps = ctx.deps
    items = next_tasks(deps.store, deps.now)
    item = deps.item = next_task(deps.store, deps.now)
    heading = status_heading(deps.now, approved_at(deps.store))
    if item is None:
        deps.notes[heading] = ["Es steht keine Aufgabe an."]
        facts = "Es steht keine Aufgabe an."
    else:
        # Die eine Aufgabe druckt der Code unter die Antwort.
        line = item_line(item, deps.profile)
        deps.notes[heading] = [f"Als Nächstes: {line}", remaining(items)]
        facts = f"Naechste Aufgabe: {line}\n{remaining(items)}"
    return (
        "Du bist der Status-Agent eines Agentensystems. Du kennst nur das Datum, "
        "die Uhrzeit und die eine Aufgabe, die jetzt als Naechstes dran ist. "
        "Sage in ein bis zwei kurzen deutschen Saetzen, welche Aufgabe das ist und "
        "warum sie jetzt dran ist (Faelligkeit, Zeit bis zum Termin). Die Aufgabe "
        "erledigt das Agentensystem, nicht der Nutzer; schreibe nie 'du musst'. "
        "Nenne keine anderen Aufgaben. Erfinde nichts. Zeitangaben uebernimmst du "
        "woertlich aus den Angaben unten (etwa 'Termin in 1 Tag 1 Std'); rechne sie "
        "nicht in Stunden oder Minuten um.\n"
        # Ob der Plan abgesegnet ist, steht nur in der Ueberschrift fuer den Nutzer:
        # Das Modell leitete daraus Behauptungen ab ("erst nach Freigabe").
        f"{status_heading(deps.now)}\n{facts}"
    )


def answer(question: str, deps: StatusDeps, model) -> str:
    return answer_with_notes(status_agent, question, deps, model)


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    question = argv[1] if len(argv) == 2 else DEFAULT_QUESTION
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    deps = StatusDeps(now=datetime.now(), store=Store(), profile=profile)
    sync.refresh(deps.store, profile, deps.now)
    print(answer(question, deps, ollama_model(profile.model_for(AGENT_NAME))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
