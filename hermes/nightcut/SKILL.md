---
name: nightcut
description: "Turn raw gameplay recordings into editor-ready timelines (DaVinci Resolve / Premiere), fully local, usually overnight: a highlight video, or Let's Play episodes."
version: 0.3.1
author: Nilson1489
license: MIT
platforms: [linux, windows, macos]
prerequisites:
  commands: [nightcut, ffmpeg, ollama]
metadata:
  hermes:
    tags: [Video, Editing, Gaming, YouTube, Local AI]
---

# nightcut: overnight gameplay editing

nightcut analyses long gameplay recordings (mic transcript, audio peaks, kill feed,
a local vision model) and writes XML timelines. The user opens them in DaVinci
Resolve or Premiere Pro, checks them and renders. Two modes:

- **highlights** (default): the best moments of one session as one video, plus Shorts
  and YouTube metadata.
- **letsplay**: keep everything, cut only dead air (menus, loading screens, pauses,
  AFK), split one or more recordings into episodes with metadata per episode.

## When to use

- The user asks to cut, edit or find highlights in a gameplay recording → highlights.
- The user asks for Let's Play episodes, a series, "Folgen", or wants recordings
  cleaned up without picking highlights → letsplay.
- A scheduled night run: process all new recordings in the inbox folder.

If it is unclear which mode the user wants, ask once. Remember the answer for this
user's night runs.

## How to run

Highlights, one recording:

```bash
nightcut run "<recording>" --profile bf6 --config <user config>
```

Let's Play, several recordings in playing order (sort by recording time):

```bash
nightcut run "<rec1>" "<rec2>" "<rec3>" --mode letsplay --profile <game> --name <series> --config <user config>
```

Useful options:
- `--profile <name>`: game profile, list them with `nightcut profiles`
- `--config <file>`: the user's config (layout, audio tracks, player name, target lengths)
- `--strength gentle|normal|tight`: how much dead air Let's Play mode removes (default gentle)
- `--no-vision` / `--no-llm`: faster runs with fewer models
- `--force <stage>`: re-run a stage and everything after it

A run is resumable. If it was interrupted, run the same command again.

Output in `<output_dir>/<recording name>/` (highlights) or `<output_dir>/<name>/` (Let's Play):
- highlights: `<name>_nightcut.xml`, `media/`, `shorts/short_01.mp4 …`, `youtube.txt`, `report.md`
- letsplay: `ep01_<name>.xml …`, `ep01_youtube.txt …`, `report.md`
- Import: Resolve *File > Import > Timeline*, Premiere *File > Import*

## Night run

1. Look for recordings in the inbox folder the user configured that have no
   matching folder in the output folder yet.
2. Group them: recordings of a game the user marked as a Let's Play series go into
   one `--mode letsplay` run per series; every other recording gets its own
   highlights run.
3. Run one job after another (one GPU, one job at a time).
4. In the morning, read every `report.md` and send the user **one** short message.

## Morning message

Every finished run writes `summary.json`. Get the ready-made German message with:

```bash
nightcut summary "<output folder>" ["<output folder 2>" ...]
```

Send that text as it is (one message for all runs of the night). Only if the command
is not available, build it yourself from `report.md` in this shape; do not invent
numbers:

Highlights run:
```
🎬 nightcut: <recording> fertig
<h> h Aufnahme → <clips> Clips, <min> min (Ziel <target> min) · <shorts> Shorts
Bester Titel: "<first title idea from youtube.txt>"
Rechenzeit <time> · max <vram> GB VRAM
Cloud hätte <budget>–<premium> € gekostet, lokal <local> € Strom
Timeline: <path to xml>
```

Let's Play run:
```
🎬 nightcut: <series>, <n> Folgen fertig
<h> h Aufnahme → <n> Folgen à <min>–<max> min · <removed> min Leerlauf entfernt
Folge 1: "<title>" … Folge <n>: "<title>"
Rechenzeit <time> · Cloud <budget>–<premium> € vs. lokal <local> €
Ordner: <output folder>
```

The cost numbers are in the section "Cloud vs. local" of `report.md` (budget,
premium, local electricity). If the report contains a ⚠️ warning (for example the
LLM failed and a fallback was used), add one line with it. If a run failed, say
which one and show the last error lines of its log instead of the summary.

## Rules

- Never delete recordings or output folders.
- Do not start a second run while one is active.
- Do not post or upload anything; the user reviews the edit first.
