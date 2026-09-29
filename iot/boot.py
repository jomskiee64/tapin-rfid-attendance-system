## firmware boot.py

## Imports
import machine
import network
import ntptime
import os

import utime as time
import urequests as requests

from configs.config import *
from configs.network_config import *
from configs.parameters import *


## Classes
class PRINTSTATUS:
    OK = "OKK"
    INFO = "INF"
    ERROR = "ERR"
    WARN = "WRN"
    SUCCESS = "SCS"
    DEBUG = "DBG"


## Call functions
def get_date_string():
    year, month, day = time.localtime()[:3]
    return f"{year:04d}_{month:02d}_{day:02d}"

def get_log_filename(log_type):
    date_str = get_date_string()
    return f"{LOG_FOLDER}/{log_type}_{date_str}.log"

def log_to_file(log_type, message):
    try:
        filename = get_log_filename(log_type)
        if log_type == "runtime":
            pass
        else:
            pass
    except:
        pass

def tprint(printstatus, message):
    try:
        rtc = machine.RTC().datetime()
        timestamp = f"{rtc[0]:04d}-{rtc[1]:02d}-{rtc[2]:02d} {rtc[4]:02d}:{rtc[5]:02d}:{rtc[6]:02d}"
    except:
        timestamp = "0000-00-00 00:00:00"
    full_message = f"[{timestamp}] - [{printstatus}]: {message}."
    print(full_message)
    log_to_file("runtime", full_message)
    return int(time.time())

def eprint(printstatus, message):
    try:
        rtc = machine.RTC().datetime()
        timestamp = f"{rtc[0]:04d}-{rtc[1]:02d}-{rtc[2]:02d} {rtc[4]:02d}:{rtc[5]:02d}:{rtc[6]:02d}"
    except:
        timestamp = "0000-00-00 00:00:00"
    full_message = f"[{timestamp}] - [{printstatus}]: {message}."
    log_to_file("error", full_message)
    return None

def download_file(url, filename):
    tprint(PRINTSTATUS.INFO, f"Downloading {filename}...")
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            with open(filename, "w") as f:
                f.write(r.text)
            tprint(PRINTSTATUS.SUCCESS, f"{filename} OK")
            return True
        else:
            tprint(PRINTSTATUS.ERROR, f"Failed: status {r.status_code}")
            return False
    except Exception as e:
        tprint(PRINTSTATUS.ERROR, f"Download error: {e}")
        return False


## Procedural functions
def startup_logo():
    logo = r"""
                      $$\                     
                      $$ |                    
 $$$$$$\  $$\   $$\ $$$$$$\    $$$$$$\        
 \____$$\ $$ |  $$ |\_$$  _|  $$  __$$\       
 $$$$$$$ |$$ |  $$ |  $$ |    $$ /  $$ |      
$$  __$$ |$$ |  $$ |  $$ |$$\ $$ |  $$ |      
\$$$$$$$ |\$$$$$$  |  \$$$$  |\$$$$$$  |      
 \_______| \______/    \____/  \______/                                    
    """
    print(logo)
    print("        AUTO FIRMWARE")
    print("        Booting...\n")

def reset_logs():
    try:
        for log_type in ["runtime", "error"]:
            filename = get_log_filename(log_type)
            with open(filename, "w") as f:
                f.write("")
    except:
        pass

