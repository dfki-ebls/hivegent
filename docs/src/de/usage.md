# Nutzung

## Anmelden

Wählen Sie Anmelden und melden Sie sich mit dem Konto Ihrer Organisation an.
Die obere Leiste zeigt dann Ihren Namen und ein Menü für Einstellungen und Abmelden.

## Mit Ihren Dokumenten chatten

Geben Sie eine Frage ein und drücken Sie die Eingabetaste.
Ein neuer Chat bietet drei Einstiege, mit denen der Assistent zuerst Ihre Dokumente oder letzten Konversationen durchsieht und Sie dann fragt, wie es weitergehen soll.
Die Antwort erscheint im Stream, und die genutzten Dokumente werden im Kontextbereich angezeigt.
Der Agent kann mehrere Schritte gehen, bevor er antwortet.
Nachrichten, die Sie währenddessen senden, werden als nächste Runde eingereiht, womit Sie eine lange Antwort lenken können.

### Gute und schwache Fragen

Stellen Sie konkrete Fragen zu bestimmten Themen.

- Gut: "Was sagt unsere Datenschutzrichtlinie über Cookies?"
- Schwach: "Zeig mir alles" (zu allgemein) oder "Info" (zu vage).

## Quellen und Kontext

Der Kontextbereich zeigt die Dokumente und Belege hinter jeder Antwort.
Klicken Sie darauf, um genau zu sehen, woher die Information stammt.

Der Assistent durchsucht stets Ihre Dokumente und gibt wieder, was sie sagen, ohne auszuschmücken.
Deckt Ihr Material eine Frage nicht ab, sagt er das.

## Mit Dokumenten arbeiten

### Hochladen

- Ziehen Sie Dateien, Ordner oder ein ZIP-Archiv auf den Upload-Bereich oder auf einen Ordner im Baum, oder nutzen Sie den Dateidialog.
- Uploads und neue Dokumente landen in dem Ordner, den Sie zuletzt im Baum ausgewählt haben.
- Hivegent wandelt jede Datei in Text um, teilt sie in Abschnitte und indexiert sie.
- Die Aufgabenleiste in der oberen Leiste zeigt den Fortschritt.

Unterstützt werden unter anderem PDFs, Office-Dokumente, Webseiten, E-Books, Bilder sowie reiner Text und Markdown, je nach Bereitstellung.
Markdown-Dokumente können Sie auch direkt im Browser erstellen und bearbeiten.

### Arbeitsbereiche und Zugriff

- Ihre Dokumente ist Ihr privater Arbeitsbereich.
- Gruppen-Arbeitsbereiche werden mit einer Gruppe geteilt, mit Lese- oder Schreibzugriff je nach Mitgliedschaft.
- Organisation ist der Arbeitsbereich, den alle Benutzer teilen, den alle lesen und Administratoren bearbeiten.

### Dokumente verwalten

Sie können jedes Dokument ansehen, herunterladen, verschieben, umbenennen oder löschen.
Alle Arbeitsbereiche teilen sich einen Baum.

- Klicken Sie auf einen Ordner, um ihn zu öffnen oder zu schließen, und auf ein Dokument, um es anzusehen. Setzen Sie das Häkchen oder halten Sie beim Klicken Strg (Cmd unter macOS) oder Umschalt gedrückt, um Einträge auszuwählen.
- Ziehen Sie die Auswahl auf einen Ordner, um sie dorthin zu verschieben, auch in einen anderen Arbeitsbereich.
- Drücken Sie F2 oder klicken Sie auf den Stift, um einen Eintrag direkt umzubenennen.
- Navigieren Sie mit den Pfeiltasten, und verschieben Sie die Auswahl mit Strg+Umschalt+D per Tastatur.

Ein Dokument, dessen Original der Browser anzeigen kann, etwa ein PDF, ein Word-Dokument oder eine Tabelle, hat neben dem Tab Text mit dem umgewandelten Text einen Tab Vorschau mit dem Original.
Ein Bild oder ein Video erscheint stattdessen über seiner Beschreibung.
Eine Passage, die der Assistent gelesen hat, öffnet sich im Tab Text, im Dokument hervorgehoben.
Außerdem können Sie Dokumente in die aktuelle Unterhaltung einschließen oder daraus ausschließen.

### Ähnliche Fälle

