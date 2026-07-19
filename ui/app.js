// WebView2 (this app's Chromium-based renderer) treats F11 as its own native
// fullscreen toggle by default, even though nothing here ever requests
// fullscreen -- it's a built-in browser accelerator, not page behavior. This
// window is frameless/fixed-size (see main.py's create_window) with Roblox
// docked into it as a native child window at a hardcoded pixel offset (see
// core.dock); WebView2 fullscreening itself resizes the webview control but
// NOT Roblox's docked position/size, which is exactly the broken half-cut
// layout this produces instead of an actual fullscreen view. Cancel it right
// at the keydown so it never engages.
window.addEventListener('keydown', (e) => {
  if (e.key === 'F11') e.preventDefault();
});

// ---------------------------------------------------------------------------
// Logs
// ---------------------------------------------------------------------------
// Lines that start with a "[Tag]" (e.g. "[Selector] ...", "[Theme] ...") are
// treated as categorized: the tag is hashed to a stable accent color from the
// app palette (same tag -> same color every time, tags that don't exist yet
// get one automatically), which drives both the tag text and the line's
// hover highlight via the --cat custom property (see .log-entry in style.css).
const LOG_TAG_COLORS = ['var(--brand)', 'var(--teal)', 'var(--amber)', 'var(--lilac)', 'var(--rose)', 'var(--slate)'];

function logTagColor(tag) {
  let h = 0;
  for (let i = 0; i < tag.length; i++) h = (h * 31 + tag.charCodeAt(i)) >>> 0;
  return LOG_TAG_COLORS[h % LOG_TAG_COLORS.length];
}

function renderLogLine(div, line) {
  const match = /^\[([^\]]+)\](.*)$/.exec(line);
  div.appendChild(document.createTextNode('> '));
  if (match) {
    div.style.setProperty('--cat', logTagColor(match[1]));
    const tag = document.createElement('span');
    tag.className = 'log-tag';
    tag.textContent = `[${match[1]}]`;
    div.appendChild(tag);
    div.appendChild(document.createTextNode(match[2]));
  } else {
    div.appendChild(document.createTextNode(line));
  }
}

// Oldest lines get dropped past this: the log is a live view, not an
// archive (Python keeps its own history buffer for pop-out replay), and an
// ever-growing list makes "am I at the newest line?" ambiguous.
const LOG_MAX_LINES = 400;

function addLog(line) {
  const list = document.getElementById('log-list');
  const div = document.createElement('div');
  div.className = 'log-entry';
  renderLogLine(div, line);
  list.appendChild(div);
  while (list.childElementCount > LOG_MAX_LINES) list.removeChild(list.firstElementChild);
  list.scrollTop = list.scrollHeight;
}

// Clears this window's view and asks Python to drop its history buffer and
// clear any other open log window (e.g. a popped-out one), so "Clear" doesn't
// leave a stale copy sitting in a second window.
function clearLogs() {
  document.getElementById('log-list').innerHTML = '';
  try { window.pywebview && pywebview.api.clear_logs(); } catch (e) {}
}

function popOutLogs() {
  try { window.pywebview && pywebview.api.pop_out_logs(); } catch (e) {}
}

