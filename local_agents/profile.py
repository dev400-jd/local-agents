"""Gemeinsames Gedaechtnis der Agenten: wer der Nutzer ist und in welchen
Arbeitskontexten (intern, Kunden) er sich bewegt.

Das Profil ist eine TOML-Datei ausserhalb des Repos, weil sie Kundennamen
enthaelt. Jeder Agent kann sie laden: ``describe()`` liefert den Text fuer die
Instruktionen, ``classify()`` ordnet etwas deterministisch einem Kontext zu.
"""

import os
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from local_agents.holidays import STATES, WEEKDAYS

DEFAULT_PROFILE_PATH = Path.home() / ".config" / "local-agents" / "profile.toml"

INTERNAL = "intern"
CUSTOMER = "kunde"
# Feste Kontexte fuer alles, was das Profil nicht abdeckt.
EXTERNAL_UNKNOWN = "extern"  # fremde Domains, die keinem Kontext zugeordnet sind
OTHER = "sonstig"  # ohne Teilnehmer: Feiertage, Blocker, Privates
# Wie stark ein Kundenprojekt gerade beansprucht, aufsteigend.
STRESS_LEVELS = ("niedrig", "mittel", "hoch")


class ProfileError(ValueError):
    """Die Profildatei ist fehlerhaft."""


@dataclass(frozen=True)
class Context:
    """Ein Arbeitskontext, zum Beispiel der Arbeitgeber oder ein Kunde."""

    id: str
    kind: str  # INTERNAL oder CUSTOMER
    label: str
    description: str = ""
    # Rolle des Nutzers in diesem Kontext, falls sie von der allgemeinen abweicht.
    role: str = ""
    # Ordner mit den User Stories dieses Kontexts als Markdown (stories.py).
    stories_dir: str = ""
    domains: frozenset[str] = frozenset()
    keywords: tuple[str, ...] = ()
    # Nur fuer Kunden: Stresslevel des Projekts (STRESS_LEVELS, leer = niedrig)
    # und Wochentage beim Kunden (0 = Montag), an denen Internes weichen soll.
    stress: str = ""
    onsite_days: frozenset[int] = frozenset()

    def has_keyword(self, text: str) -> bool:
        lowered = text.casefold()
        return any(keyword in lowered for keyword in self.keywords)


