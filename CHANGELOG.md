# Changelog

All notable changes to nightcut. Versions follow [semantic versioning](https://semver.org/).

## [Unreleased]

## [0.3.1] - 2026-11-13

### Changed
- **Every audio track stays in the edit.** Rendered clips and the Resolve/Premiere timeline now carry
  all audio tracks of the recording, separately and in their original order, not only the ones the
  analysis uses. Before, a track nightcut did not recognise (e.g. voice chat on an unnamed Aitum
  track, music, alerts) was dropped. Known tracks are named `MIC`, `GAME`, `PARTY`, the others keep
  their name. The mix (found by name, by sound, or set as `audio.mix`) is on the timeline but
  switched off.

### Fixed
- **The mix is no longer taken for the mic.** A track that sounds like the sum of the other tracks
  is recognised as the mix and never offered or guessed as the mic, whatever its name. Before, a
  mix with a gated mic in it could win the best guess. A mix that runs a few milliseconds behind
  the single tracks is found too.
- **Best guess = the first track where someone talks.** When nightcut cannot tell the mic from a
  voice chat and gets no answer (Enter, or no terminal), it now takes the lower track number instead
  of the bigger level range. OBS setups usually put the own mic before Discord.
- **Track names with a shared start** (e.g. Aitum Stream Suite: `Aitum Stream Suite Audio <scene> 1`
  ... `4`) are judged only on the part that differs. Before, the word "Stream" made every track a mix.
- The game track without a name is found also when a voice chat track is left: if Whisper listened,
  tracks where someone talks are not taken for the game.

## [0.3.0] - 2026-11-10

### Added
- **Update scripts.** Double-click `update.bat` (Windows) or run `./update.sh` (macOS/Linux) in the
  nightcut folder to get the latest version: it pulls from GitHub, updates the Python environment
  and downloads missing models. `config/local.yaml`, your own profiles and the output folder are
  kept. If you changed files that belong to nightcut, it asks before putting them aside with `git stash`.
- **Automatic mic track detection.** nightcut tries the config name, a fixed index, common track
  names (Mikrofon, Voice, Headset, ...) and finally the sound: the mic is the track that is nearly
  quiet between the words. The mic track `index` now defaults to `auto`, and the log says which track
  was taken and why. A single audio track is used as a full mix.
- **Quiet mics are found too.** Level percentiles only count frames with sound, so a mic behind a
  noise gate no longer looks dead. When the level is not clear, Whisper checks where someone really
  talks. If nightcut is still unsure, it writes short listening files per track and asks in the
  terminal. The answer is remembered per track layout in `config/known_tracks.yaml`.
- `nightcut tracks <recording>`: shows all audio tracks and lets you pick the mic by ear.
- **Automatic facecam layout.** `layout.detect: auto` (new default) finds gameplay and facecam at
  the start of every run and saves a preview `layout.png` in the output folder. A camera next to
  the gameplay is found now as well. Regions set in your config turn detection off.
- Apple Silicon macOS support (contributed by mueller-werner in #1): native MPS transcription,
  portable H.264 encoder selection (VideoToolbox), a caption fallback for Shorts without libass,
  and the `install-macos.sh` installer.
- `nightcut --version` and `nightcut models` (lists the Ollama models your config needs).

### Fixed
- Recordings shorter than 60 seconds failed with "operands could not be broadcast together".

## [0.2.0] - 2026-11-03

### Added
- `nightcut layout`: finds gameplay and facecam in a recording (canvas layouts without AI,
  burned-in cams with the vision model) and writes the layout block into a config with `--save`.
- Two NVIDIA GPUs in highlight mode: one Ollama server per card sharing one queue for kill feed
  and vision checks; transcript and Shorts on the second card. `--gpus 1` pins everything to the
  main card.
- Report: GPUs, VRAM per card and the work split per card.

## [0.1.0] - 2026-11-03

First release: highlight and Let's Play modes, kill feed scan, Shorts, YouTube metadata, XML
timelines for DaVinci Resolve and Premiere Pro, cost report and the one-click Windows installer
`install.bat`.

[Unreleased]: https://github.com/Nilson89/nightcut/compare/0.3.1...HEAD
[0.3.1]: https://github.com/Nilson89/nightcut/compare/0.3.0...0.3.1
[0.3.0]: https://github.com/Nilson89/nightcut/compare/0.2.0...0.3.0
[0.2.0]: https://github.com/Nilson89/nightcut/compare/0.1.0...0.2.0
[0.1.0]: https://github.com/Nilson89/nightcut/releases/tag/0.1.0
