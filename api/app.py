## Imports
import os
import json
import hashlib
import calendar
import jwt
import secrets
import io
import re
import socket
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, session, send_from_directory, send_file, make_response
from flask_cors import CORS
from werkzeug.utils import secure_filename

# Try to import PIL for image processing
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("Warning: Pillow not installed. Image compression will be disabled.")

# Try to import reportlab, fallback if not installed
try:
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch, cm
    from reportlab.pdfgen import canvas
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    print("Warning: reportlab not installed. PDF generation will be disabled.")

# Try to import requests for version checking
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("Warning: requests not installed. Version checking will be disabled.")

# Try to import threading + time for the nightly wipe scheduler.
# These are used to wake up at 12:00 AM and clear the feed files automatically,
# regardless of whether any HTTP request has been made.
try:
    import threading
    import time
    THREADING_AVAILABLE = True
except ImportError:
    THREADING_AVAILABLE = False
    print("Warning: threading not available. Nightly wipe scheduler will be disabled.")

## Variables ------------------------------------
# Create the Flask application.
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "tapin-development-secret-key")
app.permanent_session_lifetime = timedelta(minutes=30)

# ============================================================================
# ENVIRONMENT DETECTION
# ============================================================================

def is_production():
    """Detect if running in production environment."""
    return os.environ.get("ENVIRONMENT", "").lower() in ["production", "prod"] or \
           os.environ.get("RAILWAY_ENVIRONMENT", "").lower() == "production" or \
           os.environ.get("RENDER", "").lower() == "true"

def get_base_url():
    """Get the base URL for the application."""
    if is_production():
        return os.environ.get("BASE_URL", "https://lolenseu.pythonanywhere.com")
    else:
        return "http://localhost:5000"

# ============================================================================
# PORT UTILITY - Find a free port if the default is in use
# ============================================================================

def find_free_port(start_port=5000, max_attempts=20):
    """
    Find a free port starting from start_port.
    Tries up to max_attempts consecutive ports.
    Returns the first free port found, or None if none are available.
    """
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                return port
        except OSError:
            continue
    return None

def is_port_in_use(port):
    """Check if a specific port is currently in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True

# ============================================================================
# CORS Configuration - Works for Both Local and Production
# ============================================================================

# Allowed origins for both local and production
ALLOWED_ORIGINS = [
    # Production
    "https://tapin-2s5w.onrender.com",
    "https://tapin-api.up.railway.app",
    # Face scanner frontend on Vercel — the actual deployed URL
    "https://tapin-ispsctagudin.vercel.app",
    # Local development
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    # Allow any localhost port for development
    "http://localhost:*",
    "http://127.0.0.1:*",
]

# Extra allowed origins from an env var (comma-separated), e.g. set
# FACE_SCANNER_ORIGINS=https://your-project.vercel.app,https://your-custom-domain.com
# on the API host so you don't have to edit this file every time the Vercel
# preview URL changes.
_extra_origins = os.environ.get("FACE_SCANNER_ORIGINS", "")
if _extra_origins.strip():
    ALLOWED_ORIGINS.extend([o.strip() for o in _extra_origins.split(",") if o.strip()])

# For development, allow all origins
if not is_production():
    ALLOWED_ORIGINS.append("*")

CORS(app,
     origins=ALLOWED_ORIGINS,
     supports_credentials=True,
     allow_headers=["Content-Type", "Authorization", "Cookie", "Set-Cookie", "X-Requested-With"],
     expose_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])

# ============================================================================
# Session Configuration - Adapts to Environment
# ============================================================================

app.config.update(
    SESSION_COOKIE_SAMESITE='Lax' if not is_production() else 'None',
    SESSION_COOKIE_SECURE=is_production(),  # True in production (HTTPS), False in dev (HTTP)
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_PATH='/',
    SESSION_COOKIE_DOMAIN=None,
    SESSION_COOKIE_NAME='tapin_session',
    SESSION_TYPE='filesystem'
)

# ============================================================================
# WEB FACIAL RECOGNITION INTEGRATION - FACE SCANNER
# ============================================================================
# The face scanner frontend (index.html/app.js/styles.css) is deployed
# standalone on Vercel and does its own face detection/matching in the
# browser with face-api.js. This server only exposes the /api/faces/*
# routes it calls (defined further down, near the other storage routes) —
# there is no separate facialrecognition module/file anymore.

# JWT Configuration
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_urlsafe(32))
JWT_EXPIRATION = timedelta(hours=3)

# FIXED: Get the base directory more reliably
# Try multiple ways to find the storage folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# If app.py is in the root folder (not in app/ subfolder), adjust
if not os.path.exists(os.path.join(BASE_DIR, "storage")):
    # Try using the current working directory
    BASE_DIR = os.getcwd()

# If still not found, try the directory where app.py is located
if not os.path.exists(os.path.join(BASE_DIR, "storage")):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Store user records in the storage/database folder (outside app folder)
USER_DATA_FILE = os.path.join(BASE_DIR, "storage", "database", "users.json")

# Store every received RFID scan and timestamp.
ATTENDANCE_DATA_FILE = os.path.join(BASE_DIR, "storage", "database", "attendance.json")

# Persistent work status database (mirror of the feed, kept forever).
# Structure: {"requests": [...], "approved": [...], "rejected": [...]}
# This is the NEW work-status.json — it lives alongside users.json and is NEVER wiped.
WORK_STATUS_DATA_FILE = os.path.join(BASE_DIR, "storage", "database", "work-status.json")

# ============================================================================
# DAILY STATS LOG FILES - PERSISTENT DAILY COUNTERS
# ============================================================================
# These files track the DAILY totals for present employees, RFID scans, and
# facial-recognition scans. They are written to on EVERY RFID tap and every
# face match, regardless of whether any admin dashboard is open. They are
# reset automatically at midnight by the scheduler, and also lazily on the
# first request after midnight.
#
# Structure of daily_stats.json:
#     {
#         "date": "2026-09-28",              # the calendar day these counts are for
#         "present_uids": ["021", "045"],    # unique UIDs of employees who tapped in
#         "present_count": 2,                # len(present_uids)
#         "scan_count": 15,                  # total scans today (RFID + face)
#         "rfid_scan_count": 12,             # RFID taps only
#         "face_scan_count": 3,              # facial-recognition matches only
#         "last_updated": "..."              # ISO timestamp of last write
#     }
#
# This file lives in storage/database/ so it is NOT wiped by the nightly feed
# wipe (which only touches storage/feed/). It manages its own daily reset.
DAILY_STATS_FILE = os.path.join(BASE_DIR, "storage", "database", "daily_stats.json")

# Profile images storage
PROFILE_STORAGE = os.path.join(BASE_DIR, "storage", "profiles")

# Scan feed storage - keeps detailed logs of all scans
SCAN_FEED_FILE = os.path.join(BASE_DIR, "storage", "feed", "scan_feed.json")

# Scan events storage - keeps raw scan events (moved to feed)
SCAN_EVENTS_FILE = os.path.join(BASE_DIR, "storage", "feed", "scan_events.json")

# Work status feed storage — this is the OLD work-status.json, renamed.
# Structure matches the persistent file: {"requests": [...], "approved": [...], "rejected": [...]}
# It lives in storage/feed/ and IS wiped nightly at 12:00 AM.
WORK_STATUS_FEED_FILE = os.path.join(BASE_DIR, "storage", "feed", "work-status_feed.json")

# Work status request storage - stores uploaded files for work status requests
WORK_STATUS_REQUEST_STORAGE = os.path.join(BASE_DIR, "storage", "work-status")

# Activity feed storage - keeps all system activities for timeline
ACTIVITY_FEED_FILE = os.path.join(BASE_DIR, "storage", "feed", "activity_feed.json")

# Settings storage
SETTINGS_FILE = os.path.join(BASE_DIR, "storage", "config", "settings.json")

# ============================================================================
# NOTIFICATION STORAGE - PER-RFID FILES
# ============================================================================
# Every user (admin, hr, employee) gets their own notification file keyed by
# their RFID. Path: storage/notification/<RFID>.json
#
# Example: RFID FB822A54 -> storage/notification/FB822A54.json
#
# Right before we overwrite any notification file, the current content is
# copied to <RFID>.json.backup in the same folder — same convention as
# users.json.backup and attendance.json.backup.
NOTIFICATION_STORAGE = os.path.join(BASE_DIR, "storage", "notification")

# EMPLOYEE SETTINGS STORAGE - PER-RFID FILES
# Every employee gets their own settings file keyed by their RFID.
# Path: storage/settings/<RFID>.json
EMPLOYEE_SETTINGS_STORAGE = os.path.join(BASE_DIR, "storage", "settings")

# ============================================================================
# WORK STATUS TYPES - Predefined choices for work status requests
# ============================================================================
# These are the available work status types that employees can choose from
# when submitting a work status request. Each type has a code, label, and
# whether it requires a specific time range or covers the whole day.

WORK_STATUS_TYPES = {
    "vacation_leave": {
        "code": "vacation_leave",
        "label": "Vacation Leave (Sec. 51, Rule XVI, Omnibus Rules Implementing E.O. No. 292)",
        "description": "Vacation Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "mandatory_forced_leave": {
        "code": "mandatory_forced_leave",
        "label": "Mandatory/Forced Leave (Sec. 25, Rule XVI, Omnibus Rules Implementing E.O. No. 292)",
        "description": "Mandatory/Forced Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "sick_leave": {
        "code": "sick_leave",
        "label": "Sick Leave (Sec. 43, Rule XVI, Omnibus Rules Implementing E.O. No. 292)",
        "description": "Sick Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "maternity_leave": {
        "code": "maternity_leave",
        "label": "Maternity Leave (R.A. No. 11210 / IRR issued by CSC, DOLE and SSS)",
        "description": "Maternity Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "paternity_leave": {
        "code": "paternity_leave",
        "label": "Paternity Leave (R.A. No. 8187 / CSC MC No. 71, s. 1998, as amended)",
        "description": "Paternity Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "special_privilege_leave": {
        "code": "special_privilege_leave",
        "label": "Special Privilege Leave (Sec. 21, Rule XVI, Omnibus Rules Implementing E.O. No. 292)",
        "description": "Special Privilege Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "solo_parent_leave": {
        "code": "solo_parent_leave",
        "label": "Solo Parent Leave (RA No. 8972 / CSC MC No. 8, s. 2004)",
        "description": "Solo Parent Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "study_leave": {
        "code": "study_leave",
        "label": "Study Leave (Sec. 68, Rule XVI, Omnibus Rules Implementing E.O. No. 292)",
        "description": "Study Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "10_day_vawc_leave": {
        "code": "10_day_vawc_leave",
        "label": "10-Day VAWC Leave (RA No. 9262 / CSC MC No. 15, s. 2005)",
        "description": "10-Day VAWC Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "rehabilitation_privilege": {
        "code": "rehabilitation_privilege",
        "label": "Rehabilitation Privilege (Sec. 55, Rule XVI, Omnibus Rules Implementing E.O. No. 292)",
        "description": "Rehabilitation Privilege",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "special_leave_benefits_women": {
        "code": "special_leave_benefits_women",
        "label": "Special Leave Benefits for Women (Sec. 55, Rule XVI, Omnibus Rules Implementing E.O. No. 292)",
        "description": "Special Leave Benefits for Women",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "special_emergency_calamity_leave": {
        "code": "special_emergency_calamity_leave",
        "label": "Special Emergency (Calamity) Leave (CSC MC No. 2, s. 2012, as amended)",
        "description": "Special Emergency (Calamity) Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "adoption_leave": {
        "code": "adoption_leave",
        "label": "Adoption Leave (R.A. No. 8552)",
        "description": "Adoption Leave",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
        "on_leave": {
        "code": "on_leave",
        "label": "On Leave",
        "description": "Approved vacation, sick, emergency, etc.",
        "requires_time": False,
        "default_time_mode": "whole_day"
    },
    "official_travel": {
        "code": "official_travel",
        "label": "Official Travel",
        "description": "Traveling for work",
        "requires_time": True,
        "default_time_mode": "whole_day"
    },
    "official_business": {
        "code": "official_business",
        "label": "Official Business",
        "description": "Working outside the regular workplace",
        "requires_time": True,
        "default_time_mode": "whole_day"
    },
    "work_from_home": {
        "code": "work_from_home",
        "label": "Work From Home (WFH)",
        "description": "Working remotely",
        "requires_time": True,
        "default_time_mode": "whole_day"
    },
    "others": {
        "code": "others",
        "label": "Others:",
        "description": "Other leave/status not listed above",
        "requires_time": False,
        "default_time_mode": "whole_day"
    }
}

# ============================================================================
# TIME PERIODS FOR SPECIFIC TIME WORK STATUS
# ============================================================================
# When a work status request is approved for specific time only, the
# affected period(s) are stored. The DTR will show yellow highlighting
# only for the affected period(s), not the whole day.
#
# Periods:
#   - "am"       : Morning (AM time in/out)
#   - "pm"       : Afternoon (PM time in/out)
#   - "whole_day": Entire day (both AM and PM)
#
# For specific time, the exact time range can also be stored:
#   - start_time: "08:00"
#   - end_time:   "12:00"
#   - period:     "am" | "pm" | "whole_day"

WORK_STATUS_PERIODS = {
    "am": "Morning (AM)",
    "pm": "Afternoon (PM)",
    "whole_day": "Whole Day"
}

# Ensure directories exist
os.makedirs(os.path.dirname(USER_DATA_FILE), exist_ok=True)
os.makedirs(os.path.dirname(ATTENDANCE_DATA_FILE), exist_ok=True)
os.makedirs(os.path.dirname(WORK_STATUS_DATA_FILE), exist_ok=True)
os.makedirs(os.path.dirname(DAILY_STATS_FILE), exist_ok=True)
os.makedirs(os.path.dirname(PROFILE_STORAGE), exist_ok=True)
os.makedirs(os.path.dirname(SCAN_FEED_FILE), exist_ok=True)
os.makedirs(os.path.dirname(SCAN_EVENTS_FILE), exist_ok=True)
os.makedirs(os.path.dirname(WORK_STATUS_FEED_FILE), exist_ok=True)
os.makedirs(os.path.dirname(ACTIVITY_FEED_FILE), exist_ok=True)
os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
os.makedirs(NOTIFICATION_STORAGE, exist_ok=True)
os.makedirs(EMPLOYEE_SETTINGS_STORAGE, exist_ok=True)
os.makedirs(WORK_STATUS_REQUEST_STORAGE, exist_ok=True)

# Debug: Print paths to verify
print(f"BASE_DIR: {BASE_DIR}")
print(f"USER_DATA_FILE: {USER_DATA_FILE}")
print(f"ATTENDANCE_DATA_FILE: {ATTENDANCE_DATA_FILE}")
print(f"WORK_STATUS_DATA_FILE: {WORK_STATUS_DATA_FILE}")
print(f"DAILY_STATS_FILE: {DAILY_STATS_FILE}")
print(f"PROFILE_STORAGE: {PROFILE_STORAGE}")
print(f"SCAN_FEED_FILE: {SCAN_FEED_FILE}")
print(f"SCAN_EVENTS_FILE: {SCAN_EVENTS_FILE}")
print(f"WORK_STATUS_FEED_FILE: {WORK_STATUS_FEED_FILE}")
print(f"ACTIVITY_FEED_FILE: {ACTIVITY_FEED_FILE}")
print(f"SETTINGS_FILE: {SETTINGS_FILE}")
print(f"NOTIFICATION_STORAGE: {NOTIFICATION_STORAGE}")
print(f"WORK_STATUS_REQUEST_STORAGE: {WORK_STATUS_REQUEST_STORAGE}")
print(f"REPORTLAB_AVAILABLE: {REPORTLAB_AVAILABLE}")
print(f"REQUESTS_AVAILABLE: {REQUESTS_AVAILABLE}")
print(f"PIL_AVAILABLE: {PIL_AVAILABLE}")
print(f"Environment: {'Production' if is_production() else 'Development'}")
print(f"Base URL: {get_base_url()}")

# Default settings
DEFAULT_SETTINGS = {
    "attendance": {
        "work_start": "08:00",
        "work_end": "17:00",
        "lunch_start": "12:00",
        "lunch_end": "13:00",
        "grace_period": 10
    },
    "institution": {
        "name": "ISPSC Tagudin Campus",
        "system_name": "TAPIN",
        "academic_year": "2025-2026",
        "hr_email": "hr@ispsc.edu.ph"
    },
    "system": {
        "version": "1.0.0",
        "version_url": "https://raw.githubusercontent.com/lolenseu/tapin-rfid-attendance-system/refs/heads/main/version.txt"
    }
}

# Open the shared dashboard after a successful login.
WEB_DASHBOARD = "/pages/dashboard.html"
EMPLOYEE_DASHBOARD = "/pages/employee-dashboard.html"

# Choose the frontend destination from the role stored in users.json.
ROLE_DASHBOARDS = {
    "admin": WEB_DASHBOARD,
    "hr": WEB_DASHBOARD,
    "employee": EMPLOYEE_DASHBOARD,
}

# Assign separate UID ranges to each user role.
ROLE_UID_RANGES = {"admin": (1, 9), "hr": (10, 19), "employee": (20, float("inf"))}

# Remove devices that have not sent a heartbeat within this period.
DEVICE_TIMEOUT_SECONDS = 90

# Track the latest status reported by each RFID device.
device_status = {}

# Store the latest employee record used by the application.
latest_employee = {
    "uid": None,
    "rfid": None,
    "employeeid": None,
    "lastname": None,
    "firstname": None,
    "address": None,
    "bdate": None,
    "cpnumber": None,
    "email": None,
    "username": None,
    "role": None,
    "department": None,
    "position": None,
    "image": None,
    "timestamp_creation": None,
    "timestamp_modified": None
}

# Store the latest RFID scan received from a device.
latest_scan = {
    "rfid": None,
    "scanned_at": None
}

# Track last scan time for each RFID to enforce cooldown
# Structure: {rfid: {"last_scan_time": datetime, "last_scan_type": "in"|"out"}}
last_scan_tracking = {}
# Cooldown between scans for the same RFID (in seconds). Default 3 minutes.
SCAN_COOLDOWN_SECONDS = 3 * 60

# ============================================================================
# DAILY STATS HELPERS - PERSISTENT DAILY COUNTERS
# ============================================================================
# These functions manage the daily_stats.json file which tracks:
#   - present_count     : number of UNIQUE employees who tapped in today
#   - scan_count        : total scans today (RFID + face combined)
#   - rfid_scan_count   : RFID taps only
#   - face_scan_count   : facial-recognition matches only
#
# The file is written to on EVERY RFID tap and every face match so the
# counts are always accurate, regardless of whether any dashboard is open.
# The dashboard merely READS these values via /api/dashboard-data.
#
# The file resets automatically at midnight:
#   - The scheduler calls reset_daily_stats_if_needed() on the new day.
#   - Every read/write also checks the date and resets if needed.
# ============================================================================

def _empty_daily_stats(date_str=None):
    """Build a fresh empty daily stats document for a given date."""
    if date_str is None:
        date_str = datetime.now().date().isoformat()
    return {
        "date": date_str,
        "present_uids": [],
        "present_count": 0,
        "scan_count": 0,
        "rfid_scan_count": 0,
        "face_scan_count": 0,
        "last_updated": datetime.now().isoformat()
    }

def load_daily_stats():
    """Load the daily stats file, resetting it if the date has changed.

    Returns a dict with keys: date, present_uids, present_count, scan_count,
    rfid_scan_count, face_scan_count, last_updated.
    """
    today = datetime.now().date().isoformat()

    if not os.path.exists(DAILY_STATS_FILE):
        stats = _empty_daily_stats(today)
        _write_daily_stats(stats)
        return stats

    try:
        with open(DAILY_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading daily stats file: {e}")
        stats = _empty_daily_stats(today)
        _write_daily_stats(stats)
        return stats

    # If the file is from a previous day, reset it.
    if not isinstance(data, dict) or data.get("date") != today:
        print(f"Daily stats date changed from {data.get('date') if isinstance(data, dict) else '?'} to {today} — resetting counters.")
        stats = _empty_daily_stats(today)
        _write_daily_stats(stats)
        return stats

    # Normalize structure
    if "present_uids" not in data or not isinstance(data["present_uids"], list):
        data["present_uids"] = []
    if "present_count" not in data:
        data["present_count"] = len(data["present_uids"])
    if "scan_count" not in data:
        data["scan_count"] = 0
    if "rfid_scan_count" not in data:
        # Legacy files had only "scan_count" — treat all of it as RFID.
        data["rfid_scan_count"] = int(data.get("scan_count", 0))
    if "face_scan_count" not in data:
        data["face_scan_count"] = 0
    data["present_count"] = len(data["present_uids"])
    if "last_updated" not in data:
        data["last_updated"] = datetime.now().isoformat()
    return data

def _write_daily_stats(stats):
    """Internal: write the daily stats dict to disk."""
    try:
        os.makedirs(os.path.dirname(DAILY_STATS_FILE), exist_ok=True)
        stats["last_updated"] = datetime.now().isoformat()
        with open(DAILY_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=4)
            f.write("\n")
    except Exception as e:
        print(f"Error writing daily stats file: {e}")

def save_daily_stats(stats):
    """Public: persist the daily stats dict (auto-resets if date changed)."""
    today = datetime.now().date().isoformat()
    if not isinstance(stats, dict):
        stats = _empty_daily_stats(today)
    if stats.get("date") != today:
        stats = _empty_daily_stats(today)
    stats["present_count"] = len(stats.get("present_uids", []))
    _write_daily_stats(stats)

def record_daily_scan(uid=None, source="rfid"):
    """Record one scan for today and, if uid is provided, one present employee.

    This is called on EVERY RFID tap and every face match so the daily
    counters are always up to date regardless of whether a dashboard is open.

    Args:
        uid:    The employee's uid if the scan matched a known employee.
                If None, only the scan counters are incremented.
        source: "rfid" or "face" — which sub-counter to increment.
                Defaults to "rfid" so existing callers keep working.

    Both the combined scan_count AND the source-specific counter
    (rfid_scan_count or face_scan_count) are incremented.
    """
    stats = load_daily_stats()

    source_norm = str(source or "rfid").strip().lower()
    if source_norm not in ("rfid", "face"):
        source_norm = "rfid"

    # Increment total scan count (combined)
    stats["scan_count"] = int(stats.get("scan_count", 0)) + 1

    # Increment the source-specific counter
    if source_norm == "face":
        stats["face_scan_count"] = int(stats.get("face_scan_count", 0)) + 1
    else:
        stats["rfid_scan_count"] = int(stats.get("rfid_scan_count", 0)) + 1

    # Increment unique present count if this is a known employee
    if uid:
        uid_str = str(uid).strip()
        if uid_str and uid_str not in stats.get("present_uids", []):
            stats.setdefault("present_uids", []).append(uid_str)

    stats["present_count"] = len(stats.get("present_uids", []))
    save_daily_stats(stats)
    return stats

def reset_daily_stats_if_needed():
    """Force-reset the daily stats file if the date has changed.

    Called by the midnight scheduler and also lazily on every dashboard
    request (via load_daily_stats, which auto-resets).
    """
    return load_daily_stats()

# ============================================================================
# NIGHTLY FEED WIPE (runs automatically at 12:00 AM local time)
# ============================================================================
# The four feed files listed below are cleared at midnight every day:
#
#     storage/feed/scan_feed.json        <- recent scan entries
#     storage/feed/scan_events.json      <- raw scan events
#     storage/feed/activity_feed.json    <- system activity timeline
#     storage/feed/work-status_feed.json <- work status feed (mirror of work-status.json)
#
# A background thread wakes up every 30 seconds and, when it detects that the
# local clock has just crossed midnight, calls perform_nightly_feed_wipe()
# exactly once for that calendar day. The wipe is also triggered lazily on the
# first HTTP request after midnight — so even if the background thread is not
# available (e.g. threading disabled), the wipe still happens the moment
# someone hits the API.
#
# Files NOT wiped (persistent, in storage/database/):
#     - users.json           (employee records)
#     - attendance.json      (DTR records)
#     - work-status.json     (NEW mirror of the feed — kept forever)
#     - daily_stats.json     (daily present/scan counters — manages its own reset)
#     - settings.json        (config)
_last_feed_wipe_date = None

def perform_nightly_feed_wipe(force=False):
    """Wipe all four feed files. Only runs when force=True (used by weekly scheduler).

    Files cleared (all under storage/feed/):
        - storage/feed/scan_feed.json          (scans list)
        - storage/feed/scan_events.json        (scan_events list)
        - storage/feed/activity_feed.json      (activities list)
        - storage/feed/work-status_feed.json   (requests + approved + rejected)

    The persistent mirror at storage/database/work-status.json is NEVER touched.
    The persistent daily counters at storage/database/daily_stats.json are NEVER
    touched here — they reset themselves based on the calendar date.

    Also clears in-memory daily state (latest scan + per-RFID cooldown).

    Only runs when force=True is passed (used by the weekly Sunday scheduler).
    Daily wipe has been disabled per user request.
    """
    global _last_feed_wipe_date, latest_scan, last_scan_tracking, scan_events

    # Only run when explicitly forced (weekly scheduler)
    if not force:
        return

    today = datetime.now().date()
    if _last_feed_wipe_date == today:
        # Already wiped today
        return
    _last_feed_wipe_date = today

    now_iso = datetime.now().isoformat()

    # --- 1) scan_feed.json ---------------------------------------------------
    try:
        scan_feed_data = load_scan_feed()
        scan_feed_data["scans"] = []
        scan_feed_data["total_scans"] = 0
        scan_feed_data["last_cleanup"] = now_iso
        save_scan_feed(scan_feed_data)
        print(f"[Nightly] Wiped scan_feed.json")
    except Exception as e:
        print(f"[Nightly] Failed to wipe scan_feed.json: {e}")

    # --- 2) scan_events.json -------------------------------------------------
    try:
        scan_events = []
        save_scan_events({"scan_events": []})
        print(f"[Nightly] Wiped scan_events.json")
    except Exception as e:
        print(f"[Nightly] Failed to wipe scan_events.json: {e}")

    # --- 3) activity_feed.json -----------------------------------------------
    try:
        save_activity_feed({
            "activities": [],
            "total_activities": 0,
            "last_cleanup": now_iso
        })
        print(f"[Nightly] Wiped activity_feed.json")
    except Exception as e:
        print(f"[Nightly] Failed to wipe activity_feed.json: {e}")

    # --- 4) work-status_feed.json (the OLD work-status.json — same shape as the mirror) -
    # NOTE: We ONLY wipe the feed copy here. The mirror at
    # storage/database/work-status.json is intentionally left alone.
    try:
        empty_work_status = {"requests": [], "approved": [], "rejected": []}
        save_work_status_feed_data(empty_work_status)
        # Refresh the in-memory feed copy so the running app sees the empty state
        work_status_feed_data.clear()
        work_status_feed_data.update(empty_work_status)
        print(f"[Nightly] Wiped work-status_feed.json")
    except Exception as e:
        print(f"[Nightly] Failed to wipe work-status_feed.json: {e}")

    # --- 5) In-memory daily state -------------------------------------------
    last_scan_tracking.clear()
    latest_scan["rfid"] = None
    latest_scan["scanned_at"] = None

    # --- 6) Daily stats reset (present/scan counters) -----------------------
    # This is a SEPARATE file that manages its own daily reset, but we also
    # force it here so the counters flip over promptly at midnight.
    try:
        reset_daily_stats_if_needed()
        print(f"[Nightly] Daily stats counters reset for the new day")
    except Exception as e:
        print(f"[Nightly] Failed to reset daily stats: {e}")

    print(f"[Nightly] All feeds wiped for {today.isoformat()} at {now_iso}")

def start_nightly_wipe_scheduler():
    """Start a background thread that wipes all feeds every Sunday evening before Monday.

    The scheduler wakes up every 30 seconds and checks whether the current
    local time is Sunday at 23:30. If it is, and we haven't already
    wiped this week, it calls perform_nightly_feed_wipe(force=True).

    It ALSO checks every 30 seconds whether the calendar date has changed,
    and if so, resets the daily present/scan counters.

    This runs regardless of whether any API route is hit, so the feeds are
    always cleared at Sunday 23:30 local time and the daily counters always
    flip over at midnight.

    Runs as a daemon thread — it shuts down automatically when the app exits.
    """
    if not THREADING_AVAILABLE:
        print("⚠️  Nightly wipe scheduler not started (threading unavailable)")
        return

    # Track which date we last reset the daily stats for, so we only reset once.
    last_daily_reset_date = {"value": datetime.now().date().isoformat()}

    def _scheduler_loop():
        print("[Scheduler] Nightly feed wipe scheduler started (checks every 30s)")
        while True:
            try:
                now = datetime.now()
                # Trigger once per week on Sunday at 23:30
                if now.weekday() == 6 and now.hour == 23 and now.minute == 30:
                    # Only wipe if we haven't already wiped this week
                    if _last_feed_wipe_date != now.date():
                        print(f"[Scheduler] Sunday 23:30 detected at {now.isoformat()} — wiping feeds")
                        perform_nightly_feed_wipe(force=True)

                # --- Daily stats rollover check ------------------------------
                # If the calendar date has changed since the last check, reset
                # the daily present/scan counters so they start fresh.
                current_date = now.date().isoformat()
                if last_daily_reset_date["value"] != current_date:
                    print(f"[Scheduler] Date changed to {current_date} — resetting daily counters")
                    reset_daily_stats_if_needed()
                    last_daily_reset_date["value"] = current_date
            except Exception as e:
                print(f"[Scheduler] Error in scheduler loop: {e}")
            # Sleep 30 seconds before checking again.
            time.sleep(30)

    scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="nightly-wipe")
    scheduler_thread.start()
    print("✅ Nightly wipe scheduler thread launched (weekly on Sunday at 23:30, daily counter reset at midnight)")

## Functions ------------------------------------
# Image compression function
def compress_and_save_image(image_file, rfid, max_size_kb=100, quality=85, max_dimensions=(300, 300)):
    """
    Compress and resize an image to reduce file size.

    Args:
        image_file: The uploaded image file
        rfid: The RFID to use as filename
        max_size_kb: Maximum file size in KB (default: 100KB)
        quality: Initial JPEG quality (1-100)
        max_dimensions: Max width and height (default: 300x300)

    Returns:
        str: The saved file path or None if failed
    """
    if not PIL_AVAILABLE:
        # Fallback: save without compression
        extension = os.path.splitext(image_file.filename)[1].lower()
        rfid_filename = secure_filename(rfid)
        os.makedirs(PROFILE_STORAGE, exist_ok=True)
        filename = rfid_filename + extension
        image_file.save(os.path.join(PROFILE_STORAGE, filename))
        return os.path.join("storage", "profiles", filename).replace(os.sep, "/")

    try:
        # Open the image
        img = Image.open(image_file)

        # Convert to RGB if necessary (for PNG with transparency)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background

        # Resize image maintaining aspect ratio
        img.thumbnail(max_dimensions, Image.Resampling.LANCZOS)

        # Determine file extension
        extension = os.path.splitext(image_file.filename)[1].lower()
        rfid_filename = secure_filename(rfid)
        os.makedirs(PROFILE_STORAGE, exist_ok=True)

        # Try to save as JPEG for better compression
        if extension in ['.jpg', '.jpeg']:
            filename = rfid_filename + '.jpg'
            filepath = os.path.join(PROFILE_STORAGE, filename)

            # Try different quality settings to achieve target size
            current_quality = quality
            while current_quality > 10:
                # Save to buffer to check size
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=current_quality, optimize=True)
                size_kb = len(buffer.getvalue()) / 1024

                if size_kb <= max_size_kb:
                    # Save to file
                    img.save(filepath, format='JPEG', quality=current_quality, optimize=True)
                    print(f"Image compressed to {size_kb:.1f}KB (quality: {current_quality})")
                    return os.path.join("storage", "profiles", filename).replace(os.sep, "/")

                # Reduce quality by 5
                current_quality -= 5

            # If still too large, save with minimum quality
            img.save(filepath, format='JPEG', quality=10, optimize=True)
            return os.path.join("storage", "profiles", filename).replace(os.sep, "/")

        elif extension in ['.png']:
            # For PNG, convert to JPEG for better compression
            filename = rfid_filename + '.jpg'
            filepath = os.path.join(PROFILE_STORAGE, filename)

            # Try different quality settings
            current_quality = quality
            while current_quality > 10:
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=current_quality, optimize=True)
                size_kb = len(buffer.getvalue()) / 1024

                if size_kb <= max_size_kb:
                    img.save(filepath, format='JPEG', quality=current_quality, optimize=True)
                    print(f"PNG compressed to JPEG {size_kb:.1f}KB (quality: {current_quality})")
                    return os.path.join("storage", "profiles", filename).replace(os.sep, "/")

                current_quality -= 5

            img.save(filepath, format='JPEG', quality=10, optimize=True)
            return os.path.join("storage", "profiles", filename).replace(os.sep, "/")

        else:
            # For other formats, try to save as JPEG
            filename = rfid_filename + '.jpg'
            filepath = os.path.join(PROFILE_STORAGE, filename)
            img.save(filepath, format='JPEG', quality=quality, optimize=True)
            return os.path.join("storage", "profiles", filename).replace(os.sep, "/")

    except Exception as e:
        print(f"Error compressing image: {e}")
        # Fallback: save without compression
        try:
            extension = os.path.splitext(image_file.filename)[1].lower()
            rfid_filename = secure_filename(rfid)
            os.makedirs(PROFILE_STORAGE, exist_ok=True)
            filename = rfid_filename + extension
            image_file.save(os.path.join(PROFILE_STORAGE, filename))
            return os.path.join("storage", "profiles", filename).replace(os.sep, "/")
        except:
            return None

# Load settings
def load_settings():
    """Load settings from JSON file"""
    if not os.path.exists(SETTINGS_FILE):
        # Create default settings file
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Merge with defaults to ensure all keys exist
            merged = DEFAULT_SETTINGS.copy()
            for key in merged:
                if key in data:
                    if isinstance(merged[key], dict) and isinstance(data[key], dict):
                        merged[key].update(data[key])
                    else:
                        merged[key] = data[key]
            return merged
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading settings file: {e}")
        return DEFAULT_SETTINGS.copy()

# Save settings
def save_settings(settings_data):
    """Save settings to JSON file"""
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings_data, f, indent=4)
        f.write("\n")
    print(f"Settings saved to {SETTINGS_FILE}")

# Load settings on startup
settings = load_settings()

# Compute the required daily work hours from the current settings.
# This is what UT / OT are measured against — it replaces the old
# hardcoded "8 hours" so the Settings page actually affects the DTR.
def get_required_hours():
    """Compute required daily hours from settings (work_end − work_start − lunch).

    Reads settings["attendance"] for work_start, work_end, lunch_start, and
    lunch_end. Returns the required hours as a float. Falls back to 8.0 if
    anything is missing or malformed so attendance recording never breaks.
    """
    try:
        att = settings.get("attendance", {})
        ws = datetime.strptime(att.get("work_start", "08:00"), "%H:%M")
        we = datetime.strptime(att.get("work_end", "17:00"), "%H:%M")
        ls = datetime.strptime(att.get("lunch_start", "12:00"), "%H:%M")
        le = datetime.strptime(att.get("lunch_end", "13:00"), "%H:%M")
        work_span = (we - ws).total_seconds() / 3600
        lunch_span = (le - ls).total_seconds() / 3600
        required = work_span - lunch_span
        return max(0, required)
    except Exception:
        return 8.0

# Get current version from GitHub
def fetch_version_from_github():
    """Fetch version from GitHub raw URL"""
    if not REQUESTS_AVAILABLE:
        return None
    try:
        url = settings.get("system", {}).get("version_url", "https://raw.githubusercontent.com/lolenseu/tapin-rfid-attendance-system/refs/heads/main/version.txt")
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            version_text = response.text.strip()
            # Parse version from text (e.g., "test - v0.1.55" -> "0.1.55")
            match = re.search(r'v?(\d+\.\d+\.\d+)', version_text)
            if match:
                return match.group(1)
            return version_text
        return None
    except Exception as e:
        print(f"Error fetching version from GitHub: {e}")
        return None

# ============================================================================
# NOTIFICATION STORAGE HELPERS
# ============================================================================
# Every user (admin, hr, employee) has their own notification file keyed by
# their RFID. Path: storage/notification/<RFID>.json
#
# Each file shape:
#     {
#         "rfid": "FB822A54",
#         "uid": "021",
#         "fullname": "JIM-MAR DE LOS REYES",
#         "role": "employee",
#         "notifications": [ { id, type, title, message, read, created_at }, ... ],
#         "unread_count": 0,
#         "total_count": 0,
#         "last_updated": "..."
#     }
#
# Before overwriting any notification file, the previous content is copied to
# <RFID>.json.backup in the SAME folder — exactly like users.json.backup.

def _sanitize_rfid(rfid):
    """Sanitize an RFID so it is safe to use as a filename."""
    if not rfid:
        return ""
    cleaned = re.sub(r'[^A-Za-z0-9_\-]', '', str(rfid).strip().upper())
    return cleaned

def _notification_path(rfid):
    """Return the absolute path to a user's notification file."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return None
    return os.path.join(NOTIFICATION_STORAGE, f"{safe_rfid}.json")

