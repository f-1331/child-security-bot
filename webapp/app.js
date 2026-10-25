// Universal API Base Resolver (Supports Web, Telegram Mini App, and Native Android APK)
const REMOTE_API_HOST = "https://child-security-site.onrender.com";
const _nativeFetch = window.fetch;
window.fetch = function (resource, init) {
  if (typeof resource === "string" && resource.startsWith("/api/")) {
    const isLocalOrFile = window.location.protocol === "file:" || !window.location.host || window.location.host.includes("localhost");
    const base = isLocalOrFile ? REMOTE_API_HOST : "";
    resource = base + resource;
  }
  return _nativeFetch.call(this, resource, init);
};

// Smart Parental Control - Mini App Client Logic (Enhanced Ideal Edition)

let tg = window.Telegram?.WebApp;
if (tg) {
  try {
    tg.ready();
    tg.expand();
    if (typeof tg.disableClosingConfirmation === "function") {
      tg.disableClosingConfirmation();
    }
    if (typeof tg.disableVerticalSwipes === "function") {
      tg.disableVerticalSwipes();
    }
    // Ensure no closing confirmation prompt ever warns about unsaved changes
    if (typeof tg.disableClosingConfirmation === "function") {
      tg.disableClosingConfirmation();
    }
    if (tg.setHeaderColor) tg.setHeaderColor("#090d16");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#090d16");
    if (typeof tg.onEvent === "function") {
      tg.onEvent("viewportChanged", () => {
        if (map) map.invalidateSize();
      });
    }
  } catch (e) {
    console.warn("Telegram WebApp initialization error:", e);
  }
}

// Global application state
let parentId = null;
let currentChildId = null;
let childrenList = [];
let isDemoMode = false;
let currentRole = "parent"; // "parent" or "child"
let authenticatedSession = null;
let coinRateCoins = 1000;
let coinRateSom = 10000;
let childBalanceCoins = 0;
let childBalanceSom = 0;
let parentPhone = null;
let parentName = null;
let parentUsername = null;
let lastSosAlertState = false;

// High-accuracy device GPS acquisition with cache suppression to avoid repeated prompts
let cachedDeviceLocation = null;
let lastLocationFetchTime = 0;

async function getDeviceLocation() {
  // If in demo mode, do not trigger native GPS permission modal
  if (isDemoMode) {
    return { latitude: 41.2916, longitude: 69.2121, accuracy: 10 };
  }

  // Use cached location if available (< 5 minutes) to avoid repeated browser prompts
  if (cachedDeviceLocation && (Date.now() - lastLocationFetchTime < 300000)) {
    return cachedDeviceLocation;
  }
  if (lastSentLat && lastSentLng) {
    return { latitude: lastSentLat, longitude: lastSentLng, accuracy: 20 };
  }

  // Check Telegram LocationManager first (permanent permission)
  const tg = window.Telegram?.WebApp;
  if (tg && tg.LocationManager && tg.LocationManager.isAccessGranted) {
    return new Promise((resolve) => {
      try {
        tg.LocationManager.getLocation((loc) => {
          if (loc && loc.latitude && loc.longitude) {
            cachedDeviceLocation = {
              latitude: loc.latitude,
              longitude: loc.longitude,
              accuracy: loc.accuracy || 10
            };
            lastLocationFetchTime = Date.now();
            resolve(cachedDeviceLocation);
          } else {
            resolve(cachedDeviceLocation || null);
          }
        });
      } catch (e) {
        resolve(cachedDeviceLocation || null);
      }
    });
  }

  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(cachedDeviceLocation || null);
      return;
    }
    const timer = setTimeout(() => {
      resolve(cachedDeviceLocation || null);
    }, 3000);

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        clearTimeout(timer);
        cachedDeviceLocation = {
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        };
        lastLocationFetchTime = Date.now();
        resolve(cachedDeviceLocation);
      },
      (err) => {
        clearTimeout(timer);
        console.warn("Geolocation query note:", err);
        resolve(cachedDeviceLocation || null);
      },
      {
        enableHighAccuracy: true,
        timeout: 3000,
        maximumAge: 300000,
      }
    );
  });
}

// Device battery status acquisition (Bridge-aware + Web Battery API fallback)
async function getDeviceBattery() {
  try {
    if (window.AndroidBridge && typeof window.AndroidBridge.getBatteryLevel === "function") {
      const lvl = window.AndroidBridge.getBatteryLevel();
      if (typeof lvl === "number" && lvl >= 0) {
        return Math.round(lvl);
      }
    }
  } catch (e) {}

  try {
    if (typeof navigator !== "undefined" && navigator.getBattery) {
      const b = await navigator.getBattery();
      if (b && typeof b.level === "number") {
        return Math.round(b.level * 100);
      }
    }
  } catch (e) {}

  return null;
}

// Device charging status acquisition
async function getDeviceChargingStatus() {
  try {
    if (window.AndroidBridge && typeof window.AndroidBridge.isCharging === "function") {
      return Boolean(window.AndroidBridge.isCharging());
    }
  } catch (e) {}

  try {
    if (typeof navigator !== "undefined" && navigator.getBattery) {
      const b = await navigator.getBattery();
      if (b && typeof b.charging === "boolean") {
        return b.charging;
      }
    }
  } catch (e) {}

  return false;
}

// Map & Layers
let map = null;
let streetTileLayer = null;
let satelliteTileLayer = null;
let satelliteLabelsLayer = null;
let isSatellite = false;

let childMarker = null;
let geofenceLayers = [];
let routePolyline = null;
let routeLayerGroup = null;
let routePointsCache = [];
let lastRouteFetchTime = 0;
let previewCircle = null;
let zonePickerMarker = null;
let isZonePickerActive = false;
let replayMarker = null;
let isRouteVisible = true;
let isReplaying = false;
let replayTimer = null;
let pollInterval = null;

// Address cache to avoid spamming Nominatim
const addressCache = new Map();

// Default initial coordinates (Tashkent, Uzbekistan)
const DEFAULT_LAT = 41.2995;
const DEFAULT_LON = 69.2401;

// Clean up any injected Netlify badges, drawers, HUD iframes or watermarks
function removeNetlifyBadge() {
  try {
    var ttl = 7 * 24 * 60 * 60 * 1000;
    var now = Date.now();
    localStorage.setItem("nl-hud:public:v1", "hidden:" + (now + ttl));
    localStorage.setItem("nl-hud:owner-private:v1", "hidden");
  } catch(e) {}

  try {
    window.postMessage({ nlHud: "state", value: "hidden" }, "*");
  } catch(e) {}

  const selectors = [
    '#nl-badge-frame',
    '#nl-hud-frame',
    'iframe[id^="nl-"]',
    'iframe[title*="Netlify"]',
    'iframe[title*="Powered by Netlify"]',
    'iframe[srcdoc*="Netlify"]',
    'iframe[srcdoc*="nl-"]',
    'iframe[style*="2147483645"]',
    'netlify-drawer',
    '#netlify-drawer',
    'div#netlify-drawer',
    '.netlify-drawer',
    '.netlify-badge',
    '#netlify-badge',
    'a[href*="netlify"]',
    'iframe[src*="netlify"]',
    '[class*="netlify"]',
    '[id*="netlify"]',
    '[data-netlify-deploy-id]',
    '[data-netlify-site-id]',
    'script[src*="hud"]',
    '.leaflet-control-attribution'
  ];
  selectors.forEach((sel) => {
    document.querySelectorAll(sel).forEach((el) => {
      try { el.remove(); } catch (e) {}
    });
  });

  document.querySelectorAll("body *").forEach((el) => {
    if (el.tagName !== "BODY" && el.tagName !== "MAIN" && el.tagName !== "HEADER" && el.tagName !== "NAV") {
      const txt = (el.innerText || el.textContent || "").toLowerCase().trim();
      if (txt.includes("powered by netlify") || (txt.includes("netlify") && el.tagName === "A")) {
        if (el.tagName === "A" || el.offsetWidth < 320) {
          try { el.remove(); } catch (e) {}
        }
      }
    }
  });
}

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", () => {
  removeNetlifyBadge();
  for (let i = 1; i <= 10; i++) {
    setTimeout(removeNetlifyBadge, i * 300);
  }
  try {
    const observer = new MutationObserver(() => removeNetlifyBadge());
    observer.observe(document.body, { childList: true, subtree: true });
  } catch (e) {}

  if (window.lucide) {
    lucide.createIcons();
  }

  const savedTheme = localStorage.getItem("smart_tracker_theme") || "midnight";
  setGlobalTheme(savedTheme, false);

  const savedLang = localStorage.getItem("smart_tracker_lang") || "uz_lat";
  setGlobalLanguage(savedLang, false);

  initMap();
  checkAuthenticationState();

  // Polling every 2 seconds for high-frequency live telemetry and route tracking
  pollInterval = setInterval(() => {
    if (currentChildId && !isDemoMode) {
      updateLiveTelemetry(currentChildId);
      if (isRouteVisible && (Date.now() - lastRouteFetchTime > 3000)) {
        loadRouteHistory(currentChildId, true);
      }
    }
  }, 2000);

  // Responsive map size adjustment on screen resize & device orientation change
  window.addEventListener("resize", () => {
    if (map) map.invalidateSize();
  });
  window.addEventListener("orientationchange", () => {
    if (map) setTimeout(() => map.invalidateSize(), 200);
  });

  // Mobile phone ergonomics: backdrop dismiss & Telegram native BackButton
  initBackdropDismissListeners();
  setupTelegramBackButton();
});


// Enterprise XSS Defense: Global HTML entity escaper for safe DOM rendering
function escapeHtml(unsafe) {
  if (unsafe === null || unsafe === undefined) return "";
  return String(unsafe)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Trigger haptic feedback when Telegram WebApp is available
function haptic(type = "light") {
  if (tg && tg.HapticFeedback) {
    try {
      if (type === "medium") tg.HapticFeedback.impactOccurred("medium");
      else if (type === "success") tg.HapticFeedback.notificationOccurred("success");
      else if (type === "warning") tg.HapticFeedback.notificationOccurred("warning");
      else if (type === "heavy") tg.HapticFeedback.impactOccurred("heavy");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (e) {}
  }
}

// Web Audio API Sound Synthesis Engine (Zero external dependencies)
let audioCtx = null;
function getAudioContext() {
  try {
    if (!audioCtx) {
      const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
      if (AudioCtxClass) audioCtx = new AudioCtxClass();
    }
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume();
    }
  } catch (e) {}
  return audioCtx;
}

function playSirenSound() {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sawtooth";
    const now = ctx.currentTime;
    osc.frequency.setValueAtTime(800, now);
    osc.frequency.linearRampToValueAtTime(1400, now + 0.25);
    osc.frequency.linearRampToValueAtTime(800, now + 0.5);
    osc.frequency.linearRampToValueAtTime(1400, now + 0.75);
    osc.frequency.linearRampToValueAtTime(800, now + 1.0);
    gain.gain.setValueAtTime(0.25, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 1.1);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 1.1);
  } catch (e) {}
}

function playCoinChime() {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    [1318.51, 1975.53].forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, now + idx * 0.1);
      gain.gain.setValueAtTime(0.2, now + idx * 0.1);
      gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.1 + 0.35);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + idx * 0.1);
      osc.stop(now + idx * 0.1 + 0.35);
    });
  } catch (e) {}
}

function playSuccessChime() {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    [523.25, 659.25, 783.99, 1046.50].forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(freq, now + idx * 0.08);
      gain.gain.setValueAtTime(0.18, now + idx * 0.08);
      gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.08 + 0.25);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + idx * 0.08);
      osc.stop(now + idx * 0.08 + 0.25);
    });
  } catch (e) {}
}

function playPingSound() {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(987.77, now);
    gain.gain.setValueAtTime(0.2, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.3);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.3);
  } catch (e) {}
}

// Persistent Auth Prompt Controls (User requested: max 2 prompts, never prompt again unless switching roles)
const AUTH_PROMPT_KEY = "smart_tracker_auth_prompt_count";
const MAX_AUTO_AUTH_PROMPTS = 2;

function getAuthPromptCount() {
  return parseInt(localStorage.getItem(AUTH_PROMPT_KEY) || "0", 10);
}

function incrementAuthPromptCount() {
  const current = getAuthPromptCount();
  localStorage.setItem(AUTH_PROMPT_KEY, String(current + 1));
}

// Check session in localStorage, URL, or persistent role
async function checkAuthenticationState() {
  const urlParams = new URLSearchParams(window.location.search);
  const qRole = urlParams.get("role");
  if (qRole === "child") {
    localStorage.setItem("smart_tracker_role", "child");
    currentRole = "child";
  } else if (qRole === "parent") {
    localStorage.setItem("smart_tracker_role", "parent");
    currentRole = "parent";
  }

  const qCode = urlParams.get("code");

  // 1. Direct code in query param: ?code=123456
  if (qCode) {
    const input = document.getElementById("inputAuthCode");
    if (input) input.value = qCode;
    await submitAuthCode();
    return;
  }

  // 2. Telegram WebApp cryptographic auto-authentication (Primary & foolproof inside Telegram)
  const initData = window.Telegram?.WebApp?.initData;
  if (initData && initData.length > 10) {
    try {
      const tgRes = await fetch("/api/v1/webapp/auth/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: initData })
      });
      if (tgRes.ok) {
        const tgData = await tgRes.json();
        if (tgData && tgData.status === "success") {
          localStorage.setItem("smart_tracker_session", JSON.stringify(tgData));
          if (tgData.children && tgData.children.length > 0) {
            localStorage.setItem("smart_tracker_cached_children", JSON.stringify(tgData.children));
          }
          localStorage.setItem("smart_tracker_auth_code", tgData.user?.web_access_code || "");
          localStorage.setItem("smart_tracker_role", tgData.role);
          localStorage.setItem(AUTH_PROMPT_KEY, "2");
          hideAuthGateModal();
          applyAuthenticatedUser(tgData);
          return;
        }
      }
    } catch (e) {
      console.warn("Telegram cryptographic auth note (server deploying or offline):", e);
    }
  }

  // 3. Check localStorage session (User previously authenticated or offline fallback)
  const stored = localStorage.getItem("smart_tracker_session");
  const storedCode = localStorage.getItem("smart_tracker_auth_code");
  if (stored) {
    try {
      const data = JSON.parse(stored);
      if (data && data.user && data.role) {
        // Restore cached children if session children list was somehow empty
        if (!data.children || data.children.length === 0) {
          const cachedChildrenRaw = localStorage.getItem("smart_tracker_cached_children");
          if (cachedChildrenRaw) {
            try {
              const cachedCh = JSON.parse(cachedChildrenRaw);
              if (Array.isArray(cachedCh) && cachedCh.length > 0) {
                data.children = cachedCh;
              }
            } catch (e) {}
          }
        }

        hideAuthGateModal();
        applyAuthenticatedUser(data);

        // Silent background refresh to keep children and balance up to date
        // NEVER wipe localStorage on temporary 404/502/redeploy errors!
        if (storedCode && storedCode.trim().length > 0) {
          fetch("/api/v1/webapp/auth/code", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: storedCode.trim() })
          })
            .then(async (r) => {
              if (r.ok) {
                return r.json();
              }
              // If server is deploying or returning 404/502, retain session!
              console.warn("Background auth refresh status:", r.status, "- retaining local session.");
              return null;
            })
            .then((fresh) => {
              if (fresh && fresh.status === "success") {
                localStorage.setItem("smart_tracker_session", JSON.stringify(fresh));
                if (fresh.children && fresh.children.length > 0) {
                  localStorage.setItem("smart_tracker_cached_children", JSON.stringify(fresh.children));
                }
                applyAuthenticatedUser(fresh);
              }
            })
            .catch((e) => {
              console.warn("Background auth refresh network error, keeping session:", e);
            });
        }
        return;
      }
    } catch (e) {
      console.warn("Session parse note:", e);
    }
  }

  // 4. Check prompt count: Prompt code AT MOST 2 TIMES!
  // After 2 times, NEVER ask automatically on entry or reload again.
  const promptCount = getAuthPromptCount();
  const savedRole = localStorage.getItem("smart_tracker_role") || "parent";

  if (promptCount < MAX_AUTO_AUTH_PROMPTS) {
    incrementAuthPromptCount();
    if (savedRole === "child") {
      enableChildDemoMode();
    } else {
      enableDemoMode();
    }
    showAuthGateModal();
  } else {
    // Already asked 2 times -> never bother the user on entry again!
    hideAuthGateModal();
    if (savedRole === "child") {
      enableChildDemoMode();
    } else {
      enableDemoMode();
    }
  }
}

// =========================================================
// Saytdan kod olish & Telegram botdan kod olish taklifi
// =========================================================
window._selectedCodeGenRole = "parent";
window._lastGeneratedWebCode = "";

function selectCodeGenRole(role) {
  window._selectedCodeGenRole = role === "child" ? "child" : "parent";
  const btnParent = document.getElementById("btnRoleParent");
  const btnChild = document.getElementById("btnRoleChild");

  if (window._selectedCodeGenRole === "child") {
    if (btnChild) {
      btnChild.className = "py-2 px-2.5 rounded-xl border border-emerald-500 bg-emerald-600/30 text-white text-xs font-bold transition flex items-center justify-center gap-1.5";
    }
    if (btnParent) {
      btnParent.className = "py-2 px-2.5 rounded-xl border border-slate-700 bg-slate-900 text-slate-300 text-xs font-semibold transition flex items-center justify-center gap-1.5";
    }
  } else {
    if (btnParent) {
      btnParent.className = "py-2 px-2.5 rounded-xl border border-blue-500 bg-blue-600/30 text-white text-xs font-bold transition flex items-center justify-center gap-1.5";
    }
    if (btnChild) {
      btnChild.className = "py-2 px-2.5 rounded-xl border border-slate-700 bg-slate-900 text-slate-300 text-xs font-semibold transition flex items-center justify-center gap-1.5";
    }
  }
}

function toggleWebCodeGenBox() {
  const box = document.getElementById("webCodeGenBox");
  const icon = document.getElementById("toggleCodeGenIcon");
  const text = document.getElementById("toggleCodeGenText");
  if (!box) return;

  const isHidden = box.classList.contains("hidden");
  if (isHidden) {
    box.classList.remove("hidden");
    if (icon) icon.classList.add("rotate-180");
    if (text) text.textContent = "Yashirish";
    if (window._pendingTargetRole) {
      selectCodeGenRole(window._pendingTargetRole);
    }
  } else {
    box.classList.add("hidden");
    if (icon) icon.classList.remove("rotate-180");
    if (text) text.textContent = "Kodni olish";
  }
  if (window.lucide) lucide.createIcons();
}

async function generateWebCodeAction() {
  const btn = document.getElementById("btnGenerateWebCode");
  const nameInput = document.getElementById("inputWebGenName");
  const resultBox = document.getElementById("webCodeGenResult");
  const codeDisplay = document.getElementById("generatedCodeDisplay");

  const role = window._selectedCodeGenRole || "parent";
  const fullName = nameInput ? nameInput.value.trim() : "";

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-pulse">Yangi kod yaratilmoqda...</span>`;
  }

  try {
    const res = await fetch("/api/v1/webapp/auth/generate-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: role, full_name: fullName })
    });

    const data = await res.json();
    if (!res.ok || data.status !== "success") {
      throw new Error(data.detail || "Kod yaratishda xatolik yuz berdi.");
    }

    window._lastGeneratedWebCode = data.code;
    if (codeDisplay) codeDisplay.textContent = data.code;
    if (resultBox) resultBox.classList.remove("hidden");

    // Automatically fill the code input so 1-click login works smoothly
    const inputAuth = document.getElementById("inputAuthCode");
    if (inputAuth) inputAuth.value = data.code;

    haptic("success");
    showToast(`🎉 Yangi kod yaratildi: ${data.code}`);
  } catch (err) {
    console.error("Web code generation error:", err);
    haptic("warning");
    showToast(`⚠️ ${err.message || "Xatolik yuz berdi"}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<i data-lucide="key-round" class="w-3.5 h-3.5"></i><span>Saytda yangi kod yaratish</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

function copyGeneratedCode() {
  const code = window._lastGeneratedWebCode;
  if (!code) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(code).then(() => {
      haptic("light");
      showToast("Kod nusxalandi! 📋");
    }).catch(() => {
      prompt("Kodingiz:", code);
    });
  } else {
    prompt("Kodingiz:", code);
  }
}

function applyGeneratedCodeAndLogin() {
  const code = window._lastGeneratedWebCode;
  if (!code) return;
  const inputAuth = document.getElementById("inputAuthCode");
  if (inputAuth) inputAuth.value = code;
  submitAuthCode();
}

function showAuthGateModal(targetRole = null) {
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("authGateModal");
  if (!modal) return;

  modal.classList.remove("hidden");

  // Close button is ALWAYS visible so user is never locked out
  const closeBtn = document.getElementById("btnCloseAuthGate");
  if (closeBtn) closeBtn.classList.remove("hidden");

  const input = document.getElementById("inputAuthCode");
  const modalTitle = document.getElementById("authModalTitle");
  const modalSub = document.getElementById("authModalSub");

  if (targetRole) {
    window._pendingTargetRole = targetRole;
    selectCodeGenRole(targetRole);
    if (modalTitle) {
      modalTitle.textContent = targetRole === "child" ? "👦 Farzand hisobiga o'tish" : "👨‍👩‍👧 Ota-ona hisobiga o'tish";
    }
    if (modalSub) {
      modalSub.textContent = targetRole === "child"
        ? "Farzandingizning 6 xonali shaxsiy sayt kodini kiriting:"
        : "Ota-onaning 6 xonali oilaviy sayt kodini kiriting:";
    }
  } else {
    window._pendingTargetRole = null;
    selectCodeGenRole("parent");
    if (modalTitle) modalTitle.textContent = "Child Security Bot";
    if (modalSub) modalSub.textContent = "Bolalar xavfsizligi va rag'batlantirish platformasi";
  }

  if (input) {
    input.value = "";
    setTimeout(() => input.focus(), 200);
  }
  if (window.lucide) lucide.createIcons();
}

function hideAuthGateModal() {
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("authGateModal");
  if (modal) modal.classList.add("hidden");
  window._pendingTargetRole = null;

  // If not authenticated and not in demo mode, ensure active role is visible
  if (!authenticatedSession && !isDemoMode) {
    const role = localStorage.getItem("smart_tracker_role") || "parent";
    if (role === "child") {
      enableChildDemoMode();
    } else {
      enableDemoMode();
    }
  }
}

// User explicitly switches roles (e.g. from parent to child or vice versa)
function promptRoleSwitch(targetRole) {
  haptic("medium");
  if (targetRole === currentRole) {
    showToast(`Siz allaqachon ${targetRole === 'child' ? 'Farzand' : 'Ota-ona'} rejimidamisiz.`);
    return;
  }

  // Open the code prompt specifically asking for the code for the target role!
  showAuthGateModal(targetRole);
}

async function submitAuthCode() {
  const codeInput = document.getElementById("inputAuthCode");
  const code = codeInput ? codeInput.value.trim().replace(/\s+/g, "").replace(/-/g, "") : "";
  const errorText = document.getElementById("authErrorText");
  const errorMsg = document.getElementById("authErrorMsg");
  const btn = document.getElementById("btnSubmitAuthCode");

  if (!code || code.length < 4) {
    if (errorText) errorText.classList.remove("hidden");
    if (errorMsg) errorMsg.textContent = "Iltimos, 6 xonali kodni kiriting.";
    return;
  }

  if (errorText) errorText.classList.add("hidden");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-pulse">Tekshirilmoqda...</span>`;
  }

  try {
    const res = await fetch("/api/v1/webapp/auth/code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code })
    });

    if (res.status === 404 && (code === "123456" || code === "000000" || code.toLowerCase() === "demo")) {
      const target = window._pendingTargetRole || "parent";
      localStorage.setItem("smart_tracker_role", target);
      localStorage.setItem(AUTH_PROMPT_KEY, "2");
      hideAuthGateModal();
      if (target === "child") {
        enableChildDemoMode();
      } else {
        enableDemoMode();
      }
      showToast("🎮 Namuna (Demo) rejimiga kirildi!");
      return;
    }

    const data = await res.json();
    if (!res.ok || data.status !== "success") {
      throw new Error(data.detail || "Kiritilgan kod noto'g'ri yoki mavjud emas.");
    }

    // Save session & code persistently
    localStorage.setItem("smart_tracker_session", JSON.stringify(data));
    localStorage.setItem("smart_tracker_auth_code", code);
    localStorage.setItem("smart_tracker_role", data.role);
    localStorage.setItem(AUTH_PROMPT_KEY, "2"); // Never prompt automatically on reload again

    haptic("success");
    hideAuthGateModal();
    applyAuthenticatedUser(data);
    showToast("🎉 Xush kelibsiz!");
  } catch (err) {
    console.error("Auth error:", err);
    haptic("warning");
    if (errorText) errorText.classList.remove("hidden");
    if (errorMsg) errorMsg.textContent = err.message || "Kod noto'g'ri. Iltimos, qayta tekshiring.";
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<span>Saytga kirish</span><i data-lucide="arrow-right" class="w-4 h-4"></i>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

function startDemoMode(role) {
  haptic("medium");
  localStorage.setItem("smart_tracker_role", role);
  localStorage.setItem(AUTH_PROMPT_KEY, "2"); // Do not prompt on future reloads
  hideAuthGateModal();
  if (role === "child") {
    enableChildDemoMode();
  } else {
    enableDemoMode();
  }
}

function switchDemoRole(newRole) {
  haptic("medium");
  // Prompt for code when switching role as user requested!
  promptRoleSwitch(newRole);
}

function enableChildDemoMode() {
  currentRole = "child";
  isDemoMode = true;
  localStorage.setItem("smart_tracker_role", "child");
  parentId = null;
  currentChildId = 2002;
  coinRateCoins = 1000;
  coinRateSom = 10000;
  childBalanceCoins = 18000;
  childBalanceSom = 180000;

  childrenList = [
    {
      id: 2002,
      full_name: "Azizbek Karimov (Farzand)",
      username: "azizbek_child",
      virtual_balance: 18000,
      virtual_money_som: 180000,
      is_sos_active: false,
      location: { latitude: 41.2916, longitude: 69.2121, battery_level: 82, is_charging: false, sound_mode: "normal" }
    },
    {
      id: 2003,
      full_name: "Madinaxon Karimova (Farzand 2)",
      username: "madina_child",
      virtual_balance: 24000,
      virtual_money_som: 240000,
      is_sos_active: false,
      location: { latitude: 41.3111, longitude: 69.2797, battery_level: 95, is_charging: true, sound_mode: "normal" }
    }
  ];

  applyChildUI({
    id: 2002,
    full_name: "Azizbek Karimov (Farzand Demo)",
    virtual_balance: 18000,
    virtual_money_som: 180000,
    coin_rate_coins: 1000,
    coin_rate_som: 10000,
    parent_phone: "+998901234567",
    parent_name: "Ota-ona (Demo)"
  });

  document.getElementById("emptyStateBanner").classList.add("hidden");
  document.getElementById("demoBadge")?.classList.remove("hidden");
  document.getElementById("btnTopDemoAuth")?.classList.remove("hidden");
  document.getElementById("demoNoticeBanner")?.classList.remove("hidden");
  if (window.lucide) lucide.createIcons();

  // Render demo child live position
  const demoLat = 41.2916;
  const demoLon = 69.2121;
  renderChildMarker(demoLat, demoLon, "Azizbek Karimov (Demo)", false, 85);
  updateBatteryUI(85, false);
  updateSoundUI("normal");
  resolveAddress(demoLat, demoLon);
  renderDemoGeofences();
  renderDemoHistory();
  renderDemoTasks();
  // In demo mode do not request real device camera/GPS permissions
  showToast("👦 Farzand demo rejimiga o'tildi");
}

function applyAuthenticatedUser(data) {
  authenticatedSession = data;
  isDemoMode = false;
  currentRole = data.role;
  sessionStorage.removeItem("smart_tracker_demo_role");
  document.getElementById("demoBadge")?.classList.add("hidden");
  document.getElementById("btnTopDemoAuth")?.classList.add("hidden");
  document.getElementById("demoNoticeBanner")?.classList.add("hidden");

  // Purge any demo geofences on map and state when real user connects
  if (geofenceLayers && map) {
    geofenceLayers.forEach((layer) => {
      try { map.removeLayer(layer); } catch (e) {}
    });
  }
  geofenceLayers = [];
  geofencesList = [];
  renderChildDashboardCastles();
  const initZoneStatus = document.getElementById("zoneStatusText");
  if (initZoneStatus) initZoneStatus.textContent = "Kerakli joylar belgilanmagan";

  if (data.role === "parent") {
    parentId = data.user.id;
    coinRateCoins = data.user.coin_rate_coins || 1000;
    coinRateSom = data.user.coin_rate_som || 10000;
    updateCoinRateBadgeUI(coinRateCoins, coinRateSom);
    applyParentUI(data);

    if (data.children && data.children.length > 0) {
      childrenList = data.children;
      currentChildId = childrenList[0].id;
      renderChildSelector();
      selectChild(currentChildId);
    } else {
      showEmptyState();
    }

    // Query fresh children list from server
    loadData();
    connectWebSocket(parentId);
  } else if (data.role === "child") {
    currentChildId = data.user.id;
    coinRateCoins = data.user.coin_rate_coins || 1000;
    coinRateSom = data.user.coin_rate_som || 10000;
    childBalanceCoins = data.user.virtual_balance || 0;
    childBalanceSom = data.user.virtual_money_som || 0;
    applyChildUI(data.user);
    loadChildSelfData(data.user);
    initChildPermissionsAndTracking(data.user.id);
  }
}

function applyParentUI(data) {
  syncWithNativeAndroidBridge();
  document.getElementById("childOnboardingModal")?.classList.add("hidden");
  currentRole = "parent";
  document.body.classList.remove("role-child");
  document.body.classList.add("role-parent");

  // Show parent view section (Map & Security HUD), hide child adventure view
  const parentSection = document.getElementById("parentViewSection");
  const childSection = document.getElementById("childViewSection");
  if (parentSection) parentSection.classList.remove("hidden");
  if (childSection) childSection.classList.add("hidden");
  document.getElementById("btnChildBackFromMap")?.classList.add("hidden");

  // Show parent header row, hide child header row
  document.getElementById("parentHeaderRow")?.classList.remove("hidden");
  document.getElementById("childHeaderRow")?.classList.add("hidden");

  document.getElementById("childWelcomeBanner")?.classList.add("hidden");
  document.getElementById("btnCoinRateCorner")?.classList.remove("hidden");
  document.getElementById("childFloatingActions")?.classList.add("hidden");
  document.getElementById("btnParentSiren")?.classList.remove("hidden");
  document.getElementById("btnAddZoneMap")?.classList.remove("hidden");
  document.getElementById("btnAddZoneSection")?.classList.remove("hidden");
  document.getElementById("parentTaskActionSection")?.classList.remove("hidden");

  const dict = I18N_STRINGS[currentLanguage] || I18N_STRINGS.uz_lat;

  // Titles
  const zonesTitle = document.getElementById("zonesModalTitle");
  if (zonesTitle) zonesTitle.textContent = dict.zones_title || "Kerakli Joylar (Geozonalar)";
  const tasksTitle = document.getElementById("tasksModalTitle");
  if (tasksTitle) tasksTitle.textContent = dict.tasks_title || "Farzandga Topshiriq Berish";

  // Tab 1: Map
  const tab1Icon = document.getElementById("tabIcon-map");
  if (tab1Icon) tab1Icon.setAttribute("data-lucide", "map");
  const tab1Label = document.getElementById("tabLabel-map");
  if (tab1Label) tab1Label.textContent = dict.tab_map || "Xarita";

  // Tab 2: Zones
  const tab2Icon = document.getElementById("tabIcon-zones");
  if (tab2Icon) tab2Icon.setAttribute("data-lucide", "map-pin");
  const tab2Label = document.getElementById("tabLabel-zones");
  if (tab2Label) tab2Label.textContent = dict.tab_zones || "Joylar";

  // Tab 3: Tasks
  const tab3Icon = document.getElementById("tabIcon-tasks");
  if (tab3Icon) tab3Icon.setAttribute("data-lucide", "check-square");
  const tab3Label = document.getElementById("tabLabel-tasks");
  if (tab3Label) tab3Label.textContent = dict.tab_tasks || "Vazifalar";

  // Tab 4 (History for Parent)
  const tab4Btn = document.getElementById("tabBtn-history");
  if (tab4Btn) {
    tab4Btn.setAttribute("onclick", "switchTab('history')");
  }
  const tab4Icon = document.getElementById("tabIcon-history");
  if (tab4Icon) {
    tab4Icon.setAttribute("data-lucide", "history");
  }
  const tab4Label = document.getElementById("tabLabel-history");
  if (tab4Label) tab4Label.textContent = dict.tab_history || "Tarix";

  // Tab 5: Settings
  const tab5Label = document.getElementById("tabLabel-settings");
  if (tab5Label) tab5Label.textContent = dict.tab_settings || "Dizayn & Til";

  // Demo Switcher Segmented Button Styling
  const pBtn = document.getElementById("btnDemoRoleParent");
  const cBtn = document.getElementById("btnDemoRoleChild");
  if (pBtn && cBtn) {
    pBtn.className = "px-2 sm:px-2.5 py-1 rounded-lg font-bold transition flex items-center gap-1 bg-slate-900 text-blue-300 shadow";
    cBtn.className = "px-2 sm:px-2.5 py-1 rounded-lg font-bold transition flex items-center gap-1 text-slate-900 hover:text-white";
  }

  const sel = document.getElementById("childSelect");
  if (sel) sel.disabled = false;

  if (map) setTimeout(() => map.invalidateSize(), 80);
  if (window.lucide) lucide.createIcons();
}

// =========================================================
// GAMIFIED CHILD LEVEL & XP SYSTEM (Dynamic, starts at Lvl 1)
// =========================================================
function calculateChildLevel(coins = 0, completedTasksCount = 0) {
  const safeCoins = Math.max(0, parseInt(coins, 10) || 0);
  const safeTasks = Math.max(0, parseInt(completedTasksCount, 10) || 0);
  // XP formula: Each coin earned gives 1 XP, each completed quest gives 100 XP
  const xp = safeCoins + (safeTasks * 100);

  let level = 1;
  let title = "Yangi Sayohatchi";
  let icon = "🌱";
  let minXp = 0;
  let maxXp = 500;

  if (xp >= 10000) {
    level = 5;
    title = "Afsonaviy Super Qahramon";
    icon = "👑";
    minXp = 10000;
    maxXp = 10000;
  } else if (xp >= 4000) {
    level = 4;
    title = "Tezkor Qalqon";
    icon = "⚡";
    minXp = 4000;
    maxXp = 10000;
  } else if (xp >= 1500) {
    level = 3;
    title = "Oltin Qalqon Qo'riqchisi";
    icon = "🛡️";
    minXp = 1500;
    maxXp = 4000;
  } else if (xp >= 500) {
    level = 2;
    title = "Bilimdon Qahramon";
    icon = "⭐";
    minXp = 500;
    maxXp = 1500;
  } else {
    level = 1;
    title = "Yangi Sayohatchi";
    icon = "🌱";
    minXp = 0;
    maxXp = 500;
  }

  const range = Math.max(1, maxXp - minXp);
  const currentInLevel = Math.max(0, xp - minXp);
  const percent = level >= 5 ? 100 : Math.min(100, Math.round((currentInLevel / range) * 100));
  const remainingXp = level >= 5 ? 0 : Math.max(0, maxXp - xp);

  return {
    xp,
    level,
    title,
    icon,
    minXp,
    maxXp,
    percent,
    remainingXp
  };
}

function updateChildLevelAndXP(coins = 0, completedTasksCount = 0) {
  const lvl = calculateChildLevel(coins, completedTasksCount);

  // 1. Top Bar Level Badge
  const heroBadge = document.getElementById("childHeroLevelBadge");
  if (heroBadge) heroBadge.textContent = `Lvl ${lvl.level}`;

  // 2. Adventure Hub Level Badge
  const dashBadge = document.getElementById("childDashboardLevelBadge");
  if (dashBadge) dashBadge.textContent = `Lvl ${lvl.level}`;

  // 3. Adventure Hub Hero Title
  const dashTitle = document.getElementById("childDashboardHeroTitle");
  if (dashTitle) {
    dashTitle.innerHTML = `<span>${lvl.icon}</span> <span>${lvl.title}</span>`;
  }

  // 4. Adventure Hub XP Progress Fill
  const dashXpFill = document.getElementById("childDashboardXpFill");
  if (dashXpFill) {
    dashXpFill.style.width = `${lvl.percent}%`;
  }

  // 5. Adventure Hub XP Remaining Text
  const dashXpText = document.getElementById("childDashboardXpText");
  if (dashXpText) {
    dashXpText.textContent = lvl.level >= 5
      ? "Maksimal daraja (Cheksiz XP)"
      : `Keyingi darajagacha ${lvl.remainingXp.toLocaleString()} XP qoldi`;
  }

  // 6. Child Wallet Modal Level Pill
  const walletPill = document.getElementById("childWalletLevelPill");
  if (walletPill) {
    walletPill.textContent = `${lvl.icon} ${lvl.level}-Daraja (${lvl.title})`;
  }
}

function applyChildUI(user) {
  syncWithNativeAndroidBridge();
  currentRole = "child";
  document.body.classList.remove("role-parent");
  document.body.classList.add("role-child");

  // Show child adventure hub view, hide parent tactical map
  const parentSection = document.getElementById("parentViewSection");
  const childSection = document.getElementById("childViewSection");
  if (parentSection) parentSection.classList.add("hidden");
  if (childSection) childSection.classList.remove("hidden");
  document.getElementById("btnChildBackFromMap")?.classList.add("hidden");

  // Show child header row, hide parent header row
  document.getElementById("parentHeaderRow")?.classList.add("hidden");
  document.getElementById("childHeaderRow")?.classList.remove("hidden");

  document.getElementById("childWelcomeBanner")?.classList.remove("hidden");
  document.getElementById("btnCoinRateCorner")?.classList.add("hidden");
  document.getElementById("childFloatingActions")?.classList.remove("hidden");
  document.getElementById("btnParentSiren")?.classList.add("hidden");
  document.getElementById("btnAddZoneMap")?.classList.add("hidden");
  document.getElementById("btnAddZoneSection")?.classList.add("hidden");
  document.getElementById("parentTaskActionSection")?.classList.add("hidden");

  const dict = I18N_STRINGS[currentLanguage] || I18N_STRINGS.uz_lat;

  // Titles
  const zonesTitle = document.getElementById("zonesModalTitle");
  if (zonesTitle) zonesTitle.textContent = dict.safe_castles || "Mening Xavfsiz Makonlarim 🛡️";
  const tasksTitle = document.getElementById("tasksModalTitle");
  if (tasksTitle) tasksTitle.textContent = dict.active_missions || "Mening Missiyalarim 🎯";

  // Tab 1: Sarguzasht
  const tab1Icon = document.getElementById("tabIcon-map");
  if (tab1Icon) tab1Icon.setAttribute("data-lucide", "compass");
  const tab1Label = document.getElementById("tabLabel-map");
  if (tab1Label) tab1Label.textContent = dict.tab_adventure || "Sarguzasht";

  // Tab 2: Makonlarim (Xavfsiz hududlar)
  const tab2Icon = document.getElementById("tabIcon-zones");
  if (tab2Icon) tab2Icon.setAttribute("data-lucide", "shield-check");
  const tab2Label = document.getElementById("tabLabel-zones");
  if (tab2Label) tab2Label.textContent = dict.tab_castles || "Makonlarim";

  // Tab 3: Missiyalar
  const tab3Icon = document.getElementById("tabIcon-tasks");
  if (tab3Icon) tab3Icon.setAttribute("data-lucide", "trophy");
  const tab3Label = document.getElementById("tabLabel-tasks");
  if (tab3Label) tab3Label.textContent = dict.tab_missions || "Missiyalar";

  // Tab 4 (Hamyonim / Xazina for Child)
  const tab4Btn = document.getElementById("tabBtn-history");
  if (tab4Btn) {
    tab4Btn.setAttribute("onclick", "switchTab('wallet')");
  }
  const tab4Icon = document.getElementById("tabIcon-history");
  if (tab4Icon) {
    tab4Icon.setAttribute("data-lucide", "wallet");
  }
  const tab4Label = document.getElementById("tabLabel-history");
  if (tab4Label) tab4Label.textContent = dict.tab_treasure || "Xazina 🐷";

  // Tab 5: Settings
  const tab5Label = document.getElementById("tabLabel-settings");
  if (tab5Label) tab5Label.textContent = dict.tab_settings || "Dizayn & Til";

  // Demo Switcher Segmented Button Styling
  const pBtn = document.getElementById("btnDemoRoleParent");
  const cBtn = document.getElementById("btnDemoRoleChild");
  if (pBtn && cBtn) {
    cBtn.className = "px-2 sm:px-2.5 py-1 rounded-lg font-bold transition flex items-center gap-1 bg-slate-900 text-emerald-300 shadow";
    pBtn.className = "px-2 sm:px-2.5 py-1 rounded-lg font-bold transition flex items-center gap-1 text-slate-900 hover:text-white";
  }

  const moneyStr = (user.virtual_money_som || 0).toLocaleString();
  const coinsStr = (user.virtual_balance || 0).toLocaleString();

  const youngHeroFallback = dict.young_hero || "Yosh Qahramon";

  // Child Hero Bar Updates
  const heroName = document.getElementById("childHeroName");
  if (heroName) heroName.textContent = user.full_name || youngHeroFallback;

  const heroMoneyText = document.getElementById("childHeroMoneyText");
  if (heroMoneyText) heroMoneyText.textContent = `${moneyStr} so'm`;

  const bannerName = document.getElementById("childBannerName");
  if (bannerName) bannerName.textContent = dict.hero_adventure || "Bugungi sarguzasht";

  const bannerMoney = document.getElementById("childBannerMoney");
  if (bannerMoney) bannerMoney.textContent = `💵 Sizda hozir ${moneyStr} so'm bor! (${coinsStr} tanga)`;

  // Child Adventure Dashboard Card updates
  const dashHeroName = document.getElementById("childDashboardHeroName");
  if (dashHeroName) dashHeroName.textContent = user.full_name || youngHeroFallback;

  // Dynamically compute and display gamified level and XP
  const completedCount = (tasksList && tasksList.length)
    ? tasksList.filter(t => t.status === "completed" || t.status === "approved").length
    : 0;
  updateChildLevelAndXP(user.virtual_balance || 0, completedCount);

  const portalMoney = document.getElementById("childPortalMoneyText");
  if (portalMoney) portalMoney.textContent = `${moneyStr} so'm`;

  const portalCoins = document.getElementById("childPortalCoinsText");
  if (portalCoins) portalCoins.innerHTML = `<span>🪙</span> <span>${coinsStr} tanga</span>`;

  const portalRate = document.getElementById("childPortalRateText");
  if (portalRate) {
    portalRate.textContent = `${(coinRateCoins/1000).toFixed(0)}k 🪙 = ${(coinRateSom/1000).toFixed(0)}k so'm`;
  }

  // Update modal-wallet piggy bank numbers
  const wMoney = document.getElementById("childWalletMoneyText");
  if (wMoney) wMoney.textContent = `${moneyStr} so'm`;
  const wCoins = document.getElementById("childWalletCoinsText");
  if (wCoins) {
    const rateText = `(Kurs: ${(coinRateCoins/1000).toFixed(0)}k = ${(coinRateSom/1000).toFixed(0)}k so'm)`;
    wCoins.innerHTML = `<i data-lucide="coins" class="w-3.5 h-3.5"></i> <span>${coinsStr} tanga ${rateText}</span>`;
  }

  const sel = document.getElementById("childSelect");
  if (sel) {
    sel.innerHTML = `<option value="${escapeHtml(String(user.id))}">${escapeHtml(user.full_name || 'Mening hisobim')}</option>`;
    sel.disabled = true;
  }

  // Populate dynamic cards on child dashboard
  renderChildDashboardQuests();
  renderChildDashboardCastles();

  if (user.parent_phone) parentPhone = user.parent_phone;
  if (user.parent_name) parentName = user.parent_name;
  if (user.is_sos_active) {
    const sosModal = document.getElementById("modal-sos-active");
    if (sosModal) sosModal.classList.remove("hidden");
  }

  if (window.lucide) lucide.createIcons();
}

async function loadChildSelfData(user) {
  childrenList = [user];
  currentChildId = user.id;
  isDemoMode = false;

  // Purge demo geofences on child screen
  if (geofenceLayers && map) {
    geofenceLayers.forEach((layer) => {
      try { map.removeLayer(layer); } catch (e) {}
    });
  }
  geofenceLayers = [];
  geofencesList = [];
  renderChildDashboardCastles();
  const childZoneStatus = document.getElementById("zoneStatusText");
  if (childZoneStatus) childZoneStatus.textContent = "Kerakli joylar belgilanmagan";

  try {
    const res = await fetch(`/api/v1/webapp/child/${user.id}/status`);
    if (res.ok) {
      const data = await res.json();
      if (data.status === "success" && data.child) {
        const ch = data.child;
        childBalanceCoins = ch.virtual_balance || 0;
        childBalanceSom = ch.virtual_money_som || 0;
        applyChildUI(ch);

        if (ch.location && ch.location.latitude && ch.location.longitude) {
          renderChildMarker(ch.location.latitude, ch.location.longitude, ch.full_name, ch.is_sos_active, ch.location.battery_level);
          updateBatteryUI(ch.location.battery_level, ch.location.is_charging);
          updateSoundUI(ch.location.sound_mode);
          resolveAddress(ch.location.latitude, ch.location.longitude);
        }
      }
    }
  } catch (e) {
    console.warn("Child self status load error:", e);
  }

  await loadGeofences(user.id);
  await loadRouteHistory(user.id);
  await loadTasks();
  await loadWishlist(user.id);
}

function logoutSession() {
  haptic("medium");
  if (confirm("Haqiqatan ham sayt hisobidan chiqmoqchimisiz?")) {
    localStorage.removeItem("smart_tracker_session");
    localStorage.removeItem("smart_tracker_auth_code");
    localStorage.removeItem("smart_tracker_role");
    localStorage.removeItem(AUTH_PROMPT_KEY);
    sessionStorage.removeItem("smart_tracker_demo_role");
    authenticatedSession = null;
    isDemoMode = false;
    currentChildId = null;
    parentId = null;
    childrenList = [];
    tasksList = [];
    geofencesList = [];
    document.getElementById("demoBadge")?.classList.add("hidden");
    document.getElementById("btnTopDemoAuth")?.classList.add("hidden");
    document.getElementById("demoNoticeBanner")?.classList.add("hidden");
    if (childWatchId !== null && navigator.geolocation) {
      navigator.geolocation.clearWatch(childWatchId);
      childWatchId = null;
    }
    if (appWebSocket) {
      try { appWebSocket.close(); } catch (e) {}
      appWebSocket = null;
    }
    clearInterval(childNotificationPollTimer);
    if (window._tgLocationPollInterval) {
      clearInterval(window._tgLocationPollInterval);
      window._tgLocationPollInterval = null;
    }
    if (window.AndroidBridge && typeof window.AndroidBridge.stopBackgroundTracking === "function") {
      try { window.AndroidBridge.stopBackgroundTracking(); } catch (e) {}
    }
    const input = document.getElementById("inputAuthCode");
    if (input) input.value = "";
    showAuthGateModal();
    showToast("🚪 Hisobdan chiqildi");
  }
}

// Coin Rate Settings Modal functions
function openCoinRateModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  const modal = document.getElementById("modal-coin-rate");
  if (!modal) return;

  const tipDismissed = localStorage.getItem("coin_rate_tip_dismissed") === "true";
  const tipBox = document.getElementById("coinRateTipBox");
  if (tipBox) {
    if (tipDismissed) tipBox.classList.add("hidden");
    else tipBox.classList.remove("hidden");
  }

  document.getElementById("inputCoinRateCoins").value = coinRateCoins || 1000;
  document.getElementById("inputCoinRateSom").value = coinRateSom || 10000;
  updateCoinRatePreview();
  modal.classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
}

function closeCoinRateModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  const modal = document.getElementById("modal-coin-rate");
  if (modal) modal.classList.add("hidden");
}

function dismissCoinRateTip() {
  localStorage.setItem("coin_rate_tip_dismissed", "true");
  const tipBox = document.getElementById("coinRateTipBox");
  if (tipBox) tipBox.classList.add("hidden");
  showToast("Tavsiya berkitildi");
}

function applyCoinRatePreset(coins, som) {
  haptic("light");
  document.getElementById("inputCoinRateCoins").value = coins;
  document.getElementById("inputCoinRateSom").value = som;
  updateCoinRatePreview();
}

function updateCoinRatePreview() {
  const coins = parseInt(document.getElementById("inputCoinRateCoins").value) || 1000;
  const som = parseInt(document.getElementById("inputCoinRateSom").value) || 10000;
  const perCoin = som / Math.max(1, coins);
  const preview = document.getElementById("coinRateLivePreview");
  if (preview) {
    preview.textContent = `1 tanga = ${perCoin.toLocaleString()} so'm (1,000 tanga = ${(perCoin * 1000).toLocaleString()} so'm)`;
  }
}

async function saveCoinRate() {
  const coins = parseInt(document.getElementById("inputCoinRateCoins").value) || 1000;
  const som = parseInt(document.getElementById("inputCoinRateSom").value) || 10000;
  const btn = document.getElementById("btnSaveCoinRate");

  if (coins <= 0 || som <= 0) {
    alert("Iltimos, musbat sonlar kiriting.");
    return;
  }

  if (isDemoMode) {
    coinRateCoins = coins;
    coinRateSom = som;
    updateCoinRateBadgeUI(coins, som);
    closeCoinRateModal();
    showToast("✅ Tangalar kursi saqlandi (Demo)");
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/v1/webapp/coin-rate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent_id: parentId, coins, som })
    });
    const data = await res.json();
    if (data.status === "success") {
      coinRateCoins = coins;
      coinRateSom = som;
      updateCoinRateBadgeUI(coins, som);
      closeCoinRateModal();
      showToast("✅ Tangalar kursi saqlandi va bolalarga xabar berildi!");
      if (parentId) loadData();
    } else {
      alert("Xatolik: " + (data.detail || "Kursni saqlashda xatolik"));
    }
  } catch (e) {
    console.error("Save coin rate error:", e);
    alert("Server bilan aloqa uzildi.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function updateCoinRateBadgeUI(coins, som) {
  const cornerText = document.getElementById("coinRateCornerText");
  if (cornerText) {
    cornerText.textContent = `${(coins / 1000).toFixed(0)}k = ${(som / 1000).toFixed(0)}k so'm`;
  }
}

// =========================================================
// THEME & DESIGN PALETTE ENGINE (Transforming Entire Site)
// =========================================================

let currentTheme = localStorage.getItem("smart_tracker_theme") || "midnight";
let currentLanguage = localStorage.getItem("smart_tracker_lang") || "uz_lat";

const I18N_STRINGS = {
  uz_lat: {
    tab_map: "Xarita",
    tab_zones: "Joylar",
    tab_tasks: "Vazifalar",
    tab_history: "Tarix",
    tab_settings: "Dizayn",
    tab_adventure: "Sarguzasht",
    tab_castles: "Makonlarim",
    tab_missions: "Missiyalar",
    tab_treasure: "Xazina",

    live_tracking: "Jonli kuzatuv",
    current_address: "Joriy Manzil",
    route: "Yo'l",
    zones_title: "Kerakli Joylar (Geozonalar)",
    tasks_title: "Farzandga Topshiriq Berish",
    history_title: "Bugungi Harakat Tarixi",
    add_zone_btn: "Yangi kerakli joy qo'shish",
    play_route_btn: "Harakatni animatsiyada ko'rish",
    add_task_btn: "Yangi topshiriq berish",

    hero_adventure: "Bugungi sarguzasht",
    treasure_balance: "Jamg'arilgan naqd pulingiz:",
    active_missions: "Bugungi Missiyalar & Vazifalar",
    safe_castles: "Mening Xavfsiz Makonlarim 🛡️",
    sos_button: "XAVF SIGNALINI YOQISH (SOS)",
    check_in: "Yetib keldim",
    go_home: "Uyga yo'l",
    call_parent: "Ota-onam",
    view_adventure_map: "Sarguzasht xaritasini to'liq ochish",
    seen_button: "Ko'rdim",
    decide_time: "Bajarish vaqti",
    send_photo: "Rasm yuborish",
    wallet_title: "Mening Hamyonim & Jamg'armam",

    settings_title: "Dizayn va Til Sozlamalari",
    lang_heading: "🌐 Tilni tanlash",
    theme_heading: "🎨 Dizayn Mavzulari (12 xil)",
    theme_desc: "O'zingizga yoqqan dizaynni tanlang. U butun sayt, xarita va tugmalar ranglarini darhol o'zgartiradi:",
    theme_applied: "Dizayn muvaffaqiyatli o'zgartirildi",
    lang_applied: "Til muvaffaqiyatli o'zgartirildi",
    close_btn: "Yopish"
  },
  uz_cyr: {
    tab_map: "Харита",
    tab_zones: "Жойлар",
    tab_tasks: "Вазифалар",
    tab_history: "Тарих",
    tab_settings: "Дизайн",
    tab_adventure: "Саргузашт",
    tab_castles: "Маконларим",
    tab_missions: "Миссиялар",
    tab_treasure: "Хазина",

    live_tracking: "Жонли кузатув",
    current_address: "Жорий Манзил",
    route: "Йўл",
    zones_title: "Керакли Жойлар (Геозоналар)",
    tasks_title: "Фарзандга Топшириқ Бериш",
    history_title: "Бугунги Ҳаракат Тарихи",
    add_zone_btn: "Янги керакли жой қўшиш",
    play_route_btn: "Ҳаракатни анимацияда кўриш",
    add_task_btn: "Янги топшириқ бериш",

    hero_adventure: "Бугунги саргузашт",
    treasure_balance: "Жамғарилган нақд пулингиз:",
    active_missions: "Бугунги Миссиялар & Вазифалар",
    safe_castles: "Менинг Хавфсиз Маконларим 🛡️",
    sos_button: "ХАВФ СИГНАЛИНИ ЁҚИШ (SOS)",
    check_in: "Етиб келдим",
    go_home: "Уйга йўл",
    call_parent: "Ота-онам",
    view_adventure_map: "Саргузашт харитасини тўлиқ очиш",
    seen_button: "Кўрдим",
    decide_time: "Бажариш вақти",
    send_photo: "Расм юбориш",
    wallet_title: "Менинг Ҳамёним & Жамғармам",

    settings_title: "Дизайн ва Тил Созламалари",
    lang_heading: "🌐 Тилни танлаш",
    theme_heading: "🎨 Дизайн Мавзулари (12 хил)",
    theme_desc: "Ўзингизга ёққан дизайнни танланг. У бутун сайт, харита ва тугмалар рангларини дарҳол ўзгартиради:",
    theme_applied: "Дизайн муваффақиятли ўзгартирилди",
    lang_applied: "Тил муваффақиятли ўзгартирилди",
    close_btn: "Ёпиш"
  },
  ru: {
    tab_map: "Карта",
    tab_zones: "Места",
    tab_tasks: "Задания",
    tab_history: "История",
    tab_settings: "Дизайн",
    tab_adventure: "Приключение",
    tab_castles: "Мои Места",
    tab_missions: "Миссии",
    tab_treasure: "Копилка",

    live_tracking: "Прямой эфир",
    current_address: "Текущий адрес",
    route: "Маршрут",
    zones_title: "Безопасные зоны (Геозоны)",
    tasks_title: "Поручить задание ребенку",
    history_title: "История перемещений сегодня",
    add_zone_btn: "Добавить безопасную зону",
    play_route_btn: "Анимация маршрута",
    add_task_btn: "Добавить новое задание",

    hero_adventure: "Приключение сегодня",
    treasure_balance: "Накопленные средства:",
    active_missions: "Активные миссии и задания",
    safe_castles: "Мои Безопасные Места 🛡️",
    sos_button: "СИГНАЛ ТРЕВОГИ (SOS)",
    check_in: "Я добрался",
    go_home: "Путь домой",
    call_parent: "Позвонить родителям",
    view_adventure_map: "Открыть карту приключений",
    seen_button: "Просмотрено",
    decide_time: "Время выполнения",
    send_photo: "Отправить фотоотчет",
    wallet_title: "Мой кошелек и копилка",

    settings_title: "Настройки Дизайна и Языка",
    lang_heading: "🌐 Выбор языка",
    theme_heading: "🎨 Темы оформления (12 тем)",
    theme_desc: "Выберите оформление по душе. Оно моментально преобразит весь сайт, карту и кнопки:",
    theme_applied: "Тема успешно применена",
    lang_applied: "Язык успешно изменен",
    close_btn: "Закрыть"
  },
  en: {
    tab_map: "Map",
    tab_zones: "Places",
    tab_tasks: "Tasks",
    tab_history: "History",
    tab_settings: "Design",
    tab_adventure: "Adventure",
    tab_castles: "My Places",
    tab_missions: "Missions",
    tab_treasure: "Treasure",

    live_tracking: "Live Tracking",
    current_address: "Current Address",
    route: "Route",
    zones_title: "Safe Places (Geofences)",
    tasks_title: "Assign Task to Child",
    history_title: "Today's Travel History",
    add_zone_btn: "Add New Safe Place",
    play_route_btn: "Animate Travel Route",
    add_task_btn: "Create New Task",

    hero_adventure: "Today's Adventure",
    treasure_balance: "Your Saved Pocket Money:",
    active_missions: "Active Missions & Quests",
    safe_castles: "My Safe Havens 🛡️",
    sos_button: "TRIGGER SOS ALARM",
    check_in: "I Arrived",
    go_home: "Take Me Home",
    call_parent: "Call Parents",
    view_adventure_map: "Open Full Adventure Map",
    seen_button: "I Saw This",
    decide_time: "Pick Time",
    send_photo: "Send Photo Proof",
    wallet_title: "My Wallet & Piggy Bank",

    settings_title: "Design & Language Settings",
    lang_heading: "🌐 Choose Language",
    theme_heading: "🎨 Design Themes (12 styles)",
    theme_desc: "Choose a theme you like. It instantly transforms the entire site, map and buttons:",
    theme_applied: "Theme applied successfully",
    lang_applied: "Language changed successfully",
    close_btn: "Close"
  }
};

function setGlobalLanguage(lang, showToastMsg = true) {
  if (!I18N_STRINGS[lang]) lang = "uz_lat";
  currentLanguage = lang;
  localStorage.setItem("smart_tracker_lang", lang);

  const dict = I18N_STRINGS[lang];

  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    if (dict[key]) {
      el.textContent = dict[key];
    }
  });

  document.querySelectorAll(".lang-option-btn").forEach(btn => {
    btn.classList.remove("ring-2", "ring-offset-2", "ring-offset-slate-900", "border-white");
    const check = btn.querySelector(".lang-check");
    if (check) check.classList.add("hidden");
  });
  const activeLangBtn = document.getElementById(`langBtn-${lang}`);
  if (activeLangBtn) {
    activeLangBtn.classList.add("ring-2", "ring-offset-2", "ring-offset-slate-900", "border-white");
    const check = activeLangBtn.querySelector(".lang-check");
    if (check) check.classList.remove("hidden");
  }

  // Refresh active tab labels according to language & role
  if (currentRole === "child") {
    applyChildUI(authenticatedSession?.user || {});
  } else {
    applyParentUI(authenticatedSession || {});
  }

  if (showToastMsg) {
    haptic("light");
    showToast(`🌐 ${dict.lang_applied || 'Til yangilandi'}`);
  }
}

const THEME_NAMES = {
  galaxy: "🌌 Kosmik Neon",
  midnight: "🛡️ Taktik Qora",
  comic: "🦸 Super Qahramon",
  emerald: "🌿 Zümrad Tabiat",
  sunset: "🌅 Oltin Quyosh",
  daylight: "☀️ Yorug' Kunduz",
  ocean: "🌊 Chuqur Okean",
  sakura: "🌸 Pushti Sakura",
  cyberpunk: "⚡ Kiberpank 2077",
  magma: "🌋 Olovli Magma",
  arcade: "🎮 Retro Piksel",
  frost: "❄️ Muzlik Arktika"
};

const THEME_HEADER_COLORS = {
  galaxy: "#120a2c",
  midnight: "#0b1120",
  comic: "#101938",
  emerald: "#082114",
  sunset: "#221004",
  daylight: "#ffffff",
  ocean: "#062338",
  sakura: "#2b0e20",
  cyberpunk: "#161822",
  magma: "#240808",
  arcade: "#180f30",
  frost: "#122238"
};

function applyMapThemeTiles(themeName) {
  if (isSatellite) return;
  if (!map) return;

  const mapEl = document.getElementById("map");

  // OpenStreetMap Standard - 100% Free, No CartoDB watermarks, No API key required
  if (!streetTileLayer) {
    streetTileLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      subdomains: ["a", "b", "c"],
      attribution: '&copy; OpenStreetMap'
    });
  }

  if (!map.hasLayer(streetTileLayer)) {
    streetTileLayer.addTo(map);
  }

  // Adjust dark/light tile styling via CSS filters on the map container
  if (mapEl) {
    mapEl.classList.remove("map-dark-tiles", "map-cyber-tiles", "map-light-tiles");
    if (themeName === "midnight" || themeName === "galaxy" || themeName === "magma" || themeName === "arcade") {
      mapEl.classList.add("map-dark-tiles");
    } else if (themeName === "cyberpunk") {
      mapEl.classList.add("map-cyber-tiles");
    } else {
      mapEl.classList.add("map-light-tiles");
    }
  }
}

function setGlobalTheme(themeName, showNotification = true) {
  if (!THEME_NAMES[themeName]) themeName = "midnight";
  currentTheme = themeName;
  localStorage.setItem("smart_tracker_theme", themeName);

  document.body.classList.remove(
    "theme-galaxy", "theme-midnight", "theme-comic", "theme-emerald",
    "theme-sunset", "theme-daylight", "theme-ocean", "theme-sakura",
    "theme-cyberpunk", "theme-magma", "theme-arcade", "theme-frost"
  );
  document.body.classList.add(`theme-${themeName}`);

  document.querySelectorAll(".theme-option-btn").forEach((btn) => {
    btn.classList.remove("ring-2", "ring-offset-2", "ring-offset-slate-900", "border-white");
    const check = btn.querySelector(".theme-check");
    if (check) check.classList.add("hidden");
  });
  const activeThemeBtn = document.getElementById(`themeBtn-${themeName}`);
  if (activeThemeBtn) {
    activeThemeBtn.classList.add("ring-2", "ring-offset-2", "ring-offset-slate-900", "border-white");
    const check = activeThemeBtn.querySelector(".theme-check");
    if (check) check.classList.remove("hidden");
  }

  applyMapThemeTiles(themeName);

  if (tg) {
    try {
      const headerColor = THEME_HEADER_COLORS[themeName] || "#090d16";
      if (tg.setHeaderColor) tg.setHeaderColor(headerColor);
      if (tg.setBackgroundColor) tg.setBackgroundColor(headerColor);
    } catch (e) {}
  }

  if (showNotification) {
    haptic("medium");
    playSuccessChime();
    showToast(`🎨 Yangi dizayn: ${THEME_NAMES[themeName]}`);
    closeSettingsModal();
  }
}

function openSettingsModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("medium");
  switchTab("settings");
}

function closeSettingsModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  const modal = document.getElementById("modal-settings");
  if (modal) modal.classList.add("hidden");
  switchTab("map");
}

function openThemeModal() {
  openSettingsModal();
}

function closeThemeModal() {
  closeSettingsModal();
}

// Initialize Leaflet Map with OpenStreetMap and Esri Satellite layers
function initMap() {
  if (map) {
    try {
      map.remove();
    } catch (e) {
      console.warn("Map cleanup error:", e);
    }
    map = null;
    window.map = null;
  }
  const mapContainer = document.getElementById("map");
  if (!mapContainer) return;
  if (mapContainer._leaflet_id) {
    delete mapContainer._leaflet_id;
  }

  map = L.map("map", {
    zoomControl: false,
    attributionControl: false,
    tap: false,
    touchZoom: true,
  }).setView([DEFAULT_LAT, DEFAULT_LON], 14);

  // Expose map to global window for external triggers and UI controls
  window.map = map;

  // Auto-resize observer to handle container height shifts (fullscreen toggle, orientation change, header collapse)
  if (window.ResizeObserver && mapContainer) {
    try {
      let resizeTimer = null;
      const ro = new ResizeObserver(() => {
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
          try {
            if (map) {
              map.invalidateSize({ pan: false });
            }
          } catch (e) {}
        }, 50);
      });
      ro.observe(mapContainer);
    } catch (e) {}
  }

  // Apply map tiles according to active theme
  applyMapThemeTiles(currentTheme);

  // 2. Satellite Layer: Esri World Imagery (High Resolution, Free)
  satelliteTileLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19,
    attribution: '&copy; Esri'
  });

  // 3. Satellite Labels: Esri Reference Boundaries and Places (Free, No Key)
  satelliteLabelsLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19,
  });

  // Map click listener: when Zone Picker is active, move pin & circle directly to tapped location!
  map.on("click", (e) => {
    if (isZonePickerActive) {
      setZonePickerLocation(e.latlng.lat, e.latlng.lng);
    }
  });

  // Prevent Leaflet map from absorbing or hijacking touch/click events on floating dock
  const dock = document.getElementById("floatingMapControls");
  if (dock && window.L && L.DomEvent) {
    try {
      L.DomEvent.disableClickPropagation(dock);
      L.DomEvent.disableScrollPropagation(dock);
    } catch (e) {}
  }
}

// Toggle between Street and Satellite Hybrid Map
function toggleMapLayer() {
  haptic("medium");
  const btn = document.getElementById("btnMapLayer");
  const mapEl = document.getElementById("map");
  isSatellite = !isSatellite;

  if (isSatellite) {
    if (mapEl) mapEl.classList.remove("map-dark-tiles", "map-cyber-tiles");
    if (streetTileLayer && map.hasLayer(streetTileLayer)) {
      map.removeLayer(streetTileLayer);
    }
    satelliteTileLayer.addTo(map);
    satelliteLabelsLayer.addTo(map);
    btn.classList.add("text-yellow-400");
    btn.classList.remove("text-emerald-400");
    showToast("🛰 Yo'ldosh xaritasi yoqildi");
  } else {
    map.removeLayer(satelliteTileLayer);
    map.removeLayer(satelliteLabelsLayer);
    applyMapThemeTiles(currentTheme);
    btn.classList.remove("text-yellow-400");
    btn.classList.add("text-emerald-400");
    showToast("🗺 Standart xarita yoqildi");
  }
}

// Load real data with persistent caching across server redeployments
async function loadData() {
  try {
    const res = await fetch(`/api/v1/webapp/parent/${parentId}/children`);
    if (!res.ok) throw new Error("API status " + res.status);
    const data = await res.json();

    if (data.status === "success" && data.children && data.children.length > 0) {
      childrenList = data.children;
      localStorage.setItem("smart_tracker_cached_children", JSON.stringify(childrenList));
      isDemoMode = false;
      document.getElementById("demoBadge")?.classList.add("hidden");
      document.getElementById("btnTopDemoAuth")?.classList.add("hidden");
      document.getElementById("emptyStateBanner").classList.add("hidden");

      renderChildSelector();

      if (!currentChildId || !childrenList.some((c) => c.id == currentChildId)) {
        currentChildId = childrenList[0].id;
      }

      document.getElementById("childSelect").value = currentChildId;
      await selectChild(currentChildId);
    } else if (data.status === "success" && (!data.children || data.children.length === 0)) {
      // Check cached children before declaring empty state
      const cached = localStorage.getItem("smart_tracker_cached_children");
      if (cached) {
        try {
          const parsed = JSON.parse(cached);
          if (Array.isArray(parsed) && parsed.length > 0) {
            childrenList = parsed;
            renderChildSelector();
            return;
          }
        } catch (e) {}
      }

      if (authenticatedSession) {
        childrenList = [];
        renderChildSelector();
        showEmptyState();
      } else {
        enableDemoMode();
      }
    }
  } catch (err) {
    console.warn("Backend API not reachable (server deploying or network drop):", err);
    // DO NOT clear childrenList or show empty state if we already have children!
    if (childrenList && childrenList.length > 0) {
      renderChildSelector();
      return;
    }
    const cached = localStorage.getItem("smart_tracker_cached_children");
    if (cached) {
      try {
        const parsed = JSON.parse(cached);
        if (Array.isArray(parsed) && parsed.length > 0) {
          childrenList = parsed;
          renderChildSelector();
          return;
        }
      } catch (e) {}
    }

    if (authenticatedSession) {
      showEmptyState();
    } else {
      enableDemoMode();
    }
  }
}

// Show empty state prompt
function showEmptyState() {
  document.getElementById("emptyStateBanner").classList.remove("hidden");
  document.getElementById("childSelect").innerHTML = `<option value="">Farzand ulanmagan</option>`;
  document.getElementById("addressText").textContent = "Ulangan farzandlar mavjud emas";
  document.getElementById("zoneStatusText").textContent = "Farzand qo'shing yoki Demo rejimni yoqing";
}

function closeEmptyState() {
  document.getElementById("emptyStateBanner").classList.add("hidden");
}

// Enable Demo Mode with simulated child, movement, and safe zones
function enableDemoMode() {
  haptic("success");
  isDemoMode = true;
  currentRole = "parent";
  localStorage.setItem("smart_tracker_role", "parent");
  parentId = 1001;
  coinRateCoins = 1000;
  coinRateSom = 10000;

  document.getElementById("emptyStateBanner").classList.add("hidden");
  document.getElementById("demoBadge")?.classList.remove("hidden");
  document.getElementById("btnTopDemoAuth")?.classList.remove("hidden");
  document.getElementById("demoNoticeBanner")?.classList.remove("hidden");

  childrenList = [
    {
      id: 2002,
      full_name: "Azizbek Karimov (Farzand)",
      username: "azizbek_child",
      virtual_balance: 18000,
      virtual_money_som: 180000,
      is_sos_active: false,
      location: { latitude: 41.2916, longitude: 69.2121, battery_level: 82, is_charging: false, sound_mode: "normal" }
    },
    {
      id: 2003,
      full_name: "Madinaxon Karimova (Farzand 2)",
      username: "madina_child",
      virtual_balance: 24000,
      virtual_money_som: 240000,
      is_sos_active: false,
      location: { latitude: 41.3111, longitude: 69.2797, battery_level: 95, is_charging: true, sound_mode: "normal" }
    }
  ];

  currentChildId = 2002;
  renderChildSelector();

  applyParentUI({
    user: { id: 1001, full_name: "Ota-ona (Demo)", coin_rate_coins: 1000, coin_rate_som: 10000 },
    children: childrenList
  });

  // Render demo child live position in Tashkent
  const demoLat = 41.2916;
  const demoLon = 69.2121;

  renderChildMarker(demoLat, demoLon, "Azizbek Karimov (Farzand)", false, 82);
  updateBatteryUI(82, false);
  updateSoundUI("normal");
  resolveAddress(demoLat, demoLon);

  // Load demo geofences
  renderDemoGeofences();

  // Load demo route history
  renderDemoHistory();

  // Load demo tasks
  renderDemoTasks();

  showToast("👨‍👩‍👧 Ota-ona demo rejimiga o'tildi");
}

// Populate child select dropdown & multi-child visual pill bar
function renderChildSelector() {
  const select = document.getElementById("childSelect");
  const multiBar = document.getElementById("parentMultiChildBar");

  if (select) {
    select.innerHTML = childrenList
      .map((c) => `<option value="${escapeHtml(String(c.id))}">${escapeHtml(c.full_name || "Farzand #" + c.id)}</option>`)
      .join("");
    if (currentChildId) {
      select.value = currentChildId;
    }
    select.onchange = (e) => {
      haptic("light");
      selectChild(parseInt(e.target.value));
    };
  }

  // Visual Multi-Child Quick Switcher Bar
  if (multiBar) {
    if (childrenList.length > 1) {
      multiBar.classList.remove("hidden");
      multiBar.innerHTML = childrenList
        .map((c) => {
          const isSelected = c.id == currentChildId;
          const initial = (c.full_name || "B").charAt(0).toUpperCase();
          const activeClass = isSelected
            ? "bg-blue-600 text-white shadow-md border-blue-400 ring-2 ring-blue-400/40 font-black scale-[1.02]"
            : "bg-slate-800/90 text-slate-300 hover:text-white hover:bg-slate-700/80 border-slate-700 font-bold opacity-85";
          const sosIndicator = c.is_sos_active
            ? `<span class="w-2 h-2 rounded-full bg-red-500 animate-ping shrink-0"></span>`
            : `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0"></span>`;

          return `
            <button
              type="button"
              onclick="selectChild(${c.id})"
              class="px-2.5 py-1 rounded-xl text-xs flex items-center gap-1.5 border transition-all active:scale-95 shrink-0 ${activeClass}"
            >
              <span class="w-4 h-4 rounded-full bg-slate-950/60 text-[9px] flex items-center justify-center font-black">${initial}</span>
              <span class="truncate max-w-[110px]">${escapeHtml(c.full_name || 'Farzand')}</span>
              ${sosIndicator}
            </button>
          `;
        })
        .join("");
    } else {
      multiBar.classList.add("hidden");
    }
  }
}

