Reference images the macro's image search (core/vision.py, core/runner.py)
looks for by name. Each is a small, tightly cropped screenshot of just the
button/text being searched for -- not a full screen capture. See
Assets/README.txt for how this folder relates to the others (item_icons,
maps, stage_data.json).

Filenames must match the `name` string a runner call passes to
core.vision.find_image(hwnd, "<name>", ...) EXACTLY -- core.vision.
template_path just does `f"{name}.png"`, no normalizing of spaces/case.
Windows' filesystem is case-insensitive, so a casing mismatch still
resolves fine, but a SPACE where the code has an underscore does not. Every
file in this folder is lowercase snake_case with no spaces for exactly
that reason -- keep any new one the same way.

FOLDER LAYOUT
-------------
Every button that has more than one visual variant on file (nav_start_
game.png/_2/_3/_4, expedition.png/_2, and so on -- the ones this catalog
below describes as "tried in order"/"same idea as nav_start_game.png and
friends") lives grouped together in its own subfolder named after the
base button (Assets/ui/nav_start_game/nav_start_game.png, .../
nav_start_game_2.png, ...) instead of all loose in one flat folder.
Nothing in the code or in this catalog needs to know about that --
core.vision.template_path still resolves a plain name like
"nav_start_game_2" on its own, falling back to a shallow search of this
folder's immediate subfolders if it's not directly here. Everything
that's still just ONE image (no numbered variant) stays loose at the top
level as before. Dropping a same-named override (see REPLACING AN IMAGE
YOURSELF below) still works exactly the same regardless of which
subfolder the bundled original lives in -- overrides always stay flat.

REPLACING AN IMAGE YOURSELF
----------------------------
If a button isn't being found/clicked reliably on your setup (a common
cause: Roblox rendering its UI at a slightly different size -- see
core.vision.SCALE_FACTORS, which already tries a few scales automatically
before giving up), you can swap in your own screenshot without touching
the app at all:

1. In the macro, open Settings > General > "Open Assets Folder" -- this
   opens (creating if needed) a persistent Assets/ui folder next to the
   exe. This is a SEPARATE location from the one this README lives in
   (which is bundled inside the app and gets re-extracted fresh every
   launch for a packaged exe -- editing it directly doesn't stick).
2. Crop a tight screenshot of just the button/text that isn't matching,
   the same way the existing reference images are cropped.
3. Save it there under the EXACT same filename as the one it's replacing
   (see the catalog below for which name goes with which button).
4. Restart the macro. core.vision checks this folder FIRST for every
   template, falling back to the bundled original if nothing's there --
   so this only ever overrides what you've actually replaced.

(Running from source instead of the packaged exe? Assets/ui IS that
persistent folder already -- just edit the file directly, no separate
override location needed.)

CATALOG
-------

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
  Expedition/...) after Play is clicked. Used only to CONFIRM that menu
  has actually finished opening -- once it's found, Story is clicked at
  the fixed coordinate (666, 147) rather than via image search (see
  core.runner.STORY_CLICK).

nav_disband.png
  Optional. Checked right before clicking the gamemode card (Story/Raid/
  Expedition) for a "Disband Party" prompt that can block the click --
  found and clicked, this clears the way first. Missing template just
  skips the check.

story.png
  Not used by the runner -- Story's screen position is fixed once the
  gamemode menu is open (see core.runner.STORY_CLICK), so it's just a
  hardcoded click, no search needed. Left here in case a future screen
  needs a "find the Story card" search.

raid.png
  Used to find and click Raid on the gamemode menu (Story's neighboring
  card) -- unlike Story, Raid has no hardcoded click point, so this is
  searched for instead. Tight crop of just the word (see
  core.runner._click_gamemode).

expedition.png
  Same idea as raid.png, for the Expedition gamemode card.

expedition_flower_forest.png / expedition_rose_kingdom.png
  Expedition's own map cards (core.runner._select_expedition_map,
  EXPEDITION_MAP_IMAGES) -- clicked to pick that map on the Expedition
  screen. School Grounds has NO image here: it's whatever's selected by
  default when the screen opens, so no search/click happens for it at all.