def _notification_backup_path(rfid):
    """Return the absolute path to a user's notification backup file (.json.backup)."""
    path = _notification_path(rfid)
    if not path:
        return None
    return path + ".backup"

def _empty_notification_doc(rfid, uid=None, fullname=None, role=None):
    """Build a fresh empty notification document for a given RFID."""
    return {
        "rfid": _sanitize_rfid(rfid),
        "uid": uid or "",
        "fullname": fullname or "",
        "role": role or "",
        "notifications": [],
        "unread_count": 0,
        "total_count": 0,
        "last_updated": datetime.now().isoformat()
    }

def _backup_notification_file(rfid, existing_doc):
    """Write a .json.backup copy of the current notification document.

    Called right before we overwrite the live file so the previous version
    is preserved as <RFID>.json.backup in the same folder.
    """
    try:
        backup_path = _notification_backup_path(rfid)
        if not backup_path or existing_doc is None:
            return
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(existing_doc, f, indent=4)
            f.write("\n")
        print(f"Notification backup written: {backup_path}")
    except Exception as e:
        print(f"Warning: failed to write notification backup for {rfid}: {e}")

def load_notifications(rfid, uid=None, fullname=None, role=None):
    """Load the notification document for a given RFID.

    If the file does not exist, an empty document is created and returned.
    """
    path = _notification_path(rfid)
    if not path:
        return _empty_notification_doc(rfid, uid, fullname, role)

    if not os.path.exists(path):
        doc = _empty_notification_doc(rfid, uid, fullname, role)
        # Persist the empty doc so the user has a real file going forward.
        try:
            os.makedirs(NOTIFICATION_STORAGE, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=4)
                f.write("\n")
        except Exception as e:
            print(f"Warning: could not create notification file for {rfid}: {e}")
        return doc

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading notification file for {rfid}: {e}")
        return _empty_notification_doc(rfid, uid, fullname, role)

    # Normalize structure
    if not isinstance(data, dict):
        data = _empty_notification_doc(rfid, uid, fullname, role)
    if "notifications" not in data or not isinstance(data["notifications"], list):
        data["notifications"] = []
    data["rfid"] = _sanitize_rfid(rfid)
    if uid is not None:
        data["uid"] = uid
    if fullname is not None:
        data["fullname"] = fullname
    if role is not None:
        data["role"] = role
    # Recompute counts so the file is always consistent
    data["total_count"] = len(data["notifications"])
    data["unread_count"] = sum(1 for n in data["notifications"] if not n.get("read", False))
    if "last_updated" not in data:
        data["last_updated"] = datetime.now().isoformat()
    return data

def save_notifications(rfid, doc, backup=True):
    """Persist a notification document to disk.

    If `backup` is True and the live file already exists, the previous
    content is first written to <RFID>.json.backup in the same folder.
    """
    path = _notification_path(rfid)
    if not path:
        return False

    existing = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = None

    if backup and existing is not None:
        _backup_notification_file(rfid, existing)

    doc["rfid"] = _sanitize_rfid(rfid)
    doc["total_count"] = len(doc.get("notifications", []))
    doc["unread_count"] = sum(1 for n in doc.get("notifications", []) if not n.get("read", False))
    doc["last_updated"] = datetime.now().isoformat()

    try:
        os.makedirs(NOTIFICATION_STORAGE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=4)
            f.write("\n")
        print(f"Notifications saved for {rfid} ({doc['total_count']} entries, {doc['unread_count']} unread)")
        return True
    except Exception as e:
        print(f"Error saving notifications for {rfid}: {e}")
        return False

def push_notification(rfid, title, message, notif_type="system", uid=None, fullname=None, role=None):
    """Append a new notification for the given RFID and save.

    Returns the created notification entry, or None if the RFID is invalid.
    """
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return None

    doc = load_notifications(safe_rfid, uid=uid, fullname=fullname, role=role)

    # Build a request-safe ID based on the current count.
    try:
        next_num = max([int(n.get("id", "0")) for n in doc.get("notifications", [])] + [0]) + 1
    except Exception:
        next_num = len(doc.get("notifications", [])) + 1
    new_id = str(next_num).zfill(3)

    entry = {
        "id": new_id,
        "type": notif_type,
        "title": title,
        "message": message,
        "read": False,
        "created_at": datetime.now().isoformat()
    }

    doc["notifications"].insert(0, entry)
    # Cap at 200 entries per user to keep files small.
    if len(doc["notifications"]) > 200:
        doc["notifications"] = doc["notifications"][:200]

    save_notifications(safe_rfid, doc)
    return entry

def clear_notifications(rfid):
    """Clear all notifications for a given RFID (keeps the file itself)."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return False

    doc = load_notifications(safe_rfid)
    doc["notifications"] = []
    doc["unread_count"] = 0
    doc["total_count"] = 0
    save_notifications(safe_rfid, doc)
    print(f"Cleared all notifications for {safe_rfid}")
    return True

def mark_notification_read(rfid, notification_id):
    """Mark a single notification as read."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return False

    doc = load_notifications(safe_rfid)
    found = False
    for n in doc.get("notifications", []):
        if str(n.get("id")) == str(notification_id):
            n["read"] = True
            found = True
            break

    if not found:
        return False

    save_notifications(safe_rfid, doc)
    return True

def mark_all_notifications_read(rfid):
    """Mark every notification for a given RFID as read."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return False

    doc = load_notifications(safe_rfid)
    for n in doc.get("notifications", []):
        n["read"] = True
    save_notifications(safe_rfid, doc)
    return True

def delete_notification(rfid, notification_id):
    """Delete a single notification entry."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return False

    doc = load_notifications(safe_rfid)
    before = len(doc.get("notifications", []))
    doc["notifications"] = [
        n for n in doc.get("notifications", [])
        if str(n.get("id")) != str(notification_id)
    ]
    after = len(doc["notifications"])

    if before == after:
        return False

    save_notifications(safe_rfid, doc)
    return True

# ============================================================================
# EMPLOYEE SETTINGS STORAGE - PER-RFID FILES
# ============================================================================
# Every employee gets their own settings file keyed by their RFID.
# Path: storage/settings/<RFID>.json
#
# Each file shape:
#     {
#         "rfid": "FB822A54",
#         "uid": "021",
#         "fullname": "JIM-MAR DE LOS REYES",
#         "role": "employee",
#         "settings": { ... },  // Employee-specific settings override
#         "last_updated": "..."
#     }
#
# Before overwriting any settings file, the previous content is copied to
# <RFID>.json.backup in the SAME folder — exactly like users.json.backup.

def _employee_settings_path(rfid):
    """Return the absolute path to an employee's settings file."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return None
    return os.path.join(EMPLOYEE_SETTINGS_STORAGE, f"{safe_rfid}.json")

def _employee_settings_backup_path(rfid):
    """Return the absolute path to an employee's settings backup file (.json.backup)."""
    path = _employee_settings_path(rfid)
    if not path:
        return None
    return path + ".backup"

def _empty_employee_settings_doc(rfid, uid=None, fullname=None, role=None):
    """Build a fresh empty employee settings document for a given RFID."""
    return {
        "rfid": _sanitize_rfid(rfid),
        "uid": uid or "",
        "fullname": fullname or "",
        "role": role or "",
        "settings": {},  # Empty settings object - will inherit from system settings
        "last_updated": datetime.now().isoformat()
    }

def _backup_employee_settings_file(rfid, existing_doc):
    """Write a .json.backup copy of the current employee settings document.

    Called right before we overwrite the live file so the previous version
    is preserved as <RFID>.json.backup in the same folder.
    """
    try:
        backup_path = _employee_settings_backup_path(rfid)
        if not backup_path or existing_doc is None:
            return
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(existing_doc, f, indent=4)
            f.write("\n")
        print(f"Employee settings backup written: {backup_path}")
    except Exception as e:
        print(f"Warning: failed to write employee settings backup for {rfid}: {e}")

def load_employee_settings(rfid, uid=None, fullname=None, role=None):
    """Load the settings document for a given employee RFID.

    If the file does not exist, an empty document is created and returned.
    """
    path = _employee_settings_path(rfid)
    if not path:
        return _empty_employee_settings_doc(rfid, uid, fullname, role)

    if not os.path.exists(path):
        doc = _empty_employee_settings_doc(rfid, uid, fullname, role)
        # Persist the empty doc so the employee has a real file going forward.
        try:
            os.makedirs(EMPLOYEE_SETTINGS_STORAGE, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=4)
                f.write("\n")
        except Exception as e:
            print(f"Warning: could not create employee settings file for {rfid}: {e}")
        return doc

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading employee settings file for {rfid}: {e}")
        return _empty_employee_settings_doc(rfid, uid, fullname, role)

    # Normalize structure
    if not isinstance(data, dict):
        data = _empty_employee_settings_doc(rfid, uid, fullname, role)
    if "settings" not in data or not isinstance(data["settings"], dict):
        data["settings"] = {}
    data["rfid"] = _sanitize_rfid(rfid)
    if uid is not None:
        data["uid"] = uid
    if fullname is not None:
        data["fullname"] = fullname
    if role is not None:
        data["role"] = role
    if "last_updated" not in data:
        data["last_updated"] = datetime.now().isoformat()
    return data

def save_employee_settings(rfid, doc, backup=True):
    """Persist an employee settings document to disk.

    If `backup` is True and the live file already exists, the previous
    content is first written to <RFID>.json.backup in the same folder.
    """
    path = _employee_settings_path(rfid)
    if not path:
        return False

    existing = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = None

    if backup and existing is not None:
        _backup_employee_settings_file(rfid, existing)

    doc["rfid"] = _sanitize_rfid(rfid)
    doc["last_updated"] = datetime.now().isoformat()

    try:
        os.makedirs(EMPLOYEE_SETTINGS_STORAGE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=4)
            f.write("\n")
        print(f"Employee settings saved for {rfid}")
        return True
    except Exception as e:
        print(f"Error saving employee settings for {rfid}: {e}")
        return False

def update_employee_setting(rfid, setting_key, setting_value, uid=None, fullname=None, role=None):
    """Update a specific setting for an employee."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return False

    doc = load_employee_settings(safe_rfid, uid=uid, fullname=fullname, role=role)

    # Update the specific setting
    if "settings" not in doc:
        doc["settings"] = {}
    doc["settings"][setting_key] = setting_value

    return save_employee_settings(safe_rfid, doc)

def get_employee_setting(rfid, setting_key, default=None):
    """Get a specific setting for an employee, falling back to system settings."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return default

    doc = load_employee_settings(safe_rfid)

    # First check employee-specific settings
    if "settings" in doc and isinstance(doc["settings"], dict) and setting_key in doc["settings"]:
        return doc["settings"][setting_key]

    # Fall back to system settings
    # Handle nested settings like "attendance.work_start"
    if "." in setting_key:
        keys = setting_key.split(".")
        value = settings
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
    else:
        return settings.get(setting_key, default)

# ============================================================================
# WORK STATUS STORAGE (TWO FILES, SAME SHAPE)
# ============================================================================
# Both files use the SAME structure:
#     {"requests": [...], "approved": [...], "rejected": [...]}
#
# storage/feed/work-status_feed.json      <- OLD work-status.json, wiped nightly
# storage/database/work-status.json       <- NEW mirror, never wiped
#
# Every write goes to BOTH files so they always match. Only the feed copy
# is emptied at 12:00 AM by perform_nightly_feed_wipe().

def _empty_work_status():
    """Return a fresh empty work status structure."""
    return {"requests": [], "approved": [], "rejected": []}

def _normalize_work_status_payload(data):
    """Ensure the three top-level keys exist and are lists."""
    if not isinstance(data, dict):
        return _empty_work_status()
    if "requests" not in data or not isinstance(data["requests"], list):
        data["requests"] = []
    if "approved" not in data or not isinstance(data["approved"], list):
        data["approved"] = []
    if "rejected" not in data or not isinstance(data["rejected"], list):
        data["rejected"] = []
    return data

