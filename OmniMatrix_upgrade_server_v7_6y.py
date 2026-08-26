
# ...existing code...

# All imports below here
import os, sys, threading, urllib.request, webbrowser, logging, time, json, re, subprocess, socket, ssl, csv, tempfile, traceback, platform, io, zipfile
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
def _default_data_dir() -> Path:
    override = (os.getenv("OMNI_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "OmniSuite"
    if system == "windows":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OmniSuite"
    return Path.home() / ".omnisuite"


DATA_DIR = _default_data_dir()
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"[DATA_DIR] Failed to create {DATA_DIR}: {e}")
    DATA_DIR = SCRIPT_DIR
CWD = DATA_DIR
CACHE = CWD / "units_cache.json"
SCAN_RESULTS = CWD / "scan_results.json"
CSV_VIEW = CWD / "units_view.csv"
PORT = int(os.getenv("OMNI_PORT", "8080"))
log = logging.getLogger("omni_upgrade")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_cache_io_lock = threading.Lock()
_usb_route_lock = threading.RLock()


def _windows_hidden_subprocess_kwargs() -> dict:
    if platform.system().lower() != "windows":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }

def _app_version() -> str:
    env_version = (os.getenv("OMNI_VERSION") or "").strip()
    if env_version:
        return env_version
    for candidate in (ASSET_DIR / "VERSION", CWD / "VERSION"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:
            continue
    return "V0.0.0"

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
DATA_DIR = _default_data_dir()
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"[DATA_DIR] Failed to create {DATA_DIR}: {e}")
    DATA_DIR = SCRIPT_DIR
CWD = DATA_DIR
CACHE = CWD / "units_cache.json"
SCAN_RESULTS = CWD / "scan_results.json"
CSV_VIEW = CWD / "units_view.csv"
PORT = int(os.getenv("OMNI_PORT", "8080"))
log = logging.getLogger("omni_upgrade")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log.info("Runtime data directory: %s", CWD)

# Configure matrix logic once app config is loaded
def _configure_matrix_logic_from_app():
    if not HAS_MATRIX:
        return
    try:
        omni_matrix_logic._data_dir = CWD
        omni_matrix_logic._cache_file = CACHE
        omni_matrix_logic.configure(
            username=app.config.get('USERNAME', 'admin'),
            password=app.config.get('PASSWORD', 'Atlona'),
            ws_port=int(app.config.get('WS_PORT', 80)),
            ws_path=app.config.get('WS_PATH', '/wsapp/'),
            timeout=float(app.config.get('TIMEOUT', 4.0)),
        )
    except Exception as e:
        log.info("matrix_logic configure failed: %s", e)

def _infer_unit_role(unit: dict) -> str:
    role_text = " ".join(str((unit or {}).get(k) or "") for k in ("role", "type", "model")).strip().lower()
    if "encoder" in role_text or role_text == "enc" or "-e" in role_text:
        return "encoder"
    if "decoder" in role_text or role_text == "dec" or "-d" in role_text:
        return "decoder"
    if any((unit or {}).get(k) is not None for k in ("ip1_addr", "ip3_addr", "sap_input_enabled", "video_wall_enabled")):
        return "decoder"
    if any((unit or {}).get(k) is not None for k in ("v_mcast", "a_mcast", "session1_video_mcast", "session1_audio_mcast")):
        return "encoder"
    return ""

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

def _join_supported_versions(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v is not None)
    if value is None:
        return ""
    return str(value)

def _merge_unit_records(base: dict, extra: dict) -> dict:
    merged = dict(base or {})
    for key, value in (extra or {}).items():
        if value not in (None, "", []):
            merged[key] = value
    return merged

def _units_for_export():
    units = _load_cache()
    scan_data = _load_scan_results_file() or {}
    by_ip = {}
    for collection_name in ("devices", "encoders", "decoders"):
        for item in scan_data.get(collection_name) or []:
            ip = (item or {}).get("ip")
            if ip:
                by_ip[ip] = _merge_unit_records(by_ip.get(ip, {}), item)
    enriched = []
    seen = set()
    for unit in units:
        ip = (unit or {}).get("ip")
        enriched_unit = _merge_unit_records(unit, by_ip.get(ip, {}))
        enriched.append(enriched_unit)
        if ip:
            seen.add(ip)
    for ip, unit in by_ip.items():
        if ip not in seen:
            enriched.append(unit)
    return enriched