// ---------------------------------------------------------------------------
// Session / All Time timers
// ---------------------------------------------------------------------------
function formatDuration(totalSeconds) {
  const s = Math.floor(totalSeconds % 60);
  const m = Math.floor((totalSeconds / 60) % 60);
  const h = Math.floor(totalSeconds / 3600);
  const pad = n => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

let sessionStart = null;
let allTimeBase = 0;

function tickTimers() {
  if (sessionStart === null) return;
  const elapsed = (Date.now() / 1000) - sessionStart;
  document.getElementById('session-time').textContent = formatDuration(elapsed);
  document.getElementById('alltime-time').textContent = formatDuration(allTimeBase + elapsed);
}

// ---------------------------------------------------------------------------
// Task screen: waiting / docked status
// ---------------------------------------------------------------------------
let hasAutoShownDashboard = false;

// Called from Python (main.py) the moment docking actually succeeds,
// don't wait on the 1.5s status poll for a state this important to flip.
function showDocked() {
  document.getElementById('waiting-screen').style.display = 'none';
  document.getElementById('main-layout').style.display = 'flex';
  document.getElementById('titlebar').style.display = 'flex';

  // First-ever dock this session: jump to the Dashboard so the user actually
  // sees it worked. After that, respect wherever they navigated to.
  if (!hasAutoShownDashboard) {
    hasAutoShownDashboard = true;
    switchScreen('dashboard');
  } else if (currentScreen === 'dashboard') {
    try { window.pywebview && pywebview.api.show_game(); } catch (e) {}
  }
}

// ---------------------------------------------------------------------------
// Update popup -- shown when main._check_for_update_background finds a
// newer tagged GitHub release than VERSION. Called via push_ui (no args,
// same pattern as showDocked/showWaiting above), so the actual version/
// notes/url are fetched here rather than passed in.
// ---------------------------------------------------------------------------
async function showUpdateAvailable() {
  try {
    const info = await pywebview.api.get_update_info();
    if (!info || !info.available) return;
    document.getElementById('update-version').textContent = info.version;
    document.getElementById('update-current-version').textContent = info.current_version || '-';
    document.getElementById('update-notes').textContent = info.notes || 'No release notes provided.';
    document.getElementById('update-modal').style.display = 'flex';
  } catch (e) {}
}

function dismissUpdateModal() {
  document.getElementById('update-modal').style.display = 'none';
}

async function manualCheckForUpdate() {
  const badge = document.getElementById('ver-badge');
  const original = badge.textContent;
  badge.textContent = 'Checking...';
  try {
    await pywebview.api.check_for_updates();
    // check_for_updates fires the background check and returns immediately
    // -- give it a moment to actually land before asking for the result.
    setTimeout(async () => {
      badge.textContent = original;
      const info = await pywebview.api.get_update_info();
      if (info && info.available) {
        showUpdateAvailable();
      } else {
        addLog && addLog("[Update] You're up to date.");
      }
    }, 2500);
  } catch (e) {
    badge.textContent = original;
  }
}

async function applyUpdate() {
  const btn = document.getElementById('update-apply-btn');
  btn.disabled = true;
  btn.textContent = 'Updating...';
  try {
    const result = await pywebview.api.apply_update();
    if (!result || !result.ok) {
      btn.disabled = false;
      btn.textContent = 'Update & Restart';
      addLog && addLog('[Update] Failed to start the update -- check the log for details.');
    }
    // On success the app closes itself shortly after (see main.Api.
    // apply_update) and a relaunch helper brings it back up -- nothing
    // left to do here.
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Update & Restart';
  }
}

let skipped = false;

function showWaiting() {
  if (skipped) return;  // user chose to use the panel before Roblox docks, don't yank it away
  document.getElementById('main-layout').style.display = 'none';
  document.getElementById('waiting-screen').style.display = 'flex';
  document.getElementById('titlebar').style.display = 'none';
}

function skipWaiting() {
  skipped = true;
  try { window.pywebview && pywebview.api.skip_waiting(); } catch (e) {}
  document.getElementById('waiting-screen').style.display = 'none';
  document.getElementById('main-layout').style.display = 'flex';
  document.getElementById('titlebar').style.display = 'flex';
}

// ---------------------------------------------------------------------------
// Screen switching (Dashboard / Creation / Settings)
// ---------------------------------------------------------------------------
// Switching away from Dashboard hides the docked Roblox window entirely (it's
// a native child window, not DOM content, so CSS alone can't hide it) so the
// other screens get the full window instead of Roblox showing through.
let currentScreen = 'dashboard';
let lastNonDashboardScreen = 'creation';
const SCREENS = ['dashboard', 'task', 'creation', 'settings'];

function switchScreen(name) {
  const changed = currentScreen !== name;
  currentScreen = name;
  if (name !== 'dashboard') lastNonDashboardScreen = name;

  for (const n of SCREENS) {
    const el = document.getElementById(`screen-${n}`);
    el.style.display = n === name ? 'flex' : 'none';
    document.getElementById(`nav-${n}`).classList.toggle('active', n === name);
    // Re-trigger the entrance animation on the screen being revealed --
    // remove + reflow + re-add, since re-adding the same class without a
    // reflow in between wouldn't restart a finished animation. Skipped for
    // the Dashboard: the docked Roblox window is a native child window that
    // doesn't move with CSS transforms, so animating that screen would
    // visibly desync the HTML chrome from the game sitting inside it.
    if (n === name && changed && name !== 'dashboard') {
      el.classList.remove('screen-enter');
      void el.offsetWidth;
      el.classList.add('screen-enter');
    }
  }

  try {
    if (window.pywebview) {
      if (name === 'dashboard') pywebview.api.show_game();
      else pywebview.api.hide_game();
    }
  } catch (e) {}

  if (name === 'creation') { refreshTemplateList(); refreshSavedPaths(); }
  if (name === 'task') refreshTaskQueue();
  if (name === 'settings') { refreshSavedPaths(); loadMacroCoords(); loadRewardTestMaps(); }

  // The Process Log only exists on the Dashboard; addLog()'s scroll-to-bottom
  // is a no-op for lines that arrive while another screen is up (a
  // display:none element has no scroll height), so snap to the newest line
  // on the way back in.
  if (name === 'dashboard') {
    const log = document.getElementById('log-list');
    if (log) log.scrollTop = log.scrollHeight;
  }
}

// Bound to the "Toggle Game Visibility" hotkey (default F4) from Python.
// Routed through here (not a raw show/hide toggle) so it reuses switchScreen's
// own hide/show coordination instead of fighting it as a second source of truth.
function toggleGameScreenHotkey() {
  switchScreen(currentScreen === 'dashboard' ? lastNonDashboardScreen : 'dashboard');
}

// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------
async function refreshStatus() {
  if (!window.pywebview) return;
  try {
    const status = await pywebview.api.get_status();
    if (status.docked) {
      showDocked();
    } else {
      showWaiting();
    }
    document.getElementById('stat-current-task').textContent = status.current_task ?? '-';
    document.getElementById('stat-current-repeat').textContent = status.current_repeat ?? '-';
    document.getElementById('stat-map').textContent = status.map ?? '-';
    document.getElementById('stat-action').textContent = status.action ?? '-';
    document.getElementById('stat-last-run').textContent = status.last_run ?? '-';
    document.getElementById('stat-challenge').textContent = status.time_until_challenge ?? '-';

    const wins = status.wins ?? 0;
    const losses = status.losses ?? 0;
    const allTimeWins = status.all_time_wins ?? 0;
    const allTimeLosses = status.all_time_losses ?? 0;
    document.getElementById('stat-wins').textContent = wins;
    document.getElementById('stat-losses').textContent = losses;
    document.getElementById('stat-winrate').textContent = status.win_rate == null ? '-' : `${status.win_rate}%`;
    document.getElementById('stat-alltime-wins').textContent = allTimeWins;
    document.getElementById('stat-alltime-losses').textContent = allTimeLosses;
    document.getElementById('stat-alltime-winrate').textContent =
      status.all_time_win_rate == null ? '-' : `${status.all_time_win_rate}%`;

    const totalRuns = allTimeWins + allTimeLosses;
    document.getElementById('stat-total-runs').textContent = `${totalRuns} total run${totalRuns === 1 ? '' : 's'}`;
    setRatioBar('bar-session-wins', 'bar-session-losses', wins, losses);
    setRatioBar('bar-alltime-wins', 'bar-alltime-losses', allTimeWins, allTimeLosses);
    renderRunHistory(status.run_history ?? []);
  } catch (e) { /* backend not ready yet */ }

  try {
    const macro = await pywebview.api.is_macro_running();
    setMacroButtons(!!macro.running, !!macro.paused);
  } catch (e) {}
}

// Start disabled while a run is already going (the runner is a single
// module-level instance -- see core.runner.MacroRunner -- so a second Start
// click would just no-op against it); Pause/Stop only make sense while
// running. Pause relabels to Resume and lights up while paused, same
// on/off vocabulary as the toggle switches elsewhere in the app.
function setMacroButtons(running, paused) {
  const startBtn = document.getElementById('btn-macro-start');
  const pauseBtn = document.getElementById('btn-macro-pause');
  const stopBtn = document.getElementById('btn-macro-stop');
  if (startBtn) startBtn.disabled = running;
  if (stopBtn) stopBtn.disabled = !running;
  if (pauseBtn) {
    pauseBtn.disabled = !running;
    pauseBtn.classList.toggle('on', !!paused);
    const label = pauseBtn.childNodes[pauseBtn.childNodes.length - 1];
    if (label) label.textContent = paused ? ' Resume' : ' Pause';
  }
}

async function startMacro() {
  switchScreen('dashboard');
  setMacroButtons(true, false);
  try {
    const result = await pywebview.api.start_macro();
    if (!result.ok) {
      setMacroButtons(false, false);
      addLog(`[Macro] Couldn't start: ${result.reason === 'already_running' ? 'already running.' : (result.reason || 'error')}`);
    }
  } catch (e) { setMacroButtons(false, false); }
}

// F2's whole point is to be instant regardless of what the run is doing --
// routed straight to a direct Python call (see main.py's hotkey wiring for
// macro_stop), not through this function at all, so it isn't waiting on
// this button's own round-trip. This click handler just mirrors that same
// direct call for the mouse path.
async function stopMacro() {
  try { await pywebview.api.stop_macro(); } catch (e) {}
}

async function togglePauseMacro() {
  try {
    const macro = await pywebview.api.is_macro_running();
    if (macro.paused) await pywebview.api.resume_macro();
    else await pywebview.api.pause_macro();
  } catch (e) {}
}

// Renders a wins/losses split as a two-segment bar; with no runs yet, both
// segments collapse to 0% and the bar just shows its empty track color.
function setRatioBar(winsElId, lossesElId, wins, losses) {
  const total = wins + losses;
  document.getElementById(winsElId).style.width = total ? `${(wins / total) * 100}%` : '0%';
  document.getElementById(lossesElId).style.width = total ? `${(losses / total) * 100}%` : '0%';
}

// Run History panel. Each run: {result: 'win'|'loss', map, duration, ago}.
// Rebuilt only when the data actually changes, so the 1.5s status poll isn't
// tearing down and recreating identical DOM (which would also kill hover).
let lastRunHistoryJson = '';

function renderRunHistory(runs) {
  const json = JSON.stringify(runs);
  if (json === lastRunHistoryJson) return;
  lastRunHistoryJson = json;

  const list = document.getElementById('run-history-list');
  const count = document.getElementById('run-history-count');
  list.innerHTML = '';
  count.textContent = runs.length ? `${runs.length} run${runs.length === 1 ? '' : 's'}` : '';
  if (!runs.length) {
    const empty = document.createElement('div');
    empty.className = 'rh-empty';
    empty.textContent = 'No runs yet';
    list.appendChild(empty);
    return;
  }
  for (const run of runs) {
    const row = document.createElement('div');
    row.className = 'rh-row';
    row.style.setProperty('--rh', run.result === 'win' ? 'var(--teal)' : 'var(--rose)');
    const chip = document.createElement('span');
    chip.className = 'rh-chip';
    chip.textContent = run.result === 'win' ? 'W' : 'L';
    const map = document.createElement('span');
    map.className = 'rh-map';
    map.textContent = run.map || '-';
    const meta = document.createElement('span');
    meta.className = 'rh-meta';
    meta.textContent = [run.duration, run.ago].filter(Boolean).join(' · ');
    row.append(chip, map, meta);
    list.appendChild(row);
  }
}

// ---------------------------------------------------------------------------
// Settings screen
// ---------------------------------------------------------------------------
function setSettingsCategory(cat) {
  document.querySelectorAll('.settings-cat-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.cat === cat));
  document.querySelectorAll('.settings-category').forEach(sec => {
    sec.style.display = (cat === 'all' || sec.dataset.cat === cat) ? 'block' : 'none';
  });
}

// Restarts the .bounce keyframe animation on every click, even if the toggle
// was already mid-bounce -- removing then re-adding the class alone wouldn't
// replay it, the reflow (offsetWidth read) in between forces the restart.
function bounceToggle(btn) {
  btn.classList.remove('bounce');
  void btn.offsetWidth;
  btn.classList.add('bounce');
}

async function toggleSetting(key, btn) {
  const isOn = !btn.classList.contains('on');
  btn.classList.toggle('on', isOn);
  bounceToggle(btn);
  try { await pywebview.api.set_setting(key, isOn); } catch (e) {}
}

let rebindingAction = null;

// Mirrors main.py's HOTKEY_DEFAULTS so the per-row x button can restore an
// action's ORIGINAL key without a round-trip; unbinding is done by pressing
// Esc during capture instead.
const HOTKEY_DEFAULTS = {
  toggle_game: 'f4', skip_waiting: '', macro_start: 'f1', macro_stop: 'f2', debug_screenshot: 'f3',
};

// Reflects one hotkey's state into its button text and shows/hides its
// reset (x) button -- x means "back to the default key", so it only shows
// while the current binding differs from that default.
function updateKeybindDisplay(action, key) {
  const btn = document.getElementById(`keybind-${action}`);
  const clearBtn = document.getElementById(`keybind-clear-${action}`);
  if (btn) {
    btn.textContent = key ? key.toUpperCase() : 'Unbound';
    if (clearBtn) clearBtn.style.visibility = (key || '') !== (HOTKEY_DEFAULTS[action] || '') ? 'visible' : 'hidden';
  }
  // Dashboard Start/Stop show their bound key right on the button (see
  // .rp-btn-hotkey) -- kept in sync here too, whichever action changed, so
  // a rebind/reset from Settings shows up immediately without needing to
  // revisit the Dashboard to pick it up.
  const dashboardKeyEl = document.getElementById(
    action === 'macro_start' ? 'btn-macro-start-key' : action === 'macro_stop' ? 'btn-macro-stop-key' : null);
  if (dashboardKeyEl) dashboardKeyEl.textContent = key ? key.toUpperCase() : '';
}

function startRebind(action, btn) {
  rebindingAction = action;
  btn.textContent = 'Press a key...';
  btn.classList.add('listening');
}

function mapKeyName(e) {
  const special = {
    ' ': 'space', 'Escape': 'esc', 'Control': 'ctrl', 'Shift': 'shift', 'Alt': 'alt',
    'ArrowUp': 'up', 'ArrowDown': 'down', 'ArrowLeft': 'left', 'ArrowRight': 'right',
  };
  if (special[e.key] !== undefined) return special[e.key];
  return e.key.toLowerCase();
}

document.addEventListener('keydown', (e) => {
  if (!rebindingAction) return;
  e.preventDefault();
  const action = rebindingAction;
  rebindingAction = null;
  // Esc = deliberately set Unbound, not "bind to the Esc key".
  const keyName = e.key === 'Escape' ? '' : mapKeyName(e);
  document.getElementById(`keybind-${action}`).classList.remove('listening');
  updateKeybindDisplay(action, keyName);
  try { pywebview.api.set_hotkey(action, keyName); } catch (err) {}
});

// The per-row x: restores that action's original default key. (Unbinding
// lives on Esc-during-capture, not here.)
function clearHotkey(action) {
  const def = HOTKEY_DEFAULTS[action] || '';
  updateKeybindDisplay(action, def);
  try { pywebview.api.set_hotkey(action, def); } catch (e) {}
}

async function resetHotkeys() {
  try {
    const result = await pywebview.api.reset_hotkeys();
    const hk = result.hotkeys || {};
    updateKeybindDisplay('toggle_game', hk.toggle_game || '');
    updateKeybindDisplay('skip_waiting', hk.skip_waiting || '');
    updateKeybindDisplay('macro_start', hk.macro_start || '');
    updateKeybindDisplay('macro_stop', hk.macro_stop || '');
    updateKeybindDisplay('debug_screenshot', hk.debug_screenshot || '');
  } catch (e) {}
}

// ---- Theme ----
// Swatch colors mirror the --brand each data-theme sets in style.css; the
// swatch row itself never re-tints (each chip pins its own --sw) so you can
// always see every option regardless of the active theme.
const THEMES = {
  default: '#7c9dff', ocean: '#58a6ff', emerald: '#3fbf8f', sakura: '#e87a9e',
  violet: '#a878f0', sunset: '#e8935a', crimson: '#e05a6d', mono: '#aab2c8',
};
let activeTheme = 'default';

function applyTheme(name, announce) {
  activeTheme = THEMES[name] ? name : 'default';
  if (activeTheme === 'default') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = activeTheme;
  renderThemePicker();
  if (announce) {
    const label = activeTheme[0].toUpperCase() + activeTheme.slice(1);
    addLog(`[Theme] Loaded: ${label}`);
  }
}

function setTheme(name) {
  applyTheme(name, true);
  try { pywebview.api.set_setting('theme', activeTheme); } catch (e) {}
}

function renderThemePicker() {
  const el = document.getElementById('theme-picker');
  if (!el) return;
  el.innerHTML = Object.entries(THEMES).map(([name, color]) => `
    <button class="theme-swatch ${name === activeTheme ? 'active' : ''}" style="--sw: ${color};"
            onclick="setTheme('${name}')" data-tooltip="${name[0].toUpperCase() + name.slice(1)}"></button>
  `).join('');
}

async function loadSettingsUI() {
  try {
    const s = await pywebview.api.get_settings();
    document.getElementById('toggle-start-minimized').classList.toggle('on', !!s.start_minimized);
    const debugScreenshotsEl = document.getElementById('toggle-debug-screenshots');
    if (debugScreenshotsEl) debugScreenshotsEl.classList.toggle('on', !!s.debug_screenshots);
    applyTheme(s.theme || 'default', true);
    const scrollPowerEl = document.getElementById('story-scroll-power');
    if (scrollPowerEl) scrollPowerEl.value = s.story_scroll_power ?? 3;
    const scrollNudgesEl = document.getElementById('story-scroll-nudges');
    if (scrollNudgesEl) scrollNudgesEl.value = s.story_scroll_nudges ?? 8;
  } catch (e) {
    renderThemePicker();  // settings unreadable -- still show the picker at its default
  }
  try {
    const hk = await pywebview.api.get_hotkeys();
    updateKeybindDisplay('toggle_game', hk.toggle_game || '');
    updateKeybindDisplay('skip_waiting', hk.skip_waiting || '');
    updateKeybindDisplay('macro_start', hk.macro_start || '');
    updateKeybindDisplay('macro_stop', hk.macro_stop || '');
    updateKeybindDisplay('debug_screenshot', hk.debug_screenshot || '');
    updateDashboardHotkeys(hk);
  } catch (e) {}
  try {
    const r = await pywebview.api.get_reward_region();
    document.getElementById('reward-x').value = r.x;
    document.getElementById('reward-y').value = r.y;
    document.getElementById('reward-w').value = r.width;
    document.getElementById('reward-h').value = r.height;
  } catch (e) {}
  try {
    const s = await pywebview.api.get_stats_region();
    document.getElementById('stats-x').value = s.x;
    document.getElementById('stats-y').value = s.y;
    document.getElementById('stats-w').value = s.width;
    document.getElementById('stats-h').value = s.height;
  } catch (e) {}
  loadWebhookUI();
}

async function saveRewardRegion() {
  const val = (id) => parseInt(document.getElementById(id).value, 10) || 0;
  try {
    await pywebview.api.save_reward_region(val('reward-x'), val('reward-y'), val('reward-w'), val('reward-h'));
  } catch (e) {}
}

async function saveStatsRegion() {
  const val = (id) => parseInt(document.getElementById(id).value, 10) || 0;
  try {
    await pywebview.api.save_stats_region(val('stats-x'), val('stats-y'), val('stats-w'), val('stats-h'));
  } catch (e) {}
}

async function resetRewardRegion() {
  try {
    const r = await pywebview.api.reset_reward_region();
    document.getElementById('reward-x').value = r.x;
    document.getElementById('reward-y').value = r.y;
    document.getElementById('reward-w').value = r.width;
    document.getElementById('reward-h').value = r.height;
    addLog('[Debug] Reward Reader region reset to defaults.');
  } catch (e) {}
}

async function resetStatsRegion() {
  try {
    const s = await pywebview.api.reset_stats_region();
    document.getElementById('stats-x').value = s.x;
    document.getElementById('stats-y').value = s.y;
    document.getElementById('stats-w').value = s.width;
    document.getElementById('stats-h').value = s.height;
    addLog('[Debug] Game Stats region reset to defaults.');
  } catch (e) {}
}

// Scroll Power/Attempts default to 3/8 (see main.py's start_macro) --
// plain client-side reset through the existing generic set_setting, no
// dedicated backend endpoint needed since there's nothing else to persist.
async function resetStoryScrollSettings() {
  document.getElementById('story-scroll-power').value = 3;
  document.getElementById('story-scroll-nudges').value = 8;
  try {
    await pywebview.api.set_setting('story_scroll_power', 3);
    await pywebview.api.set_setting('story_scroll_nudges', 8);
  } catch (e) {}
  addLog('[Debug] Story map scroll settings reset to defaults.');
}

async function previewRewardRegion(btn) {
  const original = btn.textContent;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.preview_reward_region();
    btn.textContent = result.ok ? 'Saved' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1800);
}

// Same reasoning as saveDebugScreenshot() below: the game is hidden whenever
// you're not on the Dashboard (see switchScreen()), so capturing its reward
// row from the Settings screen would just grab whatever's behind the hidden
// window instead of the actual game -- switch over and let it settle first.
// read_rewards() only blocks on the capture + scroll (~1s) -- the actual OCR
// runs in a background Python thread and streams its results into the
// Process Log as [Rewards] lines instead of coming back with this call, so
// there's no item count to show here. The button just confirms the capture
// started; watch the Process Log for what it actually found.
async function readRewards(btn) {
  const original = btn.textContent;
  const mapName = document.getElementById('reward-test-map')?.value || '';
  const stage = document.getElementById('reward-test-stage')?.value || '';
  const difficulty = document.getElementById('reward-test-difficulty')?.value || 'Normal';
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Reading...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.read_rewards(mapName, stage, difficulty);
    btn.textContent = result.ok ? 'Started' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1800);
}

async function loadRewardTestMaps() {
  const sel = document.getElementById('reward-test-map');
  if (!sel) return;
  try {
    const maps = await pywebview.api.list_stage_data_maps();
    const prev = sel.value;
    sel.innerHTML = '<option value="">Map (optional)</option>' + maps.map(m => `<option value="${m}">${m}</option>`).join('');
    sel.value = prev;
  } catch (e) {}
}

