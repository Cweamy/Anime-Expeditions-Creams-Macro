# Cream's Macro | Anime Expeditions

**A free, open-source auto-farm macro for the Roblox game [Anime Expeditions](https://www.roblox.com/games/).** Docks Roblox directly inside its own window and automates the full Story/Raid grind loop — map and stage selection, unit placement, match start, Victory/Defeat detection, reward tracking, and repeat farming — so you can queue up a run and walk away.

[![CI](https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/actions/workflows/ci.yml/badge.svg)](https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg)](#requirements)

> Looking for an **Anime Expeditions auto farm bot**, **Anime Expeditions macro**, or a way to **auto raid / auto story farm** in Anime Expeditions on Roblox? You're in the right place.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Auto-Updater](#auto-updater)
- [Project Layout](#project-layout)
- [Contributing](#contributing)
- [Disclaimer](#disclaimer)
- [License](#license)

## Features

- **Docked automation** — Roblox is embedded as a native child window inside the macro's own UI, not remote-controlled from outside, so clicks and key presses land exactly where they should.
- **Task queue** — build a queue of Story or Raid tasks (map, stage/act, difficulty, Solo or Matchmaking, repeat count) and let the macro work through all of them in order.
- **Repeat farming with automatic recovery** — farm the same stage N times via Repeat Stage without re-doing the lobby/map/stage picks each run. A stuck battle or a missed click backs out to the lobby and retries automatically instead of derailing an unattended session.
- **Pre Start block builder (Creation)** — a drag-and-drop editor for what happens before a match starts: place starter units (with click-verify and auto-nudge if a spot is rejected), flip in-game settings via hotkey, and mark any block "Once" so it only fires on a task's first entry into a stage, not every repeat.
- **Walk path recorder** — record a WASD(+ability-key) movement path once per map and replay it automatically as part of Pre Start.
- **Victory/Defeat + reward OCR** — reads match stats (clear time, Yen, kills, damage) and reward items off the result screen automatically, cross-checked against scraped wiki stage data so garbled OCR reads get filtered out.
- **Discord webhook reporting** — optional win/loss embeds posted to a Discord channel as the macro runs.
- **Win/loss history & stats** — session and all-time win/loss counts, win rate, and a recent-run history, all in the Dashboard.
- **Global hotkeys** — start/stop/pause without touching the mouse, with the bound key shown right on the Dashboard's controls.
- **Self-updating** — checks GitHub for new releases and offers a one-click update from inside the app (see [Auto-Updater](#auto-updater)).

## Requirements

- **Windows 10/11** (this macro drives native Win32 windows directly — it does not run on macOS/Linux)
- **[Roblox](https://www.roblox.com/)** with Anime Expeditions
- **Python 3.10+**
- **[Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)** (preinstalled on most Win10/11 systems)
- **[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)** — required for reading match stats/rewards; pip cannot install this, grab the Windows installer from the link above

## Installation

```bash
git clone https://github.com/Cweamy/Anime-Expeditions-Creams-Macro.git
cd Anime-Expeditions-Creams-Macro
pip install -r requirements.txt
```

Then install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) if you haven't already (needed for stats/reward reading only — everything else works without it).

## Usage

Launch it with:

```bash
python main.py
```

...or just double-click `run.bat`. Start Roblox and join Anime Expeditions — the macro finds and docks the window automatically. From there:

1. **Task** — queue up what to farm (map, stage, difficulty, repeat count).
2. **Creation** — build a Pre Start routine (unit placement, settings, walk path) and save it as a template.
3. **Dashboard** — assign a template to a task, hit Start, and monitor progress/stats live.
4. **Settings** — hotkeys, Discord webhook, default walk paths, and calibration/debug tools.

CLI diagnostics (no GUI) are available via:

```bash
python main.py --test
```

## Auto-Updater

On launch, the macro checks GitHub for a newer tagged release than the one you're running. If one exists, a popup shows the version and release notes with an **Update & Restart** button — clicking it downloads the new release, swaps it in over your local copy (your `settings.json`, saved templates, and walk paths are never touched), and relaunches automatically. You can also trigger a manual check any time by clicking the version badge in the titlebar.

## Project Layout

```
main.py          # pywebview entry point / JS<->Python API bridge
core/            # macro engine: vision (image matching), runner (match automation),
                 # OCR, webhook, window docking, input, path recording, updater...
ui/              # frontend (HTML/CSS/JS) rendered inside the docked window
tools/           # one-off scripts for scraping wiki data (stage rewards, item icons)
Assets/ui/       # reference screenshots the macro's image search looks for
Assets/map/      # full map images for the Set Position picker
Assets/maps/     # map name-label crops for map-select image search
```

## Contributing

Issues and PRs are welcome. Every push/PR runs a CI sanity check (Python + JS syntax); there's no automated test suite yet, so please describe how you tested a change manually in your PR.

To cut a release: bump `VERSION`, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z` — this triggers the release workflow, which publishes a GitHub Release (what the auto-updater checks against) and attaches a packaged Windows build.

## Disclaimer

This is a fan-made automation tool, not affiliated with, endorsed by, or associated with Roblox Corporation or the developers of Anime Expeditions. Automating gameplay may violate the game's or Roblox's Terms of Service — use it at your own risk and discretion. All game assets referenced (screenshots, names) belong to their respective owners; only the macro's own code is covered by this repository's license.

## License

[MIT](LICENSE) — see the LICENSE file for details.
