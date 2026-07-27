import os
import sys

from core.updater import _parse_version


def test_parse_version_plain_tags():
    assert _parse_version("v0.11.0") == (0, 11, 0)
    assert _parse_version("0.11.0") == (0, 11, 0)
    assert _parse_version("v1.2") == (1, 2)
    assert _parse_version("v2") == (2,)


def test_parse_version_orders_releases_correctly():
    assert _parse_version("v0.11.0") > _parse_version("v0.10.0")
    assert _parse_version("v0.10.0") > _parse_version("v0.9.9")
    assert _parse_version("v1.0.0") > _parse_version("v0.99.99")


def test_parse_version_ignores_a_pre_release_suffix():
    """A suffix must not become a version component. Collecting every digit
    run made v0.11.0-beta2 parse as (0, 11, 0, 2), which sorts ABOVE the
    finished (0, 11, 0) -- so someone on the real 0.11.0 would be offered an
    "update" back down to the pre-release."""
    assert _parse_version("v0.11.0-beta2") == (0, 11, 0)
    assert _parse_version("v0.11.0-rc1") == (0, 11, 0)

    current = _parse_version("0.11.0")
    assert not _parse_version("v0.11.0-beta2") > current, "pre-release offered as an update"
    # A genuinely newer pre-release still reads as newer.
    assert _parse_version("v0.12.0-beta1") > current


def test_parse_version_no_digits():
    assert _parse_version("") == (0,)
    assert _parse_version("nightly") == (0,)


def test_failed_download_does_not_leak_a_temp_dir(tmp_path, monkeypatch):
    """merge_assets_update cleaned up with os.remove(zip) + os.rmdir(dir). If
    the download never created the zip, the remove raised and the rmdir never
    ran, leaving an empty aecm_* folder in TEMP after every failed attempt."""
    from core import updater

    monkeypatch.setattr(updater.tempfile, "tempdir", str(tmp_path))

    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(updater, "_get_release_zip_with_fallback", boom)

    assert updater.merge_assets_update("https://example.invalid/x.zip", lambda msg: None) is False
    leftovers = [d for d in os.listdir(tmp_path) if d.startswith("aecm_")]
    assert leftovers == [], f"left {leftovers} behind in TEMP"


def test_stage_exe_update_script_contains_retries(tmp_path, monkeypatch):
    """Ensure the exe update script includes move retries and auto-relaunch commands."""
    from core import updater

    fake_exe = str(tmp_path / "MacroApp.exe")
    fake_new_exe = str(tmp_path / "MacroApp.exe.update")
    with open(fake_exe, "w") as f:
        f.write("fake binary")
    with open(fake_new_exe, "w") as f:
        f.write("fake new binary")

    monkeypatch.setattr(updater, "_current_exe_path", lambda: fake_exe)
    helper_path = updater.stage_exe_update(fake_new_exe)

    assert os.path.exists(helper_path)
    with open(helper_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert ":moveoldloop" in content
    assert ":movenewloop" in content
    assert "start \"\"" in content



# ---------------------------------------------------------------------------
# launch_helper's process-creation flags
# ---------------------------------------------------------------------------
# The update helper is a .bat. DETACHED_PROCESS gives it no console at all, so
# the first plain `echo` (no redirect) writes to a stdout handle that does not
# exist, cmd stops there, and none of the five steps after it run. Reported as:
# "Update & Restart downloads everything, then nothing happens -- it doesn't
# reopen, and it's still the old version." The only trace left is a one-line
# _update.log, an un-consumed "<exe>.update" and the helper .bat still sitting
# next to the exe.
#
# CREATE_NO_WINDOW is the fix: still invisible, still outlives the app (which
# exits ~0.4s later), but it has a real console. Redirecting stdio to DEVNULL
# is NOT sufficient -- the taskkill/tasklist steps want a console too.

def test_launch_helper_gives_the_helper_a_console(monkeypatch):
    import subprocess as sp

    from core import updater

    monkeypatch.setattr(sys, "platform", "win32")
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["flags"] = kwargs.get("creationflags", 0)
        return object()

    monkeypatch.setattr(sp, "Popen", fake_popen)
    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)

    updater.launch_helper(r"C:\somewhere\_update.bat")

    assert seen["cmd"][:2] == ["cmd.exe", "/c"]
    assert seen["flags"] & updater.CREATE_NO_WINDOW, "helper must get a (hidden) console"
    assert seen["flags"] & updater.CREATE_NEW_PROCESS_GROUP, "helper must outlive the app"
    # 0x8 is DETACHED_PROCESS. Naming it directly rather than importing it,
    # because the point of this test is that the module no longer defines it.
    assert not seen["flags"] & 0x00000008, (
        "DETACHED_PROCESS leaves the .bat without a console; it dies at the "
        "first unredirected echo, before the exe is ever swapped"
    )


