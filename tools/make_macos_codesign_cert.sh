#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# make_macos_codesign_cert.sh
#
# Generates a SELF-SIGNED code-signing certificate ONCE, so every macOS CI
# release build can be signed with the SAME identity instead of a fresh
# ad-hoc one each time.
#
# WHY THIS EXISTS (the "stuck in a permission loop on update" bug)
# ---------------------------------------------------------------------------
# macOS's TCC database (the thing behind the Accessibility, Input Monitoring,
# and Screen Recording permission prompts) remembers a grant against the
# app's CODE IDENTITY, not its filename or its path. That identity is the
# app's designated requirement: bundle identifier + signature.
#
# An AD-HOC signature (`codesign --sign -`) has no certificate, so its
# designated requirement falls back to the binary's cdhash -- a hash of the
# actual code. Every rebuild produces different code, so every rebuild has a
# different cdhash, so every in-app update looks like a brand-new app to
# macOS and the user is prompted to re-grant Accessibility/Screen Recording
# again. That is the "permission loop": update -> re-prompt -> update ->
# re-prompt, forever.
#
# A REAL identity (even a self-signed one) changes what the designated
# requirement keys on: the certificate's leaf hash + bundle identifier.
# Signing every build with the SAME certificate makes the identity stable
# across updates, so the grant the user approved once keeps matching and the
# loop disappears. No Apple Developer Program membership is needed -- Apple
# only requires a paid account for Developer ID (Gatekeeper) trust, not for
# a stable code identity that TCC keys permissions to.
#
# The bundle identifier must stay pinned too: build_pyinstaller.py already
# passes --osx-bundle-identifier=com.cweamy.creams-macro-anime-expeditions.
# The bundle id is carried here in the OU (organizationalUnit) field of the
# certificate subject so this identity is visibly paired with that app; the
# CN stays short on purpose -- X.509 caps commonName at 64 characters, and
# "Creams Macro Code Signing (com.cweamy.creams-macro-anime-expeditions)"
# would be 70 and blow up inside `openssl req`.
#
# RUN ONCE, ON ANY MACHINE WITH OpenSSL
# ---------------------------------------------------------------------------
#     bash tools/make_macos_codesign_cert.sh
#
# Works on macOS (LibreSSL) and on Linux/Windows-Git-Bash (OpenSSL) -- the
# commands below stick to the config-file form that both implementations
# accept. The resulting .p12 is what CI imports; keep it (and the password)
# secret, exactly like any signing key.
#
# Idempotent-safe: a second run refuses to overwrite the existing cert so
# you can't accidentally ROTATE the identity out from under everyone who has
# already granted permissions. Pass --force only when you deliberately want
# a fresh identity (which will make macOS re-prompt all existing users).
#
# Where files land (override with OUT_DIR=...):
#     ~/.creams-macro-codesign/
#         codesign.key.pem    private key (unencrypted on disk -- chmod 600)
#         codesign.cert.pem   the self-signed certificate
#         codesign.p12        PKCS#12 bundle (key + cert), password-protected
#         codesign.p12.b64    single-line base64 of the .p12 (for the secret)
# ---------------------------------------------------------------------------
set -euo pipefail

# --- identity constants -----------------------------------------------------
# The certificate common name. This same string is what the release workflow
# exports as MACOS_CODESIGN_IDENTITY (see .github/workflows/release.yml and
# macos-asset.yml), so codesign finds this exact cert. Keep it <= 64 chars:
# X.509 hard-caps commonName at 64, so the bundle identifier rides in OU
# instead (still part of the subject, still pairs cert <-> app).
CN="Creams Macro Code Signing"
OU="com.cweamy.creams-macro-anime-expeditions"
# Apostrophe-free on purpose: openssl config strips the quote chars, so
# "Cream's Macro" would land in the subject as "Creams Macro" anyway. This
# matches the exe/build's own EXE_NAME spelling.
ORG="Creams Macro"
COUNTRY="US"
VALID_DAYS=3650          # 10 years -- re-create only if the key is lost/rotated

# Where the generated material goes.
OUT_DIR="${OUT_DIR:-$HOME/.creams-macro-codesign}"

KEY="$OUT_DIR/codesign.key.pem"
CERT="$OUT_DIR/codesign.cert.pem"
P12="$OUT_DIR/codesign.p12"
P12_B64="$OUT_DIR/codesign.p12.b64"

# GitHub repo the secrets belong to (used only in the printed `gh` commands).
GITHUB_REPO="Cweamy/Anime-Expeditions-Creams-Macro"

usage() {
    cat >&2 <<EOF
Usage: bash $0 [--force]

Generates a reusable self-signed macOS code-signing certificate and a
password-protected .p12 export, then prints the exact GitHub Actions secret
commands to store them as MACOS_CODESIGN_P12 / MACOS_CODESIGN_PASSWORD.

Options:
  --force   Overwrite an existing certificate in $OUT_DIR. WARNING: this
            rotates the identity, so macOS will re-prompt every user who has
            already granted Accessibility/Input Monitoring/Screen Recording.
EOF
}

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force)
            FORCE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            usage
            exit 2
            ;;
    esac
done

