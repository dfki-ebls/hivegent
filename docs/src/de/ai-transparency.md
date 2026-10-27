# KI-Transparenz

Hivegent kennzeichnet sich in der Chat-Oberfläche und in der ersten Antwort jeder Teams-Unterhaltung als KI-System.
Wenn die Wasserzeichenfunktion aktiviert ist, markiert Hivegent erzeugte Texte gemäß dem [Verhaltenskodex der Europäischen Kommission](https://ec.europa.eu/newsroom/dae/redirection/document/129555).
Texte mit weniger als 200 Tokens lassen sich nicht zuverlässig erkennen.
Exportierte Unterhaltungen enthalten eine signierte Angabe, dass sie KI-generierte Texte enthalten.

## Text prüfen

Wählen Sie **KI-Text prüfen** im Benutzermenü und fügen Sie den Text ein.
Sie können einen signierten Bericht mit dem Hash des Textes herunterladen, aber nicht mit dem Text selbst.
Den Bericht oder das Feld `provenance` eines Exports prüfen Sie mit dem öffentlichen Schlüssel unter `/api/transparency/jwks`.
Der eingereichte Text wird weder gespeichert noch protokolliert.

Ein positives Ergebnis bedeutet, dass das Hivegent-Wasserzeichen erkannt wurde.
Ein negatives Ergebnis beweist keine menschliche Urheberschaft, und kurze oder bearbeitete Texte können uneindeutig bleiben.

Für die Prüfung ist eine Anmeldung erforderlich.
Qualifizierte Fachleute können den Zugang über den im Prüfdialog genannten Betreiberkontakt anfragen.

## Zulässige Nutzung

Das Wasserzeichen darf nicht entfernt, gefälscht, verborgen oder umgangen werden.
Bei der Weitergabe KI-generierter Texte muss eine sichtbare Kennzeichnung erhalten bleiben, wenn ihre Herkunft relevant ist.
