package uz.childsecurity.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import java.util.ArrayList;
import java.util.List;

public class MainActivity extends AppCompatActivity {

    private static final String TAG = "MainActivity";
    private static final int PERMISSION_REQUEST_CODE = 2001;
    public static final String BACKEND_API_URL = "https://child-security-site.onrender.com";
    private String appRole = "parent";
    private String currentProductionUrl = "https://child-security-bot.vercel.app?role=parent&source=parent_apk";
    private String currentOfflineUrl = "file:///android_asset/index.html?role=parent&source=parent_apk";

    private WebView webView;

    private void detectRole() {
        // 1. Try BuildConfig APP_ROLE reflection
        try {
            Class<?> clazz = Class.forName(getPackageName() + ".BuildConfig");
            java.lang.reflect.Field field = clazz.getField("APP_ROLE");
            String r = (String) field.get(null);
            if (r != null && !r.isEmpty()) {
                appRole = r;
            }
        } catch (Exception ignored) {}

        // 2. Try assets/app_role.json
        try {
            java.io.InputStream is = getAssets().open("app_role.json");
            int size = is.available();
            byte[] buf = new byte[size];
            is.read(buf);
            is.close();
            org.json.JSONObject obj = new org.json.JSONObject(new String(buf, "UTF-8"));
            if (obj.has("role")) {
                appRole = obj.getString("role");
            }
        } catch (Exception ignored) {}

        // 3. Fallback package name check
        if (getPackageName().endsWith(".child")) {
            appRole = "child";
        } else if (getPackageName().endsWith(".parent")) {
            appRole = "parent";
        }

        boolean isChild = "child".equalsIgnoreCase(appRole);
        currentProductionUrl = isChild
                ? "https://child-security-bot.vercel.app?role=child&source=child_apk"
                : "https://child-security-bot.vercel.app?role=parent&source=parent_apk";

        currentOfflineUrl = isChild
                ? "file:///android_asset/index.html?role=child&source=child_apk"
                : "file:///android_asset/index.html?role=parent&source=parent_apk";

        Log.i(TAG, "Detected App Role: " + appRole + " | Target URL: " + currentProductionUrl);
    }

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Full-screen root layout
        FrameLayout rootLayout = new FrameLayout(this);
        rootLayout.setBackgroundColor(0xFF090D16);

        webView = new WebView(this);
        webView.setBackgroundColor(0xFF090D16);
        rootLayout.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
        setContentView(rootLayout);

