// Smart Parental Control - Mini App Client Logic (Enhanced Ideal Edition)

let tg = window.Telegram?.WebApp;
if (tg) {
  try {
    tg.ready();
    tg.expand();
    if (typeof tg.disableVerticalSwipes === "function") {
      tg.disableVerticalSwipes();
    }
    // Closing confirmation removed to allow smooth, instant exit back to Telegram
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
let lastSosAlertState = false;

// High-accuracy device GPS acquisition with cache suppression to avoid repeated prompts
let cachedDeviceLocation = null;
let lastLocationFetchTime = 0;

async function getDeviceLocation() {
  // If in demo mode, do not trigger native GPS permission modal
  if (isDemoMode) {
    return { latitude: 41.2916, longitude: 69.2121, accuracy: 10 };
  }

  // Use cached location if fresh (< 60 seconds) to avoid repeated browser prompts
  if (cachedDeviceLocation && (Date.now() - lastLocationFetchTime < 60000)) {
    return cachedDeviceLocation;
  }

  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(cachedDeviceLocation || null);
      return;
    }
    const timer = setTimeout(() => {
      resolve(cachedDeviceLocation || null);
    }, 4000);

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
        console.warn("Geolocation query error:", err);
        resolve(cachedDeviceLocation || null);
      },
      {
        enableHighAccuracy: true,
        timeout: 4000,
        maximumAge: 60000,
      }
    );
  });
}

// Device battery status acquisition
async function getDeviceBattery() {
  try {
    if (navigator.getBattery) {
      const b = await navigator.getBattery();
      return Math.round(b.level * 100);
    }
  } catch (e) {
    // Battery API not supported or restricted
  }
  return null;
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
let previewCircle = null;
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

  // Polling every 8 seconds for live telemetry
  pollInterval = setInterval(() => {
    if (currentChildId && !isDemoMode) {
      updateLiveTelemetry(currentChildId);
    }
  }, 8000);

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
  const qCode = urlParams.get("code");

  // 1. Direct code in query param: ?code=123456
  if (qCode) {
    const input = document.getElementById("inputAuthCode");
    if (input) input.value = qCode;
    await submitAuthCode();
    return;
  }

  // 2. Check localStorage session (User previously authenticated with code)
  const stored = localStorage.getItem("smart_tracker_session");
  const storedCode = localStorage.getItem("smart_tracker_auth_code");
  if (stored) {
    try {
      const data = JSON.parse(stored);
      if (data && data.user && data.role) {
        hideAuthGateModal();
        applyAuthenticatedUser(data);

        // Silent background refresh to keep children and balance up to date
        if (storedCode) {
          fetch("/api/v1/webapp/auth/code", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: storedCode })
          })
            .then(async (r) => {
              if (r.status === 404 || r.status === 401) {
                // User role changed or access code was revoked on server
                localStorage.removeItem("smart_tracker_session");
                localStorage.removeItem("smart_tracker_auth_code");
                localStorage.removeItem("smart_tracker_role");
                showAuthGateModal();
                return null;
              }
              return r.json();
            })
            .then((fresh) => {
              if (fresh && fresh.status === "success") {
                localStorage.setItem("smart_tracker_session", JSON.stringify(fresh));
                applyAuthenticatedUser(fresh);
              }
            })
            .catch(() => {});
        }
        return;
      }
    } catch (e) {
      localStorage.removeItem("smart_tracker_session");
    }
  }

  // 3. Telegram WebApp auto-authentication by user ID if available
  const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user;
  if (tgUser && tgUser.id) {
    try {
      const tgRes = await fetch("/api/v1/webapp/auth/code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: String(tgUser.id) })
      });
      const tgData = await tgRes.json();
      if (tgData && tgData.status === "success") {
        localStorage.setItem("smart_tracker_session", JSON.stringify(tgData));
        localStorage.setItem("smart_tracker_auth_code", String(tgUser.id));
        localStorage.setItem("smart_tracker_role", tgData.role);
        localStorage.setItem(AUTH_PROMPT_KEY, "2");
        hideAuthGateModal();
        applyAuthenticatedUser(tgData);
        return;
      }
    } catch (e) {}
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

