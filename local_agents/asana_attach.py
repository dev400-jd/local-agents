"""Passende Dateien zu einer Asana-Aufgabe finden und als Link anhaengen.

Kein eigener Aufruf: Der Schreib-Agent (asana_actions.py) nutzt das fuer die
Aktion "Anhaenge".

1. Finden (Code): Die Suche laeuft mit Name und Beschreibung der Aufgabe ueber
   die Dateien der Dokumentordner (``[documents]`` im Profil) und buendelt die
   Treffer je Datei. Das Dokument, aus dem die Beschreibung stammt, steht vorn.
2. Link (Code): Zu jeder Datei wird die Adresse in SharePoint gebildet. Die
   Zuordnung Ordner -> Bibliothek steht in den Einstellungen des
   OneDrive-Programms; sie wird nur gelesen. Laesst sich keine Adresse bilden,
   wird die Datei nicht angeboten.
3. Anhaengen: Der Schreib-Agent haengt nach der Auswahl des Nutzers Links an,
   keine Kopien. Die Datei bleibt in SharePoint, mit den Rechten dort.

Kein Modell ist beteiligt.
"""

import glob
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from local_agents.profile import Profile
from local_agents.search import DOCUMENT, Embedder, Index

SETTINGS_DIR = Path.home() / "Library/Application Support/OneDrive/settings/Business1"
DEFAULT_LIBRARY = "Shared Documents"
MAX_OFFERED = 5
QUERY_HITS = 40
MAX_NOTES_CHARS = 1500
NAME_WEIGHT = 2.0  # der Name der Aufgabe zaehlt doppelt so viel wie ihre Beschreibung
RANK_OFFSET = 10
ALLOWED_HOST_SUFFIX = ".sharepoint.com"


@dataclass(frozen=True)
class Scope:
    """Eine abgeglichene Bibliothek oder Verknuepfung: wo ihr Ordner in SharePoint liegt."""

    base: str  # Adresse der Bibliothek, ohne "/" am Ende
    remote: str  # Pfad des abgeglichenen Ordners in der Bibliothek


@dataclass(frozen=True)
class Candidate:
    name: str  # Dateiname
    title: str  # Titel des Dokuments im Suchindex
    source: str  # Ordnername/Pfad, wie die Suche ihn fuehrt
    url: str
    size: int  # Bytes
    reason: str  # worauf die Datei passt
    score: float


Finder = Callable[[str, str, str], list[Candidate]]


class NoAttachments(ValueError):
    """Es laesst sich nichts anbieten; der Text sagt, warum."""


