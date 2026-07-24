"""Every module-level constant (and the color-mask predicates) shared by
core/runner.py and its mixins (runner_challenge/runner_expedition/
runner_blocks) -- split out mechanically so the mixins and the main class
read one namespace. Import via star (underscore-prefixed mask functions
need naming explicitly -- star imports skip them).
"""

# Nav > Play button, in the docked game window's own client coordinates
# (top-left of the docked Roblox window == (0, 0), same convention as every
# other fixed region in this codebase, e.g. main.REWARD_SCROLLBAR_PROBE).
# Padded well past the button's own ~58x58 footprint (was searched at
# exactly that size, with zero margin for the button being even a few
# pixels off from where this assumed it'd be -- matchTemplate needs the
# whole template to fit inside the search region, so a template this size
# with no padding had nowhere to "look around" at all) -- template matching
# already scans every position within whatever region it's given, so a
# bigger region is a wider/fuzzier scan for free, not a slower or less
# precise one; the score threshold (see vision.DEFAULT_THRESHOLD) is what
# actually decides what counts as a match, not the region size.
NAV_PLAY_REGION = (44, 404, 118, 118)

# Recovery after a failed map search (see _spam_back_until_gone): repeatedly
# click Back until it's no longer found, rather than leaving the run stuck
# on whatever screen the failed search happened to end on.
BACK_SPAM_MAX_CLICKS = 8
BACK_SPAM_DELAY = 0.4
# Nested screens (map detail -> gamemode menu -> lobby) each have their own
# Back button -- a short poll after each click, not a one-shot check, so
# the NEXT screen's Back button gets a beat to render before concluding
# there isn't one and stopping a click early.
BACK_SPAM_CHECK_TIMEOUT = 1.5
# How many full lobby->Play->Story->map-search restarts to try (see
# _reach_map_selected) before giving up on this task entirely.
MAP_SELECT_RETRY_ATTEMPTS = 3
# How many times _run_task recovers to the lobby and retries a task from
# scratch after a mid-task failure (a stuck battle, a missed click, ...)
# before giving up on just that task and moving on to the next one -- so one
# bad match doesn't end an unattended overnight run.
TASK_RECOVERY_ATTEMPTS = 3

# Fail-safe: losing the SAME map this many times in a row usually means
# something's actually wrong (a bad team loadout, a stuck client, a map
# that's genuinely too hard) rather than plain bad luck -- rather than just
# keep feeding it more attempts, the run leaves the stage and forces a full
# Roblox restart (the same deep-link rejoin a detected disconnect already
# uses, see _attempt_rejoin) before retrying the task fresh.
MAX_CONSECUTIVE_LOSSES_SAME_MAP = 3

# The Story card's position on the gamemode-select screen (after Play) is
# fixed -- unlike Play itself, nothing here needs to be found by image
# search, just clicked. Raid's card sits somewhere else on the same panel;
# rather than guess a second fixed coordinate, it's found by image search
# (raid.png) instead -- its crop is a colored word on a transparent-ish
# background, distinct enough for template matching, unlike the earlier
# story.png attempt (see Assets/ui/README.txt).
STORY_CLICK = (666, 147)

