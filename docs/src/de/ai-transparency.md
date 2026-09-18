# KI-Transparenz

Hivegent kennzeichnet sich in der Chat-Oberfläche als KI-System.
Wenn die Wasserzeichenfunktion aktiviert ist, markiert Hivegent erzeugte Texte mit mehr als 200 Tokens gemäß dem [Verhaltenskodex der Europäischen Kommission](https://ec.europa.eu/newsroom/dae/redirection/document/129555).
Kürzere Texte werden nicht zuverlässig erfasst.

## Text prüfen

Wählen Sie **Verify AI text** unter dem Chat-Eingabefeld und fügen Sie den zu prüfenden Text ein.
Sie können einen signierten Bericht herunterladen, der den Hash des Textes, aber nicht den Text selbst enthält, und ihn mit dem unter `/api/transparency/jwks` veröffentlichten Schlüssel prüfen, wofür dieselbe Anmeldung wie für die Prüfung nötig ist.
Der eingereichte Text wird weder gespeichert noch protokolliert.

Ein positives Ergebnis bedeutet, dass das Hivegent-Wasserzeichen erkannt wurde.
Ein negatives Ergebnis beweist weder menschliche Urheberschaft noch schließt es ein anderes KI-System aus.
Kurze oder bearbeitete Texte können zu keinem eindeutigen Ergebnis führen.

Eine Anmeldung ist erforderlich.
Qualifizierte Fachleute können den Zugang über den im Prüfdialog genannten Betreiberkontakt anfragen.

## Zulässige Nutzung

Das Wasserzeichen darf nicht entfernt, gefälscht, verborgen oder mithilfe des Detektors umgangen werden.
Bei der Weitergabe KI-generierter Texte muss eine sichtbare Kennzeichnung erhalten bleiben, wenn ihre Herkunft relevant ist.
