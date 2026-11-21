package com.sharx.invest;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.NoiseSuppressor;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.provider.Settings;
import android.view.WindowManager;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Phone side of JARVIS on the phone (app/jarvis_phone.py calls these through Chaquopy).
 * <ul>
 *   <li>Microphone: recorded here with AudioRecord (16 kHz mono 16-bit PCM, 100 ms chunks) and pulled by the engine,
 *       because the WebView's getUserMedia fails on some phones (NotReadableError).</li>
 *   <li>Phone actions: every method returns a small JSON object for the assistant. Calls, messages and e-mail open
 *       prefilled in their own apps and the user presses send; apps open through launcher intents.</li>
 * </ul>
 * Nothing here needs a permission the app does not already declare (RECORD_AUDIO is requested at run time).
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
    // ------------------------------------------------------------------ microphone

    private static final int RATE = 16000, CHUNK = 3200;  // 100 ms of 16-bit mono PCM
    private static final LinkedBlockingQueue<byte[]> MIC = new LinkedBlockingQueue<>(50);
    private static AudioRecord recorder;
    private static Thread micThread;
    private static volatile boolean micRunning;
    private static volatile float micLevel;
    private static volatile String micError;

    /** Starts recording; echo cancelling source when JARVIS may be interrupted by voice on the speaker. */
    public static synchronized String micStart(boolean echoCancel) {
        Context c = context();
        final Activity a = activity();
        if (c == null || a == null) {
            return result("result", "failed", "detail", "Aplikasi tidak terbuka.");
        }
        if (c.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            a.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    a.requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 19);
                }
            });
            return result("result", "permission", "detail", "Izinkan mikrofon untuk SHARX, lalu tekan MIC lagi.");
        }
        if (micRunning) {
            return result("result", "ok");
        }
        int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        int size = Math.max(min, CHUNK * 4);
        int[] sources = echoCancel
                ? new int[]{MediaRecorder.AudioSource.VOICE_COMMUNICATION, MediaRecorder.AudioSource.MIC}
                : new int[]{MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.MIC};
        AudioRecord r = null;
        String used = null;
        for (int source : sources) {
            try {
                AudioRecord candidate = new AudioRecord(source, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size);
                if (candidate.getState() == AudioRecord.STATE_INITIALIZED) {
                    r = candidate;
                    used = source == MediaRecorder.AudioSource.MIC ? "mic"
                            : source == MediaRecorder.AudioSource.VOICE_COMMUNICATION ? "voice_communication" : "voice_recognition";
                    break;
                }
                candidate.release();
            } catch (RuntimeException e) {
                // try the next source
            }
        }
        if (r == null) {
            return result("result", "failed", "detail", "Mikrofon tidak bisa dibuka di HP ini.");
        }
        if (echoCancel && AcousticEchoCanceler.isAvailable()) {
            AcousticEchoCanceler aec = AcousticEchoCanceler.create(r.getAudioSessionId());
            if (aec != null) {
                aec.setEnabled(true);
            }
        }
        if (NoiseSuppressor.isAvailable()) {
            NoiseSuppressor ns = NoiseSuppressor.create(r.getAudioSessionId());
            if (ns != null) {
                ns.setEnabled(true);
            }
        }
        try {
            r.startRecording();
        } catch (IllegalStateException e) {
            r.release();
            return result("result", "failed", "detail", "Mikrofon tidak bisa mulai merekam.");
        }
        if (r.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
            r.release();
            return result("result", "failed", "detail", "Mikrofon sedang dipakai aplikasi lain.");
        }
        recorder = r;
        micRunning = true;
        micError = null;
        MIC.clear();
        final AudioRecord rec = r;
        micThread = new Thread(new Runnable() {
            @Override
            public void run() {
                byte[] buffer = new byte[CHUNK];
                while (micRunning) {
                    int n = rec.read(buffer, 0, buffer.length);
                    if (n < 0) {
                        micError = "Rekaman mikrofon berhenti (kode " + n + ").";
                        micRunning = false;
                        break;
                    }
                    if (n == 0) {
                        continue;
                    }
                    double sum = 0;
                    for (int i = 0; i + 1 < n; i += 2) {
                        short v = (short) ((buffer[i] & 0xff) | (buffer[i + 1] << 8));
                        sum += (double) v * v;
                    }
                    micLevel = (float) Math.sqrt(sum / Math.max(1, n / 2)) / 32768f;
                    byte[] chunk = Arrays.copyOf(buffer, n);
                    if (!MIC.offer(chunk)) {
                        MIC.poll();  // the engine fell behind: drop the oldest chunk, keep the latest audio
                        MIC.offer(chunk);
                    }
                }
            }
        }, "jarvis-mic");
        micThread.start();
        return result("result", "ok", "source", used);
    }

    /** The next chunk of 16 kHz PCM, or null when none came within timeoutMs. */
    public static byte[] micRead(int timeoutMs) {
        try {
            return MIC.poll(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        }
    }

    public static float micLevel() {
        return micRunning ? micLevel : 0f;
    }

    /** Why recording stopped by itself, or null. */
    public static String micError() {
        return micError;
    }

    public static synchronized String micStop() {
        micRunning = false;
        Thread t = micThread;
        micThread = null;
        if (t != null) {
            try {
                t.join(500);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        AudioRecord r = recorder;
        recorder = null;
        if (r != null) {
            try {
                r.stop();
            } catch (IllegalStateException e) {
                // was not recording
            }
            r.release();
        }
        MIC.clear();
        micLevel = 0f;
        return result("result", "ok");
    }
}
