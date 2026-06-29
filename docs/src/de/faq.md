# FAQ

## Allgemein

### Worin liegt der Unterschied zu ChatGPT?

Ein einfacher Chatbot antwortet nur aus dem, was sein Modell beim Training gelernt hat.
Hivegent durchsucht zuerst Ihre eigenen Dokumente und früheren Unterhaltungen, antwortet dann daraus und gibt die Quellen an.
Es kann zudem mehrere Schritte gehen und Werkzeuge nutzen, eine Frage also erkunden, statt in einem Zug zu antworten.

### Wo liegen meine Dokumente und Daten?

Sie hosten Hivegent selbst.
Die Dokumentdateien bleiben auf der Festplatte Ihres Servers, und ihr Index, Ihre Unterhaltungen und das Gedächtnis des Assistenten liegen in Ihrer eigenen PostgreSQL-Datenbank.
Die einzigen Daten, die Ihre Infrastruktur verlassen, sind die, die Sie an den von Ihnen konfigurierten Sprachmodell-Endpunkt senden.

### Welche Sprachen werden unterstützt?

Das hängt davon ab, wie Ihre Bereitstellung konfiguriert ist.
Der Assistent antwortet in den Sprachen, die das konfigurierte Sprachmodell beherrscht, und Dokumentsuche und OCR decken die von Ihrem Betreiber eingestellten Sprachen ab.
Gemischtsprachige Dokumente sind kein Problem.

### Welche Dateiformate kann ich hochladen?

Viele gängige Formate, darunter PDFs, Office-Dokumente, Webseiten, E-Books, Bilder sowie reinen Text und Markdown.
Welche genau, hängt von Ihrer Bereitstellung ab, und gescannte PDFs werden per OCR verarbeitet.

## Den Assistenten nutzen

### Warum werden nicht alle meine Dokumente angezeigt?

Der Assistent ruft nur die Stellen ab, die für Ihre Frage relevant sind.
Eine allgemeine Anfrage wie "Liste alles auf" kann keine sinnvollen Ergebnisse liefern.
Stellen Sie stattdessen eine konkrete Frage zu einem bestimmten Thema.

### Der Assistent findet ein hochgeladenes Dokument nicht.

- Es wird vielleicht noch verarbeitet. Beobachten Sie die Aufgabenleiste in der oberen Leiste und warten Sie, bis sie fertig ist.
- Prüfen Sie, ob das Dokument im richtigen Arbeitsbereich liegt und nicht aus der aktuellen Unterhaltung ausgeschlossen ist.
- Stellen Sie sicher, dass das Format unterstützt wird und die Datei nicht leer oder passwortgeschützt ist.

### Die Antworten sind ungenau.

- Stellen Sie präzisere Fragen und verwenden Sie Formulierungen, die in Ihren Dokumenten vorkommen.
- Sorgen Sie dafür, dass die richtigen Dokumente vorhanden und in der Unterhaltung eingeschlossen sind.
- Gut strukturierte Dokumente mit klaren Überschriften liefern bessere Ergebnisse.
- Für eine gründlichere Antwort erhöhen Sie den Denkaufwand oder entfernen Sie veraltete Dokumente.

### Kann der Assistent das Web nutzen?

Nur wenn der Betreiber die Web-Werkzeuge aktiviert.
Standardmäßig antwortet der Assistent allein aus Ihren indexierten Dokumenten.

## Zugriff und Konten

### Wie erhalte ich Zugriff auf geteilte Dokumente?

Gruppen-Arbeitsbereiche werden von Ihrem Identitätsanbieter gesteuert.
Ein Administrator nimmt Sie in eine Gruppe auf, und Sie erhalten dann Lese- oder Schreibzugriff auf den geteilten Arbeitsbereich dieser Gruppe.

### Wer kann administrative Aktionen ausführen?

Nur Benutzer, deren Anmeldung die Rolle `admin` trägt.
Administratoren können den Wartungsmodus aktivieren, den Arbeitsbereich neu indexieren und Daten zurücksetzen.

## Betrieb

### Wo konfiguriere ich das System?

In der Datei `config.toml` und über `HIVEGENT_*`-Umgebungsvariablen.
Die gängigen Einstellungen finden Sie unter [Einrichtung](setup.md).

### Wie aktualisiere ich?

Laden Sie das neue Image und erstellen Sie die Container neu.
Das Backend wendet etwaige Datenbankmigrationen beim Start automatisch an.

### Hohe CPU- oder Speicherauslastung.

Das ist normal während der Dokumentverarbeitung, besonders bei OCR auf gescannten PDFs, und beim Betrieb eines lokalen Sprachmodells.
Um die Last zu senken, verarbeiten Sie weniger Dokumente gleichzeitig, nutzen Sie eine GPU oder verweisen Sie auf einen gehosteten Modell-Endpunkt.

### Wann lohnen sich lokale Modelle?

Es ist eine Abwägung zwischen Kosten, Infrastruktur, Datenschutz und Modellqualität, da die stärksten kommerziellen Modelle nur über ihre gehosteten APIs verfügbar sind.
Lokale Modelle halten alle Daten auf Ihrer Infrastruktur, benötigen aber leistungsfähige Hardware.
