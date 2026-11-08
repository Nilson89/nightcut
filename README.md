# nightcut

**Play in the evening. Wake up to an edited timeline.**

nightcut is an overnight, fully local AI pipeline for gaming creators. It takes a long
OBS recording, finds the best moments and writes a timeline you open in
**DaVinci Resolve** or **Premiere Pro**. You review, adjust and render. Nothing is
uploaded anywhere; all models run on your own GPU.

> 🇩🇪 Deutsche Anleitung: [README.de.md](README.de.md)

## What it does

| Step | How | Output |
|---|---|---|
| Audio analysis | Loudness peaks on separate mic, game and voice-chat tracks | highlight score over time |
| Transcription | Whisper on the mic track (only where you talk) | `transcript.json` |
| Candidates | Score + keywords from your game profile, cuts snapped to sentence ends | `candidates.json` |
| Kill feed | The vision model reads the kill feed every few seconds and finds your kills, also silent ones (needs `killfeed.player_names` and a `killfeed_region` in the profile) | `killfeed.json` |
| Vision check | Local vision model (Ollama) rates frames of each candidate | `vision.json` |
| Edit | Local LLM picks and orders clips to your target length | `edit.json`, title ideas |
| Media | Clips are cut into clean MP4s (gameplay with all audio tracks, facecam separately) | `media/` |
| Timeline | Final Cut Pro 7 XML, imports into Resolve and Premiere | `<name>_nightcut.xml` |
| Shorts | Vertical 1080x1920 clips with word-by-word captions | `shorts/` |
| Metadata | Title ideas, description, chapters, tags (AI drafts: check that what they promise really happens) | `youtube.txt` |
| Report | Clip list, title ideas, time and VRAM per step | `report.md` |

Every step is cached. If the night is interrupted, run the same command again and it
continues where it stopped.

## Requirements

- NVIDIA GPU with **16 GB VRAM**, or an Apple Silicon Mac with **16 GB unified memory or more**
  running macOS 14 or newer (tested on M1 Pro; more memory is recommended for larger local models)