def load_work_status_data():
    """Load the persistent work status database from storage/database/work-status.json.

    Structure matches the feed: {requests, approved, rejected}.
    """
    if not os.path.exists(WORK_STATUS_DATA_FILE):
        default_data = _empty_work_status()
        with open(WORK_STATUS_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
            f.write("\n")
        print(f"Created new persistent work status file: {WORK_STATUS_DATA_FILE}")
        return default_data

    try:
        with open(WORK_STATUS_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return _normalize_work_status_payload(data)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading persistent work status file: {e}")
        return _empty_work_status()

def save_work_status_data(work_status_data):
    """Save the persistent work status database to storage/database/work-status.json."""
    os.makedirs(os.path.dirname(WORK_STATUS_DATA_FILE), exist_ok=True)
    with open(WORK_STATUS_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(work_status_data, f, indent=4)
        f.write("\n")
    print(f"Work status data saved to {WORK_STATUS_DATA_FILE}")

def load_work_status_feed_data():
    """Load the transient work status feed from storage/feed/work-status_feed.json.

    Structure matches the persistent file: {requests, approved, rejected}.
    """
    if not os.path.exists(WORK_STATUS_FEED_FILE):
        default_data = _empty_work_status()
        with open(WORK_STATUS_FEED_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
            f.write("\n")
        print(f"Created new work status feed file: {WORK_STATUS_FEED_FILE}")
        return default_data

    try:
        with open(WORK_STATUS_FEED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return _normalize_work_status_payload(data)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading work status feed file: {e}")
        return _empty_work_status()

def save_work_status_feed_data(work_status_feed):
    """Save the transient work status feed to storage/feed/work-status_feed.json."""
    os.makedirs(os.path.dirname(WORK_STATUS_FEED_FILE), exist_ok=True)
    with open(WORK_STATUS_FEED_FILE, "w", encoding="utf-8") as f:
        json.dump(work_status_feed, f, indent=4)
        f.write("\n")
    print(f"Work status feed saved to {WORK_STATUS_FEED_FILE}")

def save_both_work_status_files(payload):
    """Write the same payload to BOTH the persistent database and the feed.

    Both files hold identical content at all times. Only the feed copy
    gets emptied at midnight.
    """
    save_work_status_data(payload)
    save_work_status_feed_data(payload)
    # Also refresh the in-memory feed copy so reads stay in sync.
    try:
        work_status_feed_data.clear()
        work_status_feed_data.update(payload)
    except Exception:
        pass

# Load both on startup. If the feed is missing but the persistent file
# exists, copy the persistent data into the feed so they start in sync.
work_status_data = load_work_status_data()
work_status_feed_data = load_work_status_feed_data()

if not work_status_feed_data.get("requests") and not work_status_feed_data.get("approved") and not work_status_feed_data.get("rejected"):
    # Feed is empty — mirror the persistent file so they match on boot.
    if work_status_data.get("requests") or work_status_data.get("approved") or work_status_data.get("rejected"):
        print("Work status feed was empty — mirroring persistent work-status.json into feed for consistency.")
        work_status_feed_data = {
            "requests": list(work_status_data.get("requests", [])),
            "approved": list(work_status_data.get("approved", [])),
            "rejected": list(work_status_data.get("rejected", [])),
        }
        save_work_status_feed_data(work_status_feed_data)

# Load activity feed data
def load_activity_feed():
    """Load activity feed data from JSON file"""
    if not os.path.exists(ACTIVITY_FEED_FILE):
        default_data = {
            "activities": [],
            "total_activities": 0,
            "last_cleanup": datetime.now().isoformat()
        }
        with open(ACTIVITY_FEED_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
            f.write("\n")
        print(f"Created new activity feed file: {ACTIVITY_FEED_FILE}")
        return default_data

    try:
        with open(ACTIVITY_FEED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "activities" not in data:
                data["activities"] = []
            if "total_activities" not in data:
                data["total_activities"] = len(data["activities"])
            if "last_cleanup" not in data:
                data["last_cleanup"] = datetime.now().isoformat()
            return data
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading activity feed file: {e}")
        return {"activities": [], "total_activities": 0, "last_cleanup": datetime.now().isoformat()}

# Save activity feed data
def save_activity_feed(activity_data):
    """Save activity feed data to JSON file"""
    os.makedirs(os.path.dirname(ACTIVITY_FEED_FILE), exist_ok=True)
    with open(ACTIVITY_FEED_FILE, "w", encoding="utf-8") as f:
        json.dump(activity_data, f, indent=4)
        f.write("\n")

# Add activity to feed
def add_activity(action, details, user=None, activity_type="system"):
    """
    Add a system activity to the feed.

    Args:
        action (str): The action performed (e.g., "employee_registered", "rfid_scanned")
        details (str): Description of the activity
        user (dict): User who performed the action
        activity_type (str): Type of activity (system, employee, attendance, work_status, etc.)

    Returns:
        dict: The created activity entry
    """
    activity_feed_data = load_activity_feed()

    activity_entry = {
        "id": str(len(activity_feed_data["activities"]) + 1),
        "action": action,
        "details": details,
        "type": activity_type,
        "timestamp": datetime.now().isoformat(),
        "user": user
    }

    # Insert at the beginning (newest first)
    activity_feed_data["activities"].insert(0, activity_entry)

    # Keep only last 1000 activities
    if len(activity_feed_data["activities"]) > 1000:
        activity_feed_data["activities"] = activity_feed_data["activities"][:1000]

    activity_feed_data["total_activities"] = len(activity_feed_data["activities"])

    save_activity_feed(activity_feed_data)

    print(f"Activity logged: {action} - {details}")
    return activity_entry

# Load scan events
def load_scan_events():
    if not os.path.exists(SCAN_EVENTS_FILE):
        default_data = {"scan_events": []}
        with open(SCAN_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
            f.write("\n")
        print(f"Created new scan events file: {SCAN_EVENTS_FILE}")
        return default_data

    try:
        with open(SCAN_EVENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "scan_events" not in data:
                data["scan_events"] = []
            return data
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading scan events file: {e}")
        return {"scan_events": []}

# Save scan events
def save_scan_events(scan_events_data):
    os.makedirs(os.path.dirname(SCAN_EVENTS_FILE), exist_ok=True)
    with open(SCAN_EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(scan_events_data, f, indent=4)
        f.write("\n")
    print(f"Scan events saved to {SCAN_EVENTS_FILE}")

# Load persisted DTR records.
def load_attendance_data():
    # Ensure the directory exists
    os.makedirs(os.path.dirname(ATTENDANCE_DATA_FILE), exist_ok=True)

    if not os.path.exists(ATTENDANCE_DATA_FILE):
        # Create empty file with proper structure
        default_data = {"records": []}
        with open(ATTENDANCE_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
            f.write("\n")
        print(f"Created new attendance file: {ATTENDANCE_DATA_FILE}")
        return default_data

    try:
        with open(ATTENDANCE_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure we have the proper structure - only records
            if isinstance(data, list):
                return {"records": data}
            if "records" not in data:
                data["records"] = []
            return data
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading attendance file: {e}")
        if os.path.exists(ATTENDANCE_DATA_FILE):
            backup_file = ATTENDANCE_DATA_FILE + ".backup"
            try:
                os.rename(ATTENDANCE_DATA_FILE, backup_file)
                print(f"Corrupted file backed up to: {backup_file}")
            except:
                pass
        return {"records": []}

# Load scan feed data
def load_scan_feed():
    """Load scan feed data from JSON file"""
    if not os.path.exists(SCAN_FEED_FILE):
        default_data = {
            "scans": [],
            "total_scans": 0,
            "last_cleanup": datetime.now().isoformat()
        }
        with open(SCAN_FEED_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
            f.write("\n")
        print(f"Created new scan feed file: {SCAN_FEED_FILE}")
        return default_data

    try:
        with open(SCAN_FEED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "scans" not in data:
                data["scans"] = []
            if "total_scans" not in data:
                data["total_scans"] = len(data["scans"])
            if "last_cleanup" not in data:
                data["last_cleanup"] = datetime.now().isoformat()
            return data
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error reading scan feed file: {e}")
        return {"scans": [], "total_scans": 0, "last_cleanup": datetime.now().isoformat()}

# Save scan feed data
def save_scan_feed(scan_data):
    """Save scan feed data to JSON file"""
    os.makedirs(os.path.dirname(SCAN_FEED_FILE), exist_ok=True)
    with open(SCAN_FEED_FILE, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=4)
        f.write("\n")

# Add scan to feed
def add_scan_to_feed(rfid, scanned_at, employee=None, found=False, scan_type="unknown"):
    """Add a single scan to the feed with proper formatting.

    Deduplicates on (rfid, scanned_at). If the same physical tap is written
    twice — once by receive_rfid with scan_type="unknown" and again by
    record_attendance_scan with scan_type="am_in"/"pm_in"/etc. — the second
    call updates the existing entry in place instead of appending a new one.
    This also handles the case where receive_rfid passes employee=None first
    and record_attendance_scan passes the real employee a moment later.
    """
    scan_feed_data = load_scan_feed()

    scan_date = datetime.now().date().isoformat()

    # Build the fresh payload for this tap.
    employee_snapshot = None
    if employee:
        employee_snapshot = {
            "uid": employee.get("uid"),
            "employeeid": employee.get("employeeid"),
            "firstname": employee.get("firstname"),
            "lastname": employee.get("lastname"),
            "role": employee.get("role"),
            "department": employee.get("department")
        }

    new_entry = {
        "rfid": rfid,
        "scanned_at": scanned_at,
        "scanned_on": scan_date,
        "found": found,
        "scan_type": scan_type,
        "timestamp": datetime.now().isoformat(),
        "employee": employee_snapshot
    }

    # Deduplicate: look for an existing entry with the same rfid + scanned_at.
    # If we find one, MERGE instead of inserting a duplicate row.
    existing = None
    for idx, entry in enumerate(scan_feed_data.get("scans", [])):
        if entry.get("rfid") == rfid and entry.get("scanned_at") == scanned_at:
            existing = idx
            break

    if existing is not None:
        prev = scan_feed_data["scans"][existing]
        # Prefer the more informative scan_type (real event over "unknown").
        prev_type = str(prev.get("scan_type", "unknown")).lower()
        new_type = str(scan_type or "unknown").lower()
        if prev_type in ("unknown", "") and new_type not in ("unknown", ""):
            prev["scan_type"] = scan_type
        # Fill in the employee record if it was missing before.
        if not prev.get("employee") and employee_snapshot:
            prev["employee"] = employee_snapshot
            prev["found"] = True
        # Refresh the timestamp so the row stays newest-first in the list.
        prev["timestamp"] = new_entry["timestamp"]
        scan_feed_data["scans"][existing] = prev
        print(f"scan feed: merged duplicate tap for {rfid} at {scanned_at} (scan_type={prev['scan_type']})")
    else:
        scan_feed_data["scans"].insert(0, new_entry)

    if len(scan_feed_data["scans"]) > 1000:
        scan_feed_data["scans"] = scan_feed_data["scans"][:1000]

    scan_feed_data["total_scans"] = len(scan_feed_data["scans"])

    save_scan_feed(scan_feed_data)

    return scan_feed_data["scans"][existing] if existing is not None else new_entry

# Load attendance data
attendance_data = load_attendance_data()
attendance_records = attendance_data["records"]

# Load scan events
scan_events_data = load_scan_events()
scan_events = scan_events_data.get("scan_events", [])

if scan_events:
    latest_scan.update({
        "rfid": scan_events[-1].get("rfid"),
        "scanned_at": scan_events[-1].get("scanned_at")
    })

# Save DTR records to the attendance database (ONLY records, no scan events).
def save_attendance_data():
    """Save attendance records to attendance.json file - ONLY records, no scan events"""
    os.makedirs(os.path.dirname(ATTENDANCE_DATA_FILE), exist_ok=True)

    if os.path.exists(ATTENDANCE_DATA_FILE):
        backup_file = ATTENDANCE_DATA_FILE + ".backup"
        try:
            import shutil
            shutil.copy2(ATTENDANCE_DATA_FILE, backup_file)
        except:
            pass

    with open(ATTENDANCE_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "records": attendance_records
        }, f, indent=4)
        f.write("\n")
    print(f"Attendance data saved to {ATTENDANCE_DATA_FILE} - {len(attendance_records)} total records")

# Build DTR dictionary for a month
def build_dtr_dict(year, month):
    """Build DTR dictionary with date keys like '1-sep', '2-sep'"""
    days_in_month = calendar.monthrange(year, month)[1]
    dtr = {}
    month_name = calendar.month_abbr[month].lower()

    for day in range(1, days_in_month + 1):
        date_obj = datetime(year, month, day)
        date_key = f"{day}-{month_name}"
        dtr[date_key] = {
            "date": date_obj.strftime("%Y-%m-%d"),
            "day": calendar.day_abbr[date_obj.weekday()],
            "am_in": "",
            "am_out": "",
            "pm_in": "",
            "pm_out": "",
            "hours": "0.00",
            "ut": "0.00",
            "ot": "0.00",
            "status": "",
            # Work status fields - track specific time work status
            "work_status": None,  # {type, period, start_time, end_time, is_specific_time}
            # Hidden 24-hour copies of the four timestamps above. These are
            # what calculate_hours() actually uses so the AM/PM distinction
            # is never lost when we store the 12-hour display value.
            "am_in_24": "",
            "am_out_24": "",
            "pm_in_24": "",
            "pm_out_24": ""
        }
    return dtr

# Find or create a DTR record using the user's identity fields.
def get_attendance_record(employee, scan_date):
    """
    Find existing attendance record for a user and month, or create a new one.
    Uses DTR format with date-keyed entries.
    """
    month_key = scan_date.strftime("%Y-%m")
    month_display = scan_date.strftime("%B %Y")
    uid = employee.get("uid")

    # First try to find existing record for this user and month
    for record in attendance_records:
        if record.get("uid") == uid and record.get("month") == month_key:
            print(f"Found existing record for {employee.get('firstname')} for month {month_key}")
            return record

    # No record exists - create a new one with DTR format
    existing_ids = []
    for r in attendance_records:
        try:
            if str(r.get("id", "")).isdigit():
                existing_ids.append(int(r.get("id")))
        except:
            pass

    new_id = str(max(existing_ids + [0]) + 1).zfill(3)

    record = {
        "id": new_id,
        "uid": uid,
        "employeeid": employee.get("employeeid", ""),
        "rfid": employee.get("rfid", ""),
        "fullname": f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip(),
        "position": employee.get("position", ""),
        "department": employee.get("department", ""),
        "month": month_key,
        "month_display": month_display,
        "dtr": build_dtr_dict(scan_date.year, scan_date.month),
        "total_hours": "0.00",
        "total_ut": "0.00",
        "total_ot": "0.00"
    }
    attendance_records.append(record)
    print(f"Created new attendance record for {record['fullname']} for month {month_key}")
    return record

# Determine if a time is AM or PM period
# NOTE: Kept for backwards compatibility but no longer used to pick the slot.
# Slot selection is now handled by determine_scan_type(), which uses the
# 24-hour clock AND the current fill state of the DTR day.
def get_period(scan_time):
    """Return 'am' if hour < 12, else 'pm'"""
    return "am" if scan_time.hour < 12 else "pm"

# Check if a user has already timed in for the current period
def has_time_in_for_period(day_data, period):
    """Check if the user already has a time in for the given period"""
    in_key = f"{period}_in"
    return bool(day_data.get(in_key))

# Check if a user has already timed out for the current period
def has_time_out_for_period(day_data, period):
    """Check if the user already has a time out for the given period"""
    out_key = f"{period}_out"
    return bool(day_data.get(out_key))

# Check if a day is marked as on work status
def is_on_work_status(day_data):
    """Check if the day record is marked as on work status.

    For whole-day work status, the status will be 'on_work_status'.
    For specific-time work status, the work_status field will have details.
    """
    if day_data.get("status") == "on_work_status":
        return True
    # Also check the work_status field for any active work status
    ws = day_data.get("work_status")
    if ws and ws.get("is_active", True):
        return True
    return False

def get_work_status_for_period(day_data, period):
    """Get the work status for a specific period (am/pm).

    Returns the work status object if the period is affected, or None.
    """
    ws = day_data.get("work_status")
    if not ws:
        return None

    if not ws.get("is_active", True):
        return None

    # If whole day, both am and pm are affected
    if ws.get("period") == "whole_day":
        return ws

    # Check if the specific period is affected
    if ws.get("period") == period:
        return ws

    return None

def is_period_affected_by_work_status(day_data, period):
    """Check if a specific period (am/pm) is affected by work status."""
    return get_work_status_for_period(day_data, period) is not None

# ============================================================================
# TIME-AWARE SEQUENTIAL SLOT-FILLING LOGIC
# ============================================================================
# The DTR has four time slots, filled using BOTH the 24-hour clock time of
# the scan AND how many slots are already filled:
#
#     1. am_in   (first morning tap)
#     2. am_out  (second morning tap / first closing tap)
#     3. pm_in   (first afternoon tap)
#     4. pm_out  (last afternoon tap)
#
# Realistic scenarios this handles:
#
#   • Regular morning employee
#         07:30 (first)  -> am_in
#         11:30 (second) -> am_out
#         13:00 (third)  -> pm_in
#         17:00 (fourth) -> pm_out
#
#   • Afternoon-only employee (arrives at/after noon)
#         13:00 (first)  -> pm_in     (NOT am_in)
#         17:00 (second) -> pm_out    (NOT am_out)
#
#   • Late arrival (12:30 first tap) – afternoon time, nothing filled yet
#         12:30 (first)  -> pm_in     (Rule A: no AM slots filled yet)
#
#   • Early riser who then taps again in the afternoon
#         07:30 (first)  -> am_in
#         14:00 (second) -> am_out    (Rule C: finish AM before PM)
#         17:00 (third)  -> pm_in
#
# All times are stored in 24-hour HH:MM so AM/PM is never ambiguous.
# ============================================================================

def determine_scan_type(day_data, scan_time, employee):
    """
    Decide which DTR slot the current tap should fill, using BOTH:
      • the 24-hour wall-clock time of the scan, and
      • how many slots are already filled.

    This lets us handle all the realistic cases correctly:

    ─ Regular morning employee ─────────────────────────────────────
        07:30 (first tap)   -> am_in
        11:30 (second tap)  -> am_out
        13:00 (third tap)   -> pm_in
        17:00 (fourth tap)  -> pm_out

    ─ Afternoon-only employee (arrives at noon or later) ────────────
        13:00 (first tap)   -> pm_in          (NOT am_in)
        17:00 (second tap)  -> pm_out         (NOT am_out)

    ─ Late arrival but nothing filled yet ──────────────────────────
        12:30 (first tap)   -> pm_in          (Rule A: no AM slots filled yet)

    ─ Early arrival, PM time, AM already started ───────────────────
        07:30 (first tap)   -> am_in
        14:00 (second tap)  -> am_out         (Rule C: finish AM before PM)
        17:00 (third tap)   -> pm_in

    Returns:
        (period, scan_type)  where period is "am" | "pm" and scan_type is
                             "in" | "out"
        None                 if the scan should be skipped (cooldown, all
                             slots full, or whole-day work status)
    """
    rfid = employee.get("rfid")

    # ---- 1) Whole-day work status short-circuits everything ----------------
    ws = day_data.get("work_status")
    if ws and ws.get("period") == "whole_day" and ws.get("is_active", True):
        print(f"Day marked as WHOLE DAY WORK STATUS for {rfid} - scan skipped")
        return None

    if day_data.get("status") == "on_work_status":
        ws = day_data.get("work_status")
        if ws and ws.get("period") == "whole_day" and ws.get("is_active", True):
            print(f"Day marked as ON WORK STATUS (whole day) for {rfid} - scan skipped")
            return None
        # Specific-time work status (AM or PM only) is fine — allow the scan.

    # ---- 2) Read the wall-clock hour in 24-hour format ---------------------
    # scan_time is a datetime, so .hour is already 0–23.
    is_pm_time = scan_time.hour >= 12

    # ---- 3) Check which slots are already filled ---------------------------
    has_am_in = bool(day_data.get("am_in"))
    has_am_out = bool(day_data.get("am_out"))
    has_pm_in = bool(day_data.get("pm_in"))
    has_pm_out = bool(day_data.get("pm_out"))

    am_filled = has_am_in and has_am_out
    pm_filled = has_pm_in and has_pm_out

    if am_filled and pm_filled:
        print(f"All time slots filled for {rfid}")
        return None

    # ---- 4) Decide the next slot based on time AND fill state -------------
    #
    # Rule A — it's PM time and NOTHING has been filled yet.
    #   Employee is arriving for the first time in the afternoon.
    #   -> Start directly at pm_in. (Do NOT put "13:00" into am_in.)
    #
    # Rule B — it's PM time and the AM side is already complete.
    #   -> Continue with the next PM slot.
    #
    # Rule C — it's PM time and AM was only started (am_in exists, am_out missing).
    #   -> Finish the morning first so a late-morning tap closes am_out.
    #
    # Rule D — it's AM time.
    #   -> Fill AM slots in order; once AM is complete, move to PM slots.

    period = None
    scan_type = None

    if is_pm_time and not has_am_in and not has_pm_in:
        # Rule A: first tap of the day, happening in the afternoon
        period, scan_type = "pm", "in"

    elif is_pm_time and am_filled and not has_pm_in:
        # Rule B: AM complete, next PM slot is pm_in
        period, scan_type = "pm", "in"

    elif is_pm_time and has_pm_in and not has_pm_out:
        # Rule B: PM in already recorded, close it with pm_out
        period, scan_type = "pm", "out"

    elif is_pm_time and has_am_in and not has_am_out:
        # Rule C: it's PM time but AM was only started, finish AM first
        period, scan_type = "am", "out"

    elif not is_pm_time and not has_am_in:
        # Rule D: AM time, start of day
        period, scan_type = "am", "in"

    elif not is_pm_time and has_am_in and not has_am_out:
        # Rule D: AM time, close the morning
        period, scan_type = "am", "out"

    elif am_filled and not has_pm_in:
        # AM is complete, any time -> next is pm_in
        period, scan_type = "pm", "in"

    elif has_pm_in and not has_pm_out:
        # PM in already recorded -> close with pm_out
        period, scan_type = "pm", "out"

    else:
        print(f"No suitable slot found for {rfid} - skipping")
        return None

    # ---- 5) Cooldown guard -------------------------------------------------
    # Block if the LAST recorded tap for this RFID was the SAME direction
    # (in / out) and it happened within the cooldown window. This stops an
    # accidental double-tap from burning the next slot.
    def same_direction_within_cooldown(direction):
        if rfid not in last_scan_tracking:
            return False
        last = last_scan_tracking[rfid]
        if last.get("last_scan_type") != direction:
            return False
        last_time = last.get("last_scan_time")
        if last_time is None:
            return False
        return (scan_time - last_time).total_seconds() < SCAN_COOLDOWN_SECONDS

    if same_direction_within_cooldown(scan_type):
        print(f"{scan_type.upper()} cooldown not met for {rfid}")
        return None

    # ---- 6) Return the chosen slot ----------------------------------------
    print(
        f"Slot chosen for {rfid} at {scan_time.strftime('%H:%M')} "
        f"(is_pm_time={is_pm_time}, am_filled={am_filled}, pm_filled={pm_filled}) "
        f"-> {period}_{scan_type}"
    )
    return (period, scan_type)

# Format a datetime as 24-hour time HH:MM (no seconds) for DTR storage.
#
# This is the ONLY format written to the DTR now — the visible AM/PM fields
# and the hidden *_24 fields all carry the same 24-hour string. That means
# there is no ambiguity between "02:30" (morning) and "14:30" (afternoon):
# the DTR always stores "14:30" for a 2:30 PM tap.
def format_dtr_time(scan_time):
    """Return HH:MM in 24-hour format (00:00 – 23:59)."""
    return scan_time.strftime("%H:%M")

# Kept as a separate function name for backwards compatibility with older
# code paths that still call format_dtr_time_24h(). It returns the exact
# same HH:MM 24-hour string as format_dtr_time().
def format_dtr_time_24h(scan_time):
    """Return HH:MM in 24-hour format (00:00 – 23:59)."""
    return scan_time.strftime("%H:%M")

# Add a device timestamp to the correct DTR slot.
def record_attendance_scan(employee, scanned_at):
    """Record attendance scan - handles creating records for new employees and months"""
    scan_time = parse_scan_time(scanned_at)
    rfid = employee.get("rfid")

    # Get or create the record for this user and month
    record = get_attendance_record(employee, scan_time)

    # Find the day record in DTR
    day_date = scan_time.strftime("%Y-%m-%d")
    day_data = None
    date_key = None

    for key, day in record.get("dtr", {}).items():
        if day.get("date") == day_date:
            day_data = day
            date_key = key
            break

    if not day_data:
        # This shouldn't happen, but just in case
        day_data = {
            "date": day_date,
            "day": calendar.day_abbr[scan_time.weekday()],
            "am_in": "",
            "am_out": "",
            "pm_in": "",
            "pm_out": "",
            "hours": "0.00",
            "ut": "0.00",
            "ot": "0.00",
            "status": "",
            "work_status": None,
            "am_in_24": "",
            "am_out_24": "",
            "pm_in_24": "",
            "pm_out_24": ""
        }
        record["dtr"][f"{scan_time.day}-{calendar.month_abbr[scan_time.month].lower()}"] = day_data

    # 24-hour HH:MM, no seconds. Example: "07:30" or "13:02".
    time_value = format_dtr_time(scan_time)
    time_value_24h = format_dtr_time_24h(scan_time)

    # Determine the next slot to fill (pure sequential logic — ignores clock).
    scan_result = determine_scan_type(day_data, scan_time, employee)

    if scan_result is None:
        print(f"Scan skipped for {rfid} - cooldown not met, already scanned, or on work status")
        return record, "skipped"

    period, scan_type = scan_result
    in_key = f"{period}_in"
    out_key = f"{period}_out"
    hidden_key = f"{period}_{scan_type}_24"

    # Check if this period is affected by a specific-time work status.
    # If so, we still record the scan but mark it as work status related.
    period_work_status = get_work_status_for_period(day_data, period)

    if scan_type == "in":
        if not day_data[in_key]:
            day_data[in_key] = time_value
            day_data[hidden_key] = time_value_24h
            print(f"Recorded {period.upper()} TIME IN for {rfid} at {time_value} ({time_value_24h})")
            last_scan_tracking[rfid] = {
                "last_scan_time": scan_time,
                "last_scan_type": "in"
            }
            add_scan_to_feed(
                rfid,
                scanned_at,
                employee,
                True,
                f"{period}_in"
            )
            # Log activity
            add_activity(
                "attendance_time_in",
                f"{employee.get('firstname', '')} {employee.get('lastname', '')} ({employee.get('employeeid', '')}) clocked in for {period.upper()} shift",
                {"name": f"{employee.get('firstname', '')} {employee.get('lastname', '')}", "uid": employee.get('uid')},
                "attendance"
            )
            # Push a notification to this user's own notification file.
            try:
                push_notification(
                    rfid,
                    f"Time In Recorded ({period.upper()})",
                    f"Your {period.upper()} time-in was recorded at {time_value}.",
                    notif_type="attendance",
                    uid=employee.get("uid"),
                    fullname=f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip(),
                    role=employee.get("role")
                )
            except Exception as e:
                print(f"Warning: failed to push time-in notification for {rfid}: {e}")
        else:
            print(f"{period.upper()} TIME IN already exists for {rfid}")
            return record, "already_exists"
    elif scan_type == "out":
        if not day_data[out_key]:
            day_data[out_key] = time_value
            day_data[hidden_key] = time_value_24h
            print(f"Recorded {period.upper()} TIME OUT for {rfid} at {time_value} ({time_value_24h})")
            last_scan_tracking[rfid] = {
                "last_scan_time": scan_time,
                "last_scan_type": "out"
            }
            add_scan_to_feed(
                rfid,
                scanned_at,
                employee,
                True,
                f"{period}_out"
            )
            # Log activity
            add_activity(
                "attendance_time_out",
                f"{employee.get('firstname', '')} {employee.get('lastname', '')} ({employee.get('employeeid', '')}) clocked out for {period.upper()} shift",
                {"name": f"{employee.get('firstname', '')} {employee.get('lastname', '')}", "uid": employee.get('uid')},
                "attendance"
            )
            # Push a notification to this user's own notification file.
            try:
                push_notification(
                    rfid,
                    f"Time Out Recorded ({period.upper()})",
                    f"Your {period.upper()} time-out was recorded at {time_value}.",
                    notif_type="attendance",
                    uid=employee.get("uid"),
                    fullname=f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip(),
                    role=employee.get("role")
                )
            except Exception as e:
                print(f"Warning: failed to push time-out notification for {rfid}: {e}")
        else:
            print(f"{period.upper()} TIME OUT already exists for {rfid}")
            return record, "already_exists"

    # ------------------------------------------------------------------
    # Recalculate day totals.
    #
    # Hours are ALWAYS computed from the hidden 24-hour timestamps when
    # they are present. When they are absent (legacy records written
    # before this fix), we fall back to the period-aware heuristic so
    # old DTR entries still produce a sensible number.
    #
    # UT / OT are only charged when the day is FULLY complete — all four
    # of am_in, am_out, pm_in, pm_out are present. A partial day is
    # treated as "no hours counted yet" so an unfinished morning does
    # not generate phantom undertime against the full 8-hour target.
    # ------------------------------------------------------------------
    am_hours = calculate_hours(day_data.get("am_in", ""), day_data.get("am_out", ""),
                               period="am",
                               start_24=day_data.get("am_in_24", ""),
                               end_24=day_data.get("am_out_24", ""))
    pm_hours = calculate_hours(day_data.get("pm_in", ""), day_data.get("pm_out", ""),
                               period="pm",
                               start_24=day_data.get("pm_in_24", ""),
                               end_24=day_data.get("pm_out_24", ""))
    total_hours = am_hours + pm_hours

    # A day is only "fully complete" once every one of the four slots is
    # filled. Until then, UT / OT stay at 0.00.
    all_four_present = bool(
        day_data.get("am_in") and day_data.get("am_out") and
        day_data.get("pm_in") and day_data.get("pm_out")
    )

    # Required daily hours now come from settings.json (work_start,
    # work_end, lunch_start, lunch_end) instead of a hardcoded 8.
    required_hours = get_required_hours()

    day_data["hours"] = f"{total_hours:.2f}"

    if all_four_present:
        day_data["ut"] = f"{max(0, required_hours - total_hours):.2f}"
        day_data["ot"] = f"{max(0, total_hours - required_hours):.2f}"
    else:
        day_data["ut"] = "0.00"
        day_data["ot"] = "0.00"

    # Calculate total hours for the month
    total_hours_month = 0
    total_ut_month = 0
    total_ot_month = 0
    for day in record.get("dtr", {}).values():
        try:
            total_hours_month += float(day.get("hours", "0.00"))
            total_ut_month += float(day.get("ut", "0.00"))
            total_ot_month += float(day.get("ot", "0.00"))
        except:
            pass

    record["total_hours"] = f"{total_hours_month:.2f}"
    record["total_ut"] = f"{total_ut_month:.2f}"
    record["total_ot"] = f"{total_ot_month:.2f}"

    return record, "success"

# Parse the timestamp supplied by the RFID device.
def parse_scan_time(scanned_at):
    try:
        return datetime.strptime(scanned_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.now()

# Calculate completed hours between an in and out time.
#
# Primary path (preferred): when both start_24 and end_24 are supplied they
# are parsed as real 24-hour times and subtracted directly. This is
# unambiguous — 11:30 → 12:30 correctly yields 1 hour.
#
# Fallback path (legacy records written before the 24-hour fields existed):
# the 12-hour display string is parsed and the `period` argument decides
# whether an hour < 12 means morning or afternoon. This is the same
# heuristic the old code used, kept only for backwards compatibility.
#
# NOTE: The primary path now parses "HH:MM" (no seconds) because
# format_dtr_time() and format_dtr_time_24h() store that shorter form.
# The legacy path still parses "HH:MM:SS" for old records on disk.
def calculate_hours(start_time, end_time, period=None, start_24=None, end_24=None):
    # ---- Preferred: use the hidden 24-hour values ----------------------
    if start_24 and end_24:
        try:
            # Accept both "HH:MM" and "HH:MM:SS" so this works for both new
            # (short) and old (long) records still on disk.
            def _parse_hhmm(value):
                for fmt in ("%H:%M", "%H:%M:%S"):
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
                raise ValueError(f"unrecognized time format: {value!r}")

            start = _parse_hhmm(start_24)
            end = _parse_hhmm(end_24)
            # Cross-midnight guard: if the end is somehow before the
            # start (e.g. device clock skew), treat it as zero rather
            # than a negative span.
            delta = (end - start).total_seconds() / 3600
            return max(0, delta)
        except Exception:
            pass  # fall through to the legacy path

    # ---- Legacy fallback: parse the 12-hour display string -------------
    if not start_time or not end_time:
        return 0
    try:
        # Try HH:MM:SS first (legacy records), then HH:MM (new records).
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                start = datetime.strptime(start_time, fmt)
                end = datetime.strptime(end_time, fmt)
                break
            except ValueError:
                continue
        else:
            return 0

        # If a period was supplied and the parsed hour is less than 12,
        # add 12 hours so afternoon times are computed correctly.
        if period == "pm":
            # PM slot: an hour < 12 means 12:xx–11:xx in the afternoon.
            if start.hour < 12:
                start = start.replace(hour=start.hour + 12)
            if end.hour < 12:
                end = end.replace(hour=end.hour + 12)
        elif period == "am":
            # AM slot: 12:xx is midnight, so shift it to 00:xx. Any other
            # hour is already correct as a morning time.
            if start.hour == 12:
                start = start.replace(hour=0)
            if end.hour == 12:
                end = end.replace(hour=0)

        return max(0, (end - start).total_seconds() / 3600)
    except Exception:
        return 0

# Ensure every registered user has a DTR record for the current month.
def initialize_attendance_records():
    """Only adds missing records for the current month, never overwrites existing ones"""
    current_month = datetime.now()
    records_created = 0
    for employee in employee_database.values():
        get_attendance_record(employee, current_month)
        records_created += 1
    save_attendance_data()
    print(f"Initialized attendance records - {records_created} employees checked")

# Build the RFID lookup database from all user roles.
def load_employee_database():
    os.makedirs(os.path.dirname(USER_DATA_FILE), exist_ok=True)

    if not os.path.exists(USER_DATA_FILE):
        print("File not found: storage/database/users.json - database is empty")
        default_data = {"admin": [], "hr": [], "employees": []}
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
            f.write("\n")
        return {}

    try:
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print("Error reading storage/database/users.json:", str(e))
        if os.path.exists(USER_DATA_FILE):
            backup_file = USER_DATA_FILE + ".backup"
            try:
                os.rename(USER_DATA_FILE, backup_file)
                print(f"Corrupted users.json backed up to: {backup_file}")
            except:
                pass
        return {}

    db = {}
    for category in ["admin", "hr", "employees"]:
        if category in data and isinstance(data[category], list):
            for emp in data[category]:
                rfid = emp.get("rfid", "").strip().upper()
                emp["role"] = "employee" if category == "employees" else category
                if rfid:
                    # Normal case: index by RFID
                    db[rfid] = emp
                else:
                    # Fallback: index by UID so the employee still appears in the system
                    uid = str(emp.get("uid", "")).strip()
                    if uid:
                        # Use a synthetic key that won't collide with real RFIDs
                        fallback_key = f"__UID__{uid}"
                        db[fallback_key] = emp
                        print(f"  Loaded employee without RFID (UID {uid}): {emp.get('lastname', '')}")
    print("Loaded", len(db), "employees from storage/database/users.json")
    return db

employee_database = load_employee_database()

# Delete devices that have stopped sending heartbeat pings.
def remove_offline_devices():
    current_time = datetime.now()
    offline_devices = [
        device_id for device_id, data in device_status.items()
        if (current_time - data["last_seen_at"]).total_seconds() > DEVICE_TIMEOUT_SECONDS
    ]
    for device_id in offline_devices:
        del device_status[device_id]

# Get the current online device list for web display.
def get_online_devices():
    remove_offline_devices()
    return [
        {
            "device_id": device_id,
            "status": data["status"],
            "last_seen": data["last_seen"],
        }
        for device_id, data in device_status.items()
    ]

# Calculate live attendance totals from today's recognized scans.
def get_dashboard_statistics():
    """Return dashboard statistics.

    IMPORTANT: present_today, rfid_scans_today, and face_scans_today are now
    read from the PERSISTENT daily_stats.json file, NOT computed from the
    in-memory scan_events list. This means the counts are always accurate on
    every computer, even when no admin dashboard is open — because every
    RFID tap and every face match writes to daily_stats.json immediately.

    • rfid_scans_today  -> RFID taps only
    • face_scans_today  -> facial-recognition matches only
    """
    today = datetime.now().date()
    today_str = today.isoformat()

    # --- Read the persistent daily counters --------------------------------
    daily_stats = load_daily_stats()
    present_today = int(daily_stats.get("present_count", 0))
    rfid_scans_today = int(daily_stats.get("rfid_scan_count", 0))
    face_scans_today = int(daily_stats.get("face_scan_count", 0))
    total_scans_today = int(daily_stats.get("scan_count", 0))

    # All employees (regardless of RFID)
    all_employees = [
        emp for emp in employee_database.values()
        if emp.get("role") == "employee"
    ]
    total_employees = len(all_employees)

    # Absent = total employees minus those who tapped in today.
    # Clamp at 0 in case the daily stats file somehow has more uids than
    # employees (e.g. an employee was deleted mid-day).
    absent_today = max(total_employees - present_today, 0)

    # Attendance rate is based on the persistent present count.
    attendance_rate = round((present_today / total_employees) * 100, 1) if total_employees else 0

    # "On Work Status" should count UNIQUE EMPLOYEES whose approved work
    # status requests cover TODAY's date — not the total number of approved
    # requests ever. Otherwise the number grows forever and no longer means
    # what the dashboard card claims.
    employees_on_work_status_today = set()
    for req in work_status_data.get("approved", []):
        days = req.get("days") or []
        if today_str in days:
            uid = req.get("uid")
            if uid:
                employees_on_work_status_today.add(uid)
            else:
                # Fall back to RFID if the record has no uid.
                rfid = req.get("rfid")
                if rfid:
                    employees_on_work_status_today.add(rfid)

    # Count profile image files on disk so the face-scanner dashboard can
    # display "Profile Samples" without a separate request. Only JPG/PNG/
    # GIF/WEBP/BMP files count — same extension list used by the face API.
    try:
        os.makedirs(PROFILE_STORAGE, exist_ok=True)
        _allowed_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
        profile_images = sum(
            1 for f in os.listdir(PROFILE_STORAGE)
            if os.path.splitext(f)[1].lower() in _allowed_exts
        )
    except Exception:
        profile_images = 0

    return {
        "total_employees": total_employees,
        "present_today": present_today,
        "absent_today": absent_today,
        "employees_late": 0,
        "on_work_status": len(employees_on_work_status_today),
        "attendance_rate": attendance_rate,
        # rfid_scans_today is now RFID-only. face_scans_today is face-only.
        # total_scans_today is the combined number.
        "rfid_scans_today": rfid_scans_today,
        "face_scans_today": face_scans_today,
        "total_scans_today": total_scans_today,
        "departments": 0,
        "profile_images": profile_images,
        # Expose the date the counters are for so the UI can sanity-check it.
        "stats_date": daily_stats.get("date", today_str),
    }

# Prepare recent scans and user records for the dashboard UI.
def get_dashboard_data():
    recent_scans = []
    for event in reversed(scan_events[-50:]):
        employee = employee_database.get(event.get("rfid"))
        recent_scans.append({
            "rfid": event.get("rfid"),
            "scanned_at": event.get("scanned_at"),
            "found": bool(employee),
            "employee": {
                "uid": employee.get("uid"),
                "employeeid": employee.get("employeeid"),
                "lastname": employee.get("lastname"),
                "firstname": employee.get("firstname"),
                "role": employee.get("role"),
                "department": employee.get("department"),
                "image": employee.get("image")
            } if employee else None
        })

    users = [
        {
            "uid": employee.get("uid"),
            "rfid": employee.get("rfid"),
            "employeeid": employee.get("employeeid"),
            "lastname": employee.get("lastname"),
            "firstname": employee.get("firstname"),
            "address": employee.get("address"),
            "bdate": employee.get("bdate"),
            "cpnumber": employee.get("cpnumber"),
            "email": employee.get("email"),
            "username": employee.get("username"),
            "role": employee.get("role"),
            "department": employee.get("department"),
            "position": employee.get("position"),
            "image": employee.get("image"),
            "timestamp_creation": employee.get("timestamp_creation"),
            "timestamp_modified": employee.get("timestamp_modified")
        }
        for employee in employee_database.values()
    ]

    # Load activity feed
    activity_feed = load_activity_feed()

    # Both work status files hold the same data, so the persistent copy is the
    # source of truth for the dashboard payload.
    work_status_payload = {
        "requests": work_status_data.get("requests", []),
        "approved": work_status_data.get("approved", []),
        "rejected": work_status_data.get("rejected", []),
    }

    return {
        "stats": get_dashboard_statistics(),
        "users": users,
        "attendance": attendance_records,
        "scans": recent_scans,
        "devices": get_online_devices(),
        "latest_scan": recent_scans[0] if recent_scans else None,
        "work_status": work_status_payload,
        "activities": activity_feed.get("activities", [])
    }

# Return only the supported role from a user record.
def get_user_role(user):
    role = str(user.get("role", "employee")).strip().lower()
    return role if role in ROLE_DASHBOARDS else "employee"

# Build the frontend destination for the authenticated role.
def get_role_redirect(role):
    return ROLE_DASHBOARDS[role]

# ============================================================================
# REPORT DATA GENERATION - REAL DATA FROM THE DATABASE
# ============================================================================
# This replaces the old generate_report_preview() function, which returned
# hardcoded sample data. Everything below queries the actual attendance
# records, employee database, work status data, and scan feed so the printed /
# exported reports reflect what's really stored in the system.

def generate_report_print_data(report_type):
    """Generate real report data from the database for printing / exporting.

    Returns a dict:
        {
            "title": "...",
            "rows": [ {header: value, ...}, ... ],
            "summary": {label: value, ...},
            "generated_at": "..."
        }

    Every branch below pulls from attendance_records, employee_database,
    work_status_data, or the scan feed — no sample/placeholder data is used.
    """
    now = datetime.now()

    report_title = {
        "daily": "Daily Attendance Report",
        "weekly": "Weekly Attendance Report",
        "monthly": "Monthly Attendance Report",
        "yearly": "Yearly Attendance Report",
        "summary": "Attendance Summary Report",
        "absent": "Absent Employees Report",
        "leave": "Work Status Report",
        "rfid_logs": "RFID Scan Log Report"
    }.get(report_type, f"{report_type.capitalize()} Report")

    # All employees (excluding admin/hr) — these are the people whose
    # attendance actually matters for HR reporting.
    employees = [
        emp for emp in employee_database.values()
        if emp.get("role") == "employee"
    ]

    rows = []
    summary = {}

    if report_type == "daily":
        # ---- Daily attendance: everyone's status for TODAY --------------
        today = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")

        for emp in employees:
            # Find this employee's attendance record for the current month
            record = None
            for rec in attendance_records:
                if rec.get("uid") == emp.get("uid") and rec.get("month") == month_key:
                    record = rec
                    break

            am_in = am_out = pm_in = pm_out = ""
            hours = "0.00"
            status = "Absent"

            if record:
                for key, day in record.get("dtr", {}).items():
                    if day.get("date") == today:
                        am_in = day.get("am_in", "")
                        am_out = day.get("am_out", "")
                        pm_in = day.get("pm_in", "")
                        pm_out = day.get("pm_out", "")
                        hours = day.get("hours", "0.00")
                        if day.get("status") == "on_work_status":
                            status = "On Work Status"
                        elif day.get("work_status"):
                            status = "Work Status"
                        elif am_in or pm_in:
                            status = "Present"
                        break

            rows.append({
                "Employee ID": emp.get("employeeid", emp.get("uid", "")),
                "Employee Name": f"{emp.get('firstname', '')} {emp.get('lastname', '')}".strip(),
                "Department": emp.get("department", "--"),
                "AM In": am_in,
                "AM Out": am_out,
                "PM In": pm_in,
                "PM Out": pm_out,
                "Hours": hours,
                "Status": status
            })

        present = sum(1 for r in rows if r["Status"] == "Present")
        on_work_status = sum(1 for r in rows if r["Status"] in ("On Work Status", "Work Status"))
        absent = len(rows) - present - on_work_status

        summary = {
            "Total Employees": len(employees),
            "Present Today": present,
            "Absent Today": absent,
            "On Work Status": on_work_status,
            "Report Date": now.strftime("%B %d, %Y")
        }

    elif report_type == "weekly":
        # ---- Weekly attendance: Mon–Sun window ending today -------------
        start_of_week = now - timedelta(days=now.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        for emp in employees:
            total_hours = 0.0
            days_present = 0
            days_absent = 0

            for rec in attendance_records:
                if rec.get("uid") != emp.get("uid"):
                    continue

                for key, day in rec.get("dtr", {}).items():
                    try:
                        day_date = datetime.strptime(day.get("date", ""), "%Y-%m-%d")
                        if start_of_week <= day_date <= end_of_week:
                            if day.get("status") == "on_work_status":
                                continue
                            if day.get("am_in") or day.get("pm_in"):
                                days_present += 1
                                total_hours += float(day.get("hours", "0.00"))
                            elif day_date.weekday() < 5:  # Weekday
                                days_absent += 1
                    except Exception:
                        pass

            rows.append({
                "Employee ID": emp.get("employeeid", emp.get("uid", "")),
                "Employee Name": f"{emp.get('firstname', '')} {emp.get('lastname', '')}".strip(),
                "Department": emp.get("department", "--"),
                "Days Present": days_present,
                "Days Absent": days_absent,
                "Total Hours": f"{total_hours:.2f}",
                "Week": f"{start_of_week.strftime('%b %d')} - {end_of_week.strftime('%b %d, %Y')}"
            })

        summary = {
            "Total Employees": len(employees),
            "Week Period": f"{start_of_week.strftime('%B %d')} - {end_of_week.strftime('%B %d, %Y')}",
            "Total Working Days": 5
        }

    elif report_type == "monthly":
        # ---- Monthly attendance: consolidated per employee --------------
        month_key = now.strftime("%Y-%m")
        month_display = now.strftime("%B %Y")

        for emp in employees:
            total_hours = 0.0
            total_ot = 0.0
            total_ut = 0.0
            days_present = 0
            days_absent = 0
            days_work_status = 0

            for rec in attendance_records:
                if rec.get("uid") != emp.get("uid") or rec.get("month") != month_key:
                    continue

                for key, day in rec.get("dtr", {}).items():
                    try:
                        day_date = datetime.strptime(day.get("date", ""), "%Y-%m-%d")
                        if day_date.month == now.month and day_date.year == now.year:
                            if day.get("status") == "on_work_status":
                                days_work_status += 1
                            elif day.get("work_status"):
                                days_work_status += 1
                            elif day.get("am_in") or day.get("pm_in"):
                                days_present += 1
                                total_hours += float(day.get("hours", "0.00"))
                                total_ot += float(day.get("ot", "0.00"))
                                total_ut += float(day.get("ut", "0.00"))
                            elif day_date.weekday() < 5:
                                days_absent += 1
                    except Exception:
                        pass

            rows.append({
                "Employee ID": emp.get("employeeid", emp.get("uid", "")),
                "Employee Name": f"{emp.get('firstname', '')} {emp.get('lastname', '')}".strip(),
                "Department": emp.get("department", "--"),
                "Days Present": days_present,
                "Days Absent": days_absent,
                "Work Status Days": days_work_status,
                "Total Hours": f"{total_hours:.2f}",
                "Overtime": f"{total_ot:.2f}",
                "Undertime": f"{total_ut:.2f}"
            })

        summary = {
            "Total Employees": len(employees),
            "Month": month_display,
            "Working Days": sum(
                1 for d in range(1, calendar.monthrange(now.year, now.month)[1] + 1)
                if datetime(now.year, now.month, d).weekday() < 5
            )
        }

    elif report_type == "yearly":
        # ---- Yearly attendance: Jan–Dec rollup per employee -------------
        year = now.year

        for emp in employees:
            total_hours = 0.0
            total_ot = 0.0
            total_ut = 0.0
            days_present = 0
            days_work_status = 0

            for rec in attendance_records:
                if rec.get("uid") != emp.get("uid"):
                    continue

                for key, day in rec.get("dtr", {}).items():
                    try:
                        day_date = datetime.strptime(day.get("date", ""), "%Y-%m-%d")
                        if day_date.year == year:
                            if day.get("status") == "on_work_status":
                                days_work_status += 1
                            elif day.get("work_status"):
                                days_work_status += 1
                            elif day.get("am_in") or day.get("pm_in"):
                                days_present += 1
                                total_hours += float(day.get("hours", "0.00"))
                                total_ot += float(day.get("ot", "0.00"))
                                total_ut += float(day.get("ut", "0.00"))
                    except Exception:
                        pass

            attendance_pct = (days_present / 240 * 100) if days_present else 0

            rows.append({
                "Employee ID": emp.get("employeeid", emp.get("uid", "")),
                "Employee Name": f"{emp.get('firstname', '')} {emp.get('lastname', '')}".strip(),
                "Department": emp.get("department", "--"),
                "Days Present": days_present,
                "Work Status Days": days_work_status,
                "Total Hours": f"{total_hours:.2f}",
                "Overtime": f"{total_ot:.2f}",
                "Undertime": f"{total_ut:.2f}",
                "Attendance %": f"{attendance_pct:.1f}%"
            })

        summary = {
            "Total Employees": len(employees),
            "Year": year,
            "Total Working Days": 240
        }

    elif report_type == "summary":
        # ---- Summary: aggregate present/absent/work status counts -------------
        total_present = 0
        total_absent = 0
        total_work_status = 0

        for emp in employees:
            for rec in attendance_records:
                if rec.get("uid") != emp.get("uid"):
                    continue
                for key, day in rec.get("dtr", {}).items():
                    if day.get("status") == "on_work_status":
                        total_work_status += 1
                    elif day.get("work_status"):
                        total_work_status += 1
                    elif day.get("am_in") or day.get("pm_in"):
                        total_present += 1
                    else:
                        total_absent += 1

        rows.append({
            "Metric": "Total Employees",
            "Value": len(employees),
            "Percentage": "100%"
        })
        rows.append({
            "Metric": "Total Present Days",
            "Value": total_present,
            "Percentage": f"{(total_present / max(total_present + total_absent, 1) * 100):.1f}%"
        })
        rows.append({
            "Metric": "Total Absent Days",
            "Value": total_absent,
            "Percentage": f"{(total_absent / max(total_present + total_absent, 1) * 100):.1f}%"
        })
        rows.append({
            "Metric": "Total Work Status Days",
            "Value": total_work_status,
            "Percentage": f"{(total_work_status / max(total_present + total_absent + total_work_status, 1) * 100):.1f}%"
        })

        summary = {
            "Report Generated": now.strftime("%B %d, %Y at %I:%M %p"),
            "Total Employees": len(employees)
        }

    elif report_type == "absent":
        # ---- Absent employees: only those with at least one absence -----
        month_key = now.strftime("%Y-%m")

        for emp in employees:
            absent_count = 0
            absent_dates = []

            for rec in attendance_records:
                if rec.get("uid") != emp.get("uid") or rec.get("month") != month_key:
                    continue

                for key, day in rec.get("dtr", {}).items():
                    try:
                        day_date = datetime.strptime(day.get("date", ""), "%Y-%m-%d")
                        if day_date.weekday() < 5:  # Weekday
                            if not day.get("am_in") and not day.get("pm_in") and day.get("status") != "on_work_status" and not day.get("work_status"):
                                absent_count += 1
                                absent_dates.append(day_date.strftime("%b %d"))
                    except Exception:
                        pass

            if absent_count > 0:
                rows.append({
                    "Employee ID": emp.get("employeeid", emp.get("uid", "")),
                    "Employee Name": f"{emp.get('firstname', '')} {emp.get('lastname', '')}".strip(),
                    "Department": emp.get("department", "--"),
                    "Absence Count": absent_count,
                    "Absent Dates": ", ".join(absent_dates[:10]) + ("..." if len(absent_dates) > 10 else "")
                })

        summary = {
            "Month": now.strftime("%B %Y"),
            "Total Employees with Absences": len(rows)
        }

    elif report_type == "leave":
        # ---- Work status report: every request across all three buckets -------
        all_work_status = (
            work_status_data.get("requests", [])
            + work_status_data.get("approved", [])
            + work_status_data.get("rejected", [])
        )

        for req in all_work_status:
            rows.append({
                "Employee ID": req.get("employeeid", req.get("uid", "")),
                "Employee Name": req.get("fullname", ""),
                "Work Status Type": (req.get("work_status_type", "") or "").replace("_", " ").title(),
                "Period": req.get("period", "whole_day").replace("_", " ").title(),
                "Start Date": req.get("start_date", ""),
                "End Date": req.get("end_date", ""),
                "Days": len(req.get("days", [])),
                "Status": (req.get("status", "pending") or "pending").upper(),
                "Requested At": (req.get("requested_at", "") or "")[:10]
            })

        approved = len(work_status_data.get("approved", []))
        pending = len(work_status_data.get("requests", []))
        rejected = len(work_status_data.get("rejected", []))

        summary = {
            "Total Requests": len(all_work_status),
            "Approved": approved,
            "Pending": pending,
            "Rejected": rejected
        }

    elif report_type == "rfid_logs":
        # ---- RFID scan logs: most recent 100 scans in the feed ----------
        scan_feed = load_scan_feed()
        scans = scan_feed.get("scans", [])[:100]

        for scan in scans:
            emp = scan.get("employee") or {}
            rows.append({
                "Timestamp": scan.get("scanned_at", ""),
                "RFID": scan.get("rfid", ""),
                "Employee ID": emp.get("employeeid", ""),
                "Employee Name": f"{emp.get('firstname', '')} {emp.get('lastname', '')}".strip() or "Unknown",
                "Scan Type": (scan.get("scan_type", "") or "").replace("_", " ").upper(),
                "Status": "Success" if scan.get("found") else "Unknown Card"
            })

        summary = {
            "Total Scans": scan_feed.get("total_scans", 0),
            "Scans Shown": len(rows)
        }

    return {
        "title": report_title,
        "rows": rows,
        "summary": summary,
        "generated_at": now.strftime("%B %d, %Y at %I:%M %p")
    }

def generate_report_pdf_buffer(report_type):
    """Generate PDF report buffer using REAL data.

    Uses generate_report_print_data() to fetch actual rows and summary,
    then lays them out on a landscape letter page with a header, data
    table, and summary block.
    """
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER

    report_data = generate_report_print_data(report_type)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=10,
        alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        name='CustomSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        alignment=TA_CENTER,
        textColor=colors.grey,
        spaceAfter=15
    ))

    elements = []

    # Header block
    elements.append(Paragraph(report_data["title"], styles['CustomTitle']))
    elements.append(Paragraph(
        "ISPSC Tagudin Campus · TAPIN RFID Attendance System",
        styles['CustomSubtitle']
    ))
    elements.append(Paragraph(
        f"Generated: {report_data['generated_at']}",
        styles['CustomSubtitle']
    ))
    elements.append(Spacer(1, 0.15 * inch))

    # Data table
    if report_data["rows"]:
        headers = list(report_data["rows"][0].keys())
        table_data = [headers]

        for row in report_data["rows"]:
            table_data.append([str(row.get(h, "")) for h in headers])

        num_cols = len(headers)
        available_width = landscape(letter)[0] - 1 * inch
        col_width = available_width / num_cols

        table = Table(table_data, colWidths=[col_width] * num_cols, repeatRows=1)
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No data available for this report.", styles['Normal']))

    # Summary block
    if report_data["summary"]:
        elements.append(Spacer(1, 0.25 * inch))
        elements.append(Paragraph("Summary", styles['Heading2']))

        summary_data = [[k, str(v)] for k, v in report_data["summary"].items()]
        summary_table = Table(summary_data, colWidths=[2 * inch, 2 * inch])
        summary_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(summary_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer

def generate_report_excel_buffer(report_type):
    """Generate Excel report buffer using REAL data.

    Uses generate_report_print_data() to fetch actual rows and summary,
    then writes them into a styled worksheet with a title, header row,
    data rows, and a summary section below.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    report_data = generate_report_print_data(report_type)

    wb = Workbook()
    ws = wb.active
    ws.title = report_data["title"][:31]  # Excel sheet name limit

    # Styles
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
    title_font = Font(bold=True, size=14)
    center_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # Title rows
    ws.merge_cells('A1:H1')
    ws['A1'] = report_data["title"]
    ws['A1'].font = title_font
    ws['A1'].alignment = center_alignment

    ws.merge_cells('A2:H2')
    ws['A2'] = f"ISPSC Tagudin Campus · Generated: {report_data['generated_at']}"
    ws['A2'].alignment = center_alignment
    ws['A2'].font = Font(size=10, italic=True)

    start_row = 4

    if report_data["rows"]:
        headers = list(report_data["rows"][0].keys())

        # Header row
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=start_row, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_alignment
            cell.border = thin_border

        # Data rows
        for row_idx, row in enumerate(report_data["rows"], start=start_row + 1):
            for col_idx, header in enumerate(headers, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=row.get(header, ""))
                cell.alignment = center_alignment
                cell.border = thin_border

        # Auto column widths
        for col_idx, header in enumerate(headers, start=1):
            max_length = len(str(header))
            for row in report_data["rows"]:
                val_length = len(str(row.get(header, "")))
                if val_length > max_length:
                    max_length = val_length
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_length + 4, 40)

        # Summary block
        if report_data["summary"]:
            summary_start = start_row + len(report_data["rows"]) + 2
            ws.cell(row=summary_start, column=1, value="Summary").font = Font(bold=True, size=12)

            for idx, (key, value) in enumerate(report_data["summary"].items(), start=1):
                ws.cell(row=summary_start + idx, column=1, value=key).font = Font(bold=True)
                ws.cell(row=summary_start + idx, column=2, value=str(value))
    else:
        ws.cell(row=start_row, column=1, value="No data available for this report.")

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

def generate_report_data(report_type):
    """Generate report data for background processing.

    Returns a simple acknowledgement dict — the heavy lifting happens
    in generate_report_print_data() when the client actually requests
    the print / PDF / Excel output.
    """
    return {
        "message": f"{report_type.capitalize()} report generation initiated",
        "report_type": report_type,
        "timestamp": datetime.now().isoformat()
    }

# Helper function to verify JWT token
def verify_token():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, jsonify({"status": "error", "message": "No token provided"}), 401

    token = auth_header.split(' ')[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        user_data = payload.get('user')
        if not user_data:
            return None, jsonify({"status": "error", "message": "Invalid token"}), 401
        return user_data, None, None
    except jwt.ExpiredSignatureError:
        return None, jsonify({"status": "error", "message": "Token expired"}), 401
    except jwt.InvalidTokenError:
        return None, jsonify({"status": "error", "message": "Invalid token"}), 401

# Get the latest scan for a specific RFID with attendance details
@app.route("/api/get-employee-attendance/<rfid>", methods=["GET"])
def get_employee_attendance(rfid):
    """Get attendance details for a specific employee including today's time in/out"""
    rfid = rfid.strip().upper()
    employee = employee_database.get(rfid)

    # Fallback: try to find by UID if not found by RFID
    if not employee:
        for emp in employee_database.values():
            if str(emp.get("uid", "")).strip() == rfid:
                employee = emp
                break

    if not employee:
        return jsonify({
            "status": "error",
            "message": "Employee not found"
        }), 404

    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    month_key = today.strftime("%Y-%m")

    # Find attendance record for this employee
    attendance_record = None
    for record in attendance_records:
        if record.get("uid") == employee.get("uid") and record.get("month") == month_key:
            attendance_record = record
            break

    # Get today's data from DTR
    am_in = None
    am_out = None
    pm_in = None
    pm_out = None
    status = None

    if attendance_record:
        for key, day in attendance_record.get("dtr", {}).items():
            if day.get("date") == today_str:
                am_in = day.get("am_in", "")
                am_out = day.get("am_out", "")
                pm_in = day.get("pm_in", "")
                pm_out = day.get("pm_out", "")
                status = day.get("status", "")
                break

    # Build response
    response_data = {
        "status": "success",
        "employee": {
            "uid": employee.get("uid"),
            "employeeid": employee.get("employeeid"),
            "firstname": employee.get("firstname"),
            "lastname": employee.get("lastname"),
            "role": employee.get("role"),
            "image": employee.get("image", "")
        },
        "attendance": {
            "date": today_str,
            "am_in": am_in or "",
            "am_out": am_out or "",
            "pm_in": pm_in or "",
            "pm_out": pm_out or "",
            "status": status or ""
        }
    }

    return jsonify(response_data), 200

# ============================================================================
# WORK STATUS TYPES API
# ============================================================================

@app.route("/api/work-status-types", methods=["GET"])
def get_work_status_types():
    """Return the list of available work status types."""
    types_list = []
    for code, details in WORK_STATUS_TYPES.items():
        types_list.append({
            "code": code,
            "label": details["label"],
            "description": details["description"],
            "requires_time": details["requires_time"],
            "default_time_mode": details["default_time_mode"]
        })
    return jsonify({
        "status": "success",
        "data": types_list
    }), 200

# ============================================================================
# NOTIFICATION API ROUTES
# ============================================================================

@app.route("/api/notifications/<rfid>", methods=["GET"])
def get_notifications(rfid):
    """Fetch the notification document for a specific RFID."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return jsonify({"status": "error", "message": "Invalid RFID"}), 400

    # Pull employee info if we know this RFID so the doc is always current.
    employee = employee_database.get(safe_rfid)
    uid = employee.get("uid") if employee else None
    fullname = f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip() if employee else None
    role = employee.get("role") if employee else None

    doc = load_notifications(safe_rfid, uid=uid, fullname=fullname, role=role)
    return jsonify({"status": "success", "data": doc}), 200

@app.route("/api/notifications/<rfid>", methods=["POST"])
def create_notification(rfid):
    """Create a new notification for a specific RFID.

    Body (JSON):
        {
            "title": "...",
            "message": "...",
            "type": "system" | "attendance" | "work_status" | "employee"
        }
    """
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return jsonify({"status": "error", "message": "Invalid RFID"}), 400

    data = request.get_json() or {}
    title = str(data.get("title", "")).strip()
    message = str(data.get("message", "")).strip()
    notif_type = str(data.get("type", "system")).strip().lower() or "system"

    if not title or not message:
        return jsonify({"status": "error", "message": "title and message are required"}), 400

    employee = employee_database.get(safe_rfid)
    entry = push_notification(
        safe_rfid,
        title,
        message,
        notif_type=notif_type,
        uid=employee.get("uid") if employee else None,
        fullname=f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip() if employee else None,
        role=employee.get("role") if employee else None
    )

    if not entry:
        return jsonify({"status": "error", "message": "Could not create notification"}), 500

    return jsonify({"status": "success", "data": entry}), 200

@app.route("/api/notifications/<rfid>/clear", methods=["POST"])
def clear_user_notifications(rfid):
    """Clear ALL notifications for a given RFID."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return jsonify({"status": "error", "message": "Invalid RFID"}), 400

    ok = clear_notifications(safe_rfid)
    if not ok:
        return jsonify({"status": "error", "message": "Could not clear notifications"}), 500

    return jsonify({"status": "success", "message": "All notifications cleared"}), 200

@app.route("/api/notifications/<rfid>/read-all", methods=["POST"])
def mark_all_read(rfid):
    """Mark every notification for a given RFID as read."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return jsonify({"status": "error", "message": "Invalid RFID"}), 400

    ok = mark_all_notifications_read(safe_rfid)
    if not ok:
        return jsonify({"status": "error", "message": "Could not update notifications"}), 500

    return jsonify({"status": "success", "message": "All notifications marked read"}), 200

@app.route("/api/notifications/<rfid>/<notification_id>", methods=["PATCH"])
def update_notification(rfid, notification_id):
    """Mark a single notification as read."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return jsonify({"status": "error", "message": "Invalid RFID"}), 400

    ok = mark_notification_read(safe_rfid, notification_id)
    if not ok:
        return jsonify({"status": "error", "message": "Notification not found"}), 404

    return jsonify({"status": "success", "message": "Notification marked read"}), 200

@app.route("/api/notifications/<rfid>/<notification_id>", methods=["DELETE"])
def delete_single_notification(rfid, notification_id):
    """Delete a single notification entry."""
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return jsonify({"status": "error", "message": "Invalid RFID"}), 400

    ok = delete_notification(safe_rfid, notification_id)
    if not ok:
        return jsonify({"status": "error", "message": "Notification not found"}), 404

    return jsonify({"status": "success", "message": "Notification deleted"}), 200

## DTR GENERATION ROUTES ------------------------------------
# Get all employees (excluding admin and hr) for DTR selection
@app.route("/api/dtr/employees", methods=["GET"])
def get_dtr_employees():
    """Get list of all employees (excluding admin and hr) for DTR selection"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    employees = []
    for key, emp in employee_database.items():
        role = emp.get("role", "").lower()
        if role not in ["admin", "hr"]:
            employees.append({
                "uid": emp.get("uid"),
                "employeeid": emp.get("employeeid"),
                "rfid": emp.get("rfid", ""),
                "fullname": f"{emp.get('firstname', '')} {emp.get('lastname', '')}".strip(),
                "firstname": emp.get("firstname"),
                "lastname": emp.get("lastname"),
                "position": emp.get("position", ""),
                "department": emp.get("department", ""),
                "role": role
            })

    # Sort by fullname
    employees.sort(key=lambda x: x.get("fullname", ""))

    return jsonify({
        "status": "success",
        "data": employees
    }), 200

# Get attendance record for a specific employee for DTR generation
@app.route("/api/dtr/record/<rfid>", methods=["GET"])
def get_dtr_record(rfid):
    """Get attendance record for a specific employee for DTR generation"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    identifier = rfid.strip().upper()
    employee = employee_database.get(identifier)

    # Fallback: try to find by UID if not found by RFID
    if not employee:
        for emp in employee_database.values():
            if str(emp.get("uid", "")).strip() == identifier:
                employee = emp
                break

    if not employee:
        return jsonify({
            "status": "error",
            "message": "Employee not found"
        }), 404

    # Get month parameter (default to current month)
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))

    # Find attendance record for this employee and month
    attendance_record = None
    for record in attendance_records:
        if record.get("uid") == employee.get("uid") and record.get("month") == month:
            attendance_record = record
            break

    if not attendance_record:
        # Create a new record for this employee and month
        scan_date = datetime.strptime(month + "-01", "%Y-%m-%d")
        attendance_record = get_attendance_record(employee, scan_date)
        save_attendance_data()

    # Prepare the response with DTR data
    dtr_data = []
    for date_key, day_data in attendance_record.get("dtr", {}).items():
        dtr_data.append({
            "date": day_data.get("date", ""),
            "day": day_data.get("day", ""),
            "am_in": day_data.get("am_in", ""),
            "am_out": day_data.get("am_out", ""),
            "pm_in": day_data.get("pm_in", ""),
            "pm_out": day_data.get("pm_out", ""),
            "hours": day_data.get("hours", "0.00"),
            "ut": day_data.get("ut", "0.00"),
            "ot": day_data.get("ot", "0.00"),
            "status": day_data.get("status", ""),
            "work_status": day_data.get("work_status")
        })

    # Log activity
    add_activity(
        "dtr_viewed",
        f"DTR viewed for {employee.get('firstname', '')} {employee.get('lastname', '')} for {attendance_record.get('month_display', '')}",
        {"name": session.get("user", {}).get("fullname", "User")},
        "attendance"
    )

    return jsonify({
        "status": "success",
        "data": {
            "employee": {
                "uid": employee.get("uid"),
                "employeeid": employee.get("employeeid"),
                "rfid": employee.get("rfid", ""),
                "fullname": f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip(),
                "firstname": employee.get("firstname"),
                "lastname": employee.get("lastname"),
                "position": employee.get("position", ""),
                "department": employee.get("department", ""),
                "image": employee.get("image", "")
            },
            "record": {
                "id": attendance_record.get("id"),
                "month": attendance_record.get("month"),
                "month_display": attendance_record.get("month_display"),
                "total_hours": attendance_record.get("total_hours", "0.00"),
                "total_ut": attendance_record.get("total_ut", "0.00"),
                "total_ot": attendance_record.get("total_ot", "0.00"),
                "dtr": dtr_data
            }
        }
    }), 200

# Generate and download DTR as PDF
@app.route("/api/dtr/generate-pdf/<rfid>", methods=["GET"])
def generate_dtr_pdf(rfid):
    """Generate DTR PDF for a specific employee"""
    # Check if reportlab is available
    if not REPORTLAB_AVAILABLE:
        return jsonify({
            "status": "error",
            "message": "PDF generation is not available. Please install reportlab."
        }), 500

    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    identifier = rfid.strip().upper()
    employee = employee_database.get(identifier)

    # Fallback: try to find by UID if not found by RFID
    if not employee:
        for emp in employee_database.values():
            if str(emp.get("uid", "")).strip() == identifier:
                employee = emp
                break

    if not employee:
        return jsonify({
            "status": "error",
            "message": "Employee not found"
        }), 404

    # Get month parameter (default to current month)
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))

    # Find attendance record for this employee and month
    attendance_record = None
    for record in attendance_records:
        if record.get("uid") == employee.get("uid") and record.get("month") == month:
            attendance_record = record
            break

    if not attendance_record:
        scan_date = datetime.strptime(month + "-01", "%Y-%m-%d")
        attendance_record = get_attendance_record(employee, scan_date)
        save_attendance_data()

    # Create PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter),
                           rightMargin=0.5*inch, leftMargin=0.5*inch,
                           topMargin=0.5*inch, bottomMargin=0.5*inch)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='CenterTitle',
        parent=styles['Normal'],
        fontSize=14,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold',
        spaceAfter=6
    ))
    styles.add(ParagraphStyle(
        name='CenterSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        alignment=TA_CENTER,
        fontName='Helvetica',
        spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        name='CenterSmall',
        parent=styles['Normal'],
        fontSize=8,
        alignment=TA_CENTER,
        fontName='Helvetica',
        spaceAfter=2
    ))
    styles.add(ParagraphStyle(
        name='RightText',
        parent=styles['Normal'],
        fontSize=9,
        alignment=TA_RIGHT,
        fontName='Helvetica'
    ))
    styles.add(ParagraphStyle(
        name='InfoText',
        parent=styles['Normal'],
        fontSize=9,
        fontName='Helvetica',
        spaceAfter=2
    ))

    elements = []

    # Title
    elements.append(Paragraph("DAILY TIME RECORD", styles['CenterTitle']))
    elements.append(Paragraph("Civil Service Commission · Republic of the Philippines", styles['CenterSubtitle']))
    elements.append(Paragraph("CSC Form No. 48 — Revised 2018", styles['CenterSmall']))
    elements.append(Spacer(1, 0.15*inch))

    # Employee Info
    fullname = f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip()
    employee_info = [
        [Paragraph(f"<b>Employee:</b> {fullname}", styles['InfoText']),
         Paragraph(f"<b>Employee ID:</b> {employee.get('employeeid', '')}", styles['InfoText'])],
        [Paragraph(f"<b>Position:</b> {employee.get('position', '')}", styles['InfoText']),
         Paragraph(f"<b>Department:</b> {employee.get('department', '')}", styles['InfoText'])],
        [Paragraph(f"<b>Month:</b> {attendance_record.get('month_display', '')}", styles['InfoText']),
         Paragraph(f"<b>RFID:</b> {employee.get('rfid', '')}", styles['InfoText'])]
    ]

    info_table = Table(employee_info, colWidths=[4.5*inch, 4.5*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.15*inch))

    # DTR Table
    dtr_data = []
    header = ['Day', 'Date', 'AM In', 'AM Out', 'PM In', 'PM Out', 'Hours', 'UT', 'OT', 'Status']
    dtr_data.append(header)

    for date_key, day in attendance_record.get("dtr", {}).items():
        # Determine display status
        display_status = day.get("status", "")
        if day.get("work_status"):
            ws = day["work_status"]
            display_status = f"Work Status ({ws.get('period', 'whole_day').replace('_', ' ')})"

        row = [
            day.get("day", ""),
            day.get("date", ""),
            day.get("am_in", ""),
            day.get("am_out", ""),
            day.get("pm_in", ""),
            day.get("pm_out", ""),
            day.get("hours", "0.00"),
            day.get("ut", "0.00"),
            day.get("ot", "0.00"),
            display_status
        ]
        dtr_data.append(row)

    # Add totals row
    dtr_data.append([
        "TOTALS", "", "", "", "", "",
        attendance_record.get("total_hours", "0.00"),
        attendance_record.get("total_ut", "0.00"),
        attendance_record.get("total_ot", "0.00"),
        ""
    ])

    # Create table with column widths - landscape gives more room
    col_widths = [0.5*inch, 0.9*inch, 0.65*inch, 0.65*inch, 0.65*inch, 0.65*inch, 0.6*inch, 0.5*inch, 0.5*inch, 0.7*inch]
    dtr_table = Table(dtr_data, colWidths=col_widths, repeatRows=1)

    # Style the table
    table_style = TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.black),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 7),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, -1), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ])

    # Color rows that are on work status
    for i, row in enumerate(dtr_data[1:], start=1):
        if len(row) > 9 and row[9] == "on_work_status":
            table_style.add('BACKGROUND', (0, i), (-1, i), colors.yellow)

    dtr_table.setStyle(table_style)
    elements.append(dtr_table)
    elements.append(Spacer(1, 0.2*inch))

    # Signature lines
    sig_data = [
        ['', '', ''],
        ['______________________', '______________________', '______________________'],
        ['Employee Signature', 'Prepared By', 'Approved By'],
        ['', 'HR Officer', 'Department Head']
    ]
    sig_table = Table(sig_data, colWidths=[2.7*inch, 2.7*inch, 2.7*inch])
    sig_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),
    ]))
    elements.append(sig_table)

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    # Log activity
    add_activity(
        "dtr_generated",
        f"DTR PDF generated for {employee.get('firstname', '')} {employee.get('lastname', '')} for {attendance_record.get('month_display', '')}",
        {"name": session.get("user", {}).get("fullname", "User")},
        "attendance"
    )

    # Return PDF
    filename = f"DTR_{employee.get('lastname', 'unknown')}_{employee.get('firstname', 'unknown')}_{month}.pdf"
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'

    return response