def _write_csv_atomic(units, target_path: Path, retries: int = 6, base_delay: float = 0.35) -> bool:
    header = [
        "IP","MAC","Hostname","Type","Model","Version","SerialNumber",
        "Role","Codec","LinkSpeed","NTP Server","TimeZone",
        "HDCP Support","HDCP Negotiated","HDCP Encrypted","HDCP Supported Versions",
        "Session 1 Name","Session 1 Video MC","Session 1 Video Port","Session 1 Audio MC","Session 1 Audio Port",
        "Session 2 Name","Session 2 Video MC","Session 2 Video Port","Session 2 Audio MC","Session 2 Audio Port",
        "Decoder ip_input1 MC","Decoder ip_input1 Port","Decoder ip_input3 MC","Decoder ip_input3 Port",
    ]
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
                        u.get("role",""),
                        u.get("codec",""),
                        u.get("linkspeed",""),
                        u.get("ntp_server",""),
                        u.get("active_timezone") or u.get("timezone",""),
                        u.get("hdcp_support_version",""),
                        u.get("hdcp_negotiated_version",""),
                        u.get("hdcp_encrypted",""),
                        _join_supported_versions(u.get("hdcp_supported_versions")),
                        u.get("session1_name",""),
                        u.get("session1_video_mcast") or u.get("v_mcast",""),
                        u.get("session1_video_port") or u.get("v_port",""),
                        u.get("session1_audio_mcast") or u.get("a_mcast",""),
                        u.get("session1_audio_port") or u.get("a_port",""),
                        u.get("session2_name",""),
                        u.get("session2_video_mcast",""),
                        u.get("session2_video_port",""),
                        u.get("session2_audio_mcast",""),
                        u.get("session2_audio_port",""),
                        u.get("ip1_addr",""),
                        u.get("ip1_port",""),
                        u.get("ip3_addr",""),
                        u.get("ip3_port",""),
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
    header = [
        "IP","MAC","Hostname","Type","Model","Version","SerialNumber",
        "Role","Codec","LinkSpeed","NTP Server","TimeZone",
        "HDCP Support","HDCP Negotiated","HDCP Encrypted","HDCP Supported Versions",
        "Session 1 Name","Session 1 Video MC","Session 1 Video Port","Session 1 Audio MC","Session 1 Audio Port",
        "Session 2 Name","Session 2 Video MC","Session 2 Video Port","Session 2 Audio MC","Session 2 Audio Port",
        "Decoder ip_input1 MC","Decoder ip_input1 Port","Decoder ip_input3 MC","Decoder ip_input3 Port",
    ]
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
                u.get("role",""),
                u.get("codec",""),
                u.get("linkspeed",""),
                u.get("ntp_server",""),
                u.get("active_timezone") or u.get("timezone",""),
                u.get("hdcp_support_version",""),
                u.get("hdcp_negotiated_version",""),
                u.get("hdcp_encrypted",""),
                _join_supported_versions(u.get("hdcp_supported_versions")),
                u.get("session1_name",""),
                u.get("session1_video_mcast") or u.get("v_mcast",""),
                u.get("session1_video_port") or u.get("v_port",""),
                u.get("session1_audio_mcast") or u.get("a_mcast",""),
                u.get("session1_audio_port") or u.get("a_port",""),
                u.get("session2_name",""),
                u.get("session2_video_mcast",""),
                u.get("session2_video_port",""),
                u.get("session2_audio_mcast",""),
                u.get("session2_audio_port",""),
                u.get("ip1_addr",""),
                u.get("ip1_port",""),
                u.get("ip3_addr",""),
                u.get("ip3_port",""),
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
        with _cache_io_lock:
            cache_units = []
            # Try legacy format first (units_cache.json) - for testing
            if CACHE.exists():
                with open(CACHE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, list) and d:
                    log.info(f"[CACHE] Loaded {len(d)} units from units_cache.json (list format)")
                    cache_units = d
                elif isinstance(d, dict) and "units" in d and d["units"]:
                    log.info(f"[CACHE] Loaded {len(d['units'])} units from units_cache.json (dict format)")
                    cache_units = d["units"]
            if cache_units:
                if SCAN_RESULTS.exists():
                    try:
                        with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                            scan_data = json.load(f)
                        scan_units = scan_data.get("devices", [])
                        if isinstance(scan_units, list) and scan_units:
                            by_ip = {u.get("ip"): dict(u) for u in scan_units if u.get("ip")}
                            for unit in cache_units:
                                ip = unit.get("ip")
                                if not ip:
                                    continue
                                merged = dict(by_ip.get(ip, {}))
                                merged.update(unit)
                                by_ip[ip] = merged
                            merged_units = list(by_ip.values())
                            if len(merged_units) > len(cache_units):
                                log.info("[CACHE] Merged %d cache units with scan_results to %d units", len(cache_units), len(merged_units))
                            return merged_units
                    except Exception as e:
                        log.info("[CACHE] scan_results merge skipped: %s", e)
                return cache_units
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
        with _cache_io_lock:
            tmp_path = CACHE.with_suffix(CACHE.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(units, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CACHE)
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

def _supports_decoder_fs_colorspace(model: str) -> bool:
    m = (model or "").strip().lower()
    return m in ("hw-omni-d4111", "at-omni-d4111", "hw-omni-d4511", "at-omni-d4511")

CODEC_LABELS = {
    "Colibri": "VCx",
    "VC2/LeGall": "VC-2 Video",
    "VC2/Haar": "VC-2 PC application",
}
CODEC_VALUES = set(CODEC_LABELS)

def _codec_label(system_mode: str) -> str:
    return CODEC_LABELS.get(system_mode or "", system_mode or "")

def _norm_usb_mac(mac: str) -> str:
    return re.sub(r"[^0-9A-F]", "", str(mac or "").upper())

def _unit_system_mode(unit: dict) -> str:
    unit = unit or {}
    si_cfg = (((unit.get("details") or {}).get("systeminfo") or {}).get("config") or {})
    return (unit.get("system_mode") or si_cfg.get("system_mode") or "").strip()

def _is_codec_configurable_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return bool(m) and not m.startswith("hw-omni")

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

def _first_present_string(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            return value
        return str(value)
    return None

def _input_option_name(item):
    if isinstance(item, dict):
        for key in ("name", "input", "value", "id"):
            value = item.get(key)
            if value is not None:
                return str(value)
        return None
    if item is None:
        return None
    return str(item)

def _dedupe_input_options(values):
    out = []
    seen = set()
    for item in values or []:
        name = _input_option_name(item)
        if name is None:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out

def _extract_available_inputs(*containers, fallback=(), current=None):
    options = ["notused"]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in (
            "available_inputs",
            "inputs",
            "input_options",
            "available_input",
            "supported_inputs",
            "supported_input",
            "available",
        ):
            value = container.get(key)
            if isinstance(value, list):
                options.extend(value)
    if not options:
        options.extend(fallback or [])
    deduped = _dedupe_input_options(options)
    if current and str(current).lower() not in {opt.lower() for opt in deduped}:
        deduped.insert(0, str(current))
    return deduped

def _ws_get_decoder_inputs(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, attempts: int = 5, delay: float = 0.5):
    """Fetch decoder matrix input and SAP input session fields.

    Try primary password first, then fallback password if primary fails.
    Returns dict with route fields and optional SAP/session fields, or {} on failure.
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
                enabled_ip_input_options = [
                    e.get("name")
                    for e in lst
                    if isinstance(e, dict) and e.get("name") and e.get("enabled")
                ]
                all_ip_input_options = [
                    e.get("name")
                    for e in lst
                    if isinstance(e, dict) and e.get("name")
                ]
                fields = {
                    "ip1_addr": ((ip1.get("multicast") or {}).get("address")),
                    "ip1_port": ip1.get("port"),
                    "ip3_addr": ((ip3.get("multicast") or {}).get("address")),
                    "ip3_port": ip3.get("port"),
                }

                # Best-effort HDMI output settings extraction from hdmi_output1.
                try:
                    hdmi_req = {"id":"hdmi_output-get","username":user,"password":attempt_pwd,"config_get":"hdmi_output"}
                    hdmi_resp = _ws_send_recv(url, hdmi_req, timeout=min(timeout, 2.0))
                    hdmi_cfg = (hdmi_resp or {}).get("config") or []
                    hdmi_list = hdmi_cfg if isinstance(hdmi_cfg, list) else (hdmi_cfg.get("hdmi_output") or [])
                    hdmi_output = next((entry for entry in hdmi_list if entry.get("name") == "hdmi_output1"), hdmi_list[0] if hdmi_list else {})
                    sap = (hdmi_output or {}).get("sap_input") or {}
                    output_cfg = (hdmi_output or {}).get("output") or {}
                    hdcp = (output_cfg.get("hdcp") or (hdmi_output or {}).get("hdcp") or {})
                    video = (hdmi_output or {}).get("video") or {}
                    video_backup = (video.get("backup") or {}) if isinstance(video, dict) else {}
                    video_output = (video.get("output") or {}) if isinstance(video, dict) else {}
                    fsm = (video_output.get("fsm") or {}) if isinstance(video_output, dict) else {}
                    audio = (hdmi_output or {}).get("audio") or {}
                    audio_backup = (audio.get("backup") or {}) if isinstance(audio, dict) else {}

                    fields["sap_input_enabled"] = sap.get("enabled")
                    fields["input_session"] = sap.get("session")

                    options = []
                    for key in ("sessions", "available_sessions", "session_options", "available"):
                        vals = sap.get(key)
                        if isinstance(vals, list):
                            for item in vals:
                                if isinstance(item, dict):
                                    name = item.get("name") or item.get("session") or item.get("value")
                                else:
                                    name = item
                                if name:
                                    options.append(str(name))
                    if fields.get("input_session"):
                        options.insert(0, str(fields.get("input_session")))
                    deduped = []
                    seen = set()
                    for opt in options:
                        if opt not in seen:
                            seen.add(opt)
                            deduped.append(opt)
                    fields["input_session_options"] = deduped
                    fields["hdcp_support_version"] = hdcp.get("support_version")
                    fields["hdcp_supported_versions"] = hdcp.get("supported_versions") or []

                    # Decoder control fields requested for matrix UI.
                    fields["video_input"] = _first_present_string(
                        video.get("input"),
                        video_backup.get("input"),
                        video_backup.get("active_input"),
                    )
                    fields["audio_input"] = _first_present_string(
                        audio.get("input"),
                        audio_backup.get("input"),
                        audio_backup.get("active_input"),
                    )
                    fields["stretch_crop_mode"] = video_output.get("aspect_ratio")
                    fields["resolution"] = video_output.get("resolution")
                    fr_obj = (video_output.get("framerate") or {}) if isinstance(video_output, dict) else {}
                    fr_mode = str(fr_obj.get("mode") or "").strip().lower()
                    fr_val = fr_obj.get("framerate")
                    if fr_mode == "auto":
                        fields["framerate"] = "auto"
                    elif isinstance(fr_val, (int, float)):
                        fields["framerate"] = f"{int(fr_val)} Hz"
                    else:
                        fields["framerate"] = None
                    fields["fast_switching_enabled"] = fsm.get("enabled")
                    fields["fast_switching_timeout"] = fsm.get("timeout")
                    fields["fast_switching_colorspace"] = fsm.get("colorspace")

                    wall = (video_output.get("wall") or {}) if isinstance(video_output, dict) else {}
                    input_selection = (wall.get("input_selection") or {}) if isinstance(wall, dict) else {}
                    physical_size = (wall.get("physical_size") or {}) if isinstance(wall, dict) else {}
                    edge_comp = (wall.get("edge_compensation") or {}) if isinstance(wall, dict) else {}
                    wall_unit = str(wall.get("unit") or "").strip().lower() if isinstance(wall, dict) else ""

                    def _coerce_num(v):
                        if isinstance(v, bool):
                            return None
                        if isinstance(v, (int, float)):
                            return float(v)
                        if isinstance(v, str):
                            s = v.strip()
                            if not s:
                                return None
                            try:
                                return float(s)
                            except Exception:
                                return None
                        return None

                    raw_grid_w = input_selection.get("width")
                    raw_grid_h = input_selection.get("height")
                    raw_grid_x = input_selection.get("x")
                    raw_grid_y = input_selection.get("y")
                    raw_total_w = physical_size.get("width")
                    raw_total_h = physical_size.get("height")

                    grid_w = _coerce_num(raw_grid_w)
                    grid_h = _coerce_num(raw_grid_h)
                    grid_x = _coerce_num(raw_grid_x)
                    grid_y = _coerce_num(raw_grid_y)
                    total_w = _coerce_num(raw_total_w)
                    total_h = _coerce_num(raw_total_h)

                    def _is_near_int(v, eps=1e-6):
                        return v is not None and abs(v - round(v)) <= eps

                    # Some units return raw grid coordinates, while others can surface
                    # decimal display values in input_selection. Normalize both forms.
                    looks_decimal_payload = any(
                        (v is not None and not _is_near_int(v))
                        for v in (grid_w, grid_h, grid_x, grid_y)
                    )

                    display_w = grid_w
                    display_h = grid_h
                    display_x = grid_x
                    display_y = grid_y

                    norm_grid_w = grid_w
                    norm_grid_h = grid_h
                    norm_grid_x = grid_x
                    norm_grid_y = grid_y

                    if grid_w is not None and grid_h is not None and grid_w > 0 and grid_h > 0 and (wall_unit == "pixels" or (total_w is not None and total_h is not None)):
                        if wall_unit == "pixels":
                            if looks_decimal_payload:
                                display_w = int(round(float(grid_w) * 1920))
                                display_h = int(round(float(grid_h) * 1080))
                                display_x = int(round(float(grid_x) / float(grid_w))) if grid_x is not None and grid_w else None
                                display_y = int(round(float(grid_y) / float(grid_h))) if grid_y is not None and grid_h else None
                            else:
                                display_w = int(round(float(grid_w)))
                                display_h = int(round(float(grid_h)))
                                display_x = int(round(float(grid_x))) if grid_x is not None else None
                                display_y = int(round(float(grid_y))) if grid_y is not None else None

                            if display_w and display_w > 0:
                                norm_grid_w = max(1, int(round(3840 / float(display_w))))
                            if display_h and display_h > 0:
                                norm_grid_h = max(1, int(round(2160 / float(display_h))))
                            if display_x is not None and display_w and display_w > 0:
                                norm_grid_x = max(0, int(round(float(display_x) / float(display_w))))
                            if display_y is not None and display_h and display_h > 0:
                                norm_grid_y = max(0, int(round(float(display_y) / float(display_h))))
                        elif wall_unit in ("inches", "mm"):
                            display_w = round(float(grid_w), 4)
                            display_h = round(float(grid_h), 4)
                            display_x = round(float(grid_x), 4) if grid_x is not None else None
                            display_y = round(float(grid_y), 4) if grid_y is not None else None

                            # Keep raw grid fields normalized for downstream set operations.
                            norm_base_w = total_w
                            norm_base_h = total_h
                            if norm_base_w and grid_w > 0:
                                norm_grid_w = max(1, int(round(float(norm_base_w) / float(display_w))))
                            if norm_base_h and grid_h > 0:
                                norm_grid_h = max(1, int(round(float(norm_base_h) / float(display_h))))
                            if display_x is not None and display_w and display_w > 0:
                                norm_grid_x = max(0, int(round(float(display_x) / float(display_w))))
                            if display_y is not None and display_h and display_h > 0:
                                norm_grid_y = max(0, int(round(float(display_y) / float(display_h))))
                        else:
                            unit_w = round(float(total_w) / float(grid_w), 4)
                            unit_h = round(float(total_h) / float(grid_h), 4)
                            display_w = unit_w
                            display_h = unit_h
                            if grid_x is not None:
                                display_x = round(float(unit_w) * float(grid_x), 4)
                            if grid_y is not None:
                                display_y = round(float(unit_h) * float(grid_y), 4)

                    if norm_grid_w is not None:
                        norm_grid_w = max(1, int(round(norm_grid_w)))
                    if norm_grid_h is not None:
                        norm_grid_h = max(1, int(round(norm_grid_h)))
                    if norm_grid_x is not None:
                        norm_grid_x = max(0, int(round(norm_grid_x)))
                    if norm_grid_y is not None:
                        norm_grid_y = max(0, int(round(norm_grid_y)))
                    if norm_grid_w is not None and norm_grid_x is not None:
                        norm_grid_x = min(norm_grid_w - 1, norm_grid_x)
                    if norm_grid_h is not None and norm_grid_y is not None:
                        norm_grid_y = min(norm_grid_h - 1, norm_grid_y)

                    fields["video_wall_enabled"] = wall.get("enabled")
                    fields["video_wall_unit"] = wall.get("unit")
                    fields["video_wall_width"] = display_w
                    fields["video_wall_height"] = display_h
                    fields["video_wall_horizontal"] = display_x
                    fields["video_wall_vertical"] = display_y
                    fields["video_wall_rotation"] = wall.get("rotation")
                    fields["video_wall_edge_mode"] = edge_comp.get("mode")
                    fields["video_wall_edge_top"] = edge_comp.get("top")
                    fields["video_wall_edge_bottom"] = edge_comp.get("bottom")
                    fields["video_wall_edge_left"] = edge_comp.get("left")
                    fields["video_wall_edge_right"] = edge_comp.get("right")
                    fields["video_wall_total_width"] = total_w if total_w is not None else raw_total_w
                    fields["video_wall_total_height"] = total_h if total_h is not None else raw_total_h
                    fields["video_wall_grid_width"] = norm_grid_w if norm_grid_w is not None else (grid_w if grid_w is not None else raw_grid_w)
                    fields["video_wall_grid_height"] = norm_grid_h if norm_grid_h is not None else (grid_h if grid_h is not None else raw_grid_h)
                    fields["video_wall_grid_x"] = norm_grid_x if norm_grid_x is not None else (grid_x if grid_x is not None else raw_grid_x)
                    fields["video_wall_grid_y"] = norm_grid_y if norm_grid_y is not None else (grid_y if grid_y is not None else raw_grid_y)

                    ip_input_fallback = enabled_ip_input_options or all_ip_input_options
                    fields["video_input_options"] = _extract_available_inputs(
                        video,
                        video_backup,
                        fallback=ip_input_fallback,
                        current=fields.get("video_input"),
                    )
                    fields["audio_input_options"] = _extract_available_inputs(
                        audio,
                        audio_backup,
                        fallback=ip_input_fallback,
                        current=fields.get("audio_input"),
                    )
                    fields["stretch_crop_mode_options"] = ["keep aspect ratio", "fullscreen", "16:9", "16:10", "4:3"]
                    resolution_options = [
                        "auto", "4096x2160", "3840x2160", "1920x1200", "1920x1080", "1680x1050",
                        "1600x900", "1400x1050", "1440x900", "1280x1024", "1280x800", "1280x768", "1280x720", "1024x768"
                    ]
                    if not fields.get("fast_switching_enabled") and not fields.get("video_wall_enabled"):
                        resolution_options.insert(0, "input")
                    fields["resolution_options"] = resolution_options
                    fields["framerate_options"] = ["auto", "60 Hz", "50 Hz", "30 Hz"]
                    fields["fast_switching_colorspace_options"] = ["RGB", "YUV"]

                    unit_options = ["pixels", "inches", "mm"]
                    current_unit = fields.get("video_wall_unit")
                    if current_unit and current_unit not in unit_options:
                        unit_options.insert(0, current_unit)
                    fields["video_wall_unit_options"] = list(dict.fromkeys(unit_options))

                    rotation_options = [0, 90, 180, 270]
                    current_rotation = fields.get("video_wall_rotation")
                    if isinstance(current_rotation, int) and current_rotation not in rotation_options:
                        rotation_options.insert(0, current_rotation)
                    fields["video_wall_rotation_options"] = rotation_options

                    edge_mode_options = ["none", "bezel compensation"]
                    current_edge_mode = fields.get("video_wall_edge_mode")
                    if current_edge_mode and current_edge_mode not in edge_mode_options:
                        edge_mode_options.insert(0, current_edge_mode)
                    dedup_edge_modes = []
                    seen_modes = set()
                    for mode_opt in edge_mode_options:
                        mode_key = str(mode_opt)
                        if mode_key in seen_modes:
                            continue
                        seen_modes.add(mode_key)
                        dedup_edge_modes.append(mode_key)
                    fields["video_wall_edge_mode_options"] = dedup_edge_modes
                except Exception:
                    # Keep route fields even if HDMI output settings extraction is unavailable.
                    pass

                return fields
            except Exception:
                if i < attempts-1:
                    time.sleep(delay)
                    continue
                # This attempt failed, try next password
                break
    
    # All passwords and retries exhausted
    return {}

def _ws_get_encoder_input_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, attempts: int = 3, delay: float = 0.3):
    """Fetch encoder hdmi_input and edid list via WebSocket config_get with retry and fallback password."""
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    for attempt_pwd in passwords_to_try:
        for i in range(max(1, attempts)):
            try:
                url = _ws_url(ip, ws_port, ws_path)
                hdmi_req = {"id":"hdmi_input-get","username":user,"password":attempt_pwd,"config_get":"hdmi_input"}
                edid_req = {"id":"edid-get","username":user,"password":attempt_pwd,"config_get":"edid"}

                hdmi_resp = _ws_send_recv(url, hdmi_req, timeout=min(timeout, 2.5))
                edid_resp = _ws_send_recv(url, edid_req, timeout=min(timeout, 2.5))
                if not hdmi_resp or hdmi_resp.get("error"):
                    raise ValueError("empty hdmi_input resp or error")

                hdmi_cfg = (hdmi_resp or {}).get("config") or []
                hdmi_list = hdmi_cfg if isinstance(hdmi_cfg, list) else (hdmi_cfg.get("hdmi_input") or [])
                hdmi_input = next((entry for entry in hdmi_list if entry.get("name") == "hdmi_input1"), hdmi_list[0] if hdmi_list else {})

                edid_cfg = (edid_resp or {}).get("config") or []
                edid_list = edid_cfg if isinstance(edid_cfg, list) else (edid_cfg.get("edid") or [])

                hdcp = hdmi_input.get("hdcp") or {}
                cable_present = hdmi_input.get("cabledetect")
                if cable_present is None:
                    active_name = hdmi_input.get("active_input")
                    for status in (hdmi_input.get("input_status") or []):
                        if status.get("name") == active_name and status.get("cabledetect") is not None:
                            cable_present = status.get("cabledetect")
                            break
                return {
                    "input_auto_switch": hdmi_input.get("input_auto_switch"),
                    "active_input": hdmi_input.get("active_input"),
                    "input_status": hdmi_input.get("input_status") or [],
                    "cable_present": cable_present,
                    "edid": hdmi_input.get("edid"),
                    "edid_options": [item.get("name") for item in edid_list if item.get("name")],
                    "hdcp_encrypted": hdcp.get("encrypted"),
                    "hdcp_negotiated_version": hdcp.get("negotiated_version"),
                    "hdcp_support_version": hdcp.get("support_version"),
                    "hdcp_supported_versions": hdcp.get("supported_versions") or [],
                }
            except Exception:
                if i < attempts - 1:
                    time.sleep(delay)
                    continue
                break

    return {}

def _ws_set_encoder_input_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, *, input_auto_switch=None, active_input=None, edid=None, hdcp_support_version=None):
    """Set encoder hdmi_input1 settings and return fresh polled values."""
    if input_auto_switch is None and active_input is None and edid is None and hdcp_support_version is None:
        return {"ok": False, "error": "no settings provided"}

    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "set failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            current = _ws_send_recv(url, {
                "id": "hdmi_input-get",
                "username": user,
                "password": attempt_pwd,
                "config_get": "hdmi_input"
            }, timeout=min(timeout, 2.5))
            if not current or current.get("error"):
                raise ValueError((current or {}).get("error") or "failed to fetch current hdmi_input")

            current_cfg = (current or {}).get("config") or []
            current_list = current_cfg if isinstance(current_cfg, list) else (current_cfg.get("hdmi_input") or [])
            current_input = next((entry for entry in current_list if entry.get("name") == "hdmi_input1"), current_list[0] if current_list else None)
            if not current_input:
                raise ValueError("hdmi_input1 not found")

            payload_cfg = {"name": current_input.get("name") or "hdmi_input1"}
            if input_auto_switch is not None:
                payload_cfg["input_auto_switch"] = bool(input_auto_switch)
            if active_input is not None:
                payload_cfg["active_input"] = active_input
            if edid is not None:
                payload_cfg["edid"] = edid
            if hdcp_support_version is not None:
                payload_cfg["hdcp"] = {"support_version": hdcp_support_version}

            set_resp = _ws_send_recv(url, {
                "id": "hdmi_input-set",
                "username": user,
                "password": attempt_pwd,
                "config_set": {
                    "name": "hdmi_input",
                    "config": [payload_cfg]
                }
            }, timeout=max(timeout, 4.0))
            if set_resp and set_resp.get("error"):
                raise ValueError(set_resp.get("error"))

            fields = _ws_get_encoder_input_settings(ip, user, attempt_pwd, ws_port, ws_path, timeout=max(timeout, 2.5), attempts=1, delay=0)
            if not fields:
                raise ValueError("failed to verify updated settings")
            return {"ok": True, "fields": fields}
        except Exception as e:
            last_error = str(e)

    return {"ok": False, "error": last_error}

def _ws_get_encoder_output_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, attempts: int = 3, delay: float = 0.3):
    """Fetch encoder output sessions used by the device Output page."""
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    for attempt_pwd in passwords_to_try:
        for i in range(max(1, attempts)):
            try:
                url = _ws_url(ip, ws_port, ws_path)
                resp = _ws_send_recv(url, {
                    "id": "sessions-get",
                    "username": user,
                    "password": attempt_pwd,
                    "config_get": "sessions",
                }, timeout=min(timeout, 4.0))
                if not resp or resp.get("error"):
                    raise ValueError((resp or {}).get("error_message") or (resp or {}).get("error") or "sessions-get failed")
                cfg = resp.get("config") or []
                sessions = cfg if isinstance(cfg, list) else (cfg.get("sessions") or [])
                if not isinstance(sessions, list):
                    raise ValueError("invalid sessions response")
                return {"sessions": sessions}
            except Exception:
                if i < attempts - 1:
                    time.sleep(delay)
                    continue
                break
    return {}

def _encoder_session_matrix_fields(sessions):
    fields = {}
    if not isinstance(sessions, list):
        return fields
    session1 = next((s for s in sessions if (s.get("name") or "").lower() == "session1"), sessions[0] if sessions else None)
    if session1:
        video_stream = ((session1.get("video") or {}).get("stream") or {})
        audio_stream = ((session1.get("audio") or {}).get("stream") or {})
        fields.update({
            "v_mcast": video_stream.get("destination_address"),
            "v_port": video_stream.get("destination_port"),
            "a_mcast": audio_stream.get("destination_address"),
            "a_port": audio_stream.get("destination_port"),
        })
    for idx, session in enumerate(sessions[:2], start=1):
        video_stream = ((session.get("video") or {}).get("stream") or {})
        audio_stream = ((session.get("audio") or {}).get("stream") or {})
        fields.update({
            f"session{idx}_name": session.get("name") or f"session{idx}",
            f"session{idx}_video_mcast": video_stream.get("destination_address"),
            f"session{idx}_video_port": video_stream.get("destination_port"),
            f"session{idx}_audio_mcast": audio_stream.get("destination_address"),
            f"session{idx}_audio_port": audio_stream.get("destination_port"),
        })
    return {k: v for k, v in fields.items() if v is not None}

def _ws_set_encoder_output_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, sessions):
    """Set encoder output sessions and return fresh sessions."""
    if not isinstance(sessions, list):
        return {"ok": False, "error": "sessions array required"}

    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "sessions-set failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            current = _ws_send_recv(url, {
                "id": "sessions-get",
                "username": user,
                "password": attempt_pwd,
                "config_get": "sessions",
            }, timeout=min(timeout, 4.0))
            if not current or current.get("error"):
                raise ValueError((current or {}).get("error_message") or (current or {}).get("error") or "sessions-get failed")

            current_cfg = current.get("config") or []
            current_sessions = current_cfg if isinstance(current_cfg, list) else (current_cfg.get("sessions") or [])
            if not isinstance(current_sessions, list):
                current_sessions = []

            incoming_by_name = {s.get("name"): s for s in sessions if isinstance(s, dict) and s.get("name")}
            merged_sessions = []
            for existing in current_sessions:
                if not isinstance(existing, dict):
                    continue
                name = existing.get("name")
                incoming = incoming_by_name.get(name)
                merged_sessions.append(json.loads(json.dumps(incoming if incoming is not None else existing)))

            existing_names = {s.get("name") for s in merged_sessions if isinstance(s, dict)}
            for incoming in sessions:
                if isinstance(incoming, dict) and incoming.get("name") not in existing_names:
                    merged_sessions.append(json.loads(json.dumps(incoming)))

            set_resp = _ws_send_recv(url, {
                "id": "sessions-set",
                "username": user,
                "password": attempt_pwd,
                "config_set": {
                    "name": "sessions",
                    "config": merged_sessions,
                },
            }, timeout=max(timeout, 6.0))
            if set_resp and set_resp.get("error"):
                raise ValueError(set_resp.get("error_message") or set_resp.get("error") or "sessions-set failed")

            fields = _ws_get_encoder_output_settings(ip, user, attempt_pwd, ws_port, ws_path, timeout=max(timeout, 4.0), attempts=1, delay=0)
            if not fields:
                raise ValueError("failed to verify updated sessions")
            return {"ok": True, **fields}
        except Exception as e:
            last_error = str(e)

    return {"ok": False, "error": last_error}

def _ws_get_encoder_encoding_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, attempts: int = 3, delay: float = 0.3):
    """Fetch encoder Encoding page VC2 properties."""
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    for attempt_pwd in passwords_to_try:
        for i in range(max(1, attempts)):
            try:
                url = _ws_url(ip, ws_port, ws_path)
                vc2_resp = _ws_send_recv(url, {
                    "id": "vc2-get",
                    "username": user,
                    "password": attempt_pwd,
                    "config_get": "vc2",
                }, timeout=min(timeout, 4.0))
                if not vc2_resp or vc2_resp.get("error"):
                    raise ValueError((vc2_resp or {}).get("error_message") or (vc2_resp or {}).get("error") or "vc2-get failed")

                vc2_cfg = vc2_resp.get("config") or []
                encoders = vc2_cfg if isinstance(vc2_cfg, list) else (vc2_cfg.get("vc2") or [])
                if not isinstance(encoders, list):
                    raise ValueError("invalid vc2 response")

                input_options = []
                try:
                    hdmi_resp = _ws_send_recv(url, {
                        "id": "hdmi_input-get",
                        "username": user,
                        "password": attempt_pwd,
                        "config_get": "hdmi_input",
                    }, timeout=min(timeout, 3.0))
                    hdmi_cfg = (hdmi_resp or {}).get("config") or []
                    hdmi_list = hdmi_cfg if isinstance(hdmi_cfg, list) else (hdmi_cfg.get("hdmi_input") or [])
                    input_options = [entry.get("name") for entry in hdmi_list if isinstance(entry, dict) and entry.get("name")]
                except Exception:
                    input_options = []

                return {"encoders": encoders, "input_options": input_options}
            except Exception:
                if i < attempts - 1:
                    time.sleep(delay)
                    continue
                break
    return {}

def _ws_set_encoder_encoding_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, encoders):
    """Set encoder Encoding page VC2 properties and return fresh VC2 config."""
    if not isinstance(encoders, list):
        return {"ok": False, "error": "encoders array required"}

    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "vc2-set failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            current = _ws_send_recv(url, {
                "id": "vc2-get",
                "username": user,
                "password": attempt_pwd,
                "config_get": "vc2",
            }, timeout=min(timeout, 4.0))
            if not current or current.get("error"):
                raise ValueError((current or {}).get("error_message") or (current or {}).get("error") or "vc2-get failed")

            current_cfg = current.get("config") or []
            current_encoders = current_cfg if isinstance(current_cfg, list) else (current_cfg.get("vc2") or [])
            if not isinstance(current_encoders, list):
                current_encoders = []

            incoming_by_name = {e.get("name"): e for e in encoders if isinstance(e, dict) and e.get("name")}
            merged_encoders = []
            for existing in current_encoders:
                if not isinstance(existing, dict):
                    continue
                name = existing.get("name")
                incoming = incoming_by_name.get(name)
                merged_encoders.append(json.loads(json.dumps(incoming if incoming is not None else existing)))

            existing_names = {e.get("name") for e in merged_encoders if isinstance(e, dict)}
            for incoming in encoders:
                if isinstance(incoming, dict) and incoming.get("name") not in existing_names:
                    merged_encoders.append(json.loads(json.dumps(incoming)))

            set_resp = _ws_send_recv(url, {
                "id": "vc2-set",
                "username": user,
                "password": attempt_pwd,
                "config_set": {
                    "name": "vc2",
                    "config": merged_encoders,
                },
            }, timeout=max(timeout, 6.0))
            if set_resp and set_resp.get("error"):
                raise ValueError(set_resp.get("error_message") or set_resp.get("error") or "vc2-set failed")

            fields = _ws_get_encoder_encoding_settings(ip, user, attempt_pwd, ws_port, ws_path, timeout=max(timeout, 4.0), attempts=1, delay=0)
            if not fields:
                raise ValueError("failed to verify updated encoding settings")
            return {"ok": True, **fields}
        except Exception as e:
            last_error = str(e)

    return {"ok": False, "error": last_error}

def _ws_get_logo_library(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float):
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "logo_library-get failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            resp = _ws_send_recv(url, {
                "id": "logo_library-get",
                "username": user,
                "password": attempt_pwd,
                "config_get": "logo_library",
            }, timeout=min(timeout, 4.0))
            if not resp or resp.get("error"):
                raise ValueError((resp or {}).get("error_message") or (resp or {}).get("error") or "logo_library-get failed")
            cfg = resp.get("config") or []
            logos = cfg if isinstance(cfg, list) else (cfg.get("logo_library") or [])
            if not isinstance(logos, list):
                logos = []
            return {"ok": True, "logos": logos, "password": attempt_pwd}
        except Exception as e:
            last_error = str(e)
    return {"ok": False, "error": last_error, "logos": []}

def _slate_logo_options(logos, current_logo=None):
    names = ["Not used"]
    for logo in logos or []:
        name = logo.get("name") if isinstance(logo, dict) else str(logo or "")
        if name and name not in names:
            names.append(name)
    if current_logo and current_logo not in names:
        names.append(current_logo)
    return names

def _ws_get_decoder_slate_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float):
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "hdmi_output-get failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            resp = _ws_send_recv(url, {
                "id": "hdmi_output-get",
                "username": user,
                "password": attempt_pwd,
                "config_get": "hdmi_output",
            }, timeout=min(timeout, 4.0))
            if not resp or resp.get("error"):
                raise ValueError((resp or {}).get("error_message") or (resp or {}).get("error") or "hdmi_output-get failed")
            cfg = resp.get("config") or []
            outputs = cfg if isinstance(cfg, list) else (cfg.get("hdmi_output") or [])
            output = next((o for o in outputs if isinstance(o, dict) and o.get("name") == "hdmi_output1"), outputs[0] if outputs else {})
            slate = (((output or {}).get("video") or {}).get("generator") or {}).get("slate") or {}
            logo = slate.get("logo") or ""
            return {"ok": True, "mode": slate.get("mode") or "off", "logo": logo or "Not used", "raw_logo": logo, "password": attempt_pwd}
        except Exception as e:
            last_error = str(e)
    return {"ok": False, "error": last_error}

def _ws_set_decoder_slate_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, mode: str, logo: str):
    logo = "" if logo == "Not used" else (logo or "")
    mode = "off" if not logo else (mode or "auto")
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "hdmi_output-set failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            current = _ws_send_recv(url, {
                "id": "hdmi_output-get",
                "username": user,
                "password": attempt_pwd,
                "config_get": "hdmi_output",
            }, timeout=min(timeout, 4.0))
            if not current or current.get("error"):
                raise ValueError((current or {}).get("error_message") or (current or {}).get("error") or "hdmi_output-get failed")

            current_cfg = current.get("config") or []
            outputs = current_cfg if isinstance(current_cfg, list) else (current_cfg.get("hdmi_output") or [])
            current_output = next((o for o in outputs if isinstance(o, dict) and o.get("name") == "hdmi_output1"), outputs[0] if outputs else None)
            if not current_output:
                raise ValueError("hdmi_output1 not found")

            payload_cfg = json.loads(json.dumps(current_output))
            video_cfg = payload_cfg.setdefault("video", {})
            generator_cfg = video_cfg.setdefault("generator", {})
            slate_cfg = generator_cfg.setdefault("slate", {})
            slate_cfg["mode"] = mode
            slate_cfg["logo"] = logo

            set_resp = _ws_send_recv(url, {
                "id": "hdmi_output-set",
                "username": user,
                "password": attempt_pwd,
                "config_set": {
                    "name": "hdmi_output",
                    "config": [payload_cfg],
                },
            }, timeout=max(timeout, 5.0))
            if set_resp and set_resp.get("error"):
                raise ValueError(set_resp.get("error_message") or set_resp.get("error") or "hdmi_output-set failed")
            return _ws_get_decoder_slate_settings(ip, user, attempt_pwd, ws_port, ws_path, timeout)
        except Exception as e:
            last_error = str(e)
    return {"ok": False, "error": last_error}

def _ws_set_encoder_slate_settings(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, mode: str, logo: str):
    fields = _ws_get_encoder_encoding_settings(ip, user, pwd, ws_port, ws_path, timeout=timeout, attempts=1, delay=0)
    if not fields:
        return {"ok": False, "error": "failed to fetch encoder encoding settings"}
    logo = "" if logo == "Not used" else (logo or "")
    mode = "off" if not logo else (mode or "auto")
    encoders = json.loads(json.dumps(fields.get("encoders") or []))
    for encoder in encoders:
        if not isinstance(encoder, dict):
            continue
        slate = encoder.setdefault("slate", {})
        slate["mode"] = mode
        slate["logo"] = logo
    return _ws_set_encoder_encoding_settings(ip, user, pwd, ws_port, ws_path, timeout, encoders)

def _upload_urls(ip: str):
    http_urls = [f"http://{ip}/upload/", f"http://{ip}/upload"]
    https_urls = [f"https://{ip}/upload/", f"https://{ip}/upload"]
    if app.config.get('WS_PORT') in (443, 8443):
        return https_urls + http_urls
    return http_urls + https_urls

def _http_upload_logo(ip: str, file_path: Path, timeout: float = 60.0):
    urls = _upload_urls(ip)
    last_err = None
    for url in urls:
        try:
            with open(file_path, "rb") as fh:
                files = {"Upgrade file": (file_path.name, fh, "application/octet-stream")}
                if url.startswith("https://"):
                    r = requests.post(url, files=files, timeout=timeout, verify=False)
                else:
                    r = requests.post(url, files=files, timeout=timeout)
            if 200 <= r.status_code < 300:
                uploaded = (r.text or "").strip().strip('"')
                return {"ok": True, "url": url, "uploaded": uploaded, "status": r.status_code}
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
    return {"ok": False, "error": last_err or "upload failed"}

def _ws_add_logo(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, uploaded_file: str, logo_name: str):
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "add_logo failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            resp = _ws_send_recv(url, {
                "id": "add_logo-method",
                "username": user,
                "password": attempt_pwd,
                "method": {
                    "add_logo": {
                        "file": uploaded_file,
                        "name": logo_name,
                    }
                }
            }, timeout=max(timeout, 8.0))
            if resp and resp.get("error"):
                raise ValueError(resp.get("error_message") or resp.get("error") or "add_logo failed")
            return {"ok": True, "password": attempt_pwd, "response": resp}
        except Exception as e:
            last_error = str(e)
    return {"ok": False, "error": last_error}

def _ws_delete_logo(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, logo_name: str):
    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "delete_logo failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            resp = _ws_send_recv(url, {
                "id": "delete_logo-method",
                "username": user,
                "password": attempt_pwd,
                "method": {
                    "delete_logo": {
                        "name": logo_name,
                    }
                }
            }, timeout=max(timeout, 8.0))
            if resp and resp.get("error"):
                raise ValueError(resp.get("error_message") or resp.get("error") or "delete_logo failed")
            return {"ok": True, "password": attempt_pwd, "response": resp}
        except Exception as e:
            last_error = str(e)
    return {"ok": False, "error": last_error}

def _ws_set_decoder_input_settings(
    ip: str,
    user: str,
    pwd: str,
    ws_port: int,
    ws_path: str,
    timeout: float,
    *,
    sap_input_enabled=None,
    input_session=None,
    video_input=None,
    audio_input=None,
    stretch_crop_mode=None,
    resolution=None,
    framerate=None,
    fast_switching_enabled=None,
    fast_switching_timeout=None,
    fast_switching_colorspace=None,
    hdcp_support_version=None,
    video_wall_enabled=None,
    video_wall_unit=None,
    video_wall_total_width=None,
    video_wall_total_height=None,
    video_wall_width=None,
    video_wall_height=None,
    video_wall_horizontal=None,
    video_wall_vertical=None,
    video_wall_grid_width=None,
    video_wall_grid_height=None,
    video_wall_grid_x=None,
    video_wall_grid_y=None,
    video_wall_rotation=None,
    video_wall_edge_mode=None,
    video_wall_edge_top=None,
    video_wall_edge_bottom=None,
    video_wall_edge_left=None,
    video_wall_edge_right=None,
):
    """Set decoder hdmi_output1 sap_input fields and return fresh polled values."""
    if all(v is None for v in (
        sap_input_enabled,
        input_session,
        video_input,
        audio_input,
        stretch_crop_mode,
        resolution,
        framerate,
        fast_switching_enabled,
        fast_switching_timeout,
        fast_switching_colorspace,
        hdcp_support_version,
        video_wall_enabled,
        video_wall_unit,
        video_wall_total_width,
        video_wall_total_height,
        video_wall_width,
        video_wall_height,
        video_wall_horizontal,
        video_wall_vertical,
        video_wall_grid_width,
        video_wall_grid_height,
        video_wall_grid_x,
        video_wall_grid_y,
        video_wall_rotation,
        video_wall_edge_mode,
        video_wall_edge_top,
        video_wall_edge_bottom,
        video_wall_edge_left,
        video_wall_edge_right,
    )):
        return {"ok": False, "error": "no settings provided"}

    fallback_pwd = app.config['FALLBACK_PASSWORD']
    passwords_to_try = [pwd]
    if fallback_pwd != pwd:
        passwords_to_try.append(fallback_pwd)

    last_error = "set failed"
    for attempt_pwd in passwords_to_try:
        try:
            url = _ws_url(ip, ws_port, ws_path)
            requested_fields = {}
            for key, value in (
                ("sap_input_enabled", sap_input_enabled),
                ("input_session", input_session),
                ("video_input", video_input),
                ("audio_input", audio_input),
                ("stretch_crop_mode", stretch_crop_mode),
                ("resolution", resolution),
                ("framerate", framerate),
                ("fast_switching_enabled", fast_switching_enabled),
                ("fast_switching_timeout", fast_switching_timeout),
                ("fast_switching_colorspace", fast_switching_colorspace),
                ("hdcp_support_version", hdcp_support_version),
                ("video_wall_enabled", video_wall_enabled),
                ("video_wall_unit", video_wall_unit),
                ("video_wall_total_width", video_wall_total_width),
                ("video_wall_total_height", video_wall_total_height),
                ("video_wall_width", video_wall_width),
                ("video_wall_height", video_wall_height),
                ("video_wall_horizontal", video_wall_horizontal),
                ("video_wall_vertical", video_wall_vertical),
                ("video_wall_grid_width", video_wall_grid_width),
                ("video_wall_grid_height", video_wall_grid_height),
                ("video_wall_grid_x", video_wall_grid_x),
                ("video_wall_grid_y", video_wall_grid_y),
                ("video_wall_rotation", video_wall_rotation),
                ("video_wall_edge_mode", video_wall_edge_mode),
                ("video_wall_edge_top", video_wall_edge_top),
                ("video_wall_edge_bottom", video_wall_edge_bottom),
                ("video_wall_edge_left", video_wall_edge_left),
                ("video_wall_edge_right", video_wall_edge_right),
            ):
                if value is not None:
                    requested_fields[key] = value
            current = _ws_send_recv(url, {
                "id": "hdmi_output-get",
                "username": user,
                "password": attempt_pwd,
                "config_get": "hdmi_output"
            }, timeout=min(timeout, 2.5))
            if not current or current.get("error"):
                raise ValueError((current or {}).get("error") or "failed to fetch current hdmi_output")

            current_cfg = (current or {}).get("config") or []
            current_list = current_cfg if isinstance(current_cfg, list) else (current_cfg.get("hdmi_output") or [])
            current_output = next((entry for entry in current_list if entry.get("name") == "hdmi_output1"), current_list[0] if current_list else None)
            if not current_output:
                raise ValueError("hdmi_output1 not found")

            # Update a deep copy of current config to avoid unintentionally dropping sibling keys.
            payload_cfg = json.loads(json.dumps(current_output)) if current_output else {}
            if not isinstance(payload_cfg, dict):
                payload_cfg = {}
            payload_cfg["name"] = current_output.get("name") or "hdmi_output1"

            sap_payload = dict((payload_cfg.get("sap_input") or {}))
            if sap_input_enabled is not None:
                sap_payload["enabled"] = bool(sap_input_enabled)
            if input_session is not None:
                sap_payload["session"] = input_session
            payload_cfg["sap_input"] = sap_payload

            video_cfg = payload_cfg.get("video") or {}
            if not isinstance(video_cfg, dict):
                video_cfg = {}
            video_backup_cfg = video_cfg.get("backup") or {}
            if not isinstance(video_backup_cfg, dict):
                video_backup_cfg = {}
            if video_input is not None:
                video_cfg["input"] = video_input
            video_cfg["backup"] = video_backup_cfg

            video_output_cfg = video_cfg.get("output") or {}
            if not isinstance(video_output_cfg, dict):
                video_output_cfg = {}
            if stretch_crop_mode is not None:
                video_output_cfg["aspect_ratio"] = stretch_crop_mode
            fsm_cfg = video_output_cfg.get("fsm") or {}
            if not isinstance(fsm_cfg, dict):
                fsm_cfg = {}
            wall_cfg = video_output_cfg.get("wall") or {}
            if not isinstance(wall_cfg, dict):
                wall_cfg = {}
            if resolution is not None:
                resolution_value = str(resolution).strip()
                if resolution_value.lower() == "input":
                    effective_fsm_enabled = bool(fsm_cfg.get("enabled"))
                    if fast_switching_enabled is not None:
                        effective_fsm_enabled = bool(fast_switching_enabled)
                    if effective_fsm_enabled:
                        return {
                            "ok": False,
                            "error": "Resolution 'input' requires Fast Switching to be disabled",
                            "status_code": 400,
                        }
                    effective_wall_enabled = bool(wall_cfg.get("enabled"))
                    if video_wall_enabled is not None:
                        effective_wall_enabled = bool(video_wall_enabled)
                    if effective_wall_enabled:
                        return {
                            "ok": False,
                            "error": "Resolution 'input' requires Video Wall to be disabled",
                            "status_code": 400,
                        }
                video_output_cfg["resolution"] = resolution_value
            if framerate is not None:
                fr_value = str(framerate).strip().lower()
                fr_cfg = video_output_cfg.get("framerate") or {}
                if not isinstance(fr_cfg, dict):
                    fr_cfg = {}
                if fr_value == "auto":
                    fr_cfg["mode"] = "auto"
                else:
                    fr_num = None
                    for token in str(framerate).replace("hz", "").replace("Hz", "").split():
                        try:
                            fr_num = int(float(token))
                            break
                        except Exception:
                            continue
                    if fr_num is not None:
                        fr_cfg["mode"] = "fixed"
                        fr_cfg["framerate"] = fr_num
                video_output_cfg["framerate"] = fr_cfg

            if fast_switching_enabled is not None:
                fsm_cfg["enabled"] = bool(fast_switching_enabled)
            if fast_switching_timeout is not None:
                try:
                    fsm_cfg["timeout"] = int(fast_switching_timeout)
                except Exception:
                    pass
            if fast_switching_colorspace is not None:
                fsm_cfg["colorspace"] = fast_switching_colorspace
            video_output_cfg["fsm"] = fsm_cfg

            if video_wall_enabled is not None:
                wall_cfg["enabled"] = bool(video_wall_enabled)
            if video_wall_unit is not None:
                wall_cfg["unit"] = str(video_wall_unit)
            if video_wall_rotation is not None:
                try:
                    wall_cfg["rotation"] = int(video_wall_rotation)
                except Exception:
                    pass

            physical_size_cfg = wall_cfg.get("physical_size") or {}
            if not isinstance(physical_size_cfg, dict):
                physical_size_cfg = {}
            if video_wall_total_width is not None:
                try:
                    physical_size_cfg["width"] = float(video_wall_total_width)
                except Exception:
                    pass
            if video_wall_total_height is not None:
                try:
                    physical_size_cfg["height"] = float(video_wall_total_height)
                except Exception:
                    pass
            wall_cfg["physical_size"] = physical_size_cfg

            input_selection_cfg = wall_cfg.get("input_selection") or {}
            if not isinstance(input_selection_cfg, dict):
                input_selection_cfg = {}

            def _to_float(v):
                try:
                    return float(v)
                except Exception:
                    return None

            if video_wall_width is not None:
                vw = _to_float(video_wall_width)
                if vw is not None:
                    input_selection_cfg["width"] = vw
            if video_wall_height is not None:
                vh = _to_float(video_wall_height)
                if vh is not None:
                    input_selection_cfg["height"] = vh
            if video_wall_horizontal is not None:
                vx = _to_float(video_wall_horizontal)
                if vx is not None:
                    input_selection_cfg["x"] = vx
            if video_wall_vertical is not None:
                vy = _to_float(video_wall_vertical)
                if vy is not None:
                    input_selection_cfg["y"] = vy
            if video_wall_grid_width is not None:
                try:
                    grid_w = int(float(video_wall_grid_width))
                    input_selection_cfg["width"] = grid_w
                except Exception:
                    pass
            if video_wall_grid_height is not None:
                try:
                    grid_h = int(float(video_wall_grid_height))
                    input_selection_cfg["height"] = grid_h
                except Exception:
                    pass
            if video_wall_grid_x is not None:
                try:
                    grid_x = int(float(video_wall_grid_x))
                    input_selection_cfg["x"] = grid_x
                except Exception:
                    pass
            if video_wall_grid_y is not None:
                try:
                    grid_y = int(float(video_wall_grid_y))
                    input_selection_cfg["y"] = grid_y
                except Exception:
                    pass
            wall_cfg["input_selection"] = input_selection_cfg

            edge_comp_cfg = wall_cfg.get("edge_compensation") or {}
            if not isinstance(edge_comp_cfg, dict):
                edge_comp_cfg = {}
            if video_wall_edge_mode is not None:
                edge_comp_cfg["mode"] = str(video_wall_edge_mode).strip().replace("_", " ")
            if video_wall_edge_top is not None:
                try:
                    edge_comp_cfg["top"] = float(video_wall_edge_top)
                except Exception:
                    pass
            if video_wall_edge_bottom is not None:
                try:
                    edge_comp_cfg["bottom"] = float(video_wall_edge_bottom)
                except Exception:
                    pass
            if video_wall_edge_left is not None:
                try:
                    edge_comp_cfg["left"] = float(video_wall_edge_left)
                except Exception:
                    pass
            if video_wall_edge_right is not None:
                try:
                    edge_comp_cfg["right"] = float(video_wall_edge_right)
                except Exception:
                    pass
            wall_cfg["edge_compensation"] = edge_comp_cfg

            video_output_cfg["wall"] = wall_cfg
            video_cfg["output"] = video_output_cfg
            payload_cfg["video"] = video_cfg

            output_cfg = payload_cfg.get("output") or {}
            if not isinstance(output_cfg, dict):
                output_cfg = {}

            hdcp_cfg = output_cfg.get("hdcp") or payload_cfg.get("hdcp") or {}
            if not isinstance(hdcp_cfg, dict):
                hdcp_cfg = {}
            if hdcp_support_version is not None:
                hdcp_cfg["support_version"] = hdcp_support_version
            output_cfg["hdcp"] = hdcp_cfg
            payload_cfg["output"] = output_cfg
            # Keep legacy root location updated for older firmware variants.
            payload_cfg["hdcp"] = hdcp_cfg

            audio_cfg = payload_cfg.get("audio") or {}
            if not isinstance(audio_cfg, dict):
                audio_cfg = {}
            audio_backup_cfg = audio_cfg.get("backup") or {}
            if not isinstance(audio_backup_cfg, dict):
                audio_backup_cfg = {}
            if audio_input is not None:
                audio_cfg["input"] = audio_input
            audio_cfg["backup"] = audio_backup_cfg
            payload_cfg["audio"] = audio_cfg

            set_resp = _ws_send_recv(url, {
                "id": "hdmi_output-set",
                "username": user,
                "password": attempt_pwd,
                "config_set": {
                    "name": "hdmi_output",
                    "config": [payload_cfg]
                }
            }, timeout=max(timeout, 4.0))
            if set_resp and set_resp.get("error"):
                raise ValueError(set_resp.get("error"))

            fields = _ws_get_decoder_inputs(ip, user, attempt_pwd, ws_port, ws_path, timeout=max(timeout, 2.5), attempts=1, delay=0)
            if not fields:
                log.warning("[DECODER_INPUT] %s set acknowledged but verify read failed; returning requested fields", ip)
                return {"ok": True, "fields": requested_fields, "warning": "verify_failed"}
            return {"ok": True, "fields": fields}
        except Exception as e:
            last_error = str(e)

    return {"ok": False, "error": last_error}

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

@app.route("/matrix/configure")
def matrix_configure():
    idx = ASSET_DIR / "ui" / "matrix" / "configure.html"
    if idx.exists():
        return send_file(str(idx), mimetype="text/html; charset=utf-8")
    return "<h1>Configure UI not found</h1>", 404

@app.route("/matrix/usb")
def usb_matrix_index():
    idx = ASSET_DIR / "ui" / "matrix" / "usb.html"
    if idx.exists():
        return send_file(str(idx), mimetype="text/html; charset=utf-8")
    return "<h1>USB Matrix UI not found</h1>", 404

@app.route("/help")
def user_guide():
    idx = ASSET_DIR / "ui" / "user-guide.html"
    if idx.exists():
        resp = send_file(str(idx), mimetype="text/html; charset=utf-8")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    return "<h1>User guide not found</h1>", 404

@app.route("/")
def index():
    idx = ASSET_DIR / "ui" / "index.html"
    if idx.exists():
        resp = send_file(str(idx), mimetype="text/html; charset=utf-8")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    return "<h1>Omni Upgrade Server</h1><p>UI not found (ui/index.html). Backend API available.</p>"

@app.route("/ui/<path:filename>")
def ui_files(filename):
    resp = send_from_directory(ASSET_DIR / "ui", filename)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp

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

def _adapter_entry(name: str, ip: str, mask: str):
    if not ip or not mask:
        return None
    if ip.startswith("169.254.") or not _is_private_ipv4(ip):
        return None
    try:
        net = _ipa.IPv4Network(f"{ip}/{mask}", strict=False)
        cidr = f"{net.network_address}/{net.prefixlen}"
        scan = f"{str(net.network_address).rsplit('.',1)[0]}.1-254" if net.prefixlen <= 24 else cidr
        return {"name": name or f"iface {ip}", "ip": ip, "netmask": str(net.netmask), "cidr": cidr, "scan": scan}
    except Exception:
        return None

def _adapters_ip_addr(active_only=True):
    out = []
    if platform.system().lower() != "linux":
        return out
    cmd = ["ip", "-o", "-4", "addr", "show"]
    if active_only:
        cmd.append("up")
    try:
        txt = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="ignore")
    except Exception:
        return out
    for line in txt.splitlines():
        m = re.match(r"\d+:\s+([^:\s]+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if not m:
            continue
        name, ip, prefix = m.groups()
        try:
            mask = str(_ipa.IPv4Network(f"0.0.0.0/{prefix}").netmask)
        except Exception:
            continue
        entry = _adapter_entry(name, ip, mask)
        if entry:
            out.append(entry)
    return out

def _adapters_ifconfig(active_only=True):
    out = []
    if platform.system().lower() not in ("darwin", "linux"):
        return out
    try:
        txt = subprocess.check_output(["ifconfig"], text=True, encoding="utf-8", errors="ignore")
    except Exception:
        return out
    for block in re.split(r"\n(?=\S)", txt):
        first = block.splitlines()[0] if block.splitlines() else ""
        name = first.split(":", 1)[0].strip()
        if not name:
            continue
        if active_only and "status: inactive" in block.lower():
            continue
        m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\s+(?:netmask\s+)?(0x[0-9a-fA-F]+|\d+\.\d+\.\d+\.\d+)", block)
        if not m:
            continue
        ip, raw_mask = m.groups()
        if raw_mask.lower().startswith("0x"):
            try:
                mask_int = int(raw_mask, 16)
                mask = ".".join(str((mask_int >> shift) & 0xff) for shift in (24, 16, 8, 0))
            except Exception:
                continue
        else:
            mask = raw_mask
        entry = _adapter_entry(name, ip, mask)
        if entry:
            out.append(entry)
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
        if not res: res = _adapters_ip_addr(active_only=not include_all)
        if not res: res = _adapters_ifconfig(active_only=not include_all)
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
            "firmware_path": app.config.get('FIRMWARE_PATH', ''),
            "app_version": _app_version(),
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
        if platform.system() == "Windows" and re.match(r"^[A-Za-z]:$", path_arg or ""):
            path_arg = path_arg + "\\"
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

        drives = []
        if platform.system() == "Windows":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                root = f"{letter}:\\"
                if Path(root).exists():
                    drives.append(root)

        parent = ""
        try:
            parent_path = base.parent
            if parent_path != base:
                parent = str(parent_path)
        except Exception:
            parent = ""

        return jsonify({
            "ok": True,
            "path": str(base),
            "parent": parent,
            "drives": drives,
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

@app.route("/api/lldp", methods=["POST"])
def api_lldp():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400

    user, preferred_pwd, _device = _device_credentials(ip)
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = min(float(app.config.get('TIMEOUT', 4.5)), 3.0)
    payload = {
        "id": "lldp-get",
        "username": user,
        "password": preferred_pwd,
        "config_get": "lldp",
    }
    try:
        resp = _ws_send_recv_with_fallback(ip, payload, timeout, ws_port, ws_path, preferred_pwd)
        if not resp or resp.get("error"):
            return jsonify({"ok": False, "error": (resp or {}).get("error") or "LLDP request failed"}), 502
        return jsonify({"ok": True, "lldp": resp.get("config") or {}, "raw": resp})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def _systeminfo_edit_payload(si_cfg: dict, hostname: str = None, system_mode: str = None):
    buttons = si_cfg.get("buttons") or {}
    buttons_cfg = {
        "enabled": bool(buttons.get("enabled", True)),
    }
    for key in ("infoenabled", "updownenabled", "menuenabled"):
        if key in buttons:
            buttons_cfg[key] = bool(buttons.get(key))

    leds = si_cfg.get("leds") or {}
    payload = {
        "description": si_cfg.get("description", "") or "",
        "location": si_cfg.get("location", "") or "",
        "hostname": hostname if hostname is not None else (si_cfg.get("hostname", "") or ""),
        "ntpserver": si_cfg.get("ntpserver") or si_cfg.get("ntp_server") or si_cfg.get("ntpServer") or "",
        "buttons": buttons_cfg,
        "leds": {
            "enabled": bool(leds.get("enabled", True)),
        },
        "system_mode": system_mode if system_mode is not None else (si_cfg.get("system_mode", "Colibri") or "Colibri"),
    }
    if isinstance(si_cfg.get("lcd"), dict):
        payload["lcd"] = si_cfg.get("lcd")
    return payload

def _update_hostname_cache(ip: str, hostname: str, used_password: str = None):
    if HAS_MATRIX and omni_matrix_logic:
        for table_name in ("_encoders", "_decoders"):
            table = getattr(omni_matrix_logic, table_name, None)
            if isinstance(table, dict) and ip in table:
                table[ip]["hostname"] = hostname
                table[ip]["host"] = hostname
    units = _load_cache() or []
    changed = False
    for unit in units:
        if (unit or {}).get("ip") != ip:
            continue
        if unit.get("hostname") != hostname:
            unit["hostname"] = hostname
            changed = True
        if used_password and unit.get("password") != used_password:
            unit["password"] = used_password
            changed = True
        details = unit.setdefault("details", {})
        si_details = details.setdefault("systeminfo", {"config": {}})
        si_cfg = si_details.setdefault("config", {})
        if si_cfg.get("hostname") != hostname:
            si_cfg["hostname"] = hostname
            changed = True
    if changed:
        _save_cache(units)

    if SCAN_RESULTS.exists():
        try:
            with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                scan_data = json.load(f)
            scan_changed = False
            for key in ("devices", "encoders", "decoders"):
                arr = scan_data.get(key)
                if not isinstance(arr, list):
                    continue
                for device in arr:
                    if (device or {}).get("ip") != ip:
                        continue
                    if device.get("hostname") != hostname:
                        device["hostname"] = hostname
                        scan_changed = True
                    details = device.setdefault("details", {})
                    si_details = details.setdefault("systeminfo", {"config": {}})
                    si_cfg = si_details.setdefault("config", {})
                    if si_cfg.get("hostname") != hostname:
                        si_cfg["hostname"] = hostname
                        scan_changed = True
            if scan_changed:
                with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                    json.dump(scan_data, f, indent=2)
        except Exception as e:
            log.warning("[HOSTNAME] Failed to update scan_results.json: %s", e)

def _update_codec_cache(ip: str, system_mode: str, supported_modes=None, used_password: str = None):
    codec = _codec_label(system_mode)
    supported_modes = supported_modes if isinstance(supported_modes, list) else []
    units = _load_cache() or []
    changed = False
    for unit in units:
        if (unit or {}).get("ip") != ip:
            continue
        for key, value in (
            ("system_mode", system_mode),
            ("codec", codec),
            ("supported_system_modes", supported_modes),
            ("codec_configurable", _is_codec_configurable_model(unit.get("model"))),
        ):
            if unit.get(key) != value:
                unit[key] = value
                changed = True
        if used_password and unit.get("password") != used_password:
            unit["password"] = used_password
            changed = True
        si_cfg = unit.setdefault("details", {}).setdefault("systeminfo", {"config": {}}).setdefault("config", {})
        if si_cfg.get("system_mode") != system_mode:
            si_cfg["system_mode"] = system_mode
            changed = True
        if supported_modes and si_cfg.get("supported_system_modes") != supported_modes:
            si_cfg["supported_system_modes"] = supported_modes
            changed = True
    if changed:
        _save_cache(units)

    if SCAN_RESULTS.exists():
        try:
            with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                scan_data = json.load(f)
            scan_changed = False
            for key in ("devices", "encoders", "decoders"):
                arr = scan_data.get(key)
                if not isinstance(arr, list):
                    continue
                for device in arr:
                    if (device or {}).get("ip") != ip:
                        continue
                    for field, value in (
                        ("system_mode", system_mode),
                        ("codec", codec),
                        ("supported_system_modes", supported_modes),
                        ("codec_configurable", _is_codec_configurable_model(device.get("model"))),
                    ):
                        if device.get(field) != value:
                            device[field] = value
                            scan_changed = True
                    si_cfg = device.setdefault("details", {}).setdefault("systeminfo", {"config": {}}).setdefault("config", {})
                    if si_cfg.get("system_mode") != system_mode:
                        si_cfg["system_mode"] = system_mode
                        scan_changed = True
                    if supported_modes and si_cfg.get("supported_system_modes") != supported_modes:
                        si_cfg["supported_system_modes"] = supported_modes
                        scan_changed = True
            if scan_changed:
                with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                    json.dump(scan_data, f, indent=2)
        except Exception as e:
            log.warning("[CODEC] Failed to update scan_results.json: %s", e)

def _update_firmware_cache(ip: str, firmware_version: str):
    firmware_version = (firmware_version or "").strip()
    if not ip or not firmware_version:
        return

    units = _load_cache() or []
    changed = False
    for unit in units:
        if (unit or {}).get("ip") != ip:
            continue
        for key in ("version", "firmwareversion"):
            if unit.get(key) != firmware_version:
                unit[key] = firmware_version
                changed = True
        si_cfg = unit.setdefault("details", {}).setdefault("systeminfo", {"config": {}}).setdefault("config", {})
        if si_cfg.get("firmwareversion") != firmware_version:
            si_cfg["firmwareversion"] = firmware_version
            changed = True
    if changed:
        _save_cache(units)

    if SCAN_RESULTS.exists():
        try:
            with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                scan_data = json.load(f)
            scan_changed = False
            for key in ("devices", "encoders", "decoders"):
                arr = scan_data.get(key)
                if not isinstance(arr, list):
                    continue
                for device in arr:
                    if (device or {}).get("ip") != ip:
                        continue
                    for field in ("version", "firmwareversion"):
                        if device.get(field) != firmware_version:
                            device[field] = firmware_version
                            scan_changed = True
                    si_cfg = device.setdefault("details", {}).setdefault("systeminfo", {"config": {}}).setdefault("config", {})
                    if si_cfg.get("firmwareversion") != firmware_version:
                        si_cfg["firmwareversion"] = firmware_version
                        scan_changed = True
            if scan_changed:
                with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                    json.dump(scan_data, f, indent=2)
        except Exception as e:
            log.warning("[POLL] Failed to update cached firmware version: %s", e)

def _update_poll_detail_cache(ip: str, updates: dict):
    if not ip or not isinstance(updates, dict):
        return

    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        return

    def apply_updates(unit):
        if not isinstance(unit, dict) or unit.get("ip") != ip:
            return False
        changed = False
        for key, value in updates.items():
            if unit.get(key) != value:
                unit[key] = value
                changed = True

        details = unit.setdefault("details", {})
        si_cfg = details.setdefault("systeminfo", {"config": {}}).setdefault("config", {})
        tz_cfg = details.setdefault("timezone", {"config": {}}).setdefault("config", {})

        for key in ("hostname", "ntpserver", "ntp_server", "version", "firmwareversion"):
            if key in updates and si_cfg.get(key) != updates[key]:
                si_cfg[key] = updates[key]
                changed = True
        if "hostname" in updates and si_cfg.get("hostname") != updates["hostname"]:
            si_cfg["hostname"] = updates["hostname"]
            changed = True
        if "ntpserver" in updates and si_cfg.get("ntp_server") != updates["ntpserver"]:
            si_cfg["ntp_server"] = updates["ntpserver"]
            changed = True
        if "ntp_server" in updates and si_cfg.get("ntpserver") != updates["ntp_server"]:
            si_cfg["ntpserver"] = updates["ntp_server"]
            changed = True
        if "timezone" in updates:
            for key in ("timezone", "active_timezone"):
                if tz_cfg.get(key) != updates["timezone"]:
                    tz_cfg[key] = updates["timezone"]
                    changed = True
        if "active_timezone" in updates:
            for key in ("timezone", "active_timezone"):
                if tz_cfg.get(key) != updates["active_timezone"]:
                    tz_cfg[key] = updates["active_timezone"]
                    changed = True
        return changed

    units = _load_cache() or []
    changed = False
    for unit in units:
        changed = apply_updates(unit) or changed
    if changed:
        _save_cache(units)

    if SCAN_RESULTS.exists():
        try:
            with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                scan_data = json.load(f)
            scan_changed = False
            for key in ("devices", "encoders", "decoders"):
                arr = scan_data.get(key)
                if isinstance(arr, list):
                    for device in arr:
                        scan_changed = apply_updates(device) or scan_changed
            if scan_changed:
                with open(SCAN_RESULTS, "w", encoding="utf-8") as f:
                    json.dump(scan_data, f, indent=2)
        except Exception as e:
            log.warning("[POLL] Failed to update cached poll details: %s", e)

@app.route("/api/hostname", methods=["POST"])
def api_hostname():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    hostname = (data.get("hostname") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400
    if not hostname:
        return jsonify({"ok": False, "error": "hostname required"}), 400
    if not re.fullmatch(r"[A-Za-z0-9.-]+", hostname):
        return jsonify({"ok": False, "error": "hostname may only contain letters, numbers, hyphen, and period"}), 400

    cache_devices = _load_cache() or []
    cache_map = {d.get("ip"): d for d in cache_devices if d.get("ip")}
    user, preferred_pwd, _device = _device_credentials(ip, cache_map)
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    last_error = "hostname update failed"

    for pwd_try in _password_candidates(preferred_pwd):
        try:
            url = _ws_url(ip, ws_port, ws_path)
            si_resp = _ws_send_recv(url, {
                "id": "systeminfo-get",
                "username": user,
                "password": pwd_try,
                "config_get": "systeminfo"
            }, timeout=min(timeout, 4.0))
            if not si_resp or si_resp.get("error"):
                last_error = (si_resp or {}).get("error") or "systeminfo-get failed"
                continue

            si_cfg = (si_resp or {}).get("config") or {}
            if not isinstance(si_cfg, dict):
                si_cfg = {}
            old_hostname = (si_cfg.get("hostname") or "").strip()
            if old_hostname == hostname:
                _update_hostname_cache(ip, hostname, pwd_try)
                return jsonify({"ok": True, "changed": False, "hostname": hostname})

            set_resp = _ws_send_recv(url, {
                "id": "systeminfo-set",
                "username": user,
                "password": pwd_try,
                "config_set": {
                    "name": "systeminfo",
                    "config": _systeminfo_edit_payload(si_cfg, hostname)
                }
            }, timeout=max(timeout, 6.0))
            if not set_resp or set_resp.get("error"):
                last_error = (set_resp or {}).get("error") or "systeminfo-set failed"
                continue

            verify_resp = _ws_send_recv(url, {
                "id": "systeminfo-get-verify",
                "username": user,
                "password": pwd_try,
                "config_get": "systeminfo"
            }, timeout=min(timeout, 4.0))
            verify_cfg = (verify_resp or {}).get("config") or {}
            verified_hostname = (verify_cfg.get("hostname") or "").strip() if isinstance(verify_cfg, dict) else ""
            if verified_hostname != hostname:
                last_error = f"verification returned hostname '{verified_hostname}'"
                continue

            _update_hostname_cache(ip, hostname, pwd_try)
            return jsonify({"ok": True, "changed": True, "hostname": hostname})
        except Exception as e:
            last_error = str(e)

    return jsonify({"ok": False, "error": last_error}), 502

@app.route("/api/codec", methods=["POST"])
def api_codec():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    system_mode = (data.get("system_mode") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400
    if system_mode not in CODEC_VALUES:
        return jsonify({"ok": False, "error": "unsupported codec mode"}), 400

    cache_devices = _load_cache() or []
    cache_map = {d.get("ip"): d for d in cache_devices if d.get("ip")}
    user, preferred_pwd, device = _device_credentials(ip, cache_map)
    model = (device or {}).get("model") or ""
    if model and not _is_codec_configurable_model(model):
        return jsonify({"ok": False, "error": "codec is not configurable on HW-OMNI units"}), 400

    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    last_error = "codec update failed"

    for pwd_try in _password_candidates(preferred_pwd):
        try:
            url = _ws_url(ip, ws_port, ws_path)
            si_resp = _ws_send_recv(url, {
                "id": "systeminfo-get",
                "username": user,
                "password": pwd_try,
                "config_get": "systeminfo"
            }, timeout=min(timeout, 4.0))
            if not si_resp or si_resp.get("error"):
                last_error = (si_resp or {}).get("error") or "systeminfo-get failed"
                continue

            si_cfg = (si_resp or {}).get("config") or {}
            if not isinstance(si_cfg, dict):
                si_cfg = {}
            live_model = si_cfg.get("model") or model
            if not _is_codec_configurable_model(live_model):
                return jsonify({"ok": False, "error": "codec is not configurable on HW-OMNI units"}), 400
            supported_modes = si_cfg.get("supported_system_modes") or []
            if isinstance(supported_modes, list) and supported_modes and system_mode not in supported_modes:
                return jsonify({"ok": False, "error": "codec mode not supported by unit"}), 400
            old_mode = (si_cfg.get("system_mode") or "").strip()
            if old_mode == system_mode:
                _update_codec_cache(ip, system_mode, supported_modes, pwd_try)
                return jsonify({"ok": True, "changed": False, "system_mode": system_mode, "codec": _codec_label(system_mode), "supported_system_modes": supported_modes})

            set_resp = _ws_send_recv(url, {
                "id": "systeminfo-set",
                "username": user,
                "password": pwd_try,
                "config_set": {
                    "name": "systeminfo",
                    "config": _systeminfo_edit_payload(si_cfg, system_mode=system_mode)
                }
            }, timeout=max(timeout, 6.0))
            if not set_resp or set_resp.get("error"):
                last_error = (set_resp or {}).get("error") or "systeminfo-set failed"
                continue

            _update_codec_cache(ip, system_mode, supported_modes, pwd_try)
            return jsonify({"ok": True, "changed": True, "system_mode": system_mode, "codec": _codec_label(system_mode), "supported_system_modes": supported_modes})
        except Exception as e:
            last_error = str(e)

    return jsonify({"ok": False, "error": last_error}), 502

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
    system_mode = cfg.get("system_mode") or ""
    supported_system_modes = cfg.get("supported_system_modes") or []
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
                for idx, session in enumerate((sessions or [])[:2], start=1):
                    video_stream = ((session.get("video") or {}).get("stream") or {})
                    audio_stream = ((session.get("audio") or {}).get("stream") or {})
                    matrix_data.update({
                        f"session{idx}_name": session.get("name") or f"session{idx}",
                        f"session{idx}_video_mcast": video_stream.get("destination_address"),
                        f"session{idx}_video_port": video_stream.get("destination_port"),
                        f"session{idx}_audio_mcast": audio_stream.get("destination_address"),
                        f"session{idx}_audio_port": audio_stream.get("destination_port"),
                    })
        except Exception as e:
            log.debug("[SCAN] %s - sessions fetch failed: %s", ip, e)
        try:
            enc_input = _ws_get_encoder_input_settings(ip, user, pwd, ws_port, ws_path, timeout=min(timeout, 1.5), attempts=1, delay=0)
            for key in ("hdcp_encrypted", "hdcp_negotiated_version", "hdcp_support_version", "hdcp_supported_versions"):
                if enc_input.get(key) is not None:
                    matrix_data[key] = enc_input.get(key)
        except Exception as e:
            log.debug("[SCAN] %s - encoder HDCP fetch failed: %s", ip, e)
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
        try:
            dec_fields = _ws_get_decoder_inputs(ip, user, pwd, ws_port, ws_path, timeout=min(timeout, 1.5), attempts=1, delay=0)
            for key in ("hdcp_support_version", "hdcp_supported_versions"):
                if dec_fields.get(key) is not None:
                    matrix_data[key] = dec_fields.get(key)
        except Exception as e:
            log.debug("[SCAN] %s - decoder HDCP fetch failed: %s", ip, e)
    
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
        "system_mode": system_mode,
        "codec": _codec_label(system_mode),
        "supported_system_modes": supported_system_modes if isinstance(supported_system_modes, list) else [],
        "codec_configurable": _is_codec_configurable_model(model),
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
        enc = [u for u in units if _infer_unit_role(u) == "encoder"]
        dec = [u for u in units if _infer_unit_role(u) == "decoder"]
    else:
        # Fall back to scan_results format
        data = _load_scan_results_file() or {}
        enc = data.get("encoders") or []
        dec = data.get("decoders") or []
        if not enc and not dec:
            devs = data.get("devices") or []
            enc = [u for u in devs if _infer_unit_role(u) == "encoder"]
            dec = [u for u in devs if _infer_unit_role(u) == "decoder"]
    # Enrich cache-loaded units with scan_results multicast info if missing
    scan_data = _load_scan_results_file() or {}
    sr_enc_map = {e.get("ip"): e for e in (scan_data.get("encoders") or [])}
    if not sr_enc_map:
        # derive from devices if encoders list absent
        sr_enc_map = {u.get("ip"): u for u in (scan_data.get("devices") or []) if _infer_unit_role(u) == "encoder"}
    sr_dec_map = {d.get("ip"): d for d in (scan_data.get("decoders") or [])}
    if not sr_dec_map:
        sr_dec_map = {u.get("ip"): u for u in (scan_data.get("devices") or []) if _infer_unit_role(u) == "decoder"}

    def _merge_enc(e):
        src = sr_enc_map.get(e.get("ip")) or {}
        e_si = (((e.get("details") or {}).get("systeminfo") or {}).get("config") or {})
        src_si = (((src.get("details") or {}).get("systeminfo") or {}).get("config") or {})
        system_mode = e.get("system_mode") or src.get("system_mode") or e_si.get("system_mode") or src_si.get("system_mode")
        return {
            "ip": e.get("ip"),
            "host": e.get("hostname") or e.get("host"),
            "model": e.get("model"),
            "system_mode": system_mode,
            "codec": e.get("codec") or src.get("codec") or _codec_label(system_mode),
            "supported_system_modes": e.get("supported_system_modes") or src.get("supported_system_modes") or e_si.get("supported_system_modes") or src_si.get("supported_system_modes") or [],
            "codec_configurable": e.get("codec_configurable") if e.get("codec_configurable") is not None else _is_codec_configurable_model(e.get("model")),
            "fw": e.get("version") or e.get("fw"),
            "serial": e.get("serialnumber") or e.get("serial"),
            "v_mcast": e.get("v_mcast") or src.get("v_mcast"),
            "v_port": e.get("v_port") or src.get("v_port"),
            "a_mcast": e.get("a_mcast") or src.get("a_mcast"),
            "a_port": e.get("a_port") or src.get("a_port"),
            "input_auto_switch": e.get("input_auto_switch") if e.get("input_auto_switch") is not None else src.get("input_auto_switch"),
            "active_input": e.get("active_input") or src.get("active_input"),
            "input_status": e.get("input_status") or src.get("input_status") or [],
            "cable_present": e.get("cable_present") if e.get("cable_present") is not None else src.get("cable_present"),
            "edid": e.get("edid") or src.get("edid"),
            "edid_options": e.get("edid_options") or src.get("edid_options") or [],
            "hdcp_encrypted": e.get("hdcp_encrypted") if e.get("hdcp_encrypted") is not None else src.get("hdcp_encrypted"),
            "hdcp_negotiated_version": e.get("hdcp_negotiated_version") or src.get("hdcp_negotiated_version"),
            "hdcp_support_version": e.get("hdcp_support_version") or src.get("hdcp_support_version"),
            "hdcp_supported_versions": e.get("hdcp_supported_versions") or src.get("hdcp_supported_versions") or [],
        }

    def _merge_dec(d):
        src = sr_dec_map.get(d.get("ip")) or {}
        d_si = (((d.get("details") or {}).get("systeminfo") or {}).get("config") or {})
        src_si = (((src.get("details") or {}).get("systeminfo") or {}).get("config") or {})
        system_mode = d.get("system_mode") or src.get("system_mode") or d_si.get("system_mode") or src_si.get("system_mode")
        return {
            "ip": d.get("ip"),
            "host": d.get("hostname") or d.get("host"),
            "model": d.get("model"),
            "system_mode": system_mode,
            "codec": d.get("codec") or src.get("codec") or _codec_label(system_mode),
            "supported_system_modes": d.get("supported_system_modes") or src.get("supported_system_modes") or d_si.get("supported_system_modes") or src_si.get("supported_system_modes") or [],
            "codec_configurable": d.get("codec_configurable") if d.get("codec_configurable") is not None else _is_codec_configurable_model(d.get("model")),
            "fw": d.get("version") or d.get("fw"),
            "serial": d.get("serialnumber") or d.get("serial"),
            "ip1_addr": d.get("ip1_addr") or src.get("ip1_addr"),
            "ip1_port": d.get("ip1_port") or src.get("ip1_port"),
            "ip3_addr": d.get("ip3_addr") or src.get("ip3_addr"),
            "ip3_port": d.get("ip3_port") or src.get("ip3_port"),
            "sap_input_enabled": d.get("sap_input_enabled") if d.get("sap_input_enabled") is not None else src.get("sap_input_enabled"),
            "input_session": d.get("input_session") or src.get("input_session"),
            "input_session_options": d.get("input_session_options") or src.get("input_session_options") or [],
            "hdcp_support_version": d.get("hdcp_support_version") or src.get("hdcp_support_version"),
            "hdcp_supported_versions": d.get("hdcp_supported_versions") or src.get("hdcp_supported_versions") or [],
            "video_input": d.get("video_input") or src.get("video_input"),
            "audio_input": d.get("audio_input") or src.get("audio_input"),
            "video_input_options": d.get("video_input_options") or src.get("video_input_options") or [],
            "audio_input_options": d.get("audio_input_options") or src.get("audio_input_options") or [],
            "stretch_crop_mode": d.get("stretch_crop_mode") or src.get("stretch_crop_mode"),
            "stretch_crop_mode_options": d.get("stretch_crop_mode_options") or src.get("stretch_crop_mode_options") or [],
            "resolution": d.get("resolution") or src.get("resolution"),
            "resolution_options": d.get("resolution_options") or src.get("resolution_options") or [],
            "framerate": d.get("framerate") or src.get("framerate"),
            "framerate_options": d.get("framerate_options") or src.get("framerate_options") or [],
            "fast_switching_enabled": d.get("fast_switching_enabled") if d.get("fast_switching_enabled") is not None else src.get("fast_switching_enabled"),
            "fast_switching_timeout": d.get("fast_switching_timeout") if d.get("fast_switching_timeout") is not None else src.get("fast_switching_timeout"),
            "fast_switching_colorspace": d.get("fast_switching_colorspace") or src.get("fast_switching_colorspace"),
            "fast_switching_colorspace_options": d.get("fast_switching_colorspace_options") or src.get("fast_switching_colorspace_options") or [],
            "video_wall_enabled": d.get("video_wall_enabled") if d.get("video_wall_enabled") is not None else src.get("video_wall_enabled"),
            "video_wall_unit": d.get("video_wall_unit") or src.get("video_wall_unit"),
            "video_wall_unit_options": d.get("video_wall_unit_options") or src.get("video_wall_unit_options") or [],
            "video_wall_total_width": d.get("video_wall_total_width") if d.get("video_wall_total_width") is not None else src.get("video_wall_total_width"),
            "video_wall_total_height": d.get("video_wall_total_height") if d.get("video_wall_total_height") is not None else src.get("video_wall_total_height"),
            "video_wall_grid_width": d.get("video_wall_grid_width") if d.get("video_wall_grid_width") is not None else src.get("video_wall_grid_width"),
            "video_wall_grid_height": d.get("video_wall_grid_height") if d.get("video_wall_grid_height") is not None else src.get("video_wall_grid_height"),
            "video_wall_grid_x": d.get("video_wall_grid_x") if d.get("video_wall_grid_x") is not None else src.get("video_wall_grid_x"),
            "video_wall_grid_y": d.get("video_wall_grid_y") if d.get("video_wall_grid_y") is not None else src.get("video_wall_grid_y"),
            "video_wall_width": d.get("video_wall_width") if d.get("video_wall_width") is not None else src.get("video_wall_width"),
            "video_wall_height": d.get("video_wall_height") if d.get("video_wall_height") is not None else src.get("video_wall_height"),
            "video_wall_horizontal": d.get("video_wall_horizontal") if d.get("video_wall_horizontal") is not None else src.get("video_wall_horizontal"),
            "video_wall_vertical": d.get("video_wall_vertical") if d.get("video_wall_vertical") is not None else src.get("video_wall_vertical"),
            "video_wall_rotation": d.get("video_wall_rotation") if d.get("video_wall_rotation") is not None else src.get("video_wall_rotation"),
            "video_wall_rotation_options": d.get("video_wall_rotation_options") or src.get("video_wall_rotation_options") or [],
            "video_wall_edge_mode": d.get("video_wall_edge_mode") or src.get("video_wall_edge_mode"),
            "video_wall_edge_mode_options": d.get("video_wall_edge_mode_options") or src.get("video_wall_edge_mode_options") or [],
            "video_wall_edge_top": d.get("video_wall_edge_top") if d.get("video_wall_edge_top") is not None else src.get("video_wall_edge_top"),
            "video_wall_edge_bottom": d.get("video_wall_edge_bottom") if d.get("video_wall_edge_bottom") is not None else src.get("video_wall_edge_bottom"),
            "video_wall_edge_left": d.get("video_wall_edge_left") if d.get("video_wall_edge_left") is not None else src.get("video_wall_edge_left"),
            "video_wall_edge_right": d.get("video_wall_edge_right") if d.get("video_wall_edge_right") is not None else src.get("video_wall_edge_right"),
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
            live_encs = {e.get("ip"): e for e in (mstate.get("encoders") or [])}
            live_decs = {d.get("ip"): d for d in (mstate.get("decoders") or [])}
            for i,e in enumerate(enc_mapped):
                live = live_encs.get(e.get("ip"))
                if not live:
                    continue
                for k in ("input_auto_switch","active_input","input_status","cable_present","edid","edid_options","hdcp_encrypted","hdcp_negotiated_version","hdcp_support_version","hdcp_supported_versions"):
                    if live.get(k) is not None:
                        enc_mapped[i][k] = live.get(k)
            for i,d in enumerate(dec_mapped):
                live = live_decs.get(d.get("ip"))
                if not live:
                    continue
                # prefer live decoder input fields so refresh doesn't revert UI
                log.info(f"[API/STATE] Overlaying live decoder {d.get('ip')}: {live}")
                for k in (
                    "ip1_addr","ip1_port","ip3_addr","ip3_port",
                    "sap_input_enabled","input_session","input_session_options",
                    "hdcp_support_version","hdcp_supported_versions",
                    "video_input","audio_input","video_input_options","audio_input_options",
                    "stretch_crop_mode","stretch_crop_mode_options",
                    "resolution","resolution_options",
                    "framerate","framerate_options",
                    "fast_switching_enabled","fast_switching_timeout","fast_switching_colorspace","fast_switching_colorspace_options",
                    "video_wall_enabled","video_wall_unit","video_wall_unit_options",
                    "video_wall_total_width","video_wall_total_height",
                    "video_wall_grid_width","video_wall_grid_height","video_wall_grid_x","video_wall_grid_y",
                    "video_wall_width","video_wall_height","video_wall_horizontal","video_wall_vertical",
                    "video_wall_rotation","video_wall_rotation_options",
                    "video_wall_edge_mode","video_wall_edge_mode_options",
                    "video_wall_edge_top","video_wall_edge_bottom","video_wall_edge_left","video_wall_edge_right",
                ):
                    if live.get(k) is not None:
                        dec_mapped[i][k] = live.get(k)
    
    if dec_mapped:
        log.info(f"[API/STATE] Sample decoder after overlay: {dec_mapped[0]}")

    routes = {}
    for dec in dec_mapped:
        dec_ip = dec.get("ip")
        if not dec_ip:
            continue
        for enc in enc_mapped:
            if (
                dec.get("ip1_addr") == enc.get("v_mcast")
                and int(dec.get("ip1_port") or 0) == int(enc.get("v_port") or 0)
            ):
                routes[dec_ip] = enc.get("ip")
                break

    # Do not filter discovered units based on a transient reachability probe.
    # Control pages must remain driven by the cache/discovery list so a unit that
    # missed one poll can still be selected and controlled.

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
    try:
        omni_matrix_logic._load_cache()
    except Exception as e:
        log.warning("[ROUTE] matrix_logic cache reload failed: %s", e)
    decoder_user, decoder_pref_pwd, _ = _device_credentials(decoder, cache_map)
    encoder_user, encoder_pref_pwd, _ = _device_credentials(encoder, cache_map)
    timeout = app.config['TIMEOUT']

    route_encoder = omni_matrix_logic._encoders.get(encoder) or cache_map.get(encoder)
    route_decoder = omni_matrix_logic._decoders.get(decoder) or cache_map.get(decoder)
    if not route_encoder:
        return jsonify({"ok": False, "error": f"Encoder {encoder} not found in current state"}), 200
    if not route_decoder:
        return jsonify({"ok": False, "error": f"Decoder {decoder} not found in current state"}), 200
    encoder_mode = _unit_system_mode(route_encoder)
    decoder_mode = _unit_system_mode(route_decoder)
    if encoder_mode and decoder_mode and encoder_mode != decoder_mode:
        return jsonify({
            "ok": False,
            "error": f"Codec mismatch: encoder {_codec_label(encoder_mode)} cannot route to decoder {_codec_label(decoder_mode)}",
            "encoder_codec": _codec_label(encoder_mode),
            "decoder_codec": _codec_label(decoder_mode),
        }), 200

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
        enc = route_encoder
        dec = route_decoder
        if not enc:
            return jsonify({"ok": False, "error": f"Encoder {encoder} not found in current state"}), 200
        if not dec:
            return jsonify({"ok": False, "error": f"Decoder {decoder} not found in current state"}), 200
        detail = route_errors[-1] if route_errors else ""
        message = "Route command failed (device may be offline, unreachable, password mismatch, or unsupported AV route)"
        if detail:
            message = f"{message}: {detail}"
        return jsonify({"ok": False, "error": message}), 200

    # Note: Decoder inputs will be fetched by the polling system (every 5 seconds)
    # No need to fetch them here - route response returns immediately

    dec_payload = {"ip": decoder}
    try:
        fields = _ws_get_decoder_inputs(decoder, decoder_user, used_decoder_pwd or decoder_pref_pwd, app.config['WS_PORT'], app.config['WS_PATH'], timeout=4, attempts=1, delay=0)
        if fields:
            dec_payload.update(fields)
            if HAS_MATRIX and decoder in omni_matrix_logic._decoders:
                omni_matrix_logic._decoders[decoder].update(fields)
            units = _load_cache() or []
            for unit in units:
                if unit.get("ip") == decoder:
                    unit.update(fields)
                    unit["role"] = "decoder"
                    unit["type"] = "Decoder"
                    break
            _save_cache(units)
    except Exception as e:
        log.info("[ROUTE] post-route decoder refresh failed for %s: %s", decoder, e)
    return jsonify({"ok": bool(ok), "decoder": dec_payload})

@app.route("/api/poll_encoders", methods=["POST"])
def api_poll_encoders():
    """Poll encoders for current input settings and dropdown option lists."""
    if not HAS_MATRIX:
        return jsonify({"ok": False, "error": "matrix logic unavailable"}), 500

    data = request.get_json(silent=True) or {}
    encoder_ips = data.get("encoders") or []
    if not encoder_ips:
        return jsonify({"ok": False, "error": "encoders array required"}), 400

    default_user = app.config['USERNAME']
    default_pwd = app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']

    cache_devices = {d.get("ip"): d for d in _load_cache()}
    results = {}
    updated_count = 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        def poll_encoder(ip):
            try:
                device = cache_devices.get(ip, {})
                user = device.get("username") or default_user
                pwd = device.get("password") or default_pwd
                fields = _ws_get_encoder_input_settings(ip, user, pwd, ws_port, ws_path, timeout=4, attempts=1, delay=0)
                if not isinstance(fields, dict):
                    fields = {}

                url = _ws_url(ip, ws_port, ws_path)
                try:
                    sysinfo_resp = _ws_send_recv(url, {
                        "id": "systeminfo-get",
                        "username": user,
                        "password": pwd,
                        "config_get": "systeminfo"
                    }, timeout=min(timeout, 2.0))
                    sysinfo_cfg = (sysinfo_resp or {}).get("config") or {}
                    if isinstance(sysinfo_cfg, dict):
                        if "hostname" in sysinfo_cfg:
                            fields["hostname"] = (sysinfo_cfg.get("hostname") or "").strip()
                            fields["host"] = fields["hostname"]
                        version = (sysinfo_cfg.get("firmwareversion") or sysinfo_cfg.get("version") or "").strip()
                        if version:
                            fields["version"] = version
                            fields["firmwareversion"] = version
                            fields["fw"] = version
                except Exception as e:
                    log.debug("[POLL_ENCODERS] %s systeminfo refresh failed: %s", ip, e)

                try:
                    session_fields = _ws_get_encoder_output_settings(ip, user, pwd, ws_port, ws_path, timeout=4, attempts=1, delay=0)
                    sessions = session_fields.get("sessions") or []
                    fields.update(_encoder_session_matrix_fields(sessions))
                except Exception as e:
                    log.debug("[POLL_ENCODERS] %s sessions refresh failed: %s", ip, e)

                if fields and (
                    fields.get("edid_options") or fields.get("hdcp_supported_versions") or fields.get("edid") or
                    fields.get("hdcp_support_version") or fields.get("hostname") or fields.get("v_mcast") or fields.get("a_mcast")
                ):
                    return (ip, fields, True)
                return (ip, {"error": "failed to fetch"}, False)
            except Exception as e:
                return (ip, {"error": str(e)}, False)

        for ip, fields, success in executor.map(poll_encoder, encoder_ips):
            results[ip] = fields
            if success:
                if ip in omni_matrix_logic._encoders:
                    omni_matrix_logic._encoders[ip].update(fields)
                updated_count += 1

    if updated_count > 0:
        try:
            units = _load_cache() or []
            enc_fields = {ip: fields for ip, fields in results.items() if isinstance(fields, dict) and "error" not in fields}
            for u in units:
                fields = enc_fields.get(u.get("ip"))
                if not fields:
                    continue
                u.update({
                    "hostname": fields.get("hostname") or u.get("hostname"),
                    "host": fields.get("host") or fields.get("hostname") or u.get("host"),
                    "version": fields.get("version") or u.get("version"),
                    "firmwareversion": fields.get("firmwareversion") or u.get("firmwareversion"),
                    "input_auto_switch": fields.get("input_auto_switch"),
                    "active_input": fields.get("active_input"),
                    "input_status": fields.get("input_status") or [],
                    "cable_present": fields.get("cable_present"),
                    "edid": fields.get("edid"),
                    "edid_options": fields.get("edid_options") or [],
                    "hdcp_encrypted": fields.get("hdcp_encrypted"),
                    "hdcp_negotiated_version": fields.get("hdcp_negotiated_version"),
                    "hdcp_support_version": fields.get("hdcp_support_version"),
                    "hdcp_supported_versions": fields.get("hdcp_supported_versions") or [],
                    "v_mcast": fields.get("v_mcast") or u.get("v_mcast"),
                    "v_port": fields.get("v_port") or u.get("v_port"),
                    "a_mcast": fields.get("a_mcast") or u.get("a_mcast"),
                    "a_port": fields.get("a_port") or u.get("a_port"),
                    "session1_name": fields.get("session1_name") or u.get("session1_name"),
                    "session1_video_mcast": fields.get("session1_video_mcast") or u.get("session1_video_mcast"),
                    "session1_video_port": fields.get("session1_video_port") or u.get("session1_video_port"),
                    "session1_audio_mcast": fields.get("session1_audio_mcast") or u.get("session1_audio_mcast"),
                    "session1_audio_port": fields.get("session1_audio_port") or u.get("session1_audio_port"),
                    "session2_name": fields.get("session2_name") or u.get("session2_name"),
                    "session2_video_mcast": fields.get("session2_video_mcast") or u.get("session2_video_mcast"),
                    "session2_video_port": fields.get("session2_video_port") or u.get("session2_video_port"),
                    "session2_audio_mcast": fields.get("session2_audio_mcast") or u.get("session2_audio_mcast"),
                    "session2_audio_port": fields.get("session2_audio_port") or u.get("session2_audio_port"),
                })
            _save_cache(units)
        except Exception as e:
            log.error(f"[POLL_ENCODERS] Failed to save cache: {e}")

    return jsonify({"ok": True, "results": results, "updated": updated_count})

@app.route("/api/encoder_input", methods=["POST"])
def api_set_encoder_input():
    """Set encoder hdmi_input1 EDID and/or HDCP support version."""
    data = request.get_json(silent=True) or {}
    ip = (data.get("encoder") or data.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "encoder ip required"}), 400

    input_auto_switch = data.get("input_auto_switch") if "input_auto_switch" in data else None
    active_input = data.get("active_input") if "active_input" in data else None
    edid = data.get("edid") if "edid" in data else None
    hdcp_support_version = data.get("hdcp_support_version") if "hdcp_support_version" in data else None
    if input_auto_switch is None and active_input is None and edid is None and hdcp_support_version is None:
        return jsonify({"ok": False, "error": "one or more encoder input fields required"}), 400

    cache_devices = {d.get("ip"): d for d in _load_cache()}
    device = cache_devices.get(ip, {})
    user = device.get("username") or app.config['USERNAME']
    pwd = device.get("password") or app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']

    result = _ws_set_encoder_input_settings(
        ip, user, pwd, ws_port, ws_path, timeout,
        input_auto_switch=input_auto_switch,
        active_input=active_input,
        edid=edid,
        hdcp_support_version=hdcp_support_version,
    )
    if not result.get("ok"):
        status_code = int(result.pop("status_code", 500) or 500)
        return jsonify(result), status_code

    fields = result.get("fields") or {}
    if HAS_MATRIX and ip in omni_matrix_logic._encoders:
        omni_matrix_logic._encoders[ip].update(fields)

    try:
        units = _load_cache() or []
        for u in units:
            if u.get("ip") == ip:
                u.update({
                    "input_auto_switch": fields.get("input_auto_switch"),
                    "active_input": fields.get("active_input"),
                    "input_status": fields.get("input_status") or [],
                    "cable_present": fields.get("cable_present"),
                    "edid": fields.get("edid"),
                    "edid_options": fields.get("edid_options") or [],
                    "hdcp_encrypted": fields.get("hdcp_encrypted"),
                    "hdcp_negotiated_version": fields.get("hdcp_negotiated_version"),
                    "hdcp_support_version": fields.get("hdcp_support_version"),
                    "hdcp_supported_versions": fields.get("hdcp_supported_versions") or [],
                })
                break
        _save_cache(units)
    except Exception as e:
        log.error(f"[ENCODER_INPUT] Failed to save cache: {e}")

    return jsonify({"ok": True, "encoder": {"ip": ip, **fields}})

@app.route("/api/encoder_output", methods=["GET", "POST"])
def api_encoder_output():
    """Get or set encoder Output page session properties."""
    data = request.get_json(silent=True) or {}
    ip = (
        request.args.get("encoder")
        or request.args.get("ip")
        or data.get("encoder")
        or data.get("ip")
        or ""
    ).strip()
    if not ip:
        return jsonify({"ok": False, "error": "encoder ip required"}), 400

    cache_devices = {d.get("ip"): d for d in _load_cache()}
    device = cache_devices.get(ip, {})
    user = device.get("username") or app.config['USERNAME']
    pwd = device.get("password") or app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']

    if request.method == "GET":
        fields = _ws_get_encoder_output_settings(ip, user, pwd, ws_port, ws_path, timeout=timeout, attempts=1, delay=0)
        if not fields:
            return jsonify({"ok": False, "error": "failed to fetch encoder output settings"}), 500
        return jsonify({"ok": True, "encoder": ip, **fields})

    sessions = data.get("sessions")
    result = _ws_set_encoder_output_settings(ip, user, pwd, ws_port, ws_path, timeout, sessions)
    if not result.get("ok"):
        return jsonify(result), 500
    return jsonify({"ok": True, "encoder": ip, "sessions": result.get("sessions") or []})

@app.route("/api/encoder_encoding", methods=["GET", "POST"])
def api_encoder_encoding():
    """Get or set encoder Encoding page VC2 properties."""
    data = request.get_json(silent=True) or {}
    ip = (
        request.args.get("encoder")
        or request.args.get("ip")
        or data.get("encoder")
        or data.get("ip")
        or ""
    ).strip()
    if not ip:
        return jsonify({"ok": False, "error": "encoder ip required"}), 400

    cache_devices = {d.get("ip"): d for d in _load_cache()}
    device = cache_devices.get(ip, {})
    user = device.get("username") or app.config['USERNAME']
    pwd = device.get("password") or app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']

    if request.method == "GET":
        fields = _ws_get_encoder_encoding_settings(ip, user, pwd, ws_port, ws_path, timeout=timeout, attempts=1, delay=0)
        if not fields:
            return jsonify({"ok": False, "error": "failed to fetch encoder encoding settings"}), 500
        return jsonify({"ok": True, "encoder": ip, **fields})

    encoders = data.get("encoders")
    result = _ws_set_encoder_encoding_settings(ip, user, pwd, ws_port, ws_path, timeout, encoders)
    if not result.get("ok"):
        return jsonify(result), 500
    return jsonify({
        "ok": True,
        "encoder": ip,
        "encoders": result.get("encoders") or [],
        "input_options": result.get("input_options") or [],
    })

@app.route("/api/slate_status", methods=["POST"])
def api_slate_status():
    data = request.get_json(silent=True) or {}
    ips = data.get("ips") or []
    if isinstance(ips, str):
        ips = [ips]
    ips = [str(ip).strip() for ip in ips if str(ip).strip()]
    if not ips:
        return jsonify({"ok": False, "error": "ips required"}), 400

    cache_map = {d.get("ip"): d for d in (_load_cache() or []) if d.get("ip")}
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    results = {}

    for ip in ips:
        user, preferred_pwd, device = _device_credentials(ip, cache_map)
        role = (device.get("type") or device.get("role") or "").strip().lower()
        target = "encoder" if "encoder" in role else "decoder" if "decoder" in role else "unknown"
        try:
            logo_result = _ws_get_logo_library(ip, user, preferred_pwd, ws_port, ws_path, timeout)
            if target == "encoder":
                enc_fields = _ws_get_encoder_encoding_settings(ip, user, preferred_pwd, ws_port, ws_path, timeout=timeout, attempts=1, delay=0)
                encoders = enc_fields.get("encoders") or []
                slate = ((encoders[0] or {}).get("slate") or {}) if encoders else {}
                raw_logo = slate.get("logo") or ""
                results[ip] = {
                    "ok": True,
                    "ip": ip,
                    "target": target,
                    "mode": slate.get("mode") or "off",
                    "logo": raw_logo or "Not used",
                    "logos": logo_result.get("logos") or [],
                    "logo_options": _slate_logo_options(logo_result.get("logos") or [], raw_logo),
                }
            elif target == "decoder":
                slate_result = _ws_get_decoder_slate_settings(ip, user, preferred_pwd, ws_port, ws_path, timeout)
                if not slate_result.get("ok"):
                    raise ValueError(slate_result.get("error") or "failed to fetch decoder slate")
                raw_logo = slate_result.get("raw_logo") or ""
                results[ip] = {
                    "ok": True,
                    "ip": ip,
                    "target": target,
                    "mode": slate_result.get("mode") or "off",
                    "logo": slate_result.get("logo") or "Not used",
                    "logos": logo_result.get("logos") or [],
                    "logo_options": _slate_logo_options(logo_result.get("logos") or [], raw_logo),
                }
            else:
                results[ip] = {"ok": False, "ip": ip, "target": target, "error": "unit role is not encoder or decoder"}
        except Exception as e:
            results[ip] = {"ok": False, "ip": ip, "target": target, "error": str(e)}

    return jsonify({"ok": True, "results": results})

@app.route("/api/slate_settings", methods=["POST"])
def api_slate_settings():
    data = request.get_json(silent=True) or {}
    ips = data.get("ips") or []
    if isinstance(ips, str):
        ips = [ips]
    ips = [str(ip).strip() for ip in ips if str(ip).strip()]
    mode = (data.get("mode") or "off").strip()
    logo = (data.get("logo") or "").strip()
    if mode not in ("off", "auto", "manual"):
        return jsonify({"ok": False, "error": "mode must be off, auto, or manual"}), 400
    if logo == "Not used":
        mode = "off"
    if not ips:
        return jsonify({"ok": False, "error": "ips required"}), 400

    cache_map = {d.get("ip"): d for d in (_load_cache() or []) if d.get("ip")}
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    results = {}

    def job(ip):
        user, preferred_pwd, device = _device_credentials(ip, cache_map)
        role = (device.get("type") or device.get("role") or "").strip().lower()
        if "encoder" in role:
            res = _ws_set_encoder_slate_settings(ip, user, preferred_pwd, ws_port, ws_path, timeout, mode, logo)
            return {"ip": ip, "target": "encoder", **res}
        if "decoder" in role:
            res = _ws_set_decoder_slate_settings(ip, user, preferred_pwd, ws_port, ws_path, timeout, mode, logo)
            return {"ip": ip, "target": "decoder", **res}
        return {"ip": ip, "target": "unknown", "ok": False, "error": "unit role is not encoder or decoder"}

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(ips)))) as ex:
        futs = {ex.submit(job, ip): ip for ip in ips}
        for fut in as_completed(futs):
            ip = futs[fut]
            try:
                results[ip] = fut.result()
            except Exception as e:
                results[ip] = {"ip": ip, "ok": False, "error": str(e)}

    return jsonify({"ok": True, "results": results})

@app.route("/api/slate_delete", methods=["POST"])
def api_slate_delete():
    data = request.get_json(silent=True) or {}
    ips = data.get("ips") or []
    if isinstance(ips, str):
        ips = [ips]
    ips = [str(ip).strip() for ip in ips if str(ip).strip()]
    logo = (data.get("logo") or "").strip()
    if not ips:
        return jsonify({"ok": False, "error": "ips required"}), 400
    if not logo or logo == "Not used":
        return jsonify({"ok": False, "error": "select a slate logo to delete"}), 400

    cache_map = {d.get("ip"): d for d in (_load_cache() or []) if d.get("ip")}
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    results = {}

    def job(ip):
        user, preferred_pwd, device = _device_credentials(ip, cache_map)
        role = (device.get("type") or device.get("role") or "").strip().lower()
        try:
            if "encoder" in role:
                status = _ws_get_encoder_encoding_settings(ip, user, preferred_pwd, ws_port, ws_path, timeout=timeout, attempts=1, delay=0)
                encoders = status.get("encoders") or []
                if any(((enc or {}).get("slate") or {}).get("logo") == logo for enc in encoders if isinstance(enc, dict)):
                    unset = _ws_set_encoder_slate_settings(ip, user, preferred_pwd, ws_port, ws_path, timeout, "off", "Not used")
                    if not unset.get("ok"):
                        return {"ip": ip, "target": "encoder", "ok": False, "stage": "unset", "error": unset.get("error") or "failed to clear slate use"}
            elif "decoder" in role:
                status = _ws_get_decoder_slate_settings(ip, user, preferred_pwd, ws_port, ws_path, timeout)
                if status.get("raw_logo") == logo:
                    unset = _ws_set_decoder_slate_settings(ip, user, preferred_pwd, ws_port, ws_path, timeout, "off", "Not used")
                    if not unset.get("ok"):
                        return {"ip": ip, "target": "decoder", "ok": False, "stage": "unset", "error": unset.get("error") or "failed to clear slate use"}
            else:
                return {"ip": ip, "target": "unknown", "ok": False, "error": "unit role is not encoder or decoder"}

            delete_result = _ws_delete_logo(ip, user, preferred_pwd, ws_port, ws_path, timeout, logo)
            if not delete_result.get("ok"):
                return {"ip": ip, "ok": False, "stage": "delete", "error": delete_result.get("error") or "delete_logo failed"}
            logos = _ws_get_logo_library(ip, user, delete_result.get("password") or preferred_pwd, ws_port, ws_path, timeout)
            return {
                "ip": ip,
                "ok": True,
                "stage": "done",
                "logo": logo,
                "logo_options": _slate_logo_options(logos.get("logos") or []),
            }
        except Exception as e:
            return {"ip": ip, "ok": False, "stage": "exception", "error": str(e)}

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(ips)))) as ex:
        futs = {ex.submit(job, ip): ip for ip in ips}
        for fut in as_completed(futs):
            ip = futs[fut]
            try:
                results[ip] = fut.result()
            except Exception as e:
                results[ip] = {"ip": ip, "ok": False, "stage": "exception", "error": str(e)}

    return jsonify({"ok": True, "results": results})

@app.route("/api/slate_upload", methods=["POST"])
def api_slate_upload():
    raw_ips = request.form.get("ips") or "[]"
    try:
        ips = json.loads(raw_ips)
    except Exception:
        ips = raw_ips.split(",")
    ips = [str(ip).strip() for ip in ips if str(ip).strip()]
    logo_name = (request.form.get("name") or "").strip()
    upload_file = request.files.get("file")
    if not ips:
        return jsonify({"ok": False, "error": "ips required"}), 400
    if not logo_name:
        return jsonify({"ok": False, "error": "logo name required"}), 400
    if not re.fullmatch(r"[A-Za-z0-9_. -]+", logo_name):
        return jsonify({"ok": False, "error": "logo name may only contain letters, numbers, space, underscore, hyphen, and period"}), 400
    if not upload_file or not upload_file.filename:
        return jsonify({"ok": False, "error": "file required"}), 400

    suffix = Path(upload_file.filename).suffix or ".png"
    if suffix.lower() not in (".jpg", ".jpeg", ".png"):
        return jsonify({"ok": False, "error": "Slate upload only accepts .jpg, .jpeg, or .png files."}), 400
    tmp_path = None
    try:
        log.info("[SLATE] Upload request: name=%s filename=%s targets=%s", logo_name, upload_file.filename, ips)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = Path(tmp.name)
            upload_file.save(tmp)
        try:
            from PIL import Image
        except Exception:
            return jsonify({"ok": False, "error": "Slate image validation requires Pillow. Install Pillow to validate and convert slate images."}), 400

        allowed_sizes = {(1280, 720), (1920, 1080)}
        with Image.open(tmp_path) as img:
            width, height = img.size
            if (width, height) not in allowed_sizes:
                return jsonify({
                    "ok": False,
                    "error": f"Slate image must be 1280x720 or 1920x1080. Uploaded image is {width}x{height}."
                }), 400
            if tmp_path.suffix.lower() != ".png":
                png_path = tmp_path.with_suffix(".png")
                img.convert("RGBA").save(png_path, "PNG")
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                tmp_path = png_path
                log.info("[SLATE] Converted uploaded slate to PNG: %s", tmp_path)
        log.info("[SLATE] Validated slate image resolution: %sx%s", width, height)

        cache_map = {d.get("ip"): d for d in (_load_cache() or []) if d.get("ip")}
        ws_port = app.config['WS_PORT']
        ws_path = app.config['WS_PATH']
        timeout = app.config['TIMEOUT']
        results = {}

        def job(ip):
            lock = _lock_for_ip(ip)
            if not lock.acquire(blocking=False):
                return {"ip": ip, "ok": False, "stage": "throttle", "error": "another upload in progress"}
            try:
                user, preferred_pwd, _device = _device_credentials(ip, cache_map)
                log.info("[SLATE] %s uploading logo file %s as %s", ip, upload_file.filename, logo_name)
                up = _http_upload_logo(ip, tmp_path, timeout=90.0)
                if not up.get("ok"):
                    log.warning("[SLATE] %s HTTP upload failed: %s", ip, up)
                    return {"ip": ip, "ok": False, "stage": "upload", "error": up.get("error") or "upload failed"}
                uploaded = up.get("uploaded") or ""
                log.info("[SLATE] %s HTTP upload ok: uploaded=%s", ip, uploaded)
                if not uploaded:
                    uploaded = _get_latest_upload(ip, user, preferred_pwd, ws_port, ws_path, timeout)
                    log.info("[SLATE] %s detected latest upload: %s", ip, uploaded)
                add = _ws_add_logo(ip, user, preferred_pwd, ws_port, ws_path, timeout, uploaded, logo_name)
                if not add.get("ok"):
                    log.warning("[SLATE] %s add_logo failed: %s", ip, add)
                    return {"ip": ip, "ok": False, "stage": "add_logo", "error": add.get("error") or "add_logo failed"}
                log.info("[SLATE] %s add_logo ok: %s", ip, logo_name)
                logos = _ws_get_logo_library(ip, user, add.get("password") or preferred_pwd, ws_port, ws_path, timeout)
                return {
                    "ip": ip,
                    "ok": True,
                    "stage": "done",
                    "uploaded": uploaded,
                    "logo": logo_name,
                    "logo_options": _slate_logo_options(logos.get("logos") or [], logo_name),
                }
            finally:
                try:
                    lock.release()
                except Exception:
                    pass

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(ips)))) as ex:
            futs = {ex.submit(job, ip): ip for ip in ips}
            for fut in as_completed(futs):
                ip = futs[fut]
                try:
                    results[ip] = fut.result()
                except Exception as e:
                    results[ip] = {"ip": ip, "ok": False, "stage": "exception", "error": str(e)}
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        log.exception("[SLATE] upload failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

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
                    system_mode = cfg.get("system_mode")
                    if system_mode and system_mode != unit.get("system_mode"):
                        unit["system_mode"] = system_mode
                        unit["codec"] = _codec_label(system_mode)
                        unit["supported_system_modes"] = cfg.get("supported_system_modes") or unit.get("supported_system_modes") or []
                        unit["codec_configurable"] = _is_codec_configurable_model(cfg.get("model") or unit.get("model"))
                        if unit.get("details", {}).get("systeminfo", {}).get("config"):
                            unit["details"]["systeminfo"]["config"]["system_mode"] = system_mode
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
        si_cfg = (((unit_copy.get("details") or {}).get("systeminfo") or {}).get("config") or {})
        system_mode = unit_copy.get("system_mode") or si_cfg.get("system_mode") or ""
        unit_copy["system_mode"] = system_mode
        unit_copy["codec"] = unit_copy.get("codec") or _codec_label(system_mode)
        unit_copy["supported_system_modes"] = unit_copy.get("supported_system_modes") or si_cfg.get("supported_system_modes") or []
        unit_copy["codec_configurable"] = unit_copy.get("codec_configurable") if unit_copy.get("codec_configurable") is not None else _is_codec_configurable_model(unit_copy.get("model"))
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
    units = _units_for_export()
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
    units = _units_for_export()
    return _stream_csv_from_units(units)

def _mask_sensitive(value):
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            if str(key).lower() in ("password", "fallback_password", "pass", "pwd"):
                masked[key] = "***"
            else:
                masked[key] = _mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    return value

def _ts_settings_only(value):
    """Return current settings/status only; omit option lists and raw bulky blocks."""
    option_key_fragments = (
        "options",
        "supported_versions",
        "supported_system_modes",
        "available_sessions",
        "available_inputs",
        "supported_inputs",
    )
    raw_block_keys = {"details", "license", "scan_results"}
    sensitive_keys = {"password", "fallback_password", "pass", "pwd"}
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_text = str(key)
            key_l = key_text.lower()
            if key_l in sensitive_keys:
                out[key] = "***"
                continue
            if key_l in raw_block_keys:
                continue
            if any(fragment in key_l for fragment in option_key_fragments):
                continue
            cleaned = _ts_settings_only(item)
            if cleaned in (None, "", [], {}):
                continue
            out[key] = cleaned
        return out
    if isinstance(value, list):
        cleaned_list = [_ts_settings_only(item) for item in value]
        return [item for item in cleaned_list if item not in (None, "", [], {})]
    return value

@app.route("/api/ts_export", methods=["GET"])
def api_ts_export():
    """Download a troubleshooting JSON bundle of current collected settings."""
    scan_data = _load_scan_results_file() or {}
    cache_units = _load_cache()
    units = _ts_settings_only(_units_for_export())
    payload = {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": _app_version(),
        "note": "Passwords are masked. Option lists and raw scan/detail blocks are omitted; this export contains current collected settings and status only.",
        "config": {
            "username": app.config.get("USERNAME", "admin"),
            "ws_port": app.config.get("WS_PORT", 80),
            "timeout": app.config.get("TIMEOUT", 4.5),
            "concurrency": app.config.get("UPLOAD_CONCURRENCY", 6),
            "firmware_path": app.config.get("FIRMWARE_PATH", ""),
        },
        "summary": {
            "cache_units": len(cache_units),
            "scan_devices": len(scan_data.get("devices") or []),
            "scan_encoders": len(scan_data.get("encoders") or []),
            "scan_decoders": len(scan_data.get("decoders") or []),
        },
        "units": units,
    }
    body = json.dumps(_ts_settings_only(payload), indent=2, sort_keys=True)
    filename_stamp = time.strftime("%Y%m%d-%H%M%S")
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=omnisuite-ts-{filename_stamp}.json"},
    )

def _safe_filename_part(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    text = text.strip(".-_")
    return text or "unit"

def _unit_config_filename(unit: dict) -> str:
    ip = _safe_filename_part((unit or {}).get("ip", "").replace(":", "-"))
    base = _safe_filename_part((unit or {}).get("hostname") or (unit or {}).get("model") or "configuration")
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    return f"{ip}_{base}-configuration_{stamp}.json"

def _ws_export_unit_config(ip: str):
    user, preferred_pwd, device = _device_credentials(ip)
    url = _ws_url(ip, app.config["WS_PORT"], app.config["WS_PATH"])
    last_error = "export_config failed"
    for pwd_try in _password_candidates(preferred_pwd):
        try:
            resp = _ws_send_recv(url, {
                "id": "export_config-method",
                "username": user,
                "password": pwd_try,
                "method": {"export_config": {"name": "current"}},
            }, timeout=max(float(app.config.get("TIMEOUT", 4.5)), 10.0))
            if not resp or resp.get("error"):
                last_error = (resp or {}).get("error_message") or (resp or {}).get("error") or "export_config failed"
                continue
            config = (resp or {}).get("configuration")
            reply = (resp or {}).get("reply")
            if config is None and isinstance(reply, dict):
                config = reply.get("configuration")
            if config is None:
                config = (resp or {}).get("config")
            if config is None:
                last_error = "export_config returned no configuration"
                continue
            return {"ok": True, "ip": ip, "unit": device or {"ip": ip}, "configuration": config, "used_password": pwd_try}
        except Exception as e:
            last_error = str(e)
    return {"ok": False, "ip": ip, "error": last_error}

def _ws_import_unit_config(ip: str, uploaded_file: str):
    user, preferred_pwd, _device = _device_credentials(ip)
    url = _ws_url(ip, app.config["WS_PORT"], app.config["WS_PATH"])
    last_error = "import_config_file failed"
    attempts = []
    for pwd_try in _password_candidates(preferred_pwd):
        for attempt in range(1, 5):
            ws = None
            sent = False
            try:
                sslopt = None
                if url.startswith("wss://") and not app.config['WS_STRICT']:
                    sslopt = {"cert_reqs": ssl.CERT_NONE}
                ws = websocket.create_connection(url, timeout=max(float(app.config.get("TIMEOUT", 4.5)), 15.0), sslopt=sslopt)
                ws.send(json.dumps({
                    "id": "import_config_file-method",
                    "username": user,
                    "password": pwd_try,
                    "method": {"import_config_file": {"name": "current", "file": uploaded_file}},
                }))
                sent = True
                raw = ws.recv()
                try:
                    resp = json.loads(raw)
                except Exception:
                    resp = {"raw": raw}
                if not resp or resp.get("error"):
                    last_error = (resp or {}).get("error_message") or (resp or {}).get("error") or "import_config_file failed"
                    attempts.append({"url": url, "attempt": attempt, "error": str(last_error)})
                    error_text = str(last_error).lower()
                    if "auth" in error_text or "password" in error_text or "unauthorized" in error_text:
                        break
                    time.sleep(0.6 * attempt)
                    continue
                return {"ok": True, "ip": ip, "reply": resp.get("reply") or resp.get("config") or resp, "used_password": pwd_try}
            except Exception as e:
                last_error = str(e)
                attempts.append({"url": url, "attempt": attempt, "error": last_error})
                if sent:
                    return {
                        "ok": True,
                        "ip": ip,
                        "pending": True,
                        "reply": {"message": "import command sent; device closed the connection while applying configuration"},
                        "used_password": pwd_try,
                    }
                time.sleep(0.6 * attempt)
            finally:
                try:
                    if ws is not None:
                        ws.close()
                except Exception:
                    pass
    return {"ok": False, "ip": ip, "error": last_error, "attempts": attempts}

def _set_unit_hostname_direct(ip: str, hostname: str):
    hostname = (hostname or "").strip()
    if not hostname or not re.fullmatch(r"[A-Za-z0-9.-]+", hostname):
        return {"ok": False, "ip": ip, "error": "invalid hostname in imported configuration"}

    user, preferred_pwd, _device = _device_credentials(ip)
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    url = _ws_url(ip, ws_port, ws_path)
    last_error = "hostname update failed"

    for pwd_try in _password_candidates(preferred_pwd):
        try:
            si_resp = _ws_send_recv(url, {
                "id": "systeminfo-get",
                "username": user,
                "password": pwd_try,
                "config_get": "systeminfo"
            }, timeout=min(timeout, 4.0))
            if not si_resp or si_resp.get("error"):
                last_error = (si_resp or {}).get("error") or "systeminfo-get failed"
                continue

            si_cfg = (si_resp or {}).get("config") or {}
            if not isinstance(si_cfg, dict):
                si_cfg = {}
            current = (si_cfg.get("hostname") or "").strip()
            if current == hostname:
                _update_hostname_cache(ip, hostname, pwd_try)
                return {"ok": True, "changed": False, "hostname": hostname}

            set_resp = _ws_send_recv(url, {
                "id": "systeminfo-set",
                "username": user,
                "password": pwd_try,
                "config_set": {
                    "name": "systeminfo",
                    "config": _systeminfo_edit_payload(si_cfg, hostname)
                }
            }, timeout=max(timeout, 6.0))
            if not set_resp or set_resp.get("error"):
                last_error = (set_resp or {}).get("error") or "systeminfo-set failed"
                continue

            verify_resp = _ws_send_recv(url, {
                "id": "systeminfo-get-verify",
                "username": user,
                "password": pwd_try,
                "config_get": "systeminfo"
            }, timeout=min(timeout, 4.0))
            verify_cfg = (verify_resp or {}).get("config") or {}
            verified = (verify_cfg.get("hostname") or "").strip() if isinstance(verify_cfg, dict) else ""
            if verified != hostname:
                last_error = f"verification returned hostname '{verified}'"
                continue

            _update_hostname_cache(ip, hostname, pwd_try)
            return {"ok": True, "changed": True, "hostname": hostname}
        except Exception as e:
            last_error = str(e)

    return {"ok": False, "ip": ip, "error": last_error}

def _config_import_finalizer(ip: str, imported_config: dict):
    sysinfo = (imported_config or {}).get("systeminfo") or {}
    desired_hostname = (sysinfo.get("hostname") or "").strip() if isinstance(sysinfo, dict) else ""
    if not desired_hostname:
        return

    # Legacy units apply most config sections from import, but can ignore hostname.
    # Wait for the import reboot, then restore hostname through the normal systeminfo path.
    for attempt in range(1, 41):
        time.sleep(3)
        try:
            result = _set_unit_hostname_direct(ip, desired_hostname)
            if result.get("ok"):
                log.info("[CONFIG_IMPORT] %s hostname finalizer: %s", ip, result)
                return
            log.info("[CONFIG_IMPORT] %s hostname finalizer attempt %s failed: %s", ip, attempt, result.get("error"))
        except Exception as e:
            log.info("[CONFIG_IMPORT] %s hostname finalizer attempt %s waiting: %s", ip, attempt, e)
    log.warning("[CONFIG_IMPORT] %s hostname finalizer gave up after timeout", ip)

def _config_export_units_for_role(role: str):
    role = (role or "all").strip().lower()
    units = _units_for_export()
    if role in ("encoder", "encoders"):
        return [u for u in units if "encoder" in str(u.get("role") or u.get("type") or "").lower()]
    if role in ("decoder", "decoders"):
        return [u for u in units if "decoder" in str(u.get("role") or u.get("type") or "").lower()]
    return units

@app.route("/api/unit_config/export", methods=["GET"])
def api_unit_config_export():
    ip = (request.args.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400
    result = _ws_export_unit_config(ip)
    if not result.get("ok"):
        return jsonify(result), 502
    body = json.dumps(result.get("configuration"), indent=2, sort_keys=True)
    filename = _unit_config_filename(result.get("unit") or {"ip": ip})
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@app.route("/api/unit_config/export_bulk", methods=["GET"])
def api_unit_config_export_bulk():
    role = (request.args.get("role") or "all").strip().lower()
    ips_arg = (request.args.get("ips") or "").strip()
    if ips_arg:
        wanted = {ip.strip() for ip in ips_arg.split(",") if ip.strip()}
        units = [u for u in _units_for_export() if u.get("ip") in wanted]
    else:
        units = _config_export_units_for_role(role)
    if not units:
        return jsonify({"ok": False, "error": "no units found for export"}), 404

    zip_buffer = io.BytesIO()
    manifest = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "role": role, "results": []}
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for unit in units:
            ip = unit.get("ip")
            if not ip:
                continue
            result = _ws_export_unit_config(ip)
            entry = {"ip": ip, "ok": bool(result.get("ok"))}
            if result.get("ok"):
                filename = _unit_config_filename(result.get("unit") or unit)
                zf.writestr(filename, json.dumps(result.get("configuration"), indent=2, sort_keys=True))
                entry["file"] = filename
            else:
                entry["error"] = result.get("error") or "export failed"
            manifest["results"].append(entry)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))

    zip_buffer.seek(0)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    zip_role = _safe_filename_part(role or "all")
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename=omnisuite-{zip_role}-configs_{stamp}.zip"},
    )

@app.route("/api/unit_config/import", methods=["POST"])
def api_unit_config_import():
    ip = (request.form.get("ip") or "").strip()
    upload_file = request.files.get("file")
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400
    if not upload_file or not upload_file.filename:
        return jsonify({"ok": False, "error": "json file required"}), 400
    if Path(upload_file.filename).suffix.lower() != ".json":
        return jsonify({"ok": False, "error": "config import only accepts .json files"}), 400

    tmp_path = None
    imported_config = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            tmp_path = Path(tmp.name)
            upload_file.save(tmp)
        try:
            with open(tmp_path, "r", encoding="utf-8") as fh:
                imported_config = json.load(fh)
        except Exception as e:
            return jsonify({"ok": False, "error": f"invalid json: {e}"}), 400

        lock = _lock_for_ip(ip)
        if not lock.acquire(blocking=False):
            return jsonify({"ok": False, "ip": ip, "stage": "throttle", "error": "another upload is already in progress for this unit"}), 409
        try:
            up = _http_upload_file(ip, tmp_path, field="Import Config", timeout=90.0)
            if not up.get("ok"):
                return jsonify({"ok": False, "ip": ip, "stage": "upload", "error": up.get("error") or "upload failed"}), 502
            imported = _ws_import_unit_config(ip, up.get("uploaded") or "")
        finally:
            lock.release()
        if not imported.get("ok"):
            imported["stage"] = "import"
            return jsonify(imported), 502
        if isinstance(imported_config, dict):
            threading.Thread(
                target=_config_import_finalizer,
                args=(ip, imported_config),
                daemon=True,
            ).start()
        return jsonify({
            "ok": True,
            "ip": ip,
            "uploaded": up.get("uploaded"),
            "pending": True,
            "reply": imported.get("reply"),
            "finalizer": bool(isinstance(imported_config, dict) and ((imported_config.get("systeminfo") or {}).get("hostname")))
        })
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

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

def _http_upload_firmware(ip: str, file_path: Path, timeout: float=900.0):
    """Upload firmware the same way the unit's own Upgrade page does."""
    field = "Upgrade file"
    last_error = None
    for url in _upload_urls(ip):
        try:
            with open(file_path, "rb") as f:
                files = {field: (file_path.name, f, "application/octet-stream")}
                kwargs = {"timeout": (10.0, timeout)}
                if url.startswith("https://"):
                    kwargs["verify"] = False
                r = requests.post(url, files=files, **kwargs)
            uploaded = (r.text or "").strip().strip('"')
            if 200 <= r.status_code < 300 and uploaded:
                return {"ok": True, "url": url, "field": field, "status": r.status_code, "uploaded": uploaded}
            last_error = {
                "ok": False,
                "url": url,
                "field": field,
                "status": r.status_code,
                "error": uploaded or r.reason or "upload failed",
            }
        except requests.exceptions.Timeout as e:
            return {
                "ok": False,
                "url": url,
                "field": field,
                "stage": "timeout",
                "error": f"upload timed out; device may still be writing the file ({e})",
            }
        except Exception as e:
            last_error = {"ok": False, "url": url, "field": field, "error": str(e)}
    return last_error or {"ok": False, "field": field, "error": "upload failed"}

def _http_upload_file(ip: str, file_path: Path, field: str=None, timeout: float=900.0):
    field_name = field or "Upgrade file"
    last_error = None
    for url in _upload_urls(ip):
        try:
            with open(file_path, "rb") as f:
                files = {field_name: (file_path.name, f, "application/octet-stream")}
                kwargs = {"timeout": (10.0, timeout)}
                if url.startswith("https://"):
                    kwargs["verify"] = False
                r = requests.post(url, files=files, **kwargs)
            uploaded = (r.text or "").strip().strip('"')
            if 200 <= r.status_code < 300 and uploaded:
                return {"ok": True, "url": url, "field": field_name, "status": r.status_code, "uploaded": uploaded}
            last_error = {
                "ok": False,
                "url": url,
                "field": field_name,
                "status": r.status_code,
                "error": uploaded or r.reason or "upload failed",
            }
        except requests.exceptions.Timeout as e:
            return {
                "ok": False,
                "url": url,
                "field": field_name,
                "stage": "timeout",
                "error": f"upload timed out; device may still be writing the file ({e})",
            }
        except Exception as e:
            last_error = {"ok": False, "url": url, "field": field_name, "error": str(e)}
    return last_error or {"ok": False, "field": field_name, "error": "upload failed"}

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
    
    ws = None
    sent = False
    try:
        log.info("[UPGRADE_CMD] Opening WebSocket to: %s", url)
        ws = websocket.create_connection(url, timeout=max(timeout, 8.0), sslopt=sslopt)
        ws.send(json.dumps(payload))
        sent = True
        log.info("[UPGRADE_CMD] Upgrade command sent; waiting briefly for an immediate error")
        try:
            resp = ws.recv()
            try:
                obj = json.loads(resp)
            except Exception:
                obj = {"raw": resp}
            log.info("[UPGRADE_CMD] Upgrade response received: %s", obj)
            if isinstance(obj, dict) and obj.get("error"):
                err = obj.get("error_message") or obj.get("error") or "upgrade command failed"
                log.error("[UPGRADE_CMD] Device returned error: %s", err)
                return {"ok": False, "error": err, "resp": obj}
            return {"ok": True, "resp": obj}
        except Exception as recv_err:
            log.info("[UPGRADE_CMD] No upgrade response after send; treating as expected: %s", recv_err)
            return {"ok": True, "resp": {"warning": "no_ack", "error": str(recv_err)}}
    except Exception as e:
        if sent:
            log.info("[UPGRADE_CMD] Socket failed after send; treating as expected upgrade reboot: %s", e)
            return {"ok": True, "resp": {"warning": "sent_then_closed", "error": str(e)}}
        log.error("[UPGRADE_CMD] Failed before sending upgrade command: %s", e, exc_info=True)
        return {"ok": False, "error": str(e)}
    finally:
        try:
            if ws is not None:
                ws.close()
        except Exception:
            pass

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
        cache_updates = {}
        device_contact_ok = status.get("status") != "disconnected"

        # Query device directly for fresh firmware version (bypass cache which may be stale after upgrade)
        # Only do this if reasonable - don't hammer devices with constant WebSocket queries
        fresh_version = ""
        fresh_system_mode = ""
        fresh_supported_modes = []
        fresh_hostname = ""
        fresh_ntp = ""
        fresh_timezone = ""
        has_fresh_hostname = False
        has_fresh_ntp = False
        has_fresh_timezone = False
        try:
            # Get systeminfo directly from device (fresh, not cached) - use very short timeout
            sysinfo_resp = _ws_send_recv(url, 
                {"id":"systeminfo-get","username":user,"password":pwd,"config_get":"systeminfo"}, 
                timeout=0.5)  # Short timeout - device may be busy
            
            if sysinfo_resp:
                if not sysinfo_resp.get("error"):
                    device_contact_ok = True
                # Try multiple locations where version might be
                sysinfo_cfg = (sysinfo_resp or {}).get("config") or {}
                fresh_version = (sysinfo_cfg.get("firmwareversion") or sysinfo_cfg.get("version") or "").strip()
                fresh_system_mode = (sysinfo_cfg.get("system_mode") or "").strip()
                fresh_supported_modes = sysinfo_cfg.get("supported_system_modes") or []
                if "hostname" in sysinfo_cfg:
                    fresh_hostname = (sysinfo_cfg.get("hostname") or "").strip()
                    has_fresh_hostname = True
                if "ntpserver" in sysinfo_cfg or "ntp_server" in sysinfo_cfg:
                    fresh_ntp = (sysinfo_cfg.get("ntpserver") or sysinfo_cfg.get("ntp_server") or "").strip()
                    has_fresh_ntp = True

                # If not found in config, check other locations
                if not fresh_version:
                    fresh_version = (sysinfo_resp.get("firmwareversion") or sysinfo_resp.get("version") or "").strip()
                if not has_fresh_hostname and "hostname" in sysinfo_resp:
                    fresh_hostname = (sysinfo_resp.get("hostname") or "").strip()
                    has_fresh_hostname = True
                if not has_fresh_ntp and ("ntpserver" in sysinfo_resp or "ntp_server" in sysinfo_resp):
                    fresh_ntp = (sysinfo_resp.get("ntpserver") or sysinfo_resp.get("ntp_server") or "").strip()
                    has_fresh_ntp = True

                if fresh_version:
                    log.info(f"[POLL] {ip} fresh firmware version from device: '{fresh_version}'")
        except Exception as e:
            # Silently skip fresh query on timeout or error - cached version is good enough
            pass
        try:
            timezone_resp = _ws_send_recv(url,
                {"id":"timezone-get","username":user,"password":pwd,"config_get":"timezone"},
                timeout=min(timeout, 1.0))
            if timezone_resp and not timezone_resp.get("error"):
                device_contact_ok = True
                timezone_cfg = (timezone_resp or {}).get("config") or {}
                if "timezone" in timezone_cfg or "active_timezone" in timezone_cfg:
                    fresh_timezone = (timezone_cfg.get("timezone") or timezone_cfg.get("active_timezone") or "").strip()
                    has_fresh_timezone = True
                if not has_fresh_timezone and ("timezone" in timezone_resp or "active_timezone" in timezone_resp):
                    fresh_timezone = (timezone_resp.get("timezone") or timezone_resp.get("active_timezone") or "").strip()
                    has_fresh_timezone = True
        except Exception:
            pass

        # Use fresh version if available, otherwise use cached
        if fresh_version:
            status["fw"] = fresh_version
            status["version"] = fresh_version
            cache_updates["version"] = fresh_version
            cache_updates["firmwareversion"] = fresh_version
            cached_version = (device.get("version") or device.get("firmwareversion") or "").strip()
            if fresh_version != cached_version:
                _update_firmware_cache(ip, fresh_version)
            log.info(f"[POLL] {ip} updated from device: '{fresh_version}'")
        if has_fresh_hostname:
            status["hostname"] = fresh_hostname
            cache_updates["hostname"] = fresh_hostname
        if has_fresh_ntp:
            status["ntpserver"] = fresh_ntp
            status["ntp_server"] = fresh_ntp
            cache_updates["ntpserver"] = fresh_ntp
            cache_updates["ntp_server"] = fresh_ntp
        if has_fresh_timezone:
            status["timezone"] = fresh_timezone
            status["active_timezone"] = fresh_timezone
            cache_updates["timezone"] = fresh_timezone
            cache_updates["active_timezone"] = fresh_timezone
        if fresh_system_mode:
            status["system_mode"] = fresh_system_mode
            status["codec"] = _codec_label(fresh_system_mode)
            status["supported_system_modes"] = fresh_supported_modes if isinstance(fresh_supported_modes, list) else []
            status["codec_configurable"] = _is_codec_configurable_model(status.get("model") or device.get("model"))
            cached_modes = device.get("supported_system_modes") if isinstance(device.get("supported_system_modes"), list) else []
            if fresh_system_mode != (device.get("system_mode") or "").strip() or (fresh_supported_modes and fresh_supported_modes != cached_modes):
                _update_codec_cache(ip, fresh_system_mode, fresh_supported_modes)
        
        log.info(f"[POLL] {ip} final version: '{status.get('fw', '')}'")

        # Fetch link speed via net-get
        try:
            net_resp = _ws_send_recv(url,
                {"id": "net-get", "username": user, "password": pwd, "config_get": "net"},
                timeout=min(timeout, 2.0))
            if net_resp and not net_resp.get("error"):
                device_contact_ok = True
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
                    cache_updates["linkspeed"] = link_speed
                    log.info(f"[POLL] {ip} linkspeed: {link_speed}")
                else:
                    log.info(f"[POLL] {ip} net-get returned no linkspeed field")
            else:
                log.info(f"[POLL] {ip} net-get error or empty response: {net_resp}")
        except Exception as e:
            log.info(f"[POLL] {ip} net-get failed: {e}")

        if cache_updates:
            _update_poll_detail_cache(ip, cache_updates)

        # poll_unit_status can report "disconnected" from a short ping/TCP check
        # even when the device responds to the detailed WebSocket reads above.
        # Treat any successful device response as authoritative reachability so
        # polling does not mark controllable units offline.
        if status.get("status") == "disconnected" and device_contact_ok:
            status["status"] = "connected"

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
                model = device.get("model")
                if not model and HAS_MATRIX:
                    model = (omni_matrix_logic._decoders.get(ip) or {}).get("model")
                
                # Use reasonable timeout for polling - give devices time to respond
                fields = _ws_get_decoder_inputs(ip, user, pwd, ws_port, ws_path, timeout=4, attempts=1, delay=0)
                if not isinstance(fields, dict):
                    fields = {}
                url = _ws_url(ip, ws_port, ws_path)
                try:
                    sysinfo_resp = _ws_send_recv(url, {
                        "id": "systeminfo-get",
                        "username": user,
                        "password": pwd,
                        "config_get": "systeminfo"
                    }, timeout=min(timeout, 2.0))
                    sysinfo_cfg = (sysinfo_resp or {}).get("config") or {}
                    if isinstance(sysinfo_cfg, dict):
                        if "hostname" in sysinfo_cfg:
                            fields["hostname"] = (sysinfo_cfg.get("hostname") or "").strip()
                            fields["host"] = fields["hostname"]
                        version = (sysinfo_cfg.get("firmwareversion") or sysinfo_cfg.get("version") or "").strip()
                        if version:
                            fields["version"] = version
                            fields["firmwareversion"] = version
                            fields["fw"] = version
                except Exception as e:
                    log.debug("[POLL_DECODERS] %s systeminfo refresh failed: %s", ip, e)
                if fields and not _supports_decoder_fs_colorspace(model):
                    fields["fast_switching_colorspace"] = None
                    fields["fast_switching_colorspace_options"] = []
                if fields and any(fields.get(k) is not None for k in ("ip1_addr", "ip1_port", "ip3_addr", "ip3_port", "hostname", "version")):
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
                    u.update({
                        k: v
                        for k, v in dec_fields[u.get("ip")].items()
                        if k in (
                            "hostname", "host", "version", "firmwareversion", "fw",
                            "ip1_addr", "ip1_port", "ip3_addr", "ip3_port",
                            "sap_input_enabled", "input_session", "input_session_options",
                            "hdcp_support_version", "hdcp_supported_versions",
                            "video_input", "audio_input", "video_input_options", "audio_input_options",
                            "stretch_crop_mode", "stretch_crop_mode_options",
                            "resolution", "resolution_options",
                            "framerate", "framerate_options",
                            "fast_switching_enabled", "fast_switching_timeout", "fast_switching_colorspace", "fast_switching_colorspace_options",
                            "video_wall_enabled", "video_wall_unit", "video_wall_unit_options",
                            "video_wall_total_width", "video_wall_total_height",
                            "video_wall_grid_width", "video_wall_grid_height", "video_wall_grid_x", "video_wall_grid_y",
                            "video_wall_width", "video_wall_height", "video_wall_horizontal", "video_wall_vertical",
                            "video_wall_rotation", "video_wall_rotation_options",
                            "video_wall_edge_mode", "video_wall_edge_mode_options",
                            "video_wall_edge_top", "video_wall_edge_bottom", "video_wall_edge_left", "video_wall_edge_right",
                        )
                    })
            _save_cache(units)
            log.info(f"[POLL_DECODERS] Updated {updated_count} decoders, saved to cache")
        except Exception as e:
            log.error(f"[POLL_DECODERS] Failed to save cache: {e}")
    
    return jsonify({"ok": True, "results": results, "updated": updated_count})

@app.route("/api/decoder_input", methods=["POST"])
def api_set_decoder_input():
    """Set decoder hdmi_output1 SAP Input enable and/or session selection."""
    data = request.get_json(silent=True) or {}
    ip = (data.get("decoder") or data.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "decoder ip required"}), 400

    sap_input_enabled = data.get("sap_input_enabled") if "sap_input_enabled" in data else None
    input_session = data.get("input_session") if "input_session" in data else None
    video_input = data.get("video_input") if "video_input" in data else None
    audio_input = data.get("audio_input") if "audio_input" in data else None
    stretch_crop_mode = data.get("stretch_crop_mode") if "stretch_crop_mode" in data else None
    resolution = data.get("resolution") if "resolution" in data else None
    framerate = data.get("framerate") if "framerate" in data else None
    fast_switching_enabled = data.get("fast_switching_enabled") if "fast_switching_enabled" in data else None
    fast_switching_timeout = data.get("fast_switching_timeout") if "fast_switching_timeout" in data else None
    fast_switching_colorspace = data.get("fast_switching_colorspace") if "fast_switching_colorspace" in data else None
    hdcp_support_version = data.get("hdcp_support_version") if "hdcp_support_version" in data else None
    video_wall_enabled = data.get("video_wall_enabled") if "video_wall_enabled" in data else None
    video_wall_unit = data.get("video_wall_unit") if "video_wall_unit" in data else None
    video_wall_total_width = data.get("video_wall_total_width") if "video_wall_total_width" in data else None
    video_wall_total_height = data.get("video_wall_total_height") if "video_wall_total_height" in data else None
    video_wall_width = data.get("video_wall_width") if "video_wall_width" in data else None
    video_wall_height = data.get("video_wall_height") if "video_wall_height" in data else None
    video_wall_horizontal = data.get("video_wall_horizontal") if "video_wall_horizontal" in data else None
    video_wall_vertical = data.get("video_wall_vertical") if "video_wall_vertical" in data else None
    video_wall_grid_width = data.get("video_wall_grid_width") if "video_wall_grid_width" in data else None
    video_wall_grid_height = data.get("video_wall_grid_height") if "video_wall_grid_height" in data else None
    video_wall_grid_x = data.get("video_wall_grid_x") if "video_wall_grid_x" in data else None
    video_wall_grid_y = data.get("video_wall_grid_y") if "video_wall_grid_y" in data else None
    video_wall_rotation = data.get("video_wall_rotation") if "video_wall_rotation" in data else None
    video_wall_edge_mode = data.get("video_wall_edge_mode") if "video_wall_edge_mode" in data else None
    video_wall_edge_top = data.get("video_wall_edge_top") if "video_wall_edge_top" in data else None
    video_wall_edge_bottom = data.get("video_wall_edge_bottom") if "video_wall_edge_bottom" in data else None
    video_wall_edge_left = data.get("video_wall_edge_left") if "video_wall_edge_left" in data else None
    video_wall_edge_right = data.get("video_wall_edge_right") if "video_wall_edge_right" in data else None

    if all(v is None for v in (
        sap_input_enabled,
        input_session,
        video_input,
        audio_input,
        stretch_crop_mode,
        resolution,
        framerate,
        fast_switching_enabled,
        fast_switching_timeout,
        fast_switching_colorspace,
        hdcp_support_version,
        video_wall_enabled,
        video_wall_unit,
        video_wall_total_width,
        video_wall_total_height,
        video_wall_width,
        video_wall_height,
        video_wall_horizontal,
        video_wall_vertical,
        video_wall_grid_width,
        video_wall_grid_height,
        video_wall_grid_x,
        video_wall_grid_y,
        video_wall_rotation,
        video_wall_edge_mode,
        video_wall_edge_top,
        video_wall_edge_bottom,
        video_wall_edge_left,
        video_wall_edge_right,
    )):
        return jsonify({"ok": False, "error": "one or more decoder input fields required"}), 400

    cache_devices = {d.get("ip"): d for d in _load_cache()}
    device = cache_devices.get(ip, {})
    user = device.get("username") or app.config['USERNAME']
    pwd = device.get("password") or app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']

    result = _ws_set_decoder_input_settings(
        ip, user, pwd, ws_port, ws_path, timeout,
        sap_input_enabled=sap_input_enabled,
        input_session=input_session,
        video_input=video_input,
        audio_input=audio_input,
        stretch_crop_mode=stretch_crop_mode,
        resolution=resolution,
        framerate=framerate,
        fast_switching_enabled=fast_switching_enabled,
        fast_switching_timeout=fast_switching_timeout,
        fast_switching_colorspace=fast_switching_colorspace,
        hdcp_support_version=hdcp_support_version,
        video_wall_enabled=video_wall_enabled,
        video_wall_unit=video_wall_unit,
        video_wall_total_width=video_wall_total_width,
        video_wall_total_height=video_wall_total_height,
        video_wall_width=video_wall_width,
        video_wall_height=video_wall_height,
        video_wall_horizontal=video_wall_horizontal,
        video_wall_vertical=video_wall_vertical,
        video_wall_grid_width=video_wall_grid_width,
        video_wall_grid_height=video_wall_grid_height,
        video_wall_grid_x=video_wall_grid_x,
        video_wall_grid_y=video_wall_grid_y,
        video_wall_rotation=video_wall_rotation,
        video_wall_edge_mode=video_wall_edge_mode,
        video_wall_edge_top=video_wall_edge_top,
        video_wall_edge_bottom=video_wall_edge_bottom,
        video_wall_edge_left=video_wall_edge_left,
        video_wall_edge_right=video_wall_edge_right,
    )
    if not result.get("ok"):
        return jsonify(result), 500

    fields = result.get("fields") or {}
    if HAS_MATRIX and ip in omni_matrix_logic._decoders:
        omni_matrix_logic._decoders[ip].update(fields)

    try:
        units = _load_cache() or []
        for u in units:
            if u.get("ip") == ip:
                for key in (
                    "ip1_addr", "ip1_port", "ip3_addr", "ip3_port",
                    "sap_input_enabled", "input_session", "input_session_options",
                    "hdcp_support_version", "hdcp_supported_versions",
                    "video_input", "audio_input", "video_input_options", "audio_input_options",
                    "stretch_crop_mode", "stretch_crop_mode_options",
                    "resolution", "resolution_options",
                    "framerate", "framerate_options",
                    "fast_switching_enabled", "fast_switching_timeout",
                    "fast_switching_colorspace", "fast_switching_colorspace_options",
                    "video_wall_enabled", "video_wall_unit", "video_wall_unit_options",
                    "video_wall_total_width", "video_wall_total_height",
                    "video_wall_grid_width", "video_wall_grid_height",
                    "video_wall_grid_x", "video_wall_grid_y",
                    "video_wall_width", "video_wall_height",
                    "video_wall_horizontal", "video_wall_vertical",
                    "video_wall_rotation", "video_wall_rotation_options",
                    "video_wall_edge_mode", "video_wall_edge_mode_options",
                    "video_wall_edge_top", "video_wall_edge_bottom",
                    "video_wall_edge_left", "video_wall_edge_right",
                ):
                    if key in fields:
                        u[key] = fields.get(key)
                break
        _save_cache(units)
    except Exception as e:
        log.error(f"[DECODER_INPUT] Failed to save cache: {e}")

    return jsonify({"ok": True, "decoder": {"ip": ip, **fields}})


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
        conc = int(data.get("concurrency") or app.config.get('UPLOAD_CONCURRENCY', 2))
        conc = max(1, min(2, conc))

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
                log.info("[UPGRADE] Uploading to %s (field=Upgrade file, file_size=%.1fMB)", ip, file_path.stat().st_size / (1024*1024))
                up = _http_upload_file(ip, file_path, timeout=900.0)
                if not up.get("ok"):
                    return {
                        "ip": ip,
                        "ok": False,
                        "stage": up.get("stage") or "upload",
                        "error": up.get("error") or "upload failed",
                    }

                uploaded_file = up.get("uploaded") or ""
                log.info("[UPGRADE] Upload successful on %s, device returned: %s", ip, uploaded_file)
                time.sleep(2.0)  # Give device more time to finalize file write

                if not uploaded_file:
                    log.error("[UPGRADE] Upload completed but no filename was returned; not sending upgrade command")
                    return {"ip": ip, "ok": False, "stage": "upload", "error": "upload completed but device returned no filename"}

                ws = _ws_upgrade(ip, user, device_pwd, ws_port, ws_path, timeout=timeout, file_path=uploaded_file)
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
                    "paired_devices": usb_cfg.get("paired_devices") or {},
                    "found_count": len(usb_cfg.get("found_devices") or {}),
                }
            except Exception as e:
                log.info("USB query failed for %s: %s", ip, e)
                return None
        
        # Query devices in parallel, then resolve REX peer entries back to the
        # control IPs used by the UI. Firmware may report peer USB IPs or MACs.
        usb_results = []
        with ThreadPoolExecutor(max_workers=min(8, len(units))) as ex:
            futures = {ex.submit(get_usb_info, u): u for u in units}
            for fut in as_completed(futures):
                result = fut.result()
                if not result:
                    continue
                usb_results.append(result)
                
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

        lex_by_control_ip = {l.get("ip"): l.get("ip") for l in lex_units if l.get("ip")}
        lex_by_usb_ip = {l.get("usb_ip"): l.get("ip") for l in lex_units if l.get("usb_ip") and l.get("ip")}
        lex_by_mac = {_norm_usb_mac(l.get("mac")): l.get("ip") for l in lex_units if l.get("mac") and l.get("ip")}
        rex_by_control_ip = {r.get("ip"): r.get("ip") for r in rex_units if r.get("ip")}
        rex_by_usb_ip = {r.get("usb_ip"): r.get("ip") for r in rex_units if r.get("usb_ip") and r.get("ip")}
        rex_by_mac = {_norm_usb_mac(r.get("mac")): r.get("ip") for r in rex_units if r.get("mac") and r.get("ip")}

        def resolve_lex_peer(mac, info):
            info = info if isinstance(info, dict) else {}
            candidates = [
                info.get("host_ipaddress"),
                info.get("control_ipaddress"),
                info.get("ipaddress"),
                info.get("ip"),
            ]
            for candidate in candidates:
                if candidate in lex_by_control_ip:
                    return candidate
                if candidate in lex_by_usb_ip:
                    return lex_by_usb_ip[candidate]
            mac_key = _norm_usb_mac(info.get("macaddress") or mac)
            if mac_key and mac_key in lex_by_mac:
                return lex_by_mac[mac_key]
            return ""

        def resolve_rex_peer(mac, info):
            info = info if isinstance(info, dict) else {}
            candidates = [
                info.get("host_ipaddress"),
                info.get("control_ipaddress"),
                info.get("ipaddress"),
                info.get("ip"),
            ]
            for candidate in candidates:
                if candidate in rex_by_control_ip:
                    return candidate
                if candidate in rex_by_usb_ip:
                    return rex_by_usb_ip[candidate]
            mac_key = _norm_usb_mac(info.get("macaddress") or mac)
            if mac_key and mac_key in rex_by_mac:
                return rex_by_mac[mac_key]
            return ""

        rex_pairings = {}
        for result in usb_results:
            if result.get("role") != "REX":
                continue
            rex_ip = result["ip"]
            active_lex = None
            available_lex = []
            paired_devices = result.get("paired_devices") or {}
            log.info("Device %s paired_devices: %s", rex_ip, paired_devices)
            for mac, info in paired_devices.items():
                peer_ip = resolve_lex_peer(mac, info)
                if not peer_ip:
                    continue
                if isinstance(info, dict) and info.get("linked", False):
                    active_lex = peer_ip
                elif peer_ip not in available_lex:
                    available_lex.append(peer_ip)
            rex_pairings[rex_ip] = {"active": active_lex, "available": available_lex}

        lex_pairings = {}
        for result in usb_results:
            if result.get("role") != "LEX":
                continue
            lex_ip = result["ip"]
            for mac, info in (result.get("paired_devices") or {}).items():
                rex_ip = resolve_rex_peer(mac, info)
                if not rex_ip:
                    continue
                pairing = lex_pairings.setdefault(rex_ip, {"active": None, "available": []})
                if isinstance(info, dict) and info.get("linked", False):
                    pairing["active"] = lex_ip
                elif lex_ip not in pairing["available"]:
                    pairing["available"].append(lex_ip)

        pairings = dict(rex_pairings)
        for rex_ip, lex_pairing in lex_pairings.items():
            current = pairings.get(rex_ip) or {"active": None, "available": []}
            if not current.get("active") and not current.get("available"):
                pairings[rex_ip] = lex_pairing

        pairing_conflicts = {}
        for rex_ip, lex_pairing in lex_pairings.items():
            rex_pairing = rex_pairings.get(rex_ip) or {"active": None, "available": []}
            rex_peers = set(([rex_pairing.get("active")] if rex_pairing.get("active") else []) + (rex_pairing.get("available") or []))
            lex_peers = set(([lex_pairing.get("active")] if lex_pairing.get("active") else []) + (lex_pairing.get("available") or []))
            if rex_peers and lex_peers and rex_peers != lex_peers:
                pairing_conflicts[rex_ip] = {"rex_side": rex_pairing, "lex_side": lex_pairing}
        
        return jsonify({
            "ok": True,
            "lex": lex_units,
            "rex": rex_units,
            "pairings": pairings,
            "pairings_rex_side": rex_pairings,
            "pairings_lex_side": lex_pairings,
            "pairing_conflicts": pairing_conflicts,
        })
    except Exception as e:
        log.exception("usb_state error")
        return jsonify({"ok": False, "error": str(e)}), 500