async function previewStatsRegion(btn) {
  const original = btn.textContent;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.preview_stats_region();
    btn.textContent = result.ok ? 'Saved' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1800);
}

// Same reasoning as readRewards() above: the game is hidden whenever you're
// not on the Dashboard (see switchScreen()), so capturing its stats panel
// from the Settings screen would just grab whatever's behind the hidden
// window instead of the actual game -- switch over and let it settle first.
async function readGameStats(btn) {
  const original = btn.textContent;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Reading...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.read_game_stats();
    btn.textContent = result.ok ? 'Read' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1800);
}

// ---- Debug ----
// Switches to the Dashboard first (so Roblox is actually visible/un-hidden --
// it's shown/hidden per-screen, see switchScreen()) and gives it a moment to
// settle before asking Python to grab and save the screenshot; doesn't touch
// docking/parenting at all, unlike the old top-left debug button that fought
// the dock watchdog and thrashed the UI.
// btn is optional -- the F3 hotkey (see main.py's hotkey wiring) triggers
// this with no button element behind it at all, so every touch of btn below
// is guarded instead of assuming a click always started this.
async function saveDebugScreenshot(btn) {
  const original = btn ? btn.textContent : null;
  switchScreen('dashboard');
  if (btn) { btn.disabled = true; btn.textContent = 'Capturing...'; }
  await new Promise(resolve => setTimeout(resolve, 400));
  let result = null;
  try {
    result = await pywebview.api.save_debug_screenshot();
    if (btn) btn.textContent = result.ok ? 'Saved' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    if (btn) btn.textContent = 'Failed';
  }
  if (!btn) {
    // Success (and most failure reasons) already get their own line from
    // push_log on the Python side -- only the reasons that return silently
    // there (no_roblox/bad_region) need a line added here.
    if (result && !result.ok && (result.reason === 'no_roblox' || result.reason === 'bad_region')) {
      addLog(`[Debug] Screenshot failed: ${result.reason}`);
    }
    return;
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1600);
}

// Settings > Debug > "Story Map Region" -- same dance as saveDebugScreenshot:
// the game only renders on the Dashboard, so switch there and let it settle
// before asking Python to grab the band core.stage_select searches.
async function saveStoryMapRegionDebug(btn) {
  const original = btn.textContent;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Capturing...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.debug_story_map_region();
    btn.textContent = result.ok ? 'Saved' : `Failed (${result.reason || 'error'})`;
    if (result.ok) addLog(`[Debug] Story map region saved: ${result.path}`);
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1600);
}

// Settings > Debug > "Camera Setup" -- the backend does the right-drag +
// zoom-hold on its own thread (~3s); the game has to be visible and focused,
// so switch to the Dashboard first, same as every other live-input debug
// action.
async function runCameraSetup(btn) {
  const original = btn.textContent;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Running...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.debug_camera_setup();
    btn.textContent = result.ok ? 'Started' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3200);
}

// Settings > Debug > "Test Walking Path" -- replays a saved WASD recording
// (see core.paths.replay_events) against the live game so a Custom Path can
// be sanity-checked on its own. Run/Stop swap visibility instead of one
// button changing label, since a replay can run long enough that "click
// Stop mid-walk" needs to stay available the whole time, not just flash by.
async function runTestPath(btn) {
  const name = document.getElementById('debug-path-select').value;
  if (!name) return;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Starting...';
  await new Promise(resolve => setTimeout(resolve, 300));
  try {
    const result = await pywebview.api.debug_test_path(name);
    if (result.ok) {
      btn.style.display = 'none';
      document.getElementById('btn-stop-test-path').style.display = '';
    } else {
      btn.textContent = `Failed (${result.reason || 'error'})`;
      setTimeout(() => { btn.textContent = 'Run'; btn.disabled = false; }, 1800);
      return;
    }
  } catch (e) {
    btn.textContent = 'Failed';
    setTimeout(() => { btn.textContent = 'Run'; btn.disabled = false; }, 1800);
    return;
  }
  btn.textContent = 'Run';
  btn.disabled = false;
}

async function stopTestPath(btn) {
  try { await pywebview.api.stop_test_path(); } catch (e) {}
  btn.style.display = 'none';
  document.getElementById('btn-test-path').style.display = '';
}

async function loadWebhookUI() {
  try {
    const wh = await pywebview.api.get_webhook_settings();
    const urlInput = document.getElementById('webhook-url');
    urlInput.value = wh.url || '';
    // An already-saved webhook link is sensitive (anyone who has it can post
    // to your Discord channel) and isn't something you need to actually read
    // on every visit to Settings -- mask it like a password by default,
    // reveal on focus/click so it's still there to copy or edit when needed.
    urlInput.type = wh.url ? 'password' : 'text';
    document.getElementById('webhook-mention-id').value = wh.mention_id || '';
    document.getElementById('toggle-webhook-enabled').classList.toggle('on', !!wh.enabled);
    document.getElementById('toggle-webhook-silent').classList.toggle('on', !!wh.silent);
    updateWebhookValidity(wh.url || '');
  } catch (e) {}
}

function revealWebhookUrl() {
  document.getElementById('webhook-url').type = 'text';
}

function maskWebhookUrl() {
  const el = document.getElementById('webhook-url');
  if (el.value) el.type = 'password';  // an empty field (still being typed into) stays visible
}

function setWebhookStatus(text, color) {
  const el = document.getElementById('webhook-status-text');
  if (!el) return;
  el.textContent = text;
  el.style.color = color || 'var(--text-muted)';
}

// Recomputes the whole panel state -- the inline validity dot next to the URL
// input, plus the header chip and the "Delivery" hero readout. valid has three
// states: null (no URL / backend unreachable), false (bad URL), true (linked).
async function updateWebhookValidity(url) {
  let valid = null;
  if (url) {
    try { valid = (await pywebview.api.validate_webhook_url(url)).valid; }
    catch (e) { valid = null; }
  }

  const dot = document.getElementById('webhook-validity');
  if (dot) {
    const c = valid == null ? 'var(--text-muted)' : valid ? 'var(--teal)' : 'var(--rose)';
    dot.style.background = c;
    dot.style.color = c;
  }

  const chip = document.getElementById('webhook-chip');
  const heroDot = document.getElementById('webhook-dot');
  const state = document.getElementById('webhook-state-text');
  if (!chip || !heroDot || !state) return;

  const enabled = document.getElementById('toggle-webhook-enabled').classList.contains('on');
  let chipText, chipColor, heroText, heroColor, live = false;
  if (valid === false) {
    chipText = 'Invalid URL'; chipColor = 'var(--rose)';
    heroText = 'Invalid URL'; heroColor = 'var(--rose)';
  } else if (valid === null) {
    chipText = 'Not linked'; chipColor = 'var(--text-muted)';
    heroText = 'Not linked'; heroColor = 'var(--text-dim)';
  } else if (enabled) {
    chipText = 'Linked'; chipColor = 'var(--teal)';
    heroText = 'Active'; heroColor = 'var(--teal)'; live = true;
  } else {
    chipText = 'Linked'; chipColor = 'var(--teal)';
    heroText = 'Off'; heroColor = 'var(--text-dim)';
  }
  chip.textContent = chipText;
  chip.style.color = chipColor;
  heroDot.style.color = live ? 'var(--teal)' : (valid === false ? 'var(--rose)' : 'var(--text-muted)');
  heroDot.classList.toggle('live', live);
  state.textContent = heroText;
  state.style.color = heroColor;
}

document.addEventListener('input', (e) => {
  if (e.target && e.target.id === 'webhook-url') updateWebhookValidity(e.target.value.trim());
});

async function pasteWebhookUrl() {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    document.getElementById('webhook-url').value = text;
    updateWebhookValidity(text);
  } catch (e) {
    setWebhookStatus('Could not read the clipboard, paste manually with Ctrl+V.', 'var(--rose)');
  }
}

async function toggleWebhookField(field, btn) {
  btn.classList.toggle('on', !btn.classList.contains('on'));
  bounceToggle(btn);
  await saveWebhookSettings(true);
}

// Called on every field's onchange -- there's no explicit Save button, this
// is the only save path.
async function saveWebhookSettings(silentSave) {
  const url = document.getElementById('webhook-url').value.trim();
  const mentionId = document.getElementById('webhook-mention-id').value.trim();
  const enabled = document.getElementById('toggle-webhook-enabled').classList.contains('on');
  const silent = document.getElementById('toggle-webhook-silent').classList.contains('on');
  try {
    await pywebview.api.save_webhook_settings(url, enabled, silent, mentionId);
    updateWebhookValidity(url);
    if (!silentSave) setWebhookStatus('Saved.', 'var(--teal)');
  } catch (e) {
    if (!silentSave) setWebhookStatus('Failed to save.', 'var(--rose)');
  }
}

// ---------------------------------------------------------------------------
// Task screen: self-editing card queue (reference: Anime Squadron macro UI)
// ---------------------------------------------------------------------------
// Each queued task is one card with inline dropdowns -- no separate config
// form. Infinite/Mastery live in the *Stage* picker (picking one hides the
// Difficulty picker entirely, since in-game they're locked to Hard);
// Equipment only shows once a Team Loadout is chosen (with no team there's
// no loadout to include equipment from); Macro Operation runs one of the
// Creation tab's saved templates during the task's matches.
const TASK_DATA = {
  story: {
    label: 'Story',
    maps: ['School Grounds', 'Rose Kingdom', 'Fairy King Forest', "King's Tomb"],
    stages: ['1', '2', '3', '4', '5', 'Infinite', 'Mastery'],
    difficulties: ['Normal', 'Hard'],
  },
  raid: {
    label: 'Raid',
    maps: ['Spirit City'],
    stages: ['1', '2', '3'],
    fixedDifficulty: 'Hard',
  },
  challenge: {
    label: 'Challenge',
    types: ['Regular', 'Daily', 'Weekly'],
    numbers: ['1', '2', '3'],
  },
  expedition: { label: 'Expedition' },
};

let taskCards = [];
let selectedTaskId = null;
let taskTemplates = [];  // Creation template names, for the Macro Operation picker
let taskSaveTimer = null;

