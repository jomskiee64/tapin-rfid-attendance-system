/* TapIn Face Scanner
   - Runs standalone (e.g. hosted on Vercel) — talks to the TapIn API over CORS
   - Camera frames stay in the browser
   - Loads profile images/templates from the API's /api/faces/employees route
   - Face detection + recognition runs locally in the browser
   - When a face is matched with enough confidence, attendance is recorded
     via /api/faces/record — no RFID tap required
   - Logs all face detections */

// ============================================================================
// API CONNECTION — mirrors dashboard.js
// ============================================================================
const API_ORIGIN = (window.TAPIN_API_URL || 'https://lolenseu.pythonanywhere.com').replace(/\/+$/, '');
const API_BASE = API_ORIGIN + '/api/faces';

// Diagnostic — remove once everything works
console.log("[faces.js] window.TAPIN_API_URL =", window.TAPIN_API_URL);
console.log("[faces.js] API_ORIGIN =", API_ORIGIN);
console.log("[faces.js] API_BASE =", API_BASE);

const MODEL_URL = "https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js@0.22.2/weights";

// Distance threshold for face matching. Lower = stricter.
//   0.50  → ~50% confidence (old default, loose)
//   0.45  → ~55% confidence
//   0.40  → ~60% confidence  ← this is what we want now
//
// The value below is tuned to ~60% confidence so a face match is
// accepted a bit more easily without becoming unreliable.
const MATCH_THRESHOLD = 0.40;

// Detector input size. Lower = faster but less accurate.
//   416 = default (slow, accurate)
//   320 = balanced
//   224 = fast (what we use now, still plenty accurate for a webcam)
const DETECTOR_INPUT_SIZE = 224;

// Minimum detector confidence to consider a box a real face.
// Lowered slightly so partial faces still register, but not so low
// that random shapes get detected.
const DETECTOR_SCORE_THRESHOLD = 0.45;

// How often the recognition loop runs. 250 ms = 4 scans/second.
// This is fast enough to feel instant for attendance, but slow enough
// that the CPU is not pegged at 100% (which was causing the flicker).
const SCAN_INTERVAL_MS = 250;

// Per-person cooldown between recorded attendance events. 3 minutes = 180000 ms.
// The cooldown ONLY starts AFTER a successful server record,
// never on a low-confidence or rejected scan.
//
// Behaviour:
//   1. First time a face is matched and ACCEPTED → cooldown starts NOW.
//   2. Every subsequent frame within 3 minutes → skip entirely.
//   3. After 3 minutes have passed, if the face is seen again → send a NEW
//      scan to the API with a fresh timestamp.
const ATTENDANCE_COOLDOWN = 180000; // 3 minutes between scans per person

const STATS_REFRESH_MS = 10000; // Refresh "Present Today" + stats every 10s

// --- Single-face-at-a-time setting ---------------------------------------
// The scanner now uses detectSingleFace(), which guarantees only ONE
// face is considered per frame. This constant is kept for clarity and
// to keep the recording path explicit.
const SINGLE_FACE_MODE = true;

const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const statusEl = document.getElementById("statusText");
const statusDot = document.getElementById("statusDot");
const messageEl = document.getElementById("message");
const faceCountEl = document.getElementById("faceCount");
const logEntries = document.getElementById("logEntries");

let modelsReady = false;
let stream = null;
let running = false;
let busy = false;
let faceTemplates = [];
let lastSent = new Map();
let scanCount = 0;
let todayAttendance = [];
let statsRefreshTimer = null;
let scanLoopTimer = null;

// Tracks the last UID/RFID we recorded, so we can keep the message line
// showing "Waiting for next scan" instead of spamming the cooldown count.
let lastRecordedKey = null;
let lastRecordedName = "";

// ========================================================================
// STATUS HELPERS
// ========================================================================

function setStatus(text, state) {
  if (statusEl) statusEl.textContent = text;
  if (statusDot) {
    statusDot.className = "status-dot";
    if (state === "ready" || state === "online") statusDot.classList.add("online");
    else if (state === "error" || state === "offline") statusDot.classList.add("offline");
    else statusDot.classList.add("unknown");
  }
}

// Remove the "Waiting for face detection…" placeholder the moment we have
// a real log entry to show, so the panel doesn't stay stuck on that text.
function clearLogPlaceholder() {
  if (!logEntries) return;
  const empty = logEntries.querySelector(".log-empty");
  if (empty) empty.remove();
}

