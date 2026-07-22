"""Posts a release changelog to the update-log Discord webhook -- one embed
whose description is the tag's own message (see release.yml's changelog
step and the release checklist in README.md).

Used to be a bash+jq step inline in release.yml, but it hit the exact same
failure post_git_log.py already documents: the payload passed every local
reproduction yet arrived at Discord as invalid JSON on the real Windows
runner (HTTP 400, code 50109) the first time a changelog contained
non-ASCII characters (em dashes). Same fix as the git-log post -- Python's
json module can't produce invalid JSON by construction, so build the
payload here instead of trying to out-quote the shell.

Reads its inputs from environment variables (set by the calling workflow
step) rather than argv, so the workflow YAML doesn't need to shell-escape
a multi-line changelog into a command line.
"""
import os
import sys

# Discord embed descriptions cap at 4096 chars -- trim (in CHARACTERS, not
# bytes: a byte-level cut can split a multi-byte character and corrupt the
# whole string) rather than let an unusually long changelog fail the post.
MAX_DESCRIPTION = 3800
# See post_git_log.py's USER_AGENT comment -- Discord sits behind
# Cloudflare, which blocks requests' default User-Agent outright.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def main() -> int:
    webhook_url = os.environ.get("DISCORD_UPDATE_LOGS_WEBHOOK", "").strip()
    if not webhook_url:
        print("No DISCORD_UPDATE_LOGS_WEBHOOK secret set -- skipping the Discord post.")
        return 0

    tag_name = os.environ.get("TAG_NAME", "").strip()
    release_url = os.environ.get("RELEASE_URL", "").strip()
    body = os.environ.get("BODY", "").strip()

    payload = {
        "embeds": [{
            "title": f"Cream's Macro {tag_name} is out",
            "description": body[:MAX_DESCRIPTION],
            "url": release_url,
            "color": 5793266,
        }]
    }

    import requests
    resp = requests.post(webhook_url, json=payload, headers={"User-Agent": USER_AGENT}, timeout=15)
    print(f"Discord response: HTTP {resp.status_code}")
    print(resp.text)
    if not (200 <= resp.status_code < 300):
        # A hard failure, unlike post_git_log's warning-only -- a release
        # announcement silently not going out is exactly what this step
        # exists to prevent, and the release itself is already published
        # by this point so failing loudly costs nothing.
        print(f"::error::Discord release webhook returned HTTP {resp.status_code} -- see response body above.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