function renderChildrenSwitcher() {
  renderChildSelector();
}

function switchChild(childId) {
  selectChild(childId);
}

// Select active child and load telemetry
async function selectChild(childId) {
  if (!childId) return;
  currentChildId = parseInt(childId);

  const child = childrenList.find((c) => c.id == currentChildId);
  if (!child) return;

  const select = document.getElementById("childSelect");
  if (select && select.value != currentChildId) {
    select.value = currentChildId;
  }

  const initial = (child.full_name || "B").charAt(0).toUpperCase();
  const avatar = document.getElementById("childAvatar");
  if (avatar) avatar.textContent = initial;

  toggleSosBanner(child.is_sos_active);

  // Clear previous polyline and geofence layers
  if (routePolyline && map) {
    try { map.removeLayer(routePolyline); } catch (e) {}
    routePolyline = null;
  }
  if (geofenceLayers && map) {
    geofenceLayers.forEach((layer) => {
      try { map.removeLayer(layer); } catch (e) {}
    });
  }
  geofenceLayers = [];
  geofencesList = [];
  renderChildDashboardCastles();

  // Re-highlight active child pill
  renderChildSelector();

  // If child location is known, immediately center & render marker
  const loc = child.latest_location || child.location;
  const noLocCard = document.getElementById("noLocationCard");
  if (loc && loc.latitude && loc.longitude) {
    if (noLocCard) noLocCard.classList.add("hidden");
    renderChildMarker(loc.latitude, loc.longitude, child.full_name, child.is_sos_active, loc.battery_level);
    if (map) map.flyTo([loc.latitude, loc.longitude], 16, { animate: true, duration: 0.8 });
  } else {
    if (noLocCard) {
      noLocCard.classList.remove("hidden");
      if (window.lucide) lucide.createIcons();
    }
    if (childMarker && map) {
      try { map.removeLayer(childMarker); } catch (e) {}
      childMarker = null;
    }
    const addrEl = document.getElementById("addressText");
    if (addrEl) addrEl.textContent = "Joylashuv kutilmoqda (Ilovaga kirib ruxsat bering)";
    const lastSeenEl = document.getElementById("lastSeenText");
    if (lastSeenEl) lastSeenEl.textContent = "Kutilmoqda...";
    updateBatteryUI(null, false);
  }

  await updateLiveTelemetry(currentChildId);
  await loadGeofences(currentChildId);
  await loadRouteHistory(currentChildId);
  await loadTasks();
  await loadWishlist(currentChildId);
}

// Fetch live telemetry for child
async function updateLiveTelemetry(childId) {
  if (isDemoMode) return;

  try {
    const res = await fetch(`/api/v1/webapp/child/${childId}/status`);
    const data = await res.json();
    if (data.status !== "success" || !data.child) return;

    const child = data.child;
    toggleSosBanner(child.is_sos_active);

    // Feature 1: Walk Safe mode status sync for Parent
    if (child.walk_safe) {
      renderParentWalkSafeBanner(child.walk_safe);
    }

    // Feature 4: Wishlist status sync
    if (child.wishlist) {
      renderWishlistUI(child.wishlist);
    }

    // Feature 5: Device policy and screen time sync
    if (child.device_policy) {
      updateDevicePolicyUI(child.device_policy);
    }

    const loc = child.location;
    const noLocCard = document.getElementById("noLocationCard");
    if (loc && loc.latitude && loc.longitude) {
      if (noLocCard) noLocCard.classList.add("hidden");
      const lat = loc.latitude;
      const lon = loc.longitude;

      updateBatteryUI(loc.battery_level, loc.is_charging);
      updateSoundUI(loc.sound_mode);
      updateSpeedUI(loc.speed);

      // Accurate Online/Offline computation (> 3 minutes without telemetry = offline)
      const lastSeenEl = document.getElementById("lastSeenText");
      const onlineDot = document.getElementById("onlineDot");
      let isOnline = false;
      let offlineText = "";

      if (loc.recorded_at) {
        let s = String(loc.recorded_at).trim();
        if (!s.endsWith("Z") && !s.includes("+")) s += "Z";
        const recordedDate = new Date(s);
        const diffMinutes = Math.floor((Date.now() - recordedDate.getTime()) / 60000);
        isOnline = diffMinutes <= 3;
        const timeFormatted = recordedDate.toLocaleTimeString("uz-UZ", { hour: "2-digit", minute: "2-digit" });

        if (isOnline) {
          if (onlineDot) onlineDot.className = "absolute -bottom-0.5 -right-0.5 w-3 h-3 bg-emerald-500 border-2 border-slate-950 rounded-full shadow-sm";
          if (lastSeenEl) {
            lastSeenEl.innerHTML = `
              <span class="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-ping shrink-0"></span>
              <span class="text-emerald-400 font-extrabold text-xs">Online</span>
              <span class="text-slate-400 text-[10px]">(${formatTimeAgo(loc.recorded_at)})</span>
            `;
          }
        } else {
          offlineText = `${timeFormatted} da uzilgan`;
          if (onlineDot) onlineDot.className = "absolute -bottom-0.5 -right-0.5 w-3 h-3 bg-rose-500 border-2 border-slate-950 rounded-full shadow-sm";
          if (lastSeenEl) {
            lastSeenEl.innerHTML = `
              <span class="inline-block w-2 h-2 rounded-full bg-rose-500 shrink-0"></span>
              <span class="text-rose-400 font-extrabold text-xs">Oflayn</span>
              <span class="text-slate-300 text-[10px]">(${timeFormatted} da uzilgan — ${formatTimeAgo(loc.recorded_at)})</span>
            `;
          }
        }
      }

      renderChildMarker(lat, lon, child.full_name, child.is_sos_active, loc.battery_level, isOnline, offlineText);
      resolveAddress(lat, lon);
    } else {
      if (noLocCard) {
        noLocCard.classList.remove("hidden");
        if (window.lucide) lucide.createIcons();
      }
      document.getElementById("addressText").textContent = "Joylashuv kutilmoqda (Ilovaga kirib ruxsat bering)";
      document.getElementById("lastSeenText").textContent = "Kutilmoqda...";
      if (childMarker && map) {
        try { map.removeLayer(childMarker); } catch (e) {}
        childMarker = null;
      }
      updateBatteryUI(null, false);
      updateSpeedUI(0);
    }
  } catch (err) {
    console.warn("Telemetry polling error:", err);
  }
}

// Reverse Geocoding with Nominatim & Cache
async function resolveAddress(lat, lon) {
  if (lat === null || lat === undefined || lon === null || lon === undefined || isNaN(lat) || isNaN(lon)) {
    const el = document.getElementById("addressText");
    if (el) el.textContent = "Joylashuv kutilmoqda";
    return;
  }
  const numLat = Number(lat);
  const numLon = Number(lon);
  const key = `${numLat.toFixed(4)},${numLon.toFixed(4)}`;
  if (addressCache.has(key)) {
    document.getElementById("addressText").textContent = addressCache.get(key);
    return;
  }

  try {
    const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${numLat}&lon=${numLon}&zoom=18&addressdetails=1`, {
      headers: { "Accept-Language": "uz,ru,en" },
    });
    const data = await res.json();
    if (data && data.display_name) {
      // Extract street and district and real city/region (e.g. Navoiy, Samarqand, etc.)
      const addr = data.address || {};
      const cityName = addr.city || addr.town || addr.village || addr.county || addr.state || "";
      const shortAddr = addr.road || addr.suburb || addr.neighbourhood || addr.city_district;
      let fullText = "";
      if (shortAddr && cityName) {
        fullText = `${shortAddr}, ${cityName}`;
      } else if (shortAddr) {
        fullText = shortAddr;
      } else if (cityName) {
        fullText = cityName;
      } else {
        fullText = data.display_name.split(",").slice(0, 2).join(",");
      }
      addressCache.set(key, fullText);
      const el = document.getElementById("addressText");
      if (el) el.textContent = fullText;
      return;
    }
  } catch (e) {
    // Fallback to coordinates
  }

  document.getElementById("addressText").textContent = `Koordinata: ${numLat.toFixed(6)}, ${numLon.toFixed(6)}`;
}

// Ultra-smooth marker interpolation (0.01s / 10ms frame animation)
function smoothMoveMarker(marker, targetLat, targetLon, durationMs = 600) {
  if (!marker) return;
  const fromLatLng = marker.getLatLng();
  const startLat = fromLatLng.lat;
  const startLng = fromLatLng.lng;
  const deltaLat = targetLat - startLat;
  const deltaLng = targetLon - startLng;
  if (Math.abs(deltaLat) < 0.0000001 && Math.abs(deltaLng) < 0.0000001) {
    marker.setLatLng([targetLat, targetLon]);
    return;
  }

  if (marker._animFrame) {
    cancelAnimationFrame(marker._animFrame);
    marker._animFrame = null;
  }

  const startTime = performance.now();
  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / durationMs, 1.0);
    const ease = 1 - Math.pow(1 - progress, 3);
    const curLat = startLat + deltaLat * ease;
    const curLng = startLng + deltaLng * ease;
    marker.setLatLng([curLat, curLng]);
    if (progress < 1.0) {
      marker._animFrame = requestAnimationFrame(step);
    } else {
      marker.setLatLng([targetLat, targetLon]);
      marker._animFrame = null;
    }
  }
  marker._animFrame = requestAnimationFrame(step);
}

// Render or move pulsing radar child marker
function renderChildMarker(lat, lon, name, isSos, battery, isOnline = true, offlineText = "") {
  if (lat === null || lat === undefined || lon === null || lon === undefined || isNaN(lat) || isNaN(lon)) {
    return;
  }
  const initial = (name || "B").charAt(0).toUpperCase();
  const pulseClass = isSos ? "sos-pulse-ring" : "child-pulse-ring";
  const pinBg = isSos ? "bg-red-600" : (isOnline ? "bg-gradient-to-tr from-blue-700 to-indigo-500" : "bg-gradient-to-tr from-slate-600 to-slate-800");

  const customHtml = `
    <div class="child-radar-marker">
      <div class="${pulseClass}"></div>
      <div class="child-avatar-pin ${pinBg}">${initial}</div>
    </div>
  `;

  const icon = L.divIcon({
    html: customHtml,
    className: "custom-leaflet-div-icon",
    iconSize: [44, 44],
    iconAnchor: [22, 22],
  });

  if (childMarker) {
    smoothMoveMarker(childMarker, lat, lon, 600);
    childMarker.setIcon(icon);
  } else {
    childMarker = L.marker([lat, lon], { icon: icon }).addTo(map);
  }

  // Auto-fly to child if far away (> 3km e.g. Navoiy vs Tashkent is 400km) or not yet centered
  if (map) {
    try {
      const center = map.getCenter();
      const dist = map.distance(center, [lat, lon]);
      if (dist > 3000 || !map._hasAutoCenteredChild) {
        map.flyTo([lat, lon], 15, { animate: true, duration: 1.2 });
        map._hasAutoCenteredChild = true;
      }
    } catch (e) {
      map.setView([lat, lon], 15);
      map._hasAutoCenteredChild = true;
    }
  }

  const statusBadge = isOnline
    ? `<div class="text-[11px] font-bold text-emerald-600 flex items-center gap-1 mt-1">🟢 <span>Online (Jonli nazorat faol)</span></div>`
    : `<div class="text-[11px] font-bold text-rose-600 flex items-center gap-1 mt-1">🔴 <span>Oflayn ${offlineText ? '(' + offlineText + ')' : ''}</span></div>`;

  childMarker.bindPopup(`
    <div class="p-1 text-slate-900 text-xs font-semibold font-sans">
      <div class="font-bold text-sm mb-1 text-blue-700">${name || "Farzand"}</div>
      <div>🔋 Batareya: <b>${battery !== null && battery !== undefined ? battery + "%" : "Noma'lum"}</b></div>
      ${statusBadge}
      <div class="text-[9px] text-slate-500 font-mono mt-1">📍 ${lat.toFixed(6)}, ${lon.toFixed(6)}</div>
    </div>
  `);
}

// Center view on child
function centerOnChild() {
  haptic("light");
  if (childMarker && map) {
    map.flyTo(childMarker.getLatLng(), 16, { animate: true, duration: 1 });
  } else if (cachedDeviceLocation && map) {
    map.flyTo([cachedDeviceLocation.latitude, cachedDeviceLocation.longitude], 16, { animate: true, duration: 1 });
  } else {
    showToast("Farzand joylashuvi hali mavjud emas");
  }
}

// Load safe zones (Geofences)
async function loadGeofences(childId) {
  if (isDemoMode) {
    renderDemoGeofences();
    return;
  }

  const targetId = childId || currentChildId || authenticatedSession?.user?.id;
  if (!targetId) {
    renderChildDashboardCastles();
    return;
  }

  // Clear all previous geofence layers and tooltips from map
  if (geofenceLayers && map) {
    geofenceLayers.forEach((layer) => {
      try { map.removeLayer(layer); } catch (e) {}
    });
  }
  geofenceLayers = [];
  geofencesList = [];

  try {
    const res = await fetch(`/api/v1/webapp/child/${targetId}/geofences`);
    const data = await res.json();
    if (data.status !== "success") {
      renderChildDashboardCastles();
      return;
    }

    const fences = data.geofences || [];
    geofencesList = fences;
    const container = document.getElementById("zonesListContainer");

    if (fences.length === 0) {
      if (container) {
        if (currentRole === "child") {
          container.innerHTML = `
            <div class="text-center py-6 px-4">
              <span class="text-2xl mb-2 block">🛡️</span>
              <p class="text-xs font-bold text-slate-300 mb-1">Xavfsiz makonlar hali belgilanmagan</p>
              <p class="text-[11px] text-slate-400">Ota-onangiz bot yoki sayt orqali kerakli joylarni qo'shganda bu yerda ko'rinadi.</p>
            </div>
          `;
        } else {
          container.innerHTML = `
            <div class="text-center py-6 px-4">
              <p class="text-xs text-slate-400 mb-3">Hozircha kerakli joylar belgilanmagan.</p>
              <button onclick="openAddZoneModal()" class="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white text-xs font-bold rounded-xl shadow-md transition active:scale-95">
                ➕ Kerakli joy qo'shish
              </button>
            </div>
          `;
        }
      }
      const zoneStatusEl = document.getElementById("zoneStatusText");
      if (zoneStatusEl) zoneStatusEl.textContent = "Kerakli joylar belgilanmagan";
      renderChildDashboardCastles();
      return;
    }

    renderFencesToMapAndList(fences);
  } catch (err) {
    console.error("Failed to load geofences:", err);
    geofencesList = [];
    renderChildDashboardCastles();
  }
}

function renderFencesToMapAndList(fences) {
  geofencesList = fences;
  renderChildDashboardCastles();
  const container = document.getElementById("zonesListContainer");
  const colors = ["#10b981", "#3b82f6", "#8b5cf6", "#f59e0b"];

  if (map) {
    if (geofenceLayers && geofenceLayers.length > 0) {
      geofenceLayers.forEach((layer) => {
        try { map.removeLayer(layer); } catch (e) {}
      });
      geofenceLayers = [];
    }

    fences.forEach((fence, idx) => {
      const color = colors[idx % colors.length];
      const circle = L.circle([fence.latitude, fence.longitude], {
        color: color,
        fillColor: color,
        fillOpacity: 0.16,
        weight: 2,
        radius: fence.radius_meters,
      }).addTo(map);

      circle.bindTooltip(`📍 ${fence.name} (${Math.round(fence.radius_meters)}m)`, {
        permanent: true,
        direction: "top",
        className: "geofence-tooltip",
      });

      geofenceLayers.push(circle);
    });
  }

  if (container) {
    const isChild = (currentRole === "child");
    container.innerHTML = fences
      .map(
        (f) => `
      <div class="bg-slate-800/90 border border-slate-700/80 p-3 rounded-2xl flex items-center justify-between shadow-sm">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-xl bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-xs">
            <i data-lucide="map-pin" class="w-4 h-4"></i>
          </div>
          <div>
            <h4 class="text-xs font-bold text-white">${f.name}</h4>
            <span class="text-[11px] text-slate-400">Radiusi: ${Math.round(f.radius_meters)} metr</span>
          </div>
        </div>
        ${
          isChild
            ? `<span class="text-[10px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 px-2.5 py-1 rounded-xl font-bold flex items-center gap-1">🛡️ Xavfsiz</span>`
            : `<button onclick="deleteGeofence(${f.id})" class="p-2 text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-xl transition" title="O'chirish">
                <i data-lucide="trash-2" class="w-4 h-4"></i>
              </button>`
        }
      </div>
    `
      )
      .join("");
  }

  if (window.lucide) lucide.createIcons();
  const zoneStatusTextEl = document.getElementById("zoneStatusText");
  if (zoneStatusTextEl) {
    zoneStatusTextEl.textContent = `✅ ${fences.length} ta kerakli joy nazoratda`;
  }

  // Auto-fit view to show all geofences
  if (map && geofenceLayers.length > 0 && currentRole === "parent") {
    try {
      const group = new L.featureGroup(geofenceLayers.concat(childMarker ? [childMarker] : []));
      map.fitBounds(group.getBounds().pad(0.2));
    } catch (e) {}
  }
}

// Render Demo Geofences
function renderDemoGeofences() {
  if (!isDemoMode) {
    geofencesList = [];
    renderChildDashboardCastles();
    return;
  }
  const demoFences = [
    { id: 1, name: "🏠 Uyimiz (Chilonzor)", latitude: 41.2858, longitude: 69.2035, radius_meters: 200 },
    { id: 2, name: "🏫 Maktab №45", latitude: 41.2915, longitude: 69.2120, radius_meters: 250 },
    { id: 3, name: "📚 O'quv Markazi", latitude: 41.3000, longitude: 69.2250, radius_meters: 200 },
  ];
  renderFencesToMapAndList(demoFences);
}

// Focus a specific route checkpoint on map in Fullscreen mode
function focusRoutePoint(lat, lon) {
  haptic("light");
  closeModal("history");
  if (currentRole === "parent" && currentTab !== "map") {
    switchTab("map");
  }
  toggleMapUiVisibility(true);
  if (map) {
    map.flyTo([lat, lon], 17, { animate: true, duration: 0.8 });
  }
}

// Load route history with high precision
async function loadRouteHistory(childId, isSilent = false) {
  if (isDemoMode) {
    renderDemoHistory();
    return;
  }
  const targetId = childId || currentChildId;
  if (!targetId) return;

  try {
    lastRouteFetchTime = Date.now();
    const res = await fetch(`/api/v1/webapp/child/${targetId}/history?limit=500`);
    const data = await res.json();
    if (data.status !== "success") return;

    const points = data.points || [];
    renderHistoryToMapAndList(points);
  } catch (err) {
    if (!isSilent) console.error("Failed to load route history:", err);
  }
}

function renderHistoryToMapAndList(points) {
  routePointsCache = points || [];

  if (routeLayerGroup && map) {
    try { map.removeLayer(routeLayerGroup); } catch (e) {}
    routeLayerGroup = null;
  }
  if (routePolyline && map) {
    try { map.removeLayer(routePolyline); } catch (e) {}
    routePolyline = null;
  }

  const container = document.getElementById("historyListContainer");
  if (!points || points.length === 0) {
    if (container) {
      container.innerHTML = `<p class="text-center text-xs text-slate-400 py-6">Bugun uchun harakat yozuvlari yo'q.</p>`;
    }
    return;
  }

  routeLayerGroup = L.featureGroup();
  const latlngs = points.map((p) => [p.latitude, p.longitude]);

  // 1. Layered high-contrast glowing polyline
  const glowPolyline = L.polyline(latlngs, {
    color: "#38bdf8",
    weight: 7,
    opacity: 0.35,
    lineCap: "round",
    lineJoin: "round"
  });
  glowPolyline.addTo(routeLayerGroup);

  routePolyline = L.polyline(latlngs, {
    color: "#f59e0b",
    weight: 4,
    opacity: 0.95,
    dashArray: "8, 6"
  });
  routePolyline.addTo(routeLayerGroup);

  // 2. Identify repeated visits (Bitta yo'ldan qayta-qayta yurgan joylarni aniqlash)
  // Cluster points within 25 meters
  const clusters = [];
  const pointVisitInfo = new Map();

  points.forEach((p, idx) => {
    let pTimeStr = "--:--:--";
    let rawDate = null;
    if (p.recorded_at) {
      let s = String(p.recorded_at).trim();
      if (!s.endsWith("Z") && !s.includes("+")) s += "Z";
      rawDate = new Date(s);
      pTimeStr = rawDate.toLocaleTimeString("uz-UZ", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }
    const speedVal = (typeof p.speed === "number" && p.speed > 0) ? `${Math.round(p.speed)} km/soat` : "Piyoda";
    const batVal = (p.battery_level !== null && p.battery_level !== undefined) ? `${p.battery_level}%` : "--";

    let matchedCluster = null;
    for (const c of clusters) {
      const dist = calculateDistanceMeters(c.latitude, c.longitude, p.latitude, p.longitude);
      if (dist <= 25) { // 25 meters proximity = same street or junction
        matchedCluster = c;
        break;
      }
    }

    const visitItem = {
      pointIndex: idx,
      stepNum: idx + 1,
      time: pTimeStr,
      rawDate: rawDate,
      battery: batVal,
      speed: speedVal,
      isCharging: p.is_charging,
      lat: p.latitude,
      lng: p.longitude
    };

    if (matchedCluster) {
      matchedCluster.visits.push(visitItem);
    } else {
      clusters.push({
        latitude: p.latitude,
        longitude: p.longitude,
        visits: [visitItem]
      });
    }
  });

  // Annotate each point with its pass index and total passes
  clusters.forEach((c) => {
    c.visits.forEach((v, passIdx) => {
      pointVisitInfo.set(v.pointIndex, {
        passNumber: passIdx + 1,
        totalPasses: c.visits.length
      });
    });
  });

  // 3. Render Checkpoints on Map
  // A. Start point
  const firstP = points[0];
  let firstTime = firstP.recorded_at ? new Date(firstP.recorded_at.endsWith("Z") ? firstP.recorded_at : firstP.recorded_at + "Z").toLocaleTimeString("uz-UZ", { hour: "2-digit", minute: "2-digit" }) : "";
  const startIcon = L.divIcon({
    className: "route-pin-start",
    html: `
      <div class="flex items-center gap-1 bg-emerald-600 text-white text-[10px] font-black px-2.5 py-1 rounded-full shadow-2xl border-2 border-white whitespace-nowrap transform -translate-x-1/2 -translate-y-1/2">
        <span>🟢 Boshlanish</span> <span class="opacity-95 font-mono">${firstTime}</span>
      </div>
    `,
    iconSize: [0, 0],
    iconAnchor: [0, 0]
  });
  L.marker([firstP.latitude, firstP.longitude], { icon: startIcon }).addTo(routeLayerGroup);

  // B. Finish / Latest point
  if (points.length > 1) {
    const lastP = points[points.length - 1];
    let lastTime = lastP.recorded_at ? new Date(lastP.recorded_at.endsWith("Z") ? lastP.recorded_at : lastP.recorded_at + "Z").toLocaleTimeString("uz-UZ", { hour: "2-digit", minute: "2-digit" }) : "";
    const endIcon = L.divIcon({
      className: "route-pin-end",
      html: `
        <div class="flex items-center gap-1 bg-gradient-to-r from-amber-500 to-rose-600 text-white text-[10px] font-black px-2.5 py-1 rounded-full shadow-2xl border-2 border-white whitespace-nowrap transform -translate-x-1/2 -translate-y-1/2">
          <span>🏁 Oxirgi joy</span> <span class="opacity-95 font-mono">${lastTime}</span>
        </div>
      `,
      iconSize: [0, 0],
      iconAnchor: [0, 0]
    });
    L.marker([lastP.latitude, lastP.longitude], { icon: endIcon }).addTo(routeLayerGroup);
  }

  // C. Render repeated visit spots & intermediate step checkpoints
  clusters.forEach((c) => {
    if (c.visits.length > 1) {
      // REPEATED LOCATION: Child walked here 2+ times!
      const passCount = c.visits.length;
      const repeatIcon = L.divIcon({
        className: "route-repeat-badge",
        html: `
          <div class="relative group cursor-pointer transform -translate-x-1/2 -translate-y-1/2">
            <div class="w-7 h-7 rounded-full bg-gradient-to-tr from-rose-600 via-pink-600 to-amber-500 text-white font-black text-xs flex items-center justify-center shadow-2xl border-2 border-white animate-bounce">
              ${passCount}x
            </div>
            <span class="absolute -top-1 -right-1 w-2.5 h-2.5 bg-yellow-400 rounded-full border border-white animate-ping"></span>
          </div>
        `,
        iconSize: [0, 0],
        iconAnchor: [0, 0]
      });

      const repeatMarker = L.marker([c.latitude, c.longitude], { icon: repeatIcon });
      const passesHtml = c.visits.map((v, i) => `
        <div class="flex items-center justify-between p-2 bg-slate-800/90 rounded-xl border border-slate-700/70 text-xs">
          <div class="flex items-center gap-2">
            <span class="w-5 h-5 rounded-full bg-rose-500/25 text-rose-300 font-black text-[10px] flex items-center justify-center border border-rose-500/40">${i + 1}</span>
            <div>
              <p class="font-bold text-white text-xs">${i + 1}-o'tish vaqti:</p>
              <p class="font-mono text-amber-300 font-extrabold text-[11px]">${v.time}</p>
            </div>
          </div>
          <div class="text-[10px] text-slate-300 text-right">
            <div>🔋 ${v.battery}</div>
            <div class="text-cyan-300 font-bold">⚡ ${v.speed}</div>
          </div>
        </div>
      `).join("");

      repeatMarker.bindPopup(`
        <div class="p-2 min-w-[260px] max-w-[320px] text-slate-100 font-sans">
          <div class="flex items-center gap-1.5 font-black text-xs text-rose-400 mb-2 border-b border-white/10 pb-1.5">
            <span class="text-base">🔁</span>
            <span>Ushbu joydan bola ${passCount} marta o'tgan:</span>
          </div>
          <div class="space-y-1.5 max-h-56 overflow-y-auto custom-scroll">
            ${passesHtml}
          </div>
          <p class="text-[9px] text-slate-400 mt-2 font-mono">📍 ${c.latitude.toFixed(6)}, ${c.longitude.toFixed(6)} (santimetr aniqlik)</p>
        </div>
      `);
      repeatMarker.addTo(routeLayerGroup);
    } else {
      // Single pass intermediate point
      const v = c.visits[0];
      if (v.stepNum !== 1 && v.stepNum !== points.length) {
        const stepIcon = L.divIcon({
          className: "route-step-pin",
          html: `
            <div class="w-3.5 h-3.5 rounded-full bg-amber-400 border-2 border-slate-950 shadow-md hover:scale-150 transition cursor-pointer transform -translate-x-1/2 -translate-y-1/2" title="Qadam #${v.stepNum}"></div>
          `,
          iconSize: [0, 0],
          iconAnchor: [0, 0]
        });
        const stepMarker = L.marker([c.latitude, c.longitude], { icon: stepIcon });
        stepMarker.bindPopup(`
          <div class="p-1.5 text-xs text-slate-900 font-semibold font-sans">
            <div class="font-black text-blue-700">Qadam #${v.stepNum}</div>
            <div class="mt-0.5">🕒 Vaqti: <b>${v.time}</b></div>
            <div>🔋 Batareya: <b>${v.battery}</b></div>
            <div>⚡ Tezlik: <b>${v.speed}</b></div>
            <div class="text-[9px] text-slate-500 font-mono mt-1">${c.latitude.toFixed(6)}, ${c.longitude.toFixed(6)}</div>
          </div>
        `);
        stepMarker.addTo(routeLayerGroup);
      }
    }
  });

  if (isRouteVisible && map) {
    routeLayerGroup.addTo(map);
  }

  // 4. Calculate Route Stats & Populate Summary Cards
  calculateRouteStats(points);

  // 5. Populate Detailed History List with Repeat Badges & Zoom triggers
  renderHistoryListItems(points);
}

// Calculate total distance, max speed, stops, and steps
function calculateRouteStats(points) {
  if (!points || points.length === 0) {
    document.getElementById("statTotalDistance") && (document.getElementById("statTotalDistance").textContent = "0.0 km");
    document.getElementById("statTotalSteps") && (document.getElementById("statTotalSteps").textContent = "0 ta");
    document.getElementById("statMaxSpeed") && (document.getElementById("statMaxSpeed").textContent = "0 km/s");
    document.getElementById("statTotalStops") && (document.getElementById("statTotalStops").textContent = "0 joy");
    return;
  }

  let totalDistM = 0;
  let maxSpd = 0;
  let stops = 0;

  for (let i = 0; i < points.length - 1; i++) {
    const p1 = points[i];
    const p2 = points[i + 1];
    const d = calculateDistanceMeters(p1.latitude, p1.longitude, p2.latitude, p2.longitude);
    if (d < 50000) {
      totalDistM += d;
    }
    if (p2.speed && p2.speed > maxSpd) {
      maxSpd = Math.round(p2.speed);
    }
    if (p1.recorded_at && p2.recorded_at) {
      const t1 = new Date(p1.recorded_at.endsWith("Z") ? p1.recorded_at : p1.recorded_at + "Z").getTime();
      const t2 = new Date(p2.recorded_at.endsWith("Z") ? p2.recorded_at : p2.recorded_at + "Z").getTime();
      if ((t2 - t1) >= 300000 && d <= 30) { // 5 minutes stopped in one place
        stops++;
      }
    }
  }

  const distKm = (totalDistM / 1000).toFixed(1);
  const elDist = document.getElementById("statTotalDistance");
  const elSteps = document.getElementById("statTotalSteps");
  const elSpeed = document.getElementById("statMaxSpeed");
  const elStops = document.getElementById("statTotalStops");

  if (elDist) elDist.textContent = `${distKm} km`;
  if (elSteps) elSteps.textContent = `${points.length} ta`;
  if (elSpeed) elSpeed.textContent = `${maxSpd} km/s`;
  if (elStops) elStops.textContent = `${stops} joy`;
}

// Filter History List by Time of Day (All, Morning, Afternoon, Evening)
let currentHistoryFilter = "all";
function filterHistoryByTime(filterType) {
  haptic("light");
  currentHistoryFilter = filterType;

  document.querySelectorAll(".filter-time-pill").forEach((btn) => {
    btn.classList.remove("bg-blue-600", "text-white");
    btn.classList.add("bg-slate-800", "text-slate-300");
  });
  const activeBtn = document.getElementById(`btnFilterTime-${filterType}`);
  if (activeBtn) {
    activeBtn.classList.remove("bg-slate-800", "text-slate-300");
    activeBtn.classList.add("bg-blue-600", "text-white");
  }

  renderHistoryListItems(routePointsCache);
}

