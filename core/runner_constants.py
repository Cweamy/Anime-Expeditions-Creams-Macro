"""Every module-level constant (and the color-mask predicates) shared by
core/runner.py and its mixins (runner_challenge/runner_expedition/
runner_blocks) -- split out mechanically so the mixins and the main class
read one namespace. Import via star (underscore-prefixed mask functions
need naming explicitly -- star imports skip them).
"""

# Nav > Play button: searched FULL-WINDOW now (no fixed region). It used to
# be boxed to the left nav strip (NAV_PLAY_REGION) as a speed/false-match
# optimization, but that assumed the button always lands in one exact spot --
# a layout shift (different window size, cutout mode, a game UI update) could
# put it outside the box and make every lobby check fail. Full-window is a
# wider scan but template matching still only accepts a real match by score
# (see vision.DEFAULT_THRESHOLD), so it's more robust for the same result.

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

# Optional full-client refresh for long unattended runs. The runner only
# checks this at a completed-match boundary, so it never adds a polling loop
# or competes with the capture/input path while a match is active.
MEMORY_REFRESH_DEFAULT_HOURS = 4.0
MEMORY_REFRESH_MIN_HOURS = 1.0
MEMORY_REFRESH_MAX_HOURS = 12.0

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
# A gamemode click can be misdirected onto a nearby player/invite control.
# Check immediately afterward and, if that opened a party overlay, dismiss
# it and retry the exact intended card instead of claiming navigation worked.
GAMEMODE_OVERLAY_CHECK_DELAY = 0.5
GAMEMODE_OVERLAY_RETRY_ATTEMPTS = 2
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
# ── Native Expedition encounter handling (_handle_expedition_encounter).
# An encounter node parks the client somewhere a match result can never come
# from. Recovering means: reset position, walk to that map's NPC, talk to it.
# All coordinates are in the 1152x756 reference space, measured on a real
# client -- the same kind of constant as STORY_CLICK. Where an image exists for
# a step it is used instead (nav_settings / nav_closeui are already shipped),
# because an image survives a layout shift and a coordinate does not.
ENCOUNTER_REGION = (414, 58, 41, 45)      # the encounter marker's HUD slot
ENCOUNTER_TELEPORT_SPAWN_CLICK = (621, 443)  # "teleport to spawn" inside Settings
# Dialogue advance clicks, in order. The prompt is opened with E; these step
# through the exchange that follows.
ENCOUNTER_DIALOGUE_CLICKS = ((403, 663), (581, 577), (667, 665), (581, 577))
ENCOUNTER_STEP_SETTLE = 0.3        # between UI steps, so each registers
ENCOUNTER_ARRIVE_SETTLE = 1.5      # after the walk, before looking for the prompt
ENCOUNTER_SPEAK_TIMEOUT = 4.0      # how long to wait for the interact prompt
# Opening Settings is the step most likely to be blocked: a level-up "Select an
# upgrade!" modal renders over the gear, so the search legitimately fails while
# one is up. Longer than the others, and the modal is cleared first.
ENCOUNTER_SETTINGS_TIMEOUT = 6.0
# A level-up "Select an upgrade!" modal covers the settings gear, and several
# can queue up back to back after a wave. Clearing one and pressing on is not
# enough -- the next is already rendering. Wait until none is left, up to this
# long, dismissing each as it appears. Bounded rather than a flat sleep, so a
# run with nothing blocking pays nothing.
ENCOUNTER_MODAL_CLEAR_TIMEOUT = 12.0
ENCOUNTER_MODAL_POLL = 0.4
# Closing Settings took more than one click in practice: observed closing on
# the second attempt roughly as often as the first.
ENCOUNTER_CLOSE_ATTEMPTS = 3
# The dialogue is several boxes, not one. Firing the click sequence once and
# moving on left the run standing in an open box, so it repeats until the
# interact prompt is gone -- bounded, because a dialogue that never clears is
# a different problem and should not loop forever.
ENCOUNTER_DIALOGUE_ROUNDS = 3
# One second between dialogue clicks, matching the spacing the template version
# actually ran at: each Click block there advances on its own battle tick, and
# MATCH_RESULT_POLL_INTERVAL is 1.0s. Firing them back to back instead landed
# clicks on a box that had not advanced yet -- same coordinates, wrong pace.
ENCOUNTER_DIALOGUE_CLICK_GAP = 1.0
# Two separate pauses, because they wait on different things.
#
# The first is before ANY of the menu work: the encounter has just appeared,
# the wave that triggered it is still resolving, and level-up cards are still
# queueing. Reaching for Settings into that is what produced the alternating
# "nav_settings not found" / "Settings is still open" failures.
#
# DEFERRED, never slept: the handler returns and lets the caller's poll loop
# carry on picking upgrade cards and clicking wave Continues, and only starts
# the menu once this much has passed since the icon first appeared. A blocking
# wait here froze the whole run -- reward cards auto-selected untouched.
ENCOUNTER_PRE_MENU_SETTLE = 20.0
# The second is after the teleport, before replaying the route: the world is
# reloading around the player and keys pressed through that are lost, so the
# route would start part-way in and land short of the NPC. Short, because the
# 20s above has already absorbed the encounter settling.
ENCOUNTER_TELEPORT_SETTLE = 3.0
# Do not re-enter while the marker is still fading, and never twice in a row
# for one encounter.
ENCOUNTER_COOLDOWN = 20.0

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
# How long a checkpoint may stay up, being re-found and re-clicked on every
# poll, before the run is treated as stalled rather than progressing. Every
# individual step of the checkpoint chain is already bounded, but nothing
# noticed the WHOLE chain repeating: a Continue that never clears is
# re-clicked every poll, and the only escape was MATCH_RESULT_TIMEOUT half
# an hour later. A healthy run cannot trip this -- waves are minutes apart,
# so the polls between two checkpoints find no Continue at all and reset
# the clock. Only a checkpoint that never clears keeps it running.
# Measured in elapsed time rather than poll count so it means the same
# thing regardless of how long each retry cycle happens to take.
EXPEDITION_STALL_TIMEOUT = 300.0
# A Start Game popup or a level-up reward card is handled BEFORE the
# checkpoint is looked at, and that poll returns early -- so those polls see
# no checkpoint either way. They must not age the stall clock above (the
# checkpoint may have cleared while they were in the way, unobserved), but a
# run that never gets past them is not progressing either, so they get their
# own cap rather than resetting anything. Kept separate so the log can say
# which of the two actually happened.
EXPEDITION_INTERCEPT_TIMEOUT = 300.0

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
# Infinite has been observed in two different layouts at the same normalized
# 1152x756 viewport: one where the legacy fixed click is centered, and a
# roughly 10%-larger panel where that X lands outside the row. Locate its
# distinctive infinity glyph inside the left stage column instead.
STORY_STAGE_VISUAL_IMAGES = {"Infinite": "stage_infinite"}
STORY_STAGE_SEARCH_REGION = (140, 120, 200, 560)
STORY_STAGE_MATCH_THRESHOLD = 0.82
STORY_STAGE_CLICK_ATTEMPTS = 3
STORY_STAGE_SELECTED_VERIFY_TIMEOUT = 2.0
STORY_STAGE_SELECTED_VERIFY_POLL = 0.15
STORY_STAGE_SELECTED_SAMPLE_HALF_SIZE = 24
STORY_STAGE_SELECTED_BLUE_MIN_FRACTION = 0.30

