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
    public static final String DEFAULT_SERVER_URL = "https://child-security-bot.vercel.app";

    private LocationManager locationManager;
    private PowerManager.WakeLock wakeLock;
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();

    private long lastSentTime = 0;
    private double lastLat = 0.0;
    private double lastLon = 0.0;
    private float lastAccuracy = 15.0f;

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

        // 4. Heartbeat watchdog: every 25 seconds, send fresh telemetry even if phone is stationary
        scheduler.scheduleWithFixedDelay(() -> {
            try {
                long now = System.currentTimeMillis();
                if (now - lastSentTime >= 25000) {
                    Location fresh = getBestLastLocation();
                    if (fresh != null) {
                        lastLat = fresh.getLatitude();
                        lastLon = fresh.getLongitude();
                        lastAccuracy = fresh.getAccuracy();
                    }
                    if (lastLat != 0.0 && lastLon != 0.0) {
                        sendLocationTelemetry(lastLat, lastLon, lastAccuracy, 0);
                    }
                }
            } catch (Exception ignored) {}
        }, 15, 25, TimeUnit.SECONDS);
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
            // Request GPS provider (every 15 seconds or 5 meters)
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.GPS_PROVIDER, 15000L, 5f, this);
            }
            // Fallback network provider
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 15000L, 5f, this);
            }
            // Passive provider
            if (locationManager.isProviderEnabled(LocationManager.PASSIVE_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.PASSIVE_PROVIDER, 15000L, 5f, this);
            }
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

        double lat = location.getLatitude();
        double lon = location.getLongitude();
        float accuracy = location.getAccuracy();
        int speedKmh = Math.round(location.getSpeed() * 3.6f);

        lastLat = lat;
        lastLon = lon;
        lastAccuracy = accuracy;

        sendLocationTelemetry(lat, lon, accuracy, speedKmh);
    }

    private void sendLocationTelemetry(double lat, double lon, float accuracy, int speedKmh) {
        try {
            SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
            String childId = prefs.getString(KEY_CHILD_ID, null);
            String serverUrl = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);

            if (childId == null || childId.trim().isEmpty()) {
                return;
            }

            int batteryLevel = getDeviceBatteryLevel();
            boolean isCharging = isDeviceCharging();
            String soundMode = getDeviceSoundMode();

            JSONObject payload = new JSONObject();
            payload.put("latitude", lat);
            payload.put("longitude", lon);
            payload.put("accuracy", accuracy);
            payload.put("speed_kmh", speedKmh);
            payload.put("battery_level", batteryLevel);
            payload.put("is_charging", isCharging);
            payload.put("sound_mode", soundMode);
            payload.put("is_sos_active", false);

            String endpoint = serverUrl.replaceAll("/+$", "") + "/api/v1/webapp/child/" + childId.trim() + "/location";
            URL url = new URL(endpoint);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            conn.setRequestProperty("Accept", "application/json");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(8000);
            conn.setDoOutput(true);

            byte[] postData = payload.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(postData);
                os.flush();
            }

            int code = conn.getResponseCode();
            conn.disconnect();
            lastSentTime = System.currentTimeMillis();
            Log.d(TAG, "Sent background GPS telemetry (" + lat + ", " + lon + ") Code: " + code);
        } catch (Exception e) {
            Log.w(TAG, "Failed to send background GPS telemetry: " + e.getMessage());
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

    @Override
    public void onDestroy() {
        super.onDestroy();
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
        Log.d(TAG, "ChildTrackingService stopped");
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
