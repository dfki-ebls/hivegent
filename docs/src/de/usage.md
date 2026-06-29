# Nutzung

## Anmelden

Öffnen Sie Hivegent im Browser und wählen Sie Anmelden.
Sie werden zur Anmeldeseite Ihrer Organisation geleitet und nach erfolgreicher Anmeldung zur App zurückgebracht.
Ihr Name und ein Menü für Einstellungen und Abmelden erscheinen in der oberen Leiste.

## Mit Ihren Dokumenten chatten

Der Chat-Arbeitsbereich funktioniert wie ein gewöhnliches Chat-Programm.

1. Geben Sie Ihre Frage in das Eingabefeld am unteren Rand ein.
2. Drücken Sie die Eingabetaste oder die Senden-Schaltfläche.
3. Die Antwort erscheint darunter im Stream, und die genutzten Dokumente werden im Kontextbereich angezeigt.

Im Hintergrund durchsucht der Agent Ihre Dokumente, liest die passenden Stellen und kann mehrere Schritte gehen, bevor er antwortet.
Sie können weiter tippen, während er antwortet. Zusätzliche Nachrichten werden gesammelt und als nächste Runde gesendet, womit Sie eine lange Antwort lenken können.

### Gute und schwache Fragen

Stellen Sie konkrete Fragen zu bestimmten Themen.

- Gut: "Was sagt unsere Datenschutzrichtlinie über Cookies?"
- Gut: "Welche Installationsschritte beschreibt das Handbuch?"
- Schwach: "Zeig mir alles" (zu allgemein)
- Schwach: "Info" (zu vage)

## Quellen und Kontext

Jede Antwort stützt sich auf Ihr Material, und der Kontextbereich zeigt die Dokumente und Stellen, die der Assistent herangezogen hat.
Klicken Sie auf ein Dokument oder eine Stelle, um es zu öffnen und genau zu sehen, woher die Information stammt.
So bleibt jede Antwort bis zu ihrer Quelle nachvollziehbar.

## Mit Dokumenten arbeiten

Im Dokumenten-Arbeitsbereich fügen Sie Ihr Material hinzu und ordnen es.

### Hochladen

- Ziehen Sie Dateien, ganze Ordner oder ein ZIP-Archiv auf den Upload-Bereich, oder wählen Sie sie über den Dateidialog aus.
- Hivegent wandelt jede Datei in durchsuchbaren Text um, teilt sie in Abschnitte und indexiert sie für den Abruf.
- Eine Aufgabenleiste in der oberen Leiste zeigt den Fortschritt von Uploads und Verarbeitung.

Hivegent verarbeitet viele gängige Formate, darunter PDFs, Office-Dokumente, Webseiten, E-Books, Bilder sowie reinen Text und Markdown.
Welche genau, hängt von Ihrer Bereitstellung ab.
Sie können Markdown-Dokumente auch direkt im Browser erstellen und bearbeiten.

### Arbeitsbereiche und Zugriff

Dokumente liegen in Bereichen, die im Dokumentenbereich angezeigt werden.

- Ihre Dokumente ist Ihr privater Arbeitsbereich, den nur Sie lesen und schreiben können.
- Gruppen-Arbeitsbereiche werden mit allen Mitgliedern einer Gruppe geteilt, mit Lese- oder Schreibzugriff je nach Mitgliedschaft.

### Dokumente verwalten

Zu jedem Dokument können Sie es ansehen, das Original herunterladen, verschieben oder löschen.
Sie können einzelne Dokumente auch in die aktuelle Unterhaltung einschließen oder daraus ausschließen, sodass der Assistent für eine Frage nur das gewünschte Material nutzt.

## Unterhaltungen

Frühere Unterhaltungen werden in der Seitenleiste mit Titel und Zeitstempel aufgeführt.

- Titel werden automatisch erzeugt und lassen sich direkt bearbeiten.
- Wählen Sie eine Unterhaltung aus, um zu ihr zurückzukehren, oder löschen Sie eine nicht mehr benötigte.
- Beginnen Sie jederzeit eine neue Unterhaltung über die entsprechende Schaltfläche.
- Bearbeiten Sie eine Ihrer früheren Nachrichten oder lassen Sie eine Antwort neu erzeugen, um eine andere Richtung auszuprobieren.
- Exportieren Sie eine Unterhaltung in eine Datei und importieren Sie sie später wieder.

Wird eine Unterhaltung sehr lang, verdichten Sie mit Komprimieren den früheren Verlauf, damit er weiter in das Kontextfenster des Modells passt.

## Den Assistenten anpassen

Zwei Bedienelemente steuern, wie der Assistent antwortet.

- Persönlichkeit: wählen Sie Standard, Knapp, Ausführlich oder Strukturiert, oder geben Sie eine eigene Systemnachricht an.
- Denkaufwand: von Automatisch über Keiner bis zu höheren Stufen, ein Abwägen von Tiefe gegen Geschwindigkeit und Kosten.

## Gedächtnis

Der Assistent kann sich nützliche Fakten über einzelne Unterhaltungen hinweg merken.
Sie können das gesamte gespeicherte Gedächtnis jederzeit im Einstellungsdialog löschen.

## Über die Weboberfläche hinaus

Fortgeschrittene Nutzer können dasselbe Backend ohne Browser erreichen.

- MCP: wenn der Betreiber den MCP-Endpunkt aktiviert, können MCP-fähige Clients wie Code-Editoren oder andere Agenten einen Teil der Dokumentwerkzeuge von Hivegent nutzen. Sie verbinden sich mit der `/mcp`-URL und melden sich über dieselbe OIDC-Anmeldung an.
- API und Kommandozeile: das Backend stellt eine REST-API bereit, und das mitgelieferte Kommandozeilenwerkzeug `hivegent` kann sich anmelden und Dokumente hochladen, auflisten, herunterladen oder löschen, etwa für Skripte.
