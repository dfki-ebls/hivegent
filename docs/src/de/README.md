# Einleitung

<div align="center">
  <img src="assets/logo.svg" alt="Hivegent Logo" width="256">
</div>

Hivegent ist ein selbst gehosteter Assistent, der Fragen zu Ihren eigenen Dokumenten beantwortet.
Er verbindet eine vertraute Chat-Oberfläche mit einem Agenten, der die hochgeladenen Dateien durchsuchen, lesen und durchdenken kann.

Anders als ein einfacher Chatbot verlässt sich Hivegent nicht nur auf das, was ein Sprachmodell beim Training gelernt hat.
Vor einer Antwort arbeitet sich der Agent durch Ihre Dokumente und früheren Unterhaltungen, entscheidet, welche Werkzeuge er nutzt, und gibt die verwendeten Quellen an.
Genau das bedeuten "abrufgestützt" und "agentisch" in der Praxis: Antworten stützen sich auf Ihr Material, und der Assistent kann mehrere Schritte gehen, um sie zu finden.

## Was Sie tun können

- In natürlicher Sprache mit Ihren Dokumenten chatten und Antworten mit Quellenangaben erhalten.
- PDFs, Office-Dateien, Bilder und mehr hochladen, die Hivegent für Sie umwandelt und indexiert.
- Dokumente in einem privaten Arbeitsbereich und in geteilten Gruppen-Arbeitsbereichen mit Zugriffssteuerung verwalten.
- Den Agenten mehrstufige Aufgaben bearbeiten, optional im Web suchen und Kontext über Unterhaltungen hinweg merken lassen.
- Externe Werkzeuge oder Ihren eigenen Editor über das Model Context Protocol (MCP) anbinden.

## Unterschied zu einem einfachen Chatbot

- Aktuell: Antworten stützen sich auf Ihre neuesten Dokumente, nicht nur auf Trainingswissen.
- Belegt: Jede Antwort kann auf die genaue Textstelle verweisen, aus der sie stammt.
- Agentisch: Der Assistent wählt Werkzeuge und sucht in mehreren Schritten, statt in einem Zug zu antworten.
- Privat: Sie betreiben das System selbst, Ihre Dokumente bleiben auf Ihrer eigenen Infrastruktur.

Lesen Sie [Funktionsweise](concepts.md) für die Ideen dahinter, [Architektur](architecture.md), um zu sehen, wie die Teile zusammenpassen, [Einrichtung](setup.md) zur Installation und [Nutzung](usage.md) für den Einstieg.
