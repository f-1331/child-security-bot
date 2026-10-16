// Smart Parental Control - Mini App Client Logic

let tg = window.Telegram?.WebApp;
if (tg) {
  try {
    tg.ready();
    tg.expand();
  } catch (e) {
    console.warn("Telegram WebApp not fully supported or running in browser", e);
  }
}

// Global state
let parentId = null;
let currentChildId = null;
let childrenList = [];
let map = null;
let childMarker = null;
let geofenceLayers = [];
let routePolyline = null;
let previewCircle = null;
let isRouteVisible = true;
let pollInterval = null;

// Default initial coordinates (Tashkent, Uzbekistan)
const DEFAULT_LAT = 41.2995;
const DEFAULT_LON = 69.2401;

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", () => {
  if (window.lucide) {
    lucide.createIcons();
  }

  parseUrlParams();
  initMap();
  loadData();

  // Polling every 10 seconds for live telemetry
  pollInterval = setInterval(() => {
    if (currentChildId) {
      updateLiveTelemetry(currentChildId);
    }
  }, 10000);
});

// Trigger haptic feedback when Telegram WebApp is available
function haptic(type = "light") {
  if (tg && tg.HapticFeedback) {
    if (type === "medium") tg.HapticFeedback.impactOccurred("medium");
    else if (type === "success") tg.HapticFeedback.notificationOccurred("success");
    else tg.HapticFeedback.impactOccurred("light");
  }
}

// Extract parent_id and child_id from URL query or Telegram initData
function parseUrlParams() {
  const urlParams = new URLSearchParams(window.location.search);
  parentId = urlParams.get("parent_id");
  currentChildId = urlParams.get("child_id");

  if (!parentId && tg?.initDataUnsafe?.user?.id) {
    parentId = tg.initDataUnsafe.user.id;
  }

  // Fallback demo parent ID if running standalone preview
  if (!parentId) {
    parentId = 1001;
  }
}

// Initialize Leaflet Map with dark tile layer suitable for modern app
function initMap() {
  map = L.map("map", {
    zoomControl: false,
    attributionControl: false,
  }).setView([DEFAULT_LAT, DEFAULT_LON], 14);

  // CartoDB Dark Matter / Voyager OpenStreetMap tiles
  L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
  }).addTo(map);

  // Map click listener to allow positioning new safe zones
  map.on("click", (e) => {
    if (document.getElementById("modal-add-zone").classList.contains("hidden")) {
      // Normal click, do nothing
    } else {
      updatePreviewCircle(e.latlng.lat, e.latlng.lng);
    }
  });
}

// Fetch children and initial state
async function loadData() {
  try {
    const res = await fetch(`/api/v1/webapp/parent/${parentId}/children`);
    const data = await res.json();

    if (data.status === "success" && data.children.length > 0) {
      childrenList = data.children;
      renderChildSelector();

      // Pick first child or child from URL
      if (!currentChildId || !childrenList.some((c) => c.id == currentChildId)) {
        currentChildId = childrenList[0].id;
      }

      document.getElementById("childSelect").value = currentChildId;
      selectChild(currentChildId);
    } else {
      // No children yet
      document.getElementById("childSelect").innerHTML = `<option value="">Farzand ulanmagan</option>`;
      document.getElementById("coordText").textContent = "Ulangan farzandlar mavjud emas";
      document.getElementById("zoneStatusText").textContent = "Telegram bot orqali kod bilan farzand ulang";
    }
  } catch (err) {
    console.error("Error loading children:", err);
    document.getElementById("coordText").textContent = "Ma'lumotlarni yuklab bo'lmadi";
  }
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

// Select active child and load their telemetry, zones, route history
async function selectChild(childId) {
  const child = childrenList.find((c) => c.id == childId);
  if (!child) return;

  // Avatar initial
  const initial = (child.full_name || "B").charAt(0).toUpperCase();
  document.getElementById("childAvatar").textContent = initial;

  // Apply SOS State
  toggleSosBanner(child.is_sos_active);

  // Load telemetry, history, geofences, tasks
  await updateLiveTelemetry(childId);
  await loadGeofences(childId);
  await loadRouteHistory(childId);
  await loadTasks(childId);
}

// Update single child live coordinates and battery
async function updateLiveTelemetry(childId) {
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

      document.getElementById("coordText").textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;

      // Update battery pill
      updateBatteryUI(loc.battery_level, loc.is_charging);

      // Update sound mode
      updateSoundUI(loc.sound_mode);

      // Update last seen
      if (loc.recorded_at) {
        document.getElementById("lastSeenText").textContent = formatTimeAgo(loc.recorded_at);
      }

      // Render or update child marker on map
      renderChildMarker(lat, lon, child.full_name, child.is_sos_active, loc.battery_level);
    } else {
      document.getElementById("coordText").textContent = "Joylashuv hali yuborilmagan";
      document.getElementById("lastSeenText").textContent = "Kutilmoqda...";
    }
  } catch (err) {
    console.error("Telemetry refresh failed:", err);
  }
}

