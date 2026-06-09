
# ...existing code...

# All imports below here
import os, sys, threading, urllib.request, webbrowser, logging, time, json, re, subprocess, socket, ssl, csv, tempfile, traceback, platform
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory, Response
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress

import websocket
import requests

try:
    import psutil
except Exception:
    psutil = None

# Import matrix logic for unified scanning
try:
    import omni_matrix_logic
    HAS_MATRIX = True
except Exception as e:
    HAS_MATRIX = False
    omni_matrix_logic = None
    print("[IMPORT ERROR] omni_matrix_logic import failed:")
    import traceback
    traceback.print_exc()

# Thread pool for background tasks (limited to 2 concurrent to avoid overload)
_background_executor = ThreadPoolExecutor(max_workers=2)

FROZEN = getattr(sys, "frozen", False)
SCRIPT_DIR = Path(getattr(sys, "executable", __file__)).resolve().parent if FROZEN else Path(__file__).resolve().parent
ASSET_DIR = Path(getattr(sys, "_MEIPASS", SCRIPT_DIR)).resolve() if FROZEN else SCRIPT_DIR
CWD = SCRIPT_DIR
CACHE = CWD / "units_cache.json"
SCAN_RESULTS = CWD / "scan_results.json"
CSV_VIEW = CWD / "units_view.csv"
PORT = int(os.getenv("OMNI_PORT", "8088"))
log = logging.getLogger("omni_upgrade")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _windows_hidden_subprocess_kwargs() -> dict:
    if platform.system().lower() != "windows":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }

app = Flask(__name__)

# --- Global error handler for full traceback logging ---
@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    print("\n--- Unhandled Exception ---")
    traceback.print_exc()
    print("--------------------------\n")
    code = getattr(e, 'code', 500)
    return jsonify({"ok": False, "error": str(e)}), code

# ...existing code...


# ...existing code...

# Register /api/poll endpoint after app and config
# Add /api/poll endpoint after app is created and configured

# Place this after the Flask app object is created
# OmniMatrix Upgrade Server (v7.6y)
# - Fixes firmware filtering behavior (handled in UI)
# - Adds /api/login to send systeminfo-login before opening device UI
# - Retains factory reset option and upload logic from v7.6x
# - Unified scan: combines upgrade + matrix data into single shared cache
import os, sys, threading, urllib.request, webbrowser, logging, time, json, re, subprocess, socket, ssl, csv, tempfile, traceback
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory, Response
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress

import websocket
import requests

try:
    import psutil
except Exception:
    psutil = None

# Import matrix logic for unified scanning
try:
    import omni_matrix_logic
    HAS_MATRIX = True
except Exception as e:
    HAS_MATRIX = False
    omni_matrix_logic = None

FROZEN = getattr(sys, "frozen", False)
SCRIPT_DIR = Path(getattr(sys, "executable", __file__)).resolve().parent if FROZEN else Path(__file__).resolve().parent
ASSET_DIR = Path(getattr(sys, "_MEIPASS", SCRIPT_DIR)).resolve() if FROZEN else SCRIPT_DIR
CWD = SCRIPT_DIR
CACHE = CWD / "units_cache.json"
SCAN_RESULTS = CWD / "scan_results.json"
CSV_VIEW = CWD / "units_view.csv"
PORT = int(os.getenv("OMNI_PORT", "8088"))
log = logging.getLogger("omni_upgrade")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Configure matrix logic once app config is loaded
def _configure_matrix_logic_from_app():
    if not HAS_MATRIX:
        return
    try:
        omni_matrix_logic.configure(
            username=app.config.get('USERNAME', 'admin'),
            password=app.config.get('PASSWORD', 'Atlona'),
            ws_port=int(app.config.get('WS_PORT', 80)),
            ws_path=app.config.get('WS_PATH', '/wsapp/'),
            timeout=float(app.config.get('TIMEOUT', 4.0)),
        )
    except Exception as e:
        log.info("matrix_logic configure failed: %s", e)

# Early config loading functions (needed before app init)
def _load_config():
    """Load configuration from config.json, with fallback to environment variables."""
    config_file = CWD / "config.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return {
                    "USERNAME": cfg.get("username", os.getenv("OMNI_USER", "admin")),
                    "PASSWORD": cfg.get("password", os.getenv("OMNI_PASS", "password")),
                    "FALLBACK_PASSWORD": cfg.get("fallback_password", os.getenv("OMNI_FALLBACK_PASS", "Atlona")),
                    "WS_PORT": int(cfg.get("ws_port", os.getenv("OMNI_WS_PORT", 80))),
                    "TIMEOUT": float(cfg.get("timeout", os.getenv("OMNI_WS_TIMEOUT", 4.5))),
                    "UPLOAD_CONCURRENCY": int(cfg.get("concurrency", os.getenv("OMNI_UP_CONC", 6))),
                    "FIRMWARE_PATH": cfg.get("firmware_path", "")
                }
        except Exception as e:
            log.info("config.json load failed: %s", e)
    # Fallback to environment variables
    return {
        "USERNAME": os.getenv("OMNI_USER", "admin"),
        "PASSWORD": os.getenv("OMNI_PASS", "password"),
        "FALLBACK_PASSWORD": os.getenv("OMNI_FALLBACK_PASS", "Atlona"),
        "WS_PORT": int(os.getenv("OMNI_WS_PORT", 80)),
        "TIMEOUT": float(os.getenv("OMNI_WS_TIMEOUT", 4.5)),
        "UPLOAD_CONCURRENCY": int(os.getenv("OMNI_UP_CONC", 6)),
        "FIRMWARE_PATH": ""
    }

def _save_config(cfg):
    """Save configuration to config.json."""
    config_file = CWD / "config.json"
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        log.info("config.json save failed: %s", e)

app = Flask(__name__)
# Load config from file first, then apply
_cfg = _load_config()
log.info("Loaded config: username=%s, ws_port=%s, timeout=%s, concurrency=%s, firmware_path=%s", 
         _cfg.get('USERNAME'), _cfg.get('WS_PORT'), _cfg.get('TIMEOUT'), 
         _cfg.get('UPLOAD_CONCURRENCY'), _cfg.get('FIRMWARE_PATH') or '(current directory)')
app.config.update({
    'USERNAME': _cfg['USERNAME'],
    'PASSWORD': _cfg['PASSWORD'],
    'FALLBACK_PASSWORD': _cfg['FALLBACK_PASSWORD'],
    'WS_PORT': _cfg['WS_PORT'],
    'WS_PATH': "/wsapp/",
    'TIMEOUT': _cfg['TIMEOUT'],
    'WS_STRICT': os.getenv("OMNI_WS_STRICT","0") in ("1","true","True","YES","yes"),
    'UPLOAD_CONCURRENCY': _cfg['UPLOAD_CONCURRENCY'],
    'FIRMWARE_PATH': _cfg['FIRMWARE_PATH'],
})

try:
    import omni_matrix_logic
    HAS_MATRIX = True
except Exception as e:
    HAS_MATRIX = False
    omni_matrix_logic = None

