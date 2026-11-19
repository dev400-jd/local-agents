"""Nachrichten-Agent: schreibt den Entwurf zu einer Verschiebe-Aufgabe.

Aufruf:
  python -m local_agents.message_agent             naechste offene Verschiebe-Aufgabe
  python -m local_agents.message_agent 18          Aufgabe #18
  python -m local_agents.message_agent --neu 18    neuen Entwurf schreiben, auch wenn einer gespeichert ist
  python -m local_agents.message_agent vorgaben    gemerkte Vorgaben zeigen
  python -m local_agents.message_agent vergiss 3   Vorgabe 3 entfernen

Der erste Agent, der eine Aufgabe erledigt statt sie nur zu nennen. Der Ablauf:

1. Ausfuehren: Das Modell schreibt die Nachricht aus Angaben, die der Code
   zusammenstellt (Termin, Empfaenger, Grund, Art der Nachricht).
2. Selbst pruefen: Der Code prueft den Entwurf (Termin und Datum genannt,
   keine erfundene Zahl, kein Platzhalter, richtige Anrede, Grußformel) und schickt ihn bei
   Maengeln zurueck ans Modell. Gelingt es nicht, gilt eine feste Vorlage.
3. Freigeben: Du uebernimmst den Entwurf, aenderst ihn mit einem Wunsch in
   eigenen Worten, verwirfst ihn oder lehnst die Aufgabe ab.
4. Lernen: Einen Aenderungswunsch kannst du als Vorgabe merken lassen; er gilt
   dann fuer alle kuenftigen Entwuerfe.

Gesendet wird nichts. "Uebernehmen" legt den Text in die Zwischenablage und
hakt die Aufgabe ab; das Abschicken in Teams oder Outlook bleibt bei dir.
Der Entwurf steht in der Spalte ``result`` der Aufgabe, die Aufgabe ist
solange ``in_arbeit``.
"""

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from local_agents.agent import MODEL_SETTINGS, ollama_model
from local_agents.checks import invented_numbers
from local_agents.holidays import WEEKDAYS
from local_agents.planner_agent import ANNOUNCE, ASK, DECLINE, MOVE, parse_move_title
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import DISCARDED, DONE, IN_PROGRESS, OPEN, REJECTED, Store, StoredEvent, Task

AGENT_NAME = "nachricht"
MIN_CHARS, MAX_CHARS = 60, 700
MODEL, TEMPLATE = "modell", "vorlage"
# Kennt der Kalender den Organisator nicht, steht dieser Platzhalter im Titel der Aufgabe.
UNKNOWN_ORGANIZER = "Organisator"
PLACEHOLDER = re.compile(r"\[[^\]]*\]|\{[^}]*\}|<[^>]*>|XXX|TODO", re.IGNORECASE)
# Foermliche Anrede, wo "du" verlangt ist: "Ihnen", "Ihre", "Sie" mitten im Satz.
FORMAL = re.compile(r"\bIhnen\b|\bIhre[mnrs]?\b|(?<=[a-zäöüß,] )Sie\b")
INFORMAL = re.compile(r"\b(du|dir|dich|dein\w*)\b", re.IGNORECASE)

KINDS = {
    ASK: "Bitte an den Organisator, den Termin auf einen anderen Tag zu verschieben",
    DECLINE: "Absage: Der Termin ist eine große Runde und findet ohne den Absender statt",
    ANNOUNCE: "Ankündigung an die Eingeladenen: Der Absender hat selbst eingeladen und verschiebt den Termin",
}


class NoMessageTask(ValueError):
    """Die Aufgabe ist keine Verschiebe-Aufgabe mit Termin."""


class MeetingStarted(NoMessageTask):
    """Der Termin hat schon begonnen; eine Nachricht dazu kommt zu spaet."""


# Wendungen, an denen eine Bitte an den Organisator zu erkennen ist.
ASKING = re.compile(r"\?|\bbitte\b|\bkönntest\b|\bkannst du\b|\bkönnten sie\b|\bwäre es möglich\b", re.IGNORECASE)
# Grussformel am Schluss, vor dem Namen des Absenders.
CLOSING = re.compile(r"gr[uü](ß|ss)e?n?\b|\bdanke?\b|\b[lmv]fg\b|\b[lv]g\b|\bbis (bald|dann)\b", re.IGNORECASE)
CLOSING_TAIL = 45  # so viele Zeichen vor dem Namen zaehlen als Schluss