nav_select_stage.png
  The confirm button that finalizes a Story/Raid map+stage+difficulty
  pick. Waited on to confirm the stage-select screen has opened (Raid
  reuses the same screen as Story, just a different row layout), then
  clicked (verified/retried) to move on to Start/Enter Matchmaking.

exp_select_stage.png
  Expedition's equivalent of nav_select_stage.png -- its own confirm
  button after picking a map and difficulty, Solo mode only (matchmaking
  skips straight to exp_enter_matchmaking.png below).

enter_matchmaking.png / exp_enter_matchmaking.png
  "Enter Matchmaking", matchmaking play mode only, searched for after the
  stage/difficulty confirm click. enter_matchmaking.png is restricted to a
  calibrated region (coords.matchmaking_region_*) for Story/Raid;
  exp_enter_matchmaking.png is searched full-window instead since
  Expedition has no calibrated region for it.

nav_start.png
  Solo mode's "Start" button, clicked once the stage/difficulty confirm
  screen closes (core.runner._click_start_and_wait_teleport). Re-clicked
  only while still visible (a dropped click); once it's gone, that's
  success and the runner waits on nav_unitmanager.png instead of
  continuing to click a button that already worked.

nav_unitmanager.png
  Only renders once you're actually inside a match -- polled continuously
  as the actual confirmation a teleport-in finished (used by both the
  initial entry and every repeat's re-teleport).

warning.png
  Optional. If a warning popup is blocking Start Game right after Pre
  Start finishes, the runner waits up to 10s for it to clear before
  searching for Start Game at all.

nav_start_game.png / nav_start_game_2.png / nav_start_game_3.png /
nav_start_game_4.png
  The in-match "Start Game" (ready-up) button, tried in this order as
  different visual variants of the same button seen in practice
  (core.runner._find_start_game_button) -- so the actual click isn't
  dependent on just one of them matching. nav_start_game.png alone is also
  used earlier to check party leadership (only the leader sees it) before
  Pre Start runs.

victory.png / defeat.png
  Story/Raid's match-result screen, polled throughout battle
  (core.runner._wait_for_match_result). Expedition doesn't use these at
  all -- see exp_continue.png/exp_extract.png below instead.

School Grounds.png / Rose Kingdom.png / Fairy King Forest.png /
King's Tomb.png / Flower Forest.png
  Regular Challenge map detection (core.runner._detect_current_challenge_
  map) -- Challenge is Story's own flow, just with the game picking a
  random one of these 5 maps for you instead of you picking it, so this
  is a "which one did it land on" check, NOT the map-card search
  Assets/maps/<map>.png does (different folder, different purpose --
  that one's for scrolling/clicking a map by NAME in Story's own map
  carousel, this is for recognizing a map that's already showing).

challenge.png
  Regular Challenge's gamemode card, same idea as raid.png/expedition.png
  (core.runner._click_gamemode) -- found by image search on the Play
  menu, no fixed coordinate like Story's.

challenge_loaded.png
  Confirms the Challenge screen has actually finished opening
  (core.runner._enter_challenge_stage) before clicking a stage slot at
  one of its 3 fixed positions (CHALLENGE_STAGE_CLICK) -- a load-
  confirmation banner, not a button, so it's only waited on, never
  clicked itself.

chal_enter.png
  Challenge's own "Enter Matchmaking" button, Matchmaking play mode only
  -- searched full-window (no calibrated region exists for it, same
  reasoning as exp_enter_matchmaking.png).

chal_select.png
  Challenge's own confirm button, Solo play mode only, clicked
  (verified/retried) right before the actual "Start" click
  (core.runner._enter_challenge_stage) -- Challenge's equivalent of
  nav_select_stage.png/exp_select_stage.png.

exp_continue.png / continue_2.png
  Expedition's wave-continue flow. exp_continue.png shows up once per
  wave clear -- clicking it, then waiting for continue_2.png and clicking
  that, moves on to the next wave.

exp_extract.png / continue.png / continue_2.png / extract.png / extract_confirm.png
  Expedition's recurring checkpoint choice -- exp_extract.png shows up
  once per checkpoint, offering Extract or Continue side by side. Every
  sighting up to the task's configured "Extract After" count is declined
  (click continue.png, then wait for continue_2.png/continue.png and click
  that too, same two-step exp_continue's own flow uses, with a cooldown
  after so a laggy still-visible banner isn't miscounted as the next
  sighting); the sighting right after that is accepted (click
  exp_extract.png itself, wait for extract.png and click that). That opens
  a SECOND confirmation ("Extraction -- Are you sure you'd like to end
  this run?", its own separate red Extract/Cancel buttons, a rewards
  preview) -- extract_confirm.png is that dialog's own Extract button,
  optional/best-effort like nav_disband.png (missing just skips this step
  rather than failing). Clicking through both lands on the reward screen --
  Expedition's equivalent of victory.png. A "Select an upgrade!" level-up
  reward-card modal (select upgrade card.png) can land on top of any of
  this, or right after Victory before Repeat/Leave Stage even renders --
  dismissed with a middle-screen click wherever it might show up.

click_anywhere_to_close.png / click_anywhere_to_close_2.png
  Optional, checked every poll tick during battle ONLY on Spirit City Act
  3 (Raid) -- a boss/cutscene intro popup, clicked if found. Two visual
  variants seen in practice, tried in order (same idea as
  nav_start_game.png and friends).

upgradeable.png / not_upgradeable.png
  Used by Battle-phase Upgrade Unit blocks (core.runner._run_upgrade_unit_tick).
  After clicking a placed unit, the runner searches for whichever of these
  actually renders on its info panel: upgradeable.png means click it now;
  not_upgradeable.png (greyed out / insufficient gold / on cooldown,
  whatever this game shows) means wait and retry later instead of clicking.

cannot_place.png / max_placement_reached.png
  Place Unit block, checked right after each placement click. Both
  optional -- missing template just skips the check. max_placement_reached
  means the unit cap is hit, abandoning the whole block; cannot_place
  (matched against the LOWEST/bottommost hit on screen, since rejection
  banners can stack) means the spot itself was blocked, triggering a
  nudge-and-retry loop instead.

unit_exist.png
  Same Place Unit block, post-placement verification -- checked first, one
  retry click if it's not there yet, re-checked. Missing template just
  disables the verification step entirely.

leave_stage.png
  Used in three places: quitting to menu on Stop/F2, the first step of
  mid-task recovery, and the normal end-of-run flow when no repeats are
  left (verified/retried click that backs out to the lobby).

repeat_stage.png
  End-of-match flow when the task has repeats left (and isn't
  matchmaking) -- verified/retried click that re-queues the same stage,
  skipping the lobby/map/stage picks entirely.

return.png
  Optional. Leave Stage can bring up its own "Return to Lobby"
  confirmation instead of backing out on its own; polled briefly (not a
  one-shot check -- the popup can take a moment to animate in) right after
  every Leave Stage click and clicked if found.

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

reconnect.png / reconnect_2.png / reconnect_3.png / retry.png
  Optional. Roblox's own "Reconnect"/"Retry" prompt, shown when it actually
  disconnects -- checked every poll during a teleport-in wait, but unlike
  nav_unitmanager, finding ANY of these is treated as an immediate,
  definite disconnect (no continuous-visibility wait needed). Triggers a
  deep-link rejoin (core.runner._attempt_rejoin) -- skipped if any OTHER
  standalone Roblox window is currently open, since the deep link's own
  single-instance handling would force-close it.

nav_settings.png / nav_search.png / toggle_true.png / toggle_false.png /
nav_settings_on.png
  Used by core.runner._open_settings_search/_search_and_set_toggle to
  open Settings, search a setting by name, and read/click its on/off
  toggle. Used by any Setting block of "toggle" kind (_run_setting_block).

restart_btn.png / restart_btn2.png
  UNUSED -- leftovers from an earlier "disable Auto Vote Start + restart
  the game via Settings" flow that's since been removed (most people run
  with Auto Vote Start on deliberately, so the macro no longer fights it;
  see core.runner's party-leadership check). Kept here rather than
  deleted in case that flow ever comes back, but nothing currently
  searches for either of these.

Add more <name>.png files here as new macro steps need to recognize other
buttons/screens -- core.vision.find_image(hwnd, "<name>", ...) will pick
up any file added under this folder automatically, no code change needed
beyond the step that calls it.