function applyChildUI(user) {
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

  // Child Hero Bar Updates
  const heroName = document.getElementById("childHeroName");
  if (heroName) heroName.textContent = user.full_name || "Azizbek Qahramon";

  const heroMoneyText = document.getElementById("childHeroMoneyText");
  if (heroMoneyText) heroMoneyText.textContent = `${moneyStr} so'm`;

  const bannerName = document.getElementById("childBannerName");
  if (bannerName) bannerName.textContent = dict.hero_adventure || "Bugungi sarguzasht";

  const bannerMoney = document.getElementById("childBannerMoney");
  if (bannerMoney) bannerMoney.textContent = `💵 Sizda hozir ${moneyStr} so'm bor! (${coinsStr} tanga)`;

  // Child Adventure Dashboard Card updates
  const dashHeroName = document.getElementById("childDashboardHeroName");
  if (dashHeroName) dashHeroName.textContent = user.full_name || "Azizbek Qahramon";

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
    sel.innerHTML = `<option value="${user.id}">${user.full_name || 'Mening hisobim'}</option>`;
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

  // Map click listener to set position for new safe zone
  map.on("click", (e) => {
    const modal = document.getElementById("modal-add-zone");
    if (!modal.classList.contains("hidden")) {
      updatePreviewCircle(e.latlng.lat, e.latlng.lng);
    }
  });
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

// Load real data or show interactive demo
async function loadData() {
  try {
    const res = await fetch(`/api/v1/webapp/parent/${parentId}/children`);
    if (!res.ok) throw new Error("API status " + res.status);
    const data = await res.json();

    if (data.status === "success" && data.children && data.children.length > 0) {
      childrenList = data.children;
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
    } else {
      if (authenticatedSession) {
        childrenList = [];
        renderChildSelector();
        showEmptyState();
      } else {
        enableDemoMode();
      }
    }
  } catch (err) {
    console.warn("Backend API not reachable or static Netlify host, loading interactive demo mode:", err);
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
      .map((c) => `<option value="${c.id}">${c.full_name || "Farzand #" + c.id}</option>`)
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
              <span class="truncate max-w-[110px]">${c.full_name || 'Farzand'}</span>
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
  if (loc && loc.latitude && loc.longitude) {
    renderChildMarker(loc.latitude, loc.longitude, child.full_name, child.is_sos_active, loc.battery_level);
    if (map) map.flyTo([loc.latitude, loc.longitude], 16, { animate: true, duration: 0.8 });
  } else {
    if (childMarker && map) {
      try { map.removeLayer(childMarker); } catch (e) {}
      childMarker = null;
    }
    const addrEl = document.getElementById("addressText");
    if (addrEl) addrEl.textContent = "Joylashuv kutilmoqda (Bot orqali yuboring)";
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

    const loc = child.location;
    if (loc && loc.latitude && loc.longitude) {
      const lat = loc.latitude;
      const lon = loc.longitude;

      updateBatteryUI(loc.battery_level, loc.is_charging);
      updateSoundUI(loc.sound_mode);
      updateSpeedUI(loc.speed);

      if (loc.recorded_at) {
        document.getElementById("lastSeenText").innerHTML = `
          <span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
          <span>${formatTimeAgo(loc.recorded_at)}</span>
        `;
      }

      renderChildMarker(lat, lon, child.full_name, child.is_sos_active, loc.battery_level);
      resolveAddress(lat, lon);
    } else {
      document.getElementById("addressText").textContent = "Joylashuv kutilmoqda (Bot orqali yuboring)";
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
      // Extract street and district
      const addr = data.address || {};
      const shortAddr = addr.road || addr.suburb || addr.neighbourhood || addr.city_district || data.display_name.split(",").slice(0, 2).join(",");
      const fullText = shortAddr ? `${shortAddr}, Tashkent` : data.display_name.split(",").slice(0, 2).join(",");
      addressCache.set(key, fullText);
      document.getElementById("addressText").textContent = fullText;
      return;
    }
  } catch (e) {
    // Fallback to coordinates
  }

  document.getElementById("addressText").textContent = `Koordinata: ${numLat.toFixed(5)}, ${numLon.toFixed(5)}`;
}

// Render or move pulsing radar child marker
function renderChildMarker(lat, lon, name, isSos, battery) {
  if (lat === null || lat === undefined || lon === null || lon === undefined || isNaN(lat) || isNaN(lon)) {
    return;
  }
  const initial = (name || "B").charAt(0).toUpperCase();
  const pulseClass = isSos ? "sos-pulse-ring" : "child-pulse-ring";
  const pinBg = isSos ? "bg-red-600" : "bg-gradient-to-tr from-blue-700 to-indigo-500";

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
    childMarker.setLatLng([lat, lon]);
    childMarker.setIcon(icon);
  } else {
    childMarker = L.marker([lat, lon], { icon: icon }).addTo(map);
    map.setView([lat, lon], 15);
  }

  childMarker.bindPopup(`
    <div class="p-1 text-slate-900 text-xs font-semibold">
      <div class="font-bold text-sm mb-1 text-blue-700">${name || "Farzand"}</div>
      <div>🔋 Batareya: <b>${battery !== null ? battery + "%" : "Noma'lum"}</b></div>
      <div class="text-[10px] text-slate-500 mt-1">Jonli xavfsizlik nazorati faol</div>
    </div>
  `);
}

// Center view on child
function centerOnChild() {
  haptic("light");
  if (childMarker) {
    map.flyTo(childMarker.getLatLng(), 16, { animate: true, duration: 1 });
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

  // Clear all previous geofence layers and tooltips from map
  if (geofenceLayers && map) {
    geofenceLayers.forEach((layer) => {
      try { map.removeLayer(layer); } catch (e) {}
    });
  }
  geofenceLayers = [];
  geofencesList = [];

  try {
    const res = await fetch(`/api/v1/webapp/child/${childId}/geofences`);
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
        container.innerHTML = `
          <div class="text-center py-6 px-4">
            <p class="text-xs text-slate-400 mb-3">Hozircha kerakli joylar belgilanmagan.</p>
            <button onclick="openAddZoneModal()" class="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white text-xs font-bold rounded-xl shadow-md transition active:scale-95">
              ➕ Kerakli joy qo'shish
            </button>
          </div>
        `;
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
      <button onclick="deleteGeofence(${f.id})" class="p-2 text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-xl transition" title="O'chirish">
        <i data-lucide="trash-2" class="w-4 h-4"></i>
      </button>
    </div>
  `
    )
    .join("");

  if (window.lucide) lucide.createIcons();
  document.getElementById("zoneStatusText").textContent = `✅ ${fences.length} ta kerakli joy nazoratda`;

  // Auto-fit view to show all geofences
  if (geofenceLayers.length > 0) {
    const group = new L.featureGroup(geofenceLayers.concat(childMarker ? [childMarker] : []));
    map.fitBounds(group.getBounds().pad(0.2));
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

// Load route history
async function loadRouteHistory(childId) {
  if (isDemoMode) {
    renderDemoHistory();
    return;
  }

  try {
    const res = await fetch(`/api/v1/webapp/child/${childId}/history`);
    const data = await res.json();
    if (data.status !== "success") return;

    if (routePolyline) {
      map.removeLayer(routePolyline);
      routePolyline = null;
    }

    const points = data.points;
    const container = document.getElementById("historyListContainer");

    if (points.length === 0) {
      container.innerHTML = `<p class="text-center text-xs text-slate-400 py-6">Bugun uchun harakat yozuvlari yo'q.</p>`;
      return;
    }

    renderHistoryToMapAndList(points);
  } catch (err) {
    console.error("Failed to load route history:", err);
  }
}

function renderHistoryToMapAndList(points) {
  if (routePolyline && map) {
    try { map.removeLayer(routePolyline); } catch (e) {}
    routePolyline = null;
  }
  const container = document.getElementById("historyListContainer");
  const latlngs = points.map((p) => [p.latitude, p.longitude]);

  routePolyline = L.polyline(latlngs, {
    color: "#f59e0b",
    weight: 4,
    opacity: 0.85,
    dashArray: "6, 8",
  });

  if (isRouteVisible) {
    routePolyline.addTo(map);
  }

  container.innerHTML = points
    .slice()
    .reverse()
    .map((p, idx) => {
      const time = p.recorded_at ? new Date(p.recorded_at).toLocaleTimeString("uz-UZ", { hour: "2-digit", minute: "2-digit" }) : "--:--";
      const batteryStr = p.battery_level ? `${p.battery_level}%` : "--";
      const chargingIcon = p.is_charging ? "⚡" : "";

      return `
      <div class="bg-slate-800/80 border border-slate-700/60 p-2.5 rounded-xl flex items-center justify-between text-xs">
        <div class="flex items-center gap-2">
          <div class="w-2.5 h-2.5 rounded-full bg-amber-400"></div>
          <span class="font-mono text-slate-200">${p.latitude.toFixed(4)}, ${p.longitude.toFixed(4)}</span>
        </div>
        <div class="flex items-center gap-2.5 text-slate-400 text-[11px]">
          <span>🔋 ${batteryStr} ${chargingIcon}</span>
          <span class="font-bold text-slate-200">🕒 ${time}</span>
        </div>
      </div>
    `;
    })
    .join("");
}

// Render Demo History
function renderDemoHistory() {
  const demoPoints = [
    { latitude: 41.2858, longitude: 69.2035, battery_level: 85, is_charging: false, recorded_at: "2026-10-16T08:00:00" },
    { latitude: 41.2870, longitude: 69.2060, battery_level: 84, is_charging: false, recorded_at: "2026-10-16T08:10:00" },
    { latitude: 41.2890, longitude: 69.2090, battery_level: 83, is_charging: false, recorded_at: "2026-10-16T08:20:00" },
    { latitude: 41.2915, longitude: 69.2120, battery_level: 82, is_charging: false, recorded_at: "2026-10-16T08:30:00" },
    { latitude: 41.2916, longitude: 69.2121, battery_level: 80, is_charging: false, recorded_at: "2026-10-16T08:45:00" },
  ];
  renderHistoryToMapAndList(demoPoints);
}

// Smooth Route Playback Animation
function startRoutePlayback() {
  haptic("medium");
  if (!routePolyline) {
    showToast("Harakat marshruti mavjud emas");
    return;
  }

  const latlngs = routePolyline.getLatLngs();
  if (latlngs.length < 2) {
    showToast("Harakat nuqtalari yetarli emas");
    return;
  }

  closeModal("history");
  showToast("▶ Marshrut harakati ko'rsatilmoqda...");

  if (replayMarker) {
    map.removeLayer(replayMarker);
  }

  const replayIcon = L.divIcon({
    html: `<div class="replay-marker-dot"></div>`,
    className: "custom-replay-icon",
    iconSize: [20, 20],
    iconAnchor: [10, 10],
  });

  replayMarker = L.marker(latlngs[0], { icon: replayIcon }).addTo(map);
  map.panTo(latlngs[0]);

  let step = 0;
  clearInterval(replayTimer);

  replayTimer = setInterval(() => {
    step++;
    if (step >= latlngs.length) {
      clearInterval(replayTimer);
      showToast("🏁 Harakat yakunlandi");
      setTimeout(() => {
        if (replayMarker) map.removeLayer(replayMarker);
      }, 2000);
      return;
    }

    replayMarker.setLatLng(latlngs[step]);
    map.panTo(latlngs[step], { animate: true, duration: 0.5 });
  }, 900);
}

// Toggle route history polyline on map
function toggleRouteHistory() {
  haptic("light");
  if (!routePolyline) {
    showToast("Harakat tarixi mavjud emas");
    return;
  }

  isRouteVisible = !isRouteVisible;
  const btn = document.getElementById("btnToggleRoute");

  if (isRouteVisible) {
    routePolyline.addTo(map);
    btn.classList.add("text-amber-400");
    btn.classList.remove("text-slate-500");
    showToast("Marshrut ko'rsatildi");
  } else {
    map.removeLayer(routePolyline);
    btn.classList.remove("text-amber-400");
    btn.classList.add("text-slate-500");
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
      showToast("🚨 DIQQAT: Farzandingiz SOS xavf signalini yoqdi!");
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
function switchTab(tabName) {
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

  if (tabName !== "map") {
    const targetModal = document.getElementById(`modal-${tabName}`);
    if (targetModal) targetModal.classList.remove("hidden");
    if (floatControls) floatControls.classList.add("hidden");
    if (childActions) childActions.classList.add("hidden");
    if (locCard) locCard.classList.add("hidden");
    if (tabName === "tasks") {
      loadTasks();
    }
    if (tabName === "wallet") {
      loadWishlist(currentChildId);
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
    ? geofencesList.slice(0, 4)
    : (isDemoMode ? [
        { name: "Uyimiz (Asosiy Baza 🏡)", radius_meters: 200 },
        { name: "Maktab №45 (Bilim Maskani 🏫)", radius_meters: 250 },
        { name: "O'quv Markazi (Zukko Hub 📚)", radius_meters: 200 }
      ] : []);

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

  container.innerHTML = places.map((c, i) => `
    <div class="bg-slate-950/60 p-2.5 rounded-2xl border border-slate-800 flex items-center gap-2">
      <span class="text-xl shrink-0">${placeEmojis[i % placeEmojis.length]}</span>
      <div class="min-w-0">
        <p class="font-bold text-white text-xs truncate">${c.name}</p>
        <p class="text-[10px] text-emerald-400 font-medium">Xavfsiz (${Math.round(c.radius_meters || 100)}m)</p>
      </div>
    </div>
  `).join("");
}

function closeModal(tabName) {
  haptic("light");
  const modal = document.getElementById(`modal-${tabName}`);
  if (modal) modal.classList.add("hidden");
  switchTab("map");
  updateTelegramBackButton();
}

// Add Kerakli Joy Modal & Presets
function openAddZoneModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("medium");
  const modal = document.getElementById("modal-add-zone");
  modal.classList.remove("hidden");

  const floatControls = document.getElementById("floatingMapControls");
  if (floatControls) floatControls.classList.add("hidden");
  const locCard = document.getElementById("locationCard");
  if (locCard) locCard.classList.add("hidden");

  const center = map.getCenter();
  const radius = parseFloat(document.getElementById("inputZoneRadius").value);
  updatePreviewCircle(center.lat, center.lng, radius);
}

function closeAddZoneModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  document.getElementById("modal-add-zone").classList.add("hidden");
  if (previewCircle) {
    map.removeLayer(previewCircle);
    previewCircle = null;
  }
  const floatControls = document.getElementById("floatingMapControls");
  if (floatControls) floatControls.classList.remove("hidden");
  const locCard = document.getElementById("locationCard");
  if (locCard) locCard.classList.remove("hidden");
}

function applyZonePreset(namePrefix, radius) {
  haptic("light");
  document.getElementById("inputZoneName").value = namePrefix;
  document.getElementById("inputZoneRadius").value = radius;
  updateRadiusPreview(radius);
}

function updateRadiusPreview(val) {
  document.getElementById("zoneRadiusDisplay").textContent = `${val} m`;
  if (previewCircle) {
    previewCircle.setRadius(parseFloat(val));
  }
}

function updatePreviewCircle(lat, lon, radius = null) {
  if (!radius) {
    radius = parseFloat(document.getElementById("inputZoneRadius").value);
  }
  if (previewCircle) {
    previewCircle.setLatLng([lat, lon]);
    previewCircle.setRadius(radius);
  } else {
    previewCircle = L.circle([lat, lon], {
      color: "#10b981",
      fillColor: "#10b981",
      fillOpacity: 0.25,
      weight: 2,
      dashArray: "5, 5",
      radius: radius,
    }).addTo(map);
  }
}

// Submit new Kerakli joy
async function submitNewZone() {
  const nameInput = document.getElementById("inputZoneName");
  const name = nameInput.value.trim();
  if (!name) {
    showToast("Iltimos, joy nomini kiriting!");
    return;
  }

  const radius = parseFloat(document.getElementById("inputZoneRadius").value);
  const center = previewCircle ? previewCircle.getLatLng() : map.getCenter();

  if (isDemoMode) {
    haptic("success");
    showToast("✅ Kerakli joy saqlandi!");
    nameInput.value = "";
    closeAddZoneModal();
    return;
  }

  try {
    const res = await fetch("/api/v1/webapp/geofence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parent_id: parseInt(parentId),
        child_id: currentChildId ? parseInt(currentChildId) : null,
        name: name,
        latitude: center.lat,
        longitude: center.lng,
        radius_meters: radius,
      }),
    });

    const data = await res.json();
    if (data.status === "success") {
      haptic("success");
      showToast("✅ Kerakli joy saqlandi!");
      nameInput.value = "";
      closeAddZoneModal();
      if (currentChildId) {
        await loadGeofences(currentChildId);
      }
    }
  } catch (err) {
    showToast("Server bilan aloqa uzildi");
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
    const res = await fetch(`/api/v1/webapp/geofence/${fenceId}?parent_id=${parentId}`, {
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
  }, 2300);
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
            <span class="text-[10px] text-slate-400 font-bold uppercase tracking-wider">${activeChild.full_name || 'Farzand'} balansi:</span>
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

  label.textContent = selectedTaskMediaFile.name;
  previewBox.classList.remove("hidden");

  const url = URL.createObjectURL(selectedTaskMediaFile);
  if (selectedTaskMediaFile.type.startsWith("video/")) {
    imgEl.classList.add("hidden");
    videoEl.classList.remove("hidden");
    videoEl.src = url;
  } else {
    videoEl.classList.add("hidden");
    imgEl.classList.remove("hidden");
    imgEl.src = url;
  }
  if (window.lucide) lucide.createIcons();
}

function clearTaskMedia() {
  selectedTaskMediaFile = null;
  const input = document.getElementById("taskMediaFileInput");
  if (input) input.value = "";
  const previewBox = document.getElementById("taskMediaPreview");
  if (previewBox) previewBox.classList.add("hidden");
  const imgEl = document.getElementById("taskPreviewImg");
  if (imgEl) { imgEl.src = ""; imgEl.classList.add("hidden"); }
  const videoEl = document.getElementById("taskPreviewVideo");
  if (videoEl) { videoEl.src = ""; videoEl.classList.add("hidden"); }
  const label = document.getElementById("taskMediaLabel");
  if (label) label.textContent = "Rasm / Video olish yoki tanlash";
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
  selectedProofPhotoFile = null;
  document.getElementById("modal-task-proof").classList.add("hidden");
}

function handleProofPhotoSelected(input) {
  if (!input.files || input.files.length === 0) return;
  selectedProofPhotoFile = input.files[0];

  const prev = document.getElementById("proofPhotoPreviewContainer");
  const img = document.getElementById("proofPreviewImg");
  const btn = document.getElementById("btnSubmitProof");

  img.src = URL.createObjectURL(selectedProofPhotoFile);
  prev.classList.remove("hidden");
  btn.disabled = false;
  document.getElementById("proofFileLabel").textContent = "📸 Rasm tanlandi";
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

async function triggerChildSOS() {
  haptic("heavy");
  playSirenSound();

  // Show emergency active screen on child device
  const modal = document.getElementById("modal-sos-active");
  if (modal) {
    modal.classList.remove("hidden");
    if (window.lucide) window.lucide.createIcons();
  }

  showToast("🚨 SOS XAVF SIGNALI FAOLLASHTIRILDI! Ota-onangizga xabar ketmoqda...");

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
        showToast("🚨 Ota-onangizga SOS va joriy lokatsiyangiz yetkazildi!");
      }
    } catch (err) {
      console.error("SOS dispatch error:", err);
      showToast("⚠️ SOS signali yuborildi (tarmoq tekshirilmoqda)");
    }
  } else {
    showToast("🚨 (Demo) SOS signali faollashtirildi!");
  }
}

