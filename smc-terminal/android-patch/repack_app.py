"""Rebuilds Chaquopy's app.imy with the patched Python files (every other entry byte-identical, same compression)
and records its new SHA-1 in build.json, so Chaquopy extracts the new code.

Usage: python3 repack_app.py ORIGINAL_APP_IMY PATCHED_DIR OUT_APP_IMY BUILD_JSON
PATCHED_DIR holds the patched files at their paths inside app.imy (android_gateway.py, app/article_retrieval.py).
"""
import hashlib
import json
import os
import sys
import zipfile

src, patched, dst, build_json = sys.argv[1:5]
changed = {}
for root, _, files in os.walk(patched):
    for name in files:
        full = os.path.join(root, name)
        changed[os.path.relpath(full, patched).replace(os.sep, "/")] = full
with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst + ".new", "w") as zout:
    seen = set()
    for info in zin.infolist():
        data = zin.read(info.filename)
        if info.filename in changed:
            data = open(changed[info.filename], "rb").read()
            seen.add(info.filename)
        zout.writestr(info, data, compress_type=info.compress_type)
missing = set(changed) - seen
if missing:
    sys.exit(f"not in app.imy: {sorted(missing)}")
os.replace(dst + ".new", dst)
sha = hashlib.sha1(open(dst, "rb").read()).hexdigest()
build = json.load(open(build_json))
build["assets"]["app.imy"] = sha
json.dump(build, open(build_json, "w"))
print("app.imy", sha)
