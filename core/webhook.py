import json
import urllib.error
import urllib.parse
import urllib.request

# Any Discord client build (stable/canary/PTB) and both the current and
# legacy API host resolve webhooks identically -- matched by suffix instead
# of an exact host list so this doesn't need updating for every variant.
DISCORD_HOST_SUFFIXES = ("discord.com", "discordapp.com")

SUPPRESS_NOTIFICATIONS_FLAG = 4096  # Discord webhook message flag for "silent" sends


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


def send(url: str, embed: dict, content: str = "", silent: bool = False) -> bool:
    if not url:
        return False
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content
    if silent:
        payload["flags"] = SUPPRESS_NOTIFICATIONS_FLAG
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False