# Raid's stage-select screen only ever shows 3 Acts, spaced much further
# apart than Story's rows -- same screen (nav_select_stage), same confirm
# click, just a different row layout (matches TASK_DATA.raid.stages).
ACT_ORDER = ["1", "2", "3"]
ACT_CLICK_BASE = (250, 267)  # Act 1's click point
ACT_ROW_HEIGHT = 129

# Event mode: reached straight from the lobby via its own nav_event button
# (NOT through Play like Story/Raid/Expedition/Challenge), then the
# event_gamemode card, then one of the Act cards (each a villain). There's no
# map carousel and no difficulty picker -- picking the Act IS the whole
# selection, so it goes straight from the Act to the Solo/Matchmaking tail
# (nav_select_stage + nav_start, or enter_matchmaking) the other modes share.
# The image folder names are exactly as they ship under Assets/ui/ -- the
# mixed "villian"/"villain" spelling is intentional, it matches the real
# folders. Mirrors TASK_DATA.event.stages in ui/app.js.
# Act 4 (Villian Invasion "Crow - Dawn") is a relic-gated Act: it costs 1 Crow
# Relic to enter, so its card shows locked ("0/1x Owned", VILLIAN4_CLOSE_IMAGE)
# until you've banked one. It's selectable now, and farm tasks can auto-divert
# to it when a relic drops (see runner._run_act4_diversion / DROP_RELIC_IMAGE).
EVENT_ACT_ORDER = ["1", "2", "3", "4"]
# Values are a tuple of candidate crops per Act (any match wins), so an Act
# card that shows in more than one visual state can be matched in whichever
# it's currently in.
EVENT_ACT_IMAGES = {
    "1": ("villian1",),
    "2": ("villian2",),
    "3": ("villain3",),
    "4": ("villian4",),
}
# Acts from this one on can sit below the fold on the Event gamemode screen
# and only come into view by scrolling the villain list -- picking one of
# these runs the same wheel-scroll search Story maps use (see
# _reach_event_act_selected / _scroll_find_and_click). Acts before it are
# already on screen and get a plain wait-then-click. The scroll search checks
# what's already visible first, so it's a no-op for an Act that didn't need
# scrolling anyway.
EVENT_ACT_SCROLL_FROM_INDEX = 2  # 0-based into EVENT_ACT_ORDER: index 2 == Act "3"
EVENT_SCREEN_TIMEOUT = 10.0  # how long to wait for each Event screen (nav_event / event_gamemode / the Act card) to appear