LOBBY_CHECK_TIMEOUT = 15.0   # how long to wait for the Play button to appear before giving up
STORY_SCREEN_TIMEOUT = 10.0  # Play's menu (Story/Raid) animates in, not instant
BACK_CONFIRM_TIMEOUT = 8.0   # how long to wait for nav_back after clicking Story, to confirm it landed
GAMEMODE_CLICK_TIMEOUT = 8.0  # how long to search for the Raid card once the menu's open
# A perfectly-matched Play click that never opens the gamemode menu is
# exactly the "click didn't register" focus flakiness _click_play's own
# activate_window() reassertion was already added for (see its comment) --
# still reported by some users even with that fix in place, so this retries
# the whole click instead of trusting one attempt, same idea as
# SOLO_START_RETRY_ATTEMPTS below.
PLAY_CLICK_RETRY_ATTEMPTS = 3
# Same "click didn't register" flakiness, but for the actual "start the
# round" click -- previously fired once with no verification at all, so a
# dropped click here left the run sitting on the Start Game confirmation
# forever while _wait_for_match_result was already off watching for a
# Victory/Defeat that could never come.
START_GAME_CLICK_RETRY_ATTEMPTS = 3
START_GAME_CLICK_VERIFY_SETTLE = 1.0  # after clicking, how long to wait before checking it's actually gone
START_GAME_BUTTON_WAIT_TIMEOUT = 5.0  # how long to poll for Start Game right after Pre Start hands off
EXPEDITION_WAVE_TIMEOUT = 8.0  # how long to wait for Continue_2/extract after clicking exp_continue/exp_extract
# A level-up "Select an upgrade!" reward modal can be on screen at the exact
# same moment as the extract/continue choice (confirmed via a real capture:
# vision_exp_extract.png caught both up at once), auto-selecting on its own
# after ~12s -- covering/intercepting the extract click until it clears.
# EXPEDITION_WAVE_TIMEOUT alone (8s) isn't enough to wait that out, so
# "extract" specifically gets a longer allowance; exp_extract itself doesn't
# go anywhere in the meantime (see _check_expedition_wave_result), so this
# just avoids burning a couple of whole retry cycles on the same modal.
EXPEDITION_EXTRACT_CONFIRM_TIMEOUT = 16.0
EXTRACT_CONFIRM_SETTLE = 5.0  # settle after clicking "extract" -- reported as a click that can visually land without registering
EXPEDITION_CONTINUE_COOLDOWN = 5.0  # settle after exp_continue/continue_2 -- a lingering banner right after the

# ── Color-based Expedition checkpoint detection (the default engine --
# Settings > Debug > "Expedition Color Detection" toggles back to the
# template path). The checkpoint UI has exactly TWO layouts: Continue alone,
# centered, for a plain wave transition; or Extract + Continue side by side,
# symmetric about the window's vertical centerline, when the checkpoint
# offers extraction. That symmetry means ONE cheap color search answers
# everything: find the green Continue's button face in the bottom band --
# centered means "continue", pushed right means "Extract is being offered,
# and its button sits at Continue's position mirrored across the
# centerline". No Extract template, no per-variant matchTemplate sweeps;
# each check is a few ms of pixel math on a normalized reference-space
# capture (see vision.find_color_run), which also makes the between-click
# settles far cheaper to keep short. All bands are (x, y, w, h) in the
# 1152x756 reference space, so they hold on any window size/density the
# capture pipeline already normalizes (Retina included).
# Band/threshold numbers validated against real captured frames (debug/
# vision_exp_continue.png, vision_exp_extract.png): plain-wave Continue
# lands at cx≈575 (the 576 centerline), the extract-offered Continue at
# cx≈637 with the red Extract button at cx≈513 -- 2px off the mirrored
# prediction -- both at y≈584.
EXP_COLOR_CONTINUE_BAND = (288, 559, 576, 121)   # bottom band the Continue face renders in
EXP_COLOR_CONFIRM_BAND = (288, 408, 380, 121)    # where the Extract confirm dialog's red button lands
EXP_COLOR_FOLLOWUP_BAND = (380, 355, 320, 110)   # the smaller second Continue -- its real match box in
# debug/vision_Continue_2.png spans x 457-582, y 400-438 (center 519, 419); band is that plus margin
EXP_COLOR_MIRROR_MARGIN = 40      # Continue at least this far right of center = Extract offered
EXP_COLOR_CONTINUE_MIN_RUN = 60   # narrower green runs are HUD noise, not a button face
EXP_COLOR_CONFIRM_MIN_RUN = 45    # the smallest real confirm crop on file shows a 51px run
EXP_COLOR_FOLLOWUP_MIN_RUN = 24
# A checkpoint re-seen within this window is the SAME sighting, not a new
# one -- the lingering wave banner used to double-count sightings, which is
# what the template path's 5s cooldown existed to prevent; a debounce keyed
# on time-since-last-sighting prevents it without stalling the loop.
EXP_COLOR_SIGHTING_DEBOUNCE = 8.0
EXP_COLOR_CONTINUE_SETTLE = 2.0   # brief settle after the continue chain so the next tick reads a fresh frame


def _exp_green(b, g, r):
    """The checkpoint Continue button's green face -- green well above both
    other channels, so gameplay art (grass etc.) rarely qualifies and never
    in a >=EXP_COLOR_CONTINUE_MIN_RUN solid horizontal run."""
    return (g > 120) & (g > r + 45) & (g > b + 95)


