import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import requests

# Any Discord client build (stable/canary/PTB) and both the current and
# legacy API host resolve webhooks identically -- matched by suffix instead
# of an exact host list so this doesn't need updating for every variant.
DISCORD_HOST_SUFFIXES = ("discord.com", "discordapp.com")

SUPPRESS_NOTIFICATIONS_FLAG = 4096  # Discord webhook message flag for "silent" sends

# Discord sits behind Cloudflare, which blocks urllib's default User-Agent
# ("Python-urllib/3.x") outright -- every send was failing with a 403
# ("error code: 1010", Cloudflare's own bot-block page, not a Discord API
# error) with nothing logged about it, since send() swallowed the exception
# and just returned False. Same fix as tools/fetch_item_icons.py's wiki
# requests needed for the same reason: a normal browser User-Agent clears it.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Discord rate-limits webhooks (HTTP 429) and tells you how long to wait in
# the response -- send()/send_file() honour that and retry rather than
# dropping the notification. Kept SYNCHRONOUS on purpose: the caller relies
# on the real {ok, reason} result (the Settings "Send Test" button reports
# whether the webhook actually works, and result/event sends log the failure
# reason), so a fire-and-forget queue that always returns "ok" would break
# both. The frequent send -- match results -- already runs on a background
# thread (see runner._finish_match_result_background), so the brief wait
# here doesn't stall the macro loop.
_RETRY_MAX = 3          # attempts after the first, on a 429
_RETRY_WAIT_CAP = 5.0   # never sleep longer than this per retry, whatever Discord asks


def _retry_after(source) -> float:
    """Seconds to wait before retrying a 429, from Discord's JSON
    `retry_after` (webhook API gives it in seconds) or the Retry-After
    header as a fallback. A small buffer is added and the total is capped so
    a malformed/huge value can't hang the send. `source` is a requests
    Response or a urllib HTTPError -- both expose .headers, and JSON is read
    via the matching call."""
    delay = None
    try:
        body = source.json() if hasattr(source, "json") else json.loads(
            source.read().decode("utf-8", errors="replace"))
        delay = float(body.get("retry_after"))
    except Exception:
        try:
            hdr = source.headers.get("Retry-After") if source.headers else None
            delay = float(hdr) if hdr is not None else None
        except (TypeError, ValueError):
            delay = None
    if delay is None:
        delay = 1.0
    return max(0.0, min(delay + 0.25, _RETRY_WAIT_CAP))


def validate(url: str) -> dict:
    url = (url or "").strip()
    if not url:
        return {"valid": False, "reason": "empty"}
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return {"valid": False, "reason": "bad_format"}
    if parsed.scheme != "https":
        return {"valid": False, "reason": "not_https"}

    host = parsed.netloc.lower()
    if not any(host == suffix or host.endswith("." + suffix) for suffix in DISCORD_HOST_SUFFIXES):
        return {"valid": False, "reason": "not_discord"}

    # .../api/webhooks/<id>/<token>, checked from the end so a trailing slash,
    # `?wait=true`-style query string, or an API version segment don't matter.
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4 or parts[-4] != "api" or parts[-3] != "webhooks":
        return {"valid": False, "reason": "bad_format"}
    webhook_id, token = parts[-2], parts[-1]
    if not webhook_id.isdigit() or not token:
        return {"valid": False, "reason": "bad_format"}
    return {"valid": True, "reason": "ok"}


def send(url: str, embed: dict, content: str = "", silent: bool = False) -> dict:
    """Returns {"ok": bool, "reason": str} instead of a bare bool -- a
    failed send used to disappear silently (the caller never even logged
    it), which is exactly how the Cloudflare User-Agent block above went
    unnoticed. "ok" is False for a genuine failure; "reason" is empty on
    success."""
    if not url:
        return {"ok": False, "reason": "no webhook URL configured"}
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content
    if silent:
        payload["flags"] = SUPPRESS_NOTIFICATIONS_FLAG
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    for attempt in range(_RETRY_MAX + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return {"ok": True, "reason": ""}
                return {"ok": False, "reason": f"HTTP {resp.status}"}
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < _RETRY_MAX:
                time.sleep(_retry_after(exc))
                continue  # Discord asked us to slow down -- wait and resend
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            return {"ok": False, "reason": f"HTTP {exc.code}: {body}" if body else f"HTTP {exc.code}"}
        except (urllib.error.URLError, OSError) as exc:
            return {"ok": False, "reason": str(exc)}
    return {"ok": False, "reason": "rate limited -- gave up after retries"}


def send_file(url: str, embed: dict, screenshot_path: str, content: str = "", silent: bool = False) -> dict:
    """Like send(), but attaches a screenshot -- for events worth SEEING,
    not just reading about (a stuck Start Game click, a disconnect, a task
    finally giving up). Discord's webhook endpoint only accepts a file
    alongside JSON as multipart/form-data (the payload as a "payload_json"
    field, not the request body directly), which needs actual multipart
    encoding -- urllib has no built-in support for that, hence `requests`
    here instead of send()'s plain urllib request.

    Falls back to a screenshot-less send() if the file itself can't be
    read, rather than losing the notification entirely over a missing/
    unreadable debug screenshot."""
    if not url:
        return {"ok": False, "reason": "no webhook URL configured"}
    if not screenshot_path or not os.path.isfile(screenshot_path):
        return send(url, embed, content=content, silent=silent)

    filename = os.path.basename(screenshot_path)
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content
    if silent:
        payload["flags"] = SUPPRESS_NOTIFICATIONS_FLAG
    embed["image"] = {"url": f"attachment://{filename}"}

    # Read the bytes up front (not streamed from the open handle inside the
    # request): the same bytes are reused across 429 retries, and a debug
    # screenshot getting deleted between here and the send can't race the
    # upload. If it can't be read, fall back to a text-only send rather than
    # lose the whole notification over an unreadable screenshot -- what this
    # function's docstring has always promised.
    try:
        with open(screenshot_path, "rb") as f:
            file_bytes = f.read()
    except OSError:
        return send(url, embed, content=content, silent=silent)

    data = {"payload_json": json.dumps(payload)}
    for attempt in range(_RETRY_MAX + 1):
        try:
            resp = requests.post(
                url, data=data, files={"file": (filename, file_bytes, "image/png")},
                headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException as exc:
            return {"ok": False, "reason": str(exc)}
        if 200 <= resp.status_code < 300:
            return {"ok": True, "reason": ""}
        if resp.status_code == 429 and attempt < _RETRY_MAX:
            time.sleep(_retry_after(resp))
            continue
        return {"ok": False, "reason": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    return {"ok": False, "reason": "rate limited -- gave up after retries"}
