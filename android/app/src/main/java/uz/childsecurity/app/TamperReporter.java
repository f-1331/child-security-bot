package uz.childsecurity.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.util.Log;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Dispatches real-time tamper and intrusion security alerts to backend server
 * (3x wrong PIN, clock tampering, accessibility disable attempts, mock GPS).
 */
public class TamperReporter {

    private static final String TAG = "TamperReporter";
    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();

    public static void report(final Context context, final String eventType, final String details) {
        if (context == null) return;
        EXECUTOR.execute(new Runnable() {
            @Override
            public void run() {
                try {
                    SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, Context.MODE_PRIVATE);
                    String childId = prefs.getString(ChildTrackingService.KEY_CHILD_ID, null);
                    String serverUrl = prefs.getString(ChildTrackingService.KEY_SERVER_URL, ChildTrackingService.DEFAULT_SERVER_URL);

                    if (childId == null || childId.trim().isEmpty()) {
                        Log.w(TAG, "Cannot report tamper: child_id not configured");
                        return;
                    }

                    float lat = prefs.getFloat("cached_lat", 0.0f);
                    float lon = prefs.getFloat("cached_lon", 0.0f);

                    JSONObject payload = new JSONObject();
                    payload.put("event_type", eventType != null ? eventType : "SECURITY_TAMPER");
                    payload.put("details", details != null ? details : "Xavfsizlik qoidabuzarligi aniqlandi");
                    payload.put("latitude", (double) lat);
                    payload.put("longitude", (double) lon);
                    payload.put("device_model", Build.MANUFACTURER + " " + Build.MODEL);

                    byte[] postData = payload.toString().getBytes(StandardCharsets.UTF_8);
                    String cleanBase = serverUrl.replaceAll("/+$", "");
                    String endpoint = cleanBase + "/api/v1/webapp/child/" + childId.trim() + "/tamper-alert";

                    URL url = new URL(endpoint);
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
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
                    Log.i(TAG, "Tamper alert [" + eventType + "] sent. Response code: " + code);
                    conn.disconnect();
                } catch (Exception e) {
                    Log.w(TAG, "Failed to send tamper alert: " + e.getMessage());
                }
            }
        });
    }
}
