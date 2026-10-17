# FAQ

## Allgemein

### Worin liegt der Unterschied zu ChatGPT?

Ein einfacher Chatbot antwortet aus seinen Trainingsdaten.
Hivegent durchsucht zuerst Ihre Dokumente, antwortet daraus in mehreren Schritten und gibt die Quellen an.

### Wo liegen meine Dokumente und Daten?

Auf Ihrem eigenen Server.
Die Dateien liegen auf der Festplatte, Index, Unterhaltungen und Gedächtnis in Ihrer PostgreSQL-Datenbank.
Nur was Sie an das konfigurierte Sprachmodell senden, verlässt Ihre Infrastruktur.

### Welche Sprachen werden unterstützt?

Alle, die Ihr Sprachmodell beherrscht.
Suche und OCR decken die von Ihrem Betreiber eingestellten Sprachen ab, und gemischtsprachige Dokumente funktionieren.

### Welche Dateiformate kann ich hochladen?

PDFs, Office-Dokumente, Webseiten, E-Books, Bilder sowie reinen Text und Markdown, je nach Bereitstellung.
Gescannte PDFs werden per OCR verarbeitet.

## Den Assistenten nutzen

### Warum werden nicht alle meine Dokumente angezeigt?

Er ruft nur die für Ihre Frage relevanten Stellen ab, daher liefert "Liste alles auf" schlechte Ergebnisse.
Stellen Sie stattdessen eine konkrete Frage.

### Der Assistent findet ein hochgeladenes Dokument nicht.

- Warten Sie, bis die Aufgabenleiste die Verarbeitung als abgeschlossen anzeigt.
- Prüfen Sie den Arbeitsbereich und ob das Dokument aus der Unterhaltung ausgeschlossen ist.
- Prüfen Sie, ob das Format unterstützt wird und die Datei nicht leer oder passwortgeschützt ist.

### Die Antworten sind ungenau.

- Stellen Sie präzise Fragen mit Formulierungen aus Ihren Dokumenten.
- Sorgen Sie dafür, dass die richtigen Dokumente vorhanden und eingeschlossen sind.
- Nutzen Sie gut strukturierte Dokumente mit klaren Überschriften.
- Erhöhen Sie den Denkaufwand oder entfernen Sie veraltete Dokumente.

### Kann der Assistent das Web nutzen?

Nur wenn der Betreiber die Web-Werkzeuge aktiviert.

## Zugriff und Konten

### Wie erhalte ich Zugriff auf geteilte Dokumente?

Ein Administrator nimmt Sie in Ihrem Identitätsanbieter in eine Gruppe auf, die Zugriff auf deren Arbeitsbereich gewährt.

### Wer kann administrative Aktionen ausführen?

Benutzer mit der Rolle `admin`.
Sie können den Wartungsmodus aktivieren, den Arbeitsbereich neu indexieren und Daten zurücksetzen.

## Betrieb

### Wo konfiguriere ich das System?

In `config.toml` und über `HIVEGENT_*`-Umgebungsvariablen, siehe [Einrichtung](setup.md).

### Wie aktualisiere ich?

Laden Sie das neue Image und erstellen Sie die Container neu.
Migrationen laufen beim Start automatisch.

### Hohe CPU- oder Speicherauslastung.

Das ist normal bei der Dokumentverarbeitung, besonders bei OCR, und mit einem lokalen Modell.
Verarbeiten Sie weniger Dokumente gleichzeitig, nutzen Sie eine GPU oder ein gehostetes Modell.

### Wann lohnen sich lokale Modelle?

Lokale Modelle halten alle Daten auf Ihrer Infrastruktur, brauchen aber leistungsfähige Hardware.
Die stärksten Modelle gibt es nur über gehostete APIs.
