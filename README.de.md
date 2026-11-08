# nightcut – Deutsche Anleitung

**Abends zocken. Morgens liegt der Rohschnitt fertig in DaVinci Resolve oder Premiere.**

nightcut ist eine lokale KI-Pipeline für Gaming-Creator. Sie nimmt eine lange
OBS-Aufnahme, findet die besten Momente und baut daraus eine Timeline, die du in
**DaVinci Resolve** oder **Premiere Pro** importierst. Du prüfst, passt an und renderst.
Alles läuft auf deiner eigenen Grafikkarte, nichts wird hochgeladen.

## Was du brauchst

- NVIDIA-Grafikkarte mit **16 GB VRAM** oder Apple-Silicon-Mac mit mindestens
  **16 GB gemeinsamem Speicher** und macOS 14 oder neuer (getestet auf einem M1 Pro)
- [ffmpeg](https://ffmpeg.org/) (muss im PATH liegen)
- [Ollama](https://ollama.com/)
- [uv](https://docs.astral.sh/uv/) – installiert Python und alle Pakete automatisch
- Windows, Linux oder Apple-Silicon-macOS

## Installation mit einem Klick (Windows)

1. [install.bat herunterladen](https://github.com/Nilson89/nightcut/releases/latest/download/install.bat)
   (oder im Repository auf `install.bat` → *Download raw file*)
2. Doppelklick. Falls Windows warnt: *Weitere Informationen → Trotzdem ausführen*.

Das Skript installiert Git, ffmpeg, Ollama und uv (per winget), holt nightcut auf den Desktop,
richtet die Python-Umgebung ein und lädt die beiden KI-Modelle. Du brauchst etwa 25 GB freien
Speicher. Wird dein Desktop mit OneDrive synchronisiert, landet nightcut in `C:\nightcut`, und
auf dem Desktop liegt eine Verknüpfung. Für ein Update startest du `install.bat` einfach noch einmal.

Den NVIDIA-Treiber installiert das Skript nicht, der muss schon aktuell sein.

## Installation auf Apple Silicon macOS

Lade `install-macos.sh` herunter und führe es im Terminal aus:

```bash
chmod +x install-macos.sh
./install-macos.sh
```

Das Skript installiert bei Bedarf Homebrew und anschließend Git, ffmpeg, Ollama und uv. Danach
klont es nightcut nach `~/nightcut`, richtet die Python-Umgebung ein und lädt beide Ollama-Modelle.
Ein erneuter Lauf aktualisiert die Installation. Ollama und PyTorch verwenden Metal automatisch;
FFmpeg rendert H.264 über VideoToolbox. Unterstützt werden derzeit M1-Macs und neuer mit
macOS 14 oder neuer. Benötigt
werden ungefähr 25 GB freier Speicher.

## Installation von Hand

```bash
# 1. Repository holen
git clone https://github.com/Nilson89/nightcut.git
cd nightcut

# 2. Python-Umgebung installieren (CUDA auf NVIDIA, Metal auf macOS)
uv sync

# 3. KI-Modelle für Ollama laden
ollama pull qwen2.5vl:7b
ollama pull qwen3:14b

# 4. Erste Aufnahme verarbeiten
uv run nightcut run "D:/Aufnahmen/session.mkv" --profile bf6
```

Das Whisper-Modell für die Transkription wird beim ersten Lauf automatisch geladen.

Unter macOS lässt du `render.codec: auto` in der Konfiguration stehen. nightcut wählt automatisch
`h264_videotoolbox` und fällt bei Bedarf auf `libx264` zurück. Fehlt dem installierten FFmpeg der
`subtitles`-Filter, verwendet der Shorts-Export automatisch einen portablen Caption-Fallback.

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

**Deine Spuren heißen anders oder gar nicht?** Kein Problem, nightcut sucht selbst:
1. nach den Namen aus der Config (`MIC`, `GAME`, `PARTY` …; „MIC“ findet auch „MIC Main Headset“),
2. nach üblichen Namen: *Mikrofon, Mikro, Mic, Voice, Stimme, Headset* fürs Mikro,
   *Game, Spiel, Desktop* fürs Spiel, *Discord, TeamSpeak, Voice Chat, Chat* für den Voice-Chat,
3. **am Klang**, wenn die Spuren nur „Track1“, „Track2“ … heißen: Das Mikro ist die Spur, die zwischen
   den Wörtern fast still ist. Spiel und Gesamtmix sind das nie.
4. Hat die Aufnahme nur **eine** Tonspur, nimmt nightcut sie als Gesamtmix.

Zu Beginn jedes Laufs steht im Fenster, welche Spur genommen wurde und warum, z.B.
`Audio: mic = track 3 'Track3' (by its sound, level range 39 dB)`. Stimmt das mal nicht, legst du die
Spur in `config/local.yaml` fest (Position ab 0 gezählt, nur Tonspuren):

```yaml
audio:
  mic: { title: null, index: 2 }   # dritte Tonspur
```

**Facecam im Bild?** Auch das erkennt nightcut zu Beginn jedes Laufs selbst (`layout.detect: auto`,
siehe nächster Abschnitt): Kamera über, unter oder **neben** dem Gameplay auf einer gemeinsamen
Leinwand, oder fest ins Spielbild eingebaut. Bei einer Leinwand schneidet nightcut beide Bereiche
als getrennte Dateien aus, und du kannst die Kamera im Schnittprogramm frei platzieren.

## Wo sitzt deine Facecam? `nightcut layout`

Normalerweise musst du nichts tun: Mit `layout.detect: auto` (Standard) prüft nightcut am Anfang jedes
Laufs, wo Gameplay und Kamera liegen, schreibt das Ergebnis ins Fenster und legt ein Vorschaubild
`layout.png` in den Ausgabeordner (grün = Gameplay, orange = Kamera).

Willst du das Layout einmal prüfen und fest eintragen (spart pro Lauf ein paar Sekunden und das
Bildmodell bei eingebauter Cam), öffne die Eingabeaufforderung im nightcut-Ordner und gib eine deiner
Aufnahmen an:

```bash
uv run nightcut layout "D:/Aufnahmen/session.mkv" --save config/local.yaml
```

Das passiert:
1. nightcut schaut sich 8 Bilder aus der Aufnahme an (verteilt über die ganze Länge).
2. **Gameplay und Kamera nebeneinander oder untereinander** (eine große Leinwand): Die Bereiche
   werden ohne KI erkannt. Was in allen Bildern schwarz bleibt, ist leere Fläche. Sind beide Bilder
   gleich groß, ist das Gameplay das Bild, das sich mehr verändert.
3. **Kamera fest im Spielbild** (auch freigestellt ohne Hintergrund): Das Bildmodell sucht dich auf
   jedem der 8 Bilder, nightcut nimmt daraus einen Bereich, in dem du dich bewegen kannst.
4. Du bekommst den fertigen `layout`-Block angezeigt und ein Vorschaubild `layout_<aufnahme>.png`
   im nightcut-Ordner. Grün ist das Gameplay, orange die Kamera. **Schau es dir kurz an.**
5. Mit `--save config/local.yaml` landet der Block direkt in deiner Config. Gibt es die Datei noch
   nicht, wird sie angelegt. Steht schon ein `layout` drin, wird nur dieser Teil ersetzt; alles andere
   (Spielername, Videolänge …) bleibt, wie es ist.

Danach startest du jeden Lauf mit deiner Config, und nightcut nimmt das neue Layout:

```bash
uv run nightcut run "D:/Aufnahmen/session.mkv" --profile bf6 --config config/local.yaml
```

So sieht das Ergebnis in `config/local.yaml` aus (Zahlen sind Anteile des Bildes von 0 bis 1:
x/y = linke obere Ecke, w/h = Breite und Höhe):

```yaml
# found by nightcut layout: session.mkv (2026-11-03)
layout:
  detect: off
  gameplay: { x: 0.0, y: 0.0, w: 1.0, h: 1.0 }
  facecam:  { x: 0.79, y: 0.76, w: 0.21, h: 0.24 }
  facecam_burned_in: true
```

Stehen in deiner Config eigene Bereiche, nimmt nightcut immer diese, die automatische Erkennung ist dann aus.

- **Ohne `--save`** zeigt nightcut den Block nur an. Dann kopierst du ihn selbst in `config/local.yaml`
  (einen vorhandenen `layout`-Block vorher löschen).
- **Rahmen sitzt nicht richtig?** Zahlen in `config/local.yaml` von Hand anpassen, oder eine andere
  Aufnahme nehmen, in der du gut zu sehen bist.
- **Keine Kamera gefunden?** Dann steht `facecam: null` im Block. Das ist richtig, wenn du ohne Cam aufnimmst.
- Das Layout bleibt gleich, solange du deine OBS-Szene nicht umbaust. Der Befehl ist also nur einmal nötig.

## Eigene Einstellungen

Kopiere `config/default.yaml` nach `config/local.yaml` und ändere nur, was du brauchst:

```bash
uv run nightcut run aufnahme.mkv --config config/local.yaml --profile bf6
```

Die wichtigsten Punkte:
- `layout` – wo Gameplay und Facecam in deiner Aufnahme liegen (Standard: automatisch, fest eintragen mit `nightcut layout`)
- `audio` – deine Audiospuren (Standard: automatisch, siehe „OBS richtig einstellen“)
- `editor.target_minutes` – gewünschte Videolänge
- `transcribe.language` – Sprache deines Kommentars
- `killfeed.player_names` – dein Name im Spiel. Dann liest nightcut alle paar Sekunden
  den Killfeed mit und findet auch Kills, bei denen du nicht laut geworden bist
  (das Spielprofil braucht dafür eine `killfeed_region`, bei `bf2042` ist sie drin).
- `render.codec` – `auto` nutzt auf macOS VideoToolbox, auf NVIDIA-Systemen NVENC, sonst libx264

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

**Facecam fest im Bild und nur eine Tonspur?** Das erkennt nightcut jetzt selbst: Die Cam-Fläche wird
bei der Bewegungsprüfung ausgeblendet (sonst hielte nightcut ein stehendes Spielbild wegen der bewegten
Cam für „Bewegung“), und die einzige Tonspur gilt als Gesamtmix. Bei mehreren Aufnahmen prüft nightcut
das Layout an der ersten; alle Folgen einer Serie sollten also mit derselben OBS-Szene aufgenommen sein.

**Ruhige Session?** Hat eine Aufnahme zu wenige starke Momente, fragt nightcut beim Start, ob
mit ruhigen Stellen bis zur Ziellänge aufgefüllt werden soll oder das Video lieber kürzer wird.
Direkt mitgeben mit `--fill` / `--no-fill`, dauerhaft mit `editor.fill: true|false` in deiner Config.

## Abgebrochen? Einfach neu starten

Jeder Schritt speichert sein Ergebnis. Wenn der PC nachts neu startet, führst du
denselben Befehl erneut aus und nightcut macht dort weiter, wo es aufgehört hat. Einen
einzelnen Schritt wiederholen: `--force vision` (oder `audio`, `transcript`,
`candidates`, `edit`, `render`, `export`, `shorts`, `metadata`; im Let's-Play-Modus
`audio`, `scan`, `transcript`, `cuts`, `episodes`, `export`, `metadata`).

## Zwei NVIDIA-Grafikkarten

Steckt eine zweite NVIDIA-Karte im PC (z.B. RTX 5080 + RTX 5060 Ti), nutzt nightcut im
Highlight-Modus automatisch beide. Du musst nichts einstellen, der Befehl bleibt derselbe:

Apple Silicon nutzt die integrierte GPU über Metal und ignoriert die NVIDIA-Multi-GPU-Einstellungen.

```bash
uv run nightcut run "D:/Aufnahmen/session.mkv" --profile bf2042 --config config/local.yaml
```

So teilen sich die Karten die Arbeit:
- **Killfeed und Bildanalyse** (der größte Brocken): Hunderte einzelne Bilder an das Bildmodell. Jede Karte
  bekommt einen eigenen Ollama-Server, beide nehmen sich das nächste Bild aus derselben Warteschlange.
  Die schnellere Karte schafft automatisch mehr.
- **Transkript**: läuft auf der zweiten Karte, während die erste schon den Killfeed liest. Danach hilft die
  zweite Karte beim Killfeed mit.
- **Am Ende**: Clips werden auf beiden Karten gerendert, die Shorts entstehen auf der zweiten Karte, während die
  erste die Metadaten schreibt.

Beim Start siehst du, welche Karten genutzt werden (`GPUs: … → sharing the work`). Im `report.md` stehen
die Karten, die Gesamtzeit und der VRAM pro Karte (z.B. `9.8 / 6.1 GB`). Stufen, die gleichzeitig liefen,
sind markiert und werden bei der Gesamtzeit nicht doppelt gezählt.

- **Vergleichen:** `--gpus 1` rechnet nur auf der Hauptkarte, `--gpus 2` (oder nichts) auf beiden.
  Mit einem anderen `--name` landen beide Läufe in eigenen Ordnern.
- **Hauptkarte** ist die Karte im ersten PCIe-Steckplatz (GPU 0 in `nvidia-smi`). Andere Reihenfolge:
  `gpus.devices: [1, 0]` in `config/local.yaml`.
- Karten unter 10 GB VRAM werden nicht genutzt (`gpus.min_vram_gb`).
- nightcut startet die zwei Ollama-Server selbst (Ports ab 11435) und beendet sie am Ende wieder.
  Dein normales Ollama läuft einfach weiter.
- Der Let's-Play-Modus nutzt weiterhin eine Karte.

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