- [ffmpeg](https://ffmpeg.org/) in your PATH
- [Ollama](https://ollama.com/)
- [uv](https://docs.astral.sh/uv/) (installs Python and all packages)
- Windows, Linux or Apple Silicon macOS

## One-click install (Windows)

Download [install.bat](https://github.com/Nilson89/nightcut/releases/latest/download/install.bat)
(or open `install.bat` in the repository → *Download raw file*) and double-click it. It installs
Git, ffmpeg, Ollama and uv via winget, clones nightcut to your desktop (or `C:\nightcut` with a
desktop shortcut if your desktop is synced by OneDrive), runs `uv sync` and pulls both models.
About 25 GB of free space needed; your NVIDIA driver must already be up to date. Run it again to update.

## One-command install (Apple Silicon macOS)

Download `install-macos.sh`, then run:

```bash
chmod +x install-macos.sh
./install-macos.sh
```

The script installs Homebrew when needed, then Git, FFmpeg, Ollama and uv. It clones nightcut to
`~/nightcut`, creates the Python environment and downloads both Ollama models. Run it again to update.
Ollama and PyTorch use Metal automatically; FFmpeg uses VideoToolbox for H.264. The installer currently
targets M1 or newer Macs running macOS 14 or newer. About 25 GB of free space is required.

## Quick start (manual)

```bash
git clone https://github.com/Nilson89/nightcut.git
cd nightcut
uv sync                       # installs Python 3.12 and native PyTorch (CUDA on NVIDIA, Metal on macOS)
ollama pull qwen2.5vl:7b      # vision model
ollama pull qwen3:14b         # editing model

uv run nightcut run "D:/Recordings/session.mkv" --profile bf6
```

The Whisper model is downloaded automatically on the first run. Model weights are never
part of this repository; they come from their official sources under their own licenses.

On macOS, leave `render.codec: auto` in the config. nightcut selects `h264_videotoolbox` and falls
back to `libx264` if necessary. If the installed FFmpeg lacks the `subtitles` filter, Shorts use an
automatic portable caption-overlay fallback instead.

**Windows, nightcut on a network drive (NAS):** `uv run` and `nightcut.exe` resolve a mapped drive
to its `\\server\share` path, and Windows then fails to load the NumPy/PyTorch DLLs
("DLL load failed … wrong parameter"). Keep the repository on a local disk (recordings may stay
on the NAS), or start it with the venv's Python via the drive letter:
`Z:\...\nightcut\.venv\Scripts\python.exe -c "from nightcut.cli import app; app()" run ...`

## Recording setup (OBS)

nightcut works best with **separate audio tracks**. In OBS: *Settings → Output →
Recording → Audio Track* enable several tracks and name them in *Advanced Audio
Properties*, for example:

| Track | Content | Config name |
|---|---|---|
| 1 | Mix | (ignored) |
| 2 | Microphone | `MIC` |
| 3 | Game | `GAME` |
| 4 | Discord / party chat | `PARTY` |

**Tracks named differently, or not at all?** nightcut finds them itself:
1. by the names in the config (`MIC`, `GAME`, `PARTY` …; "MIC" also finds "MIC Main Headset"),
2. by common names: *Mic, Microphone, Voice, Headset, Mikrofon, Stimme* for the mic,
   *Game, Spiel, Desktop* for the game, *Discord, TeamSpeak, Voice Chat, Chat* for voice chat,
3. **by level** when tracks are just "Track1", "Track2" …: the mic is loud while you talk and nearly
   quiet in between; game audio and a full mix keep going. Only taken when one track is clearly ahead,
4. **unsure?** Whisper listens to short samples of the candidate tracks and finds where someone
   really talks. This also finds the mic when you talk little or a noise gate keeps it silent,
5. **still unsure?** nightcut writes a 10-second piece of each candidate (`track1.m4a`, `track2.m4a` …),
   you listen and type the number of the file with your voice (`o` opens the folder). The answer is
   remembered in `config/known_tracks.yaml` for all recordings with the same tracks. The question
   comes at start, not in the middle of the night; without a terminal (scheduled runs) nightcut takes
   its best guess and says so in the log,
6. with a **single** track, it is used as a full mix.

Every run prints which track was taken and why, e.g.
`Audio: mic = track 3 'Track3' (by its level, level range 39 dB)`. If that is ever wrong, run
`nightcut tracks <recording>` once: it lists all tracks with their levels, writes the listening pieces
and asks you. Or set it in `config/local.yaml` (position counted from 0, audio tracks only):
`audio: { mic: { title: null, index: 2 } }`.

**Facecam?** Also found at the start of every run (`layout.detect: auto`): camera above, below or
**next to** the gameplay on a shared canvas, or burned into the game picture. Canvas regions are cut
into separate files, so you can place the camera freely in your editor.

## Where is your facecam? `nightcut layout`

Usually nothing to do: with `layout.detect: auto` (default) every run finds gameplay and camera, prints
the result and writes a preview `layout.png` (green: gameplay, orange: camera) into the output folder.

To check it once and keep it fixed (saves a few seconds per run, and the vision model for burned-in
cams), run this in the nightcut folder with one of your recordings:

```bash
uv run nightcut layout "D:/Recordings/session.mkv" --save config/local.yaml
```

nightcut looks at 8 frames spread over the recording. A **canvas layout** (gameplay and camera
side by side or stacked) is found without AI: whatever stays black in every frame is empty space.
If both pictures have the same size, the one that changes more is the gameplay.
A **camera burned into the game picture** (also with the background removed) is found by the
vision model on each frame, and the boxes are combined into one region you can move around in.

You get the `layout` block and a preview `layout_<recording>.png` in the nightcut folder (green:
gameplay, orange: camera); have a quick look at it. With `--save` the block goes straight into your
config: the file is created if needed, an existing `layout` section is replaced and everything else
stays as it is. Then use that config for every run:

```bash
uv run nightcut run "D:/Recordings/session.mkv" --profile bf6 --config config/local.yaml
```

```yaml
# found by nightcut layout: session.mkv (2026-11-03)
layout:
  detect: off
  gameplay: { x: 0.0, y: 0.0, w: 1.0, h: 1.0 }
  facecam:  { x: 0.79, y: 0.76, w: 0.21, h: 0.24 }   # fractions: x/y top left corner, w/h size
  facecam_burned_in: true
```

Without `--save` the block is only printed; copy it into `config/local.yaml` yourself. If the box is
off, edit the numbers or try a recording where you are clearly visible. `facecam: null` means no
camera was found. You only need to do this again when you change your OBS scene. Regions set in your
config always win; automatic detection is then off.

## Configuration

Copy `config/default.yaml` to `config/local.yaml`, change what you need and pass it
with `--config config/local.yaml`. The most important settings:

- `layout`: where gameplay and facecam are in your recording (default: found automatically; fix it with `nightcut layout`)
- `audio`: mic, game and voice chat tracks (default: found automatically, see "Recording setup")
- `editor.target_minutes`: length of the final video
- `vision.model`, `editor.model`: which Ollama models to use
- `render.codec`: `auto` selects VideoToolbox on macOS, NVENC on NVIDIA systems, otherwise libx264

## Game profiles

A profile (`profiles/*.yaml`) holds what is game specific: keywords you typically say
when something happens and hints for the vision model. Included: `bf6`, `bf2042`, `awayout`,
`anno117`, `cardealer`, `minecraft`, `pokemon`, `policechief`, `sushi`. To add a game, copy a profile and adjust it. Pull requests welcome.

The vision model rates every moment as an event. Shooter events (kill, multikill, …) and
game-independent ones (`success`, `fail`, `funny`, `reaction`) are built in; a profile can add its
own with a weight between 0 and 1:

```yaml
vision_events: { rush_hour: 0.9 }
```

## Adapting and improving it

nightcut does not learn on its own. You can tune it in three places without writing code:

1. **Your settings** (`config/local.yaml`): video length (`editor.target_minutes`), whether calm
   sessions are filled up (`editor.fill`), how hard Let's Plays are cut (`letsplay.strength`),
   language, audio tracks, facecam position and your player name for the killfeed.
2. **The game profile** (`profiles/<game>.yaml`): this is where your taste lives. Add what you
   typically shout to `keywords` (weight 1 to 3), describe good and boring moments in `vision_hints`,
   and set how much each event matters in `vision_events` (0 to 1).
3. **The code**: for anything missing, a coding assistant is the fastest way. Give it a concrete
   example from your report ("clip 12 cuts off mid-sentence"). Local coding models manage small
   changes; for changes across several files, large cloud models are much more reliable.

**Trying changes quickly:** after editing a profile, `--force candidates` repeats only the clip
selection and what follows it; audio and transcript come from the cache. Then compare `report.md`,
which says for every clip why it was picked.

## Let's Play mode

Not every video is a highlight reel. For series, nightcut can keep everything and only
remove dead air, then split one or more recordings into episodes:

```bash
uv run nightcut run part1.mkv part2.mkv part3.mkv --mode letsplay --profile mygame --name mygame
```

| Step | How |
|---|---|
| Picture scan | Every keyframe as a tiny thumbnail: where does the picture stand still or go black? |
| Labels | The vision model says what a still stretch is: menu, loading screen, pause, cutscene, gameplay |
| Cuts | Only labels of the chosen strength are removed, never while someone is talking |
| Episodes | Split near `episode_minutes`, at the most natural break: loading screen, menu, black frame, long pause, or "see you next time" |
| Metadata | Title ideas, description and chapters per episode from the transcript |

Switching to the next recording counts as a natural episode end (`file_break_score`), and
it may end an episode further from the target length than other breaks (`file_break_reach`,
default 2× the tolerance). One recording per session therefore usually means one episode
per session; long sessions are split, short ones are not glued to the next one.

If every recording already is a finished episode (own intro and outro), set
`letsplay.episodes: per_recording`: nothing is split, only the dead-air cuts are applied.
`letsplay.first_episode: 226` continues the numbering of titles and timelines at episode 226.

Your mic being quiet does not mean nobody talks. Before cutting, nightcut listens to the quiet parts
on the voice chat track (`audio.party`) and, if set, the full mix (`audio.mix`, e.g. for an AI assistant
or text-to-speech that is only heard there). Where someone talks, nothing is cut
(`letsplay.protect_voices`).

Strengths (`--strength`): `gentle` (default: menus, loading screens, pauses, AFK),
`normal` (plus 30 s without talking), `tight` (plus 12 s without talking).

The episode timelines point at your original recordings. A cut is only an in/out point,
so you can pull any removed part back in the editor. Recordings with a stacked layout
(separate facecam area) are rendered into clean files instead.

**Facecam burned into the picture, one mixed audio track?** Found automatically: the cam area is
left out of the motion check (so a moving cam does not hide a frozen game picture) and the single
track is used as a full mix. With several recordings the layout is checked on the first one, so record
a series with the same OBS scene.

## Two NVIDIA GPUs

With a second NVIDIA card in the PC (e.g. RTX 5080 + RTX 5060 Ti), highlights mode uses both
automatically; the command stays the same.

Apple Silicon uses its single integrated GPU through Metal and ignores the NVIDIA multi-GPU settings.

- **Kill feed and vision check** (the biggest part): hundreds of independent model calls. Each card gets
  its own Ollama server and both take the next image from one queue, so the faster card does more.
- **Transcript** runs on the second card while the first one starts the kill feed; then the second card
  joins the kill feed scan.
- **At the end** clips are rendered on both encoders, Shorts are made on the second card while the first
  one writes the metadata.

The start shows which cards are used (`GPUs: … → sharing the work`). `report.md` lists the cards, the
total time and VRAM per card (`9.8 / 6.1 GB`); stages that ran at the same time are marked and not
counted twice. Compare with `--gpus 1` (main card only) and a different `--name`. The main card is the
one in the first PCIe slot (GPU 0 in `nvidia-smi`); change the order with `gpus.devices: [1, 0]`. Cards
below 10 GB VRAM are left out (`gpus.min_vram_gb`). nightcut starts and stops the two Ollama servers
itself (ports from 11435); your normal Ollama keeps running. Let's Play mode still uses one card.

## Importing the timeline

- **DaVinci Resolve:** *File → Import → Timeline…* and select the XML. Keep the media in the `media/` folder next to it.
- **Premiere Pro:** *File → Import…* and select the XML.

Clips have handles (extra seconds before and after) so you can trim freely. Each clip
carries a marker with the reason it was chosen.

## Using it with Hermes Agent

Copy `hermes/nightcut` into your Hermes skills folder. Hermes can then run nightcut on
new recordings at night and send you a short message in the morning. Every run writes
`summary.json`; `nightcut summary <output folder>` prints the message (clips or
episodes, best title, processing time, cloud vs. local cost).

## Command reference

```bash
nightcut run <recording> [<recording> ...] [--mode highlights|letsplay] [--profile bf6]
             [--config my.yaml] [--output dir] [--name folder] [--strength gentle|normal|tight]
             [--no-vision] [--no-llm] [--fill|--no-fill] [--gpus auto|1|2] [--force <stage>]
nightcut layout <recording> [--save config/local.yaml]
nightcut tracks <recording> [--config my.yaml]
nightcut summary <output folder> [...]
nightcut profiles
```

`--fill` / `--no-fill` (highlights): if a calm session has too few strong moments, fill up to the target
length with quiet ones, or rather make a shorter video. Without the option nightcut asks at start
(`editor.fill` in your config answers it for good; without a terminal it fills up).

Stages for `--force`: highlights: `audio`, `transcript`, `killfeed`, `candidates`, `vision`, `edit`, `render`,
`export`, `shorts`, `metadata`. Let's Play: `audio`, `scan`, `transcript`, `cuts`, `episodes`,
`export`, `metadata`.

## License

MIT. See [LICENSE](LICENSE). Models you download are covered by their own licenses.