# Get available months for DTR
@app.route("/api/dtr/months", methods=["GET"])
def get_dtr_months():
    """Get list of available months with attendance records"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    months = set()
    for record in attendance_records:
        month = record.get("month")
        month_display = record.get("month_display")
        if month and month_display:
            months.add((month, month_display))

    # Sort by month descending (newest first)
    sorted_months = sorted(list(months), key=lambda x: x[0], reverse=True)

    # If no months exist, add current month
    if not sorted_months:
        current_month = datetime.now().strftime("%Y-%m")
        current_display = datetime.now().strftime("%B %Y")
        sorted_months = [(current_month, current_display)]

    return jsonify({
        "status": "success",
        "data": [{"value": m[0], "label": m[1]} for m in sorted_months]
    }), 200

## REPORT GENERATION ROUTES ------------------------------------
# NOTE: The old /api/reports/<report_type>/preview route has been REMOVED.
# The front-end no longer exposes a Preview button — Print, PDF, Excel,
# and Generate are the only report actions now.

# Report print data — real data, used by the front-end "Print" button
@app.route("/api/reports/<report_type>/print", methods=["GET"])
def print_report(report_type):
    """Generate print data for a report using REAL database data."""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Accept both "leave" (legacy) and "work-status" (new alias) — both map
    # to the same Work Status report branch inside generate_report_print_data().
    report_type = "leave" if report_type == "work-status" else report_type
    valid_reports = ["daily", "weekly", "monthly", "yearly", "summary", "absent", "leave", "rfid_logs"]
    if report_type not in valid_reports:
        return jsonify({
            "status": "error",
            "message": f"Invalid report type. Valid types are: {', '.join(valid_reports)}"
        }), 400

    try:
        # Generate report print data with REAL rows pulled from the database
        print_data = generate_report_print_data(report_type)
        return jsonify({
            "status": "success",
            "data": print_data
        }), 200
    except Exception as e:
        print(f"Error generating report print data: {e}")
        return jsonify({
            "status": "error",
            "message": "Failed to generate report print data"
        }), 500

# Report PDF — real data, used by the front-end "PDF" button
@app.route("/api/reports/<report_type>/pdf", methods=["GET"])
def generate_report_pdf(report_type):
    """Generate PDF report using REAL database data."""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Accept both "leave" (legacy) and "work-status" (new alias) — both map
    # to the same Work Status report branch inside generate_report_print_data().
    report_type = "leave" if report_type == "work-status" else report_type
    valid_reports = ["daily", "weekly", "monthly", "yearly", "summary", "absent", "leave", "rfid_logs"]
    if report_type not in valid_reports:
        return jsonify({
            "status": "error",
            "message": f"Invalid report type. Valid types are: {', '.join(valid_reports)}"
        }), 400

    # Check if reportlab is available
    if not REPORTLAB_AVAILABLE:
        return jsonify({
            "status": "error",
            "message": "PDF generation is not available. Please install reportlab."
        }), 500

    try:
        # Generate PDF report from real data
        pdf_buffer = generate_report_pdf_buffer(report_type)

        # Log activity
        add_activity(
            "report_pdf_generated",
            f"{report_type.capitalize()} report PDF generated",
            {"name": session.get("user", {}).get("fullname", "User")},
            "report"
        )

        # Return PDF
        filename = f"{report_type}-report-{datetime.now().strftime('%Y%m%d')}.pdf"
        response = make_response(pdf_buffer.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response
    except Exception as e:
        print(f"Error generating PDF report: {e}")
        return jsonify({
            "status": "error",
            "message": "Failed to generate PDF report"
        }), 500

# Report Excel — real data, used by the front-end "Excel" button
@app.route("/api/reports/<report_type>/excel", methods=["GET"])
def generate_report_excel(report_type):
    """Generate Excel report using REAL database data."""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Accept both "leave" (legacy) and "work-status" (new alias) — both map
    # to the same Work Status report branch inside generate_report_print_data().
    report_type = "leave" if report_type == "work-status" else report_type
    valid_reports = ["daily", "weekly", "monthly", "yearly", "summary", "absent", "leave", "rfid_logs"]
    if report_type not in valid_reports:
        return jsonify({
            "status": "error",
            "message": f"Invalid report type. Valid types are: {', '.join(valid_reports)}"
        }), 400

    try:
        # Generate Excel report from real data
        excel_buffer = generate_report_excel_buffer(report_type)

        # Log activity
        add_activity(
            "report_excel_generated",
            f"{report_type.capitalize()} report Excel generated",
            {"name": session.get("user", {}).get("fullname", "User")},
            "report"
        )

        # Return Excel file
        filename = f"{report_type}-report-{datetime.now().strftime('%Y%m%d')}.xlsx"
        response = make_response(excel_buffer.getvalue())
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response
    except Exception as e:
        print(f"Error generating Excel report: {e}")
        return jsonify({
            "status": "error",
            "message": "Failed to generate Excel report"
        }), 500

# Report generate (trigger background generation)
@app.route("/api/reports/<report_type>/generate", methods=["POST"])
def generate_report(report_type):
    """Trigger report generation"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Accept both "leave" (legacy) and "work-status" (new alias) — both map
    # to the same Work Status report branch inside generate_report_print_data().
    report_type = "leave" if report_type == "work-status" else report_type
    valid_reports = ["daily", "weekly", "monthly", "yearly", "summary", "absent", "leave", "rfid_logs"]
    if report_type not in valid_reports:
        return jsonify({
            "status": "error",
            "message": f"Invalid report type. Valid types are: {', '.join(valid_reports)}"
        }), 400

    try:
        # Generate report data
        report_data = generate_report_data(report_type)

        # Log activity
        add_activity(
            "report_generated",
            f"{report_type.capitalize()} report generated",
            {"name": session.get("user", {}).get("fullname", "User")},
            "report"
        )

        return jsonify({
            "status": "success",
            "message": f"{report_type.capitalize()} report generated successfully",
            "data": report_data
        }), 200
    except Exception as e:
        print(f"Error generating report: {e}")
        return jsonify({
            "status": "error",
            "message": "Failed to generate report"
        }), 500