# Endpoint to reload encoders/decoders from cache file (must be after app is created)
@app.route("/api/reload_cache", methods=["POST"])
def api_reload_cache():
    try:
        if omni_matrix_logic:
            omni_matrix_logic._load_cache()
            return jsonify({"ok": True, "message": "Cache reloaded from units_cache.json"})
        else:
            return jsonify({"ok": False, "error": "matrix_logic not available"}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

# Initialize matrix logic configuration

_configure_matrix_logic_from_app()
# Ensure in-memory cache is loaded for polling
if HAS_MATRIX and omni_matrix_logic:
    try:
        omni_matrix_logic._load_cache()
    except Exception as e:
        log.info("matrix_logic _load_cache failed: %s", e)

# Verify cache on startup in background (non-blocking)
def _trigger_startup_verification():
    """Trigger cache verification after short delay to let server fully start"""
    time.sleep(1.0)  # Give server time to fully initialize
    _verify_cache_in_background()

threading.Thread(target=_trigger_startup_verification, daemon=True).start()
log.info("[STARTUP] Cache verification will run in background")

# ---------------- CSV helpers ----------------
def _excel_safe_text(value: str) -> str:
    text = "" if value is None else str(value)
    if not text:
        return text
    # Prevent Excel from parsing as formula or number.
    return "'" + text

def _write_csv_atomic(units, target_path: Path, retries: int = 6, base_delay: float = 0.35) -> bool:
    header = ["IP","MAC","Hostname","Type","Model","Version","SerialNumber"]
    for attempt in range(retries):
        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", delete=False, dir=str(target_path.parent)) as tf:
                tmp = Path(tf.name)
                w = csv.writer(tf)
                w.writerow(header)
                for u in units:
                    w.writerow([
                        u.get("ip",""),
                        u.get("mac",""),
                        u.get("hostname",""),
                        u.get("type",""),
                        u.get("model",""),
                        u.get("version",""),
                        _excel_safe_text(u.get("serialnumber","")),
                    ])
            os.replace(str(tmp), str(target_path))
            return True
        except PermissionError as e:
            if tmp and tmp.exists():
                try: tmp.unlink()
                except Exception: pass
            delay = base_delay * (1 + attempt)
            log.info("CSV write locked (attempt %d/%d): %s; retrying in %.2fs", attempt+1, retries, e, delay)
            time.sleep(delay)
        except Exception as e:
            if tmp and tmp.exists():
                try: tmp.unlink()
                except Exception: pass
            log.info("write csv failed (non-retriable): %s", e)
            return False
    log.info("write csv failed: file locked after %d attempts", retries)
    return False

def _stream_csv_from_units(units):
    header = ["IP","MAC","Hostname","Type","Model","Version","SerialNumber"]
    def gen():
        yield ",".join(header) + "\r\n"
        for u in units:
            row = [
                u.get("ip",""),
                u.get("mac",""),
                u.get("hostname",""),
                u.get("type",""),
                u.get("model",""),
                u.get("version",""),
                _excel_safe_text(u.get("serialnumber","")),
            ]
            def esc(x):
                x = str(x)
                if any(c in x for c in [',','"','\r','\n']):
                    x = '"' + x.replace('"','""') + '"'
                return x
            yield ",".join(esc(x) for x in row) + "\r\n"
    return Response(gen(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=units_view.csv"})

def _load_cache():
    """Load devices from scan_results.json (new format) or units_cache.json (legacy)"""
    try:
        # Try legacy format first (units_cache.json) - for testing
        if CACHE.exists():
            with open(CACHE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, list) and d:
                log.info(f"[CACHE] Loaded {len(d)} units from units_cache.json (list format)")
                return d
            elif isinstance(d, dict) and "units" in d and d["units"]:
                log.info(f"[CACHE] Loaded {len(d['units'])} units from units_cache.json (dict format)")
                return d["units"]
        # Fall back to new format (scan_results.json)
        if SCAN_RESULTS.exists():
            with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                d = json.load(f)
            devices = d.get("devices", [])
            if isinstance(devices, list) and devices:
                log.info(f"[CACHE] Loaded {len(devices)} units from scan_results.json")
                return devices
    except Exception as e:
        log.warning("_load_cache failed: %s", e)
    return []

def _save_cache(units):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(units, f, indent=2)
    except Exception as e:
        log.info("save cache failed: %s", e)

def _password_candidates(preferred_pwd: str = None):
    primary = app.config.get('PASSWORD', '')
    fallback = app.config.get('FALLBACK_PASSWORD', '')
    candidates = []
    for pwd in [preferred_pwd, primary, fallback]:
        if pwd and pwd not in candidates:
            candidates.append(pwd)
    return candidates

def _device_credentials(ip: str, cache_devices_map: dict = None):
    cache_map = cache_devices_map or {d.get("ip"): d for d in (_load_cache() or []) if d.get("ip")}
    device = cache_map.get(ip, {})
    user = device.get("username") or app.config.get('USERNAME', 'admin')
    preferred_pwd = device.get("password") or app.config.get('PASSWORD', 'password')
    return user, preferred_pwd, device

def _write_csv(units):
    _ = _write_csv_atomic(units, CSV_VIEW)

def _load_scan_results_file():
    if not SCAN_RESULTS.exists():
        return None
    try:
        with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.info("scan_results load failed: %s", e)
        return None

def _hydrate_matrix_from_scan(data):
    if not (HAS_MATRIX and data):
        return
    try:
        omni_matrix_logic.clear_state()
    except Exception:
        return
    encoders = data.get("encoders") or []
    decoders = data.get("decoders") or []
    # Fallback: derive enc/dec from devices if top-level lists missing
    if not encoders and not decoders:
        devs = data.get("devices") or []
        encoders = [u for u in devs if (u.get("role") == "encoder")]
        decoders = [u for u in devs if (u.get("role") == "decoder")]
    try:
        for e in encoders:
            omni_matrix_logic._encoders[e.get("ip")] = {
                "ip": e.get("ip"),
                "mac": e.get("mac"),
                "hostname": e.get("hostname") or e.get("host"),
                "firmwareversion": e.get("version") or e.get("fw"),
                "model": e.get("model"),
                "serial": e.get("serialnumber") or e.get("serial"),
                "v_mcast": e.get("v_mcast"),
                "v_port": e.get("v_port"),
                "a_mcast": e.get("a_mcast"),
                "a_port": e.get("a_port"),
            }
        for d in decoders:
            omni_matrix_logic._decoders[d.get("ip")] = {
                "ip": d.get("ip"),
                "mac": d.get("mac"),
                "hostname": d.get("hostname") or d.get("host"),
                "firmwareversion": d.get("version") or d.get("fw"),
                "model": d.get("model"),
                "serial": d.get("serialnumber") or d.get("serial"),
                "ip1_addr": d.get("ip1_addr"),
                "ip1_port": d.get("ip1_port"),
                "ip3_addr": d.get("ip3_addr"),
                "ip3_port": d.get("ip3_port"),
            }
    except Exception as e:
        log.info("hydrate matrix state failed: %s", e)

def _ws_get_decoder_inputs(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, attempts: int = 5, delay: float = 0.5):
    """Fetch decoder ip_input1/ip_input3 via WebSocket config_get with simple retry.
    Try primary password first, then fallback password if primary fails.
    Returns dict: {ip1_addr, ip1_port, ip3_addr, ip3_port} or {} on failure.
    """
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)
    
    for attempt_pwd in passwords_to_try:
        for i in range(max(1, attempts)):
            try:
                url = _ws_url(ip, ws_port, ws_path)
                req = {"id":"ip_input-get","username":user,"password":attempt_pwd,"config_get":"ip_input"}
                resp = _ws_send_recv(url, req, timeout=min(timeout, 2.0))
                if not resp or resp.get("error"):
                    raise ValueError("empty resp or error")
                cfg = (resp or {}).get("config") or []
                lst = cfg if isinstance(cfg, list) else (cfg.get("ip_input") or [])
                ip1 = next((e for e in lst if e.get("name") == "ip_input1"), {})
                ip3 = next((e for e in lst if e.get("name") == "ip_input3"), {})
                return {
                    "ip1_addr": ((ip1.get("multicast") or {}).get("address")),
                    "ip1_port": ip1.get("port"),
                    "ip3_addr": ((ip3.get("multicast") or {}).get("address")),
                    "ip3_port": ip3.get("port"),
                }
            except Exception:
                if i < attempts-1:
                    time.sleep(delay)
                    continue
                # This attempt failed, try next password
                break
    
    # All passwords and retries exhausted
    return {}

def _update_scan_results_decoder(ip: str, fields: dict):
    """Persist updated decoder multicast fields into scan_results.json."""
    data = _load_scan_results_file() or {}
    changed = False
    for key in ("decoders", "devices"):
        arr = data.get(key)
        if arr is None:
            arr = []
            data[key] = arr
        if not isinstance(arr, list):
            continue
        found = False
        for u in arr:
            if (u or {}).get("ip") == ip:
                found = True
                for k in ("ip1_addr","ip1_port","ip3_addr","ip3_port"):
                    if k in fields and fields[k] is not None:
                        if u.get(k) != fields[k]:
                            u[k] = fields[k]
                            changed = True
                break
        if not found:
            new_entry = {"ip": ip}
            for k in ("ip1_addr","ip1_port","ip3_addr","ip3_port"):
                if k in fields and fields[k] is not None:
                    new_entry[k] = fields[k]
            arr.append(new_entry)
            changed = True
    if changed:
        data["timestamp"] = time.time()
        try:
            with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.info("scan_results update failed: %s", e)

_cache_verification_in_progress = False
_cache_last_verified = 0
_cache_startup_verified = False

def _verify_cache_in_background():
    """Verify cached device list by scanning cached IPs with tight timeouts (non-blocking background task)"""
    global _cache_verification_in_progress, _cache_last_verified
    
    if _cache_verification_in_progress:
        return
    
    cached_units = _load_cache()
    if not cached_units:
        return
    
    cached_ips = [u.get("ip") for u in cached_units if u.get("ip")]
    if not cached_ips:
        return
    
    def do_verify():
        global _cache_verification_in_progress, _cache_last_verified
        _cache_verification_in_progress = True
        try:
            user = app.config['USERNAME']
            default_pwd = app.config['PASSWORD']
            ws_port = app.config['WS_PORT']
            ws_path = app.config['WS_PATH']
            timeout = 3.0  # Increased timeout for startup verification

            
            updated_count = 0
            
            def verify_unit(cached_unit):
                ip = cached_unit.get("ip")
                if not ip:
                    return None
                try:
                    # Get device-specific password from cache, fall back to default if not stored
                    pwd = cached_unit.get("password") or default_pwd
                    
                    # Try multiple times to reach device - startup may have connectivity delays
                    for attempt in range(3):
                        try:
                            # Quick systeminfo query to get current version/model/hostname
                            url = _ws_url(ip, ws_port, ws_path)
                            payload = {"id":"systeminfo-get","username":user,"password":pwd,"config_get":"systeminfo"}
                            resp = _ws_send_recv(url, payload, timeout=timeout)
                            
                            if not resp or resp.get("error"):
                                if attempt < 2:
                                    time.sleep(0.5)
                                    continue
                                return None
                            
                            cfg = (resp or {}).get("config") or {}
                            
                            # Update device info - only add non-None values to preserve existing data
                            updates = {}
                            fw_version = cfg.get("firmwareversion") or cfg.get("version")
                            if fw_version:
                                updates["version"] = fw_version
                                updates["firmwareversion"] = fw_version  # Update both field names
                            
                            hostname = cfg.get("hostname")
                            if hostname:
                                  updates["hostname"] = hostname.strip()
                            
                            sn = cfg.get("serialnumber") or cfg.get("serial")
                            if sn:
                                  updates["serialnumber"] = sn.strip()

                            ntp_server = (cfg.get("ntpserver") or cfg.get("ntp_server") or cfg.get("ntpServer") or "").strip()
                            if ntp_server:
                                updates["ntp_server"] = ntp_server

                            try:
                                tz_payload = {"id":"timezone-get","username":user,"password":pwd,"config_get":"timezone"}
                                tz_resp = _ws_send_recv(url, tz_payload, timeout=min(timeout, 1.5))
                                tz_cfg = (tz_resp or {}).get("config") or {}
                                if isinstance(tz_cfg, dict):
                                    timezone = (tz_cfg.get("timezone") or "").strip()
                                    active_timezone = (tz_cfg.get("active_timezone") or timezone).strip()
                                    if timezone:
                                        updates["timezone"] = timezone
                                    if active_timezone:
                                        updates["active_timezone"] = active_timezone
                                    if tz_resp:
                                        updates["timezone_details"] = tz_resp
                            except Exception:
                                pass
                            
                            # Try USB info if device is USB-capable
                            model = (cached_unit.get("model") or "").lower()
                            usb_models = ["hw-omni-e4521", "hw-omni-d4521", "hw-omni-e4511", "hw-omni-d4511", "4521", "4511"]
                            if any(m in model for m in usb_models):
                                try:
                                    usb_payload = {"id":"usb_icron-get","username":user,"password":pwd,"config_get":"usb_icron"}
                                    usb_resp = _ws_send_recv(url, usb_payload, timeout=timeout)
                                    usb_cfg = (usb_resp or {}).get("config") or {}
                                    if usb_cfg.get("type"):
                                        updates["usb_type"] = usb_cfg.get("type")
                                    if usb_cfg.get("macaddress"):
                                        updates["usb_mac"] = usb_cfg.get("macaddress")
                                except Exception:
                                    pass  # USB info is optional
                            
                            if not updates:
                                return None
                            
                            return {"ip": ip, "updates": updates}
                        except Exception as retry_err:
                            if attempt < 2:
                                time.sleep(0.5)
                                continue
                            raise
                except Exception as e:
                    return None
            
            # Verify in parallel with thread pool, fail fast on timeouts
            with ThreadPoolExecutor(max_workers=min(16, len(cached_ips))) as executor:
                futures = {executor.submit(verify_unit, unit): unit for unit in cached_units if unit.get("ip")}
                for fut in as_completed(futures, timeout=15):  # Overall timeout
                    try:
                        result = fut.result(timeout=0.1)  # Individual result timeout
                        if result:
                            ip = result["ip"]
                            updates = result["updates"]
                            # Find and update the unit in cache
                            for unit in cached_units:
                                if unit.get("ip") == ip:
                                    timezone_details = updates.pop("timezone_details", None)
                                    # Update top-level fields
                                    unit.update(updates)
                                    # Also update nested details.systeminfo.config if present
                                    if "hostname" in updates and unit.get("details", {}).get("systeminfo", {}).get("config"):
                                        unit["details"]["systeminfo"]["config"]["hostname"] = updates["hostname"]
                                    if "firmwareversion" in updates and unit.get("details", {}).get("systeminfo", {}).get("config"):
                                        unit["details"]["systeminfo"]["config"]["firmwareversion"] = updates["firmwareversion"]
                                    if timezone_details:
                                        unit.setdefault("details", {})["timezone"] = timezone_details
                                    updated_count += 1
                                    break
                    except Exception:
                        pass
            
            # Save updated cache
            if updated_count > 0:
                try:
                    _save_cache(cached_units)
                except Exception as e:
                    log.warning(f"[VERIFY_CACHE] Failed to save cache: {e}")
        
        except Exception as e:
            log.error(f"[VERIFY_CACHE] Verification failed: {e}")
        finally:
            _cache_verification_in_progress = False
            _cache_last_verified = time.time()
    
    # Run verification in background thread (don't block the request)
    thread = threading.Thread(target=do_verify, daemon=True)
    thread.start()


def _update_scan_results_from_matrix_state(state: dict):
    """Persist decoder fields from omni_matrix_logic.list_state into scan_results."""
    if not state: return
    decs = state.get("decoders") or []
    data = _load_scan_results_file() or {}
    changed = False
    for key in ("decoders", "devices"):
        arr = data.get(key)
        if arr is None:
            arr = []
            data[key] = arr
        if not isinstance(arr, list):
            continue
        for d in decs:
            ip = d.get("ip")
            if not ip: continue
            found = False
            for u in arr:
                if (u or {}).get("ip") == ip:
                    found = True
                    for k in ("ip1_addr","ip1_port","ip3_addr","ip3_port"):
                        if d.get(k) is not None and u.get(k) != d.get(k):
                            u[k] = d.get(k)
                            changed = True
                    break
            if not found:
                entry = {"ip": ip}
                for k in ("ip1_addr","ip1_port","ip3_addr","ip3_port"):
                    if d.get(k) is not None:
                        entry[k] = d.get(k)
                arr.append(entry)
                changed = True
    if changed:
        data["timestamp"] = time.time()
        try:
            with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.info("scan_results update (matrix state) failed: %s", e)

# ---------------- basic routes ----------------
@app.route("/__health")
def __health(): return "ok", 200

# --------------- matrix UI entry ----------------
@app.route("/matrix")
def matrix_index():
    idx = ASSET_DIR / "ui" / "matrix" / "index.html"
    if idx.exists():
        return send_file(str(idx), mimetype="text/html; charset=utf-8")
    return "<h1>Matrix UI not found</h1>", 404

@app.route("/matrix/usb")
def usb_matrix_index():
    idx = ASSET_DIR / "ui" / "matrix" / "usb.html"
    if idx.exists():
        return send_file(str(idx), mimetype="text/html; charset=utf-8")
    return "<h1>USB Matrix UI not found</h1>", 404

@app.route("/")
def index():
    idx = ASSET_DIR / "ui" / "index.html"
    if idx.exists(): return send_file(str(idx), mimetype="text/html; charset=utf-8")
    return "<h1>Omni Upgrade Server</h1><p>UI not found (ui/index.html). Backend API available.</p>"

@app.route("/ui/<path:filename>")
def ui_files(filename): return send_from_directory(ASSET_DIR / "ui", filename)

# ---------------- adapters ----------------
import ipaddress as _ipa
def _is_private_ipv4(ip: str)->bool:
    try: return _ipa.IPv4Address(ip).is_private
    except Exception: return False

def _adapters_windows(active_only=True):
    out = []
    try:
        txt = subprocess.check_output(["ipconfig","/all"], text=True, encoding="utf-8", errors="ignore", **_windows_hidden_subprocess_kwargs())
    except Exception:
        return out
    blocks = re.split(r"\r?\n\r?\n", txt)
    for b in blocks:
        if active_only and re.search(r"(?mi)^\s*Media\s*State\s*.*:\s*Media\s*disconnected\s*$", b):
            continue
        name_m = re.search(r"(?mi)^(?:.*adapter)\s+(.+?):\s*$", b)
        ipv4_m = re.search(r"(?mi)^\s*IPv4[^:]*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", b)
        mask_m = re.search(r"(?mi)^\s*Subnet\s*Mask[^:]*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", b)
        if not (name_m and ipv4_m and mask_m): continue
        name = name_m.group(1).strip(); ip = ipv4_m.group(1); mask = mask_m.group(1)
        if ip.startswith("169.254.") or not _is_private_ipv4(ip): continue
        try:
            net = _ipa.IPv4Network(f"{ip}/{mask}", strict=False)
            cidr = f"{net.network_address}/{net.prefixlen}"
            scan = f"{str(net.network_address).rsplit('.',1)[0]}.1-254" if net.prefixlen <= 24 else cidr
            out.append({"name": name, "ip": ip, "netmask": mask, "cidr": cidr, "scan": scan})
        except Exception: pass
    return out

def _adapters_psutil(active_only=True):
    out = []
    if not psutil: return out
    try:
        addrs = psutil.net_if_addrs(); stats = psutil.net_if_stats()
    except Exception:
        return out
    for name, lst in addrs.items():
        if active_only and name in stats and not stats[name].isup: continue
        for a in lst:
            fam = getattr(a, "family", None)
            if fam == getattr(psutil, "AF_LINK", 17): continue
            if fam == 2 and a.address and a.netmask:
                ip = a.address; mask = a.netmask
                if ip.startswith("169.254.") or not _is_private_ipv4(ip): continue
                try:
                    net = _ipa.IPv4Network(f"{ip}/{mask}", strict=False)
                    cidr = f"{net.network_address}/{net.prefixlen}"
                    scan = f"{str(net.network_address).rsplit('.',1)[0]}.1-254" if net.prefixlen <= 24 else cidr
                    out.append({"name": name, "ip": ip, "netmask": mask, "cidr": cidr, "scan": scan})
                except Exception: pass
    return out

def _adapters_route_print():
    out = []
    try:
        txt = subprocess.check_output(["route","print","-4"], text=True, encoding="utf-8", errors="ignore", **_windows_hidden_subprocess_kwargs())
    except Exception:
        return out
    for line in txt.splitlines():
        m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+\S+\s+(\d+\.\d+\.\d+\.\d+)", line)
        if not m: continue
        network, mask, iface_ip = m.groups()
        try:
            net = _ipa.IPv4Network(f"{network}/{mask}", strict=False)
            if net.prefixlen < 8 or net.prefixlen > 30: continue
            if iface_ip.startswith("169.254.") or not _is_private_ipv4(iface_ip): continue
            cidr = f"{net.network_address}/{net.prefixlen}"
            scan = f"{str(net.network_address).rsplit('.',1)[0]}.1-254" if net.prefixlen <= 24 else cidr
            out.append({"name": f"iface {iface_ip}", "ip": iface_ip, "netmask": str(net.netmask), "cidr": cidr, "scan": scan})
        except Exception: pass
    return out

@app.route("/api/adapters", methods=["GET"])
def api_adapters():
    include_all = request.args.get("all") in ("1","true","yes")
    try:
        res = _adapters_windows(active_only=not include_all)
        if not res: res = _adapters_psutil(active_only=not include_all)
        if not res: res = _adapters_route_print()
        return jsonify({"ok": True, "adapters": res})
    except Exception as e:
        return jsonify({"ok": True, "adapters": [], "note": f"error: {e}"}), 200

# ---------------- config & files ----------------
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        include_password = request.args.get("include_password", "0") in ("1", "true")
        return jsonify({
            "ok": True,
            "username": app.config.get('USERNAME', 'admin'),
            "password": app.config.get('PASSWORD', 'password') if include_password else "",
            "fallback_password": app.config.get('FALLBACK_PASSWORD', 'Atlona') if include_password else "",
            "ws_port": app.config.get('WS_PORT', 80),
            "timeout": app.config.get('TIMEOUT', 4.5),
            "concurrency": app.config.get('UPLOAD_CONCURRENCY', 6),
            "firmware_path": app.config.get('FIRMWARE_PATH', '')
        })
    
    data = request.get_json(silent=True) or {}
    app.config['USERNAME'] = data.get("username","admin")
    app.config['PASSWORD'] = data.get("password","password")
    app.config['FALLBACK_PASSWORD'] = data.get("fallback_password","Atlona")
    app.config['WS_PORT'] = int(data.get("ws_port",80))
    app.config['TIMEOUT'] = float(data.get("timeout",4.5))
    app.config['UPLOAD_CONCURRENCY'] = int(data.get("concurrency", 6))
    app.config['FIRMWARE_PATH'] = data.get("firmware_path", "")
    # Save to file
    _save_config({
        "username": app.config['USERNAME'],
        "password": app.config['PASSWORD'],
        "fallback_password": app.config['FALLBACK_PASSWORD'],
        "ws_port": app.config['WS_PORT'],
        "timeout": app.config['TIMEOUT'],
        "concurrency": app.config['UPLOAD_CONCURRENCY'],
        "firmware_path": app.config['FIRMWARE_PATH']
    })
    _configure_matrix_logic_from_app()
    return jsonify({"ok": True})

@app.route("/api/sync_passwords", methods=["POST"])
def api_sync_passwords():
    data = request.get_json(silent=True) or {}
    target = (data.get("target") or "").strip().lower()
    if target not in ("primary", "fallback"):
        return jsonify({"ok": False, "error": "target must be 'primary' or 'fallback'"}), 400

    primary_pwd = app.config.get('PASSWORD', 'password')
    fallback_pwd = app.config.get('FALLBACK_PASSWORD', 'Atlona')
    target_pwd = primary_pwd if target == "primary" else fallback_pwd
    user = app.config.get('USERNAME', 'admin')
    ws_port = app.config.get('WS_PORT', 80)
    ws_path = app.config.get('WS_PATH', '/wsapp/')
    timeout = float(app.config.get('TIMEOUT', 4.5))

    units = _load_cache() or []
    units_by_ip = {u.get("ip"): u for u in units if u.get("ip")}
    ips = list(units_by_ip.keys())

    if not ips:
        return jsonify({"ok": True, "updated": 0, "skipped": 0, "failed": 0, "results": {}})

    def sync_one(ip: str):
        unit = units_by_ip.get(ip, {})
        current_pwd = unit.get("password") or primary_pwd

        # Already at target password
        if current_pwd == target_pwd:
            return {"ip": ip, "ok": True, "changed": False, "stage": "skip", "reason": "already_target"}

        url = _ws_url(ip, ws_port, ws_path)

        # Try current known password first, then configured primary/fallback if needed
        pwd_candidates = []
        for p in [current_pwd, primary_pwd, fallback_pwd]:
            if p and p not in pwd_candidates:
                pwd_candidates.append(p)

        auth_config = None
        used_current_pwd = None
        last_error = "auth-get failed"

        for candidate in pwd_candidates:
            try:
                auth_get_payload = {
                    "id": "auth-get",
                    "username": user,
                    "password": candidate,
                    "config_get": "auth"
                }
                auth_get_resp = _ws_send_recv(url, auth_get_payload, timeout=min(timeout, 4.0))
                if not auth_get_resp or auth_get_resp.get("error"):
                    last_error = (auth_get_resp or {}).get("error") or "auth-get failed"
                    continue

                cfg = auth_get_resp.get("config")
                if isinstance(cfg, dict):
                    cfg = cfg.get("auth") if isinstance(cfg.get("auth"), list) else cfg
                if not isinstance(cfg, list):
                    last_error = "invalid auth-get response"
                    continue

                auth_config = json.loads(json.dumps(cfg))
                used_current_pwd = candidate
                break
            except Exception as e:
                last_error = str(e)

        if not auth_config:
            return {"ip": ip, "ok": False, "changed": False, "stage": "auth_get", "error": last_error}

        # Find administrator auth entry and set target password
        admin_entry = None
        for entry in auth_config:
            if not isinstance(entry, dict):
                continue
            role = (entry.get("role") or "").lower()
            uname = (entry.get("username") or "").lower()
            if role in ("administrator", "admin") or uname == (user or "").lower():
                admin_entry = entry
                break

        if admin_entry is None:
            return {"ip": ip, "ok": False, "changed": False, "stage": "auth_parse", "error": "administrator entry not found"}

        admin_entry["password"] = target_pwd
        if "passwordHash" in admin_entry:
            try:
                del admin_entry["passwordHash"]
            except Exception:
                pass

        # Send auth-set with current password in top-level auth, new password in config payload
        try:
            auth_set_payload = {
                "id": "auth-set",
                "username": user,
                "password": used_current_pwd,
                "config_set": {
                    "name": "auth",
                    "config": auth_config
                }
            }
            auth_set_resp = _ws_send_recv(url, auth_set_payload, timeout=max(timeout, 6.0))
            if auth_set_resp and not auth_set_resp.get("error"):
                return {"ip": ip, "ok": True, "changed": True, "stage": "done"}
            return {
                "ip": ip,
                "ok": False,
                "changed": False,
                "stage": "auth_set",
                "error": (auth_set_resp or {}).get("error") or "auth-set failed"
            }
        except Exception as e:
            return {"ip": ip, "ok": False, "changed": False, "stage": "exception", "error": str(e)}

    results = {}
    max_workers = min(16, max(1, len(ips)))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(sync_one, ip): ip for ip in ips}
        for fut in as_completed(futs):
            ip = futs[fut]
            try:
                results[ip] = fut.result()
            except Exception as e:
                results[ip] = {"ip": ip, "ok": False, "changed": False, "stage": "exception", "error": str(e)}

    changed_ips = {ip for ip, r in results.items() if r.get("ok") and r.get("changed")}
    if changed_ips:
        try:
            for unit in units:
                ip = unit.get("ip")
                if ip in changed_ips:
                    unit["password"] = target_pwd
            _save_cache(units)
        except Exception as e:
            log.warning("[SYNC_PASSWORDS] Failed to persist updated unit passwords: %s", e)

        if HAS_MATRIX:
            try:
                omni_matrix_logic._load_cache()
            except Exception as e:
                log.warning("[SYNC_PASSWORDS] matrix_logic _load_cache failed: %s", e)

        if SCAN_RESULTS.exists():
            try:
                with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                    scan_data = json.load(f)
                for key in ("devices", "encoders", "decoders"):
                    arr = scan_data.get(key)
                    if not isinstance(arr, list):
                        continue
                    for device in arr:
                        if (device or {}).get("ip") in changed_ips:
                            device["password"] = target_pwd
                with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                    json.dump(scan_data, f, indent=2)
            except Exception as e:
                log.warning("[SYNC_PASSWORDS] Failed to update scan_results.json: %s", e)

    updated = sum(1 for r in results.values() if r.get("ok") and r.get("changed"))
    skipped = sum(1 for r in results.values() if r.get("ok") and not r.get("changed"))
    failed = sum(1 for r in results.values() if not r.get("ok"))

    log.info("[SYNC_PASSWORDS] target=%s updated=%d skipped=%d failed=%d", target, updated, skipped, failed)
    return jsonify({
        "ok": True,
        "target": target,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "results": results
    })

@app.route("/api/ntp_profile", methods=["POST"])
def api_ntp_profile():
    """Fetch timezone options/current timezone from a reachable unit."""
    data = request.get_json(silent=True) or {}
    requested_ips = data.get("ips") or []

    cache_devices = _load_cache() or []
    cache_map = {d.get("ip"): d for d in cache_devices if d.get("ip")}
    cache_ips = list(cache_map.keys())

    target_ips = [ip for ip in requested_ips if ip in cache_map] if requested_ips else cache_ips
    if not target_ips:
        return jsonify({
            "ok": True,
            "zones": [{"name": "UTC"}],
            "timezone": "UTC",
            "active_timezone": "UTC",
            "ntp_server": ""
        })

    ws_port = app.config.get('WS_PORT', 80)
    ws_path = app.config.get('WS_PATH', '/wsapp/')
    timeout = float(app.config.get('TIMEOUT', 4.5))

    last_error = "timezone-get failed"
    for ip in target_ips:
        user, preferred_pwd, _ = _device_credentials(ip, cache_map)
        for pwd_try in _password_candidates(preferred_pwd):
            try:
                url = _ws_url(ip, ws_port, ws_path)
                payload = {
                    "id": "timezone-get",
                    "username": user,
                    "password": pwd_try,
                    "config_get": "timezone"
                }
                resp = _ws_send_recv(url, payload, timeout=min(timeout, 4.0))
                if not resp or resp.get("error"):
                    last_error = (resp or {}).get("error") or "timezone-get failed"
                    continue

                cfg = resp.get("config") or {}
                if not isinstance(cfg, dict):
                    cfg = {}

                zones = cfg.get("zones")
                if not isinstance(zones, list):
                    zones = [{"name": "UTC"}]

                timezone = cfg.get("timezone") or cfg.get("active_timezone") or "UTC"
                ntp_server = ""

                # NTP server is commonly exposed under systeminfo.ntpserver.
                try:
                    si_resp = _ws_send_recv(url, {
                        "id": "systeminfo-get",
                        "username": user,
                        "password": pwd_try,
                        "config_get": "systeminfo"
                    }, timeout=min(timeout, 4.0))
                    si_cfg = (si_resp or {}).get("config") or {}
                    if isinstance(si_cfg, dict):
                        ntp_server = (
                            si_cfg.get("ntpserver")
                            or si_cfg.get("ntp_server")
                            or si_cfg.get("server")
                            or si_cfg.get("ntpServer")
                            or ""
                        )
                except Exception:
                    ntp_server = ""

                if not ntp_server:
                    ntp_server = (
                        cfg.get("ntp_server")
                        or cfg.get("server")
                        or cfg.get("ntpServer")
                        or cfg.get("ntp_host")
                        or ""
                    )

                return jsonify({
                    "ok": True,
                    "source_ip": ip,
                    "zones": zones,
                    "timezone": timezone,
                    "active_timezone": cfg.get("active_timezone") or timezone,
                    "ntp_server": ntp_server
                })
            except Exception as e:
                last_error = str(e)

    return jsonify({"ok": False, "error": f"Unable to read timezone config: {last_error}"}), 502

@app.route("/api/set_ntp", methods=["POST"])
def api_set_ntp():
    """Set timezone (and optional NTP server) for selected or all units."""
    data = request.get_json(silent=True) or {}
    scope = (data.get("scope") or "selected").strip().lower()
    requested_ips = data.get("ips") or []
    timezone = (data.get("timezone") or "").strip()
    server = (data.get("server") or "").strip()

    if scope not in ("selected", "all"):
        return jsonify({"ok": False, "error": "scope must be 'selected' or 'all'"}), 400
    if not timezone:
        return jsonify({"ok": False, "error": "timezone is required"}), 400

    cache_devices = _load_cache() or []
    cache_map = {d.get("ip"): d for d in cache_devices if d.get("ip")}
    cache_ips = list(cache_map.keys())

    if scope == "all":
        target_ips = cache_ips
    else:
        target_ips = [ip for ip in requested_ips if ip in cache_map]

    if not target_ips:
        return jsonify({"ok": False, "error": "No target units found for the selected scope"}), 400

    ws_port = app.config.get('WS_PORT', 80)
    ws_path = app.config.get('WS_PATH', '/wsapp/')
    timeout = float(app.config.get('TIMEOUT', 4.5))

    def set_one(ip: str):
        user, preferred_pwd, _ = _device_credentials(ip, cache_map)
        last_error = "timezone-set failed"

        def _norm_text(value):
            return (value or "").strip()

        def _norm_host(value):
            return _norm_text(value).lower()

        for pwd_try in _password_candidates(preferred_pwd):
            try:
                url = _ws_url(ip, ws_port, ws_path)

                # Read timezone profile to ensure auth works and capture old timezone.
                get_payload = {
                    "id": "timezone-get",
                    "username": user,
                    "password": pwd_try,
                    "config_get": "timezone"
                }
                get_resp = _ws_send_recv(url, get_payload, timeout=min(timeout, 4.0))
                if not get_resp or get_resp.get("error"):
                    last_error = (get_resp or {}).get("error") or "timezone-get failed"
                    continue

                cfg = get_resp.get("config") or {}
                if not isinstance(cfg, dict):
                    cfg = {}

                old_timezone = cfg.get("timezone") or cfg.get("active_timezone") or ""

                # Read systeminfo profile for NTP server state.
                si_cfg = {}
                old_server = ""
                try:
                    si_resp = _ws_send_recv(url, {
                        "id": "systeminfo-get",
                        "username": user,
                        "password": pwd_try,
                        "config_get": "systeminfo"
                    }, timeout=min(timeout, 4.0))
                    tmp_cfg = (si_resp or {}).get("config") or {}
                    if isinstance(tmp_cfg, dict):
                        si_cfg = tmp_cfg
                        old_server = (
                            si_cfg.get("ntpserver")
                            or si_cfg.get("ntp_server")
                            or si_cfg.get("server")
                            or si_cfg.get("ntpServer")
                            or ""
                        )
                except Exception:
                    si_cfg = {}

                if not old_server:
                    old_server = (
                        cfg.get("ntp_server")
                        or cfg.get("server")
                        or cfg.get("ntpServer")
                        or cfg.get("ntp_host")
                        or ""
                    )

                requested_timezone = _norm_text(timezone)
                requested_server = _norm_text(server)
                timezone_changed_needed = (_norm_text(old_timezone) != requested_timezone)
                server_changed_needed = bool(requested_server) and (_norm_host(old_server) != _norm_host(requested_server))

                if not timezone_changed_needed and not server_changed_needed:
                    return {
                        "ip": ip,
                        "ok": True,
                        "changed": False,
                        "used_password": pwd_try,
                        "server_applied": True,
                        "skipped_reason": "already_matches"
                    }

                # Step 1: set timezone only when it actually needs to change.
                if timezone_changed_needed:
                    tz_set_payload = {
                        "id": "timezone-set",
                        "username": user,
                        "password": pwd_try,
                        "config_set": {
                            "name": "timezone",
                            "config": {"timezone": requested_timezone}
                        }
                    }
                    tz_set_resp = _ws_send_recv(url, tz_set_payload, timeout=max(timeout, 6.0))
                    if not tz_set_resp or tz_set_resp.get("error"):
                        last_error = (tz_set_resp or {}).get("error") or "timezone-set failed"
                        continue

                # Step 2: if provided, set NTP server via systeminfo ntpserver.
                server_applied = True
                new_server = old_server
                if server_changed_needed:
                    server_applied = False
                    if isinstance(si_cfg, dict) and si_cfg:
                        # Build an editable systeminfo payload shape expected by firmware.
                        si_new_cfg = {
                            "description": si_cfg.get("description", "") or "",
                            "location": si_cfg.get("location", "") or "",
                            "hostname": si_cfg.get("hostname", "") or "",
                            "ntpserver": requested_server,
                            "buttons": {
                                "enabled": bool(((si_cfg.get("buttons") or {}).get("enabled", True))),
                                "menuenabled": bool(((si_cfg.get("buttons") or {}).get("menuenabled", False)))
                            },
                            "leds": {
                                "enabled": bool(((si_cfg.get("leds") or {}).get("enabled", True)))
                            },
                            "system_mode": si_cfg.get("system_mode", "Colibri") or "Colibri",
                            "lcd": {
                                "brightness": int(((si_cfg.get("lcd") or {}).get("brightness", 10)))
                            }
                        }
                        si_set_payload = {
                            "id": "systeminfo-set",
                            "username": user,
                            "password": pwd_try,
                            "config_set": {
                                "name": "systeminfo",
                                "config": si_new_cfg
                            }
                        }
                        si_set_resp = _ws_send_recv(url, si_set_payload, timeout=max(timeout, 6.0))
                        if not si_set_resp or si_set_resp.get("error"):
                            last_error = (si_set_resp or {}).get("error") or "systeminfo-set failed"
                        else:
                            try:
                                verify_si = _ws_send_recv(url, {
                                    "id": "systeminfo-get-verify",
                                    "username": user,
                                    "password": pwd_try,
                                    "config_get": "systeminfo"
                                }, timeout=min(timeout, 4.0))
                                verify_cfg = (verify_si or {}).get("config") or {}
                                if isinstance(verify_cfg, dict):
                                    new_server = (
                                        verify_cfg.get("ntpserver")
                                        or verify_cfg.get("ntp_server")
                                        or verify_cfg.get("server")
                                        or verify_cfg.get("ntpServer")
                                        or ""
                                    )
                                    server_applied = (_norm_host(new_server) == _norm_host(requested_server))
                            except Exception:
                                server_applied = False
                                last_error = "failed to verify ntp server"

                    # Fallback: some firmware may accept server under timezone profile.
                    if not server_applied:
                        tz_server_keys = ["ntpserver", "ntp_server", "server", "ntpServer", "ntp_host"]
                        for tz_key in tz_server_keys:
                            try:
                                tz_server_payload = {
                                    "id": "timezone-set-server",
                                    "username": user,
                                    "password": pwd_try,
                                    "config_set": {
                                        "name": "timezone",
                                        "config": {"timezone": requested_timezone, tz_key: requested_server}
                                    }
                                }
                                tz_server_resp = _ws_send_recv(url, tz_server_payload, timeout=max(timeout, 6.0))
                                if not tz_server_resp or tz_server_resp.get("error"):
                                    last_error = (tz_server_resp or {}).get("error") or "timezone-set server failed"
                                    continue
                                verify_tz = _ws_send_recv(url, {
                                    "id": "timezone-get-verify",
                                    "username": user,
                                    "password": pwd_try,
                                    "config_get": "timezone"
                                }, timeout=min(timeout, 4.0))
                                verify_tz_cfg = (verify_tz or {}).get("config") or {}
                                if isinstance(verify_tz_cfg, dict):
                                    new_server = (
                                        verify_tz_cfg.get("ntpserver")
                                        or verify_tz_cfg.get("ntp_server")
                                        or verify_tz_cfg.get("server")
                                        or verify_tz_cfg.get("ntpServer")
                                        or verify_tz_cfg.get("ntp_host")
                                        or new_server
                                    )
                                server_applied = (_norm_host(new_server) == _norm_host(requested_server))
                                if server_applied:
                                    break
                            except Exception as e:
                                last_error = str(e)
                                continue

                # Verify timezone after set.
                new_timezone = old_timezone
                if timezone_changed_needed:
                    try:
                        verify_tz_resp = _ws_send_recv(url, {
                            "id": "timezone-get-verify2",
                            "username": user,
                            "password": pwd_try,
                            "config_get": "timezone"
                        }, timeout=min(timeout, 4.0))
                        verify_cfg = (verify_tz_resp or {}).get("config") or {}
                        if isinstance(verify_cfg, dict):
                            new_timezone = verify_cfg.get("timezone") or verify_cfg.get("active_timezone") or requested_timezone
                    except Exception:
                        new_timezone = requested_timezone

                if server_changed_needed and not server_applied:
                    return {
                        "ip": ip,
                        "ok": False,
                        "changed": (_norm_text(old_timezone) != _norm_text(new_timezone)),
                        "used_password": pwd_try,
                        "error": last_error or "NTP server was not applied by device"
                    }

                changed = (_norm_text(old_timezone) != _norm_text(new_timezone)) or (server_changed_needed and (_norm_host(old_server) != _norm_host(new_server)))
                return {
                    "ip": ip,
                    "ok": True,
                    "changed": changed,
                    "used_password": pwd_try,
                    "server_applied": (not server_changed_needed) or server_applied
                }
            except Exception as e:
                last_error = str(e)
                continue

        return {"ip": ip, "ok": False, "changed": False, "error": last_error}

    results = {}
    max_workers = min(16, max(1, len(target_ips)))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(set_one, ip): ip for ip in target_ips}
        for fut in as_completed(futs):
            ip = futs[fut]
            try:
                results[ip] = fut.result()
            except Exception as e:
                results[ip] = {"ip": ip, "ok": False, "changed": False, "error": str(e)}

    changed = False
    changed_ips = {ip for ip, result in results.items() if result.get("ok") and result.get("changed")}
    for unit in cache_devices:
        ip = unit.get("ip")
        used = (results.get(ip) or {}).get("used_password")
        if used and unit.get("password") != used:
            unit["password"] = used
            changed = True
        if ip in changed_ips and unit.get("timezone") != timezone:
            unit["timezone"] = timezone
            changed = True
        if server and ip in changed_ips and unit.get("ntp_server") != server:
            unit["ntp_server"] = server
            changed = True
    if changed:
        _save_cache(cache_devices)

    if changed_ips and SCAN_RESULTS.exists():
        try:
            with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                scan_data = json.load(f)
            updated_scan = False
            for key in ("devices", "encoders", "decoders"):
                arr = scan_data.get(key)
                if not isinstance(arr, list):
                    continue
                for device in arr:
                    if (device or {}).get("ip") in changed_ips:
                        if device.get("timezone") != timezone:
                            device["timezone"] = timezone
                            updated_scan = True
                        if server and device.get("ntp_server") != server:
                            device["ntp_server"] = server
                            updated_scan = True
                        details = device.setdefault("details", {})
                        timezone_details = details.setdefault("timezone", {"config": {}})
                        tz_cfg = timezone_details.setdefault("config", {})
                        if tz_cfg.get("timezone") != timezone:
                            tz_cfg["timezone"] = timezone
                            updated_scan = True
                        if server:
                            systeminfo_details = details.setdefault("systeminfo", {"config": {}})
                            si_cfg = systeminfo_details.setdefault("config", {})
                            if si_cfg.get("ntpserver") != server:
                                si_cfg["ntpserver"] = server
                                updated_scan = True
            if updated_scan:
                with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                    json.dump(scan_data, f, indent=2)
        except Exception as e:
            log.warning("[SET_NTP] Failed to update scan_results.json: %s", e)

    updated = sum(1 for r in results.values() if r.get("ok") and r.get("changed"))
    skipped = sum(1 for r in results.values() if r.get("ok") and not r.get("changed"))
    failed = sum(1 for r in results.values() if not r.get("ok"))
    warnings = sum(1 for r in results.values() if r.get("warning"))

    log.info("[SET_NTP] scope=%s timezone=%s updated=%d skipped=%d failed=%d warnings=%d", scope, timezone, updated, skipped, failed, warnings)
    return jsonify({
        "ok": True,
        "scope": scope,
        "timezone": timezone,
        "changed_ips": sorted(changed_ips),
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "warnings": warnings,
        "results": results
    })

