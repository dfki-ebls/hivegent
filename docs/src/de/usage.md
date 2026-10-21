# Nutzung

## Anmelden

Wählen Sie Anmelden und melden Sie sich mit dem Konto Ihrer Organisation an.
Die obere Leiste zeigt dann Ihren Namen und ein Menü für Einstellungen und Abmelden.

## Mit Ihren Dokumenten chatten

Geben Sie eine Frage ein und drücken Sie die Eingabetaste.
Die Antwort erscheint im Stream, und die genutzten Dokumente werden im Kontextbereich angezeigt.
Der Agent kann mehrere Schritte gehen, bevor er antwortet.
Nachrichten, die Sie währenddessen senden, werden als nächste Runde eingereiht, womit Sie eine lange Antwort lenken können.

### Gute und schwache Fragen

Stellen Sie konkrete Fragen zu bestimmten Themen.

- Gut: "Was sagt unsere Datenschutzrichtlinie über Cookies?"
- Schwach: "Zeig mir alles" (zu allgemein) oder "Info" (zu vage).

## Quellen und Kontext

Der Kontextbereich zeigt die Dokumente und Stellen hinter jeder Antwort.
Klicken Sie darauf, um genau zu sehen, woher die Information stammt.

Der Assistent durchsucht stets Ihre Dokumente und gibt wieder, was sie sagen, ohne auszuschmücken.
Deckt Ihr Material eine Frage nicht ab, sagt er das.

## Mit Dokumenten arbeiten

### Hochladen

- Ziehen Sie Dateien, Ordner oder ein ZIP-Archiv auf den Upload-Bereich, oder nutzen Sie den Dateidialog.
- Hivegent wandelt jede Datei in Text um, teilt sie in Abschnitte und indexiert sie.
- Die Aufgabenleiste in der oberen Leiste zeigt den Fortschritt.

Unterstützt werden unter anderem PDFs, Office-Dokumente, Webseiten, E-Books, Bilder sowie reiner Text und Markdown, je nach Bereitstellung.
Markdown-Dokumente können Sie auch direkt im Browser erstellen und bearbeiten.

### Arbeitsbereiche und Zugriff

- Ihre Dokumente ist Ihr privater Arbeitsbereich.
- Gruppen-Arbeitsbereiche werden mit einer Gruppe geteilt, mit Lese- oder Schreibzugriff je nach Mitgliedschaft.

### Dokumente verwalten

Sie können jedes Dokument ansehen, herunterladen, verschieben oder löschen.
Außerdem können Sie Dokumente in die aktuelle Unterhaltung einschließen oder daraus ausschließen.

## Unterhaltungen

Die Seitenleiste listet frühere Unterhaltungen auf.

- Titel werden automatisch erzeugt und lassen sich bearbeiten.
- Öffnen, löschen oder beginnen Sie Unterhaltungen jederzeit.
- Bearbeiten Sie eine frühere Nachricht oder erzeugen Sie eine Antwort neu, um eine andere Richtung auszuprobieren.
- Exportieren Sie eine Unterhaltung in eine Datei und importieren Sie sie später wieder.

Mit Komprimieren fassen Sie eine lange Unterhaltung zusammen, damit sie in den Kontext des Modells passt.
Erreicht das Modell sein Kontextlimit, wird die Unterhaltung komprimiert und die Antwort läuft von selbst weiter.
Eine Trennlinie markiert, wo die Zusammenfassung beginnt, und zeigt sie beim Öffnen an.

## Den Assistenten anpassen

- Persönlichkeit: Standard, Knapp, Ausführlich, Strukturiert oder eine eigene Systemnachricht.
- Denkaufwand: Niedrig, Mittel (Standard) oder Hoch, ein Abwägen von Tiefe gegen Geschwindigkeit und Kosten.
- Modus: Interaktiv fragt vor jeder Änderung im Arbeitsbereich, Schreiben ändert ohne Rückfrage, und Lesen erlaubt keine Änderungen.

## Gedächtnis

Der Assistent kann sich Fakten über Unterhaltungen hinweg merken.
Im Einstellungsdialog können Sie das gesamte Gedächtnis jederzeit löschen.

## Über die Weboberfläche hinaus

- MCP: wenn der Betreiber es aktiviert, verbinden sich MCP-Clients wie Editoren mit derselben Anmeldung über `/mcp`.
  Sie können Dokumente suchen, lesen, erkunden und ändern, wobei jede Änderung zuerst zur Bestätigung angezeigt wird.
- API und Kommandozeile: die REST-API und das Kommandozeilenwerkzeug `hivegent` können sich anmelden und Dokumente hochladen, auflisten, herunterladen oder löschen.
