<p align="center">
  <img src="logo.ico" width="80" alt="Cream's Macro — Anime Expeditions logo">
</p>

<h1 align="center">Cream's Macro | Anime Expeditions</h1>

<p align="center">
  <strong>Free, open-source auto-farm macro for the Roblox game Anime Expeditions</strong><br>
  Vision-based (screen capture + image matching) — no injection, no memory reading.<br>
  Docks Roblox directly inside its own window and automates the full Story/Raid/Expedition grind loop.
</p>

<p align="center">
  <a href="https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/releases/latest">
    <img src="https://img.shields.io/github/v/release/Cweamy/Anime-Expeditions-Creams-Macro?style=flat-square&color=blue" alt="Latest Release">
  </a>
  <a href="https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/releases/latest">
    <img src="https://img.shields.io/github/downloads/Cweamy/Anime-Expeditions-Creams-Macro/total?style=flat-square&color=green" alt="Downloads">
  </a>
  <a href="https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/actions/workflows/ci.yml">
    <img src="https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License: MIT">
  </a>
  <a href="#requirements">
    <img src="https://img.shields.io/badge/platform-Windows-0078D6.svg?style=flat-square" alt="Platform: Windows">
  </a>
</p>

<p align="center">
  <a href="https://discord.gg/FwU6ppjKNf">Discord</a> · <a href="https://www.youtube.com/@Cweamya">YouTube</a> · <a href="https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/releases/latest">Download</a>
</p>