# Tournament mode: reached through Play like Story/Raid -- its nav_tournament
# button sits on the same gamemode menu (picked instead of Story), NOT via its
# own lobby entry the way Event's nav_event is. After nav_tournament comes a
# type card, then the nav_entertournament confirm and the shared solo Start/
# teleport tail (nav_start). There's no map carousel and no difficulty picker --
# picking the type IS the whole selection. The chosen type string is stored in the task's
# `map` field (mirrors TASK_DATA.tournament.maps in ui/app.js), so it also
# shows verbatim in the logs, Status Readout, and match webhook. Each type maps
# to its own on-screen button image; add a new (type -> image) pair here and
# the matching entry to TASK_DATA.tournament.maps to offer another type. The
# image folder names ship under Assets/ui/ exactly as written below.
TOURNAMENT_TYPE_ORDER = ["Solo Tournament"]
# Values are a tuple of candidate crops per type (any match wins), same shape
# as EVENT_ACT_IMAGES, so a card shown in more than one visual state can still
# be matched.
TOURNAMENT_TYPE_IMAGES = {
    "Solo Tournament": ("solo_tournament",),
}
TOURNAMENT_SCREEN_TIMEOUT = 10.0  # how long to wait for each Tournament screen (nav_tournament / the type card / nav_entertournament) to appear
TOWER_SCREEN_TIMEOUT = 10.0  # how long to wait for each Tower screen (nav_tower / Traitless_Tower / nav_select_stage) to appear

# Auto Bounty derives all objective clicks from the live board. These values
# only bound waits and the board's outer scroll gesture.
BOUNTY_SCREEN_TIMEOUT = 10.0
BOUNTY_DESTINATION_TIMEOUT = 10.0
BOUNTY_NAV_CLICK_ATTEMPTS = 3
BOUNTY_NAV_CLICK_VERIFY_TIMEOUT = 4.0
BOUNTY_CLICK_FOCUS_SETTLE = 0.2
BOUNTY_OBJECTIVE_FAILURE_ATTEMPTS = 3
BOUNTY_MAX_CLAIMS_PER_START = 12
BOUNTY_SCROLL_HOVER = (720, 650)
BOUNTY_HORIZONTAL_WHEEL_DELTA = -360
BOUNTY_HORIZONTAL_SCROLL_STEPS = 8
BOUNTY_SCROLL_SETTLE = 0.45
BOUNTY_MAX_OBJECTIVES_PER_START = 10
BOUNTY_SUMMON_BATCH_SIZE = 50
BOUNTY_SUMMON_MAX_BATCHES_PER_START = 20
BOUNTY_SUMMON_NAV_TIMEOUT = 12.0
BOUNTY_SUMMON_ANIMATION_DELAY = 3.0
BOUNTY_SUMMON_MENU_SETTLE = 1.5
BOUNTY_MYTHIC_DEFAULT_REROLLS = 20
BOUNTY_MYTHIC_MIN_REROLLS = 1
BOUNTY_MYTHIC_MAX_REROLLS = 100
BOUNTY_MYTHIC_REROLL_SETTLE = 0.8
BOUNTY_MYTHIC_REROLL_VERIFY_TIMEOUT = 4.0
BOUNTY_MYTHIC_REROLL_POLL = 0.25

# Villian Invasion Act 4 ("Crow - Dawn") relic gate. DROP_RELIC_IMAGE is the
# Crow Relic reward shown on the Victory screen (relics only drop on a win) --
# spotting it is what triggers a farm task's optional auto-divert to Act 4.
# VILLIAN4_CLOSE_IMAGE is Act 4's locked card ("requires 1 Crow Relic / 0/1x
# Owned"); seeing it means there's no relic to spend, so the divert backs out.
DROP_RELIC_IMAGE = "drop_relic"
VILLIAN4_CLOSE_IMAGE = "villian4_close"
EVENT_ACT4_STAGE = "4"

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
    "East Town": "expedition_east_town",
}
# Regular Challenge is Story's own flow, just with the game picking a
# random one of these maps for you instead of you picking it -- so
# there's no map-select step to skip past, only a "which map did it land
# on" check once you're in. A map missing from this list is simply never
# recognized, so the run stalls on CHALLENGE_MAP_DETECT_TIMEOUT after
# teleporting in. Reference images live in Assets/ui/<map>.png
# (a different folder/purpose than Assets/maps/<map>.png, which is the
# scrolling map-CARD search used to pick a map by hand -- these instead
# confirm which map is already showing). Mirrors main.py's
# CHALLENGE_STORY_MAPS and ui/app.js's TASK_DATA.story.maps.
CHALLENGE_STORY_MAPS = ["School Grounds", "Rose Kingdom", "Fairy King Forest", "King's Tomb", "Flower Forest", "East Town"]
# Daily Challenge shows its map as a ~10px label rather than the art the
# image search above needs, so _detect_challenge_map_ocr falls back to
# reading it. One distinctive lowercase word per map, fuzzy-matched against
# the OCRed tokens -- a map missing an alias can never be named by that
# fallback, so this has to cover CHALLENGE_STORY_MAPS entirely.
# East Town is keyed on "east" rather than "town" deliberately: "town" and
# "tomb" score about equally against a garbled read of either, which pushes
# both below the runner-up margin and makes King's Tomb undetectable as
# collateral (test_challenge_map_ocr_uses_unique_map_words covers that read).
# Pick the word that no other map shares, not just any word from the name.
CHALLENGE_MAP_OCR_ALIASES = {
    "School Grounds": "grounds",
    "Rose Kingdom": "kingdom",
    "Fairy King Forest": "fairy",
    "King's Tomb": "tomb",
    "Flower Forest": "flower",
    "East Town": "east",
}
# Words the map label carries that never identify a map ("Grounds - Act 1").
# Scored against an alias they are just noise that can out-rank the real
# match, so they are dropped before comparison.
CHALLENGE_MAP_OCR_STOPWORDS = frozenset({"act", "stage", "challenge", "daily"})
# Mirrors main.py's CHALLENGE_STAGE_SLOTS.
CHALLENGE_STAGE_SLOTS = ["1", "2", "3"]
# Fixed click points for the 3 Regular Challenge stage rows -- no image
# search needed, same idea as Story's STAGE_CLICK_BASE.
CHALLENGE_STAGE_CLICK = {"1": (460, 277), "2": (460, 400), "3": (460, 533)}
CHALLENGE_SCREEN_TIMEOUT = 10.0  # how long to wait for challenge_loaded after clicking the Challenge card
CHALLENGE_MAP_DETECT_TIMEOUT = 20.0  # how long to poll for a recognizable map after teleporting in