def _exp_green_loose(b, g, r):
    """Looser green for the smaller follow-up Continue -- dimmer and
    narrower than the main button, so the strict face predicate can miss it."""
    return (g > 90) & (g > r + 25) & (g > b + 45)


def _exp_red(b, g, r):
    """The Extract confirm button's dark red -- red dominant over BOTH other
    channels by 2x, which the game's warm gameplay art doesn't produce in a
    solid run."""
    return (r > 90) & (r > 2 * g) & (r > 2 * b) & (g < 95) & (b < 75)


# Stage-select screen (after picking a map): a fixed vertical list of rows,
# same x for every row, y stepping by one row height per stage -- Level 1
# through 5, then Infinite, then Mastery, in that fixed order (matches
# TASK_DATA.story.stages in ui/app.js). No image search needed for the rows
# themselves, just nav_select_stage to confirm the screen has loaded before
# clicking a computed position on it.
STAGE_SCREEN_TIMEOUT = 10.0
STAGE_ORDER = ["1", "2", "3", "4", "5", "Infinite", "Mastery"]
STAGE_CLICK_BASE = (246, 230)  # Level 1's click point
STAGE_ROW_HEIGHT = 56

# Raid's stage-select screen only ever shows 3 Acts, spaced much further
# apart than Story's rows -- same screen (nav_select_stage), same confirm
# click, just a different row layout (matches TASK_DATA.raid.stages).
ACT_ORDER = ["1", "2", "3"]
ACT_CLICK_BASE = (250, 267)  # Act 1's click point
ACT_ROW_HEIGHT = 129

# Infinite/Mastery are locked to Hard in-game with no picker shown for them
# (see ui/app.js's TASK_DATA.story comment) -- no difficulty click happens
# for those stages at all, so there's nothing to look up for them here.
SPECIAL_STAGES_NO_DIFFICULTY = ("Infinite", "Mastery")

# Expedition has no stage-row picker like Story/Raid -- just a map (School
# Grounds is whatever's selected by default when the screen opens, so it has
# no reference image at all; Flower Forest/Rose Kingdom are each picked by
# image search, see EXPEDITION_MAP_IMAGES) and a difficulty stepper: one "+"
# button at a fixed spot that increments the level by 1 per click, starting
# from 1. Difficulty "2" is one click, "3" is two, "1" is none.
EXPEDITION_MAP_IMAGES = {
    "Flower Forest": "expedition_flower_forest",
    "Rose Kingdom": "expedition_rose_kingdom",
}
# Regular Challenge is Story's own flow, just with the game picking a
# random one of these 5 maps for you instead of you picking it -- so
# there's no map-select step to skip past, only a "which map did it land
# on" check once you're in. Reference images live in Assets/ui/<map>.png
# (a different folder/purpose than Assets/maps/<map>.png, which is the
# scrolling map-CARD search used to pick a map by hand -- these instead
# confirm which map is already showing). Mirrors main.py's
# CHALLENGE_STORY_MAPS and ui/app.js's TASK_DATA.story.maps.
CHALLENGE_STORY_MAPS = ["School Grounds", "Rose Kingdom", "Fairy King Forest", "King's Tomb", "Flower Forest"]
# Mirrors main.py's CHALLENGE_STAGE_SLOTS.
CHALLENGE_STAGE_SLOTS = ["1", "2", "3"]
# Fixed click points for the 3 Regular Challenge stage rows -- no image
# search needed, same idea as Story's STAGE_CLICK_BASE.
CHALLENGE_STAGE_CLICK = {"1": (460, 277), "2": (460, 400), "3": (460, 533)}
CHALLENGE_SCREEN_TIMEOUT = 10.0  # how long to wait for challenge_loaded after clicking the Challenge card
CHALLENGE_MAP_DETECT_TIMEOUT = 20.0  # how long to poll for a recognizable map after teleporting in

EXPEDITION_DIFFICULTY_CLICK = (1094, 456)
EXPEDITION_DIFFICULTY_CLICK_DELAY = 0.1  # lets each increment register before the next click

# Clicking the stage row (or the map, for Expedition) fires an animation on
# the difficulty picker that immediately clicking it can outrun -- the click
# lands before the panel/toggle has actually settled into place.
DIFFICULTY_CLICK_DELAY = 1.0