def _usb_get_config(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float) -> dict:
    url = _ws_url(ip, ws_port, ws_path)
    usb = _ws_send_recv(url, {
        "id": "usb_icron-get",
        "username": user,
        "password": pwd,
        "config_get": "usb_icron",
    }, timeout=timeout)
    return (usb or {}).get("config") or {}

def _same_subnet24(ip_a: str, ip_b: str) -> bool:
    a = str(ip_a or "").strip().split(".")
    b = str(ip_b or "").strip().split(".")
    return len(a) == 4 and len(b) == 4 and a[:3] == b[:3]

def _usb_pairing_from_rex_cfg(rex_cfg: dict, lex_mac: str, lex_ip: str) -> dict:
    lex_mac_key = _norm_usb_mac(lex_mac)
    active = None
    available = []
    for mac, info in (rex_cfg.get("paired_devices") or {}).items():
        info = info if isinstance(info, dict) else {}
        peer_ip = info.get("host_ipaddress") or info.get("control_ipaddress") or info.get("ip") or ""
        if not peer_ip and _norm_usb_mac(info.get("macaddress") or mac) == lex_mac_key:
            peer_ip = lex_ip
        if not peer_ip:
            continue
        if info.get("linked", False):
            active = peer_ip
        elif peer_ip not in available:
            available.append(peer_ip)
    return {"active": active, "available": available}

