// Smart Parental Control - Mini App Client Logic (Enhanced Ideal Edition)

let tg = window.Telegram?.WebApp;
if (tg) {
  try {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor("#090d16");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#090d16");
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

// Check session in localStorage or URL
function checkAuthenticationState() {
  const urlParams = new URLSearchParams(window.location.search);
  const qCode = urlParams.get("code");

  // 1. Direct code in query param: ?code=123456
  if (qCode) {
    const input = document.getElementById("inputAuthCode");
    if (input) input.value = qCode;
    submitAuthCode();
    return;
  }

  // 2. Check localStorage session (User previously authenticated with code)
  const stored = localStorage.getItem("smart_tracker_session");
  if (stored) {
    try {
      const data = JSON.parse(stored);
      if (data && data.user && data.role) {
        hideAuthGateModal();
        applyAuthenticatedUser(data);
        return;
      }
    } catch (e) {
      localStorage.removeItem("smart_tracker_session");
    }
  }

  // 3. Check if user previously chosen Demo mode in this session
  const demoRole = sessionStorage.getItem("smart_tracker_demo_role");
  if (demoRole) {
    hideAuthGateModal();
    if (demoRole === "child") {
      enableChildDemoMode();
    } else {
      enableDemoMode();
    }
    return;
  }

  // 4. Default for all new visitors (including Telegram Mini App):
  // ALWAYS show auth gate modal with 6-digit code prompt and Demo options!
  showAuthGateModal();
}

function showAuthGateModal() {
  const modal = document.getElementById("authGateModal");
  if (modal) {
    modal.classList.remove("hidden");
    const closeBtn = document.getElementById("btnCloseAuthGate");
    if (closeBtn) {
      if (isDemoMode || authenticatedSession) closeBtn.classList.remove("hidden");
      else closeBtn.classList.add("hidden");
    }
    const input = document.getElementById("inputAuthCode");
    if (input) setTimeout(() => input.focus(), 200);
    if (window.lucide) lucide.createIcons();
  }
}

function hideAuthGateModal() {
  const modal = document.getElementById("authGateModal");
  if (modal) modal.classList.add("hidden");
}

async function submitAuthCode() {
  const codeInput = document.getElementById("inputAuthCode");
  const code = codeInput ? codeInput.value.trim() : "";
  const errorText = document.getElementById("authErrorText");
  const errorMsg = document.getElementById("authErrorMsg");
  const btn = document.getElementById("btnSubmitAuthCode");

  if (!code || code.length < 4) {
    if (errorText) errorText.classList.remove("hidden");
    if (errorMsg) errorMsg.textContent = "Iltimos, Telegram bot bergan kodni kiriting.";
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
      sessionStorage.setItem("smart_tracker_demo_role", "parent");
      hideAuthGateModal();
      enableDemoMode();
      showToast("🎮 Namuna (Demo) rejimiga kirildi!");
      return;
    }

    const data = await res.json();
    if (!res.ok || data.status !== "success") {
      throw new Error(data.detail || "Kiritilgan kod noto'g'ri yoki mavjud emas.");
    }

    // Save session
    localStorage.setItem("smart_tracker_session", JSON.stringify(data));
    sessionStorage.removeItem("smart_tracker_demo_role");
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
  hideAuthGateModal();
  sessionStorage.setItem("smart_tracker_demo_role", role);
  if (role === "child") {
    enableChildDemoMode();
  } else {
    enableDemoMode();
  }
}

function switchDemoRole(newRole) {
  haptic("medium");
  sessionStorage.setItem("smart_tracker_demo_role", newRole);
  if (newRole === "child") {
    enableChildDemoMode();
  } else {
    enableDemoMode();
  }
}

function enableChildDemoMode() {
  currentRole = "child";
  isDemoMode = true;
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
    },
  ];

  applyChildUI({
    id: 2002,
    full_name: "Azizbek Karimov (Farzand Demo)",
    virtual_balance: 18000,
    virtual_money_som: 180000,
    coin_rate_coins: 1000,
    coin_rate_som: 10000
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

  if (data.role === "parent") {
    parentId = data.user.id;
    coinRateCoins = data.user.coin_rate_coins || 1000;
    coinRateSom = data.user.coin_rate_som || 10000;
    updateCoinRateBadgeUI(coinRateCoins, coinRateSom);
    applyParentUI(data);

    if (data.children && data.children.length > 0) {
      childrenList = data.children;
      renderChildSelector();
      currentChildId = childrenList[0].id;
      selectChild(currentChildId);
    } else {
      showEmptyState();
    }
  } else if (data.role === "child") {
    currentChildId = data.user.id;
    coinRateCoins = data.user.coin_rate_coins || 1000;
    coinRateSom = data.user.coin_rate_som || 10000;
    childBalanceCoins = data.user.virtual_balance || 0;
    childBalanceSom = data.user.virtual_money_som || 0;
    applyChildUI(data.user);
    loadChildSelfData(data.user);
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

  if (window.lucide) lucide.createIcons();
}

async function loadChildSelfData(user) {
  childrenList = [user];
  currentChildId = user.id;

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
}

function logoutSession() {
  haptic("medium");
  if (confirm("Haqiqatan ham sayt hisobidan chiqmoqchimisiz?")) {
    localStorage.removeItem("smart_tracker_session");
    sessionStorage.removeItem("smart_tracker_demo_role");
    authenticatedSession = null;
    isDemoMode = false;
    document.getElementById("demoBadge")?.classList.add("hidden");
    document.getElementById("btnTopDemoAuth")?.classList.add("hidden");
    document.getElementById("demoNoticeBanner")?.classList.add("hidden");
    showAuthGateModal();
    showToast("🚪 Hisobdan chiqildi");
  }
}

// Coin Rate Settings Modal functions
function openCoinRateModal() {
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
    tab_settings: "Dizayn & Til",
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
    tab_settings: "Дизайн & Тил",
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
    tab_settings: "Дизайн и Язык",
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
    tab_settings: "Design & Lang",
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

  if (streetTileLayer && map.hasLayer(streetTileLayer)) {
    try { map.removeLayer(streetTileLayer); } catch(e) {}
  }

  let tileUrl = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png";
  let attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>';

  if (themeName === "midnight" || themeName === "galaxy" || themeName === "cyberpunk" || themeName === "magma" || themeName === "arcade") {
    tileUrl = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
  } else if (themeName === "daylight" || themeName === "frost") {
    tileUrl = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
  } else {
    tileUrl = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png";
  }

  streetTileLayer = L.tileLayer(tileUrl, {
    maxZoom: 19,
    subdomains: ["a", "b", "c", "d"],
    attribution: attribution
  }).addTo(map);
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
  haptic("medium");
  switchTab("settings");
}

function closeSettingsModal() {
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

// Initialize Leaflet Map with CartoDB Voyager and Esri Satellite layers
function initMap() {
  map = L.map("map", {
    zoomControl: false,
    attributionControl: false,
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
  isSatellite = !isSatellite;

  if (isSatellite) {
    map.removeLayer(streetTileLayer);
    satelliteTileLayer.addTo(map);
    satelliteLabelsLayer.addTo(map);
    btn.classList.add("text-yellow-400");
    btn.classList.remove("text-emerald-400");
    showToast("🛰 Yo'ldosh xaritasi yoqildi");
  } else {
    map.removeLayer(satelliteTileLayer);
    map.removeLayer(satelliteLabelsLayer);
    streetTileLayer.addTo(map);
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
      // No linked children yet -> Auto start interactive demo mode so map is active and fully functional
      enableDemoMode();
    }
  } catch (err) {
    console.warn("Backend API not reachable or static Netlify host, loading interactive demo mode:", err);
    enableDemoMode();
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
    },
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

// Populate child select dropdown
function renderChildSelector() {
  const select = document.getElementById("childSelect");
  select.innerHTML = childrenList
    .map((c) => `<option value="${c.id}">${c.full_name || "Farzand #" + c.id}</option>`)
    .join("");

  select.addEventListener("change", (e) => {
    haptic("light");
    currentChildId = e.target.value;
    selectChild(currentChildId);
  });
}

// Select active child and load telemetry
async function selectChild(childId) {
  const child = childrenList.find((c) => c.id == childId);
  if (!child) return;

  const initial = (child.full_name || "B").charAt(0).toUpperCase();
  document.getElementById("childAvatar").textContent = initial;

  toggleSosBanner(child.is_sos_active);

  await updateLiveTelemetry(childId);
  await loadGeofences(childId);
  await loadRouteHistory(childId);
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

    const loc = child.location;
    if (loc && loc.latitude && loc.longitude) {
      const lat = loc.latitude;
      const lon = loc.longitude;

      updateBatteryUI(loc.battery_level, loc.is_charging);
      updateSoundUI(loc.sound_mode);

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
      renderChildMarker(DEFAULT_LAT, DEFAULT_LON, child.full_name, false, 80);
      resolveAddress(DEFAULT_LAT, DEFAULT_LON);
    }
  } catch (err) {
    console.warn("Telemetry polling error:", err);
  }
}

// Reverse Geocoding with Nominatim & Cache
async function resolveAddress(lat, lon) {
  const key = `${lat.toFixed(4)},${lon.toFixed(4)}`;
  if (addressCache.has(key)) {
    document.getElementById("addressText").textContent = addressCache.get(key);
    return;
  }

  try {
    const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&zoom=18&addressdetails=1`, {
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

  document.getElementById("addressText").textContent = `Koordinata: ${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

// Render or move pulsing radar child marker
function renderChildMarker(lat, lon, name, isSos, battery) {
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

  try {
    const res = await fetch(`/api/v1/webapp/child/${childId}/geofences`);
    const data = await res.json();
    if (data.status !== "success") return;

    geofenceLayers.forEach((layer) => map.removeLayer(layer));
    geofenceLayers = [];

    const fences = data.geofences || [];
    const container = document.getElementById("zonesListContainer");

    if (fences.length === 0) {
      container.innerHTML = `
        <div class="text-center py-6 px-4">
          <p class="text-xs text-slate-400 mb-3">Hozircha kerakli joylar belgilanmagan.</p>
          <button onclick="openAddZoneModal()" class="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white text-xs font-bold rounded-xl shadow-md transition active:scale-95">
            ➕ Kerakli joy qo'shish
          </button>
        </div>
      `;
      document.getElementById("zoneStatusText").textContent = "Kerakli joylar belgilanmagan";
      return;
    }

    renderFencesToMapAndList(fences);
  } catch (err) {
    console.error("Failed to load geofences:", err);
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

  if (level === null || level === undefined) {
    text.textContent = "--%";
    return;
  }

  text.textContent = `${level}%`;

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

// Format relative time (e.g. "3 daqiqa oldin")
function formatTimeAgo(isoString) {
  const diffSec = Math.floor((new Date() - new Date(isoString)) / 1000);
  if (diffSec < 60) return "Hozirgina";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} daqiqa oldin`;
  return `${Math.floor(diffSec / 3600)} soat oldin`;
}

// SOS Alert Banner handling
function toggleSosBanner(isActive) {
  const banner = document.getElementById("sosAlertBanner");
  if (isActive) {
    banner.classList.remove("hidden");
    haptic("medium");
  } else {
    banner.classList.add("hidden");
  }
}

function dismissSos() {
  document.getElementById("sosAlertBanner").classList.add("hidden");
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
    if (tabName === "settings") {
      setGlobalTheme(currentTheme, false);
      setGlobalLanguage(currentLanguage, false);
    }
  } else {
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

  const activeTasks = (tasksList && tasksList.length > 0)
    ? tasksList.slice(0, 3)
    : [
        { id: 901, title: "Matematika darslarini bajarish", reward_coins: 5000, status: "pending" },
        { id: 902, title: "Xonani tozalash va tartibga keltirish", reward_coins: 3000, status: "scheduled", promised_time: "30 daqiqadan keyin" },
        { id: 903, title: "Ingliz tili yangi lug'at yodlash", reward_coins: 4000, status: "completed_by_child" }
      ];

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

  const places = (geofencesList && geofencesList.length > 0)
    ? geofencesList.slice(0, 4)
    : [
        { name: "Uyimiz (Asosiy Baza 🏡)", radius_meters: 200 },
        { name: "Maktab №45 (Bilim Maskani 🏫)", radius_meters: 250 },
        { name: "O'quv Markazi (Zukko Hub 📚)", radius_meters: 200 }
      ];

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
}

// Add Kerakli Joy Modal & Presets
function openAddZoneModal() {
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

// Toast notification helper
function showToast(message) {
  if (tg && tg.showPopup) {
    tg.showPopup({ message: message });
    return;
  }
  const toast = document.createElement("div");
  toast.className = "fixed bottom-16 left-1/2 -translate-x-1/2 z-[999] bg-slate-900/95 text-white text-xs px-4 py-2 rounded-2xl shadow-2xl border border-slate-700/80 animate-fade-in font-medium backdrop-blur-md";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
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
      url = `/api/v1/webapp/parent/${parentId}/tasks`;
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
            ${statusBadge}
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

function openAddTaskModal() {
  haptic("medium");
  document.getElementById("modal-add-task").classList.remove("hidden");
  clearTaskMedia();
}

function closeAddTaskModal() {
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
        const cornerMoneyText = document.getElementById("childMoneyCornerText");
        if (cornerMoneyText) cornerMoneyText.textContent = `${childBalanceSom.toLocaleString()} so'm`;
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

function triggerChildSOS() {
  haptic("heavy");
  playSirenSound();
  const banner = document.getElementById("sosAlertBanner");
  if (banner) {
    banner.classList.remove("hidden");
    const span = banner.querySelector("span");
    if (span) span.textContent = "🚨 DIQQAT: SOS xavf signali ota-onangizga yuborildi!";
  }
  showToast("🚨 SOS XAVF SIGNALI YUBORILDI! Ota-onangizga geolokatsiyangiz bilan xabar ketdi!");

  if (!isDemoMode && currentChildId) {
    fetch(`/api/v1/webapp/child/${currentChildId}/sos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_sos_active: true })
    }).catch(console.error);
  }
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

function callParent() {
  haptic("medium");
  playPingSound();
  if (confirm("Ota-onangiz bilan bog'lanmoqchimisiz?")) {
    if (window.Telegram?.WebApp?.close) {
      showToast("Ota-onangiz bilan Telegram chatga o'tilmoqda...");
      window.Telegram.WebApp.close();
    } else {
      window.location.href = "tel:+998901234567";
    }
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