> Looking for an **Anime Expeditions auto farm bot**, **Anime Expeditions macro**, or a way to **auto raid / auto story farm / auto expedition** in Anime Expeditions on Roblox? You're in the right place.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Download & Install](#download--install)
- [Usage](#usage)
- [Auto-Updater](#auto-updater)
- [Project Layout](#project-layout)
- [Contributing](#contributing)
- [Disclaimer](#disclaimer)
- [License](#license)

## Features

- **Docked automation** — Roblox is embedded as a native child window inside the macro's own UI, not remote-controlled from outside, so clicks and key presses land exactly where they should.
- **Task queue** — build a queue of Story, Raid, or Expedition tasks (map, stage/act/difficulty, Solo or Matchmaking, repeat count) and let the macro work through all of them in order.
- **Repeat farming with automatic recovery** — farm the same stage N times via Repeat Stage without re-doing the lobby/map/stage picks each run. A stuck battle or a missed click backs out to the lobby and retries automatically instead of derailing an unattended session.
- **Pre Start block builder (Macro Manager)** — a drag-and-drop editor for what happens before a match starts: place starter units (with click-verify and auto-nudge if a spot is rejected), flip in-game settings via hotkey, and mark any block "Once" so it only fires on a task's first entry into a stage, not every repeat.
- **Walk path recorder** — record a WASD(+ability-key) movement path once per map and replay it automatically as part of Pre Start.
- **Victory/Defeat + reward OCR** — reads match stats (clear time, Yen, kills, damage) and reward items off the result screen automatically, cross-checked against scraped wiki stage data so garbled OCR reads get filtered out.
- **Discord webhook reporting** — optional win/loss embeds posted to a Discord channel as the macro runs.
- **Win/loss history & stats** — session and all-time win/loss counts, win rate, and a recent-run history, all in the Dashboard.
- **Global hotkeys** — start/stop/pause without touching the mouse, with the bound key shown right on the Dashboard's controls.
- **Regular Challenge automation** — its own tab: enable/disable each of the 3 stage slots independently, assign a Macro Operation per Story map (Challenge rotates a random map into each slot), and track today's play count against a daily cap. Resets on the same fixed :00/:30 clock every stage shares. Runs before the Task Queue every time you press Start.
- **Multi-scale image matching** — automatically tries a template at a few scale factors when the exact size misses, absorbing UI that renders slightly bigger/smaller on someone else's setup instead of failing outright.
- **Replaceable reference images** — if a button still isn't matching reliably on your setup, drop a same-named screenshot into Settings > General > "Open Assets Folder" to override it — no rebuild or reinstall needed (see [`Assets/ui/README.txt`](Assets/ui/README.txt) for the full catalog of what each image is for).
- **Themes** — an independent Background (Dark, true Black, Slate, or Light) and Accent color pick, mix and match freely, under Settings > General.
- **Self-updating** — checks GitHub for new releases and offers a one-click update from inside the app (see [Auto-Updater](#auto-updater)).

## Requirements

- **Windows 10/11** (this macro drives native Win32 windows directly — it does not run on macOS/Linux)
- **[Roblox](https://www.roblox.com/)** with Anime Expeditions
- **Python 3.10+**
- **[Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)** (preinstalled on most Win10/11 systems)
- **[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)** — required for reading match stats/rewards; pip cannot install this, grab the Windows installer from the link above

## Download & Install

### Option A: Download (recommended)

No `git clone`, no Python needed.

1. Open the [**Releases page**](https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/releases/latest)
2. The newest release is shown at the top
3. Under **Assets**, download **`Creams-Macro-Anime-Expeditions.zip`**
4. Extract it anywhere — you get the app `.exe` with an `Assets/` folder next to it — and run the exe

The `Assets/` folder is every image the macro searches for on screen, kept **outside** the exe on purpose: one folder per button/text, and you can open, replace, or add extra crops freely (Settings > Debug > **Image Manager** captures and crops them for you, straight from your Roblox screen). Updates never overwrite images you've changed or added.

(The old bootstrapper exe is no longer uploaded to releases — the zip is the one download. If you already have a bootstrapper from before, it keeps working: it fetches this same zip. Need a fresh one to share around? Build it locally with `build_bootstrap.py`.)

> Windows SmartScreen may warn about an unrecognized app the first time (normal for small open-source tools) — click **More info → Run anyway**, or build it yourself from source below.

The only other thing you need is [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (pip can't install this one — grab the Windows installer from the link) for reading match stats/rewards; everything else works without it.

### Option B: Run from source

```bash
git clone https://github.com/Cweamy/Anime-Expeditions-Creams-Macro.git
cd Anime-Expeditions-Creams-Macro
pip install -r requirements.txt
```

### macOS (experimental — testers wanted)

Releases also publish **`Creams-Macro-Anime-Expeditions-macOS.zip`** (a `.app` + the same editable `Assets/` folder), and source runs work via `./run.sh`. The mac port is **untested on real hardware** — it exists for testers. Key differences and setup:

1. **Permissions (required):** System Settings > Privacy & Security — grant the app (or your terminal, for source runs) **Accessibility**, **Input Monitoring**, and **Screen Recording**. Without them, clicks silently do nothing and captures come back black. The app logs a warning at startup if Accessibility is missing.
2. **Side-by-side, not docked:** macOS can't embed another app's window, so Roblox is auto-arranged *next to* the control panel at the exact reference size instead of inside it.
3. **Scaling:** all captures are normalized and clicks scaled automatically (Retina 2x included), so the same Assets images work — but expect to add your own crops via the Image Manager where Roblox's mac rendering differs.
4. **Self-update** isn't wired up for the mac build yet — replace the app with a freshly downloaded zip.
5. Global hotkeys need elevated permissions on macOS; without them, use the on-screen buttons.

Either way, install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) if you haven't already (needed for stats/reward reading only — everything else works without it).

## Usage

If you're running from source, launch it with:

```bash
python main.py
```

...or just double-click `run.bat`. (If you used the bootstrapper, just run it — it launches the app for you.) Start Roblox and join Anime Expeditions — the macro finds and docks the window automatically. From there:

1. **Task** — queue up what to farm (map, stage, difficulty, repeat count).
2. **Macro Manager** — build a Pre Start routine (unit placement, settings, walk path, clicks) and save it as a template.
3. **Challenge** — optionally enable Regular Challenge automation and assign a Macro Operation per map (runs before the Task Queue on Start).
4. **Dashboard** — assign a template to a task, hit Start, and monitor progress/stats live.
5. **Settings** — hotkeys, Discord webhook, default walk paths, themes, and calibration/debug tools.

CLI diagnostics (no GUI) are available via:

```bash
python main.py --test
```

## Auto-Updater

On launch, the macro checks GitHub for a newer tagged release than the one you're running. If one exists, a popup shows the version and release notes with an **Update & Restart** button. What "Update" downloads depends on how you're running it — the packaged exe swaps itself for the new exe; running from source instead swaps in the new source over your local copy. Either way your `settings.json`, saved templates, walk paths, **and anything you've changed or added in the `Assets/` folder** are never touched — updates only ever *add* Assets images that are new in a release, and it relaunches automatically. You can also trigger a manual check any time by clicking the version badge in the titlebar. (If you're using the bootstrapper, it also checks for a newer app exe on every launch on its own, independently of this.)

## Project Layout

```
main.py          # pywebview entry point / JS<->Python API bridge
core/            # macro engine: vision (image matching), runner (match automation),
                 # OCR, webhook, window docking, input, path recording, updater...
core/constants.py # frozen-build-aware path resolution (BUNDLE_DIR/APP_DIR) --
                 # every other core/*.py module's paths derive from this
ui/              # frontend (HTML/CSS/JS) rendered inside the docked window
tools/           # one-off scripts for scraping wiki data (stage rewards, item icons)
Assets/ui/       # reference screenshots the macro's image search looks for --
                 # one folder per searched name, every image inside it is tried
                 # as a variant. User-editable (ships loose beside the exe, never
                 # bundled) -- see Assets/ui/README.txt for the full catalog
Assets/map/      # full map images for the Set Position picker
Assets/maps/     # map name-label crops for map-select image search (same
                 # folder-per-name layout as Assets/ui)
Paths/defaults/  # known-good default walk paths, shipped with the repo
bootstrap.py     # tiny installer exe -- downloads/extracts the release zip and launches
                 # the app; built locally via build_bootstrap.py, not uploaded to releases
build_pyinstaller.py # builds the real app exe
build_bootstrap.py # builds bootstrap.py into its own small exe
```

## Contributing

Issues and PRs are welcome. Every push/PR runs a CI sanity check (Python + JS syntax); there's no automated test suite yet, so please describe how you tested a change manually in your PR.

To cut a release: bump `VERSION`, commit, then tag with an **annotated** tag whose message is a short, human-readable changelog: `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`. That message becomes both the GitHub Release body and what gets posted to Discord (see below) — a lightweight tag (no `-a`/`-m`) falls back to just the tagged commit's own message, which is usually not what you want announced. Pushing the tag triggers the release workflow, which builds the app exe with PyInstaller (see `build_pyinstaller.py`), packages it together with the user-editable `Assets/` folder into `Creams-Macro-Anime-Expeditions.zip` — the release's **only** uploaded asset, which new installs, the auto-updater, and any bootstrapper copies all read from — and publishes a GitHub Release.

Every push to `main` posts a one-line summary to a Discord "git log" channel; every tagged release posts its changelog to a separate Discord "update log" channel. Both are wired via `DISCORD_GIT_LOGS_WEBHOOK`/`DISCORD_UPDATE_LOGS_WEBHOOK` repo secrets (Settings > Secrets and variables > Actions) — unset in a fork, so both steps just no-op instead of failing.

To build either exe locally instead of waiting on CI: `pip install pyinstaller`, then `python build_pyinstaller.py` / `python build_bootstrap.py`. Output lands in `dist/`.

## Disclaimer

This is a fan-made automation tool, not affiliated with, endorsed by, or associated with Roblox Corporation or the developers of Anime Expeditions. Automating gameplay may violate the game's or Roblox's Terms of Service — use it at your own risk and discretion. All game assets referenced (screenshots, names) belong to their respective owners; only the macro's own code is covered by this repository's license.

## License

[MIT](LICENSE) — see the LICENSE file for details.
