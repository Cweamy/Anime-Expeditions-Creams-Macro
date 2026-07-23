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
  } else if (currentScreen === 'dashboard' && !isBlockingOverlayOpen()) {
    try { window.pywebview && pywebview.api.show_game(); } catch (e) {}
  }
}

// Set by the two capture dances (usePlaceUnitRobloxScreen /
// startImageCapture) while they deliberately hop to the Dashboard WITH
// their modal still open: the whole point of the hop is that the game
// becomes visible for the screenshot, so during it the modal must NOT
// count as a blocking overlay or show_game() would be suppressed and the
// capture would grab our own UI instead of Roblox.
let captureDanceActive = false;

// Any modal that the docked Roblox window must not be shown on top of --
// checked by switchScreen() (and the F4 game toggle through it) before it
// would otherwise show_game() out from under one. Roblox is a native
// child window: it paints over ALL DOM regardless of z-index, so showing
// it under an open modal doesn't close the modal, it just hides it while
// the invisible overlay keeps eating clicks -- the exact "pressed F4 with
// something open and the UI broke" report. Two tiers:
//   - update/scale modals: always blocking (their own show/dismiss
//     handlers manage hide_game/show_game explicitly).
//   - transient tool modals (Image Manager, Set Position picker, the
//     path-name prompt): blocking EXCEPT mid-capture-dance (see
//     captureDanceActive above). Their close paths call
//     restoreGameIfDashboard() so the game comes back if they were
//     closed while sitting on the Dashboard.
function isBlockingOverlayOpen() {
  const isOpen = id => {
    const el = document.getElementById(id);
    return el && el.style.display !== 'none' && el.style.display !== '';
  };
  if (['update-modal', 'scale-warning-modal'].some(isOpen)) return true;
  if (!captureDanceActive && ['im-modal', 'pu-modal', 'path-name-modal'].some(isOpen)) return true;
  return false;
}

// Shared "modal just closed" restore: shows the game again only where it's
// actually supposed to be visible (Dashboard) and only if no OTHER
// blocking overlay is still up -- same logic dismissUpdateModal/
// dismissScaleWarning already used individually.
function restoreGameIfDashboard() {
  if (currentScreen === 'dashboard' && !isBlockingOverlayOpen()) {
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
    // Roblox is docked as a real native child window, not DOM content --
    // it renders on top of this modal regardless of CSS z-index, same
    // reason switchScreen() hides it for every screen except Dashboard.
    // This can fire while sitting on Dashboard (where it's normally
    // shown), so it has to hide it explicitly here too, or the modal
    // exists but is invisible behind the game.
    try { window.pywebview && pywebview.api.hide_game(); } catch (e) {}
  } catch (e) {}
}

function dismissUpdateModal() {
  clearInterval(updateProgressPoll);
  document.getElementById('update-modal').style.display = 'none';
  restoreGameIfDashboard();
}

// ---------------------------------------------------------------------------
// Display scale warning -- shown once at startup when Windows display scale
// isn't 100% (see main._launch_ui). Every fixed click/search coordinate in
// core/runner.py was captured at 100% scale; anything else is a common,
// hard-to-diagnose cause of clicks/detection landing slightly wrong.
// ---------------------------------------------------------------------------
async function showScaleWarning() {
  try {
    const info = await pywebview.api.get_display_scale();
    document.getElementById('scale-warning-percent').textContent = `${info.percent}%`;
    document.getElementById('scale-warning-modal').style.display = 'flex';
    // Same reasoning as showUpdateAvailable -- Roblox is a native child
    // window that renders on top of this modal regardless of CSS z-index.
    try { window.pywebview && pywebview.api.hide_game(); } catch (e) {}
  } catch (e) {}
}

function dismissScaleWarning() {
  document.getElementById('scale-warning-modal').style.display = 'none';
  restoreGameIfDashboard();
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

let updateProgressPoll = null;

function resetUpdateModalButtons() {
  const btn = document.getElementById('update-apply-btn');
  btn.disabled = false;
  btn.textContent = 'Update & Restart';
  document.getElementById('update-progress-wrap').style.display = 'none';
  document.getElementById('update-notes').style.display = '';
  document.getElementById('update-actions').style.display = '';
}

// apply_update() kicks off the download/stage/relaunch in a background
// thread and returns immediately -- this polls get_update_progress() to
// drive a real progress bar instead of the button just saying "Updating..."
// with no other feedback for however long the download takes (previously
// the window would just sit there with nothing visible happening, which
// read as broken rather than in-progress).
async function applyUpdate() {
  const btn = document.getElementById('update-apply-btn');
  btn.disabled = true;
  document.getElementById('update-notes').style.display = 'none';
  document.getElementById('update-actions').style.display = 'none';
  document.getElementById('update-progress-wrap').style.display = 'block';

  try {
    const result = await pywebview.api.apply_update();
    if (!result || !result.ok) {
      resetUpdateModalButtons();
      addLog && addLog('[Update] Failed to start the update -- check the log for details.');
      return;
    }
  } catch (e) {
    resetUpdateModalButtons();
    return;
  }

  const bar = document.getElementById('update-progress-bar');
  const text = document.getElementById('update-progress-text');
  clearInterval(updateProgressPoll);
  updateProgressPoll = setInterval(async () => {
    let progress;
    try { progress = await pywebview.api.get_update_progress(); } catch (e) { return; }
    if (!progress || !progress.phase) return;

    text.textContent = progress.message || '';
    if (progress.percent == null) {
      bar.classList.add('update-progress-indeterminate');
    } else {
      bar.classList.remove('update-progress-indeterminate');
      bar.style.width = `${progress.percent}%`;
    }

    if (progress.phase === 'error') {
      clearInterval(updateProgressPoll);
      resetUpdateModalButtons();
      addLog && addLog(`[Update] ${progress.message}`);
    }
    // "restarting" -- the app closes itself moments after this (see
    // main.Api._apply_update_background) and a relaunch helper brings it
    // back up. Nothing left to poll for; just leave the bar at 100% and
    // let the window disappear on its own.
  }, 400);
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
// Screen switching (Dashboard / Macro Manager / Settings)
// ---------------------------------------------------------------------------
// Switching away from Dashboard hides the docked Roblox window entirely (it's
// a native child window, not DOM content, so CSS alone can't hide it) so the
// other screens get the full window instead of Roblox showing through.
let currentScreen = 'dashboard';
let lastNonDashboardScreen = 'creation';
const SCREENS = ['dashboard', 'task', 'creation', 'challenge', 'settings'];

// Only macOS cares: there the game sits BESIDE this window instead of inside
// it, which changes both the Dashboard's layout and how much screen this
// window should take.
//
// Detected SYNCHRONOUSLY here, at parse time, rather than only from
// Api.get_platform() in the pywebviewready handler -- Python can call
// showDocked() through evaluate_js the moment docking succeeds, which reveals
// #main-layout, and that can land before any awaited bridge round-trip has
// resolved. Setting the attribute late would paint the Windows layout (with
// its 1152px game hole) first and then visibly reflow. pywebviewready still
// re-asserts this from Python afterwards, which is authoritative.
let IS_MAC = /Mac|Macintosh|Mac OS X/i.test(
  (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent || '');
if (IS_MAC) document.documentElement.dataset.platform = 'mac';

// Previous poll's macro-running state, so refreshStatus can act on the
// running -> stopped EDGE rather than every tick (see refreshStatus).
let wasMacroRunning = false;

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
      // Roblox is a native child window that renders on top of any DOM
      // overlay regardless of CSS z-index (same reason showUpdateAvailable/
      // showScaleWarning call hide_game() themselves) -- if one of those is
      // currently up, showing it back would put Roblox right on top of it.
      // This specifically covers switchScreen('dashboard') firing WHILE one
      // is open (e.g. showDocked()'s first-time auto-switch to Dashboard,
      // if Roblox docks after the update check already popped its modal) --
      // previously this had no such check at all, so the auto-update
      // progress overlay (living in the same #update-modal the "available"
      // prompt already hides Roblox for) could end up hidden behind Roblox
      // the moment docking happened to land after it was shown. The modal's
      // own dismiss handler is what restores show_game() once it closes.
      if (name === 'dashboard' && !isBlockingOverlayOpen()) pywebview.api.show_game();
      else if (name !== 'dashboard') pywebview.api.hide_game();

      // macOS: hide_game() above is a no-op there (you cannot hide another
      // app's window), so "give this screen the room" has to be expressed as
      // window size instead. The Dashboard keeps the narrow strip so Roblox
      // stays visible alongside it; every other screen is a multi-column
      // editor built for a 1552px window, so it takes the full visible frame.
      // No-op until the panel has been arranged once -- see set_panel_expanded.
      if (IS_MAC) pywebview.api.set_panel_expanded(name !== 'dashboard');
    }
  } catch (e) {}

  if (name === 'creation') { refreshTemplateList(); refreshSavedPaths(); }
  if (name === 'task') refreshTaskQueue();
  if (name === 'challenge') refreshChallengeScreen();
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
    document.getElementById('stat-mode').textContent = status.mode ?? '-';
    document.getElementById('stat-stage').textContent = status.stage ?? '-';
    document.getElementById('stat-difficulty').textContent = status.difficulty ?? '-';
    document.getElementById('stat-play-mode').textContent = status.play_mode ?? '-';
    document.getElementById('stat-macro').textContent = status.macro ?? '-';

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

    // macOS: set_panel_expanded refuses to widen the panel while the macro is
    // running (widening covers Roblox, and core/ocr.py's reward/wave reads are
    // plain screen grabs that would then read our own pixels). So a run that
    // starts while the user sits on Settings leaves them stuck at the narrow
    // width even after it finishes -- nothing else would ask again until the
    // next navigation. Re-ask on the running -> stopped edge only, so this
    // stays off the hot path of a 1.5s poll.
    if (IS_MAC && wasMacroRunning && !macro.running && currentScreen !== 'dashboard') {
      pywebview.api.set_panel_expanded(true);
    }
    wasMacroRunning = !!macro.running;
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
    const label = document.getElementById('btn-macro-pause-label');
    if (label) label.textContent = paused ? 'Resume' : 'Pause';
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

// Settings > General > Macro Speed: extra ms after every click/keypress
// (core/pacing.py). Clamped here too so a typo'd huge number can't freeze
// every click behind a minutes-long sleep.
async function saveActionDelay(input) {
  const ms = Math.min(2000, Math.max(0, parseInt(input.value, 10) || 0));
  input.value = ms;
  try {
    await pywebview.api.set_setting('action_delay_ms', ms);
    addLog(`[Settings] Action delay set to ${ms}ms${ms ? '' : ' (full speed)'}.`);
  } catch (e) {}
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
  toggle_game: 'f4', skip_waiting: '', macro_start: 'f1', macro_stop: 'f2', macro_pause: 'f5', debug_screenshot: 'f3',
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
    action === 'macro_start' ? 'btn-macro-start-key' : action === 'macro_stop' ? 'btn-macro-stop-key'
    : action === 'macro_pause' ? 'btn-macro-pause-key' : null);
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
  // event.key is whatever character the CURRENT keyboard layout produces
  // for the physical key -- on non-US layouts the digit row often doesn't
  // type a plain digit without Shift at all, so someone pressing "1"-"6"
  // was getting captured (and stored/displayed) as that layout's symbol
  // instead ("§", "&", ...). event.code is the physical key's US-layout
  // position regardless of layout/Shift, which is what "press this key to
  // bind it" actually means here -- used for the plain digit row and
  // letter keys, where landing on one stable name matters most; anything
  // else still falls back to event.key so punctuation/media keys keep
  // whatever name they'd normally get.
  const digitMatch = /^Digit(\d)$/.exec(e.code || '');
  if (digitMatch) return digitMatch[1];
  const letterMatch = /^Key([A-Z])$/.exec(e.code || '');
  if (letterMatch) return letterMatch[1].toLowerCase();
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
    updateKeybindDisplay('macro_pause', hk.macro_pause || '');
    updateKeybindDisplay('debug_screenshot', hk.debug_screenshot || '');
  } catch (e) {}
}