def has_closing(text: str, sender: str) -> bool:
    """Ob die Nachricht mit einer Grussformel schliesst, nicht nur mit dem Namen."""
    end = text.rstrip().rstrip(".!,")
    if sender and end.casefold().endswith(sender.casefold()):
        end = end[: -len(sender)]
    return bool(CLOSING.search(end[-CLOSING_TAIL:]))


@dataclass(frozen=True)
class Request:
    """Was die Nachricht sagen soll; alles hier stellt der Code zusammen."""

    kind: str  # ASK, DECLINE oder ANNOUNCE
    recipient: str  # Vorname des Empfaengers; leer = unbekannt oder mehrere
    title: str  # Titel des Termins
    start: datetime
    why: str  # Begruendung als Satz des Absenders
    sender: str  # Vorname des Absenders; leer = ohne Namen
    form: str = "du"

    @property
    def when(self) -> str:
        return f"{WEEKDAYS[self.start.weekday()]} {self.start:%d.%m.} um {self.start:%H:%M} Uhr"


@dataclass(frozen=True)
class Draft:
    text: str
    source: str  # MODEL oder TEMPLATE
    stored: bool = False  # aus der Datenbank, nicht neu geschrieben


def first_name(display: str) -> str:
    """Vorname aus einem Anzeigenamen: "Erika Muster" oder "Muster, Erika"."""
    name = display.strip()
    if not name or name == UNKNOWN_ORGANIZER or "@" in name:
        return ""
    if "," in name:
        name = name.split(",", 1)[1]
    parts = name.split()
    return parts[0] if parts else ""


def reason_sentence(reason: str) -> str:
    """Der Grund aus dem Titel der Aufgabe als Satz, den man einem Kollegen schreibt.

    Interna wie das Stresslevel oder der Name des Kunden gehoeren nicht in die Nachricht.
    """
    if reason.startswith("Kundentag"):
        return "Ich bin an diesem Tag beim Kunden vor Ort."
    if reason.startswith("interner Block"):
        return "Ich bin im Kundenprojekt zurzeit stark eingebunden und brauche diesen Zeitraum für die Projektarbeit."
    return reason[:1].upper() + reason[1:] + ("" if reason.endswith(".") else ".")


def build_request(task: Task, event: StoredEvent | None, profile: Profile) -> Request:
    parsed = parse_move_title(task.title) if task.kind == MOVE else None
    if parsed is None or event is None:
        raise NoMessageTask(f"Aufgabe #{task.id} ist keine Verschiebe-Aufgabe mit Termin.")
    kind, reason = parsed
    return Request(
        kind=kind,
        recipient="" if kind == ANNOUNCE else first_name(event.organizer),
        title=event.title,
        start=event.start,
        why=reason_sentence(reason),
        sender=first_name(profile.name),
        form=profile.message_form,
    )


def facts(request: Request) -> str:
    """Die Angaben fuer das Modell, eine je Zeile."""
    if request.recipient:
        recipient = f"{request.recipient} (Anrede mit Vornamen)"
    elif request.kind == ANNOUNCE:
        recipient = "alle Eingeladenen (Anrede: Hallo zusammen)"
    else:
        recipient = "Name unbekannt (Anrede ohne Namen: Hallo)"
    return "\n".join(
        [
            f"Art der Nachricht: {KINDS[request.kind]}",
            f"Empfänger: {recipient}",
            f"Termin: „{request.title}“ am {request.when}",
            f"Grund: {request.why}",
            f"Absender: {request.sender or 'ohne Namen, Gruß ohne Unterschrift'}",
            f"Anrede: {request.form}",
        ]
    )


def problems(text: str, request: Request) -> list[str]:
    """Was am Entwurf nicht stimmt; leer, wenn er so hinausgehen kann."""
    found = []
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        found.append(f"Länge {len(text)} Zeichen, erlaubt sind {MIN_CHARS} bis {MAX_CHARS}")
    if request.title.casefold() not in text.casefold():
        found.append(f"der Titel des Termins fehlt: „{request.title}“ wörtlich nennen")
    if f"{request.start:%d.%m.}" not in text:
        found.append(f"das Datum des Termins fehlt: {request.start:%d.%m.}")
    invented = invented_numbers(text, facts(request))
    if invented:
        found.append(f"Zahlen, die nicht in den Angaben stehen: {', '.join(invented)}")
    if PLACEHOLDER.search(text):
        found.append("Platzhalter im Text (eckige oder geschweifte Klammern)")
    if request.recipient and request.recipient.casefold() not in text.casefold():
        found.append(f"die Anrede mit dem Vornamen fehlt: {request.recipient}")
    if request.kind == ASK and not ASKING.search(text):
        found.append(
            "die Bitte fehlt: Den Termin kann nur der Empfänger verlegen, also ihn darum bitten "
            "(etwa „Könntest du ihn auf einen anderen Tag legen?“)"
        )
    if not has_closing(text, request.sender):
        found.append(
            "die Grußformel am Schluss fehlt: vor dem Namen des Absenders mit einem Gruß "
            "schließen (etwa „Viele Grüße“)"
        )
    if request.form == "du" and FORMAL.search(text):
        found.append("förmliche Anrede (Sie, Ihnen), verlangt ist du")
    if request.form == "Sie" and INFORMAL.search(text):
        found.append("Anrede mit du, verlangt ist Sie")
    return found