@dataclass(frozen=True)
class Profile:
    """Nutzerprofil mit Arbeitskontexten; leer, wenn keine Datei existiert."""

    name: str = ""
    employer: str = ""
    role: str = ""
    # Bundesland-Kuerzel des Arbeitsorts, z. B. "HE"; bestimmt die Feiertage.
    state: str = ""
    contexts: tuple[Context, ...] = field(default=())
    # Technische Domains ohne Aussagekraft, z. B. Adressen von Teams-Kanaelen.
    ignored_domains: frozenset[str] = frozenset()
    # Planung: Aufgaben sind so viele Tage vor dem Termin fertig; Termine mit
    # einem dieser Stichwoerter im Titel bekommen keine Aufgabe.
    lead_days: int = 1
    plan_skip: tuple[str, ...] = ()
    # Aufgaben "Termin verschieben" sind so viele Tage vor dem Termin faellig.
    move_lead_days: int = 3
    # Vor dem Lesen der Aufgaben den Kalender abgleichen (sync.py).
    sync_enabled: bool = True
    # Anrede in Nachrichtenentwuerfen (message_agent.py): "du" oder "Sie".
    message_form: str = "du"
    # Kalenderwache (watch.py): Aenderungen als macOS-Mitteilung zeigen, vorlesen.
    # So oft gleicht die Wache den Asana-Spiegel ab (Minuten); 0 = gar nicht.
    asana_sync_minutes: int = 30
    # Projekte, in die der Agent schreiben darf (asana_actions.py); leer = nirgends.
    asana_write_projects: tuple[str, ...] = ()
    # Embedding-Modell der Bedeutungssuche (search.py); leer = nur Wortsuche.
    search_embeddings: str = ""
    watch_notify: bool = True
    watch_speak: bool = False
    # Sprachausgabe der Kurzfassung: an/aus und die macOS-Stimme.
    voice_enabled: bool = True
    voice: str = "Anna"
    # Neuronale Stimme (Piper); leer = immer die macOS-Stimme.
    voice_model: str = "de_DE-thorsten-high"
    # Sprechtempo (1.0 = normal, groesser = schneller) und Sprecher-Nummer
    # fuer Piper-Stimmen mit mehreren Sprechern.
    voice_speed: float = 1.0
    voice_speaker: int | None = None
    # Spracheingabe der Antwort: an/aus, Whisper-Modell und Sprache.
    listen_enabled: bool = True
    listen_model: str = "large-v3-turbo"
    listen_language: str = "de"
    # Ollama-Modell je Agent (Agentenname -> Modell); fehlt einer, gilt das Standardmodell.
    models: dict[str, str] = field(default_factory=dict)
    # Telegram fuer den Scrum Master: nur auf ausdruecklichen Wunsch, weil die
    # Nachrichten den Rechner verlassen. Zugelassen ist genau ein Chat.
    telegram_enabled: bool = False
    telegram_chat_id: int | None = None
    telegram_ask_at: tuple[str, ...] = ()  # Uhrzeiten "HH:MM", an denen der Bot nachfragt

    def model_for(self, agent: str) -> str:
        """Modellname fuer den Agenten: sein eigener Eintrag, sonst ``default`` aus
        ``[models]``; leer fuer das Standardmodell aus dem Code."""
        return self.models.get(agent) or self.models.get("default", "")

    def context_ids(self) -> list[str]:
        """Alle Werte, die ``classify`` liefern kann."""
        return [context.id for context in self.contexts] + [EXTERNAL_UNKNOWN, OTHER]

    def label(self, context_id: str) -> str:
        for context in self.contexts:
            if context.id == context_id:
                return context.label
        return {EXTERNAL_UNKNOWN: "Extern (unbekannt)", OTHER: "Sonstiges"}[context_id]

    def classify(self, text: str, organizer: str = "", attendees: Iterable[str] = ()) -> str:
        """Ordnet einen Text (z. B. Termintitel) einem Kontext zu und gibt dessen id zurueck.

        ``organizer`` ist die Mail-Domain des Einladenden (bei Mails: des
        Absenders), ``attendees`` die Domains der Teilnehmer, je Person einmal.

        Reihenfolge: Kundenstichwort oder Kunde laedt ein -> Kunde. Internes
        Stichwort -> intern. Sonst entscheidet die Mehrheit der Teilnehmer:
        Einzelne Kundenadressen in einer internen Runde (etwa Kundenpostfaecher
        von Kollegen) machen daraus keinen Kundentermin.
        """
        organizer = organizer.casefold()
        if organizer in self.ignored_domains:
            organizer = ""
        counts = Counter(domain.casefold() for domain in attendees)
        for domain in self.ignored_domains:
            counts.pop(domain, None)
        if organizer and not counts:
            counts[organizer] = 1
        customers = [c for c in self.contexts if c.kind == CUSTOMER]
        internals = [c for c in self.contexts if c.kind == INTERNAL]

        for context in customers:
            if context.has_keyword(text) or organizer in context.domains:
                return context.id
        for context in internals:
            if context.has_keyword(text):
                return context.id
        if not counts:
            return OTHER

        def weight(context: Context) -> int:
            return sum(n for domain, n in counts.items() if domain in context.domains)

        internal_weight = sum(weight(context) for context in internals)
        customer = max(customers, key=weight, default=None)
        if customer and weight(customer) > 0 and weight(customer) >= internal_weight:
            return customer.id
        foreign_organizer = organizer and not any(organizer in c.domains for c in internals)
        if internal_weight * 2 >= counts.total() and not foreign_organizer:
            return max(internals, key=weight).id
        return EXTERNAL_UNKNOWN

    def describe(self) -> str:
        """Kurzbeschreibung fuer die Instruktionen eines Agenten."""
        if not (self.name or self.contexts or self.state):
            return ""
        lines = []
        who = ", ".join(part for part in (self.name, self.role, self.employer) if part)
        if who:
            lines.append(f"Der Nutzer: {who}.")
        if self.state:
            lines.append(f"Arbeitsort: {STATES[self.state]}.")
        if self.contexts:
            lines.append("Seine Arbeitskontexte (id: Bedeutung):")
            for context in self.contexts:
                detail = f" - {context.description}" if context.description else ""
                if context.role:
                    detail += f"; Rolle {context.role}"
                if context.stress:
                    detail += f"; Stresslevel {context.stress}"
                if context.onsite_days:
                    days = ", ".join(WEEKDAYS[day] for day in sorted(context.onsite_days))
                    detail += f"; Kundentag {days}"
                lines.append(f"- {context.id}: {context.label}{detail}")
            lines.append(f"- {EXTERNAL_UNKNOWN}: andere externe Beteiligte, noch nicht zugeordnet")
            lines.append(f"- {OTHER}: ohne Beteiligte, z. B. Feiertage und Blocker")
        return "\n".join(lines)


def _weekday(path: Path, name: str) -> int:
    """Wochentag aus "Mi" oder "Mittwoch" als Zahl, 0 = Montag."""
    short = name.strip().casefold()[:2]
    for index, weekday in enumerate(WEEKDAYS):
        if short == weekday.casefold():
            return index
    raise ProfileError(f"{path}: unbekannter Wochentag {name!r}. Erlaubt: {', '.join(WEEKDAYS)}.")


def profile_path() -> Path:
    return Path(os.environ.get("LOCAL_AGENTS_PROFILE", DEFAULT_PROFILE_PATH))


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ProfileError('write_projects unter [asana] braucht eine Liste von Projektnamen, etwa ["Mein Projekt"].')
    return tuple(item.strip() for item in value)


