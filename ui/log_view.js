// Shared Process Log view: rendering, the line cap, and scroll behaviour.
//
// Loaded by BOTH the dashboard (ui/index.html, before app.js) and the
// popped-out log window (ui/logs_window.html), which are two separate
// documents showing the same stream -- Python pushes lines into each of them
// by calling window.addLog through evaluate_js (see Api.push_log in main.py).
// This used to be duplicated verbatim in the two files; it lives here instead
// because the scroll logic below carries real state, and keeping two copies of
// that in sync by hand is exactly how one of them quietly stops matching.
//
// The behaviour that matters: the log follows the newest line ONLY while you
// are already at the bottom. Scroll up to read something and it stays put --
// previously every incoming line reset scrollTop to the bottom, which made
// reading anything during an active run impossible. A "jump to newest" pill
// appears while you are scrolled away, showing how many lines you have not
// seen, and following resumes as soon as you are back at the bottom.

const LOG_TAG_COLORS = ['var(--brand)', 'var(--teal)', 'var(--amber)', 'var(--lilac)', 'var(--rose)', 'var(--slate)'];

// Oldest lines get dropped past this: the log is a live view, not an archive
// (Python keeps its own history buffer for pop-out replay), and an
// ever-growing list makes "am I at the newest line?" ambiguous.
const LOG_MAX_LINES = 400;

// How far from the bottom still counts as "at the bottom". Not zero: a
// fractional scrollHeight (sub-pixel line heights, zoom levels -- see
// applyDpiFit) can leave scrollTop a hair short of the exact bottom even
// when the view is visually pinned there, and a strict check would then
// treat normal following as "the user scrolled away".
const LOG_STICK_SLOP_PX = 24;

let logUnreadCount = 0;

function logTagColor(tag) {
  let h = 0;
  for (let i = 0; i < tag.length; i++) h = (h * 31 + tag.charCodeAt(i)) >>> 0;
  return LOG_TAG_COLORS[h % LOG_TAG_COLORS.length];
}

// Lines that start with a "[Tag]" (e.g. "[Selector] ...", "[Theme] ...") are
// treated as categorized: the tag is hashed to a stable accent color from the
// app palette (same tag -> same color every time, tags that don't exist yet
// get one automatically), which drives both the tag text and the line's hover
// highlight via the --cat custom property (see .log-entry in style.css).
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

function logListEl() {
  return document.getElementById('log-list');
}

// A hidden element (the Dashboard's log while another screen is up) reports
// every scroll metric as 0, which lands inside the slop and reads as "at the
// bottom" -- deliberately: lines arriving off-screen should not count as
// unread, and switchScreen() snaps to the newest line on the way back in.
function logIsAtBottom(list) {
  return list.scrollHeight - list.scrollTop - list.clientHeight <= LOG_STICK_SLOP_PX;
}

function updateLogJumpBtn() {
  const btn = document.getElementById('log-jump');
  if (!btn) return;
  const count = document.getElementById('log-jump-count');
  if (logUnreadCount > 0) {
    if (count) count.textContent = logUnreadCount > 99 ? '99+' : String(logUnreadCount);
    btn.classList.add('show');
  } else {
    btn.classList.remove('show');
  }
}

function jumpLogToLatest() {
  const list = logListEl();
  if (!list) return;
  list.scrollTop = list.scrollHeight;
  logUnreadCount = 0;
  updateLogJumpBtn();
}

// Unconditional snap, for the one case where following SHOULD be forced:
// returning to the Dashboard, where the list was display:none and could not
// track scroll position at all while lines were arriving.
function logSnapToLatest() {
  jumpLogToLatest();
}

function addLog(line) {
  const list = logListEl();
  if (!list) return;
  // Decided BEFORE the line is appended -- appending grows scrollHeight, so
  // asking afterwards would report "not at the bottom" for a view that was
  // pinned a moment ago and should stay pinned.
  const following = logIsAtBottom(list);

  const div = document.createElement('div');
  div.className = 'log-entry';
  renderLogLine(div, line);
  list.appendChild(div);

  while (list.childElementCount > LOG_MAX_LINES) {
    if (following) {
      list.removeChild(list.firstElementChild);
      continue;
    }
    // Trimming from the TOP while the user is reading further down shifts
    // everything up under them, which is the same "I lost my place" problem
    // the pinning fix exists to solve. Measure what the removal actually cost
    // (element height AND the margin the space-y rule stops applying once the
    // next sibling becomes first) and pay it back to scrollTop.
    const heightBefore = list.scrollHeight;
    list.removeChild(list.firstElementChild);
    list.scrollTop = Math.max(0, list.scrollTop - (heightBefore - list.scrollHeight));
  }

  if (following) {
    list.scrollTop = list.scrollHeight;
    logUnreadCount = 0;
  } else {
    logUnreadCount++;
  }
  updateLogJumpBtn();
}

// Shared by both pages' own clearLogs(), which differ in what else they do
// (the dashboard also tells Python to drop its history buffer).
function clearLogView() {
  const list = logListEl();
  if (list) list.innerHTML = '';
  logUnreadCount = 0;
  updateLogJumpBtn();
}

// Scrolling back down to the bottom by hand resumes following and clears the
// pill, so the button never lingers once it is no longer true.
function initLogView() {
  const list = logListEl();
  if (!list) return;
  list.addEventListener('scroll', () => {
    if (!logIsAtBottom(list)) return;
    if (logUnreadCount === 0) return;
    logUnreadCount = 0;
    updateLogJumpBtn();
  }, { passive: true });
}

initLogView();