def _try_wifi(ssid, password, label, max_retries=3):
    """Try to connect to a single WiFi network up to `max_retries` times.

    Returns the WLAN object on success, or None on total failure.
    The `label` is only used in log messages (e.g. "Primary" or "Backup")
    so the serial monitor shows which network is being attempted.

    We explicitly disconnect before every retry because the ESP32's WiFi
    driver can get stuck in a half-connected state after a failed attempt,
    where connect() silently does nothing. Without the disconnect, retries
    2 and 3 often never actually try to reconnect.
    """
    retry_count = 0
    while retry_count < max_retries:
        try:
            tprint(PRINTSTATUS.INFO,
                   f"Connecting to {label} WiFi '{ssid}'... (Attempt {retry_count + 1}/{max_retries})")
            wlan = network.WLAN(network.STA_IF)
            wlan.active(True)

            # Disconnect from any previous attempt before trying again.
            # Wrapped in try/except because disconnect() can raise if the
            # interface was never connected in the first place.
            try:
                wlan.disconnect()
            except Exception:
                pass
            time.sleep(1)

            wlan.connect(ssid, password)
            timeout = 15
            connected = False
            for i in range(timeout):
                if wlan.isconnected():
                    connected = True
                    break
                time.sleep(1)

            if connected:
                tprint(PRINTSTATUS.SUCCESS,
                       f"Connected to {label} WiFi: {wlan.ifconfig()}")
                return wlan

            tprint(PRINTSTATUS.INFO, f"{label} WiFi connection timeout")
            retry_count += 1
            if retry_count < max_retries:
                tprint(PRINTSTATUS.INFO, "Retrying in 5 seconds...")
                time.sleep(5)

        except Exception as e:
            error_msg = f"{label} WiFi error: {e}"
            tprint(PRINTSTATUS.ERROR, error_msg)
            eprint(PRINTSTATUS.ERROR, error_msg)
            retry_count += 1
            if retry_count < max_retries:
                tprint(PRINTSTATUS.INFO, "Retrying in 5 seconds...")
                time.sleep(5)

    return None


def start_wifi():
    """Connect to the primary WiFi. If all 3 attempts fail, fall back to
    the backup WiFi and try 3 more times. If BOTH fail, reset the device.

    This keeps the scanner online even when the main router is down,
    rebooting, or has changed its SSID/password — the device will
    automatically switch to the backup network without human intervention.
    """
    # ---- 1) Try the primary network ----------------------------------------
    wlan = _try_wifi(SSID, PASSWORD, "Primary", max_retries=3)
    if wlan is not None:
        return True

    tprint(PRINTSTATUS.WARN, "Primary WiFi failed after 3 attempts — switching to backup")

    # ---- 2) Fall back to the backup network --------------------------------
    wlan = _try_wifi(BACKUP_SSID, BACKUP_PASSWORD, "Backup", max_retries=3)
    if wlan is not None:
        return True

    # ---- 3) Both networks failed — reboot and try again ---------------------
    error_msg = "Both primary and backup WiFi failed after 3 attempts each"
    tprint(PRINTSTATUS.ERROR, error_msg)
    eprint(PRINTSTATUS.ERROR, error_msg)
    error_msg = "Resetting device..."
    tprint(PRINTSTATUS.WARN, error_msg)
    eprint(PRINTSTATUS.WARN, error_msg)
    machine.reset()
    return False

def sync_time():
    tprint(PRINTSTATUS.INFO, "Syncing time via NTP...")
    try:
        ntptime.host = "pool.ntp.org"
        ntptime.settime()
        t = time.localtime(time.time() + 8 * 3600)
        machine.RTC().datetime((t[0], t[1], t[2], t[6] + 1, t[3], t[4], t[5], 0))
        tprint(PRINTSTATUS.SUCCESS, f"Time: {t[3]:02d}:{t[4]:02d}:{t[5]:02d}")
    except Exception as e:
        tprint(PRINTSTATUS.ERROR, f"NTP sync failed: {e}")

def fech_old_version_info():
    tprint(PRINTSTATUS.INFO, "Reading old version...")
    try:
        with open(VERSION_FILE, "r") as f:
            version_info = f.read().strip()
            if version_info:
                old_status = version_info[0:4]
                old_version = version_info[7:]
                tprint(PRINTSTATUS.OK, "Successfully read old version")
                return old_status, old_version
        return None, None
    except Exception as e:
        error_msg = f"Error reading old version: {e}"
        tprint(PRINTSTATUS.ERROR, error_msg)
        eprint(PRINTSTATUS.ERROR, error_msg)
        return None, None

def fech_version_info():
    tprint(PRINTSTATUS.INFO, "Fetching latest version...")
    try:
        response = requests.get(VERSION_URL, timeout=10)
        if response.status_code == 200:
            response_text = response.text.strip()
            if response_text:
                response_status = response_text[0:4]
                response_version = response_text[7:]
                tprint(PRINTSTATUS.INFO, f"Response version: {response_text}")
                tprint(PRINTSTATUS.OK, "Successfully fetched version")
                return response_status, response_version
        return None, None
    except Exception as e:
        error_msg = f"Error fetching version: {e}"
        tprint(PRINTSTATUS.ERROR, error_msg)
        eprint(PRINTSTATUS.ERROR, error_msg)
        return None, None