@app.route("/api/files", methods=["GET"])
def api_files():
    files = []
    try:
        # Use firmware path from config if set, otherwise use CWD
        fw_path_str = app.config.get('FIRMWARE_PATH', '').strip()
        if fw_path_str:
            fw_dir = Path(fw_path_str)
            if not fw_dir.is_absolute():
                fw_dir = CWD / fw_path_str
        else:
            fw_dir = CWD
        
        if fw_dir.exists() and fw_dir.is_dir():
            for p in sorted(fw_dir.iterdir()):
                if p.is_file() and p.name.lower().endswith(".vpup2"):
                    try:
                        st=p.stat(); files.append({"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime)})
                    except Exception: pass
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "files": []}), 200
    # Prepend "Select Firmware" placeholder
    return jsonify({"ok": True, "files": [{"name":"Select Firmware","size":0,"mtime":0}] + files})

@app.route("/api/open_firmware_folder", methods=["POST"])
def api_open_firmware_folder():
    """Open the firmware folder on the local computer"""
    try:
        # Use firmware path from config if set, otherwise try multiple locations
        fw_path_str = app.config.get('FIRMWARE_PATH', '').strip()
        
        # Try paths in order of preference
        possible_paths = []
        
        if fw_path_str:
            path = Path(fw_path_str)
            if not path.is_absolute():
                path = CWD / fw_path_str
            possible_paths.append(path)
        
        # Also try common default locations
        possible_paths.extend([
            CWD / "firmware",
            CWD / "ui" / "firmware",
            Path("C:/softwareDEV/omniSuite/firmware"),  # Explicit fallback
        ])
        
        # Find the first path that exists
        fw_dir = None
        for path in possible_paths:
            resolved = path.resolve()
            log.info(f"[FIRMWARE_FOLDER] Checking path: {resolved}")
            if resolved.exists() and resolved.is_dir():
                fw_dir = resolved
                log.info(f"[FIRMWARE_FOLDER] Found valid path: {fw_dir}")
                break
        
        if not fw_dir:
            log.error(f"[FIRMWARE_FOLDER] No valid firmware folder found. Tried: {possible_paths}")
            return jsonify({"ok": False, "error": f"Firmware folder not found. Checked paths: {[str(p) for p in possible_paths]}"}), 400
        
        log.info(f"[FIRMWARE_FOLDER] Opening: {fw_dir}")
        
        # Open the folder based on the OS
        system = platform.system()
        log.info(f"[FIRMWARE_FOLDER] System: {system}")
        
        if system == "Windows":
            try:
                os.startfile(str(fw_dir))  # type: ignore[attr-defined]
                log.info(f"[FIRMWARE_FOLDER] Successfully opened with os.startfile: {fw_dir}")
            except Exception as e:
                log.warning(f"[FIRMWARE_FOLDER] os.startfile failed: {e}, trying explorer...")
                # Fallback: try using explorer directly
                subprocess.Popen(f'explorer /select, "{fw_dir}"', shell=True, **_windows_hidden_subprocess_kwargs())
                log.info(f"[FIRMWARE_FOLDER] Opened with explorer fallback")
        elif system == "Darwin":  # macOS
            subprocess.run(["open", str(fw_dir)], check=False)
            log.info(f"[FIRMWARE_FOLDER] Opened with 'open' command")
        else:  # Linux and others
            subprocess.run(["xdg-open", str(fw_dir)], check=False)
            log.info(f"[FIRMWARE_FOLDER] Opened with xdg-open")
        
        return jsonify({"ok": True, "path": str(fw_dir)})
    except Exception as e:
        log.error(f"[FIRMWARE_FOLDER] Exception: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/list_dir", methods=["GET"])