def _usb_rex_has_lex(rex_cfg: dict, lex_mac: str, lex_ip: str) -> bool:
    lex_mac_key = _norm_usb_mac(lex_mac)
    for mac, info in (rex_cfg.get("paired_devices") or {}).items():
        info = info if isinstance(info, dict) else {}
        if _norm_usb_mac(mac) == lex_mac_key or _norm_usb_mac(info.get("macaddress")) == lex_mac_key:
            return True
        if (info.get("host_ipaddress") or info.get("control_ipaddress") or info.get("ip")) == lex_ip:
            return True
    return False

def _usb_rex_has_active_lex(rex_cfg: dict, lex_mac: str, lex_ip: str) -> bool:
    lex_mac_key = _norm_usb_mac(lex_mac)
    for mac, info in (rex_cfg.get("paired_devices") or {}).items():
        info = info if isinstance(info, dict) else {}
        matched = (
            _norm_usb_mac(mac) == lex_mac_key
            or _norm_usb_mac(info.get("macaddress")) == lex_mac_key
            or (info.get("host_ipaddress") or info.get("control_ipaddress") or info.get("ip")) == lex_ip
        )
        if matched:
            return bool(info.get("linked", False))
    return False

def _usb_rex_has_any_lex(rex_cfg: dict, lex_refs: list[dict]) -> bool:
    return any(_usb_rex_has_lex(rex_cfg, ref.get("mac", ""), ref.get("ip", "")) for ref in lex_refs)