async function cancelChildSOS() {
  haptic("medium");
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
  if (parentPhone) {
    const clean = parentPhone.replace(/[\s\-]/g, "");
    showToast(`📞 Qo'ng'iroq qilinmoqda: ${parentPhone}`);
    window.location.href = `tel:${clean}`;
    return;
  }
  if (window.Telegram?.WebApp?.close) {
    showToast("⚠️ Ota-ona telefon raqami topilmadi. Telegram chatga o'tilmoqda...");
    window.Telegram.WebApp.close();
  } else {
    window.location.href = "tel:+998901234567";
  }
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

function initChildPermissionsAndTracking(childId) {
  if (!childId) return;

  // Connect real-time WebSocket for instant alerts
  connectWebSocket(childId);

  // Start HTTP fallback polling for active check alerts
  startChildNotificationPolling(childId);

  // Check if permissions were previously granted
  const granted = localStorage.getItem("smart_tracker_child_permissions_granted");
  if (!granted) {
    const modal = document.getElementById("childOnboardingModal");
    if (modal) {
      modal.classList.remove("hidden");
      if (window.lucide) lucide.createIcons();
    }
  } else {
    // Automatically start live GPS telemetry tracking
    startChildLiveTracking(childId);
  }
}

async function requestChildAllPermissions() {
  haptic("medium");
  const modal = document.getElementById("childOnboardingModal");
  const btn = document.getElementById("btnGrantChildPermissions");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-pulse">Ruxsatlar faollashtirilmoqda...</span>`;
  }

  // 1. Request Camera and Video via navigator.mediaDevices.getUserMedia
  try {
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
        .catch(() => navigator.mediaDevices.getUserMedia({ video: true }))
        .catch(() => null);
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
    }
  } catch (e) {
    console.warn("Media permissions prompt note:", e);
  }

  // 2. Start continuous watchPosition GPS telemetry
  const targetId = currentChildId || authenticatedSession?.user?.id;
  if (targetId) {
    startChildLiveTracking(targetId);
  }

  localStorage.setItem("smart_tracker_child_permissions_granted", "true");

  if (modal) modal.classList.add("hidden");
  showToast("✅ Ruxsatlar berildi! Jonli GPS xavfsizlik monitoringi ishga tushdi 🛡️");
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
    timeout: 15000,
    maximumAge: 5000
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

      // Throttle: send if moved > 5 meters or at least once every 10 seconds
      if (dist >= 5 || (now - lastSentTime) > 10000) {
        lastSentTime = now;
        lastSentLat = latitude;
        lastSentLng = longitude;

        const battery = await getDeviceBattery();
        sendChildLiveTelemetry(childId, {
          latitude,
          longitude,
          accuracy: accuracy || null,
          speed: speedKmh,
          battery_level: battery,
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

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/api/v1/webapp/ws/${userId}`;

  try {
    appWebSocket = new WebSocket(wsUrl);

    appWebSocket.onopen = () => {
      console.log("WebSocket connected for user:", userId);
    };

    appWebSocket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleIncomingWebSocketMessage(msg);
      } catch (e) {
        console.error("WS message parse error:", e);
      }
    };

    appWebSocket.onclose = () => {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = setTimeout(() => {
        if (currentRole === "child" || currentRole === "parent") {
          connectWebSocket(userId);
        }
      }, 5000);
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

  // 1. Active check alert for Child
  if (msg.type === "active_check") {
    showChildActiveCheckBanner(msg.time || new Date().toLocaleTimeString().slice(0, 5));
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
      showToast(`🚨 DIQQAT: ${msg.child_name || 'Farzandingiz'} SOS xavf signalini yoqdi!`);
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
      const child = childrenList.find((c) => c.id == currentChildId);
      const name = child ? child.full_name : "Farzand";
      renderChildMarker(msg.latitude, msg.longitude, name, child?.is_sos_active, msg.battery_level);
      updateBatteryUI(msg.battery_level, msg.is_charging);
      updateSpeedUI(msg.speed);
      resolveAddress(msg.latitude, msg.longitude);
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

async function openChildInviteParentModal() {
  setTimeout(updateTelegramBackButton, 50);
  haptic("light");
  const modal = document.getElementById("childInviteParentModal");
  const linkInput = document.getElementById("childInviteLinkInput");
  const codeText = document.getElementById("childOwnCodeText");
  const targetId = currentChildId || authenticatedSession?.user?.id;

  if (modal) {
    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
  }

  // Pre-fill or fetch from backend
  const fallbackCode = authenticatedSession?.user?.web_access_code || "------";
  if (codeText) codeText.textContent = fallbackCode;

  try {
    if (isDemoMode) {
      childCurrentInviteLink = `https://t.me/ChildSecurityBot?start=link_child_2002`;
      if (linkInput) linkInput.value = childCurrentInviteLink;
      if (codeText) codeText.textContent = "202401";
      return;
    }

    const res = await fetch(`/api/v1/webapp/child/${targetId}/invite-info`);
    if (res.ok) {
      const data = await res.json();
      if (data.status === "success") {
        childCurrentInviteLink = data.invite_link;
        if (linkInput) linkInput.value = data.invite_link;
        if (codeText) codeText.textContent = data.web_access_code || fallbackCode;
      }
    }
  } catch (e) {
    console.warn("Child invite info load error:", e);
  }
}

