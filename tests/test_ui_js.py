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


def test_fuel_interval_input_converts_hours_to_minutes(tmp_path):
    out = run_js("""
        eval(extract('normalizeFuelIntervalInput'));
        console.log(JSON.stringify([
          normalizeFuelIntervalInput('30', 'minutes'),
          normalizeFuelIntervalInput('2', 'hours'),
          normalizeFuelIntervalInput('bad', 'hours')
        ]));
    """, tmp_path)
    assert out == [30, 120, 60]


def test_fuel_interval_input_stays_editable_in_auto_mode(tmp_path):
    out = run_js("""
        let fuelState = {interval_minutes: 0};
        const elements = {
          'fuel-interval-value': {value: '30'},
          'fuel-interval-unit': {value: 'hours'},
          'fuel-interval-controls': {style: {}},
          'fuel-interval-auto': {classList: {toggle() {}}},
        };
        const document = {getElementById: id => elements[id] || null};
        eval(extract('renderFuelIntervalControl'));
        renderFuelIntervalControl();
        console.log(JSON.stringify({
          display: elements['fuel-interval-controls'].style.display,
          opacity: elements['fuel-interval-controls'].style.opacity,
          input: elements['fuel-interval-value'].value,
          unit: elements['fuel-interval-unit'].value,
        }));
    """, tmp_path)
    assert out == {
        "display": "flex",
        "opacity": "1",
        "input": "",
        "unit": "minutes",
    }


# ---------------------------------------------------------------------------
# Infinite task wave limit
# ---------------------------------------------------------------------------

def test_new_tasks_have_a_bounded_infinite_wave_default(tmp_path):
    out = run_js("""
        const TASK_DATA = { story: { maps: ['Map'] } };
        const DEFAULT_INFINITE_WAVE_LIMIT = 20;
        function newTaskId() { return 't1'; }
        eval(extract('defaultTask'));
        console.log(JSON.stringify(defaultTask()));
    """, tmp_path)
    assert out["infinite_wave_limit"] == 20


def test_memory_refresh_hours_are_clamped_and_saved(tmp_path):
    out = run_js("""
        const calls = [];
        const pywebview = {api: {set_setting: async (key, value) => calls.push([key, value])}};
        function addLog() {}
        eval(extract('saveMemoryRefreshHours'));
        (async () => {
          const input = {value: '0.25'};
          await saveMemoryRefreshHours(input);
          console.log(JSON.stringify({value: input.value, calls}));
        })();
    """, tmp_path)
    assert out == {
        "value": 1,
        "calls": [["memory_refresh_hours", 1]],
    }


def test_extract_after_normalization_preserves_decimal_strings_and_repairs_scientific_notation(
        tmp_path):
    out = run_js("""
        const MAX_EXTRACT_AFTER = 9999;
        eval(extract('normalizeExtractAfter'));
        console.log(JSON.stringify([
          normalizeExtractAfter('25'),
          normalizeExtractAfter('999999999999999999999999999999999999999999'),
          normalizeExtractAfter('2.3434734346743343e+43'),
          normalizeExtractAfter(-1),
          normalizeExtractAfter(null)
        ]));
    """, tmp_path)
    assert out == [
        "25",
        "9999",
        "9999",
        "1",
        "1",
    ]


def test_infinite_task_summary_shows_its_exit_wave(tmp_path):
    out = run_js("""
        const TASK_DATA = { story: { label: 'Story' } };
        const DEFAULT_INFINITE_WAVE_LIMIT = 20;
        eval(extract('taskSummary'));
        console.log(JSON.stringify(taskSummary({
          mode: 'story', map: 'Map', stage: 'Infinite', difficulty: 'Normal',
          repeat: 1, play_mode: 'solo', macro: '', infinite_wave_limit: 50
        })));
    """, tmp_path)
    assert "Stop after wave 50" in out["meta"]


def test_tournament_task_summary_names_its_type_and_hides_play_mode(tmp_path):
    out = run_js("""
        const TASK_DATA = { tournament: { label: 'Tournament' } };
        const DEFAULT_INFINITE_WAVE_LIMIT = 20;
        eval(extract('taskSummary'));
        console.log(JSON.stringify(taskSummary({
          mode: 'tournament', map: 'Solo Tournament', stage: '-',
          repeat: 3, play_mode: 'solo', macro: 'Boss Rush'
        })));
    """, tmp_path)
    # Substring checks rather than an exact match on the whole string: the
    # summary joins on a middle-dot that doesn't survive node's stdout under
    # every Windows locale encoding (the other summary tests dodge it the same
    # way).
    assert out["title"].startswith("Tournament") and out["title"].endswith("Solo Tournament")
    assert "3" in out["meta"]
    # "Solo Tournament" already carries the play mode, so the row must not tack
    # on a redundant "Solo"/"Matchmaking" the way the other modes do.
    assert "Solo" not in out["meta"] and "Matchmaking" not in out["meta"]
    assert "Boss Rush" in out["meta"]


