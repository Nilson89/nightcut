# Changelog

All notable changes to nightcut. Versions follow [semantic versioning](https://semver.org/).

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

[0.3.0]: https://github.com/Nilson89/nightcut/compare/0.2.0...0.3.0
[0.2.0]: https://github.com/Nilson89/nightcut/compare/0.1.0...0.2.0
[0.1.0]: https://github.com/Nilson89/nightcut/releases/tag/0.1.0
