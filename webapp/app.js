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

// Clean up any injected Netlify badges, drawers or watermarks
function removeNetlifyBadge() {
  const selectors = [
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

  parseUrlParams();
  initMap();
  loadData();

  // Polling every 8 seconds for live telemetry
  pollInterval = setInterval(() => {
    if (currentChildId && !isDemoMode) {
      updateLiveTelemetry(currentChildId);
    }
  }, 8000);
});

// Trigger haptic feedback when Telegram WebApp is available
function haptic(type = "light") {
  if (tg && tg.HapticFeedback) {
    try {
      if (type === "medium") tg.HapticFeedback.impactOccurred("medium");
      else if (type === "success") tg.HapticFeedback.notificationOccurred("success");
      else if (type === "warning") tg.HapticFeedback.notificationOccurred("warning");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (e) {}
  }
}

// Extract parent_id from URL query, Telegram initData, or fallback
function parseUrlParams() {
  const urlParams = new URLSearchParams(window.location.search);
  parentId = urlParams.get("parent_id");
  currentChildId = urlParams.get("child_id");

  // Read Telegram user id if available
  if (!parentId && tg?.initDataUnsafe?.user?.id) {
    parentId = tg.initDataUnsafe.user.id;
  }

  // Fallback demo parent ID if not specified
  if (!parentId) {
    parentId = 1001;
  }
}

// Initialize Leaflet Map with CartoDB Voyager and Esri Satellite layers
function initMap() {
  map = L.map("map", {
    zoomControl: false,
    attributionControl: false,
  }).setView([DEFAULT_LAT, DEFAULT_LON], 14);

  // 1. Street Layer: OpenStreetMap Standard (100% Free, No API Key Required)
  streetTileLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    subdomains: ["a", "b", "c"],
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(map);

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
      document.getElementById("demoBadge").classList.add("hidden");
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
  document.getElementById("emptyStateBanner").classList.add("hidden");
  document.getElementById("demoBadge").classList.remove("hidden");

  childrenList = [
    {
      id: 2002,
      full_name: "Azizbek Karimov (Demo)",
      username: "azizbek_child",
      virtual_balance: 18000,
      is_sos_active: false,
    },
  ];

  currentChildId = 2002;
  renderChildSelector();

  // Render demo child live position in Tashkent
  const demoLat = 41.2916;
  const demoLon = 69.2121;

  renderChildMarker(demoLat, demoLon, "Azizbek Karimov (Demo)", false, 82);
  updateBatteryUI(82, false);
  updateSoundUI("normal");
  resolveAddress(demoLat, demoLon);

  // Load demo geofences
  renderDemoGeofences();

  // Load demo route history
  renderDemoHistory();

  showToast("🎮 Demo rejimi faollashtirildi");
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
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.classList.remove("text-blue-400", "active");
    btn.classList.add("text-slate-400");
  });

  const activeBtn = document.getElementById(`tabBtn-${tabName}`);
  if (activeBtn) {
    activeBtn.classList.add("text-blue-400", "active");
    activeBtn.classList.remove("text-slate-400");
  }

  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.add("hidden");
  });

  const floatControls = document.getElementById("floatingMapControls");
  const locCard = document.getElementById("locationCard");

  if (tabName !== "map") {
    const targetModal = document.getElementById(`modal-${tabName}`);
    if (targetModal) targetModal.classList.remove("hidden");
    if (floatControls) floatControls.classList.add("hidden");
    if (locCard) locCard.classList.add("hidden");
    if (tabName === "tasks") {
      loadTasks();
    }
  } else {
    if (floatControls) floatControls.classList.remove("hidden");
    if (locCard) locCard.classList.remove("hidden");
  }
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
    }
  ];
  renderTasksList();
}

function renderTasksList() {
  const container = document.getElementById("tasksListContainer");
  if (!container) return;

  if (tasksList.length === 0) {
    container.innerHTML = `
      <div class="text-center py-8">
        <div class="w-12 h-12 bg-slate-800 rounded-full flex items-center justify-center mx-auto text-slate-400 mb-2">
          <i data-lucide="check-circle" class="w-6 h-6"></i>
        </div>
        <p class="text-xs font-semibold text-slate-300">Faol topshiriqlar yo'q</p>
        <p class="text-[11px] text-slate-500 mt-1">Yangi topshiriq qo'shish uchun yuqoridagi tugmani bosing</p>
      </div>
    `;
    if (window.lucide) lucide.createIcons();
    return;
  }

  container.innerHTML = tasksList.map(t => {
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
            <span>Farzand yuborgan hisobot rasmi:</span>
          </div>
          <img src="${t.proof_photo_url}" class="rounded-lg max-h-36 w-full object-cover cursor-pointer" onclick="window.open('${t.proof_photo_url}', '_blank')" />
        </div>
      `;
    }

    // Action buttons based on status
    let actionButtons = "";
    if (t.status === "completed_by_child") {
      actionButtons = `
        <div class="flex gap-2 mt-3 pt-2 border-t border-slate-800">
          <button onclick="approveTask(${t.id})" class="flex-1 py-1.5 bg-emerald-600 hover:bg-emerald-500 active:scale-95 text-white rounded-lg text-xs font-bold transition shadow">
            ✅ Tasdiqlash
          </button>
          <button onclick="rejectTask(${t.id})" class="px-3 py-1.5 bg-slate-800 hover:bg-red-600/30 text-red-400 border border-slate-700 rounded-lg text-xs font-medium transition">
            ❌ Rad etish
          </button>
        </div>
      `;
    } else if (t.status === "pending" || t.status === "seen" || t.status === "scheduled") {
      const seenBtn = t.status === "pending"
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
    }

    return `
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-2xl p-3.5 shadow-md flex flex-col justify-between transition hover:border-slate-600">
        <div>
          <div class="flex items-start justify-between gap-2">
            <h3 class="text-xs font-bold text-white">${t.title}</h3>
            ${statusBadge}
          </div>
          ${t.description ? `<p class="text-[11px] text-slate-300 mt-1">${t.description}</p>` : ''}
          <div class="flex items-center gap-1.5 mt-2 text-xs font-bold text-amber-400">
            <i data-lucide="coins" class="w-3.5 h-3.5"></i>
            <span>+${(t.reward_coins || 1000).toLocaleString()} tanga</span>
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
  document.getElementById("modal-task-schedule").classList.remove("hidden");
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
  haptic("medium");
  try {
    if (isDemoMode) {
      const t = tasksList.find(x => x.id === taskId);
      if (t) t.status = "approved";
      renderTasksList();
      showToast("Topshiriq tasdiqlandi (+tanga o'tkazildi)!");
      return;
    }

    const res = await fetch(`/api/v1/webapp/task/${taskId}/approve`, { method: "POST" });
    const data = await res.json();
    if (data.status === "success") {
      haptic("success");
      showToast("Topshiriq tasdiqlandi!");
      loadTasks();
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
      showToast("Topshiriq rad etildi.");
      return;
    }

    const res = await fetch(`/api/v1/webapp/task/${taskId}/reject`, { method: "POST" });
    const data = await res.json();
    if (data.status === "success") {
      showToast("Topshiriq rad etildi.");
      loadTasks();
    }
  } catch (e) {
    console.error("Reject task error:", e);
  }
}