EXPEDITION_DIFFICULTY_CLICK = (441, 524)
EXPEDITION_DIFFICULTY_CLICK_DELAY = 0.1  # lets each increment register before the next click

# Clicking the stage row (or the map, for Expedition) fires an animation on
# the difficulty picker that immediately clicking it can outrun -- the click
# lands before the panel/toggle has actually settled into place.
DIFFICULTY_CLICK_DELAY = 1.0

RETURN_TO_LOBBY_CHECK_TIMEOUT = 2.5  # how long to poll for the "Return to Lobby" confirmation after Leave Stage
RETURN_TO_LOBBY_CLICK_RETRY_ATTEMPTS = 3
RETURN_TO_LOBBY_VERIFY_SETTLE = 1.0

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
# nav_unitmanager (the "teleport finished" confirmation above) is a HUD
# element -- it can render before the character/camera controller has
# actually finished attaching to the freshly-spawned avatar, which the Pre
# Start camera drag doesn't wait on or verify at all (it's a blind
# right-click-and-move sequence). Reported live, rarely: the camera drag
# fires a beat too early and the right-click-drag/scroll doesn't register
# that time. This settle is the fix -- see _run_prestart.
CAMERA_SETUP_SETTLE = 0.6
# The same "nav_unitmanager is up but the world isn't ready" problem, on the
# Repeat Stage path. A first entry gets CAMERA_SETUP_SETTLE plus the camera
# drag itself (a 730ms hold and its O taps) plus Team Loadout before any
# unit is placed -- seconds of incidental settling. A repeat skips all
# three and goes straight from "Teleported in-game" to Place Unit, with
# nothing between them.
#
# What that looks like when it goes wrong: every unit in the template
# aligning to the SAME large offset, out at the edge of the 38px search box
# (reported live: four units all at (18, 17), placing the whole team ~16px
# off). A uniformly displaced view is exactly what a map still settling
# into place reads as. Placement is the only Pre Start step that reads
# pixels off the world, so it is the one that notices.
REPEAT_ENTRY_SETTLE = 5.0
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
# Search region for the gamemode select menu cards (Story, Raid, Challenge, Expedition):
# restricted to the right-side cards panel (x: 440..1152) to exclude the left 3D viewport
# where player silhouettes and party [+] invite buttons render.
GAMEMODE_CARD_REGION = (440, 0, 712, 756)
# ...but only as the FIRST attempt. The box assumes a fixed card layout, and
# the menu keeps gaining cards (Tower and Event in v0.19.0), so a mode can end
# up rendering outside it -- reported as the run repeatedly clicking Play and
# then "Expedition never showed up". A boxed miss now retries against the whole
# window for this long before the task is failed (see _find_gamemode_card).
# Shorter than the boxed attempt: by this point the menu is known to be open,
# so the card is either visible or genuinely absent.
GAMEMODE_CARD_WIDE_TIMEOUT = 5.0

# Mid-match lobby re-sync: nav_play only renders on the lobby, so seeing it
# from inside a match means we are not in one any more -- someone clicked
# Return to Lobby by hand, or the game ejected us. Confirmed over this many
# consecutive polls before acting, since aborting a live match is expensive
# and one frame caught mid-transition is not worth acting on.
LOBBY_RESYNC_CONFIRMATIONS = 2
# The same "are we actually on the lobby" question during a teleport wait, but
# that loop can run for five minutes on matchmaking and polls fast, so the
# check runs every Nth poll rather than every one -- a full-window search per
# tick would be real cost for a state that does not change that quickly.
# Counted in polls rather than seconds deliberately: the wait's timing is
# asserted tick-by-tick (see tests/test_runner_teleport.py), and reading the
# clock again here would change that shape for a rate limit that does not need
# wall time to be correct.
LOBBY_CHECK_EVERY_N_POLLS = 6

