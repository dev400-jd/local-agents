"""Planungs-Agent: leitet aus den Terminen Aufgaben mit Fertig-bis-Datum ab
und schreibt sie in die Agenten-Datenbank.

Aufruf: python -m local_agents.planner_agent [--stumm] ["Frage"]

Nach dem Plan folgt eine kurze Uebersicht der wichtigsten Aufgaben mit der
Rueckfrage "Passt das so fuer dich?". Ein Ja laesst alles stehen, sonst nimmt
der Agent Aufgaben heraus oder ergaenzt welche (``feedback.py``). Die Uebersicht
wird vorgelesen und die Antwort gesprochen (``voice.py``, ``listen.py``, beides
lokal). Mit ``--stumm`` oder ``[voice] enabled = false`` im Profil bleibt es
still: Die Uebersicht steht nur im Terminal, die Antwort wird getippt. Getippt
wird auch, wenn die Spracheingabe nichts hoert oder nicht funktioniert.
Ohne Terminal (etwa als geplanter Lauf) entfaellt die Rueckfrage.

Ohne Frage plant der Agent die kommende Woche. Je Termin entsteht eine Aufgabe
"Vorbereitung", fertig ``lead_days`` Tage vor dem Termin (Profil, Standard 1).
Interne Termine, die laut ``planning.py`` weichen sollten (Kundentag, interner
Block bei Stress), bekommen zusaetzlich eine Aufgabe "Nachricht an <Organisator>",
faellig ``move_lead_days`` Tage vorher (Standard 3), fruehestens heute.
Ein zweiter Lauf legt nichts doppelt an: Er gleicht ab, was sich geaendert hat,
und verwirft Aufgaben zu Terminen, die es nicht mehr gibt.

Was in der Datenbank landet, entscheidet der Code. Das Modell waehlt nur den
Zeitraum und kommentiert das Ergebnis.
"""

import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pydantic_ai import Agent, ModelRetry, RunContext

from local_agents import feedback, listen, voice
from local_agents.agent import MODEL_SETTINGS, answer_with_notes, format_notes, ollama_model
from local_agents.briefing import spoken_summary
from local_agents.calendar_agent import MAX_RANGE_DAYS, is_holiday_entry
from local_agents.calendar_events import CalendarAccessError, Event, fetch_events, next_week
from local_agents.holidays import WEEKDAYS
from local_agents.planning import is_large_round, move_groups
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import (
    CHANGED,
    DISCARDED,
    REJECTED,
    IN_PROGRESS,
    NEW,
    OPEN,
    UNCHANGED,
    Store,
    StoredEvent,
    Task,
)

AGENT_NAME = "planner"
PREPARATION = "vorbereitung"
MOVE = "verschieben"
APPROVAL = "freigabe"
DEFAULT_QUESTION = "Plane meine naechste Woche."


@dataclass(frozen=True)
class Move:
    """Ein Termin, der seit dem letzten Abgleich verschoben wurde."""

    title: str
    old_start: datetime
    new_start: datetime


@dataclass(frozen=True)
class Plan:
    """Ergebnis eines Planungslaufs fuer einen Zeitraum."""

    start: date
    end: date
    counts: Counter  # NEW, CHANGED, UNCHANGED -> Anzahl Aufgaben
    discarded: int  # verworfene Aufgaben zu entfallenen Terminen
    tasks: list[Task]  # alle nicht verworfenen Aufgaben zu Terminen des Zeitraums
    moves: tuple[Move, ...] = ()


def event_key(event: Event) -> str:
    """Kennung eines Termins in der Datenbank: Kalender-Kennung und urspruenglicher Beginn."""
    return f"{event.uid or event.title}|{(event.occurrence or event.start).isoformat()}"


