from core.webhook import validate


# Testes para validação de URLs de Webhook do Discord
def test_validate_empty_url():
    # URL vazia ou com apenas espaços deve retornar erro de URL vazia
    assert validate("") == {"valid": False, "reason": "empty"}
    assert validate(None) == {"valid": False, "reason": "empty"}
    assert validate("   ") == {"valid": False, "reason": "empty"}


def test_validate_non_https():
    # URLs que não utilizam o protocolo HTTPS devem ser rejeitadas
    assert validate("http://discord.com/api/webhooks/1234567890/test-token") == {
        "valid": False,
        "reason": "not_https",
    }


def test_validate_non_discord_host():
    # URLs de domínios que não pertencem ao Discord devem ser rejeitadas
    assert validate("https://google.com/api/webhooks/1234567890/test-token") == {
        "valid": False,
        "reason": "not_discord",
    }
    assert validate("https://fake-discord.com/api/webhooks/1234567890/test-token") == {
        "valid": False,
        "reason": "not_discord",
    }


def test_validate_bad_format_paths():
    # Caminhos incorretos na URL do webhook
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
    # URLs válidas com diferentes formatos suportados pelo Discord
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