function newTaskId() {
  return 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function defaultTask() {
  return {
    id: newTaskId(), mode: 'story',
    map: TASK_DATA.story.maps[0], stage: '1', difficulty: 'Normal',
    challenge_type: 'Regular', challenge_number: '1',
    repeat: 1, team: '', equipment: 'include', play_mode: 'solo', macro: '',
  };
}

function findTask(id) { return taskCards.find(t => t.id === id); }

// Debounced whole-list save -- every inline edit funnels through here, so
// rapid changes (typing a repeat count) collapse into one write.
function saveTaskQueue() {
  clearTimeout(taskSaveTimer);
  taskSaveTimer = setTimeout(() => {
    try { pywebview.api.save_tasks(taskCards); } catch (e) {}
  }, 350);
}

async function refreshTaskTemplates() {
  try { taskTemplates = await pywebview.api.list_templates(); } catch (e) { taskTemplates = []; }
}

// Export bundles the queue AND every Creation template the tasks reference
// (a task's `macro` is just a template name -- exported alone it would point
// at nothing on someone else's machine). Import restores both, giving all
// tasks fresh ids and never overwriting a template that already exists
// locally under the same name.
async function exportTasks() {
  if (taskCards.length === 0) { addLog('[Task] Nothing to export -- the queue is empty.'); return; }
  const templates = {};
  for (const t of taskCards) {
    if (t.macro && !(t.macro in templates)) {
      try { templates[t.macro] = await pywebview.api.load_template(t.macro); } catch (e) {}
    }
  }
  const payload = {
    kind: 'anime-expeditions-tasks', version: 1, exported: new Date().toISOString(),
    tasks: taskCards, templates,
  };
  let result = null;
  try { result = await pywebview.api.export_tasks_file(payload); } catch (e) {}
  if (result && result.ok) addLog(`[Task] Exported ${taskCards.length} task(s) to ${result.path}`);
  else if (result && result.reason !== 'cancelled') addLog(`[Task] Export failed: ${result.reason || 'error'}`);
}

async function importTasks() {
  let result = null;
  try { result = await pywebview.api.import_tasks_file(); } catch (e) {}
  if (!result || !result.ok) {
    if (result && result.reason !== 'cancelled') addLog(`[Task] Import failed: ${result.reason || 'error'}`);
    return;
  }
  const data = result.data || {};
  if (!Array.isArray(data.tasks)) { addLog('[Task] Import failed: that file is not a task export.'); return; }
  let tplAdded = 0;
  try {
    const existing = await pywebview.api.list_templates();
    for (const [name, t] of Object.entries(data.templates || {})) {
      if (existing.includes(name) || !t || !Array.isArray(t.blocks)) continue;
      try { await pywebview.api.save_template(name, t.blocks); tplAdded++; } catch (e) {}
    }
  } catch (e) {}
  let added = 0;
  for (const t of data.tasks) {
    taskCards.push({ ...defaultTask(), ...t, id: newTaskId() });
    added++;
  }
  await refreshTaskTemplates();
  renderTaskList();
  renderTaskBuilder();
  saveTaskQueue();
  addLog(`[Task] Imported ${added} task(s)${tplAdded ? ` and ${tplAdded} macro template(s)` : ''}.`);
}

function addTaskCard() {
  const t = defaultTask();
  taskCards.push(t);
  selectedTaskId = t.id;
  renderTaskList();
  renderTaskBuilder();
  saveTaskQueue();
  const list = document.getElementById('task-list');
  if (list) list.scrollTop = list.scrollHeight;
}

function cloneTaskCard(id) {
  const idx = taskCards.findIndex(t => t.id === id);
  if (idx === -1) return;
  const copy = { ...taskCards[idx], id: newTaskId() };
  taskCards.splice(idx + 1, 0, copy);
  selectedTaskId = copy.id;
  renderTaskList();
  renderTaskBuilder();
  saveTaskQueue();
}

function removeTaskCard(id) {
  const el = document.getElementById('task_' + id);
  const drop = () => {
    taskCards = taskCards.filter(t => t.id !== id);
    if (selectedTaskId === id) selectedTaskId = null;
    renderTaskList();
    renderTaskBuilder();
    saveTaskQueue();
  };
  // Let the exit animation play before the row actually disappears.
  if (el) { el.classList.add('removing'); setTimeout(drop, 170); } else drop();
}

function clearTaskQueue() {
  taskCards = [];
  selectedTaskId = null;
  renderTaskList();
  renderTaskBuilder();
  saveTaskQueue();
}

function selectTaskCard(id) {
  selectedTaskId = selectedTaskId === id ? null : id;
  document.querySelectorAll('#task-list .task-card').forEach(el => {
    el.classList.toggle('selected', el.id === 'task_' + selectedTaskId);
  });
  renderTaskBuilder();
}

function setTaskProp(id, key, value) {
  const t = findTask(id);
  if (!t) return;
  t[key] = value;
  // These change which controls the Builder shows (stage list, hidden
  // difficulty, number picker) -- rebuild it to reflect that. The queue row
  // labels re-render on every change either way, but the Builder is only
  // rebuilt when the *shape* changed so typing in the Repeat field doesn't
  // lose focus mid-keystroke to an innerHTML swap.
  const structural = ['mode', 'stage', 'challenge_type'];
  if (key === 'mode') {
    const d = TASK_DATA[t.mode];
    if (d.maps) t.map = d.maps[0];
    if (d.stages) t.stage = d.stages[0];
    if (d.difficulties) t.difficulty = d.difficulties[0];
  }
  renderTaskList();
  if (structural.includes(key)) renderTaskBuilder();
  saveTaskQueue();
}

function taskOpts(list, current, fmt) {
  return list.map(o => `<option value="${o}" ${String(o) === String(current) ? 'selected' : ''}>${fmt ? fmt(o) : o}</option>`).join('');
}

// One accent per mode so the queue scans by color before you even read it.
const TASK_MODE_COLORS = { story: 'var(--brand)', raid: 'var(--rose)', challenge: 'var(--amber)', expedition: 'var(--teal)' };

// The two text lines a queue row shows for a task -- where it goes, then how
// it runs. All editing happens in the Builder, rows are read-only summaries.
function taskSummary(t) {
  const d = TASK_DATA[t.mode];
  let title = d.label;
  if (t.mode === 'story' || t.mode === 'raid') {
    title += ` · ${t.map} · ${/^\d+$/.test(t.stage) ? 'Stage ' + t.stage : t.stage}`;
  } else if (t.mode === 'challenge') {
    title += ` · ${t.challenge_type}${t.challenge_type === 'Regular' ? ' #' + t.challenge_number : ''}`;
  }
  const specialStage = t.mode === 'story' && (t.stage === 'Infinite' || t.stage === 'Mastery');
  const diff = (t.mode === 'story' && !specialStage) ? t.difficulty
             : (d.fixedDifficulty || specialStage) ? 'Hard' : '';
  const meta = [
    `×${t.repeat}`,
    diff,
    t.play_mode === 'matchmaking' ? 'Matchmaking' : 'Solo',
    t.macro ? `▸ ${t.macro}` : '',
  ].filter(Boolean).join(' · ');
  return { title, meta };
}

function renderQueueRow(t, idx) {
  const { title, meta } = taskSummary(t);
  return `
    <div class="task-card ${t.id === selectedTaskId ? 'selected' : ''}" id="task_${t.id}"
         style="--tqc: ${TASK_MODE_COLORS[t.mode] || 'var(--brand)'};" onclick="selectTaskCard('${t.id}')">
      <span class="task-grip" onclick="event.stopPropagation()">&#10247;</span>
      <span class="tq-index">${idx + 1}</span>
      <span class="tq-accent"></span>
      <div class="tq-text">
        <div class="tq-title">${title}</div>
        <div class="tq-meta">${meta}</div>
      </div>
      <button class="task-icon-btn clone" onclick="event.stopPropagation(); cloneTaskCard('${t.id}')" data-tooltip="Clone">&#10697;</button>
      <button class="task-icon-btn delete" onclick="event.stopPropagation(); removeTaskCard('${t.id}')" data-tooltip="Remove">&#10005;</button>
    </div>`;
}

function renderTaskList() {
  const el = document.getElementById('task-list');
  const countEl = document.getElementById('task-queue-count');
  if (countEl) countEl.textContent = taskCards.length ? `${taskCards.length} task${taskCards.length === 1 ? '' : 's'}` : '';
  if (!el) return;
  el.innerHTML = taskCards.length === 0
    ? '<div class="rh-empty">No tasks yet -- click "+ Add Task" to queue one.</div>'
    : taskCards.map(renderQueueRow).join('');
}

// The right-hand editor: every control gets a caption so nothing has to be
// decoded from a bare dropdown. Only ever shows the selected task.
function renderTaskBuilder() {
  const el = document.getElementById('task-builder');
  if (!el) return;
  const t = findTask(selectedTaskId);
  if (!t) {
    el.innerHTML = '<div class="rh-empty">Select a task on the left to edit it.</div>';
    return;
  }
  const d = TASK_DATA[t.mode];
  const sel = (key, options, fmt) => `
    <select class="task-select" onchange="setTaskProp('${t.id}', '${key}', this.value)">
      ${taskOpts(options, t[key], fmt)}
    </select>`;
  const field = (label, control) => `<div class="task-field"><span>${label}</span>${control}</div>`;

  const fields = [
    field('Mode', sel('mode', Object.keys(TASK_DATA), k => TASK_DATA[k].label)),
    field('Repeat', `<div class="task-rep-group" style="width: 100%;">&times;<input type="number" min="1" value="${t.repeat}"
      oninput="setTaskProp('${t.id}', 'repeat', Math.max(1, parseInt(this.value, 10) || 1))"></div>`),
  ];

  if (t.mode === 'story' || t.mode === 'raid') {
    fields.push(field('Map', sel('map', d.maps)));
    fields.push(field('Stage', sel('stage', d.stages, s => /^\d+$/.test(s) ? 'Stage ' + s : s)));
  } else if (t.mode === 'challenge') {
    fields.push(field('Challenge Type', sel('challenge_type', d.types, ty => ty + ' Challenge')));
    if (t.challenge_type === 'Regular') fields.push(field('Number', sel('challenge_number', d.numbers, n => '#' + n)));
  }

  const specialStage = t.mode === 'story' && (t.stage === 'Infinite' || t.stage === 'Mastery');
  if (t.mode === 'story' && !specialStage) {
    fields.push(field('Difficulty', sel('difficulty', d.difficulties)));
  } else if (d.fixedDifficulty || specialStage) {
    fields.push(field('Difficulty', `<span class="task-chip" style="align-self: flex-start;">Hard &middot; locked</span>`));
  }

  const playSeg = `
    <div class="seg-toggle">
      <button type="button" class="seg-btn ${t.play_mode === 'solo' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'play_mode', 'solo'); renderTaskBuilder()">Solo</button>
      <button type="button" class="seg-btn ${t.play_mode === 'matchmaking' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'play_mode', 'matchmaking'); renderTaskBuilder()">Matchmaking</button>
    </div>`;
  fields.push(field('Play Mode', playSeg));

  // Team Loadout rides with the chosen template (see the Creation tab), so the
  // macro picker is the only loadout-related control left on a task.
  const macroSel = `
    <select class="task-select" onchange="setTaskProp('${t.id}', 'macro', this.value)">
      <option value="">No Macro</option>
      ${taskTemplates.map(n => `<option value="${n}" ${n === t.macro ? 'selected' : ''}>&#9654; ${n}</option>`).join('')}
    </select>`;
  fields.push(field('Macro Operation', macroSel));

  el.innerHTML = `
    <div class="task-builder-grid">${fields.join('')}</div>
    <div class="wh-hint" style="margin-top: 8px;">The macro's Team Loadout comes from its template (Creation tab).</div>
    <div class="flex items-center gap-2" style="margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border);">
      <button class="task-toolbar-btn add" onclick="cloneTaskCard('${t.id}')">&#10697; Clone Task</button>
      <span class="flex-1"></span>
      <button class="task-toolbar-btn danger" onclick="removeTaskCard('${t.id}')">&#10005; Remove Task</button>
    </div>`;
}

async function refreshTaskQueue() {
  await refreshTaskTemplates();
  try {
    // Merge over defaults, then migrate tasks saved by the old form-based
    // Task screen: team was null instead of '', stage was a number, and
    // Infinite/Mastery lived in the difficulty dropdown before they moved
    // into the Stage picker.
    taskCards = (await pywebview.api.get_tasks()).map(saved => {
      const t = { ...defaultTask(), ...saved };
      if (t.team == null) t.team = '';
      t.stage = String(t.stage);
      if (t.difficulty === 'Infinite' || t.difficulty === 'Mastery') {
        t.stage = t.difficulty;
        t.difficulty = 'Normal';
      }
      t.challenge_type = String(t.challenge_type || 'Regular').replace(' Challenge', '');
      t.challenge_number = String(t.challenge_number || '1');
      return t;
    });
  } catch (e) {
    taskCards = [];
  }
  renderTaskList();
  renderTaskBuilder();
}

// ── Task drag-reorder: grip-drag with a floating ghost + drop indicator ──
(function () {
  let dragTask = null, ghost = null, indicator = null;

  function taskLabel(t) {
    const d = TASK_DATA[t.mode];
    let s = d.label;
    if (t.mode === 'story' || t.mode === 'raid') s += ` · ${t.map} · ${/^\d+$/.test(t.stage) ? 'Stage ' + t.stage : t.stage}`;
    if (t.mode === 'challenge') s += ` · ${t.challenge_type}`;
    return `${s} ×${t.repeat}`;
  }

  function dropTargetAt(y) {
    const list = document.getElementById('task-list');
    const cards = [...list.querySelectorAll('.task-card')].filter(c => c.id !== 'task_' + dragTask.id);
    for (const c of cards) {
      const r = c.getBoundingClientRect();
      if (y < r.top + r.height / 2) return c;
    }
    return null;
  }

  document.addEventListener('mousedown', e => {
    const grip = e.target.closest('#task-list .task-grip');
    if (!grip) return;
    e.preventDefault();
    const cardEl = grip.closest('.task-card');
    dragTask = findTask(cardEl.id.replace('task_', ''));
    if (!dragTask) return;

    const rect = cardEl.getBoundingClientRect();
    ghost = document.createElement('div');
    ghost.className = 'drag-ghost';
    ghost.textContent = taskLabel(dragTask);
    document.body.appendChild(ghost);
    ghost.style.left = rect.left + 'px';
    ghost.style.top = (e.clientY - 14) + 'px';

    indicator = document.createElement('div');
    indicator.className = 'drop-indicator';

    cardEl.classList.add('dragging');
    document.body.style.cursor = 'grabbing';
  });

  document.addEventListener('mousemove', e => {
    if (!dragTask || !ghost) return;
    ghost.style.top = (e.clientY - 14) + 'px';
    ghost.style.left = (e.clientX + 14) + 'px';
    const list = document.getElementById('task-list');
    const before = dropTargetAt(e.clientY);
    if (before) list.insertBefore(indicator, before);
    else list.appendChild(indicator);
  });

  document.addEventListener('mouseup', e => {
    if (!dragTask) return;
    const before = dropTargetAt(e.clientY);
    const fromIdx = taskCards.findIndex(t => t.id === dragTask.id);
    const [moved] = taskCards.splice(fromIdx, 1);
    const toIdx = before ? taskCards.findIndex(t => t.id === before.id.replace('task_', '')) : taskCards.length;
    taskCards.splice(toIdx, 0, moved);

    if (ghost) ghost.remove();
    if (indicator) indicator.remove();
    ghost = indicator = null;
    dragTask = null;
    document.body.style.cursor = '';
    renderTaskList();
    saveTaskQueue();
  });
})();

// ---------------------------------------------------------------------------
// Creation screen: block-based drag-and-drop routine builder
// ---------------------------------------------------------------------------
// Pathing is no longer a draggable block: every routine's Pre Start phase has
// a permanent, pinned Walk Path row (auto-select by default, or a recorded
// custom path) -- see renderWalkRow(). The palette is just Units + Timing.
const BLOCK_TYPES = {
  place_unit:        { label: 'Place Unit',        group: 'Units',  color: 'var(--lilac)', params: [{ key: 'name', type: 'text', placeholder: 'unit', default: '' }, { key: 'x', type: 'number', placeholder: 'x', default: 0 }, { key: 'y', type: 'number', placeholder: 'y', default: 0 }] },
  // Upgrade/Auto Upgrade target a placed unit by its #index (the numbering
  // Place Unit rows and the map picker share) -- bespoke controls, see
  // renderUpgradeControls()/renderAutoUpgradeControls()/renderSellUnitControls().
  upgrade_unit:       { label: 'Upgrade Unit',      group: 'Units',  color: 'var(--brand)', params: [] },
  sell_unit:          { label: 'Sell Unit',         group: 'Units',  color: 'var(--rose)',  params: [] },
  auto_upgrade_unit:  { label: 'Auto Upgrade Unit', group: 'Units',  color: 'var(--amber)', params: [] },
  // Mid-battle repositioning: replays a recorded WASD path (same recordings
  // the pinned Walk Path row uses) -- picker rendered by renderWalkControls().
  walk:               { label: 'Walk',              group: 'Pathing', color: 'var(--teal)', params: [] },
  wait_ms:            { label: 'Wait (ms)',         group: 'Timing', color: 'var(--amber)', params: [{ key: 'ms', type: 'number', placeholder: 'ms', default: 500 }] },
  wait_wave:          { label: 'Wait for Wave',     group: 'Timing', color: 'var(--amber)', params: [{ key: 'wave', type: 'number', placeholder: 'wave', default: 1 }] },
  // Value's meaning depends on kind (hotkey: a key name, toggle: 'on'/'off',
  // slider: 0-2) -- one variable-shape control instead of three near-
  // identical block types, see renderSettingControls().
  setting_change:     { label: 'Setting',           group: 'Setup',  color: 'var(--slate)', params: [{ key: 'name', type: 'text', placeholder: 'setting name', default: '' }] },
};

// Two phases: Pre Start (walk to your spot, place starter units, flip any
// settings that need to be set before the match begins) and Battle
// (everything else -- upgrades/sells/waits only make sense once it's live).
const PHASES = ['prestart', 'battle'];
const PHASE_LABELS = { prestart: 'Pre Start', battle: 'Battle' };
const PHASE_TAGS = { prestart: 'Setup', battle: 'Combat' };
const PHASE_ALLOWED = {
  prestart: ['place_unit', 'setting_change'],
  battle: Object.keys(BLOCK_TYPES),
};

let creationPhases = { prestart: [], battle: [] };
let phaseCollapsed = { prestart: false, battle: false };
let recordingBlockId = null;
let savedPaths = [];

// The template's Team Loadout + the Pre Start walk config. Loadout used to
// live on each Task card; it belongs to the routine, so it saves with the
// template and the task inherits it through its Macro Operation pick.
let creationWalk = { mode: 'auto', pathName: '' };
let creationTeam = '';
let creationEquipment = 'include';

function newBlockId() {
  return 'b' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

// Blocks carry a globally-unique id regardless of which phase they're in, so
// every handler below (remove/update/toggle) just needs the id -- this finds
// which phase array + index actually owns it instead of threading a phase
// argument through every call site.
function findBlockLocation(id) {
  for (const phase of PHASES) {
    const idx = creationPhases[phase].findIndex(b => b.id === id);
    if (idx !== -1) return { phase, idx };
  }
  return null;
}

function addBlock(type, phase, atIndex) {
  const def = BLOCK_TYPES[type];
  if (!def) return;
  if (!PHASE_ALLOWED[phase].includes(type)) {
    addLog(`[Creation] Only Place Unit and Setting blocks can go in Pre Start -- "${def.label}" belongs in Battle.`);
    return;
  }
  const params = {};
  def.params.forEach(p => { params[p.key] = p.default; });
  const block = { id: newBlockId(), type, params, once: false };
  if (type === 'setting_change') { block.kind = 'toggle'; block.value = 'off'; }
  if (type === 'place_unit') { block.hotkey = ''; }
  if (type === 'walk') { block.params.path = ''; }
  if (type === 'upgrade_unit') { block.params.index = ''; block.params.times = 1; }
  if (type === 'auto_upgrade_unit') { block.params.index = ''; block.params.priority = 1; }
  if (type === 'sell_unit') { block.params.index = ''; }
  const list = creationPhases[phase];
  if (atIndex == null) list.push(block);
  else list.splice(atIndex, 0, block);
  renderPhases();
}

function removeBlock(id) {
  if (recordingBlockId === id) recordingBlockId = null;
  const loc = findBlockLocation(id);
  if (!loc) return;
  const el = document.querySelector(`#creation-phases .block-row[data-id="${id}"]`);
  const drop = () => {
    creationPhases[loc.phase].splice(loc.idx, 1);
    renderPhases();
  };
  // Let the exit animation play before the row actually disappears.
  if (el) { el.classList.add('removing'); setTimeout(drop, 170); } else drop();
}

// Duplicates a block right below itself, params and modifiers included --
// for repeating a nearly-identical step without re-picking everything.
function cloneBlock(id) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const src = creationPhases[loc.phase][loc.idx];
  const copy = { ...src, id: newBlockId(), params: { ...src.params } };
  creationPhases[loc.phase].splice(loc.idx + 1, 0, copy);
  renderPhases();
}

