# telegram-dice-bot
telegram-dice-bot

## Kreaturen und Zauber aus dem SRD 5.1

Nach jedem Encounter (`/rollencounter` und Encounter/Kreaturenhort aus `/rollwaldkarte`) schickt der Bot
für jede erwürfelte Kreatur einen Wertekasten in den Chat und danach die Beschreibungen ihrer Zauber.

- Gleiche Kreaturen kommen nur einmal, mit Anzahl im Titel, z.B. `Kobold (5×)`.
- Kreaturen, die nicht im SRD stehen, bekommen den nächstliegenden SRD-Wertekasten
  (z.B. Halboger → Oger, Orog → Ork). Das steht dann als `Ersatz für: …` unter dem Titel.
- Einträge ohne Kreatur (Wasserfall, Spuren, Wetter …) bleiben reiner Text.

## Magische Gegenstände und Zauberschriftrollen

Nach jedem Schatzhort (`/rollschatz`) kommt für jeden magischen Gegenstand die Beschreibung aus dem SRD.

- Zauberschriftrollen bekommen einen zufälligen SRD-Zauber des passenden Grades
  (z.B. `Zauberschriftrolle (Zaubergrad 3): Feuerball`), mit Rettungswurf-SG, Angriffsbonus und voller Zauberbeschreibung.
- Namen aus dem Spielleiterhandbuch werden dem SRD-Namen zugeordnet (z.B. Hewards praktischer Rucksack → Praktischer Rucksack).
- Gegenstände, die nicht im SRD stehen, werden am Ende aufgelistet.

Zum Nachschlagen: `/kreatur Skelett`, `/zauber Feuerball`, `/gegenstand Nimmervoller Beutel`.

Dateien:

- `kreaturen.py` – Erkennung, Formatierung, Aufteilen langer Nachrichten
- `srd_monster_de.json` – 317 Wertekästen aus dem SRD 5.1 (DE)
- `srd_zauber_de.json` – 318 Zauber aus dem SRD 5.1 (DE)
- `gegenstaende.py` – Zuordnung der Tabellen-Gegenstände, Schriftrollen-Zufallsgenerator
- `srd_gegenstaende_de.json` – 240 magische Gegenstände aus dem SRD 5.1 (DE)
- `kreaturen_zuordnung.json` – welche Encounter-Begriffe auf welche SRD-Kreatur zeigen.
  Hier lassen sich Zuordnungen ändern, ohne Code anzufassen:
  - `formen`: Schreibweisen einer SRD-Kreatur (Plural, Tippfehler)
  - `ersatz`: Nicht-SRD-Kreatur → nächstliegende SRD-Kreatur
  - `eintraege`: Sonderfälle für ganze Encounter-Texte (`"anzahl": "#2"` = zweite Zahl im ausgewürfelten Text)

Enthält Inhalte aus dem System Reference Document 5.1 von Wizards of the Coast LLC, lizenziert unter CC BY 4.0.
