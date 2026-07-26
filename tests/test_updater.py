import os

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
