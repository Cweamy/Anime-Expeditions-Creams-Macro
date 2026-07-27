import base64
import json
import urllib.request
import zlib

HEADER_PREFIX = "CREAM:v1:"
MAX_URL_SIZE = 5 * 1024 * 1024  # 5 MB safety limit
# Codes come from other people, so the decompressed size is capped: a tiny
# zlib stream can otherwise expand to gigabytes (a "zip bomb") and OOM the app.
# Real templates are a few KB; a big pack was ~30 KB, so 5 MB is generous.
MAX_DECOMPRESSED_SIZE = 5 * 1024 * 1024


def encode_template_code(data: dict) -> str:
    """Encodes a template dict or template pack dict into a compressed CREAM:v1: code string.

    Args:
        data: Dict containing single template or multi-template pack structure.

    Returns:
        Encoded string starting with 'CREAM:v1:'.
    """
    json_bytes = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(json_bytes, level=9)
    b64 = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    return f"{HEADER_PREFIX}{b64}"


def decode_template_code(input_str: str) -> dict:
    """Decodes a code string, URL, or raw JSON into structured template data.

    Args:
        input_str: CREAM:v1: string, raw JSON string, or http/https URL.

    Returns:
        dict with format {"ok": bool, "type": "single"|"pack", "templates": dict, "reason": str (optional)}
    """
    if not input_str or not isinstance(input_str, str):
        return {"ok": False, "reason": "Empty or invalid input."}

    raw_str = input_str.strip()

    # Case 1: Web URL (http:// or https://)
    if raw_str.startswith("http://") or raw_str.startswith("https://"):
        try:
            req = urllib.request.Request(
                raw_str,
                headers={"User-Agent": "CreamsMacro-TemplateImporter/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read(MAX_URL_SIZE + 1)
                if len(content) > MAX_URL_SIZE:
                    return {"ok": False, "reason": "URL content exceeds 5MB limit."}
                raw_str = content.decode("utf-8", errors="replace").strip()
        except Exception as exc:
            return {"ok": False, "reason": f"Failed to download URL: {exc}"}

    # Case 2: CREAM:v1: Compressed Code
    if raw_str.startswith(HEADER_PREFIX):
        b64_str = raw_str[len(HEADER_PREFIX) :].strip()
        try:
            # Re-add base64 padding if needed
            padding = len(b64_str) % 4
            if padding:
                b64_str += "=" * (4 - padding)
            # Bounded decompress: stop at MAX_DECOMPRESSED_SIZE and treat any
            # leftover (unconsumed_tail) as an oversized/hostile stream.
            dobj = zlib.decompressobj()
            decompressed = dobj.decompress(base64.urlsafe_b64decode(b64_str), MAX_DECOMPRESSED_SIZE)
            if dobj.unconsumed_tail:
                return {"ok": False, "reason": "Code expands to more than the 5MB limit."}
            payload = json.loads(decompressed.decode("utf-8"))
        except Exception as exc:
            return {"ok": False, "reason": f"Invalid or corrupted code string: {exc}"}
    else:
        # Case 3: Raw JSON string
        try:
            payload = json.loads(raw_str)
        except json.JSONDecodeError as exc:
            return {"ok": False, "reason": f"Input is not a valid code, URL, or JSON: {exc}"}

    return _parse_payload(payload)


def count_template_blocks(blocks) -> int:
    """Calculates total block count across prestart, battle, and flat list formats."""
    if isinstance(blocks, list):
        return len(blocks)
    if isinstance(blocks, dict):
        # Known block phases in Creams Macro templates
        prestart = blocks.get("prestart") if isinstance(blocks.get("prestart"), list) else (blocks.get("before") if isinstance(blocks.get("before"), list) else [])
        battle = blocks.get("battle") if isinstance(blocks.get("battle"), list) else []
        legacy = (blocks.get("during") if isinstance(blocks.get("during"), list) else []) + (blocks.get("after") if isinstance(blocks.get("after"), list) else [])
        total = len(prestart) + len(battle) + len(legacy)
        if total > 0:
            return total
        return sum(len(v) for v in blocks.values() if isinstance(v, list))
    return 0


def _parse_payload(payload: dict) -> dict:
    """Parses and validates a decoded payload dictionary."""
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "Payload is not a valid JSON object."}

    kind = payload.get("kind")
    templates = {}

    # Multi-template pack
    if kind == "anime-expeditions-template-pack" or "templates" in payload:
        raw_tpls = payload.get("templates", {})
        if isinstance(raw_tpls, dict):
            for tname, tval in raw_tpls.items():
                if isinstance(tval, dict):
                    blocks = tval.get("blocks", tval)
                    templates[str(tname)] = blocks
                elif isinstance(tval, list):
                    templates[str(tname)] = tval
        return {"ok": True, "type": "pack", "templates": templates}

    # Single template
    if "name" in payload or "blocks" in payload:
        name = str(payload.get("name", "Imported Template"))
        blocks = payload.get("blocks", {})
        templates[name] = blocks
        return {"ok": True, "type": "single", "templates": templates}

    return {"ok": False, "reason": "Unrecognized template schema."}


def preview_template_code(input_str: str) -> dict:
    """Decodes input and returns summary preview information without saving."""
    res = decode_template_code(input_str)
    if not res.get("ok"):
        return res

    templates = res.get("templates", {})
    items = []
    for tname, blocks in templates.items():
        items.append({
            "name": tname,
            "blocks_count": count_template_blocks(blocks),
        })

    return {
        "ok": True,
        "type": res.get("type", "single"),
        "total_templates": len(items),
        "items": items,
    }