RETURN_TO_LOBBY_CHECK_TIMEOUT = 2.5  # how long to poll for the "Return to Lobby" confirmation after Leave Stage

# The stage-detail panel's Normal/Hard toggle and the Enter Matchmaking
# search region default in DEFAULT_COORDS (defined below, after the last
# click-point constant it collects) -- overridable per-run via the `coords`
# dict, sourced from Settings > Debug > Macro Coordinates.
MATCHMAKING_WAIT_TIMEOUT = 10.0
SOLO_START_TIMEOUT = 10.0  # Solo mode's direct Start button, in place of Enter Matchmaking

# Teleporting into the actual match can take a while (loading screen) --
# nav_unitmanager only renders once you're actually in-game, so waiting for
# it is the "did we teleport in" confirmation. Used for the repeat-cycle
# re-teleport (already-matched session, should be near-instant) and as
# Solo's per-attempt chunk -- see SOLO_TELEPORT_PER_ATTEMPT_TIMEOUT/
# _click_start_and_wait_teleport. NOT used for matchmaking's initial entry
# (see MATCHMAKING_TELEPORT_TIMEOUT below) -- that one's a genuinely
# different wait, not just a longer version of this one.
TELEPORT_IN_TIMEOUT = 30.0
# Clicking Enter Matchmaking doesn't teleport you in on its own -- it only
# happens once the lobby actually FILLS with real players, which can take
# anywhere from seconds to several minutes depending on server population,
# nothing like Solo's near-instant teleport. Reusing TELEPORT_IN_TIMEOUT
# (30s) here was timing this out mid-legitimate-wait almost every time,
# which looked exactly like "clicked Enter Matchmaking, then just never
# did anything else."
MATCHMAKING_TELEPORT_TIMEOUT = 300.0
SOLO_START_RETRY_ATTEMPTS = 3
SOLO_TELEPORT_PER_ATTEMPT_TIMEOUT = 20.0  # generous per chunk -- a slow teleport shouldn't burn through attempts
# How long teleportstuck.png (optional -- see Assets/ui/README.txt) must be
# CONTINUOUSLY visible during a teleport-in wait before the game is treated
# as broken and needing a rejoin, rather than just a slow loading screen.
# Roblox's own disconnect prompt (Assets/ui/reconnect/ -- several visual
# variants incl. the "Retry" wording, all in that one folder) is a DEFINITE
# signal on its own -- no continuous-visibility wait needed, unlike
# teleportstuck's spinner which can be a false alarm for a moment.
TELEPORT_STUCK_TIMEOUT = 10.0
TELEPORT_POLL_INTERVAL = 0.3
RECONNECT_IMAGE_NAMES = ("reconnect",)

# These used to be ("name", "name_2") lists of separately-named visual
# variants -- that whole mechanism now lives in the template folders
# themselves: every image in Assets/ui/<name>/ is tried as a variant of
# that one name (see vision.template_variant_paths), so each of these is
# back to a single searched name and adding another variant is "drop a
# .png in the folder" (or Settings > General > Image Manager), not a code
# change. Kept as tuples because every call site feeds them to
# vision.find_image_any/wait_for_image_any, which take a tuple of names.
NAV_PLAY_IMAGE_NAMES = ("nav_play",)
EXPEDITION_IMAGE_NAMES = ("expedition",)
CHALLENGE_IMAGE_NAMES = ("challenge",)
RAID_IMAGE_NAMES = ("raid",)
STORY_IMAGE_NAMES = ("story",)
NAV_START_IMAGE_NAMES = ("nav_start",)
NAV_DISBAND_IMAGE_NAMES = ("nav_disband",)
# 10 visual variants on file, all inside Assets/ui/priority_upgrade/ --
# every one tried per search, same folder-variant mechanism as above.
PRIORITY_UPGRADE_IMAGE_NAMES = ("priority_upgrade",)

# Roblox deep link used to rejoin after a detected disconnect -- reopens
# (or, if the client fully closed, relaunches) straight into this specific
# experience instead of leaving the run stuck on a Reconnect prompt forever.
PLACE_ID = "84515722934860"
REJOIN_DEEPLINK = f"roblox://experiences/start?placeId={PLACE_ID}"
REJOIN_TIMEOUT = 90.0  # relaunching Roblox from scratch can take a while
REJOIN_POLL_INTERVAL = 2.0

