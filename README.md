# 🛡 Smart Parental Control & Child Location Tracker (O'zbekiston)

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.x-informational.svg)](https://docs.aiogram.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green.svg)](https://fastapi.tiangolo.com/)
[![SQLAlchemy 2.0](https://img.shields.io/badge/SQLAlchemy-2.0%20Async-red.svg)](https://www.sqlalchemy.org/)
[![Leaflet.js](https://img.shields.io/badge/Leaflet.js-OpenStreetMap-brightgreen.svg)](https://leafletjs.com/)

O'zbekiston bozori uchun maxsus moslashtirilgan, **Telegram Bot + Telegram Mini App (WebApp)** orqali farzandlar joylashuvi va xavfsizligini real vaqt rejimida monitoring qilish tizimi.

---

## 🌟 Asosiy Imkoniyatlar (Core Features)

1. **Rollar bo'yicha onbording (`/start`):**
   - **👨‍👩‍👧 Ota-ona (Parent):** Boshqaruv paneli, 6 xonali noyob ulanish kodi va to'g'ridan-to'g'ri ulashish havolasi (`https://t.me/<bot>?start=pair_123456`).
   - **👦 Farzand (Child):** Kod orqali ota-onaga tezkor ulanish, maxsus himoya klaviaturasi.

2. **Real-vaqt Joylashuv va Telemetriya (Real-Time GPS Tracking):**
   - **Telegram Native Live Location (`F.location`):** Telegram orqali jonli lokatsiya yuborilganda har bir nuqta avtomatik bazada saqlanadi.
   - **REST API Telemetriya (`POST /api/v1/telemetry`):** Farzand telefoni uchun engil fon ilovasi yoki skript orqali GPS, batareya darajasi, quvvatlash holati va ovoz rejimini qabul qiladi.
   - **🪫 Batareya Nazorati:** Farzand batareyasi 15% dan tushganda, ota-onaga Telegram orqali ogohlantirish yuboriladi.

3. **Xavfsiz Hududlar (Geofencing & Safe Zones):**
   - Ota-ona maktab, uy yoki o'quv markazlarini nomi va radiusi (100m – 2000m) bilan belgilaydi.
   - **Haversine matematik formulasi** yordamida farzand hududga kirganda (`ENTERED`) yoki chiqqanda (`EXITED`) ota-onaga lahzalik xabar keladi:
     > `✅ Farzandingiz soat 08:15 da 'Maktab №45' hududiga kirdi.`
     > `⚠️ Farzandingiz soat 13:40 da 'Maktab №45' hududidan chiqdi!`

4. **🚨 SOS Xavf Tugmasi (Emergency Alert):**
   - Farzand klaviaturasida doimiy **"🚨 SOS (Xavf tugmasi)"**.
   - Bosilganda:
     - Ota-onaga favqulodda xabar, oxirgi aniq koordinata, batareya foizi va Google Maps havolasi boradi.
     - Telegram native lokatsiya pini yuboriladi.
     - Farzanddan zudlik bilan ovozli xabar (golos) yoki rasm qabul qilinib, darhol ota-onaga uzatiladi.

5. **✅ Yetib Keldim (Check-in):**
   - Farzand bir tugma orqali manzilga eson-omon yetib borganini tasdiqlaydi.

6. **🎯 Vazifalar va Gamifikatsiya (Tasks & Rewards):**
   - Ota-ona farzandga vazifalar (masalan: *"Dars qilish"*, *"Xonani yig'ish"*) va mukofot tangalarini belgilaydi.
   - Farzand vazifani "Bajarildi" deb yuboradi -> Ota-ona tasdiqlaydi -> Farzandning virtual balansi oshadi.

7. **🗺 Telegram Mini App (WebApp Dashboard):**
   - **"🗺 Xaritada ko'rish"** tugmasi orqali ochiluvchi zamonaviy interaktiv xarita (Leaflet.js + TailwindCSS).
   - Farzandning jonli radar pini (pulsing marker), bugungi harakat yo'li (polyline), xavfsiz hudud doiralari (circles), batareya va ovoz profili ko'rinadi.

---

## 🏗 Arxitektura va Papkalar Strukturasi

```
smart_parental_tracker/
├── bot/                       # aiogram 3.x moduli
│   ├── __init__.py
│   ├── geofence.py            # Haversine formulasi va hudud hodisalari
│   ├── handlers.py            # /start, SOS, lokatsiya, vazifalar handlerlari
│   └── keyboards.py           # O'zbek tilidagi Reply va Inline klaviaturalar
├── api/                       # FastAPI telemetriya va WebApp API
│   ├── __init__.py
│   ├── telemetry.py           # POST /api/v1/telemetry (fon GPS va batareya)
│   └── webapp_api.py          # GET/POST /api/v1/webapp/* (Mini App ma'lumotlari)
├── db/                        # SQLAlchemy 2.0 Async ma'lumotlar bazasi
│   ├── __init__.py
│   ├── database.py            # aiosqlite / asyncpg sessiyalari
│   ├── models.py              # User, ParentChildLink, LocationLog, Geofence, Task
│   └── crud.py                # Asinxron CRUD operatsiyalari
├── webapp/                    # Telegram Mini App frontend (HTML5/Tailwind/Leaflet)
│   ├── index.html             # Asosiy Mini App sahifasi
│   ├── app.js                 # Xarita boshqaruvi, polling va WebApp SDK
│   └── style.css              # Radar animatsiyasi va maxsus stillar
├── config.py                  # Pydantic-Settings konfiguratsiyasi
├── main.py                    # FastAPI + aiogram'ni birgalikda ishga tushirish
├── test_telemetry.py          # To'liq simulyatsiya va test ssenariysi
├── requirements.txt           # Loyiha kutubxonalari
├── .env.example               # Muhit o'zgaruvchilari namunasi
└── .env                       # Lokal konfiguratsiya fayli
```

---

## 🚀 O'rnatish va Ishga Tushirish

### 1. Talablar:
- Python 3.11+
- Telegram akkaunt va Bot token ([@BotFather](https://t.me/BotFather) orqali olinadi)

### 2. Loyiha fayllarini tayyorlash:
```bash
git clone <repo_url>
cd smart_parental_tracker
```

### 3. Kutubxonalarni o'rnatish:
```bash
pip install -r requirements.txt
```

### 4. `.env` faylini sozlash:
`.env` faylini oching va bot tokeningizni kiriting:
```ini
BOT_TOKEN=123456789:ABCDefGhIJKlmNoPQRsTUVwxyZ_1234567
WEBAPP_BASE_URL=https://your-domain-or-ngrok.ngrok-free.app
API_HOST=0.0.0.0
API_PORT=8000
DATABASE_URL=sqlite+aiosqlite:///parental_tracker.db
TIMEZONE=Asia/Tashkent
DEBUG=True
```

> **Eslatma:** Telegram Mini App HTTPS talab qiladi. Lokal test qilish uchun [ngrok](https://ngrok.com/) dan foydalanishingiz mumkin:
> ```bash
> ngrok http 8000
> ```
> Olingan `https://...` manzilini `.env` dagi `WEBAPP_BASE_URL` ga yozing.

### 5. Bot va WebApp serverini ishga tushirish:
```bash
python main.py
```
Server ishga tushadi:
- **FastAPI WebApp:** `http://localhost:8000/webapp/index.html`
- **Swagger API Docs:** `http://localhost:8000/docs`
- **aiogram 3.x Bot:** Telegram'da so'rovlarni qabul qilishni boshlaydi.

---

## 🧪 Avtomatlashtirilgan Test va Simulyatsiya

Loyiha tayyorligini bir zumda tekshirish uchun test skriptini ishga tushiring:
```bash
python test_telemetry.py
```
Ushbu skript:
1. Sinov uchun Ota-ona (ID: 1001) va Farzand (ID: 2002) yaratadi.
2. Toshkent shahrida 3 ta xavfsiz hudud ("Uyimiz", "Maktab №45", "O'quv Markazi") va vazifalar qo'shadi.
3. 5 ta GPS koordinata nuqtasini API'ga jo'natadi:
   - Farzand uydan chiqadi -> `exited` geofence hodisasi!
   - Maktabga kiradi -> `entered` geofence hodisasi!
   - Batareya 14% ga tushadi -> `🪫 LOW BATTERY ALERT` ogohlantirishi!
4. Mini App API'larining barchasini tekshiradi.

---

## 📱 Botdan Foydalanish Ssenariysi

1. **Ota-ona:**
   - Botga kirib `/start` bosadi va **"👨‍👩‍👧 Ota-ona"** rolini tanlaydi.
   - Bot 6 xonali ulanish kodini beradi (masalan: `849201`).
   - "Farzandga yuborish" tugmasi orqali havolani farzandiga tashlaydi.
2. **Farzand:**
   - Havolani ochadi yoki botda **"👦 Farzand"** rolini tanlab, 6 xonali kodni kiritadi.
   - Farzandda **"🚨 SOS"**, **"📍 Joylashuvni yuborish"**, **"✅ Yetib keldim"** tugmalari paydo bo'ladi.
3. **Kuzatuv:**
   - Ota-ona bot menyusidagi **"🗺 Xaritada ko'rish (Web App)"** tugmasini bosib, to'liq interaktiv xarita orqali farzandining harakatini jonli kuzatadi.

---

## 🗄 Ma'lumotlar bazasini PostgreSQL'ga o'tkazish
Loyiha SQLAlchemy 2.0 asinxron modeli asosida qurilgan. PostgreSQL'ga o'tish uchun faqat `.env` dagi manzilni o'zgartirish kifoya:
```ini
# SQLite o'rniga:
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/parental_db
```
Qo'shimcha drayver o'rnatiladi: `pip install asyncpg`.

---

## 🛡 Xavfsizlik va Maxfiylik
- Ma'lumotlar faqat o'zaro bog'langan ota-ona va farzand o'rtasida shifrlangan holda uzatiladi.
- Begona foydalanuvchilar farzand koordinatalari yoki xavfsiz hududlarini ko'ra olmaydi.
- Tashqi fon telemetriyasi faqat ro'yxatdan o'tgan bolalar uchun qabul qilinadi.