function renderHistoryListItems(points) {
  const container = document.getElementById("historyListContainer");
  if (!container || !points) return;

  let filtered = points.slice();
  if (currentHistoryFilter === "morning") {
    filtered = filtered.filter((p) => {
      if (!p.recorded_at) return true;
      const h = new Date(p.recorded_at.endsWith("Z") ? p.recorded_at : p.recorded_at + "Z").getHours();
      return h >= 6 && h < 12;
    });
  } else if (currentHistoryFilter === "afternoon") {
    filtered = filtered.filter((p) => {
      if (!p.recorded_at) return true;
      const h = new Date(p.recorded_at.endsWith("Z") ? p.recorded_at : p.recorded_at + "Z").getHours();
      return h >= 12 && h < 18;
    });
  } else if (currentHistoryFilter === "evening") {
    filtered = filtered.filter((p) => {
      if (!p.recorded_at) return true;
      const h = new Date(p.recorded_at.endsWith("Z") ? p.recorded_at : p.recorded_at + "Z").getHours();
      return h >= 18 || h < 6;
    });
  }

  if (filtered.length === 0) {
    container.innerHTML = `<p class="text-center text-xs text-slate-400 py-6">Ushbu vaqt oralig'ida harakatlar topilmadi.</p>`;
    return;
  }

  container.innerHTML = filtered
    .slice()
    .reverse()
    .map((p, idx) => {
      const origIdx = points.indexOf(p);
      let timeWithSec = "--:--:--";
      if (p.recorded_at) {
        let s = String(p.recorded_at).trim();
        if (!s.endsWith("Z") && !s.includes("+")) s += "Z";
        timeWithSec = new Date(s).toLocaleTimeString("uz-UZ", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      }
      const batteryStr = (p.battery_level !== null && p.battery_level !== undefined) ? `${p.battery_level}%` : "--";
      const chargingIcon = p.is_charging ? "⚡" : "";
      const speedStr = (typeof p.speed === "number" && p.speed > 0) ? `${Math.round(p.speed)} km/s` : "Piyoda";

      return `
        <div onclick="focusRoutePoint(${p.latitude}, ${p.longitude})" class="bg-slate-800/80 hover:bg-slate-800 active:scale-[0.99] border border-slate-700/60 hover:border-amber-500/50 p-2.5 rounded-2xl flex flex-col gap-1.5 text-xs transition cursor-pointer shadow-sm">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="w-5 h-5 rounded-full bg-amber-400/20 text-amber-300 font-black text-[10px] flex items-center justify-center border border-amber-400/40">#${origIdx + 1}</span>
              <span class="font-mono text-slate-200 text-xs font-bold">${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}</span>
            </div>
            <span class="font-mono font-black text-amber-400 text-xs">🕒 ${timeWithSec}</span>
          </div>
          <div class="flex items-center justify-between text-slate-400 text-[11px] pt-1 border-t border-slate-700/40">
            <div class="flex items-center gap-2">
              <span>🔋 ${batteryStr} ${chargingIcon}</span>
              <span>⚡ ${speedStr}</span>
            </div>
            <span class="text-cyan-400 font-bold text-[10px] flex items-center gap-1">📍 Xaritada ko'rish</span>
          </div>
        </div>
      `;
    })
    .join("");
}

// =========================================================
// 8. INTERACTIVE ROUTE PLAYBACK HUD & SCRUBBER ENGINE
// =========================================================
let replayCurrentStep = 0;
let replaySpeedMultiplier = 1; // 1x, 2x, 4x, 8x
let isReplayPlaying = false;

function showRoutePlaybackHud() {
  const hud = document.getElementById("routePlaybackHud");
  if (!hud) return;
  hud.classList.remove("hidden");
  if (window.lucide) lucide.createIcons();

  const scrubber = document.getElementById("routeTimelineScrubber");
  if (scrubber && routePointsCache.length > 0) {
    scrubber.min = "0";
    scrubber.max = String(routePointsCache.length - 1);
    scrubber.value = String(replayCurrentStep);
  }
  updateRoutePlaybackHud(replayCurrentStep);
}

function hideRoutePlaybackHud() {
  const hud = document.getElementById("routePlaybackHud");
  if (hud) hud.classList.add("hidden");
}

function updateRoutePlaybackHud(stepIdx) {
  if (!routePointsCache || routePointsCache.length === 0) return;
  const clampedIdx = Math.max(0, Math.min(stepIdx, routePointsCache.length - 1));
  const pt = routePointsCache[clampedIdx];

  const timeEl = document.getElementById("routeHudTimeText");
  const speedEl = document.getElementById("routeHudSpeedText");
  const statsEl = document.getElementById("routeHudStatsText");
  const scrubber = document.getElementById("routeTimelineScrubber");

  if (pt && pt.recorded_at) {
    const s = pt.recorded_at.endsWith("Z") ? pt.recorded_at : pt.recorded_at + "Z";
    const tStr = new Date(s).toLocaleTimeString("uz-UZ", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    if (timeEl) timeEl.textContent = `🕒 ${tStr}`;
  }
  if (speedEl) {
    const spd = typeof pt.speed === "number" && pt.speed > 0 ? `${Math.round(pt.speed)} km/s` : "0 km/s";
    speedEl.textContent = `⚡ ${spd}`;
  }
  if (statsEl) {
    statsEl.textContent = `${clampedIdx + 1} / ${routePointsCache.length} ta`;
  }
  if (scrubber) {
    scrubber.value = String(clampedIdx);
  }
}

function onRouteScrubberInput(val) {
  const idx = parseInt(val, 10);
  if (isNaN(idx) || !routePointsCache || !routePointsCache[idx]) return;
  replayCurrentStep = idx;

  ensureReplayMarkerAt(replayCurrentStep);
  updateRoutePlaybackHud(replayCurrentStep);
}

function stepRoutePrev() {
  haptic("light");
  if (replayCurrentStep > 0) {
    replayCurrentStep--;
    ensureReplayMarkerAt(replayCurrentStep);
    updateRoutePlaybackHud(replayCurrentStep);
  }
}

function stepRouteNext() {
  haptic("light");
  if (routePointsCache && replayCurrentStep < routePointsCache.length - 1) {
    replayCurrentStep++;
    ensureReplayMarkerAt(replayCurrentStep);
    updateRoutePlaybackHud(replayCurrentStep);
  }
}

function cycleRouteSpeed() {
  haptic("light");
  const speeds = [1, 2, 4, 8];
  const curIdx = speeds.indexOf(replaySpeedMultiplier);
  replaySpeedMultiplier = speeds[(curIdx + 1) % speeds.length];

  const btn = document.getElementById("btnRouteSpeed");
  if (btn) btn.textContent = `${replaySpeedMultiplier}x`;

  if (isReplayPlaying) {
    clearInterval(replayTimer);
    const intervalMs = Math.max(80, Math.round(500 / replaySpeedMultiplier));
    replayTimer = setInterval(routePlaybackTick, intervalMs);
  }
}

function ensureReplayMarkerAt(idx) {
  if (!routePointsCache || !routePointsCache[idx]) return;
  const pt = routePointsCache[idx];

  if (!replayMarker && map) {
    const replayIcon = L.divIcon({
      html: `
        <div class="relative transform -translate-x-1/2 -translate-y-1/2">
          <div class="w-8 h-8 rounded-full bg-gradient-to-tr from-amber-400 via-orange-500 to-rose-600 flex items-center justify-center text-white shadow-2xl border-2 border-white animate-pulse">
            🚶‍♂️
          </div>
          <span class="absolute -top-1 -right-1 w-3 h-3 bg-cyan-400 rounded-full border border-white animate-ping"></span>
        </div>
      `,
      className: "custom-replay-icon",
      iconSize: [0, 0],
      iconAnchor: [0, 0],
    });
    replayMarker = L.marker([pt.latitude, pt.longitude], { icon: replayIcon }).addTo(map);
  } else if (replayMarker) {
    smoothMoveMarker(replayMarker, pt.latitude, pt.longitude, 350);
  }

  if (map) {
    map.panTo([pt.latitude, pt.longitude], { animate: true, duration: 0.35 });
  }
}

function routePlaybackTick() {
  if (!routePointsCache || routePointsCache.length < 2) {
    clearInterval(replayTimer);
    isReplayPlaying = false;
    updatePlayPauseButtonUI();
    return;
  }

  replayCurrentStep++;
  if (replayCurrentStep >= routePointsCache.length) {
    clearInterval(replayTimer);
    isReplayPlaying = false;
    updatePlayPauseButtonUI();
    showToast("🏁 Harakat yakunlandi");
    return;
  }

  ensureReplayMarkerAt(replayCurrentStep);
  updateRoutePlaybackHud(replayCurrentStep);
}

function toggleRoutePlayPause() {
  haptic("medium");
  if (!routePointsCache || routePointsCache.length < 2) {
    showToast("Harakat nuqtalari yetarli emas");
    return;
  }

  if (isReplayPlaying) {
    clearInterval(replayTimer);
    isReplayPlaying = false;
  } else {
    if (replayCurrentStep >= routePointsCache.length - 1) {
      replayCurrentStep = 0;
    }
    isReplayPlaying = true;
    ensureReplayMarkerAt(replayCurrentStep);
    updateRoutePlaybackHud(replayCurrentStep);

    clearInterval(replayTimer);
    const intervalMs = Math.max(80, Math.round(500 / replaySpeedMultiplier));
    replayTimer = setInterval(routePlaybackTick, intervalMs);
  }

  updatePlayPauseButtonUI();
}

function updatePlayPauseButtonUI() {
  const icon = document.getElementById("iconRoutePlayPause");
  const label = document.getElementById("labelRoutePlayPause");
  const btn = document.getElementById("btnRoutePlayPause");

  if (isReplayPlaying) {
    if (icon) icon.setAttribute("data-lucide", "pause");
    if (label) label.textContent = "Pauza";
    if (btn) {
      btn.classList.remove("bg-amber-500", "hover:bg-amber-400");
      btn.classList.add("bg-rose-500", "hover:bg-rose-400");
    }
  } else {
    if (icon) icon.setAttribute("data-lucide", "play");
    if (label) label.textContent = replayCurrentStep > 0 ? "Davom" : "Boshlash";
    if (btn) {
      btn.classList.remove("bg-rose-500", "hover:bg-rose-400");
      btn.classList.add("bg-amber-500", "hover:bg-amber-400");
    }
  }
  if (window.lucide) lucide.createIcons();
}

function exitRouteView() {
  haptic("light");
  clearInterval(replayTimer);
  isReplayPlaying = false;
  if (replayMarker && map) {
    try { map.removeLayer(replayMarker); } catch (e) {}
    replayMarker = null;
  }
  hideRoutePlaybackHud();
  toggleRouteHistory(false);
}

// Render Demo History
function renderDemoHistory() {
  const demoPoints = [
    { latitude: 41.285800, longitude: 69.203500, battery_level: 85, is_charging: false, speed: 4, recorded_at: "2026-10-25T08:00:00" },
    { latitude: 41.287000, longitude: 69.206000, battery_level: 84, is_charging: false, speed: 5, recorded_at: "2026-10-25T08:10:00" },
    { latitude: 41.289000, longitude: 69.209000, battery_level: 83, is_charging: false, speed: 4, recorded_at: "2026-10-25T08:20:00" },
    { latitude: 41.291500, longitude: 69.212000, battery_level: 82, is_charging: false, speed: 3, recorded_at: "2026-10-25T08:30:00" },
    // Return trip through same coordinate (Repeated pass demonstration!)
    { latitude: 41.287000, longitude: 69.206000, battery_level: 75, is_charging: false, speed: 4, recorded_at: "2026-10-25T13:40:00" },
    { latitude: 41.285800, longitude: 69.203500, battery_level: 70, is_charging: false, speed: 3, recorded_at: "2026-10-25T14:00:00" },
    // 3rd pass in evening!
    { latitude: 41.287000, longitude: 69.206000, battery_level: 60, is_charging: false, speed: 5, recorded_at: "2026-10-25T17:15:00" },
    { latitude: 41.300000, longitude: 69.225000, battery_level: 55, is_charging: false, speed: 6, recorded_at: "2026-10-25T17:45:00" },
  ];
  renderHistoryToMapAndList(demoPoints);
}

// Smooth Route Playback Animation Entrypoint
function startRoutePlayback() {
  haptic("medium");
  if (!routePointsCache || routePointsCache.length < 2) {
    showToast("Harakat nuqtalari yetarli emas");
    return;
  }

  closeModal("history");
  if (currentRole === "parent" && currentTab !== "map") {
    switchTab("map");
  }
  toggleMapUiVisibility(true); // Automatically switch to fullscreen mode!

  // Reveal the interactive Scrubber HUD
  showRoutePlaybackHud();

  // Reset and start playback
  replayCurrentStep = 0;
  toggleRoutePlayPause();
}

// Toggle route history polyline on map
async function toggleRouteHistory(forceState) {
  haptic("light");
  const newState = typeof forceState === "boolean" ? forceState : !isRouteVisible;
  isRouteVisible = newState;

  if (currentRole === "parent" && currentTab !== "map") {
    switchTab("map");
  }

  const btn = document.getElementById("btnToggleRoute");

  if (isRouteVisible) {
    toggleMapUiVisibility(true);
    if (btn) {
      btn.classList.add("text-amber-400");
      btn.classList.remove("text-slate-500");
    }
    showToast("🗺️ Marshrut xaritada ochilmoqda...");
    await loadRouteHistory(currentChildId);
    if (routeLayerGroup && map) {
      if (!map.hasLayer(routeLayerGroup)) {
        routeLayerGroup.addTo(map);
      }
      try {
        const bounds = routeLayerGroup.getBounds();
        if (bounds.isValid()) {
          map.fitBounds(bounds.pad(0.15));
        }
      } catch (e) {}
    }
    showRoutePlaybackHud();
    showToast("🗺️ Marshrut to'liq xaritada ochildi");
  } else {
    if (routeLayerGroup && map) {
      try { map.removeLayer(routeLayerGroup); } catch (e) {}
    }
    if (btn) {
      btn.classList.remove("text-amber-400");
      btn.classList.add("text-slate-500");
    }
    hideRoutePlaybackHud();
    showToast("Marshrut yashirildi");
  }
}

// Battery UI styling
function updateBatteryUI(level, isCharging) {
  const pill = document.getElementById("batteryPill");
  const text = document.getElementById("batteryText");
  const dynBatText = document.getElementById("dynamicIslandBatteryText");

  if (level === null || level === undefined) {
    if (text) text.textContent = "--%";
    if (dynBatText) dynBatText.textContent = "--%";
    return;
  }

  if (text) text.textContent = `${level}%`;
  if (dynBatText) dynBatText.textContent = `${level}%`;

  if (isCharging) {
    pill.className = "flex items-center gap-1 bg-blue-900/50 px-2 py-1 rounded-full text-xs font-bold text-blue-400 border border-blue-500/50";
  } else if (level <= 15) {
    pill.className = "flex items-center gap-1 bg-red-900/60 px-2 py-1 rounded-full text-xs font-bold text-red-400 border border-red-500/60 animate-pulse";
  } else if (level <= 30) {
    pill.className = "flex items-center gap-1 bg-amber-900/50 px-2 py-1 rounded-full text-xs font-semibold text-amber-400 border border-amber-500/50";
  } else {
    pill.className = "flex items-center gap-1 bg-emerald-900/40 px-2 py-1 rounded-full text-xs font-semibold text-emerald-400 border border-emerald-500/40";
  }
}

// Sound mode UI styling
function updateSoundUI(mode) {
  const text = document.getElementById("soundText");
  if (mode === "silent") {
    text.textContent = "Ovozsiz";
  } else if (mode === "vibrate") {
    text.textContent = "Vibratsiya";
  } else {
    text.textContent = "Oddiy";
  }
}

// Format relative time with strict UTC timezone parsing (fixes 5-hour Tashkent UTC+5 offset)
function formatTimeAgo(isoString) {
  if (!isoString) return "Hozirgina";
  let s = String(isoString).trim();
  // If timestamp doesn't include timezone offset, append 'Z' because database stores UTC
  if (!s.endsWith("Z") && !s.includes("+") && !/[+-]\d{2}:\d{2}$/.test(s)) {
    s += "Z";
  }
  const date = new Date(s);
  const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
  if (isNaN(diffSec) || diffSec < 60) return "Hozirgina";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} daqiqa oldin`;
  return `${Math.floor(diffSec / 3600)} soat oldin`;
}

// SOS Alert Banner handling
function toggleSosBanner(isActive) {
  const banner = document.getElementById("sosAlertBanner");
  const dynIslandText = document.getElementById("dynamicIslandText");
  const dynIslandDot = document.getElementById("dynamicIslandDot");
  if (!banner) return;

  if (isActive) {
    banner.classList.remove("hidden");
    if (dynIslandText) dynIslandText.textContent = "🚨 SOS FAVQULODDA!";
    if (dynIslandDot) dynIslandDot.className = "dynamic-island-dot bg-red-500 animate-ping";
    if (!lastSosAlertState) {
      haptic("heavy");
      playSirenSound();
      showToast("🚨 SOS: Farzandingiz xavf signalini yoqdi!");
    }
    lastSosAlertState = true;
  } else {
    banner.classList.add("hidden");
    lastSosAlertState = false;
    if (dynIslandText && !walkSafeActive) {
      dynIslandText.textContent = "Jonli GPS faol";
      if (dynIslandDot) dynIslandDot.className = "dynamic-island-dot";
    }
  }
}

function dismissSos() {
  const banner = document.getElementById("sosAlertBanner");
  if (banner) banner.classList.add("hidden");
}

async function resolveCurrentSos() {
  haptic("medium");
  const banner = document.getElementById("sosAlertBanner");
  if (banner) banner.classList.add("hidden");
  lastSosAlertState = false;

  const child = childrenList.find((c) => c.id == currentChildId);
  if (child) {
    child.is_sos_active = false;
  }
  renderChildrenSwitcher();

  if (!isDemoMode && currentChildId) {
    try {
      await fetch(`/api/v1/webapp/child/${currentChildId}/sos/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      showToast("✅ Farzand SOS xavf signali to'xtatildi.");
    } catch (e) {
      console.error("Resolve SOS error:", e);
    }
  } else {
    showToast("✅ (Demo) SOS signali to'xtatildi.");
  }

  // Restore marker to normal pulse
  if (childMarker) {
    const pos = childMarker.getLatLng();
    renderChildMarker(pos.lat, pos.lng, child?.full_name || "Farzand", false, child?.location?.battery_level || null);
  }
}

// Bottom tab switching

// Toggle Fullscreen Map Mode (Hide/Show Top Nav & Bottom HUD)

// Toggle & Re-close Mobile Extra Map Tools Dock (Instant 1-tap response, zero DOM recreation)
let lastExtraToolsToggleTimestamp = 0;

function closeExtraMapTools() {
  const tools = document.getElementById("extraMapTools");
  const btn = document.getElementById("btnToggleExtraTools");
  const wrapDots = document.getElementById("iconWrapMoreDots");
  const wrapClose = document.getElementById("iconWrapMoreClose");
  if (!tools || tools.classList.contains("hidden")) return;

  tools.classList.add("hidden");
  if (wrapDots) wrapDots.classList.remove("hidden");
  if (wrapClose) wrapClose.classList.add("hidden");
  if (btn) {
    btn.setAttribute("title", "Boshqa asboblarni ochish");
    btn.classList.remove("text-rose-400", "bg-rose-500/20", "border-rose-400/40");
    btn.classList.add("text-slate-300");
  }
}

function closeExtraMapToolsOnMobile() {
  if (window.innerWidth < 768) {
    closeExtraMapTools();
  }
}

function toggleExtraMapTools(event) {
  if (event) {
    if (typeof event.stopPropagation === "function") event.stopPropagation();
    if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
    if (typeof event.preventDefault === "function") event.preventDefault();
  }
  lastExtraToolsToggleTimestamp = Date.now();
  haptic("light");

  const tools = document.getElementById("extraMapTools");
  const btn = document.getElementById("btnToggleExtraTools");
  const wrapDots = document.getElementById("iconWrapMoreDots");
  const wrapClose = document.getElementById("iconWrapMoreClose");
  if (!tools) return;

  const isCurrentlyHidden = tools.classList.contains("hidden");

  if (isCurrentlyHidden) {
    // OPEN INSTANTLY on first click
    tools.classList.remove("hidden");
    if (wrapDots) wrapDots.classList.add("hidden");
    if (wrapClose) wrapClose.classList.remove("hidden");
    if (btn) {
      btn.setAttribute("title", "Yopish (Asboblarni yashirish)");
      btn.classList.remove("text-slate-300");
      btn.classList.add("text-rose-400", "bg-rose-500/20", "border-rose-400/40");
    }
  } else {
    // CLOSE
    tools.classList.add("hidden");
    if (wrapDots) wrapDots.classList.remove("hidden");
    if (wrapClose) wrapClose.classList.add("hidden");
    if (btn) {
      btn.setAttribute("title", "Boshqa asboblarni ochish");
      btn.classList.remove("text-rose-400", "bg-rose-500/20", "border-rose-400/40");
      btn.classList.add("text-slate-300");
    }
  }
}

// Global outside-click dismiss for Extra Map Tools (Ignores clicks inside dock and recent toggles)
document.addEventListener("click", function(event) {
  // If toggled less than 400ms ago, ignore outside click completely (prevents instant auto-dismiss)
  if (Date.now() - lastExtraToolsToggleTimestamp < 400) {
    return;
  }
  const dock = document.getElementById("floatingMapControls");
  if (!dock) return;
  if (dock.contains(event.target) || (event.target && event.target.closest && event.target.closest("#floatingMapControls")) || (event.composedPath && event.composedPath().includes(dock))) {
    return;
  }
  closeExtraMapTools();
});


// Parent requests live location from child with immediate push/alert
async function requestChildLocationPing() {
  haptic("medium");
  if (isDemoMode) {
    showToast("📍 Demo rejimida farzand Toshkent markazida ko'rsatilmoqda.");
    return;
  }
  if (!currentChildId) {
    showToast("Farzand tanlanmagan");
    return;
  }
  try {
    showToast("Farzand telefoniga joylashuv so'rovi yuborilmoqda...");
    const res = await fetch(`/api/v1/webapp/child/${currentChildId}/active-check`, {
      method: "POST"
    });
    if (res.ok) {
      showToast("🔔 Farzandingizga joylashuvni yuborish haqida eslatma yuborildi!");
    } else {
      showToast("Eslatma yuborildi. Farzandingiz ilovaga kirib ruxsat berishi kerak.");
    }
  } catch (e) {
    showToast("Eslatma yuborildi. Farzandingiz ilovaga kirib ruxsat berishi kerak.");
  }
}

function toggleMapUiVisibility(forceState) {
  haptic("light");
  const isCurrentlyFullscreen = document.body.classList.contains("fullscreen-map-mode");
  const newState = typeof forceState === "boolean" ? forceState : !isCurrentlyFullscreen;

  const btnRestore = document.getElementById("btnRestoreMenus");
  const icon = document.getElementById("iconFullscreenMap");
  const btnDock = document.getElementById("btnToggleFullscreenMap");

  if (newState) {
    document.body.classList.add("fullscreen-map-mode");
    if (btnRestore) btnRestore.classList.remove("hidden");
    if (icon) icon.setAttribute("data-lucide", "minimize-2");
    if (btnDock) {
      btnDock.setAttribute("title", "Menyularni ko'rsatish");
      btnDock.classList.add("text-rose-400");
      btnDock.classList.remove("text-cyan-400");
    }
    showToast("🗺️ Xarita to'liq kengaytirildi. Menyuni ochish uchun pastdagi tugmani bosing.");
  } else {
    document.body.classList.remove("fullscreen-map-mode");
    if (btnRestore) btnRestore.classList.add("hidden");
    if (icon) icon.setAttribute("data-lucide", "maximize-2");
    if (btnDock) {
      btnDock.setAttribute("title", "To'liq xarita (Menyularni yashirish)");
      btnDock.classList.remove("text-rose-400");
      btnDock.classList.add("text-cyan-400");
    }
  }

  if (window.lucide) {
    window.lucide.createIcons();
  }

  // Ensure Leaflet map recalculates its full viewport immediately and through CSS transitions
  const activeMap = map || window.map;
  if (activeMap) {
    window.map = activeMap;
    activeMap.invalidateSize({ pan: false });
    [50, 150, 300, 450].forEach((delay) => {
      setTimeout(() => {
        try {
          activeMap.invalidateSize({ pan: false });
        } catch (e) {}
      }, delay);
    });
    setTimeout(() => {
      try {
        if (childMarker) {
          activeMap.panTo(childMarker.getLatLng(), { animate: true, duration: 0.5 });
        }
      } catch (e) {}
    }, 320);
  }
}

function switchTab(tabName) {
  if (tabName !== "map" && document.body.classList.contains("fullscreen-map-mode")) {
    toggleMapUiVisibility(false);
  }
  haptic("light");

  // In child mode, Tab 4 routes to child wallet
  if (currentRole === "child" && tabName === "history") {
    tabName = "wallet";
  }

  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.classList.remove("text-blue-400", "active");
    btn.classList.add("text-slate-400");
  });

  let activeBtn = document.getElementById(`tabBtn-${tabName}`);
  if (!activeBtn && tabName === "wallet") {
    activeBtn = document.getElementById("tabBtn-history");
  }
  if (activeBtn) {
    activeBtn.classList.add("text-blue-400", "active");
    activeBtn.classList.remove("text-slate-400");
  }

  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.add("hidden");
  });

  const floatControls = document.getElementById("floatingMapControls");
  const childActions = document.getElementById("childFloatingActions");
  const locCard = document.getElementById("locationCard");
  const parentBar = document.getElementById("parentMonitoringBar");
  const bottomContainer = document.getElementById("parentBottomContainer");

  if (tabName !== "map") {
    const targetModal = document.getElementById(`modal-${tabName}`);
    if (targetModal) targetModal.classList.remove("hidden");
    if (floatControls) floatControls.classList.add("hidden");
    if (childActions) childActions.classList.add("hidden");
    if (locCard) locCard.classList.add("hidden");
    if (parentBar) parentBar.classList.add("hidden");
    if (bottomContainer) bottomContainer.classList.add("hidden");
    if (tabName === "tasks") {
      loadTasks();
    }
    if (tabName === "wallet") {
      loadWishlist(currentChildId);
    }
    if (tabName === "zones") {
      const activeChild = currentChildId || (authenticatedSession?.user?.id);
      if (activeChild) {
        loadGeofences(activeChild);
      }
      const addZoneSec = document.getElementById("btnAddZoneSection");
      const modalTitle = document.getElementById("zonesModalTitle");
      if (currentRole === "child") {
        if (addZoneSec) addZoneSec.classList.add("hidden");
        if (modalTitle) modalTitle.textContent = "Mening Xavfsiz Makonlarim";
      } else {
        if (addZoneSec) addZoneSec.classList.remove("hidden");
        if (modalTitle) modalTitle.textContent = "Kerakli Joylar (Geozonalar)";
      }
    }
    if (tabName === "history") {
      const activeChild = currentChildId || (authenticatedSession?.user?.id);
      if (activeChild) {
        loadRouteHistory(activeChild);
      }
    }
    if (tabName === "settings") {
      setGlobalTheme(currentTheme, false);
      setGlobalLanguage(currentLanguage, false);
    }
    updateTelegramBackButton();
  } else {
    updateTelegramBackButton();
    // Tab 1 (Map for parent / Adventure Hub for child)
    if (currentRole === "child") {
      document.getElementById("childViewSection")?.classList.remove("hidden");
      document.getElementById("parentViewSection")?.classList.add("hidden");
      document.getElementById("btnChildBackFromMap")?.classList.add("hidden");
      if (childActions) childActions.classList.remove("hidden");
    } else {
      document.getElementById("parentViewSection")?.classList.remove("hidden");
      document.getElementById("childViewSection")?.classList.add("hidden");
      if (floatControls) floatControls.classList.remove("hidden");
      if (locCard) locCard.classList.remove("hidden");
      document.getElementById("parentMonitoringBar")?.classList.remove("hidden");
      document.getElementById("parentBottomContainer")?.classList.remove("hidden");
      if (map) {
        setTimeout(() => map.invalidateSize(), 80);
      }
    }
  }
}

function openChildMapModal() {
  haptic("medium");
  const childSec = document.getElementById("childViewSection");
  const parentSec = document.getElementById("parentViewSection");
  if (childSec) childSec.classList.add("hidden");
  if (parentSec) parentSec.classList.remove("hidden");

  let backBtn = document.getElementById("btnChildBackFromMap");
  if (!backBtn && parentSec) {
    backBtn = document.createElement("button");
    backBtn.id = "btnChildBackFromMap";
    backBtn.className = "absolute top-3.5 left-3.5 z-20 px-3 py-2 bg-gradient-to-r from-purple-700 to-indigo-600 text-white font-bold text-xs rounded-xl shadow-xl border border-purple-400/50 flex items-center gap-1.5 active:scale-95 transition backdrop-blur";
    backBtn.innerHTML = `<i data-lucide="arrow-left" class="w-4 h-4"></i> <span>Sarguzashtga qaytish</span>`;
    backBtn.onclick = () => {
      haptic("light");
      switchTab("map");
    };
    parentSec.appendChild(backBtn);
  }
  if (backBtn) backBtn.classList.remove("hidden");

  if (map) {
    setTimeout(() => {
      map.invalidateSize();
      centerOnChild();
    }, 100);
  }
  if (window.lucide) lucide.createIcons();
}

function closeChildMapModal() {
  haptic("light");
  const childSec = document.getElementById("childViewSection");
  const parentSec = document.getElementById("parentViewSection");
  if (childSec) childSec.classList.remove("hidden");
  if (parentSec) parentSec.classList.add("hidden");

  const backBtn = document.getElementById("btnChildBackFromMap");
  if (backBtn) backBtn.classList.add("hidden");

  updateTelegramBackButton();
  if (window.lucide) lucide.createIcons();
}
window.closeChildMapModal = closeChildMapModal;

function renderChildDashboardQuests() {
  const container = document.getElementById("childDashboardTasksList");
  if (!container) return;

  if (!isDemoMode && (!tasksList || tasksList.length === 0)) {
    container.innerHTML = `
      <div class="p-3.5 bg-slate-950/40 rounded-2xl border border-slate-800/60 text-center">
        <span class="text-xl">🎯</span>
        <p class="text-xs font-bold text-slate-300 mt-1">Hozircha topshiriqlar yo'q</p>
        <p class="text-[10px] text-slate-400 mt-0.5">Ota-onangiz yangi topshiriq berganda bu yerda ko'rinadi</p>
      </div>
    `;
    return;
  }

  const activeTasks = (tasksList && tasksList.length > 0)
    ? tasksList.slice(0, 3)
    : (isDemoMode ? [
        { id: 901, title: "Matematika darslarini bajarish", reward_coins: 5000, status: "pending" },
        { id: 902, title: "Xonani tozalash va tartibga keltirish", reward_coins: 3000, status: "scheduled", promised_time: "30 daqiqadan keyin" },
        { id: 903, title: "Ingliz tili yangi lug'at yodlash", reward_coins: 4000, status: "completed_by_child" }
      ] : []);

  if (activeTasks.length === 0) {
    container.innerHTML = `
      <div class="p-3.5 bg-slate-950/40 rounded-2xl border border-slate-800/60 text-center">
        <span class="text-xl">🎯</span>
        <p class="text-xs font-bold text-slate-300 mt-1">Hozircha topshiriqlar yo'q</p>
        <p class="text-[10px] text-slate-400 mt-0.5">Ota-onangiz yangi topshiriq berganda bu yerda ko'rinadi</p>
      </div>
    `;
    return;
  }

  container.innerHTML = activeTasks.map(t => {
    const coins = t.reward_coins || 1000;
    const moneyEquivalent = Math.round(coins * (coinRateSom / Math.max(1, coinRateCoins)));
    const statusText = t.status === "completed_by_child" ? "⏳ Tekshiruvda" : (t.status === "approved" ? "🏆 Bajarildi" : (t.status === "scheduled" ? "⏰ Rejalangan" : "⚡ Faol"));
    const statusClass = t.status === "completed_by_child" ? "bg-amber-500/20 text-amber-300 border-amber-500/30" : (t.status === "approved" ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/30" : "bg-blue-500/20 text-blue-300 border-blue-500/30");

    return `
      <div class="p-3 bg-slate-950/60 rounded-2xl border border-slate-800 flex items-center justify-between gap-2 hover:border-slate-700 transition">
        <div class="min-w-0">
          <div class="flex items-center gap-1.5">
            <span class="text-xs font-bold text-white truncate">${t.title || 'Missiya'}</span>
            <span class="text-[9px] px-1.5 py-0.5 rounded-full font-bold border ${statusClass}">${statusText}</span>
          </div>
          <div class="flex items-center gap-1.5 mt-1.5 flex-wrap">
            <span class="text-[10px] sm:text-[11px] text-amber-300 font-black flex items-center gap-1 bg-amber-500/15 border border-amber-500/30 px-2 py-0.5 rounded-lg shadow-sm">
              <span>🪙 +${coins.toLocaleString()} tanga</span>
            </span>
            <span class="text-[10px] sm:text-[11px] text-emerald-300 font-black flex items-center gap-1 bg-emerald-500/15 border border-emerald-500/30 px-2 py-0.5 rounded-lg shadow-sm">
              <span>💵 +${moneyEquivalent.toLocaleString()} so'm</span>
            </span>
          </div>
        </div>
        <button onclick="switchTab('tasks')" class="px-2.5 py-1.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 text-white rounded-xl text-xs font-bold shrink-0 transition active:scale-95 shadow">
          Bajarish
        </button>
      </div>
    `;
  }).join("");
}

function renderChildDashboardCastles() {
  const container = document.getElementById("childDashboardZonesList");
  if (!container) return;

  if (!isDemoMode && (!geofencesList || geofencesList.length === 0)) {
    container.innerHTML = `
      <div class="p-3 bg-slate-950/40 rounded-2xl border border-slate-800/60 text-center col-span-2">
        <span class="text-lg">🛡️</span>
        <p class="text-xs font-bold text-slate-300 mt-0.5">Xavfsiz makonlar hali belgilanmagan</p>
        <p class="text-[10px] text-slate-400">Ota-onangiz xaritada xavfsiz hudud kiritganda chiqadi</p>
      </div>
    `;
    return;
  }

  const places = (geofencesList && geofencesList.length > 0)
    ? geofencesList
    : (isDemoMode ? [
        { name: "Uyimiz (Asosiy Baza 🏡)", radius_meters: 200 },
        { name: "Maktab №45 (Bilim Maskani 🏫)", radius_meters: 250 },
        { name: "O'quv Markazi (Zukko Hub 📚)", radius_meters: 200 }
      ] : []);

  if (places.length === 0) {
    container.innerHTML = `
      <div class="p-3 bg-slate-950/40 rounded-2xl border border-slate-800/60 text-center col-span-2">
        <span class="text-lg">🛡️</span>
        <p class="text-xs font-bold text-slate-300 mt-0.5">Xavfsiz makonlar hali belgilanmagan</p>
        <p class="text-[10px] text-slate-400">Ota-onangiz xaritada xavfsiz hudud kiritganda chiqadi</p>
      </div>
    `;
    return;
  }

  const placeEmojis = ["🏡", "🏫", "📚", "🥋", "📍", "🛡️"];

  container.innerHTML = places.map((c, i) => {
    let icon = placeEmojis[i % placeEmojis.length];
    const n = (c.name || "").toLowerCase();
    if (n.includes("uy") || n.includes("dom") || n.includes("home")) icon = "🏡";
    else if (n.includes("maktab") || n.includes("shkola") || n.includes("school")) icon = "🏫";
    else if (n.includes("kurs") || n.includes("o'quv") || n.includes("dars")) icon = "📚";
    else if (n.includes("bog'cha") || n.includes("sadik")) icon = "🧸";
    else if (n.includes("sport") || n.includes("zal")) icon = "🥋";
    else if (n.includes("bobo") || n.includes("buvi") || n.includes("ota")) icon = "👵";

    return `
      <div class="bg-slate-950/60 p-2.5 rounded-2xl border border-slate-800 flex items-center gap-2">
        <span class="text-xl shrink-0">${icon}</span>
        <div class="min-w-0">
          <p class="font-bold text-white text-xs truncate">${c.name}</p>
          <p class="text-[10px] text-emerald-400 font-medium">Xavfsiz (${Math.round(c.radius_meters || 100)}m)</p>
        </div>
      </div>
    `;
  }).join("");
}

function closeModal(tabName) {
  haptic("light");
  const modal = document.getElementById(`modal-${tabName}`);
  if (modal) modal.classList.add("hidden");
  switchTab("map");
  updateTelegramBackButton();
}

// Interactive Map Zone Picker Engine (Direct Map Selection & Live Visual Radius)
function openAddZoneModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("medium");

  // 1. Close list modal if open
  const listModal = document.getElementById("modal-zones");
  if (listModal) listModal.classList.add("hidden");

  // 2. Ensure map tab is active and visible in Fullscreen Mode
  switchTab("map");
  toggleMapUiVisibility(true);
  isZonePickerActive = true;

  // 3. Hide standard HUD controls so map is clean and clear
  const floatControls = document.getElementById("floatingMapControls");
  if (floatControls) floatControls.classList.add("hidden");
  const bottomContainer = document.getElementById("parentBottomContainer");
  if (bottomContainer) bottomContainer.classList.add("hidden");
  document.getElementById("locationCard")?.classList.add("hidden");
  document.getElementById("parentMonitoringBar")?.classList.add("hidden");
  document.getElementById("mainNavBar")?.classList.add("hidden");

  // 4. Show Zone Picker Top Bar & Bottom Dock
  const topBar = document.getElementById("zonePickerTopBar");
  const bottomDock = document.getElementById("zonePickerBottomDock");
  if (topBar) topBar.classList.remove("hidden");
  if (bottomDock) bottomDock.classList.remove("hidden");
  if (window.lucide) lucide.createIcons();

  // 5. Determine initial position: use child location if available, otherwise map center
  let initLat = DEFAULT_LAT;
  let initLng = DEFAULT_LON;
  if (childMarker) {
    const pos = childMarker.getLatLng();
    initLat = pos.lat;
    initLng = pos.lng;
  } else if (map) {
    const center = map.getCenter();
    initLat = center.lat;
    initLng = center.lng;
  }

  const radiusInput = document.getElementById("inputZoneRadius");
  const initRadius = radiusInput ? parseFloat(radiusInput.value) || 200 : 200;

  // Center map on initial point and place interactive pin & circle
  if (map) {
    map.setView([initLat, initLng], Math.max(map.getZoom(), 15), { animate: true });
    setTimeout(() => map.invalidateSize(), 150);
  }

  setZonePickerLocation(initLat, initLng, initRadius);

  const nameInput = document.getElementById("inputZoneName");
  if (nameInput) {
    nameInput.value = "";
    setTimeout(() => nameInput.focus(), 300);
  }
}

function closeAddZoneModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  isZonePickerActive = false;

  // 1. Hide Zone Picker HUD
  const topBar = document.getElementById("zonePickerTopBar");
  const bottomDock = document.getElementById("zonePickerBottomDock");
  if (topBar) topBar.classList.add("hidden");
  if (bottomDock) bottomDock.classList.add("hidden");

  // 2. Remove picker circle & marker from map
  if (previewCircle && map) {
    map.removeLayer(previewCircle);
    previewCircle = null;
  }
  if (zonePickerMarker && map) {
    map.removeLayer(zonePickerMarker);
    zonePickerMarker = null;
  }

  // 3. Restore normal HUD cards
  const floatControls = document.getElementById("floatingMapControls");
  if (floatControls) floatControls.classList.remove("hidden");
  const bottomContainer = document.getElementById("parentBottomContainer");
  if (bottomContainer) bottomContainer.classList.remove("hidden");
  document.getElementById("locationCard")?.classList.remove("hidden");
  document.getElementById("parentMonitoringBar")?.classList.remove("hidden");
  document.getElementById("mainNavBar")?.classList.remove("hidden");
}

function setZonePickerLocation(lat, lon, radius = null) {
  if (!map) return;
  if (!radius) {
    const radiusInput = document.getElementById("inputZoneRadius");
    radius = radiusInput ? parseFloat(radiusInput.value) || 200 : 200;
  }

  // 1. Update or create interactive draggable Leaflet marker
  if (zonePickerMarker) {
    zonePickerMarker.setLatLng([lat, lon]);
  } else {
    const pinIcon = L.divIcon({
      className: "custom-zone-picker-pin",
      html: `
        <div style="transform: translate(-50%, -100%); display: flex; flex-direction: column; align-items: center; pointer-events: auto;">
          <div style="background: linear-gradient(135deg, #10b981, #059669); color: white; padding: 6px 12px; border-radius: 12px; font-weight: 900; font-size: 11px; box-shadow: 0 8px 24px rgba(16, 185, 129, 0.6); border: 2px solid white; display: flex; align-items: center; gap: 4px; white-space: nowrap;">
            <span>📍 Xavfsiz Makon</span>
          </div>
          <div style="width: 4px; height: 14px; background: #10b981; border-radius: 2px;"></div>
          <div style="width: 12px; height: 12px; border-radius: 50%; background: #10b981; border: 2px solid white; box-shadow: 0 0 10px #10b981;"></div>
        </div>
      `,
      iconSize: [0, 0],
      iconAnchor: [0, 0]
    });

    zonePickerMarker = L.marker([lat, lon], {
      icon: pinIcon,
      draggable: true,
      autoPan: true
    }).addTo(map);

    zonePickerMarker.on("drag", (e) => {
      const pos = e.target.getLatLng();
      if (previewCircle) previewCircle.setLatLng(pos);
    });

    zonePickerMarker.on("dragend", (e) => {
      const pos = e.target.getLatLng();
      setZonePickerLocation(pos.lat, pos.lng);
    });
  }

  // 2. Update or create preview circle on map
  if (previewCircle) {
    previewCircle.setLatLng([lat, lon]);
    previewCircle.setRadius(radius);
  } else {
    previewCircle = L.circle([lat, lon], {
      color: "#10b981",
      fillColor: "#10b981",
      fillOpacity: 0.22,
      weight: 2.5,
      dashArray: "6, 6",
      radius: radius,
    }).addTo(map);
  }

  // 3. Reverse geocode address to show parent what street/building they picked
  reverseGeocodePickerLocation(lat, lon);
}

