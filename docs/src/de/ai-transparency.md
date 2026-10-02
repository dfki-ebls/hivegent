# KI-Transparenz

Hivegent kennzeichnet sich in der Chat-Oberfläche und in der ersten Antwort jeder Teams-Unterhaltung als KI-System.
Wenn die Wasserzeichenfunktion aktiviert ist, markiert Hivegent erzeugte Texte gemäß dem [Verhaltenskodex der Europäischen Kommission](https://ec.europa.eu/newsroom/dae/redirection/document/129555).
Texte mit weniger als 200 Tokens lassen sich nicht zuverlässig erkennen.
Exportierte Unterhaltungen enthalten eine signierte Angabe, dass sie KI-generierte Texte enthalten.

## Text prüfen

Wählen Sie **Verify AI text** unter dem Chat-Eingabefeld und fügen Sie den zu prüfenden Text ein.
Sie können einen signierten Bericht herunterladen, der den Hash des Textes, aber nicht den Text selbst enthält.
Diesen Bericht oder das Feld `provenance` eines Exports prüfen Sie mit dem unter `/api/transparency/jwks` veröffentlichten Schlüssel, der ohne Anmeldung abrufbar ist.
Der eingereichte Text wird weder gespeichert noch protokolliert.

Ein positives Ergebnis bedeutet, dass das Hivegent-Wasserzeichen erkannt wurde.
Ein negatives Ergebnis beweist weder menschliche Urheberschaft noch schließt es ein anderes KI-System aus.
Kurze oder bearbeitete Texte können zu keinem eindeutigen Ergebnis führen.

Für die Prüfung ist eine Anmeldung erforderlich.
Qualifizierte Fachleute können den Zugang über den im Prüfdialog genannten Betreiberkontakt anfragen.

## Zulässige Nutzung

Das Wasserzeichen darf nicht entfernt, gefälscht, verborgen oder mithilfe des Detektors umgangen werden.
Bei der Weitergabe KI-generierter Texte muss eine sichtbare Kennzeichnung erhalten bleiben, wenn ihre Herkunft relevant ist.
