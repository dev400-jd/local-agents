"""Aufgaben der Agenten-Datenbank ansehen und abhaken.

Aufruf:
  python -m local_agents.tasks                 offene Aufgaben
  python -m local_agents.tasks alle            auch erledigte und verworfene
  python -m local_agents.tasks erledigt 12     Aufgabe #12 abhaken
  python -m local_agents.tasks offen 12        Aufgabe #12 wieder oeffnen
  python -m local_agents.tasks abgelehnt 12    Aufgabe #12 herausnehmen
"""

import sys

from local_agents.holidays import WEEKDAYS
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import DONE, IN_PROGRESS, OPEN, REJECTED, Store, Task


def format_tasks(tasks: list[Task], profile: Profile) -> str:
    """Aufgaben nach Fertig-bis-Datum gruppiert."""
    if not tasks:
        return "Keine Aufgaben."
    lines = []
    current = None
    for task in tasks:
        if task.due_date != current:
            current = task.due_date
            lines.append(f"fertig bis {WEEKDAYS[current.weekday()]} {current:%d.%m.%Y}")
        status = "" if task.status == OPEN else f", {task.status}"
        lines.append(f"  #{task.id} {task.title} [{_label(profile, task.context)}{status}]")
    return "\n".join(lines)


def _label(profile: Profile, context: str) -> str:
    try:
        return profile.label(context)
    except KeyError:  # Kontext wurde inzwischen aus dem Profil entfernt
        return context


def main(argv: list[str], store: Store | None = None, profile: Profile | None = None) -> int:
    args = argv[1:]
    try:
        profile = load_profile() if profile is None else profile
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    store = Store() if store is None else store
    if not args or args == ["alle"]:
        statuses = None if args else (OPEN, IN_PROGRESS)
        print(format_tasks(store.tasks(statuses), profile))
        return 0
    if len(args) == 2 and args[0] in (DONE, OPEN, REJECTED) and args[1].lstrip("#").isdigit():
        task_id = int(args[1].lstrip("#"))
        if store.set_status(task_id, args[0]):
            print(f"Aufgabe #{task_id}: {args[0]}.")
            return 0
        print(f"Aufgabe #{task_id} gibt es nicht.", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