# AFK Chamber: an Expedition encounter node can drop the client in here, and
# nothing about it reads as a disconnect or a lobby -- so the runner sat
# polling a screen that can never show Victory/Defeat until MATCH_RESULT_
# TIMEOUT, once per node, for the rest of the run.
# The banner is a fixed HUD element at the top centre, so it gets a band
# rather than a full-window scan: this check runs on EVERY result poll, and a
# whole-window template sweep at that rate is not worth it for a title that
# does not move. Optional, like nav_disband -- no afk_chamber.png just skips.
AFK_CHAMBER_REGION = (451, 30, 258, 38)
# The exit sits below the banner, at a fixed spot in the 1152x756 reference
# space -- same kind of measured constant as STORY_CLICK.
AFK_CHAMBER_EXIT_CLICK = (660, 716)
# The banner lingers while the exit animates, so re-clicking every poll would
# fight the transition the first click already started.
AFK_CHAMBER_CLICK_COOLDOWN = 5.0

NAV_PLAY_IMAGE_NAMES = ("nav_play",)
EXPEDITION_IMAGE_NAMES = ("expedition",)
CHALLENGE_IMAGE_NAMES = ("challenge",)
RAID_IMAGE_NAMES = ("raid",)
STORY_IMAGE_NAMES = ("story",)
NAV_START_IMAGE_NAMES = ("nav_start",)
NAV_DISBAND_IMAGE_NAMES = ("nav_disband",)
PARTY_OVERLAY_IMAGE_NAMES = NAV_DISBAND_IMAGE_NAMES + ("invite_players_open",)
# Modals that cover the LOBBY rather than the gamemode menu -- the Update Log
# shown after a game update or a fresh login is the common one. Play renders
# behind it and still matches, so the click is found and lands on the modal
# instead: observed as three "nav_back not found -- still on the lobby,
# re-clicking Play" retries in a row while the patch notes sat on screen.
# Optional like nav_disband: no image means the check does nothing.
LOBBY_OVERLAY_CLOSE_IMAGE_NAMES = ("update_log_close",)
# 10 visual variants on file, all inside Assets/ui/priority_upgrade/ --
# every one tried per search, same folder-variant mechanism as above.
PRIORITY_UPGRADE_IMAGE_NAMES = ("priority_upgrade",)

# Roblox deep link used to rejoin after a detected disconnect -- reopens
# (or, if the client fully closed, relaunches) straight into this specific
# experience instead of leaving the run stuck on a Reconnect prompt forever.
PLACE_ID = "84515722934860"
REJOIN_DEEPLINK = f"roblox://experiences/start?placeId={PLACE_ID}"

# Project links surfaced as link buttons on the match-result webhook (see
# runner._send_result_webhook) -- the community Discord, the source repo,
# and the creator's YouTube.
DISCORD_INVITE_URL = "https://discord.gg/cgua6CZDst"
GITHUB_REPO_URL = "https://github.com/Cweamy/Anime-Expeditions-Creams-Macro"
YOUTUBE_URL = "https://www.youtube.com/@Cweamya"
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
# Character/reward portraits on Victory can open a centered Obtainments
# modal. Its Close button stays in this middle-bottom band even when the
# underlying result panel and hotbar shift slightly.
RESULT_MODAL_CLOSE_REGION = (350, 500, 450, 110)
# The Victory/Defeat panel's reward row streams its items in one at a time, so
# the screenshot (and the Crow Relic drop check) used to be able to fire before
# they'd all rendered. A short settle lets the row finish populating first.
RESULT_CAPTURE_DELAY = 1.0
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
# Consecutive "not upgradeable" reads before giving up on a unit. At
# UPGRADE_RETRY_WAIT apart that is roughly 100 seconds -- long enough to
# ride out a slow-gold stretch, short enough that a maxed unit does not eat
# the rest of the match. Reset by any successful upgrade.
UPGRADE_MAX_IDLE_ATTEMPTS = 20

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
# The priority control cycles on each left click and clears on a press-hold
# -- it is not a menu (see _run_auto_upgrade_unit_tick). Six is the highest
# priority the game offers, so asking for more can only over-cycle.
AUTO_UPGRADE_MAX_PRIORITY = 6
AUTO_UPGRADE_STEP_DELAY = 0.18   # between cycling clicks, so each registers
AUTO_UPGRADE_CLEAR_HOLD = 1.0    # press-and-hold that clears it back to off

AUTO_UPGRADE_CLICK_SETTLE = 0.6