def check_for_updates():
    """Check the remote version file and, if a newer patch is available,
    download the three firmware files that make up the runtime:

        • configs/network_config.py  (WiFi credentials)
        • driver.py                  (hardware drivers)
        • main.py                    (main firmware loop)

    The order matters: we download network_config.py FIRST so that if
    the WiFi credentials changed upstream, they are already in place
    before driver.py and main.py are written. If any download fails, we
    abort the update and return False so the caller can keep running the
    current firmware instead of booting a half-updated device.

    After all three files are updated, the device resets so the new code
    is loaded fresh from flash.
    """
    tprint(PRINTSTATUS.INFO, "Checking for updates...")
    try:
        old_status, old_version = fech_old_version_info()
        status, version = fech_version_info()
        if not status or not version or not old_version:
            tprint(PRINTSTATUS.ERROR, "Could not fetch version info properly")
            return False
        old_version_patch = old_version.rsplit('.', 1)[-1]
        version_patch = version.rsplit('.', 1)[-1]

        if status in ("live", "test") and int(version_patch) > int(old_version_patch):
            tprint(PRINTSTATUS.SUCCESS, f"New version available: {version}")
            tprint(PRINTSTATUS.INFO, "Updating version...")
            with open(VERSION_FILE, "w") as f:
                f.write(f"{status} - {version}")

            # ---- 1) network_config.py -------------------------------------
            # Pulled first so any credential change is applied before the
            # other files are written. If this fails we abort — a half-
            # updated device with a new main.py but an old WiFi config
            # would be worse than staying on the current version.
            tprint(PRINTSTATUS.INFO, "Downloading configs/network_config.py...")
            if download_file(WIFI_URL, "configs/network_config.py"):
                tprint(PRINTSTATUS.SUCCESS, "network_config.py updated")
            else:
                tprint(PRINTSTATUS.ERROR, "Failed to download network_config.py")
                return False

            # ---- 2) driver.py ---------------------------------------------
            tprint(PRINTSTATUS.INFO, "Downloading driver.py...")
            if download_file(DRIVER_URL, "driver.py"):
                tprint(PRINTSTATUS.SUCCESS, "driver.py updated")
            else:
                tprint(PRINTSTATUS.ERROR, "Failed to download driver.py")
                return False

            # ---- 3) main.py -----------------------------------------------
            tprint(PRINTSTATUS.INFO, "Downloading main.py...")
            if download_file(MAIN_URL, "main.py"):
                tprint(PRINTSTATUS.SUCCESS, "All files updated")
                time.sleep(2)
                machine.reset()
            else:
                tprint(PRINTSTATUS.ERROR, "Failed to download main.py")
                return False
        else:
            tprint(PRINTSTATUS.INFO, "No updates available")
            tprint(PRINTSTATUS.INFO, "Firmware Ready — Starting main.py...")
            time.sleep(1)
            return True
    except Exception as e:
        error_msg = f"Error: {e}"
        tprint(PRINTSTATUS.ERROR, error_msg)
        eprint(PRINTSTATUS.ERROR, error_msg)
        return False

def fail_safe():
    tprint(PRINTSTATUS.WARN, "FAIL-SAFE TRIGGERED")
    time.sleep(1000)


## Main process loop
def process():
    reset_logs()
    fail_safe_counter = 0
    while True:
        try:
            startup_logo()
            tprint(PRINTSTATUS.INFO, "Starting up...")
            start_wifi()
            sync_time()
            check_for_updates()
            if fail_safe_counter >= 10:
                tprint(PRINTSTATUS.WARN, "FAIL-SAFE: Too many restarts!")
                fail_safe()
                fail_safe_counter = 0
            else:
                tprint(PRINTSTATUS.INFO, f"Running main.py... (Attempt {fail_safe_counter + 1}/10)")
                time.sleep(1)
                import main
                main.main()
            fail_safe_counter += 1
        except Exception as e:
            error_msg = f"Startup error: {e}"
            tprint(PRINTSTATUS.ERROR, error_msg)
            eprint(PRINTSTATUS.ERROR, error_msg)
            tprint(PRINTSTATUS.WARN, "Retrying in 10 seconds...")
            eprint(PRINTSTATUS.WARN, "Retrying in 10 seconds...")
            time.sleep(10)
            machine.reset()


if __name__ == "__main__":
    process()