## WORK STATUS MANAGEMENT ROUTES ------------------------------------
# Request work status
@app.route("/api/request-work-status", methods=["POST"])
def request_work_status():
    """Submit a new work status request.

    Form data:
        rfid          - Employee RFID
        start_date    - Start date (YYYY-MM-DD)
        end_date      - End date (YYYY-MM-DD)
        reason        - Reason for the request
        work_status_type - Type of work status (from WORK_STATUS_TYPES)
        period        - "whole_day" | "am" | "pm" (default: whole_day)
        start_time    - Optional specific start time (HH:MM)
        end_time      - Optional specific end time (HH:MM)
        attachment    - Optional file attachment
    """
    try:
        # Handle both JSON and form data (for file uploads)
        if request.content_type and 'application/json' in request.content_type:
            data = request.get_json()
        else:
            data = request.form.to_dict()

        if not data:
            return jsonify({"status": "error", "message": "Missing data"}), 400

        required = ["rfid", "start_date", "end_date", "reason", "work_status_type"]
        if not all(key in data for key in required):
            return jsonify({
                "status": "error",
                "message": "Missing required fields: rfid, start_date, end_date, reason, work_status_type"
            }), 400

        rfid = data["rfid"].strip().upper()
        employee = employee_database.get(rfid)

        # Fallback: try to find by UID if not found by RFID
        if not employee:
            for emp in employee_database.values():
                if str(emp.get("uid", "")).strip() == rfid:
                    employee = emp
                    break

        if not employee:
            return jsonify({"status": "error", "message": "Employee not found"}), 404

        # Validate work status type
        work_status_type = data["work_status_type"].strip().lower()
        if work_status_type not in WORK_STATUS_TYPES:
            return jsonify({
                "status": "error",
                "message": f"Invalid work status type. Valid types: {', '.join(WORK_STATUS_TYPES.keys())}"
            }), 400

        # Get the period (whole_day, am, pm)
        period = data.get("period", "whole_day").strip().lower()
        if period not in ["whole_day", "am", "pm"]:
            period = "whole_day"

        # Get optional specific times
        start_time = data.get("start_time", "").strip()
        end_time = data.get("end_time", "").strip()

        # Validate optional specific times — must be "HH:MM" if provided.
        # We do this BEFORE the date validation block so bad input is rejected
        # early with a clear message.
        _hhmm = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
        if start_time and not _hhmm.match(start_time):
            return jsonify({
                "status": "error",
                "message": "start_time must be in HH:MM format (24-hour)"
            }), 400
        if end_time and not _hhmm.match(end_time):
            return jsonify({
                "status": "error",
                "message": "end_time must be in HH:MM format (24-hour)"
            }), 400
        if start_time and end_time and start_time >= end_time:
            return jsonify({
                "status": "error",
                "message": "start_time must be earlier than end_time"
            }), 400

        # Validate that leave dates are not in the past
        try:
            start_date = datetime.strptime(data["start_date"], "%Y-%m-%d")
            end_date = datetime.strptime(data["end_date"], "%Y-%m-%d")
            today = datetime.now().date()

            if start_date.date() < today:
                return jsonify({
                    "status": "error",
                    "message": "Work status requests cannot be submitted for past dates"
                }), 400

            if end_date.date() < today:
                return jsonify({
                    "status": "error",
                    "message": "Work status requests cannot be submitted for past dates"
                }), 400

            if start_date > end_date:
                return jsonify({
                    "status": "error",
                    "message": "Start date cannot be after end date"
                }), 400
        except ValueError:
            return jsonify({
                "status": "error",
                "message": "Invalid date format. Please use YYYY-MM-DD"
            }), 400

        # Handle file upload
        attachment_path = None
        if 'attachment' in request.files:
            attachment_file = request.files['attachment']
            if attachment_file and attachment_file.filename:
                # Validate file extension
                allowed_extensions = {'.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.txt'}
                extension = os.path.splitext(attachment_file.filename)[1].lower()
                if extension not in allowed_extensions:
                    return jsonify({
                        "status": "error",
                        "message": "File must be PDF, DOC, DOCX, JPG, JPEG, PNG, GIF, WEBP, or TXT"
                    }), 400

                # Create employee-specific directory
                employee_work_status_dir = os.path.join(WORK_STATUS_REQUEST_STORAGE, rfid)
                os.makedirs(employee_work_status_dir, exist_ok=True)

                # Generate filename with date + RFID + a short random suffix.
                # Without the suffix, two requests filed on the same day by the
                # same employee would overwrite each other's attachment.
                date_today = datetime.now().strftime("%Y%m%d")
                unique_suffix = secrets.token_hex(3)  # 6 hex chars
                secure_filename_base = secure_filename(rfid)
                filename = f"{date_today}_{secure_filename_base}_{unique_suffix}{extension}"
                file_path = os.path.join(employee_work_status_dir, filename)

                # Save the file
                attachment_file.save(file_path)

                # Store relative path for web access
                attachment_path = os.path.join("storage", "work-status", rfid, filename).replace(os.sep, "/")

        # Build a request ID that won't collide with existing entries.
        existing_ids = []
        for req in work_status_data.get("requests", []) + work_status_data.get("approved", []) + work_status_data.get("rejected", []):
            try:
                existing_ids.append(int(req.get("id", "0")))
            except (ValueError, TypeError):
                pass
        request_id = str(max(existing_ids + [0]) + 1).zfill(3)

        work_status_request = {
            "id": request_id,
            "rfid": rfid,
            "uid": employee.get("uid"),
            "employeeid": employee.get("employeeid"),
            "fullname": f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip(),
            "department": employee.get("department", ""),
            "work_status_type": work_status_type,
            "work_status_label": WORK_STATUS_TYPES[work_status_type]["label"],
            "period": period,
            "start_time": start_time,
            "end_time": end_time,
            "start_date": data["start_date"],
            "end_date": data["end_date"],
            "reason": data.get("reason", ""),
            "attachment_path": attachment_path,
            "status": "pending",
            "requested_at": datetime.now().isoformat(),
            "processed_at": None,
            "processed_by": None,
            "days": []
        }

        start_date = datetime.strptime(data["start_date"], "%Y-%m-%d")
        end_date = datetime.strptime(data["end_date"], "%Y-%m-%d")

        current_date = start_date
        while current_date <= end_date:
            if current_date.weekday() < 5:
                work_status_request["days"].append(current_date.strftime("%Y-%m-%d"))
            current_date += timedelta(days=1)

        # Append to the persistent structure and mirror to BOTH files.
        work_status_data["requests"].append(work_status_request)
        save_both_work_status_files(work_status_data)

        # Log activity
        add_activity(
            "work_status_requested",
            f"{employee.get('firstname', '')} {employee.get('lastname', '')} requested {WORK_STATUS_TYPES[work_status_type]['label']} from {data['start_date']} to {data['end_date']}",
            {"name": f"{employee.get('firstname', '')} {employee.get('lastname', '')}", "uid": employee.get('uid')},
            "work_status"
        )

        # Notify the employee that their request was received.
        try:
            push_notification(
                rfid,
                "Work Status Request Submitted",
                f"Your {WORK_STATUS_TYPES[work_status_type]['label']} request from {data['start_date']} to {data['end_date']} is now pending approval.",
                notif_type="work_status",
                uid=employee.get("uid"),
                fullname=f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip(),
                role=employee.get("role")
            )
        except Exception as e:
            print(f"Warning: failed to push work status-request notification: {e}")

        return jsonify({
            "status": "success",
            "message": "Work status request submitted successfully",
            "data": work_status_request
        }), 200

    except Exception as e:
        print(f"Work status request error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Get all work status requests (for HR/Admin)
@app.route("/api/work-status-requests", methods=["GET"])
def get_work_status_requests():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Persistent file is the source of truth — the feed mirrors it.
    return jsonify({
        "status": "success",
        "data": {
            "requests": work_status_data.get("requests", []),
            "approved": work_status_data.get("approved", []),
            "rejected": work_status_data.get("rejected", []),
        }
    }), 200

# Get work status requests for a specific employee
@app.route("/api/work-status-requests/<rfid>", methods=["GET"])
def get_employee_work_status_requests(rfid):
    # Require a valid session OR bearer token — same guard as every other
    # authenticated endpoint. Without this, anyone who knows an RFID can list
    # that employee's work status request history.
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
        caller_role = user_data.get("role", "employee")
        caller_rfid = user_data.get("rfid")
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401
        caller_role = session.get("user", {}).get("role", "employee")
        caller_rfid = session.get("user", {}).get("rfid")

    identifier = rfid.strip().upper()

    # Try RFID lookup first
    employee = employee_database.get(identifier)

    # Fallback: try to find by UID
    if not employee:
        for emp in employee_database.values():
            if str(emp.get("uid", "")).strip() == identifier:
                employee = emp
                break

    if not employee:
        return jsonify({"status": "error", "message": "Employee not found"}), 404

    # Authorization: only HR/Admin or the employee themselves can read the list.
    if caller_role not in ("admin", "hr") and caller_rfid != employee.get("rfid"):
        return jsonify({
            "status": "error",
            "message": "Unauthorized to view these work status requests"
        }), 403

    # Use the employee's UID for filtering so it works with both RFID and UID lookups
    uid = employee.get("uid")

    employee_requests = [
        req for req in work_status_data.get("requests", []) if req.get("uid") == uid
    ]
    employee_approved = [
        req for req in work_status_data.get("approved", []) if req.get("uid") == uid
    ]
    employee_rejected = [
        req for req in work_status_data.get("rejected", []) if req.get("uid") == uid
    ]

    return jsonify({
        "status": "success",
        "data": {
            "requests": employee_requests,
            "approved": employee_approved,
            "rejected": employee_rejected
        }
    }), 200

# ============================================================================
# WORK STATUS ATTACHMENT PREVIEW / METADATA
# ============================================================================
# These two routes power the front-end "View Attachment" modal.
#
#   GET /api/work-status-attachment/meta/<rfid>/<filename>
#       Returns JSON metadata about a single uploaded work status file:
#       filename, size in bytes, size_human, extension, content_type,
#       and a `previewable` flag that tells the UI whether an inline
#       preview is possible in the browser (images + PDFs).
#
#   GET /storage/work-status/<rfid>/<filename>?inline=1
#       Streams the file with Content-Disposition: inline so the browser
#       renders images/PDFs directly instead of downloading them.
#       Without ?inline=1 the same route falls back to an attachment
#       download so existing links keep working.

# Map of file extensions to MIME types used by the metadata route.
_WORK_STATUS_FILE_MIME_MAP = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
}