mkdir -p "$OUT_DIR"

# --- idempotency guard ------------------------------------------------------
# Refuse to overwrite unless --force: silently regenerating would change the
# cert (new key, new leaf hash) and silently break the "same app to TCC"
# guarantee for every build signed with the old one.
if [ -e "$KEY" ] || [ -e "$CERT" ] || [ -e "$P12" ]; then
    if [ "$FORCE" -eq 0 ]; then
        echo "ERROR: a codesign identity already exists in $OUT_DIR" >&2
        echo "       Refusing to overwrite it. Re-run with --force ONLY if you" >&2
        echo "       intend to rotate the identity (all existing users will need" >&2
        echo "       to re-grant macOS permissions afterward)." >&2
        exit 1
    fi
    echo "WARNING: --force given -- rotating the code-signing identity." >&2
    echo "         Existing macOS permission grants will stop matching and every" >&2
    echo "         user must re-approve Accessibility/Input Monitoring/Screen" >&2
    echo "         Recording after the next update." >&2
fi

# --- password ----------------------------------------------------------------
# A fresh random password per certificate. It only protects the .p12 at rest
# and during CI import; the private key is still what actually signs.
PASSWORD="$(openssl rand -hex 16)"

# --- openssl config ----------------------------------------------------------
# Written to a temp file rather than passed with -addext because macOS ships
# LibreSSL, whose `openssl req` predates/omits -addext. A config file is the
# one spelling both LibreSSL and OpenSSL 1.1+/3.x accept.
CONF="$(mktemp "${TMPDIR:-/tmp}/codesign-cfg.XXXXXX")"
cat > "$CONF" <<EOF
[req]
distinguished_name = dn
x509_extensions    = v3_codesign
prompt             = no
default_md         = sha256

[dn]
CN = $CN
O  = $ORG
OU = $OU
C  = $COUNTRY

[v3_codesign]
# This is a LEAF identity, not a CA: the certificate signs code, it does not
# mint other certificates. Keeping CA:FALSE matters because codesign wants a
# leaf (non-CA) identity to derive the designated requirement from.
basicConstraints = critical, CA:FALSE
keyUsage         = critical, digitalSignature
extendedKeyUsage = codeSigning
EOF

# --- generate the key + self-signed cert ------------------------------------
# RSA 2048 (current Apple guidance for code signing; small enough that CI
# import + signing stay fast, more than strong enough for a self-signed
# identity whose only job is to be stable). -nodes leaves the key unencrypted
# on disk; it is chmod 600 below and the .p12 carries its own password.
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY" \
    -out    "$CERT" \
    -days   "$VALID_DAYS" \
    -config "$CONF" \
    -extensions v3_codesign

chmod 600 "$KEY"
chmod 600 "$CERT"

# --- export the PKCS#12 ------------------------------------------------------
# CI can't import a bare key+cert pair non-interactively as easily as a .p12,
# so bundle both into one password-protected file (the format `security
# import` in the workflow consumes directly).
openssl pkcs12 -export \
    -out    "$P12" \
    -inkey  "$KEY" \
    -in     "$CERT" \
    -name   "$CN" \
    -passout "pass:$PASSWORD"
chmod 600 "$P12"

# Single-line base64. -A disables line wrapping; GitHub Actions secrets can
# hold a single-line string cleanly, and the workflow decodes it with
# `openssl base64 -d -A` (portable across the BSD base64(1) flag split).
openssl base64 -A -in "$P12" -out "$P12_B64"

# --- fingerprint (an alternate identity string) ------------------------------
# codesign accepts either the common name or the SHA-1 fingerprint. Print both
# so the owner can pick whichever is less ambiguous on a given keychain.
FINGERPRINT="$(openssl x509 -in "$CERT" -noout -fingerprint -sha1 \
    | sed 's/.*Fingerprint=//')"

rm -f "$CONF"

# --- report -------------------------------------------------------------------
cat <<EOF
================================================================================
macOS code-signing identity generated in:
    $OUT_DIR

  Identity (common name):   $CN
  SHA-1 fingerprint:        $FINGERPRINT
  (either works as MACOS_CODESIGN_IDENTITY -- CI uses the common name)

Files (treat the .p12 and .key.pem as secrets):
    $P12
    $P12_B64
    $CERT
    $KEY

Add these two GitHub Actions repo secrets
(repo: $GITHUB_REPO), or do it manually under
repo Settings > Secrets and variables > Actions > New repository secret:

    gh secret set MACOS_CODESIGN_P12 --repo $GITHUB_REPO < "$P12_B64"
    gh secret set MACOS_CODESIGN_PASSWORD --repo $GITHUB_REPO --body "$PASSWORD"

Manual fallback:
    MACOS_CODESIGN_P12      = the ENTIRE contents of $P12_B64 (single line)
    MACOS_CODESIGN_PASSWORD = $PASSWORD

The .p12 password is:
    $PASSWORD
Store it somewhere safe. Anyone with the .p12 + this password can sign code
as this app.

To sign a LOCAL macOS build with this identity (instead of ad-hoc):
    export MACOS_CODESIGN_IDENTITY="$CN"
    python build_pyinstaller.py
================================================================================
EOF