def match_moves(
    gone: list[StoredEvent], fresh: list[tuple[str, Event]], start: date, end: date
) -> dict[str, str]:
    """Ordnet verschwundene Termine neu aufgetauchten zu: alte Kennung -> neue.

    Ein verschobener Termin taucht im Kalender unter anderer Kennung auf. Drei
    Faelle gelten als derselbe Termin:

    - Ein Vorkommen einer Serie wurde einzeln verschoben: Der neue Eintrag ist
      ein geaendertes Vorkommen mit dem alten Beginn als Ursprung und gleichem Titel.
    - Ein Einzeltermin wurde verschoben: gleiche Kalender-Kennung, je genau einer.
    - Eine ganze Serie wurde verlegt: gleiche Kennung und im Zeitraum gleich
      viele alte wie neue Vorkommen, in zeitlicher Reihenfolge.

    Alles andere bleibt "entfallen" und "neu".
    """
    renames: dict[str, str] = {}
    fresh = list(fresh)

    def pair(old: StoredEvent, new: tuple[str, Event]) -> None:
        renames[old.id] = new[0]
        fresh.remove(new)

    for old in gone:
        _, _, origin = old.id.rpartition("|")
        hits = [
            new
            for new in fresh
            if new[1].detached
            and new[1].occurrence is not None
            and new[1].occurrence.isoformat() == origin
            and new[1].title.strip().casefold() == old.title.casefold()
        ]
        if len(hits) == 1:
            pair(old, hits[0])
    by_uid: dict[str, list[StoredEvent]] = {}
    for old in gone:
        if old.id not in renames:
            by_uid.setdefault(old.id.rpartition("|")[0], []).append(old)
    for uid, olds in by_uid.items():
        same = [new for new in fresh if (new[1].uid or new[1].title) == uid]
        single = [new for new in same if not new[1].recurring]
        inside = sorted(
            (new for new in same if start <= new[1].start.date() <= end),
            key=lambda new: new[1].start,
        )
        if len(olds) == 1 and len(single) == 1 and len(same) == 1:
            pair(olds[0], single[0])
        elif len(olds) == len(inside):
            for old, new in zip(sorted(olds, key=lambda e: e.start), inside):
                pair(old, new)
    return renames


