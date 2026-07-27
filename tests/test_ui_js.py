"""Behavioural tests for ui/app.js, run through node.

The repo already installs node in CI (.github/workflows/ci.yml runs
`node --check` over every ui/*.js), so this needs no new tooling -- it just
uses it for more than a syntax check. Locally, the whole module skips if node
is not on PATH.

Each test lifts the REAL function out of ui/app.js by brace-matching its
source and runs it against a small stand-in for the bits of the page it
touches. Nothing is reimplemented: if app.js changes, these run the changed
code.
"""
import json
import os
import shutil
import subprocess
import textwrap

import pytest

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "app.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed; ui/app.js behaviour tests need it")

# Pulls `function name(...) { ... }` (or `async function`) out of app.js by
# matching braces -- keeps these tests running the shipped source rather than a
# copy that can drift.
_EXTRACT = """
const fs = require('fs');
const src = fs.readFileSync(process.env.APP_JS, 'utf8');
function extract(name) {
  const plain = src.indexOf('function ' + name + '(');
  const asy = src.indexOf('async function ' + name + '(');
  const s = asy !== -1 && (plain === -1 || asy < plain) ? asy : plain;
  if (s === -1) throw new Error(name + ' not found in ui/app.js');
  let d = 0, i = src.indexOf('{', s);
  for (; i < src.length; i++) {
    if (src[i] === '{') d++;
    else if (src[i] === '}' && --d === 0) return src.slice(s, i + 1);
  }
  throw new Error('unbalanced braces in ' + name);
}
"""


