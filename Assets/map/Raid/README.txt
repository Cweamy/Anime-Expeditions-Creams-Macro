Full-screen map reference images for the Set Position (Place Unit) picker's
Raid tab -- lets you click a spot on the map to read off an X/Y position,
same as Assets/map/Story/. Not the same folder as Assets/maps/ (the small
name-label crops core/stage_select.py uses to find/click a map card in the
carousel).

Drop one screenshot per Raid map here, named to match, e.g.:

  Spirit City.png

If an Act's layout differs enough from the base map to need its own
reference image, name it with the Act suffix the same way
core.runner._run_prestart looks up per-Act walk paths, e.g.:

  Spirit City Act3.png

Any image dropped in this folder shows up as a pickable map automatically
(core/maps.py) -- no code change needed.