# Whether Start Game is even present depends on being the party leader, so
# this is a quick presence check, not a long wait. Short on purpose: Start
# Game (when it exists at all) reliably renders in the same beat as
# nav_unitmanager -- by the time _wait_teleport_in already confirmed
# nav_unitmanager is up, Start Game is either already there too or it was
# never going to show up, so there's nothing to gain from waiting several
# more seconds to find that out.
START_GAME_CHECK_TIMEOUT = 1.5
NAV_CLICK_TIMEOUT = 8.0  # nav_settings / nav_search in the Auto Vote Start fallback
REPEAT_STAGE_MODAL_CLEAR_TIMEOUT = 5.0  # how long to wait for the Victory/Defeat banner to actually clear after Repeat Stage
REWARD_CARD_CLEAR_TIMEOUT = 6.0  # how long to spend dismissing "select upgrade card" before Repeat/Leave Stage
SETTLE_DELAY = 0.6  # lets a panel-open animation (e.g. Settings) finish before searching it

# A warning popup can block Start Game right after Pre Start (see
# _wait_out_start_game_warning) -- waited out instead of immediately
# treating a missing nav_start_game as "already started".
WARNING_WAIT_TIMEOUT = 10.0
WARNING_POLL_INTERVAL = 1.0

# Place Unit block execution: search a small box for a valid tile by its
# pixel color (see _find_valid_place_spot), click once a valid one's found,
# then verify. Replaced the old click-first-then-check-a-rejection-image-
# and-nudge approach -- this way a click only ever fires once a genuinely
# valid tile is confirmed, instead of firing blind and finding out after.
PLACE_VALID_PIXEL_TOLERANCE = 12  # each channel allowed to be this far under 0xff (white) -- antialiasing/compression can soften a genuinely-white tile just enough to miss an exact match
PLACE_SEARCH_BOX_SIZE = 38  # side length of the region captured/scanned around the saved spot (i.e. the saved spot +/-19px each way)
PLACE_PIXEL_SEARCH_SETTLE = 0.03  # brief settle after each move before capturing
# The placement-mode highlight overlay apparently needs to actually see the
# cursor move/hover, not just land on a coordinate -- a single move then one
# capture consistently found nothing even on spots that would have been
# valid a moment later. Small back-and-forth nudges (real relative moves,
# not a static cursor) keep prodding the game's own hover state along while
# repeatedly rescanning, up to PLACE_SEARCH_WIGGLE_TIMEOUT.
PLACE_SEARCH_WIGGLE_OFFSETS = [(2, 0), (-2, 0), (0, 2), (0, -2)]
PLACE_SEARCH_WIGGLE_TIMEOUT = 2.5
# When the in-place wiggle above never sees a valid tile, the cursor walks
# OUTWARD in rings around the saved spot, rescanning at each stop (see
# _spiral_search_place_spot) -- the in-place search's 38px box can't see a
# tile the game shifted further than ~19px away, which just read as
# "giving up" on every attempt. Ring radii chosen so each stop's scan box
# overlaps the previous ring's coverage (38px box on a 24px ring step).
PLACE_SPIRAL_RADII = (24, 48, 72)
PLACE_SPIRAL_MARGIN = 20     # stops this close to the window edge are skipped -- half a scan box + slack
PLACE_SPIRAL_TIMEOUT = 8.0   # hard budget for the whole outward search
PLACE_HOTKEY_SETTLE = 0.35  # after pressing the hotkey, before the pixel search starts sampling -- the
# placement-mode overlay (what actually turns a tile white/red) needs real time to render; sampling too
# soon reads the tile's normal color instead and finds neither valid nor blocked
PLACE_UNIT_CLICK_SETTLE = 0.25   # lets the placement actually register before the next check
PLACE_UNIT_VERIFY_TIMEOUT = 2.0
PLACE_UNIT_VERIFY_ATTEMPTS = 3  # search-then-click retried up to this many times before giving up on verifying
# "Keep Placing" block toggle: re-run the WHOLE select->find->click->verify
# sequence (not just re-click a spot) until unit_exist confirms, capped so a
# genuinely-impossible placement (no gold, unit on cooldown, no valid tile
# anywhere) still moves on instead of looping forever.
PLACE_RETRY_UNTIL_PLACED_ATTEMPTS = 5
MAX_PLACEMENT_THRESHOLD = 0.85
UNIT_INFO_RESET_CLICK = (3, 3)  # near-empty corner of the Roblox screen -- closes the unit info panel after verifying
SCREEN_MIDDLE_CLICK = (576, 378)  # dead center of the 1152x756 game client area -- see FIXED_WIN_W/H in core.config

