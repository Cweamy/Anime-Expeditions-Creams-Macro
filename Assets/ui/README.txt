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
  (74, 434, 58, 58) in the docked game window's own coordinates, both to
  confirm you're on the lobby (it only renders there) and to click it.

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

Add more <name>.png files here as new macro steps need to recognize other
buttons/screens -- core.vision.find_image(hwnd, "<name>", ...) will pick
up any file added under this folder automatically, no code change needed
beyond the step that calls it.
