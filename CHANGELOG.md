# Changelog

All notable changes to Anime Expeditions (Cream's Macro) are documented here.

## [0.20.0] - 2026-09-09

### New
- **Summer event**: Event mode now runs the Summer event instead of Villian Invasion. Its own lobby entry (Event -> Summer -> gamemode card) leads to two kinds, picked in the Task Builder's Event field: **Infinite & Fishing** (unlimited waves, so it takes a **Stop After Wave** target and wants an Autoplay Macro Operation) and **Portal Mode**, which picks and activates a portal on the way in and selects the next one after every win.
- **Portals mode**: a new Task Queue mode that runs a portal from the Inventory instead of the event -- lobby -> Inventory -> Portals tab -> search -> activate, then the usual Solo/Matchmaking tail, picking a fresh portal after each win. Its **Portal Name** field is the search query *and* names the reference crop, looked up as `<name>_portal`, then `<name>`, then the shipped `summer_portal` -- so running a portal other than Summer only takes adding a crop under that name in Settings > General > Image Manager.
- **Drag block** (Macro Manager > Setup): press at one point, move to another while held, then release -- a swipe for UI a raw Click cannot reach. Both endpoints get their own position picker, and per-block **Steps** and **Duration (ms)** let the drag be slowed until the game stops reading it as a click. Allowed in Pre Start and every battle phase.
- **Examples picker**: Macro Manager gains an **Examples** button listing bundled routines with a description and per-phase block count. Picking one copies it into your own templates, so an example can never be saved over or deleted by accident.

### Improved
- **Expedition -- play on when extraction will not take**: in a matchmaking lobby the confirm can never register while other players keep going, and the run used to re-attempt the whole extract chain at every remaining checkpoint. After a few failed attempts it stops asking and plays the match out, still continuing each checkpoint. The counter resets per match.
- **Expedition -- notice a Victory the party caused**: the wave watcher only ever looked for `defeat`, so a party's extraction ended the match while the run kept clicking at checkpoints that no longer existed until the result timeout. `victory` is now checked alongside it.
- **Expedition encounters -- take the Continue when there is one**: an encounter that offers its own Continue is now cleared by that single click before any teleport, route or dialogue is attempted. It works on every map rather than the four with a bundled route, and cannot strand the character the way a recorded walk can.
- **Expedition encounters on unmapped maps**: a map with no recorded encounter route now gets one look at that Continue instead of being written off. With neither a Continue nor a route it still says so and leaves the encounter alone.
- **Expedition encounter dialogue** is now driven by the option's label text rather than four fixed coordinates -- the buttons move between clients and recolour between encounters, so position and colour both failed to pin them down.
- **Auto Challenge priority**: runs after each finished task, so the :00/:30 challenge resets are taken mid-run instead of waiting for the whole queue.
- **Wait for Wave** now releases once the counter has been unreadable past a ceiling, whether or not a reward card was seen. Cards drop for kills, so a run going badly produced none and stranded every block behind the wait -- including the deferred unit placements that would have earned them.

### Fixed
- Tower navigation now reaches the Tower screen reliably.
- Event tasks saved against the retired Villian Invasion Acts are migrated to an event kind on load and logged, instead of stopping the run on an Act that no longer exists.

### Removed
- **Villian Invasion**, along with its Acts, its relic-gated Act 4 auto-divert (the Act 4 on-drop / runs / macro / play-mode task settings), and their reference crops.

## [0.19.1] - 2026-08-13

### Improved
- **Auto Fuel interval control**: the minutes/hours fields now remain available while Auto is selected, so a custom refill wait can be entered instead of being locked to the automatic 8-hour Max interval.
- **Expedition encounters**: added native encounter handling, routes, and screen references for East Town, Flower Forest, Rose Kingdom, and School Grounds.
- **Expedition reliability**: improved checkpoint, wave counter, Repeat Stage, Start Game, upgrade-card, and unit re-placement handling.
- **Navigation recovery**: widened card searches, added lobby re-sync, and exits the AFK Chamber when it blocks progress.
- **macOS**: stabilized code-signing identity across updates and fixed the Macro Manager panel rendering blank while the macro runs.

### Fixed
- Villian Invasion navigation now opens the event card before selecting the game mode.
- East Town is now available in the Challenge and Bounty Story map list.

## [0.19.0] - 2026-08-11

### New
- **East Town map**: added to the expedition and story map lists.
- **Tower game mode**: Play -> Tower -> Select Stage -> Start (solo). Wins advance floors with `Next_Floor`; defeats retry with `Repeat_Floor`. Supports Normal and Traitless Tower. No map dropdown in the builder; Rose Kingdom is the internal default.
- **Auto Fuel custom interval**: set any refill interval in minutes or hours (e.g. 30 minutes, 1 hour), or leave it on Auto to keep the per-amount default behavior.

### Improved
- **Event mode**: waits for the Event gamemode screen, clicks a user-configurable card coordinate, then image-clicks the Event Gamemode button.
- **Disconnect recovery**: kills a stuck Roblox client before deep-link relaunch, still respecting the multi-window guard.
- **File dialogs**: cancelled or failed native file dialogs now return clean results instead of rejected JS promises.
- **Auto Upgrade Unit**: bounded wait for the unit info panel before searching `priority_upgrade`, so slow-rendering panels are no longer skipped. New `quote_on` / `quote_off` reference images for user-built Detect conditions.
- **OCR overhaul**:
  - Auto Bounty wave OCR: wave-anchored parsing with card-local crops and contrast voting. Clipped wave numbers (`6`, `6C`) now resolve to `60` instead of ending the run at wave 6.
  - Optional RapidOCR engine layer (separate `requirements-rapidocr.txt`, Python 3.13-safe) with a RapidOCR -> Windows OCR -> Tesseract fallback chain.
  - Windows OCR output is always filtered by the config whitelist, preserving stats/wave/shop reads.
  - Daily Challenge map OCR keeps the HUD-anchored crop primary and adds a fixed relative top-right crop as fallback.
- **Packaging**: PyInstaller keeps `winsdk`/`winrt` collection and adds RapidOCR data only when installed.

### Fixed
- Auto Bounty no longer exits early on wave-60 bounties when OCR clips the trailing zero.
- Challenge map OCR recovers when the Daily Challenge HUD label is not found.