function updateBlockParam(id, key, value) {
  const loc = findBlockLocation(id);
  if (loc) creationPhases[loc.phase][loc.idx].params[key] = value;
}

function setWalkMode(mode) {
  creationWalk.mode = mode;
  renderPhases();
}

function setWalkPath(name) {
  creationWalk.pathName = name;
}

// "Once" -- a block flagged this way only runs the first time the routine
// executes, even across repeats (e.g. a starter placement that shouldn't
// happen again every loop).
function toggleBlockOnce(id) {
  const loc = findBlockLocation(id);
  if (loc) creationPhases[loc.phase][loc.idx].once = !creationPhases[loc.phase][loc.idx].once;
  renderPhases();
}

function togglePhaseCollapsed(phase) {
  phaseCollapsed[phase] = !phaseCollapsed[phase];
  renderPhases();
}

async function refreshSavedPaths() {
  try {
    savedPaths = await pywebview.api.list_paths();
  } catch (e) {
    savedPaths = [];
  }
  // Also keeps Settings > Debug > "Test Walking Path" and "Default Auto
  // Walk" in sync -- one saved-paths list feeds the Custom Path block
  // picker, the debug tester, and the per-map default picker.
  const options = savedPaths.length
    ? savedPaths.map(n => `<option value="${n}">${n}</option>`).join('')
    : '<option value="">No saved paths</option>';
  const sel = document.getElementById('debug-path-select');
  if (sel) { const prev = sel.value; sel.innerHTML = options; sel.value = prev; }
  const defaultSel = document.getElementById('default-walk-path');
  if (defaultSel) { const prev = defaultSel.value; defaultSel.innerHTML = options; defaultSel.value = prev; }
  await loadDefaultWalkPaths();
}

// Settings > Debug > "Default Auto Walk": map name -> saved path, so a
// template's Walk Path can stay on Auto for a map that already has a good
// recorded route instead of every template needing the same Custom path
// picked by hand.
async function loadDefaultWalkPaths() {
  const list = document.getElementById('default-walk-list');
  if (!list) return;
  let defaults = {};
  try { defaults = await pywebview.api.get_default_walk_paths(); } catch (e) {}
  const entries = Object.entries(defaults);
  list.innerHTML = entries.length === 0
    ? '<div class="text-xs" style="color: var(--text-muted); padding: 2px 0;">No defaults set yet.</div>'
    : entries.map(([map, path]) => `
        <div class="flex items-center gap-2 justify-between text-xs" style="padding: 4px 2px; color: var(--text-dim);">
          <span><b>${map}</b> &rarr; ${path}</span>
          <span class="block-delete" onclick="removeDefaultWalkPath('${map.replace(/'/g, "\\'")}')" data-tooltip="Remove">&times;</span>
        </div>`).join('');
}

async function setDefaultWalkPath() {
  const mapInput = document.getElementById('default-walk-map');
  const pathSel = document.getElementById('default-walk-path');
  const mapName = mapInput.value.trim();
  if (!mapName || !pathSel.value) return;
  try { await pywebview.api.set_default_walk_path(mapName, pathSel.value); } catch (e) {}
  mapInput.value = '';
  await loadDefaultWalkPaths();
}

async function removeDefaultWalkPath(mapName) {
  try { await pywebview.api.set_default_walk_path(mapName, ''); } catch (e) {}
  await loadDefaultWalkPaths();
}

// Settings > Debug > "Macro Coordinates" -- core.runner's fixed click points
// and search regions for the Select Stage screen, editable here instead of
// hardcoded so a game update shifting the UI just needs a number changed.
const MACRO_COORD_KEYS = [
  'difficulty_normal_x', 'difficulty_normal_y', 'difficulty_hard_x', 'difficulty_hard_y',
  'matchmaking_region_x', 'matchmaking_region_y', 'matchmaking_region_w', 'matchmaking_region_h',
];

async function loadMacroCoords() {
  let coords = {};
  try { coords = await pywebview.api.get_macro_coords(); } catch (e) {}
  for (const key of MACRO_COORD_KEYS) {
    const el = document.getElementById(`coord-${key}`);
    if (el) el.value = coords[key] ?? '';
  }
}

async function setMacroCoord(key, value) {
  const n = parseInt(value);
  if (Number.isNaN(n)) return;
  try { await pywebview.api.set_macro_coord(key, n); } catch (e) {}
}

async function resetMacroCoords() {
  try { await pywebview.api.reset_macro_coords(); } catch (e) {}
  await loadMacroCoords();
  addLog('[Debug] Macro coordinates reset to defaults.');
}

async function saveMatchmakingRegionDebug(btn) {
  const original = btn.textContent;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Capturing...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.debug_matchmaking_region();
    btn.textContent = result.ok ? 'Saved' : `Failed (${result.reason || 'error'})`;
    if (result.ok) addLog(`[Debug] Enter Matchmaking region saved: ${result.path}`);
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1600);
}

// Click once to start (Python polls WASD via start_path_recording), click
// again to stop -- naming happens *after* recording, at save time, so the
// player isn't stuck typing a name before they've even walked the path.
// blockId is always 'walkpath' now (the pinned Pre Start row is the only
// place recording lives), kept as a param so a future per-block recorder
// wouldn't need a rewrite.
// Where the freshly saved path name should land once the player names it in
// the modal: 'walkpath' = the pinned Walk Path row (creationWalk), anything
// else = that Walk block's own path param. Survives the gap between Stop
// and Save, which recordingBlockId (nulled on Stop) doesn't.
let pendingRecordingTarget = null;

function stopActiveRecording() {
  if (recordingBlockId) toggleRecordPath(recordingBlockId);
}

async function toggleRecordPath(blockId) {
  if (recordingBlockId === blockId) {
    pendingRecordingTarget = blockId;
    recordingBlockId = null;
    document.getElementById('rec-popout').style.display = 'none';
    // Kill the WASD poll FIRST (stop_path_capture), then ask for a name: the
    // poll reads physical keys regardless of focus, so typing a name that
    // contains w/a/s/d would otherwise tack phantom movement onto the path.
    let stopRes = null;
    try { stopRes = await pywebview.api.stop_path_capture(); } catch (e) {}
    renderPhases();
    // Back to Creation BEFORE showing the naming dialog: the docked Roblox
    // window paints over all DOM on the Dashboard, so a dialog there sits
    // invisibly behind the game. Creation hides Roblox entirely.
    switchScreen('creation');
    if (!stopRes || !stopRes.count) {
      addLog('[Creation] Nothing recorded -- no movement detected.');
      try { await pywebview.api.discard_pending_path(); } catch (e) {}
      return;
    }
    const input = document.getElementById('path-name-input');
    input.value = '';
    document.getElementById('path-name-modal').style.display = 'flex';
    setTimeout(() => input.focus(), 50);
    return;
  }
  if (recordingBlockId) return;  // already recording
  // The game-slot layout (where the docked Roblox window actually sits) only
  // exists on the Dashboard screen -- Creation hides Roblox entirely (see
  // switchScreen()), so recording has to switch there first or there'd be
  // nothing visible to walk in. start_path_recording() then hands Roblox
  // real OS focus so the player's WASD actually reaches the game instead of
  // this panel.
  switchScreen('dashboard');
  await new Promise(resolve => setTimeout(resolve, 200));
  try {
    const result = await pywebview.api.start_path_recording();
    if (result.ok) {
      recordingBlockId = blockId;
      document.getElementById('rec-popout').style.display = 'flex';
      addLog('[Creation] Recording path -- walk with WASD (I/O also recorded, timer starts on your first key), click Stop Recording when done.');
    } else {
      addLog(`[Creation] Couldn't start recording: ${result.reason || 'error'}`);
    }
  } catch (e) {}
  renderPhases();
}