def template(request: Request) -> str:
    """Feste Vorlage, falls das Modell keinen brauchbaren Entwurf liefert."""
    polite = request.form == "Sie"
    hello = f"Hallo {request.recipient}," if request.recipient else (
        "Hallo zusammen," if request.kind == ANNOUNCE else "Hallo,"
    )
    meeting = f"„{request.title}“ am {request.when}"
    if request.kind == ASK:
        ask = "Könnten Sie ihn" if polite else "Könntest du ihn"
        body = f"den Termin {meeting} kann ich leider nicht wahrnehmen. {request.why} {ask} auf einen anderen Tag verschieben?"
    elif request.kind == DECLINE:
        body = f"am Termin {meeting} kann ich leider nicht teilnehmen. {request.why}"
    else:
        body = f"den Termin {meeting} muss ich verschieben. {request.why} Ich melde mich mit einem neuen Vorschlag."
    sign = f"\n\nViele Grüße\n{request.sender}" if request.sender else "\n\nViele Grüße"
    return f"{hello}\n\n{body}{sign}"


@dataclass
class MessageDeps:
    request: Request
    preferences: list[str]
    previous: str = ""  # bisheriger Entwurf, wenn der Nutzer eine Aenderung wuenscht
    change: str = ""  # sein Aenderungswunsch


message_agent = Agent(deps_type=MessageDeps, model_settings=MODEL_SETTINGS, retries=3)


@message_agent.instructions
def instructions(ctx: RunContext[MessageDeps]) -> str:
    deps = ctx.deps
    text = (
        "Du schreibst für den Absender eine kurze Nachricht an einen Kollegen, wie man "
        "sie im Firmenchat schickt: Anrede, drei bis fünf Sätze, zum Schluss eine Grußformel "
        "(etwa „Viele Grüße“) und danach der Vorname des Absenders. Gib nur den Text der Nachricht aus, ohne Betreff und ohne "
        "Erklärung. Regeln:\n"
        "- Schreibe in der Ich-Form des Absenders, freundlich und ohne Floskeln.\n"
        "- Nenne den Termin mit seinem Titel wörtlich und mit Datum und Uhrzeit, genau "
        "wie unten angegeben.\n"
        "- Nenne den Grund in eigenen Worten, aber ohne etwas hinzuzufügen.\n"
        "- Bei einer Bitte um Verschiebung: Der Empfänger hat eingeladen, nur er kann "
        "den Termin verlegen. Schreibe also nicht, dass der Absender den Termin "
        "verschiebt, sondern bitte den Empfänger ausdrücklich darum, ihn auf einen "
        "anderen Tag zu legen. Schlage keinen konkreten Ersatztermin vor. Bei einer Absage: kein Ersatztermin. Bei "
        "einer Ankündigung: Ein neuer Terminvorschlag folgt.\n"
        "- Erfinde nichts: keine weiteren Daten, Uhrzeiten, Namen oder Projektdetails. "
        "Keine Platzhalter in Klammern.\n"
        "- Verwende durchgehend die angegebene Anrede (du oder Sie).\n"
        f"Angaben:\n{facts(deps.request)}"
    )
    if deps.preferences:
        text += "\nVorgaben des Absenders aus früheren Korrekturen, immer einhalten:\n" + "\n".join(
            f"- {item}" for item in deps.preferences
        )
    if deps.change:
        text += (
            f"\nBisheriger Entwurf:\n{deps.previous}\n"
            f"Der Absender möchte ihn geändert haben: {deps.change}\n"
            "Schreibe den Entwurf entsprechend neu; die Regeln oben gelten weiter."
        )
    return text


@message_agent.output_validator
def checked(ctx: RunContext[MessageDeps], text: str) -> str:
    # Leerzeichen am Zeilenende sind Markdown-Zeilenumbrueche und stoeren im Chat.
    text = "\n".join(line.rstrip() for line in text.strip().splitlines())
    found = problems(text, ctx.deps.request)
    if found:
        raise ModelRetry("Der Entwurf hat Mängel, bitte neu schreiben: " + "; ".join(found) + ".")
    return text


