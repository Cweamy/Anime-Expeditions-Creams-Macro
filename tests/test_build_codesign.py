"""Guards the macOS code-signing-identity contract across its three homes:
build_pyinstaller.py, the two macOS CI workflows, and the cert-generation
script.

These are source-text tests, not functional ones: the signing itself runs
`codesign`/`security` on a real Mac, which a Windows test host can't do.
What CAN be pinned down here is the contract that makes the fix work -- the
build reads MACOS_CODESIGN_IDENTITY and falls back to ad-hoc, the workflows
import the p12 and export the matching identity, and the script is
idempotent-safe. A regression in any one of those is silent until a real
macOS update re-prompts a user, so the cheap text assertion is worth it.
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]

# The common name both the cert script and the workflows must agree on:
# codesign finds the imported identity by this string, so a mismatch means
# CI signs with a name that isn't in the keychain and silently falls back
# (or fails) -- exactly the loop this work exists to stop. (The bundle id
# is carried in the cert's OU field instead of the CN, because X.509 caps
# commonName at 64 chars -- the full "CN (bundle-id)" form is 70.)
CERT_CN = "Creams Macro Code Signing"
BUNDLE_ID = "com.cweamy.creams-macro-anime-expeditions"


def _read(name):
    return (REPO / name).read_text(encoding="utf-8")


def test_build_reads_stable_identity_from_env():
    build = _read("build_pyinstaller.py")
    assert "MACOS_CODESIGN_IDENTITY" in build
    assert 'os.environ.get("MACOS_CODESIGN_IDENTITY"' in build


def test_build_signs_with_the_identity_when_set():
    build = _read("build_pyinstaller.py")
    # Stable path: pass the identity as the --sign argument (not the ad-hoc
    # dash), so a cert-signed bundle keys its TCC grant to the certificate.
    assert '["codesign", "--force", "--deep", "--sign", identity, app_path]' in build


def test_build_keeps_adhoc_fallback_without_a_cert():
    build = _read("build_pyinstaller.py")
    # Local/no-secret builds must keep working: ad-hoc signing stays as the
    # else branch. Asserting the exact argv list keeps that from drifting.
    assert '["codesign", "--force", "--deep", "--sign", "-", app_path]' in build


def test_workflows_import_the_p12_and_export_the_identity():
    for wf in ("release.yml", "macos-asset.yml"):
        text = _read(pathlib.PurePath(".github", "workflows", wf).as_posix())
        assert "security create-keychain" in text
        assert "security default-keychain -s build.keychain" in text
        assert "security unlock-keychain" in text
        assert "security import cert.p12" in text
        # This is the step that makes codesign able to use the key without a
        # GUI prompt on modern macOS -- a silently dropped partition-list is
        # a stuck build, so keep it asserted by name.
        assert "security set-key-partition-list -S apple-tool:,apple:,codesign:" in text
        # The identity exported to the build step must be the cert's common
        # name, in lock-step with the script's CN.
        assert f"MACOS_CODESIGN_IDENTITY={CERT_CN}" in text


def test_workflows_are_conditional_on_the_secret():
    # Builds without the secret must still succeed: the import step is
    # guarded, so a fork or a pre-secret build just skips to ad-hoc signing.
    for wf in ("release.yml", "macos-asset.yml"):
        text = _read(pathlib.PurePath(".github", "workflows", wf).as_posix())
        assert "if: ${{ secrets.MACOS_CODESIGN_P12 != '' }}" in text


def test_cert_script_is_idempotent_safe():
    script = _read(pathlib.PurePath("tools", "make_macos_codesign_cert.sh").as_posix())
    # The whole point of reusing ONE cert is that rotating it silently would
    # break every existing user's macOS grants. A re-run must refuse unless
    # the owner explicitly asks to rotate.
    assert "--force" in script
    assert "Refusing to overwrite" in script
    assert "MACOS_CODESIGN_P12" in script
    assert "MACOS_CODESIGN_PASSWORD" in script
    # The identity baked into the script must match what the workflows export.
    assert f'CN="{CERT_CN}"' in script
    # The bundle id is what TCC's designated requirement pairs with the cert
    # hash, so it must appear in the cert subject (OU) and stay in lock-step
    # with build_pyinstaller.py's pinned --osx-bundle-identifier.
    assert f'OU="{BUNDLE_ID}"' in script
