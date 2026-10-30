package uz.childsecurity.app;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;

import java.util.HashSet;
import java.util.Set;

/**
 * Child Security Accessibility Service
 * 1. Enforces App Lockdown and selective app blocking in real-time.
 * 2. Protects phone settings: Prevents child from revoking permissions or uninstalling without parent PIN.
 */
public class ChildSecurityAccessibilityService extends AccessibilityService {

    private static final String TAG = "ChildAccessibility";

    private final Set<String> whitelistedPackages = new HashSet<>();

    @Override
    public void onCreate() {
        super.onCreate();
        initWhitelist();
    }

    private void initWhitelist() {
        whitelistedPackages.clear();
        whitelistedPackages.add(getPackageName()); // Child Security app itself
        whitelistedPackages.add("com.android.dialer");
        whitelistedPackages.add("com.google.android.dialer");
        whitelistedPackages.add("com.samsung.android.dialer");
        whitelistedPackages.add("com.android.phone");
        whitelistedPackages.add("com.android.systemui");
        whitelistedPackages.add("com.google.android.permissioncontroller");
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;

        CharSequence pkgChar = event.getPackageName();
        if (pkgChar == null) return;
        String currentPackage = pkgChar.toString();

        if (whitelistedPackages.contains(currentPackage)) {
            return;
        }

        SharedPreferences prefs = getSharedPreferences(ChildTrackingService.PREFS_NAME, Context.MODE_PRIVATE);

        // 0. ANTI-CLOCK TAMPERING SHIELD (Soatni orqaga yoki oldinga surib blokdan qochishni aniqlash)
        long currentWallMs = System.currentTimeMillis();
        long currentElapsedMs = android.os.SystemClock.elapsedRealtime();
        long lastWall = prefs.getLong("last_check_wall_time", 0);
        long lastElapsed = prefs.getLong("last_check_elapsed_time", 0);

        if (lastWall > 0 && lastElapsed > 0) {
            long wallDelta = currentWallMs - lastWall;
            long elapsedDelta = currentElapsedMs - lastElapsed;
            // If elapsed time grew by >5s but wall time moved backward by >30s or jumped >30 min
            if (elapsedDelta >= 5000 && (wallDelta < -30000 || Math.abs(wallDelta - elapsedDelta) > 1800000)) {
                Log.w(TAG, "⚠️ Tamper detected: Child manually manipulated system clock!");
                TamperReporter.report(this, "CLOCK_MANIPULATION", "Telefon vaqti qo'lda o'zgartirildi (Vaqt manipulyatsiyasi)!");
                performGlobalAction(GLOBAL_ACTION_HOME);
                launchLockOverlay("settings", "Sozlamalar", "Telefon vaqti o'zgartirilishi aniqlandi! Cheklovni aylanib o'tish taqiqlanadi.");
                return;
            }
        }
        prefs.edit()
                .putLong("last_check_wall_time", currentWallMs)
                .putLong("last_check_elapsed_time", currentElapsedMs)
                .apply();

        // 1. SETTINGS & ANTI-UNINSTALL PROTECTION
        if (isSettingsPackage(currentPackage)) {
            boolean antiUninstall = prefs.getBoolean("anti_uninstall_enabled", true);
            if (antiUninstall) {
                long tempUnlock = prefs.getLong("temp_settings_unlock_time", 0);
                boolean isTempUnlocked = (System.currentTimeMillis() - tempUnlock) < 300000L; // 5 min

                if (!isTempUnlocked && isSensitiveSettingsScreen(event)) {
                    Log.w(TAG, "Child attempted to enter sensitive settings! Intercepting...");
                    performGlobalAction(GLOBAL_ACTION_HOME);

                    Intent pinIntent = new Intent(this, ParentPinVerifyActivity.class);
                    pinIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
                    startActivity(pinIntent);
                    return;
                }
            }
        }

        // 1.5. BEDTIME OFFLINE SCHEDULE (Internetsiz ham aniq ishlaydigan uyqu rejimi)
        String bedtimeStart = prefs.getString("bedtime_start", "");
        String bedtimeEnd = prefs.getString("bedtime_end", "");
        if (isWithinBedtime(bedtimeStart, bedtimeEnd)) {
            String appName = getAppLabel(currentPackage);
            launchLockOverlay(currentPackage, appName, "🌙 Uyqu vaqti rejimi: Telefon dam olish vaqtida (Bloklangan)");
            return;
        }

        // 2. MASTER LOCKDOWN (BARCHA ILOVALARNI BLOKLASH)
        boolean isAllBlocked = prefs.getBoolean("is_all_apps_blocked", false);
        if (isAllBlocked) {
            String reason = prefs.getString("lockdown_reason", "Dars yoki uyqu vaqti");
            String appName = getAppLabel(currentPackage);

            launchLockOverlay(currentPackage, appName, reason);
            return;
        }

        // 3. SELECTIVE APP BLOCKING
        String blockedJson = prefs.getString("blocked_packages", "[]");
        if (isPackageInBlockedList(currentPackage, blockedJson)) {
            long tempPkgUnlock = prefs.getLong("temp_unlock_" + currentPackage, 0);
            if (System.currentTimeMillis() > tempPkgUnlock) {
                String appName = getAppLabel(currentPackage);
                launchLockOverlay(currentPackage, appName, "Ota-onangiz ushbu ilovani bloklab qo'ygan");
            }
        }
    }