def plan_events(
    events: list[Event],
    profile: Profile,
    store: Store,
    start: date,
    end: date,
    today: date | None = None,
    now: datetime | None = None,
    plannable: Callable[[date], bool] | None = None,
) -> Plan:
    """Schreibt Termine und ihre Aufgaben in die Datenbank.

    ``today`` ist das frueheste Fertig-bis-Datum der Verschiebe-Aufgaben.
    ``events`` darf ueber ``start`` bis ``end`` hinausreichen (Kalenderabgleich):
    Ausserhalb zaehlen nur Termine, die die Datenbank schon kennt, etwa weil
    sie dorthin verschoben wurden. Neue Termine bekommen Aufgaben nur an Tagen,
    fuer die ``plannable`` gilt (Standard: der Zeitraum), und mit ``now`` nur,
    wenn sie noch nicht begonnen haben; Aufgaben begonnener Termine bleiben,
    wie sie sind.
    """

    def context_of(event: Event) -> str:
        return profile.classify(event.title, event.organizer_domain, event.attendee_domains)

    if plannable is None:
        plannable = lambda day: start <= day <= end  # noqa: E731
    # Warum ein Termin weichen sollte, je (Beginn, Ende, Titel).
    move_reasons = {
        (meeting.start, meeting.end, meeting.title): group.reason
        for group in move_groups(events, profile, context_of)
        for meeting in group.meetings
    }
    candidates: list[tuple[str, Event]] = []
    seen: set[tuple] = set()
    for event in sorted(events, key=lambda e: (e.start, e.title)):
        duplicate = (event.start, event.end, event.title)
        if not needs_task(event, profile) or duplicate in seen:
            continue
        seen.add(duplicate)
        candidates.append((event_key(event), event))

    stored = {event.id: event for event in store.events(date.min, date.max)}
    fetched = {event_id for event_id, _ in candidates}
    gone = [event for event in store.events(start, end) if event.id not in fetched]
    fresh = [item for item in candidates if item[0] not in stored]
    for old_id, new_id in match_moves(gone, fresh, start, end).items():
        store.rekey_event(old_id, new_id)
        stored[new_id] = stored.pop(old_id)
    existing = {(task.event_id, task.kind): task for task in store.tasks()}

    counts: Counter = Counter()
    discarded = 0
    kept: set[str] = set()
    moves: list[Move] = []
    for event_id, event in candidates:
        day = event.start.date()
        before = stored.get(event_id)
        if before is None and (not plannable(day) or (now is not None and event.start <= now)):
            continue
        if before is not None and before.start != event.start:
            moves.append(Move(event.title.strip(), before.start, event.start))
        context = context_of(event)
        store.upsert_event(
            StoredEvent(
                event_id,
                event.title.strip(),
                event.start,
                event.end,
                context,
                event.location,
                len(event.attendee_domains),
                event.organizer,
            )
        )
        kept.add(event_id)
        if now is not None and event.start <= now:
            # Der Termin laeuft oder ist vorbei: Seine Aufgaben sind hinfaellig
            # und sollen nicht wieder aufleben.
            continue
        what = f"{event.title.strip()} ({WEEKDAYS[day.weekday()]} {event.start:%d.%m. %H:%M})"
        due = day - timedelta(days=profile.lead_days)
        counts[store.upsert_task(event_id, PREPARATION, f"Vorbereitung: {what}", context, due, AGENT_NAME)] += 1
        reason = move_reasons.get((event.start, event.end, event.title))
        if reason:
            ideal = day - timedelta(days=profile.move_lead_days)
            due = max(ideal, min(today, day)) if today else ideal
            # Eine schon angelegte Aufgabe rueckt nicht mit jedem Tag weiter,
            # sonst waere sie nie ueberfaellig.
            old = existing.get((event_id, MOVE))
            if old is not None and ideal <= old.due_date <= due:
                due = old.due_date
            title = move_title(event, what, reason)
            counts[store.upsert_task(event_id, MOVE, title, context, due, AGENT_NAME)] += 1
        else:
            # Der Termin muss nicht mehr weichen (Profil oder Kalender geaendert).
            discarded += store.discard_task(event_id, MOVE)
    discarded += store.drop_events(start, end, kept)
    tasks = [
        task
        for task in store.tasks()
        if task.event_id in kept and task.status not in (DISCARDED, REJECTED)
    ]
    return Plan(start, end, counts, discarded, tasks, tuple(moves))


def move_title(event: Event, what: str, reason: str) -> str:
    """Titel der Aufgabe, den Termin zu verschieben oder dafuer abzusagen."""
    if event.organizer_is_me:
        return f"Selbst verschieben: {what}, weil {reason}"
    who = event.organizer or "Organisator"
    if is_large_round(event):
        return (
            f"Absage an {who}: {what}, weil {reason} "
            f"(große Runde mit {len(event.attendee_domains)} Eingeladenen)"
        )
    return f"Nachricht an {who}: {what} verschieben, weil {reason}"


# Arten von Verschiebe-Aufgaben, wie ``move_title`` sie formuliert.
ASK, DECLINE, ANNOUNCE = "bitte", "absage", "ankuendigung"
MOVE_TITLES = (
    (ANNOUNCE, re.compile(r"^Selbst verschieben: .+, weil (?P<reason>.+)$")),
    (DECLINE, re.compile(r"^Absage an (?P<who>.+?): .+, weil (?P<reason>.+?) \(große Runde mit \d+ Eingeladenen\)$")),
    (ASK, re.compile(r"^Nachricht an (?P<who>.+?): .+ verschieben, weil (?P<reason>.+)$")),
)


def parse_move_title(title: str) -> tuple[str, str] | None:
    """Art und Grund einer Verschiebe-Aufgabe aus ihrem Titel; None, wenn es keiner ist."""
    for kind, pattern in MOVE_TITLES:
        match = pattern.match(title)
        if match:
            return kind, match.group("reason")
    return None