function updateRadiusPreview(val) {
  const radiusNum = parseFloat(val) || 200;
  const display = document.getElementById("zoneRadiusDisplay");
  if (display) {
    display.textContent = radiusNum >= 1000 ? `${(radiusNum / 1000).toFixed(1)} km (${Math.round(radiusNum)} m)` : `${Math.round(radiusNum)} metr`;
  }
  if (previewCircle) {
    previewCircle.setRadius(radiusNum);
  }
}

function setZoneRadiusVal(val) {
  haptic("light");
  const input = document.getElementById("inputZoneRadius");
  if (input) {
    input.value = val;
    updateRadiusPreview(val);
  }
}

function setZoneNameTag(name) {
  haptic("light");
  const input = document.getElementById("inputZoneName");
  if (input) {
    input.value = name;
  }
}

function centerPickerOnChild() {
  haptic("medium");
  if (childMarker) {
    const pos = childMarker.getLatLng();
    map.flyTo(pos, 16, { duration: 1.0 });
    setZonePickerLocation(pos.lat, pos.lng);
    showToast("📍 Markaz farzandingiz joriy joylashuviga ko'chirildi");
  } else {
    showToast("Farzand joylashuvi hali aniqlanmagan");
  }
}

async function reverseGeocodePickerLocation(lat, lon) {
  const addrText = document.getElementById("zonePickerAddressText");
  if (!addrText) return;

  const key = `${lat.toFixed(4)},${lon.toFixed(4)}`;
  if (addressCache.has(key)) {
    addrText.textContent = addressCache.get(key);
    return;
  }

  addrText.textContent = `${lat.toFixed(4)}, ${lon.toFixed(4)} (Manzil aniqlanmoqda...)`;

  try {
    const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lon}&zoom=18&addressdetails=1`, {
      headers: { "Accept-Language": "uz,ru,en" }
    });
    if (res.ok) {
      const data = await res.json();
      if (data && data.display_name) {
        const parts = data.display_name.split(",");
        const clean = parts.slice(0, 3).join(",").trim();
        addressCache.set(key, clean);
        addrText.textContent = clean;
        return;
      }
    }
  } catch (e) {}

  addrText.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

// Submit newly customized Kerakli Joy to server
async function submitNewZone() {
  const nameInput = document.getElementById("inputZoneName");
  const name = nameInput ? nameInput.value.trim() : "";
  if (!name) {
    haptic("warning");
    showToast("⚠️ Iltimos, joy nomini kiriting (masalan: Uyimiz, Maktab)!");
    if (nameInput) nameInput.focus();
    return;
  }

  const radiusInput = document.getElementById("inputZoneRadius");
  const radius = radiusInput ? parseFloat(radiusInput.value) || 200 : 200;

  let centerLat = DEFAULT_LAT;
  let centerLng = DEFAULT_LON;
  if (zonePickerMarker) {
    const pos = zonePickerMarker.getLatLng();
    centerLat = pos.lat;
    centerLng = pos.lng;
  } else if (previewCircle) {
    const pos = previewCircle.getLatLng();
    centerLat = pos.lat;
    centerLng = pos.lng;
  } else if (map) {
    const c = map.getCenter();
    centerLat = c.lat;
    centerLng = c.lng;
  }

  const btn = document.getElementById("btnSubmitZone");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-pulse">Saqlanmoqda...</span>`;
  }

  if (isDemoMode) {
    haptic("success");
    playSuccessChime();
    showToast(`✅ Kerakli joy saqlandi: ${name} (${Math.round(radius)}m)`);
    geofencesList.push({
      id: Date.now(),
      name: name,
      latitude: centerLat,
      longitude: centerLng,
      radius_meters: radius,
      is_active: true
    });
    renderFencesToMapAndList(geofencesList);
    closeAddZoneModal();
    return;
  }

  try {
    const effectiveParentId = parentId || authenticatedSession?.user?.id;
    const res = await fetch("/api/v1/webapp/geofence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parent_id: parseInt(effectiveParentId),
        name: name,
        latitude: centerLat,
        longitude: centerLng,
        radius_meters: radius,
        child_id: currentChildId ? parseInt(currentChildId) : null,
      }),
    });

    const data = await res.json();
    if (res.ok && data.status === "success") {
      haptic("success");
      playSuccessChime();
      showToast(`✅ '${name}' xavfsiz makon sifatida saqlandi (${Math.round(radius)}m)!`);
      closeAddZoneModal();
      if (currentChildId) {
        await loadGeofences(currentChildId);
      }
    } else {
      throw new Error(data.detail || "Saqlashda xatolik yuz berdi");
    }
  } catch (err) {
    console.error("Create geofence error:", err);
    haptic("warning");
    showToast("❌ " + (err.message || "Server bilan aloqa uzildi"));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<i data-lucide="check" class="w-4 h-4"></i><span>Ushbu joyni saqlash</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

// Delete geofence
async function deleteGeofence(fenceId) {
  if (!confirm("Haqiqatan ham bu kerakli joyni o'chirmoqchimisiz?")) return;

  if (isDemoMode) {
    haptic("medium");
    showToast("Kerakli joy o'chirildi");
    return;
  }

  try {
    const effectiveParentId = parentId || authenticatedSession?.user?.id;
    const res = await fetch(`/api/v1/webapp/geofence/${fenceId}?parent_id=${effectiveParentId}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (data.status === "success") {
      haptic("medium");
      showToast("Kerakli joy o'chirildi");
      if (currentChildId) {
        await loadGeofences(currentChildId);
      }
    }
  } catch (err) {
    console.error("Failed to delete geofence:", err);
  }
}

// Open Google / Yandex navigation
function openExternalNavigation() {
  haptic("light");
  if (!childMarker) {
    showToast("Farzand koordinatasi hali olinmagan");
    return;
  }
  const pos = childMarker.getLatLng();
  const url = `https://www.google.com/maps/dir/?api=1&destination=${pos.lat},${pos.lng}`;
  window.open(url, "_blank");
}

// Manual refresh button
async function refreshAllData() {
  haptic("light");
  const btn = document.getElementById("btnRefresh");
  btn.classList.add("animate-spin");

  if (isDemoMode) {
    setTimeout(() => {
      btn.classList.remove("animate-spin");
      showToast("Yangilandi (Demo)");
    }, 400);
    return;
  }

  if (currentChildId) {
    await updateLiveTelemetry(currentChildId);
    await loadGeofences(currentChildId);
    await loadRouteHistory(currentChildId);
  }

  setTimeout(() => {
    btn.classList.remove("animate-spin");
    showToast("Yangilandi");
  }, 600);
}

// Toast notification helper (Non-blocking floating pill at top of screen)
let activeToastTimer = null;

function showToast(message) {
  const oldToast = document.getElementById("appGlobalToast");
  if (oldToast) {
    clearTimeout(activeToastTimer);
    oldToast.remove();
  }

  const toast = document.createElement("div");
  toast.id = "appGlobalToast";
  toast.className = "app-toast-pill";
  toast.textContent = message;
  document.body.appendChild(toast);

  activeToastTimer = setTimeout(() => {
    toast.classList.add("toast-leaving");
    setTimeout(() => {
      if (toast.parentNode) toast.remove();
    }, 280);
  }, 4000);
}


// =========================================================
// TASKS & ASSIGNMENTS SERVICE (Topshiriq berish xizmati)
// =========================================================

let tasksList = [];
let activeScheduleTaskId = null;
let activeProofTaskId = null;
let selectedTaskMediaFile = null;
let selectedProofPhotoFile = null;

async function loadTasks() {
  const container = document.getElementById("tasksListContainer");
  if (!container) return;

  if (isDemoMode) {
    renderDemoTasks();
    return;
  }

  container.innerHTML = '<p class="text-center text-xs text-slate-400 py-6">Topshiriqlar yuklanmoqda...</p>';

  try {
    let url = "";
    if (parentId) {
      url = currentChildId
        ? `/api/v1/webapp/parent/${parentId}/tasks?child_id=${currentChildId}`
        : `/api/v1/webapp/parent/${parentId}/tasks`;
    } else if (currentChildId) {
      url = `/api/v1/webapp/child/${currentChildId}/tasks`;
    } else {
      container.innerHTML = '<p class="text-center text-xs text-slate-400 py-6">Farzand tanlanmagan.</p>';
      return;
    }

    const res = await fetch(url);
    const data = await res.json();
    if (data.status === "success") {
      tasksList = data.tasks || [];
      renderTasksList();
    } else {
      container.innerHTML = '<p class="text-center text-xs text-red-400 py-6">Topshiriqlarni yuklashda xatolik.</p>';
    }
  } catch (err) {
    console.error("Error loading tasks:", err);
    container.innerHTML = '<p class="text-center text-xs text-red-400 py-6">Server bilan aloqa uzildi.</p>';
  }
}

function renderDemoTasks() {
  tasksList = [
    {
      id: 991,
      title: "Matematika darslarini qilish",
      description: "45-sahifadagi 1-dan 5-gacha bo'lgan misollar",
      reward_coins: 5000,
      status: "pending",
      media_type: null,
      media_url: null,
      seen_at: null,
      promised_time: null,
      proof_photo_url: null,
      created_at: new Date().toISOString()
    },
    {
      id: 992,
      title: "Xonani tartibga keltirish",
      description: "Kitoblarni taxlab, kiyimlarni joyiga qo'yish",
      reward_coins: 3000,
      status: "scheduled",
      media_type: null,
      media_url: null,
      seen_at: new Date().toISOString(),
      promised_time: "30 daqiqadan keyin",
      proof_photo_url: null,
      created_at: new Date().toISOString()
    },
    {
      id: 993,
      title: "Ingliz tili yangi lug'at yodlash",
      description: "Unit 4 mavzusidagi 15 ta yangi so'zni daftarga yozib yodlash",
      reward_coins: 4000,
      status: "completed_by_child",
      media_type: null,
      media_url: null,
      seen_at: new Date().toISOString(),
      promised_time: "1 soat ichida",
      proof_photo_url: "https://images.unsplash.com/photo-1456513080510-7bf3a84b82f8?auto=format&fit=crop&w=600&q=80",
      created_at: new Date().toISOString()
    }
  ];
  renderTasksList();
}

function renderTasksList() {
  renderChildDashboardQuests();
  if (currentRole === "child") {
    const doneCount = (tasksList && tasksList.length)
      ? tasksList.filter(t => t.status === "completed" || t.status === "approved").length
      : 0;
    updateChildLevelAndXP(childBalanceCoins || 0, doneCount);
  }
  const container = document.getElementById("tasksListContainer");
  if (!container) return;

  const isChild = (currentRole === "child");

  let balanceBanner = "";
  if (isChild) {
    const moneyStr = (childBalanceSom || 0).toLocaleString();
    const coinStr = (childBalanceCoins || 0).toLocaleString();
    const perCoin = coinRateSom / Math.max(1, coinRateCoins);
    balanceBanner = `
      <div class="p-3.5 bg-gradient-to-r from-emerald-950/90 via-slate-900 to-indigo-950/80 border border-emerald-500/40 rounded-2xl mb-3 shadow-lg">
        <div class="flex items-center justify-between">
          <span class="text-xs font-bold text-emerald-400 flex items-center gap-1.5">
            <span class="text-base">🐷</span>
            <span>Jamg'arilgan naqd pulingiz:</span>
          </span>
          <span class="text-[10px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 px-2 py-0.5 rounded-full font-bold">
            1k = ${(perCoin * 1000).toLocaleString()} so'm
          </span>
        </div>
        <p class="text-2xl font-black text-white mt-1 tracking-tight">
          ${moneyStr} so'm
        </p>
        <p class="text-xs text-amber-400 font-bold mt-0.5 flex items-center gap-1">
          <i data-lucide="coins" class="w-3.5 h-3.5"></i>
          <span>${coinStr} tanga</span>
        </p>
      </div>
    `;
  } else {
    // Parent view
    const activeChild = childrenList.find(c => c.id == currentChildId);
    if (activeChild) {
      const cMoney = activeChild.virtual_money_som || Math.round((activeChild.virtual_balance || 0) * (coinRateSom / Math.max(1, coinRateCoins)));
      balanceBanner = `
        <div class="p-3 bg-gradient-to-r from-slate-900 to-slate-800/80 border border-slate-700/80 rounded-2xl mb-3 flex items-center justify-between">
          <div>
            <span class="text-[10px] text-slate-400 font-bold uppercase tracking-wider">${escapeHtml(activeChild.full_name || 'Farzand')} balansi:</span>
            <p class="text-xs font-bold text-white mt-0.5">${(activeChild.virtual_balance || 0).toLocaleString()} tanga <span class="text-emerald-400 font-semibold">(≈ ${cMoney.toLocaleString()} so'm)</span></p>
          </div>
          <button onclick="openCoinRateModal()" class="px-2.5 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 rounded-xl text-xs font-bold transition active:scale-95 shadow">
            💰 Kurs: ${(coinRateCoins/1000).toFixed(0)}k=${(coinRateSom/1000).toFixed(0)}k
          </button>
        </div>
      `;
    }
  }

  if (tasksList.length === 0) {
    container.innerHTML = balanceBanner + `
      <div class="text-center py-8">
        <div class="w-12 h-12 bg-slate-800 rounded-full flex items-center justify-center mx-auto text-slate-400 mb-2">
          <i data-lucide="check-circle" class="w-6 h-6"></i>
        </div>
        <p class="text-xs font-semibold text-slate-300">${isChild ? "Sizda hozircha topshiriqlar yo'q" : "Faol topshiriqlar yo'q"}</p>
        <p class="text-[11px] text-slate-500 mt-1">${isChild ? "Ota-onangiz yangi topshiriq berganda bu yerda ko'rinadi" : "Yangi topshiriq berish uchun yuqoridagi tugmani bosing"}</p>
      </div>
    `;
    if (window.lucide) lucide.createIcons();
    return;
  }

  container.innerHTML = balanceBanner + tasksList.map(t => {
    const rewardCoins = t.reward_coins || 1000;
    const rewardSom = Math.round(rewardCoins * (coinRateSom / Math.max(1, coinRateCoins)));

    let statusBadge = "";
    if (t.status === "completed_by_child") {
      statusBadge = '<span class="text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/30 px-2 py-0.5 rounded-full font-semibold">⏳ Tekshiruvda</span>';
    } else if (t.status === "approved") {
      statusBadge = '<span class="text-[10px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 px-2 py-0.5 rounded-full font-semibold">🏆 Tasdiqlangan</span>';
    } else if (t.status === "rejected") {
      statusBadge = '<span class="text-[10px] bg-red-500/20 text-red-300 border border-red-500/30 px-2 py-0.5 rounded-full font-semibold">❌ Rad etilgan</span>';
    } else if (t.status === "scheduled") {
      statusBadge = `<span class="text-[10px] bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-2 py-0.5 rounded-full font-semibold">⏰ ${t.promised_time || 'Rejalashtirilgan'}</span>`;
    } else if (t.status === "seen") {
      statusBadge = '<span class="text-[10px] bg-blue-500/20 text-blue-300 border border-blue-500/30 px-2 py-0.5 rounded-full font-semibold">👁 Ko\'rildi</span>';
    } else {
      statusBadge = '<span class="text-[10px] bg-slate-700/50 text-slate-300 border border-slate-600 px-2 py-0.5 rounded-full font-semibold">▫️ Kutilmoqda</span>';
    }

    let mediaHtml = "";
    if (t.media_type === "photo" && t.media_url) {
      mediaHtml = `
        <div class="mt-2 rounded-xl overflow-hidden border border-slate-700 max-h-40 bg-slate-950 flex items-center justify-center">
          <img src="${t.media_url}" class="max-h-40 w-full object-cover cursor-pointer" onclick="window.open('${t.media_url}', '_blank')" />
        </div>
      `;
    } else if (t.media_type === "video" && t.media_url) {
      mediaHtml = `
        <div class="mt-2 rounded-xl overflow-hidden border border-slate-700 max-h-48 bg-slate-950">
          <video src="${t.media_url}" controls class="w-full max-h-48"></video>
        </div>
      `;
    }

    let proofHtml = "";
    if (t.proof_photo_url) {
      proofHtml = `
        <div class="mt-2.5 p-2 bg-slate-950/60 rounded-xl border border-slate-800">
          <div class="flex items-center gap-1.5 text-[10px] font-bold text-emerald-400 mb-1">
            <i data-lucide="camera" class="w-3.5 h-3.5"></i>
            <span>${isChild ? 'Yuborgan hisobot rasmingiz:' : 'Farzand yuborgan hisobot rasmi:'}</span>
          </div>
          <img src="${t.proof_photo_url}" class="rounded-lg max-h-36 w-full object-cover cursor-pointer" onclick="window.open('${t.proof_photo_url}', '_blank')" />
        </div>
      `;
    }

    // Role-specific action buttons
    let actionButtons = "";
    if (isChild) {
      // CHILD ACTIONS
      if (t.status === "pending" || t.status === "seen" || t.status === "scheduled") {
        const seenBtn = (t.status === "pending")
          ? `<button onclick="markTaskSeen(${t.id})" class="flex-1 py-1.5 bg-slate-800 hover:bg-slate-700 active:scale-95 border border-slate-700 text-blue-400 rounded-lg text-xs font-semibold flex items-center justify-center gap-1 transition">
               <i data-lucide="eye" class="w-3.5 h-3.5"></i>
               <span>Ko'rdim</span>
             </button>`
          : `<div class="px-2 py-1 text-[11px] text-emerald-400 font-semibold flex items-center gap-1">
               <i data-lucide="check" class="w-3.5 h-3.5"></i>
               <span>Ko'rildi</span>
             </div>`;

        const schedBtn = `<button onclick="openScheduleModal(${t.id})" class="flex-1 py-1.5 bg-slate-800 hover:bg-slate-700 active:scale-95 border border-slate-700 text-amber-300 rounded-lg text-xs font-semibold flex items-center justify-center gap-1 transition">
               <i data-lucide="clock" class="w-3.5 h-3.5"></i>
               <span>${t.promised_time ? t.promised_time : 'Vaqt tanlash'}</span>
             </button>`;

        const proofBtn = `<button onclick="openProofModal(${t.id})" class="w-full py-2 bg-emerald-600 hover:bg-emerald-500 active:scale-95 text-white rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition shadow">
               <i data-lucide="camera" class="w-4 h-4"></i>
               <span>Bajarildi (Rasm yuborish)</span>
             </button>`;

        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 space-y-2">
            <div class="flex gap-2">
              ${seenBtn}
              ${schedBtn}
            </div>
            ${proofBtn}
          </div>
        `;
      } else if (t.status === "completed_by_child") {
        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 text-center py-1">
            <span class="text-xs text-amber-300 font-medium flex items-center justify-center gap-1.5">
              <span>⏳</span> <span>Rasm hisoboti ota-onangizga yuborildi. Tasdiqlanishi kutilmoqda...</span>
            </span>
          </div>
        `;
      } else if (t.status === "approved") {
        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 text-center py-1 bg-emerald-950/30 rounded-xl border border-emerald-500/20">
            <span class="text-xs text-emerald-300 font-bold flex items-center justify-center gap-1.5">
              <span>🎉</span> <span>Ota-onangiz tasdiqladi! +${rewardCoins.toLocaleString()} tanga (+${rewardSom.toLocaleString()} so'm) berildi</span>
            </span>
          </div>
        `;
      } else if (t.status === "rejected") {
        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 space-y-1.5">
            <p class="text-xs text-red-300 text-center font-medium">❌ Ota-onangiz rad etdi. Qaytadan topshiring:</p>
            <button onclick="openProofModal(${t.id})" class="w-full py-2 bg-amber-600 hover:bg-amber-500 active:scale-95 text-white rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition shadow">
              <i data-lucide="camera" class="w-4 h-4"></i>
              <span>Qaytadan rasm yuborish</span>
            </button>
          </div>
        `;
      }
    } else {
      // PARENT ACTIONS
      if (t.status === "completed_by_child") {
        actionButtons = `
          <div class="flex gap-2 mt-3 pt-2 border-t border-slate-800">
            <button onclick="approveTask(${t.id})" class="flex-1 py-2 bg-emerald-600 hover:bg-emerald-500 active:scale-95 text-white rounded-xl text-xs font-bold transition shadow-lg flex items-center justify-center gap-1">
              <i data-lucide="check" class="w-4 h-4"></i>
              <span>Tasdiqlash (+${rewardCoins.toLocaleString()} tanga / ${rewardSom.toLocaleString()} so'm)</span>
            </button>
            <button onclick="rejectTask(${t.id})" class="px-3 py-2 bg-slate-800 hover:bg-red-600/30 text-red-400 border border-slate-700 active:scale-95 rounded-xl text-xs font-semibold transition">
              Rad etish
            </button>
          </div>
        `;
      } else if (t.status === "pending") {
        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 text-[11px] text-slate-400 flex items-center justify-between">
            <span>⏳ Farzand hali xabarni ko'rmadi</span>
            <div class="flex items-center gap-1.5">
              <span class="text-amber-400 font-bold">+${rewardCoins.toLocaleString()} tanga</span>
              <span class="text-emerald-400 font-bold text-[10px]">(≈ ${rewardSom.toLocaleString()} so'm)</span>
            </div>
          </div>
        `;
      } else if (t.status === "seen") {
        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 text-[11px] text-blue-400 flex items-center justify-between font-medium">
            <span>👁 Farzand topshiriqni ko'rdi</span>
            <span class="text-slate-400">Bajarilish kutilmoqda</span>
          </div>
        `;
      } else if (t.status === "scheduled") {
        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 text-[11px] text-amber-300 flex items-center justify-between font-medium">
            <span>⏰ Farzand ${t.promised_time || 'vaqtda'} bajarishga va'da berdi</span>
            <span class="text-emerald-400 font-bold">Jarayonda</span>
          </div>
        `;
      } else if (t.status === "approved") {
        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 text-[11px] text-emerald-400 flex items-center justify-between font-medium">
            <span>🏆 Tasdiqlangan</span>
            <div class="flex items-center gap-1.5">
              <span class="font-bold">+${rewardCoins.toLocaleString()} tanga</span>
              <span class="text-emerald-400 font-bold text-[10px]">(+${rewardSom.toLocaleString()} so'm)</span>
            </div>
          </div>
        `;
      } else if (t.status === "rejected") {
        actionButtons = `
          <div class="mt-3 pt-2 border-t border-slate-800 text-[11px] text-red-400 flex items-center justify-between font-medium">
            <span>❌ Rad etilgan</span>
            <span class="text-slate-400">Farzand qayta topshirishi mumkin</span>
          </div>
        `;
      }
    }

    return `
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-2xl p-3.5 shadow-md flex flex-col justify-between transition hover:border-slate-600">
        <div>
          <div class="flex items-start justify-between gap-2">
            <h3 class="text-xs font-bold text-white">${t.title}</h3>
            <div class="flex items-center gap-1.5 shrink-0">
              ${statusBadge}
              ${!isChild ? `<button onclick="deleteTask(${t.id})" class="p-1 text-slate-400 hover:text-red-400 active:scale-90 transition rounded-lg hover:bg-red-500/10" title="Vazifani o'chirish"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>` : ''}
            </div>
          </div>
          ${t.description ? `<p class="text-[11px] text-slate-300 mt-1">${t.description}</p>` : ''}
          <div class="flex items-center justify-between gap-2 mt-2.5 pt-2 border-t border-slate-700/60">
            <div class="flex items-center gap-1.5 text-xs font-black text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2.5 py-1 rounded-xl shadow-sm">
              <i data-lucide="coins" class="w-3.5 h-3.5 text-amber-400"></i>
              <span>+${rewardCoins.toLocaleString()} tanga</span>
            </div>
            <div class="flex items-center gap-1 text-xs font-black text-emerald-400 bg-emerald-500/15 border border-emerald-500/30 px-2.5 py-1 rounded-xl shadow-sm">
              <span>💵</span>
              <span>+${rewardSom.toLocaleString()} so'm</span>
            </div>
          </div>
          ${mediaHtml}
          ${proofHtml}
        </div>
        ${actionButtons}
      </div>
    `;
  }).join("");

  if (window.lucide) lucide.createIcons();
}

async function deleteTask(taskId) {
  haptic("medium");
  if (!confirm("Haqiqatan ham ushbu vazifani o'chirmoqchimisiz?")) return;

  if (isDemoMode) {
    tasksList = tasksList.filter(t => t.id != taskId);
    renderTasksList();
    showToast("🗑 Vazifa o'chirildi (Demo)");
    return;
  }

  try {
    const res = await fetch(`/api/v1/webapp/task/${taskId}?parent_id=${parentId}`, { method: "DELETE" });
    const data = await res.json();
    if (data.status === "success") {
      tasksList = tasksList.filter(t => t.id != taskId);
      renderTasksList();
      showToast("🗑 Vazifa muvaffaqiyatli o'chirildi");
    } else {
      showToast("⚠️ Vazifani o'chirishda xatolik yuz berdi");
    }
  } catch (err) {
    console.error("Delete task error:", err);
    showToast("⚠️ Server bilan aloqa uzildi");
  }
}

function openAddTaskModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("medium");
  document.getElementById("modal-add-task").classList.remove("hidden");
  clearTaskMedia();
}

function closeAddTaskModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  document.getElementById("modal-add-task").classList.add("hidden");
  clearTaskMedia();
}

function handleTaskMediaSelected(input) {
  if (!input.files || input.files.length === 0) return;
  selectedTaskMediaFile = input.files[0];

  const previewBox = document.getElementById("taskMediaPreview");
  const imgEl = document.getElementById("taskPreviewImg");
  const videoEl = document.getElementById("taskPreviewVideo");
  const label = document.getElementById("taskMediaLabel");
  const badge = document.getElementById("taskMediaStatusBadge");
  const icon = document.getElementById("taskMediaStatusIcon");

  const isVideo = selectedTaskMediaFile.type.startsWith("video/");
  if (label) label.textContent = selectedTaskMediaFile.name;
  if (icon) icon.textContent = isVideo ? "🎥" : "📸";
  if (badge) badge.classList.remove("hidden");
  if (previewBox) previewBox.classList.remove("hidden");

  const url = URL.createObjectURL(selectedTaskMediaFile);
  if (isVideo) {
    if (imgEl) imgEl.classList.add("hidden");
    if (videoEl) {
      videoEl.classList.remove("hidden");
      videoEl.src = url;
    }
  } else {
    if (videoEl) videoEl.classList.add("hidden");
    if (imgEl) {
      imgEl.classList.remove("hidden");
      imgEl.src = url;
    }
  }
  if (window.lucide) lucide.createIcons();
}

function clearTaskMedia() {
  selectedTaskMediaFile = null;
  ["taskCameraPhotoInput", "taskCameraVideoInput", "taskGalleryInput", "taskMediaFileInput"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const previewBox = document.getElementById("taskMediaPreview");
  if (previewBox) previewBox.classList.add("hidden");
  const badge = document.getElementById("taskMediaStatusBadge");
  if (badge) badge.classList.add("hidden");
  const imgEl = document.getElementById("taskPreviewImg");
  if (imgEl) { imgEl.src = ""; imgEl.classList.add("hidden"); }
  const videoEl = document.getElementById("taskPreviewVideo");
  if (videoEl) { videoEl.src = ""; videoEl.classList.add("hidden"); }
}

async function submitNewTask() {
  const title = document.getElementById("inputTaskTitle").value.trim();
  const desc = document.getElementById("inputTaskDesc").value.trim();
  const reward = parseInt(document.getElementById("inputTaskReward").value) || 1000;

  if (!title) {
    alert("Iltimos, topshiriq nomini kiriting!");
    return;
  }

  const submitBtn = document.getElementById("btnSubmitTask");
  submitBtn.disabled = true;
  submitBtn.textContent = "Yuborilmoqda...";

  let mediaUrl = null;
  let mediaType = null;

  try {
    if (selectedTaskMediaFile) {
      const formData = new FormData();
      formData.append("file", selectedTaskMediaFile);
      const uploadRes = await fetch("/api/v1/webapp/upload", {
        method: "POST",
        body: formData
      });
      const uploadData = await uploadRes.json();
      if (uploadData.status === "success") {
        mediaUrl = uploadData.file_url;
        mediaType = uploadData.media_type;
      }
    }

    if (isDemoMode) {
      tasksList.unshift({
        id: Date.now(),
        title: title,
        description: desc,
        reward_coins: reward,
        status: "pending",
        media_type: mediaType,
        media_url: mediaUrl,
        created_at: new Date().toISOString()
      });
      renderTasksList();
      closeAddTaskModal();
      showToast("Topshiriq yuborildi (Demo)");
      return;
    }

    const payload = {
      parent_id: parseInt(parentId),
      child_id: parseInt(currentChildId),
      title: title,
      description: desc || null,
      reward_coins: reward,
      media_type: mediaType,
      media_url: mediaUrl
    };

    const res = await fetch("/api/v1/webapp/task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const resData = await res.json();
    if (resData.status === "success") {
      haptic("success");
      closeAddTaskModal();
      showToast("Topshiriq muvaffaqiyatli yuborildi!");
      document.getElementById("inputTaskTitle").value = "";
      document.getElementById("inputTaskDesc").value = "";
      loadTasks();
    } else {
      alert("Xatolik: " + (resData.detail || "Topshiriq saqlanmadi"));
    }
  } catch (err) {
    console.error("Submit task error:", err);
    alert("Topshiriqni yuborishda xatolik yuz berdi.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Yuborish";
  }
}

async function markTaskSeen(taskId) {
  haptic("light");
  try {
    if (isDemoMode) {
      const t = tasksList.find(x => x.id === taskId);
      if (t) t.status = "seen";
      renderTasksList();
      showToast("Xabar ko'rildi (Demo)");
      return;
    }

    const res = await fetch(`/api/v1/webapp/task/${taskId}/seen`, { method: "POST" });
    const data = await res.json();
    if (data.status === "success") {
      showToast("Ota-onangizga xabar ko'rilgani bildirildi!");
      loadTasks();
    }
  } catch (e) {
    console.error("Mark seen error:", e);
  }
}

function openScheduleModal(taskId) {
  haptic("medium");
  activeScheduleTaskId = taskId;

  const modal = document.getElementById("modal-task-schedule");
  if (modal) modal.classList.remove("hidden");

  // Automatically compute and fill current date and time
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  const todayStr = `${year}-${month}-${day}`;

  const dateInput = document.getElementById("scheduleCustomDate");
  if (dateInput) {
    dateInput.value = todayStr;
    dateInput.min = todayStr; // Cannot pick past dates
  }

  // Set time to round 15-30 mins ahead of current time
  let hours = now.getHours();
  let minutes = now.getMinutes();
  minutes = Math.ceil((minutes + 15) / 5) * 5;
  if (minutes >= 60) {
    hours = (hours + 1) % 24;
    minutes = minutes % 60;
  }
  const hoursStr = String(hours).padStart(2, "0");
  const minsStr = String(minutes).padStart(2, "0");
  const timeStr = `${hoursStr}:${minsStr}`;

  const timeInput = document.getElementById("scheduleCustomTime");
  if (timeInput) {
    timeInput.value = timeStr;
  }

  updateScheduleTimeBadge();
  if (window.lucide) lucide.createIcons();
}

function adjustScheduleMinutes(addMins) {
  haptic("light");
  const timeInput = document.getElementById("scheduleCustomTime");
  const dateInput = document.getElementById("scheduleCustomDate");
  if (!timeInput) return;

  const currentTime = timeInput.value || "12:00";
  const [h, m] = currentTime.split(":").map(Number);

  let totalMins = (h || 0) * 60 + (m || 0) + addMins;
  if (totalMins >= 24 * 60) {
    totalMins = totalMins % (24 * 60);
    if (dateInput && dateInput.value) {
      const d = new Date(dateInput.value);
      d.setDate(d.getDate() + 1);
      dateInput.value = d.toISOString().split("T")[0];
    }
  }

  const newH = Math.floor(totalMins / 60);
  const newM = totalMins % 60;
  timeInput.value = `${String(newH).padStart(2, "0")}:${String(newM).padStart(2, "0")}`;
  updateScheduleTimeBadge();
}

function updateScheduleTimeBadge() {
  const timeInput = document.getElementById("scheduleCustomTime");
  const badge = document.getElementById("customTimePreviewBadge");
  if (timeInput && badge) {
    badge.textContent = `Soat: ${timeInput.value || "--:--"}`;
  }
}

function toggleCustomSchedulePicker() {
  haptic("light");
  const box = document.getElementById("customScheduleBox");
  if (box) {
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const timeInput = document.getElementById("scheduleCustomTime");
    if (timeInput) timeInput.focus();
  }
}

function submitCustomSchedule() {
  const dateInput = document.getElementById("scheduleCustomDate");
  const timeInput = document.getElementById("scheduleCustomTime");

  const selectedTime = timeInput ? timeInput.value : "";
  if (!selectedTime) {
    showToast("Iltimos, bajarish vaqtini tanlang");
    return;
  }

  const selectedDate = dateInput ? dateInput.value : "";
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  const todayStr = `${year}-${month}-${day}`;

  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const tomYear = tomorrow.getFullYear();
  const tomMonth = String(tomorrow.getMonth() + 1).padStart(2, "0");
  const tomDay = String(tomorrow.getDate()).padStart(2, "0");
  const tomorrowStr = `${tomYear}-${tomMonth}-${tomDay}`;

  let promisedStr = "";
  if (!selectedDate || selectedDate === todayStr) {
    promisedStr = `Bugun soat ${selectedTime}`;
  } else if (selectedDate === tomorrowStr) {
    promisedStr = `Ertaga soat ${selectedTime}`;
  } else {
    const parts = selectedDate.split("-");
    promisedStr = `${parts[2]}.${parts[1]} soat ${selectedTime}`;
  }

  confirmTaskSchedule(promisedStr);
}

function updateTaskRewardPreview(coinsVal) {
  const coins = parseInt(coinsVal) || 0;
  const som = Math.round(coins * (coinRateSom / Math.max(1, coinRateCoins)));
  const badge = document.getElementById("inputTaskRewardSom");
  if (badge) {
    badge.textContent = `≈ ${som.toLocaleString()} so'm`;
  }
}

function closeScheduleModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  activeScheduleTaskId = null;
  document.getElementById("modal-task-schedule").classList.add("hidden");
}

async function confirmTaskSchedule(promisedTime) {
  if (!activeScheduleTaskId) return;
  haptic("medium");

  try {
    if (isDemoMode) {
      const t = tasksList.find(x => x.id === activeScheduleTaskId);
      if (t) {
        t.status = "scheduled";
        t.promised_time = promisedTime;
      }
      closeScheduleModal();
      renderTasksList();
      showToast(`Vaqt belgilandi: ${promisedTime}`);
      return;
    }

    const res = await fetch(`/api/v1/webapp/task/${activeScheduleTaskId}/schedule`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ promised_time: promisedTime })
    });
    const data = await res.json();
    if (data.status === "success") {
      closeScheduleModal();
      showToast(`Vaqt belgilandi: ${promisedTime}`);
      loadTasks();
    }
  } catch (e) {
    console.error("Confirm schedule error:", e);
  }
}

function openProofModal(taskId) {
  haptic("medium");
  activeProofTaskId = taskId;
  selectedProofPhotoFile = null;
  const input = document.getElementById("proofFileInput");
  if (input) input.value = "";
  const prev = document.getElementById("proofPhotoPreviewContainer");
  if (prev) prev.classList.add("hidden");
  const btn = document.getElementById("btnSubmitProof");
  if (btn) btn.disabled = true;
  document.getElementById("modal-task-proof").classList.remove("hidden");
}

function closeProofModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  activeProofTaskId = null;
  clearProofMedia();
  document.getElementById("modal-task-proof").classList.add("hidden");
}

function clearProofMedia() {
  selectedProofPhotoFile = null;
  ["proofCameraPhotoInput", "proofCameraVideoInput", "proofGalleryInput", "proofFileInput"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const prev = document.getElementById("proofPhotoPreviewContainer");
  if (prev) prev.classList.add("hidden");
  const img = document.getElementById("proofPreviewImg");
  if (img) { img.src = ""; img.classList.add("hidden"); }
  const video = document.getElementById("proofPreviewVideo");
  if (video) { video.src = ""; video.classList.add("hidden"); }
  const btn = document.getElementById("btnSubmitProof");
  if (btn) btn.disabled = true;
}

function handleProofPhotoSelected(input) {
  if (!input.files || input.files.length === 0) return;
  selectedProofPhotoFile = input.files[0];

  const prev = document.getElementById("proofPhotoPreviewContainer");
  const img = document.getElementById("proofPreviewImg");
  const video = document.getElementById("proofPreviewVideo");
  const btn = document.getElementById("btnSubmitProof");

  const isVideo = selectedProofPhotoFile.type.startsWith("video/");
  const url = URL.createObjectURL(selectedProofPhotoFile);

  if (isVideo) {
    if (img) img.classList.add("hidden");
    if (video) {
      video.classList.remove("hidden");
      video.src = url;
    }
  } else {
    if (video) video.classList.add("hidden");
    if (img) {
      img.classList.remove("hidden");
      img.src = url;
    }
  }

  if (prev) prev.classList.remove("hidden");
  if (btn) btn.disabled = false;
  if (window.lucide) lucide.createIcons();
}

async function submitTaskProof() {
  if (!activeProofTaskId || !selectedProofPhotoFile) {
    alert("Iltimos, topshiriq bajarilganini tasdiqlovchi rasm oling!");
    return;
  }

  const btn = document.getElementById("btnSubmitProof");
  btn.disabled = true;
  btn.textContent = "Yuklanmoqda...";

  try {
    const formData = new FormData();
    formData.append("file", selectedProofPhotoFile);
    const upRes = await fetch("/api/v1/webapp/upload", {
      method: "POST",
      body: formData
    });
    const upData = await upRes.json();
    if (upData.status !== "success") {
      throw new Error("Rasm yuklanmadi");
    }

    if (isDemoMode) {
      const t = tasksList.find(x => x.id === activeProofTaskId);
      if (t) {
        t.status = "completed_by_child";
        t.proof_photo_url = upData.file_url;
      }
      closeProofModal();
      renderTasksList();
      showToast("Hisobot rasmi ota-onaga yuborildi! (Demo)");
      return;
    }

    const completeRes = await fetch(`/api/v1/webapp/task/${activeProofTaskId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proof_photo_url: upData.file_url })
    });
    const completeData = await completeRes.json();
    if (completeData.status === "success") {
      haptic("success");
      closeProofModal();
      showToast("Rasm hisoboti ota-onangizga yuborildi!");
      loadTasks();
    } else {
      alert("Xatolik yuz berdi: " + (completeData.detail || "Topshirish amalga oshmadi"));
    }
  } catch (err) {
    console.error("Submit proof error:", err);
    alert("Rasmni yuborishda xatolik yuz berdi.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Ota-onaga yuborish";
  }
}

async function approveTask(taskId) {
  haptic("success");
  playCoinChime();
  try {
    if (isDemoMode) {
      const t = tasksList.find(x => x.id === taskId);
      if (t) {
        t.status = "approved";
        const coins = t.reward_coins || 1000;
        childBalanceCoins += coins;
        childBalanceSom = Math.round(childBalanceCoins * (coinRateSom / Math.max(1, coinRateCoins)));

        const c = childrenList.find(ch => ch.id == currentChildId);
        if (c) {
          c.virtual_balance = childBalanceCoins;
          c.virtual_money_som = childBalanceSom;
        }

        renderTasksList();
        const heroMoney = document.getElementById("childHeroMoneyText");
        if (heroMoney) heroMoney.textContent = `${childBalanceSom.toLocaleString()} so'm`;
        const bannerMoney = document.getElementById("childBannerMoney");
        if (bannerMoney) bannerMoney.textContent = `💵 Sizda hozir ${childBalanceSom.toLocaleString()} so'm pulingiz bor! (${childBalanceCoins.toLocaleString()} tanga)`;
        const wMoney = document.getElementById("childWalletMoneyText");
        if (wMoney) wMoney.textContent = `${childBalanceSom.toLocaleString()} so'm`;
      }
      showToast("🏆 Topshiriq tasdiqlandi (+tanga o'tkazildi)!");
      return;
    }

    const res = await fetch(`/api/v1/webapp/task/${taskId}/approve`, { method: "POST" });
    const data = await res.json();
    if (data.status === "success") {
      playCoinChime();
      showToast("🏆 Topshiriq tasdiqlandi (+tanga o'tkazildi)!");
      loadTasks();
      if (currentChildId) updateLiveTelemetry(currentChildId);
    }
  } catch (e) {
    console.error("Approve task error:", e);
  }
}

async function rejectTask(taskId) {
  haptic("warning");
  try {
    if (isDemoMode) {
      const t = tasksList.find(x => x.id === taskId);
      if (t) t.status = "rejected";
      renderTasksList();
      showToast("❌ Topshiriq rad etildi.");
      return;
    }

    const res = await fetch(`/api/v1/webapp/task/${taskId}/reject`, { method: "POST" });
    const data = await res.json();
    if (data.status === "success") {
      showToast("❌ Topshiriq rad etildi.");
      loadTasks();
    }
  } catch (e) {
    console.error("Reject task error:", e);
  }
}

// CHILD FLOATING & SAFETY ACTION HANDLERS

let isSosTriggering = false;
let lastSosTriggerTimestamp = 0;

async function triggerChildSOS() {
  const now = Date.now();
  if (isSosTriggering || (now - lastSosTriggerTimestamp < 8000)) {
    console.warn("SOS allaqachon faollashtirilmoqda, takroriy bosish bekor qilindi.");
    return;
  }
  isSosTriggering = true;
  lastSosTriggerTimestamp = now;

  const btnBig = document.getElementById("btnChildBigSOS");
  if (btnBig) {
    btnBig.classList.add("pointer-events-none", "opacity-80");
  }

  try {
      haptic("heavy");
      playSirenSound();
  
    // Show emergency active screen on child device
    const modal = document.getElementById("modal-sos-active");
    if (modal) {
      modal.classList.remove("hidden");
      if (window.lucide) window.lucide.createIcons();
    }
  
    showToast("🚨 SOS yuborildi! Ota-onangizga xabar ketmoqda...");
  
    // Coordinates fallback
    let fallbackLat = DEFAULT_LAT;
    let fallbackLon = DEFAULT_LON;
    if (childMarker) {
      const pos = childMarker.getLatLng();
      fallbackLat = pos.lat;
      fallbackLon = pos.lng;
    }
  
    let finalLat = fallbackLat;
    let finalLon = fallbackLon;
    let finalAcc = null;
    let battery = null;
  
    try {
      const [geo, batt] = await Promise.all([
        getDeviceLocation(),
        getDeviceBattery(),
      ]);
      if (geo) {
        finalLat = geo.latitude;
        finalLon = geo.longitude;
        finalAcc = geo.accuracy;
      }
      if (batt !== null) {
        battery = batt;
      }
    } catch (e) {
      console.warn("GPS/Battery acquisition error:", e);
    }
  
    // Find child name for marker
    const cObj = childrenList.find(c => c.id == currentChildId) || childrenList[0] || {};
    const cName = cObj.full_name || "Farzand";
  
    // Pulse red radar marker on map immediately
    if (finalLat && finalLon) {
      renderChildMarker(finalLat, finalLon, cName, true, battery);
      if (map) map.flyTo([finalLat, finalLon], 16, { animate: true, duration: 1 });
    }
  
    const payload = {
      is_sos_active: true,
      latitude: finalLat,
      longitude: finalLon,
      accuracy: finalAcc,
      battery_level: battery,
    };
  
    if (!isDemoMode && currentChildId) {
      // Feature 2: 5-second ambient audio recording in background
      recordAndUploadSOSAudio(currentChildId);
      try {
        const res = await fetch(`/api/v1/webapp/child/${currentChildId}/sos`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data && data.status === "success") {
          if (data.parent_phone) parentPhone = data.parent_phone;
          showToast("🚨 Ota-onangizga SOS manzilingiz yetkazildi!");
        }
      } catch (err) {
        console.error("SOS dispatch error:", err);
        showToast("⚠️ SOS signali yuborildi (tarmoq tekshirilmoqda)");
      }
    } else {
      showToast("🚨 (Demo) SOS signali faollashtirildi!");
    }
    } finally {
    setTimeout(() => {
      isSosTriggering = false;
      const btn = document.getElementById("btnChildBigSOS");
      if (btn) {
        btn.classList.remove("pointer-events-none", "opacity-80");
      }
    }, 4000);
  }
}

async function cancelChildSOS() {
  haptic("medium");
  isSosTriggering = false;
  lastSosTriggerTimestamp = 0;
  const btnBig = document.getElementById("btnChildBigSOS");
  if (btnBig) {
    btnBig.classList.remove("pointer-events-none", "opacity-80");
  }
  const modal = document.getElementById("modal-sos-active");
  if (modal) modal.classList.add("hidden");

  showToast("✅ Xavf bartaraf etildi. Ota-onangizga xabar yetkazilmoqda...");

  if (!isDemoMode && currentChildId) {
    try {
      await fetch(`/api/v1/webapp/child/${currentChildId}/sos/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      showToast("✅ SOS xavf signali to'xtatildi.");
    } catch (e) {
      console.error("Cancel SOS error:", e);
    }
  } else {
    showToast("✅ (Demo) SOS signali to'xtatildi.");
  }

  // Restore marker to non-SOS
  if (childMarker) {
    const pos = childMarker.getLatLng();
    const cObj = childrenList.find(c => c.id == currentChildId) || childrenList[0] || {};
    renderChildMarker(pos.lat, pos.lng, cObj.full_name || "Farzand", false, null);
  }
}

function callParent() {
  haptic("heavy");
  playPingSound();

  const modal = document.getElementById("modalCallParent");
  const phoneText = document.getElementById("modalParentCallPhoneText");
  const descText = document.getElementById("modalParentCallDesc");
  const linkBtn = document.getElementById("btnModalParentTel");
  const nameText = document.getElementById("modalParentCallName");

  const cleanPhone = (parentPhone || "").replace(/[\s\-]/g, "");
  const pName = parentName || "Ota-onangiz";

  if (nameText) nameText.textContent = `${pName} bilan bog'lanish`;

  if (cleanPhone) {
    if (phoneText) phoneText.textContent = `Qo'ng'iroq qilish (${parentPhone})`;
    if (linkBtn) {
      linkBtn.href = `tel:${cleanPhone}`;
      linkBtn.classList.remove("hidden");
    }
    if (descText) {
      descText.textContent = "Qo'ng'iroq qilish tugmasini bosing yoki Telegram orqali bog'laning:";
    }

    // Attempt direct phone call triggering
    try {
      const tempLink = document.createElement("a");
      tempLink.href = `tel:${cleanPhone}`;
      tempLink.target = "_blank";
      document.body.appendChild(tempLink);
      tempLink.click();
      setTimeout(() => tempLink.remove(), 500);
    } catch(e) {}
  } else {
    if (phoneText) phoneText.textContent = "Telefon raqam hali kiritilmagan";
    if (linkBtn) linkBtn.classList.add("hidden");
    if (descText) {
      descText.textContent = "Ota-onangiz hali botga telefon raqamini kiritmagan. Telegram orqali qo'ng'iroq qilishingiz yoki yozishingiz mumkin:";
    }
  }

  // Trigger backend to send native contact card to child's Telegram chat so child can dial in 1 tap!
  if (!isDemoMode && currentChildId) {
    fetch(`/api/v1/webapp/child/${currentChildId}/call-parent-trigger`, {
      method: "POST"
    }).catch(console.warn);
  }

  if (modal) {
    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
    setTimeout(updateTelegramBackButton, 50);
  }
}

function closeCallParentModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  const modal = document.getElementById("modalCallParent");
  if (modal) modal.classList.add("hidden");
}

function copyParentPhoneNumber() {
  haptic("medium");
  if (!parentPhone) {
    showToast("Telefon raqami kiritilmagan");
    return;
  }
  const clean = parentPhone.replace(/[\s\-]/g, "");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(clean).then(() => {
      showToast("📋 Telefon raqami nusxalandi: " + clean);
    }).catch(() => {
      showToast("Raqam: " + clean);
    });
  } else {
    showToast("Raqam: " + clean);
  }
}

function openParentTelegramChat() {
  haptic("medium");
  const tg = window.Telegram?.WebApp;
  const pObj = authenticatedSession?.parent || {};
  const uname = parentUsername || pObj.username;
  const pid = pObj.id || parentId;

  if (uname && tg && tg.openTelegramLink) {
    tg.openTelegramLink(`https://t.me/${uname}`);
    return;
  }
  if (pid && tg && tg.openTelegramLink) {
    tg.openTelegramLink(`tg://user?id=${pid}`);
    return;
  }
  if (tg && typeof tg.close === "function") {
    showToast("Telegram chatga o'tilmoqda...");
    tg.close();
    return;
  }
  showToast("Telegram chatga o'ting.");
}

function openParentChat() {
  haptic("medium");
  if (window.Telegram?.WebApp) {
    if (typeof window.Telegram.WebApp.close === "function") {
      window.Telegram.WebApp.close();
      return;
    }
  }
  showToast("Telegram chatga o'ting.");
}

function triggerChildCheckIn() {
  haptic("medium");
  playSuccessChime();
  showToast("✅ «Men yetib keldim» deb ota-onangizga bildirishnoma yuborildi!");
  if (!isDemoMode && currentChildId) {
    fetch(`/api/v1/webapp/child/${currentChildId}/checkin`, {
      method: "POST"
    }).catch(console.error);
  }
}

function navigateChildHome() {
  haptic("light");
  playPingSound();
  let homeLat = 41.2995;
  let homeLon = 69.2401;
  const homeZone = geofencesList.find(z => (z.name && z.name.toLowerCase().includes("uy")));
  if (homeZone) {
    homeLat = homeZone.latitude;
    homeLon = homeZone.longitude;
  }
  showToast("🏠 Uyga yo'l ochilmoqda...");
  const url = `https://www.google.com/maps/dir/?api=1&destination=${homeLat},${homeLon}&travelmode=walking`;
  if (window.Telegram?.WebApp?.openLink) {
    window.Telegram.WebApp.openLink(url);
  } else {
    window.open(url, "_blank");
  }
}

function triggerRemoteSiren() {
  haptic("heavy");
  playSirenSound();
  showToast("🔔 Farzand telefoniga baland ovozli signal yuborildi!");
  if (!isDemoMode && currentChildId) {
    fetch(`/api/v1/webapp/child/${currentChildId}/siren`, {
      method: "POST"
    }).catch(console.error);
  }
}

// =========================================================
// 1. DISTANCE CALCULATION HELPER (Haversine formula)
// =========================================================
function calculateDistanceMeters(lat1, lon1, lat2, lon2) {
  const R = 6371e3;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

// =========================================================
// 2. PROFILNI O'CHIRISH (CRUD: Delete Child)
// =========================================================
function openDeleteChildModal() {
  setTimeout(updateTelegramBackButton, 50);
  if (!currentChildId) {
    showToast("⚠️ O'chirish uchun farzand tanlanmagan.");
    return;
  }
  const child = childrenList.find((c) => c.id == currentChildId);
  const nameText = document.getElementById("deleteChildNameText");
  if (nameText) {
    nameText.textContent = child ? (child.full_name || `Farzand #${currentChildId}`) : "Ushbu farzand";
  }
  const modal = document.getElementById("deleteChildModal");
  if (modal) {
    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
  }
}

function closeDeleteChildModal() {
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("deleteChildModal");
  if (modal) modal.classList.add("hidden");
}

async function confirmDeleteChild() {
  if (!currentChildId) return;
  const targetId = currentChildId;
  const btn = document.getElementById("btnConfirmDeleteChild");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-pulse">O'chirilmoqda...</span>`;
  }

  try {
    if (isDemoMode) {
      childrenList = childrenList.filter((c) => c.id != targetId);
      closeDeleteChildModal();
      showToast("🗑️ Farzand muvaffaqiyatli o'chirildi (Demo)");
      if (childrenList.length > 0) {
        currentChildId = childrenList[0].id;
        renderChildSelector();
        selectChild(currentChildId);
      } else {
        currentChildId = null;
        showEmptyState();
      }
      return;
    }

    const currentParentId = parentId || authenticatedSession?.user?.id;
    const res = await fetch(`/api/v1/webapp/parent/${currentParentId}/child/${targetId}`, {
      method: "DELETE"
    });
    const data = await res.json();
    if (!res.ok || data.status !== "success") {
      throw new Error(data.detail || "Farzandni o'chirishda xatolik yuz berdi");
    }

    closeDeleteChildModal();
    showToast("✅ Farzand profili bazadan butunlay o'chirildi!");

    // Update local state
    childrenList = childrenList.filter((c) => c.id != targetId);
    if (childrenList.length > 0) {
      currentChildId = childrenList[0].id;
      renderChildSelector();
      selectChild(currentChildId);
    } else {
      currentChildId = null;
      showEmptyState();
    }
  } catch (err) {
    console.error("Delete child error:", err);
    showToast("❌ " + (err.message || "Xatolik yuz berdi"));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Ha, o'chirilsin";
    }
  }
}

// =========================================================
// 3. IKKI XIL KO'RISH REJIMI (PASSIV VS FAOL)
// =========================================================

// 1-TUGMA: 'Passiv monitoring' (Silent check)
// Ota-ona bu tugmani bosganda serverdagi eng oxirgi jonli koordinata va ma'lumotlar ota-onaga ko'rinsin,
// lekin farzand ekraniga hech qanday UI bildirishnoma (alert/notification) yuborilmasin!
async function executeSilentCheck() {
  haptic("light");
  if (!currentChildId) {
    showToast("⚠️ Iltimos, avval farzandni tanlang.");
    return;
  }

  const btn = document.getElementById("btnSilentCheck");
  if (btn) btn.classList.add("opacity-70", "animate-pulse");

  try {
    if (isDemoMode) {
      if (currentMarker) {
        map.flyTo(currentMarker.getLatLng(), 17, { duration: 1.2 });
      }
      showToast("👁️ Passiv monitoring: Jonli joylashuv olindi (Farzandga bildirishnoma yuborilmadi)");
      return;
    }

    const res = await fetch(`/api/v1/webapp/child/${currentChildId}/silent-check`, {
      method: "POST"
    });
    const data = await res.json();
    if (!res.ok || data.status !== "success") {
      throw new Error(data.detail || "Ma'lumotlarni olishda xatolik");
    }

    if (data.location && data.location.latitude && data.location.longitude) {
      const lat = data.location.latitude;
      const lng = data.location.longitude;
      const child = childrenList.find((c) => c.id == currentChildId);
      const name = child ? child.full_name : "Farzand";

      renderChildMarker(lat, lng, name, child?.is_sos_active, data.location.battery_level);
      map.flyTo([lat, lng], 17, { duration: 1.2 });
      updateBatteryUI(data.location.battery_level, data.location.is_charging);
      updateSoundUI(data.location.sound_mode);
      resolveAddress(lat, lng);
    }

    showToast("👁️ Passiv monitoring: Jonli koordinatalar olindi (Farzandga bildirishnoma yuborilmadi)");
  } catch (err) {
    console.error("Silent check error:", err);
    showToast("❌ " + (err.message || "Xatolik yuz berdi"));
  } finally {
    if (btn) btn.classList.remove("opacity-70", "animate-pulse");
  }
}

// 2-TUGMA: 'Faol so'rov' (Active check)
// Ota-ona 'Farzandning hozirgi joylashuvi' tugmasini bosganda jonli joylashuv ko'rinsin
// va WebSocket orqali farzand ekraniga 'Ota-onangiz joylashuvingizni tekshirdi' degan bildirishnoma chiqsin!
async function executeActiveCheck() {
  haptic("medium");
  if (!currentChildId) {
    showToast("⚠️ Iltimos, avval farzandni tanlang.");
    return;
  }

  const btn = document.getElementById("btnActiveCheck");
  if (btn) btn.classList.add("opacity-70", "animate-pulse");

  try {
    if (isDemoMode) {
      if (currentMarker) {
        map.flyTo(currentMarker.getLatLng(), 17, { duration: 1.2 });
      }
      showToast("📍 Faol so'rov yuborildi: Farzand ekranida 'Ota-onangiz joylashuvingizni tekshirdi' xabari chiqdi!");
      return;
    }

    const res = await fetch(`/api/v1/webapp/child/${currentChildId}/active-check`, {
      method: "POST"
    });
    const data = await res.json();
    if (!res.ok || data.status !== "success") {
      throw new Error(data.detail || "Faol so'rov yuborishda xatolik");
    }

    if (data.location && data.location.latitude && data.location.longitude) {
      const lat = data.location.latitude;
      const lng = data.location.longitude;
      const child = childrenList.find((c) => c.id == currentChildId);
      const name = child ? child.full_name : "Farzand";

      renderChildMarker(lat, lng, name, child?.is_sos_active, data.location.battery_level);
      map.flyTo([lat, lng], 17, { duration: 1.2 });
      updateBatteryUI(data.location.battery_level, data.location.is_charging);
      updateSoundUI(data.location.sound_mode);
      resolveAddress(lat, lng);
    }

    showToast("📍 Faol so'rov: Farzand ekraniga 'Ota-onangiz joylashuvingizni tekshirdi' xabarnomasi chiqdi!");
  } catch (err) {
    console.error("Active check error:", err);
    showToast("❌ " + (err.message || "Xatolik yuz berdi"));
  } finally {
    if (btn) btn.classList.remove("opacity-70", "animate-pulse");
  }
}

// =========================================================
// 4. ONBOARDING RUXSATLARI & UZLUKSIZ JONLI JOYLASHUV
// =========================================================

let childWatchId = null;
let lastSentLat = null;
let lastSentLng = null;
let lastSentTime = 0;
let appWebSocket = null;
let wsReconnectTimer = null;
let childNotificationPollTimer = null;
let activeBannerTimer = null;

const PRODUCTION_BACKEND_URL = "https://child-security-site.onrender.com";

async function fetchInstantChildLocation(childId) {
  if (!childId || isDemoMode) return;
  if (navigator.geolocation) {
    try {
      const battery = await getDeviceBattery();
      const isCharging = await getDeviceChargingStatus();
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const { latitude, longitude, accuracy, speed } = pos.coords;
          cachedDeviceLocation = { latitude, longitude, accuracy };
          lastLocationFetchTime = Date.now();
          renderChildMarker(latitude, longitude, "Men", false, battery);
          sendChildLiveTelemetry(childId, {
            latitude,
            longitude,
            accuracy: accuracy || null,
            speed: (typeof speed === "number" && speed > 0) ? Math.round(speed * 3.6) : 0,
            battery_level: battery,
            is_charging: isCharging,
            sound_mode: "normal",
            is_sos_active: false
          });
        },
        (err) => {
          console.warn("fetchInstantChildLocation note:", err);
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
      );
    } catch (e) {}
  }
}