def _usb_cfg_has_peer(device_cfg: dict, peer_mac: str, peer_ip: str) -> bool:
    peer_mac_key = _norm_usb_mac(peer_mac)
    for mac, info in (device_cfg.get("paired_devices") or {}).items():
        info = info if isinstance(info, dict) else {}
        if _norm_usb_mac(mac) == peer_mac_key or _norm_usb_mac(info.get("macaddress")) == peer_mac_key:
            return True
        if (info.get("host_ipaddress") or info.get("control_ipaddress") or info.get("ip")) == peer_ip:
            return True
    return False

def _usb_remove_peer_from_paired_devices(paired_devices: dict, peer_mac: str, peer_ip: str) -> dict:
    peer_mac_key = _norm_usb_mac(peer_mac)
    filtered = {}
    for mac, info in (paired_devices or {}).items():
        info = info if isinstance(info, dict) else info
        info_mac = _norm_usb_mac(info.get("macaddress")) if isinstance(info, dict) else ""
        info_ip = ""
        if isinstance(info, dict):
            info_ip = info.get("host_ipaddress") or info.get("control_ipaddress") or info.get("ip") or info.get("ipaddress") or ""
        if _norm_usb_mac(mac) == peer_mac_key or info_mac == peer_mac_key or info_ip == peer_ip:
            continue
        filtered[mac] = info
    return filtered