function closeChildInviteParentModal() {
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("childInviteParentModal");
  if (modal) modal.classList.add("hidden");
}

function copyChildInviteLink() {
  if (!childCurrentInviteLink) return;
  navigator.clipboard.writeText(childCurrentInviteLink).then(() => {
    haptic("light");
    showToast("📋 Ulanish havolasi nusxalandi!");
  }).catch(() => {
    showToast("Nusxa olindi");
  });
}

function shareChildInviteToTelegram() {
  if (!childCurrentInviteLink) return;
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
  const targetId = parentId || authenticatedSession?.user?.id;

  if (modal) {
    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
  }

  try {
    if (isDemoMode) {
      parentCurrentInviteLink = `https://t.me/ChildSecurityBot?start=coparent_1001`;
      if (linkInput) linkInput.value = parentCurrentInviteLink;
      return;
    }

    const res = await fetch(`/api/v1/webapp/parent/${targetId}/coparent-invite-info`);
    if (res.ok) {
      const data = await res.json();
      if (data.status === "success") {
        parentCurrentInviteLink = data.invite_link;
        if (linkInput) linkInput.value = data.invite_link;
      }
    }
  } catch (e) {
    console.warn("Parent coparent invite info load error:", e);
  }
}

function closeParentInviteSpouseModal() {
  setTimeout(updateTelegramBackButton, 50);
  const modal = document.getElementById("parentInviteSpouseModal");
  if (modal) modal.classList.add("hidden");
}

