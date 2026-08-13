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
  if (e.key === 'Escape') {
    const faqModal = document.getElementById('faq-modal');
    if (faqModal && faqModal.style.display !== 'none') {
      closeFaqModal();
      return;
    }
    const detectTestModal = document.getElementById('detect-test-modal');
    if (detectTestModal && detectTestModal.style.display !== 'none') {
      closeDetectTest();
    }
  }
});

// ---------------------------------------------------------------------------
// Help & FAQ Modal
// ---------------------------------------------------------------------------
function openFaqModal() {
  const el = document.getElementById('faq-modal');
  if (el) {
    el.style.display = 'flex';
    try { window.pywebview && pywebview.api.hide_game(); } catch (e) {}
  }
}

function closeFaqModal() {
  const el = document.getElementById('faq-modal');
  if (el) {
    el.style.display = 'none';
    restoreGameIfDashboard();
  }
}

function toggleFaqModal() {
  const el = document.getElementById('faq-modal');
  if (!el) return;
  if (el.style.display === 'none' || !el.style.display) {
    openFaqModal();
  } else {
    closeFaqModal();
  }
}

function toggleFaqItem(btn) {
  const item = btn.closest('.faq-item');
  if (!item) return;
  const isOpen = item.classList.contains('open');
  document.querySelectorAll('.faq-item.open').forEach(el => {
    if (el !== item) el.classList.remove('open');
  });
  if (isOpen) {
    item.classList.remove('open');
  } else {
    item.classList.add('open');
  }
}

function filterFaqItems() {
  const query = (document.getElementById('faq-search-input')?.value || '').toLowerCase().trim();
  const items = document.querySelectorAll('.faq-item');
  items.forEach(item => {
    const text = item.textContent.toLowerCase();
    const keywords = (item.getAttribute('data-keywords') || '').toLowerCase();
    if (!query || text.includes(query) || keywords.includes(query)) {
      item.style.display = 'block';
    } else {
      item.style.display = 'none';
    }
  });
}

// ---------------------------------------------------------------------------
// HTML Sanitization
// ---------------------------------------------------------------------------
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ---------------------------------------------------------------------------
// Logs
// ---------------------------------------------------------------------------
// Line rendering, the line cap, and the scroll-follow behaviour all live in
// ui/log_view.js -- loaded before this file and shared with the popped-out log
// window (ui/logs_window.html), which is a separate document showing the same
// stream. Only what the DASHBOARD does beyond displaying lines stays here.

