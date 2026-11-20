package com.sharx.invest;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.media.AudioManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.provider.Settings;
import android.view.WindowManager;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Phone actions for JARVIS on the phone (app/jarvis_phone.py calls these through Chaquopy). Every method returns a
 * small JSON object for the assistant. Nothing here needs a permission the app does not already have: calls, messages
 * and e-mail open prefilled in their own apps and the user presses send; apps open through launcher intents.
 */
public final class JarvisHooks {
    private JarvisHooks() {
    }

    private static String result(Object... pairs) {
        JSONObject out = new JSONObject();
        try {
            for (int i = 0; i + 1 < pairs.length; i += 2) {
                out.put(String.valueOf(pairs[i]), pairs[i + 1]);
            }
        } catch (JSONException e) {
            return "{\"result\":\"failed\"}";
        }
        return out.toString();
    }

    private static Activity activity() {
        return ReaderHooks.currentActivity();
    }

    private static Context context() {
        Activity a = activity();
        return a != null ? a.getApplicationContext() : null;
    }

    /** Runs on the UI thread and waits briefly for it, so the answer says whether it worked. */
    private static String onUi(final Activity a, final Action action) {
        final String[] out = {result("result", "failed")};
        final CountDownLatch done = new CountDownLatch(1);
        a.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    out[0] = action.run();
                } catch (ActivityNotFoundException e) {
                    out[0] = result("result", "no app can do this on the phone");
                } catch (RuntimeException e) {
                    out[0] = result("result", "failed", "detail", e.getClass().getSimpleName());
                } finally {
                    done.countDown();
                }
            }
        });
        try {
            done.await(5, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return out[0];
    }

    private interface Action {
        String run();
    }

    private static String start(final Intent intent, final String done) {
        final Activity a = activity();
        if (a == null) {
            return result("result", "the app is not open");
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return onUi(a, new Action() {
            @Override
            public String run() {
                a.startActivity(intent);
                return result("result", done);
            }
        });
    }

    public static String volume(String action, int level) {
        Context c = context();
        if (c == null) {
            return result("result", "the app is not open");
        }
        AudioManager audio = (AudioManager) c.getSystemService(Context.AUDIO_SERVICE);
        int stream = AudioManager.STREAM_MUSIC;
        int max = audio.getStreamMaxVolume(stream);
        if ("volume_set".equals(action) && level >= 0) {
            audio.setStreamVolume(stream, Math.round(Math.min(100, level) / 100f * max), AudioManager.FLAG_SHOW_UI);
        } else if ("volume_up".equals(action)) {
            audio.adjustStreamVolume(stream, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI);
        } else if ("volume_down".equals(action)) {
            audio.adjustStreamVolume(stream, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI);
        } else if ("mute".equals(action)) {
            audio.adjustStreamVolume(stream, AudioManager.ADJUST_MUTE, AudioManager.FLAG_SHOW_UI);
        } else if ("unmute".equals(action)) {
            audio.adjustStreamVolume(stream, AudioManager.ADJUST_UNMUTE, AudioManager.FLAG_SHOW_UI);
        }
        int now = audio.getStreamVolume(stream);
        return result("result", "ok", "mediaVolumePct", Math.round(now * 100f / Math.max(1, max)));
    }

    public static String torch(boolean on) {
        Context c = context();
        if (c == null) {
            return result("result", "the app is not open");
        }
        try {
            CameraManager camera = (CameraManager) c.getSystemService(Context.CAMERA_SERVICE);
            for (String id : camera.getCameraIdList()) {
                Boolean flash = camera.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
                if (Boolean.TRUE.equals(flash)) {
                    camera.setTorchMode(id, on);
                    return result("result", on ? "flashlight on" : "flashlight off");
                }
            }
            return result("result", "this phone has no flashlight");
        } catch (Exception e) {
            return result("result", "failed", "detail", e.getClass().getSimpleName());
        }
    }

    public static String battery() {
        Context c = context();
        if (c == null) {
            return result("result", "the app is not open");
        }
        Intent status = c.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (status == null) {
            return result("result", "unknown");
        }
        int level = status.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int scale = status.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
        int plugged = status.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0);
        int temp = status.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1);
        return result("result", "ok", "batteryPct", Math.round(level * 100f / Math.max(1, scale)), "charging", plugged != 0,
                "temperatureC", temp < 0 ? null : temp / 10.0);
    }

    public static String device() {
        return result("result", "ok", "model", Build.MANUFACTURER + " " + Build.MODEL, "android", Build.VERSION.RELEASE,
                "sdk", Build.VERSION.SDK_INT);
    }

    public static String keepScreenOn(final boolean on) {
        final Activity a = activity();
        if (a == null) {
            return result("result", "the app is not open");
        }
        return onUi(a, new Action() {
            @Override
            public String run() {
                if (on) {
                    a.getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                } else {
                    a.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                }
                return result("result", on ? "screen stays on while SHARX is open" : "screen may turn off again");
            }
        });
    }

    public static String settings(String which) {
        String action = Settings.ACTION_SETTINGS;
        if ("wifi".equals(which)) {
            action = Settings.ACTION_WIFI_SETTINGS;
        } else if ("bluetooth".equals(which)) {
            action = Settings.ACTION_BLUETOOTH_SETTINGS;
        } else if ("display".equals(which)) {
            action = Settings.ACTION_DISPLAY_SETTINGS;
        } else if ("sound".equals(which)) {
            action = Settings.ACTION_SOUND_SETTINGS;
        }
        return start(new Intent(action), "settings opened");
    }

    /** Launches an installed app by package through its launcher activity. */
    public static String openApp(String pkg) {
        Intent intent = new Intent(Intent.ACTION_MAIN);
        intent.addCategory(Intent.CATEGORY_LAUNCHER);
        intent.setPackage(pkg);
        String out = start(intent, "app opened");
        if (out.contains("no app can do this")) {
            return result("result", "that app is not installed on this phone", "package", pkg);
        }
        return out;
    }

    /** http(s), geo: and google.navigation: links open in the app that handles them. */
    public static String openUri(String uri) {
        Uri u = Uri.parse(uri);
        String scheme = String.valueOf(u.getScheme()).toLowerCase();
        if (!("http".equals(scheme) || "https".equals(scheme) || "geo".equals(scheme) || "google.navigation".equals(scheme))) {
            return result("result", "link type not allowed");
        }
        return start(new Intent(Intent.ACTION_VIEW, u), "opened");
    }

    /** Opens the dialer, SMS, WhatsApp or e-mail prefilled; the user presses call or send. */
    public static String compose(String kind, String uri, String text) {
        Uri u = Uri.parse(uri);
        Intent intent;
        if ("call".equals(kind) && "tel".equals(u.getScheme())) {
            intent = new Intent(Intent.ACTION_DIAL, u);
        } else if ("sms".equals(kind) && "smsto".equals(u.getScheme())) {
            intent = new Intent(Intent.ACTION_SENDTO, u);
            intent.putExtra("sms_body", text);
        } else if ("email".equals(kind) && "mailto".equals(u.getScheme())) {
            intent = new Intent(Intent.ACTION_SENDTO, u);
        } else if ("whatsapp".equals(kind) && "https".equals(u.getScheme()) && "wa.me".equals(u.getHost())) {
            intent = new Intent(Intent.ACTION_VIEW, u);
        } else {
            return result("result", "not allowed");
        }
        return start(intent, "opened prefilled; waiting for the user to send");
    }
}