// ---- Theme ----
// Two INDEPENDENT pickers instead of one flat row of preset combos: Base
// (background palette) and Accent (--brand color) -- see style.css's own
// comment on data-theme-base/data-theme-accent for how they combine. '' /
// 'default' means "no override" for either, i.e. the plain :root palette.
const THEME_BASES = {
  default: { label: 'Dark', bg: '#171a26', border: '#2a2e42' },
  black:   { label: 'Black', bg: '#0a0a0a', border: '#262626' },
  slate:   { label: 'Slate', bg: '#1a1b1e', border: '#313338' },
  light:   { label: 'Light', bg: '#ffffff', border: '#d8dbe4' },
};
const THEME_ACCENTS = {
  default: '#7c9dff', ocean: '#58a6ff', emerald: '#3fbf8f', sakura: '#e87a9e',
  violet: '#a878f0', sunset: '#e8935a', crimson: '#e05a6d', mono: '#aab2c8',
};
let activeThemeBase = 'default';
let activeThemeAccent = 'default';

function applyThemeBase(name, announce) {
  activeThemeBase = THEME_BASES[name] ? name : 'default';
  if (activeThemeBase === 'default') delete document.documentElement.dataset.themeBase;
  else document.documentElement.dataset.themeBase = activeThemeBase;
  renderThemePicker();
  if (announce) addLog(`[Theme] Background: ${THEME_BASES[activeThemeBase].label}`);
}

function applyThemeAccent(name, announce) {
  activeThemeAccent = THEME_ACCENTS[name] !== undefined ? name : 'default';
  if (activeThemeAccent === 'default') delete document.documentElement.dataset.themeAccent;
  else document.documentElement.dataset.themeAccent = activeThemeAccent;
  renderThemePicker();
  if (announce) addLog(`[Theme] Accent: ${activeThemeAccent[0].toUpperCase() + activeThemeAccent.slice(1)}`);
}

function setThemeBase(name) {
  applyThemeBase(name, true);
  try { pywebview.api.set_setting('theme_base', activeThemeBase); } catch (e) {}
}

function setThemeAccent(name) {
  applyThemeAccent(name, true);
  try { pywebview.api.set_setting('theme_accent', activeThemeAccent); } catch (e) {}
}

// One-time migration off the old single combined `theme` setting (e.g.
// "black" or "ocean" meant one or the other) into the new independent
// base/accent pair -- only runs when neither new setting has been saved
// yet, so it never clobbers a real choice made under the new system.
function migrateLegacyTheme(legacy) {
  if (!legacy || legacy === 'default') return { base: 'default', accent: 'default' };
  if (THEME_BASES[legacy]) return { base: legacy, accent: 'default' };
  if (THEME_ACCENTS[legacy] !== undefined) return { base: 'default', accent: legacy };
  return { base: 'default', accent: 'default' };
}

function renderThemePicker() {
  const baseEl = document.getElementById('theme-base-picker');
  if (baseEl) {
    baseEl.innerHTML = Object.entries(THEME_BASES).map(([name, t]) => `
      <button class="theme-base-tile ${name === activeThemeBase ? 'active' : ''}"
              style="--tb-bg: ${t.bg}; --tb-border: ${t.border};"
              onclick="setThemeBase('${name}')" data-tooltip="${t.label}"></button>
    `).join('');
  }
  const accentEl = document.getElementById('theme-accent-picker');
  if (accentEl) {
    accentEl.innerHTML = Object.entries(THEME_ACCENTS).map(([name, color]) => `
      <button class="theme-swatch ${name === activeThemeAccent ? 'active' : ''}" style="--sw: ${color};"
              onclick="setThemeAccent('${name}')" data-tooltip="${name[0].toUpperCase() + name.slice(1)}"></button>
    `).join('');
  }
}

async function loadSettingsUI() {
  try {
    const s = await pywebview.api.get_settings();
    document.getElementById('toggle-start-minimized').classList.toggle('on', !!s.start_minimized);
    const actionDelayEl = document.getElementById('setting-action-delay');
    if (actionDelayEl) actionDelayEl.value = s.action_delay_ms || 0;
    const debugScreenshotsEl = document.getElementById('toggle-debug-screenshots');
    if (debugScreenshotsEl) debugScreenshotsEl.classList.toggle('on', !!s.debug_screenshots);
    const expColorEl = document.getElementById('toggle-expedition-color');
    // Default ON -- the key is simply absent until the user first flips it.
    if (expColorEl) expColorEl.classList.toggle('on', s.expedition_color_buttons !== false);
    if (!s.theme_base && !s.theme_accent && s.theme && s.theme !== 'default') {
      // First load since the base/accent split -- migrate the old value
      // once, then persist the split so this branch never runs again.
      const migrated = migrateLegacyTheme(s.theme);
      applyThemeBase(migrated.base, false);
      applyThemeAccent(migrated.accent, false);
      try {
        pywebview.api.set_setting('theme_base', migrated.base);
        pywebview.api.set_setting('theme_accent', migrated.accent);
      } catch (e) {}
    } else {
      applyThemeBase(s.theme_base || 'default', false);
      applyThemeAccent(s.theme_accent || 'default', false);
    }
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
    updateKeybindDisplay('macro_pause', hk.macro_pause || '');
    updateKeybindDisplay('debug_screenshot', hk.debug_screenshot || '');
    updateKeybindDisplay('image_manager', hk.image_manager || '');
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
  refreshRobloxWindowList();
  refreshDebugMacroOpSelect();
}

// Settings > Debug > "Test Pre Start"/"Test Battle" -- same list_templates()
// every Macro Operation dropdown elsewhere already pulls from.
async function refreshDebugMacroOpSelect() {
  const sel = document.getElementById('debug-macro-op-select');
  if (!sel) return;
  let names = [];
  try { names = await pywebview.api.list_templates(); } catch (e) { names = []; }
  const prev = sel.value;
  sel.innerHTML = names.length
    ? names.map(n => `<option value="${n}">${n}</option>`).join('')
    : '<option value="">No Macro Operations saved yet</option>';
  if (names.includes(prev)) sel.value = prev;
}

// Settings > Debug > "Select Roblox Window": lists every standalone Roblox
// window that ISN'T already docked (core.window.list_roblox_windows
// naturally excludes the attached one -- it's reparented/hidden, so
// EnumWindows never sees it), for multi-instance setups where more than
// one Roblox is open at once.
async function refreshRobloxWindowList() {
  const sel = document.getElementById('roblox-window-select');
  if (!sel) return;
  let windows = [];
  try { windows = await pywebview.api.list_roblox_windows(); } catch (e) { windows = []; }
  const prev = sel.value;
  sel.innerHTML = windows.length
    ? windows.map(w => `<option value="${w.hwnd}">${w.title || 'Roblox'} (pid ${w.pid})</option>`).join('')
    : '<option value="">No other Roblox windows found</option>';
  if (windows.some(w => String(w.hwnd) === prev)) sel.value = prev;
}

async function attachSelectedRoblox(btn) {
  const sel = document.getElementById('roblox-window-select');
  const hwnd = sel && sel.value;
  if (!hwnd) { addLog('[Debug] No Roblox window selected to attach.'); return; }
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Attaching...';
  try {
    const result = await pywebview.api.attach_roblox_window(hwnd);
    btn.textContent = result.ok ? 'Attached' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; refreshRobloxWindowList(); }, 2400);
}

async function unattachRoblox(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Detaching...';
  try {
    const result = await pywebview.api.detach_roblox_window();
    btn.textContent = result.ok ? 'Detached' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; refreshRobloxWindowList(); }, 2000);
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
// read_rewards() only blocks on the capture + scroll (~1s) -- the actual icon
// identification runs in a background Python thread and streams its results into the
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

// Settings > Debug > "Test Expedition Wave Check" -- same dance as
// saveDebugScreenshot: switch to the Dashboard first so Roblox is actually
// visible, let it settle, then ask Python to run one tick of
// nav_start_game/exp_continue/exp_extract detection+clicking against
// whatever's on screen right now. No active macro run needed -- lets you
// tune this flow by navigating to the screen being tested in Roblox by
// hand and pressing the button repeatedly, instead of restarting a whole
// run every time. Result/errors are already logged on the Python side.
async function testExpeditionWave(btn) {
  const original = btn.textContent;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Testing...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.debug_test_expedition_wave();
    if (!result.ok && result.reason === 'no_roblox') {
      addLog('[Debug] Expedition wave check failed: Roblox not found.');
    }
    btn.textContent = result.ok ? 'Done' : 'Failed';
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1600);
}

// Settings > Debug > "Force Rejoin" -- manually fires the same deep-link
// rejoin a real disconnect uses, so Roblox can be reset back to the lobby
// between test iterations without alt-tabbing over and closing/reopening it
// by hand. Can genuinely take up to REJOIN_TIMEOUT (90s, see core.runner) if
// Roblox has to fully relaunch, so this awaits the real result instead of
// resetting the button on a short fixed delay like the other debug buttons.
async function forceRejoin(btn) {
  const original = btn.textContent;
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Rejoining...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.debug_force_rejoin();
    if (!result.ok && result.reason === 'no_roblox') {
      addLog('[Debug] Force rejoin failed: Roblox not found.');
    }
    btn.textContent = result.ok ? 'Done' : 'Failed';
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1600);
}

// Settings > Debug > "Test Pre Start"/"Test Battle" -- starts a chosen
// Macro Operation's blocks running against Roblox as it is right now, as a
// REAL tracked run (not a quick one-shot like testExpeditionWave/
// forceRejoin above) -- Battle mode in particular ticks indefinitely until
// Stop is pressed, so this only starts it and gets out of the way; the
// existing refreshStatus() poll (every 1.5s) is what keeps the Dashboard's
// own Start/Stop/Pause buttons in sync with it from here on, exactly like
// a normal Start does.
async function testMacroOperation(btn, mode) {
  const sel = document.getElementById('debug-macro-op-select');
  const macroName = sel ? sel.value : '';
  if (!macroName) {
    addLog('[Debug] No Macro Operation selected to test.');
    return;
  }
  switchScreen('dashboard');
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Starting...';
  try {
    const result = await pywebview.api.debug_test_macro_operation(mode, macroName);
    if (!result.ok) {
      const reasons = { no_roblox: 'Roblox not found.', already_running: 'already running.',
                         bad_mode: 'bad mode.', no_macro: 'no Macro Operation selected.' };
      addLog(`[Debug] Couldn't start test: ${reasons[result.reason] || result.reason || 'error'}`);
    }
    setMacroButtons(!!result.ok, false);
  } catch (e) {
    addLog("[Debug] Couldn't start test.");
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1200);
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

// Settings > Debug > "Camera Setup 2" -- same sequence as Camera Setup, but
// with a user-entered O-hold time (ms) instead of the fixed 2s.
async function runCameraSetup2(btn) {
  const original = btn.textContent;
  const msInput = document.getElementById('camera-setup-2-ms');
  const holdMs = Math.max(0, parseInt(msInput && msInput.value, 10) || 0);
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Running...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.debug_camera_setup_2(holdMs);
    btn.textContent = result.ok ? 'Started' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, Math.max(3200, holdMs + 1200));
}

// Settings > Debug > "Camera Setup 3" -- experimental: right-click drag
// down-right (diagonal), then hold the LEFT mouse button for the entered
// time (ms). For testing camera interactions the standard setup doesn't
// produce; nothing in the macro run uses it.
async function runCameraSetup3(btn) {
  const original = btn.textContent;
  const msInput = document.getElementById('camera-setup-3-ms');
  const holdMs = Math.max(0, parseInt(msInput && msInput.value, 10) || 0);
  switchScreen('dashboard');
  btn.disabled = true;
  btn.textContent = 'Running...';
  await new Promise(resolve => setTimeout(resolve, 400));
  try {
    const result = await pywebview.api.debug_camera_setup_3(holdMs);
    btn.textContent = result.ok ? 'Started' : `Failed (${result.reason || 'error'})`;
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, Math.max(3200, holdMs + 1200));
}

// Settings > General > "Install Tesseract OCR" -- unlike Camera Setup's
// fixed-timeout buttons, install_tesseract() actually signals real
// completion via push_ui (tesseractInstallDone/tesseractInstallFailed,
// see main.py), since a winget install can take anywhere from a few
// seconds to a couple minutes and a guessed timeout would either cut the
// button state off early or make a fast install look stuck.
let tesseractInstallBtn = null;

