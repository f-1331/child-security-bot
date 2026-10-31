package uz.childsecurity.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.media.AudioManager;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * 24/7 Background GPS Tracking Foreground Service
 * Keeps tracking active even when screen is locked or turned off.
 */
public class ChildTrackingService extends Service implements LocationListener {

    private static final String TAG = "ChildTrackingService";
    public static final String CHANNEL_ID = "child_security_tracking_channel";
    public static final int NOTIFICATION_ID = 1001;

    public static final String PREFS_NAME = "ChildSecurityPrefs";
    public static final String KEY_CHILD_ID = "child_id";
    public static final String KEY_SERVER_URL = "server_url";
    public static final String DEFAULT_SERVER_URL = "https://child-security-site.onrender.com";

    private LocationManager locationManager;
    private PowerManager.WakeLock wakeLock;
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();

    private long lastSentTime = 0;
    private double lastLat = 0.0;
    private double lastLon = 0.0;
    private float lastAccuracy = 15.0f;

    // Adaptive GPS Polling: Eco mode when stationary saves up to 80% battery
    private boolean isEcoMode = false;
    private int stationaryCount = 0;
    private boolean lastBreath5Sent = false;
    private boolean lastBreath2Sent = false;

    // Kalman Filter State for Anti-Drift and smooth trajectory
    private double kalmanLat = 0.0;
    private double kalmanLon = 0.0;
    private float kalmanVariance = -1f;

    private double[] filterKalman(double lat, double lon, float accuracy) {
        if (kalmanVariance < 0 || kalmanLat == 0.0 || kalmanLon == 0.0) {
            kalmanLat = lat;
            kalmanLon = lon;
            kalmanVariance = accuracy * accuracy;
            return new double[]{lat, lon};
        }
        // Jump reset if child moved > 50 meters
        double jumpDist = RustCoreBridge.haversineDistance(kalmanLat, kalmanLon, lat, lon);
        if (jumpDist > 50.0) {
            kalmanLat = lat;
            kalmanLon = lon;
            kalmanVariance = accuracy * accuracy;
            return new double[]{lat, lon};
        }
        float q = 4.0f; // Process noise
        kalmanVariance += q;
        float r = Math.max(1.0f, accuracy * accuracy);
        float k = Math.max(0.20f, Math.min(0.95f, kalmanVariance / (kalmanVariance + r)));
        kalmanLat += k * (lat - kalmanLat);
        kalmanLon += k * (lon - kalmanLon);
        kalmanVariance = (1 - k) * kalmanVariance;
        return new double[]{kalmanLat, kalmanLon};
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();

        // 1. Start Foreground Service (Android 14+ requires location type)
        try {
            Notification notification = buildForegroundNotification();
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
        } catch (Exception e) {
            Log.e(TAG, "Error starting foreground service: " + e.getMessage());
        }

        // 2. Acquire Partial WakeLock so CPU keeps running with screen off
        try {
            PowerManager powerManager = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (powerManager != null) {
                wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ChildSecurity:BackgroundGpsWakeLock");
                wakeLock.acquire(24 * 60 * 60 * 1000L); // 24 hours safe timeout
            }
        } catch (Exception e) {
            Log.w(TAG, "WakeLock acquire error: " + e.getMessage());
        }

        // 3. Register GPS, Network, and Passive Location Providers
        locationManager = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        requestLocationUpdates();

        // 4. Heartbeat watchdog: adaptive telemetry pulse
        scheduler.scheduleWithFixedDelay(() -> {
            try {
                long now = System.currentTimeMillis();
                long minInterval = isEcoMode ? 45000L : 15000L;
                if (now - lastSentTime >= minInterval) {
                    Location fresh = getBestLastLocation();
                    if (fresh != null) {
                        lastLat = fresh.getLatitude();
                        lastLon = fresh.getLongitude();
                        lastAccuracy = fresh.getAccuracy();
                    }
                    sendLocationTelemetry(lastLat, lastLon, lastAccuracy, 0, false);
                }
            } catch (Exception ignored) {}
        }, 10, 15, TimeUnit.SECONDS);

        // 5. Periodic App Usage & Screen Time Reporter (every 120 seconds)
        scheduler.scheduleWithFixedDelay(new Runnable() {
            @Override
            public void run() {
                try {
                    sendAppUsageTelemetry();
                } catch (Exception ignored) {}
            }
        }, 30, 120, TimeUnit.SECONDS);

        // 6. Ensure Watchdog heartbeat alarm is armed
        WatchdogReceiver.scheduleNextWatchdogPulse(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null) {
            String childId = intent.getStringExtra(KEY_CHILD_ID);
            String serverUrl = intent.getStringExtra(KEY_SERVER_URL);

            if (childId != null && !childId.trim().isEmpty()) {
                SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
                SharedPreferences.Editor editor = prefs.edit();
                editor.putString(KEY_CHILD_ID, childId.trim());
                if (serverUrl != null && !serverUrl.trim().isEmpty()) {
                    editor.putString(KEY_SERVER_URL, serverUrl.trim());
                }
                editor.apply();
            }

            // Check for emergency Last Breath pulse from WatchdogReceiver
            if (intent.getBooleanExtra("ACTION_LAST_BREATH", false)) {
                triggerLastBreathSnapshot();
            }
        }

        // Trigger immediate check
        Location last = getBestLastLocation();
        if (last != null) {
            onLocationChanged(last);
        }

        // START_STICKY guarantees service will be revived by Android OS if memory pressure occurs
        return START_STICKY;
    }