def api_list_dir():
    """List directories in a given path for folder browser modal"""
    try:
        path_arg = request.args.get("path", "").strip()
        if not path_arg:
            # Root of firmware path
            base = Path(CWD)
        else:
            base = Path(path_arg).resolve()
        
        # Safety: Ensure path is within reasonable bounds
        if not base.exists() or not base.is_dir():
            return jsonify({"ok": False, "error": "Path not found or not a directory"}), 400
        
        entries = []
        try:
            for item in sorted(base.iterdir()):
                if item.is_dir() and not item.name.startswith('.'):
                    entries.append({
                        "name": item.name,
                        "is_dir": True,
                        "path": str(item)
                    })
        except PermissionError:
            return jsonify({"ok": False, "error": "Permission denied"}), 403
        
        return jsonify({
            "ok": True,
            "path": str(base),
            "entries": entries
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ---------------- tcp probe / ping ----------------
def _tcp_probe(ip: str, ports, timeout: float=0.4) -> bool:
    for p in ports:
        try:
            with socket.create_connection((ip, p), timeout=timeout):
                return True
        except Exception:
            continue
    return False

@app.route("/api/tcp_probe", methods=["POST"])
def api_tcp_probe():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    timeout_ms = int(data.get("timeout_ms", 400))
    if not ip: return jsonify({"ok": False, "error": "ip required"}), 400
    ports = [app.config['WS_PORT'], 80, 443]
    ports = [p for i,p in enumerate(ports) if p not in ports[:i]]
    ok = _tcp_probe(ip, ports, timeout=timeout_ms/1000.0)
    return jsonify({"ok": True, "reachable": ok})

@app.route("/api/ping", methods=["POST"])
def api_ping():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    timeout_ms = int(data.get("timeout_ms", 400))
    if not ip: return jsonify({"ok": False, "error": "ip required"}), 400
    ports = [app.config['WS_PORT'], 80, 443]
    ports = [p for i,p in enumerate(ports) if p not in ports[:i]]
    ok = _tcp_probe(ip, ports, timeout=timeout_ms/1000.0)
    return jsonify({"ok": True, "reachable": ok})

# ---------------- websocket helpers ----------------
def _ws_url(ip: str, ws_port: int, ws_path: str) -> str:
    scheme = 'wss' if ws_port in (443, 8443) else 'ws'
    return f"{scheme}://{ip}:{ws_port}{ws_path}"

def _ws_send_recv(url: str, payload: dict, timeout: float):
    ws = None
    try:
        sslopt = None
        if url.startswith("wss://") and not app.config['WS_STRICT']:
            sslopt = {"cert_reqs": ssl.CERT_NONE}
        ws = websocket.create_connection(url, timeout=timeout, sslopt=sslopt)
        ws.send(json.dumps(payload))
        raw = ws.recv()
        try:
            obj = json.loads(raw)
        except Exception:
            obj = {"raw": raw}
        return obj
    finally:
        try:
            if ws is not None:
                ws.close()
        except Exception:
            pass

def _ws_send_recv_with_fallback(ip: str, payload: dict, timeout: float, ws_port: int, ws_path: str, primary_pwd: str = None):
    """Try sending WebSocket payload with primary password, fall back to fallback password if auth fails"""
    primary_pwd = primary_pwd or app.config['PASSWORD']
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    
    # Try with primary password first
    url = _ws_url(ip, ws_port, ws_path)
    payload_with_pwd = payload.copy()
    payload_with_pwd['password'] = primary_pwd
    
    try:
        resp = _ws_send_recv(url, payload_with_pwd, timeout)
        # Check if response indicates auth failure
        if resp and not resp.get("error"):
            return resp
        # If there's an error that looks like auth failure, try fallback
        error_msg = (resp.get("error") or "").lower()
        if "auth" not in error_msg and "password" not in error_msg and "unauthorized" not in error_msg and "forbidden" not in error_msg:
            # Not an auth error, return the error response
            return resp
    except Exception as e:
        # Connection/timeout errors, try fallback
        log.debug(f"[FALLBACK] Primary password failed on {ip}: {e}, trying fallback...")
    
    # If primary password failed or errored, try fallback password
    if fallback_pwd != primary_pwd:
        log.info(f"[FALLBACK] Trying fallback password on {ip}...")
        payload_with_pwd['password'] = fallback_pwd
        try:
            resp = _ws_send_recv(url, payload_with_pwd, timeout)
            if resp and not resp.get("error"):
                log.info(f"[FALLBACK] Fallback password succeeded on {ip}")
                return resp
            # Document that fallback also failed
            log.warning(f"[FALLBACK] Both primary and fallback passwords failed on {ip}: {resp}")
            return resp
        except Exception as e:
            log.error(f"[FALLBACK] Fallback password also failed on {ip}: {e}")
            return {"error": f"Authentication failed with both primary and fallback passwords: {e}"}
    
    # Primary password worked, return original response
    return resp

# ---------------- login (WS) ----------------
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip: return jsonify({"ok": False, "error": "ip required"}), 400
    user = data.get("username") or app.config['USERNAME']
    pwd = data.get("password") or app.config['PASSWORD']
    ws_port = app.config['WS_PORT']; ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    url = _ws_url(ip, ws_port, ws_path)
    payload = {"id":"systeminfo-login","username":user,"password":pwd,"config_get":"systeminfo"}
    try:
        resp = _ws_send_recv(url, payload, timeout)
        return jsonify({"ok": True, "resp": resp})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------- scan ----------------
def _expand_targets(spec: str):
    spec = (spec or "").strip()
    if not spec: return []
    parts = re.split(r"[\s,]+", spec); out=[]
    for p in parts:
        if not p: continue
        if "/" in p:
            try:
                ip, slash = p.split("/",1)
                net = ipaddress.ip_network(f"{ip}/{int(slash)}", strict=False)
                out.extend(str(h) for h in net.hosts()); continue
            except Exception: pass
        m = re.match(r"^(\d+\.\d+\.\d+)\.(\d+)-(\d+)$", p)
        if m:
            base,a,b = m.group(1), int(m.group(2)), int(m.group(3))
            lo,hi = (a,b) if a<=b else (b,a)
            out += [f"{base}.{i}" for i in range(lo,hi+1)]; continue
        try:
            ipaddress.ip_address(p); out.append(p)
        except Exception: pass
    seen=set(); ret=[]
    for ip in out:
        if ip not in seen: seen.add(ip); ret.append(ip)
    return ret

def _probe_one(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float):
    url = _ws_url(ip, ws_port, ws_path)
    sysinfo_req = {"id":"systeminfo-get","username":user,"password":pwd,"config_get":"systeminfo"}
    lic_req     = {"id":"license-get","username":user,"password":pwd,"config_get":"license"}
    timezone_req = {"id":"timezone-get","username":user,"password":pwd,"config_get":"timezone"}
    
    # Try with primary password first
    tried_passwords = [pwd]
    try: 
        sysinfo = _ws_send_recv(url, sysinfo_req, timeout)
    except Exception as e:
        log.debug("[SCAN] %s - systeminfo failed with primary password: %s", ip, type(e).__name__)
        sysinfo = None
    
    # If primary password failed with auth error, try fallback
    if not sysinfo or sysinfo.get("error"):
        fallback_pwd = app.config['FALLBACK_PASSWORD']
        if fallback_pwd != pwd and fallback_pwd not in tried_passwords:
            log.info("[SCAN] %s - primary password failed, trying fallback password", ip)
            tried_passwords.append(fallback_pwd)
            pwd = fallback_pwd  # Update pwd for subsequent requests
            sysinfo_req = {"id":"systeminfo-get","username":user,"password":pwd,"config_get":"systeminfo"}
            lic_req     = {"id":"license-get","username":user,"password":pwd,"config_get":"license"}
            timezone_req = {"id":"timezone-get","username":user,"password":pwd,"config_get":"timezone"}
            try:
                sysinfo = _ws_send_recv(url, sysinfo_req, timeout)
            except Exception as e:
                log.debug("[SCAN] %s - systeminfo failed with fallback password: %s", ip, type(e).__name__)
                sysinfo = None
    
    # Check for None response or authentication error
    if not sysinfo:
        log.debug("[SCAN] %s - no systeminfo response", ip)
        return None
    
    if sysinfo.get("error"):
        log.debug("[SCAN] %s - auth failed with all passwords", ip)
        return None
    
    # Try license in parallel/optional - non-blocking timeout
    license = None
    try: 
        license = _ws_send_recv(url, lic_req, timeout=min(timeout, 1.5))
    except Exception:
        pass

    timezone_resp = None
    try:
        timezone_resp = _ws_send_recv(url, timezone_req, timeout=min(timeout, 1.5))
    except Exception:
        pass
    
    cfg = (sysinfo or {}).get("config") or {}
    board = cfg.get("board") or {}
    mac_hex = ""
    if license:
        try:
            lic_cfg = (license or {}).get("config") or {}
            mac_hex = (lic_cfg.get("device_id") or "").lower()
            mac_hex = re.sub(r"[^0-9a-f]","", mac_hex)
        except Exception:
            pass
    mac = ":".join(mac_hex[i:i+2] for i in range(0,12,2)) if len(mac_hex)==12 else ""
    
    # Extract data
    device_type = cfg.get("type") or ""
    role = ("encoder" if "encoder" in device_type.lower() else ("decoder" if "decoder" in device_type.lower() else "unknown"))
    model = cfg.get("model") or ""
    version = cfg.get("firmwareversion") or ""
    hostname = cfg.get("hostname") or ""
    serialnumber = board.get("serialnumber") or ""
    ntp_server = (cfg.get("ntpserver") or cfg.get("ntp_server") or cfg.get("ntpServer") or "").strip()
    timezone_cfg = (timezone_resp or {}).get("config") or {}
    timezone = ""
    active_timezone = ""
    if isinstance(timezone_cfg, dict):
        timezone = (timezone_cfg.get("timezone") or "").strip()
        active_timezone = (timezone_cfg.get("active_timezone") or timezone).strip()
    
    # Skip devices with unknown role - only Encoder and Decoder are valid
    if role == "unknown":
        log.warning("[SCAN] %s: skipping device with unrecognized role (type=%s)", ip, device_type)
        return None
    
    # Fetch encoder/decoder specific data (multicast addresses)
    # Fetch encoder/decoder specific data (multicast addresses) - using same WS requests as omni_matrix.py
    matrix_data = {}
    if role == "encoder":
        # Get encoder sessions configuration
        try:
            sessions_req = {"id":"sessions-get","username":user,"password":pwd,"config_get":"sessions"}
            sessions_resp = _ws_send_recv(url, sessions_req, timeout=min(timeout, 1.5))
            if sessions_resp and not sessions_resp.get("error"):
                sessions_cfg = (sessions_resp or {}).get("config") or []
                # Handle both list and dict responses
                sessions = sessions_cfg if isinstance(sessions_cfg, list) else sessions_cfg.get("sessions", [])
                # Get session1 or first session
                session1 = None
                if sessions:
                    session1 = next((s for s in sessions if (s.get("name") or "").lower() == "session1"), sessions[0])
                if session1:
                    video_stream = ((session1.get("video") or {}).get("stream") or {})
                    audio_stream = ((session1.get("audio") or {}).get("stream") or {})
                    matrix_data = {
                        "v_mcast": video_stream.get("destination_address"),
                        "v_port": video_stream.get("destination_port"),
                        "a_mcast": audio_stream.get("destination_address"),
                        "a_port": audio_stream.get("destination_port")
                    }
        except Exception as e:
            log.debug("[SCAN] %s - sessions fetch failed: %s", ip, e)
    elif role == "decoder":
        # Get decoder ip_input configuration
        try:
            ip_input_req = {"id":"ip_input-get","username":user,"password":pwd,"config_get":"ip_input"}
            ip_input_resp = _ws_send_recv(url, ip_input_req, timeout=min(timeout, 1.5))
            if ip_input_resp and not ip_input_resp.get("error"):
                ip_cfg = (ip_input_resp or {}).get("config") or []
                # Handle both list and dict responses
                ip_list = ip_cfg if isinstance(ip_cfg, list) else ip_cfg.get("ip_input", [])
                # Get ip_input1 and ip_input3
                ip1 = next((e for e in ip_list if e.get("name") == "ip_input1"), {})
                ip3 = next((e for e in ip_list if e.get("name") == "ip_input3"), {})
                matrix_data = {
                    "ip1_addr": (ip1.get("multicast") or {}).get("address"),
                    "ip1_port": ip1.get("port"),
                    "ip3_addr": (ip3.get("multicast") or {}).get("address"),
                    "ip3_port": ip3.get("port")
                }
        except Exception as e:
            log.debug("[SCAN] %s - ip_input fetch failed: %s", ip, e)
    
    # Fetch link speed via net-get
    linkspeed = None
    try:
        net_resp = _ws_send_recv(url,
            {"id": "net-get", "username": user, "password": pwd, "config_get": "net"},
            timeout=min(timeout, 1.5))
        if net_resp and not net_resp.get("error"):
            net_cfg = net_resp.get("config") or []
            for iface in net_cfg:
                if iface.get("name") == "eth1" and "linkspeed" in iface:
                    linkspeed = iface["linkspeed"]
                    break
            if linkspeed is None:
                for iface in net_cfg:
                    if "linkspeed" in iface:
                        linkspeed = iface["linkspeed"]
                        break
    except Exception:
        pass

    result = {
        "ip": ip,
        "type": device_type,
        "role": role,
        "model": model,
        "version": version,
        "hostname": hostname,
        "timezone": timezone,
        "active_timezone": active_timezone,
        "ntp_server": ntp_server,
        "serialnumber": serialnumber,
        "mac": mac,
        "password": pwd,  # Store the password that successfully authenticated for this device
        "details": {"systeminfo": sysinfo, "license": license, "timezone": timezone_resp},
    }
    if linkspeed is not None:
        result["linkspeed"] = linkspeed

    # Add matrix data if available
    if matrix_data:
        result.update(matrix_data)

    return result

@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json(silent=True) or {}
    targets = (data.get("targets") or "").strip()
    if not targets: return jsonify({"ok": False, "error": "No targets provided"}), 400
    ips = _expand_targets(targets)
    user = app.config['USERNAME']; pwd = app.config['PASSWORD']
    ws_port = app.config['WS_PORT']; ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    
    log.info("[SCAN] Starting scan of %d IPs (timeout=%.1fs)", len(ips), timeout)
    start_time = time.time()

    # Single-phase probe: combine TCP + WS in one threaded pass
    raw_units = []
    def probe_ip(ip):
        try:
            # Quick TCP check first
            if not _tcp_probe(ip, [ws_port, 80, 443], timeout=0.4):
                return None
            # If reachable, do WS probe
            return _probe_one(ip, user, pwd, ws_port, ws_path, timeout)
        except Exception:
            return None
    
    with ThreadPoolExecutor(max_workers=min(96, max(8, len(ips)))) as ex:
        futs = {ex.submit(probe_ip, ip): ip for ip in ips}
        for fut in as_completed(futs):
            res = fut.result()
            if res: raw_units.append(res)
    
    # Deduplicate by MAC
    seen = set(); units = []; omitted = 0
    for u in raw_units:
        mac = (u.get("mac") or "").lower()
        if mac and mac in seen:
            omitted += 1
            continue
        if mac: seen.add(mac)
        units.append(u)

    # Merge with cache
    existing = _load_cache()
    by_mac = {}; by_ip = set(); merged = []
    for u in existing + units:
        mac = (u.get("mac") or "").lower()
        if mac:
            if mac in by_mac: continue
            by_mac[mac] = True
        if u.get("ip") in by_ip: continue
        by_ip.add(u.get("ip"))
        if not u.get("timezone"):
            matched = next((old for old in existing if old.get("ip") == u.get("ip") or ((old.get("mac") or "").lower() and (old.get("mac") or "").lower() == mac)), None)
            if matched:
                if matched.get("timezone") and not u.get("timezone"):
                    u["timezone"] = matched.get("timezone")
                if matched.get("active_timezone") and not u.get("active_timezone"):
                    u["active_timezone"] = matched.get("active_timezone")
                if matched.get("ntp_server") and not u.get("ntp_server"):
                    u["ntp_server"] = matched.get("ntp_server")
                if matched.get("details", {}).get("timezone") and not u.get("details", {}).get("timezone"):
                    u.setdefault("details", {})["timezone"] = matched.get("details", {}).get("timezone")
                if matched.get("details", {}).get("systeminfo") and not u.get("details", {}).get("systeminfo"):
                    u.setdefault("details", {})["systeminfo"] = matched.get("details", {}).get("systeminfo")
                if matched.get("linkspeed") is not None and u.get("linkspeed") is None:
                    u["linkspeed"] = matched.get("linkspeed")
        merged.append(u)
    
    _save_cache(merged); _write_csv(merged)
    if HAS_MATRIX:
        try:
            omni_matrix_logic._load_cache()
        except Exception as e:
            log.info("matrix_logic _load_cache failed: %s", e)
    
    # Separate encoders and decoders from merged devices
    encoders = [u for u in merged if u.get("role") == "encoder"]
    decoders = [u for u in merged if u.get("role") == "decoder"]
    
    # No need to call omni_matrix_logic if we already have the data from our scan
    # (omni_matrix_logic would just duplicate the same scan we already did)
    
    # Save unified results
    scan_results = {
        "timestamp": time.time(),
        "targets": targets,
        "devices": merged,
        "encoders": encoders,
        "decoders": decoders,
        "stats": {
            "ips_scanned": len(ips),
            "devices_found": len(raw_units),
            "devices_merged": len(merged),
            "devices_omitted": omitted,
            "encoders_found": len(encoders),
            "decoders_found": len(decoders)
        }
    }
    
    try:
        with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
            json.dump(scan_results, f, indent=2)
        log.info("[SCAN] Saved unified scan results to %s", SCAN_RESULTS)
    except Exception as e:
        log.warning("[SCAN] Failed to save scan results: %s", e)
    
    # Summary
    elapsed = time.time() - start_time
    mac_pop = sum(1 for u in merged if u.get("mac"))
    hostname_pop = sum(1 for u in merged if u.get("hostname"))
    model_pop = sum(1 for u in merged if u.get("model"))
    version_pop = sum(1 for u in merged if u.get("version"))
    type_pop = sum(1 for u in merged if u.get("role") and u.get("role") != "unknown")
    
    log.info("[SCAN] Complete in %.2fs: %d scanned, %d found, %d merged, %d omitted", 
             elapsed, len(ips), len(raw_units), len(merged), omitted)
    log.info("[SCAN] Fields: MAC %d/%d (%.0f%%), Hostname %d/%d (%.0f%%), Type %d/%d (%.0f%%)",
             mac_pop, len(merged), 100*mac_pop/len(merged) if merged else 0,
             hostname_pop, len(merged), 100*hostname_pop/len(merged) if merged else 0,
             type_pop, len(merged), 100*type_pop/len(merged) if merged else 0)

    return jsonify({"ok": True, "units": merged, "encoders": encoders, "decoders": decoders, "tested": len(ips), "reachable": len(raw_units), "omitted": omitted, "added": max(0,len(merged)-len(existing))})

# --- unified scan results retrieval
@app.route("/api/scan_results", methods=["GET"])
def api_scan_results():
    """Retrieve cached scan results (devices, encoders, decoders)"""
    if not SCAN_RESULTS.exists():
        return jsonify({"ok": True, "devices": [], "encoders": [], "decoders": [], "stats": {}})
    try:
        with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"ok": True, **data})
    except Exception as e:
        log.warning("Failed to load scan results: %s", e)
        return jsonify({"ok": True, "devices": [], "encoders": [], "decoders": [], "stats": {}}), 200

