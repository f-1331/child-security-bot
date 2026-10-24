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
    public static final String PRODUCTION_URL = "https://child-security-bot.vercel.app?source=apk";
    public static final String OFFLINE_URL = "file:///android_asset/index.html";

    private WebView webView;

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
                // If main production page failed to load, load local bundled assets
                if (request.isForMainFrame() && !request.getUrl().toString().startsWith("file:")) {
                    Log.w(TAG, "Network failed, loading local bundled offline assets");
                    view.loadUrl(OFFLINE_URL);
                }
            }
        });

        // Inject Native Android Bridge
        webView.addJavascriptInterface(new AndroidBridge(this), "AndroidBridge");

        // Request runtime permissions and battery optimization
        checkAndRequestPermissions();
        requestBatteryOptimization();

        // Check if child ID is already known: auto-start tracking service
        SharedPreferences prefs = getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
        String savedChildId = prefs.getString(ChildTrackingService.KEY_CHILD_ID, null);
        if (savedChildId != null && !savedChildId.isEmpty()) {
            startChildTrackingService(savedChildId, PRODUCTION_URL);
        }

        // Load production WebApp
        webView.loadUrl(PRODUCTION_URL);
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
                        .setNegativeButton("Keyinroq", (dialog, which) -> dialog.dismiss())
                        .setCancelable(false)
                        .show();
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST_CODE) {
            checkBackgroundLocationPermission();
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
        public void startBackgroundTracking(String childId, String serverUrl) {
            startChildTrackingService(childId, serverUrl != null ? serverUrl : PRODUCTION_URL);
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
        public void requestBatteryOptimizationExemption() {
            runOnUiThread(() -> requestBatteryOptimization());
        }
    }
}