def test_tower_mode_defaults_to_rose_kingdom_and_traitless_summary_chip(tmp_path):
    out = run_js("""
        const TASK_DATA = {
          story: { label: 'Story', maps: ['School Grounds'], stages: ['1'], difficulties: ['Normal'] },
          tower: {
            label: 'Tower',
            maps: ['Rose Kingdom', 'School Grounds', 'Flower Forest', 'Fairy King Forest', "King's Tomb", 'East Town'],
            stages: ['1'],
            isTower: true,
          },
        };
        const DEFAULT_INFINITE_WAVE_LIMIT = 20;
        let taskCards = [{
          id: 't1', mode: 'story', map: 'School Grounds', stage: '1', difficulty: 'Normal',
          repeat: 1, play_mode: 'matchmaking', macro: ''
        }];
        function findTask(id) { return taskCards.find(t => t.id === id); }
        function updateQueueRowInPlace() {}
        function renderTaskBuilder() {}
        function saveTaskQueue() {}
        eval(extract('setTaskProp'));
        eval(extract('taskSummary'));
        setTaskProp('t1', 'mode', 'tower');
        const normal = {...taskCards[0]};
        setTaskProp('t1', 'tower_mode', 'traitless');
        console.log(JSON.stringify({normal, summary: taskSummary(taskCards[0])}));
    """, tmp_path)
    assert out["normal"]["map"] == "Rose Kingdom"
    assert out["normal"]["stage"] == "1"
    assert out["normal"]["play_mode"] == "solo"
    assert out["normal"]["tower_mode"] == "normal"
    assert out["summary"]["title"] == "Tower"
    assert "Traitless" in out["summary"]["meta"]
    assert "Solo" not in out["summary"]["meta"] and "Matchmaking" not in out["summary"]["meta"]



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
  ${extract('containerPhase')}
  ${extract('_findInContainer')}
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
# filterSettings: matching a panel title must not hide its controls
# ---------------------------------------------------------------------------

def test_settings_search_panel_title_keeps_all_rows_visible(tmp_path):
    out = run_js("""
        function classList() {
          const names = new Set();
          return {
            toggle(name, on) { if (on) names.add(name); else names.delete(name); },
            has(name) { return names.has(name); }
          };
        }
        const rows = [
          { textContent: 'Story Card fallback click', classList: classList() },
          { textContent: 'Story Stage Rows row height', classList: classList() },
        ];
        const panel = {
          classList: classList(),
          querySelector(sel) {
            return sel === '.rp-panel-head' ? { textContent: 'Control Macro Coordinates' } : null;
          },
          querySelectorAll(sel) { return sel === '.setting-row' ? rows : []; },
          cloneNode() {
            return {
              textContent: '',
              querySelector(sel) {
                return sel === '.rp-panel-head' ? { remove() {} } : null;
              },
              querySelectorAll() { return []; },
            };
          },
        };
        const category = {
          dataset: { cat: 'debug' }, style: {}, classList: classList(),
          querySelectorAll(sel) { return sel === '.rp-panel' ? [panel] : []; },
        };
        const buttons = [
          { dataset: { cat: 'all' }, classList: classList() },
          { dataset: { cat: 'debug' }, classList: classList() },
        ];
        const document = {
          querySelectorAll(sel) {
            if (sel === '.settings-cat-btn') return buttons;
            if (sel === '.settings-category') return [category];
            return [];
          },
        };
        eval(extract('filterSettings'));
        filterSettings('Macro Coordinates');
        console.log(JSON.stringify({
          rowsHidden: rows.map(row => row.classList.has('search-hidden')),
          panelHidden: panel.classList.has('search-hidden'),
        }));
    """, tmp_path)
    assert out == {"rowsHidden": [False, False], "panelHidden": False}


# ---------------------------------------------------------------------------
# filterSettings: panel-owned content outside .setting-row stays searchable
# ---------------------------------------------------------------------------

def test_settings_search_non_row_panel_content_keeps_controls_visible(tmp_path):
    out = run_js("""
        function classList() {
          const names = new Set();
          return {
            toggle(name, on) { if (on) names.add(name); else names.delete(name); },
            has(name) { return names.has(name); }
          };
        }
        const rows = [
          { textContent: 'Silent Mode', classList: classList() },
        ];
        const panel = {
          classList: classList(),
          querySelector(sel) {
            return sel === '.rp-panel-head' ? { textContent: 'Webhook' } : null;
          },
          querySelectorAll(sel) { return sel === '.setting-row' ? rows : []; },
          cloneNode() {
            return {
              textContent: 'Webhook URL Discord User ID',
              querySelector(sel) {
                return sel === '.rp-panel-head' ? { remove() {} } : null;
              },
              querySelectorAll() { return []; },
            };
          },
        };
        const category = {
          dataset: { cat: 'webhook' }, style: {}, classList: classList(),
          querySelectorAll(sel) { return sel === '.rp-panel' ? [panel] : []; },
        };
        const buttons = [
          { dataset: { cat: 'all' }, classList: classList() },
          { dataset: { cat: 'webhook' }, classList: classList() },
        ];
        const document = {
          querySelectorAll(sel) {
            if (sel === '.settings-cat-btn') return buttons;
            if (sel === '.settings-category') return [category];
            return [];
          },
        };
        eval(extract('filterSettings'));
        filterSettings('Webhook URL');
        console.log(JSON.stringify({
          rowsHidden: rows.map(row => row.classList.has('search-hidden')),
          panelHidden: panel.classList.has('search-hidden'),
        }));
    """, tmp_path)
    assert out == {"rowsHidden": [False], "panelHidden": False}