// Format a JS Date into the "YYYY-MM-DD HH:MM:SS" string the server expects.
// We deliberately build this in LOCAL time because the DTR stores local
// wall-clock times (the server parses "YYYY-MM-DD HH:MM:SS" as naive local
// time). Sending ISO/UTC would shift the times by the timezone offset.
function formatLocalTimestamp(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  const yyyy = date.getFullYear();
  const mm = pad(date.getMonth() + 1);
  const dd = pad(date.getDate());
  const hh = pad(date.getHours());
  const mi = pad(date.getMinutes());
  const ss = pad(date.getSeconds());
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
}

// ========================================================================
// LOAD MODELS AND DATA
// ========================================================================

async function boot() {
  // ---- Phase 1: Load AI models. If this fails, nothing else can work. ----
  try {
    setStatus("Loading AI…", "unknown");
    await faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL);
    await faceapi.nets.faceLandmark68TinyNet.loadFromUri(MODEL_URL);
    await faceapi.nets.faceRecognitionNet.loadFromUri(MODEL_URL);
    modelsReady = true;
    setStatus("Camera ready", "ready");
    messageEl.textContent = "Start the scanner to detect faces.";
    console.log("[faces.js] AI models loaded successfully");
  } catch (e) {
    console.error("[faces.js] Model load error:", e);
    setStatus("AI Load Failed", "error");
    messageEl.textContent = "Could not load face models. Check internet connection.";
    return; // Nothing else will work without models
  }

  // ---- Phase 2: Load data. Each call is isolated so one failure doesn't
  //              block the other two from running. ----
  await loadTemplates().catch(e => {
    console.error("[faces.js] loadTemplates failed:", e);
  });
  await loadAttendance().catch(e => {
    console.error("[faces.js] loadAttendance failed:", e);
  });
  await loadStats().catch(e => {
    console.error("[faces.js] loadStats failed:", e);
  });

  // ---- Phase 3: Start the periodic stats refresh. This keeps "Present
  //              Today" in sync with the server's persistent daily counter,
  //              even when no RFID taps are happening on this device. ----
  if (statsRefreshTimer) clearInterval(statsRefreshTimer);
  statsRefreshTimer = setInterval(() => {
    loadStats().catch(e => console.warn("[faces.js] background loadStats failed:", e));
  }, STATS_REFRESH_MS);

  console.log("[faces.js] Boot sequence complete");
}

