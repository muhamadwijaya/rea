#!/usr/bin/env bash
# Rebuilds the patched SHARX INVEST Android APK from the original APK (SHARX-INVEST-AI-Fix9.apk).
#
#   APKTOOL=apktool_2.10.0.jar BUILD_TOOLS=build-tools/36 ANDROID_JAR=android-35/android.jar \
#     ./build.sh SHARX-INVEST-AI-Fix9.apk release.jks store-pass.txt SHARX-INVEST-AI-Fix13.apk
#
# Needs Java 17+, python3 (with pip), npm, curl, unzip, zip and patch. The keystore signs the result: keep it private
# and reuse it for later versions, otherwise Android asks to uninstall the app before installing an update.
#
# JARVIS on the phone bundles files of Mark LVII (FatihMakes, CC BY-NC 4.0, non-commercial use only) from the pinned
# commit below: from a checkout given as MARK_LVII=/path/to/Mark-LVII, otherwise downloaded from GitHub. Their SHA-256
# is checked, as is the websockets wheel (BSD-3-Clause) taken from PyPI.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
orig="$(realpath "$1")"; ks="$(realpath "$2")"; pass="$(realpath "$3")"; out="$(realpath -m "$4")"
: "${APKTOOL:?path of apktool jar}" "${BUILD_TOOLS:?Android build-tools dir}" "${ANDROID_JAR:?android.jar of API 35}"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
MARK_COMMIT=752ff618a4e6a4bf2e675866b993071d739cfce2
check() { echo "$1  $2" | sha256sum -c --quiet - || { echo "checksum mismatch: $2" >&2; exit 1; }; }

# 1) decode code and assets; resources stay as they are
java -jar "$APKTOOL" d -r -f "$orig" -o "$work/raw"
python3 "$here/smali_patch.py" "$work/raw/smali/com/sharx/invest/MainActivity.smali"

# 2) web UI: in-app reader, TradingView symbol route, reader flow of the voice assistant, JARVIS tab (jarvis.js and
#    jarvis.css shared with the desktop app; JARVIS on the phone: jarvis-phone.js/.css, jarvis-worklet.js)
for d in "$here"/web/*.diff; do patch -p1 -d "$work/raw" < "$d"; done
cp "$here/web/reader.js" "$work/raw/assets/wwwroot/js/reader.js"
cp "$here/../gateway/wwwroot/js/jarvis.js" "$work/raw/assets/wwwroot/js/jarvis.js"
cp "$here/../gateway/wwwroot/css/jarvis.css" "$work/raw/assets/wwwroot/css/jarvis.css"
cp "$here/web/jarvis-phone.js" "$here/web/jarvis-worklet.js" "$work/raw/assets/wwwroot/js/"
cp "$here/web/jarvis-phone.css" "$work/raw/assets/wwwroot/css/"

# 3) page text extractor: Mozilla Readability (Apache-2.0) + extract.js
(cd "$work" && npm pack @mozilla/readability@0.5.0 --silent >/dev/null && tar xzf mozilla-readability-0.5.0.tgz)
mkdir -p "$work/raw/assets/reader"
cp "$work/package/Readability.js" "$work/raw/assets/reader/Readability.js"
cp "$work/package/LICENSE.md" "$work/raw/assets/reader/Readability-LICENSE.md"
cp "$here/assets/reader/extract.js" "$work/raw/assets/reader/extract.js"

# 4) engine (Chaquopy app.imy): the files named by the diffs (+++ b/<path>) are patched and repacked. New modules:
#    python/add (JARVIS on the phone), Mark LVII's prompt and memory manager (mark_lvii/), websockets (Live API client)
mkdir -p "$work/add/mark_lvii" "$work/wheels"
cp -r "$here/python/add/." "$work/add/"
mark="${MARK_LVII:-}"
if [ -z "$mark" ]; then
  curl -fsSL "https://github.com/FatihMakes/Mark-LVII/archive/$MARK_COMMIT.zip" -o "$work/mark.zip"
  unzip -q "$work/mark.zip" -d "$work/mark"; mark="$work/mark/Mark-LVII-$MARK_COMMIT"
fi
check d44a83a20d2d515fd58318098ff8c91272bfd48647a69237da206253bd07f419 "$mark/core/prompt.txt"
check 3ecf5a9d80484bec73eef67e54c7d7e2880c726e4bfac58d05b1a14b6e1988ce "$mark/memory/memory_manager.py"
check 46089d78b1244dc7080e7da468e1c63c5a8cfe70c10b75870109c6e6d7458e6f "$mark/LICENSE"
cp "$mark/core/prompt.txt" "$mark/memory/memory_manager.py" "$mark/LICENSE" "$work/add/mark_lvii/"
cp "$here/python/mark_lvii/__init__.py" "$here/python/mark_lvii/NOTICE" "$work/add/mark_lvii/"
echo "$MARK_COMMIT" > "$work/add/mark_lvii/COMMIT"
python3 -m pip download "websockets==15.0.1" --no-deps --only-binary=:all: --platform any --python-version 3.13 -q -d "$work/wheels"
check f7a866fbc1e97b5c617ee4116daaa09b722101d4a3c170c787450ba409f9736f "$work/wheels/websockets-15.0.1-py3-none-any.whl"
unzip -q "$work/wheels/websockets-15.0.1-py3-none-any.whl" 'websockets/*' -x '*.c' '*.pyi' -d "$work/add"
unzip -q -p "$work/wheels/websockets-15.0.1-py3-none-any.whl" 'websockets-15.0.1.dist-info/LICENSE' > "$work/add/websockets/LICENSE"
files=$(sed -n 's|^+++ b/||p' "$here"/python/*.diff)
mkdir -p "$work/py" "$work/patched"
unzip -q "$work/raw/assets/chaquopy/app.imy" $files -d "$work/py"
for d in "$here"/python/*.diff; do patch -p1 -d "$work/py" < "$d"; done
for f in $files; do mkdir -p "$work/patched/$(dirname "$f")"; cp "$work/py/$f" "$work/patched/$f"; done
python3 "$here/repack_app.py" "$work/raw/assets/chaquopy/app.imy" "$work/patched" "$work/app.imy" "$work/raw/assets/chaquopy/build.json" "$work/add"
cp "$work/app.imy" "$work/raw/assets/chaquopy/app.imy"

# 5) ReaderHooks + JarvisHooks → classes2.dex
mkdir -p "$work/classes" "$work/dex"
javac -source 8 -target 8 -Xlint:-options -encoding UTF-8 -bootclasspath "$ANDROID_JAR" -d "$work/classes" \
  "$here/java/com/sharx/invest/ReaderHooks.java" "$here/java/com/sharx/invest/JarvisHooks.java"
"$BUILD_TOOLS/d8" --min-api 26 --release --lib "$ANDROID_JAR" --output "$work/dex" $(find "$work/classes" -name '*.class')

# 6) build, add the dex, align, sign
java -jar "$APKTOOL" b "$work/raw" -o "$work/unsigned.apk"
cp "$work/dex/classes.dex" "$work/classes2.dex"
(cd "$work" && zip -q -0 unsigned.apk classes2.dex)
"$BUILD_TOOLS/zipalign" -p -f 4 "$work/unsigned.apk" "$work/aligned.apk"
"$BUILD_TOOLS/apksigner" sign --ks "$ks" --ks-pass "file:$pass" --out "$out" "$work/aligned.apk"
"$BUILD_TOOLS/apksigner" verify "$out"
echo "built $out"
