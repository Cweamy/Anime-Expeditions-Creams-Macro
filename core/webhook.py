import json
import urllib.error
import urllib.parse
import urllib.request

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
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 300:
                return {"ok": True, "reason": ""}
            return {"ok": False, "reason": f"HTTP {resp.status}"}
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {"ok": False, "reason": f"HTTP {exc.code}: {body}" if body else f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "reason": str(exc)}
