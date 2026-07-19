One reference image per map (Story AND Raid -- e.g. "Spirit City" is a Raid
map), used by core/stage_select.py to find and click the right card in the
map carousel. Not the same folder as Assets/map/<Category>/ (the Place Unit
picker's full map preview thumbnails) -- this one holds small name-label
crops, keyed by exact map name.

Filename MUST exactly match that map's `map` value on a Task card (Task
screen > Task Builder > Map), e.g.:

  School Grounds.png
  Flower Forest.png
  Rose Kingdom.png
  Fairy King Forest.png
  King's Tomb.png
  Spirit City.png

Crop tightly around just the map's NAME LABEL (the bold white text under
the thumbnail art, e.g. "School Grounds") -- not the thumbnail art itself
and not the whole card. The search only looks inside a thin strip
(x0 y463 w1152 h30, the whole card row's label band) so the crop only
needs to cover that text, with a little margin. Matching is plain
whole-image (no background/transparency handling) -- crop it as an
ordinary rectangular screenshot, background included.

Use Settings > Debug > "Story Map Region" to capture exactly that search
band from the live game as a reference screenshot, if you want to check
what the crop should look like or verify your reference image lines up.
