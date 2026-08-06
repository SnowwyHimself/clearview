#!/bin/bash
# Build the ClearView macOS app (.app) and package it as a .dmg.
#
# Steps: PyInstaller build -> scrub any local build-machine username out of the
# bundle (metadata files can embed it) -> create a drag-to-Applications .dmg.
#
# Run from the project root:  bash packaging/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-1.0.0}"
APP="dist/ClearView.app"
DMG="dist/ClearView-${VERSION}-macOS-arm64.dmg"

# Fetch arm64 ffmpeg/ffprobe if not already present (kept out of git).
if [ ! -x build_assets/mac/ffmpeg ] || [ ! -x build_assets/mac/ffprobe ]; then
  echo ">> Fetching macOS arm64 ffmpeg/ffprobe…"
  mkdir -p build_assets/mac
  for tool in ffmpeg ffprobe; do
    curl -fsSL -o "build_assets/mac/$tool.zip" \
      "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/$tool.zip"
    (cd build_assets/mac && unzip -oq "$tool.zip" && rm -f "$tool.zip" && chmod +x "$tool")
  done
fi

echo ">> Building app with PyInstaller…"
rm -rf build dist
pyinstaller clearview.spec --noconfirm --clean >/dev/null

echo ">> Scrubbing build-machine identity from the bundle…"
# Metadata/RECORD files can embed the builder's absolute home path. Replace the
# current username with a neutral token in any *text* file that contains it.
USERNAME="$(id -un)"
if [ -n "$USERNAME" ]; then
  # shellcheck disable=SC2044
  for f in $(grep -rIl "$USERNAME" "$APP" 2>/dev/null || true); do
    LC_ALL=C sed -i '' "s/$USERNAME/user/g" "$f"
  done
fi
# Drop PyInstaller's build/ dir (contains xref HTML with absolute paths).
rm -rf build

echo ">> Verifying no personal name remains…"
if grep -rIl -e "$USERNAME" "$APP" >/dev/null 2>&1; then
  echo "!! WARNING: username still present in bundle" >&2
  exit 1
fi

echo ">> Creating .dmg…"
STAGE="dist/dmg_stage"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "ClearView" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo ">> Done: $DMG"
du -sh "$DMG"
