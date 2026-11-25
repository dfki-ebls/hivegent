"""The refusals and receipts of a changeset, and the predicates that decide them."""

from collections.abc import Callable, Mapping

from ...shared.l10n import Localized
from ...tools.changes import DeleteKind
from ..paths import directory_not_found, document_not_found


def _moved(source: str, destination: str) -> Localized[str]:
    return Localized(
        en=f"Moved '{source}' to '{destination}'.",
        de=f"„{source}“ nach „{destination}“ verschoben.",
    )


def _deleted(path: str) -> Localized[str]:
    return Localized(en=f"Deleted '{path}'.", de=f"„{path}“ gelöscht.")


def _created_directory(path: str) -> Localized[str]:
    return Localized(en=f"Created '{path}'.", de=f"„{path}“ erstellt.")


def _uploaded(path: str) -> Localized[str]:
    return Localized(en=f"Uploaded '{path}'.", de=f"„{path}“ hochgeladen.")


def _is_existing_directory(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' is an existing directory",
        de=f"„{path}“ ist ein vorhandener Ordner",
    )


_DOCUMENT_EXISTS = Localized(
    en="Document already exists", de="Das Dokument existiert bereits"
)


def _destination_exists_at(path: str) -> Localized[str]:
    return Localized(
        en=f"Destination already exists: {path}",
        de=f"Das Ziel existiert bereits: {path}",
    )


def _claimed_twice(path: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{path}' takes part in more than one change: a path may be moved "
            "or deleted once and filled once per call"
        ),
        de=(
            f"„{path}“ ist an mehr als einer Änderung beteiligt: Ein Pfad darf "
            "pro Aufruf einmal verschoben oder gelöscht und einmal befüllt werden"
        ),
    )


def _changed_meanwhile(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' changed while this change was being prepared. Retry it",
        de=f"„{path}“ wurde geändert, während diese Änderung vorbereitet wurde. Versuche es erneut",
    )


def _same_paths(path: str) -> Localized[str]:
    return Localized(
        en=(
            f"Source and destination are the same: {path}. A document's extension "
            "is derived from the entry and cannot change by moving"
        ),
        de=(
            f"Quelle und Ziel sind identisch: {path}. Die Dateiendung eines Dokuments "
            "ergibt sich aus dem Eintrag und lässt sich durch Bewegen nicht ändern"
        ),
    )


def _crosses_roots(source: str, destination: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{source}' cannot move to '{destination}', since only one of them is "
            "written directly. Write its text to the destination and delete the source instead"
        ),
        de=(
            f"„{source}“ kann nicht nach „{destination}“ verschoben werden, da nur eines "
            "von beiden direkt geschrieben wird. Schreibe den Text ins Ziel und lösche die Quelle"
        ),
    )


def _kept(path: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{path}' is reserved: an '.assets' folder belongs to its document, and "
            "a folder the host keeps can be read but not changed"
        ),
        de=(
            f"„{path}“ ist reserviert: Ein „.assets“-Ordner gehört zu seinem Dokument, "
            "und einen Ordner des Hosts kann man lesen, aber nicht ändern"
        ),
    )


def _carries_hidden(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' cannot change because it contains inaccessible entries",
        de=f"„{path}“ kann nicht geändert werden, da es unzugängliche Einträge enthält",
    )


def _inaccessible(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' is not accessible", de=f"„{path}“ ist nicht zugänglich"
    )


def _describes_nothing(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' describes no asset beside it",
        de=f"„{path}“ beschreibt kein Asset daneben",
    )


def _kept_by_cbrkit(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' is kept by cbrkit for its case base and cannot be written",
        de=f"„{path}“ wird von cbrkit für seine Fallbasis geführt und kann nicht geschrieben werden",
    )


def _sidecar_follows(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' moves with its case base and cannot be uploaded or moved alone",
        de=(
            f"„{path}“ wird mit seiner Fallbasis verschoben und kann nicht allein "
            "hochgeladen oder verschoben werden"
        ),
    )


def _into_itself(path: str) -> Localized[str]:
    return Localized(
        en=f"Cannot move a directory into itself: {path}",
        de=f"Ein Ordner kann nicht in sich selbst bewegt werden: {path}",
    )


_NOT_FOUND: Mapping[DeleteKind, Callable[[str], Localized[str]]] = {
    "dir": directory_not_found,
    "entry": document_not_found,
}
