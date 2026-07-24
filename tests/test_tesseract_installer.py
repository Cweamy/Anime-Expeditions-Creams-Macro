import subprocess
from unittest.mock import MagicMock, patch
import pytest

from core.tesseract_installer import install_tesseract


def test_install_tesseract_success():
    """Test fresh install returncode 0."""
    logs = []
    def mock_run(cmd, **kwargs):
        res = MagicMock()
        res.returncode = 0
        res.stdout = ""
        res.stderr = ""
        return res

    with patch("subprocess.run", side_effect=mock_run):
        ok = install_tesseract(log=logs.append)
        assert ok is True
        assert "[Tesseract] Installed successfully." in logs


def test_install_tesseract_already_installed_unsigned():
    """Test exit code 2316632107 (0x8A15002B) when already installed."""
    logs = []
    def mock_run(cmd, **kwargs):
        if "--version" in cmd:
            res = MagicMock()
            res.returncode = 0
            return res
        res = MagicMock()
        res.returncode = 2316632107
        res.stdout = "Foi encontrado um pacote existente ja instalado."
        res.stderr = ""
        return res

    with patch("subprocess.run", side_effect=mock_run):
        ok = install_tesseract(log=logs.append)
        assert ok is True
        assert "[Tesseract] Already installed and up to date." in logs


def test_install_tesseract_already_installed_signed():
    """Test exit code -1978335189 (signed int32) when already installed."""
    logs = []
    def mock_run(cmd, **kwargs):
        if "--version" in cmd:
            res = MagicMock()
            res.returncode = 0
            return res
        res = MagicMock()
        res.returncode = -1978335189
        res.stdout = ""
        res.stderr = ""
        return res

    with patch("subprocess.run", side_effect=mock_run):
        ok = install_tesseract(log=logs.append)
        assert ok is True
        assert "[Tesseract] Already installed and up to date." in logs


def test_install_tesseract_no_winget():
    """Test handling when winget is missing on the system."""
    logs = []
    with patch("subprocess.run", side_effect=FileNotFoundError):
        ok = install_tesseract(log=logs.append)
        assert ok is False
        assert any("winget isn't available" in m for m in logs)


def test_install_tesseract_real_failure():
    """Test handling when winget returns a generic error code and binary is not installed."""
    logs = []
    def mock_run(cmd, **kwargs):
        if "--version" in cmd:
            if "winget" in cmd:
                res = MagicMock()
                res.returncode = 0
                return res
            # Fallback path checks for tesseract --version fail
            raise FileNotFoundError
        res = MagicMock()
        res.returncode = 1
        res.stdout = "Package not found"
        res.stderr = ""
        return res

    with patch("subprocess.run", side_effect=mock_run):
        ok = install_tesseract(log=logs.append)
        assert ok is False
        assert any("winget install failed (exit 1)" in m for m in logs)