# Battle-phase Upgrade/Sell Unit blocks (see _run_battle_blocks_tick):
# selecting a unit needs a beat to actually open its info panel before the
# upgradeable/not_upgradeable search means anything.
BATTLE_BLOCK_CLICK_SETTLE = 0.3
# How long an Upgrade Unit block waits before retrying after finding
# not_upgradeable (not enough gold yet, on cooldown, ...) -- not a failure,
# just not ready, so it keeps its remaining `times` budget and tries again
# later rather than giving up or burning through a poll every second.
UPGRADE_RETRY_WAIT = 5.0
UPGRADE_PANEL_LOAD_TIMEOUT = 3.0  # how long to wait for the info panel to actually finish loading after clicking the unit

# Auto Upgrade Unit's priority menu (see _run_auto_upgrade_unit_tick):
# right-clicking "priority_upgrade" (an icon/label found on the selected
# unit's info panel) opens a context menu with Priority 1-6 stacked rows,
# then a Disable row one more row-height below Priority 6. The row
# positions are computed from priority_upgrade's OWN matched width/height
# (self-scaling if the UI ever renders at a different size) instead of a
# second set of fixed coordinates -- these multipliers are eyeballed
# proportions, not measurements off a real capture, so they're the first
# thing to adjust if the priority rows land off:
AUTO_UPGRADE_PRIORITY_ROW_HEIGHT_MULT = 1.35  # one row's height, as a multiple of priority_upgrade's own height
AUTO_UPGRADE_PRIORITY_FIRST_ROW_MULT = 1.8    # icon center down to Priority 1's row, same unit (its own height)
AUTO_UPGRADE_PRIORITY_X_OFFSET_MULT = 2.4     # icon center right to a row's click point, in multiples of its width
# Auto Upgrade Unit chains TWO nested UI transitions (select the unit ->
# its info panel opens, right-click -> the priority menu opens on top of
# that) before the priority-row click means anything -- BATTLE_BLOCK_CLICK_
# SETTLE (0.3s, tuned for Upgrade/Sell Unit's single info-panel open) was
# firing the next click before the second transition had actually
# rendered, reported as the whole block just "too fast" to work reliably.
AUTO_UPGRADE_CLICK_SETTLE = 0.6

# Team Loadout application (see _apply_team_loadout) -- H opens the panel,
# then Loadout 1-3 are stacked rows at a fixed position. 4+ exist in
# Creation's picker but aren't reachable yet without scrolling.
TEAM_PANEL_TIMEOUT = 5.0
# Clicking the Loadout row is what actually equips the team for the match --
# if Confirm never shows up afterward (a dropped click, same flakiness class
# as START_GAME_CLICK_RETRY_ATTEMPTS/SOLO_START_RETRY_ATTEMPTS), the run
# must NOT just carry on into Start Game with no team applied (a guaranteed
# loss, confirmed from a real report) -- retried instead, up to this many
# attempts, before actually giving up and failing Pre Start over it.
TEAM_LOADOUT_CONFIRM_RETRY_ATTEMPTS = 3
TEAM_LOADOUT_CLICK_1 = (800, 324)  # Loadout 1's row
TEAM_LOADOUT_ROW_HEIGHT = 126
TEAM_LOADOUT_MAX_SUPPORTED = 8
# Loadouts 4-8 need the list scrolled first -- a click-drag on the list
# itself (not a wheel scroll), same anchor point for both scroll sizes.
# The smaller drag (~1 row height) reveals 4-6 at the SAME row positions
# the 1-3 formula above already computes (the list just shifts to put
# them there); the larger drag re-anchors the list differently, landing 7
# and 8 at their own fixed positions instead of following that formula.
TEAM_LOADOUT_SCROLL_ANCHOR = (895, 285)
TEAM_LOADOUT_SCROLL_SMALL = 124  # reaches Loadout 4-6
TEAM_LOADOUT_SCROLL_LARGE = 300  # reaches Loadout 7-8
TEAM_LOADOUT_SLOT_7_Y = 410
TEAM_LOADOUT_SLOT_8_Y = 536
TEAM_LOADOUT_SCROLL_SETTLE = 0.3  # lets the drag's scroll animation finish before clicking a row

