from core.webhook import send, send_file, send_rich, validate, validate_webhook_url


def test_validate_empty_url():
    # Empty string, None, or whitespace-only URLs should be rejected as empty
    assert validate("") == {"valid": False, "reason": "empty"}
    assert validate(None) == {"valid": False, "reason": "empty"}
    assert validate("   ") == {"valid": False, "reason": "empty"}


def test_validate_non_https():
    # Non-HTTPS schemes must be rejected
    assert validate("http://discord.com/api/webhooks/1234567890/test-token") == {
        "valid": False,
        "reason": "not_https",
    }


def test_validate_non_discord_host():
    # Non-Discord hosts must be rejected
    assert validate("https://google.com/api/webhooks/1234567890/test-token") == {
        "valid": False,
        "reason": "not_discord",
    }
    assert validate("https://fake-discord.com/api/webhooks/1234567890/test-token") == {
        "valid": False,
        "reason": "not_discord",
    }


def test_validate_bad_format_paths():
    # Invalid path formats (missing segments, non-numeric ID, trailing slash without token)
    assert validate("https://discord.com/api/invalid/1234567890/test-token") == {
        "valid": False,
        "reason": "bad_format",
    }
    assert validate("https://discord.com/api/webhooks/not_a_number/test-token") == {
        "valid": False,
        "reason": "bad_format",
    }
    assert validate("https://discord.com/api/webhooks/1234567890/") == {
        "valid": False,
        "reason": "bad_format",
    }


def test_validate_valid_urls():
    # Valid webhook URLs with supported subdomains, query strings, and trailing slashes
    assert validate("https://discord.com/api/webhooks/1234567890/test-token") == {
        "valid": True,
        "reason": "ok",
    }
    assert validate("https://discordapp.com/api/webhooks/1234567890/test-token") == {
        "valid": True,
        "reason": "ok",
    }
    assert validate("https://ptb.discord.com/api/webhooks/1234567890/test-token/") == {
        "valid": True,
        "reason": "ok",
    }
    assert validate(
        "https://canary.discord.com/api/webhooks/1234567890/test-token?wait=true"
    ) == {"valid": True, "reason": "ok"}


def test_validate_webhook_url_helper():
    # Helper returns True for valid Discord webhooks and False for invalid/SSRF targets
    assert validate_webhook_url("https://discord.com/api/webhooks/123/abc") is True
    assert validate_webhook_url("https://discordapp.com/api/webhooks/1234567890/test-token") is True
    assert validate_webhook_url("http://127.0.0.1") is False
    assert validate_webhook_url("https://evil.com/webhook") is False
    assert validate_webhook_url("") is False


def test_send_invalid_url_ssrf_prevention():
    # Sending to invalid or non-Discord URLs returns an error dict without initiating HTTP requests
    invalid_reason = "invalid webhook URL format or non-Discord target"
    assert send("http://127.0.0.1", {}) == {"ok": False, "reason": invalid_reason}
    assert send("https://evil.com/webhook", {}) == {"ok": False, "reason": invalid_reason}
    assert send_file("http://127.0.0.1", {}, "nonexistent.png") == {"ok": False, "reason": invalid_reason}
    assert send_rich("https://evil.com/webhook") == {"ok": False, "reason": invalid_reason}