def test_webhook_progress_toggle_is_saved_with_other_webhook_settings(tmp_path):
    out = run_js("""
        const calls = [];
        const elements = {
          'webhook-url': { value: 'https://discord.com/api/webhooks/123/token' },
          'webhook-mention-id': { value: '456' },
          'toggle-webhook-enabled': { classList: { contains: () => true } },
          'toggle-webhook-silent': { classList: { contains: () => false } },
          'toggle-webhook-progress': { classList: { contains: () => true } },
        };
        const document = { getElementById: id => elements[id] };
        const pywebview = { api: {
          save_webhook_settings: async (...args) => calls.push(args),
        }};
        function updateWebhookValidity() {}
        function setWebhookStatus() {}
        eval(extract('saveWebhookSettings'));
        (async () => {
          await saveWebhookSettings(true);
          console.log(JSON.stringify(calls));
        })();
    """, tmp_path)
    assert out == [[
        "https://discord.com/api/webhooks/123/token",
        True,
        False,
        "456",
        True,
    ]]


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
  const MAX_EXTRACT_AFTER = 9999;
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
    import_recordings_bundle: async (bundle) => {
      restoredPaths.push(...Object.entries(bundle));
      return { ok: true, added: Object.keys(bundle).length };
    },
    save_template: async (n, b) => { saved.push(n); return { ok: true }; },
    save_tasks: async () => ({ ok: true }),
  } };
  ${extract('importCustomPaths')}
  ${extract('normalizeExtractAfter')}
  ${extract('importCustomRecordings')}
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
            export_recordings_bundle: async () => ({}),
            import_recordings_bundle: async () => ({ ok: true, added: 0 }),
            save_template: async () => ({ ok: true }),
          }};
          ${extract('collectCustomPathNames')}
          ${extract('exportCustomPaths')}
          ${extract('importCustomPaths')}
          ${extract('collectRecordingNames')}
          ${extract('exportCustomRecordings')}
          ${extract('importCustomRecordings')}
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
global.exportCustomRecordings = async () => ({});
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
const MAX_EXTRACT_AFTER = 9999;
global.addLog = m => logs.push(m);
global.confirm = () => confirmAnswer;
global.importCustomPaths = async () => 0;
global.importCustomRecordings = async () => 0;
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
eval(extract('normalizeExtractAfter'));
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
global.importCustomRecordings = async () => 0;
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
eval(extract('serializeBlock'));
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

# ---------------------------------------------------------------------------
# The in-game Roblox checklist
# ---------------------------------------------------------------------------
# These four are the only requirements nothing here can verify -- they live
# inside Roblox and Health Check cannot see into the game -- and the welcome
# modal never mentioned them at all, which is why "it walks to the wrong
# spot" and "it won't place" kept arriving as macro bugs.
#
# The same list appears in the welcome AND in Settings > Debug, because the
# welcome is dismissable and shows only once. Both are rendered from one
# array by the function under test here, so the two cannot drift.

INDEX_HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")

_CHECKLIST = """
const saved = [];
let html = {};
global.escapeHtml = s => String(s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
global.document = { getElementById: id => (html[id] = html[id] || { innerHTML: '' }) };
global.pywebview = { api: { set_setting: async (k, v) => saved.push([k, v]) } };
%s
eval(extract('renderIngameChecklist'));
eval(extract('renderAllIngameChecklists'));
eval(extract('toggleIngameCheck'));
let ingameConfirmed = %s;
"""


def _js(body, pre="", confirmed="{}"):
    src = open(os.path.join(os.path.dirname(INDEX_HTML), "app.js"), encoding="utf-8").read()
    arr = src[src.index("const INGAME_REQUIREMENTS = ["):]
    arr = arr[:arr.index("\n];") + 3]
    return (_CHECKLIST % (arr, confirmed)) + pre + body


def test_all_four_settings_render_with_a_reason(tmp_path):
    out = run_js(_js("""
      renderIngameChecklist('onboarding-ingame');
      console.log(JSON.stringify({ html: html['onboarding-ingame'].innerHTML }));
    """), tmp_path)
    body = out["html"]
    for name, value, reason in [("UI Scale", "1", "captured at 1"),
                                ("Auto Sprint", "On", "sprint speed"),
                                ("Show Match and Rewards", "Off", "covers"),
                                ("Auto Vote Start", "Off", "votes")]:
        assert name in body, f"{name} missing"
        assert f"<b>{value}</b>" in body, f"{name} does not state its required value"
        assert reason in body, f"{name} states no reason -- a checklist people don't understand is one they skip"