# Wait for Wave (see _run_wait_wave_tick) -- the "<current> / <max> wave"
# HUD badge, in the docked game window's own client coordinates.
WAVE_REGION = (467, 21, 104, 61)
# OCR here is several real Tesseract subprocess spawns (see core.wave/
# core.ocr's multi-mask sweep) -- checked on this cadence, not every single
# Battle-tick poll, so a long wait for a distant wave doesn't spend most of
# its time re-running OCR against a number that hasn't changed yet.
WAIT_WAVE_POLL_INTERVAL = 2.0

# Fallbacks if main.py doesn't pass real calibrated regions through (mirrors
# main.py's REWARD_REGION_DEFAULTS/STATS_REGION_DEFAULTS).
DEFAULT_REWARD_REGION = {"x": 212, "y": 429, "width": 504, "height": 106}
DEFAULT_STATS_REGION = {"x": 210, "y": 337, "width": 509, "height": 57}

# EVERY fixed click point/row layout the runner uses, as overridable
# settings (Settings > Debug > Macro Coordinates -- mirrors main.py's
# MACRO_COORD_DEFAULTS): a game update shifting any of these needs a number
# changed (or re-picked from a screenshot) in Settings, not a code change.
# Values come from the tuple constants above where one exists -- those stay
# the documented single source of each default; this dict is the runtime
# override surface (merged with the user's saved values in _run, read via
# self._coords/_cxy). All in the docked window's 1152x756 client space.
DEFAULT_COORDS = {
    "difficulty_normal_x": 311, "difficulty_normal_y": 315,
    "difficulty_hard_x": 364, "difficulty_hard_y": 315,
    "matchmaking_region_x": 277, "matchmaking_region_y": 543,
    "matchmaking_region_w": 437, "matchmaking_region_h": 45,
    "story_click_x": STORY_CLICK[0], "story_click_y": STORY_CLICK[1],
    "stage_row_x": STAGE_CLICK_BASE[0], "stage_row_y": STAGE_CLICK_BASE[1],
    "stage_row_height": STAGE_ROW_HEIGHT,
    "act_row_x": ACT_CLICK_BASE[0], "act_row_y": ACT_CLICK_BASE[1],
    "act_row_height": ACT_ROW_HEIGHT,
    "challenge_stage_1_x": CHALLENGE_STAGE_CLICK["1"][0], "challenge_stage_1_y": CHALLENGE_STAGE_CLICK["1"][1],
    "challenge_stage_2_x": CHALLENGE_STAGE_CLICK["2"][0], "challenge_stage_2_y": CHALLENGE_STAGE_CLICK["2"][1],
    "challenge_stage_3_x": CHALLENGE_STAGE_CLICK["3"][0], "challenge_stage_3_y": CHALLENGE_STAGE_CLICK["3"][1],
    "expedition_difficulty_x": EXPEDITION_DIFFICULTY_CLICK[0], "expedition_difficulty_y": EXPEDITION_DIFFICULTY_CLICK[1],
    "team_loadout_x": TEAM_LOADOUT_CLICK_1[0], "team_loadout_y": TEAM_LOADOUT_CLICK_1[1],
    "team_loadout_row_height": TEAM_LOADOUT_ROW_HEIGHT,
    "screen_middle_x": SCREEN_MIDDLE_CLICK[0], "screen_middle_y": SCREEN_MIDDLE_CLICK[1],
    "unit_info_reset_x": UNIT_INFO_RESET_CLICK[0], "unit_info_reset_y": UNIT_INFO_RESET_CLICK[1],
}

# Victory/Defeat: no fixed timeout makes sense for "how long can a battle
# run", so this is a generous safety net (30 min), not an expected duration --
# polled slowly since there's no rush to notice a screen that, once it
# appears, just sits there until acted on.
MATCH_RESULT_TIMEOUT = 1800.0
MATCH_RESULT_POLL_INTERVAL = 1.0