function copyParentInviteLink() {
  if (!parentCurrentInviteLink) return;
  navigator.clipboard.writeText(parentCurrentInviteLink).then(() => {
    haptic("light");
    showToast("📋 Hamkor havolasi nusxalandi!");
  }).catch(() => {
    showToast("Nusxa olindi");
  });
}

function shareParentInviteToTelegram() {
  if (!parentCurrentInviteLink) return;
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
  if (!tg || !tg.BackButton) return;
  try {
    tg.BackButton.onClick(() => {
      handleTelegramBackPress();
    });
  } catch (e) {}
}

function handleTelegramBackPress() {
  haptic("light");
  const modalPriority = [
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
    { id: "modal-add-zone", close: closeAddZoneModal },
    { id: "modal-coin-rate", close: closeCoinRateModal },
    { id: "modal-settings", close: closeSettingsModal },
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
  haptic("light");
  try {
    if (window.Telegram?.WebApp?.close) {
      window.Telegram.WebApp.close();
      return;
    }
  } catch (e) {}
  try {
    window.close();
  } catch (e) {}
}

function updateTelegramBackButton() {
  if (!tg || !tg.BackButton) return;
  try {
    const modalIds = [
      "modal-walk-safe",
      "modal-walk-safe-checkin",
      "modal-edit-wishlist",
      "modal-daily-report",
      "modal-zones",
      "modal-history",
      "modal-tasks",
      "modal-wallet",
      "modal-settings",
      "modal-add-zone",
      "modal-add-task",
      "modal-task-schedule",
      "modal-task-proof",
      "modal-coin-rate",
      "deleteChildModal",
      "childOnboardingModal",
      "childInviteParentModal",
      "parentInviteSpouseModal",
      "modal-sos-active"
    ];
    const hasOpenModal = modalIds.some(id => {
      const el = document.getElementById(id);
      return el && !el.classList.contains("hidden");
    });

    const activeTab = document.querySelector(".nav-tab.active");
    const isNonMapTab = activeTab && activeTab.id !== "tabBtn-map";
    const childMapOpen = !document.getElementById("btnChildBackFromMap")?.classList.contains("hidden");

    if (hasOpenModal || isNonMapTab || childMapOpen) {
      tg.BackButton.show();
    } else {
      tg.BackButton.hide();
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
    { id: "modal-add-zone", close: closeAddZoneModal },
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