def test_the_welcome_and_settings_copies_are_identical(tmp_path):
    """One array, two containers -- so they cannot drift apart."""
    out = run_js(_js("""
      renderAllIngameChecklists();
      console.log(JSON.stringify({ a: html['onboarding-ingame'].innerHTML,
                                   b: html['debug-ingame'].innerHTML }));
    """), tmp_path)
    assert out["a"] == out["b"] and len(out["a"]) > 0


def test_ticking_persists_and_updates_both_copies(tmp_path):
    """Ticks are saved to settings, which is excluded from both update
    paths -- verified separately with a real exe update, where the whole
    settings file came back byte-identical."""
    out = run_js(_js("""
      renderAllIngameChecklists();
      toggleIngameCheck('auto_sprint').then(() => console.log(JSON.stringify({
        saved, checkedInWelcome: (html['onboarding-ingame'].innerHTML.match(/class="checked"/g) || []).length,
        checkedInDebug: (html['debug-ingame'].innerHTML.match(/class="checked"/g) || []).length })));
    """), tmp_path)
    assert out["saved"] == [["ingame_confirmed", {"auto_sprint": True}]]
    assert out["checkedInWelcome"] == 1 and out["checkedInDebug"] == 1


def test_ticking_is_a_toggle_not_a_one_way_door(tmp_path):
    out = run_js(_js("""
      renderAllIngameChecklists();
      toggleIngameCheck('ui_scale')
        .then(() => toggleIngameCheck('ui_scale'))
        .then(() => console.log(JSON.stringify({ last: saved[saved.length - 1] })));
    """), tmp_path)
    assert out["last"] == ["ingame_confirmed", {"ui_scale": False}]


def test_a_saved_tick_comes_back_checked(tmp_path):
    out = run_js(_js("""
      renderIngameChecklist('onboarding-ingame');
      console.log(JSON.stringify({ checked: (html['onboarding-ingame'].innerHTML.match(/class="checked"/g) || []).length }));
    """, confirmed="{ auto_sprint: true, ui_scale: true }"), tmp_path)
    assert out["checked"] == 2


def test_get_started_is_never_disabled():
    """Ticking is for the reader, never a gate -- nothing may trap someone
    on first launch."""
    html = open(INDEX_HTML, encoding="utf-8").read()
    modal = html.split('id="onboarding-modal"', 1)[1]
    button = modal[modal.index("closeOnboarding"):]
    assert "disabled" not in button[:button.index("</button>")]


def test_both_containers_exist_for_the_renderer_to_fill():
    html = open(INDEX_HTML, encoding="utf-8").read()
    assert 'id="onboarding-ingame"' in html
    assert 'id="debug-ingame"' in html, "Settings > Debug must keep a permanent copy"


# ---------------------------------------------------------------------------
# "On this computer" -- verified, not self-reported
# ---------------------------------------------------------------------------
# Deliberately NOT tick-your-own-box like the Roblox list. Health Check
# already measures most of these, and a box someone ticks for "display scale
# is 100%" while it is really 175% is worse than no box at all -- that exact
# setting is the most common cause of clicks landing slightly wrong.

_MACHINE = """
let html = {};
global.IS_MAC = false;
global.escapeHtml = s => String(s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
global.document = { getElementById: id => (html[id] = html[id] || { innerHTML: '' }) };
%s
eval(extract('machineCheckState'));
eval(extract('renderMachineChecklist'));
let lastHealthChecks = %s;
renderMachineChecklist('m');
console.log(JSON.stringify({ html: html['m'].innerHTML }));
"""


def _machine(checks="null"):
    src = open(os.path.join(os.path.dirname(INDEX_HTML), "app.js"), encoding="utf-8").read()
    arr = src[src.index("const MACHINE_REQUIREMENTS = ["):]
    arr = arr[:arr.index("\n];") + 3]
    return _MACHINE % (arr, checks)


def test_nothing_is_marked_passing_before_health_check_has_run(tmp_path):
    """An unmeasured row must never look like a tick."""
    body = run_js(_machine(), tmp_path)["html"]
    assert 'class="pass"' not in body
    assert "not checked" in body
    assert 'class="unknown"' in body


def test_a_failing_check_shows_as_failing_with_its_reason(tmp_path):
    checks = ("[{name:'Display scale', ok:false, detail:'175% -- set it to 100%'},"
              " {name:'Elevation matches Roblox', ok:true, detail:''},"
              " {name:'Critical reference images present', ok:true, detail:''},"
              " {name:'Text reading (OCR)', ok:true, detail:''}]")
    body = run_js(_machine(checks), tmp_path)["html"]
    assert 'class="fail"' in body
    assert "175% -- set it to 100%" in body, "Health Check's own detail must reach the user"
    assert ">3/4<" in body