    private boolean isSettingsPackage(String pkg) {
        return pkg.equals("com.android.settings") ||
               pkg.equals("com.miui.securitycenter") ||
               pkg.equals("com.google.android.packageinstaller") ||
               pkg.equals("com.android.packageinstaller");
    }

    private boolean isSensitiveSettingsScreen(AccessibilityEvent event) {
        try {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                if (containsKeyword(root, "Child Security") ||
                    containsKeyword(root, "uz.childsecurity.app") ||
                    containsKeyword(root, "Uninstall") ||
                    containsKeyword(root, "O'chirish") ||
                    containsKeyword(root, "Удалить") ||
                    containsKeyword(root, "Clear data") ||
                    containsKeyword(root, "Tozalash") ||
                    containsKeyword(root, "Force stop") ||
                    containsKeyword(root, "To'xtatish") ||
                    containsKeyword(root, "Device admin") ||
                    containsKeyword(root, "Qurilma administratori") ||
                    containsKeyword(root, "Maxsus imkoniyatlar")) {
                    return true;
                }
            }
        } catch (Exception ignored) {}
        return false;
    }

    private boolean containsKeyword(AccessibilityNodeInfo node, String keyword) {
        if (node == null) return false;
        CharSequence text = node.getText();
        if (text != null && text.toString().toLowerCase().contains(keyword.toLowerCase())) {
            return true;
        }
        CharSequence desc = node.getContentDescription();
        if (desc != null && desc.toString().toLowerCase().contains(keyword.toLowerCase())) {
            return true;
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            if (containsKeyword(node.getChild(i), keyword)) {
                return true;
            }
        }
        return false;
    }

    private boolean isPackageInBlockedList(String pkg, String json) {
        try {
            JSONArray arr = new JSONArray(json);
            for (int i = 0; i < arr.length(); i++) {
                if (pkg.equals(arr.getString(i))) {
                    return true;
                }
            }
        } catch (Exception ignored) {}
        return false;
    }

    private String getAppLabel(String pkg) {
        try {
            PackageManager pm = getPackageManager();
            ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
            CharSequence label = pm.getApplicationLabel(ai);
            if (label != null) return label.toString();
        } catch (Exception ignored) {}
        return pkg;
    }

    private boolean isWithinBedtime(String start, String end) {
        if (start == null || start.isEmpty() || end == null || end.isEmpty()) return false;
        try {
            java.util.Calendar now = java.util.Calendar.getInstance();
            int curMinutes = now.get(java.util.Calendar.HOUR_OF_DAY) * 60 + now.get(java.util.Calendar.MINUTE);

            String[] s = start.split(":");
            int sMinutes = Integer.parseInt(s[0].trim()) * 60 + Integer.parseInt(s[1].trim());

            String[] e = end.split(":");
            int eMinutes = Integer.parseInt(e[0].trim()) * 60 + Integer.parseInt(e[1].trim());

            if (sMinutes <= eMinutes) {
                return curMinutes >= sMinutes && curMinutes < eMinutes;
            } else {
                return curMinutes >= sMinutes || curMinutes < eMinutes;
            }
        } catch (Exception ignored) {
            return false;
        }
    }

    private void launchLockOverlay(String pkg, String appName, String reason) {
        performGlobalAction(GLOBAL_ACTION_HOME);

        Intent intent = new Intent(this, LockOverlayActivity.class);
        intent.putExtra(LockOverlayActivity.EXTRA_PACKAGE, pkg);
        intent.putExtra(LockOverlayActivity.EXTRA_APP_NAME, appName);
        intent.putExtra(LockOverlayActivity.EXTRA_REASON, reason);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        startActivity(intent);
    }

    @Override
    public void onInterrupt() {}

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        AccessibilityServiceInfo info = new AccessibilityServiceInfo();
        info.eventTypes = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED;
        info.feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC;
        info.flags = AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS |
                     AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS |
                     AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS;
        info.notificationTimeout = 80;
        setServiceInfo(info);
        Log.d(TAG, "ChildSecurityAccessibilityService connected & active");
    }
}
