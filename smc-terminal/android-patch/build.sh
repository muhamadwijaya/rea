#!/usr/bin/env bash
# Rebuilds the patched SHARX INVEST Android APK from the original APK (SHARX-INVEST-AI-Fix9.apk).
#
#   APKTOOL=apktool_2.10.0.jar BUILD_TOOLS=build-tools/36 ANDROID_JAR=android-35/android.jar \
#     ./build.sh SHARX-INVEST-AI-Fix9.apk release.jks store-pass.txt SHARX-INVEST-AI-Fix10.apk
#
# Needs Java 17+, python3, npm, unzip, zip and patch. The keystore signs the result: keep it private and reuse it
# for later versions, otherwise Android asks to uninstall the app before installing an update.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
orig="$(realpath "$1")"; ks="$(realpath "$2")"; pass="$(realpath "$3")"; out="$(realpath -m "$4")"
: "${APKTOOL:?path of apktool jar}" "${BUILD_TOOLS:?Android build-tools dir}" "${ANDROID_JAR:?android.jar of API 35}"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

# 1) decode code and assets; resources stay as they are
java -jar "$APKTOOL" d -r -f "$orig" -o "$work/raw"
python3 "$here/smali_patch.py" "$work/raw/smali/com/sharx/invest/MainActivity.smali"

# 2) web UI: in-app reader, TradingView symbol route, reader flow of the voice assistant
for d in "$here"/web/*.diff; do patch -p1 -d "$work/raw" < "$d"; done
cp "$here/web/reader.js" "$work/raw/assets/wwwroot/js/reader.js"

# 3) page text extractor: Mozilla Readability (Apache-2.0) + extract.js
(cd "$work" && npm pack @mozilla/readability@0.5.0 --silent >/dev/null && tar xzf mozilla-readability-0.5.0.tgz)
mkdir -p "$work/raw/assets/reader"
cp "$work/package/Readability.js" "$work/raw/assets/reader/Readability.js"
cp "$work/package/LICENSE.md" "$work/raw/assets/reader/Readability-LICENSE.md"
cp "$here/assets/reader/extract.js" "$work/raw/assets/reader/extract.js"

# 4) engine (Chaquopy app.imy): the files named by the diffs (+++ b/<path>) are patched and repacked
files=$(sed -n 's|^+++ b/||p' "$here"/python/*.diff)
mkdir -p "$work/py" "$work/patched"
unzip -q "$work/raw/assets/chaquopy/app.imy" $files -d "$work/py"
for d in "$here"/python/*.diff; do patch -p1 -d "$work/py" < "$d"; done
for f in $files; do mkdir -p "$work/patched/$(dirname "$f")"; cp "$work/py/$f" "$work/patched/$f"; done
python3 "$here/repack_app.py" "$work/raw/assets/chaquopy/app.imy" "$work/patched" "$work/app.imy" "$work/raw/assets/chaquopy/build.json"
cp "$work/app.imy" "$work/raw/assets/chaquopy/app.imy"

# 5) ReaderHooks → classes2.dex
mkdir -p "$work/classes" "$work/dex"
javac -source 8 -target 8 -Xlint:-options -encoding UTF-8 -bootclasspath "$ANDROID_JAR" -d "$work/classes" \
  "$here/java/com/sharx/invest/ReaderHooks.java"
"$BUILD_TOOLS/d8" --min-api 26 --release --lib "$ANDROID_JAR" --output "$work/dex" $(find "$work/classes" -name '*.class')

# 6) build, add the dex, align, sign
java -jar "$APKTOOL" b "$work/raw" -o "$work/unsigned.apk"
cp "$work/dex/classes.dex" "$work/classes2.dex"
(cd "$work" && zip -q -0 unsigned.apk classes2.dex)
"$BUILD_TOOLS/zipalign" -p -f 4 "$work/unsigned.apk" "$work/aligned.apk"
"$BUILD_TOOLS/apksigner" sign --ks "$ks" --ks-pass "file:$pass" --out "$out" "$work/aligned.apk"
"$BUILD_TOOLS/apksigner" verify "$out"
echo "built $out"