def test_the_machine_list_has_no_checkboxes(tmp_path):
    """These are verified, so there is nothing for the user to assert."""
    body = run_js(_machine(), tmp_path)["html"]
    assert "<input" not in body and "toggleIngameCheck" not in body


def test_rows_with_nothing_to_measure_stay_informational(tmp_path):
    """IS_MAC=false here, so the mac-only rows are filtered out and the
    remaining rows all map to a real check."""
    body = run_js(_machine(), tmp_path)["html"]
    assert "Room for side-by-side" not in body, "mac-only row leaked onto Windows"


# ---------------------------------------------------------------------------
# First-run dialogs must wait for a window worth showing them in
# ---------------------------------------------------------------------------
# Before Roblox docks, the app window is still the small waiting-screen box
# in a corner of the display. Opening the welcome into that gave a tiny
# unreadable dialog -- observed on a real build at 676x678. It now waits for
# the layout to come up, which is either Roblox docking or the user pressing
# Skip on the waiting screen.

_FIRSTRUN = """
const shown = [];
global.showOnboarding = () => shown.push('onboarding');
global.showSubscribePrompt = () => shown.push('subscribe');
eval(extract('runPendingFirstRun'));
let pendingFirstRun = %s;
%s
console.log(JSON.stringify({ shown, pendingAfter: pendingFirstRun }));
"""


def test_a_queued_welcome_does_not_open_on_its_own(tmp_path):
    out = run_js(_FIRSTRUN % ("'onboarding'", ""), tmp_path)
    assert out["shown"] == [], "the welcome opened before the window was ready"


def test_docking_releases_the_queued_welcome(tmp_path):
    out = run_js(_FIRSTRUN % ("'onboarding'", "runPendingFirstRun();"), tmp_path)
    assert out["shown"] == ["onboarding"]


def test_it_only_ever_opens_once(tmp_path):
    """Dock, undock, dock again must not reopen it."""
    out = run_js(_FIRSTRUN % ("'onboarding'", "runPendingFirstRun(); runPendingFirstRun();"), tmp_path)
    assert out["shown"] == ["onboarding"]
    assert out["pendingAfter"] is None


def test_nothing_queued_means_nothing_opens(tmp_path):
    """Someone who already dismissed it -- an update must not bring it back."""
    out = run_js(_FIRSTRUN % ("null", "runPendingFirstRun();"), tmp_path)
    assert out["shown"] == []


def test_the_subscribe_prompt_waits_the_same_way(tmp_path):
    out = run_js(_FIRSTRUN % ("'subscribe'", "runPendingFirstRun();"), tmp_path)
    assert out["shown"] == ["subscribe"]


def test_both_dock_and_skip_release_it():
    """The two ways the layout comes up must both call it, or someone who
    skips waiting never sees the welcome at all."""
    src = open(os.path.join(os.path.dirname(INDEX_HTML), "app.js"), encoding="utf-8").read()
    for fn in ("showDocked", "skipWaiting"):
        body = src[src.index(f"function {fn}("):]
        body = body[:body.index("\n}\n") + 2]
        assert "runPendingFirstRun()" in body, f"{fn} never releases a queued first-run dialog"


def test_show_docked_reasserts_panel_width_on_mac():
    """macOS: docking (re)arranges the panel back to the narrow strip beside
    Roblox, and if the user was on a non-Dashboard screen when Roblox
    appeared (or reappeared after a relaunch) that strip leaves the
    multi-column editor clipped -- the "Macro Manager shows nothing" report.
    showDocked must re-assert the width the current screen needs, and only
    on mac; a Dashboard dock (the common first-ever case) keeps the strip."""
    src = open(os.path.join(os.path.dirname(INDEX_HTML), "app.js"), encoding="utf-8").read()
    body = src[src.index("function showDocked("):]
    body = body[:body.index("\n}\n") + 2]
    assert "if (IS_MAC) {" in body, "re-assert is not gated on mac"
    assert "set_panel_expanded(currentScreen !== 'dashboard')" in body, \
        "showDocked never re-asserts the width a non-Dashboard screen needs"