function initChildPermissionsAndTracking(childId) {
  if (!childId) return;

  // 0. If running inside Native Android APK, trigger 24/7 background GPS tracking service immediately!
  if (window.AndroidBridge && typeof window.AndroidBridge.startBackgroundTracking === "function") {
    try {
      window.AndroidBridge.startBackgroundTracking(String(childId), PRODUCTION_BACKEND_URL);
      console.log("Native Android 24/7 background GPS tracking active for child:", childId);
    } catch (e) {
      console.warn("AndroidBridge startBackgroundTracking note:", e);
    }
  }

  // Connect real-time WebSocket for instant alerts
  connectWebSocket(childId);

  // Start HTTP fallback polling for active check alerts
  startChildNotificationPolling(childId);

  // Check if permissions were previously granted or dismissed
  const granted = localStorage.getItem("smart_tracker_child_permissions_granted");
  if (!granted) {
    const modal = document.getElementById("childOnboardingModal");
    if (modal) {
      modal.classList.remove("hidden");
      if (window.lucide) lucide.createIcons();
    }
  } else if (granted === "true") {
    // Automatically attempt silent background tracking without any browser prompt!
    trySilentBackgroundLocationTracking(childId);
  }
}

function trySilentBackgroundLocationTracking(childId) {
  if (!childId || isDemoMode) return;

  // 0. Ensure AndroidBridge background tracking is active with the clean backend URL
  if (window.AndroidBridge && typeof window.AndroidBridge.startBackgroundTracking === "function") {
    try {
      window.AndroidBridge.startBackgroundTracking(String(childId), PRODUCTION_BACKEND_URL);
    } catch (e) {}
  }

  // 1. Send immediate one-shot GPS telemetry ping (0 wait!)
  fetchInstantChildLocation(childId);

  // 2. Try Telegram native LocationManager (remembers permanently in Telegram with 0 browser prompts!)
  const tg = window.Telegram?.WebApp;
  if (tg && tg.LocationManager) {
    try {
      tg.LocationManager.init(() => {
        if (tg.LocationManager.isAccessGranted) {
          fetchTelegramLocationSilently(childId);
          if (!window._tgLocationPollInterval) {
            window._tgLocationPollInterval = setInterval(() => {
              if (currentRole === "child" && currentChildId && tg.LocationManager.isAccessGranted) {
                fetchTelegramLocationSilently(currentChildId);
              }
            }, 25000);
          }
        }
      });
      return;
    } catch (e) {
      console.warn("Telegram LocationManager init note:", e);
    }
  }

  // 3. In browser/WebView: Start live tracking if permissions already granted
  if (navigator.permissions && navigator.permissions.query) {
    navigator.permissions.query({ name: "geolocation" }).then((status) => {
      if (status.state === "granted") {
        startChildLiveTracking(childId);
      }
    }).catch(() => {
      startChildLiveTracking(childId);
    });
  } else {
    startChildLiveTracking(childId);
  }
}

function fetchTelegramLocationSilently(childId) {
  const tg = window.Telegram?.WebApp;
  if (tg && tg.LocationManager && tg.LocationManager.isAccessGranted) {
    try {
      tg.LocationManager.getLocation(async (loc) => {
        if (loc && loc.latitude && loc.longitude) {
          cachedDeviceLocation = {
            latitude: loc.latitude,
            longitude: loc.longitude,
            accuracy: loc.accuracy || 15
          };
          lastLocationFetchTime = Date.now();
          const battery = await getDeviceBattery();
          const isCharging = await getDeviceChargingStatus();
          renderChildMarker(loc.latitude, loc.longitude, "Men", false, battery);
          sendChildLiveTelemetry(childId, {
            latitude: loc.latitude,
            longitude: loc.longitude,
            accuracy: loc.accuracy || 15,
            battery_level: battery,
            is_charging: isCharging,
            speed_kmh: 0,
            sound_mode: "normal",
            is_sos_active: false
          });
        }
      });
    } catch (e) {}
  }
}

async function requestChildAllPermissions() {
  haptic("medium");
  const modal = document.getElementById("childOnboardingModal");
  const btn = document.getElementById("btnGrantChildPermissions");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-pulse">Faollashtirilmoqda...</span>`;
  }

  const targetId = currentChildId || authenticatedSession?.user?.id;

  // 0. Native Android APK background tracking integration
  if (window.AndroidBridge && typeof window.AndroidBridge.startBackgroundTracking === "function" && targetId) {
    try {
      window.AndroidBridge.startBackgroundTracking(String(targetId), PRODUCTION_BACKEND_URL);
    } catch (e) {
      console.warn("AndroidBridge start:", e);
    }
  }

  const battery = await getDeviceBattery();
  const isCharging = await getDeviceChargingStatus();

  // 1. Try Telegram native LocationManager
  const tg = window.Telegram?.WebApp;
  if (tg && tg.LocationManager) {
    try {
      tg.LocationManager.init(() => {
        if (tg.LocationManager.isLocationAvailable) {
          tg.LocationManager.getLocation((loc) => {
            if (loc && loc.latitude && loc.longitude) {
              cachedDeviceLocation = {
                latitude: loc.latitude,
                longitude: loc.longitude,
                accuracy: loc.accuracy || 10
              };
              lastLocationFetchTime = Date.now();
              if (targetId) {
                sendChildLiveTelemetry(targetId, {
                  latitude: loc.latitude,
                  longitude: loc.longitude,
                  accuracy: loc.accuracy || null,
                  speed: 0,
                  battery_level: battery,
                  is_charging: isCharging,
                  sound_mode: "normal"
                });
              }
            }
          });
        }
      });
    } catch (e) {
      console.warn("Telegram LocationManager init note:", e);
    }
  }

  // 2. Immediate GPS fix using navigator.geolocation
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords;
        cachedDeviceLocation = { latitude, longitude, accuracy };
        lastLocationFetchTime = Date.now();
        if (targetId) {
          sendChildLiveTelemetry(targetId, {
            latitude,
            longitude,
            accuracy: accuracy || null,
            speed: 0,
            battery_level: battery,
            is_charging: isCharging,
            sound_mode: "normal"
          });
        }
      },
      (err) => console.warn("Initial GPS query:", err),
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 }
    );
  }

  // 3. Start continuous background GPS telemetry
  if (targetId) {
    startChildLiveTracking(targetId);
  }

  // Permanently save permission flag so it NEVER prompts again!
  localStorage.setItem("smart_tracker_child_permissions_granted", "true");
  localStorage.setItem("smart_tracker_location_permitted", "true");

  if (modal) modal.classList.add("hidden");
  showToast("✅ Ruxsat berildi! Joylashuvingiz ota-onangizga yuborilmoqda 🛡️");
}

function skipChildPermissions() {
  haptic("light");
  // Remember that user skipped so the modal NEVER pops up on entry again!
  localStorage.setItem("smart_tracker_child_permissions_granted", "dismissed");
  const modal = document.getElementById("childOnboardingModal");
  if (modal) modal.classList.add("hidden");
}

function startChildLiveTracking(childId) {
  if (!("geolocation" in navigator)) {
    console.warn("Geolocation API not supported on this device");
    return;
  }

  if (childWatchId !== null) {
    navigator.geolocation.clearWatch(childWatchId);
    childWatchId = null;
  }

  const options = {
    enableHighAccuracy: true,
    timeout: 10000,
    maximumAge: 0 // Fresh GPS fix always with centimeter resolution, no cached data!
  };

  childWatchId = navigator.geolocation.watchPosition(
    async (pos) => {
      const { latitude, longitude, accuracy, speed } = pos.coords;
      cachedDeviceLocation = { latitude, longitude, accuracy };
      lastLocationFetchTime = Date.now();
      const now = Date.now();

      // Check if child currently has active SOS
      const isSos = (authenticatedSession?.user?.is_sos_active) || (document.getElementById("modal-sos-active") && !document.getElementById("modal-sos-active").classList.contains("hidden")) || false;

      // Update local child marker on map
      renderChildMarker(latitude, longitude, "Men", isSos, null);

      // Distance from last sent point in meters
      const dist = (lastSentLat && lastSentLng)
        ? calculateDistanceMeters(lastSentLat, lastSentLng, latitude, longitude)
        : 999;

      // Feature 3: Speed tracking & conversion to km/h
      let speedKmh = 0;
      if (typeof speed === "number" && !isNaN(speed) && speed > 0) {
        speedKmh = Math.round(speed * 3.6);
      } else if (lastSentLat && lastSentLng && lastSentTime && (now - lastSentTime) > 0) {
        const timeDeltaSec = (now - lastSentTime) / 1000;
        if (timeDeltaSec >= 3 && dist >= 5) {
          speedKmh = Math.round((dist / timeDeltaSec) * 3.6);
        }
      }

      // Ultra-sensitive centimeter tracking: send if moved >= 0.5 meter or every 2 seconds
      if (dist >= 0.5 || (now - lastSentTime) > 2000) {
        lastSentTime = now;
        lastSentLat = latitude;
        lastSentLng = longitude;

        const battery = await getDeviceBattery();
        const isCharging = await getDeviceChargingStatus();
        sendChildLiveTelemetry(childId, {
          latitude,
          longitude,
          accuracy: accuracy || null,
          speed: speedKmh,
          battery_level: battery,
          is_charging: isCharging,
          sound_mode: "normal"
        });
      }
    },
    (err) => {
      console.warn("watchPosition telemetry error:", err);
    },
    options
  );
}

async function sendChildLiveTelemetry(childId, payload) {
  if (isDemoMode) return;
  try {
    // 1. Send via WebSocket if connected
    if (appWebSocket && appWebSocket.readyState === WebSocket.OPEN) {
      appWebSocket.send(JSON.stringify({ type: "telemetry", ...payload }));
    }

    // 2. Post to backend live location telemetry endpoint
    await fetch(`/api/v1/webapp/child/${childId}/location`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  } catch (e) {
    // Silent catch, next watchPosition tick will retry
  }
}

// =========================================================
// 5. WEBSOCKET & CHILD ALERT BANNER DISPATCHER
// =========================================================

function connectWebSocket(userId) {
  if (!userId) return;
  if (appWebSocket && (appWebSocket.readyState === WebSocket.OPEN || appWebSocket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
  const wsHost = isLocalhost ? window.location.host : "child-security-site.onrender.com";
  const wsProtocol = (window.location.protocol === "https:" || !isLocalhost) ? "wss:" : "ws:";
  const wsUrl = `${wsProtocol}//${wsHost}/api/v1/webapp/ws/${userId}`;

  try {
    appWebSocket = new WebSocket(wsUrl);

    appWebSocket.onopen = () => {
      console.log("WebSocket connected for user:", userId);
      clearInterval(window._wsPingTimer);
      window._wsPingTimer = setInterval(() => {
        if (appWebSocket && appWebSocket.readyState === WebSocket.OPEN) {
          try {
            appWebSocket.send(JSON.stringify({ type: "ping" }));
          } catch (e) {}
        }
      }, 25000);
    };

    appWebSocket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg && msg.type === "pong") return;
        handleIncomingWebSocketMessage(msg);
      } catch (e) {
        console.error("WS message parse error:", e);
      }
    };

    appWebSocket.onclose = () => {
      clearInterval(window._wsPingTimer);
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = setTimeout(() => {
        if (currentRole === "child" || currentRole === "parent") {
          connectWebSocket(userId);
        }
      }, 4000);
    };

    appWebSocket.onerror = (err) => {
      console.warn("WS error:", err);
    };
  } catch (e) {
    console.warn("WS init error:", e);
  }
}

