package uz.childsecurity.app;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

/**
 * Fullscreen Lock Overlay Screen
 * Displays when a blocked app is opened or when the device is in total lockdown mode.
 */
public class LockOverlayActivity extends AppCompatActivity {

    public static final String EXTRA_APP_NAME = "extra_app_name";
    public static final String EXTRA_REASON = "extra_reason";
    public static final String EXTRA_PACKAGE = "extra_package";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        String appName = getIntent().getStringExtra(EXTRA_APP_NAME);
        if (appName == null || appName.isEmpty()) appName = "Ilova";
        String reason = getIntent().getStringExtra(EXTRA_REASON);
        if (reason == null || reason.isEmpty()) reason = "Dars vaqti (Ota-ona nazorati)";

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(0xFF090D16);
        layout.setGravity(Gravity.CENTER);
        layout.setPadding(48, 64, 48, 64);

        // 1. Shield / Lock Icon
        TextView iconView = new TextView(this);
        iconView.setText("🔒");
        iconView.setTextSize(64);
        iconView.setGravity(Gravity.CENTER);
        layout.addView(iconView);

        // 2. Title
        TextView titleView = new TextView(this);
        titleView.setText("Ushbu ilova bloklangan");
        titleView.setTextColor(Color.WHITE);
        titleView.setTextSize(22);
        titleView.setTypeface(null, Typeface.BOLD);
        titleView.setGravity(Gravity.CENTER);
        titleView.setPadding(0, 24, 0, 8);
        layout.addView(titleView);

        // 3. App Name
        TextView appNameView = new TextView(this);
        appNameView.setText(appName);
        appNameView.setTextColor(0xFF38BDF8); // Cyan
        appNameView.setTextSize(16);
        appNameView.setTypeface(null, Typeface.BOLD);
        appNameView.setGravity(Gravity.CENTER);
        appNameView.setPadding(0, 0, 0, 16);
        layout.addView(appNameView);

        // 4. Reason Subtitle
        TextView reasonView = new TextView(this);
        reasonView.setText("Ota-onangiz ushbu ilovadan foydalanishni cheklagan:\n«" + reason + "»\n\nFaqat ota-onaga qo'ng'iroq qilish va favqulodda SOS ruxsat etiladi.");
        reasonView.setTextColor(0xFF94A3B8); // Slate 400
        reasonView.setTextSize(13);
        reasonView.setGravity(Gravity.CENTER);
        reasonView.setLineSpacing(6, 1);
        reasonView.setPadding(0, 0, 0, 36);
        layout.addView(reasonView);

        // 5. Button: Return Home
        Button btnHome = new Button(this);
        btnHome.setText("🏠 Bosh ekranga qaytish");
        btnHome.setTextColor(Color.WHITE);
        btnHome.setBackgroundColor(0xFF2563EB); // Blue 600
        btnHome.setTextSize(14);
        btnHome.setTypeface(null, Typeface.BOLD);
        btnHome.setPadding(24, 20, 24, 20);
        btnHome.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Intent homeIntent = new Intent(Intent.ACTION_MAIN);
                homeIntent.addCategory(Intent.CATEGORY_HOME);
                homeIntent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(homeIntent);
                finish();
            }
        });
        layout.addView(btnHome);

        // Space
        View space1 = new View(this);
        space1.setMinimumHeight(16);
        layout.addView(space1);

        // 6. Button: Call Parent
        SharedPreferences prefs = getSharedPreferences(ChildTrackingService.PREFS_NAME, MODE_PRIVATE);
        final String parentPhone = prefs.getString("parent_phone", null);
        if (parentPhone != null && !parentPhone.isEmpty()) {
            Button btnCall = new Button(this);
            btnCall.setText("📞 Ota-onaga qo'ng'iroq (" + parentPhone + ")");
            btnCall.setTextColor(0xFF34D399); // Emerald
            btnCall.setBackgroundColor(0xFF0F172A);
            btnCall.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    try {
                        Intent callIntent = new Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + parentPhone));
                        startActivity(callIntent);
                    } catch (Exception ignored) {}
                }
            });
            layout.addView(btnCall);
        }

        // Space
        View space2 = new View(this);
        space2.setMinimumHeight(16);
        layout.addView(space2);

        // 7. Button: Parent PIN Unlock
        Button btnPin = new Button(this);
        btnPin.setText("🔑 Ota-ona PIN-kodi");
        btnPin.setTextColor(0xFFFCD34D); // Amber 300
        btnPin.setBackgroundColor(0xFF1E293B);
        btnPin.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Intent pinIntent = new Intent(LockOverlayActivity.this, ParentPinVerifyActivity.class);
                pinIntent.putExtra(ParentPinVerifyActivity.EXTRA_UNLOCK_PACKAGE, getIntent().getStringExtra(EXTRA_PACKAGE));
                startActivity(pinIntent);
                finish();
            }
        });
        layout.addView(btnPin);

        setContentView(layout);
    }

    @Override
    public void onBackPressed() {
        // Redirect to Home launcher
        Intent homeIntent = new Intent(Intent.ACTION_MAIN);
        homeIntent.addCategory(Intent.CATEGORY_HOME);
        homeIntent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(homeIntent);
        finish();
    }
}