def test_updater_no_longer_exposes_detached_process():
    """Guards against it being reintroduced by name."""
    from core import updater
    assert not hasattr(updater, "DETACHED_PROCESS")


# ---------------------------------------------------------------------------
# The relaunch after the exe swap
# ---------------------------------------------------------------------------
# A onefile build's bootloader extracts to %TEMP%\_MEI<random>, exports
# _PYI_ARCHIVE_FILE / _PYI_APPLICATION_HOME_DIR / _PYI_PARENT_PROCESS_LEVEL,
# and re-runs itself -- so our code is the second process and those are in
# os.environ. They get inherited all the way down to the `start ""` in the
# helper, and because the update puts the new exe on the OLD exe's path, the
# fresh build sees _PYI_ARCHIVE_FILE equal to its own path, concludes it is
# already extracted, and loads the interpreter from the previous process's
# _MEI dir -- which the helper deleted at step [2/5]:
#
#     [PYI-28940:ERROR] Failed to load Python DLL '...\_MEI302922\python313.dll'
#     LoadLibrary: The specified module could not be found.
#
# Reported as "the update applies but the app never comes back". Running the
# same _update.bat by hand works, because Explorer starts it with a clean
# environment -- which is exactly what relaunch_env() reproduces.

def test_relaunch_env_drops_pyinstaller_bootloader_state(monkeypatch):
    from core import updater

    monkeypatch.setenv("_PYI_ARCHIVE_FILE", r"C:\App\Creams Macro.exe")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI302922")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("_MEIPASS2", r"C:\Temp\_MEI99")  # PyInstaller 5.x's name
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")

    env = updater.relaunch_env()

    assert not [k for k in env if k.startswith("_PYI_")]
    assert "_MEIPASS2" not in env
    # Everything else has to survive -- the relaunched app still needs a
    # usable environment, this is not a bare env=... wipe.
    assert env["APPDATA"] == r"C:\Users\someone\AppData\Roaming"
    assert len(env) == len(os.environ) - 4


def test_launch_helper_starts_the_helper_with_that_env(monkeypatch):
    """The strip has to be applied where the process boundary is. Doing it
    anywhere later is too late -- cmd.exe has already inherited them."""
    from core import updater

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI302922")
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return object()

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    updater.launch_helper(r"C:\somewhere\_update.bat")

    assert seen["env"] is not None, "helper must not inherit this process's environment as-is"
    assert "_PYI_APPLICATION_HOME_DIR" not in seen["env"]


def test_helper_waits_before_force_killing_the_app(tmp_path, monkeypatch):
    """taskkill is a safety net for a hung shutdown, not the normal path.

    main.Api.apply_update schedules close_window() at +0.4s; without a wait
    here taskkill was measured firing at +0.28s, so close_window -- which
    un-parents the docked Roblox window, persists all-time stats and closes
    the capture/log handles -- never ran at all on a frozen build.
    """
    from core import updater

    fake_exe = str(tmp_path / "MacroApp.exe")
    open(fake_exe, "w").write("fake binary")
    open(fake_exe + ".update", "w").write("fake new binary")
    monkeypatch.setattr(updater, "_current_exe_path", lambda: fake_exe)

    with open(updater.stage_exe_update(fake_exe + ".update"), encoding="utf-8") as f:
        content = f.read()

    wait = content.index("ping -n 3 127.0.0.1 >nul")
    force_kill = content.index('taskkill /F /IM "MacroApp.exe"')
    assert wait < force_kill, (
        "the helper force-kills the app before close_window gets a chance to run")
