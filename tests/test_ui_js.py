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
# Task export/import: a shared queue has to arrive usable
# ---------------------------------------------------------------------------
# exportTasks collected only `t.macro`. act4_macro -- Act 4's own Macro
# Operation -- was never bundled, so a queue using one exported "successfully"
# and arrived at the other end pointing at a macro the recipient does not
# have. Reproduced against the shipped function:
#
#     task references : Main Farm (macro), Act4 Relic Run (act4_macro)
#     actually bundled: ['Main Farm']
#     log             : "[Task] Exported 1 task(s) to q.json"

_TASK_EXPORT_WORLD = """
const logs = []; let exported = null;
global.addLog = m => logs.push(m);
global.exportCustomPaths = async () => ({});
global.taskCards = %s;
global.pywebview = { api: {
  list_templates: async () => %s,
  load_template: async n => ({ name: n, blocks: { prestart: [], battle: [] } }),
  export_tasks_file: async p => { exported = p; return { ok: true, path: 'q.json' }; },
}};
eval(extract('taskMacroNames'));
eval(extract('exportTasks'));
exportTasks().then(() => console.log(JSON.stringify({
  bundled: exported ? Object.keys(exported.templates).sort() : null,
  log: logs[logs.length - 1] })));
"""

_ONE_TASK = "[{id:1, mode:'story', macro:'Main Farm', act4_macro:'Act4 Relic Run'}]"


def test_export_bundles_the_act4_macro_too(tmp_path):
    out = run_js(_TASK_EXPORT_WORLD % (_ONE_TASK, "['Main Farm', 'Act4 Relic Run']"), tmp_path)
    assert out["bundled"] == ["Act4 Relic Run", "Main Farm"], (
        "the Act 4 macro was left out of the package again")


def test_export_stops_when_a_referenced_macro_no_longer_exists(tmp_path):
    """load_template returns an empty object for a name with no file and the
    failure was swallowed, so the export "succeeded" and only broke for
    whoever imported it."""
    out = run_js(_TASK_EXPORT_WORLD % (_ONE_TASK, "['Main Farm']"), tmp_path)
    assert out["bundled"] is None, "a package missing one of its macros must not be written"
    assert "Act4 Relic Run" in out["log"] and "Export stopped" in out["log"]


_TASK_IMPORT_WORLD = """
const logs = []; const saved = {}; let confirmAnswer = %s;
global.addLog = m => logs.push(m);
global.confirm = () => confirmAnswer;
global.importCustomPaths = async () => 0;
global.refreshTaskTemplates = async () => {};
global.renderTaskList = () => {}; global.renderTaskBuilder = () => {};
global.saveTaskQueue = () => {};
global.defaultTask = () => ({ id: 0, mode: 'story', macro: '' });
let nextId = 100;
global.newTaskId = () => ++nextId;
global.taskCards = [];
global.enteringTaskIds = new Set();
global.pywebview = { api: {
  import_tasks_file: async () => ({ ok: true, data: { kind: 'anime-expeditions-tasks',
    tasks: [{ id: 7, mode: 'story', macro: 'Boss Rush' }],
    templates: { 'Boss Rush': { blocks: { prestart: [], battle: [] } } } } }),
  list_templates: async () => %s,
  save_template: async (n, b) => { saved[n] = b; },
}};
eval(extract('importTasks'));
importTasks().then(() => console.log(JSON.stringify({
  macrosSaved: Object.keys(saved), tasks: taskCards.length,
  ids: taskCards.map(t => t.id), log: logs[logs.length - 1] })));
"""


def test_a_bundled_macro_that_clashes_with_yours_is_no_longer_skipped(tmp_path):
    out = run_js(_TASK_IMPORT_WORLD % ("true", "['Boss Rush']"), tmp_path)
    assert out["macrosSaved"] == ["Boss Rush"], (
        "the sender's macro was dropped, so the imported task points at a different one")
    assert out["tasks"] == 1


def test_declining_the_clash_cancels_the_whole_task_import(tmp_path):
    """Keeping the tasks while declining their macros is exactly the mismatch
    the prompt exists to prevent -- the task would silently run YOUR macro of
    that name instead of the one it was built with."""
    out = run_js(_TASK_IMPORT_WORLD % ("false", "['Boss Rush']"), tmp_path)
    assert out["macrosSaved"] == [], "declining still overwrote a macro"
    assert out["tasks"] == 0, "tasks were imported without the macros they reference"
    assert "cancelled" in out["log"]


def test_imported_tasks_get_fresh_ids(tmp_path):
    """Already true before this change -- pinned so it stays true."""
    out = run_js(_TASK_IMPORT_WORLD % ("true", "[]"), tmp_path)
    assert out["ids"] == [101], "an imported task kept its id from the file"


_CLEAR_WORLD = """
let confirms = 0;
global.confirm = () => { confirms++; return %s; };
global.renderTaskList = () => {}; global.renderTaskBuilder = () => {};
global.saveTaskQueue = () => {};
global.selectedTaskId = 1;
global.taskCards = [{id:1},{id:2},{id:3}];
eval(extract('clearTaskQueue'));
clearTaskQueue();
console.log(JSON.stringify({ remaining: taskCards.length, confirms }));
"""


def test_clear_all_asks_before_wiping_the_queue(tmp_path):
    """Wired to a "Clear All" danger button with no undo."""
    assert run_js(_CLEAR_WORLD % "false", tmp_path) == {"remaining": 3, "confirms": 1}
    assert run_js(_CLEAR_WORLD % "true", tmp_path) == {"remaining": 0, "confirms": 1}