async function loadTemplates() {
  console.log("[faces.js] loadTemplates: fetching", `${API_BASE}/employees`);
  try {
    const res = await fetch(`${API_BASE}/employees`);
    console.log("[faces.js] /employees HTTP status:", res.status);

    if (!res.ok) {
      console.error(`[faces.js] /api/faces/employees returned HTTP ${res.status}`);
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();
    console.log("[faces.js] /employees response:", data);

    const employees = data.employees || [];
    console.log(`[faces.js] ${employees.length} employee template(s) returned by API`);

    faceTemplates = [];

    for (const item of employees) {
      const employee = item.employee || {};
      const rawImageUrl = item.image_url || employee.image || "";
      if (!rawImageUrl) {
        console.warn("[faces.js] skipping employee with no image_url:", item);
        continue;
      }

      // The API returns paths like "/storage/profiles/xxx.jpg" that are
      // relative to the API host, not this page's own (Vercel) origin.
      const imageUrl = /^https?:\/\//i.test(rawImageUrl)
        ? rawImageUrl
        : API_ORIGIN + rawImageUrl;

      try {
        const img = await faceapi.fetchImage(imageUrl);
        const detection = await faceapi.detectSingleFace(
            img,
            new faceapi.TinyFaceDetectorOptions({
              inputSize: 320,
              scoreThreshold: DETECTOR_SCORE_THRESHOLD
            })
          )
          .withFaceLandmarks(true) // true = use the tiny landmark net (matches boot())
          .withFaceDescriptor();

        if (!detection) {
          console.warn(`[faces.js] No face detected in profile image for ${employee.name || item.rfid} (${imageUrl})`);
          continue;
        }

        faceTemplates.push({
          ...employee,
          rfid: item.rfid || employee.rfid,
          uid: employee.uid,
          name: employee.name
            || `${employee.firstname || ""} ${employee.lastname || ""}`.trim()
            || "Unknown",
          imageUrl,
          descriptor: Array.from(detection.descriptor)
        });

        console.log(`[faces.js] ✔ template built for ${employee.name || item.rfid}`);
      } catch (imgErr) {
        console.warn(`[faces.js] Could not load face template for ${employee.name || item.rfid}:`, imgErr);
      }
    }

    console.log(`[faces.js] ✅ Loaded ${faceTemplates.length} face templates from profile images`);
    faceCountEl.textContent = `Faces: ${faceTemplates.length}`;

    if (faceTemplates.length) {
      setStatus("Profile templates ready", "ready");
    } else {
      setStatus("No profile images found", "unknown");
    }
  } catch (e) {
    console.error("[faces.js] Error loading templates:", e);
    faceTemplates = [];
    faceCountEl.textContent = "Faces: 0";
    setStatus("Template load failed", "error");
    // Re-throw so boot() can log it, but boot() already catches per-call.
    throw e;
  }
}

async function loadAttendance() {
  console.log("[faces.js] loadAttendance: fetching", `${API_BASE}/recent-attendance`);
  try {
    const res = await fetch(`${API_BASE}/recent-attendance`);
    console.log("[faces.js] /recent-attendance HTTP status:", res.status);

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();
    console.log("[faces.js] /recent-attendance response:", data);

    if (data.status === "success") {
      todayAttendance = data.attendance || [];
      renderAttendance(todayAttendance);
      console.log(`[faces.js] rendered ${todayAttendance.length} attendance record(s)`);
    } else {
      console.warn("[faces.js] /recent-attendance returned non-success:", data);
      renderAttendance([]);
    }
  } catch (e) {
    console.error("[faces.js] Error loading attendance:", e);
    // Make sure the UI doesn't stay stuck on "Loading…"
    const container = document.getElementById("todayAttendance");
    if (container) {
      container.innerHTML = `<span class="help">⚠️ Could not load attendance (${e.message}).</span>`;
    }
    throw e;
  }
}

async function loadStats() {
  console.log("[faces.js] loadStats: fetching", `${API_BASE}/dashboard-stats`);
  try {
    const res = await fetch(`${API_BASE}/dashboard-stats`);
    console.log("[faces.js] /dashboard-stats HTTP status:", res.status);

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();
    console.log("[faces.js] /dashboard-stats response:", data);

    if (data.status === "success") {
      const stats = data.stats || {};

      const elTfs = document.getElementById("statTFS");
      const elPresent = document.getElementById("statPresent");
      const elTotal = document.getElementById("statTotal");
      const elRate = document.getElementById("statRate");

      if (elTfs) elTfs.textContent = stats.profile_images || 0;
      // "Present Today" comes from the persistent daily counter on the
      // server (storage/database/daily_stats.json). This is the same
      // number the admin dashboard shows — it does NOT depend on this
      // scanner being open, and it also counts RFID taps.
      if (elPresent) elPresent.textContent = stats.present_today || 0;
      if (elTotal) elTotal.textContent = stats.total_employees || 0;
      if (elRate) elRate.textContent = stats.attendance_rate || "0%";

      console.log("[faces.js] stats rendered:", {
        profile_images: stats.profile_images,
        present_today: stats.present_today,
        total_employees: stats.total_employees,
        attendance_rate: stats.attendance_rate
      });
    } else {
      console.warn("[faces.js] /dashboard-stats returned non-success:", data);
    }
  } catch (e) {
    console.error("[faces.js] Error loading stats:", e);
    // Only blank out the values on a HARD failure. We leave any existing
    // number on screen so the periodic refresh loop gets a chance to
    // recover on the next tick without blanking a valid value.
    throw e;
  }
}

// ========================================================================
// CAMERA CONTROLS
// ========================================================================

async function startCamera() {
  if (!modelsReady) return alert("Face models are not ready yet.");

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
      audio: false
    });

    video.srcObject = stream;
    await video.play();

    // Wait for metadata so videoWidth/videoHeight are correct
    if (!video.videoWidth) {
      await new Promise(resolve => {
        video.onloadedmetadata = () => resolve();
      });
    }

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    running = true;

    document.getElementById("start").disabled = true;
    document.getElementById("stop").disabled = false;
    document.getElementById("cameraHint").style.display = "none";
    messageEl.textContent = "🔍 Scanning for faces...";

    // Push an initial info entry so the log isn't stuck on
    // "Waiting for face detection…" after the user starts the camera.
    addLogEntry("Scanner", 0, "info", "Camera started — scanning for faces");
    clearLogPlaceholder();

    scheduleScanLoop(0);
  } catch (e) {
    console.error("Camera error:", e);
    alert("Camera access failed. Allow camera permission and use HTTPS or localhost.");
  }
}