# Extensions the browser can render inline.
_WORK_STATUS_PREVIEWABLE_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".txt"}

def _human_size(num_bytes):
    """Return a compact human-readable size string (e.g. "128 KB")."""
    try:
        num_bytes = int(num_bytes)
    except Exception:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"

@app.route("/api/work-status-attachment/meta/<rfid>/<filename>", methods=["GET"])
def get_work_status_attachment_meta(rfid, filename):
    """Return JSON metadata for a single uploaded work status file.

    Response shape:
        {
            "status": "success",
            "data": {
                "filename": "20260922_FB822A54.pdf",
                "size": 34812,
                "size_human": "34.0 KB",
                "extension": ".pdf",
                "content_type": "application/pdf",
                "previewable": true,
                "url": "/storage/work-status/FB822A54/20260922_FB822A54.pdf",
                "inline_url": "/storage/work-status/FB822A54/20260922_FB822A54.pdf?inline=1"
            }
        }
    """
    safe_rfid = _sanitize_rfid(rfid)
    safe_filename = secure_filename(filename)

    if not safe_rfid or not safe_filename:
        return jsonify({"status": "error", "message": "Invalid path"}), 400

    file_path = os.path.join(WORK_STATUS_REQUEST_STORAGE, safe_rfid, safe_filename)

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return jsonify({"status": "error", "message": "File not found"}), 404

    try:
        size = os.path.getsize(file_path)
    except Exception:
        size = 0

    extension = os.path.splitext(safe_filename)[1].lower()
    content_type = _WORK_STATUS_FILE_MIME_MAP.get(extension, "application/octet-stream")
    previewable = extension in _WORK_STATUS_PREVIEWABLE_EXTS

    rel_url = f"/storage/work-status/{safe_rfid}/{safe_filename}"

    return jsonify({
        "status": "success",
        "data": {
            "filename": safe_filename,
            "size": size,
            "size_human": _human_size(size),
            "extension": extension,
            "content_type": content_type,
            "previewable": previewable,
            "url": rel_url,
            "inline_url": f"{rel_url}?inline=1",
        }
    }), 200

# Approve work status request
@app.route("/api/approve-work-status/<request_id>", methods=["POST"])
def approve_work_status(request_id):
    """Approve a work status request.

    When approving, the request can optionally be scoped to a specific
    time period (whole_day, am, pm). This allows the HR/Admin to approve
    only the morning or afternoon portion of a day.

    JSON body (optional):
        {
            "period": "whole_day" | "am" | "pm",
            "start_time": "08:00",  # optional for specific time
            "end_time": "12:00"     # optional for specific time
        }

    If no body is provided, the request's original period is used.
    """
    try:
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            user_data, error_response, status_code = verify_token()
            if error_response:
                return error_response, status_code
        else:
            if not session.get("user"):
                return jsonify({
                    "status": "error",
                    "message": "Session expired or user is not logged in"
                }), 401

        # Read optional period from request body
        body = request.get_json(silent=True) or {}
        requested_period = body.get("period", "").strip().lower()
        requested_start_time = body.get("start_time", "").strip()
        requested_end_time = body.get("end_time", "").strip()

        request_to_approve = None
        request_index = -1

        for idx, req in enumerate(work_status_data["requests"]):
            if req.get("id") == request_id:
                request_to_approve = req
                request_index = idx
                break

        if not request_to_approve:
            return jsonify({"status": "error", "message": "Work status request not found"}), 404

        # Determine the period to use:
        # 1. If HR/Admin explicitly specified a period, use that
        # 2. Otherwise, use the request's original period
        if requested_period in ["whole_day", "am", "pm"]:
            period = requested_period
        else:
            period = request_to_approve.get("period", "whole_day")

        # Update optional specific times
        if requested_start_time:
            request_to_approve["start_time"] = requested_start_time
        if requested_end_time:
            request_to_approve["end_time"] = requested_end_time

        request_to_approve["period"] = period
        request_to_approve["status"] = "approved"
        request_to_approve["processed_at"] = datetime.now().isoformat()
        request_to_approve["processed_by"] = user_data.get("fullname") or user_data.get("username")

        work_status_data["approved"].append(request_to_approve)
        work_status_data["requests"].pop(request_index)

        # Update attendance records for the approved work status days
        uid = request_to_approve.get("uid")
        work_status_type = request_to_approve.get("work_status_type")
        work_status_label = WORK_STATUS_TYPES.get(work_status_type, {}).get("label", work_status_type)

        # Normalize: if the request had no explicit start_time/end_time, keep
        # them as "" instead of None so the frontend (which treats "" as
        # "no specific time") doesn't accidentally show "None" in the detail
        # view. This also prevents a stale time from a previous approval on
        # the same request from leaking in.
        start_time_final = (request_to_approve.get("start_time") or "").strip()
        end_time_final = (request_to_approve.get("end_time") or "").strip()

        for date_str in request_to_approve.get("days", []):
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            month_key = date_obj.strftime("%Y-%m")

            for record in attendance_records:
                if record.get("uid") == uid and record.get("month") == month_key:
                    for key, day in record.get("dtr", {}).items():
                        if day.get("date") == date_str:
                            # Set the status
                            day["status"] = "on_work_status"

                            # Store the work status details
                            day["work_status"] = {
                                "type": work_status_type,
                                "label": work_status_label,
                                "period": period,
                                "start_time": start_time_final,
                                "end_time": end_time_final,
                                "is_specific_time": period in ["am", "pm"],
                                "request_id": request_id,
                                "is_active": True,
                                "approved_by": request_to_approve.get("processed_by"),
                                "approved_at": request_to_approve.get("processed_at")
                            }

                            # For whole-day work status, clear all times
                            if period == "whole_day":
                                day["am_in"] = ""
                                day["am_out"] = ""
                                day["pm_in"] = ""
                                day["pm_out"] = ""
                                day["hours"] = "0.00"
                                day["ut"] = "0.00"
                                day["ot"] = "0.00"
                                day["am_in_24"] = ""
                                day["am_out_24"] = ""
                                day["pm_in_24"] = ""
                                day["pm_out_24"] = ""
                                print(f"Marked {date_str} as WHOLE DAY WORK STATUS ({work_status_label}) for {request_to_approve.get('fullname')}")

                            # For specific-time work status (AM or PM),
                            # mark only that period as affected.
                            # The DTR will highlight the affected period in yellow.
                            elif period == "am":
                                # Keep PM times if they exist
                                day["_am_work_status"] = {
                                    "type": work_status_type,
                                    "label": work_status_label,
                                    "start_time": start_time_final,
                                    "end_time": end_time_final
                                }
                                print(f"Marked {date_str} AM period as WORK STATUS ({work_status_label}) for {request_to_approve.get('fullname')}")
                            elif period == "pm":
                                # Keep AM times if they exist
                                day["_pm_work_status"] = {
                                    "type": work_status_type,
                                    "label": work_status_label,
                                    "start_time": start_time_final,
                                    "end_time": end_time_final
                                }
                                print(f"Marked {date_str} PM period as WORK STATUS ({work_status_label}) for {request_to_approve.get('fullname')}")

                            break
                    break

        # Mirror to BOTH work status files.
        save_both_work_status_files(work_status_data)
        save_attendance_data()

        # Log activity
        add_activity(
            "work_status_approved",
            f"Work status request #{request_id} for {request_to_approve.get('fullname', '')} was approved by {user_data.get('fullname') or user_data.get('username')} ({work_status_label}, {period})",
            {"name": user_data.get('fullname') or user_data.get('username'), "uid": user_data.get('uid')},
            "work_status"
        )

        # Notify the employee that their request was approved.
        try:
            push_notification(
                request_to_approve.get("rfid"),
                "Work Status Request Approved",
                f"Your {work_status_label} request from {request_to_approve.get('start_date', '')} to {request_to_approve.get('end_date', '')} has been approved ({period.replace('_', ' ')}).",
                notif_type="work_status",
                uid=request_to_approve.get("uid"),
                fullname=request_to_approve.get("fullname"),
                role="employee"
            )
        except Exception as e:
            print(f"Warning: failed to push work status-approved notification: {e}")

        return jsonify({
            "status": "success",
            "message": "Work status request approved successfully",
            "data": request_to_approve
        }), 200

    except Exception as e:
        print(f"Approve work status error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Reject work status request
@app.route("/api/reject-work-status/<request_id>", methods=["POST"])
def reject_work_status(request_id):
    try:
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            user_data, error_response, status_code = verify_token()
            if error_response:
                return error_response, status_code
        else:
            if not session.get("user"):
                return jsonify({
                    "status": "error",
                    "message": "Session expired or user is not logged in"
                }), 401

        request_to_reject = None
        request_index = -1

        for idx, req in enumerate(work_status_data["requests"]):
            if req.get("id") == request_id:
                request_to_reject = req
                request_index = idx
                break

        if not request_to_reject:
            return jsonify({"status": "error", "message": "Work status request not found"}), 404

        request_to_reject["status"] = "rejected"
        request_to_reject["processed_at"] = datetime.now().isoformat()
        request_to_reject["processed_by"] = user_data.get("fullname") or user_data.get("username")

        work_status_data["rejected"].append(request_to_reject)
        work_status_data["requests"].pop(request_index)

        # Mirror to BOTH work status files.
        save_both_work_status_files(work_status_data)

        # Log activity
        add_activity(
            "work_status_rejected",
            f"Work status request #{request_id} for {request_to_reject.get('fullname', '')} was rejected by {user_data.get('fullname') or user_data.get('username')}",
            {"name": user_data.get('fullname') or user_data.get('username'), "uid": user_data.get('uid')},
            "work_status"
        )

        # Notify the employee that their request was rejected.
        try:
            push_notification(
                request_to_reject.get("rfid"),
                "Work Status Request Rejected",
                f"Your {request_to_reject.get('work_status_label', 'work status')} request from {request_to_reject.get('start_date', '')} to {request_to_reject.get('end_date', '')} has been rejected.",
                notif_type="work_status",
                uid=request_to_reject.get("uid"),
                fullname=request_to_reject.get("fullname"),
                role="employee"
            )
        except Exception as e:
            print(f"Warning: failed to push work status-rejected notification: {e}")

        return jsonify({
            "status": "success",
            "message": "Work status request rejected",
            "data": request_to_reject
        }), 200

    except Exception as e:
        print(f"Reject work status error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

## Settings Routes ------------------------------------
# Update settings
@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Get current system settings"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Check for version update from GitHub
    github_version = fetch_version_from_github()
    current_version = settings.get("system", {}).get("version", "1.0.0")

    # Return settings with version info
    response_data = settings.copy()
    response_data["system"] = response_data.get("system", {}).copy()
    response_data["system"]["version"] = current_version
    response_data["system"]["github_version"] = github_version
    response_data["system"]["version_url"] = settings.get("system", {}).get("version_url", "")

    return jsonify({
        "status": "success",
        "data": response_data
    }), 200

# Update settings
@app.route("/api/settings", methods=["PUT"])
def update_settings():
    """Update system settings"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "status": "error",
                "message": "No data provided"
            }), 400

        # Update attendance settings
        if "attendance" in data:
            for key in ["work_start", "work_end", "lunch_start", "lunch_end", "grace_period"]:
                if key in data["attendance"]:
                    settings["attendance"][key] = data["attendance"][key]

        # Update institution settings
        if "institution" in data:
            for key in ["name", "system_name", "academic_year", "hr_email"]:
                if key in data["institution"]:
                    settings["institution"][key] = data["institution"][key]

        # Update system settings (except version which is auto-managed)
        if "system" in data:
            if "version_url" in data["system"]:
                settings["system"]["version_url"] = data["system"]["version_url"]

        # Save settings
        save_settings(settings)

        # Log activity
        add_activity(
            "settings_updated",
            "System settings were updated",
            {"name": session.get("user", {}).get("fullname", "User")},
            "system"
        )

        return jsonify({
            "status": "success",
            "message": "Settings updated successfully",
            "data": settings
        }), 200

    except Exception as e:
        print(f"Error updating settings: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# Check for version update
@app.route("/api/settings/check-version", methods=["GET"])
def check_version():
    """Check for newer version from GitHub"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    github_version = fetch_version_from_github()
    current_version = settings.get("system", {}).get("version", "1.0.0")

    is_newer = False
    if github_version:
        try:
            # Simple version comparison
            current_parts = current_version.split('.')
            github_parts = github_version.split('.')

            # Pad to same length
            while len(current_parts) < len(github_parts):
                current_parts.append('0')
            while len(github_parts) < len(current_parts):
                github_parts.append('0')

            for i in range(len(current_parts)):
                if int(github_parts[i]) > int(current_parts[i]):
                    is_newer = True
                    break
                elif int(github_parts[i]) < int(current_parts[i]):
                    break
        except:
            is_newer = github_version != current_version

    return jsonify({
        "status": "success",
        "data": {
            "current_version": current_version,
            "github_version": github_version,
            "is_newer_available": is_newer,
            "version_url": settings.get("system", {}).get("version_url", "")
        }
    }), 200

# Get employee-specific settings
@app.route("/api/settings/<rfid>", methods=["GET"])
def get_employee_settings(rfid):
    """Get settings for a specific employee"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Authorization: users can only access their own settings unless they are admin/hr
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return jsonify({"status": "error", "message": "Invalid RFID"}), 400

    # Check if requesting user is authorized to access this RFID's settings
    current_user_rfid = None
    current_user_role = "employee"
    if auth_header and auth_header.startswith('Bearer '):
        user_data, _, _ = verify_token()
        if user_data:
            current_user_rfid = user_data.get('rfid')
            current_user_role = user_data.get('role', 'employee')
    elif session.get("user"):
        current_user_rfid = session.get("user", {}).get('rfid')
        current_user_role = session.get("user", {}).get('role', 'employee')

    # Allow access if: user is requesting their own settings, or user is admin/hr
    if current_user_rfid != safe_rfid and current_user_role not in ['admin', 'hr']:
        return jsonify({
            "status": "error",
            "message": "Unauthorized to access these settings"
        }), 403

    try:
        # Get employee data for context
        employee = employee_database.get(safe_rfid)
        uid = employee.get("uid") if employee else None
        fullname = f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip() if employee else None
        role = employee.get("role") if employee else None

        # Load employee-specific settings
        employee_settings_doc = load_employee_settings(safe_rfid, uid=uid, fullname=fullname, role=role)

        # Merge with system settings (employee settings override system settings)
        merged_settings = {}
        # Start with system settings as base
        for key in ["attendance", "institution", "system"]:
            if key in settings:
                merged_settings[key] = settings[key].copy()

        # Override with employee-specific settings
        if "settings" in employee_settings_doc and isinstance(employee_settings_doc["settings"], dict):
            for key in employee_settings_doc["settings"]:
                # Handle nested keys like "attendance.work_start"
                if "." in key:
                    keys = key.split(".")
                    target = merged_settings
                    try:
                        # Navigate to the parent of the target key
                        for k in keys[:-1]:
                            if k not in target:
                                target[k] = {}
                            target = target[k]
                        # Set the value
                        target[keys[-1]] = employee_settings_doc["settings"][key]
                    except Exception:
                        # If there's any error in nested setting, skip it
                        pass
                else:
                    # Direct key override
                    # Determine which section it belongs to
                    if key in ["work_start", "work_end", "lunch_start", "lunch_end", "grace_period"]:
                        if "attendance" not in merged_settings:
                            merged_settings["attendance"] = {}
                        merged_settings["attendance"][key] = employee_settings_doc["settings"][key]
                    elif key in ["name", "system_name", "academic_year", "hr_email"]:
                        if "institution" not in merged_settings:
                            merged_settings["institution"] = {}
                        merged_settings["institution"][key] = employee_settings_doc["settings"][key]
                    elif key in ["version", "version_url"]:
                        if "system" not in merged_settings:
                            merged_settings["system"] = {}
                        merged_settings["system"][key] = employee_settings_doc["settings"][key]
                    else:
                        # Unknown key, store in system section for now
                        if "system" not in merged_settings:
                            merged_settings["system"] = {}
                        merged_settings["system"][key] = employee_settings_doc["settings"][key]

        return jsonify({
            "status": "success",
            "data": {
                "rfid": safe_rfid,
                "settings": merged_settings,
                "employee": {
                    "uid": uid,
                    "fullname": fullname,
                    "role": role
                } if employee else None,
                "last_updated": employee_settings_doc.get("last_updated")
            }
        }), 200
    except Exception as e:
        print(f"Error getting employee settings: {e}")
        return jsonify({
            "status": "error",
            "message": "Failed to retrieve employee settings"
        }), 500

# Update employee-specific settings
@app.route("/api/settings/<rfid>", methods=["PUT"])
def update_employee_settings(rfid):
    """Update settings for a specific employee"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Authorization: users can only update their own settings unless they are admin/hr
    safe_rfid = _sanitize_rfid(rfid)
    if not safe_rfid:
        return jsonify({"status": "error", "message": "Invalid RFID"}), 400

    # Check if requesting user is authorized to access this RFID's settings
    current_user_rfid = None
    current_user_role = "employee"
    if auth_header and auth_header.startswith('Bearer '):
        user_data, _, _ = verify_token()
        if user_data:
            current_user_rfid = user_data.get('rfid')
            current_user_role = user_data.get('role', 'employee')
    elif session.get("user"):
        current_user_rfid = session.get("user", {}).get('rfid')
        current_user_role = session.get("user", {}).get('role', 'employee')

    # Allow access if: user is requesting their own settings, or user is admin/hr
    if current_user_rfid != safe_rfid and current_user_role not in ['admin', 'hr']:
        return jsonify({
            "status": "error",
            "message": "Unauthorized to update these settings"
        }), 403

    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "status": "error",
                "message": "No data provided"
            }), 400

        # Get employee data for context
        employee = employee_database.get(safe_rfid)
        uid = employee.get("uid") if employee else None
        fullname = f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip() if employee else None
        role = employee.get("role") if employee else None

        # Load existing employee settings
        employee_settings_doc = load_employee_settings(safe_rfid, uid=uid, fullname=fullname, role=role)

        # Update settings - store as employee-specific overrides
        if "settings" not in employee_settings_doc:
            employee_settings_doc["settings"] = {}

        # Flatten the incoming data for storage as employee-specific overrides
        # We'll store user preferences as flat keys for simplicity
        if "attendance" in data:
            for key in ["work_start", "work_end", "lunch_start", "lunch_end", "grace_period"]:
                if key in data["attendance"]:
                    employee_settings_doc["settings"][key] = data["attendance"][key]

        if "institution" in data:
            for key in ["name", "system_name", "academic_year", "hr_email"]:
                if key in data["institution"]:
                    # Institution settings are typically system-wide, but we allow personal overrides
                    employee_settings_doc["settings"][f"institution.{key}"] = data["institution"][key]

        if "system" in data:
            for key in ["version_url"]:  # version is auto-managed
                if key in data["system"]:
                    employee_settings_doc["settings"][f"system.{key}"] = data["system"][key]

        # Save the updated employee settings
        if save_employee_settings(safe_rfid, employee_settings_doc):
            # Log activity
            add_activity(
                "employee_settings_updated",
                f"Employee settings updated for RFID {safe_rfid}",
                {"name": session.get("user", {}).get("fullname", "User")},
                "system"
            )

            return jsonify({
                "status": "success",
                "message": "Employee settings updated successfully",
                "data": {
                    "rfid": safe_rfid,
                    "settings": employee_settings_doc["settings"],
                    "last_updated": employee_settings_doc["last_updated"]
                }
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to save employee settings"
            }), 500

    except Exception as e:
        print(f"Error updating employee settings: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# Reset settings to defaults
@app.route("/api/settings/reset", methods=["POST"])
def reset_settings():
    """Reset settings to default values"""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    global settings
    settings = DEFAULT_SETTINGS.copy()
    save_settings(settings)

    # Log activity
    add_activity(
        "settings_reset",
        "System settings were reset to defaults",
        {"name": session.get("user", {}).get("fullname", "User")},
        "system"
    )

    return jsonify({
        "status": "success",
        "message": "Settings reset to defaults",
        "data": settings
    }), 200

## Web Routes ------------------------------------
# Add CORS and no-cache headers to API responses.
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Cookie, Set-Cookie, Authorization, X-Requested-With"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE, PATCH"
    if request.path.startswith("/api/") or request.path.startswith("/facialrecognition/"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.route('/')
def serve_index():
    return send_file('login.html')

@app.route('/login.html')
def serve_login():
    return send_file('login.html')

@app.route('/pages/<path:filename>')
def serve_pages(filename):
    return send_file(f'pages/{filename}')

@app.route('/css/<path:filename>')
def serve_css(filename):
    return send_file(f'css/{filename}')

@app.route('/js/<path:filename>')
def serve_js(filename):
    return send_file(f'js/{filename}')

# Serve profile images from storage folder (outside app)
@app.route('/storage/profiles/<filename>')
def serve_profile_image(filename):
    return send_from_directory(PROFILE_STORAGE, filename)

# Serve assets (logo, etc.)
@app.route('/storage/assets/<path:filename>')
def serve_asset(filename):
    assets_dir = os.path.join(BASE_DIR, "storage", "assets")
    return send_from_directory(assets_dir, filename)

# Serve work status request attachments from storage folder.
#
# By default the browser downloads the file (Content-Disposition: attachment).
# Adding ?inline=1 streams the file with Content-Disposition: inline so
# images and PDFs render directly inside an <iframe> / <img> — this is what
# the front-end "View Attachment" modal uses to build its preview.
@app.route("/storage/work-status/<path:filename>")
def serve_work_status_attachment(filename):
    inline = request.args.get("inline") in ("1", "true", "yes")

    # send_from_directory handles path traversal safely and returns the
    # correct Content-Type based on the file extension.
    response = send_from_directory(WORK_STATUS_REQUEST_STORAGE, filename)

    # Set explicit Content-Disposition so the caller controls download vs
    # preview. We keep the original filename so a "Save as…" still works.
    base_name = os.path.basename(filename)
    disposition = "inline" if inline else "attachment"
    response.headers["Content-Disposition"] = f'{disposition}; filename="{base_name}"'

    # Browsers cache inline previews aggressively; disable caching so a
    # freshly uploaded file always shows the latest content.
    response.headers["Cache-Control"] = "no-store"

    return response

# ============================================================================
# FACE SCANNER API — /api/faces/*
# ============================================================================
# Called by the standalone face-scanner page (faces.html/faces.js/faces.css)
# deployed on Vercel. Face detection and matching happen entirely in the
# browser with face-api.js; this server only supplies the profile-image
# templates to match against and records attendance once a match is sent
# back here. No RFID tap is required — a confident face match is enough.

FACE_LOG_DIR = os.path.join(BASE_DIR, "storage", "logs")
FACE_SCAN_LOG_FILE = os.path.join(FACE_LOG_DIR, "face_scans.json")
FACE_MIN_CONFIDENCE = float(os.environ.get("FACE_MIN_CONFIDENCE", "70.0"))
FACE_ATTENDANCE_COOLDOWN = int(os.environ.get("FACE_ATTENDANCE_COOLDOWN", "10"))


def _face_list_profile_images():
    os.makedirs(PROFILE_STORAGE, exist_ok=True)
    allowed_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
    return sorted(
        f for f in os.listdir(PROFILE_STORAGE)
        if os.path.splitext(f)[1].lower() in allowed_exts
    )


def _face_find_profile_image_for_rfid(rfid):
    if not rfid:
        return None
    os.makedirs(PROFILE_STORAGE, exist_ok=True)
    rfid_clean = str(rfid).strip().upper()
    allowed_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
    for ext in allowed_exts:
        candidate = os.path.join(PROFILE_STORAGE, f"{rfid_clean}{ext}")
        if os.path.exists(candidate):
            return candidate
    try:
        for filename in os.listdir(PROFILE_STORAGE):
            name, ext = os.path.splitext(filename)
            if name.upper() == rfid_clean and ext.lower() in allowed_exts:
                return os.path.join(PROFILE_STORAGE, filename)
    except FileNotFoundError:
        pass
    return None


def _face_employee_public(employee):
    if not employee:
        return None
    return {
        "uid": employee.get("uid", ""),
        "rfid": employee.get("rfid", ""),
        "employeeid": employee.get("employeeid", ""),
        "firstname": employee.get("firstname", ""),
        "lastname": employee.get("lastname", ""),
        "name": f"{employee.get('firstname', '')} {employee.get('lastname', '')}".strip(),
        "role": employee.get("role", "employee"),
        "department": employee.get("department", ""),
        "position": employee.get("position", ""),
        "image": employee.get("image", ""),
    }


def _face_get_employee_by_rfid(rfid):
    return employee_database.get(str(rfid).strip().upper())


def _face_get_employee_by_uid(uid):
    uid = str(uid).strip()
    for emp in employee_database.values():
        if str(emp.get("uid", "")).strip() == uid:
            return emp
    return None


def _face_load_employee_templates():
    templates = []
    for employee in employee_database.values():
        rfid = str(employee.get("rfid", "") or "").strip().upper()
        if not rfid:
            continue
        image_path = _face_find_profile_image_for_rfid(rfid)
        if not image_path:
            continue
        templates.append({
            "employee": _face_employee_public(employee),
            "rfid": rfid,
            "image_url": f"/storage/profiles/{os.path.basename(image_path)}",
            "image_file": os.path.basename(image_path),
        })
    return templates


def _face_log_scan(rfid, employee, confidence, status, scan_type="face_verification"):
    os.makedirs(FACE_LOG_DIR, exist_ok=True)
    log_entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "rfid": rfid,
        "employee": {
            "uid": employee.get("uid", "") if employee else "",
            "employeeid": employee.get("employeeid", "") if employee else "",
            "firstname": employee.get("firstname", "") if employee else "",
            "lastname": employee.get("lastname", "") if employee else "",
        } if employee else None,
        "confidence": confidence,
        "status": status,
        "scan_type": scan_type,
    }
    log_data = []
    if os.path.exists(FACE_SCAN_LOG_FILE):
        try:
            with open(FACE_SCAN_LOG_FILE, "r", encoding="utf-8") as f:
                log_data = json.load(f)
                if not isinstance(log_data, list):
                    log_data = []
        except Exception:
            log_data = []
    log_data.insert(0, log_entry)
    if len(log_data) > 1000:
        log_data = log_data[:1000]
    with open(FACE_SCAN_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)
    return log_entry


@app.route("/api/faces/status", methods=["GET"])
def faces_status():
    profile_images = _face_list_profile_images()
    return jsonify({
        "status": "success",
        "service": "face-scanner",
        "profile_images_found": len(profile_images),
        "profile_images": profile_images,
        "min_confidence": FACE_MIN_CONFIDENCE,
        "attendance_cooldown": FACE_ATTENDANCE_COOLDOWN,
    })


@app.route("/api/faces/employees", methods=["GET"])
def faces_employees():
    templates = _face_load_employee_templates()
    return jsonify({
        "status": "success",
        "count": len(templates),
        "employees": templates,
        "profile_images": _face_list_profile_images(),
    })


@app.route("/api/faces/record", methods=["POST"])
def faces_record():
    """Record attendance from a face match alone. No RFID tap is required —
    the browser sends the uid/rfid of whichever employee's profile image
    matched, plus the match confidence."""
    body = request.get_json(silent=True) or {}
    uid = str(body.get("uid", "")).strip()
    rfid = str(body.get("rfid", "")).strip().upper()
    confidence_raw = body.get("confidence", 0)
    scanned_at = str(body.get("scanned_at", "")).strip()

    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid confidence"}), 400

    if not uid and not rfid:
        return jsonify({"status": "error", "message": "Missing employee UID or RFID"}), 400

    if confidence < FACE_MIN_CONFIDENCE:
        return jsonify({
            "status": "rejected",
            "message": f"Face confidence {confidence:.1f}% is below the {FACE_MIN_CONFIDENCE:.1f}% threshold",
            "uid": uid, "rfid": rfid, "confidence": confidence,
        }), 403

    employee = None
    if uid:
        employee = _face_get_employee_by_uid(uid)
    if not employee and rfid:
        employee = _face_get_employee_by_rfid(rfid)

    if not employee:
        return jsonify({
            "status": "rejected",
            "message": "Employee not found in the system",
            "uid": uid, "rfid": rfid,
        }), 404

    employee_rfid = str(employee.get("rfid", "")).strip().upper()
    profile_image = _face_find_profile_image_for_rfid(employee_rfid)
    if not profile_image:
        return jsonify({
            "status": "rejected",
            "message": "Employee has no profile image for face recognition",
            "uid": uid, "rfid": employee_rfid,
        }), 404

    if not scanned_at:
        scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        try:
            scanned_dt = datetime.fromisoformat(scanned_at.replace("Z", "+00:00"))
            scanned_at = scanned_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        record, result = record_attendance_scan(employee, scanned_at)
        save_attendance_data()

        # Record the daily stats (present + face scan counts) so the counters
        # are updated even when no dashboard is open. The "face" source
        # increments face_scan_count instead of rfid_scan_count.
        try:
            record_daily_scan(uid=employee.get("uid"), source="face")
        except Exception as e:
            print(f"Warning: failed to update daily stats from face scan: {e}")

        _face_log_scan(employee_rfid, employee, confidence, "face_verified", "face_verification")
        add_scan_to_feed(employee_rfid, scanned_at, employee, True, scan_type="face_recognition")
        scan_events.append({
            "rfid": employee_rfid,
            "scanned_at": scanned_at,
            "scanned_on": datetime.now().date().isoformat(),
            "scan_type": "face",
            "confidence": confidence,
        })
        save_scan_events({"scan_events": scan_events[-10000:]})
        add_activity(
            "face_attendance",
            f"✅ Face recognized: {employee.get('firstname', '')} {employee.get('lastname', '')} "
            f"({employee.get('employeeid', '')}) - {confidence:.1f}% confidence - {result}",
            {"name": "Face Scanner", "uid": "system"},
            "attendance"
        )
        is_present = result in ["success", "already_exists"]
        return jsonify({
            "status": "success",
            "message": result,
            "source": "face-scanner",
            "confidence": confidence,
            "scanned_at": scanned_at,
            "employee": _face_employee_public(employee),
            "is_present": is_present,
            "rfid_used": employee_rfid,
            "attendance_status": result,
            "profile_image": f"/storage/profiles/{os.path.basename(profile_image)}",
            "samples": 1,
        }), 200
    except Exception as exc:
        print(f"❌ Attendance recording failed: {exc}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": f"Attendance recording failed: {str(exc)}",
            "uid": uid, "rfid": rfid,
        }), 500