# ---------------- matrix api ----------------
@app.route("/api/state", methods=["GET"])
def api_state_matrix():
    # Try to load from cache first (for testing with 100 units)
    units = _load_cache()
    if units:
        log.info(f"[API/STATE] Loaded {len(units)} units from cache for matrix")
        enc = [u for u in units if (u.get("type") or u.get("role") or "").lower().startswith("enc")]
        dec = [u for u in units if (u.get("type") or u.get("role") or "").lower().startswith("dec")]
    else:
        # Fall back to scan_results format
        data = _load_scan_results_file() or {}
        enc = data.get("encoders") or []
        dec = data.get("decoders") or []
        if not enc and not dec:
            devs = data.get("devices") or []
            enc = [u for u in devs if u.get("role") == "encoder"]
            dec = [u for u in devs if u.get("role") == "decoder"]
    # Enrich cache-loaded units with scan_results multicast info if missing
    scan_data = _load_scan_results_file() or {}
    sr_enc_map = {e.get("ip"): e for e in (scan_data.get("encoders") or [])}
    if not sr_enc_map:
        # derive from devices if encoders list absent
        sr_enc_map = {u.get("ip"): u for u in (scan_data.get("devices") or []) if (u or {}).get("role") == "encoder"}
    sr_dec_map = {d.get("ip"): d for d in (scan_data.get("decoders") or [])}
    if not sr_dec_map:
        sr_dec_map = {u.get("ip"): u for u in (scan_data.get("devices") or []) if (u or {}).get("role") == "decoder"}

    def _merge_enc(e):
        src = sr_enc_map.get(e.get("ip")) or {}
        return {
            "ip": e.get("ip"),
            "host": e.get("hostname") or e.get("host"),
            "model": e.get("model"),
            "fw": e.get("version") or e.get("fw"),
            "serial": e.get("serialnumber") or e.get("serial"),
            "v_mcast": e.get("v_mcast") or src.get("v_mcast"),
            "v_port": e.get("v_port") or src.get("v_port"),
            "a_mcast": e.get("a_mcast") or src.get("a_mcast"),
            "a_port": e.get("a_port") or src.get("a_port"),
        }

    def _merge_dec(d):
        src = sr_dec_map.get(d.get("ip")) or {}
        return {
            "ip": d.get("ip"),
            "host": d.get("hostname") or d.get("host"),
            "model": d.get("model"),
            "fw": d.get("version") or d.get("fw"),
            "serial": d.get("serialnumber") or d.get("serial"),
            "ip1_addr": d.get("ip1_addr") or src.get("ip1_addr"),
            "ip1_port": d.get("ip1_port") or src.get("ip1_port"),
            "ip3_addr": d.get("ip3_addr") or src.get("ip3_addr"),
            "ip3_port": d.get("ip3_port") or src.get("ip3_port"),
        }

    enc_mapped = [_merge_enc(e) for e in enc]
    dec_mapped = [_merge_dec(d) for d in dec]
    
    log.info(f"[API/STATE] After merge: {len(enc_mapped)} encoders, {len(dec_mapped)} decoders")
    if enc_mapped:
        log.info(f"[API/STATE] Sample encoder: {enc_mapped[0]}")
    if dec_mapped:
        log.info(f"[API/STATE] Sample decoder before overlay: {dec_mapped[0]}")

    # Overlay live matrix state (if available) to keep UI in sync after routing
    routes = {}
    if HAS_MATRIX:
        try:
            mstate = omni_matrix_logic.list_state()
            log.info(f"[API/STATE] Matrix logic list_state returned: encoders={len(mstate.get('encoders') or [])}, decoders={len(mstate.get('decoders') or [])}, routes={len(mstate.get('routes') or {})}")
        except Exception as e:
            log.warning(f"[API/STATE] list_state failed: {e}")
            mstate = None
        if mstate:
            routes = mstate.get("routes") or routes
            log.info(f"[API/STATE] Routes from list_state: {routes}")
            live_decs = {d.get("ip"): d for d in (mstate.get("decoders") or [])}
            for i,d in enumerate(dec_mapped):
                live = live_decs.get(d.get("ip"))
                if not live:
                    continue
                # prefer live decoder input fields so refresh doesn't revert UI
                log.info(f"[API/STATE] Overlaying live decoder {d.get('ip')}: {live}")
                for k in ("ip1_addr","ip1_port","ip3_addr","ip3_port"):
                    if live.get(k) is not None:
                        dec_mapped[i][k] = live.get(k)
    
    if dec_mapped:
        log.info(f"[API/STATE] Sample decoder after overlay: {dec_mapped[0]}")

    # Filter out offline units - check reachability in parallel with short timeout
    if enc_mapped or dec_mapped:
        try:
            online_ips = set()
            units_to_check = [(u.get("ip"), u) for u in enc_mapped + dec_mapped if u.get("ip")]
            
            if units_to_check:
                # Get credentials from cache
                cache_devices = _load_cache() or []
                cache_map = {d.get("ip"): d for d in cache_devices if d.get("ip")}
                default_user = app.config['USERNAME']
                default_pwd = app.config['PASSWORD']
                ws_port = app.config['WS_PORT']
                ws_path = app.config['WS_PATH']
                
                # Check reachability in parallel with very short timeout
                def check_online(ip):
                    try:
                        # Get device-specific password from cache or use default
                        device = cache_map.get(ip, {})
                        user = device.get("username") or default_user
                        pwd = device.get("password") or default_pwd
                        
                        url = _ws_url(ip, ws_port, ws_path)
                        # Very short timeout (0.5s) - just need to know if device is reachable
                        response = _ws_send_recv(url, 
                            {"id": "ping", "username": user, "password": pwd, "config_get": "dummy"},
                            timeout=0.5)
                        
                        # If we got any response back, unit is online
                        if response:
                            return True
                    except Exception as e:
                        log.debug(f"[API/STATE] {ip} reachability check failed: {e}")
                    return False
                
                # Check all units in parallel
                with ThreadPoolExecutor(max_workers=4) as executor:
                    results = {ip: future for ip, future in 
                              zip([ip for ip, _ in units_to_check],
                                  executor.map(check_online, [ip for ip, _ in units_to_check]))}
                
                # Build set of online IPs
                for ip, is_online in results.items():
                    if is_online:
                        online_ips.add(ip)
                        log.debug(f"[API/STATE] {ip} is ONLINE")
                    else:
                        log.debug(f"[API/STATE] {ip} is OFFLINE - filtering out")
                
                # Filter to only online units
                enc_mapped = [e for e in enc_mapped if e.get("ip") in online_ips]
                dec_mapped = [d for d in dec_mapped if d.get("ip") in online_ips]
                log.info(f"[API/STATE] Filtered to {len(enc_mapped)} encoders, {len(dec_mapped)} decoders (online only)")
        except Exception as e:
            log.warning(f"[API/STATE] Offline filtering failed: {e} - returning all units")

    return jsonify({"ok": True, "encoders": enc_mapped, "decoders": dec_mapped, "routes": routes, "poll": {}})

@app.route("/api/route", methods=["POST"])
def api_route_matrix():
    if not HAS_MATRIX:
        return jsonify({"ok": False, "error": "matrix logic unavailable"}), 500
    import time
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    log.info(f"[ROUTE] Received request: {data}")
    decoder = data.get("decoder")
    encoder = data.get("encoder")
    mode = (data.get("mode") or "av").lower()
    if not decoder or not encoder:
        log.warning(f"[ROUTE] Missing fields - decoder={decoder}, encoder={encoder}")
        return jsonify({"ok": False, "error": f"decoder and encoder required (got decoder={decoder}, encoder={encoder})"}), 400
    
    cache_devices = _load_cache() or []
    cache_map = {d.get("ip"): d for d in cache_devices if d.get("ip")}
    decoder_user, decoder_pref_pwd, _ = _device_credentials(decoder, cache_map)
    encoder_user, encoder_pref_pwd, _ = _device_credentials(encoder, cache_map)

    decoder_candidates = _password_candidates(decoder_pref_pwd)
    encoder_candidates = _password_candidates(encoder_pref_pwd)

    ok = False
    used_decoder_pwd = None
    used_encoder_pwd = None
    route_errors = []

    for d_pwd in decoder_candidates:
        for e_pwd in encoder_candidates:
            try:
                pwd_try_start = time.time()
                ok = omni_matrix_logic.set_route(decoder, encoder, mode, decoder_user, d_pwd, encoder_user, e_pwd)
                pwd_try_time = time.time() - pwd_try_start
                log.info(f"[ROUTE] set_route attempt took {pwd_try_time:.2f}s: ok={ok}")
                if ok:
                    used_decoder_pwd = d_pwd
                    used_encoder_pwd = e_pwd
                    break
            except Exception as e:
                route_errors.append(str(e))
                log.error(f"[ROUTE] set_route exception: {e}")
                continue
        if ok:
            break

    elapsed = time.time() - start_time
    log.info(f"[ROUTE] Total time: {elapsed:.2f}s, ok={ok}")
    
    if ok:
        try:
            # Save matrix logic cache after successful routing
            omni_matrix_logic._save_cache()
        except Exception:
            pass

        # Persist discovered working passwords for future matrix/firmware operations
        changed = False
        for unit in cache_devices:
            ip = unit.get("ip")
            if ip == decoder and used_decoder_pwd and unit.get("password") != used_decoder_pwd:
                unit["password"] = used_decoder_pwd
                changed = True
            elif ip == encoder and used_encoder_pwd and unit.get("password") != used_encoder_pwd:
                unit["password"] = used_encoder_pwd
                changed = True
        if changed:
            _save_cache(cache_devices)
    else:
        if route_errors:
            log.warning("[ROUTE] set_route attempts failed: %s", route_errors[-1])
    if not ok:
        # Try to provide a more specific error message
        enc = omni_matrix_logic._encoders.get(encoder)
        dec = omni_matrix_logic._decoders.get(decoder)
        if not enc:
            return jsonify({"ok": False, "error": f"Encoder {encoder} not found or offline"}), 400
        if not dec:
            return jsonify({"ok": False, "error": f"Decoder {decoder} not found or offline"}), 400
        return jsonify({"ok": False, "error": "Route command failed (device may be offline, unreachable, or password mismatch)"}), 500

    # Note: Decoder inputs will be fetched by the polling system (every 5 seconds)
    # No need to fetch them here - route response returns immediately
    
    # Return immediately with basic decoder info
    dec_payload = {"ip": decoder}
    return jsonify({"ok": bool(ok), "decoder": dec_payload})

@app.route("/api/clear", methods=["POST"])
def api_clear_matrix():
    # Clear matrix cached results and in-memory state
    try:
        if SCAN_RESULTS.exists():
            SCAN_RESULTS.unlink()
    except Exception:
        pass
    if HAS_MATRIX:
        try: omni_matrix_logic.clear_state()
        except Exception: pass
    return jsonify({"ok": True})

# ---------------- clear/export/cache/poll/upload/reset retained from v7.6x ----------------
OMNI2_MODELS = set(["at-omni-d4111","at-omni-d4511","at-omni-e4521","at-omni-e4111-wp","at-omni-e4111","hw-omni-d4111","hw-omni-d4511","hw-omni-e4521","hw-omni-e4111-wp","hw-omni-e4111"])
SINGLE_MODELS = set(["at-omni-111","at-omni-121","at-omni-111-wp"])
DUAL_MODELS = set(["at-omni-112","at-omni-122"])
RESI_MODELS = set(["at-omni-512","at-omni-521"])

# --- clear/export/cache
@app.route("/api/cache", methods=["GET"])
def api_cache_dup():
    global _cache_startup_verified
    
    units = _load_cache()
    primary_pwd = app.config.get('PASSWORD', '')
    
    # On first cache request (startup), synchronously verify ALL units quickly
    # to catch hostname and version changes before rendering
    if not _cache_startup_verified:
        _cache_startup_verified = True
        
        default_user = app.config['USERNAME']
        default_pwd = app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = 1.5  # Short timeout to fail fast on unreachable devices
        
        log.info("[STARTUP] Starting synchronous verification of all units for hostname/version changes")
        updated = False
        for unit in units:
            ip = unit.get("ip")
            if not ip:
                continue
            try:
                # Get device-specific password from cache, fall back to default
                user = unit.get("username") or default_user
                pwd = unit.get("password") or default_pwd
                
                url = _ws_url(ip, ws_port, ws_path)
                payload = {"id":"systeminfo-get","username":user,"password":pwd,"config_get":"systeminfo"}
                resp = _ws_send_recv(url, payload, timeout=timeout)
                
                if resp and not resp.get("error"):
                    cfg = (resp or {}).get("config") or {}
                    # config_get returns data directly in config section
                    hostname = cfg.get("hostname")
                    if hostname and hostname != unit.get("hostname"):
                        log.info(f"[STARTUP] {ip}: hostname updated to '{hostname}'")
                        unit["hostname"] = hostname
                        if unit.get("details", {}).get("systeminfo", {}).get("config"):
                            unit["details"]["systeminfo"]["config"]["hostname"] = hostname
                        updated = True
                    
                    fw = cfg.get("firmwareversion") or cfg.get("version")
                    if fw and fw != unit.get("version"):
                        log.info(f"[STARTUP] {ip}: version updated to '{fw}'")
                        unit["version"] = fw
                        unit["firmwareversion"] = fw
                        if unit.get("details", {}).get("systeminfo", {}).get("config"):
                            unit["details"]["systeminfo"]["config"]["firmwareversion"] = fw
                        updated = True
            except Exception as e:
                log.info(f"[STARTUP] {ip}: verification failed: {e}")
        
        if updated:
            log.info("[API/CACHE] Saving updated cache after quick verification")
            _save_cache(units)
        
        # Also trigger full background verification for any remaining details
        if not _cache_verification_in_progress:
            _verify_cache_in_background()
    # Periodic verification if cache is old
    elif not _cache_verification_in_progress and (time.time() - _cache_last_verified) > 300:
        log.info("[API/CACHE] Cache is stale, triggering background verification")
        _verify_cache_in_background()
    
    units_with_password_source = []
    for unit in units:
        unit_copy = dict(unit or {})
        used_pwd = unit_copy.get("password") or primary_pwd
        unit_copy["password_source"] = "Primary" if used_pwd == primary_pwd else "Fallback"
        units_with_password_source.append(unit_copy)

    log.info(f"[API/CACHE] Returning {len(units_with_password_source)} units from cache")
    return jsonify({"ok": True, "units": units_with_password_source, "count": len(units_with_password_source), "source": "cache"})