// "Save Recorded Path" modal (#path-name-modal): Save persists the
// already-stopped capture (held in Python by stop_path_capture) under the
// typed name; Discard/x throws it away.
async function savePathName() {
  const name = document.getElementById('path-name-input').value.trim();
  if (!name) return;
  document.getElementById('path-name-modal').style.display = 'none';
  try {
    const result = await pywebview.api.save_pending_path(name);
    if (result.ok) {
      await refreshSavedPaths();
      const loc = pendingRecordingTarget && pendingRecordingTarget !== 'walkpath'
        ? findBlockLocation(pendingRecordingTarget) : null;
      if (loc) creationPhases[loc.phase][loc.idx].params.path = result.name;
      else creationWalk = { mode: 'custom', pathName: result.name };
      addLog(`[Creation] Saved path "${result.name}".`);
    } else {
      addLog(`[Creation] Couldn't save path: ${result.reason || 'error'}`);
    }
  } catch (e) {}
  pendingRecordingTarget = null;
  renderPhases();
}

async function discardPathRecording() {
  document.getElementById('path-name-modal').style.display = 'none';
  try { await pywebview.api.discard_pending_path(); } catch (e) {}
  pendingRecordingTarget = null;
  addLog('[Creation] Recording discarded.');
  renderPhases();
}

function renderPalette() {
  const el = document.getElementById('block-palette');
  if (!el) return;
  // Grouped by what the block acts on (Units / Pathing / Timing) so the
  // palette scans as sections instead of one undifferentiated stack.
  const groups = [];
  for (const [type, def] of Object.entries(BLOCK_TYPES)) {
    let g = groups.find(x => x.name === def.group);
    if (!g) { g = { name: def.group, chips: [] }; groups.push(g); }
    g.chips.push(`
      <div class="palette-chip" style="--chip: ${def.color};" draggable="true"
           ondragstart="event.dataTransfer.setData('block-type', '${type}')">
        <span style="width:10px;height:10px;border-radius:3px;background:${def.color};display:inline-block;flex-shrink:0;"></span>
        ${def.label}
      </div>`);
  }
  el.innerHTML = groups.map(g => `
    <div class="palette-group-label">${g.name}</div>
    ${g.chips.join('')}
  `).join('');
}

function renderParamInput(b, p) {
  if (p.type === 'select') {
    const opts = p.options.map(o => `<option value="${o}" ${String(o) === String(b.params[p.key]) ? 'selected' : ''}>${o === 'None' ? 'None' : 'Priority ' + o}</option>`).join('');
    return `<select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', '${p.key}', this.value)">${opts}</select>`;
  }
  // Text fields (unit/target/setting names) are cramped at the default
  // 64px -- number fields (x/y/ms/wave) stay narrow since they only ever
  // hold a few digits.
  const width = p.type === 'text' ? 'width:130px;' : '';
  return `
    <input class="block-input" style="${width}" type="${p.type}" value="${b.params[p.key]}" placeholder="${p.placeholder}"
           oninput="updateBlockParam('${b.id}', '${p.key}', this.value)">`;
}

// Setting block: the value control's shape follows `kind` -- a key-capture
// button, an On/Off toggle, or a 0-2 slider -- so this can't be expressed as
// one of the static `params` field types renderParamInput handles. Place
// Unit's hotkey field shares the same capture mechanism (see
// startBlockHotkeyCapture below), just writing to a different block field.
function setSettingKind(id, kind) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const b = creationPhases[loc.phase][loc.idx];
  b.kind = kind;
  b.value = kind === 'toggle' ? 'off' : kind === 'slider' ? 0 : '';
  renderPhases();
}

function setSettingValue(id, value) {
  const loc = findBlockLocation(id);
  if (loc) creationPhases[loc.phase][loc.idx].value = value;
}

// Generic block-field hotkey capture -- {blockId, field} says which block
// and which of its fields ('value' for a Setting block set to Hotkey,
// 'hotkey' for a Place Unit block) the next keypress writes into. Shares
// mapKeyName() with the global Settings > Hotkeys capture (see
// startRebind()'s keydown listener) so a captured key reads the same way
// everywhere, but is a no-op whenever nothing is capturing, so it never
// fights that other listener over the same keypress.
let capturingHotkeyTarget = null;

function startBlockHotkeyCapture(blockId, field, btn) {
  capturingHotkeyTarget = { blockId, field };
  btn.textContent = 'Press a key...';
  btn.classList.add('listening');
}

document.addEventListener('keydown', (e) => {
  if (!capturingHotkeyTarget) return;
  e.preventDefault();
  const { blockId, field } = capturingHotkeyTarget;
  capturingHotkeyTarget = null;
  const loc = findBlockLocation(blockId);
  // Esc clears the field (same convention as the Settings > Hotkeys capture)
  // rather than binding the Esc key itself.
  if (loc) creationPhases[loc.phase][loc.idx][field] = e.key === 'Escape' ? '' : mapKeyName(e);
  renderPhases();
});

function renderSettingControls(b) {
  const kindSel = `
    <select class="block-input" style="width:auto;" onchange="setSettingKind('${b.id}', this.value)">
      <option value="hotkey" ${b.kind === 'hotkey' ? 'selected' : ''}>Hotkey</option>
      <option value="toggle" ${b.kind === 'toggle' ? 'selected' : ''}>Toggle</option>
      <option value="slider" ${b.kind === 'slider' ? 'selected' : ''}>Slider</option>
    </select>`;

  if (b.kind === 'hotkey') {
    return kindSel + `<button type="button" class="keybind-btn" onclick="startBlockHotkeyCapture('${b.id}', 'value', this)">${b.value ? b.value.toUpperCase() : 'Click to set'}</button>`;
  }
  if (b.kind === 'slider') {
    const v = b.value ?? 0;
    // The readout is a real number input, not a label -- click it and type
    // an exact value instead of only being able to drag the (0.1-step,
    // fiddly-by-drag) range thumb to it.
    return kindSel + `
      <div class="setting-slider-group">
        <input type="range" min="0" max="2" step="0.1" value="${v}" class="setting-slider"
               oninput="setSettingValue('${b.id}', this.value); this.nextElementSibling.value = this.value">
        <input type="number" min="0" max="2" step="0.1" value="${v}" class="setting-slider-val"
               onclick="event.stopPropagation()"
               onchange="setSettingValue('${b.id}', this.value); this.previousElementSibling.value = this.value">
      </div>`;
  }
  return kindSel + `
    <div class="seg-toggle" style="width: auto;">
      <button type="button" class="seg-btn ${b.value === 'on' ? 'active' : ''}" onclick="setSettingValue('${b.id}', 'on'); renderPhases()">On</button>
      <button type="button" class="seg-btn ${b.value === 'off' ? 'active' : ''}" onclick="setSettingValue('${b.id}', 'off'); renderPhases()">Off</button>
    </div>`;
}

// Place Unit blocks are numbered #1, #2, ... in routine order (Pre Start
// first, then Battle) -- the same numbering the map picker uses to label
// already-placed units on the canvas, so a marker there points back to an
// exact row here.
function placeUnitOrdinal(id) {
  let n = 0;
  for (const phase of PHASES) {
    for (const b of creationPhases[phase]) {
      if (b.type !== 'place_unit') continue;
      n++;
      if (b.id === id) return n;
    }
  }
  return n;
}

// Place Unit renders all of its controls bespoke (renderBlockRow skips the
// generic renderParamInput fields for it): every field carries a small
// caption -- Name / X / Y / Hotkey / Position -- so the row reads at a
// glance instead of being a strip of anonymous boxes. "Set" opens the map
// picker modal (openPlaceUnitModal); a spot clicked there writes straight
// into the same x/y params these inputs edit, so the two always agree.
function renderPlaceUnitControls(b) {
  const field = (label, inner) => `
    <label class="blk-field"><span class="blk-field-label">${label}</span>${inner}</label>`;
  const idx = `<span class="pu-idx">#${placeUnitOrdinal(b.id)}</span>`;
  const name = field('Name', `<input class="block-input" style="width:120px;" type="text" value="${b.params.name}" placeholder="unit" oninput="updateBlockParam('${b.id}', 'name', this.value)">`);
  const x = field('X', `<input class="block-input" type="number" value="${b.params.x}" oninput="updateBlockParam('${b.id}', 'x', this.value)">`);
  const y = field('Y', `<input class="block-input" type="number" value="${b.params.y}" oninput="updateBlockParam('${b.id}', 'y', this.value)">`);
  const hotkey = field('Hotkey', `<button type="button" class="keybind-btn" onclick="startBlockHotkeyCapture('${b.id}', 'hotkey', this)">${b.hotkey ? b.hotkey.toUpperCase() : 'Set key'}</button>`);
  const hasPos = b.params.x || b.params.y;
  const set = field('Position', `<button type="button" class="pu-set-btn ${hasPos ? 'has-pos' : ''} tooltip-side" data-tooltip="Pick position on a map" onclick="openPlaceUnitModal('${b.id}')">${hasPos ? 'Set &#10003;' : 'Set'}</button>`);
  return idx + name + x + y + hotkey + set;
}

// Walk block: dropdown of the same recorded paths the pinned Walk Path row
// offers -- mid-battle repositioning reuses the exact same recordings --
// plus its own Record button, which drops the freshly saved path straight
// into this block's picker instead of the Walk Path row's.
function renderWalkControls(b) {
  const isRecording = recordingBlockId === b.id;
  const options = savedPaths.map(n => `<option value="${n}" ${n === b.params.path ? 'selected' : ''}>${n}</option>`).join('');
  return `
    <button type="button" class="block-mod-btn ${isRecording ? 'on' : ''}" onclick="toggleRecordPath('${b.id}')">${isRecording ? 'Stop' : 'Record'}</button>
    <select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', 'path', this.value)">
      <option value="">Pick saved path...</option>${options}
    </select>`;
}

// Every Place Unit block as {n, name}, in the same #1, #2, ... routine order
// placeUnitOrdinal() numbers rows with -- the option list for any control
// that targets an already-placed unit.
function listPlacedUnits() {
  const out = [];
  let n = 0;
  for (const phase of PHASES) {
    for (const b of creationPhases[phase]) {
      if (b.type !== 'place_unit') continue;
      n++;
      out.push({ n, name: b.params.name || '' });
    }
  }
  return out;
}

function renderUnitIndexSelect(b, key) {
  const options = listPlacedUnits().map(u => `
    <option value="${u.n}" ${String(b.params[key]) === String(u.n) ? 'selected' : ''}>#${u.n}${u.name ? ' ' + u.name : ''}</option>`).join('');
  return `
    <select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', '${key}', this.value)">
      <option value="">Unit...</option>${options}
    </select>`;
}

const blkField = (label, inner) => `
  <label class="blk-field"><span class="blk-field-label">${label}</span>${inner}</label>`;

// Upgrade Unit: which placed unit (#index) + how many upgrade presses.
function renderUpgradeControls(b) {
  return blkField('Unit', renderUnitIndexSelect(b, 'index'))
    + blkField('Times', `<input class="block-input" type="number" min="1" value="${Number(b.params.times) || 1}" oninput="updateBlockParam('${b.id}', 'times', this.value)">`);
}

// Sell Unit: which placed unit (#index) to sell -- same picker as
// Upgrade/Auto Upgrade instead of a free-typed unit name.
function renderSellUnitControls(b) {
  return blkField('Unit', renderUnitIndexSelect(b, 'index'));
}

// Auto Upgrade Unit: which placed unit (#index) + its priority (1 = upgraded
// first, None = not included in auto-upgrade order at all).
const AUTO_UPGRADE_PRIORITIES = ['None', '1', '2', '3', '4', '5', '6'];

function renderAutoUpgradeControls(b) {
  const current = String(b.params.priority ?? 1);
  const options = AUTO_UPGRADE_PRIORITIES.map(p =>
    `<option value="${p}" ${p === current ? 'selected' : ''}>${p}</option>`).join('');
  return blkField('Unit', renderUnitIndexSelect(b, 'index'))
    + blkField('Priority', `<select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', 'priority', this.value)">${options}</select>`);
}