// Clears this window's view and asks Python to drop its history buffer and
// clear any other open log window (e.g. a popped-out one), so "Clear" doesn't
// leave a stale copy sitting in a second window.
function clearLogs() {
  clearLogView();
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
// Compact strip (F7) state -- declared up here (not beside toggleCompactStrip)
// because switchScreen above reads it.
let compactMode = false;

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
  // Docking makes the native child window visible regardless of what the
  // DOM shows -- if a blocking modal (welcome, update, scale warning) is
  // up at this exact moment, the freshly docked game would paint straight
  // over it (seen live: the first-run welcome unreachable behind the
  // game). Hide it; the modal's own close handler restores it.
  if (isBlockingOverlayOpen()) {
    try { window.pywebview && pywebview.api.hide_game(); } catch (e) {}
  }
  // Last: the window is now at its docked size, so a queued welcome opens
  // into something readable. showOnboarding hides the game itself.
  runPendingFirstRun();

  // macOS: docking (re)arranges the panel back to the narrow strip beside
  // Roblox. If the user was on a non-Dashboard screen when Roblox appeared
  // (or reappeared after a relaunch), that strip leaves the multi-column
  // editor clipped. Re-assert the width the current screen needs -- the
  // first-ever dock just switched to Dashboard above, which keeps the strip.
  if (IS_MAC) {
    try { window.pywebview && pywebview.api.set_panel_expanded(currentScreen !== 'dashboard'); } catch (e) {}
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
  if (['update-modal', 'scale-warning-modal', 'onboarding-modal', 'subscribe-modal', 'faq-modal', 'share-code-modal'].some(isOpen)) return true;
  if (!captureDanceActive && ['im-modal', 'pu-modal', 'path-name-modal', 'fuel-paths-modal'].some(isOpen)) return true;
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
  const badgeText = document.getElementById('ver-badge-text');
  const badgeIcon = document.querySelector('#ver-badge .ver-badge-icon');
  const settingsBtn = document.getElementById('btn-check-updates');

  const badge = document.getElementById('ver-badge');
  const original = badgeText ? badgeText.textContent : (badge ? badge.textContent : '');

  if (badgeText) badgeText.textContent = 'Checking...';
  else if (badge) badge.textContent = 'Checking...';

  if (badgeIcon) badgeIcon.classList.add('spinning');
  if (settingsBtn) {
    settingsBtn.disabled = true;
    settingsBtn.textContent = 'Checking...';
  }

  const resetState = () => {
    if (badgeText) badgeText.textContent = original;
    else if (badge) badge.textContent = original;
    if (badgeIcon) badgeIcon.classList.remove('spinning');
    if (settingsBtn) {
      settingsBtn.disabled = false;
      settingsBtn.innerHTML = `<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg> Check for Updates`;
    }
  };

  try {
    await pywebview.api.check_for_updates();
    // check_for_updates fires the background check and returns immediately
    // -- give it a moment to actually land before asking for the result.
    setTimeout(async () => {
      resetState();
      const info = await pywebview.api.get_update_info();
      if (info && info.available) {
        showUpdateAvailable();
      } else {
        addLog && addLog("[Update] You're up to date.");
      }
    }, 2500);
  } catch (e) {
    resetState();
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

// One-time dialogs (welcome, subscribe) wait for the window to reach its
// real size before opening. That happens when Roblox docks, or when the
// user skips waiting for it -- either way the layout is up and the window
// is no longer the small corner box the waiting screen uses.
//
// If neither ever happens the dialog simply doesn't open this session and
// is still queued next launch, which is the right outcome: a welcome nobody
// can read is worse than one shown a run later.
let pendingFirstRun = null;

function runPendingFirstRun() {
  const what = pendingFirstRun;
  pendingFirstRun = null;
  if (what === 'onboarding') showOnboarding();
  else if (what === 'subscribe') showSubscribePrompt();
}

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
  runPendingFirstRun();
}

function launchRoblox() {
  try { window.pywebview && pywebview.api.launch_roblox(); } catch (e) {}
}


// ---------------------------------------------------------------------------
// Screen switching (Dashboard / Macro Manager / Settings)
// ---------------------------------------------------------------------------
// Switching away from Dashboard hides the docked Roblox window entirely (it's
// a native child window, not DOM content, so CSS alone can't hide it) so the
// other screens get the full window instead of Roblox showing through.
let currentScreen = 'dashboard';
let lastNonDashboardScreen = 'creation';
const SCREENS = ['dashboard', 'task', 'creation', 'resource', 'settings'];

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

// High-DPI fit (issue #11) -------------------------------------------------
// The Windows layout reserves a fixed 1152px game hole (#game-slot) plus a
// 400px control panel -- together GUI_WIDTH_FULL -- and those numbers are
// PHYSICAL pixels, because the docked native Roblox window and every fixed
// macro click coordinate live in physical pixels (the DPI-aware process
// sizes the window in physical px too). At >100% Windows display scaling the
// WebView still renders CSS pixels LARGER than physical ones
// (devicePixelRatio > 1), so this fixed-px layout overflows the physical
// window: the right panel is clipped off the edge and the game slot no
// longer lines up with the docked game (exactly what issue #11 shows).
// Counter-scaling the whole document by 1/dpr makes 1 CSS px == 1 physical
// px again, so the layout occupies precisely the pixels the window was sized
// in and everything fits and aligns -- the same physical-pixel space the
// rest of the app already assumes. A no-op at 100% scale (dpr 1), so setups
// that already work are untouched. Windows-only: the mac side-by-side layout
// works in logical points and must not be zoomed.
function applyDpiFit() {
  if (IS_MAC) { document.documentElement.style.zoom = ''; return; }
  const dpr = window.devicePixelRatio || 1;
  document.documentElement.style.zoom = dpr > 1.001 ? String(1 / dpr) : '';
}
applyDpiFit();
// devicePixelRatio changes (dragging the window to a monitor at a different
// scale) surface as a resize here -- re-fit so it doesn't clip again.
window.addEventListener('resize', applyDpiFit);

// Previous poll's macro-running state, so refreshStatus can act on the
// running -> stopped EDGE rather than every tick (see refreshStatus).
let wasMacroRunning = false;

function switchScreen(name) {
  // Navigating anywhere off the Dashboard drops out of the compact strip --
  // the strip is a Dashboard overlay, so leaving the Dashboard should restore
  // the full UI (and the full window size) rather than leave the strip
  // stranded over a trimmed window.
  if (compactMode && name !== 'dashboard') {
    compactMode = false;
    document.body.classList.remove('compact-mode');
    try { pywebview.api.exit_compact(); } catch (e) {}
  }
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

  if (name === 'creation') { refreshTemplateList(); refreshSavedPaths(); refreshSavedRecordings(); }
  if (name === 'task') refreshTaskQueue();
  if (name === 'resource') {
    refreshCraftingScreen();
    refreshFuelScreen();
    refreshAutoShopScreen();
    refreshChallengeScreen();
    refreshBountyScreen();
  }
  if (name === 'settings') { refreshSavedPaths(); loadMacroCoords(); loadRewardTestMaps(); }

  // The Process Log only exists on the Dashboard, and a display:none element
  // has no scroll height -- so while another screen is up the list cannot
  // track where it was, and lines arriving off-screen must not pile up as
  // "unread". Snap to the newest line (and reset that counter) on the way in;
  // from there normal follow/pin behaviour takes over. See ui/log_view.js.
  if (name === 'dashboard') logSnapToLatest();
}

// Bound to the "Toggle Game Visibility" hotkey (default F4) from Python.
// Routed through here (not a raw show/hide toggle) so it reuses switchScreen's
// own hide/show coordination instead of fighting it as a second source of truth.
function toggleGameScreenHotkey() {
  switchScreen(currentScreen === 'dashboard' ? lastNonDashboardScreen : 'dashboard');
}

// ---------------------------------------------------------------------------
// Compact strip (F7): hide the busy side panel + process log and show a slim
// control bar at the bottom, leaving the docked game exactly where it is
// (visible and clickable -- nothing about the window size or docking changes,
// so the macro keeps clicking Roblox normally). Pure DOM: body.compact-mode
// does all the hiding via CSS. (compactMode is declared near the top so the
// earlier switchScreen can read it.)
// ---------------------------------------------------------------------------
function toggleCompactStrip() {
  if (!compactMode) {
    // The strip only makes sense over the game, which lives on the Dashboard
    // -- make sure we're there (also un-hides the game if we were elsewhere).
    switchScreen('dashboard');
    compactMode = true;
    document.body.classList.add('compact-mode');
    // Trim the window to just the game + strip (drops the empty side column
    // and the log gap). Pure size change -- the game stays docked/clickable.
    try { pywebview.api.enter_compact(); } catch (e) {}
  } else {
    compactMode = false;
    document.body.classList.remove('compact-mode');
    try { pywebview.api.exit_compact(); } catch (e) {}
  }
}

// ---------------------------------------------------------------------------
// Status Polling & UI Synchronization
// ---------------------------------------------------------------------------
// Fetches live status dict from the backend every 1.5 seconds and updates all
// Status Readout DOM elements. When the macro enters Idle/Stopped state, the
// backend returns reset '-' placeholders for task/map/repeat fields.
async function refreshStatus() {
  if (!window.pywebview) return;
  try {
    const status = await pywebview.api.get_status();
    if (status.docked) {
      showDocked();
    } else {
      showWaiting();
    }
    // Synchronize live readout fields (Action, Task, Repeat, Map, Mode, etc.)
    document.getElementById('stat-current-task').textContent = status.current_task ?? '-';
    document.getElementById('stat-current-repeat').textContent = status.current_repeat ?? '-';
    document.getElementById('stat-map').textContent = status.map ?? '-';
    document.getElementById('stat-action').textContent = status.action ?? '-';
    const csAction = document.getElementById('compact-action');
    if (csAction) csAction.textContent = status.action ?? 'Idle';
    document.getElementById('stat-last-run').textContent = status.last_run ?? '-';
    const runsPerHour = status.runs_per_hour ?? '-';
    const rphElem = document.getElementById('stat-runs-per-hour');
    if (rphElem) rphElem.textContent = runsPerHour;
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
  if (stopBtn) {
    stopBtn.disabled = !running;
    stopBtn.classList.toggle('ui-running-glow', running && !paused);
  }
  if (pauseBtn) {
    pauseBtn.disabled = !running;
    pauseBtn.classList.toggle('on', !!paused);
    const label = document.getElementById('btn-macro-pause-label');
    if (label) label.textContent = paused ? 'Resume' : 'Pause';
  }
  // Mirror the same state onto the compact strip's buttons.
  const csStart = document.getElementById('cs-start');
  const csPause = document.getElementById('cs-pause');
  const csStop = document.getElementById('cs-stop');
  const csDot = document.getElementById('cs-dot');
  if (csStart) csStart.disabled = running;
  if (csStop) csStop.disabled = !running;
  if (csPause) {
    csPause.disabled = !running;
    csPause.classList.toggle('on', !!paused);
    csPause.setAttribute('data-tooltip', paused ? 'Resume' : 'Pause');
  }
  if (csDot) csDot.className = 'cs-dot' + (running ? (paused ? ' paused' : ' running') : '');
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
  // Clear search when switching categories
  const searchInput = document.getElementById('settings-search');
  if (searchInput && searchInput.value) { searchInput.value = ''; filterSettings(''); }
}

// Settings search: filters visible setting-rows by matching their text
// content (label + description) against the query. Some panels also own
// controls/descriptions outside a .setting-row (Webhook, Reward Reader and
// Game Stats), so those panel-owned nodes are searched too. Automatically
// switches to the "All" view so results from every category show. Empty query
// restores all rows.
function filterSettings(query) {
  const q = (query || '').trim().toLowerCase();
  // Switch to "All" when searching so every category is visible
  if (q) {
    document.querySelectorAll('.settings-cat-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.cat === 'all'));
    document.querySelectorAll('.settings-category').forEach(sec => { sec.style.display = 'block'; });
  }
  // Process each category section
  document.querySelectorAll('.settings-category').forEach(catSection => {
    let catHasHit = false;
    catSection.querySelectorAll('.rp-panel').forEach(panel => {
      let panelHasHit = false;
      // Search content owned directly by the panel without counting row text
      // twice. A panel-level match keeps its controls visible, just like a
      // header match; otherwise a query such as "Webhook URL" would hide the
      // entire panel because that field is not wrapped in .setting-row.
      const contentCopy = panel.cloneNode(true);
      contentCopy.querySelector('.rp-panel-head')?.remove();
      contentCopy.querySelectorAll('.setting-row').forEach(row => row.remove());
      const panelContentText = (contentCopy.textContent || '').toLowerCase();
      const panelContentHit = !!q && panelContentText.includes(q);
      // A search for a panel's own title (for example "Macro Coordinates")
      // should show the panel's controls, not leave only its header and
      // description visible. The old code kept the panel because the header
      // matched, but had already hidden every row because none of the row
      // labels matched the same query.
      const headerText = (panel.querySelector('.rp-panel-head')?.textContent || '').toLowerCase();
      const headerHit = !!q && headerText.includes(q);
      panel.querySelectorAll('.setting-row').forEach(row => {
        const text = (row.textContent || '').toLowerCase();
        const hit = !q || headerHit || panelContentHit || text.includes(q);
        row.classList.toggle('search-hidden', !hit);
        row.classList.toggle('search-hit', hit && !!q && !headerHit && !panelContentHit);
        if (hit) panelHasHit = true;
      });
      // Also check the panel header text (e.g. "Webhook", "General")
      if (headerHit) panelHasHit = true;
      if (panelContentHit) panelHasHit = true;
      panel.classList.toggle('search-hidden', !panelHasHit && !!q);
      if (panelHasHit) catHasHit = true;
    });
    catSection.classList.toggle('search-hidden', !catHasHit && !!q);
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

// Settings > General > Roblox controls: the runner samples this only at
// completed-match boundaries. Keeping the interval here (rather than in a
// task) makes it apply consistently across every queued task and repeat pass.
async function saveMemoryRefreshHours(input) {
  const hours = Math.min(12, Math.max(1, parseFloat(input.value) || 4));
  input.value = hours;
  try {
    await pywebview.api.set_setting('memory_refresh_hours', hours);
    addLog(`[Settings] Periodic Roblox refresh set to ${hours} hour${hours === 1 ? '' : 's'}.`);
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
  image_manager: 'f6', toggle_compact: 'f7', game_auto_upgrade: '',
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
    updateKeybindDisplay('image_manager', hk.image_manager || '');
    updateKeybindDisplay('toggle_compact', hk.toggle_compact || '');
    updateKeybindDisplay('game_auto_upgrade', hk.game_auto_upgrade || '');
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
  space:   { label: 'Space', bg: '#0b0d19', border: '#1e2640' },
  gold:    { label: 'Semi Gold', bg: '#2f2a20', border: '#524837' },
  silver:  { label: 'Semi Silver', bg: '#262b34', border: '#434c5b' },
  // Frosted panels over an animated in-app aurora -- see #glass-aurora in style.css.
  glass:   { label: 'Liquid Glass', bg: 'linear-gradient(135deg, rgba(110,166,255,0.45), rgba(181,140,224,0.45))', border: 'rgba(255,255,255,0.25)' },
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
  if (activeThemeBase === 'glass') ensureLiquidLensMap();
  if (announce) addLog(`[Theme] Background: ${THEME_BASES[activeThemeBase].label}`);
}

// ---- Liquid Glass lens ----------------------------------------------------
// What separates liquid glass from plain glassmorphism is REFRACTION: panel
// rims bend the content behind them like a lens. The panels' backdrop-filter
// references the SVG filter in index.html (#glass-lens), whose
// feDisplacementMap reads per-pixel bend vectors from the map generated
// here: a rounded-rect edge band where displacement ramps up toward the rim
// along the edge normal (R = x-bend, G = y-bend, 128 = neutral -- the
// feDisplacementMap convention). Generated once, on the first switch into
// the glass theme.
let liquidLensMapReady = false;

function ensureLiquidLensMap() {
  if (liquidLensMapReady) return;
  const target = document.getElementById('glass-lens-map');
  if (!target) return;
  const W = 400, H = 300, CORNER = 24, RIM = 46;

  // Signed distance to a rounded rectangle centered in the canvas
  // (negative inside) -- the standard 2D SDF.
  const hw = W / 2 - 1, hh = H / 2 - 1;
  function sdf(px, py) {
    const qx = Math.abs(px) - (hw - CORNER);
    const qy = Math.abs(py) - (hh - CORNER);
    const ox = Math.max(qx, 0), oy = Math.max(qy, 0);
    return Math.hypot(ox, oy) + Math.min(Math.max(qx, qy), 0) - CORNER;
  }

  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(W, H);
  const d = img.data;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const px = x + 0.5 - W / 2, py = y + 0.5 - H / 2;
      const inside = -sdf(px, py);           // distance in from the edge
      let dx = 0, dy = 0;
      if (inside >= 0 && inside < RIM) {
        const t = 1 - inside / RIM;          // 0 interior -> 1 at edge
        const k = Math.pow(t, 2.2);          // lens curve: gentle, then steep
        // Edge normal via numeric SDF gradient; bend OUTWARD so the rim
        // magnifies what's behind it, the way thick glass edges do.
        const e = 0.75;
        const gx = sdf(px + e, py) - sdf(px - e, py);
        const gy = sdf(px, py + e) - sdf(px, py - e);
        const len = Math.hypot(gx, gy) || 1;
        dx = (gx / len) * k;
        dy = (gy / len) * k;
      }
      const i = (y * W + x) * 4;
      d[i] = Math.round(128 + dx * 127);
      d[i + 1] = Math.round(128 + dy * 127);
      d[i + 2] = 128;
      d[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  target.setAttribute('href', canvas.toDataURL('image/png'));
  liquidLensMapReady = true;
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
              onclick="setThemeBase('${name}')" title="${t.label}"></button>
    `).join('');
  }
  const accentEl = document.getElementById('theme-accent-picker');
  if (accentEl) {
    accentEl.innerHTML = Object.entries(THEME_ACCENTS).map(([name, color]) => `
      <button class="theme-swatch ${name === activeThemeAccent ? 'active' : ''}" style="--sw: ${color};"
              onclick="setThemeAccent('${name}')" title="${name[0].toUpperCase() + name.slice(1)}"></button>
    `).join('');
  }
}

async function loadSettingsUI() {
  try {
    const s = await pywebview.api.get_settings();
    document.getElementById('toggle-start-minimized').classList.toggle('on', !!s.start_minimized);
    const autoRelaunchEl = document.getElementById('toggle-auto-relaunch-roblox');
    // Default ON -- absent key means enabled.
    if (autoRelaunchEl) autoRelaunchEl.classList.toggle('on', s.auto_relaunch_roblox !== false);
    const memoryRefreshEl = document.getElementById('toggle-memory-refresh');
    if (memoryRefreshEl) memoryRefreshEl.classList.toggle('on', !!s.memory_refresh_enabled);
    const memoryRefreshHoursEl = document.getElementById('setting-memory-refresh-hours');
    if (memoryRefreshHoursEl) memoryRefreshHoursEl.value = s.memory_refresh_hours ?? 4;
    const actionDelayEl = document.getElementById('setting-action-delay');
    if (actionDelayEl) actionDelayEl.value = s.action_delay_ms || 0;
    const debugScreenshotsEl = document.getElementById('toggle-debug-screenshots');
    if (debugScreenshotsEl) debugScreenshotsEl.classList.toggle('on', !!s.debug_screenshots);
    const looseTeamOcrEl = document.getElementById('toggle-loose-team-ocr-match');
    if (looseTeamOcrEl) looseTeamOcrEl.classList.toggle('on', !!s.loose_team_ocr_match);
    const expColorEl = document.getElementById('toggle-expedition-color');
    // Default ON -- the key is simply absent until the user first flips it.
    if (expColorEl) expColorEl.classList.toggle('on', s.expedition_color_buttons !== false);
    const expOEl = document.getElementById('setting-expedition-o-ms');
    if (expOEl) expOEl.value = s.expedition_camera_o_ms ?? 100;
    const flickerEl = document.getElementById('toggle-flicker-free');
    if (flickerEl) {
      // Default on -- absent key means enabled.
      flickerEl.classList.toggle('on', s.flicker_free_capture !== false);
      if (IS_MAC) flickerEl.closest('.setting-row').style.display = 'none';
    }
    const cutoutEl = document.getElementById('toggle-game-cutout');
    if (cutoutEl) {
      cutoutEl.classList.toggle('on', !!s.game_cutout);
      // Windows-only technique -- hide the whole row on mac rather than
      // offering a switch that can't do anything there.
      if (IS_MAC) cutoutEl.closest('.setting-row').style.display = 'none';
    }
    const wgcEl = document.getElementById('toggle-wgc-capture');
    if (wgcEl) {
      wgcEl.classList.toggle('on', !!s.use_wgc_capture);
      // Windows-only (Windows.Graphics.Capture) -- hide on mac.
      if (IS_MAC) wgcEl.closest('.setting-row').style.display = 'none';
    }
    // Onboarding first (once), then the subscribe prompt (once) -- if both
    // are pending on a fresh install, the subscribe prompt waits until
    // onboarding is dismissed rather than stacking on top of it.
    ingameConfirmed = (s.ingame_confirmed && typeof s.ingame_confirmed === 'object')
      ? s.ingame_confirmed : {};
    renderAllIngameChecklists();
    renderMachineChecklist('onboarding-machine');
    // Queued, not shown. Before Roblox docks the window is still its small
    // waiting-screen size in a corner of the display, and a first-run
    // dialog opening into that is a tiny unreadable box -- which is exactly
    // what it did. See runPendingFirstRun.
    if (!s.onboarding_done) pendingFirstRun = 'onboarding';
    else if (!s.subscribe_prompted) pendingFirstRun = 'subscribe';
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
    updateKeybindDisplay('toggle_compact', hk.toggle_compact || '');
    updateKeybindDisplay('game_auto_upgrade', hk.game_auto_upgrade || '');
    // (There was an updateDashboardHotkeys(hk) call here. No such function has
    // ever existed in this file, so every load of Settings threw a
    // ReferenceError that this bare catch swallowed. Nothing broke visibly
    // because updateKeybindDisplay already syncs the Dashboard's Start/Stop/
    // Pause key badges -- see the dashboardKeyEl lookup inside it -- but the
    // catch meant anything appended after that line would also have silently
    // never run.)
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
    ? names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('')
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
    ? windows.map(w => `<option value="${escapeHtml(w.hwnd)}">${escapeHtml(w.title || 'Roblox')} (pid ${escapeHtml(w.pid)})</option>`).join('')
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
    sel.innerHTML = '<option value="">Map (optional)</option>' + maps.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
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
// Settings > Debug > "Wave Monitor" -- opens the always-on-top pop-out that
// polls read_current_wave (see main.py's pop_out_wave_monitor).
async function openWaveMonitor(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Opening...';
  try {
    await pywebview.api.pop_out_wave_monitor();
    btn.textContent = 'Opened';
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1400);
}

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

// First-run welcome (see #onboarding-modal): shown once per install, rows
// filtered to the current platform. "Get Started" persists the flag so it
// never shows again; the Health Check button inside it reuses the normal
// Settings > Debug handler.
// ============ In-game Roblox settings ============
// The only requirements nothing here can verify: they live inside Roblox,
// and Health Check cannot see into the game. Rendered into BOTH the
// first-run welcome and Settings > Debug from this one array -- two
// hand-maintained copies of the same list drift apart, one source cannot.
//
// `why` is not decoration. A checklist people understand is one they
// follow, and the sprint line is the load-bearing example: the built-in
// walk paths in Paths/defaults are bare key timings with no sprint flag
// (core/paths.py replay_events only holds Shift when a block asks), so they
// were recorded at sprint speed and only reach their spot at sprint speed.
const INGAME_REQUIREMENTS = [
  { key: 'ui_scale', name: 'UI Scale', value: '1',
    why: 'Every reference image was captured at 1. At any other scale the macro is hunting for buttons that are the wrong size.' },
  { key: 'auto_sprint', name: 'Auto Sprint', value: 'On',
    why: 'The built-in walk paths are timed for sprint speed. With this off your character stops short, and units place in the wrong spot or not at all.' },
  { key: 'match_rewards', name: 'Show Match and Rewards', value: 'Off',
    why: 'It covers the part of the screen the macro reads after a match.' },
  { key: 'auto_vote', name: 'Auto Vote Start', value: 'Off',
    why: 'The macro votes at the right moment itself. Left on, rounds start before Pre Start has run.' },
];

// Ticked boxes persist (settings.ingame_confirmed), so Settings > Debug
// shows what you already confirmed and a restart or an update does not wipe
// it -- settings.json is excluded from both update paths.
let ingameConfirmed = {};

function renderIngameChecklist(containerId) {
  const host = document.getElementById(containerId);
  if (!host) return;
  const done = INGAME_REQUIREMENTS.filter(r => ingameConfirmed[r.key]).length;
  host.innerHTML =
    `<div class="onb-ingame">
       <div class="onb-ingame-head">
         <span>Set these in Roblox <span class="onb-ingame-sub">only you can &mdash; the macro can't see into the game</span></span>
         <span class="onb-ingame-count ${done === INGAME_REQUIREMENTS.length ? 'all' : ''}">${done}/${INGAME_REQUIREMENTS.length}</span>
       </div>
       <ul class="onb-ingame-list">` +
    INGAME_REQUIREMENTS.map(r => `
         <li class="${ingameConfirmed[r.key] ? 'checked' : ''}">
           <label>
             <input type="checkbox" ${ingameConfirmed[r.key] ? 'checked' : ''}
                    onchange="toggleIngameCheck('${r.key}')">
             <span class="onb-ingame-box" aria-hidden="true"></span>
             <span class="onb-ingame-text">
               <span class="onb-ingame-name">${escapeHtml(r.name)}<b>${escapeHtml(r.value)}</b></span>
               <span class="onb-ingame-why">${escapeHtml(r.why)}</span>
             </span>
           </label>
         </li>`).join('') +
    `</ul>
     </div>`;
}

function renderAllIngameChecklists() {
  renderIngameChecklist('onboarding-ingame');
  renderIngameChecklist('debug-ingame');
}

// Ticking is for the reader's benefit, never a gate -- Get Started stays
// enabled the whole time. Both copies re-render so the count agrees
// wherever it is on screen.
async function toggleIngameCheck(key) {
  ingameConfirmed = { ...ingameConfirmed, [key]: !ingameConfirmed[key] };
  renderAllIngameChecklists();
  try { await pywebview.api.set_setting('ingame_confirmed', ingameConfirmed); } catch (e) {}
}

// ============ On this computer ============
// Deliberately NOT tick-your-own-box like the Roblox list above. Health
// Check already verifies most of these (main.Api.run_health_check), and a
// checkbox someone ticks for "display scale is 100%" while it is actually
// 175% is worse than no checkbox at all -- that exact setting is the single
// most common cause of clicks landing slightly wrong.
//
// `check` is the run_health_check name this row reflects; rows without one
// are things nothing can measure and stay informational. `plat` mirrors the
// old data-plat attributes so each OS only sees its own.
const MACHINE_REQUIREMENTS = [
  { plat: 'win', check: 'Display scale', name: 'Windows display scale at 100%',
    why: 'Settings > Display. Any other scale shifts every click.' },
  { plat: 'win', check: 'Elevation matches Roblox', name: 'Same elevation as Roblox',
    why: "Don't run one as Administrator without the other -- Windows silently drops clicks upward." },
  { plat: 'mac', check: 'Simulated input moves the cursor', name: 'Accessibility and Input Monitoring granted',
    why: 'System Settings > Privacy & Security, then restart the app. Without them clicks do nothing.' },
  { plat: 'mac', check: 'Screen capture returns pixels', name: 'Screen Recording granted',
    why: 'Without it every capture comes back black.' },
  { plat: 'mac', check: null, name: 'Room for side-by-side',
    why: 'Needs ~1564 logical points of width -- pick a "More Space" scaled resolution on small MacBooks.' },
  { plat: null, check: 'Critical reference images present', name: 'Assets folder next to the app',
    why: 'It holds every reference image the macro searches for.' },
  { plat: null, check: 'Text reading (OCR)', name: 'Text reading (optional)',
    why: 'Only used for stats and reward reading. Install Tesseract later from Settings > General if you want those.' },
];

// Last run_health_check result, so the rows can show a real verdict instead
// of a box someone ticked. Null until Health Check has actually run.
let lastHealthChecks = null;

function machineCheckState(row) {
  if (!row.check || !lastHealthChecks) return null;
  const hit = lastHealthChecks.find(c => c.name === row.check);
  return hit ? { ok: hit.ok, detail: hit.detail || '' } : null;
}

function renderMachineChecklist(containerId) {
  const host = document.getElementById(containerId);
  if (!host) return;
  const rows = MACHINE_REQUIREMENTS.filter(r => !r.plat || (r.plat === 'mac') === IS_MAC);
  const rated = rows.map(r => machineCheckState(r)).filter(Boolean);
  const passed = rated.filter(s => s.ok).length;
  const badge = lastHealthChecks
    ? `<span class="onb-ingame-count ${passed === rated.length ? 'all' : 'bad'}">${passed}/${rated.length}</span>`
    : `<span class="onb-ingame-count">not checked</span>`;
  host.innerHTML =
    `<div class="onb-ingame onb-machine">
       <div class="onb-ingame-head">
         <span>On this computer <span class="onb-ingame-sub">Health Check verifies these for you</span></span>
         ${badge}
       </div>
       <ul class="onb-ingame-list">` +
    rows.map(r => {
      const st = machineCheckState(r);
      const cls = st ? (st.ok ? 'pass' : 'fail') : (r.check ? 'unknown' : 'info');
      const detail = st && st.detail ? ` <span class="onb-machine-detail">&mdash; ${escapeHtml(st.detail)}</span>` : '';
      return `
         <li class="${cls}">
           <span class="onb-machine-mark" aria-hidden="true"></span>
           <span class="onb-ingame-text">
             <span class="onb-ingame-name">${escapeHtml(r.name)}</span>
             <span class="onb-ingame-why">${escapeHtml(r.why)}${detail}</span>
           </span>
         </li>`;
    }).join('') +
    `</ul>
     </div>`;
}

function showOnboarding() {
  renderIngameChecklist('onboarding-ingame');
  renderMachineChecklist('onboarding-machine');
  document.querySelectorAll('#onboarding-modal [data-plat]').forEach(el => {
    const plat = el.getAttribute('data-plat');
    if ((plat === 'mac') !== IS_MAC) el.style.display = 'none';
  });
  document.getElementById('onboarding-modal').style.display = 'flex';
  // Same as the update/scale modals: the docked game is a native child
  // window that paints over ALL DOM, this modal included -- hide it while
  // the welcome is up (no-op when nothing's docked yet; the dock-time
  // guard in showDocked covers Roblox arriving mid-modal).
  try { window.pywebview && pywebview.api.hide_game(); } catch (e) {}
}

async function closeOnboarding() {
  document.getElementById('onboarding-modal').style.display = 'none';
  try { await pywebview.api.set_setting('onboarding_done', true); } catch (e) {}
  restoreGameIfDashboard();
  // Chain the one-time subscribe prompt after onboarding on a fresh install.
  try {
    const s = await pywebview.api.get_settings();
    if (!s.subscribe_prompted) { showSubscribePrompt(); return; }
  } catch (e) {}
}

// One-time subscribe prompt (see #subscribe-modal). Shown once per install;
// dismissing it EITHER way sets the flag so it never returns. Same game-hide
// dance as the other startup modals (the docked game paints over DOM).
function showSubscribePrompt() {
  document.getElementById('subscribe-modal').style.display = 'flex';
  try { window.pywebview && pywebview.api.hide_game(); } catch (e) {}
}

async function closeSubscribePrompt() {
  document.getElementById('subscribe-modal').style.display = 'none';
  try { await pywebview.api.set_setting('subscribe_prompted', true); } catch (e) {}
  restoreGameIfDashboard();
}

async function subscribeAndClose() {
  try { await pywebview.api.open_youtube_channel(); } catch (e) {}
  await closeSubscribePrompt();
}

// Settings > Debug > "Health Check" -- backend runs every environment probe;
// full results render right under the button (the Process Log only shows on
// the Dashboard, so a bare "Issues found" verdict here explained nothing).
// The onboarding modal's copy of the button has no results div nearby -- its
// callers get the log copy the backend writes either way.
async function runHealthCheck(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Checking...';
  const out = document.getElementById('health-results');
  try {
    const result = await pywebview.api.run_health_check();
    btn.textContent = result.ok ? 'All good' : 'Issues found';
    if (out) {
      out.innerHTML = (result.checks || []).map(c => {
        const mark = c.ok ? '<span style="color: var(--teal);">&#10003;</span>' : '<span style="color: var(--rose);">&#10007;</span>';
        const detail = c.detail ? ` <span style="color: var(--text-muted);">-- ${escapeHtml(c.detail)}</span>` : '';
        // A check can carry a fix-it action the backend named -- render it
        // as a real button so the fix is one click, not a URL to retype.
        const action = c.action === 'open_releases'
          ? ` <button type="button" class="block-mod-btn" style="margin-left: 6px;" onclick="pywebview.api.open_releases_page()">Open latest release</button>`
          : '';
        return `<div>${mark} <span style="color: var(--text-dim);">${escapeHtml(c.name)}</span>${detail}${action}</div>`;
      }).join('');
      out.style.display = '';
    }
    lastHealthChecks = result.checks || [];
    renderMachineChecklist('onboarding-machine');
  } catch (e) {
    btn.textContent = 'Failed';
    if (out) { out.textContent = `Health check crashed: ${e}`; out.style.display = ''; }
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2600);
}

// Settings > Debug > "Export Failure Report" -- one zip with everything a
// bug report needs (screenshots, log tail, redacted settings, health check).
async function exportFailureReport(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Bundling...';
  try {
    const result = await pywebview.api.export_failure_report();
    btn.textContent = result.ok ? 'Saved' : (result.reason === 'cancelled' ? original : 'Failed');
    if (result.ok) addLog(`[Debug] Failure report ready to share: ${result.path}`);
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2600);
}

// Settings > Debug > "Expedition Camera Zoom" -- how long O is held during
// Expedition's Pre Start camera setup. Saved through the generic
// set_setting; the runner reads it at the next Start.
// Settings > Debug > Story Map Search. Clamped in JS, like every other
// numeric setting here (saveActionDelay, saveExpeditionOZoom) -- the min/max
// attributes on a number input do NOT constrain a typed or pasted value.
// They mark it :invalid and set validity.rangeOverflow, but .value still
// reads whatever was typed and nothing in this app calls checkValidity().
//
// It matters because the consumer only bounds these from below:
// stage_select.find_and_click_map does max(1, scroll_power) and
// max(0, scroll_nudges), then loops `for nudge in range(scroll_nudges + 1)`
// across MAX_PASSES with an image search each time. A typo'd 9999 turns a
// map lookup into roughly fifteen minutes of a run that just looks hung.
async function saveStoryScrollPower(el) {
  const n = Math.max(1, Math.min(10, parseInt(el.value, 10) || 1));
  el.value = n;
  try { await pywebview.api.set_setting('story_scroll_power', n); } catch (e) {}
}

async function saveStoryScrollNudges(el) {
  const n = Math.max(1, Math.min(30, parseInt(el.value, 10) || 1));
  el.value = n;
  try { await pywebview.api.set_setting('story_scroll_nudges', n); } catch (e) {}
}

async function saveExpeditionOZoom(el) {
  const ms = Math.max(0, Math.min(3000, parseInt(el.value, 10) || 0));
  el.value = ms;
  try { await pywebview.api.set_setting('expedition_camera_o_ms', ms); } catch (e) {}
  addLog(`[Settings] Expedition camera zoom hold set to ${ms}ms.`);
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
  const btn = tesseractInstallBtn || document.getElementById('btn-install-tesseract');
  if (!btn) return;
  if (success) {
    btn.textContent = 'Installed';
    btn.disabled = true;
    tesseractInstallBtn = null;
  } else {
    btn.textContent = 'Failed';
    setTimeout(() => {
      btn.textContent = btn.dataset.original || 'Install';
      btn.disabled = false;
      tesseractInstallBtn = null;
    }, 3200);
  }
}

async function updateTesseractButtonStatus() {
  const btn = document.getElementById('btn-install-tesseract');
  if (!btn || !window.pywebview) return;
  try {
    const status = await pywebview.api.get_ocr_status();
    if (status && status.ok) {
      if (status.tesseract_installed) {
        btn.textContent = 'Installed';
        btn.disabled = true;
      } else if (status.windows_ocr) {
        btn.textContent = 'Installed (Win OCR)';
        btn.disabled = true;
      }
    }
  } catch (e) {}
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
  showTestPathIdle();
}

// Restores the Run/Stop pair to its idle state. Called both by the Stop button
// and by Python when the replay ends on its own (see debug_test_path's
// finally: push_ui("testPathFinished")) -- previously only the former existed,
// so a walk that simply finished left Stop showing for the rest of the session.
function showTestPathIdle() {
  const stop = document.getElementById('btn-stop-test-path');
  const run = document.getElementById('btn-test-path');
  if (stop) stop.style.display = 'none';
  if (run) { run.style.display = ''; run.disabled = false; run.textContent = 'Run'; }
}

window.testPathFinished = showTestPathIdle;

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
    document.getElementById('toggle-webhook-progress').classList.toggle('on', !!wh.progress);
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
  const progress = document.getElementById('toggle-webhook-progress').classList.contains('on');
  try {
    await pywebview.api.save_webhook_settings(url, enabled, silent, mentionId, progress);
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
    maps: ['School Grounds', 'Rose Kingdom', 'Fairy King Forest', "King's Tomb", 'Flower Forest', 'East Town'],
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
    maps: ['School Grounds', 'Flower Forest', 'Rose Kingdom', 'East Town'],
    difficulties: ['1', '2', '3'],
    // How many "exp_extract" prompts to decline before actually taking
    // one -- 0 extracts at the first one shown, 1 (default, matches the
    // old hardcoded behavior) waits for a second, and so on for a deeper
    // run. See core.runner._expedition_extract_accept_at.
    extractAfter: ['0', '1', '2', '3', '4', '5'],
  },
  event: {
    label: 'Event',
    // Event has its own lobby entry (nav_event -> event_gamemode -> Act),
    // no map carousel and no difficulty picker -- just one of the Acts (each a
    // villain), then Solo/Matchmaking. Stored in `stage` (values '1'-'4')
    // the same way Raid stores its Acts, so it reuses the existing
    // stage/act plumbing. Acts past the second are reached by scrolling the
    // villain list (see runner._reach_event_act_selected). Act 4 ("Crow -
    // Dawn") is relic-gated -- pick it to run it directly, or let a farm task
    // auto-divert to it on a Crow Relic drop (see the Act 4 controls the Task
    // Builder adds for event tasks). Mirrors core.runner_constants'
    // EVENT_ACT_ORDER.
    stages: ['1', '2', '3', '4'],
    isEvent: true,
  },
  tournament: {
    label: 'Tournament',
    // Tournament has its own lobby entry (nav_tournament -> a type card ->
    // nav_entertournament -> Start), no map carousel and no difficulty picker.
    // The "maps" list here IS the type picker -- each entry maps to its own
    // on-screen button image (see runner_constants' TOURNAMENT_TYPE_IMAGES,
    // hand-synced with this list). The chosen type is stored in the task's
    // `map` field, so it reads straight through to logs/status/webhook. Solo/
    // Matchmaking isn't offered: "Solo Tournament" already is the mode.
    maps: ['Solo Tournament'],
    isTournament: true,
  },
  tower: {
    label: 'Tower',
    maps: ['Rose Kingdom'],  // internal default only -- Tower has no map picker in-game
    stages: ['1'],
    isTower: true,
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
const DEFAULT_INFINITE_WAVE_LIMIT = 20;
const MAX_EXTRACT_AFTER = 9999;

function newTaskId() {
  return 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function defaultTask() {
  return {
    id: newTaskId(), mode: 'story',
    map: TASK_DATA.story.maps[0], stage: '1', difficulty: 'Normal',
    infinite_wave_limit: DEFAULT_INFINITE_WAVE_LIMIT,
    extract_after: '1',
    repeat: 1, team: '', equipment: 'include', play_mode: 'solo', macro: '',
    // Event-only: auto-clear Villian Invasion Act 4 when a Crow Relic drops.
    // act4_mode 'once' spends one relic then resumes; 'until_locked' spends
    // every banked relic. act4_macro is Act 4's own Macro Operation (it plays
    // nothing like Acts 1-3). See runner._run_act4_diversion.
    act4_on_drop: false, act4_mode: 'once', act4_macro: '',
  };
}

function normalizeExtractAfter(value) {
  // Number inputs serialize absurd values in scientific notation. Treat them
  // as "run as far as supported" instead of saving a value Python's int parser
  // cannot read when battle begins.
  const text = String(value ?? '').trim();
  if (!text) return '1';
  const number = Number(text);
  if (!Number.isFinite(number) || !Number.isInteger(number) || number < 0) return '1';
  return String(Math.min(number, MAX_EXTRACT_AFTER));
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

function collectCustomPathNames(templates) {
  const names = new Set();
  for (const template of Object.values(templates || {})) {
    const root = template && template.blocks != null ? template.blocks : template;
    const lists = Array.isArray(root)
      ? [root]
      : Object.values(root || {}).filter(Array.isArray);
    for (const blocks of lists) {
      for (const block of blocks) {
        if (block && block.type === 'walk_path' && block.mode === 'custom' && block.pathName) {
          names.add(block.pathName);
        }
      }
    }
  }
  return [...names];
}

async function exportCustomPaths(templates) {
  const paths = {};
  for (const name of collectCustomPathNames(templates)) {
    try {
      const saved = await pywebview.api.load_walk_path(name);
      if (saved && Array.isArray(saved.events)) paths[name] = saved;
    } catch (e) {}
  }
  return paths;
}

async function importCustomPaths(paths) {
  let existing = [];
  // A shipped default with the same name must not suppress the custom
  // recording carried by the export. Only user recordings count as existing.
  try { existing = await pywebview.api.list_custom_paths(); } catch (e) {}
  let added = 0;
  for (const [name, saved] of Object.entries(paths || {})) {
    if (existing.includes(name) || !saved || !Array.isArray(saved.events)) continue;
    try {
      const result = await pywebview.api.save_walk_path(name, saved.events);
      if (result && result.ok) { existing.push(name); added++; }
    } catch (e) {}
  }
  return added;
}

// Record block counterpart of collectCustomPathNames/exportCustomPaths/
// importCustomPaths above. Unlike that one, this recurses into a Detect
// block's then/else branches -- Record blocks running in Battle/Loop
// commonly sit inside one (see core.share._iter_blocks, the same
// traversal the Python side of the Share Code export already uses).
function collectRecordingNames(templates) {
  const names = new Set();
  const walk = (blocks) => {
    for (const block of blocks || []) {
      if (!block) continue;
      if (block.type === 'record' && block.params && block.params.recording) {
        names.add(block.params.recording);
      } else if (block.type === 'detect') {
        walk(block.then);
        walk(block.else);
      }
    }
  };
  for (const template of Object.values(templates || {})) {
    const root = template && template.blocks != null ? template.blocks : template;
    const lists = Array.isArray(root) ? [root] : Object.values(root || {}).filter(Array.isArray);
    for (const blocks of lists) walk(blocks);
  }
  return [...names];
}

// Unlike exportCustomPaths/importCustomPaths above, this bundles and
// restores recordings' events zlib-compressed (see
// core.input_record.collect_recordings_compressed) in one batched call --
// a dense mouse-move recording is thousands of small similar objects, and
// the plain-JSON task/template export this feeds isn't compressed
// otherwise, so an uncompressed recording could dominate the file's size
// on its own.
async function exportCustomRecordings(templates) {
  const names = collectRecordingNames(templates);
  if (names.length === 0) return {};
  try {
    return await pywebview.api.export_recordings_bundle(names) || {};
  } catch (e) {
    return {};
  }
}

async function importCustomRecordings(recordings) {
  if (!recordings || Object.keys(recordings).length === 0) return 0;
  try {
    const result = await pywebview.api.import_recordings_bundle(recordings);
    return (result && result.ok) ? result.added : 0;
  } catch (e) {
    return 0;
  }
}

async function exportSettings() {
  try {
    const s = await pywebview.api.get_settings();
    const payload = {
      kind: 'anime-expeditions-settings',
      version: 1,
      exported: new Date().toISOString(),
      settings: s,
    };
    const result = await pywebview.api.export_tasks_file(payload, 'settings');
    if (result && result.ok) addLog(`[Settings] Exported settings to ${result.path}`);
    else if (result && result.reason === 'cancelled') addLog('[Settings] Export cancelled.');
    else if (result) addLog(`[Settings] Export failed: ${result.reason || 'error'}`);
  } catch (e) {
    addLog(`[Settings] Export failed: ${e.message || e}`);
  }
}

async function importSettings() {
  try {
    const result = await pywebview.api.import_tasks_file();
    if (!result || !result.ok) {
      if (!result) {
        addLog('[Settings] Import failed: the file dialog could not be opened.');
      } else if (result.reason === 'cancelled') {
        addLog('[Settings] Import cancelled.');
      } else {
        addLog(`[Settings] Import failed: ${result.reason || 'error'}`);
      }
      return;
    }
    const data = result.data || {};
    if (data.kind !== 'anime-expeditions-settings' || !data.settings) {
      addLog('[Settings] Import failed: file is not a valid settings export.');
      return;
    }
    for (const [key, val] of Object.entries(data.settings)) {
      try { await pywebview.api.set_setting(key, val); } catch (e) {}
    }
    await loadSettingsUI();
    addLog('[Settings] Successfully imported and applied settings.');
  } catch (e) {
    addLog(`[Settings] Import failed: ${e.message || e}`);
  }
}

// Export bundles the queue AND every Macro Manager template the tasks reference
// (a task's `macro` is just a template name -- exported alone it would point
// at nothing on someone else's machine). Import restores both, giving all
// tasks fresh ids and never overwriting a template that already exists
// locally under the same name.
// Every macro a task can point at. act4_macro is Act 4's own Macro
// Operation and was left out of the export entirely, so a shared queue
// arrived referencing a macro the recipient did not have -- and the export
// still reported success.
function taskMacroNames(task) {
  return [task.macro, task.act4_macro].filter(Boolean);
}

async function exportTasks() {
  if (taskCards.length === 0) { addLog('[Task] Nothing to export -- the queue is empty.'); return; }
  let saved = [];
  try { saved = await pywebview.api.list_templates(); } catch (e) {}
  // Stop rather than ship a package that cannot work. load_template returns
  // an empty object for a name with no file, and the failure used to be
  // swallowed by `catch (e) {}`, so a task pointing at a renamed or deleted
  // macro exported "successfully" and only broke for whoever imported it.
  const missing = [...new Set(taskCards.flatMap(taskMacroNames))]
    .filter(name => !saved.includes(name));
  if (missing.length > 0) {
    addLog(`[Task] Export stopped -- ${missing.length} macro(s) referenced by the queue `
      + `no longer exist: ${missing.join(', ')}. Fix or remove those tasks first.`);
    return;
  }
  const templates = {};
  for (const t of taskCards) {
    for (const name of taskMacroNames(t)) {
      if (name in templates) continue;
      try { templates[name] = await pywebview.api.load_template(name); } catch (e) {}
    }
  }
  const paths = await exportCustomPaths(templates);
  const recordings = await exportCustomRecordings(templates);
  const payload = {
    kind: 'anime-expeditions-tasks', version: 2, exported: new Date().toISOString(),
    tasks: taskCards, templates, paths, recordings,
  };
  let result = null;
  let errMsg = null;
  try { result = await pywebview.api.export_tasks_file(payload); } catch (e) { errMsg = (e && e.message) || String(e); }
  if (result && result.ok) addLog(`[Task] Exported ${taskCards.length} task(s) to ${result.path}`);
  else if (!result) addLog(`[Task] Export failed: the save dialog could not be opened${errMsg ? ` (${errMsg})` : ''}.`);
  else if (result.reason === 'cancelled') addLog('[Task] Export cancelled.');
  else addLog(`[Task] Export failed: ${result.reason || 'error'}`);
}

async function importTasks() {
  let result = null;
  let errMsg = null;
  try { result = await pywebview.api.import_tasks_file('tasks'); }
  catch (e) { errMsg = (e && e.message) || String(e); }
  if (!result || !result.ok) {
    if (!result) {
      addLog(`[Task] Import failed: the file dialog could not be opened${errMsg ? ` (${errMsg})` : ''}.`);
    } else if (result.reason === 'cancelled') {
      addLog('[Task] Import cancelled.');
    } else {
      addLog(`[Task] Import failed: ${result.reason || 'error'}`);
    }
    return;
  }
  const data = result.data || {};
  // importSettings/importTemplates both check what kind of file this is before
  // trusting its contents; this one only ever checked for a `tasks` array, so
  // any JSON with that key was accepted as a task export.
  if (data.kind && data.kind !== 'anime-expeditions-tasks') {
    addLog('[Task] Import failed: that file is not a task export.');
    return;
  }
  if (!Array.isArray(data.tasks)) { addLog('[Task] Import failed: that file is not a task export.'); return; }
  // A bundled macro whose name you already use was skipped in silence, so
  // the imported task quietly pointed at YOUR macro of that name and ran
  // something other than what the sender built. Ask, and treat Cancel as
  // cancelling the whole import: keeping the tasks while declining their
  // macros is exactly the mismatch this is here to prevent.
  let existing = [];
  try { existing = await pywebview.api.list_templates(); } catch (e) {}
  const bundled = Object.entries(data.templates || {}).filter(([, t]) => t && t.blocks != null);
  const clashes = bundled.filter(([name]) => existing.includes(name));
  if (clashes.length > 0 && !confirm(
      `${clashes.length} macro(s) in this file have the same name as yours:\n\n`
      + clashes.map(([name]) => `    ${name}`).join('\n')
      + '\n\nReplace yours with the imported versions?\n'
      + 'Cancel stops the import, so the tasks cannot end up pointing at a different macro.')) {
    addLog('[Task] Import cancelled -- your macros were left alone.');
    return;
  }
  const pathAdded = await importCustomPaths(data.paths);
  const recordingAdded = await importCustomRecordings(data.recordings);
  let tplAdded = 0;
  try {
    for (const [name, t] of bundled) {
      // `t.blocks` is an OBJECT ({team, equipment, prestart, battle}) for every
      // template saved since Pre Start/Battle phases existed -- Array.isArray
      // is only true for the oldest flat-list format, so this silently dropped
      // every modern template. exportTasks bundles them precisely so a shared
      // queue does not arrive pointing at macros the recipient does not have,
      // and the whole point was being lost with no message. importTemplates
      // (same file) has always used the `!= null` form.
      try { await pywebview.api.save_template(name, t.blocks); tplAdded++; } catch (e) {}
    }
  } catch (e) {}
  let added = 0;
  for (const t of data.tasks) {
    const newTask = { ...defaultTask(), ...t, id: newTaskId() };
    newTask.extract_after = normalizeExtractAfter(newTask.extract_after);
    taskCards.push(newTask);
    enteringTaskIds.add(newTask.id);
    added++;
  }
  await refreshTaskTemplates();
  renderTaskList();
  renderTaskBuilder();
  saveTaskQueue();
  addLog(`[Task] Imported ${added} task(s)${tplAdded ? `, ${tplAdded} macro template(s)` : ''}${pathAdded ? `, ${pathAdded} custom path(s)` : ''}${recordingAdded ? `, and ${recordingAdded} recording(s)` : ''}.`);
}

// ---------------------------------------------------------------------------
// Task Queue presets
// ---------------------------------------------------------------------------
// Named queues saved on THIS machine, picked from a dropdown. Export/Import
// above is the other half of the story: that writes a file through a native
// dialog because it exists to move a queue to someone else's install. See
// core/task_presets.py.

async function refreshTaskPresets(keepSelected) {
  const sel = document.getElementById('task-preset-select');
  if (!sel) return;
  const previous = keepSelected != null ? keepSelected : sel.value;
  let names = [];
  try { names = await pywebview.api.list_task_presets(); } catch (e) {}
  // Built with new Option(...) rather than an innerHTML template: a preset
  // name is free text the user typed, and Option's text/value are set as
  // properties, so nothing in the name can be parsed as markup.
  sel.replaceChildren();
  if (names.length === 0) {
    sel.appendChild(new Option('No saved presets', ''));
  } else {
    for (const n of names) sel.appendChild(new Option(n, n));
  }
  if (previous && names.includes(previous)) sel.value = previous;
}

// Picking one pre-fills the name box, so Save overwrites the preset you're
// looking at instead of silently creating a near-duplicate.
function onTaskPresetPicked() {
  const sel = document.getElementById('task-preset-select');
  const nameInput = document.getElementById('task-preset-name');
  if (sel && nameInput && sel.value) nameInput.value = sel.value;
}

async function saveTaskPreset() {
  const nameInput = document.getElementById('task-preset-name');
  const name = (nameInput ? nameInput.value : '').trim();
  if (!name) { addLog('[Task] Give the preset a name first.'); return; }
  if (taskCards.length === 0) { addLog('[Task] Nothing to save -- the queue is empty.'); return; }
  let result = null;
  try { result = await pywebview.api.save_task_preset(name, taskCards); } catch (e) {}
  if (!result || !result.ok) {
    addLog(`[Task] Couldn't save preset: ${(result && result.reason) || 'error'}`);
    return;
  }
  await refreshTaskPresets(result.name);
}

async function loadTaskPreset() {
  const sel = document.getElementById('task-preset-select');
  const name = sel ? sel.value : '';
  if (!name) { addLog('[Task] Pick a preset to load first.'); return; }
  let result = null;
  try { result = await pywebview.api.load_task_preset(name); } catch (e) {}
  if (!result || !result.ok) { addLog('[Task] Couldn\'t load that preset.'); return; }
  if (!Array.isArray(result.tasks) || result.tasks.length === 0) {
    addLog(`[Task] Preset "${name}" has no tasks in it.`);
    return;
  }
  // Same sanitising refreshTaskQueue() does to the saved queue, for the same
  // reason: a preset is a plain .json the user is invited to edit by hand
  // (see the Folder button), and it can outlive a mode being renamed or
  // retired -- "challenge" was a real Task Queue mode once. An unrecognized
  // mode reaches TASK_DATA[t.mode] as undefined in taskSummary() and takes
  // the whole Task screen down with a TypeError, so those rows are dropped
  // and counted rather than trusted. stage is coerced to a String because
  // every comparison downstream assumes one.
  const merged = result.tasks
    .filter(t => t && typeof t === 'object')
    .map(t => ({ ...defaultTask(), ...t, id: newTaskId() }));
  const usable = merged.filter(t => TASK_DATA[t.mode]);
  const dropped = result.tasks.length - usable.length;
  if (usable.length === 0) {
    addLog(`[Task] Preset "${name}" has no usable tasks (unrecognized mode or malformed `
           + `entries) -- the queue was left as it was.`);
    return;
  }
  usable.forEach(t => {
    t.stage = String(t.stage);
    t.extract_after = normalizeExtractAfter(t.extract_after);
  });

  // Replaces the queue rather than appending -- Import appends (you're
  // merging someone else's tasks into yours), but loading a preset means
  // "run this queue instead", so appending would just pile duplicates up
  // every time you switched between two presets.
  taskCards = usable;
  if (dropped) {
    addLog(`[Task] Skipped ${dropped} task(s) in "${name}" with an unrecognized mode.`);
  }
  enteringTaskIds = new Set(taskCards.map(t => t.id));
  selectedTaskId = taskCards.length ? taskCards[0].id : null;
  await refreshTaskTemplates();
  renderTaskList();
  renderTaskBuilder();
  saveTaskQueue();
  if (result.missing_macros && result.missing_macros.length) {
    // The preset still loads -- those tasks just have no macro attached
    // now, which is worth saying out loud rather than letting a run start
    // with a silently empty Pre Start.
    addLog(`[Task] Heads up: this preset references ${result.missing_macros.length} Macro Operation(s) `
           + `that no longer exist (${result.missing_macros.join(', ')}) -- reassign those tasks.`);
  }
}

async function openTaskPresetsFolder() {
  // Also re-reads the folder afterwards: the whole point of opening it is to
  // add/rename/remove files by hand, and the dropdown should reflect that
  // without needing an app restart.
  try { await pywebview.api.open_task_presets_folder(); } catch (e) {}
  await refreshTaskPresets();
}

async function deleteTaskPreset() {
  const sel = document.getElementById('task-preset-select');
  const name = sel ? sel.value : '';
  if (!name) { addLog('[Task] Pick a preset to delete first.'); return; }
  let result = null;
  try { result = await pywebview.api.delete_task_preset(name); } catch (e) {}
  if (!result || !result.ok) { addLog(`[Task] Couldn't delete "${name}".`); return; }
  const nameInput = document.getElementById('task-preset-name');
  if (nameInput && nameInput.value === name) nameInput.value = '';
  await refreshTaskPresets('');
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
  // Wired to a "Clear All" danger button and there is no undo -- the whole
  // queue went in one click, silently.
  if (taskCards.length > 0 && !confirm(
      `Remove all ${taskCards.length} task(s) from the queue? This can't be undone.`)) return;
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
    else if (d.isEvent) t.map = 'Event';  // no map to pick, but a label keeps logs/status readable
    if (d.stages) t.stage = d.stages[0];
    if (d.difficulties) t.difficulty = d.difficulties[0];
    if (d.extractAfter) t.extract_after = '1';
    // Tournament has no Solo/Matchmaking toggle -- "Solo Tournament" already
    // is the mode, and the runner's solo Start tail only runs when this isn't
    // 'matchmaking'. Force it so switching from a matchmaking task can't leave
    // Tournament silently waiting on an Enter Matchmaking button.
    if (d.isTournament) t.play_mode = 'solo';
    if (d.isTower) {
      t.map = d.maps[0];
      t.stage = d.stages[0];
      if (!t.tower_mode) t.tower_mode = 'normal';
      t.play_mode = 'solo';
    }
  }
  if (key === 'stage' && value === 'Infinite' && !Number.isInteger(Number(t.infinite_wave_limit))) {
    t.infinite_wave_limit = DEFAULT_INFINITE_WAVE_LIMIT;
  }
  updateQueueRowInPlace(t);
  if (structural.includes(key)) renderTaskBuilder();
  saveTaskQueue();
}

function taskOpts(list, current, fmt) {
  return list.map(o => `<option value="${escapeHtml(o)}" ${String(o) === String(current) ? 'selected' : ''}>${escapeHtml(fmt ? fmt(o) : o)}</option>`).join('');
}

// One accent per mode so the queue scans by color before you even read it.
const TASK_MODE_COLORS = { story: 'var(--brand)', raid: 'var(--rose)', expedition: 'var(--teal)', event: 'var(--amber)', tournament: 'var(--lilac)', tower: 'var(--slate)' };

// The two text lines a queue row shows for a task -- where it goes, then how
// it runs. All editing happens in the Builder, rows are read-only summaries.
function taskSummary(t) {
  const d = TASK_DATA[t.mode];
  let title = d.label;
  if (t.mode === 'story' || t.mode === 'raid') {
    title += ` · ${t.map} · ${/^\d+$/.test(t.stage) ? 'Stage ' + t.stage : t.stage}`;
  } else if (t.mode === 'expedition' || t.mode === 'tournament') {
    title += ` · ${t.map}`;
  } else if (t.mode === 'event') {
    title += ` · Act ${t.stage}`;
  }
  const specialStage = t.mode === 'story' && (t.stage === 'Infinite' || t.stage === 'Mastery');
  const diff = ((t.mode === 'story' && !specialStage) || t.mode === 'expedition') ? t.difficulty
             : (d.fixedDifficulty || specialStage) ? 'Hard' : '';
  const meta = [
    `×${t.repeat}`,
    diff,
    t.mode === 'story' && t.stage === 'Infinite'
      ? `Stop after wave ${t.infinite_wave_limit || DEFAULT_INFINITE_WAVE_LIMIT}` : '',
    t.tower_mode === 'traitless' ? 'Traitless' : '',
    (t.mode === 'tournament' || t.mode === 'tower') ? '' : (t.play_mode === 'matchmaking' ? 'Matchmaking' : 'Solo'),
    t.macro ? `▸ ${t.macro}` : '',
    (t.mode === 'event' && t.stage !== '4' && t.act4_on_drop)
      ? `⮡ Act 4 on drop${t.act4_mode === 'until_locked' ? ' (until locked)' : ''}` : '',
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
        <div class="tq-title">${escapeHtml(title)}</div>
        <div class="tq-meta">${escapeHtml(meta)}</div>
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
  const sel = (key, options, fmt, tooltip = '') => `
    <select class="task-select" onchange="setTaskProp('${t.id}', '${key}', this.value)" ${tooltip ? `data-tooltip="${escapeHtml(tooltip)}"` : ''}>
      ${taskOpts(options, t[key], fmt)}
    </select>`;
  const field = (label, control, tooltip = '') => `<div class="task-field" ${tooltip ? `data-tooltip="${escapeHtml(tooltip)}"` : ''}><span>${label}</span>${control}</div>`;

  const fields = [
    field('Mode', sel('mode', Object.keys(TASK_DATA), k => TASK_DATA[k].label, 'Select game mode: Story, Raid, Expedition, Event, Tournament, or Tower'), 'Choose game mode'),
    field('Repeat', `<div class="task-rep-group" style="width: 100%;">&times;<input type="number" min="1" value="${t.repeat}"
      oninput="setTaskProp('${t.id}', 'repeat', Math.max(1, parseInt(this.value, 10) || 1))"></div>`, 'Number of times to run this task'),
  ];

  if (t.mode === 'story' || t.mode === 'raid') {
    fields.push(field('Map', sel('map', d.maps, null, 'Select map')));
    const stageTooltip = t.mode === 'raid' ? 'Select Raid Act 1, Act 2, or Act 3' : 'Select Stage 1-5, Infinite, or Mastery';
    fields.push(field('Stage', sel('stage', d.stages, s => /^\d+$/.test(s) ? 'Stage ' + s : s, stageTooltip), stageTooltip));
  } else if (t.mode === 'expedition') {
    fields.push(field('Expedition', sel('map', d.maps, null, 'Select Expedition map')));
  } else if (t.mode === 'event') {
    fields.push(field('Act', sel('stage', d.stages, s => 'Act ' + s, 'Select Event Act 1-4'), 'Select Event Act 1-4'));
  } else if (t.mode === 'tournament') {
    fields.push(field('Type', sel('map', d.maps, null, 'Select the Tournament type to enter'), 'Select the Tournament type to enter'));
  } else if (t.mode === 'tower') {
    // Tower has no map choice in-game -- map stays at its internal default.
    const towerMode = t.tower_mode || 'normal';
    const towerSeg = `
      <div class="seg-toggle">
        <button type="button" class="seg-btn ${towerMode !== 'traitless' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'tower_mode', 'normal'); renderTaskBuilder()">Normal</button>
        <button type="button" class="seg-btn ${towerMode === 'traitless' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'tower_mode', 'traitless'); renderTaskBuilder()">Traitless</button>
      </div>`;
    fields.push(field('Tower Mode', towerSeg));
  }

  const specialStage = t.mode === 'story' && (t.stage === 'Infinite' || t.stage === 'Mastery');
  if ((t.mode === 'story' && !specialStage) || t.mode === 'expedition') {
    fields.push(field('Difficulty', sel('difficulty', d.difficulties, null, 'Select difficulty level')));
  } else if (d.fixedDifficulty || specialStage) {
    fields.push(field('Difficulty', `<span class="task-chip" style="align-self: flex-start;">Hard &middot; locked</span>`, 'Difficulty locked to Hard for this mode'));
  }

  if (t.mode === 'story' && t.stage === 'Infinite') {
    fields.push(field('Stop After Wave', `<input type="number" class="block-input" min="1"
      value="${Math.max(1, parseInt(t.infinite_wave_limit, 10) || DEFAULT_INFINITE_WAVE_LIMIT)}"
      oninput="setTaskProp('${t.id}', 'infinite_wave_limit', Math.max(1, parseInt(this.value, 10) || 1))">`,
      'The macro lets this wave finish, then leaves when the next wave begins'));
  }

  if (t.mode === 'expedition') {
    fields.push(field('Extract After', `<input type="number" class="block-input" min="0" max="${MAX_EXTRACT_AFTER}" step="1" value="${t.extract_after}"
      onchange="this.value = normalizeExtractAfter(this.value); setTaskProp('${t.id}', 'extract_after', this.value)">`,
      `Number of extraction prompts to decline before extracting (maximum ${MAX_EXTRACT_AFTER})`));
  }

  // Tournament and Tower have no Solo/Matchmaking choice -- their runner paths
  // force the solo Start tail, so the toggle would be a no-op here.
  if (t.mode !== 'tournament' && t.mode !== 'tower') {
    const playSeg = `
      <div class="seg-toggle" data-tooltip="Select Solo or Matchmaking / Party mode">
        <button type="button" class="seg-btn ${t.play_mode === 'solo' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'play_mode', 'solo'); renderTaskBuilder()">Solo</button>
        <button type="button" class="seg-btn ${t.play_mode === 'matchmaking' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'play_mode', 'matchmaking'); renderTaskBuilder()">Matchmaking</button>
      </div>`;
    fields.push(field('Play Mode', playSeg, 'Select Solo or Matchmaking / Party mode'));
  }

  // Team Loadout rides with the chosen template (see the Macro Manager tab), so the
  // macro picker is the only loadout-related control left on a task.
  const macroSel = `
    <select class="task-select" onchange="setTaskProp('${t.id}', 'macro', this.value)" data-tooltip="Select a pre-start placement macro template">
      <option value="">No Macro</option>
      ${taskTemplates.map(n => `<option value="${escapeHtml(n)}" ${n === t.macro ? 'selected' : ''}>&#9654; ${escapeHtml(n)}</option>`).join('')}
    </select>`;
  fields.push(field('Macro Operation', macroSel, 'Select a pre-start placement macro template'));

  // Event farm tasks (Acts 1-3) can auto-divert to Villian Invasion Act 4
  // ("Crow - Dawn") when a Crow Relic drops. Not shown on an Act 4 task
  // itself -- there's nothing to divert TO. Act 4 needs its own Macro
  // Operation since it plays nothing like Acts 1-3.
  if (t.mode === 'event' && t.stage !== '4') {
    const on = !!t.act4_on_drop;
    const onOffSeg = `
      <div class="seg-toggle">
        <button type="button" class="seg-btn ${on ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'act4_on_drop', true); renderTaskBuilder()">On</button>
        <button type="button" class="seg-btn ${!on ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'act4_on_drop', false); renderTaskBuilder()">Off</button>
      </div>`;
    fields.push(field('Auto-clear Act 4 on relic drop', onOffSeg));
    if (t.act4_on_drop) {
      const runsSeg = `
        <div class="seg-toggle">
          <button type="button" class="seg-btn ${t.act4_mode !== 'until_locked' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'act4_mode', 'once'); renderTaskBuilder()">Once</button>
          <button type="button" class="seg-btn ${t.act4_mode === 'until_locked' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'act4_mode', 'until_locked'); renderTaskBuilder()">Until locked</button>
        </div>`;
      fields.push(field('Act 4 Runs', runsSeg));
      const act4MacroSel = `
        <select class="task-select" onchange="setTaskProp('${t.id}', 'act4_macro', this.value)">
          <option value="">No Macro</option>
          ${taskTemplates.map(n => `<option value="${escapeHtml(n)}" ${n === t.act4_macro ? 'selected' : ''}>&#9654; ${escapeHtml(n)}</option>`).join('')}
        </select>`;
      fields.push(field('Act 4 Macro Operation', act4MacroSel));
      // Act 4 gets its own play mode -- e.g. farm Solo but clear Act 4 in
      // Matchmaking, or vice versa. Defaults to the task's own play mode until
      // set (t.act4_play_mode absent -> runner falls back to t.play_mode).
      const act4Play = t.act4_play_mode || t.play_mode;
      const act4PlaySeg = `
        <div class="seg-toggle">
          <button type="button" class="seg-btn ${act4Play === 'solo' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'act4_play_mode', 'solo'); renderTaskBuilder()">Solo</button>
          <button type="button" class="seg-btn ${act4Play === 'matchmaking' ? 'active' : ''}" onclick="setTaskProp('${t.id}', 'act4_play_mode', 'matchmaking'); renderTaskBuilder()">Matchmaking</button>
        </div>`;
      fields.push(field('Act 4 Play Mode', act4PlaySeg));
    }
  }

  const extractHint = t.mode === 'expedition'
    ? `<div class="wh-hint">"Extract After" is how many extract prompts to skip before actually taking one -- 0 extracts at the first node, higher goes deeper (and takes longer) per run.</div>` : '';
  const infiniteHint = (t.mode === 'story' && t.stage === 'Infinite')
    ? `<div class="wh-hint"><b>Stop After Wave</b> completes the wave you enter, waits for the counter to advance once, then uses Leave Stage and returns to the lobby. For example, 20 leaves when wave 21 begins.</div>` : '';
  const act4Hint = (t.mode === 'event' && t.stage !== '4' && t.act4_on_drop)
    ? `<div class="wh-hint">When a Crow Relic drops on a win, the run leaves this stage, clears Act 4 (Crow - Dawn) with its own Macro Operation above, then comes back. <b>Once</b> spends one relic; <b>Until locked</b> spends every banked relic. Give Act 4 its own Macro Operation ${'&#8212;'} it plays nothing like Acts 1-3.</div>` : '';
  el.innerHTML = `
    <div class="task-builder-grid">${fields.join('')}</div>
    ${extractHint}
    ${infiniteHint}
    ${act4Hint}
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
    // Drop any task whose mode the queue no longer recognizes rather than
    // trying to render it. "Challenge" used to be a Task Queue mode (it never
    // ran and is now its own tab); "bounty" can leak in from Auto Bounty,
    // which is its own Resource-tab screen, not a queue mode. Either way an
    // unknown mode makes taskSummary() read TASK_DATA[mode].label off
    // undefined and throw, which aborts renderTaskList() mid-map and leaves
    // the whole list blank while the header still shows a count.
    const dropped = rawTasks.filter(t => !TASK_DATA[t.mode]).length;
    let repairedExtractAfter = 0;
    taskCards = rawTasks.filter(t => TASK_DATA[t.mode]).map(saved => {
      const t = { ...defaultTask(), ...saved };
      if (t.team == null) t.team = '';
      const normalizedExtractAfter = normalizeExtractAfter(t.extract_after);
      if (String(t.extract_after ?? '').trim() !== normalizedExtractAfter) {
        repairedExtractAfter++;
      }
      t.extract_after = normalizedExtractAfter;
      t.stage = String(t.stage);
      if (t.difficulty === 'Infinite' || t.difficulty === 'Mastery') {
        t.stage = t.difficulty;
        t.difficulty = 'Normal';
      }
      return t;
    });
    if (dropped || repairedExtractAfter) {
      if (dropped) {
        addLog(`[Task] Removed ${dropped} task(s) with an unrecognized mode (e.g. old Challenge/Bounty entries).`);
      }
      if (repairedExtractAfter) {
        addLog(`[Task] Adjusted invalid or oversized Expedition "Extract After" value(s) to the supported range.`);
      }
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
  refreshTaskPresets();  // fills the preset dropdown alongside the queue itself
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
// the Story maps over time (see main.py's CHALLENGE_STORY_MAPS comment) --
// config here is split the same way the backend models it: the daily play
// limit tracks each STAGE SLOT (whichever map is currently rotated into it),
// while Macro Operation assignment is tracked per MAP, since that's what
// needs to follow the map around as it rotates through slots.
const CHALLENGE_STAGE_SLOTS = ['1', '2', '3'];
// Mirrors main.py's CHALLENGE_STORY_MAPS -- keep in sync if Story's map
// list (TASK_DATA.story.maps) ever changes. This list is what renders the
// Story Map Setup rows, so a map missing here cannot be assigned a Macro
// Operation at all; tests/test_challenge_maps.py fails when it drifts.
const CHALLENGE_STORY_MAPS = ['School Grounds', 'Rose Kingdom', 'Fairy King Forest', "King's Tomb", 'Flower Forest', 'East Town'];
let challengeState = null;

function renderStoryMapSetupWarning(id, state, featureName) {
  const warning = document.getElementById(id);
  if (!warning) return;
  if (!state || state.setup_ready !== false) {
    warning.innerHTML = '';
    warning.style.display = 'none';
    return;
  }
  const missing = state.missing_maps || [];
  const invalid = state.invalid_maps || [];
  const problems = [];
  if (missing.length) problems.push(`Assign: ${missing.map(escapeHtml).join(', ')}`);
  if (invalid.length) {
    problems.push(`Repair: ${invalid.map(item =>
      `${escapeHtml(item.map)} (${escapeHtml(item.macro)})`).join(', ')}`);
  }
  warning.innerHTML = `<strong>Setup required:</strong> ${featureName} needs a saved Macro Operation for every Story map. ${problems.join('. ')}.`;
  warning.style.display = '';
}

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
  renderStoryMapSetupWarning('challenge-setup-warning', s, 'Auto Challenge');
  const daily = (s && s.daily) || { enabled: false, ready: true };
  const dailyEnabledBtn = document.getElementById('toggle-daily-challenge-enabled');
  if (dailyEnabledBtn) dailyEnabledBtn.classList.toggle('on', !!daily.enabled);
  const dailyStatus = document.getElementById('daily-challenge-status');
  if (dailyStatus) {
    dailyStatus.textContent = daily.ready ? 'Ready' : 'Completed today';
    dailyStatus.className = daily.ready ? 'challenge-ready-chip' : 'challenge-cap-chip';
  }
  const dailyCount = document.getElementById('daily-challenge-count');
  if (dailyCount) dailyCount.value = daily.ready ? 0 : 1;

  const summary = document.getElementById('resource-challenge-summary');
  if (summary) {
    const enabled = !!(daily.enabled || (s && s.enabled));
    summary.textContent = enabled ? 'Enabled' : 'Disabled';
    summary.classList.toggle('active', enabled);
  }
  const details = document.getElementById('resource-challenge-details');
  if (details) {
    const dailyText = daily.enabled
      ? `Daily: ${daily.ready ? 'Ready' : 'Complete'}`
      : 'Daily: Off';
    const regularText = s && s.enabled
      ? `Regular: ${CHALLENGE_STAGE_SLOTS.map(slot => {
          const info = (s.stages && s.stages[slot]) || {};
          return info.enabled ? `#${slot} ${info.count || 0}/${s.cap}` : `#${slot} Off`;
        }).join(', ')}`
      : 'Regular: Off';
    details.textContent = `${dailyText} | ${regularText}`;
    details.title = details.textContent;
  }
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
      // Manual cooldown control -- put a Ready slot on cooldown (skip it until
      // the next :00/:30 window, no daily play used), or clear a cooldown to
      // make it Ready now. Hidden when Capped: the daily cap is the blocker
      // there, not the window cooldown.
      const cooldownBtn = atCap ? ''
        : info.ready
          ? `<button class="task-toolbar-btn challenge-cooldown-btn" onclick="setChallengeStageCooldown('${slot}', true)" title="Skip this slot until the next :00 / :30 window -- doesn't use a daily play">Set on cooldown</button>`
          : `<button class="task-toolbar-btn challenge-cooldown-btn" onclick="setChallengeStageCooldown('${slot}', false)" title="Make this slot Ready again right now">Clear cooldown</button>`;
      return `
        <div class="task-card" style="--tqc: ${atCap ? 'var(--rose)' : 'var(--amber)'}; cursor: default;">
          <div class="tq-text" style="min-width: 0;">
            <div class="tq-title">Regular Challenge #${slot} ${statusChip}</div>
            <div class="challenge-map-row">
              <button class="toggle-switch ${info.enabled ? 'on' : ''}" onclick="toggleChallengeStage('${slot}', this)"></button>
              ${cooldownBtn}
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
    taskTemplates.map(n => `<option value="${escapeHtml(n)}" ${n === current ? 'selected' : ''}>&#9654; ${escapeHtml(n)}</option>`).join('');
  mapList.innerHTML = CHALLENGE_STORY_MAPS.map(map => {
    const info = s.maps[map] || { macro: '' };
    return `
      <div class="task-card" style="--tqc: var(--lilac); cursor: default;">
        <div class="tq-text" style="min-width: 0;">
          <div class="tq-title">${escapeHtml(map)}</div>
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
  try {
    const result = await pywebview.api.set_challenge_enabled(isOn);
    if (!result.ok && result.reason === 'incomplete_challenge_maps') {
      addLog('[Macro] Auto Challenge needs a saved Macro Operation for every Story map before it can be enabled.');
    }
  } catch (e) {}
  await refreshChallengeScreen();
}

async function setChallengePlayMode(playMode) {
  try { await pywebview.api.set_challenge_play_mode(playMode); } catch (e) {}
  await refreshChallengeScreen();
}

async function toggleDailyChallengeEnabled(btn) {
  const isOn = !btn.classList.contains('on');
  btn.classList.toggle('on', isOn);
  bounceToggle(btn);
  try {
    const result = await pywebview.api.set_daily_challenge_enabled(isOn);
    if (!result.ok && result.reason === 'incomplete_challenge_maps') {
      addLog('[Macro] Daily Challenge needs a saved Macro Operation for every Story map before it can be enabled.');
    }
  } catch (e) {}
  await refreshChallengeScreen();
}

async function setDailyChallengeCount(value) {
  const count = Math.max(0, Math.min(1, parseInt(value, 10) || 0));
  try { await pywebview.api.set_daily_challenge_count(count); } catch (e) {}
  await refreshChallengeScreen();
}

async function toggleChallengeStage(stage, btn) {
  const isOn = !btn.classList.contains('on');
  btn.classList.toggle('on', isOn);
  bounceToggle(btn);
  try { await pywebview.api.set_challenge_stage_enabled(stage, isOn); } catch (e) {}
  await refreshChallengeScreen();
}

async function setChallengeMapMacro(map, value) {
  try {
    const result = await pywebview.api.set_challenge_map_macro(map, value);
    if (result.auto_disabled) {
      addLog(`[Macro] Auto Challenge disabled: ${map} no longer has a usable Macro Operation.`);
    }
  } catch (e) {}
  await refreshChallengeScreen();
}

async function setChallengeStageCount(stage, value) {
  const count = Math.max(0, parseInt(value, 10) || 0);
  try { await pywebview.api.set_challenge_stage_count(stage, count); } catch (e) {}
  await refreshChallengeScreen();
}

async function setChallengeStageCooldown(stage, onCooldown) {
  try { await pywebview.api.set_challenge_stage_cooldown(stage, onCooldown); } catch (e) {}
  await refreshChallengeScreen();
}

async function resetChallengeCounts() {
  try { await pywebview.api.reset_challenge_counts(); } catch (e) {}
  addLog('[Challenge] Daily status, play counts, and cooldowns reset.');
  await refreshChallengeScreen();
}

// ---------------------------------------------------------------------------
// Auto Bounty
// ---------------------------------------------------------------------------
let bountyState = null;

async function refreshBountyScreen() {
  try {
    bountyState = await pywebview.api.get_bounty_settings();
  } catch (e) {
    bountyState = null;
  }
  await refreshTaskTemplates();
  renderBountyScreen();
}

function renderBountyScreen() {
  const s = bountyState;
  renderStoryMapSetupWarning('bounty-setup-warning', s, 'Auto Bounty');
  const summary = document.getElementById('resource-bounty-summary');
  if (summary) {
    const remaining = s ? `${s.remaining}/${s.total} left` : '';
    summary.textContent = s
      ? `${s.enabled ? 'Enabled' : 'Disabled'} | ${remaining}`
      : 'Disabled';
    summary.classList.toggle('active', !!(s && s.enabled));
  }
  document.getElementById('toggle-bounty-enabled')?.classList.toggle(
    'on', !!(s && s.enabled && s.setup_ready));
  document.getElementById('toggle-bounty-mythic')?.classList.toggle(
    'on', !!(s && s.mythic_only));
  const mythicMax = document.getElementById('bounty-mythic-max-rerolls');
  if (mythicMax && s) mythicMax.value = s.mythic_max_rerolls || 20;
  const playMode = (s && s.play_mode) || 'solo';
  document.getElementById('bounty-mode-solo')?.classList.toggle('active', playMode === 'solo');
  document.getElementById('bounty-mode-matchmaking')?.classList.toggle('active', playMode === 'matchmaking');
  const summonBanner = (s && s.summon_banner) || 'standard';
  document.getElementById('bounty-banner-standard')?.classList.toggle('active', summonBanner === 'standard');
  document.getElementById('bounty-banner-villain')?.classList.toggle('active', summonBanner === 'villain');
  const remaining = document.getElementById('bounty-remaining');
  if (remaining && s) {
    remaining.textContent = `${s.remaining} / ${s.total}`;
  }

  const list = document.getElementById('bounty-map-list');
  if (!list) return;
  if (!s) {
    list.innerHTML = '<div class="rh-empty">Couldn\'t load Auto Bounty settings.</div>';
    return;
  }
  const macroOpts = current => `<option value="">No Macro</option>` +
    taskTemplates.map(name =>
      `<option value="${escapeHtml(name)}" ${name === current ? 'selected' : ''}>&#9654; ${escapeHtml(name)}</option>`
    ).join('');
  list.innerHTML = CHALLENGE_STORY_MAPS.map(map => {
    const info = s.maps[map] || { macro: '' };
    return `
      <div class="task-card" style="--tqc: var(--lilac); cursor: default;">
        <div class="tq-text" style="min-width: 0;">
          <div class="tq-title">${escapeHtml(map)}</div>
          <div class="challenge-map-row">
            <select class="task-select" style="width: 100%;" onchange="setBountyMapMacro('${escJs(map)}', this.value)">
              ${macroOpts(info.macro)}
            </select>
          </div>
        </div>
      </div>`;
  }).join('');
}

async function toggleBountyEnabled(btn) {
  const isOn = !btn.classList.contains('on');
  bounceToggle(btn);
  try {
    const result = await pywebview.api.set_bounty_enabled(isOn);
    if (!result.ok && result.reason === 'incomplete_bounty_maps') {
      addLog('[Macro] Auto Bounty needs a saved Macro Operation for every Story map before it can be enabled.');
    }
  } catch (e) {}
  await refreshBountyScreen();
}

async function toggleBountyMythic(btn) {
  const isOn = !btn.classList.contains('on');
  bounceToggle(btn);
  try { await pywebview.api.set_bounty_mythic_only(isOn); } catch (e) {}
  await refreshBountyScreen();
}

async function setBountyMythicMaxRerolls(value) {
  try { await pywebview.api.set_bounty_mythic_max_rerolls(value); } catch (e) {}
  await refreshBountyScreen();
}

async function setBountyPlayMode(playMode) {
  try { await pywebview.api.set_bounty_play_mode(playMode); } catch (e) {}
  await refreshBountyScreen();
}

async function setBountySummonBanner(banner) {
  try { await pywebview.api.set_bounty_summon_banner(banner); } catch (e) {}
  await refreshBountyScreen();
}

async function setBountyMapMacro(map, macro) {
  try {
    const result = await pywebview.api.set_bounty_map_macro(map, macro);
    if (result.auto_disabled) {
      addLog(`[Macro] Auto Bounty disabled: ${map} no longer has a usable Macro Operation.`);
    }
  } catch (e) {}
  await refreshBountyScreen();
}

async function resetBountyRemaining() {
  try { await pywebview.api.reset_bounty_remaining(); } catch (e) {}
  await refreshBountyScreen();
}

// ---------------------------------------------------------------------------
// Auto Fuel screen (see core/runner_fuel.py). The runner only checks it at
// safe queue boundaries, so these controls never trigger a live run directly.
const FUEL_RESOURCE_LABELS = {
  resource_drill: 'Resource Drill',
  gold_mine: 'Gold Mine',
};
const FUEL_PATH_LABELS = {
  hub_to_resource_drill: 'Hub to Resource Drill',
  hub_to_gold_mine: 'Hub to Gold Mine',
  resource_drill_to_gold_mine: 'Resource Drill to Gold Mine',
};
let fuelState = null;

function formatFuelCountdown(seconds) {
  const total = Math.max(0, Math.ceil(Number(seconds) || 0));
  if (total === 0) return 'Ready now';
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function normalizeFuelIntervalInput(value, unit) {
  const amount = Math.max(1, parseInt(value, 10) || 1);
  return unit === 'hours' ? amount * 60 : amount;
}

function renderFuelIntervalControl() {
  const minutes = Math.max(0, parseInt(fuelState && fuelState.interval_minutes, 10) || 0);
  const input = document.getElementById('fuel-interval-value');
  const unit = document.getElementById('fuel-interval-unit');
  const controls = document.getElementById('fuel-interval-controls');
  const autoButton = document.getElementById('fuel-interval-auto');
  const isAuto = minutes === 0;
  if (autoButton) autoButton.classList.toggle('active', isAuto);
  if (controls) {
    controls.style.display = 'flex';
    controls.style.opacity = '1';
  }
  if (input && unit) {
    if (isAuto) {
      input.value = '';
      unit.value = 'minutes';
    } else if (minutes >= 60 && minutes % 60 === 0) {
      input.value = minutes / 60;
      unit.value = 'hours';
    } else {
      input.value = minutes;
      unit.value = 'minutes';
    }
  }
}

function renderFuelTimers() {
  if (!fuelState) return;
  const now = Date.now() / 1000;
  const cardDetails = [];
  for (const key of Object.keys(FUEL_RESOURCE_LABELS)) {
    const state = fuelState.resources[key];
    const enabled = fuelState.enabled && state.enabled;
    const remaining = Math.max(0, Number(state.next_due_at || 0) - now);
    const timer = document.getElementById(`fuel-${key.replaceAll('_', '-')}-timer`);
    const status = document.getElementById(`fuel-${key.replaceAll('_', '-')}-status`);
    if (timer) timer.textContent = enabled ? formatFuelCountdown(remaining) : 'Disabled';
    if (status) status.textContent = !enabled ? 'Disabled' : (remaining <= 0 ? 'Ready' : 'Waiting');
    cardDetails.push(
      `${FUEL_RESOURCE_LABELS[key]}: ${!enabled ? 'Off' : (remaining <= 0 ? 'Ready' : formatFuelCountdown(remaining))}`
    );
  }
  const summary = document.getElementById('fuel-summary-status');
  if (summary) {
    const enabledResources = Object.values(fuelState.resources).filter(x => x.enabled);
    const anyReady = fuelState.enabled && enabledResources.some(x => Number(x.next_due_at || 0) <= now);
    summary.textContent = !fuelState.enabled ? 'Disabled' : (!enabledResources.length ? 'No resources' : (anyReady ? 'Ready' : 'Waiting'));
  }
  const cardSummary = document.getElementById('resource-fuel-summary');
  if (cardSummary) {
    cardSummary.textContent = fuelState.enabled ? 'Enabled' : 'Disabled';
    cardSummary.classList.toggle('active', !!fuelState.enabled);
  }
  const details = document.getElementById('resource-fuel-details');
  if (details) {
    details.textContent = cardDetails.join(' | ');
    details.title = details.textContent;
  }
}

async function refreshFuelScreen() {
  try {
    fuelState = await pywebview.api.get_fuel_settings();
  } catch (e) {
    return;
  }
  const enabledToggle = document.getElementById('toggle-fuel-enabled');
  if (enabledToggle) enabledToggle.classList.toggle('on', !!fuelState.enabled);
  for (const key of Object.keys(FUEL_RESOURCE_LABELS)) {
    const state = fuelState.resources[key];
    const id = key.replaceAll('_', '-');
    const checkbox = document.getElementById(`fuel-${id}-enabled`);
    const maxButton = document.getElementById(`fuel-${id}-max`);
    const numberButton = document.getElementById(`fuel-${id}-number`);
    const amountInput = document.getElementById(`fuel-${id}-amount`);
    const isMax = String(state.amount).toLowerCase() === 'max';
    if (checkbox) checkbox.classList.toggle('on', !!state.enabled);
    if (maxButton) maxButton.classList.toggle('active', isMax);
    if (numberButton) numberButton.classList.toggle('active', !isMax);
    if (amountInput) {
      amountInput.value = isMax ? 1 : state.amount;
      amountInput.style.visibility = isMax ? 'hidden' : 'visible';
    }
  }
  renderFuelIntervalControl();
  renderFuelTimers();
  renderFuelPaths();
}

async function toggleFuelEnabled(btn) {
  const enabled = !btn.classList.contains('on');
  btn.classList.toggle('on', enabled);
  bounceToggle(btn);
  try { await pywebview.api.set_fuel_enabled(enabled); } catch (e) {}
  await refreshFuelScreen();
}

async function toggleFuelResourceEnabled(resource, button) {
  const enabled = !button.classList.contains('on');
  button.classList.toggle('on', enabled);
  bounceToggle(button);
  try { await pywebview.api.set_fuel_resource_enabled(resource, enabled); } catch (e) {}
  await refreshFuelScreen();
}

async function setFuelAmountMode(resource, mode) {
  const state = fuelState && fuelState.resources[resource];
  const amount = mode === 'max' ? 'max' : (
    state && String(state.amount).toLowerCase() !== 'max' ? state.amount : 1
  );
  await setFuelResourceAmount(resource, amount);
}

async function setFuelResourceAmount(resource, amount) {
  const normalized = String(amount).toLowerCase() === 'max'
    ? 'max'
    : Math.max(1, Math.min(100, parseInt(amount, 10) || 1));
  try { await pywebview.api.set_fuel_resource_amount(resource, normalized); } catch (e) {}
  await refreshFuelScreen();
}

async function setFuelIntervalValue() {
  const input = document.getElementById('fuel-interval-value');
  const unit = document.getElementById('fuel-interval-unit');
  const minutes = normalizeFuelIntervalInput(input ? input.value : 1, unit ? unit.value : 'minutes');
  try { await pywebview.api.set_fuel_interval(minutes); } catch (e) {}
  await refreshFuelScreen();
}

async function setFuelIntervalAuto(btn) {
  if (btn) {
    btn.classList.add('active');
    bounceToggle(btn);
  }
  try { await pywebview.api.set_fuel_interval(0); } catch (e) {}
  await refreshFuelScreen();
}

async function resetFuelTimer() {
  try { await pywebview.api.reset_fuel_timer(); } catch (e) {}
  await refreshFuelScreen();
}

async function setFuelPath(pathKey, pathName) {
  try { await pywebview.api.set_fuel_path(pathKey, pathName); } catch (e) {}
  await refreshFuelScreen();
}

async function openFuelPaths() {
  await refreshSavedPaths();
  await refreshFuelScreen();
  const modal = document.getElementById('fuel-paths-modal');
  if (modal) modal.style.display = 'flex';
}

function closeFuelPaths() {
  const modal = document.getElementById('fuel-paths-modal');
  if (modal) modal.style.display = 'none';
}

function renderFuelPaths() {
  const list = document.getElementById('fuel-path-list');
  if (!list || !fuelState) return;
  list.innerHTML = Object.entries(FUEL_PATH_LABELS).map(([key, label]) => {
    const current = fuelState.paths[key] || '';
    const options = ['<option value="">Not assigned</option>'].concat(
      savedPaths.map(name => `<option value="${escapeHtml(name)}" ${name === current ? 'selected' : ''}>${escapeHtml(name)}</option>`)
    ).join('');
    const recording = typeof recordingFuelPathKey !== 'undefined' && recordingFuelPathKey === key;
    return `<div class="fuel-path-row">
      <div>
        <div class="setting-label">${escapeHtml(label)}</div>
        <div class="setting-desc">${current ? 'Path assigned' : 'Recording required'}</div>
      </div>
      <select class="task-select" onchange="setFuelPath('${key}', this.value)">${options}</select>
      <button class="task-toolbar-btn ${recording ? 'danger' : ''}" onclick="toggleRecordFuelPath('${key}')">${recording ? 'Stop' : 'Record'}</button>
    </div>`;
  }).join('');
}

setInterval(renderFuelTimers, 1000);

// ---------------------------------------------------------------------------
// Auto Shop screen. The backend owns the catalog and daily state; the UI only
// edits stable shop/item identifiers and their requested daily targets.
// ---------------------------------------------------------------------------
let autoShopState = null;

function autoShopStatusLabel(status) {
  return {
    completed: 'Complete',
    out_of_stock: 'Out of stock',
    max_inventory: 'Max inventory',
    failed_today: 'Failed today',
    pending_verification: 'Verifying',
    retry_pending: 'Retry scheduled',
    pending: 'Pending',
  }[status] || 'Pending';
}

async function refreshAutoShopScreen() {
  try {
    autoShopState = await pywebview.api.get_auto_shop_settings();
  } catch (e) {
    autoShopState = null;
  }
  renderAutoShopScreen();
}

function renderAutoShopScreen() {
  const state = autoShopState;
  const goldShop = state && state.shops ? state.shops.gold_shop : null;
  const items = (goldShop && goldShop.items) || [];
  const enabledItems = items.filter(item => item.enabled);
  const completeItems = enabledItems.filter(
    item => ['completed', 'out_of_stock', 'max_inventory'].includes(
      (item.state || {}).status
    )
  );

  const summary = document.getElementById('resource-auto-shop-summary');
  if (summary) {
    summary.textContent = state && state.enabled ? 'Enabled' : 'Disabled';
    summary.classList.toggle('active', !!(state && state.enabled));
  }
  const details = document.getElementById('resource-auto-shop-details');
  if (details) {
    details.textContent = `Gold Shop: ${enabledItems.length} enabled | ${completeItems.length} complete`;
    details.title = details.textContent;
  }
  document.getElementById('toggle-auto-shop-enabled')?.classList.toggle(
    'on',
    !!(state && state.enabled)
  );
  document.getElementById('toggle-gold-shop-enabled')?.classList.toggle(
    'on',
    !!(goldShop && goldShop.enabled)
  );

  const list = document.getElementById('auto-shop-gold-items');
  if (!list) return;
  if (!goldShop) {
    list.innerHTML = '<div class="rh-empty">Couldn\'t load Auto Shop settings.</div>';
    return;
  }
  list.innerHTML = items.map(item => {
    const isMax = String(item.target).toLowerCase() === 'max';
    const numericTarget = isMax ? '' : item.target;
    const runtime = item.state || {};
    const attempts = Number(runtime.attempts || 0);
    const status = autoShopStatusLabel(runtime.status);
    const isCompleted = runtime.status === 'completed' || runtime.status === 'out_of_stock' || runtime.status === 'max_inventory';
    const resetToday = runtime.status && runtime.status !== 'pending'
      ? `<button type="button" class="block-mod-btn" style="padding: 3px 10px; font-size: 11px; border-color: rgba(224, 86, 122, 0.4); color: var(--rose);"
                 onclick="resetAutoShopItemToday('gold_shop', '${item.key}')">Reset Today</button>`
      : '';
    return `
      <div class="task-card" data-key="${escapeHtml(item.key)}" style="--tqc: var(--amber); cursor: default; padding: 10px 14px; margin-bottom: 6px;">
        <div class="tq-text" style="width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 12px;">
          <div style="display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1;">
            <button class="toggle-switch ${item.enabled ? 'on' : ''}"
                    onclick="toggleAutoShopItem('gold_shop', '${item.key}', this)"></button>
            <div style="min-width: 0; flex: 1;">
              <div style="display: flex; align-items: center; gap: 8px;">
                <span class="tq-title" style="font-weight: 600; font-size: 14px; color: var(--text);">${escapeHtml(item.name)}</span>
                ${resetToday}
              </div>
              <div class="setting-desc" style="font-size: 12px; color: var(--text-dim); margin-top: 2px;">
                Daily max: ${item.daily_maximum} | <span style="color: ${isCompleted ? 'var(--teal)' : 'var(--text-muted)'};">${status}</span>${attempts ? ` | ${attempts}/3 attempts` : ''}
              </div>
            </div>
          </div>
          <div style="display: flex; align-items: center; gap: 8px; flex-shrink: 0;">
            <div class="seg-toggle" style="width: auto;">
              <button type="button" value="max" class="seg-btn ${isMax ? 'active' : ''}"
                      onclick="setAutoShopItemMax('gold_shop', '${item.key}')">Max</button>
              <button type="button" class="seg-btn ${isMax ? '' : 'active'}"
                      onclick="setAutoShopItemNumberMode('gold_shop', '${item.key}')">Number</button>
            </div>
            <input type="number" class="block-input" min="1" max="${item.daily_maximum}"
                   style="width: 58px; text-align: center; ${isMax ? 'visibility: hidden;' : ''}"
                   value="${numericTarget}" placeholder="Qty"
                   onchange="setAutoShopItemTarget('gold_shop', '${item.key}', this.value, ${item.daily_maximum})">
          </div>
        </div>
      </div>`;
  }).join('');
}

async function toggleAutoShopEnabled(button) {
  const enabled = !button.classList.contains('on');
  button.classList.toggle('on', enabled);
  bounceToggle(button);
  try { await pywebview.api.set_auto_shop_enabled(enabled); } catch (e) {}
  await refreshAutoShopScreen();
}

async function toggleAutoShopShopEnabled(shopKey, button) {
  const enabled = !button.classList.contains('on');
  button.classList.toggle('on', enabled);
  bounceToggle(button);
  try { await pywebview.api.set_auto_shop_shop_enabled(shopKey, enabled); } catch (e) {}
  await refreshAutoShopScreen();
}

async function toggleAutoShopItem(shopKey, itemKey, button) {
  const enabled = !button.classList.contains('on');
  button.classList.toggle('on', enabled);
  bounceToggle(button);
  try {
    await pywebview.api.set_auto_shop_item_enabled(shopKey, itemKey, enabled);
  } catch (e) {}
  await refreshAutoShopScreen();
}

async function setAutoShopItemMax(shopKey, itemKey) {
  try {
    await pywebview.api.set_auto_shop_item_target(shopKey, itemKey, 'max');
  } catch (e) {}
  await refreshAutoShopScreen();
}

async function setAutoShopItemNumberMode(shopKey, itemKey) {
  const shop = autoShopState && autoShopState.shops
    ? autoShopState.shops[shopKey]
    : null;
  const item = ((shop && shop.items) || []).find(entry => entry.key === itemKey);
  const target = item && String(item.target).toLowerCase() !== 'max' ? item.target : 1;
  await setAutoShopItemTarget(shopKey, itemKey, target, item ? item.daily_maximum : 1);
}

async function setAutoShopItemTarget(shopKey, itemKey, value, dailyMaximum) {
  const target = Math.max(1, Math.min(
    Number(dailyMaximum) || 1,
    parseInt(value, 10) || 1
  ));
  try {
    await pywebview.api.set_auto_shop_item_target(shopKey, itemKey, target);
  } catch (e) {}
  await refreshAutoShopScreen();
}

async function resetAutoShopItemToday(shopKey, itemKey) {
  try {
    await pywebview.api.reset_auto_shop_item_today(shopKey, itemKey);
  } catch (e) {}
  await refreshAutoShopScreen();
}

// Auto Crafting screen (see core/runner_crafting.py). Interleaved like
// Challenge: after every N qualifying wins it runs one crafting pass. The
// label map mirrors CRAFT_SPRITE_LABELS in core/runner_constants.py -- same
// JS-side duplication as CHALLENGE_STAGE_SLOTS above.
// ---------------------------------------------------------------------------
const CRAFT_SPRITE_LABELS = {
  sprite_rainbow: 'Rainbow', sprite_red: 'Red', sprite_yellow: 'Yellow',
  sprite_green: 'Green', sprite_blue: 'Blue', sprite_purple: 'Purple', sprite_pink: 'Pink',
};
let craftingState = null;

async function refreshCraftingScreen() {
  try {
    craftingState = await pywebview.api.get_crafting_settings();
  } catch (e) {
    craftingState = null;
  }
  renderCraftingScreen();
}

function renderCraftingScreen() {
  const s = craftingState;
  const summary = document.getElementById('resource-crafting-summary');
  if (summary) {
    summary.textContent = s && s.enabled ? 'Enabled' : 'Disabled';
    summary.classList.toggle('active', !!(s && s.enabled));
  }
  const details = document.getElementById('resource-crafting-details');
  if (details) {
    const selected = ((s && s.items) || []).filter(item => item.enabled);
    const labels = selected.map(item => {
      const label = CRAFT_SPRITE_LABELS[item.key] || item.key;
      return `${label} (${String(item.amount).toLowerCase() === 'max' ? 'Max' : item.amount})`;
    });
    const spriteText = labels.length ? labels.join(', ') : 'No sprites selected';
    const progressText = s ? `${s.count}/${s.every} wins` : 'progress unavailable';
    details.textContent = `${spriteText} | ${progressText}`;
    details.title = details.textContent;
  }
  const enabledBtn = document.getElementById('toggle-crafting-enabled');
  if (enabledBtn) enabledBtn.classList.toggle('on', !!(s && s.enabled));
  const everyInput = document.getElementById('crafting-every');
  if (everyInput && s) everyInput.value = s.every;
  const prog = document.getElementById('crafting-progress');
  if (prog && s) prog.textContent = `${s.count} / ${s.every}`;

  const list = document.getElementById('crafting-item-list');
  if (!list) return;
  if (!s) { list.innerHTML = '<div class="rh-empty">Couldn\'t load crafting settings.</div>'; return; }
  const items = s.items || [];
  list.innerHTML = items.map((it, i) => {
    const label = CRAFT_SPRITE_LABELS[it.key] || it.key;
    const isMax = String(it.amount).toLowerCase() === 'max';
    const num = isMax ? '' : it.amount;
    const upDis = i === 0 ? 'disabled' : '';
    const downDis = i === items.length - 1 ? 'disabled' : '';
    return `
      <div class="task-card" data-key="${it.key}" style="--tqc: var(--teal); cursor: default;">
        <span class="task-grip crafting-grip" onclick="event.stopPropagation()" title="Drag to reorder priority">&#10247;</span>
        <div class="tq-text" style="min-width: 0; flex: 1;">
          <div class="challenge-map-row" style="margin-top: 0;">
            <div style="display: flex; flex-direction: column; gap: 2px;">
              <button class="task-toolbar-btn" ${upDis} style="padding: 0 6px; line-height: 1.2;" onclick="moveCraftingItem('${it.key}', -1)" title="Higher priority">&#9650;</button>
              <button class="task-toolbar-btn" ${downDis} style="padding: 0 6px; line-height: 1.2;" onclick="moveCraftingItem('${it.key}', 1)" title="Lower priority">&#9660;</button>
            </div>
            <button class="toggle-switch ${it.enabled ? 'on' : ''}" onclick="toggleCraftingItem('${it.key}', this)"></button>
            <span class="tq-title" style="flex: 1;">${escapeHtml(label)}</span>
            <div class="seg-toggle" style="width: auto;">
              <button type="button" class="seg-btn ${isMax ? 'active' : ''}" onclick="setCraftingItemMax('${it.key}')">Max</button>
              <button type="button" class="seg-btn ${isMax ? '' : 'active'}" onclick="setCraftingItemNumberMode('${it.key}')">Number</button>
            </div>
            <input type="number" class="block-input" min="1" max="9999" style="width: 64px; ${isMax ? 'visibility: hidden;' : ''}"
                   value="${num}" placeholder="Qty" onchange="setCraftingItemAmount('${it.key}', this.value)">
          </div>
        </div>
      </div>`;
  }).join('');
}

// ── Crafting item drag-reorder: grip-drag with a floating ghost + drop indicator ──
(function () {
  let dragItemKey = null, ghost = null, indicator = null;

  function craftingItemLabel(key) {
    return CRAFT_SPRITE_LABELS[key] || key;
  }

  function dropTargetAt(y) {
    const list = document.getElementById('crafting-item-list');
    if (!list) return null;
    const cards = [...list.querySelectorAll('.task-card')].filter(c => c.dataset.key !== dragItemKey);
    for (const c of cards) {
      const r = c.getBoundingClientRect();
      if (y < r.top + r.height / 2) return c;
    }
    return null;
  }

  document.addEventListener('mousedown', e => {
    const grip = e.target.closest('#crafting-item-list .crafting-grip');
    if (!grip) return;
    e.preventDefault();
    const cardEl = grip.closest('.task-card');
    if (!cardEl || !cardEl.dataset.key) return;
    dragItemKey = cardEl.dataset.key;

    const rect = cardEl.getBoundingClientRect();
    ghost = document.createElement('div');
    ghost.className = 'drag-ghost';
    ghost.textContent = craftingItemLabel(dragItemKey);
    document.body.appendChild(ghost);
    ghost.style.left = rect.left + 'px';
    ghost.style.top = (e.clientY - 14) + 'px';

    indicator = document.createElement('div');
    indicator.className = 'drop-indicator';

    cardEl.classList.add('dragging');
    document.body.style.cursor = 'grabbing';
  });

  document.addEventListener('mousemove', e => {
    if (!dragItemKey || !ghost) return;
    ghost.style.top = (e.clientY - 14) + 'px';
    ghost.style.left = (e.clientX + 14) + 'px';
    const list = document.getElementById('crafting-item-list');
    if (!list) return;
    const before = dropTargetAt(e.clientY);
    if (before) list.insertBefore(indicator, before);
    else list.appendChild(indicator);
  });

  document.addEventListener('mouseup', async e => {
    if (!dragItemKey) return;
    const list = document.getElementById('crafting-item-list');
    const before = list ? dropTargetAt(e.clientY) : null;

    const items = (craftingState && craftingState.items) || [];
    const order = items.map(x => x.key);
    const fromIdx = order.indexOf(dragItemKey);

    if (ghost) ghost.remove();
    if (indicator) indicator.remove();
    const cardEl = list ? list.querySelector(`.task-card[data-key="${dragItemKey}"]`) : null;
    if (cardEl) cardEl.classList.remove('dragging');

    ghost = indicator = null;
    const currentDragKey = dragItemKey;
    dragItemKey = null;
    document.body.style.cursor = '';

    if (fromIdx !== -1) {
      order.splice(fromIdx, 1);
      const toIdx = before ? order.indexOf(before.dataset.key) : order.length;
      if (toIdx !== -1 && toIdx !== fromIdx) {
        order.splice(toIdx, 0, currentDragKey);
        try { await pywebview.api.set_crafting_order(order); } catch (err) {}
        await refreshCraftingScreen();
      }
    }
  });
})();

async function toggleCraftingEnabled(btn) {
  const isOn = !btn.classList.contains('on');
  btn.classList.toggle('on', isOn);
  bounceToggle(btn);
  try { await pywebview.api.set_crafting_enabled(isOn); } catch (e) {}
  await refreshCraftingScreen();
}

async function setCraftingEvery(value) {
  const n = Math.max(1, Math.min(999, parseInt(value, 10) || 1));
  try { await pywebview.api.set_crafting_every(n); } catch (e) {}
  await refreshCraftingScreen();
}

async function toggleCraftingItem(key, btn) {
  const isOn = !btn.classList.contains('on');
  btn.classList.toggle('on', isOn);
  bounceToggle(btn);
  try { await pywebview.api.set_crafting_item_enabled(key, isOn); } catch (e) {}
  await refreshCraftingScreen();
}

async function setCraftingItemMax(key) {
  try { await pywebview.api.set_crafting_item_amount(key, 'max'); } catch (e) {}
  await refreshCraftingScreen();
}

async function setCraftingItemNumberMode(key) {
  // Switching Max -> Number seeds a real quantity (1) so there's something to
  // craft; keeps the existing number if it already had one.
  const it = ((craftingState && craftingState.items) || []).find(x => x.key === key);
  const amt = it && String(it.amount).toLowerCase() !== 'max' ? it.amount : 1;
  try { await pywebview.api.set_crafting_item_amount(key, amt); } catch (e) {}
  await refreshCraftingScreen();
}

async function setCraftingItemAmount(key, value) {
  const n = Math.max(1, Math.min(9999, parseInt(value, 10) || 1));
  try { await pywebview.api.set_crafting_item_amount(key, n); } catch (e) {}
  await refreshCraftingScreen();
}

async function moveCraftingItem(key, dir) {
  const items = (craftingState && craftingState.items) || [];
  const order = items.map(x => x.key);
  const i = order.indexOf(key);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= order.length) return;
  order.splice(i, 1);
  order.splice(j, 0, key);
  try { await pywebview.api.set_crafting_order(order); } catch (e) {}
  await refreshCraftingScreen();
}

async function resetCraftingCount() {
  try { await pywebview.api.reset_crafting_count(); } catch (e) {}
  await refreshCraftingScreen();
}

function openCraftingSprites() {
  const m = document.getElementById('crafting-sprites-modal');
  if (m) m.style.display = 'flex';
  renderCraftingScreen();  // populate the list (also refreshes the toggle/progress on the screen)
}

function closeCraftingSprites() {
  const m = document.getElementById('crafting-sprites-modal');
  if (m) m.style.display = 'none';
}

function openChallengeMaps() {
  const m = document.getElementById('challenge-maps-modal');
  if (m) m.style.display = 'flex';
  refreshChallengeScreen();  // (re)populate the per-map Macro Operation dropdowns
}

function closeChallengeMaps() {
  const m = document.getElementById('challenge-maps-modal');
  if (m) m.style.display = 'none';
}

function openBountyMaps() {
  const m = document.getElementById('bounty-maps-modal');
  if (m) m.style.display = 'flex';
  refreshBountyScreen();  // (re)populate Auto Bounty's independent map assignments
}

function closeBountyMaps() {
  const m = document.getElementById('bounty-maps-modal');
  if (m) m.style.display = 'none';
}

async function testCrafting() {
  let res = null;
  try { res = await pywebview.api.test_crafting(); } catch (e) {}
  if (res && res.ok) {
    addLog('[Craft] Running a test crafting pass now -- watch the log.');
    switchScreen('dashboard');  // so the docked game + Process Log are visible while it runs
  } else if (res && res.reason === 'already_running') {
    addLog('[Craft] Can\'t test -- the macro is already running. Stop it first (F8).');
  } else {
    addLog('[Craft] Couldn\'t start the crafting test (is Roblox docked?).');
  }
}

async function testFuel() {
  let result = null;
  try { result = await pywebview.api.test_fuel(); } catch (e) {}
  if (result && result.ok) {
    addLog('[Fuel] Running a test Auto Fuel pass now -- watch the log.');
    switchScreen('dashboard');
  } else if (result && result.reason === 'already_running') {
    addLog('[Fuel] Can\'t test -- the macro is already running. Stop it first (F8).');
  } else if (result && result.reason === 'no_resources') {
    addLog('[Fuel] Can\'t test -- enable Resource Drill or Gold Mine first.');
  } else {
    addLog('[Fuel] Couldn\'t start the Auto Fuel test (is Roblox docked?).');
  }
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
  target_priority:    { label: 'Target Priority',   group: 'Units',  color: 'var(--brand)', params: [] },
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
  // After this many minutes into the match, leave to the lobby (clicks
  // nav_todalobby -> Return); the next repeat re-enters from the lobby. The
  // leave flag is checked after both the Battle and Loop ticks, so it works in
  // Battle or a Loop phase. See core/runner_blocks._run_leave_at_minute_tick.
  leave_at_minute:    { label: 'Leave at Minute',   group: 'Timing', color: 'var(--rose)',  params: [{ key: 'minutes', type: 'number', placeholder: 'min', default: 10 }] },
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
  // Presses a keyboard key at this point (an ability, interact, menu key --
  // anything no dedicated block covers). Bespoke controls: a key-capture
  // button + an optional hold time. See renderSendKeyControls / the runner's
  // _run_send_key_tick.
  send_key:           { label: 'Send Key',          group: 'Setup',  color: 'var(--brand)', params: [{ key: 'hold_ms', type: 'number', placeholder: 'hold ms', default: 0 }] },
  // Records a whole mouse+keyboard input sequence (movement, clicks, scroll,
  // any key -- not just WASD or one Click/Send Key at a time) via its own
  // Record/Stop button, then replays it with the original timing. The
  // general-purpose escape hatch Click/Send Key don't cover on their own:
  // a multi-step UI interaction, a precisely-timed combo, ... Bespoke
  // controls: renderRecordControls(); recording/replay live in
  // core.input_record, runs via core.runner_blocks._run_record_macro_tick.
  // Allowed in both phases, same as Walk.
  record:             { label: 'Record',            group: 'Setup',  color: 'var(--rose)',  params: [] },
  // Detect: search for an image (or a combination, or a raw condition) and run
  // one of two nested block groups -- Then when found, Else when not. The
  // macro's one branching block. Bespoke controls: renderDetectControls();
  // runs via core.detect on the Python side. Allowed in both phases.
  detect:             { label: 'Detect',            group: 'Logic',  color: 'var(--sky)',   params: [] },
};

// Two phases: Pre Start (walk to your spot, place starter units, flip any
// settings that need to be set before the match begins -- plus Wait, for
// pacing those against game UI that needs a beat to settle) and Battle
// (everything else -- upgrades/sells/wave waits only make sense once it's live).
// Four phases. Pre Start (walk to spot, place starters, flip settings) and
// Battle (upgrades/sells/waits) both run ONCE through per match. Loop A and
// Loop B run DURING the match too, but their whole list repeats continuously
// alongside Battle -- built for polling patterns like "wait until an image
// shows up, then do something" via the Detect block, which a once-through list
// can't express.
const PHASES = ['prestart', 'battle', 'loop_a', 'loop_b'];
const PHASE_LABELS = { prestart: 'Pre Start', battle: 'Battle', loop_a: 'Loop A', loop_b: 'Loop B' };
const PHASE_TAGS = { prestart: 'Setup', battle: 'Combat', loop_a: 'Repeats', loop_b: 'Repeats' };
const _BATTLE_ALLOWED = Object.keys(BLOCK_TYPES).filter(t => t !== 'walk_path');
const PHASE_ALLOWED = {
  // walk_path is deliberately in NEITHER palette: it's the one unique
  // pinned block -- every routine always has exactly one in Pre Start
  // (synthesized on new/load, never removable), so offering it as an
  // addable block would only create duplicates. 'walk' (replay a recorded
  // path) is a normal addable block, allowed in BOTH phases -- you can drop
  // several into Pre Start to walk between multiple starter-placement spots
  // before the match begins. The Loop phases take the same set as Battle.
  prestart: ['place_unit', 'setting_change', 'auto_upgrade_unit', 'target_priority', 'walk', 'record', 'click', 'wait_ms', 'send_key', 'detect'],
  battle: _BATTLE_ALLOWED,
  loop_a: _BATTLE_ALLOWED,
  loop_b: _BATTLE_ALLOWED,
};

let creationPhases = { prestart: [], battle: [], loop_a: [], loop_b: [] };
let phaseCollapsed = { prestart: false, battle: false, loop_a: false, loop_b: false };
let recordingBlockId = null;
let recordingFuelPathKey = null;
let savedPaths = [];
// Record block (mouse+keyboard input recordings) -- kept separate from the
// Walk Path/Walk recorder state above rather than generalizing it, so this
// new recorder can't accidentally interact with the existing, already-
// working path-recording flow.
let recordingMacroBlockId = null;
let pendingMacroRecordingTarget = null;
let savedRecordings = [];

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
// A "container" is any list blocks live in: a phase's own list, or one branch
// (then/else) of a Detect block. It's addressed by a string key so drag-drop
// and rendering can pass it around and resolve it back to the array:
//   'prestart' | 'battle'                       -- a phase's top-level list
//   '<phase>|<detectId>|then' (repeatable)      -- a Detect branch, nestable,
//                                                  e.g. 'battle|d1|then|d2|else'
function containerPhase(key) { return key.split('|')[0]; }

function resolveContainer(key) {
  const parts = key.split('|');
  let list = creationPhases[parts[0]] || null;
  for (let i = 1; list && i + 1 < parts.length; i += 2) {
    const b = list.find(x => x.id === parts[i] && x.type === 'detect');
    if (!b) return null;
    if (!Array.isArray(b[parts[i + 1]])) b[parts[i + 1]] = [];
    list = b[parts[i + 1]];
  }
  return list;
}

// Recursively locate a block by id anywhere in either phase, descending into
// Detect then/else branches. Returns { phase, idx, container, key, block }:
// `container` is the owning array (phase list OR a branch), `idx` its index in
// it, `phase` the owning phase (for PHASE_ALLOWED), `key` the container key.
function findBlockLocation(id) {
  for (const phase of PHASES) {
    const hit = _findInContainer(creationPhases[phase], phase, id);
    if (hit) return hit;
  }
  return null;
}

function _findInContainer(list, key, id) {
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === id) return { phase: containerPhase(key), idx: i, container: list, key, block: b };
    if (b.type === 'detect') {
      for (const branch of ['then', 'else']) {
        const hit = _findInContainer(b[branch] || [], `${key}|${b.id}|${branch}`, id);
        if (hit) return hit;
      }
    }
  }
  return null;
}

// `key` is a container key (a phase, or a Detect branch -- see
// findBlockLocation). Palette clicks pass a bare phase; drops pass whichever
// container was dropped into.
function addBlock(type, key, atIndex) {
  const def = BLOCK_TYPES[type];
  if (!def) return;
  const phase = containerPhase(key);
  if (!allowedInContainer(type, key)) {
    addLog(`[Macro Manager] "${def.label}" can't go in ${PHASE_LABELS[phase] || phase}.`);
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
  if (type === 'send_key') { block.key = ''; }
  if (type === 'upgrade_unit') { block.params.index = ''; block.params.times = 1; }
  if (type === 'auto_upgrade_unit') {
    block.params.index = '';
    block.params.priority = 1;
    block.params.input = 'click';
  }
  if (type === 'sell_unit') { block.params.index = ''; }
  if (type === 'target_priority') { block.params.index = ''; block.params.priority = 'Boss'; }
  if (type === 'detect') {
    Object.assign(block, {
      image: '', advanced: false, mode: 'single', images: [], logic: 'and',
      expr: '', region: null, threshold: null, showAll: false,
      loop: false, loopAttempts: 0, loopIntervalMs: 1000,
      then: [], else: [],
    });
  }
  const list = resolveContainer(key);
  if (!list) return;
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
  const b = loc.container[loc.idx];
  if (b.type === 'walk_path' && loc.phase === 'prestart'
      && creationPhases.prestart.filter(x => x.type === 'walk_path').length <= 1) {
    return;
  }
  const el = document.querySelector(`#creation-phases .block-row[data-id="${id}"]`);
  const drop = () => {
    // Re-resolve by id rather than reusing loc.idx from 180ms ago. The row
    // stays in the DOM for the whole exit animation -- that is the point of
    // the delay -- so its X button is still clickable, and any other removal
    // in that window shifts every index after it. With the stale index this
    // deleted whichever block had moved into that slot: double-clicking one
    // block's X destroyed the block after it too, and removing two blocks
    // quickly removed one the user never touched. The sibling removeTaskCard
    // already filters by id for exactly this reason.
    const cur = findBlockLocation(id);
    if (!cur) return;                       // already gone -- nothing to do
    cur.container.splice(cur.idx, 1);
    renderPhases();
  };
  // Let the exit animation play before the row actually disappears. 180ms is
  // the .block-row opacity/transform transition in style.css; it used to be
  // 170, which cut the fade off 10ms early.
  if (el) { el.classList.add('removing'); setTimeout(drop, 180); } else drop();
}

// Duplicates a block right below itself, params and modifiers included --
// for repeating a nearly-identical step without re-picking everything.
function cloneBlock(id) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const copy = deepCloneBlock(loc.block);
  enteringBlockIds.add(copy.id);
  loc.container.splice(loc.idx + 1, 0, copy);
  renderPhases();
}

// A fresh-id copy of a block. Detect blocks recurse into then/else so the
// clone's nested blocks get their own ids too (sharing the arrays would make
// edits to one copy silently change the other).
function deepCloneBlock(src) {
  const copy = { ...src, id: newBlockId(), params: { ...src.params } };
  if (src.type === 'detect') {
    copy.images = [...(src.images || [])];
    copy.region = src.region ? { ...src.region } : null;
    copy.then = (src.then || []).map(deepCloneBlock);
    copy.else = (src.else || []).map(deepCloneBlock);
  }
  return copy;
}

function updateBlockParam(id, key, value) {
  const loc = findBlockLocation(id);
  if (loc) loc.container[loc.idx].params[key] = value;
}

// "Once" -- a block flagged this way only runs the first time the routine
// executes, even across repeats (e.g. a starter placement that shouldn't
// happen again every loop).
function toggleBlockOnce(id) {
  const loc = findBlockLocation(id);
  if (loc) loc.container[loc.idx].once = !loc.container[loc.idx].once;
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
  const block = loc.container[loc.idx];
  block.ignoreHighlight = !block.ignoreHighlight;
  renderPhases();
}

// Place Unit "Keep Placing": re-run the whole placement until unit_exist
// confirms the unit is down (see _place_unit_retrying in the runner).
function toggleRetryUntilPlaced(id) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const block = loc.container[loc.idx];
  block.retryUntilPlaced = !block.retryUntilPlaced;
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
    ? savedPaths.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('')
    : '<option value="">No saved paths</option>';
  const sel = document.getElementById('debug-path-select');
  if (sel) { const prev = sel.value; sel.innerHTML = options; sel.value = prev; }
  const defaultSel = document.getElementById('default-walk-path');
  if (defaultSel) { const prev = defaultSel.value; defaultSel.innerHTML = options; defaultSel.value = prev; }
  await loadDefaultWalkPaths();
}

async function refreshSavedRecordings() {
  try {
    savedRecordings = await pywebview.api.list_recordings();
  } catch (e) {
    savedRecordings = [];
  }
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
          <span><b>${escapeHtml(map)}</b> &rarr; ${escapeHtml(path)}</span>
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
  'event_gamemode_x', 'event_gamemode_y',
  'challenge_stage_1_x', 'challenge_stage_1_y',
  'challenge_stage_2_x', 'challenge_stage_2_y',
  'challenge_stage_3_x', 'challenge_stage_3_y',
  'expedition_difficulty_x', 'expedition_difficulty_y',
  'team_loadout_x', 'team_loadout_y', 'team_loadout_row_height',
  'team_button_x', 'team_button_y',
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

async function clearMacroCoord(prefix) {
  try {
    const result = await pywebview.api.clear_macro_coord(prefix);
    if (result && result.ok) {
      for (const suffix of ['_x', '_y']) {
        const el = document.getElementById(`coord-${prefix}${suffix}`);
        if (el) el.value = '';
      }
      addLog(`[Debug] ${prefix} coordinate override cleared -- using Auto.`);
    }
  } catch (e) {}
}

// Several coordinate keys in one atomic write (see set_macro_coords) -- the
// picker sets x/y (and row-height) together, and separate calls raced and
// lost keys. Values coerced to ints; NaN entries dropped.
async function saveMacroCoords(changes) {
  const clean = {};
  for (const [k, v] of Object.entries(changes)) {
    const n = parseInt(v);
    if (!Number.isNaN(n)) clean[k] = n;
  }
  try { await pywebview.api.set_macro_coords(clean); } catch (e) {}
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
  // Row-based points have a height companion input and become a TWO-STEP
  // pick: click the first row, then the second, and the spacing IS the row
  // height -- no more eyeballing a pixel count. Step 0 = waiting for row 1,
  // step 1 = waiting for row 2. The height key isn't uniform (stage_row ->
  // stage_row_height, but team_loadout -> team_loadout_row_height), so both
  // suffixes are probed rather than reconstructed.
  puState.coordHeightKey = [`${prefix}_height`, `${prefix}_row_height`]
    .find(k => document.getElementById(`coord-${k}`)) || null;
  puState.coordStep = puState.coordHeightKey ? 0 : null;
  puState.coordFirst = null;
  puState.coordPreview = null;
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
  document.getElementById('pu-pos-readout').textContent = puState.coordHeightKey
    ? 'Click the FIRST row (e.g. Level 1 / Act 1 / Loadout 1)'
    : (puState.markX != null ? `X ${puState.markX}, Y ${puState.markY}` : 'Not set');
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

// A recording target keeps both its owner and return screen. Macro Manager
// blocks and Auto Fuel routes share the same recorder and naming flow.
let pendingRecordingTarget = null;

function stopActiveRecording() {
  if (recordingBlockId) toggleRecordPath(recordingBlockId);
  else if (recordingFuelPathKey) toggleRecordFuelPath(recordingFuelPathKey);
  else if (recordingMacroBlockId) toggleRecordMacro(recordingMacroBlockId);
}

async function startRecordingTarget(target) {
  if (recordingBlockId || recordingFuelPathKey || recordingMacroBlockId) return;
  closeFuelPaths();
  switchScreen('dashboard');
  await new Promise(resolve => setTimeout(resolve, 200));
  try {
    const result = await pywebview.api.start_path_recording();
    if (result.ok) {
      if (target.kind === 'fuel') recordingFuelPathKey = target.pathKey;
      else recordingBlockId = target.blockId;
      const textEl = document.getElementById('rec-popout-text');
      if (textEl) textEl.textContent = 'Recording path (WASD + I/O) - timer starts on your first key';
      document.getElementById('rec-popout').style.display = 'flex';
      addLog(`[${target.kind === 'fuel' ? 'Fuel' : 'Macro Manager'}] Recording path -- walk with WASD (I/O also recorded, timer starts on your first key), then click Stop Recording.`);
    } else {
      addLog(`[Path Recorder] Couldn't start recording: ${result.reason || 'error'}`);
    }
  } catch (e) {}
  renderPhases();
  renderFuelPaths();
}

async function stopRecordingTarget(target) {
  pendingRecordingTarget = target;
  recordingBlockId = null;
  recordingFuelPathKey = null;
  document.getElementById('rec-popout').style.display = 'none';
  // Stop the physical-key poll before opening the name field, otherwise
  // typing WASD into the field would append fake movement to the route.
  let stopResult = null;
  try { stopResult = await pywebview.api.stop_path_capture(); } catch (e) {}
  renderPhases();
  switchScreen(target.returnScreen);
  if (!stopResult || !stopResult.count) {
    addLog('[Path Recorder] Nothing recorded -- no movement detected.');
    try { await pywebview.api.discard_pending_path(); } catch (e) {}
    pendingRecordingTarget = null;
    return;
  }
  const input = document.getElementById('path-name-input');
  input.value = target.suggestedName || '';
  document.getElementById('path-name-modal').style.display = 'flex';
  setTimeout(() => { input.focus(); input.select(); }, 50);
}

async function toggleRecordPath(blockId) {
  if (recordingBlockId === blockId) {
    await stopRecordingTarget({ kind: 'block', blockId, returnScreen: 'creation' });
    return;
  }
  await startRecordingTarget({ kind: 'block', blockId, returnScreen: 'creation' });
}

async function toggleRecordFuelPath(pathKey) {
  if (!FUEL_PATH_LABELS[pathKey]) return;
  if (recordingFuelPathKey === pathKey) {
    await stopRecordingTarget({
      kind: 'fuel',
      pathKey,
      returnScreen: 'resource',
      suggestedName: `Auto Fuel - ${FUEL_PATH_LABELS[pathKey]}`,
    });
    return;
  }
  await startRecordingTarget({ kind: 'fuel', pathKey, returnScreen: 'resource' });
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
      if (pendingRecordingTarget && pendingRecordingTarget.kind === 'fuel') {
        await pywebview.api.set_fuel_path(pendingRecordingTarget.pathKey, result.name);
        await refreshFuelScreen();
        addLog(`[Fuel] Saved and assigned path "${result.name}".`);
      } else {
        const blockId = pendingRecordingTarget && pendingRecordingTarget.blockId;
        const loc = blockId ? findBlockLocation(blockId) : null;
        if (loc) {
          const block = loc.container[loc.idx];
          if (block.type === 'walk_path') { block.mode = 'custom'; block.pathName = result.name; }
          else block.params.path = result.name;
        }
        addLog(`[Macro Manager] Saved path "${result.name}".`);
      }
    } else {
      addLog(`[Path Recorder] Couldn't save path: ${result.reason || 'error'}`);
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
  addLog('[Path Recorder] Recording discarded.');
  renderPhases();
}

// ---------------------------------------------------------------------------
// Record block (mouse+keyboard input recording) -- a separate flow from the
// Walk Path/Walk recorder above (own state, own naming modal) rather than a
// generalization of it, so this new recorder can't regress the existing,
// already-working WASD path recording.
// ---------------------------------------------------------------------------
async function toggleRecordMacro(blockId) {
  if (recordingMacroBlockId === blockId) {
    pendingMacroRecordingTarget = { blockId };
    recordingMacroBlockId = null;
    document.getElementById('rec-popout').style.display = 'none';
    let stopResult = null;
    try { stopResult = await pywebview.api.stop_input_capture(); } catch (e) {}
    renderPhases();
    switchScreen('creation');
    if (!stopResult || !stopResult.count) {
      addLog('[Input Recorder] Nothing recorded -- no mouse/keyboard input detected.');
      try { await pywebview.api.discard_pending_recording(); } catch (e) {}
      pendingMacroRecordingTarget = null;
      return;
    }
    const input = document.getElementById('macro-record-name-input');
    if (input) { input.value = ''; }
    document.getElementById('macro-record-name-modal').style.display = 'flex';
    setTimeout(() => { if (input) { input.focus(); } }, 50);
    return;
  }
  if (recordingBlockId || recordingFuelPathKey || recordingMacroBlockId) return;
  switchScreen('dashboard');
  await new Promise(resolve => setTimeout(resolve, 200));
  try {
    const result = await pywebview.api.start_input_recording();
    if (result.ok) {
      recordingMacroBlockId = blockId;
      const textEl = document.getElementById('rec-popout-text');
      if (textEl) textEl.textContent = 'Recording mouse + keyboard input inside the game window';
      document.getElementById('rec-popout').style.display = 'flex';
      addLog('[Macro Manager] Recording input -- act inside the Roblox window, then click Stop Recording.');
    } else {
      addLog(`[Input Recorder] Couldn't start recording: ${result.reason || 'error'}`);
    }
  } catch (e) {}
  renderPhases();
}

async function saveMacroRecordingName() {
  const input = document.getElementById('macro-record-name-input');
  const name = input ? input.value.trim() : '';
  if (!name) return;
  document.getElementById('macro-record-name-modal').style.display = 'none';
  restoreGameIfDashboard();
  try {
    const result = await pywebview.api.save_pending_recording(name);
    if (result.ok) {
      await refreshSavedRecordings();
      const blockId = pendingMacroRecordingTarget && pendingMacroRecordingTarget.blockId;
      const loc = blockId ? findBlockLocation(blockId) : null;
      if (loc) loc.container[loc.idx].params.recording = result.name;
      addLog(`[Macro Manager] Saved recording "${result.name}".`);
    } else {
      addLog(`[Input Recorder] Couldn't save recording: ${result.reason || 'error'}`);
    }
  } catch (e) {}
  pendingMacroRecordingTarget = null;
  renderPhases();
}

async function discardMacroRecording() {
  document.getElementById('macro-record-name-modal').style.display = 'none';
  restoreGameIfDashboard();
  try { await pywebview.api.discard_pending_recording(); } catch (e) {}
  pendingMacroRecordingTarget = null;
  addLog('[Input Recorder] Recording discarded.');
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
    const opts = p.options.map(o => `<option value="${escapeHtml(o)}" ${String(o) === String(b.params[p.key]) ? 'selected' : ''}>${o === 'None' ? 'None' : 'Priority ' + escapeHtml(o)}</option>`).join('');
    return `<select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', '${p.key}', this.value)">${opts}</select>`;
  }
  // Text fields (unit/target/setting names) are cramped at the default
  // 64px -- number fields (x/y/ms/wave) stay narrow since they only ever
  // hold a few digits.
  const width = p.type === 'text' ? 'width:130px;' : '';
  return `
    <input class="block-input" style="${width}" type="${p.type}" value="${escapeHtml(b.params[p.key])}" placeholder="${escapeHtml(p.placeholder)}"
           oninput="updateBlockParam('${b.id}', '${p.key}', this.value)">`;
}

// Setting block: the value control's shape follows `kind` -- a typed custom
// key spec, or an On/Off toggle -- so this can't be expressed as one of the
// static `params` field types renderParamInput handles.
function setSettingKind(id, kind) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const b = loc.container[loc.idx];
  b.kind = kind;
  b.value = kind === 'toggle' ? 'off' : '';
  renderPhases();
}

function setSettingValue(id, value) {
  const loc = findBlockLocation(id);
  if (loc) loc.container[loc.idx].value = value;
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
  if (loc) loc.container[loc.idx][field] = e.key === 'Escape' ? '' : mapKeyName(e);
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
  const name = field('Name', `<input class="block-input" style="width:120px;" type="text" value="${escapeHtml(b.params.name)}" placeholder="unit" oninput="updateBlockParam('${b.id}', 'name', this.value)">`);
  const x = field('X', `<input class="block-input" type="number" value="${b.params.x}" oninput="updateBlockParam('${b.id}', 'x', this.value)">`);
  const y = field('Y', `<input class="block-input" type="number" value="${b.params.y}" oninput="updateBlockParam('${b.id}', 'y', this.value)">`);
  const hotkey = field('Hotkey', `<button type="button" class="keybind-btn" onclick="startBlockHotkeyCapture('${b.id}', 'hotkey', this)">${b.hotkey ? b.hotkey.toUpperCase() : 'Set key'}</button>`);
  const hasPos = b.params.x || b.params.y;
  const set = field('Position', `<button type="button" class="pu-set-btn ${hasPos ? 'has-pos' : ''} tooltip-side" data-tooltip="Pick position on a map" onclick="openPlaceUnitModal('${b.id}')">${hasPos ? 'Set &#10003;' : 'Set'}</button>`);
  const ignoreHighlight = `<button type="button" class="block-mod-btn ${b.ignoreHighlight ? 'on' : ''} tooltip-side" data-tooltip="Skip the white-tile search and click the saved X/Y directly" onclick="toggleIgnoreHighlight('${b.id}')">Ignore Highlight</button>`;
  const retryUntilPlaced = `<button type="button" class="block-mod-btn ${b.retryUntilPlaced ? 'on' : ''} tooltip-side" data-tooltip="Keep re-placing until the unit is confirmed placed (needs Assets/ui/unit_exist.png)" onclick="toggleRetryUntilPlaced('${b.id}')">Keep Placing</button>`;
  return idx + name + x + y + hotkey + set + ignoreHighlight + retryUntilPlaced;
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

// Send Key block: capture a key (stored in b.key, reusing the same keybind
// capture the Place Unit hotkey uses) + an optional hold time in ms (0 = a
// quick tap). See the runner's _run_send_key_tick.
function renderSendKeyControls(b) {
  const field = (label, inner) => `
    <label class="blk-field"><span class="blk-field-label">${label}</span>${inner}</label>`;
  const key = field('Key', `<button type="button" class="keybind-btn" onclick="startBlockHotkeyCapture('${b.id}', 'key', this)">${b.key ? b.key.toUpperCase() : 'Set key'}</button>`);
  const hold = field('Hold (ms)', `<input class="block-input" type="number" min="0" style="width:70px;" value="${b.params.hold_ms ?? 0}" oninput="updateBlockParam('${b.id}', 'hold_ms', this.value)" title="0 = quick tap; higher = hold the key that long">`);
  return key + hold;
}

// Walk block: dropdown of the same recorded paths the pinned Walk Path row
// offers -- mid-battle repositioning reuses the exact same recordings --
// plus its own Record button, which drops the freshly saved path straight
// into this block's picker instead of the Walk Path row's.
function renderWalkControls(b) {
  const isRecording = recordingBlockId === b.id;
  const options = savedPaths.map(n => `<option value="${escapeHtml(n)}" ${n === b.params.path ? 'selected' : ''}>${escapeHtml(n)}</option>`).join('');
  return `
    <button type="button" class="block-mod-btn ${isRecording ? 'on' : ''}" onclick="toggleRecordPath('${b.id}')">${isRecording ? 'Stop' : 'Record'}</button>
    <select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', 'path', this.value)">
      <option value="">Pick saved path...</option>${options}
    </select>
    ${sprintToggle(b)}`;
}

// Hold Left Shift for the whole walk -- for paths that only reach their spot
// at sprint speed. Shared by both walk block types (see replay_events).
function sprintToggle(b) {
  return `<button type="button" class="block-mod-btn ${b.sprint ? 'on' : ''} tooltip-side" data-tooltip="Hold Left Shift while walking (sprint)" onclick="toggleSprint('${b.id}')">Sprint</button>`;
}

function toggleSprint(id) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const block = loc.container[loc.idx];
  block.sprint = !block.sprint;
  renderPhases();
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
    const options = savedPaths.map(n => `<option value="${escapeHtml(n)}" ${n === b.pathName ? 'selected' : ''}>${escapeHtml(n)}</option>`).join('');
    customControls = `
      <button type="button" class="block-mod-btn ${isRecording ? 'on' : ''}" onclick="toggleRecordPath('${b.id}')">${isRecording ? 'Stop' : 'Record'}</button>
      <select class="block-input" style="width:auto;" onchange="setWalkPathPath('${b.id}', this.value)"><option value="">Pick saved path...</option>${options}</select>`;
  }
  return modeSeg + customControls + sprintToggle(b);
}

function setWalkPathMode(id, mode) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  loc.container[loc.idx].mode = mode;
  renderPhases();
}

function setWalkPathPath(id, name) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  loc.container[loc.idx].pathName = name;
  renderPhases();
}

// Record block: a saved input recording (mouse+keyboard, see
// core.input_record) picked from a dropdown, same Record/Stop button
// pattern as renderWalkControls but backed by toggleRecordMacro/the
// recordingMacroBlockId state above instead of the Walk Path recorder.
function renderRecordControls(b) {
  const isRecording = recordingMacroBlockId === b.id;
  const options = savedRecordings.map(n => `<option value="${escapeHtml(n)}" ${n === b.params.recording ? 'selected' : ''}>${escapeHtml(n)}</option>`).join('');
  return `
    <button type="button" class="block-mod-btn ${isRecording ? 'on' : ''}" onclick="toggleRecordMacro('${b.id}')">${isRecording ? 'Stop' : 'Record'}</button>
    <select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', 'recording', this.value)">
      <option value="">Pick saved recording...</option>${options}
    </select>`;
}

// Every Place Unit block as {n, name}, in the same #1, #2, ... routine order
// placeUnitOrdinal() numbers rows with -- the option list for any control
// that targets an already-placed unit.
function listPlacedUnits() {
  const out = [];
  let n = 0;
  // Numbered in the same static order core.detect.flatten stamps _ordinal:
  // both phases in order, descending into each Detect's then before its else,
  // so "unit #N" means the same placement in the editor and at runtime.
  const walk = (list) => {
    for (const b of list) {
      if (b.type === 'place_unit') { n++; out.push({ n, name: b.params.name || '' }); }
      else if (b.type === 'detect') { walk(b.then || []); walk(b.else || []); }
    }
  };
  for (const phase of PHASES) walk(creationPhases[phase]);
  return out;
}

function renderUnitIndexSelect(b, key) {
  const options = listPlacedUnits().map(u => `
    <option value="${u.n}" ${String(b.params[key]) === String(u.n) ? 'selected' : ''}>#${u.n}${u.name ? ' ' + escapeHtml(u.name) : ''}</option>`).join('');
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
  const input = String(b.params.input || 'click').toLowerCase();
  const options = AUTO_UPGRADE_PRIORITIES.map(p =>
    `<option value="${p}" ${p === current ? 'selected' : ''}>${p}</option>`).join('');
  return blkField('Unit', renderUnitIndexSelect(b, 'index'))
    + blkField('Priority', `<select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', 'priority', this.value)">${options}</select>`)
    + blkField('Input', `<select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', 'input', this.value)">
        <option value="click" ${input === 'click' ? 'selected' : ''}>Click</option>
        <option value="hotkey" ${input === 'hotkey' ? 'selected' : ''}>Hotkey</option>
      </select>`);
}

// Target Priority: which placed unit (#index) + target priority mode (First, Last, Strongest, Boss, Weakest, Shielded, Fastest, None).
const TARGET_PRIORITIES = ['First', 'Last', 'Strongest', 'Boss', 'Weakest', 'Shielded', 'Fastest', 'None'];

function renderTargetPriorityControls(b) {
  const current = String(b.params.priority ?? 'Boss');
  const options = TARGET_PRIORITIES.map(p =>
    `<option value="${p}" ${p === current ? 'selected' : ''}>${p}</option>`).join('');
  return blkField('Unit', renderUnitIndexSelect(b, 'index'))
    + blkField('Target', `<select class="block-input" style="width:auto;" onchange="updateBlockParam('${b.id}', 'priority', this.value)">${options}</select>`);
}

// `key` is the container key the block lives in (a phase, or a Detect branch
// -- see findBlockLocation), threaded through every drag/drop handler so a row
// knows which list it belongs to.
function renderBlockRow(b, key) {
  const def = BLOCK_TYPES[b.type];
  const phase = containerPhase(key);
  if (b.type === 'detect') return renderDetectRow(b, key);
  // place_unit and click render ALL their fields bespoke (labeled X/Y +
  // the Set picker button) -- the generic anonymous param inputs would
  // duplicate them.
  const inputs = (b.type === 'place_unit' || b.type === 'click' || b.type === 'send_key')
    ? '' : def.params.map(p => renderParamInput(b, p)).join('');
  const extra = b.type === 'setting_change' ? renderSettingControls(b)
    : b.type === 'place_unit' ? renderPlaceUnitControls(b)
    : b.type === 'click' ? renderClickControls(b)
    : b.type === 'send_key' ? renderSendKeyControls(b)
    : b.type === 'walk' ? renderWalkControls(b)
    : b.type === 'walk_path' ? renderWalkPathControls(b)
    : b.type === 'record' ? renderRecordControls(b)
    : b.type === 'upgrade_unit' ? renderUpgradeControls(b)
    : b.type === 'auto_upgrade_unit' ? renderAutoUpgradeControls(b)
    : b.type === 'sell_unit' ? renderSellUnitControls(b)
    : b.type === 'target_priority' ? renderTargetPriorityControls(b) : '';
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
         ondragover="onBlockRowDragOver(event, '${key}', '${b.id}')"
         ondrop="onBlockDrop(event, '${key}', '${b.id}')">
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
         ondragstart="event.stopPropagation(); if (['INPUT','SELECT','BUTTON','TEXTAREA'].includes(event.target.tagName)) { event.preventDefault(); return false; } event.dataTransfer.setData('block-reorder', '${b.id}')"
         ondragover="onBlockRowDragOver(event, '${key}', '${b.id}')"
         ondrop="onBlockDrop(event, '${key}', '${b.id}')">
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

// A Detect block: a header (image/condition controls + advanced panel) over
// two nested drop-zones -- Then (found) and Else (not found). Each branch is a
// real container (see findBlockLocation) so blocks drag into and out of it.
function renderDetectRow(b, key) {
  const def = BLOCK_TYPES.detect;
  const entering = enteringBlockIds.has(b.id) ? ' entering' : '';
  return `
    <div class="block-row block-detect${entering}" style="--blk: ${def.color};" draggable="true" data-id="${b.id}"
         ondragstart="event.stopPropagation(); if (['INPUT','SELECT','BUTTON','TEXTAREA'].includes(event.target.tagName)) { event.preventDefault(); return false; } event.dataTransfer.setData('block-reorder', '${b.id}')"
         ondragover="onBlockRowDragOver(event, '${key}', '${b.id}')"
         ondrop="onBlockDrop(event, '${key}', '${b.id}')">
      <div class="detect-head">
        <span class="block-drag-handle">&#8942;&#8942;</span>
        <span class="block-label">${def.label}</span>
        ${renderDetectControls(b)}
        <span class="flex-1"></span>
        <div class="block-actions">
          <span class="block-clone" onclick="cloneBlock('${b.id}')" data-tooltip="Clone">&#10697;</span>
          <span class="block-delete" onclick="removeBlock('${b.id}')" data-tooltip="Remove">&times;</span>
        </div>
      </div>
      <div class="detect-branches">
        ${renderDetectBranch(b, key, 'then', 'Then', 'found')}
        ${renderDetectBranch(b, key, 'else', 'Else', 'not found')}
      </div>
    </div>
  `;
}

function renderDetectBranch(b, key, branch, label, sub) {
  const childKey = `${key}|${b.id}|${branch}`;
  const kids = b[branch] || [];
  const body = kids.length
    ? kids.map(k => renderBlockRow(k, childKey)).join('')
    : `<div class="detect-branch-empty">Drag blocks here</div>`;
  return `
    <div class="detect-branch detect-branch-${branch}">
      <div class="detect-branch-label">${label} <span class="detect-branch-sub">${sub}</span></div>
      <div id="creation-canvas-${childKey}" class="canvas-dropzone detect-dropzone"
           ondragover="onCanvasDragOver(event, '${childKey}')"
           ondragleave="onCanvasDragLeave(event, '${childKey}')"
           ondrop="onCanvasDrop(event, '${childKey}')">${body}</div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Detect block controls (header)
// ---------------------------------------------------------------------------
function detectBlock(id) {
  const loc = findBlockLocation(id);
  return loc && loc.block.type === 'detect' ? loc.block : null;
}

function renderDetectControls(b) {
  const testBtn = `<button type="button" class="block-mod-btn detect-test-btn" onclick="testDetect('${b.id}', this)" title="Capture the current Roblox window and test this Detect block without running its branches">Test now</button>`;
  const advBtn = `<button type="button" class="block-mod-btn ${b.advanced ? 'on' : ''}" onclick="toggleDetectAdvanced('${b.id}')" title="Multiple images, a search region, a match threshold, or a raw condition">Advanced</button>`;
  if (!b.advanced) return `<div class="detect-controls">${renderDetectImagePick(b)}${testBtn}${advBtn}</div>`;
  return `<div class="detect-controls">${testBtn}${advBtn}</div>${renderDetectAdvanced(b)}`;
}

function renderDetectImagePick(b) {
  const label = b.image ? escapeHtml(b.image) : 'Pick image…';
  return `<button type="button" class="blk-btn detect-pick ${b.image ? '' : 'unset'}" onclick="openDetectImagePicker('${b.id}')">
    <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
    <span>${label}</span></button>`;
}

const DETECT_MODES = [['single', 'One image'], ['multi', 'Many images'], ['expr', 'Condition']];

function renderDetectAdvanced(b) {
  const modeSeg = DETECT_MODES.map(([m, lbl]) =>
    `<button type="button" class="seg-btn ${b.mode === m ? 'active' : ''}" onclick="setDetectMode('${b.id}', '${m}')">${lbl}</button>`).join('');
  let cond = '';
  if (b.mode === 'multi') cond = renderDetectMulti(b);
  else if (b.mode === 'expr') cond = renderDetectExpr(b);
  else cond = blkField('Image', renderDetectImagePick(b));
  const region = b.region
    ? `${b.region.x}, ${b.region.y} · ${b.region.w}×${b.region.h}`
    : 'Whole screen';
  const regionRow = blkField('Region', `<span class="detect-region-val">${region}</span>
    <button type="button" class="blk-btn" onclick="openDetectRegionPicker('${b.id}')">Set</button>
    ${b.region ? `<button type="button" class="blk-btn" onclick="clearDetectRegion('${b.id}')">Clear</button>` : ''}`);
  const pct = Math.round((b.threshold == null ? 0.90 : b.threshold) * 100);
  const thrRow = blkField('Threshold', `
    <input type="range" min="50" max="100" value="${pct}" class="detect-thr-slider"
           oninput="updateDetectThresholdLive('${b.id}', this.value)"
           onchange="setDetectThreshold('${b.id}', this.value / 100)">
    <span class="detect-thr-val" id="detect-thr-${b.id}">${b.threshold == null ? 'default' : pct + '%'}</span>
    ${b.threshold == null ? '' : `<button type="button" class="blk-btn" onclick="clearDetectThreshold('${b.id}')">Default</button>`}`);
  const showAll = `<label class="detect-check"><input type="checkbox" ${b.showAll ? 'checked' : ''} onchange="toggleDetectShowAll('${b.id}')"> Log every match location</label>`;
  const loopToggle = `<label class="detect-check"><input type="checkbox" ${b.loop ? 'checked' : ''} onchange="toggleDetectLoop('${b.id}')"> Until found</label>`;
  const loopOptions = b.loop ? `
    <div class="detect-loop-settings">
      ${blkField('Max searches', `<input class="block-input" type="number" min="0" step="1" value="${Math.max(0, Number(b.loopAttempts) || 0)}" oninput="updateDetectLoopAttempts('${b.id}', this.value)">`)}
      ${blkField('Retry every', `<input class="block-input" type="number" min="100" max="60000" step="100" value="${Math.max(100, Number(b.loopIntervalMs) || 1000)}" oninput="updateDetectLoopInterval('${b.id}', this.value)"> <span class="detect-hint">ms</span>`)}
    </div>` : `<span class="detect-hint">Polls this condition until it is found.</span>`;
  const loopBox = `
    <div class="detect-loop-box ${b.loop ? 'on' : ''}">
      <div class="detect-loop-head"><span class="detect-loop-title">Loop</span>${loopToggle}</div>
      ${loopOptions}
      <span class="detect-hint detect-loop-help">0 searches = unlimited. After a limit, Else runs. Then runs once per match.</span>
    </div>`;
  return `<div class="detect-advanced">
    <div class="detect-adv-seg">${modeSeg}</div>
    ${cond}${regionRow}${thrRow}
    <div class="detect-adv-row">${showAll}</div>
    ${loopBox}
  </div>`;
}

function renderDetectMulti(b) {
  const chips = (b.images || []).map((n, i) =>
    `<span class="detect-img-chip">${escapeHtml(n)}<span class="chip-x" onclick="removeDetectImage('${b.id}', ${i})" title="Remove">&times;</span></span>`).join('');
  const add = `<button type="button" class="blk-btn" onclick="openDetectImagePicker('${b.id}')">+ image</button>`;
  const logic = ['and', 'or'].map(l =>
    `<button type="button" class="seg-btn ${(b.logic || 'and') === l ? 'active' : ''}" onclick="setDetectLogic('${b.id}', '${l}')">${l.toUpperCase()}</button>`).join('');
  return blkField('Images', `<div class="detect-img-list">${chips || '<span class="detect-hint">none yet</span>'}${add}</div>`)
    + blkField('Match', `<div class="detect-adv-seg">${logic}</div><span class="detect-hint">${(b.logic || 'and') === 'and' ? 'all must be found' : 'any one found'}</span>`);
}

function renderDetectExpr(b) {
  return blkField('Condition', `
    <textarea class="block-input detect-expr" rows="2" placeholder="find('boss') and not find('shield')"
              oninput="updateDetectExpr('${b.id}', this.value)">${escapeHtml(b.expr || '')}</textarea>
    <span class="detect-hint">Use <code>find('name')</code> and <code>count('name')</code> with <code>and</code>, <code>or</code>, <code>not</code>, and comparisons.</span>`);
}

function closeDetectTest() {
  const modal = document.getElementById('detect-test-modal');
  if (!modal) return;
  modal.style.display = 'none';
  const image = document.getElementById('detect-test-image');
  if (image) image.removeAttribute('src');
}

function renderDetectTestResult(result) {
  const status = document.getElementById('detect-test-status');
  const meta = document.getElementById('detect-test-meta');
  const details = document.getElementById('detect-test-details');
  const image = document.getElementById('detect-test-image');
  if (!status || !meta || !details || !image) return;

  const found = Boolean(result.found);
  status.className = `detect-test-status ${found ? 'found' : 'missed'}`;
  status.textContent = found ? 'FOUND — Then branch would run' : 'NOT FOUND — Else branch would run';
  const r = result.region;
  meta.textContent = r
    ? `Full Roblox window · search region ${r.x}, ${r.y} · ${r.w}×${r.h}`
    : 'Full Roblox window · whole-window search';
  image.src = result.data_uri;
  image.alt = 'Detect test capture with search and match boxes';

  const rows = (result.details || []).map(detail => {
    const missing = detail.error === 'missing';
    const matched = Boolean(detail.matched);
    const state = missing ? 'MISSING' : matched ? 'FOUND' : 'MISS';
    const score = detail.score == null ? 'no candidate' : `best ${Number(detail.score).toFixed(2)} / required ${Number(detail.threshold).toFixed(2)}`;
    const best = detail.best_match;
    const location = best ? ` · best at (${best.cx}, ${best.cy})` : '';
    const count = detail.matches && detail.matches.length > 1 ? ` · ${detail.matches.length} matches` : '';
    return `<div class="detect-test-detail ${missing ? 'missing' : matched ? 'found' : 'missed'}">
      <span class="detect-test-detail-name">${escapeHtml(detail.name)}</span>
      <span class="detect-test-detail-state">${state}</span>
      <span class="detect-test-detail-score">${score}${location}${count}</span>
    </div>`;
  }).join('');
  details.innerHTML = rows || '<div class="detect-test-empty">This condition has no image to test yet.</div>';
}

async function testDetect(id, btn) {
  const block = detectBlock(id);
  if (!block || !window.pywebview || !pywebview.api) return;
  const original = btn ? btn.textContent : 'Test now';
  if (btn) { btn.disabled = true; btn.textContent = 'Testing…'; }
  let result = null;
  try {
    result = await pywebview.api.debug_test_detect(JSON.parse(JSON.stringify(block)));
  } catch (e) {
    addLog('[Debug] Detect test failed -- is Roblox attached?');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = original; }
  }
  if (!result || !result.ok) {
    const reason = result && result.reason === 'no_roblox'
      ? 'Roblox is not attached.'
      : `Detect test failed${result && result.reason ? `: ${result.reason}` : '.'}`;
    addLog(`[Debug] ${reason}`);
    return;
  }
  // Native Roblox paints over DOM on the Dashboard. Move to Settings before
  // showing the result, just like Image Manager does, while the backend test
  // itself remains a read-only window capture.
  if (currentScreen === 'dashboard') switchScreen('settings');
  renderDetectTestResult(result);
  const modal = document.getElementById('detect-test-modal');
  if (modal) modal.style.display = 'flex';
}

function toggleDetectAdvanced(id) {
  const b = detectBlock(id); if (!b) return;
  b.advanced = !b.advanced;
  if (!b.advanced) b.mode = 'single';  // basic mode is always single-image
  renderPhases();
}
function setDetectMode(id, mode) { const b = detectBlock(id); if (b) { b.mode = mode; renderPhases(); } }
function setDetectLogic(id, logic) { const b = detectBlock(id); if (b) { b.logic = logic; renderPhases(); } }
function toggleDetectShowAll(id) { const b = detectBlock(id); if (b) { b.showAll = !b.showAll; renderPhases(); } }
function toggleDetectLoop(id) { const b = detectBlock(id); if (b) { b.loop = !b.loop; renderPhases(); } }
function updateDetectLoopAttempts(id, val) {
  const b = detectBlock(id); if (b) b.loopAttempts = Math.max(0, Math.floor(Number(val) || 0));
}
function updateDetectLoopInterval(id, val) {
  const b = detectBlock(id); if (b) b.loopIntervalMs = Math.max(100, Math.min(60000, Math.floor(Number(val) || 1000)));
}
function removeDetectImage(id, i) { const b = detectBlock(id); if (b) { (b.images || []).splice(i, 1); renderPhases(); } }
function updateDetectExpr(id, val) { const b = detectBlock(id); if (b) b.expr = val; }  // no re-render -- keep textarea focus
function clearDetectRegion(id) { const b = detectBlock(id); if (b) { b.region = null; renderPhases(); } }
function clearDetectThreshold(id) { const b = detectBlock(id); if (b) { b.threshold = null; renderPhases(); } }
function setDetectThreshold(id, val) {
  const b = detectBlock(id); if (!b) return;
  b.threshold = val == null ? null : Math.max(0.5, Math.min(1, Number(val)));
  renderPhases();
}
function updateDetectThresholdLive(id, val) {  // slider drag: update label only, no re-render
  const b = detectBlock(id); if (!b) return;
  b.threshold = Math.max(0.5, Math.min(1, Number(val) / 100));
  const lab = document.getElementById('detect-thr-' + id);
  if (lab) lab.textContent = Math.round(Number(val)) + '%';
}

// Image + region pickers reuse the Image Manager's capture/crop canvas.
let detectPickTarget = null;    // { blockId, multi } while picking an image
let detectRegionTarget = null;  // blockId while picking a region

function openDetectImagePicker(id) {
  const b = detectBlock(id); if (!b) return;
  detectPickTarget = { blockId: id, multi: b.mode === 'multi' };
  detectRegionTarget = null;
  imState.saveCategory = 'detect';  // new crops saved here land in Detection Images
  openImageManager();
}

function useDetectImage(name) {
  if (!detectPickTarget) return;
  const b = detectBlock(detectPickTarget.blockId);
  if (!b) { detectPickTarget = null; return; }
  if (detectPickTarget.multi) {
    b.images = b.images || [];
    if (!b.images.includes(name)) b.images.push(name);
    renderPhases();
    refreshImageManagerData();  // stay open -- multi usually adds several
  } else {
    b.image = name;
    detectPickTarget = null;
    closeImageManager();
    renderPhases();
  }
}

function openDetectRegionPicker(id) {
  const b = detectBlock(id); if (!b) return;
  detectRegionTarget = id;
  detectPickTarget = null;
  imState.saveCategory = 'detect';
  openImageManager();
  addLog('[Detect] Capture the screen, drag a box around the search area, then click "Use as region".');
}

// Shows the "Use as region" button in the capture view only while a Detect
// block is waiting for a region.
function updateDetectRegionButton() {
  const btn = document.getElementById('im-use-region-btn');
  if (btn) btn.style.display = detectRegionTarget ? '' : 'none';
}

function useDetectRegion() {
  if (!detectRegionTarget || !imState.sel || !imState.naturalW || !imState.naturalH) {
    addLog('[Detect] Capture the screen and drag a box first, then click "Use as region".');
    return;
  }
  const b = detectBlock(detectRegionTarget);
  if (!b) { detectRegionTarget = null; return; }
  // imState.sel is in capture-image pixels; vision regions are in the fixed
  // 1152x756 client reference space (see core.vision region usage), so
  // normalize before storing.
  const sx = 1152 / imState.naturalW, sy = 756 / imState.naturalH;
  const s = imState.sel;
  b.region = {
    x: Math.round(s.x * sx), y: Math.round(s.y * sy),
    w: Math.round(s.w * sx), h: Math.round(s.h * sy),
  };
  detectRegionTarget = null;
  closeImageManager();
  renderPhases();
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
  // Two-step row pick (see openCoordPicker): coordHeightKey is the row-height
  // setting key when the target has one, coordStep is 0 (awaiting row 1) or
  // 1 (awaiting row 2), coordFirst is row 1's point, coordPreview the derived
  // row markers.
  coordHeightKey: null, coordStep: null, coordFirst: null, coordPreview: null,
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

let puRequestId = 0;

async function openPlaceUnitModal(blockId) {
  const reqId = ++puRequestId;
  const loc = findBlockLocation(blockId);
  if (!loc) return;
  const b = loc.container[loc.idx];
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
  if (reqId !== puRequestId) return;
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
    if (reqId !== puRequestId) return;
    if (puState.maps.includes(recent.name)) {
      await selectPlaceUnitMap(recent.name, reqId);
      return;
    }
  }
  await selectPlaceUnitCategory(puState.categories[0], reqId);
}

function closePlaceUnitModal() {
  ++puRequestId;
  document.getElementById('pu-modal').style.display = 'none';
  puState.blockId = null;
  puState.coordTarget = null;
  puState.coordHeightKey = null;
  puState.coordStep = null;
  puState.coordFirst = null;
  puState.coordPreview = null;
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

async function selectPlaceUnitCategory(category, expectedReqId) {
  const reqId = expectedReqId || ++puRequestId;
  puState.category = category;
  renderPlaceUnitCategoryTabs();
  document.getElementById('pu-canvas-wrap').style.display = 'none';
  document.getElementById('pu-map-grid').style.display = '';
  try {
    puState.maps = await pywebview.api.list_maps(category);
  } catch (e) {
    puState.maps = [];
  }
  if (reqId !== puRequestId) return;
  renderPlaceUnitMapGrid(reqId);
}

// Built via DOM calls (not innerHTML + inline onclick) so map names with
// apostrophes ("King's Tomb") don't need attribute-quote escaping.
function renderPlaceUnitMapGrid(expectedReqId) {
  const reqId = expectedReqId || puRequestId;
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
      if (reqId !== puRequestId) return;
      if (result && result.ok) img.src = result.data_uri;
    }).catch(() => {});
  }
}

async function selectPlaceUnitMap(name, expectedReqId) {
  const reqId = expectedReqId || ++puRequestId;
  try {
    const result = await pywebview.api.get_map_image(puState.category, name);
    if (reqId !== puRequestId) return;
    if (!result.ok) { addLog(`[Macro Manager] Couldn't load map "${name}".`); return; }
    loadPlaceUnitImage(result.data_uri, reqId);
    setRecentPlaceUnitMap(puState.category, name);
  } catch (e) {}
}

// Same dance as saveDebugScreenshot()/readRewards(): the game is hidden and
// not rendering anywhere except the Dashboard, so switch there first, let it
// settle and paint a real frame, capture, then come straight back. The modal
// stays open the whole time (the game just paints over it for a moment).
async function usePlaceUnitRobloxScreen() {
  const reqId = ++puRequestId;
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
  if (reqId !== puRequestId) return false;
  if (!result || !result.ok) {
    addLog(`[Macro Manager] Couldn't capture Roblox screen: ${(result && result.reason) || 'error'}`);
    return false;
  }
  loadPlaceUnitImage(result.data_uri, reqId);
  return true;
}

function loadPlaceUnitImage(dataUri, expectedReqId) {
  const reqId = expectedReqId || puRequestId;
  const img = new Image();
  img.onload = () => {
    if (reqId !== puRequestId) return;
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
  ++puRequestId;
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

  // Two-step row pick: teal dots at every derived row (base + n*height),
  // numbered, so the computed spacing is visibly checkable against the real
  // rows before you trust it.
  if (puState.coordPreview) {
    for (const r of puState.coordPreview) {
      const sx = puState.panX + r.x * puState.zoom;
      const sy = puState.panY + r.y * puState.zoom;
      ctx.beginPath();
      ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#45c9b5';
      ctx.strokeStyle = 'rgba(0,0,0,0.65)';
      ctx.lineWidth = 1.5;
      ctx.fill();
      ctx.stroke();
      ctx.font = '600 11px Inter, "Segoe UI", sans-serif';
      const tw = ctx.measureText(r.label).width;
      ctx.fillStyle = 'rgba(8,10,18,0.78)';
      ctx.fillRect(sx + 8, sy - 9, tw + 10, 18);
      ctx.fillStyle = '#45c9b5';
      ctx.fillText(r.label, sx + 13, sy + 4);
    }
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
    const readout = document.getElementById('pu-pos-readout');

    if (puState.coordHeightKey) {
      // Two-step row pick. Step 0 records the base point (row 1) and
      // prompts for row 2; step 1 derives the row height from the gap.
      const xEl = document.getElementById(`coord-${p}_x`);
      const yEl = document.getElementById(`coord-${p}_y`);
      if (puState.coordStep === 0) {
        puState.coordFirst = { x: puState.markX, y: puState.markY };
        if (xEl) xEl.value = puState.markX;
        if (yEl) yEl.value = puState.markY;
        // Bulk atomic save -- x and y in one write, no race (see set_macro_coords).
        saveMacroCoords({ [`${p}_x`]: puState.markX, [`${p}_y`]: puState.markY });
        puState.coordStep = 1;
        readout.textContent = `Row 1 set (X ${puState.markX}, Y ${puState.markY}). Now click the SECOND row down.`;
        return;
      }
      // Step 1: height = vertical gap; base stays row 1 (already saved).
      const h = Math.abs(puState.markY - puState.coordFirst.y);
      const hEl = document.getElementById(`coord-${puState.coordHeightKey}`);
      if (hEl) hEl.value = h;
      saveMacroCoords({ [puState.coordHeightKey]: h });
      // Preview every derived row so the math is visible before you trust it.
      const rows = [];
      for (let i = 0; i < 7; i++) rows.push({ x: puState.coordFirst.x, y: puState.coordFirst.y + i * h, label: `${i + 1}` });
      puState.coordPreview = rows;
      puState.coordStep = 0;  // click again to redo from row 1
      readout.textContent = `Rows set: base (X ${puState.coordFirst.x}, Y ${puState.coordFirst.y}), row height ${h}px. Click row 1 again to redo.`;
      return;
    }

    const xEl = document.getElementById(`coord-${p}_x`);
    const yEl = document.getElementById(`coord-${p}_y`);
    if (xEl) xEl.value = puState.markX;
    if (yEl) yEl.value = puState.markY;
    saveMacroCoords({ [`${p}_x`]: puState.markX, [`${p}_y`]: puState.markY });
    readout.textContent = `X ${puState.markX}, Y ${puState.markY}`;
    return;
  }
  if (!puState.blockId) return;
  const loc = findBlockLocation(puState.blockId);
  if (!loc) return;
  const b = loc.container[loc.idx];
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
  category: 'ui',   // default folder for a manually-typed new name (see categoryForName)
  saveCategory: null,  // set when a card's "+" is used -- locks the save to THAT card's folder
  image: null, naturalW: 0, naturalH: 0,   // the frozen capture (an <img>, drawn to canvas)
  zoom: 1, panX: 0, panY: 0,               // canvas view transform (image px -> canvas px)
  sel: null,        // crop box in IMAGE pixels {x, y, w, h} -- null until a drag happens
};

// One-line "what is this for" shown under each Image Manager card, keyed by
// the folder name the macro searches. The UI and Maps folders were split into
// two tabs and people kept adding crops under the wrong one -- now it's one
// combined list where every card says what it's for and carries its own
// folder, so there's no tab to pick wrong. Names not listed here just show no
// description (e.g. a brand-new template someone added by hand).
const IMAGE_DESCRIPTIONS = {
  cannot_place: "Shown when a unit-placement spot is invalid (can't place here).",
  chal_enter: "The Challenge mode Enter/Join button.",
  chal_select: "The Challenge mode select button.",
  challenge: "The Challenge card on the Play menu.",
  challenge_loaded: "Confirms the Challenge screen finished loading.",
  click_anywhere_to_close: "The Spirit City Act 3 Raid popup. Click-text, sword, Lvl 1, and 8th Sword image variants are all fallback signals for dismissing it.",
  confirm: "The Confirm button -- e.g. confirming a Team Loadout.",
  continue_2: "The smaller second 'Continue' button in Expedition wave transitions.",
  defeat: "The Defeat result screen -- how the macro knows a run was lost.",
  enter_matchmaking: "The 'Enter Matchmaking' button (Story/Raid/Event).",
  event_gamemode: "The Event's gamemode card, clicked after the lobby Event button.",
  exclude: "The Exclude-equipment option in the Team Loadout panel.",
  exp_continue: "Expedition's 'Continue' button at a wave checkpoint.",
  exp_enter_matchmaking: "Expedition's Enter Matchmaking button.",
  exp_extract: "Expedition's 'Extract' choice at a checkpoint.",
  exp_extract_continue: "The 'Continue' choice shown next to Extract in Expedition.",
  exp_select_stage: "Expedition's Select Stage confirm button.",
  expedition: "The Expedition card on the Play menu.",
  expedition_flower_forest: "The Flower Forest map in Expedition's map picker.",
  expedition_rose_kingdom: "The Rose Kingdom map in Expedition's map picker.",
  extract: "The Extract button (Expedition).",
  extract_confirm: "The confirmation dialog after choosing Extract.",
  include: "The Include-equipment option in the Team Loadout panel.",
  leave_stage: "The 'Leave Stage' button on the result screen.",
  max_placement_reached: "The 'max units placed' indicator.",
  nav_back: "The Back button used to back out of menus.",
  nav_disband: "The Disband button (leaving a party).",
  nav_event: "The lobby 'Event' button -- Event mode's own entry (not under Play).",
  nav_play: "The lobby 'Play' button -- how the macro knows it's on the lobby.",
  nav_search: "The Search button (Settings search / map search).",
  nav_select_stage: "The 'Select Stage' confirm button on the stage screen.",
  nav_settings: "The Settings (gear) button.",
  nav_settings_on: "The Settings button in its open/active state.",
  nav_start: "The Solo 'Start' button that launches the match.",
  nav_start_game: "The party leader's 'Start Game' button (matchmaking).",
  nav_start_game_confirm: "The confirmation after pressing Start Game.",
  nav_unitmanager: "The Unit Manager button -- only shows in-match, so it's how the macro confirms it teleported in.",
  not_upgradeable: "A unit's info panel when it can't be upgraded yet (not enough gold / on cooldown).",
  priority_upgrade: "The Priority / Auto-Upgrade icon on a unit's info panel.",
  raid: "The Raid card on the Play menu.",
  reconnect: "Roblox's own Reconnect/Retry disconnect prompt -- triggers a rejoin.",
  repeat_stage: "The 'Repeat Stage' button on the result screen (re-queues the same stage).",
  restart_btn: "A restart button.",
  return: "The 'Return to Lobby' confirmation after Leave Stage.",
  "select upgrade card": "The level-up 'Select an upgrade!' reward-card popup.",
  story: "The Story card on the Play menu.",
  team: "The Team Loadout panel (opened with H).",
  teleportstuck: "Legacy normal-loading reference; no longer used as a disconnect signal.",
  toggle_false: "A Settings toggle in its OFF state.",
  toggle_true: "A Settings toggle in its ON state.",
  unit_exist: "Confirms a unit was actually placed on the field.",
  upgradeable: "A unit's info panel when it CAN be upgraded.",
  victory: "The Victory result screen -- how the macro knows a run was won.",
  villian1: "Event Act 1's villain card (Solo/Matchmaking event entry).",
  villian2: "Event Act 2's villain card (Solo/Matchmaking event entry).",
  villain3: "Event Act 3's villain card -- scrolled into view if it's below the fold (Solo/Matchmaking event entry).",
  villian4: "Event Act 4's villain card (Crow - Dawn) -- scrolled into view; clicked to enter Act 4.",
  villian4_close: "Act 4's LOCKED card ('requires 1 Crow Relic', 0/1x Owned) -- means there's no relic to spend, so the Act 4 auto-divert backs out here.",
  drop_relic: "The Crow Relic reward on the Victory screen -- spotting it is what triggers a farm task's optional auto-divert to Act 4.",
  warning: "A warning popup that can block Start Game.",
};
// The map NAME is reused for two different images: a "UI" one (the map's name
// label shown in-match, used to confirm which map you're on) and a "Maps" one
// (the map's card in the Play carousel, used to pick it). Same word, different
// job -- exactly the mix-up the badges + these descriptions clear up.
const MAP_LABEL_DESC = "The map's NAME label shown in-match -- used to confirm which map you landed on (mainly Challenge).";
const MAP_CARD_DESC = "The map's card in the Play > Story/Raid carousel -- used to PICK this map.";

function describeImage(catKey, name) {
  if (catKey === 'maps') return MAP_CARD_DESC;
  if (IMAGE_DESCRIPTIONS[name]) return IMAGE_DESCRIPTIONS[name];
  // A map name living under the UI folder is the in-match name label.
  if (mapCardNames().has(name)) return MAP_LABEL_DESC;
  return '';
}

// Names that exist as map CARDS (the Maps folder) -- used both by describeImage
// and categoryForName. Derived from the live data so it needs no hardcoding.
function mapCardNames() {
  const maps = (imState.data || []).find(c => c.key === 'maps');
  return new Set((maps ? maps.names : []).map(n => n.name));
}

// Which folder a manually-typed name should save to when it wasn't started
// from a specific card's "+" (which locks the folder itself). Prefer an
// existing card: a name that only exists under Maps saves to Maps; everything
// else -- including names in BOTH folders and brand-new names -- defaults to
// UI, the folder the vast majority of searched images live in.
function categoryForName(name) {
  const cats = imState.data || [];
  const inCat = (key) => (cats.find(c => c.key === key)?.names || []).some(n => n.name === name);
  const inUi = inCat('ui');
  // A detect-only name routes to detect; a name already in detect keeps going
  // there. Otherwise fall back to the old ui/maps rule.
  if (inCat('detect') && !inUi) return 'detect';
  return (inCat('maps') && !inUi) ? 'maps' : 'ui';
}

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
  detectPickTarget = null;    // picking is only in effect while the manager is open
  detectRegionTarget = null;
  restoreGameIfDashboard();  // closed while on the Dashboard (e.g. via the F6/F4 hotkeys) -- bring the game back
}

async function refreshImageManagerData() {
  try {
    const result = await pywebview.api.list_vision_templates();
    imState.data = (result && result.ok) ? result.categories : [];
    imState.defaultThreshold = (result && result.default_threshold) || 0.90;
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
  // No more UI/Maps tabs -- both folders are shown in one combined list
  // (renderImageLibrary), each card badged with where it's used, so there's
  // no tab to pick wrong. This spot now just holds the badge legend.
  const el = document.getElementById('im-category-tabs');
  // While a Detect block is picking, the legend turns into a clear "you're
  // choosing an image/region" banner instead.
  if (detectPickTarget) {
    el.innerHTML = `<span class="im-pick-banner">Pick an image for the Detect block — click <b>Use</b> on a card${detectPickTarget.multi ? ' (add as many as you like)' : ''}, or capture a new one below.</span>`;
    return;
  }
  if (detectRegionTarget) {
    el.innerHTML = `<span class="im-pick-banner">Capturing a Detect search region — Capture Roblox, drag a box, then click <b>Use as region</b>.</span>`;
    return;
  }
  el.innerHTML =
    `<span class="im-legend">` +
    `<span class="im-badge im-badge-ui">UI</span>in-game buttons &amp; screens` +
    `<span class="im-badge im-badge-map">Map</span>map-select cards` +
    `<span class="im-badge im-badge-detect">Detect</span>Detect block images` +
    `</span>`;
}

// The save bar's name suggestions -- every existing name across BOTH folders,
// so "add a variant to something that already exists" is a pick instead of an
// exact retype (a typo'd name would silently create a NEW folder the runner
// never searches). Deduped since map names appear in both folders.
function renderImageNameDatalist() {
  const el = document.getElementById('im-name-list');
  const seen = new Set();
  const opts = [];
  for (const c of (imState.data || [])) {
    for (const n of c.names) {
      if (seen.has(n.name)) continue;
      seen.add(n.name);
      const opt = document.createElement('option');
      opt.value = n.name;
      opts.push(opt.outerHTML);
    }
  }
  el.innerHTML = opts.join('');
}

// Built via DOM calls (not innerHTML + inline onclick) so names with
// apostrophes ("King's Tomb") never need attribute-quote escaping -- same
// reasoning as renderPlaceUnitMapGrid.
function renderImageLibrary() {
  const el = document.getElementById('im-library');
  el.innerHTML = '';
  const filter = (document.getElementById('im-filter').value || '').toLowerCase();
  // One combined list of BOTH folders instead of a per-tab view -- each item
  // carries its own category so the +/delete buttons route to the right
  // folder no matter what. Sorted by name (then category) so the two entries
  // that share a map name land next to each other, their badges making the
  // difference obvious.
  const items = [];
  for (const c of (imState.data || [])) {
    for (const n of c.names) {
      if (n.name.toLowerCase().includes(filter)) items.push({ ...n, catKey: c.key, catLabel: c.label });
    }
  }
  items.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()) || a.catKey.localeCompare(b.catKey));
  if (items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'im-empty';
    empty.textContent = filter
      ? 'No names match that filter.'
      : 'No reference images yet -- use Capture Roblox to add some, or check the Assets folder exists next to the app.';
    el.appendChild(empty);
    return;
  }
  for (const n of items) {
    const catKey = n.catKey;
    const card = document.createElement('div');
    card.className = 'im-card';

    const head = document.createElement('div');
    head.className = 'im-card-head';
    // Where this image is used -- "UI" (in-game buttons/screens) or "Map"
    // (map-select carousel cards). This is the whole point of the combined
    // list: the same map name exists in both folders for different jobs.
    const badge = document.createElement('span');
    const badgeKind = catKey === 'maps' ? 'map' : catKey === 'detect' ? 'detect' : 'ui';
    badge.className = `im-badge im-badge-${badgeKind}`;
    badge.textContent = catKey === 'maps' ? 'Map' : catKey === 'detect' ? 'Detect' : 'UI';
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
    add.addEventListener('click', () => startImageCapture(n.name, catKey));
    head.appendChild(badge);
    head.appendChild(label);
    head.appendChild(count);
    head.appendChild(add);
    // When a Detect block opened the manager to pick an image, every card
    // gets a Use button that drops that name into the block.
    if (detectPickTarget) {
      const use = document.createElement('button');
      use.type = 'button';
      use.className = 'im-card-use';
      use.textContent = 'Use';
      use.title = `Use "${n.name}" in this Detect block`;
      use.addEventListener('click', () => useDetectImage(n.name));
      head.appendChild(use);
    }
    card.appendChild(head);

    // One-line "what this is for" so nobody has to guess what nav_unitmanager
    // or a given map entry actually does (see IMAGE_DESCRIPTIONS).
    const descText = describeImage(catKey, n.name);
    if (descText) {
      const desc = document.createElement('div');
      desc.className = 'im-card-desc';
      desc.textContent = descText;
      card.appendChild(desc);
    }

    // Match-sensitivity row: a slider + readout for this name's threshold.
    // Lower = looser (matches on setups where the button renders a bit
    // differently), higher = stricter (fewer false matches). Saved and
    // applied live via set_image_threshold; a Reset chip clears the
    // override back to the global default.
    const defaultT = imState.defaultThreshold ?? 0.90;
    const curT = (n.threshold ?? defaultT);
    const thr = document.createElement('div');
    thr.className = 'im-card-threshold';
    const thrLabel = document.createElement('span');
    thrLabel.className = 'im-thr-label';
    thrLabel.textContent = 'Match ≥';
    const slider = document.createElement('input');
    slider.type = 'range'; slider.min = '0.50'; slider.max = '1.00'; slider.step = '0.01';
    slider.value = curT.toFixed(2); slider.className = 'im-thr-slider';
    const val = document.createElement('span');
    val.className = 'im-thr-val';
    val.textContent = curT.toFixed(2);
    if (Math.abs(curT - defaultT) > 1e-6) val.classList.add('custom');
    const reset = document.createElement('span');
    reset.className = 'im-thr-reset';
    reset.textContent = 'default';
    reset.title = `Reset to the default (${defaultT.toFixed(2)})`;
    slider.addEventListener('input', () => {
      val.textContent = Number(slider.value).toFixed(2);
      val.classList.toggle('custom', Math.abs(Number(slider.value) - defaultT) > 1e-6);
    });
    slider.addEventListener('change', () => saveImageThreshold(n.name, Number(slider.value)));
    reset.addEventListener('click', () => {
      slider.value = defaultT.toFixed(2);
      val.textContent = defaultT.toFixed(2);
      val.classList.remove('custom');
      saveImageThreshold(n.name, defaultT);
    });
    thr.appendChild(thrLabel); thr.appendChild(slider); thr.appendChild(val); thr.appendChild(reset);
    card.appendChild(thr);

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
      del.addEventListener('click', () => deleteTemplateImage(catKey, n.name, img.file, del));
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

async function deleteTemplateImage(catKey, name, file, el) {
  if (!imDeleteArmed || imDeleteArmed.el !== el) {
    imDisarmDelete();
    el.classList.add('armed');
    imDeleteArmed = { el, timer: setTimeout(imDisarmDelete, 2500) };
    return;
  }
  imDisarmDelete();
  try {
    const result = await pywebview.api.delete_vision_template_image(catKey, name, file);
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
// Save a per-image match threshold (Image Manager slider). Updates imState
// so the value survives a re-render without a full reload, and applies live.
async function saveImageThreshold(name, value) {
  try {
    await pywebview.api.set_image_threshold(name, value);
    for (const cat of imState.data || []) {
      const entry = (cat.names || []).find(n => n.name === name);
      if (entry) entry.threshold = value;
    }
    addLog(`[Image Manager] "${name}" match sensitivity set to ${Number(value).toFixed(2)}.`);
  } catch (e) {}
}

async function startImageCapture(prefillName, catKey) {
  // catKey is set when this came from a specific card's "+" -- it locks the
  // save to THAT card's folder so a variant can never land in the wrong one.
  // The top "Capture Roblox" button passes nothing, and saveImageCrop then
  // routes a manually-typed name via categoryForName instead.
  imState.saveCategory = catKey || null;
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
    updateDetectRegionButton();  // reveal "Use as region" if a Detect block is picking one
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
  // Folder to save into: locked to the card's category when the capture was
  // started from a card's "+", otherwise routed from the typed name (see
  // categoryForName). Either way the user never has to pick a tab.
  const catKey = imState.saveCategory || categoryForName(name);
  const catLabel = (imState.data || []).find(c => c.key === catKey)?.label || catKey;
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const result = await pywebview.api.save_image_search_crop(
      catKey, name, imState.sel.x, imState.sel.y, imState.sel.w, imState.sel.h);
    if (!result.ok) {
      addLog(`[Images] Save failed: ${result.reason || 'error'}`);
      btn.textContent = 'Failed';
    } else {
      // Name the folder it went to -- a manually-typed name auto-routes, so
      // this makes a wrong guess visible instead of silent.
      addLog(`[Images] Saved "${name}" under ${catLabel}.`);
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
  // Ensure string conversion so numeric values (e.g. 2) match dropdown option strings
  const teamSel = `
    <select class="task-select" onchange="creationTeam = this.value; renderCreationLoadout()">
      ${teams.map(v => `<option value="${v}" ${v === String(creationTeam) ? 'selected' : ''}>${v === '' ? 'No Team' : 'Team ' + v}</option>`).join('')}
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
      : (phase === 'loop_a' || phase === 'loop_b')
      ? 'Drag blocks here -- this list repeats over and over during the match. Pair a Detect block with a Wait to "watch for an image, then act".'
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
          <span class="rp-head-tag" style="--rp-tag: ${phase === 'prestart' ? 'var(--teal)' : (phase === 'loop_a' || phase === 'loop_b') ? 'var(--sky)' : 'var(--rose)'}; margin-left: 2px;">${PHASE_TAGS[phase]}</span>
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
  // The zone id is `creation-canvas-<containerKey>` -- recover the key so a
  // drop on the placeholder inside a Detect branch lands in that branch.
  const prefix = 'creation-canvas-';
  const key = zone.id.startsWith(prefix) ? zone.id.slice(prefix.length) : 'battle';
  let toIdx = 0;
  for (const child of zone.children) {
    if (child === placeholder) break;
    if (child.classList && child.classList.contains('block-row')) toIdx++;
  }
  removeBlockDropPlaceholder();

  const newType = e.dataTransfer.getData('block-type');
  if (newType) { addBlock(newType, key, toIdx); return; }
  const draggedId = e.dataTransfer.getData('block-reorder');
  if (draggedId) moveBlockToContainer(draggedId, key, toIdx);
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

function onCanvasDragOver(e, key) {
  e.preventDefault();
  const zone = document.getElementById(`creation-canvas-${key}`);
  if (!zone) return;
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

function onCanvasDragLeave(e, key) {
  const zone = document.getElementById(`creation-canvas-${key}`);
  if (!zone) return;
  zone.classList.remove('drag-over');
  // relatedTarget is where the pointer moved TO -- still inside the zone
  // (e.g. onto a child row) isn't actually leaving it, just bubbling.
  if (!zone.contains(e.relatedTarget)) removeBlockDropPlaceholder();
}

function onCanvasDrop(e, key) {
  e.preventDefault();
  e.stopPropagation();  // a branch dropzone must not also trigger its parent's
  const zone = document.getElementById(`creation-canvas-${key}`);
  if (zone) zone.classList.remove('drag-over');
  removeBlockDropPlaceholder();
  const type = e.dataTransfer.getData('block-type');
  if (type) { addBlock(type, key); return; }
  const draggedId = e.dataTransfer.getData('block-reorder');
  if (draggedId) moveBlockToContainer(draggedId, key, null);
}

// Moves an existing block to another container (a phase reorder, a cross-phase
// drag, or into/out of a Detect then/else branch). The destination must allow
// the block's type (same rule addBlock enforces), and a Detect block can't be
// dropped inside one of its own branches. toIdx is computed BEFORE the source
// is removed (null = end of list).
function moveBlockToContainer(id, key, toIdx) {
  const loc = findBlockLocation(id);
  if (!loc) return;
  const b = loc.block;
  const phase = containerPhase(key);
  if (!allowedInContainer(b.type, key)) {
    addLog(`[Macro Manager] "${BLOCK_TYPES[b.type].label}" can't go in ${PHASE_LABELS[phase] || phase}.`);
    return;
  }
  // Dropping a Detect into its own then/else (directly or nested) would make
  // it contain itself -- the key carries every ancestor Detect's id, so this
  // catches it at any depth.
  if (b.type === 'detect' && key.split('|').includes(id)) return;
  const dest = resolveContainer(key);
  if (!dest) return;
  // Same-list reorder where the drop target sits AT OR AFTER the dragged
  // block's own current spot: toIdx was computed against the list before the
  // splice below removes the source, so once that removal shifts everything
  // after it back by one, toIdx has to shift down too or the insert lands one
  // slot early -- worst case, right back where it started.
  if (loc.container === dest && toIdx != null && toIdx > loc.idx) toIdx -= 1;
  loc.container.splice(loc.idx, 1);
  if (toIdx == null || toIdx === -1) dest.push(b);
  else dest.splice(toIdx, 0, b);
  renderPhases();
}

// Which block types a container accepts: a phase's own PHASE_ALLOWED for its
// top-level list; a Detect branch takes anything its owning phase's Battle-
// style set allows (everything except the pinned Walk Path).
function allowedInContainer(type, key) {
  if (key.indexOf('|') === -1) return PHASE_ALLOWED[key].includes(type);
  return type !== 'walk_path';
}

function onBlockDrop(e, key, targetId) {
  e.preventDefault();
  e.stopPropagation();
  const dropAfter = e.currentTarget.dataset.dropAfter === '1';
  removeBlockDropPlaceholder();

  const list = resolveContainer(key);
  if (!list) return;
  const newType = e.dataTransfer.getData('block-type');
  if (newType) {
    let toIdx = list.findIndex(b => b.id === targetId);
    if (toIdx !== -1 && dropAfter) toIdx += 1;
    addBlock(newType, key, toIdx === -1 ? null : toIdx);
    return;
  }

  const draggedId = e.dataTransfer.getData('block-reorder');
  if (!draggedId || draggedId === targetId) return;
  let toIdx = list.findIndex(b => b.id === targetId);
  if (toIdx !== -1 && dropAfter) toIdx += 1;
  moveBlockToContainer(draggedId, key, toIdx === -1 ? null : toIdx);
}

// No more separate top-level "walk" config -- Walk Path is a real block
// now, so its mode/pathName save as part of the block itself, same as
// every other block's own fields.
function currentCreationPayload() {
  const payload = { team: creationTeam, equipment: creationEquipment };
  PHASES.forEach(phase => { payload[phase] = creationPhases[phase].map(serializeBlock); });
  return payload;
}

// One block as it's saved to disk / shared. Detect blocks carry their extra
// fields AND recurse into then/else so nested groups round-trip.
function serializeBlock(b) {
  const out = {
    type: b.type, params: b.params, once: b.once, kind: b.kind, value: b.value, hotkey: b.hotkey,
    mode: b.mode, pathName: b.pathName, ignoreHighlight: b.ignoreHighlight, retryUntilPlaced: b.retryUntilPlaced,
    sprint: b.sprint, key: b.key,
  };
  if (b.type === 'detect') {
    out.image = b.image || '';
    out.advanced = !!b.advanced;
    out.images = [...(b.images || [])];
    out.logic = b.logic === 'or' ? 'or' : 'and';
    out.expr = b.expr || '';
    out.region = b.region ? { ...b.region } : null;
    out.threshold = typeof b.threshold === 'number' ? b.threshold : null;
    out.showAll = !!b.showAll;
    out.loop = !!b.loop;
    out.loopAttempts = Math.max(0, Math.floor(Number(b.loopAttempts) || 0));
    out.loopIntervalMs = Math.max(100, Math.min(60000, Math.floor(Number(b.loopIntervalMs) || 1000)));
    out.then = (b.then || []).map(serializeBlock);
    out.else = (b.else || []).map(serializeBlock);
  }
  return out;
}

// What the editor held the last time it was in sync with disk (saved, loaded
// or reset). Compared against, rather than a dirty flag set from every input
// handler, so edit-then-undo doesn't leave a false "unsaved" warning -- and
// so nothing has to remember to set the flag when a new control is added.
let creationSavedSnapshot = null;

function currentCreationSnapshot() {
  const name = document.getElementById('template-name')?.value.trim() || '';
  return JSON.stringify({ name, blocks: currentCreationPayload() });
}

function markCreationEditorSaved() {
  creationSavedSnapshot = currentCreationSnapshot();
}

// null until the editor has been in a known state once: with no baseline
// there is nothing to compare against, and warning then would fire on a
// fresh, empty editor.
function creationEditorHasUnsavedChanges() {
  return creationSavedSnapshot != null && currentCreationSnapshot() !== creationSavedSnapshot;
}

async function saveCurrentTemplate() {
  const nameInput = document.getElementById('template-name');
  const name = nameInput.value.trim();
  if (!name) return;
  try {
    const result = await pywebview.api.save_template(name, currentCreationPayload());
    addLog(`Saved template "${result.name}".`);
    markCreationEditorSaved();
    refreshTemplateList();
  } catch (e) {}
}

// Resets the editor to a blank routine -- same defaults renderPhases()
// already assumes on first load, just re-applied on demand so starting a
// new template doesn't require manually clearing out whatever was loaded.
function newTemplate() {
  // The unique pinned Auto Walk Path block (see renderBlockRow's
  // isPinnedWalk) -- always there, Once always on, still reorderable.
  creationPhases = { prestart: [{ id: newBlockId(), type: 'walk_path', params: {}, once: true, mode: 'auto', pathName: '' }], battle: [], loop_a: [], loop_b: [] };
  creationTeam = '';
  creationEquipment = 'include';
  document.getElementById('template-name').value = '';
  document.getElementById('template-select').value = '';
  creationFreshLoad = true;
  renderPhases();
  renderCreationLoadout();
  markCreationEditorSaved();
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
  const paths = await exportCustomPaths(templates);
  const recordings = await exportCustomRecordings(templates);
  const payload = {
    kind: 'anime-expeditions-templates', version: 2, exported: new Date().toISOString(),
    templates, paths, recordings,
  };
  let result = null;
  let errMsg = null;
  try { result = await pywebview.api.export_tasks_file(payload, 'templates'); } catch (e) { errMsg = (e && e.message) || String(e); }
  if (result && result.ok) addLog(`[Macro Manager] Exported ${names.length} template(s) to ${result.path}`);
  else if (!result) addLog(`[Macro Manager] Export failed: the save dialog could not be opened${errMsg ? ` (${errMsg})` : ''}.`);
  else if (result.reason === 'cancelled') addLog('[Macro Manager] Export cancelled.');
  else addLog(`[Macro Manager] Export failed: ${result.reason || 'error'}`);
}

async function importTemplates() {
  let result = null;
  let errMsg = null;
  try { result = await pywebview.api.import_tasks_file('templates'); }
  catch (e) { errMsg = (e && e.message) || String(e); }
  if (!result || !result.ok) {
    if (!result) {
      addLog(`[Macro Manager] Import failed: the file dialog could not be opened${errMsg ? ` (${errMsg})` : ''}.`);
    } else if (result.reason === 'cancelled') {
      addLog('[Macro Manager] Import cancelled.');
    } else {
      addLog(`[Macro Manager] Import failed: ${result.reason || 'error'}`);
    }
    return;
  }
  const data = result.data || {};
  const templates = data.templates && typeof data.templates === 'object' ? data.templates : null;
  if (!templates) { addLog('[Macro Manager] Import failed: that file is not a template export.'); return; }
  const entries = Object.entries(templates).filter(([, t]) => t && t.blocks != null);
  if (entries.length === 0) {
    addLog('[Macro Manager] Import failed: that file contains no macros.');
    return;
  }
  // The import loads a macro into the editor at the end, so anything
  // unsaved there is about to be replaced. Ask first.
  if (creationEditorHasUnsavedChanges() && !confirm(
      'The Macro Manager editor has unsaved changes, and importing will replace them. Continue?')) {
    addLog('[Macro Manager] Import cancelled -- your unsaved editor changes were kept.');
    return;
  }
  let existing = [];
  try { existing = await pywebview.api.list_templates(); } catch (e) {}
  // A same-name macro used to be skipped in silence: re-importing your own
  // edited export did nothing at all, and the log still said it imported
  // fine. Overwriting without asking is the other way to lose work, though
  // -- a shared pack that happens to contain "Boss Rush" would take out the
  // one you built. So ask once, for all of them, and say which is which.
  const conflicts = entries.filter(([name]) => existing.includes(name));
  const replaceExisting = conflicts.length === 0 || confirm(
    `${conflicts.length} of these already exist:\n\n`
    + conflicts.map(([name]) => `    ${name}`).join('\n')
    + '\n\nReplace them with the imported versions? '
    + 'Choose Cancel to keep yours and import only the new ones.');
  const pathAdded = await importCustomPaths(data.paths);
  const recordingAdded = await importCustomRecordings(data.recordings);
  const imported = [];
  let replaced = 0;
  for (const [name, t] of entries) {
    const isConflict = existing.includes(name);
    if (isConflict && !replaceExisting) continue;
    try {
      await pywebview.api.save_template(name, t.blocks);
      imported.push(name);
      if (isConflict) replaced++;
    } catch (e) {}
  }
  await refreshTemplateList();
  if (imported.length > 0) {
    // Open the first one. The dropdown used to refresh but keep its empty
    // selection, so even a fully successful import looked like nothing had
    // happened.
    const sel = document.getElementById('template-select');
    if (sel) {
      sel.value = imported[0];
      await loadSelectedTemplate();
    }
  }
  const kept = conflicts.length - replaced;
  addLog(`[Macro Manager] Imported ${imported.length} macro(s)`
    + `${replaced ? ` (${replaced} replaced)` : ''}`
    + `${kept ? `; kept your existing ${kept}` : ''}`
    + `${pathAdded ? `, ${pathAdded} custom path(s)` : ''}`
    + `${recordingAdded ? `, and ${recordingAdded} recording(s)` : ''}.`);
}

async function refreshTemplateList() {
  const sel = document.getElementById('template-select');
  if (!sel) return;
  try {
    const names = await pywebview.api.list_templates();
    sel.innerHTML = '<option value="">Load...</option>' + names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
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
    block.retryUntilPlaced = !!b.retryUntilPlaced;
  }
  if (b.type === 'walk_path') {
    block.mode = b.mode === 'custom' ? 'custom' : 'auto';
    block.pathName = b.pathName || '';
  }
  if (b.type === 'walk_path' || b.type === 'walk') block.sprint = !!b.sprint;
  if (b.type === 'send_key') block.key = b.key || '';
  if (b.type === 'detect') {
    block.image = b.image || '';
    block.advanced = !!b.advanced;
    block.mode = ['single', 'multi', 'expr'].includes(b.mode) ? b.mode : 'single';
    block.images = Array.isArray(b.images) ? [...b.images] : [];
    block.logic = b.logic === 'or' ? 'or' : 'and';
    block.expr = b.expr || '';
    block.region = (b.region && typeof b.region === 'object') ? { ...b.region } : null;
    block.threshold = typeof b.threshold === 'number' ? b.threshold : null;
    block.showAll = !!b.showAll;
    block.loop = !!b.loop;
    block.loopAttempts = Math.max(0, Math.floor(Number(b.loopAttempts) || 0));
    block.loopIntervalMs = Math.max(100, Math.min(60000, Math.floor(Number(b.loopIntervalMs) || 1000)));
    block.then = (Array.isArray(b.then) ? b.then : []).map(blockFromSaved);
    block.else = (Array.isArray(b.else) ? b.else : []).map(blockFromSaved);
  }
  return block;
}

async function loadSelectedTemplate() {
  const name = document.getElementById('template-select').value;
  if (!name) return;
  try {
    const data = await pywebview.api.load_template(name);
    const payload = data.blocks || {};
    creationPhases = { prestart: [], battle: [], loop_a: [], loop_b: [] };
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
    // Freshly in sync with disk -- this is the baseline the unsaved-changes
    // check compares against.
    markCreationEditorSaved();
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
    applyDpiFit();  // now that the platform is authoritative (mac must not be zoomed)
  } catch (e) {}

  try {
    const version = await pywebview.api.get_version();
    const badgeText = document.getElementById('ver-badge-text');
    if (badgeText) {
      badgeText.textContent = `v${version}`;
    } else {
      const badge = document.getElementById('ver-badge');
      if (badge) badge.textContent = `v${version}`;
    }
    const loadingVer = document.getElementById('ver-badge-loading');
    if (loadingVer) loadingVer.textContent = `v${version}`;
  } catch (e) {}
  try {
    const info = await pywebview.api.get_time_info();
    sessionStart = info.session_start;
    allTimeBase = info.all_time_base;
  } catch (e) {}

  updateTesseractButtonStatus();


  renderPalette();
  renderPhases();
  refreshSavedPaths();
  refreshSavedRecordings();
  loadSettingsUI();

  refreshTaskQueue();

  tickTimers();
  setInterval(tickTimers, 1000);
  refreshStatus();
  setInterval(refreshStatus, 1500);
});

// --- Share Code (Export / Import via Code or URL) ---
async function openShareCodeModal(initialTab = 'export') {
  const modal = document.getElementById('share-code-modal');
  if (!modal) return;
  modal.style.display = 'flex';
  try { window.pywebview && pywebview.api.hide_game(); } catch (e) {}
  switchShareTab(initialTab || 'export');
  if (initialTab === 'export') {
    updateExportCode();
  }
}

function closeShareCodeModal() {
  const modal = document.getElementById('share-code-modal');
  if (modal) modal.style.display = 'none';
  const statusEl = document.getElementById('share-import-status');
  if (statusEl) statusEl.style.display = 'none';
  const previewBox = document.getElementById('share-import-preview-box');
  if (previewBox) previewBox.style.display = 'none';
  const inputEl = document.getElementById('share-import-input');
  if (inputEl) inputEl.value = '';
  restoreGameIfDashboard();
}

function switchShareTab(tab) {
  const btnExport = document.getElementById('share-tab-export');
  const btnImport = document.getElementById('share-tab-import');
  const contentExport = document.getElementById('share-content-export');
  const contentImport = document.getElementById('share-content-import');

  if (tab === 'export') {
    btnExport.className = 'task-toolbar-btn primary';
    btnImport.className = 'task-toolbar-btn';
    contentExport.style.display = 'flex';
    contentImport.style.display = 'none';
  } else {
    btnImport.className = 'task-toolbar-btn primary';
    btnExport.className = 'task-toolbar-btn';
    contentImport.style.display = 'flex';
    contentExport.style.display = 'none';
    const inputEl = document.getElementById('share-import-input');
    if (inputEl) inputEl.focus();
  }
}

async function pasteFromClipboardPython() {
  const inputEl = document.getElementById('share-import-input');
  if (!inputEl) return;
  try {
    const res = await pywebview.api.read_clipboard_text();
    if (res && res.ok && res.text) {
      inputEl.value = res.text.trim();
      onShareImportInput();
    }
  } catch (e) {}
}

let importPreviewTimer = null;
function onShareImportInput() {
  if (importPreviewTimer) clearTimeout(importPreviewTimer);
  importPreviewTimer = setTimeout(updateImportPreview, 200);
}

async function updateImportPreview() {
  const inputEl = document.getElementById('share-import-input');
  const previewBox = document.getElementById('share-import-preview-box');
  const badgeEl = document.getElementById('share-import-preview-badge');
  const countEl = document.getElementById('share-import-preview-count');
  const itemsEl = document.getElementById('share-import-preview-items');
  const statusEl = document.getElementById('share-import-status');
  const btnSubmit = document.getElementById('btn-submit-import-share');

  if (!inputEl) return;
  const val = inputEl.value.trim();

  if (!val) {
    if (previewBox) previewBox.style.display = 'none';
    if (statusEl) statusEl.style.display = 'none';
    if (btnSubmit) btnSubmit.textContent = 'Import Now';
    return;
  }

  try {
    const res = await pywebview.api.preview_template_code(val);
    if (res && res.ok && res.items && res.items.length > 0) {
      if (statusEl) statusEl.style.display = 'none';
      if (previewBox) previewBox.style.display = 'flex';

      const isSingle = res.type === 'single';
      if (badgeEl) {
        badgeEl.textContent = isSingle ? 'Single Template' : 'Template Pack';
        badgeEl.style.background = isSingle ? 'color-mix(in srgb, var(--teal) 20%, transparent)' : 'color-mix(in srgb, var(--lilac) 20%, transparent)';
        badgeEl.style.color = isSingle ? 'var(--teal)' : 'var(--lilac)';
      }
      if (countEl) {
        const walkNote = res.walk_paths ? ` + ${res.walk_paths} walk path(s)` : '';
        const recNote = res.recordings ? ` + ${res.recordings} recording(s)` : '';
        countEl.textContent = `${res.total_templates} template(s)${walkNote}${recNote}`;
      }

      if (itemsEl) {
        itemsEl.innerHTML = res.items.map(item =>
          `<div style="display: flex; justify-content: space-between; padding: 2px 0; border-bottom: 1px dashed var(--border);">` +
            `<span><b>${item.name}</b></span>` +
            `<span style="color: var(--text-muted);">${item.blocks_count} block(s)</span>` +
          `</div>`
        ).join('');
      }

      if (btnSubmit) btnSubmit.textContent = `Import ${res.total_templates} Template(s)`;
    } else {
      if (previewBox) previewBox.style.display = 'none';
      if (statusEl) {
        statusEl.textContent = res.reason || 'Invalid code, URL, or JSON schema.';
        statusEl.style.display = 'block';
      }
      if (btnSubmit) btnSubmit.textContent = 'Import Now';
    }
  } catch (e) {
    if (previewBox) previewBox.style.display = 'none';
    if (statusEl) {
      statusEl.textContent = 'Invalid input format.';
      statusEl.style.display = 'block';
    }
    if (btnSubmit) btnSubmit.textContent = 'Import Now';
  }
}

async function onExportScopeChange() {
  const selectedScope = document.querySelector('input[name="share-export-scope"]:checked');
  const scope = selectedScope ? selectedScope.value : 'single';
  const box = document.getElementById('share-export-checklist-box');
  const itemsContainer = document.getElementById('share-export-checklist-items');

  if (scope === 'custom') {
    if (box) box.style.display = 'flex';
    if (itemsContainer) {
      itemsContainer.innerHTML = 'Loading templates...';
      try {
        const names = await pywebview.api.list_templates();
        if (names.length === 0) {
          itemsContainer.innerHTML = '<span style="font-size: 11px; color: var(--text-muted);">No saved templates found.</span>';
        } else {
          itemsContainer.innerHTML = names.map(name =>
            `<label style="font-size: 11px; cursor: pointer; display: flex; align-items: center; gap: 6px; padding: 2px 0;">` +
              `<input type="checkbox" class="share-export-check" value="${name}" checked onchange="updateExportCode()">` +
              `<span>${name}</span>` +
            `</label>`
          ).join('');
        }
      } catch (e) {
        itemsContainer.innerHTML = '<span style="font-size: 11px; color: var(--rose);">Error loading templates.</span>';
      }
    }
  } else {
    if (box) box.style.display = 'none';
  }
  updateExportCode();
}

function toggleExportAllCheckboxes(check) {
  const checkboxes = document.querySelectorAll('.share-export-check');
  checkboxes.forEach(cb => { cb.checked = check; });
  updateExportCode();
}

async function updateExportCode() {
  const outputEl = document.getElementById('share-export-code-output');
  const sizeEl = document.getElementById('share-export-size-info');
  if (!outputEl) return;

  outputEl.value = 'Generating code...';
  const selectedScope = document.querySelector('input[name="share-export-scope"]:checked');
  const scope = selectedScope ? selectedScope.value : 'single';

  let targetNames = null;
  if (scope === 'single') {
    const inputName = (document.getElementById('template-name')?.value || '').trim();
    const selectName = document.getElementById('template-select')?.value || '';
    targetNames = inputName || selectName || null;
  } else if (scope === 'custom') {
    const checked = Array.from(document.querySelectorAll('.share-export-check:checked')).map(cb => cb.value);
    targetNames = checked;
    if (checked.length === 0) {
      outputEl.value = 'Please select at least one template.';
      if (sizeEl) sizeEl.textContent = 'Size: 0 chars (0 templates)';
      return;
    }
  } else {
    targetNames = null; // all
  }

  try {
    const res = await pywebview.api.export_template_code(targetNames);
    if (res && res.ok && res.code) {
      outputEl.value = res.code;
      if (sizeEl) sizeEl.textContent = `Size: ${res.code.length} chars (${res.count} template(s))`;
    } else {
      outputEl.value = 'Failed to generate code.';
    }
  } catch (e) {
    outputEl.value = 'Error generating code.';
  }
}

async function copyShareCodeOutput() {
  const outputEl = document.getElementById('share-export-code-output');
  const btn = document.getElementById('btn-copy-share-code');
  if (!outputEl || !outputEl.value) return;

  try {
    await navigator.clipboard.writeText(outputEl.value);
    if (btn) {
      const origText = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = origText; }, 1500);
    }
  } catch (e) {
    outputEl.select();
    document.execCommand('copy');
  }
}

async function submitImportShareCode() {
  const inputEl = document.getElementById('share-import-input');
  const statusEl = document.getElementById('share-import-status');
  if (!inputEl) return;

  const rawInput = inputEl.value.trim();
  if (!rawInput) {
    if (statusEl) {
      statusEl.textContent = 'Please enter a code, JSON, or URL.';
      statusEl.style.display = 'block';
    }
    return;
  }

  if (statusEl) statusEl.style.display = 'none';

  try {
    const res = await pywebview.api.import_template_code(rawInput);
    if (res && res.ok) {
      addLog(`[Macro Manager] Imported ${res.count} template(s) via Share Code.`);
      await refreshTemplateList();
      closeShareCodeModal();
    } else {
      if (statusEl) {
        statusEl.textContent = res.reason || 'Import failed.';
        statusEl.style.display = 'block';
      }
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = `Import error: ${e.message || e}`;
      statusEl.style.display = 'block';
    }
  }
}