function handleIncomingWebSocketMessage(msg) {
  if (!msg) return;

  // 1. Active check alert for Child (from Bot or Parent App)
  if (msg.type === "active_check") {
    showChildActiveCheckBanner(msg.time || new Date().toLocaleTimeString().slice(0, 5));
    // Trigger fresh high-accuracy location acquisition automatically
    if (typeof sendLocationUpdate === "function") {
      try { sendLocationUpdate(); } catch(e) {}
    }
    return;
  }

  // 1.1 Remote siren signal for Child
  if (msg.type === "remote_siren" && currentRole === "child") {
    playSirenSound();
    haptic("heavy");
    showToast("🔔 Ota-onangiz telefoningizga qidiruv signali yubordi!");
    return;
  }

  // 1.2 Child checkin notification for Parent
  if (msg.type === "child_checkin" && currentRole === "parent") {
    showToast(`✅ ${msg.child_name || 'Farzandingiz'}: "Men manzilga yetib keldim" deb xabar berdi!`);
    haptic("medium");
    return;
  }

  // 1.3 Device Policy Realtime Update (Lock All Apps / App Block)
  if (msg.type === "device_policy_update") {
    updateDevicePolicyUI(msg);
    if (currentRole === "parent") {
      if (msg.is_all_apps_blocked) {
        showToast("🛑 Farzand telefonida barcha ilovalar bloklandi!");
      } else {
        showToast("✅ Farzand telefonida cheklovlar ochildi");
      }
    }
    return;
  }

  // 1.4 Realtime Screen Time Update
  if (msg.type === "screen_time_update") {
    if (document.getElementById("modalScreenTimeControl") && !document.getElementById("modalScreenTimeControl").classList.contains("hidden")) {
      loadScreenTimeAndPolicy();
    }
    return;
  }

  // 2. SOS Alert received by Parent
  if (msg.type === "sos_alert" && currentRole === "parent") {
    const child = childrenList.find((c) => c.id == msg.child_id);
    if (child) {
      child.is_sos_active = true;
    }
    renderChildrenSwitcher();

    if (msg.child_id == currentChildId) {
      toggleSosBanner(true);
      if (msg.latitude && msg.longitude) {
        renderChildMarker(msg.latitude, msg.longitude, child ? child.full_name : "Farzand", true, msg.battery_level);
        updateBatteryUI(msg.battery_level, msg.is_charging);
        if (map) map.flyTo([msg.latitude, msg.longitude], 16, { animate: true, duration: 1 });
      }
    } else {
      showToast(`🚨 SOS: ${msg.child_name || 'Farzandingiz'} xavf signalini yoqdi!`);
      playSirenSound();
      haptic("heavy");
    }
    return;
  }

  // 3. SOS Resolved received by Parent or Child
  if (msg.type === "sos_resolved") {
    if (currentRole === "parent") {
      const child = childrenList.find((c) => c.id == msg.child_id);
      if (child) {
        child.is_sos_active = false;
      }
      renderChildrenSwitcher();

      if (msg.child_id == currentChildId) {
        toggleSosBanner(false);
        showToast("✅ Xavf bartaraf etildi (SOS to'xtatildi)");
        if (childMarker) {
          const pos = childMarker.getLatLng();
          renderChildMarker(pos.lat, pos.lng, child?.full_name || "Farzand", false, child?.location?.battery_level || null);
        }
      }
    } else if (currentRole === "child") {
      const modal = document.getElementById("modal-sos-active");
      if (modal) modal.classList.add("hidden");
      if (childMarker) {
        const pos = childMarker.getLatLng();
        renderChildMarker(pos.lat, pos.lng, "Men", false, null);
      }
      showToast("✅ SOS xavf signali to'xtatildi.");
    }
    return;
  }

  // 4. Real-time location update received by Parent
  if (msg.type === "child_location_update" && currentRole === "parent") {
    if (msg.child_id == currentChildId) {
      const noLocCard = document.getElementById("noLocationCard");
      if (noLocCard) noLocCard.classList.add("hidden");
      const child = childrenList.find((c) => c.id == currentChildId);
      const name = child ? child.full_name : "Farzand";
      renderChildMarker(msg.latitude, msg.longitude, name, child?.is_sos_active, msg.battery_level);
      updateBatteryUI(msg.battery_level, msg.is_charging);
      updateSpeedUI(msg.speed);
      resolveAddress(msg.latitude, msg.longitude);
      const lastSeen = document.getElementById("lastSeenText");
      if (lastSeen) {
        lastSeen.innerHTML = `
          <span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
          <span>Hozirgina</span>
        `;
      }
    }
    return;
  }

  // 4.1 Walk Safe Started received by Parent
  if (msg.type === "walk_safe_started" && currentRole === "parent") {
    if (msg.child_id == currentChildId) {
      renderParentWalkSafeBanner({
        active: true,
        destination: msg.destination,
        deadline: msg.deadline,
        duration: msg.duration_minutes
      });
      showToast(`🚶‍♂️ ${msg.child_name || 'Farzandingiz'} "Xavfsiz Yo'l" rejimini yoqdi (${msg.duration_minutes} daqiqa)`);
    }
    return;
  }

  // 4.2 Walk Safe Check-in received by Parent
  if (msg.type === "walk_safe_checkin" && currentRole === "parent") {
    if (msg.child_id == currentChildId) {
      showToast("✅ Farzandingiz yo'lda hammasi yaxshiligini tasdiqladi");
    }
    return;
  }

  // 4.3 Walk Safe Completed received by Parent
  if (msg.type === "walk_safe_completed" && currentRole === "parent") {
    if (msg.child_id == currentChildId) {
      renderParentWalkSafeBanner(null);
      showToast(`🎉 Farzandingiz manzilga (${msg.destination || 'Uyga'}) eson-omon yetib keldi!`);
    }
    return;
  }

  // 5. Child deleted update received by Parent
  if (msg.type === "child_deleted" && currentRole === "parent") {
    childrenList = childrenList.filter((c) => c.id != msg.child_id);
    renderChildrenSwitcher();
    if (currentChildId == msg.child_id) {
      if (childrenList.length > 0) {
        switchChild(childrenList[0].id);
      } else {
        showToast("ℹ️ Barcha farzand hisoblari o'chirildi");
        window.location.reload();
      }
    }
    return;
  }

  // 6. Child linked update received by Parent
  if (msg.type === "child_linked" && currentRole === "parent") {
    showToast(`🎉 Yangi farzand (${msg.full_name || 'Farzand'}) muvaffaqiyatli ulandi!`);
    if (parentId) {
      fetch(`/api/v1/webapp/parent/${parentId}/children`)
        .then((r) => r.json())
        .then((data) => {
          if (data && data.status === "success" && data.children) {
            childrenList = data.children;
            renderChildrenSwitcher();
            if (!currentChildId && childrenList.length > 0) {
              switchChild(childrenList[0].id);
            }
          }
        })
        .catch(() => {});
    }
    return;
  }

  // 7. Safe arrival check-in received by Parent
  if (msg.type === "checkin" && currentRole === "parent") {
    haptic("medium");
    playSuccessChime();
    showToast(`✅ Farzandingiz (${msg.child_name || 'Farzand'}) manzilga eson-omon yetib keldi!`);
    if (msg.child_id == currentChildId) {
      updateLiveTelemetry(currentChildId);
    }
    return;
  }

  // 8. Task submitted with photo by Child
  if (msg.type === "task_submitted" && currentRole === "parent") {
    haptic("medium");
    showToast(`📋 Farzandingiz "${msg.task_title || 'topshiriq'}" bo'yicha hisobot rasmini yubordi!`);
    loadTasks();
    return;
  }

  // 9. Task approved by Parent
  if (msg.type === "task_approved") {
    if (currentRole === "child") {
      playSuccessChime();
      playCoinChime();
      showToast(`🏆 Topshiriq tasdiqlandi! +${(msg.reward_coins || 0).toLocaleString()} tanga xazinangizga qo'shildi!`);
      if (msg.new_balance !== undefined) {
        childBalanceCoins = msg.new_balance;
        childBalanceSom = Math.round(childBalanceCoins * (coinRateSom / Math.max(1, coinRateCoins)));
        const heroMoney = document.getElementById("childHeroMoneyText");
        if (heroMoney) heroMoney.textContent = `${childBalanceSom.toLocaleString()} so'm`;
        const bannerMoney = document.getElementById("childBannerMoney");
        if (bannerMoney) bannerMoney.textContent = `💵 Sizda hozir ${childBalanceSom.toLocaleString()} so'm pulingiz bor! (${childBalanceCoins.toLocaleString()} tanga)`;
        const wMoney = document.getElementById("childWalletMoneyText");
        if (wMoney) wMoney.textContent = `${childBalanceSom.toLocaleString()} so'm`;
      }
    }
    loadTasks();
    return;
  }

  // 10. Task rejected by Parent
  if (msg.type === "task_rejected") {
    if (currentRole === "child") {
      haptic("warning");
      showToast("❌ Topshiriq rad etildi. Qayta topshirib ko'ring.");
    }
    loadTasks();
    return;
  }
}

function startChildNotificationPolling(childId) {
  clearInterval(childNotificationPollTimer);
  childNotificationPollTimer = setInterval(() => {
    if (currentRole === "child" && currentChildId) {
      checkChildPendingNotifications(currentChildId);
    }
  }, 4000);
}

async function checkChildPendingNotifications(childId) {
  if (isDemoMode) return;
  try {
    const res = await fetch(`/api/v1/webapp/child/${childId}/notifications`);
    if (res.ok) {
      const data = await res.json();
      if (data.status === "success" && data.notifications && data.notifications.length > 0) {
        for (const notif of data.notifications) {
          if (notif.type === "active_check") {
            showChildActiveCheckBanner(notif.time || new Date().toLocaleTimeString().slice(0, 5));
          }
        }
      }
    }
  } catch (e) {}
}

function showChildActiveCheckBanner(time) {
  const banner = document.getElementById("childActiveCheckBanner");
  const timeText = document.getElementById("childCheckTimeText");
  if (timeText) timeText.textContent = time || new Date().toLocaleTimeString().slice(0, 5);
  if (banner) {
    banner.classList.remove("hidden");
    haptic("success");
    if (navigator.vibrate) {
      try { navigator.vibrate([150, 80, 150]); } catch (e) {}
    }
    clearTimeout(activeBannerTimer);
    activeBannerTimer = setTimeout(() => {
      dismissChildActiveBanner();
    }, 6000);
  }
}

function dismissChildActiveBanner() {
  const banner = document.getElementById("childActiveCheckBanner");
  if (banner) banner.classList.add("hidden");
}

// =========================================================
// 6. INVITATION LOGIC: CHILD INVITES PARENT & CO-PARENT (Ona <-> Ota)
// =========================================================

let childCurrentInviteLink = "";
let parentCurrentInviteLink = "";
const REAL_BOT_USERNAME = "child_security_bot";

async function openChildInviteParentModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  const modal = document.getElementById("childInviteParentModal");
  const linkInput = document.getElementById("childInviteLinkInput");
  const codeText = document.getElementById("childOwnCodeText");
  const demoWarning = document.getElementById("childInviteDemoWarning");

  let realChildId = null;
  if (authenticatedSession?.user?.id && authenticatedSession.user.id !== 2002) {
    realChildId = authenticatedSession.user.id;
  } else if (window.Telegram?.WebApp?.initDataUnsafe?.user?.id) {
    realChildId = window.Telegram.WebApp.initDataUnsafe.user.id;
  } else if (currentChildId && currentChildId !== 2002) {
    realChildId = currentChildId;
  }

  if (modal) {
    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
  }

  const fallbackCode = authenticatedSession?.user?.web_access_code || "------";
  if (codeText) codeText.textContent = fallbackCode;

  // Handle Demo mode or unauthenticated user
  if (!realChildId || isDemoMode) {
    if (demoWarning) demoWarning.classList.remove("hidden");
    childCurrentInviteLink = "";
    if (linkInput) linkInput.value = "⚠️ Avval shaxsiy 6 xonali kodingizni kiriting";
    return;
  }

  if (demoWarning) demoWarning.classList.add("hidden");

  try {
    const res = await fetch(`/api/v1/webapp/child/${realChildId}/invite-info`);
    if (res.ok) {
      const data = await res.json();
      if (data.status === "success") {
        childCurrentInviteLink = data.invite_link;
        if (linkInput) linkInput.value = data.invite_link;
        if (codeText) codeText.textContent = data.web_access_code || fallbackCode;
        return;
      }
    }
  } catch (e) {
    console.warn("Child invite info load error:", e);
  }

  // Guaranteed fallback using real child ID and actual bot username
  childCurrentInviteLink = `https://t.me/${REAL_BOT_USERNAME}?start=link_child_${realChildId}`;
  if (linkInput) linkInput.value = childCurrentInviteLink;
}

function closeChildInviteParentModal() {
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("childInviteParentModal");
  if (modal) modal.classList.add("hidden");
}

function copyChildInviteLink() {
  if (!childCurrentInviteLink) {
    haptic("warning");
    showToast("⚠️ Haqiqiy havolani olish uchun avval kodingizni kiriting!");
    promptRoleSwitch("child");
    return;
  }
  navigator.clipboard.writeText(childCurrentInviteLink).then(() => {
    haptic("light");
    showToast("📋 Ulanish havolasi nusxalandi!");
  }).catch(() => {
    showToast("Nusxa olindi");
  });
}

function shareChildInviteToTelegram() {
  let realChildId = null;
  if (authenticatedSession?.user?.id && authenticatedSession.user.id !== 2002) {
    realChildId = authenticatedSession.user.id;
  } else if (window.Telegram?.WebApp?.initDataUnsafe?.user?.id) {
    realChildId = window.Telegram.WebApp.initDataUnsafe.user.id;
  } else if (currentChildId && currentChildId !== 2002) {
    realChildId = currentChildId;
  }

  if (!realChildId || isDemoMode) {
    haptic("warning");
    showToast("⚠️ Siz sinov (demo) rejimidasiz! Avval shaxsiy 6 xonali kodingizni kiriting.");
    closeChildInviteParentModal();
    promptRoleSwitch("child");
    return;
  }

  if (!childCurrentInviteLink) {
    childCurrentInviteLink = `https://t.me/${REAL_BOT_USERNAME}?start=link_child_${realChildId}`;
  }

  const shareText = encodeURIComponent("Dada/Oyi, men Child Security botiga ulanyapman. Xavfsizligimni kuzatib turishingiz uchun ushbu havolani bosing!");
  const tgUrl = `https://t.me/share/url?url=${encodeURIComponent(childCurrentInviteLink)}&text=${shareText}`;
  if (window.Telegram?.WebApp?.openTelegramLink) {
    window.Telegram.WebApp.openTelegramLink(tgUrl);
  } else {
    window.open(tgUrl, "_blank");
  }
}

async function openParentInviteSpouseModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  const modal = document.getElementById("parentInviteSpouseModal");
  const linkInput = document.getElementById("parentInviteLinkInput");
  const demoWarning = document.getElementById("parentInviteDemoWarning");

  let realParentId = null;
  if (authenticatedSession?.user?.id && authenticatedSession.user.id !== 1001) {
    realParentId = authenticatedSession.user.id;
  } else if (window.Telegram?.WebApp?.initDataUnsafe?.user?.id) {
    realParentId = window.Telegram.WebApp.initDataUnsafe.user.id;
  } else if (parentId && parentId !== 1001) {
    realParentId = parentId;
  }

  if (modal) {
    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
  }

  if (!realParentId || isDemoMode) {
    if (demoWarning) demoWarning.classList.remove("hidden");
    parentCurrentInviteLink = "";
    if (linkInput) linkInput.value = "⚠️ Avval oilaviy 6 xonali kodingizni kiriting";
    return;
  }

  if (demoWarning) demoWarning.classList.add("hidden");

  try {
    const res = await fetch(`/api/v1/webapp/parent/${realParentId}/coparent-invite-info`);
    if (res.ok) {
      const data = await res.json();
      if (data.status === "success") {
        parentCurrentInviteLink = data.invite_link;
        if (linkInput) linkInput.value = data.invite_link;
        return;
      }
    }
  } catch (e) {
    console.warn("Parent coparent invite info load error:", e);
  }

  parentCurrentInviteLink = `https://t.me/${REAL_BOT_USERNAME}?start=coparent_${realParentId}`;
  if (linkInput) linkInput.value = parentCurrentInviteLink;
}

function closeParentInviteSpouseModal() {
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("parentInviteSpouseModal");
  if (modal) modal.classList.add("hidden");
}

function copyParentInviteLink() {
  if (!parentCurrentInviteLink) {
    haptic("warning");
    showToast("⚠️ Haqiqiy havolani olish uchun avval kodingizni kiriting!");
    promptRoleSwitch("parent");
    return;
  }
  navigator.clipboard.writeText(parentCurrentInviteLink).then(() => {
    haptic("light");
    showToast("📋 Hamkor havolasi nusxalandi!");
  }).catch(() => {
    showToast("Nusxa olindi");
  });
}

function shareParentInviteToTelegram() {
  let realParentId = null;
  if (authenticatedSession?.user?.id && authenticatedSession.user.id !== 1001) {
    realParentId = authenticatedSession.user.id;
  } else if (window.Telegram?.WebApp?.initDataUnsafe?.user?.id) {
    realParentId = window.Telegram.WebApp.initDataUnsafe.user.id;
  } else if (parentId && parentId !== 1001) {
    realParentId = parentId;
  }

  if (!realParentId || isDemoMode) {
    haptic("warning");
    showToast("⚠️ Siz sinov (demo) rejimidasiz! Avval oilaviy 6 xonali kodingizni kiriting.");
    closeParentInviteSpouseModal();
    promptRoleSwitch("parent");
    return;
  }

  if (!parentCurrentInviteLink) {
    parentCurrentInviteLink = `https://t.me/${REAL_BOT_USERNAME}?start=coparent_${realParentId}`;
  }

  const shareText = encodeURIComponent("Assalomu alaykum! Farzandlarimizni birgalikda kuzatishimiz uchun ushbu oilaviy havolani bosing.");
  const tgUrl = `https://t.me/share/url?url=${encodeURIComponent(parentCurrentInviteLink)}&text=${shareText}`;
  if (window.Telegram?.WebApp?.openTelegramLink) {
    window.Telegram.WebApp.openTelegramLink(tgUrl);
  } else {
    window.open(tgUrl, "_blank");
  }
}


// =========================================================
// MOBILE PHONE ERGONOMICS & TELEGRAM BACKBUTTON INTEGRATION
// =========================================================

function setupTelegramBackButton() {
  const tgApp = window.Telegram?.WebApp;
  if (tgApp) {
    try {
      if (typeof tgApp.disableClosingConfirmation === "function") {
        tgApp.disableClosingConfirmation();
      }
      if (tgApp.BackButton) {
        tgApp.BackButton.onClick(() => {
          handleTelegramBackPress();
        });
      }
    } catch (e) {
      console.warn("setupTelegramBackButton note:", e);
    }
  }

  // Intercept beforeunload so browser never warns about unsaved changes
  try {
    window.onbeforeunload = null;
    window.addEventListener("beforeunload", (e) => {
      delete e["returnValue"];
    });
  } catch (e) {}

  // Update BackButton state initially
  updateTelegramBackButton();
}

function handleTelegramBackPress() {
  haptic("light");
  const modalPriority = [
    { id: "modalHelp", close: closeHelpModal },
    { id: "modalInstallApp", close: closeInstallAppModal },
    { id: "modalCallParent", close: closeCallParentModal },
    { id: "modal-walk-safe", close: closeWalkSafeModal },
    { id: "modal-walk-safe-checkin", close: closeWalkSafeCheckInModal },
    { id: "modal-edit-wishlist", close: closeEditWishlistModal },
    { id: "modal-daily-report", close: closeDailyReportModal },
    { id: "childOnboardingModal", close: () => { const m = document.getElementById("childOnboardingModal"); if (m) m.classList.add("hidden"); } },
    { id: "deleteChildModal", close: closeDeleteChildModal },
    { id: "childInviteParentModal", close: closeChildInviteParentModal },
    { id: "parentInviteSpouseModal", close: closeParentInviteSpouseModal },
    { id: "modal-sos-active", close: () => { const m = document.getElementById("modal-sos-active"); if (m) m.classList.add("hidden"); } },
    { id: "modal-task-proof", close: closeProofModal },
    { id: "modal-task-schedule", close: closeScheduleModal },
    { id: "modal-add-task", close: closeAddTaskModal },
    { id: "zonePickerBottomDock", close: closeAddZoneModal },
    { id: "modal-coin-rate", close: closeCoinRateModal },
    { id: "modal-settings", close: closeSettingsModal },
    { id: "modalScreenTimeControl", close: closeScreenTimeControlModal },
    { id: "modalParentPinSetup", close: closeParentPinSetupModal },
    { id: "modalHelp", close: closeHelpModal },
    { id: "modalInstallApp", close: closeInstallAppModal },
    { id: "modalCallParent", close: closeCallParentModal },
    { id: "childOnboardingModal", close: () => { const m = document.getElementById("childOnboardingModal"); if (m) m.classList.add("hidden"); } },
    { id: "modalThemeSelector", close: closeThemeModal },
    { id: "modal-wallet", close: () => closeModal("wallet") },
    { id: "modal-history", close: () => closeModal("history") },
    { id: "modal-tasks", close: () => closeModal("tasks") },
    { id: "modal-zones", close: () => closeModal("zones") },
    { id: "authGateModal", close: hideAuthGateModal }
  ];

  for (const item of modalPriority) {
    const el = document.getElementById(item.id);
    if (el && !el.classList.contains("hidden")) {
      item.close();
      updateTelegramBackButton();
      return;
    }
  }

  const backBtn = document.getElementById("btnChildBackFromMap");
  if (backBtn && !backBtn.classList.contains("hidden")) {
    switchTab("map");
    updateTelegramBackButton();
    return;
  }

  const activeTab = document.querySelector(".nav-tab.active");
  if (activeTab && activeTab.id !== "tabBtn-map") {
    switchTab("map");
    updateTelegramBackButton();
    return;
  }

  // If no modals are open and user is on the main screen, exit WebApp back to Telegram!
  closeWebApp();
}

function closeWebApp() {
  haptic("medium");
  if (window.AndroidBridge && typeof window.AndroidBridge.minimizeApp === "function") {
    window.AndroidBridge.minimizeApp();
    return;
  }

  const tgApp = window.Telegram?.WebApp;
  if (tgApp) {
    try {
      if (typeof tgApp.disableClosingConfirmation === "function") {
        tgApp.disableClosingConfirmation();
      }
    } catch (e) {}
    try {
      if (typeof tgApp.close === "function") {
        tgApp.close();
      }
    } catch (e) {}
  }

  try {
    window.close();
  } catch (e) {}

  // Instant fallback to return directly to Telegram chat
  setTimeout(() => {
    try {
      window.location.href = "https://t.me/child_security_bot";
    } catch (e) {}
  }, 120);
}

function updateTelegramBackButton() {
  const tgApp = window.Telegram?.WebApp;
  if (!tgApp || !tgApp.BackButton) return;

  const openModal = document.querySelector(
    "#modal-tasks:not(.hidden), #modal-wallet:not(.hidden), #modal-history:not(.hidden), " +
    "#modal-zones:not(.hidden), #modal-settings:not(.hidden), #modalInstallApp:not(.hidden), " +
    "#modalHelp:not(.hidden), #modal-daily-report:not(.hidden), #modal-add-task:not(.hidden), " +
    "#modal-task-schedule:not(.hidden), #modal-task-proof:not(.hidden), #modal-edit-wishlist:not(.hidden), " +
    "#modal-walk-safe-checkin:not(.hidden), #parentInviteSpouseModal:not(.hidden), " +
    "#modalCallParent:not(.hidden), #modal-walk-safe:not(.hidden), #modal-coin-rate:not(.hidden), " +
    "#deleteChildModal:not(.hidden), #childInviteParentModal:not(.hidden), #zonePickerBottomDock:not(.hidden), #modalScreenTimeControl:not(.hidden), #modalParentPinSetup:not(.hidden), " +
    "#modalThemeSelector:not(.hidden)"
  );

  const activeTab = document.querySelector(".nav-tab.active");
  const isSubTab = activeTab && activeTab.id !== "tabBtn-map";

  try {
    if (openModal || isSubTab) {
      // Sub-screen is open: show BackButton to return to map
      tgApp.BackButton.show();
    } else {
      // Main screen: HIDE BackButton so Telegram natively displays its own Close [X] button!
      tgApp.BackButton.hide();
    }
  } catch (e) {}
}

function initBackdropDismissListeners() {
  const modalList = [
    { id: "modal-walk-safe", close: closeWalkSafeModal },
    { id: "modal-walk-safe-checkin", close: closeWalkSafeCheckInModal },
    { id: "modal-edit-wishlist", close: closeEditWishlistModal },
    { id: "modal-daily-report", close: closeDailyReportModal },
    { id: "modal-zones", close: () => closeModal("zones") },
    { id: "modal-history", close: () => closeModal("history") },
    { id: "modal-tasks", close: () => closeModal("tasks") },
    { id: "modal-wallet", close: () => closeModal("wallet") },
    { id: "modal-settings", close: closeSettingsModal },
    { id: "zonePickerBottomDock", close: closeAddZoneModal },
    { id: "modal-add-task", close: closeAddTaskModal },
    { id: "modal-task-schedule", close: closeScheduleModal },
    { id: "modal-task-proof", close: closeProofModal },
    { id: "modal-coin-rate", close: closeCoinRateModal },
    { id: "deleteChildModal", close: closeDeleteChildModal },
    { id: "childInviteParentModal", close: closeChildInviteParentModal },
    { id: "parentInviteSpouseModal", close: closeParentInviteSpouseModal }
  ];

  modalList.forEach(item => {
    const el = document.getElementById(item.id);
    if (el) {
      el.addEventListener("click", (e) => {
        if (e.target === el) {
          haptic("light");
          item.close();
          updateTelegramBackButton();
        }
      });
    }
  });
}


// ============================================================================
// 5 YANGI XAVFSIZLIK VA INTERAKTIV FUNKSIYALAR LOGIKASI (v29)
// ============================================================================

// ----------------------------------------------------------------------------
// FUNKSIYA 2: SOS 5-SONIYALIK OVOZ YOZUVI (Ambient Audio Snapshot)
// ----------------------------------------------------------------------------
async function recordAndUploadSOSAudio(childId) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    console.log("MediaDevices not supported on this browser/environment");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    const mimeType = (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported("audio/webm;codecs=opus"))
      ? "audio/webm;codecs=opus"
      : (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported("audio/ogg;codecs=opus"))
      ? "audio/ogg;codecs=opus"
      : (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported("audio/mp4"))
      ? "audio/mp4"
      : "";

    const options = mimeType ? { mimeType } : {};
    const mediaRecorder = new MediaRecorder(stream, options);
    const chunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      // Mikrofonni zudlik bilan bo'shatish
      try {
        stream.getTracks().forEach((track) => track.stop());
      } catch (e) {}

      if (chunks.length === 0) return;

      const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
      const ext = (mimeType && mimeType.includes("mp4")) ? "m4a" : "webm";
      const formData = new FormData();
      formData.append("file", blob, `sos_audio_${Date.now()}.${ext}`);

      try {
        await fetch(`/api/v1/webapp/child/${childId}/sos/media`, {
          method: "POST",
          body: formData,
        });
        console.log("SOS audio snapshot successfully dispatched to parents");
      } catch (err) {
        console.warn("Failed to upload SOS audio snapshot:", err);
      }
    };

    mediaRecorder.start();
    // 5 soniya davomida yozib avtomatik to'xtatish
    setTimeout(() => {
      if (mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
      }
    }, 5000);
  } catch (err) {
    console.warn("SOS ambient microphone capture skipped/denied:", err);
  }
}

// ----------------------------------------------------------------------------
// FUNKSIYA 3: TEZLIKNI KO'RSATISH (Speed Badge)
// ----------------------------------------------------------------------------
function updateSpeedUI(speed) {
  const badge = document.getElementById("speedBadge");
  const val = document.getElementById("speedValue");
  const dynIslandText = document.getElementById("dynamicIslandText");
  const dynIslandDot = document.getElementById("dynamicIslandDot");
  if (!badge || !val) return;

  const kmh = Math.round(Number(speed) || 0);
  if (kmh > 0) {
    val.textContent = `${kmh} km/h`;
    if (kmh >= 30) {
      badge.className = "text-[9px] bg-rose-500/25 text-rose-300 border border-rose-500/50 px-2 py-0.5 rounded-full font-bold flex items-center gap-1 animate-pulse shadow-sm";
      if (dynIslandText) dynIslandText.textContent = `🚗 ${kmh} km/h (Tezlik!)`;
      if (dynIslandDot) dynIslandDot.className = "dynamic-island-dot bg-amber-400";
    } else {
      badge.className = "text-[9px] bg-amber-500/20 text-amber-300 border border-amber-500/30 px-2 py-0.5 rounded-full font-bold flex items-center gap-1";
      if (dynIslandText && !walkSafeActive) dynIslandText.textContent = `🚗 ${kmh} km/h`;
    }
    badge.classList.remove("hidden");
    if (window.lucide) window.lucide.createIcons();
  } else {
    badge.classList.add("hidden");
    if (dynIslandText && !walkSafeActive && !lastSosAlertState) {
      dynIslandText.textContent = "Jonli GPS faol";
      if (dynIslandDot) dynIslandDot.className = "dynamic-island-dot";
    }
  }
}

// ----------------------------------------------------------------------------
// FUNKSIYA 1: "XAVFSIZ YO'L" TAYMERI (Walk Safe / Meni kuzatib turing)
// ----------------------------------------------------------------------------
let walkSafeActive = false;
let walkSafeDurationMinutes = 20;
let walkSafeDestination = "Uyga qaytish 🏠";
let walkSafeTimer = null;
let walkSafeCheckInTimer = null;
let walkSafeDeadlineTs = null;

function openWalkSafeModal() {
  haptic("light");
  const modal = document.getElementById("modal-walk-safe");
  if (modal) {
    modal.classList.remove("hidden");
    setWalkSafeDuration(walkSafeDurationMinutes || 20);
    const input = document.getElementById("inputWalkSafeDestination");
    if (input && walkSafeDestination) input.value = walkSafeDestination;
    if (window.lucide) window.lucide.createIcons();
  }
  updateTelegramBackButton();
}

function closeWalkSafeModal() {
  haptic("light");
  const modal = document.getElementById("modal-walk-safe");
  if (modal) modal.classList.add("hidden");
  updateTelegramBackButton();
}

function setWalkSafeDestination(dest) {
  walkSafeDestination = dest;
  const input = document.getElementById("inputWalkSafeDestination");
  if (input) input.value = dest;
}

function setWalkSafeDuration(mins) {
  walkSafeDurationMinutes = parseInt(mins) || 20;
  const disp = document.getElementById("walkSafeDurationDisplay");
  if (disp) disp.textContent = `${walkSafeDurationMinutes} daqiqa`;
  const slider = document.getElementById("inputWalkSafeSlider");
  if (slider) slider.value = walkSafeDurationMinutes;
}