# Click input searches for priority_upgrade after AUTO_UPGRADE_CLICK_SETTLE.
# That single check was reported missing panels that were merely slow: a unit
# placed as a wave spawns can still be rendering its info panel at 0.6s, and
# the block logged "not found -- skipping" against a panel that appeared a
# moment later. Poll to a deadline instead, the same shape _run_upgrade_unit_
# tick already uses for upgradeable/not_upgradeable (UPGRADE_PANEL_LOAD_
# TIMEOUT above) -- a panel that is already up still costs one search, so the
# common case is unchanged. Hotkey input deliberately never searches at all
# and is untouched by this.
AUTO_UPGRADE_PANEL_LOAD_TIMEOUT = 3.0
AUTO_UPGRADE_PANEL_POLL_INTERVAL = 0.15

# Team Loadout application (see _apply_team_loadout) -- H opens the panel,
# then Loadout 1-3 are stacked rows at a fixed position. 4+ exist in
# Creation's picker but aren't reachable yet without scrolling.
TEAM_PANEL_TIMEOUT = 5.0
# The first "Teams" click only opens the Load Team list; it does not select
# anything. Do not scroll/click a row until the list's own title is visible.
# A missed Teams click is retried as a Teams click, rather than incorrectly
# repeating row clicks against the still-closed Unit Manager screen.
TEAM_LOADOUT_OPEN_RETRY_ATTEMPTS = 3
TEAM_LOADOUT_OPEN_THRESHOLD = 0.85
TEAM_LOADOUT_OPEN_SETTLE = 0.5
# Clicking the Loadout row is what actually equips the team for the match --
# if Confirm never shows up afterward (a dropped click, same flakiness class
# as START_GAME_CLICK_RETRY_ATTEMPTS/SOLO_START_RETRY_ATTEMPTS), the run
# must NOT just carry on into Start Game with no team applied (a guaranteed
# loss, confirmed from a real report) -- retried instead, up to this many
# attempts, before actually giving up and failing Pre Start over it.
TEAM_LOADOUT_CONFIRM_RETRY_ATTEMPTS = 3
# Clicking a Loadout row makes the Confirm button SLIDE UP into place, and
# it's still animating for a beat afterward -- searching for "confirm"
# immediately finds it mid-slide, so the click lands where the button WAS a
# fraction of a second ago instead of where it comes to rest, missing it
# entirely (verified from a real test: a 2s wait here fixed it). Settle here
# first so the button has stopped moving before it's located, so the found
# position is the final one.
TEAM_LOADOUT_CONFIRM_SETTLE = 2.0
TEAM_LOADOUT_CLICK_1 = (800, 324)  # Loadout 1's row
TEAM_LOADOUT_ROW_HEIGHT = 126
TEAM_LOADOUT_MAX_SUPPORTED = 8
# The configured X is the old button-edge coordinate. The current green
# Load Team button spans roughly x=792..911; move 50px inward so the click
# lands at its center instead of on a bevel that intermittently ignores it.
TEAM_LOADOUT_BUTTON_CENTER_X_OFFSET = 50
# Loadouts 4-8: hover the actual scrollbar (not a row button) and wheel down.
# Live 1152x756 measurements: each notch shifts the list by 100px and current
# rows are 137px apart. Seven notches reaches the bottom; the last slot's
# button is clipped by the panel but remains clickable at y=579.
TEAM_LOADOUT_SCROLLBAR_HOVER = (927, 400)
TEAM_LOADOUT_WHEEL_DELTA = -120
TEAM_LOADOUT_WHEEL_MAX_STEPS = 7
TEAM_LOADOUT_WHEEL_ROW_SHIFT = 100
TEAM_LOADOUT_CURRENT_ROW_HEIGHT = 137
TEAM_LOADOUT_VISIBLE_BOTTOM_Y = 579
TEAM_LOADOUT_SCROLL_HOVER_SETTLE = 0.08
TEAM_LOADOUT_WHEEL_INTERVAL = 0.15
TEAM_LOADOUT_SCROLL_SETTLE = 0.5

