"""Posts a "commit(s) just pushed to main" message to the git-log Discord
webhook -- one embed per commit in this push, not just HEAD.

Used to be a bash+jq one-liner in ci.yml itself, but building nested JSON
(an embed list, each containing a multi-line description) through a chain
of shell variable interpolation + jq calls turned out fragile in practice:
it silently sent Discord an invalid payload on a real multi-commit push
(confirmed via the HTTP 400 body once the workflow was changed to actually
print curl's response instead of discarding it) despite every local
reproduction of the same shell logic working fine -- a real environment
difference too subtle to keep chasing in YAML. Python's own json module
can't produce invalid JSON by construction, so this sidesteps the whole
class of bug rather than trying to out-quote it.

Reads its inputs from environment variables (set by the calling workflow
step) rather than argv, so the workflow YAML doesn't need to shell-escape
anything into a command line either.
"""
import json
import os
import subprocess
import sys

MAX_EMBEDS = 10  # Discord's per-message limit
# Mirrors core/webhook.py's USER_AGENT -- not imported from there since this
# script runs as a standalone file (its own directory, not the repo root,
# ends up on sys.path), and duplicating one short constant beats a sys.path
# hack just to avoid it. Discord sits behind Cloudflare, which blocks
# requests' default User-Agent outright.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def run_git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    webhook_url = os.environ.get("DISCORD_GIT_LOGS_WEBHOOK", "").strip()
    if not webhook_url:
        return 0

    job_status = os.environ.get("JOB_STATUS", "success")
    before_sha = os.environ.get("BEFORE_SHA", "").strip()
    repo_url = os.environ.get("REPO_URL", "").strip()
    emoji = "✅" if job_status == "success" else "❌"

    range_spec = "HEAD"
    if before_sha and before_sha != "0" * 40:
        try:
            subprocess.run(["git", "cat-file", "-e", before_sha], check=True, capture_output=True)
            range_spec = f"{before_sha}..HEAD"
        except subprocess.CalledProcessError:
            pass  # before_sha not reachable (shallow history, force-push, ...) -- fall back to just HEAD

    shas = [s for s in run_git("log", range_spec, "--format=%H", "--reverse").splitlines() if s]
    if not shas:
        shas = [run_git("rev-parse", "HEAD")]
    # Oldest first, capped at Discord's limit -- a push bundling more than
    # that is rare enough that seeing the newest 10 plus the branch's own
    # commit history on GitHub covers it well enough.
    shas = shas[-MAX_EMBEDS:]

    embeds = []
    for sha in shas:
        msg = run_git("log", "-1", "--format=%s", sha)[:200]
        short = run_git("rev-parse", "--short", sha)
        author = run_git("log", "-1", "--format=%an", sha)
        description = f"**{msg}**\n{author} · [`{short}`]({repo_url}/commit/{sha})"
        embeds.append({"description": description, "color": 5793266})

    payload = {"content": f"{emoji} **{len(shas)} commit(s) pushed to main**", "embeds": embeds}

    import requests
    resp = requests.post(webhook_url, json=payload, headers={"User-Agent": USER_AGENT}, timeout=15)
    print(f"Discord response: HTTP {resp.status_code}")
    print(resp.text)
    if not (200 <= resp.status_code < 300):
        print(f"::warning::Discord git-log webhook returned HTTP {resp.status_code} -- see response body above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
