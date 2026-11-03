# nightcut – Deutsche Anleitung

**Abends zocken. Morgens liegt der Rohschnitt fertig in DaVinci Resolve oder Premiere.**

nightcut ist eine lokale KI-Pipeline für Gaming-Creator. Sie nimmt eine lange
OBS-Aufnahme, findet die besten Momente und baut daraus eine Timeline, die du in
**DaVinci Resolve** oder **Premiere Pro** importierst. Du prüfst, passt an und renderst.
Alles läuft auf deiner eigenen Grafikkarte, nichts wird hochgeladen.

## Was du brauchst

- NVIDIA-Grafikkarte mit **16 GB VRAM** (z.B. RTX 5080, 5070 Ti, 5060 Ti 16 GB)
- [ffmpeg](https://ffmpeg.org/) (muss im PATH liegen)
- [Ollama](https://ollama.com/)
- [uv](https://docs.astral.sh/uv/) – installiert Python und alle Pakete automatisch
- Windows oder Linux

## Installation mit einem Klick (Windows)

1. [install.bat herunterladen](https://github.com/Nilson89/nightcut/releases/latest/download/install.bat)
   (oder im Repository auf `install.bat` → *Download raw file*)
2. Doppelklick. Falls Windows warnt: *Weitere Informationen → Trotzdem ausführen*.

Das Skript installiert Git, ffmpeg, Ollama und uv (per winget), holt nightcut auf den Desktop,
richtet die Python-Umgebung ein und lädt die beiden KI-Modelle. Du brauchst etwa 25 GB freien
Speicher. Wird dein Desktop mit OneDrive synchronisiert, landet nightcut in `C:\nightcut`, und
auf dem Desktop liegt eine Verknüpfung. Für ein Update startest du `install.bat` einfach noch einmal.

Den NVIDIA-Treiber installiert das Skript nicht, der muss schon aktuell sein.

## Installation von Hand

```bash
# 1. Repository holen
git clone https://github.com/Nilson89/nightcut.git
cd nightcut

# 2. Python-Umgebung installieren (inkl. PyTorch mit CUDA)
uv sync

# 3. KI-Modelle für Ollama laden
ollama pull qwen2.5vl:7b
ollama pull qwen3:14b

# 4. Erste Aufnahme verarbeiten
uv run nightcut run "D:/Aufnahmen/session.mkv" --profile bf6
```

Das Whisper-Modell für die Transkription wird beim ersten Lauf automatisch geladen.

**Windows, nightcut auf einem Netzlaufwerk (NAS):** `uv run` und `nightcut.exe` lösen ein verbundenes
Laufwerk in den Pfad `\\server\freigabe` auf, und Windows kann dann die DLLs von NumPy/PyTorch nicht
laden („DLL load failed … Falscher Parameter“). Leg das Repository auf eine lokale Platte (die Aufnahmen
dürfen auf dem NAS bleiben) oder starte es mit dem Python der venv über den Laufwerksbuchstaben:
`Z:\...\nightcut\.venv\Scripts\python.exe -c "from nightcut.cli import app; app()" run ...`

## OBS richtig einstellen

Am besten funktioniert nightcut mit **getrennten Audiospuren**. In OBS unter
*Einstellungen → Ausgabe → Aufnahme → Audiospur* mehrere Spuren aktivieren und unter
*Erweiterte Audioeigenschaften* zuweisen und benennen:

| Spur | Inhalt | Name |
|---|---|---|
| 1 | Gesamtmix | MIX |
| 2 | Mikrofon | MIC |
| 3 | Spiel | GAME |
| 4 | Discord / Party | PARTY |

**Facecam im Bild?** Wenn du Gameplay und Kamera auf einer gemeinsamen Leinwand
aufnimmst (z.B. Gameplay oben, Kamera unten), trägst du die beiden Bereiche unter
`layout` in deiner Config ein. nightcut schneidet sie als getrennte Dateien aus, und du
kannst die Kamera im Schnittprogramm frei platzieren.

## Eigene Einstellungen

Kopiere `config/default.yaml` nach `config/local.yaml` und ändere nur, was du brauchst:

```bash
uv run nightcut run aufnahme.mkv --config config/local.yaml --profile bf6
```

Die wichtigsten Punkte:
- `layout` – wo Gameplay und Facecam in deiner Aufnahme liegen (Anteile von 0 bis 1)
- `audio` – Namen deiner Audiospuren
- `editor.target_minutes` – gewünschte Videolänge
- `transcribe.language` – Sprache deines Kommentars
- `killfeed.player_names` – dein Name im Spiel. Dann liest nightcut alle paar Sekunden
  den Killfeed mit und findet auch Kills, bei denen du nicht laut geworden bist
  (das Spielprofil braucht dafür eine `killfeed_region`, bei `bf2042` ist sie drin).

## Was am Morgen im Ausgabeordner liegt

```
output/<aufnahme>/
├── <aufnahme>_nightcut.xml   ← Timeline zum Importieren
├── media/                    ← geschnittene Clips (Gameplay + Facecam)
├── shorts/                   ← Hochkant-Shorts mit Untertiteln
├── youtube.txt               ← Titelideen, Beschreibung, Kapitel, Tags (KI-Entwürfe, bitte prüfen)
├── summary.json              ← Kurzfassung für die Morgen-Nachricht
├── report.md                 ← Übersicht: Clips, Titelideen, Laufzeit, VRAM, Kosten
├── transcript.json
├── candidates.json
└── vision.json / edit.json
```

**Import in DaVinci Resolve:** *Datei → Importieren → Timeline…* und die XML wählen.
**Import in Premiere Pro:** *Datei → Importieren…* und die XML wählen.

Jeder Clip hat etwas Luft davor und danach zum Trimmen und einen Marker mit dem Grund,
warum er ausgewählt wurde.

## Let's-Play-Modus: Folgen statt Highlights

Für Serien behält nightcut alles, schneidet nur Leerlauf raus und teilt eine oder
mehrere Aufnahmen in Folgen auf:

```bash
uv run nightcut run teil1.mkv teil2.mkv teil3.mkv --mode letsplay --profile meinspiel --name meinspiel
```

So funktioniert es:
1. **Bild-Scan:** Jeder Keyframe wird als Mini-Vorschaubild geprüft: Wo steht das Bild still oder ist schwarz?
2. **Einordnen:** Das Vision-Modell sagt, was das ist: Menü, Ladebildschirm, Pause, Zwischensequenz oder normales Spiel.
3. **Schneiden:** Nur was zur gewählten Stärke passt, fliegt raus, und nie, während jemand redet.
4. **Folgen:** Geteilt wird nahe der Ziellänge (`episode_minutes`) an der natürlichsten Stelle: Ladebildschirm, Menü, Schwarzbild, lange Pause oder ein „bis zum nächsten Mal“.
5. **Metadaten:** Titelideen, Beschreibung und Kapitel pro Folge aus dem Transkript.

Der Wechsel zur nächsten Aufnahme zählt als natürliches Folgenende (`file_break_score`).
Dafür darf eine Folge auch weiter von der Ziellänge abweichen als bei anderen Pausen
(`file_break_reach`, Standard: doppelte Toleranz). Eine Aufnahme pro Abend ergibt also
meist eine Folge pro Abend: Lange Abende werden geteilt, kurze nicht an den nächsten geklebt.

Ist jede Aufnahme schon eine fertige Folge (eigenes Intro und Outro), stell
`letsplay.episodes: per_recording` ein: Dann wird nichts geteilt, nur die Leerlauf-Schnitte
werden angewendet. Mit `letsplay.first_episode: 226` geht die Nummerierung in Titeln und
Timelines bei Folge 226 weiter.

Dein Mikro still heißt nicht, dass niemand redet. Vor dem Schneiden hört nightcut die stillen Stellen
auf der Voice-Chat-Spur (`audio.party`) und, falls eingestellt, auf der Gesamtmischung (`audio.mix`,
z.B. für einen KI-Assistenten oder Text-to-Speech, die nur dort zu hören sind) ab. Wo jemand spricht,
wird nicht geschnitten (`letsplay.protect_voices`).

Schnittstärke (`--strength`):
- `gentle` (sanft, Standard): Menüs, Ladebildschirme, Pausen, AFK
- `normal`: zusätzlich Stellen mit über 30 s ohne Kommentar
- `tight` (straff): zusätzlich ab 12 s ohne Kommentar

Die Timelines zeigen direkt auf deine Original-Aufnahmen. Ein Schnitt ist nur ein Ein-
und Ausstiegspunkt: Gefällt dir einer nicht, ziehst du den Clip in DaVinci einfach wieder auf.

**Facecam fest im Bild und nur eine Tonspur?** Nimm `config/examples/burned-in-cam.yaml`
und trag ein, wo deine Cam sitzt. Sonst hält nightcut ein stehendes Spielbild wegen der
bewegten Cam für „Bewegung“.

**Ruhige Session?** Hat eine Aufnahme zu wenige starke Momente, fragt nightcut beim Start, ob
mit ruhigen Stellen bis zur Ziellänge aufgefüllt werden soll oder das Video lieber kürzer wird.
Direkt mitgeben mit `--fill` / `--no-fill`, dauerhaft mit `editor.fill: true|false` in deiner Config.

## Abgebrochen? Einfach neu starten

Jeder Schritt speichert sein Ergebnis. Wenn der PC nachts neu startet, führst du
denselben Befehl erneut aus und nightcut macht dort weiter, wo es aufgehört hat. Einen
einzelnen Schritt wiederholen: `--force vision` (oder `audio`, `transcript`,
`candidates`, `edit`, `render`, `export`, `shorts`, `metadata`; im Let's-Play-Modus
`audio`, `scan`, `transcript`, `cuts`, `episodes`, `export`, `metadata`).

## Eigenes Spiel hinzufügen

Kopiere eine Datei aus `profiles/` (z.B. `bf6.yaml`) und passe an:
- `keywords` – was du typischerweise sagst, wenn etwas passiert
- `vision_hints` – woran man im Bild einen guten Moment erkennt
- `vision_events` (optional) – eigene Ereignisse mit Gewicht 0–1, z.B. `{ rush_hour: 0.9 }`.
  Eingebaut sind Shooter-Ereignisse (kill, multikill, …) und spielunabhängige (`success`, `fail`,
  `funny`, `reaction`), damit auch ruhige Spiele wie Simulatoren genug Highlights bekommen.

Mitgeliefert: `bf6`, `bf2042`, `awayout`, `anno117`, `cardealer`, `minecraft`, `pokemon`, `policechief`, `sushi`. Bei `bf6` fehlt noch die `killfeed_region`.

## Anpassen und verbessern

nightcut lernt nicht von selbst dazu. Du kannst es aber an drei Stellen an dich anpassen,
ohne programmieren zu können:

1. **Deine Einstellungen** (`config/local.yaml`): Videolänge (`editor.target_minutes`), ob ruhige
   Sessions aufgefüllt werden (`editor.fill`), wie stark Let's Plays gekürzt werden
   (`letsplay.strength`), Sprache, Tonspuren, Facecam-Position und dein Spielername für den Killfeed.
2. **Das Spielprofil** (`profiles/<spiel>.yaml`): Hier steckt dein Geschmack.
   - Fehlen gute Momente, weil du dabei etwas anderes sagst? Trag deine typischen Ausrufe unter
     `keywords` ein, mit Gewicht 1 bis 3.
   - Nimmt die KI langweilige Szenen? Beschreib unter `vision_hints`, woran man im Bild einen guten
     Moment erkennt („Gegner fällt um“, „Fahrzeug explodiert“) und was keiner ist („Menü“, „Karte“).
   - Unter `vision_events` stellst du ein, wie wichtig dir Ereignisse sind (0 bis 1), z.B.
     `{ funny: 1.0, success: 0.5 }`.
3. **Den Code**: Wenn dir etwas grundsätzlich fehlt, ist ein Coding-Assistent der schnellste Weg.
   Beschreib ihm das Problem mit einem konkreten Beispiel aus deinem Report („Clip 12 schneidet
   mitten im Satz ab“). Lokale Coding-Modelle schaffen kleine Änderungen; für Umbauten über
   mehrere Dateien sind große Cloud-Modelle deutlich zuverlässiger.

**So probierst du Änderungen schnell aus:** Nach einer Änderung am Profil musst du nicht die ganze
Nacht neu rechnen. `--force candidates` wiederholt nur die Auswahl und alles danach; Audio und
Transkript kommen aus dem Zwischenspeicher. Vergleich dann `report.md`: Dort steht zu jedem Clip,
warum er gewählt wurde.

## Mit Hermes Agent nutzen

Kopiere den Ordner `hermes/nightcut` in deinen Hermes-Skills-Ordner. Hermes kann
nightcut dann nachts auf neue Aufnahmen loslassen und dir morgens eine kurze Nachricht
schicken. Jeder Lauf schreibt eine `summary.json`, und `nightcut summary <Ausgabeordner>`
gibt die fertige Nachricht aus: Clips oder Folgen, bester Titel, Rechenzeit und was die
Cloud gekostet hätte.

## Lizenz

MIT. Die KI-Modelle, die du herunterlädst, haben ihre eigenen Lizenzen und sind nicht
Teil dieses Repositorys.