Eine Tabelle, eine JSON-Datei, ein Ordner mit JSON-Dateien oder BPMN-Prozessmodelle bilden eine Fallbasis: Bitten Sie den Assistenten um die Datensätze oder Prozesse, die einem Beispiel am ähnlichsten sind, und er ordnet sie und erklärt jeden Treffer, Attribut für Attribut oder bei Prozessen Knoten für Knoten.
Der Tab Ähnlichkeit eines solchen Dokuments zeigt, wie jedes Attribut verglichen wird, wer das entschieden hat (aus den Daten abgeleitet, die KI oder Sie) und warum.
Ändern Sie dort ein Maß oder ein Gewicht und speichern Sie es, probieren Sie die Konfiguration mit einer Testanfrage aus, oder lassen Sie die KI die Entscheidungen verfeinern, die niemand getroffen hat, anhand von Statistiken und einigen Werten jedes Attributs.
Bis Sie sie speichern, wird die Konfiguration aus den Daten abgeleitet und nichts geschrieben, und Auf abgeleitet zurücksetzen löscht eine gespeicherte.
Die Konfiguration liegt neben den Daten, wird mit ihnen verschoben und gelöscht und gilt für alle, die die Daten lesen.
Datensätze werden nach ihrer Zeile geschlüsselt, außer Sie wählen unter Schlüssel eine Schlüsselspalte, deren Werte vorhanden und eindeutig sein müssen, und dann erhalten neue Fälle den nächsten Schlüssel darin.
Der Dokumentbaum markiert die Dateien und Ordner mit gespeicherter Konfiguration, und die Ähnlichkeit jedes Ordners mit JSON- oder BPMN-Dateien, auch in Unterordnern, öffnet sich über seine Zeile.
Der Assistent kann außerdem akzeptierte Lösungen in einer Fallbasis speichern und sie bereinigen, soweit die Abschnitte `retain` und `maintain` der Konfiguration es erlauben, die Sie im Tab Ähnlichkeit einschalten und anpassen, und nennt den Grund für jeden Fall, den er hinzufügt, ändert oder entfernt.
Im Modus Interaktiv bittet er um Ihre Freigabe, bevor sich eine Datei ändert, im Modus Schreiben ändert er die Fallbasis direkt, und im Modus Lesen kann er Ihre Arbeitsbereiche nicht ändern.

## Unterhaltungen

Die Seitenleiste listet frühere Unterhaltungen auf.

- Titel werden automatisch erzeugt und lassen sich bearbeiten.
- Öffnen, löschen oder beginnen Sie Unterhaltungen jederzeit.
- Bearbeiten Sie eine frühere Nachricht oder erzeugen Sie eine Antwort neu, um eine andere Richtung auszuprobieren.
- Exportieren Sie eine Unterhaltung in eine Datei und importieren Sie sie später wieder.
- Übertragen Sie eine Antwort als Goldstandard-Eintrag an RAGold, sofern Ihre Installation das anbietet.
  Der Dialog beginnt mit der Frage, der Antwort und den zitierten Zeilen, die Sie bearbeiten, als Passagen bewerten und um Dokumente und weitere Passagen ergänzen können, auch mehrere aus einem Dokument nacheinander.
  Nichts ist vorausgewählt, Sie wählen also Fragetyp, Beantwortbarkeit und jede Bewertung selbst und markieren Kernaussagen, indem Sie Wörter in der Antwort auswählen.
  Sobald alles ausgefüllt ist, öffnet sich RAGold in einem neuen Tab mit dem geladenen Eintrag.
  Erlauben Sie Pop-ups für Hivegent, falls der Browser diesen Tab blockiert.

Mit Komprimieren fassen Sie eine lange Unterhaltung zusammen, damit sie in den Kontext des Modells passt.
Erreicht das Modell sein Kontextlimit, wird die Unterhaltung komprimiert und die Antwort läuft von selbst weiter.
Eine Trennlinie markiert, wo die Zusammenfassung beginnt, und zeigt sie beim Öffnen an.

## Den Assistenten anpassen

- Modell: Bietet der Betreiber mehrere an, wählen Sie eines im Eingabefeld.
  Bildanhänge und der Denkaufwand erscheinen nur für ein Modell, das sie unterstützt.