function stopCamera() {
  running = false;
  if (scanLoopTimer) {
    clearTimeout(scanLoopTimer);
    scanLoopTimer = null;
  }
  if (stream) {
    stream.getTracks().forEach(t => t.stop());
    stream = null;
  }
  video.srcObject = null;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  document.getElementById("start").disabled = false;
  document.getElementById("stop").disabled = true;
  document.getElementById("cameraHint").style.display = "grid";
  messageEl.textContent = "Camera stopped.";

  // Log the stop so the recognition log reflects the current state.
  addLogEntry("Scanner", 0, "info", "Camera stopped");
}

// Fixed-interval scan scheduler. Replaces requestAnimationFrame so the
// scanner runs at ~4 fps for face detection instead of 60 fps — that's
// what was causing the visual flicker and CPU pegging.
function scheduleScanLoop(delay = SCAN_INTERVAL_MS) {
  if (!running) return;
  if (scanLoopTimer) clearTimeout(scanLoopTimer);
  scanLoopTimer = setTimeout(async () => {
    await recognitionLoop();
    scheduleScanLoop(SCAN_INTERVAL_MS);
  }, delay);
}

// ========================================================================
// FACE RECOGNITION
// ========================================================================

function distance(a, b) {
  let sum = 0;
  for (let i = 0; i < a.length; i++) {
    const d = a[i] - b[i];
    sum += d * d;
  }
  return Math.sqrt(sum);
}

function identify(descriptor) {
  let best = null;

  for (const record of faceTemplates) {
    const savedDescriptor = record.descriptor || [];
    if (!Array.isArray(savedDescriptor) || savedDescriptor.length !== descriptor.length) continue;

    const d = distance(descriptor, savedDescriptor);
    if (!best || d < best.distance) {
      const employee = record.employee || {};
      best = {
        employee,
        rfid: record.rfid || employee.rfid || "",
        uid: employee.uid || record.uid || "",
        distance: d,
        name: employee.name
          || `${employee.firstname || ""} ${employee.lastname || ""}`.trim()
          || record.name
          || "Unknown",
        samples: 1
      };
    }
  }

  if (!best || best.distance > MATCH_THRESHOLD) return null;

  const confidence = Math.max(0, Math.min(100, (1 - best.distance) * 100));
  return { ...best, confidence };
}

// Decide whether a given match is still inside its 3-minute cooldown.
// We keep the "last scanned at" timestamp in memory (lastSent map) so
// the browser never spams the server with the same person over and over.
//
// NOTE: The check is done against Date.now() at the moment the decision is
// made, and the timestamp stored is ALSO Date.now() at that exact moment.
// That guarantees the cooldown window is exactly 180 seconds per person.
function isWithinCooldown(key) {
  const previous = lastSent.get(key) || 0;
  if (!previous) return false;
  return (Date.now() - previous) < ATTENDANCE_COOLDOWN;
}

// Format the remaining cooldown seconds for the log message. Purely
// cosmetic — helps whoever is watching the scanner understand WHY a
// known face is being skipped.
function cooldownRemainingSeconds(key) {
  const previous = lastSent.get(key) || 0;
  if (!previous) return 0;
  const elapsedMs = Date.now() - previous;
  const remainingMs = ATTENDANCE_COOLDOWN - elapsedMs;
  return Math.max(0, Math.ceil(remainingMs / 1000));
}