def test_show_docked_reasserts_panel_width_on_mac_behaviorally(tmp_path):
    """Behavioural twin of the source-contract check above: actually run the
    shipped showDocked() (lifted by brace-matching) against a stand-in DOM and
    pywebview bridge, and assert the re-assert fires exactly when it should.
    A non-Dashboard screen needs the full frame; the Dashboard keeps the strip;
    and on Windows the call must not happen at all (the bridge method is
    mac-only)."""
    out = run_js("""
        const calls = [];
        // First dock already happened, so showDocked does not auto-hop to
        // Dashboard and clobber currentScreen -- we want the re-assert path.
        let hasAutoShownDashboard = true;
        let currentScreen = 'manager';
        function switchScreen() {}
        function isBlockingOverlayOpen() { return false; }
        function runPendingFirstRun() {}
        // Any id gets a fresh {style:{}} so the three display writes land
        // without a real DOM; show_game/hide_game are no-ops for this test.
        global.document = { getElementById: id => ({ style: {} }) };
        global.window = { pywebview: {} };
        global.pywebview = { api: {
          set_panel_expanded: v => calls.push(v),
          show_game: () => {},
          hide_game: () => {},
        }};
        global.IS_MAC = true;

        eval(extract('showDocked'));

        showDocked();
        const managerCall = calls.slice();
        calls.length = 0;
        currentScreen = 'dashboard';
        showDocked();
        const dashboardCall = calls.slice();
        calls.length = 0;
        global.IS_MAC = false;
        currentScreen = 'manager';
        showDocked();
        const nonMacCall = calls.slice();

        console.log(JSON.stringify({ managerCall, dashboardCall, nonMacCall }));
    """, tmp_path)
    assert out["managerCall"] == [True], "non-Dashboard screen did not expand the panel on mac"
    assert out["dashboardCall"] == [False], "Dashboard kept the narrow strip instead of collapsing the panel"
    assert out["nonMacCall"] == [], "set_panel_expanded fired when not on mac"


# ---------------------------------------------------------------------------
# Detect block: nested then/else and its advanced fields must round-trip
# through the real serializeBlock/blockFromSaved pair.
# ---------------------------------------------------------------------------
def test_detect_block_round_trips_through_save_and_load(tmp_path):
    out = run_js("""
      global.newBlockId = (() => { let n = 0; return () => 'id' + (++n); })();
      eval(extract('serializeBlock'));
      eval(extract('blockFromSaved'));
      const saved = {
        type: 'detect', image: 'boss', advanced: true, mode: 'multi',
        images: ['a', 'b'], logic: 'or', expr: "find('x')",
        region: { x: 1, y: 2, w: 3, h: 4 }, threshold: 0.8, showAll: true,
        loop: true, loopAttempts: 5, loopIntervalMs: 750,
        then: [{ type: 'wait_ms', params: { ms: 500 } }],
        else: [{ type: 'send_key', params: {}, key: 'q',
                 then: undefined }],
      };
      const loaded = blockFromSaved(saved);
      const reser = serializeBlock(loaded);
      console.log(JSON.stringify({
        mode: loaded.mode, images: loaded.images, logic: loaded.logic,
        region: loaded.region, threshold: loaded.threshold, showAll: loaded.showAll,
        loop: loaded.loop, loopAttempts: loaded.loopAttempts, loopIntervalMs: loaded.loopIntervalMs,
        thenType: loaded.then[0].type, thenMs: loaded.then[0].params.ms,
        elseType: loaded.else[0].type, elseKey: loaded.else[0].key,
        thenHasId: !!loaded.then[0].id,
        reserThen: reser.then.length, reserElse: reser.else.length,
        reserImages: reser.images, reserRegion: reser.region,
      }));
    """, tmp_path)
    assert out['mode'] == 'multi'
    assert out['images'] == ['a', 'b']
    assert out['logic'] == 'or'
    assert out['region'] == {'x': 1, 'y': 2, 'w': 3, 'h': 4}
    assert out['threshold'] == 0.8
    assert out['showAll'] is True
    assert out['loop'] is True
    assert out['loopAttempts'] == 5 and out['loopIntervalMs'] == 750
    assert out['thenType'] == 'wait_ms' and out['thenMs'] == 500
    assert out['elseType'] == 'send_key' and out['elseKey'] == 'q'
    assert out['thenHasId'] is True           # nested blocks get real ids on load
    assert out['reserThen'] == 1 and out['reserElse'] == 1
    assert out['reserImages'] == ['a', 'b'] and out['reserRegion'] == {'x': 1, 'y': 2, 'w': 3, 'h': 4}


def test_list_placed_units_numbers_across_detect_branches(tmp_path):
    out = run_js("""
      global.PHASES = ['prestart', 'battle'];
      global.creationPhases = {
        prestart: [{ type: 'place_unit', params: { name: 'A' } }],
        battle: [
          { type: 'detect', type_: 'd',
            then: [{ type: 'place_unit', params: { name: 'B' } }],
            else: [{ type: 'place_unit', params: { name: 'C' } }] },
          { type: 'place_unit', params: { name: 'D' } },
        ],
      };
      eval(extract('listPlacedUnits'));
      console.log(JSON.stringify(listPlacedUnits()));
    """, tmp_path)
    # then before else, prestart before battle -- matches core.detect.flatten
    assert out == [{'n': 1, 'name': 'A'}, {'n': 2, 'name': 'B'},
                   {'n': 3, 'name': 'C'}, {'n': 4, 'name': 'D'}]