function renderBlockRow(b, phase) {
  const def = BLOCK_TYPES[b.type];
  const inputs = b.type === 'place_unit' ? '' : def.params.map(p => renderParamInput(b, p)).join('');
  const extra = b.type === 'setting_change' ? renderSettingControls(b)
    : b.type === 'place_unit' ? renderPlaceUnitControls(b)
    : b.type === 'walk' ? renderWalkControls(b)
    : b.type === 'upgrade_unit' ? renderUpgradeControls(b)
    : b.type === 'auto_upgrade_unit' ? renderAutoUpgradeControls(b)
    : b.type === 'sell_unit' ? renderSellUnitControls(b) : '';
  const onceBtn = `<button type="button" class="block-mod-btn ${b.once ? 'on' : ''}" onclick="toggleBlockOnce('${b.id}')" title="Only run this block once, even if the routine repeats">Once</button>`;
  return `
    <div class="block-row" style="--blk: ${def.color};" draggable="true" data-id="${b.id}"
         ondragstart="if (['INPUT','SELECT','BUTTON'].includes(event.target.tagName)) { event.preventDefault(); return false; } event.dataTransfer.setData('block-reorder', '${b.id}')"
         ondragover="event.preventDefault(); event.stopPropagation(); event.currentTarget.classList.add('drag-over')"
         ondragleave="event.currentTarget.classList.remove('drag-over')"
         ondrop="onBlockDrop(event, '${phase}', '${b.id}')">
      <span class="block-drag-handle">&#8942;&#8942;</span>
      <span class="block-label">${def.label}</span>
      ${inputs}
      ${extra}
      <div class="block-actions">
        ${onceBtn}
        <span class="block-clone" onclick="cloneBlock('${b.id}')" data-tooltip="Clone">&#10697;</span>
        <span class="block-delete" onclick="removeBlock('${b.id}')" data-tooltip="Remove">&times;</span>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Place Unit map picker modal
// ---------------------------------------------------------------------------
// Category tabs (Story/Raid/... -- whatever subfolders exist under
// Assets/map) -> a thumbnail grid of that category's maps -> a zoomable/
// pannable canvas to click a spot on. "Use Roblox Screen" swaps the canvas's
// background for a one-shot mss screenshot of the live game instead of a
// static map image (see get_roblox_snapshot in main.py) -- either way it's
// just a frozen picture drawn on a <canvas>, so nothing done in this modal
// (clicking, dragging, zooming) can ever reach the real game; it's purely
// for reading off a position.
let puState = {
  blockId: null, categories: [], category: null, maps: [],
  image: null, naturalW: 0, naturalH: 0,
  zoom: 1, panX: 0, panY: 0,
  markX: null, markY: null,
};

async function openPlaceUnitModal(blockId) {
  const loc = findBlockLocation(blockId);
  if (!loc) return;
  const b = creationPhases[loc.phase][loc.idx];
  puState.blockId = blockId;
  puState.markX = b.params.x || null;
  puState.markY = b.params.y || null;
  puState.image = null;

  document.getElementById('pu-canvas-wrap').style.display = 'none';
  document.getElementById('pu-map-grid').style.display = '';
  document.getElementById('pu-pos-readout').textContent = puState.markX != null ? `X ${puState.markX}, Y ${puState.markY}` : 'Not set';
  document.getElementById('pu-modal').style.display = 'flex';

  try {
    puState.categories = await pywebview.api.list_map_categories();
  } catch (e) {
    puState.categories = [];
  }
  if (puState.categories.length === 0) {
    document.getElementById('pu-category-tabs').innerHTML = '';
    document.getElementById('pu-map-grid').innerHTML = '<div class="rh-empty">No maps found in Assets/map -- add category folders with map images, or use "Use Roblox Screen" instead.</div>';
    return;
  }
  await selectPlaceUnitCategory(puState.categories[0]);
}

function closePlaceUnitModal() {
  document.getElementById('pu-modal').style.display = 'none';
  puState.blockId = null;
}

function renderPlaceUnitCategoryTabs() {
  const el = document.getElementById('pu-category-tabs');
  el.innerHTML = `<div class="seg-toggle" style="width: auto;">` +
    puState.categories.map(c => `
      <button type="button" class="seg-btn ${c === puState.category ? 'active' : ''}" style="padding: 6px 16px;"
              onclick="selectPlaceUnitCategory('${c.replace(/'/g, "\\'")}')">${c}</button>
    `).join('') + `</div>`;
}

async function selectPlaceUnitCategory(category) {
  puState.category = category;
  renderPlaceUnitCategoryTabs();
  document.getElementById('pu-canvas-wrap').style.display = 'none';
  document.getElementById('pu-map-grid').style.display = '';
  try {
    puState.maps = await pywebview.api.list_maps(category);
  } catch (e) {
    puState.maps = [];
  }
  renderPlaceUnitMapGrid();
}

// Built via DOM calls (not innerHTML + inline onclick) so map names with
// apostrophes ("King's Tomb") don't need attribute-quote escaping.
function renderPlaceUnitMapGrid() {
  const el = document.getElementById('pu-map-grid');
  el.innerHTML = '';
  if (puState.maps.length === 0) {
    el.innerHTML = '<div class="rh-empty">No maps in this category yet.</div>';
    return;
  }
  for (const name of puState.maps) {
    const card = document.createElement('div');
    card.className = 'pu-map-thumb';
    const img = document.createElement('img');
    img.alt = name;
    const label = document.createElement('div');
    label.className = 'pu-map-thumb-label';
    label.textContent = name;
    card.appendChild(img);
    card.appendChild(label);
    card.addEventListener('click', () => selectPlaceUnitMap(name));
    el.appendChild(card);
    pywebview.api.get_map_image(puState.category, name).then(result => {
      if (result && result.ok) img.src = result.data_uri;
    }).catch(() => {});
  }
}

async function selectPlaceUnitMap(name) {
  try {
    const result = await pywebview.api.get_map_image(puState.category, name);
    if (!result.ok) { addLog(`[Creation] Couldn't load map "${name}".`); return; }
    loadPlaceUnitImage(result.data_uri);
  } catch (e) {}
}

// Same dance as saveDebugScreenshot()/readRewards(): the game is hidden and
// not rendering anywhere except the Dashboard, so switch there first, let it
// settle and paint a real frame, capture, then come straight back. The modal
// stays open the whole time (the game just paints over it for a moment).
async function usePlaceUnitRobloxScreen() {
  switchScreen('dashboard');
  await new Promise(resolve => setTimeout(resolve, 400));
  let result = null;
  try {
    result = await pywebview.api.get_roblox_snapshot();
  } catch (e) {}
  switchScreen('creation');
  if (!result || !result.ok) {
    addLog(`[Creation] Couldn't capture Roblox screen: ${(result && result.reason) || 'error'}`);
    return;
  }
  loadPlaceUnitImage(result.data_uri);
}

function loadPlaceUnitImage(dataUri) {
  const img = new Image();
  img.onload = () => {
    puState.image = img;
    puState.naturalW = img.naturalWidth;
    puState.naturalH = img.naturalHeight;
    fitPlaceUnitCanvas();
    document.getElementById('pu-map-grid').style.display = 'none';
    document.getElementById('pu-canvas-wrap').style.display = '';
    document.getElementById('pu-pos-readout').textContent = puState.markX != null ? `X ${puState.markX}, Y ${puState.markY}` : 'Not set';
    drawPlaceUnitCanvas();
  };
  img.src = dataUri;
}

function backToPlaceUnitMapGrid() {
  document.getElementById('pu-canvas-wrap').style.display = 'none';
  document.getElementById('pu-map-grid').style.display = '';
  puState.image = null;
}

// Fits the whole image in the canvas (contain, centered) as the starting
// zoom/pan -- scroll-to-zoom and drag-to-pan take over from there.
function fitPlaceUnitCanvas() {
  const canvas = document.getElementById('pu-canvas');
  const scale = Math.min(canvas.width / puState.naturalW, canvas.height / puState.naturalH);
  puState.zoom = scale;
  puState.panX = (canvas.width - puState.naturalW * scale) / 2;
  puState.panY = (canvas.height - puState.naturalH * scale) / 2;
}

// Every OTHER Place Unit block that already has a position -- shown as amber
// markers on the picker canvas (labeled with their #number + name) so you can
// see where units are already going and don't stack a second one on the same
// spot by accident.
function otherPlacedUnits() {
  const out = [];
  let n = 0;
  for (const phase of PHASES) {
    for (const b of creationPhases[phase]) {
      if (b.type !== 'place_unit') continue;
      n++;
      if (b.id === puState.blockId) continue;
      const x = Number(b.params.x) || 0, y = Number(b.params.y) || 0;
      if (!x && !y) continue;
      out.push({ x, y, label: `#${n}${b.params.name ? ' ' + b.params.name : ''}` });
    }
  }
  return out;
}