// Render or move child pulsing radar marker
function renderChildMarker(lat, lon, name, isSos, battery) {
  const initial = (name || "B").charAt(0).toUpperCase();
  const pulseClass = isSos ? "sos-pulse-ring" : "child-pulse-ring";
  const pinBg = isSos ? "bg-red-600" : "bg-blue-600";

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
    <div class="p-1 text-slate-800 text-xs font-semibold">
      <div class="font-bold text-sm mb-1">${name || "Farzand"}</div>
      <div>🔋 Batareya: ${battery ? battery + "%" : "Noma'lum"}</div>
      <div class="text-[10px] text-slate-500 mt-1">Jonli nazorat faol</div>
    </div>
  `);
}

// Center map view on child's current location
function centerOnChild() {
  haptic("light");
  if (childMarker) {
    map.flyTo(childMarker.getLatLng(), 16, { animate: true, duration: 1 });
  } else {
    showToast("Farzand joylashuvi hozircha mavjud emas");
  }
}

// Load and display safe zones (Geofences)
async function loadGeofences(childId) {
  try {
    const res = await fetch(`/api/v1/webapp/child/${childId}/geofences`);
    const data = await res.json();
    if (data.status !== "success") return;

    // Clear old fence layers
    geofenceLayers.forEach((layer) => map.removeLayer(layer));
    geofenceLayers = [];

    const fences = data.geofences;
    const container = document.getElementById("zonesListContainer");

    if (fences.length === 0) {
      container.innerHTML = `<p class="text-center text-xs text-slate-400 py-6">Hozircha xavfsiz hududlar belgilanmagan.</p>`;
      document.getElementById("zoneStatusText").textContent = "Xavfsiz hududlar o'rnatilmagan";
      return;
    }

    // Render circles on map
    fences.forEach((fence, idx) => {
      const colors = ["#10b981", "#3b82f6", "#8b5cf6", "#f59e0b"];
      const color = colors[idx % colors.length];

      const circle = L.circle([fence.latitude, fence.longitude], {
        color: color,
        fillColor: color,
        fillOpacity: 0.15,
        weight: 2,
        radius: fence.radius_meters,
      }).addTo(map);

      circle.bindTooltip(`🛡 ${fence.name} (${Math.round(fence.radius_meters)}m)`, {
        permanent: true,
        direction: "top",
        className: "geofence-tooltip",
      });

      geofenceLayers.push(circle);
    });

    // Populate modal list
    container.innerHTML = fences
      .map(
        (f) => `
      <div class="bg-slate-800/90 border border-slate-700/80 p-3 rounded-xl flex items-center justify-between shadow-sm">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-xs">
            <i data-lucide="shield" class="w-4 h-4"></i>
          </div>
          <div>
            <h4 class="text-xs font-bold text-white">${f.name}</h4>
            <span class="text-[11px] text-slate-400">Radiusi: ${Math.round(f.radius_meters)} metr</span>
          </div>
        </div>
        <button onclick="deleteGeofence(${f.id})" class="p-1.5 text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition" title="O'chirish">
          <i data-lucide="trash-2" class="w-4 h-4"></i>
        </button>
      </div>
    `
      )
      .join("");

    if (window.lucide) lucide.createIcons();
    document.getElementById("zoneStatusText").textContent = `${fences.length} ta xavfsiz hudud faol`;
  } catch (err) {
    console.error("Failed to load geofences:", err);
  }
}

// Load today's route history and draw polyline
async function loadRouteHistory(childId) {
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

    const latlngs = points.map((p) => [p.latitude, p.longitude]);

    // Draw smooth route polyline
    routePolyline = L.polyline(latlngs, {
      color: "#f59e0b",
      weight: 4,
      opacity: 0.8,
      dashArray: "6, 8",
    });

    if (isRouteVisible) {
      routePolyline.addTo(map);
    }

    // Populate timeline list
    container.innerHTML = points
      .slice()
      .reverse()
      .map((p) => {
        const time = p.recorded_at ? new Date(p.recorded_at).toLocaleTimeString("uz-UZ", { hour: "2-digit", minute: "2-digit" }) : "--:--";
        const batteryStr = p.battery_level ? `${p.battery_level}%` : "--";
        const chargingIcon = p.is_charging ? "⚡" : "";

        return `
        <div class="bg-slate-800/80 border border-slate-700/60 p-2.5 rounded-xl flex items-center justify-between text-xs">
          <div class="flex items-center gap-2">
            <div class="w-2 h-2 rounded-full bg-amber-400"></div>
            <span class="font-mono text-slate-200">${p.latitude.toFixed(4)}, ${p.longitude.toFixed(4)}</span>
          </div>
          <div class="flex items-center gap-3 text-slate-400 text-[11px]">
            <span>🔋 ${batteryStr} ${chargingIcon}</span>
            <span class="font-semibold text-slate-300">🕒 ${time}</span>
          </div>
        </div>
      `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load route history:", err);
  }
}

// Toggle route history polyline on map
function toggleRouteHistory() {
  haptic("light");
  if (!routePolyline) {
    showToast("Harakat tarixi topilmadi");
    return;
  }

  isRouteVisible = !isRouteVisible;
  const btn = document.getElementById("btnToggleRoute");

  if (isRouteVisible) {
    routePolyline.addTo(map);
    btn.classList.add("text-amber-400");
    btn.classList.remove("text-slate-500");
    showToast("Harakat yo'li ko'rsatildi");
  } else {
    map.removeLayer(routePolyline);
    btn.classList.remove("text-amber-400");
    btn.classList.add("text-slate-500");
    showToast("Harakat yo'li yashirildi");
  }
}

// Load tasks for child
async function loadTasks(childId) {
  try {
    const res = await fetch(`/api/v1/webapp/child/${childId}/tasks`);
    const data = await res.json();
    if (data.status !== "success") return;

    const child = childrenList.find((c) => c.id == childId);
    if (child) {
      document.getElementById("tasksBalanceText").textContent = `${child.virtual_balance.toLocaleString()} tanga`;
    }

    const tasks = data.tasks;
    const container = document.getElementById("tasksListContainer");

    if (tasks.length === 0) {
      container.innerHTML = `<p class="text-center text-xs text-slate-400 py-6">Faol vazifalar mavjud emas.</p>`;
      return;
    }

    container.innerHTML = tasks
      .map((t) => {
        let badge = "";
        if (t.status === "approved") {
          badge = `<span class="bg-emerald-500/20 text-emerald-400 text-[10px] px-2 py-0.5 rounded-full font-bold">✅ Bajarildi</span>`;
        } else if (t.status === "completed_by_child") {
          badge = `<span class="bg-amber-500/20 text-amber-400 text-[10px] px-2 py-0.5 rounded-full font-bold">⏳ Tekshiruvda</span>`;
        } else {
          badge = `<span class="bg-blue-500/20 text-blue-400 text-[10px] px-2 py-0.5 rounded-full font-bold">▫️ Bajarilmoqda</span>`;
        }

        return `
        <div class="bg-slate-800/90 border border-slate-700/80 p-3 rounded-xl flex items-center justify-between">
          <div>
            <h4 class="text-xs font-bold text-white">${t.title}</h4>
            ${t.description ? `<p class="text-[11px] text-slate-400 mt-0.5">${t.description}</p>` : ""}
            <div class="mt-1 flex items-center gap-2">
              <span class="text-xs font-semibold text-yellow-400">+${t.reward_coins} tanga</span>
              ${badge}
            </div>
          </div>
        </div>
      `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load tasks:", err);
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
    pill.className = "flex items-center gap-1 bg-blue-900/40 px-2 py-1 rounded-full text-xs font-medium text-blue-400 border border-blue-500/40";
  } else if (level <= 15) {
    pill.className = "flex items-center gap-1 bg-red-900/40 px-2 py-1 rounded-full text-xs font-bold text-red-400 border border-red-500/50 animate-pulse";
  } else if (level <= 30) {
    pill.className = "flex items-center gap-1 bg-amber-900/40 px-2 py-1 rounded-full text-xs font-medium text-amber-400 border border-amber-500/40";
  } else {
    pill.className = "flex items-center gap-1 bg-emerald-900/40 px-2 py-1 rounded-full text-xs font-medium text-emerald-400 border border-emerald-500/40";
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
    text.textContent = "Qo'ng'iroq";
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

  // Hide all sliding modals first
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.add("hidden");
  });

  if (tabName !== "map") {
    const targetModal = document.getElementById(`modal-${tabName}`);
    if (targetModal) targetModal.classList.remove("hidden");
  }
}

function closeModal(tabName) {
  haptic("light");
  const modal = document.getElementById(`modal-${tabName}`);
  if (modal) modal.classList.add("hidden");
  switchTab("map");
}

// Add Safe Zone Modal & Preview Circle
function openAddZoneModal() {
  haptic("medium");
  const modal = document.getElementById("modal-add-zone");
  modal.classList.remove("hidden");

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

// Submit new safe zone to API
async function submitNewZone() {
  const nameInput = document.getElementById("inputZoneName");
  const name = nameInput.value.trim();
  if (!name) {
    showToast("Iltimos, hudud nomini kiriting!");
    return;
  }

  const radius = parseFloat(document.getElementById("inputZoneRadius").value);
  const center = previewCircle ? previewCircle.getLatLng() : map.getCenter();

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
      showToast("✅ Xavfsiz hudud saqlandi!");
      nameInput.value = "";
      closeAddZoneModal();
      if (currentChildId) {
        await loadGeofences(currentChildId);
      }
    } else {
      showToast("Xatolik: Saqlab bo'lmadi");
    }
  } catch (err) {
    console.error("Failed to create safe zone:", err);
    showToast("Server bilan aloqa uzildi");
  }
}

// Delete geofence
async function deleteGeofence(fenceId) {
  if (!confirm("Haqiqatan ham bu xavfsiz hududni o'chirmoqchimisiz?")) return;

  try {
    const res = await fetch(`/api/v1/webapp/geofence/${fenceId}?parent_id=${parentId}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (data.status === "success") {
      haptic("medium");
      showToast("Hudud o'chirildi");
      if (currentChildId) {
        await loadGeofences(currentChildId);
      }
    }
  } catch (err) {
    console.error("Failed to delete geofence:", err);
  }
}

// Open Google / Yandex Maps external navigation
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
  toast.className = "fixed bottom-16 left-1/2 -translate-x-1/2 z-[999] bg-slate-800 text-white text-xs px-4 py-2 rounded-xl shadow-2xl border border-slate-700 animate-fade-in";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}