def _usb_set_paired_devices(ip: str, paired_devices: dict, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, op_id: str) -> dict:
    url = _ws_url(ip, ws_port, ws_path)
    return _ws_send_recv(url, {
        "id": op_id,
        "username": user,
        "password": pwd,
        "config_set": {
            "name": "usb_icron",
            "config": {"paired_devices": paired_devices},
        },
    }, timeout=timeout) or {}

def _usb_write_peer_membership(ip: str, paired_devices: dict, peer_mac: str, peer_ip: str, should_exist: bool, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, op_id: str) -> tuple[dict, dict]:
    response = {}
    last_cfg = {}
    for attempt in range(1, 4):
        response = _usb_set_paired_devices(ip, paired_devices, user, pwd, ws_port, ws_path, timeout, op_id)
        if response.get("error"):
            return response, last_cfg
        for delay in (0.35, 0.8, 1.4):
            time.sleep(delay)
            last_cfg = _usb_get_config(ip, user, pwd, ws_port, ws_path, max(timeout, 2.5))
            if _usb_cfg_has_peer(last_cfg, peer_mac, peer_ip) == should_exist:
                return response, last_cfg
        log.info("[USB] %s peer membership verify attempt %s did not match yet; retrying write", ip, attempt)
    return response, last_cfg