function drawPlaceUnitCanvas() {
  const canvas = document.getElementById('pu-canvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!puState.image) return;
  ctx.drawImage(puState.image, puState.panX, puState.panY, puState.naturalW * puState.zoom, puState.naturalH * puState.zoom);

  for (const u of otherPlacedUnits()) {
    const sx = puState.panX + u.x * puState.zoom;
    const sy = puState.panY + u.y * puState.zoom;
    ctx.beginPath();
    ctx.arc(sx, sy, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#ffc15e';
    ctx.strokeStyle = 'rgba(0,0,0,0.65)';
    ctx.lineWidth = 1.5;
    ctx.fill();
    ctx.stroke();
    ctx.font = '600 11px Inter, "Segoe UI", sans-serif';
    const tw = ctx.measureText(u.label).width;
    ctx.fillStyle = 'rgba(8,10,18,0.78)';
    ctx.fillRect(sx + 8, sy - 9, tw + 10, 18);
    ctx.fillStyle = '#ffc15e';
    ctx.fillText(u.label, sx + 13, sy + 4);
  }

  if (puState.markX != null && puState.markY != null) {
    const sx = puState.panX + puState.markX * puState.zoom;
    const sy = puState.panY + puState.markY * puState.zoom;
    ctx.beginPath();
    ctx.arc(sx, sy, 8, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(124,157,255,0.3)';
    ctx.fill();
    ctx.beginPath();
    ctx.arc(sx, sy, 4, 0, Math.PI * 2);
    ctx.fillStyle = '#7c9dff';
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.fill();
    ctx.stroke();
  }
}

function applyPlaceUnitPosition() {
  if (!puState.blockId) return;
  const loc = findBlockLocation(puState.blockId);
  if (!loc) return;
  const b = creationPhases[loc.phase][loc.idx];
  b.params.x = puState.markX;
  b.params.y = puState.markY;
  document.getElementById('pu-pos-readout').textContent = `X ${puState.markX}, Y ${puState.markY}`;
  renderPhases();  // refreshes the block row's x/y inputs + Set button behind the modal
}

// Scroll to zoom (toward the cursor, so the point under it stays put),
// drag to pan, a plain click (mousedown+up with no real movement in
// between) reads off the position under the cursor.
(function () {
  const canvas = document.getElementById('pu-canvas');
  if (!canvas) return;
  let dragging = false, dragMoved = false, lastX = 0, lastY = 0;

  function canvasPoint(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    return {
      cx: (clientX - rect.left) * (canvas.width / rect.width),
      cy: (clientY - rect.top) * (canvas.height / rect.height),
    };
  }

  canvas.addEventListener('wheel', (e) => {
    if (!puState.image) return;
    e.preventDefault();
    const { cx, cy } = canvasPoint(e.clientX, e.clientY);
    const imgX = (cx - puState.panX) / puState.zoom;
    const imgY = (cy - puState.panY) / puState.zoom;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    puState.zoom = Math.min(8, Math.max(0.2, puState.zoom * factor));
    puState.panX = cx - imgX * puState.zoom;
    puState.panY = cy - imgY * puState.zoom;
    drawPlaceUnitCanvas();
  }, { passive: false });

  canvas.addEventListener('mousedown', (e) => {
    if (!puState.image) return;
    dragging = true;
    dragMoved = false;
    lastX = e.clientX;
    lastY = e.clientY;
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragMoved = true;
    if (dragMoved) {
      const rect = canvas.getBoundingClientRect();
      puState.panX += dx * (canvas.width / rect.width);
      puState.panY += dy * (canvas.height / rect.height);
      lastX = e.clientX;
      lastY = e.clientY;
      drawPlaceUnitCanvas();
    }
  });

  window.addEventListener('mouseup', (e) => {
    if (!dragging) return;
    dragging = false;
    if (!dragMoved && puState.image) {
      const { cx, cy } = canvasPoint(e.clientX, e.clientY);
      puState.markX = Math.round((cx - puState.panX) / puState.zoom);
      puState.markY = Math.round((cy - puState.panY) / puState.zoom);
      applyPlaceUnitPosition();
      drawPlaceUnitCanvas();
    }
  });
})();

// The permanent first row of Pre Start: which walk path this routine uses to
// get to its spot. Auto (the game's default pathing) or a recorded custom
// path. Runs once per task by definition -- you only walk out once -- so the
// "Runs once" chip is fixed, not a toggle, and the row has no delete.
function renderWalkRow() {
  const isRecording = recordingBlockId === 'walkpath';
  const modeSeg = `
    <div class="seg-toggle">
      <button type="button" class="seg-btn ${creationWalk.mode === 'auto' ? 'active' : ''}" onclick="setWalkMode('auto')">Auto</button>
      <button type="button" class="seg-btn ${creationWalk.mode === 'custom' ? 'active' : ''}" onclick="setWalkMode('custom')">Custom</button>
    </div>`;
  let customControls = '';
  if (creationWalk.mode === 'custom') {
    const options = savedPaths.map(n => `<option value="${n}" ${n === creationWalk.pathName ? 'selected' : ''}>${n}</option>`).join('');
    customControls = `
      <button type="button" class="block-mod-btn ${isRecording ? 'on' : ''}" onclick="toggleRecordPath('walkpath')">${isRecording ? 'Stop' : 'Record'}</button>
      <select class="block-input" style="width:auto;" onchange="setWalkPath(this.value)"><option value="">Pick saved path...</option>${options}</select>`;
  }
  return `
    <div class="block-row pinned" style="--blk: var(--teal);">
      <svg class="w-3.5 h-3.5 flex-shrink-0" style="color: var(--teal);" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 2.5l8 8-3.5 1-4 6.5-2-2L5.5 22 2 18.5 8 12l-2-2 6.5-4z"/>
      </svg>
      <span class="block-label">Walk Path</span>
      ${modeSeg}
      ${customControls}
      <div class="block-actions">
        <span class="task-chip">Runs once</span>
      </div>
    </div>`;
}

// Team Loadout controls in the Creation top bar -- saved as part of the
// template (see saveCurrentTemplate). Equipment include/exclude only means
// anything once an actual team is picked.
function renderCreationLoadout() {
  const el = document.getElementById('creation-loadout');
  if (!el) return;
  const teams = ['', '1', '2', '3', '4', '5', '6', '7', '8'];
  const teamSel = `
    <select class="task-select" onchange="creationTeam = this.value; renderCreationLoadout()">
      ${teams.map(v => `<option value="${v}" ${v === creationTeam ? 'selected' : ''}>${v === '' ? 'No Team' : 'Team ' + v}</option>`).join('')}
    </select>`;
  const eqSeg = creationTeam === '' ? '' : `
    <span class="palette-group-label" style="margin: 0; white-space: nowrap; flex-shrink: 0;">Equipment :</span>
    <div class="seg-toggle">
      <button type="button" class="seg-btn ${creationEquipment === 'include' ? 'active' : ''}" onclick="creationEquipment = 'include'; renderCreationLoadout()">Include</button>
      <button type="button" class="seg-btn ${creationEquipment === 'exclude' ? 'active' : ''}" onclick="creationEquipment = 'exclude'; renderCreationLoadout()">Exclude</button>
    </div>`;
  el.innerHTML = `<span class="palette-group-label" style="margin: 0; white-space: nowrap; flex-shrink: 0;">Team Loadout</span>${teamSel}${eqSeg}`;
}

function renderPhases() {
  const el = document.getElementById('creation-phases');
  if (!el) return;
  el.innerHTML = PHASES.map(phase => {
    const blocks = creationPhases[phase];
    const emptyText = phase === 'prestart'
      ? 'Drag Place Unit or Setting blocks here -- only those are possible before the match starts.'
      : 'Drag blocks here -- upgrades, sells, waits, anything goes mid-battle.';
    const emptyDiv = `<div class="text-xs text-center" style="color: var(--text-muted); padding: 16px 0;">${emptyText}</div>`;
    // In Pre Start only Setting blocks sit above Walk Path (a setting that
    // must fire the instant the match loads, before you even start walking);
    // Place Unit blocks always render below it, since placing happens after
    // you've walked to your spot. Walk Path itself stays pinned between the
    // two -- it isn't a reorderable block.
    let body;
    if (phase === 'prestart') {
      const settingRows = blocks.filter(b => b.type === 'setting_change').map(b => renderBlockRow(b, phase)).join('');
      const unitRows = blocks.filter(b => b.type !== 'setting_change').map(b => renderBlockRow(b, phase)).join('');
      body = settingRows + renderWalkRow() + (blocks.length === 0 ? emptyDiv : unitRows);
    } else {
      body = blocks.length === 0 ? emptyDiv : blocks.map(b => renderBlockRow(b, phase)).join('');
    }
    return `
      <div class="phase-panel ${phaseCollapsed[phase] ? 'collapsed' : ''}">
        <div class="phase-head" onclick="togglePhaseCollapsed('${phase}')">
          <svg class="phase-chevron w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
          ${PHASE_LABELS[phase]}
          <span class="rp-head-tag" style="--rp-tag: ${phase === 'prestart' ? 'var(--teal)' : 'var(--rose)'}; margin-left: 2px;">${PHASE_TAGS[phase]}</span>
          <span class="phase-count">${blocks.length}</span>
        </div>
        <div id="creation-canvas-${phase}" class="canvas-dropzone p-2"
             ondragover="onCanvasDragOver(event, '${phase}')" ondragleave="onCanvasDragLeave(event, '${phase}')"
             ondrop="onCanvasDrop(event, '${phase}')">${body}</div>
      </div>
    `;
  }).join('');
  renderCreationLoadout();
}

function onCanvasDragOver(e, phase) {
  e.preventDefault();
  document.getElementById(`creation-canvas-${phase}`).classList.add('drag-over');
}

function onCanvasDragLeave(e, phase) {
  document.getElementById(`creation-canvas-${phase}`).classList.remove('drag-over');
}

function onCanvasDrop(e, phase) {
  e.preventDefault();
  document.getElementById(`creation-canvas-${phase}`).classList.remove('drag-over');
  const type = e.dataTransfer.getData('block-type');
  if (type) { addBlock(type, phase); return; }
  const draggedId = e.dataTransfer.getData('block-reorder');
  if (draggedId) moveBlockToPhase(draggedId, phase, null);
}

// Moves an existing block (same-phase reorder OR a cross-phase drag, e.g.
// a Setting block from Pre Start into Battle) -- destination still has to
// allow the block's type, same rule addBlock enforces for palette drops.
// toIdx is the destination index computed BEFORE the source is removed
// (null = end of list).
function moveBlockToPhase(id, phase, toIdx) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const b = creationPhases[loc.phase][loc.idx];
  if (loc.phase !== phase && !PHASE_ALLOWED[phase].includes(b.type)) {
    addLog(`[Creation] "${BLOCK_TYPES[b.type].label}" can't go in ${PHASE_LABELS[phase]}.`);
    return;
  }
  creationPhases[loc.phase].splice(loc.idx, 1);
  const list = creationPhases[phase];
  if (toIdx == null || toIdx === -1) list.push(b);
  else list.splice(toIdx, 0, b);
  renderPhases();
}

function onBlockDrop(e, phase, targetId) {
  e.preventDefault();
  e.stopPropagation();
  e.currentTarget.classList.remove('drag-over');

  const list = creationPhases[phase];
  const newType = e.dataTransfer.getData('block-type');
  if (newType) {
    const toIdx = list.findIndex(b => b.id === targetId);
    addBlock(newType, phase, toIdx === -1 ? null : toIdx);
    return;
  }

  const draggedId = e.dataTransfer.getData('block-reorder');
  if (!draggedId || draggedId === targetId) return;
  const toIdx = list.findIndex(b => b.id === targetId);
  moveBlockToPhase(draggedId, phase, toIdx === -1 ? null : toIdx);
}

async function saveCurrentTemplate() {
  const nameInput = document.getElementById('template-name');
  const name = nameInput.value.trim();
  if (!name) return;
  const payload = { walk: { ...creationWalk }, team: creationTeam, equipment: creationEquipment };
  PHASES.forEach(phase => {
    payload[phase] = creationPhases[phase].map(b => ({
      type: b.type, params: b.params, once: b.once, kind: b.kind, value: b.value, hotkey: b.hotkey,
    }));
  });
  try {
    const result = await pywebview.api.save_template(name, payload);
    addLog(`Saved template "${result.name}".`);
    refreshTemplateList();
  } catch (e) {}
}

// Resets the editor to a blank routine -- same defaults renderPhases()
// already assumes on first load, just re-applied on demand so starting a
// new template doesn't require manually clearing out whatever was loaded.
function newTemplate() {
  creationPhases = { prestart: [], battle: [] };
  creationWalk = { mode: 'auto', pathName: '' };
  creationTeam = '';
  creationEquipment = 'include';
  document.getElementById('template-name').value = '';
  document.getElementById('template-select').value = '';
  renderPhases();
  renderCreationLoadout();
}

async function deleteSelectedTemplate() {
  const sel = document.getElementById('template-select');
  const name = sel.value || document.getElementById('template-name').value.trim();
  if (!name) return;
  if (!confirm(`Delete template "${name}"? This can't be undone.`)) return;
  try {
    await pywebview.api.delete_template(name);
    addLog(`Deleted template "${name}".`);
  } catch (e) {}
  await refreshTemplateList();
  if (sel.value === name || document.getElementById('template-name').value.trim() === name) newTemplate();
}

// Export bundles every saved template into one file (a full backup of your
// template library, same "bundle everything" approach as the Task screen's
// Export) -- the currently-open-but-unsaved editor state isn't included,
// only what's actually saved, since Import only knows how to restore real
// template files anyway.
async function exportTemplates() {
  let names = [];
  try { names = await pywebview.api.list_templates(); } catch (e) {}
  if (names.length === 0) { addLog('[Creation] Nothing to export -- no saved templates yet.'); return; }
  const templates = {};
  for (const name of names) {
    try { templates[name] = await pywebview.api.load_template(name); } catch (e) {}
  }
  const payload = {
    kind: 'anime-expeditions-templates', version: 1, exported: new Date().toISOString(), templates,
  };
  let result = null;
  try { result = await pywebview.api.export_tasks_file(payload, 'templates'); } catch (e) {}
  if (result && result.ok) addLog(`[Creation] Exported ${names.length} template(s) to ${result.path}`);
  else if (result && result.reason !== 'cancelled') addLog(`[Creation] Export failed: ${result.reason || 'error'}`);
}

async function importTemplates() {
  let result = null;
  try { result = await pywebview.api.import_tasks_file(); } catch (e) {}
  if (!result || !result.ok) {
    if (result && result.reason !== 'cancelled') addLog(`[Creation] Import failed: ${result.reason || 'error'}`);
    return;
  }
  const data = result.data || {};
  const templates = data.templates && typeof data.templates === 'object' ? data.templates : null;
  if (!templates) { addLog('[Creation] Import failed: that file is not a template export.'); return; }
  let existing = [];
  try { existing = await pywebview.api.list_templates(); } catch (e) {}
  let added = 0;
  for (const [name, t] of Object.entries(templates)) {
    if (existing.includes(name) || !t || t.blocks == null) continue;
    try { await pywebview.api.save_template(name, t.blocks); added++; } catch (e) {}
  }
  await refreshTemplateList();
  addLog(`[Creation] Imported ${added} template(s).`);
}

async function refreshTemplateList() {
  const sel = document.getElementById('template-select');
  if (!sel) return;
  try {
    const names = await pywebview.api.list_templates();
    sel.innerHTML = '<option value="">Load...</option>' + names.map(n => `<option value="${n}">${n}</option>`).join('');
  } catch (e) {}
}

function blockFromSaved(b) {
  const block = { id: newBlockId(), type: b.type, params: b.params, once: !!b.once };
  if (b.type === 'setting_change') {
    block.kind = b.kind || 'toggle';
    block.value = b.value !== undefined ? b.value : (block.kind === 'slider' ? 0 : 'off');
  }
  if (b.type === 'place_unit') {
    block.hotkey = b.hotkey || '';
  }
  return block;
}

async function loadSelectedTemplate() {
  const name = document.getElementById('template-select').value;
  if (!name) return;
  try {
    const data = await pywebview.api.load_template(name);
    const payload = data.blocks || {};
    creationPhases = { prestart: [], battle: [] };
    creationWalk = { mode: 'auto', pathName: '' };
    creationTeam = '';
    creationEquipment = 'include';

    if (Array.isArray(payload)) {
      // Oldest shape: one flat pre-phases list. Everything that still exists
      // as a block lands in Battle; pathing blocks became the walk config.
      migrateLegacyBlocks(payload, []);
    } else if (payload.before || payload.during || payload.after) {
      // Three-phase shape (Before/In/After Match): Before's placements are
      // Pre Start by definition; everything else runnable goes to Battle.
      migrateLegacyBlocks([...(payload.during || []), ...(payload.after || [])], payload.before || []);
    } else {
      PHASES.forEach(phase => { creationPhases[phase] = (payload[phase] || []).map(blockFromSaved); });
      if (payload.walk) creationWalk = { mode: payload.walk.mode === 'custom' ? 'custom' : 'auto', pathName: payload.walk.pathName || '' };
      creationTeam = payload.team || '';
      creationEquipment = payload.equipment === 'exclude' ? 'exclude' : 'include';
    }
    renderPhases();
    document.getElementById('template-name').value = data.name || name;
  } catch (e) {}
}

// Shared by both legacy template shapes: sort old blocks into the two-phase
// model. Pathing block types no longer exist as blocks -- a custom_path with
// a recorded path becomes the pinned walk row's config, auto_select just
// confirms the default -- and any placement from the old "before" list stays
// in Pre Start while everything else runs in Battle.
function migrateLegacyBlocks(mainBlocks, beforeBlocks) {
  for (const b of beforeBlocks) {
    if (b.type === 'custom_path' || b.type === 'auto_select') { migrateWalkBlock(b); continue; }
    if (!BLOCK_TYPES[b.type]) continue;
    (b.type === 'place_unit' ? creationPhases.prestart : creationPhases.battle).push(blockFromSaved(b));
  }
  for (const b of mainBlocks) {
    if (b.type === 'custom_path' || b.type === 'auto_select') { migrateWalkBlock(b); continue; }
    if (!BLOCK_TYPES[b.type]) continue;
    creationPhases.battle.push(blockFromSaved(b));
  }
}

function migrateWalkBlock(b) {
  if (b.type === 'custom_path' && b.pathName) creationWalk = { mode: 'custom', pathName: b.pathName };
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
window.addEventListener('pywebviewready', async () => {
  try {
    const version = await pywebview.api.get_version();
    document.getElementById('ver-badge').textContent = `v${version}`;
  } catch (e) {}
  try {
    const info = await pywebview.api.get_time_info();
    sessionStart = info.session_start;
    allTimeBase = info.all_time_base;
  } catch (e) {}

  renderPalette();
  renderPhases();
  refreshSavedPaths();
  loadSettingsUI();

  refreshTaskQueue();

  tickTimers();
  setInterval(tickTimers, 1000);
  refreshStatus();
  setInterval(refreshStatus, 1500);
});