@app.route("/api/faces/dashboard-stats", methods=["GET"])
def faces_dashboard_stats():
    """Return dashboard statistics for the face scanner interface.
    This is a public endpoint that doesn't require authentication,
    as it's used by the standalone face scanner hosted on Vercel.
    """
    try:
        stats = get_dashboard_statistics()
        return jsonify({
            "status": "success",
            "stats": {
                "profile_images": stats.get("profile_images", 0),
                "present_today": stats.get("present_today", 0),
                "total_employees": stats.get("total_employees", 0),
                "attendance_rate": stats.get("attendance_rate", "0%")
            }
        }), 200
    except Exception as e:
        print(f"Error in faces_dashboard_stats: {e}")
        return jsonify({
            "status": "error",
            "message": "Failed to load dashboard statistics",
            "stats": {
                "profile_images": 0,
                "present_today": 0,
                "total_employees": 0,
                "attendance_rate": "0%"
            }
        }), 500


@app.route("/api/faces/scan-log", methods=["GET"])
def faces_scan_log():
    limit = request.args.get("limit", default=50, type=int)
    log_data = []
    if os.path.exists(FACE_SCAN_LOG_FILE):
        try:
            with open(FACE_SCAN_LOG_FILE, "r", encoding="utf-8") as f:
                log_data = json.load(f)
                if not isinstance(log_data, list):
                    log_data = []
        except Exception:
            log_data = []
    if limit and limit > 0:
        log_data = log_data[:limit]
    return jsonify({"status": "success", "count": len(log_data), "logs": log_data})


@app.route("/api/faces/recent-attendance", methods=["GET"])
def faces_recent_attendance():
    """Return ONLY the employees who were recognized through the FACE SCANNER
    today — not employees whose only attendance came from an RFID tap.

    How we know an employee was recognized by face:
      • When faces_record() records attendance, it also appends an entry to
        the in-memory scan_events list with scan_type="face" and the
        employee's rfid. We use that list as the source of truth here.
      • We then cross-reference each rfid against today's DTR rows to pull
        the AM/PM in/out times for the UI.
    """
    try:
        today = datetime.now().date().isoformat()

        # ---- Step 1: collect every rfid that was recognized by face today ---
        face_rfids_today = set()
        for event in scan_events:
            if event.get("scanned_on") != today:
                continue
            if event.get("scan_type") != "face":
                continue
            rfid = str(event.get("rfid", "")).strip().upper()
            if rfid:
                face_rfids_today.add(rfid)

        # If no face scans happened at all today, return an empty list so
        # the UI shows the "No attendance records for today" placeholder.
        if not face_rfids_today:
            return jsonify({"status": "success", "count": 0, "attendance": []})

        # ---- Step 2: build the DTR lookup for today ------------------------
        recent = []
        seen_uids = set()

        for record in attendance_records:
            record_rfid = str(record.get("rfid", "")).strip().upper()

            # Only include records whose rfid actually came from a face scan.
            if record_rfid not in face_rfids_today:
                continue

            uid = record.get("uid", "")
            if uid in seen_uids:
                continue

            dtr = record.get("dtr", {})
            for date_key, day in dtr.items():
                if day.get("date") != today:
                    continue

                # Only include rows that have at least one time recorded
                # today. A freshly-created DTR row with no times would
                # otherwise appear as a phantom "Present" entry.
                if not (day.get("am_in") or day.get("am_out") or day.get("pm_in") or day.get("pm_out")):
                    continue

                recent.append({
                    "uid": uid,
                    "employee": record.get("fullname", ""),
                    "employeeid": record.get("employeeid", ""),
                    "date": day.get("date", ""),
                    "am_in": day.get("am_in", ""),
                    "am_out": day.get("am_out", ""),
                    "pm_in": day.get("pm_in", ""),
                    "pm_out": day.get("pm_out", ""),
                    "hours": day.get("hours", "0.00"),
                    "status": day.get("status", ""),
                })
                seen_uids.add(uid)
                break

        return jsonify({"status": "success", "count": len(recent), "attendance": recent})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "attendance": []}), 200

## Authentication Routes ------------------------------------
# FIXED: Authenticate all roles and create a three-hour session.
@app.route("/api/login", methods=["POST"])
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "status": "error",
                "message": "Username and password are required"
            }), 400

        username = str(data.get("username", "")).strip()
        password = str(data.get("password", "")).strip()
        password_hash = hashlib.md5(password.encode("utf-8")).hexdigest()

        print(f"Login attempt - Username: '{username}'")
        print(f"Password hash: '{password_hash}'")
        print(f"Total users in database: {len(employee_database)}")

        for emp in employee_database.values():
            print(f"  User in DB: '{emp.get('username')}' with role: {emp.get('role')}")

        for emp in employee_database.values():
            stored_username = emp.get("username")
            if stored_username and stored_username == username:
                print(f"Username match found for: {username}")
                stored_hash = emp.get("password_hash", "").lower()
                print(f"Stored hash: '{stored_hash}'")
                print(f"Input hash:  '{password_hash}'")

                if password_hash == stored_hash:
                    role = get_user_role(emp)
                    user_data = {
                        "uid": emp.get("uid"),
                        "employeeid": emp.get("employeeid"),
                        "username": emp.get("username"),
                        "fullname": emp.get("firstname", "") + " " + emp.get("lastname", ""),
                        "role": role,
                        "rfid": emp.get("rfid")
                    }

                    token = jwt.encode({
                        'user': user_data,
                        'exp': datetime.utcnow() + JWT_EXPIRATION
                    }, JWT_SECRET, algorithm='HS256')

                    session.permanent = True
                    session["user"] = user_data
                    session.modified = True

                    print("Login successful for:", username)

                    # Log login activity
                    add_activity(
                        "user_login",
                        f"{user_data.get('fullname')} ({username}) logged in as {role.upper()}",
                        {"name": user_data.get('fullname'), "uid": user_data.get('uid')},
                        "system"
                    )

                    # Push a login notification to this user's own file.
                    try:
                        push_notification(
                            emp.get("rfid"),
                            "Login Successful",
                            f"Your account was accessed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.",
                            notif_type="system",
                            uid=emp.get("uid"),
                            fullname=f"{emp.get('firstname', '')} {emp.get('lastname', '')}".strip(),
                            role=role
                        )
                    except Exception as e:
                        print(f"Warning: failed to push login notification: {e}")

                    return jsonify({
                        "status": "success",
                        "message": "Login successful",
                        "redirect": get_role_redirect(role),
                        "user": user_data,
                        "token": token
                    }), 200

        print(f"Login failed for: {username} - User not found or password mismatch")
        return jsonify({
            "status": "error",
            "message": "Invalid username or password"
        }), 401
    except Exception as e:
        print("Login error:", str(e))
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# Verify JWT token
@app.route("/api/verify-token", methods=["POST"])
def verify_token_route():
    user_data, error_response, status_code = verify_token()
    if error_response:
        return error_response, status_code
    return jsonify({"status": "success", "user": user_data}), 200

# Return the currently authenticated user's session.
@app.route("/api/session", methods=["GET"])
def get_session():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if not error_response:
            return jsonify({"status": "success", "user": user_data}), 200

    user = session.get("user")
    if not user:
        return jsonify({
            "status": "error",
            "message": "Session expired or user is not logged in"
        }), 401
    return jsonify({
        "status": "success",
        "user": user
    }), 200

# Clear the authenticated user's session.
@app.route("/api/logout", methods=["POST", "GET", "OPTIONS"])
def logout():
    try:
        user = session.get("user")

        # Log logout activity before clearing session
        if user:
            add_activity(
                "user_logout",
                f"{user.get('fullname', 'User')} logged out",
                {"name": user.get('fullname'), "uid": user.get('uid')},
                "system"
            )

        session.clear()

        response = jsonify({
            "status": "success",
            "message": "Logged out successfully"
        })

        response.set_cookie('tapin_session', '', expires=0)
        response.set_cookie('session', '', expires=0)

        print("User logged out successfully")
        return response, 200
    except Exception as e:
        print(f"Logout error: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Logout failed: {str(e)}"
        }), 500

# ============================================================================
# CHANGE PASSWORD ROUTE
# ============================================================================
# Called by the "Update Password" button on both the desktop and mobile
# employee dashboards. Verifies the current password (MD5-hashed to match
# the stored password_hash), then overwrites password_hash with the MD5
# hash of the new password.
#
# This route was previously missing, which caused the front-end to receive
# a 404 from Flask's default 404 handler and report "Network error."
# ============================================================================
@app.route("/api/change-password", methods=["POST"])
def change_password():
    """Change a user's password.

    Body (JSON):
        {
            "rfid": "FB822A54",
            "current_password": "oldpass",
            "new_password": "newpass123"
        }

    Steps:
      1. Look up the employee by RFID in users.json.
      2. Hash the submitted current_password with MD5 and compare it to
         the stored password_hash.
      3. If it matches, hash new_password with MD5 and overwrite
         password_hash.
      4. Backup users.json → users.json.backup, then save.
      5. Refresh the in-memory employee_database so the next login uses
         the new hash immediately.
    """
    try:
        data = request.get_json() or {}
        rfid = str(data.get("rfid", "")).strip().upper()
        current_password = str(data.get("current_password", ""))
        new_password = str(data.get("new_password", ""))

        # ---- 1) Required-field validation ---------------------------------
        if not rfid or not current_password or not new_password:
            return jsonify({
                "status": "error",
                "message": "rfid, current_password, and new_password are required"
            }), 400

        # ---- 2) Minimum length -------------------------------------------
        if len(new_password) < 8:
            return jsonify({
                "status": "error",
                "message": "New password must be at least 8 characters"
            }), 400

        # ---- 3) Must differ from the current password --------------------
        if new_password == current_password:
            return jsonify({
                "status": "error",
                "message": "New password must be different from the current password"
            }), 400

        # ---- 4) Load users.json ------------------------------------------
        if not os.path.exists(USER_DATA_FILE):
            return jsonify({
                "status": "error",
                "message": "User database not found"
            }), 404

        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            database = json.load(f)

        current_hash = hashlib.md5(current_password.encode("utf-8")).hexdigest()
        new_hash = hashlib.md5(new_password.encode("utf-8")).hexdigest()

        # ---- 5) Find the employee across all role buckets ----------------
        updated = False
        for category in ["admin", "hr", "employees"]:
            for emp in database.get(category, []):
                if str(emp.get("rfid", "")).strip().upper() == rfid:
                    stored_hash = str(emp.get("password_hash", "")).lower()
                    if stored_hash != current_hash:
                        return jsonify({
                            "status": "error",
                            "message": "Current password is incorrect"
                        }), 401
                    emp["password_hash"] = new_hash
                    emp["timestamp_modified"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    updated = True
                    break
            if updated:
                break

        if not updated:
            return jsonify({
                "status": "error",
                "message": "Employee not found"
            }), 404

        # ---- 6) Backup + save --------------------------------------------
        import shutil
        try:
            shutil.copy2(USER_DATA_FILE, USER_DATA_FILE + ".backup")
        except Exception as e:
            print(f"Warning: could not back up users.json: {e}")

        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(database, f, indent=4)
            f.write("\n")

        # ---- 7) Refresh the in-memory database ---------------------------
        # The next login reads password_hash from employee_database, so we
        # must rebuild it here for the new hash to take effect immediately.
        global employee_database
        employee_database = load_employee_database()

        # ---- 8) Log the activity -----------------------------------------
        add_activity(
            "password_changed",
            f"Password changed for RFID {rfid}",
            {"name": "System", "uid": "system"},
            "system"
        )

        return jsonify({
            "status": "success",
            "message": "Password updated successfully"
        }), 200

    except Exception as e:
        print(f"Change password error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": f"Failed to update password: {str(e)}"
        }), 500

## Employee Management Routes ------------------------------------
# Register a new admin, HR, or employee account.
@app.route("/api/register-employee", methods=["POST"])
def register_employee():
    try:
        data = request.form
        required = ["employeeid", "rfid", "lastname", "firstname", "address", "bdate", "cpnumber", "email", "username", "password", "role"]
        if not data or not all(key in data for key in required):
            return jsonify({
                "status": "error",
                "message": "Missing required fields",
                "required_fields": required
            }), 400

        role = str(data.get("role", "employee")).strip().lower()
        if role not in ["admin", "hr", "employee"]:
            return jsonify({
                "status": "error",
                "message": "Role must be admin, hr, or employee"
            }), 400

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        category = "employees" if role == "employee" else role
        image_file = request.files.get("image")
        image_path = ""

        os.makedirs(os.path.dirname(USER_DATA_FILE), exist_ok=True)

        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                database = json.load(f)
        else:
            database = {"admin": [], "hr": [], "employees": []}

        username = str(data.get("username", "")).strip()
        rfid = str(data.get("rfid", "")).strip().upper()

        if any(emp.get("rfid", "").strip().upper() == rfid or emp.get("username") == username
               for records in database.values() for emp in records):
            return jsonify({
                "status": "error",
                "message": "RFID or username is already registered"
            }), 409

        uid_start, uid_end = ROLE_UID_RANGES[role]
        employee_uids = [
            int(emp.get("uid"))
            for records in database.values()
            for emp in records
            if str(emp.get("uid", "")).isdigit()
        ]
        role_uids = [value for value in employee_uids if uid_start <= value <= uid_end]
        uid = str(max([uid_start - 1] + role_uids) + 1).zfill(3)

        # Process image with compression
        if image_file and image_file.filename:
            extension = os.path.splitext(image_file.filename)[1].lower()
            if extension not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                return jsonify({
                    "status": "error",
                    "message": "Image must be JPG, JPEG, PNG, GIF, or WEBP"
                }), 400

            # Use the compression function
            image_path = compress_and_save_image(image_file, rfid)

            if not image_path:
                # Fallback to original saving method if compression fails
                rfid_filename = secure_filename(rfid)
                os.makedirs(PROFILE_STORAGE, exist_ok=True)
                filename = rfid_filename + extension
                image_file.save(os.path.join(PROFILE_STORAGE, filename))
                image_path = os.path.join("storage", "profiles", filename).replace(os.sep, "/")

        employee = {
            "uid": uid,
            "rfid": rfid,
            "employeeid": str(data.get("employeeid", "")).strip(),
            "lastname": str(data.get("lastname", "")).strip(),
            "firstname": str(data.get("firstname", "")).strip(),
            "address": str(data.get("address", "")).strip(),
            "bdate": str(data.get("bdate", "")).strip(),
            "cpnumber": str(data.get("cpnumber", "")).strip(),
            "email": str(data.get("email", "")).strip(),
            "username": str(data.get("username", "")).strip(),
            "password_hash": hashlib.md5(str(data.get("password", "")).encode("utf-8")).hexdigest(),
            "role": role,
            "department": None,
            "position": None,
            "image": image_path,
            "timestamp_creation": now,
            "timestamp_modified": now
        }
        database.setdefault(category, []).append(employee)

        if os.path.exists(USER_DATA_FILE):
            import shutil
            shutil.copy2(USER_DATA_FILE, USER_DATA_FILE + ".backup")

        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(database, f, indent=4)
            f.write("\n")

        employee_database[rfid] = employee

        print("Registered:", employee["firstname"], employee["lastname"], "UID:", uid, "RFID:", rfid)

        # Log activity
        add_activity(
            "employee_registered",
            f"New {role.upper()} registered: {employee['firstname']} {employee['lastname']} (ID: {employee['employeeid']})",
            {"name": "System", "uid": "system"},
            "employee"
        )

        # Create an empty notification file for the new user, with a welcome entry.
        try:
            push_notification(
                rfid,
                "Welcome to TAPIN",
                f"Hello {employee['firstname']}, your account has been registered successfully.",
                notif_type="system",
                uid=uid,
                fullname=f"{employee['firstname']} {employee['lastname']}".strip(),
                role=role
            )
        except Exception as e:
            print(f"Warning: failed to create welcome notification: {e}")

        return jsonify({
            "status": "success",
            "message": "Employee registered successfully",
            "data": employee
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Registration failed: " + str(e)
        }), 500

