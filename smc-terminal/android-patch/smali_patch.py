"""Hooks MainActivity (smali, decoded by apktool) into com.sharx.invest.ReaderHooks:

- external(Uri): links leaving the app open the in-app reader / the app chart instead of another app;
- openWeb(): installs the SharxReader bridge once the app WebView is the content view (before its first page loads);
- onBackPressed(): inside the reader the back key goes back a page or closes the reader;
- onDestroy(): releases the reader and fetcher WebViews.

Usage: python3 smali_patch.py path/to/smali/com/sharx/invest/MainActivity.smali
"""
import sys

path = sys.argv[1]
s = open(path, encoding="utf-8").read()
if "ReaderHooks" in s:
    sys.exit("already patched")

start = s.index(".method private external(Landroid/net/Uri;)V")
end = s.index(".end method", start) + len(".end method")
s = s[:start] + """.method private external(Landroid/net/Uri;)V
    .locals 0

    invoke-static {p0, p1}, Lcom/sharx/invest/ReaderHooks;->external(Landroid/app/Activity;Landroid/net/Uri;)V

    return-void
.end method""" + s[end:]

ow = s.index(".method private openWeb()V")
owe = s.index(".end method", ow)
body = s[ow:owe]
anchor = "    invoke-virtual {p0, v0}, Lcom/sharx/invest/MainActivity;->setContentView(Landroid/view/View;)V\n"
assert body.count(anchor) == 1, "openWeb: setContentView(web) not found once"
body = body.replace(anchor, anchor + """
    iget-object v0, p0, Lcom/sharx/invest/MainActivity;->web:Landroid/webkit/WebView;

    invoke-static {p0, v0}, Lcom/sharx/invest/ReaderHooks;->install(Landroid/app/Activity;Landroid/webkit/WebView;)V
""")
s = s[:ow] + body + s[owe:]

anchor = ".method public onBackPressed()V\n    .locals 4\n"
assert s.count(anchor) == 1, "onBackPressed not found"
s = s.replace(anchor, anchor + """
    invoke-static {p0}, Lcom/sharx/invest/ReaderHooks;->back(Landroid/app/Activity;)Z

    move-result v0

    if-eqz v0, :sharx_reader_not_open

    return-void

    :sharx_reader_not_open
""")

anchor = ".method protected onDestroy()V\n    .locals 2\n"
assert s.count(anchor) == 1, "onDestroy not found"
s = s.replace(anchor, anchor + """
    invoke-static {}, Lcom/sharx/invest/ReaderHooks;->destroy()V
""")
open(path, "w", encoding="utf-8").write(s)
print("MainActivity hooked")
