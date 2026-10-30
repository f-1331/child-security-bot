package uz.childsecurity.app;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.SystemClock;
import android.util.Log;

import androidx.core.content.ContextCompat;

/**
 * OEM Killer Bypass & Self-Healing Watchdog:
 * Resurrects ChildTrackingService if terminated by aggressive OEM battery killers (MIUI, OneUI, EMUI)
 * and captures "Last Breath" emergency snapshot when battery drops to critical levels (<= 5%).
 */
public class WatchdogReceiver extends BroadcastReceiver {

    private static final String TAG = "WatchdogReceiver";
    public static final String ACTION_WATCHDOG_PULSE = "uz.childsecurity.app.ACTION_WATCHDOG_PULSE";
    public static final String ACTION_RESTART_TRACKING = "uz.childsecurity.app.RESTART_TRACKING";
    public static final long WATCHDOG_INTERVAL_MS = 15 * 60 * 1000; // 15 minutes

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent != null ? intent.getAction() : null;
        Log.d(TAG, "Watchdog received action: " + action);

        // 1. Check for "Last Breath" battery emergency
        if (Intent.ACTION_BATTERY_LOW.equals(action)) {
            triggerLastBreathEmergency(context);
        }

        // 2. Self-Healing check: verify ChildTrackingService is running
        ensureServiceActive(context);

        // 3. Schedule next pulse with AlarmManager setExactAndAllowWhileIdle
        scheduleNextWatchdogPulse(context);
    }

    public static void ensureServiceActive(Context context) {
        try {
            SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, Context.MODE_PRIVATE);
            String childId = prefs.getString(ChildTrackingService.KEY_CHILD_ID, null);

            if (childId != null && !childId.isEmpty()) {
                Intent serviceIntent = new Intent(context, ChildTrackingService.class);
                serviceIntent.putExtra(ChildTrackingService.KEY_CHILD_ID, childId);

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    ContextCompat.startForegroundService(context, serviceIntent);
                } else {
                    context.startService(serviceIntent);
                }
                Log.d(TAG, "Watchdog ensured ChildTrackingService is active for child: " + childId);
            }
        } catch (Exception e) {
            Log.e(TAG, "Failed to ensure tracking service: " + e.getMessage());
        }
    }

    public static void scheduleNextWatchdogPulse(Context context) {
        try {
            AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
            if (am == null) return;

            Intent intent = new Intent(context, WatchdogReceiver.class);
            intent.setAction(ACTION_WATCHDOG_PULSE);

            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                flags |= PendingIntent.FLAG_IMMUTABLE;
            }

            PendingIntent pi = PendingIntent.getBroadcast(context, 1001, intent, flags);
            long triggerAt = SystemClock.elapsedRealtime() + WATCHDOG_INTERVAL_MS;

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pi);
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT) {
                am.setExact(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pi);
            } else {
                am.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pi);
            }
        } catch (Exception e) {
            Log.w(TAG, "Failed to schedule watchdog pulse: " + e.getMessage());
        }
    }

    private void triggerLastBreathEmergency(Context context) {
        try {
            Log.w(TAG, "BATTERY CRITICAL (Last Breath): Triggering emergency location snapshot!");
            SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, Context.MODE_PRIVATE);
            String childId = prefs.getString(ChildTrackingService.KEY_CHILD_ID, null);

            if (childId != null && !childId.isEmpty()) {
                Intent intent = new Intent(context, ChildTrackingService.class);
                intent.putExtra(ChildTrackingService.KEY_CHILD_ID, childId);
                intent.putExtra("ACTION_LAST_BREATH", true);

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    ContextCompat.startForegroundService(context, intent);
                } else {
                    context.startService(intent);
                }
            }
        } catch (Exception e) {
            Log.e(TAG, "Error triggering last breath: " + e.getMessage());
        }
    }
}
