package com.sharx.invest;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebBackForwardList;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.lang.ref.WeakReference;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * In-app news reader and browser-based article fetcher of the Android host.
 *
 * <ul>
 * <li>Links that used to leave the app (news, "Buka sumber") open in a reader page on top of the app. Once the article
 * page has rendered, its readable text is taken from that page (Mozilla Readability, the page as the reader sees it)
 * and handed to the app's web UI, which reads it aloud and has the AI analyse exactly that text.</li>
 * <li>TradingView links (ticker tape, widgets) open the app's own chart page of the symbol.</li>
 * <li>{@link #fetch} lets the Python engine load an article in a hidden WebView when plain HTTP gets no article text
 * (JavaScript redirects such as Google News, sites that only answer a real browser).</li>
 * </ul>
 * Every entry point catches its own failures: the reader must never take the app down.
 */
public final class ReaderHooks {
    private static final String TAG = "SharxReader";
    private static final Handler UI = new Handler(Looper.getMainLooper());
    private static final int MIN_CHARS = 300;
    private static final int READER_POLL_MS = 2500, READER_MAX_POLLS = 48;  // keep trying ~2 minutes while open
    // opened by the assistant: closed again once the text is taken; given up after AUTO_MAX_MS, or AUTO_CHECK_MS after
    // a verification page appeared that nobody touched
    private static final long AUTO_MAX_MS = 30000, AUTO_CHECK_MS = 6000, AUTO_CLOSE_MS = 700;
    private static final int NAVY = 0xFF001B33, BAR = 0xFF04223F, TEXT = 0xFFEDF3FB, ACCENT = 0xFF99FF00,
            RED = 0xFFFF4D6A, DIM = 0xFF8FA6C3;

    private static WeakReference<Activity> activity = new WeakReference<>(null);
    private static WebView main;
    private static String script;  // Readability + extractor, evaluated in article pages

    // visible reader
    private static FrameLayout overlay;
    private static LinearLayout column;
    private static WebView reader;
    private static TextView titleView, statusView;
    private static ProgressBar progress;
    private static Session session;

    // hidden fetcher used by the engine
    private static final Object FETCH_LOCK = new Object();
    private static WebView fetcher;
    private static Fetch fetch;

    private ReaderHooks() {
    }

    static final class Session {
        String url, id, title, symbol, source, lang;
        boolean delivered, extracting, ended;
        boolean auto;  // opened by the assistant (closes itself); false once the user touches the page
        int polls;
        long started, checkSince;
    }

    interface Done {
        void result(String json);
    }

    static final class Fetch {
        String url, title;
        Done done;
        boolean finished, extracting, scheduled;  // scheduled: one poll chain at a time
        long deadline;
    }

    static final class Result {
        String url = "", title = "", text = "", site = "", byline = "";
        boolean blocked;

        static Result parse(String value) {
            if (value == null || value.isEmpty() || "null".equals(value)) return null;
            try {
                JSONObject o = new JSONObject(value);
                Result r = new Result();
                r.url = o.optString("url", "");
                r.title = o.optString("title", "");
                r.text = o.optString("text", "");
                r.site = o.optString("site", "");
                r.byline = o.optString("byline", "");
                r.blocked = o.optBoolean("blocked", false);
                return r;
            } catch (Exception e) {
                return null;
            }
        }

        String json() {
            try {
                JSONObject o = new JSONObject();
                o.put("url", url);
                o.put("title", title);
                o.put("text", text);
                o.put("site", site);
                o.put("byline", byline);
                return o.toString();
            } catch (Exception e) {
                return null;
            }
        }
    }

    // ------------------------------------------------------------------ entry points from MainActivity

    /** Called once the app WebView exists (MainActivity.openWeb), before its first page loads. */
    public static void install(Activity a, WebView web) {
        try {
            activity = new WeakReference<>(a);
            main = web;
            script = loadScript(a);
            web.addJavascriptInterface(new Bridge(), "SharxReader");
        } catch (Throwable t) {
            Log.w(TAG, "install", t);
        }
    }

    /** A link of the app page wants to leave the app: TradingView opens the app chart, the rest the reader. */
    public static void external(Activity a, Uri uri) {
        try {
            if (uri == null) return;
            String scheme = lower(uri.getScheme());
            if (!"http".equals(scheme) && !"https".equals(scheme)) return;
            if (activity.get() == null) activity = new WeakReference<>(a);
            String tv = tradingViewSymbol(uri);
            if (tv != null) {
                openChart(tv);
                return;
            }
            Session s = new Session();
            s.url = uri.toString();
            s.title = "";
            openReader(s);
        } catch (Throwable t) {
            Log.w(TAG, "external", t);
            try {
                a.startActivity(new Intent(Intent.ACTION_VIEW, uri));
            } catch (Throwable ignored) {
            }
        }
    }

    /** Back key: inside the reader it goes back a page or closes the reader. True when it was handled. */
    public static boolean back(Activity a) {
        try {
            if (overlay == null || overlay.getVisibility() != View.VISIBLE) return false;
            if (reader != null) {
                WebBackForwardList history = reader.copyBackForwardList();
                int i = history.getCurrentIndex();
                if (i > 0 && !waiting(history.getItemAtIndex(i - 1).getUrl())) {
                    reader.goBack();
                    return true;
                }
            }
            closeReader();
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "back", t);
            return false;
        }
    }

    public static void destroy() {
        try {
            UI.removeCallbacksAndMessages(null);
            if (fetch != null) finishFetch(fetch, null);
            if (reader != null) reader.destroy();
            if (fetcher != null) fetcher.destroy();
            if (main != null) main.removeJavascriptInterface("SharxReader");
        } catch (Throwable ignored) {
        }
        reader = null;
        fetcher = null;
        overlay = null;
        column = null;
        titleView = null;
        statusView = null;
        progress = null;
        session = null;
        fetch = null;
        main = null;
    }

    // ------------------------------------------------------------------ bridge of the app page

    public static final class Bridge {
        @JavascriptInterface
        public boolean available() {
            return true;
        }

        @JavascriptInterface
        public void open(final String json) {
            UI.post(new Runnable() {
                public void run() {
                    openJson(json);
                }
            });
        }

        @JavascriptInterface
        public void close() {
            UI.post(new Runnable() {
                public void run() {
                    closeReader();
                }
            });
        }

        @JavascriptInterface
        public void status(final String text) {
            UI.post(new Runnable() {
                public void run() {
                    setStatus(text);
                }
            });
        }
    }

    private static void openJson(String json) {
        try {
            JSONObject o = new JSONObject(json);
            Session s = new Session();
            s.url = o.optString("url", "");
            s.id = text(o, "id");
            s.title = o.optString("title", "");
            s.symbol = text(o, "symbol");
            s.source = o.optString("source", "");
            s.lang = o.optString("lang", "");
            s.auto = o.optBoolean("auto", false);
            openReader(s);
        } catch (Throwable t) {
            Log.w(TAG, "open", t);
        }
    }

    // ------------------------------------------------------------------ visible reader

    private static void openReader(Session s) {
        Activity a = activity.get();
        if (a == null || a.isFinishing() || !web(s.url)) {
            fail(s, "Halaman baca tidak tersedia.");
            return;
        }
        Session old = session;
        if (s.auto && old != null && !old.auto && overlay != null && overlay.getVisibility() == View.VISIBLE) {
            fail(s, "Halaman baca sedang dibuka manual.");  // the assistant never takes over a page the user reads
            return;
        }
        if (old != null && !old.delivered && !old.ended) fail(old, "Diganti halaman lain.");
        ensureOverlay(a);
        // a fresh page per article: no history or script state of the previous one
        if (reader != null) {
            column.removeView(reader);
            reader.destroy();
        }
        reader = new WebView(a);
        configure(reader, false);
        reader.setOnTouchListener(new View.OnTouchListener() {
            public boolean onTouch(View v, MotionEvent e) {
                takeOver();
                return false;  // the page handles the touch as usual
            }
        });
        column.addView(reader, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        s.started = SystemClock.uptimeMillis();
        session = s;
        titleView.setText(TextUtils.isEmpty(s.title) ? host(s.url) : s.title);
        setStatus(s.auto ? "Dibuka otomatis untuk mengambil teks berita; ditutup sendiri setelah teks diambil. Sentuh halaman untuk membaca manual."
                : "Memuat halaman berita di aplikasi…");
        overlay.setVisibility(View.VISIBLE);
        overlay.bringToFront();
        reader.loadUrl(s.url);
        schedulePoll(s);
    }

    private static void ensureOverlay(Activity a) {
        if (overlay != null) return;
        float dp = a.getResources().getDisplayMetrics().density;
        int pad = (int) (6 * dp);
        overlay = new FrameLayout(a);
        overlay.setBackgroundColor(NAVY);
        overlay.setClickable(true);  // touches never reach the app below
        column = new LinearLayout(a);
        column.setOrientation(LinearLayout.VERTICAL);

        LinearLayout bar = new LinearLayout(a);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(pad, pad / 2, pad, pad / 2);
        bar.setBackgroundColor(BAR);
        Button close = button(a, "✕ TUTUP", RED);
        close.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                closeReader();
            }
        });
        titleView = new TextView(a);
        titleView.setTextColor(TEXT);
        titleView.setTextSize(15);
        titleView.setSingleLine(true);
        titleView.setEllipsize(TextUtils.TruncateAt.END);
        titleView.setTypeface(Typeface.DEFAULT_BOLD);
        titleView.setPadding(pad * 2, 0, pad * 2, 0);
        Button speak = button(a, "🔊 BACAKAN", ACCENT);
        speak.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                request("speak");
            }
        });
        Button analyze = button(a, "AI ANALISA", ACCENT);
        analyze.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                request("analyze");
            }
        });
        Button browser = button(a, "BROWSER ↗", DIM);
        browser.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openOutside();
            }
        });
        bar.addView(close);
        bar.addView(titleView, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        bar.addView(speak);
        bar.addView(analyze);
        bar.addView(browser);

        statusView = new TextView(a);
        statusView.setTextColor(ACCENT);
        statusView.setTextSize(12);
        statusView.setPadding(pad * 2, pad / 2, pad * 2, pad / 2);
        statusView.setBackgroundColor(BAR);
        progress = new ProgressBar(a, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);

        column.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        column.addView(statusView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        column.addView(progress, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, (int) (3 * dp)));
        overlay.addView(column, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        overlay.setVisibility(View.GONE);
        a.addContentView(overlay, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private static Button button(Activity a, String label, int color) {
        Button b = new Button(a);
        b.setText(label);
        b.setTextColor(color);
        b.setTextSize(12);
        b.setAllCaps(false);
        b.setBackgroundColor(Color.TRANSPARENT);
        b.setMinWidth(0);
        b.setMinimumWidth(0);
        b.setMinHeight(0);
        b.setMinimumHeight(0);
        b.setPadding(18, 6, 18, 6);
        return b;
    }

    private static void closeReader() {
        if (overlay == null) return;
        Session s = session;
        hide();
        if (s != null && !s.delivered && !s.ended) {
            s.ended = true;
            event(s, "closed", "Halaman ditutup sebelum teks diambil.");
        }
    }

    /** Hides the reader without reporting anything (the page's text was delivered or its failure reported). */
    private static void hide() {
        session = null;
        if (reader != null) {
            reader.stopLoading();
            reader.loadUrl("about:blank");
        }
        if (overlay != null) overlay.setVisibility(View.GONE);
    }

    /** The user touched the page or a button: it is theirs now, it is not closed automatically. */
    private static void takeOver() {
        Session s = session;
        if (s == null || !s.auto) return;
        s.auto = false;
        s.checkSince = 0;
        setStatus(s.delivered ? "Dibuka manual: halaman tidak ditutup otomatis." : "Dibuka manual: teks diambil saat berita tampil; halaman tidak ditutup otomatis.");
    }

    /** The page gave no text: reported to the app, which continues without it; an automatic page closes. */
    private static void fail(Session s, String reason) {
        if (s.ended || s.delivered) return;
        s.ended = true;
        event(s, "failed", reason);
        if (session == s) {
            if (s.auto) hide();
            else setStatus(reason);
        }
    }

    private static void event(Session s, String type, String reason) {
        try {
            JSONObject o = new JSONObject();
            o.put("type", type);
            o.put("id", s.id == null ? JSONObject.NULL : s.id);
            o.put("url", s.url);
            o.put("reason", reason);
            o.put("auto", s.auto);
            emit(o.toString());
        } catch (Throwable t) {
            Log.w(TAG, "event", t);
        }
    }

    private static void openOutside() {
        Activity a = activity.get();
        String url = reader != null ? reader.getUrl() : null;
        if (a == null || !web(url)) return;
        try {
            a.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Throwable ignored) {
        }
    }

    private static void setStatus(String text) {
        if (statusView != null && text != null) statusView.setText(text);
    }

    private static void schedulePoll(final Session s) {
        UI.postDelayed(new Runnable() {
            public void run() {
                if (session != s || s.delivered || s.ended) return;
                if (s.auto && SystemClock.uptimeMillis() - s.started > AUTO_MAX_MS) {
                    fail(s, "Teks berita belum tampil dalam 30 detik.");
                    return;
                }
                if (++s.polls > READER_MAX_POLLS) {
                    setStatus("Teks belum terbaca otomatis. Setelah isi berita tampil, tekan 🔊 BACAKAN.");
                    return;
                }
                extract(s);
                UI.postDelayed(this, READER_POLL_MS);
            }
        }, READER_POLL_MS);
    }

    private static void extract(final Session s) {
        if (reader == null || script == null || s.extracting) return;
        String url = reader.getUrl();
        if (!web(url) || waiting(url)) {
            setStatus("Membuka tautan penerbit…");
            return;
        }
        s.extracting = true;
        reader.evaluateJavascript(script, new ValueCallback<String>() {
            public void onReceiveValue(String value) {
                s.extracting = false;
                if (session != s || s.delivered || s.ended) return;
                Result r = Result.parse(value);
                if (r == null) return;
                if (r.blocked) {
                    if (s.auto) {  // a verification is the user's to do: it is not waited out automatically
                        long now = SystemClock.uptimeMillis();
                        if (s.checkSince == 0) s.checkSince = now;
                        if (now - s.checkSince > AUTO_CHECK_MS) fail(s, "Halaman meminta verifikasi.");
                        else setStatus("Halaman meminta verifikasi. Sentuh halaman untuk menyelesaikannya sendiri; jika tidak, ditutup dan asisten lanjut.");
                        return;
                    }
                    setStatus("Halaman meminta verifikasi. Selesaikan di layar ini; teks diambil otomatis setelah berita tampil.");
                    return;
                }
                if (r.text.length() < MIN_CHARS) {
                    setStatus("Menunggu isi berita tampil…");
                    return;
                }
                if (s.auto && s.checkSince != 0) {  // the check page went away without the user: still not used
                    fail(s, "Halaman meminta verifikasi.");
                    return;
                }
                s.delivered = true;
                deliver(s, r, "text");
                if (s.auto) {
                    setStatus("Teks berita diambil (" + words(r.text) + " kata). Halaman ditutup otomatis.");
                    UI.postDelayed(new Runnable() {
                        public void run() {
                            if (session == s && s.auto) hide();
                        }
                    }, AUTO_CLOSE_MS);
                } else {
                    setStatus("Teks berita diambil dari halaman (" + words(r.text) + " kata). AI membacakan, lalu menganalisa.");
                }
            }
        });
    }

    /** Buttons of the reader: read again / analyse the page's current text. */
    private static void request(final String kind) {
        final Session s = session;
        if (s == null || reader == null || script == null) return;
        takeOver();
        reader.evaluateJavascript(script, new ValueCallback<String>() {
            public void onReceiveValue(String value) {
                if (session != s) return;
                Result r = Result.parse(value);
                if (r == null || r.blocked || r.text.length() < MIN_CHARS) {
                    setStatus("Isi berita belum terbaca di halaman ini. Tunggu halaman selesai dimuat lalu coba lagi.");
                    return;
                }
                s.delivered = true;
                setStatus(("speak".equals(kind) ? "Membacakan ulang " : "AI menganalisa ") + words(r.text) + " kata dari halaman ini…");
                deliver(s, r, kind);
            }
        });
    }

    private static void deliver(Session s, Result r, String type) {
        try {
            JSONObject o = new JSONObject();
            o.put("type", type);
            o.put("id", s.id == null ? JSONObject.NULL : s.id);
            o.put("symbol", s.symbol == null ? JSONObject.NULL : s.symbol);
            o.put("url", s.url);
            o.put("finalUrl", r.url);
            o.put("title", TextUtils.isEmpty(r.title) ? s.title : r.title);
            o.put("itemTitle", s.title);
            o.put("text", r.text);
            o.put("site", r.site);
            o.put("byline", r.byline);
            o.put("lang", s.lang);
            o.put("source", s.source);
            o.put("auto", s.auto);
            emit(o.toString());
        } catch (Throwable t) {
            Log.w(TAG, "deliver", t);
        }
    }

    private static void emit(String json) {
        if (main == null) return;
        String safe = json.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029");
        main.evaluateJavascript("window.__sharxReaderEvent&&window.__sharxReaderEvent(" + safe + ")", null);
    }

    private static void openChart(String tvSymbol) {
        if (main == null) return;
        if (overlay != null && overlay.getVisibility() == View.VISIBLE) closeReader();
        main.evaluateJavascript("window.__sharxOpenTv&&window.__sharxOpenTv(" + JSONObject.quote(tvSymbol) + ")", null);
    }

    // ------------------------------------------------------------------ hidden fetcher (engine)

    /**
     * Loads {@code url} in a hidden WebView and returns the readable text as JSON ({url, title, text, site, byline}),
     * or null (timeout, no text, no activity). Blocks the calling engine thread; never call on the UI thread.
     */
    public static String fetch(String url, String title, int timeoutMs) {
        if (Looper.myLooper() == Looper.getMainLooper() || !web(url) || activity.get() == null || script == null) return null;
        final int timeout = Math.max(5000, Math.min(60000, timeoutMs));
        synchronized (FETCH_LOCK) {  // one hidden page at a time
            final CountDownLatch latch = new CountDownLatch(1);
            final String[] out = new String[1];
            final Fetch f = new Fetch();
            f.url = url;
            f.title = title == null ? "" : title;
            f.deadline = SystemClock.uptimeMillis() + timeout;
            f.done = new Done() {
                public void result(String json) {
                    out[0] = json;
                    latch.countDown();
                }
            };
            UI.post(new Runnable() {
                public void run() {
                    startFetch(f);
                }
            });
            try {
                latch.await(timeout + 4000L, TimeUnit.MILLISECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            UI.post(new Runnable() {
                public void run() {
                    finishFetch(f, null);
                }
            });
            return out[0];
        }
    }

    private static void startFetch(Fetch f) {
        Activity a = activity.get();
        if (a == null || a.isFinishing()) {
            finishFetch(f, null);
            return;
        }
        try {
            if (fetcher == null) {
                fetcher = new WebView(a);
                configure(fetcher, true);
                fetcher.setAlpha(0.01f);  // attached (timers and redirects run) but not visible
                FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(2, 2);
                lp.gravity = Gravity.BOTTOM | Gravity.END;
                a.addContentView(fetcher, lp);
            }
            fetch = f;
            fetcher.loadUrl(f.url);
            pollFetch(f, 4000);
        } catch (Throwable t) {
            Log.w(TAG, "fetch", t);
            finishFetch(f, null);
        }
    }

    private static void pollFetch(final Fetch f, long delay) {
        if (f.scheduled) return;  // a poll is already on its way
        f.scheduled = true;
        UI.postDelayed(new Runnable() {
            public void run() {
                f.scheduled = false;
                if (fetch != f || f.finished || fetcher == null) return;
                if (f.extracting) {
                    pollFetch(f, 1500);
                    return;
                }
                if (SystemClock.uptimeMillis() > f.deadline) {
                    finishFetch(f, null);
                    return;
                }
                String url = fetcher.getUrl();
                if (!web(url) || waiting(url)) {
                    pollFetch(f, 1500);
                    return;
                }
                f.extracting = true;
                fetcher.evaluateJavascript(script, new ValueCallback<String>() {
                    public void onReceiveValue(String value) {
                        f.extracting = false;
                        if (fetch != f || f.finished) return;
                        Result r = Result.parse(value);
                        // a bot check or captcha is left alone: the article stays unread here and can be opened
                        // in the visible reader, where the user decides
                        if (r != null && r.blocked) finishFetch(f, null);
                        else if (r != null && r.text.length() >= MIN_CHARS) finishFetch(f, r.json());
                        else pollFetch(f, 1500);
                    }
                });
            }
        }, delay);
    }

    private static void finishFetch(Fetch f, String json) {
        if (f.finished) return;
        f.finished = true;
        if (fetch == f) {
            fetch = null;
            if (fetcher != null) {
                fetcher.stopLoading();
                fetcher.loadUrl("about:blank");
            }
        }
        if (f.done != null) f.done.result(json);
    }

    // ------------------------------------------------------------------ WebView setup

    private static void configure(final WebView w, final boolean hidden) {
        WebSettings st = w.getSettings();
        st.setJavaScriptEnabled(true);
        st.setDomStorageEnabled(true);
        st.setDatabaseEnabled(true);
        st.setLoadWithOverviewMode(true);
        st.setUseWideViewPort(true);
        st.setBuiltInZoomControls(true);
        st.setDisplayZoomControls(false);
        st.setSupportMultipleWindows(false);
        st.setJavaScriptCanOpenWindowsAutomatically(false);
        st.setMediaPlaybackRequiresUserGesture(true);  // article videos never talk over the voice
        st.setAllowFileAccess(false);
        st.setAllowContentAccess(false);
        st.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        if (hidden) st.setBlockNetworkImage(true);  // the engine needs the text only
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(w, true);
        w.setBackgroundColor(hidden ? Color.TRANSPARENT : Color.WHITE);
        w.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                String scheme = lower(u.getScheme());
                if ("http".equals(scheme) || "https".equals(scheme)) return false;  // stays in this page
                if ("intent".equals(scheme) && request.isForMainFrame()) {  // app links: their web fallback, here
                    String fallback = intentFallback(u.toString());
                    if (fallback != null) view.loadUrl(fallback);
                }
                return true;  // an article never launches another app
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                if (!hidden && view == reader && session != null && !session.delivered) setStatus("Memuat " + host(url) + " …");
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (hidden) {
                    final Fetch f = fetch;
                    if (view == fetcher && f != null) pollFetch(f, 1200);
                    return;
                }
                final Session s = session;
                if (view == reader && s != null && !s.delivered) {
                    UI.postDelayed(new Runnable() {
                        public void run() {
                            if (session == s && !s.delivered) extract(s);
                        }
                    }, 1200);
                }
            }
        });
        if (!hidden) {
            w.setWebChromeClient(new WebChromeClient() {
                @Override
                public void onProgressChanged(WebView view, int p) {
                    if (progress == null || view != reader) return;
                    progress.setProgress(p);
                    progress.setVisibility(p < 100 ? View.VISIBLE : View.INVISIBLE);
                }

                @Override
                public void onReceivedTitle(WebView view, String title) {
                    if (view == reader && session != null && TextUtils.isEmpty(session.title) && titleView != null && title != null) {
                        titleView.setText(title);
                    }
                }
            });
        }
    }

    // ------------------------------------------------------------------ helpers

    private static String loadScript(Activity a) throws Exception {
        String readability = asset(a, "reader/Readability.js");
        String extract = asset(a, "reader/extract.js");
        return "(function(){if(!window.__sharxExtract){" + readability + "\n" + extract + "\n}return window.__sharxExtract();})()";
    }

    private static String asset(Activity a, String name) throws Exception {
        InputStream in = a.getAssets().open(name);
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            in.close();
        }
    }

    /** "FX:EURUSD" from a TradingView link (?tvwidgetsymbol=, ?symbol= or /symbols/FX-EURUSD/), else null. */
    static String tradingViewSymbol(Uri u) {
        String h = lower(u.getHost());
        if (!(h.equals("tradingview.com") || h.endsWith(".tradingview.com") || h.endsWith("tradingview-widget.com"))) return null;
        for (String key : new String[]{"tvwidgetsymbol", "symbol"}) {
            String v = u.getQueryParameter(key);
            if (v != null && v.trim().length() > 0) return v.trim().toUpperCase(Locale.US);
        }
        java.util.List<String> parts = u.getPathSegments();
        for (int i = 0; i + 1 < parts.size(); i++) {
            if ("symbols".equals(parts.get(i))) {
                String seg = parts.get(i + 1).toUpperCase(Locale.US);
                int dash = seg.indexOf('-');
                return dash > 0 ? seg.substring(0, dash) + ":" + seg.substring(dash + 1) : seg;
            }
        }
        return null;
    }

    private static String intentFallback(String s) {
        try {
            Intent i = Intent.parseUri(s, Intent.URI_INTENT_SCHEME);
            String f = i.getStringExtra("browser_fallback_url");
            return web(f) ? f : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** Pages that only redirect to the publisher (Google News) or ask for consent first: not the article yet. */
    private static boolean waiting(String url) {
        String h = host(url);
        return h.equals("news.google.com") || h.startsWith("consent.") || h.equals("accounts.google.com");
    }

    private static boolean web(String url) {
        return url != null && (url.startsWith("http://") || url.startsWith("https://"));
    }

    private static String host(String url) {
        try {
            String h = Uri.parse(url).getHost();
            return h == null ? "" : h.toLowerCase(Locale.US);
        } catch (Exception e) {
            return "";
        }
    }

    private static String lower(String s) {
        return s == null ? "" : s.toLowerCase(Locale.US);
    }

    private static String text(JSONObject o, String key) {
        if (!o.has(key) || o.isNull(key)) return null;
        String v = o.optString(key, "");
        return v.isEmpty() ? null : v;
    }

    private static int words(String text) {
        String t = text.trim();
        return t.isEmpty() ? 0 : t.split("\\s+").length;
    }
}