// Format the remaining cooldown as "M:SS" for a friendlier readout.
function formatCooldownMmSs(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Record attendance for a single face match.
//
// Flow:
//   1. Compute the stable key for this employee (uid preferred, else rfid).
//   2. If we already scanned this person within the last 3 minutes → skip
//      silently (messageEl shows the countdown so the operator knows why).
//   3. Otherwise: capture the CURRENT local timestamp as `scanned_at`,
//      then POST to the server.
//
// IMPORTANT: The cooldown marker is set ONLY after a successful server
// response. Low-confidence, rejected, or failed requests do NOT lock the
// person out — they can retry immediately.
async function recordAttendance(match) {
  const uid = String(match.uid || match.employee?.uid || "");
  const rfid = String(match.rfid || match.employee?.rfid || "");

  if (!uid && !rfid) {
    console.warn("No UID or RFID found for match");
    return;
  }

  const key = uid || rfid;

  // ---- Cooldown guard ----------------------------------------------------
  // Same person, seen again within 3 minutes → do nothing. We only update
  // the on-screen message so the operator sees why nothing was recorded,
  // and we keep the message line in a calm "Waiting for next scan" state
  // instead of spamming the countdown every frame.
  if (isWithinCooldown(key)) {
    const remaining = cooldownRemainingSeconds(key);
    messageEl.textContent =
      `⏳ ${match.name} — already scanned. Next scan in ${formatCooldownMmSs(remaining)}. Waiting for next face…`;
    return;
  }

  // ---- Capture the exact scan time NOW -----------------------------------
  // This is the wall-clock time of the frame that triggered the scan.
  // We format it in LOCAL time because the server's DTR parses it as naive
  // local time — sending UTC/ISO would shift the recorded time by the
  // timezone offset.
  const scannedAt = formatLocalTimestamp(new Date());

  try {
    const res = await fetch(`${API_BASE}/record`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        uid: uid,
        rfid: rfid,
        confidence: match.confidence,
        scanned_at: scannedAt,
        face_detected: true
      })
    });

    const data = await res.json();

    if (res.ok && data.status === "success") {
      const empName = data.employee?.name || data.employee?.firstname || match.name;

      // Remember who we just recorded so the message line can transition
      // to "Waiting for next scan" without losing the last scan info.
      lastRecordedKey = key;
      lastRecordedName = empName;

      // ✅ Cooldown starts ONLY here, after a real successful record.
      lastSent.set(key, Date.now());

      addLogEntry(empName, data.confidence || match.confidence, "success", data.attendance_status || "recorded");

      // Refresh the two panels that depend on attendance.
      loadAttendance().catch(() => {});
      loadStats().catch(() => {});

      // Show a clear confirmation and immediately tell the operator the
      // scanner is now idle and waiting for the next face.
      messageEl.textContent =
        `✅ ${empName} — recorded at ${scannedAt.split(" ")[1]}. Waiting for next face…`;

    } else if (res.status === 403) {
      addLogEntry(match.name, match.confidence, "warning", "low confidence");
      messageEl.textContent = "⚠️ Confidence too low for attendance. Waiting for next face…";
      // No cooldown — the person can retry immediately by facing the
      // camera more clearly.

    } else {
      addLogEntry(match.name, match.confidence, "error", "rejected");
      messageEl.textContent = "❌ Attendance not recorded. Waiting for next face…";
      // No cooldown — don't punish the next attempt.
    }
  } catch (e) {
    console.error("Attendance error:", e);
    addLogEntry(match.name, match.confidence, "error", "API error");
    messageEl.textContent = "⚠️ API connection failed. Waiting for next face…";
    // Network error → allow a retry immediately.
  }
}

// ========================================================================
// RECOGNITION LOOP
// ========================================================================
//
// Now uses detectSingleFace — only ONE face is ever considered per scan.
// This is what stops the multi-person race condition and the flicker.
//
// Runs on a fixed 250 ms timer (via scheduleScanLoop) instead of
// requestAnimationFrame, so the detector is not called 60× per second.

async function recognitionLoop() {
  if (!running || busy) return;
  busy = true;

  try {
    const detection = await faceapi.detectSingleFace(
      video,
      new faceapi.TinyFaceDetectorOptions({
        inputSize: DETECTOR_INPUT_SIZE,
        scoreThreshold: DETECTOR_SCORE_THRESHOLD
      })
    ).withFaceLandmarks(true).withFaceDescriptors();

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!detection) {
      faceCountEl.textContent = "Faces: 0";
      // Idle state — do not touch the message line if a person was just recorded.
      return;
    }

    faceCountEl.textContent = "Faces: 1";

    const box = detection.detection.box;
    const match = identify(detection.descriptor);

    let label = "Unknown";
    let color = "#ef4444";

    if (match) {
      const key = String(match.uid || match.employee?.uid || match.rfid || "");
      const onCooldown = key && isWithinCooldown(key);

      if (onCooldown) {
        // ---- COOLDOWN-AWARE COLOUR ------------------------------------
        // A face that is still inside its 3-minute cooldown is drawn in
        // amber with a "cooldown" hint so the operator immediately sees
        // that this person is being intentionally skipped (so the scanner
        // can move on to the next face).
        const remaining = cooldownRemainingSeconds(key);
        label = `${match.name} ⏳ ${formatCooldownMmSs(remaining)}`;
        color = "#f59e0b"; // amber
      } else {
        label = `${match.name} ${match.confidence.toFixed(1)}%`;
        color = "#22c55e"; // green

        // Record — awaited so only one request is in flight at a time.
        // The cooldown is set INSIDE recordAttendance() only on success.
        await recordAttendance(match);
      }
    }

    // Manual flip: canvas has NO CSS mirror, video DOES.
    const flippedX = canvas.width - box.x - box.width;

    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.strokeRect(flippedX, box.y, box.width, box.height);

    ctx.fillStyle = color;
    const labelWidth = Math.min(canvas.width - flippedX, 300);
    const labelY = Math.max(0, box.y - 28);
    ctx.fillRect(flippedX, labelY, labelWidth, 28);

    ctx.fillStyle = "#fff";
    ctx.font = "bold 14px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(label.trim(), flippedX + 6, Math.max(18, box.y - 9));

  } catch (e) {
    console.error("Recognition loop error:", e);
  } finally {
    busy = false;
  }
}