@app.route("/api/clear_units", methods=["POST"])
def api_clear_units_dup():
    try:
        if CACHE.exists(): CACHE.unlink()
        # Also clear unified scan results so UI cache empties
        if SCAN_RESULTS.exists(): SCAN_RESULTS.unlink()
        if CSV_VIEW.exists(): CSV_VIEW.unlink()
    except Exception: pass
    return jsonify({"ok": True})

@app.route("/api/remove_units", methods=["POST"])
def api_remove_units():
    """Remove selected units from cache by IP address"""
    data = request.get_json(silent=True) or {}
    ips_to_remove = data.get("ips") or []
    if not ips_to_remove:
        return jsonify({"ok": False, "error": "ips required"}), 400
    
    try:
        # Load current cache
        units = _load_cache()
        if not units:
            return jsonify({"ok": True, "removed": 0})
        
        # Filter out the units with IPs in the removal list
        original_count = len(units)
        units = [u for u in units if u.get("ip") not in ips_to_remove]
        removed_count = original_count - len(units)
        
        # Save the updated cache
        _save_cache(units)
        _write_csv(units)
        
        # Also update scan_results.json if it exists
        if SCAN_RESULTS.exists():
            try:
                with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                    scan_data = json.load(f)
                # Update devices, encoders, and decoders
                scan_data["devices"] = units
                scan_data["encoders"] = [u for u in units if u.get("role") == "encoder"]
                scan_data["decoders"] = [u for u in units if u.get("role") == "decoder"]
                scan_data["stats"]["devices_merged"] = len(units)
                scan_data["stats"]["encoders_found"] = len(scan_data["encoders"])
                scan_data["stats"]["decoders_found"] = len(scan_data["decoders"])
                with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                    json.dump(scan_data, f, indent=2)
                log.info(f"Updated scan_results.json after removing {removed_count} unit(s)")
            except Exception as e:
                log.warning(f"Failed to update scan_results.json: {e}")
        
        log.info(f"Removed {removed_count} unit(s) from cache")
        return jsonify({"ok": True, "removed": removed_count, "remaining": len(units)})
    except Exception as e:
        log.error(f"Error removing units: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/export_csv", methods=["POST"])
def api_export_csv_dup():
    units = _load_cache()
    _write_csv(units)
    return jsonify({"ok": True, "count": len(units), "path": str(CSV_VIEW)})

@app.route("/api/download_csv", methods=["GET"])
def api_download_csv_dup():
    if CSV_VIEW.exists():
        try:
            return send_file(str(CSV_VIEW), as_attachment=True, download_name="units_view.csv", mimetype="text/csv")
        except PermissionError:
            pass
        except Exception:
            pass
    units = _load_cache()
    return _stream_csv_from_units(units)

# --- upload/reset/poll (same as previous)
def _choose_field(model: str, filename: str) -> str:
    m = (model or "").lower().strip()
    f = (filename or "").lower()
    if m in OMNI2_MODELS: return "file"
    if (m in SINGLE_MODELS) or (m in DUAL_MODELS) or (m in RESI_MODELS): return "update"
    if "omni2" in f: return "file"
    if any(k in f for k in ["single","dual","residential"]): return "update"
    return "file"

def _get_firmware_type(filename: str) -> str:
    """Determine firmware type from filename keywords. Returns category or 'any' if no match."""
    f = (filename or "").lower()
    if "-dual-" in f:
        return "dual"
    elif "-single-" in f:
        return "single"
    elif "-omni2-" in f:
        return "omni2"
    elif "-residential-" in f:
        return "residential"
    return "any"  # No restriction

def _get_compatible_models(firmware_type: str) -> set:
    """Get set of device models compatible with firmware type."""
    if firmware_type == "dual":
        return DUAL_MODELS
    elif firmware_type == "single":
        return SINGLE_MODELS
    elif firmware_type == "omni2":
        return OMNI2_MODELS
    elif firmware_type == "residential":
        return RESI_MODELS
    return set()  # Empty means no models match (shouldn't happen)


_upload_locks = {}
_upload_locks_guard = threading.Lock()
def _lock_for_ip(ip: str):
    with _upload_locks_guard:
        if ip not in _upload_locks:
            _upload_locks[ip] = threading.Lock()
        return _upload_locks[ip]

def _http_upload_one_variant(ip: str, file_path: Path, field: str, timeout: float=60.0):
    urls = [
        f"http://{ip}/upload/",
        f"http://{ip}/upload",
        f"https://{ip}/upload/",
        f"https://{ip}/upload",
    ]
    last_err = None
    for url in urls:
        try:
            # Force a fixed upload filename to prevent device-side indexing
            # Multipart format: (filename, fileobj, content_type)
            files = {field: ("0000000001", open(file_path, "rb"), "application/octet-stream")}
            if url.startswith("https://"):
                r = requests.post(url, files=files, timeout=timeout, verify=False)
            else:
                r = requests.post(url, files=files, timeout=timeout)
            if 200 <= r.status_code < 300:
                return {"ok": True, "url": url, "field": field, "status": r.status_code}
        except Exception as e:
            last_err = str(e)
    return {"ok": False, "error": last_err or "upload failed", "field": field}

def _http_upload_file(ip: str, file_path: Path, field: str, timeout: float=60.0):
    return _http_upload_one_variant(ip, file_path, field, timeout=timeout)

def _get_latest_upload(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float) -> str:
    """Get the most recently uploaded file in /var/uploads on the device"""
    url = _ws_url(ip, ws_port, ws_path)
    
    # Simple, reliable approach: get full file listing with ls -1 and parse
    try:
        cmd = "ls -1 /var/uploads/"
        payload = {"id":"shell-list","username":user,"password":pwd,"method":{"shell":{"command":cmd}}}
        sslopt = None
        if url.startswith("wss://") and not app.config['WS_STRICT']:
            sslopt = {"cert_reqs": ssl.CERT_NONE}
        
        resp = _ws_send_recv(url, payload, timeout)
        output = ""
        if isinstance(resp, dict):
            output = (resp.get("response") or resp.get("stdout") or resp.get("output") or resp.get("raw") or "")
        elif isinstance(resp, str):
            output = resp
        
        output = (output or "").strip()
        log.info(f"[UPLOAD] Raw directory listing output length: {len(output)}")
        log.info(f"[UPLOAD] Directory listing output:\n{output}")
        
        if output:
            lines = output.split('\n')
            log.info(f"[UPLOAD] Split into {len(lines)} lines")
            
            # Filter to numeric filenames only
            numeric_files = []
            for i, line in enumerate(lines):
                line = line.strip()
                # Skip if empty or contains path separators
                if not line or '/' in line:
                    log.debug(f"[UPLOAD] Line {i} skipped (empty or contains /): '{line}'")
                    continue
                # Check if it's purely numeric
                if line.isdigit():
                    try:
                        num = int(line)
                        numeric_files.append(num)
                        log.info(f"[UPLOAD] Found numeric file: {num}")
                    except ValueError:
                        log.warning(f"[UPLOAD] Could not parse as int: '{line}'")
                else:
                    log.debug(f"[UPLOAD] Line {i} is not numeric: '{line}'")
            
            if numeric_files:
                # Sort and pick the highest number (most recently uploaded)
                numeric_files.sort()
                latest_num = numeric_files[-1]
                # Format with leading zeros to match what we saw on device
                file_path = f"/var/uploads/{latest_num:010d}"
                log.info(f"[UPLOAD] Found {len(numeric_files)} numeric files: {numeric_files}")
                log.info(f"[UPLOAD] Highest number: {latest_num}")
                log.info(f"[UPLOAD] Formatted path: {file_path}")
                return file_path
            else:
                log.warning(f"[UPLOAD] No numeric files found in {len(lines)} lines")
    except Exception as e:
        log.error(f"[UPLOAD] Exception during file listing: {e}", exc_info=True)
    
    # Fallback: try to find ANY numeric file
    try:
        cmd = "find /var/uploads -maxdepth 1 -type f -name '[0-9]*' | sort -V | tail -1"
        payload = {"id":"shell-find","username":user,"password":pwd,"method":{"shell":{"command":cmd}}}
        sslopt = None
        if url.startswith("wss://") and not app.config['WS_STRICT']:
            sslopt = {"cert_reqs": ssl.CERT_NONE}
        
        resp = _ws_send_recv(url, payload, timeout)
        output = ""
        if isinstance(resp, dict):
            output = (resp.get("response") or resp.get("stdout") or resp.get("output") or resp.get("raw") or "")
        elif isinstance(resp, str):
            output = resp
        
        output = (output or "").strip()
        log.info(f"[UPLOAD] Fallback find command output: '{output}'")
        if output and output.startswith("/var/uploads/"):
            log.info(f"[UPLOAD] Fallback found: {output}")
            return output
    except Exception as e:
        log.warning(f"[UPLOAD] Fallback find failed: {e}")
    
    # Last resort
    log.critical(f"[UPLOAD] Could not detect latest upload for {ip}, may fail")
    return "/var/uploads/0000000001"

def _ws_upgrade(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, file_path: str = None):
    url = _ws_url(ip, ws_port, ws_path)
    # Use provided file_path or detect the latest upload
    if not file_path:
        file_path = _get_latest_upload(ip, user, pwd, ws_port, ws_path, timeout)
    
    log.info("[UPGRADE_CMD] Sending upgrade command for: %s", file_path)
    
    # Standard upgrade command format
    payload = {
        "id": "upgrade-method",
        "username": user,
        "password": pwd,
        "method": {
            "upgrade": {
                "file": file_path
            }
        }
    }
    
    log.info("[UPGRADE_CMD] Payload JSON: %s", json.dumps(payload, indent=2))
    
    sslopt = None
    if url.startswith("wss://") and not app.config['WS_STRICT']:
        sslopt = {"cert_reqs": ssl.CERT_NONE}
    
    try:
        log.info("[UPGRADE_CMD] Opening WebSocket to: %s", url)
        resp = _ws_send_recv(url, payload, timeout)
        log.info("[UPGRADE_CMD] Upgrade response received: %s", resp)
        
        # Check for error response
        if isinstance(resp, dict):
            if resp.get("error"):
                log.error("[UPGRADE_CMD] Device returned error: %s", resp["error"])
                return {"ok": False, "error": resp["error"], "resp": resp}
        
        return {"ok": True, "resp": resp}
    except Exception as e:
        # Devices often reboot immediately after receiving the upgrade command, which
        # can close the websocket before we read a response. Fire the command again
        # without waiting for an ack and treat that as success so UI feedback relies
        # on post-upgrade polling instead of this transient error.
        log.warning("[UPGRADE_CMD] First attempt exception: %s (this may be normal if device is rebooting)", e)
        try:
            log.info("[UPGRADE_CMD] Retrying without waiting for response...")
            ws = websocket.create_connection(url, timeout=timeout, sslopt=sslopt)
            ws.send(json.dumps(payload))
            log.info("[UPGRADE_CMD] Command sent, closing socket...")
            try:
                ws.close()
            except Exception:
                pass
            log.info("[UPGRADE_CMD] Upgrade command sent (no ack), device likely rebooting")
            return {"ok": True, "resp": {"warning": "no_ack", "error": str(e)}}
        except Exception as e2:
            log.error("[UPGRADE_CMD] Retry also failed: %s", e2, exc_info=True)
            return {"ok": False, "error": str(e2), "root_error": str(e)}

# --- /api/poll endpoint ---
@app.route("/api/poll", methods=["POST"])
def api_poll():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400
    if not HAS_MATRIX or not omni_matrix_logic:
        return jsonify({"ok": False, "error": "matrix logic not available"}), 500
    try:
        log.info(f"[POLL] Polling {ip}...")
        status = omni_matrix_logic.poll_unit_status(ip)
        log.info(f"[POLL] {ip} status: {status}")
        
        # Resolve credentials and WS URL once, shared by all per-device queries below
        cache_devices = _load_cache()
        device = next((d for d in cache_devices if d.get("ip") == ip), {})
        user = device.get("username") or app.config['USERNAME']
        pwd = device.get("password") or app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']
        url = _ws_url(ip, ws_port, ws_path)

        # Query device directly for fresh firmware version (bypass cache which may be stale after upgrade)
        # Only do this if reasonable - don't hammer devices with constant WebSocket queries
        fresh_version = ""
        try:
            # Get systeminfo directly from device (fresh, not cached) - use very short timeout
            sysinfo_resp = _ws_send_recv(url, 
                {"id":"systeminfo-get","username":user,"password":pwd,"config_get":"systeminfo"}, 
                timeout=0.5)  # Short timeout - device may be busy
            
            if sysinfo_resp:
                # Try multiple locations where version might be
                sysinfo_cfg = (sysinfo_resp or {}).get("config") or {}
                fresh_version = (sysinfo_cfg.get("firmwareversion") or sysinfo_cfg.get("version") or "").strip()
                
                # If not found in config, check other locations
                if not fresh_version:
                    fresh_version = (sysinfo_resp.get("firmwareversion") or sysinfo_resp.get("version") or "").strip()
                
                if fresh_version:
                    log.info(f"[POLL] {ip} fresh firmware version from device: '{fresh_version}'")
        except Exception as e:
            # Silently skip fresh query on timeout or error - cached version is good enough
            pass
        
        # Use fresh version if available, otherwise use cached
        if fresh_version:
            status["fw"] = fresh_version
            status["version"] = fresh_version
            log.info(f"[POLL] {ip} updated from device: '{fresh_version}'")
        
        log.info(f"[POLL] {ip} final version: '{status.get('fw', '')}'")

        # Fetch link speed via net-get
        try:
            net_resp = _ws_send_recv(url,
                {"id": "net-get", "username": user, "password": pwd, "config_get": "net"},
                timeout=min(timeout, 2.0))
            if net_resp and not net_resp.get("error"):
                net_cfg = net_resp.get("config") or []
                # Use eth1 if present, otherwise first entry with a linkspeed value
                link_speed = None
                for iface in net_cfg:
                    if iface.get("name") == "eth1" and "linkspeed" in iface:
                        link_speed = iface["linkspeed"]
                        break
                if link_speed is None:
                    for iface in net_cfg:
                        if "linkspeed" in iface:
                            link_speed = iface["linkspeed"]
                            break
                if link_speed is not None:
                    status["linkspeed"] = link_speed
                    log.info(f"[POLL] {ip} linkspeed: {link_speed}")
                else:
                    log.info(f"[POLL] {ip} net-get returned no linkspeed field")
            else:
                log.info(f"[POLL] {ip} net-get error or empty response: {net_resp}")
        except Exception as e:
            log.info(f"[POLL] {ip} net-get failed: {e}")

        # If status is disconnected, return ok:False
        if status.get("status") == "disconnected":
            log.warning(f"[POLL] {ip} returned disconnected status")
            return jsonify({"ok": False, "unit": status})
        
        # Try to fetch USB info if device is USB-capable
        usb_data = {}
        try:
            model = (status.get("model") or "").lower()
            usb_models = ["hw-omni-e4521", "hw-omni-d4521", "hw-omni-e4511", "hw-omni-d4511", "4521", "4511"]
            if any(m in model for m in usb_models):
                usb_resp = _ws_send_recv(url, {"id":"usb_icron-get","username":user,"password":pwd,"config_get":"usb_icron"}, timeout=min(timeout, 2.0))
                usb_cfg = (usb_resp or {}).get("config") or {}
                
                role = (usb_cfg.get("type") or "").upper()
                if role:
                    usb_data = {
                        "role": role,
                        "mac": usb_cfg.get("macaddress", ""),
                        "host_port": usb_cfg.get("usbhostport-current") or usb_cfg.get("usbhostport", ""),
                        "found_count": len(usb_cfg.get("found_devices") or {}),
                    }
                    log.info(f"[POLL] {ip} USB info: {usb_data}")
        except Exception as e:
            log.debug(f"[POLL] {ip} USB query failed (non-critical): {e}")
        
        return jsonify({"ok": True, "unit": status, "usb": usb_data})
    except Exception as e:
        log.error(f"[POLL] Error polling {ip}: {e}")
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/poll_decoders", methods=["POST"])
def api_poll_decoders():
    """Poll all decoders for their current input settings and update cache"""
    if not HAS_MATRIX:
        return jsonify({"ok": False, "error": "matrix logic unavailable"}), 500
    
    data = request.get_json(silent=True) or {}
    decoder_ips = data.get("decoders") or []
    
    if not decoder_ips:
        return jsonify({"ok": False, "error": "decoders array required"}), 400
    
    default_user = app.config['USERNAME']
    default_pwd = app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    
    # Load cache for device-specific passwords
    cache_devices = {d.get("ip"): d for d in _load_cache()}
    
    results = {}
    updated_count = 0
    
    log.info(f"[POLL_DECODERS] Polling {len(decoder_ips)} decoders in parallel...")
    
    # Poll all decoders in parallel with limited concurrency (2 workers to avoid overwhelming server)
    with ThreadPoolExecutor(max_workers=2) as executor:
        def poll_decoder(ip):
            try:
                # Get device-specific password from cache, fall back to default
                device = cache_devices.get(ip, {})
                user = device.get("username") or default_user
                pwd = device.get("password") or default_pwd
                
                # Use reasonable timeout for polling - give devices time to respond
                fields = _ws_get_decoder_inputs(ip, user, pwd, ws_port, ws_path, timeout=4, attempts=1, delay=0)
                if fields and any(fields.get(k) for k in ("ip1_addr", "ip1_port", "ip3_addr", "ip3_port")):
                    return (ip, fields, True)
                else:
                    return (ip, {"error": "failed to fetch"}, False)
            except Exception as e:
                return (ip, {"error": str(e)}, False)
        
        for ip, fields, success in executor.map(poll_decoder, decoder_ips):
            results[ip] = fields
            if success:
                # Update in-memory decoder state
                if ip in omni_matrix_logic._decoders:
                    omni_matrix_logic._decoders[ip].update(fields)
                    updated_count += 1
                log.info(f"[POLL_DECODERS] {ip}: {fields}")
            else:
                log.warning(f"[POLL_DECODERS] {ip}: {fields}")
    
    # Save updated decoder input fields into server cache (avoid overwriting hostnames)
    if updated_count > 0:
        try:
            units = _load_cache() or []
            dec_fields = {ip: fields for ip, fields in results.items() if isinstance(fields, dict) and "error" not in fields}
            for u in units:
                if u.get("ip") in dec_fields:
                    u.update({k: v for k, v in dec_fields[u.get("ip")].items() if k in ("ip1_addr", "ip1_port", "ip3_addr", "ip3_port")})
            _save_cache(units)
            log.info(f"[POLL_DECODERS] Updated {updated_count} decoders, saved to cache")
        except Exception as e:
            log.error(f"[POLL_DECODERS] Failed to save cache: {e}")
    
    return jsonify({"ok": True, "results": results, "updated": updated_count})


