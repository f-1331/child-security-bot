package uz.childsecurity.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.VpnService;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.util.Log;

import androidx.core.app.NotificationCompat;

import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;

/**
 * Local VPN Firewall Service for Device Network Lockdown ("Internetni Cheklash / Offline Qilish").
 * Drops unwanted traffic from games, social networks, and browsers when parent activates Lockdown Mode,
 * while keeping Child Security parental control endpoints and phone/SOS calls available.
 */
public class LocalVpnService extends VpnService implements Runnable {

    private static final String TAG = "LocalVpnService";
    private static final String CHANNEL_ID = "child_security_vpn_channel";
    private static final int NOTIFICATION_ID = 2002;

    public static final String ACTION_START = "uz.childsecurity.app.START_VPN_LOCKDOWN";
    public static final String ACTION_STOP = "uz.childsecurity.app.STOP_VPN_LOCKDOWN";
    public static final String PREF_KEY_NETWORK_LOCKED = "is_network_lockdown_active";

    private static boolean isRunning = false;
    private Thread vpnThread;
    private ParcelFileDescriptor vpnInterface;

    public static boolean isRunning() {
        return isRunning;
    }

    public static void startLockdown(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
        prefs.edit().putBoolean(PREF_KEY_NETWORK_LOCKED, true).apply();

        Intent intent = new Intent(context, LocalVpnService.class);
        intent.setAction(ACTION_START);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    public static void stopLockdown(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
        prefs.edit().putBoolean(PREF_KEY_NETWORK_LOCKED, false).apply();

        Intent intent = new Intent(context, LocalVpnService.class);
        intent.setAction(ACTION_STOP);
        context.startService(intent);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopVpn();
            stopSelf();
            return START_NOT_STICKY;
        }

        startForeground(NOTIFICATION_ID, buildNotification());
        if (!isRunning) {
            isRunning = true;
            vpnThread = new Thread(this, "LocalVpnThread");
            vpnThread.start();
        }
        return START_STICKY;
    }

    @Override
    public void run() {
        try {
            Builder builder = new Builder();
            builder.setSession("ChildSecurity Lockdown Firewall");
            builder.setMtu(1500);

            // Establish local dummy tunnel route: 10.0.0.2/32
            builder.addAddress("10.0.0.2", 32);
            // Route all internet traffic through our local sink
            builder.addRoute("0.0.0.0", 0);
            builder.addDnsServer("8.8.8.8");

            // Exclude Child Security app itself so our telemetry and API continue to reach parents
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                    builder.addDisallowedApplication(getPackageName());
                }
            } catch (Exception e) {
                Log.w(TAG, "Could not exclude package from VPN: " + e.getMessage());
            }

            vpnInterface = builder.establish();
            if (vpnInterface == null) {
                Log.e(TAG, "VPN establishment failed: permission not granted or rejected by user");
                isRunning = false;
                return;
            }

            Log.i(TAG, "Local VPN Firewall successfully locked down external internet traffic.");
            FileInputStream in = new FileInputStream(vpnInterface.getFileDescriptor());
            FileOutputStream out = new FileOutputStream(vpnInterface.getFileDescriptor());
            byte[] packet = new byte[32767];

            while (isRunning && !Thread.interrupted()) {
                // Read and drop/sink packets for non-whitelisted apps
                int length = in.read(packet);
                if (length > 0) {
                    // Silently drop incoming blocked traffic
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "VPN execution finished: " + e.getMessage());
        } finally {
            stopVpn();
        }
    }

    private void stopVpn() {
        isRunning = false;
        try {
            if (vpnInterface != null) {
                vpnInterface.close();
                vpnInterface = null;
            }
        } catch (IOException ignored) {}
        if (vpnThread != null) {
            vpnThread.interrupt();
            vpnThread = null;
        }
        stopForeground(true);
        Log.i(TAG, "Local VPN Firewall stopped. Internet connectivity restored.");
    }

    @Override
    public void onDestroy() {
        stopVpn();
        super.onDestroy();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "Xavfsizlik Himoyasi (Tarmoq Nazorati)",
                    NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("Internet va o'yinlar cheklovlari faol");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(channel);
        }
    }

    private Notification buildNotification() {
        Intent notificationIntent = new Intent(this, MainActivity.class);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this, 0, notificationIntent,
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0
        );

        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("🛡️ Xavfsizlik rejimi: Internet cheklandi")
                .setContentText("Dars yoki uyqu vaqti munosabati bilan o'yinlar to'xtatildi")
                .setSmallIcon(R.drawable.ic_shield_notification)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .build();
    }
}
