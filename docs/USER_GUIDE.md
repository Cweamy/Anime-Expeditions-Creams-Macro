# Cream's Macro: Complete User Guide

This guide covers installation, first-time setup, creating a farming routine,
running it safely, troubleshooting, and the commands used by source users and
contributors.

> **Important:** This is an unofficial automation tool. Game updates can move
> buttons or change screens, and using automation may violate Roblox or game
> rules. Use it at your own risk. Test with a short run before leaving it
> unattended, and never share a Discord webhook URL or your `settings.json`.

## 1. Choose an installation method

### Windows release (recommended)

1. Download `Creams-Macro-Anime-Expeditions-Windows.zip` from the
   [latest release](https://github.com/Cweamy/Anime-Expeditions-Creams-Macro/releases/latest).
2. Extract the entire ZIP to a normal folder. Do not run the executable from
   inside the ZIP.
3. Keep the executable and `Assets` folder together.
4. Run the executable. If SmartScreen appears, choose **More info**, verify the
   publisher/source, then choose **Run anyway** only if you trust the download.

Install Tesseract OCR if you want reward and match-stat reading. The rest of
the macro can run without OCR.

### Windows source install

Open PowerShell and run:

```powershell
git clone https://github.com/Cweamy/Anime-Expeditions-Creams-Macro.git
Set-Location Anime-Expeditions-Creams-Macro
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

On later launches:

```powershell
Set-Location Anime-Expeditions-Creams-Macro
.\.venv\Scripts\Activate.ps1
python main.py
```

You can also double-click `run.bat` after installing the dependencies.

### macOS source install (experimental)

In Terminal:

```bash
git clone https://github.com/Cweamy/Anime-Expeditions-Creams-Macro.git
cd Anime-Expeditions-Creams-Macro
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
chmod +x run.sh
./run.sh
```

Grant Terminal or the packaged app **Accessibility**, **Input Monitoring**, and
**Screen Recording** in **System Settings > Privacy & Security**. Roblox runs
beside the macro rather than inside it on macOS. If the windows do not fit,
choose a display setting with more logical screen space.

## 2. Prepare Roblox

1. Start Roblox and join Anime Expeditions.
2. Return to the main lobby before starting the macro.
3. Leave Roblox UI scale and display scaling consistent between recording and
   playback.
4. Avoid covering the Roblox window, changing its size, or moving it while the
   macro is running. The Windows build normally docks it automatically.
5. Run one short test while watching the screen before using repeat counts.

## 3. Configure the macro

### General settings

Open **Settings > General** and check the following:

- Confirm the macro detects the Roblox window.
- Set the Start, Stop, Pause, game-window toggle, and skip-wait hotkeys. The
  defaults are F1 Start, F2 Stop, F5 Pause, and F4 toggle game window.
- Use **Image Manager** if the supplied reference images do not match your
  Roblox rendering. Capture a tight crop of the requested button or label.
- Configure Tesseract only if OCR diagnostics report that it cannot be found.

### Discord webhook (optional)

1. Create a webhook in the desired Discord channel.
2. Paste its URL into the webhook setting and enable notifications.
3. Use the test action before a real run.
4. Treat the URL like a password. Regenerate it in Discord if it is exposed.

Optional **Progress Updates** can send task and challenge start/finish notifications
as the macro moves through the queue.

## 4. Build a reusable macro operation

Open **Macro Manager** and create the actions that should happen before or
during a match. Save the result as a named template.

Common blocks include:

- **Place Unit:** select a slot and position. Use click verification where
  available so rejected placements can be retried.
- **Click / Send Key:** perform a simple UI action or hotkey.
- **Walk Path:** record WASD movement and ability keys, save the path, then add
  it to the operation.
- **Record:** capture a timed mouse-and-keyboard sequence for actions that are
  too complex for individual blocks.
- **Once:** enable this on setup actions that should run only on the first entry
  to a stage, not on every repeated battle.

Keep recordings short and deterministic. Prefer dedicated blocks over a long
recording when possible, because they are easier to adjust after a game update.

### Expedition encounters

Expedition nodes can drop an encounter that has to be walked to and talked to.
The macro handles this itself: when the encounter marker appears it teleports to
spawn, walks that map's route, and interacts. Nothing is needed in your template
beyond your unit placements.

Routes ship for School Grounds, Rose Kingdom, Flower Forest, and East Town. For
any other map the encounter is left alone and logged. To add one, record a walk
from spawn to that map's NPC and map it in
`Assets/default_encounter_walk_paths.json`:

```json
{ "Your Map": "Your recorded path name" }
```

A route that does not fit your spawn stops safely: the interact prompt has to be
found before anything is clicked, so a walk that lands somewhere wrong logs and
gives up rather than clicking at the world.

## 5. Create the task queue

Open **Task**, add tasks in the order they should run, and configure each one:

1. Choose Story, Raid, Expedition, or another supported mode.
2. Select its map, stage or act, difficulty, and Solo or Matchmaking.
3. Set a small repeat count for the first test.
4. Assign the saved Macro Manager operation to the task.
5. Save the queue or export it if you want a backup/shareable setup.

For challenges, open **Challenge** separately. Enable Daily and/or the desired
Regular Challenge slots, select Solo or Matchmaking, and assign an operation
for each map that may appear. Challenge automation runs before the normal task
queue.

## 6. Start and monitor a run

1. Put the character in the lobby and close unexpected popups.
2. Open **Dashboard** and press **Start**, or use the configured Start hotkey.
3. Watch the first full cycle: navigation, placement, battle, result detection,
   and return/repeat.
4. Use **Pause** only when you need to inspect the current state. Use **Stop**
   before manually moving Roblox or changing task settings.
5. Check the Dashboard log first if a run stops. It normally names the image,
   screen, or recovery step that failed.

## 7. Troubleshooting

### A button or screen is not detected

- Confirm Roblox is visible, at the expected size, and on the expected screen.
- Open **Settings > General > Image Manager** and replace or add a reference
  crop for the name shown in the log.
- Crop tightly, but retain enough unique pixels to avoid matching unrelated UI.
- Remove overlays, notifications, and unusual UI scaling, then test again.

Reference crops are specific to the size the game renders at. `core.vision`
only sweeps +/-10% around a crop's own size, so a crop captured on a different
setup can miss outright rather than score slightly lower. On one machine a
shipped crop peaked at 0.61 where a locally captured crop of the same button
scored 1.00. If a name will not match, capture it yourself in the Image Manager
before adjusting thresholds.

### Two similar states match each other's crop

Templates are matched in greyscale, so two states of the same control that
share a border, background shape, and label can score highly against each
other. Colour differences do not help. On one setup a whole-button crop of an
*unset* control matched the *set* version of that same button at 0.92, over the
0.90 default threshold.

- Crop tightly around the part that actually differs (the glyph or digit)
  rather than the whole button.
- Or raise that name's Match threshold in the Image Manager above the score the
  wrong state reaches.
- Check both directions with the Detect block's **Test now** button: a crop
  should hit on the state it is named for and miss on the other one. Read the
  reported location too -- a miss whose best hit is somewhere unrelated on
  screen is no match at all, not a near miss.

### A Detect block drives an action that must not repeat

Detect treats a missing reference image, an unreadable screen, and an invalid
condition all as *not found*, so every unknown lands in the Else branch. Put
the branch that must not fire by accident behind a positive match:

```
find('quote_off') and not find('quote_on')    -> Then = retry
```

Written the other way round -- retrying whenever a "done" image fails to match
-- every unknown becomes a retry. This matters for controls that cycle rather
than being set, where applying the same value twice is not a no-op.

### Clicks land in the wrong place

- Stop the macro, return Roblox to its normal docked/side-by-side position, and
  restart the app.
- Do not resize Roblox after recording paths or positions.
- Re-record affected placements or paths at the same display scale used for
  normal runs.

### OCR stats or rewards are missing

- Install the desktop Tesseract application; the Python requirements do not
  include the OCR executable.
- Restart the macro after installation.
- Use the debug tools in Settings to test match-stat and reward reading.

### macOS captures are black or input does nothing

- Recheck Accessibility, Input Monitoring, and Screen Recording permissions.
- After changing permissions, fully quit and reopen Terminal/the macro.
- Ensure Roblox fits beside the control panel and is not off-screen.

### Recovery keeps looping

- Press Stop and return to the lobby manually.
- Read the last log entries and refresh the named reference image.
- Reduce the queue to one task and one repeat until the complete cycle succeeds.

## 8. Command reference

Run the app or diagnostics from the repository root:

```powershell
# Start the GUI on Windows
python main.py

# Run CLI input/window diagnostics without the GUI
python main.py --test

# Install runtime and developer dependencies
python -m pip install -r requirements.txt -r requirements-dev.txt

# Run all tests
python -m pytest -q

# Run one test module
python -m pytest -q tests/test_runner_challenge.py

# Lint the Python source
python -m ruff check .
```

Git update commands for a source installation:

```powershell
git status
git pull --ff-only origin main
python -m pip install -r requirements.txt
```

Do not run `git pull` with unsaved local edits. Back up `settings.json`, custom
templates, paths, recordings, and changed `Assets` before making large manual
changes, even though the built-in updater is designed to preserve user data.

## 9. Contributor workflow

Create a branch and verify changes before opening a pull request:

```powershell
git switch -c fix/short-description
python -m pytest -q
python -m ruff check .
git status
git add path\to\changed-file.py tests\test_changed_file.py
git commit -m "fix: short description"
git push -u origin fix/short-description
```

Pull requests should explain the problem, the behavior change, how the change
was tested, and any platform or real-game testing that is still needed.

## 10. Safe unattended-use checklist

- A one-repeat test completed successfully from lobby back to lobby.
- The correct task and operation are assigned.
- Stop and Pause hotkeys work.
- Roblox is unobstructed and will not be resized.
- Discord webhook testing succeeded, if enabled.
- The computer will not sleep and no scheduled restart is pending.
- Repeat counts and resource limits are reasonable.

