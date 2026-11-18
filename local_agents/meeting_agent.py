"""Termin-Agent: versteht, um welche Art von Termin es geht und welche
Ergebnisse dem Nutzer dafuer helfen.

Er bekommt vom Status-Agenten die naechste Aufgabe mit ihrem Termin. Das Modell
ordnet den Termin einer Art aus dem Katalog zu (``meeting_types.py``) und
leitet aus der Rolle des Nutzers ab, was dieser dort liefern sollte. Zweck und
uebliche Beitraege der Terminart kommen unveraendert aus dem Katalog. Das
Ergebnis (``Brief``) landet in der Datenbank; spaetere Agenten, die den Termin
vorbereiten, lesen es als Auftrag.
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from local_agents.agent import MODEL_SETTINGS, structured
from local_agents.holidays import WEEKDAYS
from local_agents.meeting_types import BY_ID, MEETING_TYPES, OTHER_TYPE, keyword_hint
from local_agents.profile import CUSTOMER, Profile
from local_agents.store import Brief, Store, StoredEvent, Task

AGENT_NAME = "termin"
MIN_DELIVERABLES, MAX_DELIVERABLES = 1, 5
MAX_ITEM_CHARS = 220


class MeetingAnalysis(BaseModel):
    """Einordnung eines Termins und was der Nutzer dafuer braucht."""

    type: str = Field(description="id der Terminart aus dem Katalog")
    reason: str = Field(description="Ein kurzer deutscher Satz, woran die Terminart erkennbar ist")
    deliverables: list[str] = Field(
        description="Zwei bis fuenf konkrete Ergebnisse, die dem Nutzer in seiner Rolle fuer "
        "diesen Termin helfen; jedes ein kurzer deutscher Halbsatz"
    )


@dataclass
class MeetingDeps:
    event: StoredEvent
    profile: Profile


# Mittlerer Reasoning-Aufwand: Mit niedrigem uebernahm gpt-oss die Beitraege
# woertlich oder erfand Projektdetails; mit mittlerem bezieht es sie auf die
# Rolle (9-20 s statt 2-10 s je Termin, gemessen 2026-11-15).
meeting_agent = Agent(
    deps_type=MeetingDeps,
    output_type=structured(MeetingAnalysis),
    model_settings={**MODEL_SETTINGS, "max_tokens": 4096, "openai_reasoning_effort": "medium"},
    retries=2,
)


def role_in(profile: Profile, context_id: str) -> str:
    """Rolle des Nutzers im Kontext des Termins, sonst seine allgemeine Rolle."""
    for context in profile.contexts:
        if context.id == context_id and context.role:
            return context.role
    return profile.role


def situation(event: StoredEvent, profile: Profile) -> str:
    """Was das Modell ueber Termin und Nutzer weiss, als Text."""
    minutes = int((event.end - event.start).total_seconds() // 60)
    lines = [
        f"Titel: {event.title}",
        f"Zeit: {WEEKDAYS[event.start.weekday()]} {event.start:%d.%m.%Y %H:%M}, {minutes} Minuten",
        f"Eingeladene: {event.attendees}",
    ]
    context = next((c for c in profile.contexts if c.id == event.context), None)
    if context:
        kind = "Kundenprojekt" if context.kind == CUSTOMER else "intern beim Arbeitgeber"
        detail = f" - {context.description}" if context.description else ""
        lines.append(f"Kontext: {context.label} ({kind}){detail}")
    role = role_in(profile, event.context)
    if role:
        lines.append(f"Rolle des Nutzers in diesem Kontext: {role}")
    hint = keyword_hint(event.title)
    if hint:
        lines.append(f"Stichwort im Titel deutet auf: {hint.id}")
    return "\n".join(lines)


@meeting_agent.instructions
def instructions(ctx: RunContext[MeetingDeps]) -> str:
    catalogue = "\n".join(
        f"- {t.id}: {t.label}{f' ({t.framework})' if t.framework else ''}. {t.purpose} "
        f"Uebliche Beitraege: {'; '.join(t.contributions)}."
        for t in MEETING_TYPES
    )
    return (
        "Du kennst dich mit Besprechungsformaten im Berufsalltag aus, auch mit Scrum. "
        "Ordne den Termin unten genau einer Terminart aus dem Katalog zu (type = id) "
        "und begruende das in einem Satz. Titel, Dauer und Zahl der Eingeladenen sind "
        f"deine Anhaltspunkte; passt nichts eindeutig, nimm {OTHER_TYPE}.\n"
        "Leite dann ab, welche Ergebnisse dem Nutzer in seiner Rolle fuer diesen Termin "
        f"helfen (deliverables, {MIN_DELIVERABLES} bis {MAX_DELIVERABLES} Stueck): Was sollte er "
        "vorbereitet in der Hand haben, um die ueblichen Beitraege dieser Terminart zu "
        "liefern? Regeln dafuer:\n"
        "- Halte dich an die ueblichen Beitraege der gewaehlten Terminart: je Beitrag "
        "hoechstens ein Ergebnis, keine zusaetzlichen Pflichten. Erwartet die Terminart "
        "keinen eigenen Beitrag, nenne nur ein Ergebnis, das genau das sagt.\n"
        "- Uebernimm die Beitraege nicht woertlich. Uebersetze sie in die Rolle des "
        "Nutzers und sein Projekt: Wofuer ist jemand in dieser Rolle verantwortlich, "
        "und was heisst der Beitrag dann konkret? Nenne Rolle oder Projekt im Text.\n"
        "- Jedes Ergebnis ist etwas, das man vorbereiten kann, etwa eine Liste, ein "
        "Stand oder eine Frage; kurze deutsche Halbsaetze ohne Anrede.\n"
        "- Erfinde keine Projektdetails, die unten nicht stehen.\n"
        f"Katalog:\n{catalogue}\n"
        f"Termin und Nutzer:\n{situation(ctx.deps.event, ctx.deps.profile)}"
    )


@meeting_agent.output_validator
def valid_analysis(ctx: RunContext[MeetingDeps], analysis: MeetingAnalysis) -> MeetingAnalysis:
    if analysis.type not in BY_ID:
        raise ModelRetry(f"type muss eine id aus dem Katalog sein: {', '.join(BY_ID)}.")
    items = [" ".join(item.split()) for item in analysis.deliverables if item.strip()]
    if len(items) < MIN_DELIVERABLES:
        raise ModelRetry(f"deliverables braucht {MIN_DELIVERABLES} bis {MAX_DELIVERABLES} Eintraege.")
    # Zu viel oder zu lang wird gekuerzt statt zurueckgewiesen.
    analysis.deliverables = [item[:MAX_ITEM_CHARS] for item in items[:MAX_DELIVERABLES]]
    return analysis


def analyse(task: Task, event: StoredEvent, profile: Profile, store: Store, model, fresh: bool = False) -> Brief:
    """Ordnet den Termin ein und speichert das Ergebnis zur Aufgabe.

    Liegt schon eine Einordnung fuer denselben Titel vor, wird sie
    wiederverwendet, ausser ``fresh`` verlangt eine neue.
    """
    known = store.brief(task.id)
    if known and known.title == event.title and not fresh:
        return known
    run_id = store.start_run(AGENT_NAME)
    try:
        deps = MeetingDeps(event=event, profile=profile)
        try:
            analysis = meeting_agent.run_sync(
                "Ordne den Termin ein und nenne die Ergebnisse.", deps=deps, model=model
            ).output
        except UnexpectedModelBehavior:
            # Das Modell liefert nichts Brauchbares: Stichwort im Titel oder "sonstiges",
            # mit den ueblichen Beitraegen aus dem Katalog.
            fallback = keyword_hint(event.title) or BY_ID[OTHER_TYPE]
            analysis = MeetingAnalysis(
                type=fallback.id,
                reason="Das Modell konnte den Termin nicht einordnen; Angaben aus dem Katalog.",
                deliverables=list(fallback.contributions),
            )
        brief = store.save_brief(
            task.id,
            event.title,
            analysis.type,
            " ".join(analysis.reason.split()),
            tuple(analysis.deliverables),
            role_in(profile, event.context),
        )
    except BaseException:
        store.finish_run(run_id, "fehler")
        raise
    store.finish_run(run_id, "ok", f"Aufgabe #{task.id}: {brief.meeting_type}")
    return brief


def brief_notes(brief: Brief, profile: Profile, context_id: str) -> dict[str, list[str]]:
    """Die Einordnung als Abschnitte (Ueberschrift -> Zeilen) fuers Terminal."""
    meeting_type = BY_ID.get(brief.meeting_type, BY_ID[OTHER_TYPE])
    framework = f", {meeting_type.framework}" if meeting_type.framework else ""
    try:
        where = profile.label(context_id)
    except KeyError:
        where = context_id
    who = f"als {brief.role}, {where}" if brief.role else where
    return {
        f"Terminart: {meeting_type.label}{framework}": [
            f"Woran erkannt: {brief.reason}",
            f"Zweck: {meeting_type.purpose}",
            "Was man dort üblicherweise beiträgt:",
            *(f"- {item}" for item in meeting_type.contributions),
        ],
        f"Ergebnisse, die dir für diesen Termin helfen ({who})": [
            f"- {item}" for item in brief.deliverables
        ],
    }
