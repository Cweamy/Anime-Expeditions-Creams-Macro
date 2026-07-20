Reference images the macro's image search (core/vision.py, core/runner.py)
looks for by name. Each is a small, tightly cropped screenshot of just the
button/text being searched for -- not a full screen capture.

Filenames must match the `name` string a runner call passes to
core.vision.find_image(hwnd, "<name>", ...) EXACTLY -- core.vision.
template_path just does `f"{name}.png"`, no normalizing of spaces/case.
Windows' filesystem is case-insensitive, so a casing mismatch (Victory.png
for "victory") still resolves fine, but a SPACE where the code has an
underscore does not ("Leave Stage.png" silently never matched "leave_stage"
and just as silently no-opped, since every lookup here is wrapped as
optional/best-effort -- see leave_stage.png/repeat_stage.png/
max_placement_reached.png/cannot_place.png below, all renamed from
space-separated names that were quietly dead code). When adding a new one,
match whatever snake_case name the calling code actually uses.

nav_play.png
  The Play button on the Nav bar (bottom-left menu: Store/Units/Items/
  Quests/Summon/Areas/Play/Events). Searched for inside a fixed region
  (core.runner.NAV_PLAY_REGION) in the docked game window's own
  coordinates, both to confirm you're on the lobby (it only renders there)
  and to click it. The region is padded well past the button's own size on
  purpose -- gives template matching room to find it even if it's drifted
  a bit, see the region's own comment in core/runner.py.

nav_back.png
  The "Back" button shown on the gamemode-select screen (Story/Raid/
  Challenge/...) after Play is clicked. Used only to CONFIRM that menu has
  actually finished opening -- once it's found, Story is clicked at the
  fixed coordinate (666, 147) rather than via image search (see
  core.runner.STORY_CLICK).

story.png
  Not used by the runner -- Story's screen position is fixed once the
  gamemode menu is open (see core.runner.STORY_CLICK), so it's just a
  hardcoded click, no search needed. Left here in case a future screen
  needs a "find the Story card" search.

raid.png
  Used by the runner to find and click Raid on the gamemode menu (Story's
  neighboring card) -- unlike Story, Raid has no hardcoded click point, so
  this is searched for instead. Tight crop of just the word (see
  core.runner._click_gamemode).

upgradeable.png / not_upgradeable.png
  Used by Battle-phase Upgrade Unit blocks (core.runner._run_upgrade_unit_tick).
  After clicking a placed unit, the runner searches for whichever of these
  actually renders on its info panel: upgradeable.png means click it now;
  not_upgradeable.png (greyed out / insufficient gold / on cooldown,
  whatever this game shows) means wait and retry later instead of clicking.

team.png
  Used by Team Loadout application (core.runner._apply_team_loadout).
  After pressing H, the runner waits for this to confirm the team-select
  panel actually opened, then clicks it before picking a Loadout row.

confirm.png
  Used by Team Loadout application, right after clicking a Loadout row --
  confirms the choice before moving on to the equipment pick.

include.png / exclude.png
  Used by Team Loadout application to pick Include or Exclude for
  equipment (whichever the task's template has set) after Confirm.
  exclude.png hasn't been added yet -- until it is, an "Exclude" template
  just logs and skips the equipment click (team/loadout itself still
  applies fine either way).

warning.png
  Optional (core.runner._wait_out_start_game_warning): if a warning popup
  is blocking Start Game right after Pre Start finishes, the runner waits
  up to 10s for it to clear before searching for Start Game at all.

nav_start_game_2.png
  Two uses: (1) optional, core.runner._click_start_game_2_if_found -- a
  second Start Game / confirm button that can show up alongside the
  warning above (e.g. a "Start Anyway" prompt) -- found and clicked, this
  skips the warning wait entirely instead of sitting through the full
  timeout for something that was already dismissable right away. (2) a
  fallback variant for the actual "start the round" click -- see
  nav_start_game_3.png below.

nav_start_game_3.png
  Optional. core.runner._find_start_game_button tries nav_start_game,
  then nav_start_game_2, then this one, in order, for the actual "start
  the round" click (core.runner._play_one_match) -- different visual
  variants of the same button seen in practice, so that click isn't
  dependent on just one of them matching.

Add more <name>.png files here as new macro steps need to recognize other
buttons/screens -- core.vision.find_image(hwnd, "<name>", ...) will pick
up any file added under this folder automatically, no code change needed
beyond the step that calls it.