// ========================================================================
// UI HELPERS
// ========================================================================

function addLogEntry(name, confidence, type, message) {
  const time = new Date().toLocaleTimeString();
  const entry = document.createElement("div");
  entry.className = `log-entry log-${type}`;

  const icons = {
    success: "✅",
    warning: "⚠️",
    error: "❌",
    info: "ℹ️"
  };

  // Only show a confidence value when we actually have one (> 0). This
  // keeps the "Camera started" / "Camera stopped" info lines clean.
  const confidenceText = (typeof confidence === "number" && confidence > 0)
    ? `${confidence.toFixed(1)}%`
    : "---";

  entry.innerHTML = `
    <span class="log-time">${time}</span>
    <span class="log-icon">${icons[type] || "ℹ️"}</span>
    <span class="log-name">${escapeHtml(name)}</span>
    <span class="log-confidence">${confidenceText}</span>
    <span class="log-message">${escapeHtml(message)}</span>
  `;

  logEntries.insertBefore(entry, logEntries.firstChild);

  while (logEntries.children.length > 50) {
    logEntries.removeChild(logEntries.lastChild);
  }

  // Remove the "Waiting for face detection…" placeholder as soon as the
  // first real log entry appears.
  clearLogPlaceholder();
}

function renderAttendance(records) {
  const container = document.getElementById("todayAttendance");

  if (!records || records.length === 0) {
    container.innerHTML = `<span class="help">📭 No face scans recorded today.</span>`;
    return;
  }

  container.innerHTML = records.map(r => {
    // Build a compact time display from whichever slots are filled.
    // AM pair first (in → out) when present, then PM pair (in → out).
    // We only show "in" times normally, but if the pair is complete we
    // also show the "out" side so a half-day can be read at a glance.
    const timeParts = [];
    if (r.am_in && r.am_out) {
      timeParts.push(`AM: ${r.am_in} → ${r.am_out}`);
    } else if (r.am_in) {
      timeParts.push(`AM: ${r.am_in}`);
    }
    if (r.pm_in && r.pm_out) {
      timeParts.push(`PM: ${r.pm_in} → ${r.pm_out}`);
    } else if (r.pm_in) {
      timeParts.push(`PM: ${r.pm_in}`);
    }
    const timeText = timeParts.join("  ");

    const statusText = r.status === "on_leave" ? "🔵 On Leave" : "🟢 Present";
    const statusClass = r.status === "on_leave" ? "on-leave" : "";

    return `
      <div class="attendance-item">
        <div class="attendance-name"><strong>${escapeHtml(r.employee || "Unknown")}</strong></div>
        <div class="attendance-details">
          <span>${escapeHtml(r.employeeid || "")}</span>
          <span>${escapeHtml(timeText)}</span>
          <span class="attendance-status ${statusClass}">${statusText}</span>
        </div>
      </div>
    `;
  }).join("");
}

function escapeHtml(v) {
  return String(v).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[c]));
}

// ========================================================================
// EVENT LISTENERS
// ========================================================================

document.getElementById("start").addEventListener("click", startCamera);
document.getElementById("stop").addEventListener("click", stopCamera);
document.getElementById("refreshBtn").addEventListener("click", async () => {
  messageEl.textContent = "🔄 Refreshing data…";
  await loadTemplates().catch(e => console.error("refresh loadTemplates:", e));
  await loadAttendance().catch(e => console.error("refresh loadAttendance:", e));
  await loadStats().catch(e => console.error("refresh loadStats:", e));
  messageEl.textContent = "🔄 Data refreshed!";
});

window.addEventListener("beforeunload", stopCamera);

// ========================================================================
// START
// ========================================================================

boot();