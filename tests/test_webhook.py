from core.webhook import validate


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