def run_js(body, tmp_path):
    """Run a node snippet with extract() available; return its parsed stdout."""
    script = tmp_path / "t.js"
    script.write_text(_EXTRACT + textwrap.dedent(body), encoding="utf-8")
    env = {**os.environ, "APP_JS": APP_JS}
    proc = subprocess.run(["node", str(script)], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# removeBlock: the deferred splice must not use a stale index
# ---------------------------------------------------------------------------
# The row stays in the DOM for the whole exit animation, so its X is still
# clickable and any other removal in that window shifts every later index.
# With the index captured up front, double-clicking one block's X deleted the
# block after it too, and removing two blocks quickly removed one the user
# never touched.

_REMOVE_HARNESS = """
const world = () => new Function(`
  const PHASES = ['prestart','battle'];
  let recordingBlockId = null;
  let creationPhases = { prestart: [], battle: ['A','B','C','D'].map(id => ({ id, type: 'wait_ms', params: {} })) };
  function renderPhases() {}
  const document = { querySelector(sel) {
    const id = /data-id="([^"]+)"/.exec(sel)[1];
    return PHASES.some(p => creationPhases[p].some(b => b.id === id)) ? { classList: { add() {} } } : null;
  } };
  ${extract('findBlockLocation')}
  ${extract('removeBlock')}
  return { removeBlock, ids: () => creationPhases.battle.map(b => b.id).join(',') };
`)();
const wait = ms => new Promise(r => setTimeout(r, ms));
"""


def test_remove_block_double_click_deletes_only_that_block(tmp_path):
    out = run_js(_REMOVE_HARNESS + """
        (async () => {
          const w = world();
          w.removeBlock('B'); w.removeBlock('B');   // second click inside the animation window
          await wait(400);
          console.log(JSON.stringify({ ids: w.ids() }));
        })();
    """, tmp_path)
    assert out["ids"] == "A,C,D", "double-clicking one block's X removed a second block"


def test_remove_two_blocks_quickly_removes_exactly_those_two(tmp_path):
    out = run_js(_REMOVE_HARNESS + """
        (async () => {
          const w = world();
          w.removeBlock('A'); w.removeBlock('C');
          await wait(400);
          console.log(JSON.stringify({ ids: w.ids() }));
        })();
    """, tmp_path)
    assert out["ids"] == "B,D", "a rapid second removal deleted the wrong block"


def test_remove_block_still_works_one_at_a_time(tmp_path):
    """Control: the slow path was never broken, so it must stay unbroken."""
    out = run_js(_REMOVE_HARNESS + """
        (async () => {
          const w = world();
          w.removeBlock('A'); await wait(400);
          w.removeBlock('C'); await wait(400);
          console.log(JSON.stringify({ ids: w.ids() }));
        })();
    """, tmp_path)
    assert out["ids"] == "B,D"


# ---------------------------------------------------------------------------
# importTasks: bundled templates must actually be restored
# ---------------------------------------------------------------------------
# exportTasks bundles every template its tasks reference so a shared queue does
# not arrive pointing at macros the recipient lacks. The import guard tested
# Array.isArray(t.blocks), which is only true for the oldest flat-list format,
# so every template saved since Pre Start/Battle phases existed was dropped in
# silence.

_IMPORT_HARNESS = """
const world = (data) => new Function('data', `
  const saved = []; const restoredPaths = []; const logs = []; let taskCards = [];
  const enteringTaskIds = new Set();
  function addLog(m) { logs.push(m); }
  function newTaskId() { return 't' + saved.length + Math.random(); }
  function defaultTask() { return { mode: 'story', map: 'x', stage: '1', repeat: 1 }; }
  function renderTaskList() {} function renderTaskBuilder() {} function saveTaskQueue() {}
  async function refreshTaskTemplates() {}
  const pywebview = { api: {
    import_tasks_file: async () => ({ ok: true, data }),
    list_templates: async () => [],
    list_custom_paths: async () => [],
    save_walk_path: async (name, events) => {
      restoredPaths.push([name, events]);
      return { ok: true };
    },
    save_template: async (n, b) => { saved.push(n); return { ok: true }; },
    save_tasks: async () => ({ ok: true }),
  } };
  ${extract('importCustomPaths')}
  ${extract('importTasks')}
  return { importTasks, saved, restoredPaths, logs, cards: () => taskCards };
`)(data);
"""

_MODERN = {"kind": "anime-expeditions-tasks",
           "tasks": [{"mode": "story", "macro": "Rose Farm"}],
           "templates": {"Rose Farm": {"name": "Rose Farm",
                                        "blocks": {"team": "", "equipment": "include",
                                                   "prestart": [], "battle": []}}}}
_LEGACY = {"kind": "anime-expeditions-tasks",
           "tasks": [{"mode": "story", "macro": "Old"}],
           "templates": {"Old": {"name": "Old", "blocks": [{"type": "place_unit"}]}}}


@pytest.mark.parametrize("data,label", [(_MODERN, "object"), (_LEGACY, "flat list")])
def test_import_tasks_restores_bundled_templates(data, label, tmp_path):
    out = run_js(_IMPORT_HARNESS + f"""
        (async () => {{
          const w = world({json.dumps(data)});
          await w.importTasks();
          console.log(JSON.stringify({{ saved: w.saved, logs: w.logs }}));
        }})();
    """, tmp_path)
    assert out["saved"] == list(data["templates"]), (
        f"a template whose blocks are a {label} was silently dropped on import"
    )


def test_import_tasks_rejects_a_settings_export(tmp_path):
    """importSettings/importTemplates both check `kind`; this one accepted any
    JSON carrying a `tasks` array."""
    data = {"kind": "anime-expeditions-settings", "tasks": [{"mode": "story"}], "settings": {}}
    out = run_js(_IMPORT_HARNESS + f"""
        (async () => {{
          const w = world({json.dumps(data)});
          await w.importTasks();
          console.log(JSON.stringify({{ cards: w.cards().length }}));
        }})();
    """, tmp_path)
    assert out["cards"] == 0


def test_import_tasks_still_accepts_an_export_without_a_kind_field(tmp_path):
    """Files written before `kind` existed must keep importing."""
    data = {"tasks": [{"mode": "story"}]}
    out = run_js(_IMPORT_HARNESS + f"""
        (async () => {{
          const w = world({json.dumps(data)});
          await w.importTasks();
          console.log(JSON.stringify({{ cards: w.cards().length }}));
        }})();
    """, tmp_path)
    assert out["cards"] == 1


def test_custom_path_transfer_helpers_export_and_restore_referenced_paths(tmp_path):
    templates = {
        "Modern": {"blocks": {"prestart": [
            {"type": "walk_path", "mode": "custom", "pathName": "Boss Route"},
            {"type": "walk_path", "mode": "auto", "pathName": "Ignore Me"},
        ], "battle": []}},
        "Legacy": {"blocks": [
            {"type": "walk_path", "mode": "custom", "pathName": "Old Route"},
            {"type": "walk_path", "mode": "custom", "pathName": "Boss Route"},
        ]},
    }
    out = run_js(f"""
        const w = new Function(`
          const restored = [];
          const source = {{
            'Boss Route': {{ name: 'Boss Route', events: [{{ t: 0, key: 'w', state: 'down' }}] }},
            'Old Route': {{ name: 'Old Route', events: [{{ t: 0, key: 'a', state: 'down' }}] }},
          }};
          const pywebview = {{ api: {{
            load_walk_path: async name => source[name],
            list_custom_paths: async () => [],
            save_walk_path: async (name, events) => {{
              restored.push([name, events]);
              return {{ ok: true }};
            }},
          }} }};
          ${{extract('collectCustomPathNames')}}
          ${{extract('exportCustomPaths')}}
          ${{extract('importCustomPaths')}}
          return {{ exportCustomPaths, importCustomPaths, restored }};
        `)();
        (async () => {{
          const bundle = await w.exportCustomPaths({json.dumps(templates)});
          const added = await w.importCustomPaths(bundle);
          console.log(JSON.stringify({{ names: Object.keys(bundle).sort(), added, restored: w.restored }}));
        }})();
    """, tmp_path)

    assert out["names"] == ["Boss Route", "Old Route"]
    assert out["added"] == 2
    assert [entry[0] for entry in out["restored"]] == ["Boss Route", "Old Route"]


def test_task_import_restores_bundled_custom_path(tmp_path):
    data = {
        "kind": "anime-expeditions-tasks",
        "version": 2,
        "tasks": [{"mode": "story", "macro": "Farm"}],
        "templates": {},
        "paths": {"Boss Route": {
            "name": "Boss Route",
            "events": [{"t": 0, "key": "w", "state": "down"}],
        }},
    }
    out = run_js(_IMPORT_HARNESS + f"""
        (async () => {{
          const w = world({json.dumps(data)});
          await w.importTasks();
          console.log(JSON.stringify({{ restored: w.restoredPaths }}));
        }})();
    """, tmp_path)

    assert out["restored"] == [["Boss Route", data["paths"]["Boss Route"]["events"]]]


def test_macro_manager_export_import_round_trips_custom_path(tmp_path):
    out = run_js("""
        const w = new Function(`
          const logs = []; const restored = []; let exported = null;
          function addLog(message) { logs.push(message); }
          async function refreshTemplateList() {}
          // importTemplates now confirms before replacing a macro you
          // already have, and opens the first imported one in the editor.
          function confirm() { return true; }
          function creationEditorHasUnsavedChanges() { return false; }
          async function loadSelectedTemplate() {}
          const document = { getElementById: () => ({ value: '' }) };
          const template = { name: 'Farm', blocks: {
            prestart: [{ type: 'walk_path', mode: 'custom', pathName: 'Boss Route' }],
            battle: [],
          }};
          const route = { name: 'Boss Route', events: [{ t: 0, key: 'w', state: 'down' }] };
          const pywebview = { api: {
            list_templates: async () => ['Farm'],
            load_template: async () => template,
            load_walk_path: async () => route,
            export_tasks_file: async payload => { exported = payload; return { ok: true, path: 'x.json' }; },
            import_tasks_file: async () => ({ ok: true, data: exported }),
            list_custom_paths: async () => [],
            save_walk_path: async (name, events) => {
              restored.push([name, events]);
              return { ok: true };
            },
            save_template: async () => ({ ok: true }),
          }};
          ${extract('collectCustomPathNames')}
          ${extract('exportCustomPaths')}
          ${extract('importCustomPaths')}
          ${extract('exportTemplates')}
          ${extract('importTemplates')}
          return {
            exportTemplates, importTemplates, restored,
            exported: () => exported,
          };
        `)();
        (async () => {
          await w.exportTemplates();
          await w.importTemplates();
          console.log(JSON.stringify({
            pathNames: Object.keys(w.exported().paths),
            restored: w.restored,
          }));
        })();
    """, tmp_path)

    assert out["pathNames"] == ["Boss Route"]
    assert out["restored"][0][0] == "Boss Route"


# ---------------------------------------------------------------------------
# Story Map Search: the min/max attributes do not constrain a typed value
# ---------------------------------------------------------------------------
# They mark the input :invalid and set validity.rangeOverflow, but .value still
# reads what was typed and nothing here calls checkValidity(). stage_select
# only bounds these from below, so an unclamped 9999 turns one map lookup into
# roughly fifteen minutes of a run that just looks hung.

@pytest.mark.parametrize("typed,lo,hi", [("9999", 1, 10), ("0", 1, 10), ("abc", 1, 10)])
def test_scroll_power_is_clamped_before_it_is_persisted(typed, lo, hi, tmp_path):
    out = run_js(f"""
        const w = new Function(`
          const out = [];
          const pywebview = {{ api: {{ set_setting: async (k, v) => out.push([k, v]) }} }};
          ${{extract('saveStoryScrollPower')}}
          return {{ saveStoryScrollPower, out }};
        `)();
        (async () => {{
          const el = {{ value: {json.dumps(typed)} }};
          await w.saveStoryScrollPower(el);
          console.log(JSON.stringify({{ sent: w.out, el: el.value }}));
        }})();
    """, tmp_path)
    assert out["sent"], "nothing was persisted"
    key, value = out["sent"][0]
    assert key == "story_scroll_power"
    assert lo <= value <= hi, f"typed {typed!r} persisted as {value}"


@pytest.mark.parametrize("typed", ["9999", "0", "abc"])
def test_scroll_attempts_is_clamped_before_it_is_persisted(typed, tmp_path):
    out = run_js(f"""
        const w = new Function(`
          const out = [];
          const pywebview = {{ api: {{ set_setting: async (k, v) => out.push([k, v]) }} }};
          ${{extract('saveStoryScrollNudges')}}
          return {{ saveStoryScrollNudges, out }};
        `)();
        (async () => {{
          const el = {{ value: {json.dumps(typed)} }};
          await w.saveStoryScrollNudges(el);
          console.log(JSON.stringify({{ sent: w.out }}));
        }})();
    """, tmp_path)
    key, value = out["sent"][0]
    assert key == "story_scroll_nudges"
    assert 1 <= value <= 30, f"typed {typed!r} persisted as {value}"


# ---------------------------------------------------------------------------
# No call to a function that does not exist
# ---------------------------------------------------------------------------

def test_app_js_calls_no_undefined_top_level_function():
    """updateDashboardHotkeys() was called in loadSettingsUI but defined
    nowhere, so every visit to Settings threw a ReferenceError that a bare
    catch swallowed. Nothing broke visibly, which is exactly why it survived.
    """
    import re
    src = open(APP_JS, encoding="utf-8").read()
    # Any indentation: helper arrows declared inside a function (const field =
    # ...) are legitimate definitions too, so a column-0-only match would
    # report them as missing.
    defined = set(re.findall(r"^\s*(?:async\s+)?function\s+(\w+)", src, re.M))
    defined |= set(re.findall(r"^\s*(?:const|let|var)\s+(\w+)\s*=", src, re.M))
    # Calls made at the start of a line (i.e. statements, not member calls).
    called = set(re.findall(r"^\s{2,}(\w+)\(", src, re.M))
    browser_and_globals = {
        "if", "for", "while", "switch", "return", "catch", "function", "await",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval", "requestAnimationFrame",
        "parseInt", "parseFloat", "String", "Number", "Boolean", "Array", "Object", "JSON",
        "console", "alert", "confirm", "prompt", "fetch", "Math", "Promise", "Set", "Map",
        "addLog", "clearLogView", "jumpLogToLatest", "logSnapToLatest", "appendLogBatch",
    }
    missing = sorted(n for n in called - defined - browser_and_globals
                     if not n.startswith("_") and n[0].islower())
    assert not missing, f"ui/app.js calls functions it never defines: {missing}"


# ---------------------------------------------------------------------------
# importTemplates: a same-name macro was skipped in silence
# ---------------------------------------------------------------------------
# Export a macro, edit it, import it back: nothing happened. The loop did
# `if (existing.includes(name) ...) continue`, so every macro you already had
# was dropped, and the log still reported a successful import. Reproduced
# against the shipped function before the fix:
#
#     saved_to_disk: ['New Macro']          <- the edited "Boss Rush" is gone
#     logs: ['[Macro Manager] Imported 1 template(s).']
#
# Overwriting without asking is the opposite failure -- a shared pack
# containing "Boss Rush" would take out the one you built -- so conflicts are
# now confirmed once, and the log says what was replaced and what was kept.

_MACRO_IMPORT_HARNESS = """
const logs = [], saved = {};
let confirmAnswer = %s, confirmsSeen = [], loadedIntoEditor = null;
global.addLog = m => logs.push(m);
global.confirm = m => { confirmsSeen.push(m); return confirmAnswer; };
global.importCustomPaths = async () => 0;
global.refreshTemplateList = async () => {};
global.loadSelectedTemplate = async () => { loadedIntoEditor = selectValue; };
global.creationEditorHasUnsavedChanges = () => %s;
let selectValue = '';
global.document = { getElementById: id => id === 'template-select'
  ? { get value() { return selectValue; }, set value(v) { selectValue = v; } } : null };
global.pywebview = { api: {
  import_tasks_file: async () => ({ ok: true, data: { templates: %s } }),
  list_templates: async () => %s,
  save_template: async (n, b) => { saved[n] = b; },
}};
eval(extract('importTemplates'));
importTemplates().then(() => console.log(JSON.stringify(
  { saved: Object.keys(saved).sort(), savedBossRush: saved['Boss Rush'] || null,
    logs, confirms: confirmsSeen.length, loadedIntoEditor })));
"""

_TWO = ("{'Boss Rush': {blocks: {start: ['EDITED v2']}}, "
        "'New Macro': {blocks: {start: ['brand new']}}}")


def test_reimporting_an_edited_macro_actually_overwrites_it(tmp_path):
    out = run_js(_MACRO_IMPORT_HARNESS % ("true", "false", _TWO, "['Boss Rush']"), tmp_path)
    assert out["saved"] == ["Boss Rush", "New Macro"], "the edited macro was dropped again"
    assert out["savedBossRush"] == {"start": ["EDITED v2"]}, "the OLD version survived"
    assert out["confirms"] == 1, "replacing what you already have must be confirmed"
    assert "1 replaced" in out["logs"][-1]


def test_declining_the_prompt_keeps_your_macro_and_still_imports_the_new_ones(tmp_path):
    out = run_js(_MACRO_IMPORT_HARNESS % ("false", "false", _TWO, "['Boss Rush']"), tmp_path)
    assert out["saved"] == ["New Macro"], "declining must not silently drop the new macros too"
    assert out["savedBossRush"] is None, "declining still overwrote the user's macro"
    assert "kept your existing 1" in out["logs"][-1]


def test_no_prompt_when_nothing_collides(tmp_path):
    out = run_js(_MACRO_IMPORT_HARNESS % ("false", "false", _TWO, "[]"), tmp_path)
    assert out["saved"] == ["Boss Rush", "New Macro"]
    assert out["confirms"] == 0, "a clean import must not ask anything"


def test_the_first_imported_macro_opens_in_the_editor(tmp_path):
    """The dropdown used to refresh but keep its empty selection, so a fully
    successful import still looked like it had done nothing."""
    out = run_js(_MACRO_IMPORT_HARNESS % ("true", "false", _TWO, "[]"), tmp_path)
    assert out["loadedIntoEditor"] == "Boss Rush"


def test_import_warns_before_replacing_unsaved_editor_work(tmp_path):
    """Because the import now loads a macro into the editor, it destroys
    whatever was in there -- so it has to ask first."""
    out = run_js(_MACRO_IMPORT_HARNESS % ("false", "true", _TWO, "[]"), tmp_path)
    assert out["saved"] == [], "declining the warning must not import anything"
    assert "cancelled" in out["logs"][-1]


def test_a_file_with_no_macros_reports_that_instead_of_importing_nothing(tmp_path):
    out = run_js(_MACRO_IMPORT_HARNESS % ("true", "false", "{'Broken': {}}", "[]"), tmp_path)
    assert out["saved"] == []
    assert "no macros" in out["logs"][-1]


# ---------------------------------------------------------------------------
# The unsaved-changes check itself
# ---------------------------------------------------------------------------

_DIRTY_HARNESS = """
global.PHASES = ['prestart', 'battle'];
let nameValue = 'Boss Rush';
global.document = { getElementById: () => ({ get value() { return nameValue; } }) };
global.creationTeam = ''; global.creationEquipment = 'include';
global.creationPhases = { prestart: [], battle: [] };
eval(extract('currentCreationPayload'));
eval(extract('currentCreationSnapshot'));
eval(extract('markCreationEditorSaved'));
eval(extract('creationEditorHasUnsavedChanges'));
let creationSavedSnapshot = null;
const before = creationEditorHasUnsavedChanges();
markCreationEditorSaved();
const afterSave = creationEditorHasUnsavedChanges();
creationPhases.battle.push({ type: 'attack', params: {} });
const afterEdit = creationEditorHasUnsavedChanges();
creationPhases.battle.pop();
const afterUndo = creationEditorHasUnsavedChanges();
nameValue = 'Renamed';
const afterRename = creationEditorHasUnsavedChanges();
console.log(JSON.stringify({ before, afterSave, afterEdit, afterUndo, afterRename }));
"""


def test_unsaved_changes_tracking(tmp_path):
    out = run_js(_DIRTY_HARNESS, tmp_path)
    assert out["before"] is False, "no baseline yet -- must not warn on a fresh editor"
    assert out["afterSave"] is False
    assert out["afterEdit"] is True
    assert out["afterUndo"] is False, "edit-then-undo must not leave a false warning"
    assert out["afterRename"] is True, "renaming is an unsaved change too"