def write(request: Request, preferences: list[str], model, previous: str = "", change: str = "") -> Draft:
    """Laesst das Modell den Entwurf schreiben; scheitert es, gilt die Vorlage."""
    deps = MessageDeps(request, preferences, previous, change)
    try:
        text = message_agent.run_sync("Schreibe die Nachricht.", deps=deps, model=model).output
    except UnexpectedModelBehavior:
        return Draft(template(request), TEMPLATE)
    return Draft(text, MODEL)


def draft(
    task: Task,
    event: StoredEvent | None,
    profile: Profile,
    store: Store,
    model,
    fresh: bool = False,
    change: str = "",
    now: datetime | None = None,
) -> Draft:
    """Der Entwurf zu einer Verschiebe-Aufgabe, gespeichert bei der Aufgabe.

    Ein gespeicherter Entwurf wird wiederverwendet, ausser ``fresh`` oder ein
    Aenderungswunsch (``change``) verlangt einen neuen. Mit ``now`` gibt es
    keinen Entwurf mehr, sobald der Termin begonnen hat.
    """
    request = build_request(task, event, profile)
    if now is not None and request.start <= now:
        raise MeetingStarted(
            f"Der Termin zu Aufgabe #{task.id} hat schon begonnen ({request.when}); "
            "eine Nachricht dazu kommt zu spät."
        )
    if task.status == IN_PROGRESS and task.result and not fresh and not change:
        return Draft(task.result, MODEL, stored=True)
    run_id = store.start_run(AGENT_NAME)
    try:
        previous = task.result if change else ""
        result = write(request, store.preferences(AGENT_NAME), model, previous, change)
        store.set_status(task.id, IN_PROGRESS, result.text)
    except BaseException:
        store.finish_run(run_id, "fehler")
        raise
    store.finish_run(run_id, "ok", f"Aufgabe #{task.id}: Entwurf aus {result.source}")
    return result


def draft_notes(task: Task, result: Draft) -> dict[str, list[str]]:
    """Der Entwurf als Abschnitt fuers Terminal."""
    origin = "Vorlage, das Modell lieferte nichts Brauchbares" if result.source == TEMPLATE else (
        "gespeichert" if result.stored else "neu"
    )
    return {f"Entwurf der Nachricht zu Aufgabe #{task.id} ({origin})": result.text.splitlines()}


# --- Freigabe

ACCEPT = {"", "j", "ja", "ok", "passt", "übernehmen", "uebernehmen"}
DROP = {"n", "nein", "verwerfen"}
REFUSE = {"a", "ablehnen"}
QUESTION = (
    "Übernehmen (Enter), verwerfen (n), Aufgabe ablehnen (a) "
    "oder Änderungswunsch eintippen: "
)


def to_clipboard(text: str) -> bool:
    """Legt den Text in die macOS-Zwischenablage; False, wenn das nicht geht."""
    try:
        return subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def review(
    task: Task,
    result: Draft,
    store: Store,
    redo: Callable[[str], Draft],
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    copy: Callable[[str], bool] = to_clipboard,
    max_rounds: int = 5,
) -> str:
    """Legt den Entwurf zur Freigabe vor und setzt die Entscheidung um.

    ``redo`` schreibt den Entwurf mit einem Aenderungswunsch neu. Gibt zurueck,
    was entschieden wurde: "uebernommen", "verworfen", "abgelehnt" oder "offen".
    """
    changes: list[str] = []
    for _ in range(max_rounds):
        out(result.text + "\n")
        try:
            answer = ask(QUESTION).strip()
        except EOFError:
            return "offen"
        word = answer.casefold()
        if word in ACCEPT:
            copied = copy(result.text)
            store.set_status(task.id, DONE, result.text)
            out(
                "Übernommen: Der Text liegt in der Zwischenablage, die Aufgabe ist erledigt."
                if copied
                else "Übernommen und Aufgabe erledigt. Die Zwischenablage war nicht erreichbar; der Text steht oben."
            )
            for change in changes:
                try:
                    keep = ask(f"Für künftige Nachrichten merken: „{change}“? (j/n) ").strip().casefold()
                except EOFError:
                    break
                if keep in ("j", "ja"):
                    store.add_preference(AGENT_NAME, change)
                    out("Gemerkt.")
            return "uebernommen"
        if word in DROP:
            store.set_status(task.id, OPEN, "")
            out("Entwurf verworfen, die Aufgabe bleibt offen.")
            return "verworfen"
        if word in REFUSE:
            store.set_status(task.id, REJECTED, "vom Nutzer herausgenommen")
            out("Aufgabe abgelehnt; der Planungs-Agent legt sie nicht wieder an.")
            return "abgelehnt"
        changes.append(answer)
        result = redo(answer)
    out("Der Entwurf bleibt gespeichert; weiter mit demselben Befehl.")
    return "offen"