def needs_task(event: Event, profile: Profile) -> bool:
    """Ganztaegige Eintraege und im Profil ausgenommene Termine bekommen keine Aufgabe."""
    if event.all_day or is_holiday_entry(event):
        return False
    title = event.title.casefold()
    return not any(word in title for word in profile.plan_skip)


def plan_lines(plan: Plan, profile: Profile) -> list[str]:
    """Die Aufgaben des Plans als Liste, nach Fertig-bis-Datum gruppiert."""
    lines = []
    current = None
    for task in plan.tasks:
        if task.due_date != current:
            current = task.due_date
            lines.append(f"- fertig bis {WEEKDAYS[current.weekday()]} {current:%d.%m.%Y}:")
        status = "" if task.status == "offen" else f", {task.status}"
        lines.append(f"  - #{task.id} {task.title} [{profile.label(task.context)}{status}]")
    return lines


def plan_summary(plan: Plan) -> str:
    counts = plan.counts
    return (
        f"{counts[NEW]} neu, {counts[CHANGED]} geändert, {counts[UNCHANGED]} unverändert, "
        f"{plan.discarded} verworfen"
    )


@dataclass
class PlannerDeps:
    """Laufzeitabhaengigkeiten: heutiges Datum, Profil, Terminquelle und Datenbank."""

    today: date
    store: Store
    profile: Profile = field(default_factory=Profile)
    fetch: Callable[[date, date], list[Event]] = fetch_events
    notes: dict[str, list[str]] = field(default_factory=dict)
    plans: list[Plan] = field(default_factory=list)


planner_agent = Agent(deps_type=PlannerDeps, model_settings=MODEL_SETTINGS, retries=2)


@planner_agent.instructions
def instructions(ctx: RunContext[PlannerDeps]) -> str:
    today = ctx.deps.today
    monday, sunday = next_week(today)
    return (
        "Du bist ein Planungs-Assistent. Rufe plan_week fuer den gewuenschten "
        "Zeitraum auf; das legt die Aufgaben in der Datenbank an: je Termin eine "
        "Vorbereitung und fuer interne Termine, die dem Kundenprojekt weichen "
        "sollten, eine Nachricht an den Organisator. "
        f"Heute ist {WEEKDAYS[today.weekday()]}, {today.isoformat()}. "
        f"Die naechste Woche reicht von {monday.isoformat()} bis {sunday.isoformat()}. "
        "Sage danach in zwei, drei Saetzen auf Deutsch, was angelegt wurde und an "
        "welchen Tagen besonders viel faellig ist. Die Aufgabenliste wird "
        "automatisch angehaengt; zaehle die Aufgaben nicht einzeln auf."
    )


@planner_agent.tool
def plan_week(ctx: RunContext[PlannerDeps], start: date, end: date) -> str:
    """Legt fuer alle Termine des Zeitraums Aufgaben in der Datenbank an.

    Args:
        start: Erster Tag im Format JJJJ-MM-TT.
        end: Letzter Tag (einschliesslich) im Format JJJJ-MM-TT.
    """
    if end < start:
        raise ModelRetry("end liegt vor start.")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise ModelRetry(f"Zeitraum zu lang, hoechstens {MAX_RANGE_DAYS} Tage.")
    return _plan(ctx.deps, start, end)


def _plan(deps: PlannerDeps, start: date, end: date) -> str:
    plan = plan_events(deps.fetch(start, end), deps.profile, deps.store, start, end, deps.today)
    deps.store.mark_planned(start, end)
    deps.plans.append(plan)
    heading = f"Aufgaben für {start:%d.%m.}–{end:%d.%m.%Y} ({plan_summary(plan)})"
    deps.notes[heading] = plan_lines(plan, deps.profile) or ["- keine Aufgaben"]
    per_day = Counter(task.due_date for task in plan.tasks)
    days = ", ".join(
        f"{WEEKDAYS[day.weekday()]} {day:%d.%m.}: {count}" for day, count in sorted(per_day.items())
    )
    moves = sum(task.kind == MOVE for task in plan.tasks)
    text = (
        f"Aufgaben in der Datenbank: {plan_summary(plan)}. "
        f"Faellig je Tag: {days or 'nichts'}."
    )
    if moves:
        text += f" Davon {moves} Nachrichten, um interne Termine zu verschieben."
    return text


