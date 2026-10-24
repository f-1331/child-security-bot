# 📱 Child Security - Android Mobil Ilovasi

Ushbu katalogda **Child Security** loyihasining to'laqonli Android mobil ilovasi joylashgan.

## 🌟 Asosiy Xususiyatlari:
1. **24/7 Doimiy Foniy GPS (Ekran o'chiq holatda ham):**
   - `ChildTrackingService.java` orqali Android Foreground Service ishlaydi.
   - Status barda "Child Security 🛡️" himoya xabarnomasi turadi.
   - `PowerManager.PARTIAL_WAKE_LOCK` yordamida telefon ekrani o'chirilgan yoki bloklangan holatda ham GPS to'xtovsiz har 20-30 soniyada serverga ma'lumot uzatadi.
2. **Avtomatik qayta ishga tushish (Boot Receiver):**
   - Telefon o'chib-yonsa ham, `BootReceiver.java` orqali ilova avtomatik qayta ishlab ketadi.
   - Bola ilovani yopib yuborsa ham foniy xizmat o'chmaydi.
3. **Avtomatik APK Yaratish (GitHub Actions CI/CD):**
   - Har safar kod GitHub'ga push qilinganda, GitHub bulutli serverlarida avtomatik ravishda `.apk` fayl yig'iladi va GitHub Releases bo'limiga joylanadi.
   - Foydalanuvchilar to'g'ridan-to'g'ri havoladan `.apk` faylni yuklab olib, telefonga o'rnatishlari mumkin.
4. **Android Studio'da ochish:**
   - Ushbu `android` papkasini bevosita **Android Studio** dasturida ochib, `Run` tugmasini bosish yoki `Build -> Build Bundle(s) / APK(s) -> Build APK(s)` orqali o'zingiz ham APK yaratishingiz mumkin.