# Wait for Wave (see _run_wait_wave_tick) -- the "<current> / <max> wave"
# HUD badge, in the docked game window's own client coordinates.
WAVE_REGION = (467, 21, 104, 61)
# Expedition puts the same badge somewhere else, and the box above does not
# reach it: it starts 50px right of where Expedition renders the badge, so
# "3 / 5 wave" is captured as just "5 wave". read_wave reports NO MAXIMUM for
# slash-free text -- Infinite's HUD genuinely is "6 wave" -- so a finite run
# comes back as "5 (unlimited)", the maximum read as the current. A Wait for
# Wave block then unblocks on wave 1 while logging that it reached wave 5,
# and every block behind it runs early. read_wave's own preference for
# slash-bearing votes cannot rescue that: with the slash outside the crop,
# every vote is current-only.
#
# It is also 61px tall against a 33px badge, reaching into the
# "<n> / <max> units" chip underneath -- the same digits-and-slash shape,
# feeding a second number to the same parse.
#
# Measured on a live Expedition frame with the Image Manager's region tool.
# Only Expedition is changed; the shared box above is left exactly as it is,
# since it is what Story/Raid/Infinite have been reading correctly.
EXPEDITION_WAVE_REGION = (417, 16, 110, 33)
# Not every Expedition gamemode HAS waves. The payload modes count enemies
# around the objective instead, and their HUD shows "<n> enemies" where a
# wave badge would be -- so a Wait for Wave block there waits on a number
# that will never exist, and every block behind it (the placements it was
# put in front of) never runs at all.
#
# A level-up "Select an upgrade!" card is proof the battle is genuinely
# under way: they are handed out for kills, so one cannot appear before the
# fighting starts. On Expedition, that is accepted as the release condition
# when the badge cannot be read -- after a short settle, so the block does
# not fire on the same tick the card is still being clicked through.
# Story/Raid keep waiting for a real number; their badge always exists, and
# an unreadable one there means a detection problem worth surfacing rather
# than working around.
# A QUIET PERIOD, not a countdown from the first card -- every fresh
# disruption restarts it, deliberately. A card means the round is still
# churning; a mid-run Start Game means it is re-staging and the units have
# just run off the board. Placing into either is what this exists to avoid,
# so the clock measures "nothing has happened for a while" rather than "some
# time has passed since the battle began".
#
# Waiting costs nothing: the poll loop keeps playing the match -- taking
# cards, clicking Continues, handling encounters -- the entire time.
#
# The trade-off, stated plainly: on a run where cards keep arriving closer
# together than this, the wait never releases and the deferred placements
# never happen. MATCH_RESULT_TIMEOUT is the only backstop.
WAIT_WAVE_NO_COUNTER_SETTLE = 20.0
# A mid-run "Start Game?" stages a new sub-round, and the units already
# placed run off the board entirely -- their tiles free up, so the Battle
# phase is replayed from the top to put them back.
#
# Once per MATCH, on that match's FIRST Start Game only -- and every repeat
# of the stage is its own match, so each one gets its own replay. Later Start
# Game popups within the same match are left alone deliberately: re-arming on
# each would let a chatty popup rewind the phase indefinitely, so the
# placements keep restarting and never finish. One replay covers the case
# this exists for -- the re-stage that empties the board -- and anything past
# that is the run misbehaving in a way more re-placing will not fix.
# OCR here is several real Tesseract subprocess spawns (see core.wave/
# core.ocr's multi-mask sweep) -- checked on this cadence, not every single
# Battle-tick poll, so a long wait for a distant wave doesn't spend most of
# its time re-running OCR against a number that hasn't changed yet.
WAIT_WAVE_POLL_INTERVAL = 2.0
# New Infinite tasks default to a bounded run instead of silently running
# forever. The Task builder exposes this value per task.
DEFAULT_INFINITE_WAVE_LIMIT = 20
# Default for a new "Leave at Minute" battle block -- how many minutes into the
# match it waits before leaving to the lobby (clicks nav_todalobby -> return).
DEFAULT_LEAVE_AT_MINUTES = 10
# Never let one OCR frame end an Infinite run.
INFINITE_WAVE_LIMIT_CONFIRMATIONS = 2

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
    # Event gamemode card click point (see runner._reach_event_act_selected):
    # the card is clicked HERE by coordinate, then the event_gamemode button
    # (the image with the "Event Gamemode" text) is found and clicked by
    # image search. No tuple constant above -- keep it in sync with main.py's
    # MACRO_COORD_DEFAULTS.
    "event_gamemode_x": 152, "event_gamemode_y": 253,
    "challenge_stage_1_x": CHALLENGE_STAGE_CLICK["1"][0], "challenge_stage_1_y": CHALLENGE_STAGE_CLICK["1"][1],
    "challenge_stage_2_x": CHALLENGE_STAGE_CLICK["2"][0], "challenge_stage_2_y": CHALLENGE_STAGE_CLICK["2"][1],
    "challenge_stage_3_x": CHALLENGE_STAGE_CLICK["3"][0], "challenge_stage_3_y": CHALLENGE_STAGE_CLICK["3"][1],
    "expedition_difficulty_x": EXPEDITION_DIFFICULTY_CLICK[0], "expedition_difficulty_y": EXPEDITION_DIFFICULTY_CLICK[1],
    "team_loadout_x": TEAM_LOADOUT_CLICK_1[0], "team_loadout_y": TEAM_LOADOUT_CLICK_1[1],
    "team_loadout_row_height": TEAM_LOADOUT_ROW_HEIGHT,
    # Optional manual point inside the Unit Manager's Teams button. None
    # keeps the normal image-match center click; Settings > Debug can fill
    # this from a live screenshot when a user's button needs a lower/safer
    # click point than the matched crop's center.
    "team_button_x": None, "team_button_y": None,
    "screen_middle_x": SCREEN_MIDDLE_CLICK[0], "screen_middle_y": SCREEN_MIDDLE_CLICK[1],
    "unit_info_reset_x": UNIT_INFO_RESET_CLICK[0], "unit_info_reset_y": UNIT_INFO_RESET_CLICK[1],
    "daily_challenge_tab_x": 250, "daily_challenge_tab_y": 315,
    "daily_challenge_stage_x": 650, "daily_challenge_stage_y": 360,
}

