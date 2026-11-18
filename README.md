# telegram-dice-bot
telegram-dice-bot

## Kreaturen und Zauber aus dem SRD 5.1

Nach jedem Encounter (`/rollencounter` und Encounter/Kreaturenhort aus `/rollwaldkarte`) schickt der Bot
für jede erwürfelte Kreatur einen Wertekasten in den Chat und danach die Beschreibungen ihrer Zauber.

- Gleiche Kreaturen kommen nur einmal, mit Anzahl im Titel, z.B. `Kobold (5×)`.
- Kreaturen, die nicht im SRD stehen, bekommen den nächstliegenden SRD-Wertekasten
  (z.B. Halboger → Oger, Orog → Ork). Das steht dann als `Ersatz für: …` unter dem Titel.
- Einträge ohne Kreatur (Wasserfall, Spuren, Wetter …) bleiben reiner Text.

Zum Nachschlagen ohne Encounter: `/kreatur Skelett`, `/zauber Feuerball`.

Dateien:

- `kreaturen.py` – Erkennung, Formatierung, Aufteilen langer Nachrichten
- `srd_monster_de.json` – 317 Wertekästen aus dem SRD 5.1 (DE)
- `srd_zauber_de.json` – 318 Zauber aus dem SRD 5.1 (DE)
- `kreaturen_zuordnung.json` – welche Encounter-Begriffe auf welche SRD-Kreatur zeigen.
  Hier lassen sich Zuordnungen ändern, ohne Code anzufassen:
  - `formen`: Schreibweisen einer SRD-Kreatur (Plural, Tippfehler)
  - `ersatz`: Nicht-SRD-Kreatur → nächstliegende SRD-Kreatur
  - `eintraege`: Sonderfälle für ganze Encounter-Texte (`"anzahl": "#2"` = zweite Zahl im ausgewürfelten Text)