- Denkaufwand: Niedrig, Mittel (Standard) oder Hoch, ein Abwägen von Tiefe gegen Geschwindigkeit und Kosten.
- Modus: Interaktiv fragt vor jeder Änderung im Arbeitsbereich, Schreiben ändert jeden Arbeitsbereich, den Sie beschreiben dürfen, ohne Rückfrage, und Lesen erlaubt keine Änderungen.
  Änderungen an Skills und am Gedächtnis fragen immer nach, in jedem Modus, da sie spätere Konversationen prägen.
- Anweisungen: Ihre eigenen dauerhaften Anweisungen für jede Unterhaltung, etwa ein Ton oder eine Hausregel, unter Einstellungen, Anweisungen.
  Sie erreichen den Assistenten als Ihre eigenen Worte und können seine Regeln nicht außer Kraft setzen.

## Gedächtnis

Der Assistent kann sich Fakten über Unterhaltungen hinweg merken.
Vor jeder Änderung seines Gedächtnisses fragt er nach, in jedem Modus, da alles dort Gespeicherte jede spätere Unterhaltung erreicht.
Unter Einstellungen, Account können Sie das gesamte Gedächtnis jederzeit löschen.

## Skills

Skills bringen dem Assistenten eine Aufgabe bei, etwa einen Hausstil oder eine Checkliste, als Ordner mit einer `SKILL.md` im Format [Agent Skills](https://agentskills.io).
Der Assistent sieht Namen und Beschreibung jedes Skills, den Sie nutzen können, und lädt einen, wenn eine Aufgabe dazu passt.
Beginnen Sie eine Nachricht mit `/<name>`, damit der Assistent diesen Skill zuerst lädt, das Eingabefeld schlägt beim Tippen passende Skills vor.
Skills verschiedener Bereiche dürfen denselben Namen tragen, ein solcher Skill wird deshalb über seinen Verweis aufgerufen, etwa `/~/<name>`, `/@<gruppe>/<name>` oder `/srv/<name>`, den die Vorschläge einfügen.
Der Text nach dem Befehl ersetzt jedes `$ARGUMENTS` in den Anweisungen des Skills, sodass `/review src/app.py` einem Skill, der `$ARGUMENTS` prüft, `src/app.py` übergibt.
Das Frontmatter eines Skills kann `disable-model-invocation: true` enthalten, sodass nur Sie ihn laden, nie der Assistent von sich aus, oder `user-invocable: false`, sodass er nicht als Befehl angeboten wird und der Assistent ihn lädt, wenn eine Aufgabe passt.
Jeder Wert im Frontmatter wird so gelesen, wie er dasteht, sodass `version: 1.0` Text bleibt, und ein doppelter Schlüssel macht den Skill ungültig.
Einstellungen, Skills listet die Skills der Organisation, Ihrer Gruppen und Ihre eigenen, öffnet die `SKILL.md` jedes Skills und zeigt, warum ein Skill nicht nutzbar ist.
Laden Sie einen Skill-Ordner oder sein ZIP-Archiv zu Ihren eigenen Skills oder zu einer Gruppe hoch, in die Sie schreiben dürfen, wo er einen gleichnamigen Skill ersetzt, und löschen Sie ihn dort.
Der Assistent kann auf Ihren Wunsch auch selbst einen Skill schreiben und fragt vor jeder Änderung nach, in jedem Modus.
Skripte eines Skills laufen in der Python-Sandbox, wenn sie eine Funktion `main` definieren, andere bleiben zum Lesen.
Ein geladener Skill bleibt geladen, wenn eine lange Unterhaltung zusammengefasst wird.

## Über die Weboberfläche hinaus

- MCP: wenn der Betreiber es aktiviert, verbinden sich MCP-Clients wie Editoren mit derselben Anmeldung über `/mcp`.
  Sie können Dokumente suchen, lesen, erkunden und ändern, wobei jede Änderung zuerst zur Bestätigung angezeigt wird.
- API: Skripte laden Dokumente über die REST-API oder den MCP-Endpunkt hoch, listen sie auf, laden sie herunter oder löschen sie, wie [Skripte](setup.md#skripte) zeigt.
  Das Kommandozeilenwerkzeug `hivegent` meldet sich nicht mehr an und verwaltet keine Dokumente, es administriert nur den Server auf seinem Host.
