import atexit
import json
import os
import queue
import threading
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
# ("Python-urllib/3.x") outright -- every send was failing with a 403.
# A normal browser User-Agent clears it.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Global in-memory queue to store pending webhook notifications
_webhook_queue = queue.Queue()

# Background worker thread reference and concurrency control
_worker_thread = None
_thread_lock = threading.Lock()

# Maximum retries for transient HTTP/network failures
MAX_RETRIES = 3


def validate(url: str) -> dict:
    """Validates whether the given URL is a properly formatted Discord webhook URL.
    
    Returns {"valid": bool, "reason": str}.
    """
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

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4 or parts[-4] != "api" or parts[-3] != "webhooks":
        return {"valid": False, "reason": "bad_format"}
    webhook_id, token = parts[-2], parts[-1]
    if not webhook_id.isdigit() or not token:
        return {"valid": False, "reason": "bad_format"}
    return {"valid": True, "reason": "ok"}


def _ensure_worker_started():
    """Ensures the background worker thread is safely initialized (Lazy Initialization).
    
    Uses a Lock to prevent race conditions when multiple calls occur simultaneously.
    """
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        with _thread_lock:
            if _worker_thread is None or not _worker_thread.is_alive():
                _worker_thread = threading.Thread(
                    target=_worker_loop, daemon=True, name="WebhookWorkerThread"
                )
                _worker_thread.start()


def _worker_loop():
    """Continuous background loop processing queued notification items sequentially."""
    while True:
        item = _webhook_queue.get()
        if item is None:
            _webhook_queue.task_done()
            break

        url = item["url"]
        payload = item["payload"]
        file_bytes = item.get("file_bytes")
        filename = item.get("filename")
        attempts = item.get("attempts", 0)

        # Attempt HTTP dispatch
        result, should_retry, delay_seconds = _dispatch_request(url, payload, file_bytes, filename)

        if result.get("ok"):
            _webhook_queue.task_done()
            continue

        # Handle rate limiting (HTTP 429) or transient network errors
        if should_retry and attempts < MAX_RETRIES:
            item["attempts"] = attempts + 1
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            # Re-enqueue item for another retry
            _webhook_queue.put(item)
        else:
            # Drop notification after maximum retry limit reached
            pass

        _webhook_queue.task_done()


def _dispatch_request(url: str, payload: dict, file_bytes: bytes = None, filename: str = None) -> tuple:
    """Performs the actual HTTP request to Discord API.
    
    Captures and handles rate limits (HTTP 429) by reading Discord's retry_after response.
    Returns a tuple: (result_dict, should_retry_bool, delay_seconds_float).
    """
    try:
        if file_bytes and filename:
            # Multipart form-data upload with screenshot file attachment using requests
            payload["embeds"][0]["image"] = {"url": f"attachment://{filename}"}
            files = {"file": (filename, file_bytes, "image/png")}
            data = {"payload_json": json.dumps(payload)}

            resp = requests.post(
                url, data=data, files=files, headers={"User-Agent": USER_AGENT}, timeout=15
            )

            if 200 <= resp.status_code < 300:
                return {"ok": True, "reason": ""}, False, 0.0

            if resp.status_code == 429:
                # Extract retry_after delay requested by Discord
                retry_after = 1.0
                try:
                    data_json = resp.json()
                    retry_after = float(data_json.get("retry_after", 1.0))
                except Exception:
                    header_retry = resp.headers.get("Retry-After")
                    if header_retry:
                        retry_after = float(header_retry)
                # Add 0.2s safety buffer to avoid immediate secondary rate-limits
                return {"ok": False, "reason": "Rate Limited (429)"}, True, retry_after + 0.2

            return {"ok": False, "reason": f"HTTP {resp.status_code}"}, True, 1.0

        else:
            # Standard JSON payload request using urllib
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return {"ok": True, "reason": ""}, False, 0.0
                return {"ok": False, "reason": f"HTTP {resp.status}"}, True, 1.0

    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry_after = 1.0
            try:
                body = exc.read().decode("utf-8", errors="replace")
                data_json = json.loads(body)
                retry_after = float(data_json.get("retry_after", 1.0))
            except Exception:
                header_retry = exc.headers.get("Retry-After") if exc.headers else None
                if header_retry:
                    retry_after = float(header_retry)
            return {"ok": False, "reason": "Rate Limited (429)"}, True, retry_after + 0.2

        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {"ok": False, "reason": f"HTTP {exc.code}: {body}" if body else f"HTTP {exc.code}"}, True, 1.0

    except (urllib.error.URLError, OSError, requests.RequestException) as exc:
        return {"ok": False, "reason": str(exc)}, True, 1.0


def send(url: str, embed: dict, content: str = "", silent: bool = False) -> dict:
    """Enqueues a notification to be sent asynchronously in the background.
    
    Returns immediately with {"ok": True, "reason": "queued"} without blocking the macro loop.
    """
    if not url:
        return {"ok": False, "reason": "no webhook URL configured"}

    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content
    if silent:
        payload["flags"] = SUPPRESS_NOTIFICATIONS_FLAG

    _webhook_queue.put({"url": url, "payload": payload, "attempts": 0})
    _ensure_worker_started()
    return {"ok": True, "reason": "queued"}


def send_file(url: str, embed: dict, screenshot_path: str, content: str = "", silent: bool = False) -> dict:
    """Enqueues a notification with an attached screenshot file.
    
    Reads screenshot bytes into memory immediately upon call to avoid race conditions
    where temporary image files on disk are deleted before the background worker finishes sending.
    """
    if not url:
        return {"ok": False, "reason": "no webhook URL configured"}

    if not screenshot_path or not os.path.isfile(screenshot_path):
        return send(url, embed, content=content, silent=silent)

    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content
    if silent:
        payload["flags"] = SUPPRESS_NOTIFICATIONS_FLAG

    try:
        with open(screenshot_path, "rb") as f:
            file_bytes = f.read()
        filename = os.path.basename(screenshot_path)
    except OSError:
        # Fallback to text-only send if file reading fails
        return send(url, embed, content=content, silent=silent)

    _webhook_queue.put(
        {
            "url": url,
            "payload": payload,
            "file_bytes": file_bytes,
            "filename": filename,
            "attempts": 0,
        }
    )
    _ensure_worker_started()
    return {"ok": True, "reason": "queued"}


def _on_shutdown():
    """Flushes remaining pending queue items with a 3-second timeout during application shutdown."""
    if not _webhook_queue.empty():
        end_time = time.time() + 3.0
        while not _webhook_queue.empty() and time.time() < end_time:
            time.sleep(0.1)


# Register graceful shutdown handler
atexit.register(_on_shutdown)