        // Configure modern WebView settings
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setGeolocationEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        // Auto-grant Geolocation without browser prompt popups
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                callback.invoke(origin, true, false);
            }
        });

        // Detect whether this APK is for Parent or Child
        detectRole();
        final boolean isChild = "child".equalsIgnoreCase(appRole);

        // WebView navigation handling with offline fallback
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri.getScheme();
                if ("tel".equals(scheme) || "mailto".equals(scheme) || "tg".equals(scheme)) {
                    try {
                        Intent intent = new Intent(Intent.ACTION_VIEW, uri);
                        startActivity(intent);
                        return true;
                    } catch (Exception ignored) {}
                }
                return false;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request.isForMainFrame() && !request.getUrl().toString().startsWith("file:")) {
                    Log.w(TAG, "Network failed, loading local bundled offline assets");
                    view.loadUrl(currentOfflineUrl);
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                view.evaluateJavascript("if (typeof syncWithNativeAndroidBridge === 'function') { syncWithNativeAndroidBridge(); }", null);
                view.evaluateJavascript("if (typeof setAppNativeRole === 'function') { setAppNativeRole('" + appRole + "'); }", null);
            }
        });

        // Inject Native Android Bridge
        webView.addJavascriptInterface(new AndroidBridge(this), "AndroidBridge");

        if (isChild) {
            // Farzand telefoni: 24/7 foniy GPS, Accessibility, Device Admin va Batareya optimallashtirish
            checkAndRequestPermissions();
            requestBatteryOptimization();

            SharedPreferences prefs = getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
            String savedChildId = prefs.getString(ChildTrackingService.KEY_CHILD_ID, null);
            if (savedChildId != null && !savedChildId.isEmpty()) {
                startChildTrackingService(savedChildId, BACKEND_API_URL);
            }
        } else {
            // Ota-ona telefoni: Faqatgina favqulodda SOS bildirishnomalarini qabul qilish ruxsati (Android 13+)
            // Ota-ona telefoni kuzatilmaydi, u boshqaruv pulti hisoblanadi!
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.POST_NOTIFICATIONS}, 101);
                }
            }
        }

        // Load role-specific production WebApp
        webView.loadUrl(currentProductionUrl);
    }

    private void checkAndRequestPermissions() {
        List<String> needed = new ArrayList<>();

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.ACCESS_COARSE_LOCATION);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                needed.add(Manifest.permission.POST_NOTIFICATIONS);
            }
        }

        if (!needed.isEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toArray(new String[0]), PERMISSION_REQUEST_CODE);
        } else {
            checkBackgroundLocationPermission();
        }
    }

    private void checkBackgroundLocationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_BACKGROUND_LOCATION) != PackageManager.PERMISSION_GRANTED) {
                new androidx.appcompat.app.AlertDialog.Builder(this)
                        .setTitle("📍 Doimiy Joylashuv (Xavfsizlik Himoyasi)")
                        .setMessage("Child Security farzand xavfsizligini ta'minlash, xavfsiz makonlarga yetib borishni aniqlash va favqulodda SOS holatlarida ota-onaga tezkor yordam ko'rsatish maqsadida, ilova yopiq yoki fonda bo'lgan paytda ham joylashuv ma'lumotlarini to'playdi.\n\nUshbu ma'lumotlar faqat ota-onangizga ko'rsatiladi va uchinchi shaxslarga berilmaydi.\n\nIltimos, keyingi oynada \"Har doim ruxsat berish\" (Allow all the time) bandini tanlang.")
                        .setPositiveButton("Tushundim (Ruxsat berish)", (dialog, which) -> {
                            ActivityCompat.requestPermissions(MainActivity.this, new String[]{Manifest.permission.ACCESS_BACKGROUND_LOCATION}, PERMISSION_REQUEST_CODE + 1);
                        })
                        .setNegativeButton("Keyinroq", (dialog, which) -> {
                            dialog.dismiss();
                            checkUsageAccessPermission();
                        })
                        .setCancelable(false)
                        .show();
                return;
            }
        }
        checkUsageAccessPermission();
    }

    public void checkUsageAccessPermission() {
        if (!UsageStatsHelper.hasUsageAccessPermission(this)) {
            new androidx.appcompat.app.AlertDialog.Builder(this)
                    .setTitle("📊 Ilovalar Tahlili va Ekran Vaqti")
                    .setMessage("Farzandingiz qaysi o'yinlar va ilovalarda (YouTube, TikTok, o'yinlar) qancha vaqt o'tkazayotganini tahlil qilish va ota-onaga ko'rsatish uchun «Foydalanish statistikasi» (Usage Access) ruxsati zarur.\n\nOchilgan sozlamalarda «Child Security» ilovasini topib, ruxsat bering.")
                    .setPositiveButton("Ruxsat berish", (dialog, which) -> {
                        try {
                            Intent intent = new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS);
                            startActivity(intent);
                        } catch (Exception ignored) {}
                    })
                    .setNegativeButton("Keyinroq", (dialog, which) -> {
                        dialog.dismiss();
                        checkOverlayPermission();
                    })
                    .setCancelable(false)
                    .show();
        } else {
            checkOverlayPermission();
        }
    }

    public void checkOverlayPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (!Settings.canDrawOverlays(this)) {
                new androidx.appcompat.app.AlertDialog.Builder(this)
                        .setTitle("🔒 Ilovalarni Bloklash Qalqoni (Overlay)")
                        .setMessage("Ota-ona ilovalarni bloklaganda yoki belgilangan dars/uyqu vaqti bo'lganda, ekranda to'liq qulflash oynasini ko'rsatish uchun «Boshqa ilovalar ustida ko'rinish» (Display over other apps) ruxsati zarur.\n\nIltimos, ruxsat bering.")
                        .setPositiveButton("Ruxsat berish", (dialog, which) -> {
                            try {
                                Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + getPackageName()));
                                startActivity(intent);
                            } catch (Exception e) {
                                try {
                                    Intent fallback = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION);
                                    startActivity(fallback);
                                } catch (Exception ignored) {}
                            }
                        })
                        .setNegativeButton("Keyinroq", (dialog, which) -> {
                            dialog.dismiss();
                            checkAccessibilityPermission();
                        })
                        .setCancelable(false)
                        .show();
                return;
            }
        }
        checkAccessibilityPermission();
    }

    public boolean isAccessibilityServiceEnabled() {
        try {
            int accessibilityEnabled = 0;
            final String service = getPackageName() + "/" + ChildSecurityAccessibilityService.class.getCanonicalName();
            accessibilityEnabled = Settings.Secure.getInt(
                    getContentResolver(),
                    Settings.Secure.ACCESSIBILITY_ENABLED
            );
            if (accessibilityEnabled == 1) {
                String settingValue = Settings.Secure.getString(
                        getContentResolver(),
                        Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
                );
                if (settingValue != null) {
                    return settingValue.contains(service) || settingValue.contains(ChildSecurityAccessibilityService.class.getSimpleName());
                }
            }
        } catch (Exception ignored) {}
        return false;
    }

    public void checkAccessibilityPermission() {
        if (!isAccessibilityServiceEnabled()) {
            new androidx.appcompat.app.AlertDialog.Builder(this)
                    .setTitle("🛡 Ilovalarni Bloklash va Himoya (Accessibility)")
                    .setMessage("Farzand bloklangan ilovani ochganda uni darhol to'xtatish va ilovani telefon sozlamalaridan o'chirishga yo'l qo'ymaslik uchun «Maxsus imkoniyatlar» (Accessibility) xizmatini yoqing.\n\n⚠️ DIQQAT (Android 13/14/15 uchun):\nAgar tizim «Cheklangan sozlama» (Restricted setting) desa:\n1. Telefon sozlamalari ➔ Ilovalar (Apps) ➔ Child Security ga kiring;\n2. Yuqori o'ng burchakdagi 3 nuqta (⋮) ni bosing;\n3. «Cheklangan sozlamalarga ruxsat berish» (Allow restricted settings) ni bosing;\n4. So'ng Maxsus imkoniyatlardan Child Security'ni yoqing.")
                    .setPositiveButton("Xizmatni yoqish", (dialog, which) -> {
                        try {
                            Intent intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
                            startActivity(intent);
                        } catch (Exception ignored) {}
                    })
                    .setNeutralButton("📱 Ilova sozlamalari (3 nuqta)", (dialog, which) -> {
                        try {
                            Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
                            intent.setData(Uri.parse("package:" + getPackageName()));
                            startActivity(intent);
                        } catch (Exception ignored) {}
                    })
                    .setNegativeButton("Tushundim", (dialog, which) -> dialog.dismiss())
                    .setCancelable(false)
                    .show();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST_CODE) {
            checkBackgroundLocationPermission();
        } else if (requestCode == PERMISSION_REQUEST_CODE + 1) {
            checkUsageAccessPermission();
        }
    }

    @SuppressLint("BatteryLife")
    private void requestBatteryOptimization() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
                if (pm != null && !pm.isIgnoringBatteryOptimizations(getPackageName())) {
                    Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                    intent.setData(Uri.parse("package:" + getPackageName()));
                    startActivity(intent);
                }
            } catch (Exception ignored) {}
        }
    }

    private void startChildTrackingService(String childId, String serverUrl) {
        try {
            Intent intent = new Intent(this, ChildTrackingService.class);
            intent.putExtra(ChildTrackingService.KEY_CHILD_ID, childId);
            intent.putExtra(ChildTrackingService.KEY_SERVER_URL, serverUrl);

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ContextCompat.startForegroundService(this, intent);
            } else {
                startService(intent);
            }
        } catch (Exception e) {
            Log.e(TAG, "Failed to start tracking service: " + e.getMessage());
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null) {
            // Trigger web app back button handler first
            webView.evaluateJavascript("if (typeof handleTelegramBackPress === 'function') { handleTelegramBackPress(); } else { window.history.back(); }", null);
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.evaluateJavascript("if (typeof refreshProtectionPermissionsStatus === 'function') { refreshProtectionPermissionsStatus(); }", null);
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            android.webkit.CookieManager.getInstance().flush();
        }
    }

    /**
     * JavaScript Interface Bridge exposed to web app as window.AndroidBridge
     */
    public class AndroidBridge {
        private final Context context;

        public AndroidBridge(Context context) {
            this.context = context;
        }

        @JavascriptInterface
        public boolean isNativeApp() {
            return true;
        }

        @JavascriptInterface
        public String getAppRole() {
            return appRole;
        }

        @JavascriptInterface
        public boolean isParentApp() {
            return "parent".equalsIgnoreCase(appRole);
        }

        @JavascriptInterface
        public boolean isChildApp() {
            return "child".equalsIgnoreCase(appRole);
        }

        @JavascriptInterface
        public void startBackgroundTracking(String childId, String serverUrl) {
            String cleanServer = (serverUrl != null && !serverUrl.contains("?")) ? serverUrl : BACKEND_API_URL;
            startChildTrackingService(childId, cleanServer);
        }

        @JavascriptInterface
        public boolean isCharging() {
            try {
                android.content.IntentFilter ifilter = new android.content.IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED);
                android.content.Intent batteryStatus = context.registerReceiver(null, ifilter);
                if (batteryStatus != null) {
                    int status = batteryStatus.getIntExtra(android.os.BatteryManager.EXTRA_STATUS, -1);
                    return status == android.os.BatteryManager.BATTERY_STATUS_CHARGING ||
                           status == android.os.BatteryManager.BATTERY_STATUS_FULL;
                }
            } catch (Exception ignored) {}
            return false;
        }

        @JavascriptInterface
        public void stopBackgroundTracking() {
            try {
                SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
                prefs.edit().remove(ChildTrackingService.KEY_CHILD_ID).apply();
                Intent intent = new Intent(context, ChildTrackingService.class);
                context.stopService(intent);
            } catch (Exception e) {
                Log.e(TAG, "Error stopping tracking service: " + e.getMessage());
            }
        }

        @JavascriptInterface
        public void saveSession(String sessionJson, String authCode, String role) {
            try {
                SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
                SharedPreferences.Editor ed = prefs.edit();
                if (sessionJson != null) ed.putString("saved_session_json", sessionJson);
                if (authCode != null) ed.putString("saved_auth_code", authCode);
                if (role != null) ed.putString("saved_role", role);
                ed.apply();
                Log.d(TAG, "Saved permanent session in native Android SharedPreferences");
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public String getSavedSession() {
            try {
                SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
                return prefs.getString("saved_session_json", "");
            } catch (Exception ignored) {
                return "";
            }
        }

        @JavascriptInterface
        public String getSavedAuthCode() {
            try {
                SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
                return prefs.getString("saved_auth_code", "");
            } catch (Exception ignored) {
                return "";
            }
        }

        @JavascriptInterface
        public String getSavedRole() {
            try {
                SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
                return prefs.getString("saved_role", "");
            } catch (Exception ignored) {
                return "";
            }
        }

        @JavascriptInterface
        public void clearSavedSession() {
            try {
                SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
                prefs.edit()
                     .remove("saved_session_json")
                     .remove("saved_auth_code")
                     .remove("saved_role")
                     .remove(ChildTrackingService.KEY_CHILD_ID)
                     .apply();
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public void minimizeApp() {
            runOnUiThread(() -> moveTaskToBack(true));
        }

        @JavascriptInterface
        public int getBatteryLevel() {
            try {
                BatteryManager bm = (BatteryManager) context.getSystemService(Context.BATTERY_SERVICE);
                if (bm != null) {
                    return bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
                }
            } catch (Exception ignored) {}
            return 100;
        }

        @JavascriptInterface
        public void showToast(String message) {
            runOnUiThread(() -> Toast.makeText(context, message, Toast.LENGTH_SHORT).show());
        }

        @JavascriptInterface
        public void playNativeSiren() {
            try {
                android.net.Uri alert = android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_ALARM);
                if (alert == null) {
                    alert = android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_RINGTONE);
                }
                android.media.Ringtone r = android.media.RingtoneManager.getRingtone(context, alert);
                if (r != null) {
                    if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.LOLLIPOP) {
                        r.setAudioAttributes(new android.media.AudioAttributes.Builder()
                                .setUsage(android.media.AudioAttributes.USAGE_ALARM)
                                .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION)
                                .build());
                    }
                    r.play();
                    new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> {
                        try {
                            if (r.isPlaying()) r.stop();
                        } catch (Exception ignored) {}
                    }, 15000);
                }
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public void requestBatteryOptimizationExemption() {
            runOnUiThread(() -> requestBatteryOptimization());
        }

        @JavascriptInterface
        public boolean isUsageAccessGranted() {
            return UsageStatsHelper.hasUsageAccessPermission(context);
        }

        @JavascriptInterface
        public void requestUsageAccess() {
            try {
                Intent intent = new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(intent);
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public boolean isOverlayGranted() {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                return Settings.canDrawOverlays(context);
            }
            return true;
        }

        @JavascriptInterface
        public void requestOverlayPermission() {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                try {
                    Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + context.getPackageName()));
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    context.startActivity(intent);
                } catch (Exception e) {
                    try {
                        Intent fallback = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION);
                        fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        context.startActivity(fallback);
                    } catch (Exception ignored) {}
                }
            }
        }

        @JavascriptInterface
        public boolean isAccessibilityGranted() {
            return isAccessibilityServiceEnabled();
        }

        @JavascriptInterface
        public void requestAccessibility() {
            try {
                Intent intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(intent);
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public void openAppDetailsSettings() {
            try {
                Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
                intent.setData(Uri.parse("package:" + context.getPackageName()));
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(intent);
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public void requestAllChildProtectionPermissions() {
            runOnUiThread(() -> {
                if (!UsageStatsHelper.hasUsageAccessPermission(MainActivity.this)) {
                    checkUsageAccessPermission();
                } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(MainActivity.this)) {
                    checkOverlayPermission();
                } else if (!isAccessibilityServiceEnabled()) {
                    checkAccessibilityPermission();
                } else {
                    Toast.makeText(MainActivity.this, "✅ Barcha himoya va bloklash ruxsatlari to'liq yoqilgan!", Toast.LENGTH_SHORT).show();
                }
            });
        }

        @JavascriptInterface
        public String getProtectionPermissionsStatus() {
            try {
                org.json.JSONObject obj = new org.json.JSONObject();
                obj.put("is_native", true);
                obj.put("usage_access", UsageStatsHelper.hasUsageAccessPermission(context));
                boolean overlay = true;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    overlay = Settings.canDrawOverlays(context);
                }
                obj.put("overlay", overlay);
                obj.put("accessibility", isAccessibilityServiceEnabled());
                boolean batt = true;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    PowerManager pm = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
                    batt = (pm != null && pm.isIgnoringBatteryOptimizations(context.getPackageName()));
                }
                obj.put("battery_optimization", batt);
                boolean bgLoc = true;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    bgLoc = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED;
                }
                obj.put("background_location", bgLoc);
                return obj.toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public boolean isDeviceAdminGranted() {
            try {
                android.app.admin.DevicePolicyManager dpm = (android.app.admin.DevicePolicyManager) context.getSystemService(Context.DEVICE_POLICY_SERVICE);
                android.content.ComponentName comp = new android.content.ComponentName(context, ChildDeviceAdminReceiver.class);
                return dpm != null && dpm.isAdminActive(comp);
            } catch (Exception ignored) {}
            return false;
        }

        @JavascriptInterface
        public void requestDeviceAdmin() {
            try {
                android.content.ComponentName comp = new android.content.ComponentName(context, ChildDeviceAdminReceiver.class);
                Intent intent = new Intent(android.app.admin.DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN);
                intent.putExtra(android.app.admin.DevicePolicyManager.EXTRA_DEVICE_ADMIN, comp);
                intent.putExtra(android.app.admin.DevicePolicyManager.EXTRA_ADD_EXPLANATION, "Child Security o'chirishdan himoyalanishi va xavfsizlikni ta'minlash uchun administrator ruxsati zarur.");
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(intent);
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public void setNetworkLockdown(boolean active) {
            if (active) {
                LocalVpnService.startLockdown(context);
            } else {
                LocalVpnService.stopLockdown(context);
            }
        }

        @JavascriptInterface
        public boolean isNetworkLockdownActive() {
            return LocalVpnService.isRunning();
        }

        @JavascriptInterface
        public int getOfflineLocationCount() {
            return OfflineDbHelper.getInstance(context).getUnsyncedCount();
        }

        @JavascriptInterface
        public void setBedtimeSchedule(String start, String end) {
            try {
                SharedPreferences.Editor ed = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE).edit();
                ed.putString("bedtime_start", start != null ? start : "");
                ed.putString("bedtime_end", end != null ? end : "");
                ed.apply();
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public void sendParentSmsCommand(String childPhone, String command, String pin) {
            try {
                if (childPhone == null || childPhone.isEmpty()) return;
                android.telephony.SmsManager sm = android.telephony.SmsManager.getDefault();
                String body = "#" + command.toUpperCase() + ":" + pin;
                sm.sendTextMessage(childPhone, null, body, null, null);
                showToast("SMS buyruq jo'natildi: " + body);
            } catch (Exception e) {
                showToast("SMS xatolik: " + e.getMessage());
            }
        }

        @JavascriptInterface
        public void sendEmergencySms(String recipientPhone, String messageText) {
            try {
                if (recipientPhone == null || recipientPhone.trim().isEmpty() || messageText == null || messageText.trim().isEmpty()) return;
                android.telephony.SmsManager sm = android.telephony.SmsManager.getDefault();
                java.util.ArrayList<String> parts = sm.divideMessage(messageText);
                if (parts != null && parts.size() > 1) {
                    sm.sendMultipartTextMessage(recipientPhone.trim(), null, parts, null, null);
                } else {
                    sm.sendTextMessage(recipientPhone.trim(), null, messageText, null, null);
                }
                showToast("🚨 Ota-onangizga favqulodda SMS yetkazildi!");
            } catch (Exception e) {
                Log.e("AndroidBridge", "sendEmergencySms error: " + e.getMessage());
            }
        }

        @JavascriptInterface
        public String getLastSmsChildLocation() {
            try {
                SharedPreferences prefs = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
                return prefs.getString("last_sms_child_location", "");
            } catch (Exception e) {
                return "";
            }
        }

        @JavascriptInterface
        public String getTodayScreenTimeJson() {
            try {
                return UsageStatsHelper.getDailyAppUsageStats(context).toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public void setLocalDevicePolicy(String jsonPolicy) {
            try {
                org.json.JSONObject obj = new org.json.JSONObject(jsonPolicy);
                SharedPreferences.Editor ed = context.getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE).edit();
                if (obj.has("is_all_apps_blocked")) {
                    ed.putBoolean("is_all_apps_blocked", obj.getBoolean("is_all_apps_blocked"));
                }
                if (obj.has("lockdown_reason")) {
                    ed.putString("lockdown_reason", obj.getString("lockdown_reason"));
                }
                if (obj.has("blocked_packages")) {
                    ed.putString("blocked_packages", obj.getJSONArray("blocked_packages").toString());
                }
                if (obj.has("anti_uninstall_enabled")) {
                    ed.putBoolean("anti_uninstall_enabled", obj.getBoolean("anti_uninstall_enabled"));
                }
                if (obj.has("is_network_restricted")) {
                    boolean netRestricted = obj.getBoolean("is_network_restricted");
                    ed.putBoolean("is_network_restricted", netRestricted);
                    if (netRestricted) {
                        LocalVpnService.startLockdown(context);
                    } else {
                        LocalVpnService.stopLockdown(context);
                    }
                }
                if (obj.has("bedtime_start")) {
                    ed.putString("bedtime_start", obj.getString("bedtime_start"));
                }
                if (obj.has("bedtime_end")) {
                    ed.putString("bedtime_end", obj.getString("bedtime_end"));
                }
                if (obj.has("parent_pin_hash")) {
                    ed.putString(ParentPinVerifyActivity.PREFS_PIN_HASH, obj.getString("parent_pin_hash"));
                }
                ed.apply();
            } catch (Exception ignored) {}
        }
    }
}