async function installTesseract(btn) {
  if (tesseractInstallBtn) return;  // already running
  tesseractInstallBtn = btn;
  btn.dataset.original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Installing...';
  try {
    const result = await pywebview.api.install_tesseract();
    if (!result.ok) { finishTesseractInstall(false); }
  } catch (e) {
    finishTesseractInstall(false);
  }
}

function finishTesseractInstall(success) {
  const btn = tesseractInstallBtn;
  if (!btn) return;
  btn.textContent = success ? 'Installed' : 'Failed';
  setTimeout(() => {
    btn.textContent = btn.dataset.original || 'Install';
    btn.disabled = false;
    tesseractInstallBtn = null;
  }, 3200);
}

window.tesseractInstallDone = () => finishTesseractInstall(true);
window.tesseractInstallFailed = () => finishTesseractInstall(false);

// Settings > General > "Open Assets Folder" (also the Image Manager's
// "Open Folder" button) -- the loose, user-editable folder every reference
// image lives in (one folder per searched name, see core/vision.py's
// template_variant_paths). Edit freely, then Reload Vision Images (or just
// use the Image Manager, which handles the reload itself).
async function openAssetsFolder(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Opening...';
  try {
    const result = await pywebview.api.open_assets_folder();
    if (!result.ok) btn.textContent = 'Failed';
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1200);
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

async function testWebhook() {
  const btn = document.getElementById('webhook-test-btn');
  const url = document.getElementById('webhook-url').value.trim();
  if (!url) { setWebhookStatus('Set a webhook URL first.', 'var(--rose)'); return; }
  btn.disabled = true;
  btn.textContent = 'Sending...';
  try {
    const result = await pywebview.api.test_webhook(url);
    setWebhookStatus(result.ok ? 'Test message sent -- check Discord.' : `Failed: ${result.reason}`,
                      result.ok ? 'var(--teal)' : 'var(--rose)');
  } catch (e) {
    setWebhookStatus('Failed to send.', 'var(--rose)');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Send Test';
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
// Macro Manager tab's saved templates during the task's matches.
const TASK_DATA = {
  story: {
    label: 'Story',
    maps: ['School Grounds', 'Rose Kingdom', 'Fairy King Forest', "King's Tomb", 'Flower Forest'],
    stages: ['1', '2', '3', '4', '5', 'Infinite', 'Mastery'],
    difficulties: ['Normal', 'Hard'],
  },
  raid: {
    label: 'Raid',
    maps: ['Spirit City'],
    stages: ['1', '2', '3'],
    fixedDifficulty: 'Hard',
  },
  expedition: {
    label: 'Expedition',
    maps: ['School Grounds', 'Flower Forest', 'Rose Kingdom'],
    difficulties: ['1', '2', '3'],
    // How many "exp_extract" prompts to decline before actually taking
    // one -- 0 extracts at the first one shown, 1 (default, matches the
    // old hardcoded behavior) waits for a second, and so on for a deeper
    // run. See core.runner._expedition_extract_accept_at.
    extractAfter: ['0', '1', '2', '3', '4', '5'],
  },
};

let taskCards = [];
let selectedTaskId = null;
// Same one-shot "only truly new rows animate" idea as enteringBlockIds on
// the Macro Manager screen -- renderTaskList() rebuilds every .task-card via
// innerHTML, so without this every card would replay its entrance
// animation on any queue change (add/remove/reorder/import), not just the
// one that's actually new.
let enteringTaskIds = new Set();
let taskTemplates = [];  // Macro Manager template names, for the Macro Operation picker
let taskSaveTimer = null;

function newTaskId() {
  return 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function defaultTask() {
  return {
    id: newTaskId(), mode: 'story',
    map: TASK_DATA.story.maps[0], stage: '1', difficulty: 'Normal',
    extract_after: '1',
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

// Export bundles the queue AND every Macro Manager template the tasks reference
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
    const newTask = { ...defaultTask(), ...t, id: newTaskId() };
    taskCards.push(newTask);
    enteringTaskIds.add(newTask.id);
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
  enteringTaskIds.add(t.id);
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
  enteringTaskIds.add(copy.id);
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
  const structural = ['mode', 'stage'];
  if (key === 'mode') {
    const d = TASK_DATA[t.mode];
    if (d.maps) t.map = d.maps[0];
    if (d.stages) t.stage = d.stages[0];
    if (d.difficulties) t.difficulty = d.difficulties[0];
    if (d.extractAfter) t.extract_after = '1';
  }
  updateQueueRowInPlace(t);
  if (structural.includes(key)) renderTaskBuilder();
  saveTaskQueue();
}

function taskOpts(list, current, fmt) {
  return list.map(o => `<option value="${o}" ${String(o) === String(current) ? 'selected' : ''}>${fmt ? fmt(o) : o}</option>`).join('');
}

// One accent per mode so the queue scans by color before you even read it.
const TASK_MODE_COLORS = { story: 'var(--brand)', raid: 'var(--rose)', expedition: 'var(--teal)' };

// The two text lines a queue row shows for a task -- where it goes, then how
// it runs. All editing happens in the Builder, rows are read-only summaries.
function taskSummary(t) {
  const d = TASK_DATA[t.mode];
  let title = d.label;
  if (t.mode === 'story' || t.mode === 'raid') {
    title += ` · ${t.map} · ${/^\d+$/.test(t.stage) ? 'Stage ' + t.stage : t.stage}`;
  } else if (t.mode === 'expedition') {
    title += ` · ${t.map}`;
  }
  const specialStage = t.mode === 'story' && (t.stage === 'Infinite' || t.stage === 'Mastery');
  const diff = ((t.mode === 'story' && !specialStage) || t.mode === 'expedition') ? t.difficulty
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
  const entering = enteringTaskIds.has(t.id) ? ' entering' : '';
  return `
    <div class="task-card${entering} ${t.id === selectedTaskId ? 'selected' : ''}" id="task_${t.id}"
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
  enteringTaskIds.clear();
}

// Patches a single queue row's summary text/accent in place instead of
// rebuilding the whole list -- setTaskProp() fires on every field edit
// (including every keystroke in Repeat), and a full renderTaskList() there
// would replay every OTHER card's entrance animation too, plus drop focus
// out of whatever input is being typed in.
function updateQueueRowInPlace(t) {
  const el = document.getElementById('task_' + t.id);
  if (!el) { renderTaskList(); return; }
  const { title, meta } = taskSummary(t);
  el.style.setProperty('--tqc', TASK_MODE_COLORS[t.mode] || 'var(--brand)');
  const titleEl = el.querySelector('.tq-title');
  const metaEl = el.querySelector('.tq-meta');
  if (titleEl) titleEl.textContent = title;
  if (metaEl) metaEl.textContent = meta;
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
  } else if (t.mode === 'expedition') {
    fields.push(field('Expedition', sel('map', d.maps)));
  }

  const specialStage = t.mode === 'story' && (t.stage === 'Infinite' || t.stage === 'Mastery');
  if ((t.mode === 'story' && !specialStage) || t.mode === 'expedition') {
    fields.push(field('Difficulty', sel('difficulty', d.difficulties)));
  } else if (d.fixedDifficulty || specialStage) {
    fields.push(field('Difficulty', `<span class="task-chip" style="align-self: flex-start;">Hard &middot; locked</span>`));
  }

  if (t.mode === 'expedition') {
    fields.push(field('Extract After', `<input type="number" class="block-input" min="0" value="${t.extract_after}"
      oninput="setTaskProp('${t.id}', 'extract_after', String(Math.max(0, parseInt(this.value, 10) || 0)))">`));
  }

  const playSeg = `
    <div class="seg-toggle">
      <button type="button" class="seg-btn ${t.play_mode === 'solo' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'play_mode', 'solo'); renderTaskBuilder()">Solo</button>
      <button type="button" class="seg-btn ${t.play_mode === 'matchmaking' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'play_mode', 'matchmaking'); renderTaskBuilder()">Matchmaking</button>
    </div>`;
  fields.push(field('Play Mode', playSeg));

  // Team Loadout rides with the chosen template (see the Macro Manager tab), so the
  // macro picker is the only loadout-related control left on a task.
  const macroSel = `
    <select class="task-select" onchange="setTaskProp('${t.id}', 'macro', this.value)">
      <option value="">No Macro</option>
      ${taskTemplates.map(n => `<option value="${n}" ${n === t.macro ? 'selected' : ''}>&#9654; ${n}</option>`).join('')}
    </select>`;
  fields.push(field('Macro Operation', macroSel));

  const extractHint = t.mode === 'expedition'
    ? `<div class="wh-hint">"Extract After" is how many extract prompts to skip before actually taking one -- 0 extracts at the first node, higher goes deeper (and takes longer) per run.</div>` : '';
  el.innerHTML = `
    <div class="task-builder-grid">${fields.join('')}</div>
    ${extractHint}
    <div class="wh-hint" style="margin-top: 8px;">The macro's Team Loadout comes from its template (Macro Manager tab).</div>
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
    const rawTasks = await pywebview.api.get_tasks();
    // "Challenge" used to be a Task Queue mode -- it never actually ran
    // (no runner support ever existed for it) and is now the dedicated
    // Challenge tab instead, so any leftover task saved under that mode
    // is dropped rather than migrated into a guessed-wrong Story task.
    const droppedChallenge = rawTasks.filter(t => t.mode === 'challenge').length;
    taskCards = rawTasks.filter(t => t.mode !== 'challenge').map(saved => {
      const t = { ...defaultTask(), ...saved };
      if (t.team == null) t.team = '';
      t.stage = String(t.stage);
      if (t.difficulty === 'Infinite' || t.difficulty === 'Mastery') {
        t.stage = t.difficulty;
        t.difficulty = 'Normal';
      }
      return t;
    });
    if (droppedChallenge) {
      addLog(`[Task] Removed ${droppedChallenge} old "Challenge" task(s) -- use the Challenge tab instead.`);
      saveTaskQueue();
    }
  } catch (e) {
    taskCards = [];
  }
  // Fresh load of the whole queue (app start / screen init) -- every card is
  // effectively new to the DOM, so let them all play the entrance stagger.
  taskCards.forEach(t => enteringTaskIds.add(t.id));
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
    if (t.mode === 'expedition') s += ` · ${t.map}`;
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
// Challenge screen: Regular Challenge automation
// ---------------------------------------------------------------------------
// Regular Challenge has 3 fixed stage slots that each rotate through one of
// the 5 Story maps over time (see main.py's CHALLENGE_STORY_MAPS comment) --
// config here is split the same way the backend models it: the daily play
// limit tracks each STAGE SLOT (whichever map is currently rotated into it),
// while Macro Operation assignment is tracked per MAP, since that's what
// needs to follow the map around as it rotates through slots.
const CHALLENGE_STAGE_SLOTS = ['1', '2', '3'];
// Mirrors main.py's CHALLENGE_STORY_MAPS -- keep in sync if Story's map
// list (TASK_DATA.story.maps) ever changes.
const CHALLENGE_STORY_MAPS = ['School Grounds', 'Rose Kingdom', 'Fairy King Forest', "King's Tomb", 'Flower Forest'];
let challengeState = null;

async function refreshChallengeScreen() {
  try {
    challengeState = await pywebview.api.get_challenge_settings();
  } catch (e) {
    challengeState = null;
  }
  await refreshTaskTemplates();  // shares the same Macro Operation list Task Builder uses
  renderChallengeScreen();
}

function renderChallengeScreen() {
  const s = challengeState;
  const enabledBtn = document.getElementById('toggle-challenge-enabled');
  if (enabledBtn) enabledBtn.classList.toggle('on', !!(s && s.enabled));
  const playMode = (s && s.play_mode) || 'solo';
  const soloBtn = document.getElementById('challenge-mode-solo');
  const mmBtn = document.getElementById('challenge-mode-matchmaking');
  if (soloBtn) soloBtn.classList.toggle('active', playMode === 'solo');
  if (mmBtn) mmBtn.classList.toggle('active', playMode === 'matchmaking');
  const lastReset = document.getElementById('challenge-last-reset');
  if (lastReset) lastReset.textContent = (s && s.last_reset_date) || '-';

  const stageList = document.getElementById('challenge-stage-list');
  if (stageList) {
    const cap = s ? s.cap : 10;
    stageList.innerHTML = CHALLENGE_STAGE_SLOTS.map(slot => {
      const info = (s && s.stages && s.stages[slot]) || { enabled: true, count: 0, ready: true };
      const atCap = cap > 0 && info.count >= cap;
      const pct = cap > 0 ? Math.min(100, Math.round((info.count / cap) * 100)) : 0;
      const statusChip = atCap ? '<span class="challenge-cap-chip">Capped</span>'
        : info.ready ? '<span class="challenge-ready-chip">Ready</span>'
        : '<span class="challenge-cap-chip" style="color: var(--text-muted); background: color-mix(in srgb, var(--text-muted) 14%, transparent); border-color: color-mix(in srgb, var(--text-muted) 35%, transparent);">Played this window</span>';
      return `
        <div class="task-card" style="--tqc: ${atCap ? 'var(--rose)' : 'var(--amber)'}; cursor: default;">
          <div class="tq-text" style="min-width: 0;">
            <div class="tq-title">Regular Challenge #${slot} ${statusChip}</div>
            <div class="challenge-map-row">
              <button class="toggle-switch ${info.enabled ? 'on' : ''}" onclick="toggleChallengeStage('${slot}', this)"></button>
              <span class="flex-1"></span>
              <div class="challenge-count-group">
                <input type="number" class="block-input" min="0" style="width: 52px;" value="${info.count}"
                       onchange="setChallengeStageCount('${slot}', this.value)">
                <span class="challenge-count-sep">/ ${cap}</span>
              </div>
            </div>
            <div class="challenge-progress"><div class="challenge-progress-fill" style="width: ${pct}%; background: ${atCap ? 'var(--rose)' : 'var(--amber)'};"></div></div>
          </div>
        </div>`;
    }).join('');
  }

  const mapList = document.getElementById('challenge-map-list');
  if (!mapList) return;
  if (!s) { mapList.innerHTML = '<div class="rh-empty">Couldn\'t load Challenge settings.</div>'; return; }
  const macroOpts = (current) => `<option value="">No Macro</option>` +
    taskTemplates.map(n => `<option value="${n}" ${n === current ? 'selected' : ''}>&#9654; ${n}</option>`).join('');
  mapList.innerHTML = CHALLENGE_STORY_MAPS.map(map => {
    const info = s.maps[map] || { macro: '' };
    return `
      <div class="task-card" style="--tqc: var(--lilac); cursor: default;">
        <div class="tq-text" style="min-width: 0;">
          <div class="tq-title">${map}</div>
          <div class="challenge-map-row">
            <select class="task-select" style="width: 100%;" onchange="setChallengeMapMacro('${escJs(map)}', this.value)">
              ${macroOpts(info.macro)}
            </select>
          </div>
        </div>
      </div>`;
  }).join('');
}

// Attribute-quote escaping for map names with apostrophes (King's Tomb),
// same problem renderPlaceUnitMapGrid's own comment already flagged.
function escJs(s) { return s.replace(/'/g, "\\'"); }

async function toggleChallengeEnabled(btn) {
  const isOn = !btn.classList.contains('on');
  btn.classList.toggle('on', isOn);
  bounceToggle(btn);
  try { await pywebview.api.set_challenge_enabled(isOn); } catch (e) {}
}

async function setChallengePlayMode(playMode) {
  try { await pywebview.api.set_challenge_play_mode(playMode); } catch (e) {}
  await refreshChallengeScreen();
}

async function toggleChallengeStage(stage, btn) {
  const isOn = !btn.classList.contains('on');
  btn.classList.toggle('on', isOn);
  bounceToggle(btn);
  try { await pywebview.api.set_challenge_stage_enabled(stage, isOn); } catch (e) {}
}

async function setChallengeMapMacro(map, value) {
  try { await pywebview.api.set_challenge_map_macro(map, value); } catch (e) {}
}

async function setChallengeStageCount(stage, value) {
  const count = Math.max(0, parseInt(value, 10) || 0);
  try { await pywebview.api.set_challenge_stage_count(stage, count); } catch (e) {}
  await refreshChallengeScreen();
}

async function resetChallengeCounts() {
  try { await pywebview.api.reset_challenge_counts(); } catch (e) {}
  addLog('[Challenge] Play counts reset.');
  await refreshChallengeScreen();
}

// ---------------------------------------------------------------------------
// Macro Manager screen: block-based drag-and-drop routine builder
// ---------------------------------------------------------------------------
const BLOCK_TYPES = {
  place_unit:        { label: 'Place Unit',        group: 'Units',  color: 'var(--lilac)', params: [{ key: 'name', type: 'text', placeholder: 'unit', default: '' }, { key: 'x', type: 'number', placeholder: 'x', default: 0 }, { key: 'y', type: 'number', placeholder: 'y', default: 0 }] },
  // Upgrade/Auto Upgrade target a placed unit by its #index (the numbering
  // Place Unit rows and the map picker share) -- bespoke controls, see
  // renderUpgradeControls()/renderAutoUpgradeControls()/renderSellUnitControls().
  upgrade_unit:       { label: 'Upgrade Unit',      group: 'Units',  color: 'var(--brand)', params: [] },
  sell_unit:          { label: 'Sell Unit',         group: 'Units',  color: 'var(--rose)',  params: [] },
  auto_upgrade_unit:  { label: 'Auto Upgrade Unit', group: 'Units',  color: 'var(--amber)', params: [] },
  // Which walk this routine uses to get to its spot before Pre Start's other
  // blocks run -- Auto (the map's own default) or a recorded Custom path.
  // Used to be a permanent pinned row instead of a real reorderable block,
  // meaning it always ran before EVERY Setting/Place Unit block no matter
  // where they were dragged -- now it's just another block, so where you
  // put it relative to the others is what actually happens. See
  // renderWalkPathControls().
  walk_path:          { label: 'Walk Path',         group: 'Pathing', color: 'var(--teal)', params: [] },
  // Mid-battle repositioning: replays a recorded WASD path (same recordings
  // walk_path uses) -- picker rendered by renderWalkControls().
  walk:               { label: 'Walk',              group: 'Pathing', color: 'var(--teal)', params: [] },
  wait_ms:            { label: 'Wait (ms)',         group: 'Timing', color: 'var(--amber)', params: [{ key: 'ms', type: 'number', placeholder: 'ms', default: 500 }] },
  wait_wave:          { label: 'Wait for Wave',     group: 'Timing', color: 'var(--amber)', params: [{ key: 'wave', type: 'number', placeholder: 'wave', default: 1 }] },
  // Value's meaning depends on kind (hotkey: a typed key spec like "hold w",
  // toggle: 'on'/'off') -- one variable-shape control instead of two near-
  // identical block types, see renderSettingControls().
  setting_change:     { label: 'Setting',           group: 'Setup',  color: 'var(--slate)', params: [{ key: 'name', type: 'text', placeholder: 'setting name', default: '' }] },
  // A raw click at a fixed position in the game window (same 1152x756
  // client coords Place Unit's x/y use) -- for any button/UI element no
  // dedicated block covers yet. Position set via the same map/Roblox-screen
  // picker Place Unit uses (see renderClickControls/openPlaceUnitModal --
  // applyPlaceUnitPosition writes params.x/y for whichever block opened
  // it, so the picker needed no changes to support this).
  click:              { label: 'Click',             group: 'Setup',  color: 'var(--rose)',  params: [{ key: 'x', type: 'number', placeholder: 'x', default: 0 }, { key: 'y', type: 'number', placeholder: 'y', default: 0 }] },
};

// Two phases: Pre Start (walk to your spot, place starter units, flip any
// settings that need to be set before the match begins -- plus Wait, for
// pacing those against game UI that needs a beat to settle) and Battle
// (everything else -- upgrades/sells/wave waits only make sense once it's live).
const PHASES = ['prestart', 'battle'];
const PHASE_LABELS = { prestart: 'Pre Start', battle: 'Battle' };
const PHASE_TAGS = { prestart: 'Setup', battle: 'Combat' };
const PHASE_ALLOWED = {
  // walk_path is deliberately in NEITHER palette: it's the one unique
  // pinned block -- every routine always has exactly one in Pre Start
  // (synthesized on new/load, never removable), so offering it as an
  // addable block would only create duplicates.
  prestart: ['place_unit', 'setting_change', 'auto_upgrade_unit', 'click', 'wait_ms'],
  battle: Object.keys(BLOCK_TYPES).filter(t => t !== 'walk_path'),
};

let creationPhases = { prestart: [], battle: [] };
let phaseCollapsed = { prestart: false, battle: false };
let recordingBlockId = null;
let savedPaths = [];

// renderPhases() rebuilds the ENTIRE block list via innerHTML on nearly every
// Macro Manager interaction (toggling Once, clone/remove, drag-drop reorder,
// changing a Setting block's kind, etc.) -- if every .block-row played its
// entrance animation unconditionally, every block would replay it on every
// one of those interactions, not just the block that actually changed. So
// the base CSS rule has no animation; only rows/panels tagged .entering get
// one, and that tag is applied ONLY to genuinely new rows (added here, then
// consumed the next time renderPhases() runs) or to the phase shell on a
// real fresh load (new template, template load) via creationFreshLoad.
let enteringBlockIds = new Set();
let creationFreshLoad = true;

// The template's Team Loadout -- used to live on each Task card; it belongs
// to the routine, so it saves with the template and the task inherits it
// through its Macro Operation pick.
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
    addLog(`[Macro Manager] Only Place Unit, Setting, Auto Upgrade Unit, Walk Path, and Click blocks can go in Pre Start -- "${def.label}" belongs in Battle.`);
    return;
  }
  const params = {};
  def.params.forEach(p => { params[p.key] = p.default; });
  const block = { id: newBlockId(), type, params, once: false };
  enteringBlockIds.add(block.id);
  if (type === 'setting_change') { block.kind = 'toggle'; block.value = 'off'; }
  if (type === 'place_unit') { block.hotkey = ''; }
  if (type === 'walk') { block.params.path = ''; }
  if (type === 'walk_path') { block.mode = 'auto'; block.pathName = ''; }
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
  // The pinned Walk Path renders without a remove button at all (see
  // renderBlockRow's isPinnedWalk) -- this guard just backs that up so no
  // other path can strip the one block every routine must keep.
  const b = creationPhases[loc.phase][loc.idx];
  if (b.type === 'walk_path' && loc.phase === 'prestart'
      && creationPhases.prestart.filter(x => x.type === 'walk_path').length <= 1) {
    return;
  }
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
  enteringBlockIds.add(copy.id);
  creationPhases[loc.phase].splice(loc.idx + 1, 0, copy);
  renderPhases();
}

function updateBlockParam(id, key, value) {
  const loc = findBlockLocation(id);
  if (loc) creationPhases[loc.phase][loc.idx].params[key] = value;
}

// "Once" -- a block flagged this way only runs the first time the routine
// executes, even across repeats (e.g. a starter placement that shouldn't
// happen again every loop).
function toggleBlockOnce(id) {
  const loc = findBlockLocation(id);
  if (loc) creationPhases[loc.phase][loc.idx].once = !creationPhases[loc.phase][loc.idx].once;
  renderPhases();
}

// Ignore Highlight -- skips the white-tile search entirely and clicks
// straight at the saved X/Y, same as clicking blind used to work before
// the search existed. For a spot where the highlight doesn't reliably
// show/detect at all, searching for it is worse than just trusting the
// saved coordinate outright.
function toggleIgnoreHighlight(id) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const block = creationPhases[loc.phase][loc.idx];
  block.ignoreHighlight = !block.ignoreHighlight;
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
  'story_click_x', 'story_click_y',
  'stage_row_x', 'stage_row_y', 'stage_row_height',
  'act_row_x', 'act_row_y', 'act_row_height',
  'challenge_stage_1_x', 'challenge_stage_1_y',
  'challenge_stage_2_x', 'challenge_stage_2_y',
  'challenge_stage_3_x', 'challenge_stage_3_y',
  'expedition_difficulty_x', 'expedition_difficulty_y',
  'team_loadout_x', 'team_loadout_y', 'team_loadout_row_height',
  'screen_middle_x', 'screen_middle_y',
  'unit_info_reset_x', 'unit_info_reset_y',
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

// The "Pick" buttons beside each coordinate pair: reuses the Place Unit
// picker modal in coord mode (see puState.coordTarget) -- captures the
// Roblox screen, and the spot clicked on it lands in the coord-<prefix>_x/_y
// inputs and saves immediately. Navigate the GAME to the screen the point
// lives on first (e.g. the stage list for stage rows) -- the capture is of
// whatever Roblox is showing right now.
async function openCoordPicker(prefix) {
  puState.blockId = null;
  puState.coordTarget = prefix;
  const xEl = document.getElementById(`coord-${prefix}_x`);
  const yEl = document.getElementById(`coord-${prefix}_y`);
  puState.markX = xEl && xEl.value !== '' ? parseInt(xEl.value) : null;
  puState.markY = yEl && yEl.value !== '' ? parseInt(yEl.value) : null;
  puState.image = null;

  document.getElementById('pu-canvas-wrap').style.display = 'none';
  document.getElementById('pu-category-tabs').innerHTML = '';
  const grid = document.getElementById('pu-map-grid');
  grid.style.display = '';
  grid.innerHTML = '<div class="rh-empty">Capturing the Roblox screen...</div>';
  document.getElementById('pu-pos-readout').textContent = puState.markX != null ? `X ${puState.markX}, Y ${puState.markY}` : 'Not set';
  document.getElementById('pu-modal').style.display = 'flex';

  const ok = await usePlaceUnitRobloxScreen();
  if (!ok) closePlaceUnitModal();  // no Roblox to capture -- nothing to pick on
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
// Where the freshly saved path name should land once the player names it in
// the modal: whichever block's Record button started this (a Walk Path
// block sets mode/pathName on itself, a plain Walk block sets its own path
// param -- see savePathName). Survives the gap between Stop and Save, which
// recordingBlockId (nulled on Stop) doesn't.
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
    // Back to Macro Manager BEFORE showing the naming dialog: the docked Roblox
    // window paints over all DOM on the Dashboard, so a dialog there sits
    // invisibly behind the game. Macro Manager hides Roblox entirely.
    switchScreen('creation');
    if (!stopRes || !stopRes.count) {
      addLog('[Macro Manager] Nothing recorded -- no movement detected.');
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
  // exists on the Dashboard screen -- Macro Manager hides Roblox entirely (see
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
      addLog('[Macro Manager] Recording path -- walk with WASD (I/O also recorded, timer starts on your first key), click Stop Recording when done.');
    } else {
      addLog(`[Macro Manager] Couldn't start recording: ${result.reason || 'error'}`);
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
  restoreGameIfDashboard();
  try {
    const result = await pywebview.api.save_pending_path(name);
    if (result.ok) {
      await refreshSavedPaths();
      const loc = pendingRecordingTarget ? findBlockLocation(pendingRecordingTarget) : null;
      if (loc) {
        const block = creationPhases[loc.phase][loc.idx];
        if (block.type === 'walk_path') { block.mode = 'custom'; block.pathName = result.name; }
        else block.params.path = result.name;
      }
      addLog(`[Macro Manager] Saved path "${result.name}".`);
    } else {
      addLog(`[Macro Manager] Couldn't save path: ${result.reason || 'error'}`);
    }
  } catch (e) {}
  pendingRecordingTarget = null;
  renderPhases();
}

async function discardPathRecording() {
  document.getElementById('path-name-modal').style.display = 'none';
  restoreGameIfDashboard();
  try { await pywebview.api.discard_pending_path(); } catch (e) {}
  pendingRecordingTarget = null;
  addLog('[Macro Manager] Recording discarded.');
  renderPhases();
}

function renderPalette() {
  const el = document.getElementById('block-palette');
  if (!el) return;
  // Grouped by what the block acts on (Units / Pathing / Timing) so the
  // palette scans as sections instead of one undifferentiated stack.
  // walk_path is skipped: it's the pinned block every routine already has
  // (see renderPhases' invariant), so there's nothing to drag in.
  const groups = [];
  for (const [type, def] of Object.entries(BLOCK_TYPES)) {
    if (type === 'walk_path') continue;
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

// Setting block: the value control's shape follows `kind` -- a typed custom
// key spec, or an On/Off toggle -- so this can't be expressed as one of the
// static `params` field types renderParamInput handles.
function setSettingKind(id, kind) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const b = creationPhases[loc.phase][loc.idx];
  b.kind = kind;
  b.value = kind === 'toggle' ? 'off' : '';
  renderPhases();
}

function setSettingValue(id, value) {
  const loc = findBlockLocation(id);
  if (loc) creationPhases[loc.phase][loc.idx].value = value;
}

// Place Unit's hotkey field still uses real key-CAPTURE (press a key, it's
// bound) -- {blockId, field} says which block/field the next keypress
// writes into. Shares mapKeyName() with the global Settings > Hotkeys
// capture (see startRebind()'s keydown listener), but is a no-op whenever
// nothing is capturing, so it never fights that other listener over the
// same keypress.
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
    </select>`;

  if (b.kind === 'hotkey') {
    // A typed spec, not a captured keypress -- lets a Setting block send a
    // key the game needs HELD (e.g. "hold w" to walk, or "hold w 800ms" for
    // an exact duration) instead of only a single tap. core.keys.py's
    // blacklist rejects Windows/Meta-style names server-side regardless of
    // what's typed here.
    return kindSel + `
      <input class="block-input" type="text" value="${b.value || ''}" placeholder="e.g. w, hold w, hold w 800ms"
             onchange="setSettingValue('${b.id}', this.value)" onclick="event.stopPropagation()">`;
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
  const ignoreHighlight = `<button type="button" class="block-mod-btn ${b.ignoreHighlight ? 'on' : ''} tooltip-side" data-tooltip="Skip the white-tile search and click the saved X/Y directly" onclick="toggleIgnoreHighlight('${b.id}')">Ignore Highlight</button>`;
  return idx + name + x + y + hotkey + set + ignoreHighlight;
}

// Click block: X/Y plus the same Set/position-picker button Place Unit has
// (openPlaceUnitModal works for any block with x/y params -- see
// applyPlaceUnitPosition), minus the unit-only extras (name/hotkey/ignore
// highlight) that make no sense for a bare click.
function renderClickControls(b) {
  const field = (label, inner) => `
    <label class="blk-field"><span class="blk-field-label">${label}</span>${inner}</label>`;
  const x = field('X', `<input class="block-input" type="number" value="${b.params.x}" oninput="updateBlockParam('${b.id}', 'x', this.value)">`);
  const y = field('Y', `<input class="block-input" type="number" value="${b.params.y}" oninput="updateBlockParam('${b.id}', 'y', this.value)">`);
  const hasPos = b.params.x || b.params.y;
  const set = field('Position', `<button type="button" class="pu-set-btn ${hasPos ? 'has-pos' : ''} tooltip-side" data-tooltip="Pick the spot to click on a map or your Roblox screen" onclick="openPlaceUnitModal('${b.id}')">${hasPos ? 'Set &#10003;' : 'Set'}</button>`);
  return x + y + set;
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

// Walk Path block: Auto (the map's own default_walk_paths entry) or a
// specific recorded Custom path -- same Auto/Custom choice the old pinned
// row offered, just stored on the block itself (b.mode/b.pathName) instead
// of a separate template-level config, so it can actually be reordered.
function renderWalkPathControls(b) {
  const isRecording = recordingBlockId === b.id;
  const modeSeg = `
    <div class="seg-toggle">
      <button type="button" class="seg-btn ${b.mode === 'auto' ? 'active' : ''}" onclick="setWalkPathMode('${b.id}', 'auto')">Auto</button>
      <button type="button" class="seg-btn ${b.mode === 'custom' ? 'active' : ''}" onclick="setWalkPathMode('${b.id}', 'custom')">Custom</button>
    </div>`;
  let customControls = '';
  if (b.mode === 'custom') {
    const options = savedPaths.map(n => `<option value="${n}" ${n === b.pathName ? 'selected' : ''}>${n}</option>`).join('');
    customControls = `
      <button type="button" class="block-mod-btn ${isRecording ? 'on' : ''}" onclick="toggleRecordPath('${b.id}')">${isRecording ? 'Stop' : 'Record'}</button>
      <select class="block-input" style="width:auto;" onchange="setWalkPathPath('${b.id}', this.value)"><option value="">Pick saved path...</option>${options}</select>`;
  }
  return modeSeg + customControls;
}

function setWalkPathMode(id, mode) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  creationPhases[loc.phase][loc.idx].mode = mode;
  renderPhases();
}

function setWalkPathPath(id, name) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  creationPhases[loc.phase][loc.idx].pathName = name;
  renderPhases();
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
  // place_unit and click render ALL their fields bespoke (labeled X/Y +
  // the Set picker button) -- the generic anonymous param inputs would
  // duplicate them.
  const inputs = (b.type === 'place_unit' || b.type === 'click') ? '' : def.params.map(p => renderParamInput(b, p)).join('');
  const extra = b.type === 'setting_change' ? renderSettingControls(b)
    : b.type === 'place_unit' ? renderPlaceUnitControls(b)
    : b.type === 'click' ? renderClickControls(b)
    : b.type === 'walk' ? renderWalkControls(b)
    : b.type === 'walk_path' ? renderWalkPathControls(b)
    : b.type === 'upgrade_unit' ? renderUpgradeControls(b)
    : b.type === 'auto_upgrade_unit' ? renderAutoUpgradeControls(b)
    : b.type === 'sell_unit' ? renderSellUnitControls(b) : '';
  const entering = enteringBlockIds.has(b.id) ? ' entering' : '';
  // Walk Path is the one unique pinned block: the sole Pre Start copy
  // (legacy templates can still carry extras, which render as normal
  // removable rows) is simply always there -- the permanent pinned-row
  // look of old: not draggable, no clone/remove/Once controls, a walk
  // icon in place of the drag handle and a fixed RUNS ONCE badge on the
  // right, since walking only ever runs on the first entry into a stage
  // (repeating it would walk you away from your spot). renderPhases'
  // invariant keeps it at the top of the list.
  const isPinnedWalk = b.type === 'walk_path' && phase === 'prestart'
    && creationPhases.prestart.filter(x => x.type === 'walk_path').length <= 1;
  if (isPinnedWalk) {
    return `
    <div class="block-row pinned${entering}" style="--blk: ${def.color};" data-id="${b.id}"
         ondragover="onBlockRowDragOver(event, '${phase}', '${b.id}')"
         ondrop="onBlockDrop(event, '${phase}', '${b.id}')">
      <svg class="pinned-walk-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/>
        <circle cx="12" cy="10" r="3"/>
      </svg>
      <span class="block-label" style="color: var(--teal);">${def.label}</span>
      ${extra}
      <span class="flex-1"></span>
      <span class="pinned-walk-badge" title="Pinned -- every routine walks once, on its first entry into a stage. Auto uses the map's default path (Settings > Debug > Pathing) and does nothing for maps without one.">Runs Once</span>
    </div>
  `;
  }
  const onceBtn = `<button type="button" class="block-mod-btn ${b.once ? 'on' : ''}" onclick="toggleBlockOnce('${b.id}')" title="Only run this block once, even if the routine repeats">Once</button>`;
  return `
    <div class="block-row${entering}" style="--blk: ${def.color};" draggable="true" data-id="${b.id}"
         ondragstart="if (['INPUT','SELECT','BUTTON'].includes(event.target.tagName)) { event.preventDefault(); return false; } event.dataTransfer.setData('block-reorder', '${b.id}')"
         ondragover="onBlockRowDragOver(event, '${phase}', '${b.id}')"
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
  // Settings > Debug > Macro Coordinates "Pick" mode: a coord key prefix
  // (e.g. 'story_click') instead of a block -- a picked spot writes to the
  // coord-<prefix>_x/_y settings inputs rather than a block's params. The
  // two targets are mutually exclusive (blockId null while this is set).
  coordTarget: null,
};

// Remembers whichever map was picked last (see selectPlaceUnitMap), across
// blocks AND app restarts (localStorage, not just in-memory) -- setting
// several units' positions in a row is almost always on the SAME map, and
// having to re-click category -> thumbnail every single time for that was
// the actual complaint.
const RECENT_PLACE_UNIT_MAP_KEY = 'aecm-recent-place-unit-map';

function getRecentPlaceUnitMap() {
  try {
    const raw = localStorage.getItem(RECENT_PLACE_UNIT_MAP_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function setRecentPlaceUnitMap(category, name) {
  try {
    localStorage.setItem(RECENT_PLACE_UNIT_MAP_KEY, JSON.stringify({ category, name }));
  } catch (e) {}
}

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

  // Jump straight to the canvas for the last-picked map instead of always
  // starting back at the first category's grid -- "<- Maps" in the canvas
  // view is still right there if a different map's actually needed this time.
  const recent = getRecentPlaceUnitMap();
  if (recent && puState.categories.includes(recent.category)) {
    puState.category = recent.category;
    renderPlaceUnitCategoryTabs();
    try {
      puState.maps = await pywebview.api.list_maps(recent.category);
    } catch (e) {
      puState.maps = [];
    }
    if (puState.maps.includes(recent.name)) {
      await selectPlaceUnitMap(recent.name);
      return;
    }
  }
  await selectPlaceUnitCategory(puState.categories[0]);
}

function closePlaceUnitModal() {
  document.getElementById('pu-modal').style.display = 'none';
  puState.blockId = null;
  puState.coordTarget = null;
  restoreGameIfDashboard();  // see isBlockingOverlayOpen -- game stays hidden while this modal is up
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
    if (!result.ok) { addLog(`[Macro Manager] Couldn't load map "${name}".`); return; }
    loadPlaceUnitImage(result.data_uri);
    setRecentPlaceUnitMap(puState.category, name);
  } catch (e) {}
}

// Same dance as saveDebugScreenshot()/readRewards(): the game is hidden and
// not rendering anywhere except the Dashboard, so switch there first, let it
// settle and paint a real frame, capture, then come straight back. The modal
// stays open the whole time (the game just paints over it for a moment).
async function usePlaceUnitRobloxScreen() {
  // captureDanceActive: this hop NEEDS show_game() to fire even though our
  // modal is open (see isBlockingOverlayOpen) -- the game being visible is
  // what makes the screenshot possible at all. Returns whether a capture
  // actually loaded (the Macro Coordinates Pick flow closes its modal on
  // false -- see openCoordPicker).
  const returnTo = currentScreen;  // 'creation' for Place Unit, 'settings' for a coord Pick
  captureDanceActive = true;
  let result = null;
  try {
    switchScreen('dashboard');
    await new Promise(resolve => setTimeout(resolve, 400));
    try {
      result = await pywebview.api.get_roblox_snapshot();
    } catch (e) {}
    switchScreen(returnTo === 'dashboard' ? 'creation' : returnTo);
  } finally {
    captureDanceActive = false;
  }
  if (!result || !result.ok) {
    addLog(`[Macro Manager] Couldn't capture Roblox screen: ${(result && result.reason) || 'error'}`);
    return false;
  }
  loadPlaceUnitImage(result.data_uri);
  return true;
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

  // Placed-unit markers are Macro Manager context -- noise on a Macro
  // Coordinates pick, where no blocks are involved.
  for (const u of (puState.coordTarget ? [] : otherPlacedUnits())) {
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
  if (puState.coordTarget) {
    // Macro Coordinates Pick mode -- write straight to the settings inputs
    // and persist, no block involved (see openCoordPicker).
    const p = puState.coordTarget;
    const xEl = document.getElementById(`coord-${p}_x`);
    const yEl = document.getElementById(`coord-${p}_y`);
    if (xEl) xEl.value = puState.markX;
    if (yEl) yEl.value = puState.markY;
    setMacroCoord(`${p}_x`, puState.markX);
    setMacroCoord(`${p}_y`, puState.markY);
    document.getElementById('pu-pos-readout').textContent = `X ${puState.markX}, Y ${puState.markY}`;
    return;
  }
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


// ---------------------------------------------------------------------------
// Image Manager modal (Settings > General > Image Search)
// ---------------------------------------------------------------------------
// Library of every reference image the macro's image search uses (one card
// per searched name = one folder on disk, see core/vision.py's
// template_variant_paths), plus capture-and-crop: freeze a screenshot of the
// docked Roblox window on a canvas, drag a box around a button/text, save it
// into a name's folder as an extra variant image. The crop itself is cut
// server-side from the exact captured frame (main.Api.save_image_search_crop)
// -- the canvas only ever reports image-space coordinates, so zoom/pan can't
// affect what actually gets saved. Same frozen-screenshot approach as the
// Place Unit picker: nothing done in this modal can ever reach the live game.

let imState = {
  data: null,       // list_vision_templates() categories, or null before first load
  category: 'ui',   // active tab key -- doubles as the category a saved crop goes to
  image: null, naturalW: 0, naturalH: 0,   // the frozen capture (an <img>, drawn to canvas)
  zoom: 1, panX: 0, panY: 0,               // canvas view transform (image px -> canvas px)
  sel: null,        // crop box in IMAGE pixels {x, y, w, h} -- null until a drag happens
};

// Hotkey entry point (Settings > Hotkeys > Image Manager, default F6,
// called via push_ui from the Python-side hook): TOGGLES the modal from
// anywhere. Opening from the Dashboard hops to Settings first -- the
// docked Roblox window is a native child that paints over all DOM, so the
// modal would open invisibly behind it there.
function toggleImageManagerHotkey() {
  const modal = document.getElementById('im-modal');
  if (modal && modal.style.display === 'flex') {
    closeImageManager();
    return;
  }
  if (currentScreen === 'dashboard') switchScreen('settings');
  openImageManager();
}

async function openImageManager() {
  document.getElementById('im-modal').style.display = 'flex';
  backToImageLibrary();
  // Render immediately from whatever's cached (instant open on a re-visit),
  // then refresh from disk -- the listing must reflect files the user may
  // have just added/removed by hand in the Assets folder.
  if (imState.data) { renderImageManagerTabs(); renderImageLibrary(); }
  await refreshImageManagerData();
}

function closeImageManager() {
  document.getElementById('im-modal').style.display = 'none';
  imState.image = null;
  imState.sel = null;
  restoreGameIfDashboard();  // closed while on the Dashboard (e.g. via the F6/F4 hotkeys) -- bring the game back
}

async function refreshImageManagerData() {
  try {
    const result = await pywebview.api.list_vision_templates();
    imState.data = (result && result.ok) ? result.categories : [];
  } catch (e) {
    imState.data = [];
  }
  if (!imState.data.some(c => c.key === imState.category) && imState.data.length > 0) {
    imState.category = imState.data[0].key;
  }
  renderImageManagerTabs();
  renderImageLibrary();
  renderImageNameDatalist();
}

function renderImageManagerTabs() {
  const el = document.getElementById('im-category-tabs');
  el.innerHTML = `<div class="seg-toggle" style="width: auto;">` +
    (imState.data || []).map(c => `
      <button type="button" class="seg-btn ${c.key === imState.category ? 'active' : ''}" style="padding: 6px 16px;"
              onclick="selectImageManagerCategory('${c.key}')">${c.label}</button>
    `).join('') + `</div>`;
}

function selectImageManagerCategory(key) {
  imState.category = key;
  renderImageManagerTabs();
  renderImageLibrary();
  renderImageNameDatalist();
}

function imActiveCategory() {
  return (imState.data || []).find(c => c.key === imState.category) || { names: [] };
}

// The save bar's name suggestions -- every existing name in the active
// category, so "add a variant to something that already exists" is a pick
// instead of an exact retype (a typo'd name would silently create a NEW
// folder the runner never searches).
function renderImageNameDatalist() {
  const el = document.getElementById('im-name-list');
  el.innerHTML = imActiveCategory().names.map(n => {
    const opt = document.createElement('option');
    opt.value = n.name;
    return opt.outerHTML;
  }).join('');
}

// Built via DOM calls (not innerHTML + inline onclick) so names with
// apostrophes ("King's Tomb") never need attribute-quote escaping -- same
// reasoning as renderPlaceUnitMapGrid.
function renderImageLibrary() {
  const el = document.getElementById('im-library');
  el.innerHTML = '';
  const filter = (document.getElementById('im-filter').value || '').toLowerCase();
  const names = imActiveCategory().names.filter(n => n.name.toLowerCase().includes(filter));
  if (names.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'im-empty';
    empty.textContent = filter
      ? 'No names match that filter.'
      : 'No images in this category yet -- use Capture Roblox to add some, or check the Assets folder exists next to the app.';
    el.appendChild(empty);
    return;
  }
  for (const n of names) {
    const card = document.createElement('div');
    card.className = 'im-card';

    const head = document.createElement('div');
    head.className = 'im-card-head';
    const label = document.createElement('span');
    label.className = 'im-card-name';
    label.textContent = n.name;
    label.title = n.name;
    const count = document.createElement('span');
    count.className = 'im-card-count';
    count.textContent = n.images.length;
    count.title = `${n.images.length} image(s) -- every one gets tried when the macro searches for "${n.name}"`;
    const add = document.createElement('span');
    add.className = 'im-card-add';
    add.textContent = '+';
    add.title = `Capture your Roblox screen and crop a new variant of "${n.name}"`;
    add.addEventListener('click', () => startImageCapture(n.name));
    head.appendChild(label);
    head.appendChild(count);
    head.appendChild(add);
    card.appendChild(head);

    const thumbs = document.createElement('div');
    thumbs.className = 'im-thumbs';
    for (const img of n.images) {
      const wrap = document.createElement('div');
      wrap.className = 'im-thumb';
      const pic = document.createElement('img');
      pic.src = img.data_uri;
      pic.alt = img.file;
      pic.title = img.file;
      const del = document.createElement('span');
      del.className = 'im-thumb-del';
      del.textContent = '×';
      del.title = 'Delete this image (click twice)';
      del.addEventListener('click', () => deleteTemplateImage(n.name, img.file, del));
      wrap.appendChild(pic);
      wrap.appendChild(del);
      thumbs.appendChild(wrap);
    }
    card.appendChild(thumbs);
    el.appendChild(card);
  }
}

// Two-step delete: first click arms the button (turns red), second click
// within 2.5s actually deletes. Deliberately NOT a native confirm() -- those
// render behind the docked Roblox window (same reason the path-name modal
// exists, see index.html) and would look like the app locked up.
let imDeleteArmed = null;  // { el, timer } of the currently-armed delete, if any

function imDisarmDelete() {
  if (!imDeleteArmed) return;
  clearTimeout(imDeleteArmed.timer);
  imDeleteArmed.el.classList.remove('armed');
  imDeleteArmed = null;
}

async function deleteTemplateImage(name, file, el) {
  if (!imDeleteArmed || imDeleteArmed.el !== el) {
    imDisarmDelete();
    el.classList.add('armed');
    imDeleteArmed = { el, timer: setTimeout(imDisarmDelete, 2500) };
    return;
  }
  imDisarmDelete();
  try {
    const result = await pywebview.api.delete_vision_template_image(imState.category, name, file);
    if (!result.ok) {
      addLog(`[Images] Couldn't delete ${file}: ${result.reason || 'error'}`);
      return;
    }
  } catch (e) {
    addLog(`[Images] Couldn't delete ${file}.`);
    return;
  }
  await refreshImageManagerData();
}

// Same dance as usePlaceUnitRobloxScreen: the game only renders while the
// Dashboard is showing, so hop there, let it paint a real frame, capture,
// hop back. The modal stays open throughout (the game just paints over it
// for a moment). prefillName comes from a card's "+" button -- straight to
// cropping a new variant of that specific name.
async function startImageCapture(prefillName) {
  const returnScreen = currentScreen === 'dashboard' ? lastNonDashboardScreen : currentScreen;
  // See usePlaceUnitRobloxScreen -- the game must actually show during
  // this hop despite the open modal.
  captureDanceActive = true;
  let result = null;
  try {
    switchScreen('dashboard');
    await new Promise(resolve => setTimeout(resolve, 400));
    try {
      result = await pywebview.api.capture_image_search_screen();
    } catch (e) {}
    switchScreen(returnScreen);
  } finally {
    captureDanceActive = false;
  }
  if (!result || !result.ok) {
    addLog(`[Images] Couldn't capture Roblox screen: ${(result && result.reason) || 'error'} -- is Roblox docked?`);
    return;
  }
  if (typeof prefillName === 'string') {
    document.getElementById('im-save-name').value = prefillName;
  }
  const img = new Image();
  img.onload = () => {
    imState.image = img;
    imState.naturalW = img.naturalWidth;
    imState.naturalH = img.naturalHeight;
    imState.sel = null;
    fitImageCanvas();
    document.getElementById('im-library').style.display = 'none';
    document.getElementById('im-capture-wrap').style.display = '';
    document.getElementById('im-crop-readout').textContent = 'No selection';
    drawImageCanvas();
  };
  img.src = result.data_uri;
}

function backToImageLibrary() {
  document.getElementById('im-capture-wrap').style.display = 'none';
  document.getElementById('im-library').style.display = '';
  imState.image = null;
  imState.sel = null;
}

// Contain-fit the capture in the canvas as the starting zoom/pan -- wheel
// zoom and right-drag pan take over from there (left-drag is the crop
// selection, unlike the Place Unit canvas where it pans).
function fitImageCanvas() {
  const canvas = document.getElementById('im-canvas');
  const scale = Math.min(canvas.width / imState.naturalW, canvas.height / imState.naturalH);
  imState.zoom = scale;
  imState.panX = (canvas.width - imState.naturalW * scale) / 2;
  imState.panY = (canvas.height - imState.naturalH * scale) / 2;
}

function drawImageCanvas() {
  const canvas = document.getElementById('im-canvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!imState.image) return;
  ctx.drawImage(imState.image, imState.panX, imState.panY,
                imState.naturalW * imState.zoom, imState.naturalH * imState.zoom);

  if (imState.sel) {
    // Dim everything OUTSIDE the selection instead of just outlining it --
    // reads instantly as "this is what gets saved" even on busy game art.
    const sx = imState.panX + imState.sel.x * imState.zoom;
    const sy = imState.panY + imState.sel.y * imState.zoom;
    const sw = imState.sel.w * imState.zoom;
    const sh = imState.sel.h * imState.zoom;
    ctx.fillStyle = 'rgba(8, 10, 18, 0.55)';
    ctx.fillRect(0, 0, canvas.width, Math.max(0, sy));                                  // above
    ctx.fillRect(0, sy + sh, canvas.width, Math.max(0, canvas.height - (sy + sh)));     // below
    ctx.fillRect(0, sy, Math.max(0, sx), sh);                                           // left
    ctx.fillRect(sx + sw, sy, Math.max(0, canvas.width - (sx + sw)), sh);               // right
    ctx.strokeStyle = '#7c9dff';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(sx, sy, sw, sh);
  }
}

function imUpdateReadout() {
  const el = document.getElementById('im-crop-readout');
  el.textContent = imState.sel
    ? `${imState.sel.w} × ${imState.sel.h}px at ${imState.sel.x}, ${imState.sel.y}`
    : 'No selection';
}

// Crop-canvas interactions: LEFT-drag draws the selection box, wheel zooms
// toward the cursor (crops are often tiny -- a nav button is ~40px tall --
// so zooming in before dragging is the normal flow, hence the hint text),
// RIGHT-drag pans (left is taken by selection, unlike the Place Unit
// canvas). Selection is stored in IMAGE pixels so zooming/panning after
// drawing it doesn't move what gets saved.
(function () {
  const canvas = document.getElementById('im-canvas');
  if (!canvas) return;
  let selecting = false, panning = false;
  let startImgX = 0, startImgY = 0, lastX = 0, lastY = 0;

  function canvasPoint(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    return {
      cx: (clientX - rect.left) * (canvas.width / rect.width),
      cy: (clientY - rect.top) * (canvas.height / rect.height),
    };
  }

  function toImagePoint(clientX, clientY) {
    const { cx, cy } = canvasPoint(clientX, clientY);
    return {
      // Clamped to the image bounds so a drag that wanders off the edge
      // still produces a valid, fully-inside crop box.
      x: Math.min(imState.naturalW, Math.max(0, (cx - imState.panX) / imState.zoom)),
      y: Math.min(imState.naturalH, Math.max(0, (cy - imState.panY) / imState.zoom)),
    };
  }

  canvas.addEventListener('wheel', (e) => {
    if (!imState.image) return;
    e.preventDefault();
    const { cx, cy } = canvasPoint(e.clientX, e.clientY);
    const imgX = (cx - imState.panX) / imState.zoom;
    const imgY = (cy - imState.panY) / imState.zoom;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    imState.zoom = Math.min(12, Math.max(0.2, imState.zoom * factor));
    imState.panX = cx - imgX * imState.zoom;
    imState.panY = cy - imgY * imState.zoom;
    drawImageCanvas();
  }, { passive: false });

  // Right-click pans, so its context menu would fire on every pan-release.
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());

  canvas.addEventListener('mousedown', (e) => {
    if (!imState.image) return;
    if (e.button === 2) {
      panning = true;
      lastX = e.clientX;
      lastY = e.clientY;
    } else if (e.button === 0) {
      selecting = true;
      const p = toImagePoint(e.clientX, e.clientY);
      startImgX = p.x;
      startImgY = p.y;
      imState.sel = { x: Math.round(p.x), y: Math.round(p.y), w: 0, h: 0 };
      drawImageCanvas();
      imUpdateReadout();
    }
  });

  window.addEventListener('mousemove', (e) => {
    if (panning) {
      const rect = canvas.getBoundingClientRect();
      imState.panX += (e.clientX - lastX) * (canvas.width / rect.width);
      imState.panY += (e.clientY - lastY) * (canvas.height / rect.height);
      lastX = e.clientX;
      lastY = e.clientY;
      drawImageCanvas();
    } else if (selecting) {
      const p = toImagePoint(e.clientX, e.clientY);
      imState.sel = {
        x: Math.round(Math.min(startImgX, p.x)),
        y: Math.round(Math.min(startImgY, p.y)),
        w: Math.round(Math.abs(p.x - startImgX)),
        h: Math.round(Math.abs(p.y - startImgY)),
      };
      drawImageCanvas();
      imUpdateReadout();
    }
  });

  window.addEventListener('mouseup', () => {
    panning = false;
    if (selecting) {
      selecting = false;
      // A no-drag click clears the selection -- matches the "click empty
      // space to deselect" instinct and removes a stray 0-size box.
      if (imState.sel && (imState.sel.w < 2 || imState.sel.h < 2)) imState.sel = null;
      drawImageCanvas();
      imUpdateReadout();
    }
  });
})();

async function saveImageCrop() {
  const btn = document.getElementById('im-save-btn');
  const name = document.getElementById('im-save-name').value.trim();
  if (!imState.sel || imState.sel.w < 4 || imState.sel.h < 4) {
    addLog('[Images] Drag a box around the button/text first (at least 4x4px).');
    return;
  }
  if (!name) {
    addLog('[Images] Type or pick a name to save the crop under first.');
    return;
  }
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const result = await pywebview.api.save_image_search_crop(
      imState.category, name, imState.sel.x, imState.sel.y, imState.sel.w, imState.sel.h);
    if (!result.ok) {
      addLog(`[Images] Save failed: ${result.reason || 'error'}`);
      btn.textContent = 'Failed';
    } else {
      btn.textContent = 'Saved!';
      // Refresh the library data in the background but STAY in capture view
      // with the screenshot up -- one capture usually yields several crops
      // (e.g. a whole screen's worth of buttons) in a row.
      refreshImageManagerData();
      imState.sel = null;
      drawImageCanvas();
      imUpdateReadout();
    }
  } catch (e) {
    addLog('[Images] Save failed.');
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = 'Save Crop'; btn.disabled = false; }, 1400);
}

// Settings > Debug > "Reload Vision Images" -- drops core.vision's in-memory
// template cache so images added/replaced by hand in the Assets folder are
// picked up without an app restart. (The Image Manager's own save/delete
// already do this automatically.)
async function reloadVisionTemplates(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Reloading...';
  try {
    await pywebview.api.reload_vision_templates();
    btn.textContent = 'Reloaded';
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1400);
}


// Team Loadout controls in the Macro Manager top bar -- saved as part of the
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
  // The pinned Walk Path invariant, enforced at the render chokepoint so
  // EVERY path into the editor keeps it -- initial page load (which starts
  // from the bare `creationPhases` literal and never went through
  // newTemplate/load), New, Load, imports, and any drag/drop edit: exactly
  // one walk_path sits at the top of Pre Start with Once on. Legacy
  // templates carrying extra walk copies keep them (in stored order) until
  // they're deleted down to the one that pins.
  const walks = creationPhases.prestart.filter(b => b.type === 'walk_path');
  if (walks.length === 0) {
    creationPhases.prestart.unshift({ id: newBlockId(), type: 'walk_path', params: {}, once: true, mode: 'auto', pathName: '' });
  } else if (walks.length === 1) {
    walks[0].once = true;
    const idx = creationPhases.prestart.indexOf(walks[0]);
    if (idx > 0) {
      creationPhases.prestart.splice(idx, 1);
      creationPhases.prestart.unshift(walks[0]);
    }
  }
  // Only a genuine fresh load (initial page render, New Template, Load
  // Template) plays the phase-panel/pinned-row entrance -- every other call
  // is just reflecting an edit to the existing list, so those shells should
  // stay put. Consumed once per call, same one-shot idea as enteringBlockIds.
  const freshPhase = creationFreshLoad;
  const panelEntering = freshPhase ? ' entering' : '';
  el.innerHTML = PHASES.map(phase => {
    const blocks = creationPhases[phase];
    const emptyText = phase === 'prestart'
      ? 'Drag Place Unit, Setting, Auto Upgrade Unit, Click, or Wait blocks here -- only those are possible before the match starts.'
      : 'Drag blocks here -- upgrades, sells, waits, clicks, anything goes mid-battle.';
    const emptyDiv = `<div class="text-xs text-center" style="color: var(--text-muted); padding: 16px 0;">${emptyText}</div>`;
    // Pre Start always holds at least the pinned Walk Path (see the
    // invariant at the top of this function), so its "empty" hint shows
    // when that's the ONLY thing there -- same layout the old permanent
    // pinned row had: the row up top, the drag hint below it.
    const onlyPinned = phase === 'prestart' && blocks.length === 1 && blocks[0].type === 'walk_path';
    const body = blocks.length === 0 ? emptyDiv
      : blocks.map(b => renderBlockRow(b, phase)).join('') + (onlyPinned ? emptyDiv : '');
    // The pinned Walk Path is furniture, not content -- the count reads as
    // "blocks you added", same as when it was a literal pinned row.
    const hasPinnedWalk = phase === 'prestart' && blocks.filter(b => b.type === 'walk_path').length === 1;
    const blockCount = blocks.length - (hasPinnedWalk ? 1 : 0);
    return `
      <div class="phase-panel${panelEntering} ${phaseCollapsed[phase] ? 'collapsed' : ''}">
        <div class="phase-head" onclick="togglePhaseCollapsed('${phase}')">
          <svg class="phase-chevron w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
          ${PHASE_LABELS[phase]}
          <span class="rp-head-tag" style="--rp-tag: ${phase === 'prestart' ? 'var(--teal)' : 'var(--rose)'}; margin-left: 2px;">${PHASE_TAGS[phase]}</span>
          <span class="phase-count">${blockCount}</span>
        </div>
        <div id="creation-canvas-${phase}" class="canvas-dropzone p-2"
             ondragover="onCanvasDragOver(event, '${phase}')" ondragleave="onCanvasDragLeave(event, '${phase}')"
             ondrop="onCanvasDrop(event, '${phase}')">${body}</div>
      </div>
    `;
  }).join('');
  enteringBlockIds.clear();
  creationFreshLoad = false;
  renderCreationLoadout();
}

// Opens a real gap where a dragged block (from the palette OR an existing
// row being reordered) would land, instead of just highlighting a border --
// a single placeholder element moved to wherever the cursor currently is,
// whose height transitioning from 0 (see .block-drop-placeholder in
// style.css) makes the actual block-rows around it slide apart/back
// together for free, no manual per-row animation needed.
let blockDropPlaceholder = null;

function getBlockDropPlaceholder() {
  if (!blockDropPlaceholder) {
    blockDropPlaceholder = document.createElement('div');
    blockDropPlaceholder.className = 'block-drop-placeholder';
    // The placeholder itself has no drop handling of its own by default --
    // dropping directly ON it (exactly what its own "here's the gap" visual
    // invites) had nowhere to go but bubble straight past the row it's
    // sitting next to, up to the canvas-level ondrop, which just appends to
    // the end (toIdx: null) instead of landing where the placeholder was
    // actually showing. ondragover needs its own preventDefault too --
    // without it the browser refuses the drop outright the moment the
    // cursor is over the placeholder rather than a row.
    blockDropPlaceholder.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); };
    blockDropPlaceholder.ondrop = onPlaceholderDrop;
  }
  return blockDropPlaceholder;
}

// Resolves a drop landing on the placeholder div itself into the same
// index math onBlockDrop uses for a row -- toIdx is just "how many
// .block-row elements sit before the placeholder right now", since
// onBlockRowDragOver already positioned it exactly where the block should
// land as the drag moved across rows.
function onPlaceholderDrop(e) {
  e.preventDefault();
  e.stopPropagation();
  const placeholder = blockDropPlaceholder;
  const zone = placeholder && placeholder.parentElement;
  if (!zone) { removeBlockDropPlaceholder(); return; }
  const phase = zone.id === 'creation-canvas-prestart' ? 'prestart' : 'battle';
  let toIdx = 0;
  for (const child of zone.children) {
    if (child === placeholder) break;
    if (child.classList && child.classList.contains('block-row')) toIdx++;
  }
  removeBlockDropPlaceholder();

  const newType = e.dataTransfer.getData('block-type');
  if (newType) { addBlock(newType, phase, toIdx); return; }
  const draggedId = e.dataTransfer.getData('block-reorder');
  if (draggedId) moveBlockToPhase(draggedId, phase, toIdx);
}

function openBlockDropPlaceholder() {
  const placeholder = getBlockDropPlaceholder();
  requestAnimationFrame(() => placeholder.classList.add('open'));
}

function removeBlockDropPlaceholder() {
  if (blockDropPlaceholder) blockDropPlaceholder.classList.remove('open');
  if (blockDropPlaceholder && blockDropPlaceholder.parentNode) {
    blockDropPlaceholder.parentNode.removeChild(blockDropPlaceholder);
  }
}

// Cleans up on ANY drag end (dropped, cancelled, dropped outside a valid
// target) regardless of where it happened -- dragend always fires on the
// element the drag started from.
document.addEventListener('dragend', removeBlockDropPlaceholder);

// Hovering the top half of a row opens the gap above it (insert before);
// the bottom half opens it below (insert after) -- tracked via
// dataset.dropAfter so onBlockDrop's actual index math matches exactly
// where the gap was shown, not just "always before this row" like before.
function onBlockRowDragOver(e, phase, targetId) {
  e.preventDefault();
  e.stopPropagation();
  const row = e.currentTarget;
  const rect = row.getBoundingClientRect();
  const after = (e.clientY - rect.top) >= rect.height / 2;
  row.dataset.dropAfter = after ? '1' : '';
  const placeholder = getBlockDropPlaceholder();
  if (after) row.after(placeholder);
  else row.before(placeholder);
  openBlockDropPlaceholder();
}

function onCanvasDragOver(e, phase) {
  e.preventDefault();
  const zone = document.getElementById(`creation-canvas-${phase}`);
  zone.classList.add('drag-over');
  // Only claim the placeholder here when the cursor isn't over a specific
  // row -- each row's own dragover (onBlockRowDragOver) already places it
  // more precisely, and this would otherwise fight that on every bubbled
  // dragover event.
  if (e.target === zone) {
    zone.appendChild(getBlockDropPlaceholder());
    openBlockDropPlaceholder();
  }
}

function onCanvasDragLeave(e, phase) {
  const zone = document.getElementById(`creation-canvas-${phase}`);
  zone.classList.remove('drag-over');
  // relatedTarget is where the pointer moved TO -- still inside the zone
  // (e.g. onto a child row) isn't actually leaving it, just bubbling.
  if (!zone.contains(e.relatedTarget)) removeBlockDropPlaceholder();
}

function onCanvasDrop(e, phase) {
  e.preventDefault();
  document.getElementById(`creation-canvas-${phase}`).classList.remove('drag-over');
  removeBlockDropPlaceholder();
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
    addLog(`[Macro Manager] "${BLOCK_TYPES[b.type].label}" can't go in ${PHASE_LABELS[phase]}.`);
    return;
  }
  // Same-list reorder where the drop target sits AT OR AFTER the dragged
  // block's own current spot: toIdx was computed against the list as it
  // looked before the splice below removes the source, so once that
  // removal shifts everything after it back by one, toIdx has to shift
  // down by one too or the insert lands one slot too early -- in the
  // worst case (dropping just below where the block already was) that
  // puts it right back where it started, looking like the drop did
  // nothing at all despite the placeholder showing it landing correctly.
  if (loc.phase === phase && toIdx != null && toIdx > loc.idx) {
    toIdx -= 1;
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
  const dropAfter = e.currentTarget.dataset.dropAfter === '1';
  removeBlockDropPlaceholder();

  const list = creationPhases[phase];
  const newType = e.dataTransfer.getData('block-type');
  if (newType) {
    let toIdx = list.findIndex(b => b.id === targetId);
    if (toIdx !== -1 && dropAfter) toIdx += 1;
    addBlock(newType, phase, toIdx === -1 ? null : toIdx);
    return;
  }

  const draggedId = e.dataTransfer.getData('block-reorder');
  if (!draggedId || draggedId === targetId) return;
  let toIdx = list.findIndex(b => b.id === targetId);
  if (toIdx !== -1 && dropAfter) toIdx += 1;
  moveBlockToPhase(draggedId, phase, toIdx === -1 ? null : toIdx);
}

async function saveCurrentTemplate() {
  const nameInput = document.getElementById('template-name');
  const name = nameInput.value.trim();
  if (!name) return;
  // No more separate top-level "walk" config -- Walk Path is a real block
  // now, so its mode/pathName save as part of the block itself, same as
  // every other block's own fields.
  const payload = { team: creationTeam, equipment: creationEquipment };
  PHASES.forEach(phase => {
    payload[phase] = creationPhases[phase].map(b => ({
      type: b.type, params: b.params, once: b.once, kind: b.kind, value: b.value, hotkey: b.hotkey,
      mode: b.mode, pathName: b.pathName, ignoreHighlight: b.ignoreHighlight,
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
  // The unique pinned Auto Walk Path block (see renderBlockRow's
  // isPinnedWalk) -- always there, Once always on, still reorderable.
  creationPhases = { prestart: [{ id: newBlockId(), type: 'walk_path', params: {}, once: true, mode: 'auto', pathName: '' }], battle: [] };
  creationTeam = '';
  creationEquipment = 'include';
  document.getElementById('template-name').value = '';
  document.getElementById('template-select').value = '';
  creationFreshLoad = true;
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
  if (names.length === 0) { addLog('[Macro Manager] Nothing to export -- no saved templates yet.'); return; }
  const templates = {};
  for (const name of names) {
    try { templates[name] = await pywebview.api.load_template(name); } catch (e) {}
  }
  const payload = {
    kind: 'anime-expeditions-templates', version: 1, exported: new Date().toISOString(), templates,
  };
  let result = null;
  try { result = await pywebview.api.export_tasks_file(payload, 'templates'); } catch (e) {}
  if (result && result.ok) addLog(`[Macro Manager] Exported ${names.length} template(s) to ${result.path}`);
  else if (result && result.reason !== 'cancelled') addLog(`[Macro Manager] Export failed: ${result.reason || 'error'}`);
}

async function importTemplates() {
  let result = null;
  try { result = await pywebview.api.import_tasks_file(); } catch (e) {}
  if (!result || !result.ok) {
    if (result && result.reason !== 'cancelled') addLog(`[Macro Manager] Import failed: ${result.reason || 'error'}`);
    return;
  }
  const data = result.data || {};
  const templates = data.templates && typeof data.templates === 'object' ? data.templates : null;
  if (!templates) { addLog('[Macro Manager] Import failed: that file is not a template export.'); return; }
  let existing = [];
  try { existing = await pywebview.api.list_templates(); } catch (e) {}
  let added = 0;
  for (const [name, t] of Object.entries(templates)) {
    if (existing.includes(name) || !t || t.blocks == null) continue;
    try { await pywebview.api.save_template(name, t.blocks); added++; } catch (e) {}
  }
  await refreshTemplateList();
  addLog(`[Macro Manager] Imported ${added} template(s).`);
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
    // "slider" was removed as a kind -- a template saved before that still
    // carrying one migrates to Toggle/Off rather than rendering a kind the
    // picker no longer offers.
    block.kind = b.kind === 'slider' ? 'toggle' : (b.kind || 'toggle');
    block.value = b.value !== undefined && b.kind !== 'slider' ? b.value : (block.kind === 'toggle' ? 'off' : '');
  }
  if (b.type === 'place_unit') {
    block.hotkey = b.hotkey || '';
    block.ignoreHighlight = !!b.ignoreHighlight;
  }
  if (b.type === 'walk_path') {
    block.mode = b.mode === 'custom' ? 'custom' : 'auto';
    block.pathName = b.pathName || '';
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
    creationTeam = '';
    creationEquipment = 'include';

    if (Array.isArray(payload)) {
      // Oldest shape: one flat pre-phases list. Everything that still exists
      // as a block lands in Battle; pathing blocks became a Walk Path block.
      migrateLegacyBlocks(payload, []);
    } else if (payload.before || payload.during || payload.after) {
      // Three-phase shape (Before/In/After Match): Before's placements are
      // Pre Start by definition; everything else runnable goes to Battle.
      migrateLegacyBlocks([...(payload.during || []), ...(payload.after || [])], payload.before || []);
    } else {
      PHASES.forEach(phase => { creationPhases[phase] = (payload[phase] || []).map(blockFromSaved); });
      // A template saved before Walk Path became a real block kept its
      // config in this separate top-level field instead -- migrate it into
      // a synthesized block at the very top of Pre Start (where it always
      // effectively ran anyway) so the template keeps working unchanged.
      // Skipped if Pre Start already has a real walk_path block (current
      // format), so a template saved since this change never gets a
      // duplicate.
      if (payload.walk && !creationPhases.prestart.some(b => b.type === 'walk_path')) {
        creationPhases.prestart.unshift({
          id: newBlockId(), type: 'walk_path', params: {}, once: false,
          mode: payload.walk.mode === 'custom' ? 'custom' : 'auto', pathName: payload.walk.pathName || '',
        });
      }
      creationTeam = payload.team || '';
      creationEquipment = payload.equipment === 'exclude' ? 'exclude' : 'include';
    }
    // No walk_path handling needed here: renderPhases() below enforces the
    // pinned-block invariant (synthesize if missing, force Once, keep at
    // top) for every load shape.
    creationFreshLoad = true;
    renderPhases();
    document.getElementById('template-name').value = data.name || name;
  } catch (e) {}
}

// Shared by both legacy template shapes: sort old blocks into the two-phase
// model. custom_path/auto_select (their oldest form as standalone blocks)
// migrate into a real Walk Path block at the top of Pre Start instead --
// any placement from the old "before" list stays in Pre Start while
// everything else runs in Battle.
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
  const mode = (b.type === 'custom_path' && b.pathName) ? 'custom' : 'auto';
  const pathName = mode === 'custom' ? b.pathName : '';
  const existing = creationPhases.prestart.find(x => x.type === 'walk_path');
  if (existing) { existing.mode = mode; existing.pathName = pathName; }
  else creationPhases.prestart.unshift({ id: newBlockId(), type: 'walk_path', params: {}, once: false, mode, pathName });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
window.addEventListener('pywebviewready', async () => {
  // Re-assert the platform from Python, which is authoritative -- the
  // synchronous navigator sniff at the top of this file is what actually beats
  // the first paint, this just corrects it if the user agent ever lies.
  try {
    const env = await pywebview.api.get_platform();
    IS_MAC = !!(env && env.mac);
    if (IS_MAC) document.documentElement.dataset.platform = 'mac';
    else delete document.documentElement.dataset.platform;
  } catch (e) {}

  try {
    const version = await pywebview.api.get_version();
    document.getElementById('ver-badge').textContent = `v${version}`;
    const loadingVer = document.getElementById('ver-badge-loading');
    if (loadingVer) loadingVer.textContent = `v${version}`;
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