@app.route("/api/debug/latest_upload", methods=["POST"])
def api_debug_latest_upload():
    """Debug endpoint to test latest upload detection on a device"""
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400
    
    user = app.config['USERNAME']
    pwd = app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    
    try:
        result = _get_latest_upload(ip, user, pwd, ws_port, ws_path, timeout)
        log.info(f"[DEBUG] Latest upload detected: {result}")
        return jsonify({"ok": True, "latest_upload": result})
    except Exception as e:
        log.error(f"[DEBUG] Error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/cleanup_uploads", methods=["POST"])
def api_cleanup_uploads():
    """Cleanup old firmware upload files on device"""
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip: return jsonify({"ok": False, "error": "ip required"}), 400
    
    # Cleanup via WebSocket shell commands not supported on current device firmware
    return jsonify({"ok": False, "error": "Cleanup not supported - device firmware does not support shell commands via WebSocket"}), 501

@app.route("/api/reset", methods=["POST"])
def api_reset():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip: return jsonify({"ok": False, "error": "ip required"}), 400
    user = data.get("username") or app.config['USERNAME']
    pwd = data.get("password") or app.config['PASSWORD']
    ws_port = app.config['WS_PORT']; ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    url = _ws_url(ip, ws_port, ws_path)
    payload = {"id":"factory_reset-method","username":user,"password":pwd,"method":{"factory_reset":{"reset_net":False,"reset_auth":False,"reset_to_defaults":False}}}
    try:
        resp = _ws_send_recv(url, payload, timeout)
        return jsonify({"ok": True, "resp": resp})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/upgrade", methods=["POST"])
def api_upgrade():
    try:
        data = request.get_json(silent=True) or {}
        file_name = (data.get("file") or "").strip()
        targets = data.get("targets") or []
        log.info("[UPGRADE] Received upgrade request for targets: %r", targets)
        if not file_name or not targets:
            return jsonify({"ok": False, "error": "file and targets required"}), 400
        
        # Use firmware path from config if set
        fw_path_str = app.config.get('FIRMWARE_PATH', '').strip()
        if fw_path_str:
            fw_dir = Path(fw_path_str)
            if not fw_dir.is_absolute():
                fw_dir = CWD / fw_path_str
        else:
            fw_dir = CWD
        
        file_path = (fw_dir / file_name).resolve()
        if not (file_path.exists() and file_path.is_file()):
            return jsonify({"ok": False, "error": "file not found"}), 400

        user = data.get("username") or app.config['USERNAME']
        default_pwd = data.get("password") or app.config['PASSWORD']
        ws_port = app.config['WS_PORT']; ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']
        conc = int(data.get("concurrency") or app.config.get('UPLOAD_CONCURRENCY', 6))
        conc = max(1, min(16, conc))

        # Load full device cache to access model and password per device
        cache_devices = _load_cache()
        cache_map = { (u.get("ip") or ""): u for u in cache_devices }  # ip -> full device dict
        
        # Determine firmware type from filename and validate targets
        fw_type = _get_firmware_type(file_name)
        if fw_type != "any":
            compatible_models = _get_compatible_models(fw_type)
            log.info("[UPGRADE] Firmware type: %s, compatible models: %s", fw_type, compatible_models)
            
            # Filter targets to only compatible devices
            incompatible_ips = []
            for ip in targets:
                device = cache_map.get(ip, {})
                model = (device.get("model") or "").lower().strip()
                if model and model not in compatible_models:
                    incompatible_ips.append(ip)
                    log.warning("[UPGRADE] Device %s (model: %s) is incompatible with %s firmware", ip, model, fw_type)
            
            if incompatible_ips:
                return jsonify({
                    "ok": False, 
                    "error": f"Firmware type '{fw_type}' is not compatible with {len(incompatible_ips)} device(s)",
                    "incompatible_devices": incompatible_ips,
                    "expected_models": list(compatible_models)
                }), 400

        results = {}
        def job(ip):
            lock = _lock_for_ip(ip)
            if not lock.acquire(blocking=False):
                return {"ip": ip, "ok": False, "stage": "throttle", "error": "another upload in progress"}
            try:
                # Get device-specific password from cache, fall back to default if not stored
                device = cache_map.get(ip, {})
                device_pwd = device.get("password") or default_pwd
                model = (device.get("model") or "").lower().strip()
                field = _choose_field(model, file_name)
                
                # Clean up old upload files first to prevent disk filling
                # Note: Direct shell commands via WebSocket not supported on current device firmware
                # Files will accumulate in /var/uploads; manual cleanup may be needed if disk fills
                log.info("[UPGRADE] Skipping cleanup - device firmware does not support shell commands via WebSocket")
                
                log.info("[UPGRADE] Uploading to %s (field=%s, file_size=%.1fMB)", ip, field, file_path.stat().st_size / (1024*1024))
                up = _http_upload_file(ip, file_path, field=field, timeout=90.0)
                if not up.get("ok"):
                    return {"ip": ip, "ok": False, "stage": "upload", "error": up.get("error") or "upload failed"}
                
                log.info("[UPGRADE] Upload successful, initiating upgrade on %s", ip)
                time.sleep(2.0)  # Give device more time to finalize file write
                
                # Verify we can detect the uploaded file before sending upgrade command
                log.info("[UPGRADE] Detecting uploaded file on %s...", ip)
                detected_file = _get_latest_upload(ip, user, device_pwd, ws_port, ws_path, timeout)
                log.info("[UPGRADE] Detected file path: %s", detected_file)
                time.sleep(0.5)  # Additional settle time
                
                if not detected_file or "/var/uploads/" not in detected_file:
                    log.error("[UPGRADE] Failed to detect uploaded file, not sending upgrade command")
                    return {"ip": ip, "ok": False, "stage": "detection", "error": f"could not detect uploaded file (got: {detected_file})"}
                
                ws = _ws_upgrade(ip, user, device_pwd, ws_port, ws_path, timeout=timeout, file_path=detected_file)
                log.info("[UPGRADE] Upgrade response for %s: %s", ip, ws)
                if not ws.get("ok"):
                    return {"ip": ip, "ok": False, "stage": "ws", "error": ws.get("error")}
                
                log.info("[UPGRADE] Upgrade command sent successfully for %s", ip)
                return {"ip": ip, "ok": True, "stage": "done"}
            finally:
                try: lock.release()
                except Exception: pass

        with ThreadPoolExecutor(max_workers=conc) as ex:
            futs = {ex.submit(job, ip): ip for ip in targets}
            for fut in as_completed(futs):
                ip = futs[fut]
                try:
                    results[ip] = fut.result()
                except Exception as e:
                    results[ip] = {"ip": ip, "ok": False, "stage": "exception", "error": str(e)}

        return jsonify({"ok": True, "results": results})
    except Exception as e:
        log.exception("[UPGRADE] Unexpected error in api_upgrade")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/blink", methods=["POST"])
def api_blink():
    """Send identify/blink command to device"""
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip: return jsonify({"ok": False, "error": "ip required"}), 400
    
    user = app.config['USERNAME']
    pwd = app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    
    try:
        ws_url = f"{'wss' if ws_port == 443 else 'ws'}://{ip}:{ws_port}{ws_path}"
        ws = websocket.create_connection(ws_url, timeout=timeout, sslopt={"cert_reqs":ssl.CERT_NONE} if ws_port==443 else {})
        
        payload = {
            "id": "identify-method",
            "username": user,
            "password": pwd,
            "method": {"identify": {}}
        }
        ws.send(json.dumps(payload))
        resp = ws.recv()
        ws.close()
        
        try:
            r = json.loads(resp)
            return jsonify({"ok": not r.get("error"), "response": r})
        except:
            return jsonify({"ok": True, "response": resp})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/reboot", methods=["POST"])
def api_reboot():
    """Send reboot command to device(s)"""
    data = request.get_json(silent=True) or {}
    ips = data.get("ips") or []
    if not ips: return jsonify({"ok": False, "error": "ips required"}), 400
    log.info("[REBOOT] Requested for %d device(s): %s", len(ips), ips)
    
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    cache_devices = _load_cache() or []
    cache_map = {d.get("ip"): d for d in cache_devices if d.get("ip")}
    
    results = {}
    def reboot_one(ip):
        user, preferred_pwd, _ = _device_credentials(ip, cache_map)
        last_error = "reboot failed"
        for pwd_try in _password_candidates(preferred_pwd):
            try:
                ws_url = f"{'wss' if ws_port == 443 else 'ws'}://{ip}:{ws_port}{ws_path}"
                ws = websocket.create_connection(ws_url, timeout=timeout, sslopt={"cert_reqs":ssl.CERT_NONE} if ws_port==443 else {})

                payload = {
                    "id": "reboot-method",
                    "username": user,
                    "password": pwd_try,
                    "method": {"reboot": {}}
                }
                ws.send(json.dumps(payload))
                try:
                    resp = ws.recv()
                    try:
                        r = json.loads(resp)
                        if r.get("error"):
                            last_error = str(r.get("error"))
                            continue
                        return {"ip": ip, "ok": True, "response": r, "used_password": pwd_try}
                    except Exception:
                        return {"ip": ip, "ok": True, "response": resp, "used_password": pwd_try}
                except Exception as e:
                    # Device may close the socket immediately on reboot; treat as success
                    return {"ip": ip, "ok": True, "response": {"warning": "no_ack", "error": str(e)}, "used_password": pwd_try}
                finally:
                    try:
                        ws.close()
                    except Exception:
                        pass
            except Exception as e:
                last_error = str(e)
                continue
        return {"ip": ip, "ok": False, "error": last_error}
    
    with ThreadPoolExecutor(max_workers=min(6, len(ips))) as ex:
        futs = {ex.submit(reboot_one, ip): ip for ip in ips}
        for fut in as_completed(futs):
            ip = futs[fut]
            try:
                results[ip] = fut.result()
            except Exception as e:
                results[ip] = {"ip": ip, "ok": False, "error": str(e)}
            if results.get(ip, {}).get("ok"):
                log.info("[REBOOT] %s ok", ip)
            else:
                log.warning("[REBOOT] %s failed: %s", ip, results.get(ip))

    changed = False
    for unit in cache_devices:
        ip = unit.get("ip")
        used = (results.get(ip) or {}).get("used_password")
        if used and unit.get("password") != used:
            unit["password"] = used
            changed = True
    if changed:
        _save_cache(cache_devices)
    
    return jsonify({"ok": True, "results": results})

# ================ USB Matrix Endpoints ================

@app.route("/api/usb_state", methods=["GET"])
def api_usb_state():
    """Return current USB state: LEX units, REX units, and pairing info"""
    try:
        # Load from cache first (for testing with 100 units)
        all_devices = _load_cache()
        if not all_devices:
            # Fall back to scan results
            if SCAN_RESULTS.exists():
                try:
                    with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        all_devices = data.get("devices", [])
                except Exception as e:
                    log.info("Failed to load scan results: %s", e)
                    all_devices = []
        
        # Filter to USB-capable models only
        units = []
        usb_models = ["hw-omni-e4521", "hw-omni-d4521", "hw-omni-e4511", "hw-omni-d4511", "4521", "4511"]
        for dev in all_devices:
            model = (dev.get("model") or "").lower()
            if any(m in model for m in usb_models):
                units.append(dev)
        
        log.info(f"[API/USB_STATE] Found {len(units)} USB-capable devices from {len(all_devices)} total")
        
        if not units:
            return jsonify({"ok": True, "lex": [], "rex": [], "pairings": {}})
        
        user = app.config['USERNAME']
        pwd = app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']
        
        lex_units = []
        rex_units = []
        pairings = {}  # {rex_ip: {active: lex_ip, available: [lex_ip, ...]}}
        
        # Query each USB-capable device for USB info
        def get_usb_info(u):
            ip = u.get("ip", "")
            hostname = u.get("hostname", "")
            if not ip:
                return None
            
            try:
                url = _ws_url(ip, ws_port, ws_path)
                usb = _ws_send_recv(url, {"id":"usb_icron-get","username":user,"password":pwd,"config_get":"usb_icron"}, timeout=min(timeout, 2.0))
                usb_cfg = (usb or {}).get("config") or {}
                
                role = (usb_cfg.get("type") or "").upper()
                if not role:
                    return None
                
                # Parse paired_devices for actual pairing info (REX only)
                active_lex = None
                available_lex = []
                if role == "REX":
                    paired_devices = usb_cfg.get("paired_devices") or {}
                    found_devices = usb_cfg.get("found_devices") or {}
                    
                    log.info("Device %s paired_devices: %s", ip, paired_devices)
                    
                    for mac, info in paired_devices.items():
                        peer_ip = info.get("host_ipaddress", "") or info.get("ipaddress", "")
                        if peer_ip:
                            # Check if currently linked/active
                            is_linked = info.get("linked", False)
                            if is_linked:
                                active_lex = peer_ip
                            else:
                                    available_lex.append(peer_ip)
                
                return {
                    "ip": ip,
                    "host": hostname,
                    "role": role,
                    "usb_ip": usb_cfg.get("ipaddress", ""),
                    "mac": usb_cfg.get("macaddress", ""),
                    "revision": usb_cfg.get("revision", ""),
                    "protocol": usb_cfg.get("protocol", ""),
                    "host_port": usb_cfg.get("usbhostport-current") or usb_cfg.get("usbhostport", ""),
                    "filter": usb_cfg.get("usbfiltering", ""),
                    "active_lex": active_lex,
                    "available_lex": available_lex,
                    "found_count": len(usb_cfg.get("found_devices") or {}),
                }
            except Exception as e:
                log.info("USB query failed for %s: %s", ip, e)
                return None
        
        # Query devices in parallel
        with ThreadPoolExecutor(max_workers=min(8, len(units))) as ex:
            futures = {ex.submit(get_usb_info, u): u for u in units}
            for fut in as_completed(futures):
                result = fut.result()
                if not result:
                    continue
                
                role = result.get("role", "")
                if role == "LEX":
                    lex_units.append({
                        "ip": result["ip"],
                        "host": result["host"],
                        "usb_ip": result["usb_ip"],
                        "mac": result["mac"],
                        "revision": result["revision"],
                        "protocol": result["protocol"],
                        "host_port": result["host_port"],
                        "filter": result.get("filter", ""),
                    })
                elif role == "REX":
                    rex_ip = result["ip"]
                    rex_units.append({
                        "ip": rex_ip,
                        "host": result["host"],
                        "usb_ip": result["usb_ip"],
                        "mac": result["mac"],
                        "revision": result["revision"],
                        "protocol": result["protocol"],
                        "host_port": result["host_port"],
                        "filter": result.get("filter", ""),
                    })
                    pairings[rex_ip] = {
                        "active": result.get("active_lex"),
                        "available": result.get("available_lex", []),
                    }
        
        return jsonify({
            "ok": True,
            "lex": lex_units,
            "rex": rex_units,
            "pairings": pairings
        })
    except Exception as e:
        log.exception("usb_state error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_pair", methods=["POST"])