def _usb_get_cached_usb_units() -> list[dict]:
    all_devices = _load_cache() or []
    usb_models = ["hw-omni-e4521", "hw-omni-d4521", "hw-omni-e4511", "hw-omni-d4511", "4521", "4511"]
    return [
        dev for dev in all_devices
        if dev.get("ip") and any(m in (dev.get("model") or "").lower() for m in usb_models)
    ]

def _usb_get_device_hostname(ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float) -> str:
    try:
        sysinfo = _ws_send_recv(_ws_url(ip, ws_port, ws_path), {
            "id": "systeminfo-get",
            "username": user,
            "password": pwd,
            "config_get": "systeminfo",
        }, timeout=timeout)
        return ((sysinfo or {}).get("config") or {}).get("hostname", "")
    except Exception:
        return ""

def _usb_write_and_verify_rex_pairing(rex_ip: str, payload: dict, lex_mac: str, lex_ip: str, should_exist: bool, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float, require_active: bool = False) -> tuple[dict, dict]:
    rex_url = _ws_url(rex_ip, ws_port, ws_path)
    response = {}
    last_cfg = {}
    for attempt in range(1, 4):
        response = _ws_send_recv(rex_url, payload, timeout=timeout) or {}
        if response.get("error"):
            return response, last_cfg
        for delay in (0.35, 0.8, 1.4):
            time.sleep(delay)
            last_cfg = _usb_get_config(rex_ip, user, pwd, ws_port, ws_path, max(timeout, 2.5))
            if should_exist and require_active:
                if _usb_rex_has_active_lex(last_cfg, lex_mac, lex_ip):
                    return response, last_cfg
            elif _usb_rex_has_lex(last_cfg, lex_mac, lex_ip) == should_exist:
                return response, last_cfg
        log.info("[USB] %s verify attempt %s did not match yet; retrying write", rex_ip, attempt)
    return response, last_cfg

