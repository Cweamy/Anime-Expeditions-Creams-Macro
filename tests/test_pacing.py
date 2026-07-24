import pytest
from core import pacing


@pytest.fixture(autouse=True)
def reset_pacing_delay():
    # Garante que o delay volte a 0 antes e depois de cada teste
    pacing.set_action_delay_ms(0)
    yield
    pacing.set_action_delay_ms(0)


def test_default_delay():
    # O delay inicial padrão deve ser 0ms
    assert pacing.get_action_delay_ms() == 0


def test_set_valid_delay():
    # Deve configurar o delay em milissegundos corretamente
    pacing.set_action_delay_ms(250)
    assert pacing.get_action_delay_ms() == 250


def test_delay_clamping_max():
    # Valores acima do limite máximo (2000ms) devem ser cortados para 2000
    pacing.set_action_delay_ms(5000)
    assert pacing.get_action_delay_ms() == 2000


def test_delay_clamping_min():
    # Valores negativos devem ser ajustados para 0
    pacing.set_action_delay_ms(-100)
    assert pacing.get_action_delay_ms() == 0


def test_delay_invalid_type():
    # Entradas inválidas devem resultar em fallback para 0
    pacing.set_action_delay_ms("invalid")
    assert pacing.get_action_delay_ms() == 0

    pacing.set_action_delay_ms(None)
    assert pacing.get_action_delay_ms() == 0


def test_action_pause_calls_sleep(monkeypatch):
    # Deve chamar time.sleep somente quando o delay for maior que 0
    sleep_calls = []

    def mock_sleep(duration):
        sleep_calls.append(duration)

    monkeypatch.setattr(pacing.time, "sleep", mock_sleep)

    # Com delay = 0, não deve chamar sleep
    pacing.set_action_delay_ms(0)
    pacing.action_pause()
    assert len(sleep_calls) == 0

    # Com delay > 0, deve chamar sleep com o valor em segundos
    pacing.set_action_delay_ms(500)
    pacing.action_pause()
    assert len(sleep_calls) == 1
    assert pytest.approx(sleep_calls[0]) == 0.5