def _minutes(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProfileError("sync_minutes unter [asana] braucht eine ganze Zahl ab 0 (0 = aus).")
    return value


def load_profile(path: Path | None = None) -> Profile:
    """Laedt das Profil; ohne Datei gibt es ein leeres Profil."""
    path = path or profile_path()
    if not path.is_file():
        return Profile()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{path}: {exc}") from exc
    person = data.get("person", {})
    contexts = []
    for raw in data.get("contexts", []):
        try:
            context = Context(
                id=raw["id"],
                kind=raw["kind"],
                label=raw.get("label", raw["id"]),
                description=raw.get("description", ""),
                role=raw.get("role", ""),
                stories_dir=raw.get("stories_dir", ""),
                domains=frozenset(d.casefold() for d in raw.get("domains", [])),
                keywords=tuple(k.casefold() for k in raw.get("keywords", [])),
                stress=raw.get("stress", "").casefold(),
                onsite_days=frozenset(_weekday(path, d) for d in raw.get("onsite_days", [])),
            )
        except KeyError as exc:
            raise ProfileError(f"{path}: Kontext ohne Feld {exc}.") from exc
        if context.stress and context.stress not in STRESS_LEVELS:
            raise ProfileError(
                f"{path}: stress von {context.id} muss {', '.join(STRESS_LEVELS)} sein."
            )
        if context.kind not in (INTERNAL, CUSTOMER):
            raise ProfileError(f"{path}: kind von {context.id} muss intern oder kunde sein.")
        if context.id in (EXTERNAL_UNKNOWN, OTHER) or context.id in [c.id for c in contexts]:
            raise ProfileError(f"{path}: Kontext-id {context.id} ist reserviert oder doppelt.")
        contexts.append(context)
    state = person.get("state", "").upper()
    if state and state not in STATES:
        raise ProfileError(f"{path}: state {state!r} unbekannt. Erlaubt: {', '.join(STATES)}.")
    planning = data.get("planning", {})
    days = {}
    for key, default in (("lead_days", 1), ("move_lead_days", 3)):
        value = planning.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ProfileError(f"{path}: {key} muss eine ganze Zahl ab 0 sein.")
        days[key] = value
    telegram = data.get("telegram", {})
    chat_id = telegram.get("chat_id")
    if chat_id is not None and (isinstance(chat_id, bool) or not isinstance(chat_id, int)):
        raise ProfileError(f"{path}: chat_id in [telegram] muss eine ganze Zahl sein.")
    ask_at = tuple(str(clock) for clock in telegram.get("ask_at", []))
    for clock in ask_at:
        hour, _, minute = clock.partition(":")
        if not (hour.isdigit() and minute.isdigit() and int(hour) < 24 and int(minute) < 60 and len(minute) == 2):
            raise ProfileError(f"{path}: ask_at in [telegram] braucht Uhrzeiten wie \"16:30\".")
    speech = data.get("voice", {})
    speed = speech.get("speed", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2:
        raise ProfileError(f"{path}: speed in [voice] muss eine Zahl von 0.5 bis 2 sein.")
    speaker = speech.get("speaker")
    if speaker is not None and (isinstance(speaker, bool) or not isinstance(speaker, int) or speaker < 0):
        raise ProfileError(f"{path}: speaker in [voice] muss eine ganze Zahl ab 0 sein.")
    form = str(data.get("messages", {}).get("form", "du"))
    if form not in ("du", "Sie"):
        raise ProfileError(f"{path}: form in [messages] muss \"du\" oder \"Sie\" sein.")
    return Profile(
        **days,
        message_form=form,
        voice_enabled=bool(speech.get("enabled", True)),
        voice=str(speech.get("voice", "Anna")),
        voice_model=str(speech.get("model", "de_DE-thorsten-high")),
        voice_speed=float(speed),
        voice_speaker=speaker,
        listen_enabled=bool(data.get("listen", {}).get("enabled", True)),
        listen_model=str(data.get("listen", {}).get("model", "large-v3-turbo")),
        listen_language=str(data.get("listen", {}).get("language", "de")),
        models={str(agent): str(model) for agent, model in data.get("models", {}).items()},
        telegram_enabled=bool(telegram.get("enabled", False)),
        telegram_chat_id=chat_id,
        telegram_ask_at=tuple(f"{int(c.split(':')[0]):02d}:{c.split(':')[1]}" for c in ask_at),
        plan_skip=tuple(word.casefold() for word in planning.get("skip", [])),
        sync_enabled=bool(planning.get("sync", True)),
        asana_sync_minutes=_minutes(data.get("asana", {}).get("sync_minutes", 30)),
        asana_write_projects=_names(data.get("asana", {}).get("write_projects", [])),
        search_embeddings=str(data.get("search", {}).get("embeddings", "")).strip(),
        watch_notify=bool(data.get("watch", {}).get("notify", True)),
        watch_speak=bool(data.get("watch", {}).get("speak", False)),
        state=state,
        name=person.get("name", ""),
        employer=person.get("employer", ""),
        role=person.get("role", ""),
        contexts=tuple(contexts),
        ignored_domains=frozenset(d.casefold() for d in data.get("ignored_domains", [])),
    )
