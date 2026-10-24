# 🚀 Child Security ilovasini Google Play Market (Console)ga joylash bo'yicha to'liq qo'llanma

Ushbu qo'llanma orqali ilovangizni **Google Play Store (Play Market)** ga 1-urinishdayoq muvaffaqiyatli topshirishingiz mumkin.

---

## 📦 Tayyorlangan fayllar ro'yxati:
1. **Google Play App Bundle:** `android/app/build/outputs/bundle/release/app-release.aab`
   *(GitHub Releases dan yuklab olasiz: `https://github.com/f-1331/child-security-bot/releases`)*
2. **512x512 Ilova belgisi (App Icon):** `android/store_assets/icon_512.png`
3. **1024x500 Banner (Feature Graphic):** `android/store_assets/feature_graphic_1024x500.png`
4. **Maxfiylik siyosati havolasi (Privacy Policy):**  
   👉 `https://child-security-bot.vercel.app/privacy.html`
5. **Keystore imzolash kaliti:** `android/app/release.keystore` (Parol: `childsecurity123`)

---

## 1-QADAM: Google Play Console hisobi
1. [play.google.com/console](https://play.google.com/console) saytiga kiring.
2. Google ishlab chiquvchi hisobini oching (bir martalik $25 to'lov).

---

## 2-QADAM: Yangi ilova yaratish (Create App)
1. **App name (Ilova nomi):** `Child Security — Bolalar Xavfsizligi va GPS Nazorati`
2. **Default language (Standart til):** Uzbek (`uz`) yoki English (`en-US`).
3. **App or game:** `App`
4. **Free or paid:** `Free` (Bepul)
5. Shartlarga rozilik bildirib, **Create app** tugmasini bosing.

---

## 3-QADAM: Ilova ma'lumotlari (Store Listing)
1. **Short description (Qisqa tavsif):**
   > Farzandingiz xavfsizligi, 24/7 jonli GPS lokatsiyasi va xavfsiz makonlar nazorati.
2. **Full description (To'liq tavsif):**
   > Child Security — ota-onalar uchun farzandlarining xavfsizligini real vaqt rejimida kuzatib borish imkonini beruvchi aqlli monitoring tizimi.
   > 
   > Asosiy imkoniyatlar:
   > • 24/7 Foniy GPS kuzatuv — telefon ekrani o'chiq bo'lsa ham ishlaydi.
   > • Geozonalar (Maktab, Uy) — bola belgilangan manzilga kelganda yoki ketganda darhol xabar keladi.
   > • SOS Favqulodda signal — xavf paytida bir bosishda ota-onaga manzil va qo'ng'iroq ketadi.
   > • Batareya va ovoz holati — quvvat kam qolganda ogohlantirish.
   > • Rag'batlantirish tizimi — topshiriqlar uchun virtual tangalar va orzular sandig'i.
3. **Grafika:**
   * **App icon:** `android/store_assets/icon_512.png` faylini yuklang.
   * **Feature graphic:** `android/store_assets/feature_graphic_1024x500.png` faylini yuklang.
   * **Phone screenshots:** Telefoningizda olingan 2-4 ta skrinshotni yuklang.

---

## 4-QADAM: Maxfiylik Siyosati (Privacy Policy)
* **Policy URL** qatoriga quyidagi havolani kiriting:  
  👉 `https://child-security-bot.vercel.app/privacy.html`

---

## 5-QADAM: Fondagi Joylashuv deklaratsiyasi (Background Location)
Google Play bolalar ilovalarida fondagi GPS ga qat'iy talab qo'yadi. So'rovnomada quyidagicha javob bering:
1. **Nega fonda joylashuv kerak?**
   > "Child Security is a parental control and child safety app. Continuous background location is essential to automatically alert parents when their child enters/leaves safe zones (geofencing) and during emergency SOS events even when the phone screen is off."
2. **Foydalanuvchiga ogohlantirish ko'rsatiladimi?**  
   Ha (Yes), ilovada Google talabiga binoan Prominent Disclosure oynasi o'rnatilgan.

---

## 6-QADAM: App Bundle (.aab) yuklash va nashr qilish
1. Chap menyudan **Production** (yoki **Internal testing**) bo'limiga o'ting.
2. **Create new release** tugmasini bosing.
3. **`app-release.aab`** faylini yuklang.
4. **Review release** va **Start rollout to Production** tugmasini bosing.
5. Google Play tekshiruvi 1-3 kun davom etadi va ilovangiz Play Marketda barcha uchun ochiq bo'ladi!