def _usb_write_and_verify_rex_unpairs(rex_ip: str, payload: dict, previous_lex_refs: list[dict], user: str, pwd: str, ws_port: int, ws_path: str, timeout: float) -> tuple[dict, dict]:
    rex_url = _ws_url(rex_ip, ws_port, ws_path)
    response = {}
    last_cfg = {}
    for attempt in range(1, 4):
        response = _ws_send_recv(rex_url, payload, timeout=timeout) or {}
        if response.get("error"):
            return response, last_cfg
        for delay in (0.35, 0.8, 1.4):
            time.sleep(delay)
            last_cfg = _usb_get_config(rex_ip, user, pwd, ws_port, ws_path, max(timeout, 2.5))
            if not _usb_rex_has_any_lex(last_cfg, previous_lex_refs):
                return response, last_cfg
        log.info("[USB] %s unpair verify attempt %s did not match yet; retrying write", rex_ip, attempt)
    return response, last_cfg

def _usb_rex_pair_count_for_lex(lex_mac: str, lex_ip: str, user: str, pwd: str, ws_port: int, ws_path: str, timeout: float) -> tuple[int, list[str]]:
    all_devices = _usb_get_cached_usb_units()
    rex_ips = []
    for dev in all_devices:
        ip = dev.get("ip")
        if ip:
            try:
                cfg = _usb_get_config(ip, user, pwd, ws_port, ws_path, min(timeout, 2.5))
                if (cfg.get("type") or "").upper() == "REX" and _usb_rex_has_lex(cfg, lex_mac, lex_ip):
                    rex_ips.append(ip)
            except Exception as e:
                log.info("[USB] Pair count query skipped %s: %s", ip, e)
    return len(set(rex_ips)), sorted(set(rex_ips), key=lambda value: tuple(int(part) for part in value.split(".") if part.isdigit()))

def _with_usb_route_lock(fn):
    def wrapped(*args, **kwargs):
        with _usb_route_lock:
            return fn(*args, **kwargs)
    wrapped.__name__ = fn.__name__
    return wrapped

@app.route("/api/usb_pair", methods=["POST"])
@_with_usb_route_lock
def api_usb_pair():
    """Pair a LEX to a REX (add to available list or set as active)"""
    data = request.get_json(silent=True) or {}
    rex_ip = data.get("rex")
    lex_ip = data.get("lex")
    make_active = data.get("makeActive", False)
    replace_existing = bool(data.get("replaceExisting", False))
    
    if not rex_ip or not lex_ip:
        return jsonify({"ok": False, "error": "rex and lex IPs required"}), 400
    if not _same_subnet24(rex_ip, lex_ip):
        return jsonify({
            "ok": False,
            "error": f"Cross-subnet USB pairing is not allowed: REX {rex_ip} and LEX {lex_ip} must be on the same /24 subnet",
        }), 400
    
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
        
        log.info("USB pair: LEX %s (MAC: %s) -> REX %s", lex_ip, lex_mac, rex_ip)
        
        # Get current paired devices from REX
        rex_url = _ws_url(rex_ip, ws_port, ws_path)
        rex_usb = _ws_send_recv(rex_url, {
            "id": "usb_icron-get",
            "username": user,
            "password": pwd,
            "config_get": "usb_icron"
        }, timeout=timeout)
        
        rex_cfg = (rex_usb or {}).get("config") or {}
        paired_devices = dict(rex_cfg.get("paired_devices") or {})
        lex_mac_upper = lex_mac.upper()
        rex_mac = (rex_cfg.get("macaddress") or "").upper()
        if not rex_mac:
            return jsonify({"ok": False, "error": "Could not get REX MAC address"}), 500
        rex_hostname = _usb_get_device_hostname(rex_ip, user, pwd, ws_port, ws_path, timeout)
        rex_entry = {
            "host_hostname": rex_hostname or "",
            "host_ipaddress": rex_ip,
            "ipaddress": rex_cfg.get("ipaddress", ""),
            "macaddress": rex_mac,
            "product": rex_cfg.get("product", "USB Over Network"),
            "protocol": rex_cfg.get("protocol", "IP"),
            "revision": rex_cfg.get("revision", ""),
            "type": "REX",
            "vendor": rex_cfg.get("vendor", ""),
            "typeL": "Device end",
            "linked": bool(make_active),
            "paired": True,
        }
        current_rex_has_lex = _usb_rex_has_lex(rex_cfg, lex_mac_upper, lex_ip)
        selected_lex_has_rex = _usb_cfg_has_peer(lex_cfg, rex_mac, rex_ip)
        selected_lex_pair_count = len(lex_cfg.get("paired_devices") or {})
        if not selected_lex_has_rex and selected_lex_pair_count >= 5:
            return jsonify({
                "ok": False,
                "error": f"LEX {lex_ip} is already paired to 5 REX units",
                "paired_count": selected_lex_pair_count,
            }), 409
        existing_other_pairings = []
        previous_lex_refs = []
        for paired_mac, paired_info in paired_devices.items():
            paired_host_ip = ""
            if isinstance(paired_info, dict):
                paired_host_ip = paired_info.get("host_ipaddress") or paired_info.get("control_ipaddress") or paired_info.get("ip") or paired_info.get("ipaddress") or ""
            if str(paired_mac).upper() != lex_mac_upper and paired_host_ip != lex_ip:
                existing_other_pairings.append(paired_host_ip or str(paired_mac))
                previous_lex_refs.append({
                    "ip": paired_host_ip,
                    "mac": paired_info.get("macaddress") if isinstance(paired_info, dict) else paired_mac,
                })
        if existing_other_pairings and not replace_existing:
            return jsonify({
                "ok": False,
                "error": "REX already has paired LEX device(s); unpair existing devices before pairing another.",
                "existing": existing_other_pairings,
            }), 409
        if existing_other_pairings and replace_existing:
            log.info("USB pair: replacing existing REX pairings on %s: %s", rex_ip, existing_other_pairings)
            unpair_payload = {
                "id": "usb_icron-unpair-before-pair",
                "username": user,
                "password": pwd,
                "config_set": {
                    "name": "usb_icron",
                    "config": {
                        "paired_devices": {
                            mac: info for mac, info in paired_devices.items()
                            if _norm_usb_mac(mac) == _norm_usb_mac(lex_mac_upper)
                        }
                    }
                }
            }
            unpair_response, unpaired_cfg = _usb_write_and_verify_rex_unpairs(
                rex_ip, unpair_payload, previous_lex_refs,
                user, pwd, ws_port, ws_path, timeout,
            )
            if unpair_response and unpair_response.get("error"):
                error_msg = unpair_response.get("error", "Unknown error")
                return jsonify({
                    "ok": False,
                    "error": f"Previous LEX unpair failed before pairing new LEX: {error_msg}",
                    "response": unpair_response,
                }), 500
            if _usb_rex_has_any_lex(unpaired_cfg, previous_lex_refs):
                return jsonify({
                    "ok": False,
                    "error": "Previous LEX unpair was sent but REX still reports the old pairing",
                    "response": unpair_response,
                }), 200
            paired_devices = dict(unpaired_cfg.get("paired_devices") or {})

        # Keep the LEX side in sync too. A REX route change must remove this
        # REX from every non-selected LEX before adding it to the selected LEX.
        lex_cleanup_errors = []
        for usb_unit in _usb_get_cached_usb_units():
            unit_ip = usb_unit.get("ip")
            if not unit_ip or unit_ip == lex_ip:
                continue
            try:
                unit_cfg = _usb_get_config(unit_ip, user, pwd, ws_port, ws_path, min(timeout, 2.5))
            except Exception as e:
                log.info("[USB] LEX cleanup read skipped %s: %s", unit_ip, e)
                continue
            if (unit_cfg.get("type") or "").upper() != "LEX":
                continue
            if not _usb_cfg_has_peer(unit_cfg, rex_mac, rex_ip):
                continue
            cleaned = _usb_remove_peer_from_paired_devices(unit_cfg.get("paired_devices") or {}, rex_mac, rex_ip)
            cleanup_response, cleanup_cfg = _usb_write_peer_membership(
                unit_ip, cleaned, rex_mac, rex_ip, False,
                user, pwd, ws_port, ws_path, timeout,
                "usb_icron-unpair-rex-from-old-lex",
            )
            if cleanup_response.get("error") or _usb_cfg_has_peer(cleanup_cfg, rex_mac, rex_ip):
                lex_cleanup_errors.append(unit_ip)
        if lex_cleanup_errors:
            return jsonify({
                "ok": False,
                "error": "Previous LEX unpair was sent but one or more LEX units still report the REX pairing",
                "lex_units": lex_cleanup_errors,
            }), 200

        selected_lex_devices = dict(lex_cfg.get("paired_devices") or {})
        selected_lex_devices = _usb_remove_peer_from_paired_devices(selected_lex_devices, rex_mac, rex_ip)
        selected_lex_devices[rex_mac] = rex_entry
        lex_pair_response, verified_lex_cfg = _usb_write_peer_membership(
            lex_ip, selected_lex_devices, rex_mac, rex_ip, True,
            user, pwd, ws_port, ws_path, timeout,
            "usb_icron-pair-rex-to-lex",
        )
        if lex_pair_response.get("error"):
            return jsonify({
                "ok": False,
                "error": f"Selected LEX pair update failed: {lex_pair_response.get('error')}",
                "response": lex_pair_response,
            }), 500
        if not _usb_cfg_has_peer(verified_lex_cfg, rex_mac, rex_ip):
            return jsonify({
                "ok": False,
                "error": "Selected LEX pair update was sent but LEX did not report the REX pairing",
                "response": lex_pair_response,
            }), 200

        # Add or refresh the requested LEX. When replacing, the device receives
        # only the selected LEX because observed firmware treats paired_devices
        # as a replacement set.
        if make_active:
            for info in paired_devices.values():
                if isinstance(info, dict):
                    info["linked"] = False
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
            "typeL": "Host end",
            "linked": bool(make_active),
            "paired": True,
        }

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

        log.info("Setting USB pairings on REX %s: %s", rex_ip, json.dumps(pairing_payload, indent=2))
        
        response, verified_cfg = _usb_write_and_verify_rex_pairing(
            rex_ip, pairing_payload, lex_mac_upper, lex_ip, True,
            user, pwd, ws_port, ws_path, timeout,
            require_active=bool(make_active),
        )
        
        log.info("Pairing response from REX %s: %s", rex_ip, json.dumps(response, indent=2))
        
        if response and response.get("error"):
            error_msg = response.get("error", "Unknown error")
            return jsonify({"ok": False, "error": error_msg, "response": response}), 500
        if bool(make_active) and not _usb_rex_has_active_lex(verified_cfg, lex_mac_upper, lex_ip):
            return jsonify({
                "ok": False,
                "error": "Pair command was sent but REX did not report the requested LEX as active",
                "response": response,
            }), 200
        if not _usb_rex_has_lex(verified_cfg, lex_mac_upper, lex_ip):
            return jsonify({
                "ok": False,
                "error": "Pair command was sent but REX did not report the requested LEX pairing",
                "response": response,
            }), 200
        return jsonify({
            "ok": True,
            "message": "Pairing successful",
            "response": response,
            "pairing": _usb_pairing_from_rex_cfg(verified_cfg, lex_mac_upper, lex_ip),
        })
        
    except Exception as e:
        log.exception("usb_pair error")
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        log.exception("usb_pair error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_unpair", methods=["POST"])
@_with_usb_route_lock
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
        rex_mac = (rex_cfg.get("macaddress") or "").upper()
        if not rex_mac:
            return jsonify({"ok": False, "error": "Could not get REX MAC address"}), 500
        
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
        
        response, verified_cfg = _usb_write_and_verify_rex_pairing(
            rex_ip, unpair_payload, lex_mac_upper, lex_ip, False,
            user, pwd, ws_port, ws_path, timeout,
        )
        
        log.info("Unpair response from REX %s: %s", rex_ip, json.dumps(response, indent=2))
        
        if response and response.get("error"):
            error_msg = response.get("error", "Unknown error")
            return jsonify({"ok": False, "error": error_msg, "response": response}), 500
        if _usb_rex_has_lex(verified_cfg, lex_mac_upper, lex_ip):
            return jsonify({
                "ok": False,
                "error": "Unpair command was sent but REX still reports the LEX pairing",
                "response": response,
            }), 200

        lex_devices = dict(lex_cfg.get("paired_devices") or {})
        cleaned_lex_devices = _usb_remove_peer_from_paired_devices(lex_devices, rex_mac, rex_ip)
        lex_response, verified_lex_cfg = _usb_write_peer_membership(
            lex_ip, cleaned_lex_devices, rex_mac, rex_ip, False,
            user, pwd, ws_port, ws_path, timeout,
            "usb_icron-unpair-rex-from-lex",
        )
        if lex_response.get("error"):
            return jsonify({
                "ok": False,
                "error": f"LEX unpair update failed: {lex_response.get('error')}",
                "response": lex_response,
            }), 500
        if _usb_cfg_has_peer(verified_lex_cfg, rex_mac, rex_ip):
            return jsonify({
                "ok": False,
                "error": "LEX unpair update was sent but LEX still reports the REX pairing",
                "response": lex_response,
            }), 200
        return jsonify({
            "ok": True,
            "message": "Unpairing successful",
            "response": response,
            "pairing": _usb_pairing_from_rex_cfg(verified_cfg, lex_mac_upper, lex_ip),
        })
        
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
        candidates.extend(p for p in range(preferred_port + 1, preferred_port + 101))
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