# Victory/Defeat: no fixed timeout makes sense for "how long can a battle
# run", so this is a generous safety net (30 min), not an expected duration --
# polled slowly since there's no rush to notice a screen that, once it
# appears, just sits there until acted on.
MATCH_RESULT_TIMEOUT = 1800.0
MATCH_RESULT_POLL_INTERVAL = 1.0


# ── Auto Crafting (interleaved, see core.runner_crafting) ──
# The sprites the crafter knows how to make, in the default priority order the
# UI shows them in. Each string is BOTH the settings key AND an image name --
# the sprite's icon in the crafting menu, matched from Assets/ui/<name>/ (the
# user supplies these; an unmatched icon is simply skipped at run time). Add a
# sprite here and it flows through the whole feature (defaults, UI, runner)
# with no other change but its image.
CRAFT_SPRITES = ["sprite_rainbow", "sprite_red", "sprite_yellow", "sprite_green",
                 "sprite_blue", "sprite_purple", "sprite_pink"]
CRAFT_SPRITE_LABELS = {
    "sprite_rainbow": "Rainbow", "sprite_red": "Red", "sprite_yellow": "Yellow",
    "sprite_green": "Green", "sprite_blue": "Blue", "sprite_purple": "Purple",
    "sprite_pink": "Pink",
}
CRAFT_DEFAULT_EVERY = 20  # trigger a crafting pass after this many qualifying wins by default

# The sprite icons all live in one scrollable panel in the crafting menu --
# restrict the per-sprite icon search to that panel (reference-space
# x, y, w, h) so a sprite is only matched where it actually appears, not
# anywhere else on screen. Only the sprite-icon lookup uses this; the menu
# anchor / Max / input / Craft / insufficient searches stay full-screen.
CRAFT_SPRITE_REGION = (246, 241, 221, 340)
CRAFT_EVERY_MIN = 1
CRAFT_EVERY_MAX = 999
CRAFT_AMOUNT_MAX = 9999  # cap a per-item typed quantity at something sane

# Timeouts for each crafting step (seconds).
CRAFT_AREA_TIMEOUT = 10.0          # the Area menu / Crafting button showing up
CRAFT_LOAD_TIMEOUT = 60.0          # the crafting area world finishing loading (nav_play back on screen)
CRAFT_LOAD_SETTLE = 1.5            # extra wait AFTER nav_play reappears (area loaded) before pressing E --
                                   # nav_play showing means the world's up, but the character/controls need
                                   # a beat more to actually accept the E keypress
CRAFT_MENU_TIMEOUT = 15.0          # the E menu fully opening (craft_menu anchor)
CRAFT_ITEM_TIMEOUT = 4.0           # a sprite icon / Max / input / Craft button appearing
CRAFT_INSUFFICIENT_TIMEOUT = 2.0   # how long to watch for the insufficient-items warning after clicking Craft


# Auto Fuel is clock-driven rather than win-driven. Each resource keeps its
# own successful-refill timestamp so one failed station never makes the other
# repeat early or wait another full cycle.
FUEL_RESOURCES = ("resource_drill", "gold_mine")
FUEL_PATH_KEYS = (
    "hub_to_resource_drill",
    "hub_to_gold_mine",
    "resource_drill_to_gold_mine",
)
FUEL_UNIT_SECONDS = 5 * 60
FUEL_MIN_SAFETY_SECONDS = 5 * 60
FUEL_SAFETY_RATIO = 0.04
FUEL_INTERVAL_SECONDS = 8 * 60 * 60
FUEL_INTERVAL_MINUTES_MIN = 1
FUEL_INTERVAL_MINUTES_MAX = 10080  # 7 days
FUEL_RETRY_SECONDS = 5 * 60
FUEL_AMOUNT_MAX = 100
FUEL_AREA_TIMEOUT = 10.0
FUEL_LOAD_TIMEOUT = 60.0
FUEL_LOAD_SETTLE = 1.5
FUEL_CLICK_DELAY = 1.2
FUEL_ACTION_TIMEOUT = 15.0
FUEL_CONFIRM_TIMEOUT = 20.0
FUEL_CLOSE_TIMEOUT = 2.0


def fuel_interval_override_seconds(minutes) -> int:
    """Return a user override interval in seconds, clamped to the allowed range."""
    if minutes == 0:
        return 0
    return min(FUEL_INTERVAL_MINUTES_MAX, max(FUEL_INTERVAL_MINUTES_MIN, minutes)) * 60


def fuel_refill_interval_seconds(amount) -> int:
    """Return a safe refill interval for Max or a numeric fuel amount."""
    if str(amount).lower() == "max":
        return FUEL_INTERVAL_SECONDS

    try:
        units = min(FUEL_AMOUNT_MAX, max(1, int(amount)))
    except (TypeError, ValueError):
        return FUEL_INTERVAL_SECONDS

    coverage = units * FUEL_UNIT_SECONDS
    safety = max(FUEL_MIN_SAFETY_SECONDS, int(coverage * FUEL_SAFETY_RATIO))
    return max(FUEL_UNIT_SECONDS, coverage - safety)