def move_tasks(store: Store, now: datetime) -> tuple[list[Task], list[Task]]:
    """Offene Verschiebe-Aufgaben, getrennt: Termin steht noch bevor / hat schon begonnen.

    Aufgaben ohne gespeicherten Termin zaehlen zu den erledigten Faellen nicht;
    sie stehen bei den bevorstehenden und werden beim Entwurf abgewiesen.
    """
    events = {event.id: event for event in store.events(datetime.min.date(), datetime.max.date())}
    ahead, started = [], []
    for task in store.tasks((OPEN, IN_PROGRESS)):
        if task.kind != MOVE:
            continue
        event = events.get(task.event_id or "")
        (started if event is not None and event.start <= now else ahead).append(task)
    return ahead, started


def discard_started(store: Store, now: datetime) -> list[Task]:
    """Verwirft Verschiebe-Aufgaben, deren Termin schon begonnen hat, und gibt sie zurueck."""
    _, started = move_tasks(store, now)
    for task in started:
        store.set_status(task.id, DISCARDED, "Termin hat begonnen, Nachricht hinfällig")
    return started


def next_move_task(store: Store, now: datetime) -> Task | None:
    """Die Verschiebe-Aufgabe, die als Naechstes faellig ist und deren Termin noch bevorsteht."""
    ahead, _ = move_tasks(store, now)
    return ahead[0] if ahead else None


def event_of(store: Store, task: Task) -> StoredEvent | None:
    if not task.event_id:
        return None
    events = store.events(datetime.min.date(), datetime.max.date())
    return next((event for event in events if event.id == task.event_id), None)


def main(argv: list[str], store: Store | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now()
    args = [arg for arg in argv[1:] if arg != "--neu"]
    fresh = "--neu" in argv
    own_store = store is None
    store = Store() if own_store else store
    try:
        if args == ["vorgaben"]:
            rows = store.preference_rows(AGENT_NAME)
            print("\n".join(f"{number}: {text}" for number, text in rows) or "Keine Vorgaben gemerkt.")
            return 0
        if len(args) == 2 and args[0] == "vergiss" and args[1].isdigit():
            removed = store.remove_preference(AGENT_NAME, int(args[1]))
            print(f"Vorgabe {args[1]} entfernt." if removed else f"Vorgabe {args[1]} gibt es nicht.")
            return 0 if removed else 1
        if len(args) > 1 or (args and not args[0].lstrip("#").isdigit()):
            print(__doc__, file=sys.stderr)
            return 2
        try:
            profile = load_profile()
        except ProfileError as exc:
            print(exc, file=sys.stderr)
            return 1
        # Aufraeumen: Eine Nachricht zu einem Termin, der schon begonnen hat, braucht niemand mehr.
        for old in discard_started(store, now):
            print(f"Verworfen, der Termin hat schon begonnen: #{old.id} {old.title}")
        if args:
            number = int(args[0].lstrip("#"))
            task = next((task for task in store.tasks() if task.id == number), None)
            if task is None:
                print(f"Aufgabe #{number} gibt es nicht.", file=sys.stderr)
                return 1
        else:
            task = next_move_task(store, now)
            if task is None:
                print("Es gibt keine offene Verschiebe-Aufgabe zu einem Termin, der noch bevorsteht.")
                return 0
        event = event_of(store, task)
        model = ollama_model(profile.model_for(AGENT_NAME))
        try:
            result = draft(task, event, profile, store, model, fresh, now=now)
        except NoMessageTask as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"#{task.id} {task.title}")
        if result.source == TEMPLATE:
            print("Hinweis: Das Modell lieferte nichts Brauchbares, der Entwurf stammt aus der Vorlage.")
        print()
        if not sys.stdin.isatty():
            print(result.text)  # ohne Terminal keine Rueckfrage; der Entwurf bleibt gespeichert
            return 0

        def redo(change: str) -> Draft:
            current = next(item for item in store.tasks() if item.id == task.id)
            return draft(current, event, profile, store, model, change=change, now=now)

        review(task, result, store, redo)
        return 0
    finally:
        if own_store:
            store.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