# Update employee data - FIXED to handle image updates
@app.route("/api/update-employee/<rfid>", methods=["PUT"])
def update_employee(rfid):
    try:
        rfid = rfid.strip().upper()
        data = request.form

        os.makedirs(os.path.dirname(USER_DATA_FILE), exist_ok=True)

        if not os.path.exists(USER_DATA_FILE):
            return jsonify({
                "status": "error",
                "message": "Database file not found"
            }), 404

        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            database = json.load(f)

        found = False
        updated_employee = None
        category_found = None
        index_found = None

        # The URL key can be either the employee's UID, their stored RFID, or
        # (if the caller is renaming) the RFID as submitted in the form body.
        # We normalize every candidate to uppercase for the RFID comparison so
        # a case mismatch can never cause "Employee not found".
        url_key = rfid  # already .strip().upper() at the top of the function
        body_rfid = str(data.get("rfid", "")).strip().upper()

        def _matches(emp):
            """Return True if the employee record matches the URL key."""
            emp_rfid = str(emp.get("rfid", "")).strip().upper()
            emp_uid = str(emp.get("uid", "")).strip()
            if emp_rfid and emp_rfid == url_key:
                return True
            if emp_uid and emp_uid == url_key:
                return True
            # When the caller is renaming the RFID, the URL still contains the
            # OLD RFID (or the UID). Match the form-body RFID too so the record
            # is always found even if the hidden field was stale.
            if body_rfid and emp_rfid and emp_rfid == body_rfid:
                return True
            return False

        for category in ["admin", "hr", "employees"]:
            if category in database:
                for idx, emp in enumerate(database[category]):
                    if _matches(emp):
                        found = True
                        category_found = category
                        index_found = idx
                        updated_employee = emp
                        break
                if found:
                    break

        if not found:
            print(f"update_employee: no match for URL key '{url_key}' (body rfid='{body_rfid}')")
            return jsonify({
                "status": "error",
                "message": "Employee not found"
            }), 404

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Update text fields
        if "lastname" in data and data.get("lastname"):
            updated_employee["lastname"] = str(data.get("lastname", "")).strip()
        if "firstname" in data and data.get("firstname"):
            updated_employee["firstname"] = str(data.get("firstname", "")).strip()
        if "address" in data and data.get("address"):
            updated_employee["address"] = str(data.get("address", "")).strip()
        if "bdate" in data and data.get("bdate"):
            updated_employee["bdate"] = str(data.get("bdate", "")).strip()
        if "cpnumber" in data and data.get("cpnumber"):
            updated_employee["cpnumber"] = str(data.get("cpnumber", "")).strip()
        if "email" in data and data.get("email"):
            updated_employee["email"] = str(data.get("email", "")).strip()
        if "username" in data and data.get("username"):
            updated_employee["username"] = str(data.get("username", "")).strip()
        if "department" in data:
            updated_employee["department"] = str(data.get("department", "")).strip()
        if "position" in data:
            updated_employee["position"] = str(data.get("position", "")).strip()

        # Update password if provided
        if "password" in data and data.get("password"):
            updated_employee["password_hash"] = hashlib.md5(str(data.get("password", "")).encode("utf-8")).hexdigest()

        # Handle image update
        image_file = request.files.get("image")
        if image_file and image_file.filename:
            # Validate file extension
            extension = os.path.splitext(image_file.filename)[1].lower()
            if extension not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                return jsonify({
                    "status": "error",
                    "message": "Image must be JPG, JPEG, PNG, GIF, or WEBP"
                }), 400

            # Delete old image file if it exists
            old_image = updated_employee.get("image")
            if old_image:
                old_image_path = os.path.join(BASE_DIR, old_image)
                if os.path.exists(old_image_path):
                    try:
                        os.remove(old_image_path)
                        print(f"Deleted old image: {old_image_path}")
                    except Exception as e:
                        print(f"Error deleting old image: {e}")

            # Use the compression function for new image
            image_path = compress_and_save_image(image_file, rfid)

            if image_path:
                updated_employee["image"] = image_path
                print(f"New image saved: {image_path}")
            else:
                # Fallback to original saving method if compression fails
                rfid_filename = secure_filename(rfid)
                os.makedirs(PROFILE_STORAGE, exist_ok=True)
                filename = rfid_filename + extension
                image_file.save(os.path.join(PROFILE_STORAGE, filename))
                image_path = os.path.join("storage", "profiles", filename).replace(os.sep, "/")
                updated_employee["image"] = image_path
                print(f"New image saved (fallback): {image_path}")

        # Allow changing RFID via form data (client sends new value in 'rfid')
        new_rfid_value = None
        if "rfid" in data and data.get("rfid"):
            candidate = str(data.get("rfid", "")).strip().upper()
            if candidate and candidate != rfid:
                # ensure uniqueness across database
                collision = False
                for cat in ["admin", "hr", "employees"]:
                    if cat in database:
                        for emp in database[cat]:
                            if emp.get("rfid", "").strip().upper() == candidate:
                                collision = True
                                break
                    if collision:
                        break
                if collision:
                    return jsonify({
                        "status": "error",
                        "message": "RFID already assigned to another employee"
                    }), 400

                # update the employee object and attendance records
                updated_employee["rfid"] = candidate
                new_rfid_value = candidate
                try:
                    for rec in attendance_records:
                        if rec.get("uid") == updated_employee.get("uid"):
                            rec["rfid"] = candidate
                    # persist attendance changes
                    save_attendance_data()
                except Exception as e:
                    print(f"Warning: failed to update attendance records for RFID change: {e}")

        updated_employee["timestamp_modified"] = now

        # Update database
        database[category_found][index_found] = updated_employee

        # Create backup
        import shutil
        shutil.copy2(USER_DATA_FILE, USER_DATA_FILE + ".backup")

        # Save to file
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(database, f, indent=4)
            f.write("\n")

        # Update in-memory database (handle RFID rename if requested)
        try:
            # Capture the OLD RFID before we mutate anything — this is the
            # actual key the employee was stored under in employee_database,
            # which is NOT necessarily the URL key (the URL key may be the UID).
            old_rfid_for_cache = None
            for k, v in list(employee_database.items()):
                if v is updated_employee or (
                    str(v.get("uid", "")).strip() == str(updated_employee.get("uid", "")).strip()
                    and str(updated_employee.get("uid", "")).strip()
                ):
                    old_rfid_for_cache = k
                    break

            if old_rfid_for_cache and old_rfid_for_cache in employee_database:
                try:
                    del employee_database[old_rfid_for_cache]
                except Exception:
                    pass

            # Re-key by the (possibly new) RFID so subsequent lookups work.
            final_rfid = str(updated_employee.get("rfid", "")).strip().upper()
            if final_rfid:
                employee_database[final_rfid] = updated_employee
            else:
                # No RFID — keep the synthetic UID key so it still appears.
                uid_key = f"__UID__{updated_employee.get('uid', '')}"
                employee_database[uid_key] = updated_employee
        except Exception as e:
            print(f"Warning: failed to update in-memory employee_database mapping: {e}")

        # Log activity
        add_activity(
            "employee_updated",
            f"Employee {updated_employee['firstname']} {updated_employee['lastname']} (ID: {updated_employee['employeeid']}) was updated",
            {"name": "System", "uid": "system"},
            "employee"
        )

        # Notify the employee that their profile was updated.
        try:
            push_notification(
                updated_employee.get("rfid") or rfid,
                "Profile Updated",
                "Your profile information was updated successfully.",
                notif_type="employee",
                uid=updated_employee.get("uid"),
                fullname=f"{updated_employee.get('firstname', '')} {updated_employee.get('lastname', '')}".strip(),
                role=updated_employee.get("role")
            )
        except Exception as e:
            print(f"Warning: failed to push profile-update notification: {e}")

        return jsonify({
            "status": "success",
            "message": "Employee updated successfully",
            "data": updated_employee
        }), 200

    except Exception as e:
        print(f"Update error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "Update failed: " + str(e)
        }), 500

## Dashboard Routes ------------------------------------
# Return all current dashboard data in one authenticated response.
@app.route("/api/dashboard-data", methods=["GET"])
def dashboard_data():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    # Return the FULL dashboard payload the frontend expects:
    # stats, users, attendance, scans, devices, latest_scan,
    # work_status, and activities.
    return jsonify({
        "status": "success",
        "data": get_dashboard_data()
    }), 200

@app.route("/api/activity-feed", methods=["GET"])
def get_activity_feed():
    """Get the activity feed data for the dashboard timeline"""
    # Wipe all feeds once per calendar day (first request after midnight).
    activity_feed_data = load_activity_feed()
    activities = activity_feed_data.get("activities", [])

    return jsonify({
        "status": "success",
        "data": {
            "activities": activities,
            "total_activities": activity_feed_data.get("total_activities", 0)
        }
    }), 200

# Return devices that have sent a recent ping.
@app.route("/api/get-device-status", methods=["GET"])
def get_device_status():
    return jsonify({
        "status": "success",
        "devices": get_online_devices()
    }), 200

# Check whether one device is currently online.
@app.route("/api/check-device/<device_id>", methods=["GET"])
def check_device(device_id):
    remove_offline_devices()
    data = device_status.get(device_id)
    if not data:
        return jsonify({
            "status": "unknown",
            "message": "Device not found"
        }), 404
    return jsonify({
        "status": "success",
        "device_id": device_id,
        "device_status": data["status"],
        "last_seen": data["last_seen"]
    }), 200

# Return persistent DTR records for a requested month.
@app.route("/api/attendance", methods=["GET"])
def get_attendance():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        user_data, error_response, status_code = verify_token()
        if error_response:
            return error_response, status_code
    else:
        if not session.get("user"):
            return jsonify({
                "status": "error",
                "message": "Session expired or user is not logged in"
            }), 401

    month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    records = [record for record in attendance_records if record.get("month") == month]
    return jsonify({
        "status": "success",
        "month": month,
        "records": records
    }), 200

# Return the latest RFID scan and online devices for the web dashboard.
@app.route("/api/get-latest-rfid", methods=["GET"])
def get_latest_rfid():
    """Get the latest RFID scan with full attendance data for the employee"""
    # Removed: Wipe all feeds once per calendar day (first request after midnight).
    scanned_at = latest_scan.get("scanned_at")
    latest_rfid = latest_scan.get("rfid")

    employee_data = None
    attendance_data = None

    if latest_rfid:
        employee = employee_database.get(latest_rfid)
        if employee:
            # Build employee data with full image URL
            stored_image = employee.get("image", "")
            image_url = ""

            if stored_image:
                if stored_image.startswith("http"):
                    image_url = stored_image
                else:
                    image_url = f"{request.host_url}{stored_image}"
            else:
                rfid_filename = employee.get("rfid", "")
                if rfid_filename:
                    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
                    for ext in image_extensions:
                        image_path = os.path.join(PROFILE_STORAGE, rfid_filename + ext)
                        if os.path.exists(image_path):
                            image_url = f"{request.host_url}storage/profiles/{rfid_filename}{ext}"
                            break

            employee_data = {
                "uid": employee.get("uid"),
                "rfid": employee.get("rfid"),
                "employeeid": employee.get("employeeid"),
                "lastname": employee.get("lastname"),
                "firstname": employee.get("firstname"),
                "role": employee.get("role"),
                "image": image_url
            }

            # Get today's attendance data - ALWAYS try to get or create record
            today = datetime.now()
            today_str = today.strftime("%Y-%m-%d")
            month_key = today.strftime("%Y-%m")

            # Try to find existing record
            for record in attendance_records:
                if record.get("uid") == employee.get("uid") and record.get("month") == month_key:
                    for key, day in record.get("dtr", {}).items():
                        if day.get("date") == today_str:
                            attendance_data = {
                                "am_in": day.get("am_in", ""),
                                "am_out": day.get("am_out", ""),
                                "pm_in": day.get("pm_in", ""),
                                "pm_out": day.get("pm_out", ""),
                                "status": day.get("status", ""),
                                "work_status": day.get("work_status")
                            }
                            break
                    break

            # If no record found, create one and return empty data
            if attendance_data is None:
                # Create a new attendance record for this employee
                record = get_attendance_record(employee, today)
                # Get the newly created record's data
                for key, day in record.get("dtr", {}).items():
                    if day.get("date") == today_str:
                        attendance_data = {
                            "am_in": "",
                            "am_out": "",
                            "pm_in": "",
                            "pm_out": "",
                            "status": "",
                            "work_status": None
                        }
                        break
                # Save the new record
                save_attendance_data()
                print(f"Created new attendance record for {employee.get('firstname')} for today")

    # If no employee found, still return a valid response
    if not employee_data and not attendance_data:
        return jsonify({
            "status": "success",
            "rfid": latest_rfid if latest_rfid else "",
            "scanned_at": scanned_at,
            "devices": get_online_devices(),
            "found": False,
            "employee": None,
            "attendance": None
        }), 200

    return jsonify({
        "status": "success",
        "rfid": latest_rfid if latest_rfid else "",
        "scanned_at": scanned_at,
        "devices": get_online_devices(),
        "found": bool(employee),
        "employee": employee_data,
        "attendance": attendance_data
    }), 200

# ============================================================================
# OPTIONAL LIGHTWEIGHT DAILY-STATS ENDPOINT
# ============================================================================
# Returns ONLY today's present + scan counters (including the separate
# rfid_scan_count and face_scan_count) without pulling the full dashboard
# payload. The dashboard polls this every few seconds to keep the Present
# Today / RFID Scans / Facial Scans cards fresh without a full refresh.
@app.route("/api/daily-stats", methods=["GET"])
def get_daily_stats():
    stats = load_daily_stats()
    return jsonify({
        "status": "success",
        "data": {
            "date": stats.get("date"),
            "present_count": stats.get("present_count", 0),
            "scan_count": stats.get("scan_count", 0),
            "rfid_scan_count": stats.get("rfid_scan_count", 0),
            "face_scan_count": stats.get("face_scan_count", 0),
            "present_uids": stats.get("present_uids", []),
            "last_updated": stats.get("last_updated")
        }
    }), 200

# Reload users.json into the in-memory RFID lookup database.
@app.route("/api/reload-db", methods=["POST"])
def reload_db():
    global employee_database
    employee_database = load_employee_database()
    initialize_attendance_records()

    # Log activity
    add_activity(
        "database_reloaded",
        "Employee database was reloaded from storage",
        {"name": "System", "uid": "system"},
        "system"
    )

    return jsonify({
        "status": "success",
        "message": "Database reloaded",
        "total": len(employee_database)
    }), 200

## IoT Routes ------------------------------------
# Receive a heartbeat ping from an RFID device.
@app.route("/api/device-ping", methods=["POST"])
def device_ping():
    try:
        data = request.get_json()
        if not data or not data.get("device_id"):
            return jsonify({
                "status": "error",
                "message": "Missing required field: device_id"
            }), 400
        device_id = str(data["device_id"]).strip()
        status = data.get("status", "alive")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        device_status[device_id] = {
            "status": status,
            "last_seen": now,
            "last_seen_at": datetime.now()
        }
        print("Ping received from", device_id, "Last seen:", now)
        return jsonify({
            "status": "success",
            "message": "Ping recorded",
            "device_id": device_id,
            "last_seen": now
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

## Receive an RFID scan and match it to a user record - RETURNS PLAIN TEXT FOR ESP32
@app.route("/api/receive-rfid", methods=["POST"])
def receive_rfid():
    try:
        print(f"RFID receive request received")
        print(f"Content-Type: {request.headers.get('Content-Type')}")

        # Removed: Wipe all feeds once per calendar day (first request after midnight).
        raw_data = request.get_data()
        print(f"Raw data: {raw_data}")

        # Try to parse JSON
        data = request.get_json()
        if not data:
            print("ERROR: Invalid JSON or missing data")
            return "ERROR: Invalid JSON or missing data", 400

        if "rfid" not in data or "scanned_at" not in data:
            print("ERROR: Missing required fields: rfid and scanned_at")
            return "ERROR: Missing required fields: rfid and scanned_at", 400

        rfid = str(data["rfid"]).strip().upper()
        scanned_at = str(data["scanned_at"])

        print(f"Processing RFID: {rfid} at {scanned_at}")

        # Update latest scan
        latest_scan["rfid"] = rfid
        latest_scan["scanned_at"] = scanned_at

        # Check if employee exists
        employee = employee_database.get(rfid)
        found = bool(employee)

        # Record the daily stats (present + RFID scan counts) IMMEDIATELY,
        # regardless of whether any dashboard is open. The "rfid" source
        # increments rfid_scan_count instead of face_scan_count.
        try:
            record_daily_scan(uid=employee.get("uid") if employee else None, source="rfid")
        except Exception as e:
            print(f"Warning: failed to update daily stats: {e}")

        # Do NOT call add_scan_to_feed here — record_attendance_scan will do it
        # with the correct scan_type (am_in / am_out / pm_in / pm_out) once the
        # in/out state machine runs below.

        # Add to scan events (raw data for statistics)
        scan_event = {
            "rfid": rfid,
            "scanned_at": scanned_at,
            "scanned_on": datetime.now().date().isoformat()
        }
        scan_events.append(scan_event)
        # Save scan events
        scan_events_data = {"scan_events": scan_events[-10000:]}
        save_scan_events(scan_events_data)

        # Process attendance if employee found
        if employee:
            print(f"RFID matched: {employee['firstname']} {employee['lastname']}")
            record, result = record_attendance_scan(employee, scanned_at)
            print(f"Attendance record result: {result}")
            # Save attendance data (ONLY records, no scan events)
            save_attendance_data()
            # Return simple OK with scan result
            return f"OK: {result}", 200
        else:
            print(f"RFID not found in database: {rfid}")
            # Log unknown RFID scan
            add_activity(
                "unknown_rfid_scanned",
                f"Unknown RFID card scanned: {rfid}",
                None,
                "system"
            )
            return "ERROR: RFID not found", 404

    except Exception as e:
        print(f"Error in receive_rfid: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"ERROR: {str(e)}", 500

## Error Handlers ------------------------------------
@app.errorhandler(404)
def page_not_found(e):
    return jsonify({
        "status": "error",
        "message": "Invalid request",
        "timestamp": datetime.now().isoformat()
    }), 404

## OPTIONS Handlers ------------------------------------
@app.route("/api/login", methods=["OPTIONS"])
@app.route("/api/session", methods=["OPTIONS"])
@app.route("/api/logout", methods=["OPTIONS"])
@app.route("/api/change-password", methods=["OPTIONS"])
@app.route("/api/dashboard-data", methods=["OPTIONS"])
@app.route("/api/daily-stats", methods=["OPTIONS"])
@app.route("/api/verify-token", methods=["OPTIONS"])
@app.route("/api/register-employee", methods=["OPTIONS"])
@app.route("/api/update-employee/<rfid>", methods=["OPTIONS"])
@app.route("/api/receive-rfid", methods=["OPTIONS"])
@app.route("/api/device-ping", methods=["OPTIONS"])
@app.route("/api/scan-feed", methods=["OPTIONS"])
@app.route("/api/activity-feed", methods=["OPTIONS"])
@app.route("/api/request-work-status", methods=["OPTIONS"])
@app.route("/api/work-status-requests", methods=["OPTIONS"])
@app.route("/api/work-status-requests/<rfid>", methods=["OPTIONS"])
@app.route("/api/approve-work-status/<request_id>", methods=["OPTIONS"])
@app.route("/api/reject-work-status/<request_id>", methods=["OPTIONS"])
@app.route("/api/work-status-types", methods=["OPTIONS"])
@app.route("/api/dtr/employees", methods=["OPTIONS"])
@app.route("/api/dtr/record/<rfid>", methods=["OPTIONS"])
@app.route("/api/dtr/generate-pdf/<rfid>", methods=["OPTIONS"])
@app.route("/api/dtr/months", methods=["OPTIONS"])
@app.route("/api/settings", methods=["OPTIONS"])
@app.route("/api/settings/check-version", methods=["OPTIONS"])
@app.route("/api/settings/reset", methods=["OPTIONS"])
@app.route("/api/settings/<rfid>", methods=["OPTIONS"])
@app.route("/api/notifications/<rfid>", methods=["OPTIONS"])
@app.route("/api/notifications/<rfid>/clear", methods=["OPTIONS"])
@app.route("/api/notifications/<rfid>/read-all", methods=["OPTIONS"])
@app.route("/api/notifications/<rfid>/<notification_id>", methods=["OPTIONS"])
@app.route("/api/work-status-attachment/meta/<rfid>/<filename>", methods=["OPTIONS"])
def handle_options():
    response = jsonify({"status": "ok"})
    origin = request.headers.get("Origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Cookie, Set-Cookie, Authorization, X-Requested-With"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE, PATCH"
    return response, 200

# ============================================================================
# FACE SCANNER OPTIONS HANDLERS
# ============================================================================
# Add OPTIONS handlers for the /api/faces/* routes the Vercel-hosted scanner
# page calls (CORS preflight).
@app.route("/api/faces/status", methods=["OPTIONS"])
@app.route("/api/faces/employees", methods=["OPTIONS"])
@app.route("/api/faces/record", methods=["OPTIONS"])
@app.route("/api/faces/recent-attendance", methods=["OPTIONS"])
@app.route("/api/faces/dashboard-stats", methods=["OPTIONS"])
@app.route("/api/faces/scan-log", methods=["OPTIONS"])
def handle_scanner_options():
    response = jsonify({"status": "ok"})
    origin = request.headers.get("Origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Cookie, Set-Cookie, Authorization, X-Requested-With"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE, PATCH"
    return response, 200

# ============================================================================
# START BACKGROUND SCHEDULERS + IMMEDIATE CATCH-UP WIPE
# ============================================================================
# This block runs at module import time, which means it executes on WSGI
# servers (gunicorn, PythonAnywhere, Railway, Render) as well as when the
# file is run directly. That is important: on PythonAnywhere the __main__
# block never runs — the WSGI server just imports the module — so the
# scheduler must be started here to guarantee the midnight wipe fires.
try:
    start_nightly_wipe_scheduler()
except Exception as e:
    print(f"⚠️ Could not start nightly wipe scheduler: {e}")

# One-time catch-up wipe on boot. If the server was down at midnight and
# restarted the next day, this clears any stale feed content immediately.
# Disabled per user request - feeds are no longer wiped on boot/restart
try:
    perform_nightly_feed_wipe(force=False)  # Changed from force=True to force=False to disable boot wipe
    print("[Boot] Initial feed wipe skipped (disabled per user request).")
except Exception as e:
    print(f"⚠️ Startup wipe error: {e}")

# Ensure the daily stats file exists and is for the correct date.
try:
    load_daily_stats()
    print("[Boot] Daily stats file initialized.")
except Exception as e:
    print(f"⚠️ Daily stats init error: {e}")

## Main ------------------------------------
if __name__ == "__main__":
    initialize_attendance_records()

    # The scheduler is already started at module level above, so we don't
    # need to start it again here. But the immediate boot wipe is idempotent
    # thanks to the `_last_feed_wipe_date` guard, so calling it twice is fine.

    # Get port from environment variable (Railway sets PORT)
    # If PORT env var is set (e.g., on Railway), use it directly.
    # Otherwise, find a free port starting from 5000 for local development.
    env_port = os.environ.get("PORT")

    if env_port:
        # Production / Railway: use the provided port
        port = int(env_port)
    else:
        # Local development: check if 5000 is free, otherwise find the next available port
        if is_port_in_use(5000):
            print(f"⚠️  Port 5000 is already in use. Searching for a free port...")
            port = find_free_port(start_port=5001, max_attempts=20)
            if port is None:
                print("❌ ERROR: Could not find any free port between 5001 and 5020.")
                print("   Please stop the process using port 5000, or set the PORT environment variable.")
                raise SystemExit(1)
            print(f"✅ Found free port: {port}")
        else:
            port = 5000

    # Print environment info
    print(f"\n{'='*60}")
    print(f"🚀 Starting TapIn API Server")
    print(f"📍 Environment: {'Production' if is_production() else 'Development'}")
    print(f"🔗 Base URL: {get_base_url()}")
    print(f"📁 Storage: {BASE_DIR}/storage")
    print(f"🌐 Port: {port}")
    print(f"{'='*60}\n")

    # For production, use HTTPS secure settings
    if is_production():
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
        # For development, run with debug enabled
        app.run(host='0.0.0.0', port=port, debug=True)