def run(question: str, deps: PlannerDeps, model) -> str:
    """Laesst den Agenten planen und protokolliert den Lauf in der Datenbank."""
    run_id = deps.store.start_run(AGENT_NAME)
    try:
        output = answer_with_notes(planner_agent, question, deps, model)
        if not deps.plans:
            # Das Modell hat das Werkzeug nicht aufgerufen: trotzdem planen.
            _plan(deps, *next_week(deps.today))
            output += format_notes(deps.notes)
    except BaseException:
        deps.store.finish_run(run_id, "fehler")
        raise
    deps.store.finish_run(run_id, "ok", "; ".join(plan_summary(plan) for plan in deps.plans))
    return output


def briefing(deps: PlannerDeps) -> str:
    """Kurzfassung zum Vorlesen: die offenen Aufgaben zu den geplanten Zeitraeumen."""
    events = [
        event for plan in deps.plans for event in deps.store.events(plan.start, plan.end)
    ]
    ids = {event.id for event in events}
    tasks = [task for task in deps.store.tasks((OPEN, IN_PROGRESS)) if task.event_id in ids]
    return spoken_summary(tasks, events, deps.today)


def hear(profile: Profile, listener=listen.listen) -> str:
    """Gesprochene Antwort als Text; leer, wenn stattdessen getippt werden soll."""
    print("\nIch höre zu … (Stille beendet die Aufnahme)", flush=True)
    try:
        answer = listener(profile.listen_model, profile.listen_language)
    except listen.ListenError as exc:
        print(f"(Spracheingabe nicht möglich: {exc})", file=sys.stderr)
        return ""
    except KeyboardInterrupt:
        print()
        return ""
    if answer:
        print(f"Verstanden: {answer}", flush=True)
    else:
        print("Ich habe nichts gehört.", flush=True)
    return answer


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if arg != "--stumm"]
    silent = len(args) < len(argv) - 1
    if len(args) > 1:
        print(__doc__, file=sys.stderr)
        return 2
    question = args[0] if args else DEFAULT_QUESTION
    try:
        profile = load_profile()
        deps = PlannerDeps(today=date.today(), store=Store(), profile=profile)
        model = ollama_model(profile.model_for(AGENT_NAME))
        output = run(question, deps, model)
    except (CalendarAccessError, ProfileError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(output, flush=True)
    aloud = not silent and profile.voice_enabled

    def say(text: str) -> None:
        if not aloud:
            return
        spoke = voice.speak_with_profile(text, profile)
        if not spoke:
            print("(Sprachausgabe nicht verfügbar.)", file=sys.stderr)
        elif spoke == "say" and profile.voice_model:
            print(
                "(Piper-Stimme nicht verfügbar, macOS-Stimme benutzt. "
                "Siehe README, Abschnitt Stimme anpassen.)",
                file=sys.stderr,
            )

    # Erst der ganze Plan zum Lesen, dann die Uebersicht mit der Rueckfrage.
    summary = briefing(deps)
    print(f"\n**Kurzfassung{' (wird vorgelesen)' if aloud else ''}**\n{summary}", flush=True)
    say(summary)
    if not sys.stdin.isatty():
        return 0
    answer = ""
    if aloud and profile.listen_enabled:
        answer = hear(profile)
    if not answer:
        try:
            answer = input("\nDeine Antwort (Enter = passt so): ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
    outcome = feedback.handle(answer, deps.store, profile, deps.today, model)
    # Mit der Antwort gilt der Plan als abgesegnet; darauf baut der Status-Agent auf.
    approval = deps.store.start_run(APPROVAL)
    deps.store.finish_run(
        approval, "ok", f"{outcome.removed} herausgenommen, {outcome.added} ergänzt"
    )
    print("\n".join([*outcome.lines, outcome.spoken]), flush=True)
    say(outcome.spoken)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