    private void requestLocationUpdates() {
        if (locationManager == null) return;
        try {
            long minTime = isEcoMode ? 45000L : 5000L;
            float minDistance = isEcoMode ? 10f : 2f;
            locationManager.removeUpdates(this);

            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.GPS_PROVIDER, minTime, minDistance, this);
            }
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, minTime, minDistance, this);
            }
            if (locationManager.isProviderEnabled(LocationManager.PASSIVE_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.PASSIVE_PROVIDER, minTime, minDistance, this);
            }
            Log.d(TAG, "Location updates requested (EcoMode=" + isEcoMode + ", minTime=" + minTime + "ms)");
        } catch (SecurityException e) {
            Log.e(TAG, "Location permission not granted: " + e.getMessage());
        } catch (Exception e) {
            Log.e(TAG, "Request location updates error: " + e.getMessage());
        }
    }

    private Location getBestLastLocation() {
        if (locationManager == null) return null;
        Location best = null;
        try {
            Location gps = locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER);
            Location net = locationManager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER);
            Location pas = locationManager.getLastKnownLocation(LocationManager.PASSIVE_PROVIDER);

            if (gps != null) best = gps;
            if (net != null && (best == null || net.getTime() > best.getTime())) best = net;
            if (pas != null && (best == null || pas.getTime() > best.getTime())) best = pas;
        } catch (SecurityException ignored) {}
        return best;
    }

    @Override
    public void onLocationChanged(Location location) {
        if (location == null) return;

        // Anti-Spoofing: Detect Mock Locations
        boolean isMock = false;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                isMock = location.isMock();
            } else {
                isMock = location.isFromMockProvider();
            }
        } catch (Exception ignored) {}

        if (isMock) {
            Log.w(TAG, "⚠️ ALERT: Mock (fake) location detected on child device!");
        }

        double rawLat = location.getLatitude();
        double rawLon = location.getLongitude();
        float accuracy = location.getAccuracy();
        int speedKmh = Math.round(location.getSpeed() * 3.6f);

        // Apply Kalman filter for centimeter-smooth trajectory without stationary drift
        double[] filtered = filterKalman(rawLat, rawLon, accuracy);
        double lat = filtered[0];
        double lon = filtered[1];

        // Stationary Adaptive Polling check
        if (lastLat != 0.0 && lastLon != 0.0) {
            double distFromLast = RustCoreBridge.haversineDistance(lastLat, lastLon, lat, lon);
            if (distFromLast < 5.0 && speedKmh <= 1) {
                stationaryCount++;
                if (stationaryCount >= 3 && !isEcoMode) {
                    isEcoMode = true;
                    Log.i(TAG, "Device stationary (<5m) for 3 pings. Switching to Eco GPS mode (45s). Saves 80% battery.");
                    requestLocationUpdates();
                }
            } else if (distFromLast >= 8.0 || speedKmh >= 2) {
                stationaryCount = 0;
                if (isEcoMode) {
                    isEcoMode = false;
                    Log.i(TAG, "Movement detected (" + Math.round(distFromLast) + "m, " + speedKmh + "km/h). Switching to Real-Time GPS mode (5s).");
                    requestLocationUpdates();
                }
            }
        }

        lastLat = lat;
        lastLon = lon;
        lastAccuracy = accuracy;

        sendLocationTelemetry(lat, lon, accuracy, speedKmh, false);
    }

    private void sendLocationTelemetry(double lat, double lon, float accuracy, int speedKmh) {
        sendLocationTelemetry(lat, lon, accuracy, speedKmh, false);
    }

    private void sendLocationTelemetry(double lat, double lon, float accuracy, int speedKmh, boolean isLastBreath) {
        try {
            SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
            String childId = prefs.getString(KEY_CHILD_ID, null);
            String serverUrl = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);

            if (childId == null || childId.trim().isEmpty()) {
                return;
            }

            // Cache non-zero location coordinates so stationary indoor updates retain last known spot
            if (lat != 0.0 && lon != 0.0) {
                prefs.edit().putFloat("cached_lat", (float) lat).putFloat("cached_lon", (float) lon).apply();
            } else {
                lat = prefs.getFloat("cached_lat", 0.0f);
                lon = prefs.getFloat("cached_lon", 0.0f);
            }

            int batteryLevel = getDeviceBatteryLevel();
            boolean isCharging = isDeviceCharging();
            String soundMode = getDeviceSoundMode();

            // Emergency Last Breath battery check (5% and 2%)
            if (!isCharging) {
                if (batteryLevel <= 2 && !lastBreath2Sent) {
                    lastBreath2Sent = true;
                    isLastBreath = true;
                } else if (batteryLevel <= 5 && !lastBreath5Sent) {
                    lastBreath5Sent = true;
                    isLastBreath = true;
                }
            } else {
                lastBreath5Sent = false;
                lastBreath2Sent = false;
            }

            JSONObject payload = new JSONObject();
            payload.put("latitude", lat);
            payload.put("longitude", lon);
            payload.put("accuracy", accuracy);
            payload.put("speed", speedKmh);
            payload.put("speed_kmh", speedKmh);
            payload.put("battery_level", batteryLevel);
            payload.put("is_charging", isCharging);
            payload.put("sound_mode", soundMode);
            payload.put("is_sos_active", false);

            if (isLastBreath) {
                payload.put("is_last_breath", true);
                String fgApp = getForegroundAppLabel();
                if (fgApp != null) payload.put("last_active_app", fgApp);
                String wifiSsid = getConnectedWifiSsid();
                if (wifiSsid != null) payload.put("wifi_ssid", wifiSsid);
            }

            byte[] postData = payload.toString().getBytes(StandardCharsets.UTF_8);
            String primaryBase = (serverUrl != null && !serverUrl.trim().isEmpty()) ? serverUrl.trim() : DEFAULT_SERVER_URL;
            String fallbackBase = primaryBase.contains("onrender.com")
                    ? "https://child-security-bot.vercel.app"
                    : "https://child-security-site.onrender.com";

            boolean success = postTelemetry(primaryBase, childId, postData);
            if (!success && !primaryBase.equals(fallbackBase)) {
                success = postTelemetry(fallbackBase, childId, postData);
            }

            if (success) {
                lastSentTime = System.currentTimeMillis();
                Log.d(TAG, "Sent live GPS telemetry (" + lat + ", " + lon + ") SUCCESS");
                // Flush any previously stored offline locations
                flushOfflineBatch(primaryBase, childId);
            } else {
                // OFFLINE-FIRST: Log GPS to local SQLite cache
                if (lat != 0.0 && lon != 0.0) {
                    OfflineDbHelper.getInstance(this).insertLocation(
                            childId, lat, lon, accuracy, speedKmh, batteryLevel, isCharging, soundMode, false, System.currentTimeMillis()
                    );
                    Log.d(TAG, "Offline GPS logged to SQLite cache. Total unsynced: " + OfflineDbHelper.getInstance(this).getUnsyncedCount());
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "Failed to send background GPS telemetry: " + e.getMessage());
        }
    }

    private void flushOfflineBatch(String baseUrl, String childId) {
        final OfflineDbHelper db = OfflineDbHelper.getInstance(this);
        int unsynced = db.getUnsyncedCount();
        if (unsynced == 0) return;

        scheduler.execute(new Runnable() {
            @Override
            public void run() {
                try {
                    org.json.JSONArray batch = db.getUnsyncedBatch(100);
                    if (batch.length() == 0) return;

                    long maxId = -1;
                    for (int i = 0; i < batch.length(); i++) {
                        long dbId = batch.getJSONObject(i).optLong("db_id", -1);
                        if (dbId > maxId) maxId = dbId;
                    }

                    JSONObject req = new JSONObject();
                    req.put("child_id", childId);
                    req.put("points", batch);

                    byte[] batchData = req.toString().getBytes(StandardCharsets.UTF_8);
                    String cleanBase = (baseUrl != null && !baseUrl.trim().isEmpty()) ? baseUrl.trim() : DEFAULT_SERVER_URL;
                    cleanBase = cleanBase.replaceAll("/+$", "");
                    String endpoint = cleanBase + "/api/v1/webapp/child/" + childId.trim() + "/location/batch";

                    URL url = new URL(endpoint);
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
                    conn.setRequestProperty("Accept", "application/json");
                    conn.setConnectTimeout(15000);
                    conn.setReadTimeout(15000);
                    conn.setDoOutput(true);

                    try (OutputStream os = conn.getOutputStream()) {
                        os.write(batchData);
                        os.flush();
                    }

                    int code = conn.getResponseCode();
                    if (code >= 200 && code < 300) {
                        if (maxId > 0) {
                            db.deleteSyncedRecords(maxId);
                            Log.i(TAG, "Successfully synced " + batch.length() + " offline GPS points to server!");
                        }
                    }
                    conn.disconnect();
                } catch (Exception e) {
                    Log.w(TAG, "Batch flush note: " + e.getMessage());
                }
            }
        });
    }

    private boolean postTelemetry(String baseUrl, String childId, byte[] postData) {
        HttpURLConnection conn = null;
        try {
            String cleanBase = (baseUrl != null && !baseUrl.trim().isEmpty()) ? baseUrl.trim() : DEFAULT_SERVER_URL;
            if (cleanBase.contains("?")) {
                cleanBase = cleanBase.substring(0, cleanBase.indexOf("?"));
            }
            cleanBase = cleanBase.replaceAll("/+$", "");
            String endpoint = cleanBase + "/api/v1/webapp/child/" + childId.trim() + "/location";
            URL url = new URL(endpoint);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            conn.setRequestProperty("Accept", "application/json");
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(15000);
            conn.setDoOutput(true);

            try (OutputStream os = conn.getOutputStream()) {
                os.write(postData);
                os.flush();
            }

            int code = conn.getResponseCode();
            if (code >= 200 && code < 300) {
                try (java.io.InputStream is = conn.getInputStream();
                     java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(is, StandardCharsets.UTF_8))) {
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        sb.append(line);
                    }
                    JSONObject resObj = new JSONObject(sb.toString());
                    if (resObj.has("policy")) {
                        applyPolicy(resObj.getJSONObject("policy"));
                    }
                } catch (Exception ignored) {}
                return true;
            }
            return false;
        } catch (Exception e) {
            Log.w(TAG, "Telemetry post error to " + baseUrl + ": " + e.getMessage());
            return false;
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private int getDeviceBatteryLevel() {
        try {
            IntentFilter ifilter = new IntentFilter(Intent.ACTION_BATTERY_CHANGED);
            Intent batteryStatus = registerReceiver(null, ifilter);
            if (batteryStatus != null) {
                int level = batteryStatus.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
                int scale = batteryStatus.getIntExtra(BatteryManager.EXTRA_SCALE, -1);
                if (level >= 0 && scale > 0) {
                    return Math.round((level / (float) scale) * 100);
                }
            }
        } catch (Exception ignored) {}
        return 100;
    }

    private boolean isDeviceCharging() {
        try {
            IntentFilter ifilter = new IntentFilter(Intent.ACTION_BATTERY_CHANGED);
            Intent batteryStatus = registerReceiver(null, ifilter);
            if (batteryStatus != null) {
                int status = batteryStatus.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
                return status == BatteryManager.BATTERY_STATUS_CHARGING ||
                       status == BatteryManager.BATTERY_STATUS_FULL;
            }
        } catch (Exception ignored) {}
        return false;
    }

    private String getDeviceSoundMode() {
        try {
            AudioManager am = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
            if (am != null) {
                switch (am.getRingerMode()) {
                    case AudioManager.RINGER_MODE_SILENT:
                        return "silent";
                    case AudioManager.RINGER_MODE_VIBRATE:
                        return "vibrate";
                    case AudioManager.RINGER_MODE_NORMAL:
                    default:
                        return "normal";
                }
            }
        } catch (Exception ignored) {}
        return "normal";
    }

    private Notification buildForegroundNotification() {
        Intent notificationIntent = new Intent(this, MainActivity.class);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this, 0, notificationIntent,
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0
        );

        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle(getString(R.string.notification_title))
                .setContentText(getString(R.string.notification_body))
                .setSmallIcon(R.drawable.ic_shield_notification)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .build();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel serviceChannel = new NotificationChannel(
                    CHANNEL_ID,
                    getString(R.string.channel_name),
                    NotificationManager.IMPORTANCE_LOW
            );
            serviceChannel.setDescription(getString(R.string.channel_desc));
            serviceChannel.setShowBadge(false);
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(serviceChannel);
            }
        }
    }

    public void applyPolicy(JSONObject pol) {
        if (pol == null) return;
        try {
            SharedPreferences.Editor ed = getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit();
            if (pol.has("is_all_apps_blocked")) {
                ed.putBoolean("is_all_apps_blocked", pol.getBoolean("is_all_apps_blocked"));
            }
            if (pol.has("lockdown_reason")) {
                ed.putString("lockdown_reason", pol.getString("lockdown_reason"));
            }
            if (pol.has("blocked_packages")) {
                ed.putString("blocked_packages", pol.getJSONArray("blocked_packages").toString());
            }
            if (pol.has("anti_uninstall_enabled")) {
                ed.putBoolean("anti_uninstall_enabled", pol.getBoolean("anti_uninstall_enabled"));
            }
            if (pol.has("bedtime_start")) {
                ed.putString("bedtime_start", pol.optString("bedtime_start", ""));
            }
            if (pol.has("bedtime_end")) {
                ed.putString("bedtime_end", pol.optString("bedtime_end", ""));
            }
            if (pol.has("is_network_restricted")) {
                boolean netRestricted = pol.getBoolean("is_network_restricted");
                ed.putBoolean("is_network_restricted", netRestricted);
                if (netRestricted) {
                    LocalVpnService.startLockdown(this);
                } else {
                    LocalVpnService.stopLockdown(this);
                }
            }
            ed.apply();
            Log.d(TAG, "Applied remote device policy: all_blocked=" + pol.optBoolean("is_all_apps_blocked") +
                    ", blocked_pkgs=" + pol.opt("blocked_packages"));
        } catch (Exception e) {
            Log.w(TAG, "Failed to apply device policy: " + e.getMessage());
        }
    }

    private void fetchDevicePolicy(String cleanBase, String childId) {
        try {
            String endpoint = cleanBase + "/api/v1/webapp/child/" + childId + "/device-policy";
            HttpURLConnection conn = (HttpURLConnection) new URL(endpoint).openConnection();
            conn.setRequestMethod("GET");
            conn.setRequestProperty("Accept", "application/json");
            conn.setConnectTimeout(6000);
            conn.setReadTimeout(6000);
            int code = conn.getResponseCode();
            if (code >= 200 && code < 300) {
                try (java.io.InputStream is = conn.getInputStream();
                     java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(is, StandardCharsets.UTF_8))) {
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        sb.append(line);
                    }
                    JSONObject resObj = new JSONObject(sb.toString());
                    if (resObj.has("policy")) {
                        applyPolicy(resObj.getJSONObject("policy"));
                    }
                }
            }
            conn.disconnect();
        } catch (Exception ignored) {}
    }

    private void sendAppUsageTelemetry() {
        try {
            SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
            String childId = prefs.getString(KEY_CHILD_ID, null);
            String serverUrl = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);
            if (childId == null || childId.trim().isEmpty()) return;

            String cleanBase = (serverUrl != null && !serverUrl.trim().isEmpty()) ? serverUrl.trim() : DEFAULT_SERVER_URL;
            if (cleanBase.contains("?")) cleanBase = cleanBase.substring(0, cleanBase.indexOf("?"));
            cleanBase = cleanBase.replaceAll("/+$", "");

            JSONObject usageObj = UsageStatsHelper.getDailyAppUsageStats(this);
            if (!usageObj.optBoolean("has_permission", false)) {
                // If usage stats permission not yet granted, keep device policy synced anyway
                fetchDevicePolicy(cleanBase, childId.trim());
                return;
            }

            JSONObject payload = new JSONObject();
            payload.put("total_screen_time_seconds", usageObj.optLong("total_seconds", 0));
            payload.put("unlock_count", usageObj.optInt("unlock_count", 0));
            payload.put("app_stats", usageObj.optJSONArray("apps"));

            byte[] postData = payload.toString().getBytes(StandardCharsets.UTF_8);
            String endpoint = cleanBase + "/api/v1/webapp/child/" + childId.trim() + "/app-usage";

            HttpURLConnection conn = (HttpURLConnection) new URL(endpoint).openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            conn.setRequestProperty("Accept", "application/json");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(8000);
            conn.setDoOutput(true);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(postData);
                os.flush();
            }
            int code = conn.getResponseCode();
            if (code >= 200 && code < 300) {
                try (java.io.InputStream is = conn.getInputStream();
                     java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(is, StandardCharsets.UTF_8))) {
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        sb.append(line);
                    }
                    JSONObject resObj = new JSONObject(sb.toString());
                    if (resObj.has("policy")) {
                        applyPolicy(resObj.getJSONObject("policy"));
                    }
                }
            }
            conn.disconnect();
        } catch (Exception ignored) {}
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        super.onTaskRemoved(rootIntent);
        Log.w(TAG, "ChildTrackingService onTaskRemoved: immediately reviving tracking service!");
        WatchdogReceiver.ensureServiceActive(this);
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        // Self-Healing: arm Watchdog for instant 3-second resurrection
        try {
            Intent restartIntent = new Intent(this, WatchdogReceiver.class);
            restartIntent.setAction(WatchdogReceiver.ACTION_RESTART_TRACKING);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags |= PendingIntent.FLAG_IMMUTABLE;
            PendingIntent pi = PendingIntent.getBroadcast(this, 1009, restartIntent, flags);
            android.app.AlarmManager am = (android.app.AlarmManager) getSystemService(Context.ALARM_SERVICE);
            if (am != null) {
                long triggerAt = System.currentTimeMillis() + 3000;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    am.setExactAndAllowWhileIdle(android.app.AlarmManager.RTC_WAKEUP, triggerAt, pi);
                } else {
                    am.set(android.app.AlarmManager.RTC_WAKEUP, triggerAt, pi);
                }
            }
            sendBroadcast(restartIntent);
        } catch (Exception ignored) {}

        if (locationManager != null) {
            try {
                locationManager.removeUpdates(this);
            } catch (Exception ignored) {}
        }
        if (wakeLock != null && wakeLock.isHeld()) {
            try {
                wakeLock.release();
            } catch (Exception ignored) {}
        }
        scheduler.shutdownNow();
        Log.d(TAG, "ChildTrackingService stopped - Watchdog 3s self-healing armed");
    }

    public void triggerLastBreathSnapshot() {
        try {
            Location best = getBestLastLocation();
            double lat = best != null ? best.getLatitude() : lastLat;
            double lon = best != null ? best.getLongitude() : lastLon;
            float acc = best != null ? best.getAccuracy() : lastAccuracy;

            sendLocationTelemetry(lat, lon, acc, 0, true);

            // If emergency parent phone number is saved, send emergency Last Breath SMS
            SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
            String parentPhone = prefs.getString("parent_phone", null);
            if (parentPhone != null && !parentPhone.trim().isEmpty() && lat != 0.0 && lon != 0.0) {
                int batt = getDeviceBatteryLevel();
                String smsText = "⚠️ DIQQAT: Farzandingiz telefonida quvvat tugadi (" + batt + "%). Oxirgi joyi: https://maps.google.com/?q=" + lat + "," + lon;
                android.telephony.SmsManager sms = android.telephony.SmsManager.getDefault();
                sms.sendTextMessage(parentPhone.trim(), null, smsText, null, null);
                Log.i(TAG, "Last Breath SMS emergency snapshot sent to parent: " + parentPhone);
            }
        } catch (Exception e) {
            Log.w(TAG, "triggerLastBreathSnapshot note: " + e.getMessage());
        }
    }

    private String getConnectedWifiSsid() {
        try {
            android.net.wifi.WifiManager wm = (android.net.wifi.WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null && wm.getConnectionInfo() != null) {
                String ssid = wm.getConnectionInfo().getSSID();
                if (ssid != null && !ssid.isEmpty() && !ssid.equals("<unknown ssid>")) {
                    return ssid.replace("\"", "");
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private String getForegroundAppLabel() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP_MR1 && UsageStatsHelper.hasUsageAccessPermission(this)) {
                android.app.usage.UsageStatsManager usm = (android.app.usage.UsageStatsManager) getSystemService(Context.USAGE_STATS_SERVICE);
                if (usm != null) {
                    long end = System.currentTimeMillis();
                    long start = end - 1000 * 60 * 5;
                    java.util.List<android.app.usage.UsageStats> list = usm.queryUsageStats(android.app.usage.UsageStatsManager.INTERVAL_BEST, start, end);
                    if (list != null && !list.isEmpty()) {
                        android.app.usage.UsageStats recent = null;
                        for (android.app.usage.UsageStats us : list) {
                            if (recent == null || us.getLastTimeUsed() > recent.getLastTimeUsed()) {
                                recent = us;
                            }
                        }
                        if (recent != null) {
                            android.content.pm.PackageManager pm = getPackageManager();
                            android.content.pm.ApplicationInfo ai = pm.getApplicationInfo(recent.getPackageName(), 0);
                            CharSequence label = pm.getApplicationLabel(ai);
                            return label != null ? label.toString() : recent.getPackageName();
                        }
                    }
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onStatusChanged(String provider, int status, Bundle extras) {}

    @Override
    public void onProviderEnabled(String provider) {}

    @Override
    public void onProviderDisabled(String provider) {}
}
