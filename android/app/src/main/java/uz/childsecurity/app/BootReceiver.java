package uz.childsecurity.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.util.Log;

import androidx.core.content.ContextCompat;

/**
 * Automatically revives ChildTrackingService when the phone boots up.
 */
public class BootReceiver extends BroadcastReceiver {

    private static final String TAG = "BootReceiver";

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent != null ? intent.getAction() : null;
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) ||
            Intent.ACTION_MY_PACKAGE_REPLACED.equals(action) ||
            "android.intent.action.QUICKBOOT_POWERON".equals(action)) {

            SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, Context.MODE_PRIVATE);
            String childId = prefs.getString(ChildTrackingService.KEY_CHILD_ID, null);

            if (childId != null && !childId.isEmpty()) {
                Log.d(TAG, "Boot completed: restarting ChildTrackingService for child " + childId);
                Intent serviceIntent = new Intent(context, ChildTrackingService.class);
                serviceIntent.putExtra(ChildTrackingService.KEY_CHILD_ID, childId);

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    ContextCompat.startForegroundService(context, serviceIntent);
                } else {
                    context.startService(serviceIntent);
                }
            }
        }
    }
}