async function confirmStartWalkSafe() {
  haptic("medium");
  const input = document.getElementById("inputWalkSafeDestination");
  const dest = input?.value?.trim() || walkSafeDestination || "Uyga qaytish 🏠";
  walkSafeDestination = dest;

  const btn = document.getElementById("btnStartWalkSafeConfirm");
  if (btn) btn.disabled = true;

  try {
    if (!isDemoMode && currentChildId) {
      const res = await fetch(`/api/v1/webapp/child/${currentChildId}/walk-safe/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          duration_minutes: walkSafeDurationMinutes,
          destination: dest
        })
      });
      const data = await res.json();
      if (data.status === "success" && data.walk_safe) {
        startLocalWalkSafeCountdown(data.walk_safe.deadline, dest);
      } else {
        const deadline = new Date(Date.now() + walkSafeDurationMinutes * 60000).toISOString();
        startLocalWalkSafeCountdown(deadline, dest);
      }
    } else {
      const deadline = new Date(Date.now() + walkSafeDurationMinutes * 60000).toISOString();
      startLocalWalkSafeCountdown(deadline, dest);
    }

    closeWalkSafeModal();
    showToast("🚶‍♂️ Xavfsiz Yo'l rejimi boshlandi! Ota-onangizga xabar yuborildi.");
  } catch (err) {
    console.error("Walk safe start error:", err);
    showToast("⚠️ Xavfsiz yo'l rejimini yoqishda xatolik");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function startLocalWalkSafeCountdown(deadlineIso, destination) {
  walkSafeActive = true;
  walkSafeDeadlineTs = deadlineIso ? new Date(deadlineIso).getTime() : Date.now() + 20 * 60000;
  walkSafeDestination = destination || "Uyga qaytish";

  // Bolaning faol yo'l paneli
  const childBar = document.getElementById("childActiveWalkSafeBar");
  if (childBar) childBar.classList.remove("hidden");

  // Sarguzasht markazidagi kartani yangilash
  const cardSub = document.getElementById("walkSafeCardSubtext");
  if (cardSub) cardSub.textContent = `Faol: ${walkSafeDestination} tomon`;
  const btnCard = document.getElementById("btnOpenWalkSafeModal");
  if (btnCard) {
    btnCard.textContent = "Holat";
    btnCard.onclick = () => showToast(`🚶‍♂️ Yo'ldasiz: ${walkSafeDestination}`);
  }

  clearInterval(walkSafeTimer);
  updateWalkSafeCountdownDisplay();
  walkSafeTimer = setInterval(updateWalkSafeCountdownDisplay, 1000);

  // Har 5 daqiqada "Ahvolingiz yaxshimi?" deb so'rash
  clearInterval(walkSafeCheckInTimer);
  walkSafeCheckInTimer = setInterval(() => {
    if (walkSafeActive && currentRole === "child") {
      openWalkSafeCheckInModal();
    }
  }, 5 * 60 * 1000);
}

function updateWalkSafeCountdownDisplay() {
  if (!walkSafeActive || !walkSafeDeadlineTs) return;
  const now = Date.now();
  const diffSec = Math.max(0, Math.floor((walkSafeDeadlineTs - now) / 1000));

  const mins = Math.floor(diffSec / 60);
  const secs = diffSec % 60;
  const timeStr = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

  // Bola ekrani
  const cdText = document.getElementById("childWalkSafeCountdown");
  if (cdText) {
    cdText.textContent = `${timeStr} qoldi (${walkSafeDestination})`;
  }

  // Ota-ona xarita ustidagi banner
  const parentBanner = document.getElementById("parentWalkSafeBanner");
  const parentText = document.getElementById("parentWalkSafeText");
  if (parentBanner && !parentBanner.classList.contains("hidden") && parentText) {
    parentText.textContent = `${timeStr} qoldi (${walkSafeDestination})`;
  }

  // Dynamic Island status pill
  const dynIslandText = document.getElementById("dynamicIslandText");
  const dynIslandDot = document.getElementById("dynamicIslandDot");
  if (dynIslandText) {
    dynIslandText.textContent = `🚶‍♂️ ${timeStr} (${walkSafeDestination})`;
    if (dynIslandDot) dynIslandDot.className = "dynamic-island-dot bg-amber-400";
  }

  if (diffSec <= 0) {
    clearInterval(walkSafeTimer);
    if (currentRole === "child") {
      showToast("⏰ Xavfsiz yo'l vaqti tugadi! Ota-onangizga xabar qilindi.");
    }
  }
}

function openWalkSafeCheckInModal() {
  const modal = document.getElementById("modal-walk-safe-checkin");
  if (modal) {
    modal.classList.remove("hidden");
    haptic("medium");
    playPingSound();
    if (window.lucide) window.lucide.createIcons();
  }
  updateTelegramBackButton();
}

function closeWalkSafeCheckInModal() {
  const modal = document.getElementById("modal-walk-safe-checkin");
  if (modal) modal.classList.add("hidden");
  updateTelegramBackButton();
}

async function confirmWalkSafeCheckInModal() {
  haptic("light");
  closeWalkSafeCheckInModal();
  showToast("✅ Ahvolingiz yaxshiligi qayd etildi!");

  if (!isDemoMode && currentChildId) {
    try {
      await fetch(`/api/v1/webapp/child/${currentChildId}/walk-safe/checkin`, {
        method: "POST"
      });
    } catch (e) {
      console.warn("Checkin send error:", e);
    }
  }
}

function childWalkSafeCheckIn() {
  confirmWalkSafeCheckInModal();
}

async function childWalkSafeComplete() {
  haptic("heavy");
  clearInterval(walkSafeTimer);
  clearInterval(walkSafeCheckInTimer);
  walkSafeActive = false;

  const childBar = document.getElementById("childActiveWalkSafeBar");
  if (childBar) childBar.classList.add("hidden");
  const dynIslandText = document.getElementById("dynamicIslandText");
  const dynIslandDot = document.getElementById("dynamicIslandDot");
  if (dynIslandText) {
    dynIslandText.textContent = "Jonli GPS faol";
    if (dynIslandDot) dynIslandDot.className = "dynamic-island-dot";
  }

  const cardSub = document.getElementById("walkSafeCardSubtext");
  if (cardSub) cardSub.textContent = "Meni kuzatib turing (Xavfsiz yetib olish)";
  const btnCard = document.getElementById("btnOpenWalkSafeModal");
  if (btnCard) {
    btnCard.textContent = "Boshlash";
    btnCard.onclick = openWalkSafeModal;
  }

  showToast("🎉 Manzilga eson-omon yetib keldingiz! Ota-onangizga xabar yetkazildi.");

  if (!isDemoMode && currentChildId) {
    try {
      await fetch(`/api/v1/webapp/child/${currentChildId}/walk-safe/complete`, {
        method: "POST"
      });
    } catch (e) {
      console.warn("Complete walk safe error:", e);
    }
  }
}

function renderParentWalkSafeBanner(walkSafe) {
  const banner = document.getElementById("parentWalkSafeBanner");
  const text = document.getElementById("parentWalkSafeText");
  if (!banner || !text) return;

  if (walkSafe && walkSafe.active && walkSafe.deadline) {
    walkSafeDestination = walkSafe.destination || "Uyga";
    walkSafeDeadlineTs = new Date(walkSafe.deadline).getTime();
    walkSafeActive = true;
    banner.classList.remove("hidden");
    clearInterval(walkSafeTimer);
    updateWalkSafeCountdownDisplay();
    walkSafeTimer = setInterval(updateWalkSafeCountdownDisplay, 1000);
    if (window.lucide) window.lucide.createIcons();
  } else {
    banner.classList.add("hidden");
    walkSafeActive = false;
    clearInterval(walkSafeTimer);
    const dynIslandText = document.getElementById("dynamicIslandText");
    const dynIslandDot = document.getElementById("dynamicIslandDot");
    if (dynIslandText && !lastSosAlertState) {
      dynIslandText.textContent = "Jonli GPS faol";
      if (dynIslandDot) dynIslandDot.className = "dynamic-island-dot";
    }
  }
}

// ----------------------------------------------------------------------------
// FUNKSIYA 4: "ORZULAR QUTISI" (Wishlist / Bolaning maqsadi)
// ----------------------------------------------------------------------------
let currentWishlistData = {
  title: "Yangi Velosiped 🚲",
  target_som: 600000,
  balance: 0,
  percent: 0,
  remaining_som: 600000,
  is_reached: false
};

async function loadWishlist(childId) {
  if (!childId) return;
  try {
    const res = await fetch(`/api/v1/webapp/child/${childId}/wishlist`);
    const data = await res.json();
    if (data.status === "success" && data.wishlist) {
      currentWishlistData = data.wishlist;
      renderWishlistUI(currentWishlistData);
    }
  } catch (err) {
    console.warn("Failed to load wishlist:", err);
  }
}

function renderWishlistUI(wl) {
  if (!wl) return;
  const titleEl = document.getElementById("wishlistTitleText");
  if (titleEl) titleEl.textContent = `Maqsad: ${wl.title || 'Yangi Velosiped 🚲'}`;

  const pct = Math.min(100, Math.max(0, wl.percent || 0));
  const pctEl = document.getElementById("wishlistPercentBadge");
  if (pctEl) pctEl.textContent = `${pct}%`;

  const barEl = document.getElementById("wishlistProgressBar");
  if (barEl) barEl.style.width = `${pct}%`;

  const moneyEl = document.getElementById("wishlistMoneyProgress");
  if (moneyEl) {
    const balStr = (wl.balance || 0).toLocaleString();
    const tgtStr = (wl.target_som || 600000).toLocaleString();
    moneyEl.textContent = `${balStr} / ${tgtStr} so'm`;
  }

  const remEl = document.getElementById("wishlistRemainingText");
  if (remEl) {
    if (wl.is_reached || pct >= 100) {
      remEl.textContent = "🎉 Orzu ushaldi! Tabriklaymiz!";
      remEl.className = "text-amber-300 font-bold animate-pulse";
    } else {
      const rem = (wl.remaining_som || Math.max(0, (wl.target_som || 0) - (wl.balance || 0))).toLocaleString();
      remEl.textContent = `Yana ${rem} so'm qoldi`;
      remEl.className = "text-emerald-300 font-semibold";
    }
  }
}

function openEditWishlistModal() {
  haptic("light");
  const modal = document.getElementById("modal-edit-wishlist");
  if (modal) {
    modal.classList.remove("hidden");
    const tInput = document.getElementById("inputWishlistTitle");
    if (tInput) tInput.value = currentWishlistData.title || "Yangi Velosiped 🚲";
    const aInput = document.getElementById("inputWishlistTargetSom");
    if (aInput) aInput.value = currentWishlistData.target_som || 600000;
    if (window.lucide) window.lucide.createIcons();
  }
  updateTelegramBackButton();
}

function closeEditWishlistModal() {
  haptic("light");
  const modal = document.getElementById("modal-edit-wishlist");
  if (modal) modal.classList.add("hidden");
  updateTelegramBackButton();
}

function setWishlistPreset(title, amount) {
  haptic("light");
  const tInput = document.getElementById("inputWishlistTitle");
  if (tInput) tInput.value = title;
  const aInput = document.getElementById("inputWishlistTargetSom");
  if (aInput) aInput.value = amount;
}

async function saveWishlistConfirm() {
  haptic("medium");
  const tInput = document.getElementById("inputWishlistTitle");
  const aInput = document.getElementById("inputWishlistTargetSom");
  const title = tInput?.value?.trim() || "Yangi Velosiped 🚲";
  const targetSom = parseInt(aInput?.value) || 600000;

  if (targetSom < 1000) {
    showToast("⚠️ Miqdor kamida 1,000 so'm bo'lishi kerak");
    return;
  }

  const btn = document.getElementById("btnSaveWishlistConfirm");
  if (btn) btn.disabled = true;

  try {
    if (!isDemoMode && currentChildId) {
      const res = await fetch(`/api/v1/webapp/child/${currentChildId}/wishlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, target_som: targetSom })
      });
      const data = await res.json();
      if (data.status === "success" && data.wishlist) {
        currentWishlistData = data.wishlist;
        renderWishlistUI(currentWishlistData);
      }
    } else {
      currentWishlistData.title = title;
      currentWishlistData.target_som = targetSom;
      currentWishlistData.percent = Math.min(100, Math.round((currentWishlistData.balance / targetSom) * 100));
      renderWishlistUI(currentWishlistData);
    }

    closeEditWishlistModal();
    showToast("🎯 Yangi orzu va maqsad muvaffaqiyatli saqlandi!");
  } catch (err) {
    console.error("Save wishlist error:", err);
    showToast("⚠️ Saqlashda xatolik yuz berdi");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ----------------------------------------------------------------------------
// FUNKSIYA 5: KUNLIK XAVFSIZLIK HISOBOTI (Daily Safety Report)
// ----------------------------------------------------------------------------
async function openDailyReportModal() {
  haptic("light");
  const modal = document.getElementById("modal-daily-report");
  if (modal) {
    modal.classList.remove("hidden");
    renderDailyReportLoading();
    if (window.lucide) window.lucide.createIcons();
  }
  updateTelegramBackButton();

  if (!currentChildId) return;

  try {
    const res = await fetch(`/api/v1/webapp/child/${currentChildId}/daily-report`);
    const data = await res.json();
    if (data.status === "success" && data.report) {
      renderDailyReportContent(data.report);
    } else {
      renderDailyReportError("Hisobot ma'lumotlarini yuklab bo'lmadi.");
    }
  } catch (err) {
    console.error("Daily report fetch error:", err);
    renderDailyReportError("Tarmoq xatosi. Qaytadan urinib ko'ring.");
  }
}

function closeDailyReportModal() {
  haptic("light");
  const modal = document.getElementById("modal-daily-report");
  if (modal) modal.classList.add("hidden");
  updateTelegramBackButton();
}

function renderDailyReportLoading() {
  const container = document.getElementById("dailyReportContent");
  if (container) {
    container.innerHTML = `
      <div class="py-8 flex flex-col items-center justify-center gap-3">
        <div class="w-8 h-8 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin"></div>
        <p class="text-xs text-slate-400">Kunlik xavfsizlik infografikasi yuklanmoqda...</p>
      </div>
    `;
  }
}

function renderDailyReportError(msg) {
  const container = document.getElementById("dailyReportContent");
  if (container) {
    container.innerHTML = `
      <div class="py-6 text-center text-xs text-rose-400">
        <i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2 opacity-80"></i>
        <p>${msg}</p>
      </div>
    `;
    if (window.lucide) window.lucide.createIcons();
  }
}

function renderDailyReportContent(report) {
  const container = document.getElementById("dailyReportContent");
  if (!container) return;

  const zones = report.visited_zones || [];
  const zonesBadges = zones.length > 0
    ? zones.map(z => `<span class="px-2 py-0.5 rounded-md bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[11px] font-bold">📍 ${z}</span>`).join(" ")
    : `<span class="text-xs text-slate-400">Belgilangan zonalarga tashrif qayd etilmadi</span>`;

  const safeHours = Math.floor((report.safe_zone_minutes || 0) / 60);
  const safeMins = (report.safe_zone_minutes || 0) % 60;
  const safeTimeStr = safeHours > 0 ? `${safeHours} soat ${safeMins} daqiqa` : `${safeMins} daqiqa`;

  const sosStatusHtml = report.is_sos_triggered
    ? `<div class="p-2.5 rounded-xl bg-rose-500/20 border border-rose-500/40 text-rose-300 text-xs font-semibold flex items-center gap-2">
         <i data-lucide="alert-triangle" class="w-4 h-4 text-rose-400 shrink-0"></i>
         <span>Kun davomida SOS xavf signali faollashtirilgan!</span>
       </div>`
    : `<div class="p-2.5 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs font-semibold flex items-center gap-2">
         <i data-lucide="shield-check" class="w-4 h-4 text-emerald-400 shrink-0"></i>
         <span>Kun davomida tinchlik va xavfsizlik to'liq ta'minlandi</span>
       </div>`;

  container.innerHTML = `
    <!-- Sana sarlavhasi -->
    <div class="flex items-center justify-between p-3 rounded-2xl bg-slate-800/80 border border-slate-700/60">
      <div>
        <p class="text-[10px] text-slate-400 font-bold uppercase tracking-wider">Hisobot sanasi</p>
        <p class="text-sm font-black text-white">${report.date || 'Bugun'}</p>
      </div>
      <div class="text-right">
        <p class="text-[10px] text-slate-400 font-bold uppercase tracking-wider">Lokatsiya nuqtalari</p>
        <p class="text-sm font-black text-indigo-300">${report.total_pings || 0} ta</p>
      </div>
    </div>

    <!-- SOS status banner -->
    ${sosStatusHtml}

    <!-- Asosiy ko'rsatkichlar kartalari (Grid 2x2) -->
    <div class="grid grid-cols-2 gap-2.5">
      <!-- 1. Masofa va Tezlik -->
      <div class="p-3 rounded-2xl bg-slate-800/60 border border-slate-700/50 flex flex-col justify-between">
        <div class="flex items-center justify-between">
          <span class="text-[10px] text-slate-400 font-bold uppercase">Yurilgan masofa</span>
          <i data-lucide="map-pin" class="w-3.5 h-3.5 text-amber-400"></i>
        </div>
        <p class="text-base font-black text-white mt-1">${report.total_distance_km || 0} <span class="text-xs font-normal text-slate-400">km</span></p>
        <p class="text-[10px] text-amber-300/90 font-semibold mt-1">Maks. tezlik: ${report.max_speed_kmh || 0} km/h</p>
      </div>

      <!-- 2. Xavfsiz zonada bo'lish vaqti -->
      <div class="p-3 rounded-2xl bg-slate-800/60 border border-slate-700/50 flex flex-col justify-between">
        <div class="flex items-center justify-between">
          <span class="text-[10px] text-slate-400 font-bold uppercase">Xavfsiz joylarda</span>
          <i data-lucide="shield" class="w-3.5 h-3.5 text-emerald-400"></i>
        </div>
        <p class="text-base font-black text-emerald-300 mt-1">${safeTimeStr}</p>
        <p class="text-[10px] text-slate-400 mt-1">Belgilangan geozonalar</p>
      </div>

      <!-- 3. Topshiriqlar va Daromad -->
      <div class="p-3 rounded-2xl bg-slate-800/60 border border-slate-700/50 flex flex-col justify-between">
        <div class="flex items-center justify-between">
          <span class="text-[10px] text-slate-400 font-bold uppercase">Topshiriqlar</span>
          <i data-lucide="check-circle" class="w-3.5 h-3.5 text-teal-400"></i>
        </div>
        <p class="text-base font-black text-teal-300 mt-1">${report.tasks_completed || 0} <span class="text-xs font-normal text-slate-400">ta</span></p>
        <p class="text-[10px] text-emerald-400 font-semibold mt-1">+${(report.earnings_som || 0).toLocaleString()} so'm</p>
      </div>

      <!-- 4. Batareya quvvati -->
      <div class="p-3 rounded-2xl bg-slate-800/60 border border-slate-700/50 flex flex-col justify-between">
        <div class="flex items-center justify-between">
          <span class="text-[10px] text-slate-400 font-bold uppercase">O'rtacha quvvat</span>
          <i data-lucide="battery" class="w-3.5 h-3.5 text-indigo-400"></i>
        </div>
        <p class="text-base font-black text-indigo-300 mt-1">${report.avg_battery || 0}%</p>
        <p class="text-[10px] text-slate-400 mt-1">Kunlik batareya holati</p>
      </div>
    </div>

    <!-- Tashrif buyurilgan zonalar ro'yxati -->
    <div class="p-3 rounded-2xl bg-slate-800/60 border border-slate-700/50">
      <p class="text-[10px] text-slate-400 font-bold uppercase mb-2">Tashrif buyurilgan xavfsiz zonalar</p>
      <div class="flex flex-wrap gap-1.5">
        ${zonesBadges}
      </div>
    </div>
  `;

  if (window.lucide) window.lucide.createIcons();
}

// Global Window Exports
window.openWalkSafeModal = openWalkSafeModal;
window.closeWalkSafeModal = closeWalkSafeModal;
window.setWalkSafeDestination = setWalkSafeDestination;
window.setWalkSafeDuration = setWalkSafeDuration;
window.confirmStartWalkSafe = confirmStartWalkSafe;
window.childWalkSafeCheckIn = childWalkSafeCheckIn;
window.childWalkSafeComplete = childWalkSafeComplete;
window.openWalkSafeCheckInModal = openWalkSafeCheckInModal;
window.closeWalkSafeCheckInModal = closeWalkSafeCheckInModal;
window.confirmWalkSafeCheckInModal = confirmWalkSafeCheckInModal;

window.openEditWishlistModal = openEditWishlistModal;
window.closeEditWishlistModal = closeEditWishlistModal;
window.setWishlistPreset = setWishlistPreset;
window.saveWishlistConfirm = saveWishlistConfirm;
window.loadWishlist = loadWishlist;

window.openDailyReportModal = openDailyReportModal;
window.closeDailyReportModal = closeDailyReportModal;

// =========================================================
// PWA & SERVICE WORKER REGISTRATION + NATIVE INSTALL ENGINE
// =========================================================
let deferredInstallPrompt = null;

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js")
      .then((reg) => {
        console.log("Child Security PWA Service Worker registered:", reg.scope);
      })
      .catch((err) => {
        console.warn("Service Worker registration note:", err);
      });
  });
}

// Capture Android/Chrome install banner prompt
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
});

function openInstallAppModal(targetTab) {
  haptic("medium");
  const modal = document.getElementById("modalInstallApp");
  if (modal) {
    modal.classList.remove("hidden");
    if (targetTab) {
      switchInstallTab(targetTab);
    } else {
      const isIos = /iPhone|iPad|iPod/i.test(navigator.userAgent);
      const isDesktop = !/Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
      if (isIos) {
        switchInstallTab("ios");
      } else if (isDesktop && window.innerWidth >= 768) {
        switchInstallTab("desktop");
      } else {
        switchInstallTab("android");
      }
    }
    if (window.lucide) lucide.createIcons();
  }
}

function closeInstallAppModal() {
  haptic("light");
  const modal = document.getElementById("modalInstallApp");
  if (modal) modal.classList.add("hidden");
}

function switchInstallTab(tab) {
  haptic("light");
  ["android", "ios", "desktop", "pwa"].forEach((t) => {
    const pane = document.getElementById(`installTabPane-${t}`);
    const btn = document.getElementById(`installTabBtn-${t}`);
    if (pane) {
      if (t === tab) pane.classList.remove("hidden");
      else pane.classList.add("hidden");
    }
    if (btn) {
      if (t === tab) {
        btn.className = "flex-1 py-2 px-1 rounded-xl text-[10px] sm:text-xs font-black bg-blue-600 text-white shadow-md transition flex items-center justify-center gap-1";
      } else {
        btn.className = "flex-1 py-2 px-1 rounded-xl text-[10px] sm:text-xs font-bold text-slate-400 hover:text-white bg-slate-800/60 transition flex items-center justify-center gap-1";
      }
    }
  });
  if (window.lucide) lucide.createIcons();
}

async function triggerPwaInstall() {
  haptic("medium");
  if (deferredInstallPrompt) {
    deferredInstallPrompt.prompt();
    const { outcome } = await deferredInstallPrompt.userChoice;
    console.log("Install prompt outcome:", outcome);
    deferredInstallPrompt = null;
    closeInstallAppModal();
    showToast("🎉 Ilova muvaffaqiyatli o'rnatildi!");
  } else {
    const isDesktop = !/Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
    if (isDesktop) {
      showToast("ℹ️ Noutbuk brauzeringiz manzil satridagi (URL o'ng tomoni) 💻 O'rnatish belgisini bosing!");
    } else {
      showToast("ℹ️ Brauzeringiz menyusidan 'Asosiy ekranga qo'shish'ni tanlang");
    }
  }
}

// =========================================================
// NATIVE ANDROID BRIDGE & PRO APP INTEGRATION
// =========================================================
function syncWithNativeAndroidBridge() {
  const isNative = Boolean(
    window.AndroidBridge ||
    window.location.protocol === "file:" ||
    new URLSearchParams(window.location.search).get("source") === "apk" ||
    new URLSearchParams(window.location.search).get("source") === "native_apk"
  );

  const nativeStatusCard = document.getElementById("nativeAppStatusCard");
  const childDownloadCard = document.getElementById("childAppDownloadCard");
  const parentBanner = document.getElementById("parentAppDownloadBanner");

  if (isNative) {
    if (nativeStatusCard) nativeStatusCard.classList.remove("hidden");
    if (childDownloadCard) childDownloadCard.classList.add("hidden");
    if (parentBanner) parentBanner.classList.add("hidden");

    // Auto-register child with native background service if child logged in
    if (window.AndroidBridge && currentRole === "child" && currentChildId) {
      try {
        window.AndroidBridge.startBackgroundTracking(String(currentChildId), REMOTE_API_HOST);
      } catch(e) {}
    }
  } else {
    // Web visitor: check if user previously dismissed banner
    try {
      const dismissed = localStorage.getItem("hide_app_banner") === "1";
      if (parentBanner && !dismissed && currentRole === "parent") {
        parentBanner.classList.remove("hidden");
      }
    } catch(e) {}
  }
}

function requestNativeBatteryOptimization() {
  if (window.AndroidBridge && typeof window.AndroidBridge.requestBatteryOptimizationExemption === "function") {
    window.AndroidBridge.requestBatteryOptimizationExemption();
  } else {
    showToast("Ushbu funksiya faqat Android ilovasida ishlaydi");
  }
}

function minimizeNativeApp() {
  if (window.AndroidBridge && typeof window.AndroidBridge.minimizeApp === "function") {
    window.AndroidBridge.minimizeApp();
  } else {
    showToast("Ilova orqa fonda ishlamoqda");
  }
}

function dismissAppBanner() {
  const b = document.getElementById("parentAppDownloadBanner");
  if (b) b.classList.add("hidden");
  try { localStorage.setItem("hide_app_banner", "1"); } catch(e) {}
}

// =========================================================
// HELP & FAQ MODAL CONTROLLER
// =========================================================
function openHelpModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("medium");
  const modal = document.getElementById("modalHelp");
  if (modal) {
    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
  }
}

function closeHelpModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  const modal = document.getElementById("modalHelp");
  if (modal) modal.classList.add("hidden");
}

function toggleHelpFaq(id) {
  haptic("light");
  const el = document.getElementById(`faq-content-${id}`);
  const arrow = document.getElementById(`faq-arrow-${id}`);
  if (el) {
    el.classList.toggle("hidden");
    if (arrow) arrow.classList.toggle("rotate-180");
  }
}

// ==============================================================================
// SCREEN TIME & DEVICE POLICY / APP BLOCKING CONTROLLER
// ==============================================================================

let currentDevicePolicy = {
  is_all_apps_blocked: false,
  lockdown_reason: "Dars vaqti",
  blocked_packages: [],
  anti_uninstall_enabled: true,
  has_parent_pin: true
};

let currentScreenTimeData = null;

function updateDevicePolicyUI(policy) {
  if (!policy) return;
  currentDevicePolicy = { ...currentDevicePolicy, ...policy };

  // 1. Master lockdown pill badge in parent utility bar
  const pillBadge = document.getElementById("pillLockdownBadge");
  if (pillBadge) {
    if (currentDevicePolicy.is_all_apps_blocked) {
      pillBadge.classList.remove("hidden");
    } else {
      pillBadge.classList.add("hidden");
    }
  }

  // 2. Map banner alert
  const lockdownBanner = document.getElementById("parentLockdownBanner");
  const lockdownText = document.getElementById("parentLockdownBannerText");
  if (lockdownBanner) {
    if (currentDevicePolicy.is_all_apps_blocked) {
      lockdownBanner.classList.remove("hidden");
      if (lockdownText) {
        lockdownText.textContent = `${currentDevicePolicy.lockdown_reason || "Dars / Uyqu rejimi"} faol`;
      }
    } else {
      lockdownBanner.classList.add("hidden");
    }
  }

  // 3. Modal switches if open
  const toggleLockAll = document.getElementById("toggleLockAllApps");
  if (toggleLockAll) {
    toggleLockAll.checked = Boolean(currentDevicePolicy.is_all_apps_blocked);
  }

  const toggleAntiUn = document.getElementById("toggleAntiUninstall");
  if (toggleAntiUn) {
    toggleAntiUn.checked = Boolean(currentDevicePolicy.anti_uninstall_enabled);
  }

  const blockedCountEl = document.getElementById("stBlockedCountText");
  if (blockedCountEl) {
    const count = (currentDevicePolicy.blocked_packages || []).length;
    blockedCountEl.textContent = `${count} ta bloklangan`;
  }

  // 4. Android native bridge sync
  if (window.AndroidBridge && typeof window.AndroidBridge.setLocalDevicePolicy === "function") {
    try {
      window.AndroidBridge.setLocalDevicePolicy(JSON.stringify(currentDevicePolicy));
    } catch (e) {}
  }
}

function setAppNativeRole(role) {
  if (role === "child") {
    localStorage.setItem("smart_tracker_role", "child");
    currentRole = "child";
    if (!authenticatedSession) {
      enableChildDemoMode();
    }
  } else if (role === "parent") {
    localStorage.setItem("smart_tracker_role", "parent");
    currentRole = "parent";
    if (!authenticatedSession) {
      enableDemoMode();
    }
  }
}

function selectChildForScreenTime(childId) {
  haptic("light");
  currentChildId = childId;
  renderScreenTimeChildSelector();
  loadScreenTimeAndPolicy();
}

function renderScreenTimeChildSelector() {
  const bar = document.getElementById("stChildSelectorBar");
  if (!bar) return;

  const list = (childrenList && childrenList.length > 0) ? childrenList : (
    authenticatedSession?.children || [
      { id: "child_demo_1", name: "Ali", full_name: "Ali (Farzand)" }
    ]
  );

  if (list.length <= 1) {
    bar.innerHTML = "";
    bar.classList.add("hidden");
    return;
  }

  bar.classList.remove("hidden");
  bar.innerHTML = list.map(c => {
    const isSel = (String(c.id) === String(currentChildId)) || (!currentChildId && c === list[0]);
    if (isSel && !currentChildId) currentChildId = c.id;
    const safeName = escapeHtml(c.name || c.full_name || "Farzand");
    const safeId = encodeURIComponent(String(c.id));
    const activeClass = isSel
      ? "bg-rose-600 text-white shadow-md border-rose-400 font-black"
      : "bg-slate-800 text-slate-300 hover:text-white border-white/10 font-bold";
    return `<button onclick="selectChildForScreenTime('${safeId}')" class="px-3 py-1.5 rounded-xl text-xs border transition active:scale-95 flex items-center gap-1.5 shrink-0 ${activeClass}">
      <span>👦</span>
      <span>${safeName}</span>
    </button>`;
  }).join("");
}

function openScreenTimeControlModal() {
  haptic("medium");
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("modalScreenTimeControl");
  if (!modal) return;
  modal.classList.remove("hidden");
  renderScreenTimeChildSelector();
  if (window.lucide) lucide.createIcons();

  loadScreenTimeAndPolicy();
}

function closeScreenTimeControlModal() {
  haptic("light");
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("modalScreenTimeControl");
  if (modal) modal.classList.add("hidden");
}

async function loadScreenTimeAndPolicy() {
  const container = document.getElementById("stAppsListContainer");
  if (container) {
    container.innerHTML = '<p class="text-center text-xs text-slate-400 py-6">Ilovalar va statistika yuklanmoqda...</p>';
  }

  if (isDemoMode || !currentChildId) {
    const emptyData = {
      total_screen_time_seconds: 0,
      total_screen_time_formatted: "0 daqiqa",
      unlock_count: 0,
      is_all_apps_blocked: currentDevicePolicy.is_all_apps_blocked,
      lockdown_reason: currentDevicePolicy.lockdown_reason,
      anti_uninstall_enabled: currentDevicePolicy.anti_uninstall_enabled,
      blocked_packages: currentDevicePolicy.blocked_packages || [],
      apps: []
    };
    renderScreenTimeData(emptyData);
    return;
  }

  try {
    const res = await fetch(`/api/v1/webapp/child/${currentChildId}/screen-time`);
    if (res.ok) {
      const json = await res.json();
      if (json.status === "success" && json.report) {
        renderScreenTimeData(json.report);
        return;
      }
    }
  } catch (e) {
    console.warn("Screen time fetch error:", e);
  }

  // Fallback real empty state if offline or no reports yet
  renderScreenTimeData({
    total_screen_time_seconds: 0,
    total_screen_time_formatted: "0 daqiqa",
    unlock_count: 0,
    is_all_apps_blocked: currentDevicePolicy.is_all_apps_blocked,
    lockdown_reason: currentDevicePolicy.lockdown_reason,
    anti_uninstall_enabled: currentDevicePolicy.anti_uninstall_enabled,
    blocked_packages: currentDevicePolicy.blocked_packages || [],
    apps: []
  });
}

function renderScreenTimeData(data) {
  currentScreenTimeData = data;

  const totalEl = document.getElementById("stTotalTime");
  if (totalEl) totalEl.textContent = data.total_screen_time_formatted || "0 daqiqa";

  const unlockEl = document.getElementById("stUnlockCount");
  if (unlockEl) {
    unlockEl.innerHTML = `<span>🔓 Telefon ochilgan:</span> <b class="text-cyan-400">${data.unlock_count || 0} marta</b>`;
  }

  const statusBadge = document.getElementById("stStatusBadge");
  if (statusBadge) {
    const totalSec = data.total_screen_time_seconds || 0;
    if (totalSec > 14400) {
      statusBadge.className = "inline-block px-2.5 py-1 rounded-xl text-xs font-black bg-rose-500/20 text-rose-300 border border-rose-500/30";
      statusBadge.textContent = "⚠️ Me'yordan ko'p (4s+)";
    } else {
      statusBadge.className = "inline-block px-2.5 py-1 rounded-xl text-xs font-black bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
      statusBadge.textContent = "🟢 Me'yorda";
    }
  }

  // Calculate category totals
  let gamesSec = 0, socialSec = 0, chatSec = 0, otherSec = 0;
  (data.apps || []).forEach(a => {
    const s = a.usage_seconds || 0;
    if (a.category === "game") gamesSec += s;
    else if (a.category === "social" || a.category === "video") socialSec += s;
    else if (a.category === "chat") chatSec += s;
    else otherSec += s;
  });

  const grandSec = Math.max(1, gamesSec + socialSec + chatSec + otherSec);
  const pGames = Math.round((gamesSec / grandSec) * 100);
  const pSocial = Math.round((socialSec / grandSec) * 100);
  const pChat = Math.round((chatSec / grandSec) * 100);
  const pOther = Math.max(0, 100 - (pGames + pSocial + pChat));

  const barG = document.getElementById("barGames");
  if (barG) { barG.style.width = pGames + "%"; barG.title = `O'yinlar: ${pGames}%`; }
  const barS = document.getElementById("barSocial");
  if (barS) { barS.style.width = pSocial + "%"; barS.title = `Video/Ijtimoiy: ${pSocial}%`; }
  const barC = document.getElementById("barChat");
  if (barC) { barC.style.width = pChat + "%"; barC.title = `Muloqot: ${pChat}%`; }
  const barO = document.getElementById("barOther");
  if (barO) { barO.style.width = pOther + "%"; barO.title = `Boshqa: ${pOther}%`; }

  // Update policy UI
  updateDevicePolicyUI({
    is_all_apps_blocked: data.is_all_apps_blocked,
    lockdown_reason: data.lockdown_reason,
    blocked_packages: data.blocked_packages,
    anti_uninstall_enabled: data.anti_uninstall_enabled
  });

  renderAppsList(data.apps || []);
}

function renderAppsList(apps, filterText = "") {
  const container = document.getElementById("stAppsListContainer");
  if (!container) return;

  const q = (filterText || "").trim().toLowerCase();
  const filtered = apps.filter(a => {
    if (!q) return true;
    return (a.app_name && a.app_name.toLowerCase().includes(q)) ||
           (a.package && a.package.toLowerCase().includes(q));
  });

  if (filtered.length === 0) {
    container.innerHTML = '<p class="text-center text-xs text-slate-400 py-6">Mos keluvchi ilovalar topilmadi</p>';
    return;
  }

  const blockedList = currentDevicePolicy.blocked_packages || [];
  const isMasterLocked = currentDevicePolicy.is_all_apps_blocked;

  let html = "";
  filtered.forEach(app => {
    const pkg = app.package;
    const name = app.app_name || pkg;
    const isIndividuallyBlocked = blockedList.includes(pkg);
    const isEffectivelyBlocked = isMasterLocked || isIndividuallyBlocked;

    const sec = app.usage_seconds || 0;
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const durStr = h > 0 ? `${h}s ${m}d` : `${m} daqiqa`;

    let iconHtml = '<i data-lucide="smartphone" class="w-4 h-4"></i>';
    if (app.category === "game") iconHtml = '<i data-lucide="gamepad-2" class="w-4 h-4 text-amber-400"></i>';
    else if (app.category === "video") iconHtml = '<i data-lucide="youtube" class="w-4 h-4 text-rose-500"></i>';
    else if (app.category === "social") iconHtml = '<i data-lucide="video" class="w-4 h-4 text-purple-400"></i>';
    else if (app.category === "chat") iconHtml = '<i data-lucide="message-circle" class="w-4 h-4 text-blue-400"></i>';

    html += `
      <div class="flex items-center justify-between p-2.5 rounded-xl bg-slate-800/60 border ${isEffectivelyBlocked ? 'border-rose-500/40 bg-rose-950/20' : 'border-white/5'} transition">
        <div class="flex items-center gap-2.5 min-w-0">
          <div class="w-8 h-8 rounded-xl bg-slate-700/60 flex items-center justify-center shrink-0">
            ${iconHtml}
          </div>
          <div class="min-w-0">
            <div class="flex items-center gap-1.5">
              <h5 class="text-xs font-black text-white truncate max-w-[140px] sm:max-w-[200px]">${name}</h5>
              ${isEffectivelyBlocked ? '<span class="text-2xs bg-rose-500/30 text-rose-300 px-1 rounded font-bold">BLOK</span>' : ''}
            </div>
            <p class="text-[10px] text-slate-400 mt-0.5 flex items-center gap-1">
              <span>⏱ Bugun:</span> <b class="text-slate-200 font-bold">${durStr}</b>
            </p>
          </div>
        </div>

        <label class="relative inline-flex items-center cursor-pointer shrink-0 ml-2" title="${isIndividuallyBlocked ? 'Blokdan chiqarish' : 'Bloklash'}">
          <input type="checkbox" ${isIndividuallyBlocked ? 'checked' : ''} onchange="toggleAppBlock('${pkg}', this.checked)" class="sr-only peer">
          <div class="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-rose-500"></div>
        </label>
      </div>
    `;
  });

  container.innerHTML = html;
  if (window.lucide) lucide.createIcons();
}

function filterAppsList(val) {
  if (currentScreenTimeData && currentScreenTimeData.apps) {
    renderAppsList(currentScreenTimeData.apps, val);
  }
}

async function handleMasterLockToggle(isChecked) {
  haptic("heavy");
  currentDevicePolicy.is_all_apps_blocked = isChecked;
  updateDevicePolicyUI(currentDevicePolicy);

  if (isChecked) {
    showToast(`🛑 Barcha ilovalar bloklandi (${currentDevicePolicy.lockdown_reason})`);
  } else {
    showToast("✅ Barcha ilovalar cheklovi ochildi");
  }

  if (currentScreenTimeData && currentScreenTimeData.apps) {
    renderAppsList(currentScreenTimeData.apps);
  }

  if (isDemoMode || !currentChildId) return;

  try {
    await fetch(`/api/v1/webapp/child/${currentChildId}/device-policy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        is_all_apps_blocked: isChecked,
        lockdown_reason: currentDevicePolicy.lockdown_reason,
        parent_id: currentUserId
      })
    });
  } catch (e) {
    console.warn("Master lock save error:", e);
  }
}

async function setLockdownReason(reason) {
  haptic("light");
  currentDevicePolicy.lockdown_reason = reason;
  updateDevicePolicyUI(currentDevicePolicy);
  showToast(`Sabab o'rnatildi: «${reason}»`);

  document.querySelectorAll(".btn-lock-reason").forEach(btn => {
    if (btn.textContent.includes(reason)) {
      btn.classList.add("bg-rose-600", "text-white");
      btn.classList.remove("bg-white/10");
    } else {
      btn.classList.remove("bg-rose-600");
      btn.classList.add("bg-white/10");
    }
  });

  if (isDemoMode || !currentChildId) return;

  try {
    await fetch(`/api/v1/webapp/child/${currentChildId}/device-policy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lockdown_reason: reason,
        parent_id: currentUserId
      })
    });
  } catch (e) {}
}

async function toggleAppBlock(pkg, isBlocked) {
  haptic("medium");
  let blocked = currentDevicePolicy.blocked_packages || [];
  if (isBlocked) {
    if (!blocked.includes(pkg)) blocked.push(pkg);
    showToast("Ilova bloklandi 🛑");
  } else {
    blocked = blocked.filter(p => p !== pkg);
    showToast("Ilovaga ruxsat berildi 🟢");
  }
  currentDevicePolicy.blocked_packages = blocked;
  updateDevicePolicyUI(currentDevicePolicy);

  if (currentScreenTimeData && currentScreenTimeData.apps) {
    renderAppsList(currentScreenTimeData.apps);
  }

  if (isDemoMode || !currentChildId) return;

  try {
    await fetch(`/api/v1/webapp/child/${currentChildId}/device-policy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        blocked_packages: blocked,
        parent_id: currentUserId
      })
    });
  } catch (e) {
    console.warn("App block save error:", e);
  }
}

async function handleAntiUninstallToggle(isChecked) {
  haptic("medium");
  currentDevicePolicy.anti_uninstall_enabled = isChecked;
  updateDevicePolicyUI(currentDevicePolicy);

  if (isChecked) {
    showToast("🛡️ Sozlamalar himoyasi yoqildi (PIN-kod talab qilinadi)");
  } else {
    showToast("⚠️ Sozlamalar himoyasi o'chirildi");
  }

  if (isDemoMode || !currentChildId) return;

  try {
    await fetch(`/api/v1/webapp/child/${currentChildId}/device-policy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        anti_uninstall_enabled: isChecked,
        parent_id: currentUserId
      })
    });
  } catch (e) {}
}

function openParentPinSetupModal() {
  haptic("light");
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("modalParentPinSetup");
  if (modal) {
    modal.classList.remove("hidden");
    const input = document.getElementById("inputNewPin");
    if (input) {
      input.value = "";
      input.focus();
    }
  }
}

function closeParentPinSetupModal() {
  haptic("light");
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("modalParentPinSetup");
  if (modal) modal.classList.add("hidden");
}

async function saveNewParentPin() {
  const input = document.getElementById("inputNewPin");
  if (!input) return;
  const pin = (input.value || "").trim();
  if (pin.length < 4) {
    showToast("❌ PIN-kod kamida 4 xonali bo'lishi kerak!");
    return;
  }

  haptic("heavy");
  showToast("✅ Ota-ona PIN-kodi muvaffaqiyatli saqlandi!");
  closeParentPinSetupModal();

  if (isDemoMode || !currentChildId) return;

  try {
    await fetch(`/api/v1/webapp/child/${currentChildId}/device-policy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        new_pin: pin,
        parent_id: currentUserId
      })
    });
  } catch (e) {}
}

