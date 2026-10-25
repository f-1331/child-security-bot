package uz.childsecurity.app;

import android.app.AppOpsManager;
import android.app.usage.UsageStats;
import android.app.usage.UsageStatsManager;
import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Process;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Calendar;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

public class UsageStatsHelper {

    private static final String TAG = "UsageStatsHelper";

    public static boolean hasUsageAccessPermission(Context context) {
        try {
            AppOpsManager appOps = (AppOpsManager) context.getSystemService(Context.APP_OPS_SERVICE);
            if (appOps == null) return false;
            int mode = appOps.checkOpNoThrow(
                    AppOpsManager.OPSTR_GET_USAGE_STATS,
                    Process.myUid(),
                    context.getPackageName()
            );
            return mode == AppOpsManager.MODE_ALLOWED;
        } catch (Exception e) {
            Log.w(TAG, "Usage access check error: " + e.getMessage());
            return false;
        }
    }

    public static JSONObject getDailyAppUsageStats(Context context) {
        JSONObject result = new JSONObject();
        JSONArray appsArray = new JSONArray();
        long totalForegroundSeconds = 0;

        try {
            result.put("has_permission", hasUsageAccessPermission(context));

            if (!hasUsageAccessPermission(context)) {
                result.put("total_seconds", 0);
                result.put("unlock_count", 0);
                result.put("apps", appsArray);
                return result;
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP_MR1) {
                UsageStatsManager usm = (UsageStatsManager) context.getSystemService(Context.USAGE_STATS_SERVICE);
                if (usm != null) {
                    // Midnight today
                    Calendar calendar = Calendar.getInstance();
                    calendar.set(Calendar.HOUR_OF_DAY, 0);
                    calendar.set(Calendar.MINUTE, 0);
                    calendar.set(Calendar.SECOND, 0);
                    calendar.set(Calendar.MILLISECOND, 0);
                    long startTime = calendar.getTimeInMillis();
                    long endTime = System.currentTimeMillis();

                    List<UsageStats> statsList = usm.queryUsageStats(
                            UsageStatsManager.INTERVAL_DAILY,
                            startTime,
                            endTime
                    );

                    if (statsList != null && !statsList.isEmpty()) {
                        PackageManager pm = context.getPackageManager();

                        // Filter out 0 time and self
                        Collections.sort(statsList, new Comparator<UsageStats>() {
                            @Override
                            public int compare(UsageStats o1, UsageStats o2) {
                                return Long.compare(o2.getTotalTimeInForeground(), o1.getTotalTimeInForeground());
                            }
                        });

                        for (UsageStats us : statsList) {
                            long fgMillis = us.getTotalTimeInForeground();
                            if (fgMillis < 30000) continue; // Skip apps used < 30 seconds

                            String pkg = us.getPackageName();
                            if (pkg.equals(context.getPackageName())) continue;

                            long fgSeconds = fgMillis / 1000;
                            totalForegroundSeconds += fgSeconds;

                            String appName = pkg;
                            String category = "other";
                            String icon = "smartphone";

                            try {
                                ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
                                CharSequence label = pm.getApplicationLabel(ai);
                                if (label != null) appName = label.toString();

                                if (pkg.contains("youtube") || pkg.contains("netflix") || pkg.contains("video")) {
                                    category = "video";
                                    icon = "youtube";
                                } else if (pkg.contains("tiktok") || pkg.contains("instagram") || pkg.contains("like")) {
                                    category = "social";
                                    icon = "video";
                                } else if (pkg.contains("game") || pkg.contains("pubg") || pkg.contains("roblox") || pkg.contains("supercell")) {
                                    category = "game";
                                    icon = "gamepad-2";
                                } else if (pkg.contains("telegram") || pkg.contains("whatsapp") || pkg.contains("chat") || pkg.contains("messaging")) {
                                    category = "chat";
                                    icon = "message-circle";
                                } else if (pkg.contains("chrome") || pkg.contains("browser")) {
                                    category = "browser";
                                    icon = "globe";
                                }
                            } catch (Exception ignored) {}

                            JSONObject appItem = new JSONObject();
                            appItem.put("package", pkg);
                            appItem.put("app_name", appName);
                            appItem.put("usage_seconds", fgSeconds);
                            appItem.put("category", category);
                            appItem.put("icon", icon);
                            appsArray.put(appItem);
                        }
                    }
                }
            }

            result.put("total_seconds", totalForegroundSeconds);
            result.put("unlock_count", Math.max(1, (int) (totalForegroundSeconds / 400)));
            result.put("apps", appsArray);
        } catch (Exception e) {
            Log.e(TAG, "Error calculating usage stats: " + e.getMessage());
        }

        return result;
    }
}
