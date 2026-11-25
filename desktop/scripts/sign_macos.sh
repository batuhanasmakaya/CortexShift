#!/usr/bin/env bash
# Run only on an approved release runner with Apple Developer ID credentials.
set -euo pipefail
umask 077
: "${APPLE_CERTIFICATE:?}" "${APPLE_CERTIFICATE_PASSWORD:?}" "${APPLE_SIGNING_IDENTITY:?}"
: "${APPLE_TEAM_ID:?}" "${APPLE_API_ISSUER:?}" "${APPLE_API_KEY:?}" "${APPLE_API_KEY_P8:?}"
: "${TAURI_SIGNING_PRIVATE_KEY:?}" "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:?}" "${RUNNER_TEMP:?}"
if [[ "$APPLE_SIGNING_IDENTITY" != 'Developer ID Application: '* ]]; then
  echo 'A Developer ID Application identity is required.' >&2
  exit 1
fi

scratch=$(mktemp -d "$RUNNER_TEMP/cortexshift-sign.XXXXXX")
keychain="$scratch/release.keychain-db"
original=()
while IFS= read -r line; do
  line=${line#*\"}
  original+=("${line%\"}")
done < <(security list-keychains -d user)
cleanup() {
  security list-keychains -d user -s "${original[@]}" || true
  security delete-keychain "$keychain" >/dev/null 2>&1 || true
  rm -rf -- "$scratch"
}
trap cleanup EXIT
password=$(openssl rand -hex 24)
printf '%s' "$APPLE_CERTIFICATE" | base64 -D > "$scratch/identity.p12"
printf '%s' "$APPLE_API_KEY_P8" | base64 -D > "$scratch/key.p8"
export APPLE_API_KEY_PATH="$scratch/key.p8"
security create-keychain -p "$password" "$keychain"
security set-keychain-settings -lut 3600 "$keychain"
security unlock-keychain -p "$password" "$keychain"
security import "$scratch/identity.p12" -k "$keychain" -P "$APPLE_CERTIFICATE_PASSWORD" -T /usr/bin/codesign >/dev/null
security set-key-partition-list -S apple-tool:,apple: -s -k "$password" "$keychain" >/dev/null
security list-keychains -d user -s "$keychain" "${original[@]}"
unset APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD APPLE_API_KEY_P8 password

uv run --locked --no-dev --group desktop-build python desktop/scripts/freeze_bridge.py
uv run --locked --no-dev python desktop/scripts/release.py --signing-config "$RUNNER_TEMP/signing.json"
(
  cd desktop
  npm run tauri -- build --ci --target aarch64-apple-darwin --config src-tauri/bundle.conf.json --config "$RUNNER_TEMP/signing.json" --bundles app,dmg -- --locked
)

bundle=desktop/src-tauri/target/aarch64-apple-darwin/release/bundle
app="$bundle/macos/CortexShift.app"
dmgs=("$bundle"/dmg/*.dmg)
[[ ${#dmgs[@]} -eq 1 && -f "${dmgs[0]}" ]]
dmg=${dmgs[0]}
xcrun stapler validate "$app"
xcrun notarytool submit "$dmg" --key "$APPLE_API_KEY_PATH" --key-id "$APPLE_API_KEY" \
  --issuer "$APPLE_API_ISSUER" --wait --output-format json > "$scratch/notarization.json"
uv run --locked --no-dev python -c 'import json, sys; sys.exit(0 if json.load(open(sys.argv[1]))["status"] == "Accepted" else 1)' "$scratch/notarization.json"
xcrun stapler staple "$dmg"
xcrun stapler validate "$dmg"
spctl --assess --type open --context context:primary-signature "$dmg"
