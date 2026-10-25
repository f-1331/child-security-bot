package uz.childsecurity.app;

import android.app.admin.DeviceAdminReceiver;
import android.content.Context;
import android.content.Intent;
import android.widget.Toast;

/**
 * Device Administrator Receiver
 * Protects Child Security application against unauthorized uninstallation.
 */
public class ChildDeviceAdminReceiver extends DeviceAdminReceiver {

    @Override
    public void onEnabled(Context context, Intent intent) {
        super.onEnabled(context, intent);
        Toast.makeText(context, "🛡️ Child Security o'chirishdan himoyasi faollashtirildi", Toast.LENGTH_SHORT).show();
    }

    @Override
    public CharSequence onDisableRequested(Context context, Intent intent) {
        return "⚠️ DIQQAT: Child Security ota-ona xavfsizligi tizimi bilan himoyalangan. Uni o'chirish faqat ota-ona ruxsati va maxfiy PIN-kodi bilan amalga oshiriladi!";
    }

    @Override
    public void onDisabled(Context context, Intent intent) {
        super.onDisabled(context, intent);
    }
}