def test_target_priority_block_type_registered(tmp_path):
    body = """
    console.log(JSON.stringify({
        hasBlockType: src.includes("target_priority:"),
        inPrestartAllowed: src.includes("'target_priority'"),
        hasTargetPriorities: src.includes("TARGET_PRIORITIES")
    }));
    """
    out = run_js(body, tmp_path)
    assert out["hasBlockType"] is True
    assert out["inPrestartAllowed"] is True
    assert out["hasTargetPriorities"] is True


def test_loop_phases_registered(tmp_path):
    body = """
    console.log(JSON.stringify({
        inPhases: src.includes("['prestart', 'battle', 'loop_a', 'loop_b']"),
        hasLabels: src.includes("loop_a: 'Loop A'") && src.includes("loop_b: 'Loop B'"),
        allowed: src.includes("loop_a: _BATTLE_ALLOWED") && src.includes("loop_b: _BATTLE_ALLOWED")
    }));
    """
    out = run_js(body, tmp_path)
    assert out["inPhases"] is True
    assert out["hasLabels"] is True
    assert out["allowed"] is True


def test_render_crafting_screen_has_drag_grip(tmp_path):
    body = """
    global.craftingState = {
      enabled: true, every: 5, count: 2,
      items: [
        { key: 'sprite_rainbow', enabled: true, amount: 'max' },
        { key: 'sprite_red', enabled: false, amount: 10 }
      ]
    };
    global.escapeHtml = s => s;
    global.CRAFT_SPRITE_LABELS = { sprite_rainbow: 'Rainbow', sprite_red: 'Red' };

    const mockElements = {
      'resource-crafting-summary': {
        textContent: '', classList: { toggle: () => {} }
      },
      'resource-crafting-details': { textContent: '', title: '' },
      'toggle-crafting-enabled': { classList: { toggle: () => {} } },
      'crafting-every': { value: '' },
      'crafting-progress': { textContent: '' },
      'crafting-item-list': { innerHTML: '' }
    };
    global.document = {
      getElementById: id => mockElements[id] || null
    };

    eval(extract('renderCraftingScreen'));
    renderCraftingScreen();

    const html = mockElements['crafting-item-list'].innerHTML;
    console.log(JSON.stringify({
      hasCraftingGrip: html.includes('crafting-grip'),
      hasDataKeyRainbow: html.includes('data-key="sprite_rainbow"'),
      hasDataKeyRed: html.includes('data-key="sprite_red"'),
      summary: mockElements['resource-crafting-summary'].textContent,
      details: mockElements['resource-crafting-details'].textContent
    }));
    """
    out = run_js(body, tmp_path)
    assert out["hasCraftingGrip"] is True
    assert out["hasDataKeyRainbow"] is True
    assert out["hasDataKeyRed"] is True
    assert out["summary"] == "Enabled"
    assert out["details"] == "Rainbow (Max) | 2/5 wins"


def test_challenge_card_summarizes_daily_and_regular_state(tmp_path):
    body = """
    global.challengeState = {
      enabled: true, cap: 10, play_mode: 'solo', last_reset_date: '2026-07-29',
      daily: { enabled: true, ready: false },
      stages: {
        '1': { enabled: true, ready: true, count: 0 },
        '2': { enabled: true, ready: false, count: 1 },
        '3': { enabled: true, ready: true, count: 10 }
      },
      maps: {}
    };
    global.CHALLENGE_STAGE_SLOTS = ['1', '2', '3'];
    const mockElements = {
      'resource-challenge-summary': {
        textContent: '', classList: { toggle: () => {} }
      },
      'resource-challenge-details': { textContent: '', title: '' }
    };
    global.document = { getElementById: id => mockElements[id] || null };

    eval(extract('renderStoryMapSetupWarning'));
    eval(extract('renderChallengeScreen'));
    renderChallengeScreen();

    console.log(JSON.stringify({
      summary: mockElements['resource-challenge-summary'].textContent,
      details: mockElements['resource-challenge-details'].textContent
    }));
    """
    out = run_js(body, tmp_path)
    assert out == {
        "summary": "Enabled",
        "details": "Daily: Complete | Regular: #1 0/10, #2 1/10, #3 10/10",
    }


def test_story_map_setup_warning_lists_missing_and_invalid_maps(tmp_path):
    body = """
    global.escapeHtml = value => String(value);
    const warning = { innerHTML: '', style: { display: 'none' } };
    global.document = {
      getElementById: id => id === 'challenge-setup-warning' ? warning : null
    };

    eval(extract('renderStoryMapSetupWarning'));
    renderStoryMapSetupWarning('challenge-setup-warning', {
      setup_ready: false,
      missing_maps: ['Flower Forest'],
      invalid_maps: [{ map: "King's Tomb", macro: 'Broken Operation' }]
    }, 'Auto Challenge');

    console.log(JSON.stringify({ display: warning.style.display, html: warning.innerHTML }));
    """
    out = run_js(body, tmp_path)
    assert out["display"] == ""
    assert "Auto Challenge needs a saved Macro Operation for every Story map" in out["html"]
    assert "Assign: Flower Forest" in out["html"]
    assert "Repair: King's Tomb (Broken Operation)" in out["html"]


