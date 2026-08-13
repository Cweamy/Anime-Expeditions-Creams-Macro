# Changelog

All notable changes to Anime Expeditions (Cream's Macro) are documented here.

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