def api_usb_pair():
    """Pair a LEX to a REX (add to available list or set as active)"""
    data = request.get_json(silent=True) or {}
    rex_ip = data.get("rex")
    lex_ip = data.get("lex")
    make_active = data.get("makeActive", False)
    
    if not rex_ip or not lex_ip:
        return jsonify({"ok": False, "error": "rex and lex IPs required"}), 400
    
    try:
        user = app.config['USERNAME']
        pwd = app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']
        
        # First, get current USB config from LEX to get its details
        lex_url = _ws_url(lex_ip, ws_port, ws_path)
        lex_usb = _ws_send_recv(lex_url, {
            "id": "usb_icron-get",
            "username": user,
            "password": pwd,
            "config_get": "usb_icron"
        }, timeout=timeout)
        
        lex_cfg = (lex_usb or {}).get("config") or {}
        lex_mac = lex_cfg.get("macaddress", "")
        lex_hostname = ""
        
        # Get LEX hostname from systeminfo
        try:
            lex_sysinfo = _ws_send_recv(lex_url, {
                "id": "systeminfo-get",
                "username": user,
                "password": pwd,
                "config_get": "systeminfo"
            }, timeout=timeout)
            lex_hostname = ((lex_sysinfo or {}).get("config") or {}).get("hostname", "")
        except Exception:
            pass
        
        if not lex_mac:
            return jsonify({"ok": False, "error": "Could not get LEX MAC address"}), 500
        
        log.info("USB pair: LEX %s (MAC: %s) -> REX %s (exclusive - will replace existing pairings)", lex_ip, lex_mac, rex_ip)
        
        # Get current paired devices from REX
        rex_url = _ws_url(rex_ip, ws_port, ws_path)
        rex_usb = _ws_send_recv(rex_url, {
            "id": "usb_icron-get",
            "username": user,
            "password": pwd,
            "config_get": "usb_icron"
        }, timeout=timeout)
        
        rex_cfg = (rex_usb or {}).get("config") or {}
        # Clear existing paired devices - exclusive pairing
        paired_devices = {}
        
        # Add only the new LEX
        lex_mac_upper = lex_mac.upper()
        paired_devices[lex_mac_upper] = {
            "host_hostname": lex_hostname or "",
            "host_ipaddress": lex_ip,
            "ipaddress": lex_cfg.get("ipaddress", ""),
            "macaddress": lex_mac_upper,
            "product": lex_cfg.get("product", "USB Over Network"),
            "protocol": lex_cfg.get("protocol", "IP"),
            "revision": lex_cfg.get("revision", ""),
            "type": "LEX",
            "vendor": lex_cfg.get("vendor", ""),
            "typeL": "Host end"
        }
        
        # Step 1: Clear existing pairings first
        clear_payload = {
            "id": "usb_icron-set",
            "username": user,
            "password": pwd,
            "config_set": {
                "name": "usb_icron",
                "config": {
                    "paired_devices": {}
                }
            }
        }
        
        log.info("Step 1: Clearing existing pairings on REX %s", rex_ip)
        clear_response = _ws_send_recv(rex_url, clear_payload, timeout=timeout)
        log.info("Clear response: %s", json.dumps(clear_response, indent=2))
        
        if not clear_response or clear_response.get("error"):
            error_msg = clear_response.get("error", "Unknown error") if clear_response else "No response"
            return jsonify({"ok": False, "error": f"Clear failed: {error_msg}"}), 500
        
        # Step 2: Wait a moment for device to process the clear
        import time
        time.sleep(0.5)
        
        # Step 3: Set new pairing
        pairing_payload = {
            "id": "usb_icron-set",
            "username": user,
            "password": pwd,
            "config_set": {
                "name": "usb_icron",
                "config": {
                    "paired_devices": paired_devices
                }
            }
        }
        
        log.info("Step 2: Setting new pairing on REX %s: %s", rex_ip, json.dumps(pairing_payload, indent=2))
        
        response = _ws_send_recv(rex_url, pairing_payload, timeout=timeout)
        
        log.info("Pairing response from REX %s: %s", rex_ip, json.dumps(response, indent=2))
        
        if response and not response.get("error"):
            return jsonify({"ok": True, "message": "Pairing successful", "response": response})
        else:
            error_msg = response.get("error", "Unknown error") if response else "No response"
            return jsonify({"ok": False, "error": error_msg, "response": response}), 500
        
    except Exception as e:
        log.exception("usb_pair error")
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        log.exception("usb_pair error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_unpair", methods=["POST"])
def api_usb_unpair():
    """Unpair a LEX from a REX (remove from available/active)"""
    data = request.get_json(silent=True) or {}
    rex_ip = data.get("rex")
    lex_ip = data.get("lex")
    
    if not rex_ip or not lex_ip:
        return jsonify({"ok": False, "error": "rex and lex IPs required"}), 400
    
    try:
        user = app.config['USERNAME']
        pwd = app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']
        
        # Get LEX MAC address
        lex_url = _ws_url(lex_ip, ws_port, ws_path)
        lex_usb = _ws_send_recv(lex_url, {
            "id": "usb_icron-get",
            "username": user,
            "password": pwd,
            "config_get": "usb_icron"
        }, timeout=timeout)
        
        lex_cfg = (lex_usb or {}).get("config") or {}
        lex_mac = lex_cfg.get("macaddress", "")
        
        if not lex_mac:
            return jsonify({"ok": False, "error": "Could not get LEX MAC address"}), 500
        
        log.info("USB unpair: LEX %s (MAC: %s) from REX %s", lex_ip, lex_mac, rex_ip)
        
        # Get current paired devices from REX
        rex_url = _ws_url(rex_ip, ws_port, ws_path)
        rex_usb = _ws_send_recv(rex_url, {
            "id": "usb_icron-get",
            "username": user,
            "password": pwd,
            "config_get": "usb_icron"
        }, timeout=timeout)
        
        rex_cfg = (rex_usb or {}).get("config") or {}
        paired_devices = dict(rex_cfg.get("paired_devices", {}))
        
        # Remove the LEX from paired devices
        lex_mac_upper = lex_mac.upper()
        if lex_mac_upper in paired_devices:
            del paired_devices[lex_mac_upper]
            log.info("Removed LEX MAC %s from REX paired devices", lex_mac_upper)
        else:
            log.info("LEX MAC %s not found in REX paired devices (may already be unpaired)", lex_mac_upper)
        
        # Send updated paired_devices back to REX
        unpair_payload = {
            "id": "usb_icron-set",
            "username": user,
            "password": pwd,
            "config_set": {
                "name": "usb_icron",
                "config": {
                    "paired_devices": paired_devices
                }
            }
        }
        
        log.info("Sending unpair command to REX %s: %s", rex_ip, json.dumps(unpair_payload, indent=2))
        
        response = _ws_send_recv(rex_url, unpair_payload, timeout=timeout)
        
        log.info("Unpair response from REX %s: %s", rex_ip, json.dumps(response, indent=2))
        
        if response and not response.get("error"):
            return jsonify({"ok": True, "message": "Unpairing successful", "response": response})
        else:
            error_msg = response.get("error", "Unknown error") if response else "No response"
            return jsonify({"ok": False, "error": error_msg, "response": response}), 500
        
    except Exception as e:
        log.exception("usb_unpair error")
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        log.exception("usb_unpair error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_set_port", methods=["POST"])
def api_usb_set_port():
    """Set the USB host port for a LEX device"""
    data = request.get_json(silent=True) or {}
    lex_ip = data.get("lex")
    port = data.get("port")
    
    if not lex_ip or not port:
        return jsonify({"ok": False, "error": "lex IP and port required"}), 400
    
    if port not in ["FollowVideo", "USB-C", "USB-B"]:
        return jsonify({"ok": False, "error": "Invalid port value"}), 400
    
    try:
        user = app.config['USERNAME']
        pwd = app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']
        
        log.info("USB set host port: LEX %s -> %s", lex_ip, port)
        
        lex_url = _ws_url(lex_ip, ws_port, ws_path)
        
        port_payload = {
            "id": "usb_icron-set",
            "username": user,
            "password": pwd,
            "config_set": {
                "name": "usb_icron",
                "config": {
                    "type": "LEX",
                    "usbhostport": port,
                    "usbfiltering": "Allow_All"
                }
            }
        }
        
        log.info("Sending port change command to LEX %s: %s", lex_ip, json.dumps(port_payload, indent=2))
        
        response = _ws_send_recv(lex_url, port_payload, timeout=timeout)
        
        log.info("Port change response from LEX %s: %s", lex_ip, json.dumps(response, indent=2))
        
        if response and not response.get("error"):
            return jsonify({"ok": True, "message": "Port changed successfully", "response": response})
        else:
            error_msg = response.get("error", "Unknown error") if response else "No response"
            return jsonify({"ok": False, "error": error_msg, "response": response}), 500
        
    except Exception as e:
        log.exception("usb_set_port error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_set_type", methods=["POST"])
def api_usb_set_type():
    """Change the USB device type between LEX and REX"""
    data = request.get_json(silent=True) or {}
    device_ip = data.get("device")
    new_type = data.get("type")
    
    if not device_ip or not new_type:
        return jsonify({"ok": False, "error": "device IP and type required"}), 400
    
    if new_type not in ["LEX", "REX"]:
        return jsonify({"ok": False, "error": "Invalid type value"}), 400
    
    try:
        user = app.config['USERNAME']
        pwd = app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']
        
        log.info("USB set type: Device %s -> %s", device_ip, new_type)
        
        # Get current USB config to preserve port and filtering settings
        device_url = _ws_url(device_ip, ws_port, ws_path)
        current_usb = _ws_send_recv(device_url, {
            "id": "usb_icron-get",
            "username": user,
            "password": pwd,
            "config_get": "usb_icron"
        }, timeout=timeout)
        
        current_cfg = (current_usb or {}).get("config") or {}
        current_port = current_cfg.get("usbhostport-current") or current_cfg.get("usbhostport") or "FollowVideo"
        
        # Build config based on device type
        config = {
            "type": new_type,
            "usbfiltering": "Allow_All"
        }
        
        # Only include usbhostport for LEX devices
        if new_type == "LEX":
            config["usbhostport"] = current_port
        
        type_payload = {
            "id": "usb_icron-set",
            "username": user,
            "password": pwd,
            "config_set": {
                "name": "usb_icron",
                "config": config
            }
        }
        
        log.info("Sending type change command to device %s: %s", device_ip, json.dumps(type_payload, indent=2))
        
        response = _ws_send_recv(device_url, type_payload, timeout=timeout)
        
        log.info("Type change response from device %s: %s", device_ip, json.dumps(response, indent=2))
        
        if response and not response.get("error"):
            return jsonify({"ok": True, "message": "Type changed successfully", "response": response})
        else:
            error_msg = response.get("error", "Unknown error") if response else "No response"
            return jsonify({"ok": False, "error": error_msg, "response": response}), 500
        
    except Exception as e:
        log.exception("usb_set_type error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_set_filter", methods=["POST"])
def api_usb_set_filter():
    """Set USB device filtering policy for encoders and decoders"""
    data = request.get_json(silent=True) or {}
    device_ip = (data.get("device") or "").strip()
    filter_val = (data.get("filter") or "").strip()

    allowed = [
        "Allow_All",
        "Allow_Hid_Hub",
        "Allow_Hid_Hub_Smartcard",
        "Block_Isochronous",
        "Block_MassStorage",
        "Block_Isochronous_MassStorage",
    ]

    if not device_ip or not filter_val:
        return jsonify({"ok": False, "error": "device and filter required"}), 400
    if filter_val not in allowed:
        return jsonify({"ok": False, "error": "Invalid filter value"}), 400

    try:
        user = app.config['USERNAME']
        pwd = app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']

        device_url = _ws_url(device_ip, ws_port, ws_path)
        # Get current config to preserve type and host port
        current_usb = _ws_send_recv(device_url, {
            "id": "usb_icron-get",
            "username": user,
            "password": pwd,
            "config_get": "usb_icron"
        }, timeout=timeout)

        cfg = (current_usb or {}).get("config") or {}
        current_type = (cfg.get("type") or "").upper() or "REX"
        current_port = cfg.get("usbhostport-current") or cfg.get("usbhostport") or "FollowVideo"

        set_cfg = {
            "type": current_type,
            "usbfiltering": filter_val,
        }
        if current_type == "LEX":
            set_cfg["usbhostport"] = current_port

        payload = {
            "id": "usb_icron-set",
            "username": user,
            "password": pwd,
            "config_set": {
                "name": "usb_icron",
                "config": set_cfg
            }
        }

        log.info("USB set filter: %s -> %s", device_ip, filter_val)
        log.info("Sending filter change: %s", json.dumps(payload, indent=2))

        response = _ws_send_recv(device_url, payload, timeout=timeout)
        log.info("Filter change response from %s: %s", device_ip, json.dumps(response, indent=2))

        if response and not response.get("error"):
            return jsonify({"ok": True, "message": "Filter changed", "response": response})
        else:
            return jsonify({"ok": False, "error": response.get("error", "Unknown error"), "response": response}), 500
    except Exception as e:
        log.exception("usb_set_filter error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/set_thumbnail", methods=["POST"])
def api_set_thumbnail():
    """Enable or disable thumbnail generation on an encoder"""
    data = request.get_json(silent=True) or {}
    device_ip = data.get("ip")
    enable = data.get("enable", True)

    if not device_ip:
        return jsonify({"ok": False, "error": "IP address required"}), 400

    try:
        user = app.config['USERNAME']
        pwd = app.config['PASSWORD']
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']

        device_url = _ws_url(device_ip, ws_port, ws_path)
        
        # Get current vc2 config to preserve existing settings
        current_vc2 = _ws_send_recv(device_url, {
            "id": "vc2-get",
            "username": user,
            "password": pwd,
            "config_get": "vc2"
        }, timeout=timeout)

        vc2_cfg = (current_vc2 or {}).get("config") or []
        if not vc2_cfg or len(vc2_cfg) == 0:
            return jsonify({"ok": False, "error": "No VC2 encoder configuration found"}), 400

        # Update first encoder's thumbnail config
        encoder = vc2_cfg[0]
        if "thumbnail" not in encoder:
            encoder["thumbnail"] = {}
        
        encoder["thumbnail"]["enable"] = enable
        if enable:
            # Set default thumbnail parameters if enabling
            encoder["thumbnail"].setdefault("framerate", 5)
            encoder["thumbnail"].setdefault("height", 180)
            encoder["thumbnail"].setdefault("width", 320)

        payload = {
            "id": "vc2-set",
            "username": user,
            "password": pwd,
            "config_set": {
                "name": "vc2",
                "config": vc2_cfg
            }
        }

        log.info("Thumbnail %s for %s", "enable" if enable else "disable", device_ip)
        log.info("Sending vc2-set: %s", json.dumps(payload, indent=2))

        response = _ws_send_recv(device_url, payload, timeout=timeout)
        log.info("Thumbnail response from %s: %s", device_ip, json.dumps(response, indent=2))

        if response and not response.get("error"):
            return jsonify({"ok": True, "message": f"Thumbnail {'enabled' if enable else 'disabled'}", "response": response})
        else:
            return jsonify({"ok": False, "error": response.get("error", "Unknown error"), "response": response}), 500
    except Exception as e:
        log.exception("set_thumbnail error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/get_thumbnail_status", methods=["POST"])
def api_get_thumbnail_status():
    """Get thumbnail enable status from an encoder"""
    data = request.get_json(silent=True) or {}
    device_ip = data.get("ip")

    if not device_ip:
        return jsonify({"ok": False, "error": "IP address required"}), 400

    try:
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']

        cache_devices = _load_cache() or []
        cache_map = {d.get("ip"): d for d in cache_devices if d.get("ip")}
        user, preferred_pwd, _ = _device_credentials(device_ip, cache_map)
        device_url = _ws_url(device_ip, ws_port, ws_path)

        last_error = "vc2-get failed"
        for pwd_try in _password_candidates(preferred_pwd):
            try:
                current_vc2 = _ws_send_recv(device_url, {
                    "id": "vc2-get",
                    "username": user,
                    "password": pwd_try,
                    "config_get": "vc2"
                }, timeout=timeout)

                if not current_vc2 or current_vc2.get("error"):
                    last_error = (current_vc2 or {}).get("error") or "vc2-get failed"
                    continue

                vc2_cfg = (current_vc2 or {}).get("config") or []
                if not vc2_cfg or len(vc2_cfg) == 0:
                    return jsonify({"ok": False, "enable": False, "error": "No VC2 encoder configuration found"}), 200

                encoder = vc2_cfg[0]
                thumbnail = encoder.get("thumbnail") or {}
                enable = thumbnail.get("enable", False)
                return jsonify({"ok": True, "enable": enable, "used_password": pwd_try})
            except Exception as e:
                last_error = str(e)
                continue

        # Offline/auth issues are expected in mixed network states; return non-500.
        return jsonify({"ok": False, "enable": False, "error": last_error}), 200
    except Exception as e:
        log.exception("get_thumbnail_status error")
        return jsonify({"ok": False, "enable": False, "error": str(e)}), 200

def main():
    host = os.getenv("OMNI_HOST", "127.0.0.1")

    def _select_bind_port(preferred_port: int, bind_host: str) -> int:
        candidates = [preferred_port]
        candidates.extend(p for p in range(8088, 8101) if p != preferred_port)
        for candidate in candidates:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind((bind_host, candidate))
                return candidate
            except OSError:
                continue
            finally:
                try:
                    s.close()
                except Exception:
                    pass
        # Last-resort fallback: ask OS for an ephemeral port.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((bind_host, 0))
            return int(s.getsockname()[1])
        finally:
            try:
                s.close()
            except Exception:
                pass

    selected_port = _select_bind_port(PORT, host)
    if selected_port != PORT:
        log.warning("Port %s is unavailable/blocked; using fallback port %s", PORT, selected_port)

    def run_server():
        app.run(host=host, port=selected_port, debug=True, use_reloader=False, threaded=True)
    th = threading.Thread(target=run_server, daemon=False); th.start()
    def open_when_ready():
        url = f"http://{host}:{selected_port}/"
        for _ in range(50):
            try:
                with urllib.request.urlopen(url+"__health", timeout=0.6) as r:
                    if 100 <= r.status < 600:
                        try:
                            os.startfile(url)  # type: ignore[attr-defined]
                        except Exception:
                            webbrowser.open(url)
                        return
            except Exception:
                time.sleep(0.2)
    threading.Thread(target=open_when_ready, daemon=True).start()
    while th.is_alive():
        th.join(timeout=0.5)

if __name__ == "__main__":
    main()