def _values(path: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            found.setdefault(key.strip(), []).append(value.strip())
    return found


def read_scopes(settings: Path = SETTINGS_DIR) -> list[Scope]:
    """Die Verknuepfungen aus den OneDrive-Einstellungen; leer, wenn sie sich nicht lesen lassen.

    Das Format ist nicht dokumentiert. Gelesen wird je Verknuepfung die Zeile
    ``AddedScope`` (Adresse der Site, Pfad des Ordners) und, fuer den Namen der
    Bibliothek, ``DavUrlNamespace`` aus der zugehoerigen ``ClientPolicy``-Datei.
    """
    libraries = []
    for path in glob.glob(str(settings / "ClientPolicy*.ini")):
        libraries += [value.rstrip("/") for value in _values(Path(path)).get("DavUrlNamespace", [])]
    scopes = []
    for path in glob.glob(str(settings / "*-*-*-*-*.ini")):
        for value in _values(Path(path)).get("AddedScope", []):
            try:
                fields = shlex.split(value)
                site, remote = fields[3].rstrip("/"), fields[9].strip("/")
            except (ValueError, IndexError):
                continue
            if not site.startswith("https://"):
                continue
            base = next((library for library in libraries if library.startswith(site + "/")), f"{site}/{DEFAULT_LIBRARY}")
            scopes.append(Scope(base, remote))
    return scopes


def scope_of(folder: Path, scopes: list[Scope]) -> Scope | None:
    """Die Verknuepfung zu diesem lokalen Ordner; None, wenn keine oder nicht eindeutig.

    Der lokale Ordner heisst wie der letzte Teil des entfernten Pfads oder
    enthaelt ihn (OneDrive stellt gelegentlich den Namen der Site voran).
    """
    name = folder.name.casefold()
    exact = [scope for scope in scopes if scope.remote.rsplit("/", 1)[-1].casefold() == name]
    if len(exact) == 1:
        return exact[0]
    partly = [scope for scope in scopes if scope.remote.rsplit("/", 1)[-1].casefold() in name]
    if len(partly) == 1:
        return partly[0]
    return scopes[0] if len(scopes) == 1 and not exact and not partly else None


def link(scope: Scope, relative: str) -> str:
    """Die Adresse der Datei in SharePoint; ``?web=1`` oeffnet sie im Browser statt sie herunterzuladen."""
    parts = urlsplit(scope.base)
    path = "/".join(part for part in (parts.path.strip("/"), scope.remote, relative.strip("/")) if part)
    return f"{parts.scheme}://{parts.netloc}/{quote(path)}?web=1"


def is_allowed_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").endswith(ALLOWED_HOST_SUFFIX)


def _size_text(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB".replace(".", ",") if size >= 1024 * 1024 else f"{max(1, size // 1024)} KB"


def find(
    index: Index,
    profile: Profile,
    task: str,
    notes: str,
    embedder: Embedder | None = None,
    preferred: str = "",
    scopes: list[Scope] | None = None,
    limit: int = MAX_OFFERED,
) -> list[Candidate]:
    """Die Dateien der Dokumentordner, die zu Name und Beschreibung der Aufgabe passen, die beste zuerst.

    ``preferred`` ist der Titel des Dokuments, aus dem die Beschreibung stammt.
    Wirft ``NoAttachments``, wenn es keine Ordner, keine Verknuepfung oder keine Treffer gibt.
    """
    folders = {Path(raw).expanduser().name: Path(raw).expanduser() for raw in profile.document_folders}
    if not folders:
        raise NoAttachments(
            "Anhänge suche ich in den Dokumentordnern; im Profil steht unter [documents] keiner."
        )
    scopes = read_scopes() if scopes is None else scopes
    linked = {name: scope for name, folder in folders.items() if (scope := scope_of(folder, scopes)) is not None}
    if not linked:
        raise NoAttachments(
            "Zu den Dokumentordnern finde ich in den OneDrive-Einstellungen keine SharePoint-Adresse. "
            "Ohne sie kann ich keinen Link anhängen."
        )
    scores: dict[str, float] = {}
    reasons: dict[str, tuple[float, str]] = {}
    titles: dict[str, str] = {}
    for question, weight in ((task, NAME_WEIGHT), (" ".join(notes.split())[:MAX_NOTES_CHARS], 1.0)):
        if not question.strip():
            continue
        seen: set[str] = set()
        for rank, hit in enumerate(index.search(question, QUERY_HITS, kinds=(DOCUMENT,), embedder=embedder)):
            source = hit.passage.source
            if source.split("/", 1)[0] not in linked or source in seen:
                continue  # je Frage zaehlt der beste Abschnitt einer Datei
            seen.add(source)
            place = 1 / (RANK_OFFSET + rank)
            scores[source] = scores.get(source, 0.0) + weight * place
            titles[source] = hit.passage.feature
            # Als Grund zaehlt der bestplatzierte Abschnitt; bei Gleichstand der zur Beschreibung,
            # weil der Name der Aufgabe oft nur auf den Dateinamen passt.
            if place >= reasons.get(source, (0.0, ""))[0]:
                heading = hit.passage.title.removeprefix(f"{hit.passage.feature}: ")
                reasons[source] = (place, heading if heading != hit.passage.feature else "")
    for source, title in titles.items():
        if preferred and title == preferred:
            scores[source] += NAME_WEIGHT  # daraus stammt die Beschreibung
            reasons[source] = (NAME_WEIGHT, "daraus stammt die Beschreibung")
    found = []
    for source in sorted(scores, key=lambda source: (-scores[source], source)):
        folder_name, relative = source.split("/", 1)
        path = folders[folder_name] / relative
        if not path.is_file():
            continue
        found.append(
            Candidate(
                path.name,
                titles[source],
                source,
                link(linked[folder_name], relative),
                path.stat().st_size,
                reasons[source][1],
                scores[source],
            )
        )
    if not found:
        raise NoAttachments(f"Zu „{task}“ finde ich in den Dokumentordnern keine passende Datei.")
    return found[:limit]


def offer_lines(candidates: list[Candidate]) -> list[str]:
    lines = []
    for number, candidate in enumerate(candidates, start=1):
        reason = f" · passt auf: {candidate.reason}" if candidate.reason else ""
        lines.append(f"  {number}. {candidate.source} ({_size_text(candidate.size)}){reason}")
        lines.append(f"     {candidate.url}")
    return lines


def chosen(answer: str, count: int) -> list[int] | None:
    """Die gewaehlten Nummern (ab 0) aus "1,3", "2" oder "alle"; leer bei nein, None bei Unverstaendlichem."""
    text = answer.strip().casefold()
    if text in ("", "n", "nein", "keine", "keinen"):
        return []
    if text in ("a", "alle", "j", "ja"):
        return list(range(count)) if text in ("a", "alle") or count == 1 else None
    try:
        numbers = sorted({int(part) for part in text.replace(",", " ").replace("und", " ").split()})
    except ValueError:
        return None
    return [number - 1 for number in numbers] if numbers and all(1 <= number <= count for number in numbers) else None