def test_fuel_card_summarizes_enabled_resources(tmp_path):
    body = """
    global.fuelState = {
      enabled: true,
      resources: {
        resource_drill: { enabled: true, next_due_at: 0 },
        gold_mine: { enabled: false, next_due_at: 0 }
      }
    };
    global.FUEL_RESOURCE_LABELS = {
      resource_drill: 'Resource Drill',
      gold_mine: 'Gold Mine'
    };
    Date.now = () => 1000;
    const mockElements = {
      'resource-fuel-summary': {
        textContent: '', classList: { toggle: () => {} }
      },
      'resource-fuel-details': { textContent: '', title: '' },
      'fuel-summary-status': { textContent: '' }
    };
    global.document = { getElementById: id => mockElements[id] || null };

    eval(extract('formatFuelCountdown'));
    eval(extract('renderFuelTimers'));
    renderFuelTimers();

    console.log(JSON.stringify({
      summary: mockElements['resource-fuel-summary'].textContent,
      details: mockElements['resource-fuel-details'].textContent
    }));
    """
    out = run_js(body, tmp_path)
    assert out == {
        "summary": "Enabled",
        "details": "Resource Drill: Ready | Gold Mine: Off",
    }


def test_auto_shop_card_renders_catalog_targets_and_daily_status(tmp_path):
    body = """
    global.autoShopState = {
      enabled: true,
      shops: {
        gold_shop: {
          enabled: true,
          items: [
            {
              key: 'cursed_boba', name: 'Cursed Boba', daily_maximum: 50,
              enabled: true, target: 'max',
              state: { status: 'completed', attempts: 0 }
            },
            {
              key: 'trait_crystal', name: 'Trait Crystal', daily_maximum: 25,
              enabled: true, target: 5,
              state: { status: 'failed_today', attempts: 3 }
            },
            {
              key: 'mana_flask', name: 'Mana Flask', daily_maximum: 150,
              enabled: true, target: 5,
              state: { status: 'retry_pending', attempts: 0 }
            }
          ]
        }
      }
    };
    global.escapeHtml = value => String(value);
    const mockElements = {
      'resource-auto-shop-summary': {
        textContent: '', classList: { toggle: () => {} }
      },
      'resource-auto-shop-details': { textContent: '', title: '' },
      'toggle-auto-shop-enabled': { classList: { toggle: () => {} } },
      'toggle-gold-shop-enabled': { classList: { toggle: () => {} } },
      'auto-shop-gold-items': { innerHTML: '' }
    };
    global.document = { getElementById: id => mockElements[id] || null };

    eval(extract('autoShopStatusLabel'));
    eval(extract('renderAutoShopScreen'));
    renderAutoShopScreen();

    const html = mockElements['auto-shop-gold-items'].innerHTML;
    console.log(JSON.stringify({
      summary: mockElements['resource-auto-shop-summary'].textContent,
      details: mockElements['resource-auto-shop-details'].textContent,
      hasCursedBoba: html.includes('Cursed Boba'),
      hasTraitCrystal: html.includes('Trait Crystal'),
      hasMaximum: html.includes('Daily max: 50'),
      hasMaxTarget: html.includes("setAutoShopItemMax('gold_shop', 'cursed_boba')"),
      hasNumericTarget: html.includes('value="5"'),
      hasResetToday: html.includes("resetAutoShopItemToday('gold_shop', 'trait_crystal')"),
      hasRetryStatus: html.includes('Retry scheduled'),
      hasRetryReset: html.includes("resetAutoShopItemToday('gold_shop', 'mana_flask')")
    }));
    """
    out = run_js(body, tmp_path)
    assert out == {
        "summary": "Enabled",
        "details": "Gold Shop: 3 enabled | 1 complete",
        "hasCursedBoba": True,
        "hasTraitCrystal": True,
        "hasMaximum": True,
        "hasMaxTarget": True,
        "hasNumericTarget": True,
        "hasResetToday": True,
        "hasRetryStatus": True,
        "hasRetryReset": True,
    }


def test_auto_shop_resource_card_has_required_controls():
    html = open(INDEX_HTML, encoding="utf-8").read()
    for element_id in (
        "resource-card-auto-shop",
        "resource-auto-shop-summary",
        "toggle-auto-shop-enabled",
        "toggle-gold-shop-enabled",
        "auto-shop-gold-items",
    ):
        assert f'id="{element_id}"' in html
    assert "Numeric quantities repeat on later passes until sold out." in html


def test_detect_controls_expose_live_test_button(tmp_path):
    out = run_js("""
        function renderDetectImagePick() { return '<image>'; }
        eval(extract('renderDetectControls'));
        console.log(JSON.stringify(renderDetectControls({ id: 'd1', advanced: false, image: 'Defense' })));
    """, tmp_path)
    assert "testDetect('d1'" in out
    assert 'Test now' in out

