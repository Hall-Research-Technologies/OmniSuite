
# ...existing code...

# All imports below here
import os, sys, threading, urllib.request, webbrowser, logging, time, json, re, subprocess, socket, ssl, csv, tempfile, traceback, platform, io, zipfile, uuid
import copy
import urllib.parse
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory, Response
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress

import websocket
import requests
import omni_usb_extender
import omni_multiview

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
_cache_io_lock = threading.RLock()
_usb_route_lock = threading.RLock()


def _with_usb_route_lock(fn):
    def wrapped(*args, **kwargs):
        with _usb_route_lock:
            return fn(*args, **kwargs)
    wrapped.__name__ = fn.__name__
    return wrapped



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
# Fields that must never reach the log, whatever a caller puts in the body.
_AUDIT_SECRET_FIELDS = {"password", "passwd", "pwd", "new_password", "username",
                        "user", "token", "secret", "fallback_password"}


def _audited(operation):
    """Record one hardware-mutating request: what was asked, of what, and how it
    ended.

    Every route that writes to a device carries this. Before it, a pair and an
    unpair were logged but a reboot, a hostname change, a codec change, a host
    port or filter change and an identify were not, so the only record that a
    device had been reconfigured was the device's own behaviour. The body is
    filtered rather than logged whole: two endpoints accept credentials in the
    request, and a verbatim dump would have written an operator's device
    password into the log file.
    """
    def decorate(view):
        def wrapper(*args, **kwargs):
            body = request.get_json(silent=True) or {}
            detail = {k: v for k, v in body.items()
                      if k.lower() not in _AUDIT_SECRET_FIELDS} if isinstance(body, dict) else {}
            # A GET-shaped operation carries its subject in the query string, so
            # a body-only record said only that something happened, to nothing.
            # Same field filter, so this cannot become a way in for a credential.
            for key, value in request.args.items():
                if key.lower() not in _AUDIT_SECRET_FIELDS:
                    detail.setdefault(key, value)
            log.info("[AUDIT] %s requested: %s", operation, detail)
            try:
                response = view(*args, **kwargs)
            except Exception as exc:
                log.info("[AUDIT] %s raised %s", operation, type(exc).__name__)
                raise
            status = response[1] if isinstance(response, tuple) and len(response) > 1 else 200
            log.info("[AUDIT] %s completed: HTTP %s", operation, status)
            return response
        wrapper.__name__ = view.__name__
        wrapper.__doc__ = view.__doc__
        return wrapper
    return decorate


@app.errorhandler(Exception)
def handle_exception(e):
    # Reported through the logger, not print(): the launcher starts this with no
    # console attached, so a traceback written to stdout was simply lost.
    code = getattr(e, "code", 500)
    if code == 500:
        log.exception("Unhandled exception serving %s", getattr(request, "path", "?"))
    else:
        log.info("%s serving %s: %s", code, getattr(request, "path", "?"), e)
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
# The import block that stood here was a verbatim repeat of the one at the top
# of the file, which already imports a superset of these names. Re-importing
# only re-bound the same modules; it also re-ran the omni_matrix_logic probe,
# so a failure was reported twice and the second, quieter handler won.

# The constants that stood here were a verbatim repeat of the block at the top
# of the file, recomputed from the same environment variables and the same
# __file__ -- nothing between the two reassigns any of them. Like the duplicated
# import block already removed from this spot, the second copy did no work; it
# only ran the data-directory mkdir a second time.
# The address main() actually bound, recorded because PORT is only the first
# candidate: _select_bind_port moves up when 8080 is already in use, so a support
# dump that reported PORT would name a port the application is not listening on.
# Empty until main() runs, which is also what distinguishes a process imported by
# the tests from a running application.
_RUNTIME_BIND = {"host": None, "port": None}
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

_config_io_lock = threading.RLock()


def _save_config(cfg):
    """Save configuration to config.json, atomically.

    This holds the operator's credentials, port, timeout, concurrency and
    firmware path, and was the last writer in the project still truncating its
    target in place: a crash between the truncate and the write left the file
    empty, silently reverting every one of those settings to an environment
    default. Same temp-file-and-rename the cache writers already use.
    """
    config_file = CWD / "config.json"
    tmp = config_file.with_name(f"{config_file.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        with _config_io_lock:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, config_file)
    except Exception as e:
        log.info("config.json save failed: %s", e)
        try:
            os.unlink(tmp)
        except OSError:
            pass

# NOT a second Flask(__name__): constructing the application again here is what
# discarded the error handler registered above, and anything else attached to
# the app between the two. This is the same object, now configured.
# Load config from file first, then apply
_cfg = _load_config()
log.info("Loaded config: username=%s, ws_port=%s, timeout=%s, concurrency=%s, firmware_path=%s", 
         _cfg.get('USERNAME'), _cfg.get('WS_PORT'), _cfg.get('TIMEOUT'), 
         _cfg.get('UPLOAD_CONCURRENCY'), _cfg.get('FIRMWARE_PATH') or '(not set)')
app.config.update({
    'USERNAME': _cfg['USERNAME'],
    'PASSWORD': _cfg['PASSWORD'],
    'FALLBACK_PASSWORD': _cfg['FALLBACK_PASSWORD'],
    'WS_PORT': _cfg['WS_PORT'],
    # The two upload endpoints take a logo image and a config JSON, both small;
    # firmware is read from a local folder and never uploaded. Without a cap
    # Flask buffered a request body of any size before a handler ever saw it.
    # Generous enough that no legitimate upload comes near it.
    'MAX_CONTENT_LENGTH': 64 * 1024 * 1024,
    'WS_PATH': "/wsapp/",
    'TIMEOUT': _cfg['TIMEOUT'],
    'WS_STRICT': os.getenv("OMNI_WS_STRICT","0") in ("1","true","True","YES","yes"),
    'UPLOAD_CONCURRENCY': _cfg['UPLOAD_CONCURRENCY'],
    'FIRMWARE_PATH': _cfg['FIRMWARE_PATH'],
})

# Standalone AT-OMNI-311/324 discovery is deliberately independent of the
# OmniStream WebSocket cache and /api/scan critical path.
_usb_extenders = omni_usb_extender.ExtenderDiscoveryService(CWD / 'usb_extenders.json')

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

# Started by main()/the launcher, not by importing the module: an import used
# to probe every address in whatever cache OMNI_DATA_DIR resolved to, which put
# a unit-test run on real hardware.
def start_background_startup_tasks():
    """Begin the read-only startup refreshes. Safe to call more than once."""
    global _startup_tasks_started
    if _startup_tasks_started:
        return
    _startup_tasks_started = True

    def _startup_update_check():
        # One bounded check for the whole application, so the four pages read a
        # result that already exists instead of each asking GitHub. Cached for
        # UPDATE_CHECK_TTL, on a daemon thread, and every failure is swallowed:
        # nothing here may delay startup or matter when there is no Internet.
        try:
            _update_check()
        except Exception as exc:
            log.debug("Startup update check skipped: %s", type(exc).__name__)

    # Recorded as they are started. Nothing here waits for them -- that is the
    # point of a daemon thread -- but they call `_refresh_icron_network_config`
    # and `_usb_live_refresh` through the module globals when they fire, so a
    # caller that is about to rebind those names needs a way to let the work
    # finish first rather than have it arrive afterwards.
    for work in (_startup_update_check, _trigger_startup_verification,
                 _startup_usb_refresh):
        thread = threading.Thread(target=work, daemon=True)
        _startup_threads.append(thread)
        thread.start()


_startup_tasks_started = False
_startup_threads = []
log.info("[STARTUP] Cache verification will run in background")


# How long the USB refresh lets normal discovery settle before it asks. Named
# rather than inline so that a caller with nothing to settle -- a test -- can
# wait for the work instead of waiting for the delay.
STARTUP_USB_SETTLE = 2.0


def _startup_usb_refresh():
    """Bring known USB endpoints back without waiting for a page or a broadcast.

    Standalone endpoints are polled by their last known address with directed
    unicast, which is routable; integrated endpoints are derived from their
    parents. Both are bounded by the same throttles the pages use, and neither
    blocks startup or `/api/scan`.
    """
    time.sleep(STARTUP_USB_SETTLE)       # let discovery settle first
    try:
        _refresh_usb_parent_map()
        _usb_live_refresh(force=True)
        _refresh_icron_network_config(force=True)
        state = _usb_extenders.state().get("devices") or []
        log.info("[STARTUP] USB refresh: %d known endpoint(s), %d online",
                 len(state), len([d for d in state if d.get("online")]))
    except Exception as exc:
        log.info("[STARTUP] USB refresh skipped: %s", type(exc).__name__)


# The USB refresh is started with the rest of the startup work, by main() or the
# launcher -- never as a side effect of importing this module.
log.info("[STARTUP] Known USB endpoints will be refreshed by directed polling")


def await_startup_tasks(timeout=15.0):
    """Let the startup refreshes finish, and report whether they did.

    Startup itself never calls this -- the threads are daemons precisely so that
    nothing waits for them -- and neither does any request path. It is here for
    a caller that must not have the work arrive late, because it is about to
    rebind the globals those threads resolve when they fire. The test suite is
    that caller today; an orderly shutdown would be the other one.
    """
    deadline = time.monotonic() + float(timeout)
    for thread in list(_startup_threads):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(remaining)
    return not any(thread.is_alive() for thread in list(_startup_threads))


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


def _device_identity(unit) -> str:
    """A device's hardware identity, or "" when it has none.

    The address is where a device answered today. This is what it *is*, and it is
    the only key any reconciliation may use to decide whether two records
    describe the same physical unit.
    """
    return re.sub(r"[^0-9a-f]", "", str((unit or {}).get("mac") or "").lower())


def _reconcile_discovered_units(existing, fresh):
    """Fold a fresh discovery into the known inventory, identity first.

    The defect this exists for: the merge iterated the cache before the scan and
    skipped any MAC it had already seen, so a device that moved from one subnet to
    another kept its cached address forever. The operator had to Clear Units and
    rescan to see where the device actually was -- for a unit that had answered,
    correctly, at its new address, seconds earlier.

    Rules, in order:

    * A fresh record wins for everything it establishes. Cached fields it does not
      carry are preserved, so nothing learned outside discovery is lost.
    * A cached record whose MAC was rediscovered is replaced, not kept beside it.
      One physical device, one row.
    * A cached record whose *address* now belongs to a different MAC is dropped.
      That address has moved on, and keeping the row would leave a ghost.
    * Two fresh records claiming one MAC are a conflict, not a coin toss: the
      first response is kept, the collision is logged, and no second row is
      created for a canonical identity.
    """
    fresh_by_mac, fresh_order, conflicts = {}, [], []
    for unit in fresh or []:
        identity = _device_identity(unit)
        if not identity:
            fresh_order.append(unit)          # no identity: it can only be itself
            continue
        if identity in fresh_by_mac:
            # Both addresses answered for one MAC. This is a duplicate MAC, a
            # stale path, or a device caught mid-move; none of them is a reason to
            # show the operator two devices, and none of them may be resolved by
            # picking arbitrarily and saying nothing.
            conflicts.append((identity, fresh_by_mac[identity].get("ip"), unit.get("ip")))
            continue
        fresh_by_mac[identity] = unit
        fresh_order.append(unit)
    for identity, kept, dropped in conflicts:
        log.warning("[SCAN] MAC %s answered at both %s and %s; keeping %s. "
                    "Check for a duplicate MAC or a stale network path.",
                    identity, kept, dropped, kept)

    fresh_addresses = {str(u.get("ip") or "") for u in fresh or [] if u.get("ip")}
    merged, seen_macs, seen_ips = [], set(), set()

    def take(unit):
        identity = _device_identity(unit)
        address = str(unit.get("ip") or "")
        if identity:
            if identity in seen_macs:
                return
            seen_macs.add(identity)
        if address:
            if address in seen_ips:
                return
            seen_ips.add(address)
        merged.append(unit)

    # Fresh first: what answered now decides where a device is.
    for unit in fresh_order:
        identity = _device_identity(unit)
        previous = next((old for old in (existing or []) if _device_identity(old) == identity), None) \
            if identity else None
        if previous is not None:
            moved_from = str(previous.get("ip") or "")
            if moved_from and moved_from != str(unit.get("ip") or ""):
                log.info("[SCAN] %s moved from %s to %s; updating in place",
                         identity, moved_from, unit.get("ip"))
            # Cached values fill gaps the scan left; nothing the scan established
            # is overwritten by the older record.
            take(_merge_unit_records(previous, unit))
        else:
            take(unit)

    for unit in existing or []:
        identity = _device_identity(unit)
        if identity and identity in seen_macs:
            continue                      # already represented by its fresh record
        address = str(unit.get("ip") or "")
        if address and address in fresh_addresses and address not in seen_ips:
            # Something else answers at that address now.
            log.info("[SCAN] dropping cached record for %s: a different device answers there", address)
            continue
        take(unit)
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
    # Identity first: a scan row for an address a known device has since left must
    # not come back as a second device.
    known_macs = {_device_identity(unit): (unit or {}).get("ip") for unit in units if _device_identity(unit)}
    for unit in units:
        ip = (unit or {}).get("ip")
        scan_row = by_ip.get(ip, {})
        # The cache is the fresher store -- background verification writes only
        # there -- so it wins, as it already does in _load_cache. And a scan row
        # left at this address by a different device is not this device.
        if _device_identity(scan_row) and _device_identity(unit) \
                and _device_identity(scan_row) != _device_identity(unit):
            scan_row = {}
        enriched_unit = _merge_unit_records(scan_row, unit)
        enriched.append(enriched_unit)
        if ip:
            seen.add(ip)
    for ip, unit in by_ip.items():
        if ip in seen:
            continue
        identity = _device_identity(unit)
        if identity and identity in known_macs:
            continue                    # the same device, at the address it left
        enriched.append(unit)
    return enriched

# The device inventory columns, defined once. The CSV writers and the workbook
# all render the same inventory, so they must not each carry their own copy of
# this list and drift apart.
INVENTORY_COLUMNS = [
    "IP", "MAC", "Hostname", "Type", "Model", "Version", "SerialNumber",
    "Role", "Codec", "LinkSpeed", "NTP Server", "TimeZone",
    "HDCP Support", "HDCP Negotiated", "HDCP Encrypted", "HDCP Supported Versions",
    "Session 1 Name", "Session 1 Video MC", "Session 1 Video Port", "Session 1 Audio MC", "Session 1 Audio Port",
    "Session 2 Name", "Session 2 Video MC", "Session 2 Video Port", "Session 2 Audio MC", "Session 2 Audio Port",
    "Decoder ip_input1 MC", "Decoder ip_input1 Port", "Decoder ip_input3 MC", "Decoder ip_input3 Port",
]


def _inventory_row(u):
    """One inventory row, in INVENTORY_COLUMNS order."""
    u = u or {}
    return [
        u.get("ip", ""), u.get("mac", ""), u.get("hostname", ""), u.get("type", ""),
        u.get("model", ""), u.get("version", ""), _excel_safe_text(u.get("serialnumber", "")),
        u.get("role", ""), u.get("codec", ""), u.get("linkspeed", ""),
        u.get("ntp_server", ""), u.get("active_timezone") or u.get("timezone", ""),
        u.get("hdcp_support_version", ""), u.get("hdcp_negotiated_version", ""),
        u.get("hdcp_encrypted", ""), _join_supported_versions(u.get("hdcp_supported_versions")),
        u.get("session1_name", ""),
        u.get("session1_video_mcast") or u.get("v_mcast", ""),
        u.get("session1_video_port") or u.get("v_port", ""),
        u.get("session1_audio_mcast") or u.get("a_mcast", ""),
        u.get("session1_audio_port") or u.get("a_port", ""),
        u.get("session2_name", ""), u.get("session2_video_mcast", ""), u.get("session2_video_port", ""),
        u.get("session2_audio_mcast", ""), u.get("session2_audio_port", ""),
        u.get("ip1_addr", ""), u.get("ip1_port", ""), u.get("ip3_addr", ""), u.get("ip3_port", ""),
    ]


def _write_csv_atomic(units, target_path: Path, retries: int = 6, base_delay: float = 0.35) -> bool:
    header = list(INVENTORY_COLUMNS)
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
    header = list(INVENTORY_COLUMNS)
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
                    log.debug("[CACHE] Loaded %d units from units_cache.json (list format)", len(d))
                    cache_units = d
                elif isinstance(d, dict) and "units" in d and d["units"]:
                    log.debug("[CACHE] Loaded %d units from units_cache.json (dict format)", len(d["units"]))
                    cache_units = d["units"]
            if cache_units:
                if SCAN_RESULTS.exists():
                    try:
                        scan_data = _read_scan_results()
                        scan_units = scan_data.get("devices", [])
                        if isinstance(scan_units, list) and scan_units:
                            by_ip = {u.get("ip"): dict(u) for u in scan_units if u.get("ip")}
                            # A device that moved leaves a scan_results row at its
                            # old address. Merging purely by address would hand
                            # that row back as an extra unit, so any address whose
                            # MAC now lives elsewhere in the cache is dropped
                            # first: one physical device, one row.
                            cached_macs = {_device_identity(unit): unit.get("ip")
                                           for unit in cache_units if _device_identity(unit)}
                            for address in [a for a, u in by_ip.items()
                                            if _device_identity(u) in cached_macs
                                            and cached_macs[_device_identity(u)] != a]:
                                log.info("[CACHE] dropping stale scan_results row at %s; that device is now at %s",
                                         address, cached_macs[_device_identity(by_ip[address])])
                                by_ip.pop(address, None)
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
                d = _read_scan_results()
                devices = d.get("devices", [])
                if isinstance(devices, list) and devices:
                    log.debug("[CACHE] Loaded %d units from scan_results.json", len(devices))
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


# scan_results.json is written from several code paths (scan, polling, hostname
# and video-wall updates, USB detail refresh). Those used a plain truncating
# `open(..., "w")`, which is neither atomic nor serialized: a reader could
# observe a half-written file, two writers could interleave, and an interrupted
# write left the file permanently truncated. Route every read and write through
# these helpers so the file is written atomically under one lock and a malformed
# file degrades gracefully instead of propagating corrupt data.
_scan_results_lock = threading.RLock()


def _atomic_tmp_path(target: Path) -> Path:
    """Per-process, per-thread temporary path.

    A shared temporary name lets two writers collide: on Windows os.replace
    fails with a sharing violation while another writer still holds the file.
    """
    return target.with_suffix(f"{target.suffix}.{os.getpid()}-{threading.get_ident()}.tmp")


def _save_scan_results(payload) -> bool:
    """Atomically replace scan_results.json. Never leaves a partial file."""
    try:
        with _scan_results_lock:
            tmp_path = _atomic_tmp_path(SCAN_RESULTS)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, SCAN_RESULTS)
        return True
    except Exception as e:
        log.warning("[SCAN] Failed to save scan results: %s", e)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _quarantine_scan_results(reason):
    """Move a malformed scan_results.json aside so it can be inspected."""
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = SCAN_RESULTS.with_name(f"{SCAN_RESULTS.stem}.corrupt-{stamp}{SCAN_RESULTS.suffix}")
        os.replace(SCAN_RESULTS, target)
        log.warning("[SCAN] scan_results.json was malformed (%s); quarantined as %s", reason, target.name)
        return target
    except Exception as e:
        log.warning("[SCAN] Could not quarantine malformed scan_results.json: %s", e)
        return None


def _read_scan_results(quarantine=True) -> dict:
    """Read scan_results.json, returning {} when it is missing or malformed.

    A corrupt file is quarantined once so the next write starts clean and the
    bad content stays available for diagnosis. Discovery continues either way.
    """
    try:
        with _scan_results_lock:
            if not SCAN_RESULTS.exists():
                return {}
            try:
                with open(SCAN_RESULTS, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                if quarantine:
                    _quarantine_scan_results(e)
                return {}
            return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("[SCAN] Failed to read scan results: %s", e)
        return {}

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
    data = _read_scan_results()
    return data or None


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


_cache_verification_in_progress = False
_cache_last_verified = 0
_cache_startup_verified = False

def _as_completed_tolerant(futures, timeout):
    """`as_completed`, but a timeout ends iteration instead of unwinding.

    The caller keeps whatever finished in time. The TimeoutError used to escape
    the gathering loop, skipping the save below it, so one slow device discarded
    every result the pass had already collected and made the whole sweep a
    no-op.
    """
    try:
        for future in as_completed(futures, timeout=timeout):
            yield future
    except TimeoutError:
        log.info("[VERIFY] gathering timed out; keeping the results already collected")


def _verify_cache_in_background():
    """Verify cached device list by scanning cached IPs with tight timeouts (non-blocking background task)"""
    global _cache_verification_in_progress, _cache_last_verified

    # Claimed here, on the calling thread, before the worker starts. Setting the
    # flag inside the thread left a window in which two requests each launched a
    # full 16-worker sweep of every cached address.
    with _cache_io_lock:
        if _cache_verification_in_progress:
            return
        _cache_verification_in_progress = True

    def release():
        # Claiming before these early returns would otherwise strand the flag:
        # on an empty cache nothing ever clears it, and verification is disabled
        # for the rest of the process.
        global _cache_verification_in_progress
        with _cache_io_lock:
            _cache_verification_in_progress = False

    cached_units = _load_cache()
    if not cached_units:
        release()
        return

    cached_ips = [u.get("ip") for u in cached_units if u.get("ip")]
    if not cached_ips:
        release()
        return
    
    def do_verify():
        global _cache_verification_in_progress, _cache_last_verified
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
                                    _record_usb_association(ip, usb_cfg.get("macaddress"), usb_cfg.get("type"), usb_cfg.get("ipaddress"), cached_unit.get("mac"))
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
                for fut in _as_completed_tolerant(futures, timeout=15):  # Overall timeout
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

@app.route("/matrix/multiview")
def multiview_index():
    idx = ASSET_DIR / "ui" / "matrix" / "multiview.html"
    if idx.exists():
        return send_file(str(idx), mimetype="text/html; charset=utf-8")
    return "<h1>Multiview UI not found</h1>", 404

@app.route("/license")
def license_text():
    """The licence the running build was made from, as plain text.

    Served from the file rather than restated in the UI: a summary in a dialog
    is a convenience, and the terms are whatever LICENSE says.
    """
    for candidate in (ASSET_DIR / "LICENSE", CWD / "LICENSE"):
        try:
            if candidate.exists():
                # Flask adds the charset itself; naming it here too produced
                # "text/plain; charset=utf-8; charset=utf-8".
                return Response(candidate.read_text(encoding="utf-8"),
                                mimetype="text/plain")
        except Exception as exc:
            log.info("Could not read %s: %s", candidate, type(exc).__name__)
    return "LICENSE not found", 404


@app.route("/help")
def user_guide():
    """The User Guide, with the running version substituted into it.

    The guide is a release artefact and has to say which release it describes.
    Leaving that as a hand-typed string in the document meant it was correct
    only until the next version bump, so the file carries a token and the
    version is filled in from the same place Settings reads it.
    """
    idx = ASSET_DIR / "ui" / "user-guide.html"
    if not idx.exists():
        return "<h1>User guide not found</h1>", 404
    try:
        body = idx.read_text(encoding="utf-8").replace(
            "{{OMNI_VERSION}}", _app_version() or "an unknown version")
    except Exception:
        # A guide that renders with the token still in it is better than no
        # guide at all.
        resp = send_file(str(idx), mimetype="text/html; charset=utf-8")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    resp = Response(body, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp

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

# ---------------- LLDP switch topology ----------------
# Derived information, never a poll of its own. Chassis identity is recorded by
# the scan that already talks to each device, and everything below reads what is
# already in the inventory.
_MAC_LIKE = re.compile(r"^[0-9a-f]{12}$")


def _normalize_chassis_id(value):
    """Canonical form of an LLDP Chassis ID, for grouping only.

    A switch reports the same chassis with cosmetic differences -- case, and
    `:`/`-`/`.` separators in a MAC -- and two devices behind one switch must not
    look like two switches because of punctuation. Only a value that is
    recognisably a MAC has its separators stripped; anything else keeps its shape,
    because a chassis ID may equally be a network address or a locally assigned
    string and those must not be mangled into false equality.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    stripped = re.sub(r"[\s:.\-]", "", text)
    if _MAC_LIKE.match(stripped):
        return stripped
    return re.sub(r"\s+", " ", text)


def _lldp_chassis_from_config(lldp):
    """The chassis this device's LLDP neighbours report, if any.

    Mirrors what Device Info already shows in the LLDP hover panel: the first
    neighbour interface that names a chassis. A device with no LLDP information
    contributes nothing -- absence is not a second switch.
    """
    interfaces = ((lldp or {}).get("neighbors") or {}).get("interface") or {}
    # Observed on hardware in two shapes: a mapping of interface name to
    # neighbour, and a list of single-key mappings ([{"lan0": {...}}]). Both are
    # flattened to (name, info) pairs rather than one being treated as malformed.
    pairs = []
    if isinstance(interfaces, dict):
        pairs = list(interfaces.items())
    elif isinstance(interfaces, list):
        for entry in interfaces:
            if isinstance(entry, dict):
                pairs.extend(entry.items())
    for name, info in pairs:
        if not isinstance(info, dict):
            continue
        chassis_map = (info or {}).get("chassis") or {}
        if not isinstance(chassis_map, dict) or not chassis_map:
            continue
        chassis_name = next(iter(chassis_map))
        chassis = chassis_map.get(chassis_name) or {}
        identifier = ((chassis.get("id") or {}) if isinstance(chassis.get("id"), dict) else {}).get("value", "")
        normalized = _normalize_chassis_id(identifier)
        if not normalized:
            continue
        port = (info or {}).get("port") or {}
        return {
            "lldp_chassis_id": normalized,
            "lldp_chassis_id_raw": str(identifier),
            "lldp_chassis_name": str(chassis_name or ""),
            "lldp_chassis_mgmt_ip": str(chassis.get("mgmt-ip") or ""),
            "lldp_local_interface": str(name or ""),
            "lldp_port": str((port.get("descr") or ((port.get("id") or {}) or {}).get("value") or "")),
        }
    return {}


def _lldp_device_topology(units):
    """Per-device topology: what it sees, and what it is actually behind.

    An LLDP neighbour is not necessarily a switch. An OmniStream unit has a
    two-port bridge, so a daisy-chained device reports the *unit in front of it*
    as its chassis -- observed on the bench, where an E4521 named the E4521 it
    hangs off rather than the Catalyst both are behind. Grouping on that would
    invent a second switch.

    So the chain is followed to the switch, and **the path is kept**. Which switch
    a device is under and how it gets there are two different facts, and an
    operator needs both: the second one is where a shared 1 Gb link lives.

    A neighbour is recognised as one of our own devices only by exact identity --
    its advertised management address matching a discovered unit's address, or its
    chassis ID matching a discovered unit's MAC. Never by name similarity: on the
    bench the neighbour's advertised name differed from the unit's hostname by a
    single character, and their MACs differed too, because they are different
    interfaces on one box.

    Traversal is bounded and loop-protected. A chain that runs past the bound,
    loops, or leads to a device that was never discovered leaves the upstream
    switch honestly unknown rather than guessed.
    """
    by_address, mac_of = {}, {}
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        address = str(unit.get("ip") or "").strip()
        if not address:
            continue
        by_address[address] = unit
        own_mac = _normalize_chassis_id(unit.get("mac"))
        if own_mac:
            mac_of[own_mac] = address

    def neighbour_of(unit):
        """The discovered device this unit reports, or None if it is a switch."""
        chassis = _normalize_chassis_id(unit.get("lldp_chassis_id"))
        if not chassis:
            return None
        mgmt = str(unit.get("lldp_chassis_mgmt_ip") or "").strip()
        return by_address.get(mgmt) or by_address.get(mac_of.get(chassis, ""))

    def describe(unit):
        return {
            "ip": _ts_unknown(str(unit.get("ip") or "")),
            "mac": _ts_unknown(unit.get("mac")),
            "hostname": _ts_unknown(unit.get("hostname")),
            "model": _ts_unknown(unit.get("model")),
        }

    topology = {}
    for address, unit in by_address.items():
        immediate = {
            "chassis_id": _normalize_chassis_id(unit.get("lldp_chassis_id")),
            "chassis_id_raw": _ts_unknown(unit.get("lldp_chassis_id_raw")),
            "chassis_name": _ts_unknown(unit.get("lldp_chassis_name")),
            "management_ip": _ts_unknown(unit.get("lldp_chassis_mgmt_ip")),
            "port_id": _ts_unknown(unit.get("lldp_port")),
            "local_interface": _ts_unknown(unit.get("lldp_local_interface")),
        }
        # Stream subscriptions and link speed are already collected by the scan.
        # They are not a bandwidth measurement -- no device reports actual bitrate
        # to OmniSuite -- but they are enough to say how many streams *could*
        # share a path, which is the whole point of the advisory.
        subscriptions = [value for value in (unit.get("ip1_addr"), unit.get("ip3_addr")) if value]
        record = {
            "ip": address,
            "mac": _ts_unknown(unit.get("mac")),
            "hostname": _ts_unknown(unit.get("hostname")),
            "model": _ts_unknown(unit.get("model")),
            "role": _ts_unknown(unit.get("role")),
            "stream_subscriptions": len(subscriptions),
            "link_speed_mbps": _ts_unknown(unit.get("linkspeed")),
            "immediate_neighbor": immediate,
            "is_daisy_chained": False,
            "daisy_chain_via_device": None,
            "daisy_chain_via_ip": None,
            "daisy_chain_via_mac": None,
            "daisy_chain_hops": [],
            "resolved_upstream_switch": None,
            "resolution": "none" if not immediate["chassis_id"] else "direct",
        }
        if not immediate["chassis_id"]:
            topology[address] = record
            continue

        seen, hops, current = {address}, [], unit
        for _hop in range(LLDP_MAX_CHAIN_HOPS + 1):
            neighbour = neighbour_of(current)
            if neighbour is None:
                # The neighbour is not one of ours, so it is the switch.
                record["resolved_upstream_switch"] = {
                    "chassis_id": _normalize_chassis_id(current.get("lldp_chassis_id")),
                    "chassis_id_raw": _ts_unknown(current.get("lldp_chassis_id_raw")),
                    "chassis_name": _ts_unknown(current.get("lldp_chassis_name")),
                    "management_ip": _ts_unknown(current.get("lldp_chassis_mgmt_ip")),
                    # The port is only this device's own port when it is attached
                    # straight to the switch; through a chain it belongs to the
                    # device at the far end, not to this one.
                    "port_id": _ts_unknown(current.get("lldp_port")) if not hops else None,
                }
                record["resolution"] = "direct" if not hops else "resolved_through_device"
                break
            next_address = str(neighbour.get("ip") or "")
            if next_address in seen or not _normalize_chassis_id(neighbour.get("lldp_chassis_id")):
                # A loop, or a neighbour that reports nothing: the upstream switch
                # is genuinely unknown and must not be guessed.
                record["resolution"] = "unresolved"
                break
            seen.add(next_address)
            hops.append(describe(neighbour))
            current = neighbour
        else:
            record["resolution"] = "unresolved"

        if hops:
            first = hops[0]
            record.update({
                "is_daisy_chained": True,
                "daisy_chain_via_device": first.get("hostname") or first.get("model"),
                "daisy_chain_via_ip": first.get("ip"),
                "daisy_chain_via_mac": first.get("mac"),
                "daisy_chain_hops": hops,
            })
        topology[address] = record
    return topology


# A chain deeper than this is either a misreading or a topology nobody should be
# building; either way the traversal stops and says so rather than recursing.
LLDP_MAX_CHAIN_HOPS = 8


def _lldp_upstream_chassis(units):
    """Address -> resolved upstream chassis, for grouping. See the function above."""
    resolved = {}
    for address, record in _lldp_device_topology(units).items():
        switch = record.get("resolved_upstream_switch") or {}
        resolved[address] = {
            "chassis": switch.get("chassis_id", ""),
            "via": [hop["ip"] for hop in record.get("daisy_chain_hops") or []],
            "unresolved": record.get("resolution") == "unresolved",
        }
    return resolved


def _lldp_topology(units):
    """Group the current inventory by the switch each device reports.

    Devices without LLDP are counted separately and never influence the grouping:
    a missing neighbour is missing information, not another switch. Neither is a
    daisy chain -- see :func:`_lldp_upstream_chassis`.

    The dominant switch is the one with strictly the most devices. A tie has no
    dominant switch and therefore no minority -- calling one of two equal groups
    the odd one out would be an invention, so nothing is designated.
    """
    per_device = _lldp_device_topology(units)
    upstream = _lldp_upstream_chassis(units)
    groups = {}
    without = []
    chained = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        address = str(unit.get("ip") or "")
        hop = upstream.get(address) or {}
        chassis = hop.get("chassis", "")
        if hop.get("via"):
            chained.append(address)
        if not chassis:
            without.append(address)
            continue
        direct = _normalize_chassis_id(unit.get("lldp_chassis_id")) == chassis
        entry = groups.setdefault(chassis, {
            "chassis_id": chassis,
            "chassis_id_raw": chassis,
            "chassis_name": "",
            "count": 0,
            "ips": [],
        })
        entry["count"] += 1
        entry["ips"].append(address)
        # Only a device attached straight to the switch can name it; a chained
        # device names the unit in front of it, which is a different thing.
        if direct:
            if unit.get("lldp_chassis_id_raw"):
                entry["chassis_id_raw"] = str(unit["lldp_chassis_id_raw"])
            if not entry["chassis_name"] and unit.get("lldp_chassis_name"):
                entry["chassis_name"] = str(unit["lldp_chassis_name"])
    # Deterministic ordering: biggest group first, then by chassis id, so the UI
    # never depends on dict insertion order.
    ordered = sorted(groups.values(), key=lambda g: (-g["count"], g["chassis_id"]))
    for group in ordered:
        group["ips"] = sorted(group["ips"], key=_ip_sort_key)
    dominant, tied = "", False
    if len(ordered) > 1:
        if ordered[0]["count"] > ordered[1]["count"]:
            dominant = ordered[0]["chassis_id"]
        else:
            tied = True
    elif ordered:
        dominant = ordered[0]["chassis_id"]
    minority = []
    if dominant and len(ordered) > 1:
        minority = sorted((address for group in ordered if group["chassis_id"] != dominant
                           for address in group["ips"]), key=_ip_sort_key)
    return {
        "switch_count": len(ordered),
        "groups": ordered,
        "dominant_chassis_id": dominant,
        "tied": tied,
        "minority_ips": minority,
        "devices_with_lldp": sum(group["count"] for group in ordered),
        "devices_without_lldp": len(without),
        "devices_daisy_chained": sorted(chained, key=_ip_sort_key),
        "multi_switch": len(ordered) > 1,
        # Resolving the switch is grouping. This is the physical path, kept
        # beside it, because a shared upstream link is the operator's problem
        # even when the grouping is perfectly tidy.
        "daisy_chained": bool(chained),
        # A daisy-chained receiver pulling more than one stream is the case the
        # advisory exists for: several streams over one shared upstream link.
        # OmniSuite has no authoritative bitrate from any device, so this counts
        # subscriptions and says nothing about actual utilisation.
        "daisy_chained_multi_stream": sorted(
            [address for address in chained
             if str((per_device.get(address) or {}).get("role") or "").lower() == "decoder"
             and int((per_device.get(address) or {}).get("stream_subscriptions") or 0) > 1],
            key=_ip_sort_key),
        "bitrate_data_available": False,
        "devices": {address: per_device[address] for address in sorted(per_device, key=_ip_sort_key)},
    }


def _ip_sort_key(address):
    """Sort IPv4 addresses numerically; anything else falls after them, by text."""
    try:
        return (0, int(ipaddress.IPv4Address(str(address).strip())), "")
    except (ipaddress.AddressValueError, ValueError):
        return (1, 0, str(address))


# The acknowledgement belongs to the lifetime of the discovered inventory, not to
# a scan, a subnet, a chassis or a session. It is functional state with its own
# store, so it is never inferred from whether a banner happens to be on screen,
# and it is cleared by exactly one thing: the operator clearing discovered units.
TOPOLOGY_ACK = DATA_DIR / "topology_ack.json"
_topology_ack_lock = threading.RLock()


def _read_topology_ack():
    try:
        with _topology_ack_lock:
            data = json.loads(TOPOLOGY_ACK.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        # A malformed file degrades to "not acknowledged", which shows the warning
        # once more rather than hiding a topology the operator never saw.
        log.info("Topology acknowledgement unreadable (%s); treating as unacknowledged",
                 type(exc).__name__)
        return {}


def _write_topology_ack(state):
    tmp = _atomic_tmp_path(TOPOLOGY_ACK)
    try:
        with _topology_ack_lock:
            TOPOLOGY_ACK.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, TOPOLOGY_ACK)
    except OSError as exc:
        log.info("Could not persist topology acknowledgement: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _daisy_signature(topology):
    """What a daisy-chain acknowledgement covers.

    Identity, not address: a chained device that merely moves subnet is the same
    risk the operator already accepted, and re-notifying for that would be noise.
    """
    devices = (topology or {}).get("devices") or {}
    macs = sorted({_normalize_chassis_id(record.get("mac"))
                   for record in devices.values()
                   if record.get("is_daisy_chained") and record.get("mac")})
    return ",".join(macs)


def _daisy_warning_acknowledged(topology, state=None):
    """True while the operator's dismissal still covers what is on the network.

    An acknowledgement covers the devices that were chained when it was given. A
    chain that disappears, or a chained device that moves, changes nothing: the
    risk was already accepted. A device that is *newly* chained is a new condition
    and is worth saying once -- so the stored signature must still contain
    everything currently chained for the dismissal to hold.
    """
    state = _read_topology_ack() if state is None else state
    if not state.get("daisy_acknowledged"):
        return False
    covered = set(filter(None, str(state.get("daisy_signature") or "").split(",")))
    current = set(filter(None, _daisy_signature(topology).split(",")))
    return current.issubset(covered)


def _topology_ack_reset():
    """Called only where the discovered-device inventory is actually cleared."""
    try:
        with _topology_ack_lock:
            if TOPOLOGY_ACK.exists():
                TOPOLOGY_ACK.unlink()
    except OSError as exc:
        log.info("Could not reset topology acknowledgement: %s", exc)


# ---------------- config & files ----------------
# ---------------- scan interface preference ----------------
# Its own tiny store: the credential config's POST rewrites every field it owns,
# so putting an unrelated preference there would risk clearing credentials, and
# the USB discovery networks live with the USB inventory. Three separate choices,
# three separate stores, none able to clear another.
SCAN_PREFS = DATA_DIR / "scan_preferences.json"
_scan_prefs_lock = threading.RLock()


def _read_scan_preferences():
    try:
        with _scan_prefs_lock:
            data = json.loads(SCAN_PREFS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        log.info("Scan preferences unreadable (%s); starting empty", type(exc).__name__)
        return {}


def _write_scan_preferences(prefs):
    tmp = _atomic_tmp_path(SCAN_PREFS)
    try:
        with _scan_prefs_lock:
            SCAN_PREFS.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(prefs, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, SCAN_PREFS)
    except OSError as exc:
        log.info("Could not persist scan preferences: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass


# UI preferences: appearance only. Deliberately its own store, so resetting how
# the application looks can never reach discovered units, routes, scan settings,
# USB state or a topology acknowledgement.
UI_PREFS = DATA_DIR / "ui_preferences.json"
UI_TEMPLATES = ("classic", "compact", "modern", "workspace")
UI_PRESETS = ("default", "slate", "cool", "ocean", "forest", "warm", "plum", "rose",
              "soft", "contrast")
UI_LIGHT_BACKGROUND_DEFAULT = "#f7f8fb"
# A value that is not a hex colour never reaches a stylesheet: a CSS custom
# property is an injection point, so it is validated rather than escaped.
_UI_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _valid_ui_color(value):
    return isinstance(value, str) and bool(_UI_HEX.match(value.strip()))
_ui_prefs_lock = threading.RLock()


def _read_ui_preferences():
    try:
        with _ui_prefs_lock:
            data = json.loads(UI_PREFS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        log.info("UI preferences unreadable (%s); using defaults", type(exc).__name__)
        return {}


def _write_ui_preferences(prefs):
    tmp = _atomic_tmp_path(UI_PREFS)
    try:
        with _ui_prefs_lock:
            UI_PREFS.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(prefs, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, UI_PREFS)
    except OSError as exc:
        log.info("Could not persist UI preferences: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass


@app.route("/api/ui_preferences", methods=["GET", "POST"])
def api_ui_preferences():
    """The selected UI template, so the choice survives an OmniSuite restart.

    The browser also keeps it, which is what makes the first frame correct; this
    is the authority behind that, and what carries the choice to another browser.
    """
    if request.method == "GET":
        prefs = _read_ui_preferences()
        light = prefs.get("light_background")
        return jsonify({"ok": True,
                        "template": prefs.get("template") if prefs.get("template") in UI_TEMPLATES else "classic",
                        "preset": prefs.get("preset") if prefs.get("preset") in UI_PRESETS else "default",
                        "light_background": light if _valid_ui_color(light) else UI_LIGHT_BACKGROUND_DEFAULT,
                        "templates": list(UI_TEMPLATES), "presets": list(UI_PRESETS)})
    data = request.get_json(silent=True) or {}
    prefs = _read_ui_preferences()
    if "template" in data:
        template = str(data.get("template") or "").strip().lower()
        if template not in UI_TEMPLATES:
            return jsonify({"ok": False, "error": f"template must be one of {', '.join(UI_TEMPLATES)}"}), 400
        prefs["template"] = template
    if "preset" in data:
        preset = str(data.get("preset") or "").strip().lower()
        if preset not in UI_PRESETS:
            return jsonify({"ok": False, "error": f"preset must be one of {', '.join(UI_PRESETS)}"}), 400
        prefs["preset"] = preset
    if "light_background" in data:
        light = str(data.get("light_background") or "").strip()
        if not _valid_ui_color(light):
            return jsonify({"ok": False,
                            "error": "light_background must be a hex colour such as #eef2f5"}), 400
        prefs["light_background"] = light.lower()
    # A previous build stored per-role colour overrides. Those controls are gone,
    # so the field is dropped rather than left to keep tinting the UI from a
    # control nobody can see. Everything else in the file is preserved.
    prefs.pop("colors", None)
    _write_ui_preferences(prefs)
    return jsonify({"ok": True, "template": prefs.get("template", "classic"),
                    "preset": prefs.get("preset", "default"),
                    "light_background": prefs.get("light_background", UI_LIGHT_BACKGROUND_DEFAULT)})


@app.route("/api/ui_preferences/reset", methods=["POST"])
def api_ui_preferences_reset():
    """Restore the default look. Appearance only -- nothing operational."""
    try:
        with _ui_prefs_lock:
            if UI_PREFS.exists():
                UI_PREFS.unlink()
    except OSError as exc:
        log.info("Could not reset UI preferences: %s", exc)
    return jsonify({"ok": True, "template": "classic", "preset": "default",
                    "light_background": UI_LIGHT_BACKGROUND_DEFAULT})


# ---------------- first-run notices ----------------
# An acknowledgement is functional state with its own store, so this one lives in
# neither of the stores that already exist and could plausibly have held it:
# UI_PREFS is deleted wholesale by Reset Appearance, and the device cache is
# emptied by Clear Units. Either would have re-shown a first-run notice to an
# operator who had already read and dismissed it, which is exactly the defect a
# separate store prevents. Same shape as TOPOLOGY_ACK and SCAN_PREFS: one small
# file, one lock, an atomic replace, so an interrupted write can never leave a
# half-acknowledged state behind.
NOTICE_ACK = DATA_DIR / "notice_ack.json"
# Every notice this store may record. An unknown name is refused rather than
# written, so a typo on a page cannot silently create a notice nothing displays.
NOTICE_IDS = ("network_access",)
# The release the operator has already been alerted about. Stored here rather
# than in UI_PREFS because Reset Appearance deletes that file wholesale, and
# having the icon start flashing again because someone changed a colour would be
# nonsense. Clear Units does not touch this file either.
UPDATE_ACK_KEY = "acknowledged_update"
_notice_ack_lock = threading.RLock()


def _read_notice_ack():
    try:
        with _notice_ack_lock:
            data = json.loads(NOTICE_ACK.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        # Degrades to "not acknowledged", which shows the notice once more rather
        # than silently hiding advice the operator may never have seen.
        log.info("Notice acknowledgements unreadable (%s); treating as unacknowledged",
                 type(exc).__name__)
        return {}


def _write_notice_ack(state):
    tmp = _atomic_tmp_path(NOTICE_ACK)
    try:
        with _notice_ack_lock:
            NOTICE_ACK.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, NOTICE_ACK)
    except OSError as exc:
        log.info("Could not persist notice acknowledgement: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _notice_acknowledged(notice_id):
    entry = _read_notice_ack().get(notice_id)
    return bool(isinstance(entry, dict) and entry.get("acknowledged"))


def _acknowledged_update_version():
    """The release whose alert the operator has already dismissed, or ""."""
    entry = _read_notice_ack().get(UPDATE_ACK_KEY)
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("version") or "").strip()


def _update_alert_state(result):
    """Whether the Settings icon should draw attention, and to what.

    Separate from `status` on purpose: the alert is a one-time visual, while the
    status is a standing fact. Settings goes on saying an update is available
    for as long as one is; only the flashing stops once it has been seen.
    """
    latest = str((result or {}).get("latest_version") or "").strip()
    if (result or {}).get("status") != UPDATE_AVAILABLE or not latest:
        return {"alert": False, "acknowledged_version": _acknowledged_update_version()}
    acknowledged = _acknowledged_update_version()
    # Compared by version, not by a flag: a NEWER release than the one already
    # dismissed must alert again, and re-checking and finding the same one must
    # not.
    seen = bool(acknowledged) and not _version_is_newer(latest, acknowledged)
    return {"alert": not seen, "acknowledged_version": acknowledged}


@app.route("/api/update_ack", methods=["POST"])
def api_update_ack():
    """Record that the operator has seen the alert for a particular release.

    Opening Settings is the acknowledgement. It silences the icon for that
    version only -- a later release re-arms it -- and changes nothing about what
    Settings reports.
    """
    data = request.get_json(silent=True) or {}
    version = str(data.get("version") or "").strip()
    if _version_tuple(version) is None:
        return jsonify({"ok": False, "error": "a release version is required"}), 400
    with _notice_ack_lock:
        state = _read_notice_ack()
        previous = state.get(UPDATE_ACK_KEY)
        previous_version = previous.get("version") if isinstance(previous, dict) else ""
        # Never move the mark backwards: acknowledging an older alert must not
        # re-arm one for a newer release that has already been dismissed.
        if previous_version and not _version_is_newer(version, previous_version):
            return jsonify({"ok": True, "version": previous_version, "acknowledged": True})
        state[UPDATE_ACK_KEY] = {"version": version,
                                 "acknowledged_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write_notice_ack(state)
    return jsonify({"ok": True, "version": version, "acknowledged": True})


@app.route("/api/notices", methods=["GET"])
def api_notices():
    """Which one-time notices this installation has already been shown.

    Server side on purpose. The notice is shown once for the installation, not
    once per page and not once per browser: all four pages load ui/settings.js,
    so a localStorage flag would have re-asked on every page that had not set it
    yet, and again in any other browser or profile.
    """
    state = _read_notice_ack()
    notices = {}
    for notice_id in NOTICE_IDS:
        entry = state.get(notice_id)
        entry = entry if isinstance(entry, dict) else {}
        notices[notice_id] = {"acknowledged": bool(entry.get("acknowledged")),
                              "acknowledged_at": entry.get("acknowledged_at")}
    return jsonify({"ok": True, "notices": notices})


@app.route("/api/notices/ack", methods=["POST"])
def api_notices_ack():
    """Record that the operator has seen a one-time notice.

    The read-modify-write is held under one lock for its whole duration, so two
    notices acknowledged at once cannot each apply their change to their own copy
    and have the last save discard the other.
    """
    data = request.get_json(silent=True) or {}
    notice_id = str(data.get("notice") or "").strip()
    if notice_id not in NOTICE_IDS:
        return jsonify({"ok": False,
                        "error": f"notice must be one of {', '.join(NOTICE_IDS)}"}), 400
    with _notice_ack_lock:
        state = _read_notice_ack()
        state[notice_id] = {"acknowledged": True,
                            "acknowledged_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write_notice_ack(state)
    return jsonify({"ok": True, "notice": notice_id, "acknowledged": True})


@app.route("/api/scan_preferences", methods=["GET", "POST"])
def api_scan_preferences():
    """Remember which adapter the operator chose for Scan.

    Identity is the adapter's address. A stored address that no longer exists is
    returned as-is so the client can fall back deliberately rather than having a
    dead adapter silently selected for it.
    """
    if request.method == "GET":
        return jsonify({"ok": True, **_read_scan_preferences()})
    data = request.get_json(silent=True) or {}
    interface_ip = str(data.get("interface_ip") or "").strip()
    if interface_ip:
        try:
            ipaddress.IPv4Address(interface_ip)
        except ValueError:
            return jsonify({"ok": False, "error": "interface_ip must be an IPv4 address"}), 400
    prefs = _read_scan_preferences()
    # Only what the request actually carries is changed, so saving the Targets
    # field cannot quietly forget which adapter the operator picked.
    if "interface_ip" in data:
        prefs["interface_ip"] = interface_ip
    # The scan expression is a convenience for restoring the same target text;
    # the address above is what identifies the adapter.
    if "scan_expression" in data:
        prefs["scan_expression"] = str(data.get("scan_expression") or "").strip()
    if "subnet_mask" in data:
        prefs["subnet_mask"] = str(data.get("subnet_mask") or "").strip()
    # The Targets field is the operator's own search space, and it is one
    # definition for both protocols: /api/scan addresses those hosts over the
    # OmniStream WebSocket and USB discovery addresses the same hosts with
    # directed UDP. Remembering it here is what makes a second, USB-only list of
    # networks unnecessary -- it existed only because this one was not persisted.
    usb_ranges_error = None
    if "scan_targets" in data:
        targets_expression = str(data.get("scan_targets") or "").strip()
        prefs["scan_targets"] = targets_expression
        expressions = [e for e in re.split(r"[\s,]+", targets_expression) if e]
        try:
            # Stored in the extender service, which already owns persisted ranges
            # and already feeds them to the bounded directed scanner: one store,
            # one input, no second concept.
            _usb_extenders.set_ranges(expressions)
        except (omni_usb_extender.ProtocolError, ValueError) as exc:
            # The rest of the preference still saves; the operator is told what
            # was rejected rather than finding it silently dropped.
            usb_ranges_error = str(exc)
            log.info("Scan targets not usable as USB discovery ranges: %s", exc)
    _write_scan_preferences(prefs)
    return jsonify({"ok": True, **prefs, "usb_ranges_error": usb_ranges_error})


# ---------------- release update check ----------------
#
# OmniSuite is told where its official releases live so an operator can see that
# a newer one exists. It is deliberately not a dependency: the application runs,
# scans, routes and upgrades with no Internet at all, and every failure here is
# reported as "unable to check" and nothing more.
#
# The request is made server-side rather than from the page. That keeps CORS out
# of it, puts the version comparison and the cache in one place, and means all
# four pages see the same answer instead of each asking separately.

GITHUB_OWNER = "Hall-Research-Technologies"
GITHUB_REPO = "omniSuite"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
GITHUB_LATEST_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# Several hours, not minutes. Nothing about a release changes quickly, and this
# must never look like another polling loop.
UPDATE_CHECK_TTL = 6 * 60 * 60
UPDATE_CHECK_TIMEOUT = 4.0

# The three answers a completed check can give. CHECKING exists only in the UI,
# which shows it while a request it started is in flight; the server never
# returns it, because a server that is answering has already finished checking.
UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
UPDATE_CURRENT = "CURRENT"
UPDATE_UNABLE = "UNABLE_TO_CHECK"

_update_check_lock = threading.RLock()
_update_check_cache = {"checked_at": 0.0, "result": None}


def _version_tuple(text):
    """A comparable tuple from a version or tag, or None if it is not one.

    Numeric, never lexical: "1.0.10" is newer than "1.0.9", which a string
    comparison gets backwards. An optional leading V/v is accepted because the
    project's own tags carry one.
    """
    cleaned = str(text or "").strip()
    if cleaned[:1] in ("V", "v"):
        cleaned = cleaned[1:]
    # A trailing suffix such as -rc1 is not a normal release; the numeric core
    # is compared and the suffix is what marks it as a prerelease elsewhere.
    core = re.match(r"^(\d+(?:\.\d+)*)", cleaned)
    if not core:
        return None
    parts = [int(piece) for piece in core.group(1).split(".") if piece != ""]
    if not parts:
        return None
    # Padded so 1.0 and 1.0.0 compare equal.
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def _version_is_newer(candidate, installed):
    """True when `candidate` is a strictly newer release than `installed`."""
    left, right = _version_tuple(candidate), _version_tuple(installed)
    if left is None or right is None:
        return False
    return left > right


def _is_official_release_url(url):
    """Only this repository's own github.com URLs are ever handed to the UI.

    Without this the endpoint would happily return whatever URL an upstream
    response contained, which is an open redirect wearing a helpful hat.
    """
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "objects.githubusercontent.com"):
        return False
    if host == "github.com":
        expected = f"/{GITHUB_OWNER}/{GITHUB_REPO}/".lower()
        if not (parsed.path or "").lower().startswith(expected):
            return False
    return True


def _platform_asset_key():
    """How this machine's release asset is named, or None when unsure.

    Returning None is a real answer: an asset is only offered when the platform
    is identified confidently, because handing someone the wrong build is worse
    than handing them the releases page.
    """
    system = platform.system().lower()
    machine = (platform.machine() or "").lower()
    # These keys are the platform-and-architecture tails of the published
    # artifact names, which tools/build_release.py owns. They carry the
    # architecture because "windows" alone does not say x86-64, and an asset
    # list containing both a Windows x86-64 zip and a macOS x86-64 zip would
    # otherwise be ambiguous.
    if system == "windows":
        # Only an x86-64 build is published. Windows on ARM runs it under
        # emulation, so it is still the right download for that machine.
        return "windows-x86_64"
    if system == "linux":
        # Ubuntu x86-64 is the only Linux artifact built and tested; nothing is
        # offered to an architecture we do not publish.
        if machine in ("x86_64", "amd64"):
            return "ubuntu-x86_64"
        return None
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        if machine in ("x86_64", "amd64"):
            return "macos-x86_64"
        return None
    return None


def _select_platform_asset(assets):
    """The asset matching this platform, or None."""
    key = _platform_asset_key()
    if not key:
        return None
    for asset in assets or []:
        name = str((asset or {}).get("name") or "")
        url = (asset or {}).get("browser_download_url")
        if not name or not _is_official_release_url(url):
            continue
        stem = name.lower()
        # The suffix is matched on a token boundary so "x86_64" cannot be
        # satisfied by "arm64" or the reverse.
        if re.search(rf"[-_.]{re.escape(key)}\.(zip|tar\.gz|tgz)$", stem):
            return {"name": name, "url": url, "size": (asset or {}).get("size"),
                    "platform": key}
    return None


def _fetch_latest_release():
    """Ask GitHub for the latest normal release. Raises on any failure.

    `/releases/latest` is used rather than the full list because GitHub already
    excludes drafts and prereleases from it, so "the latest normal release" is
    the endpoint's own definition rather than something reimplemented here.
    """
    response = requests.get(
        GITHUB_LATEST_API,
        timeout=UPDATE_CHECK_TIMEOUT,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"OmniSuite/{_app_version()}"},
    )
    if response.status_code == 404:
        raise LookupError("no releases published")
    if response.status_code in (403, 429):
        raise RuntimeError("rate limited")
    response.raise_for_status()
    return response.json()


def _update_check(force=False):
    """The cached answer, refreshed at most once per TTL unless forced.

    No credential is used or wanted: this reads a public endpoint. A failure is
    logged once at INFO and returned as a status, never raised at the caller.
    """
    now = time.time()
    with _update_check_lock:
        cached = _update_check_cache["result"]
        age = now - _update_check_cache["checked_at"]
        if cached and not force and age < UPDATE_CHECK_TTL:
            fresh = dict(cached)
            fresh["stale"] = False
            fresh["age_seconds"] = int(age)
            return fresh

    installed = _app_version()
    result = {
        "ok": True,
        # The UI switches on `status` and never parses the text. The states are
        # deliberately exhaustive, because the one thing that must never happen
        # is a failed request being presented as "you are up to date".
        "status": UPDATE_UNABLE,
        "installed_version": installed,
        "latest_version": None,
        "update_available": False,
        "release_url": GITHUB_RELEASES_URL,
        "asset": None,
        "asset_url": None,
        "checked_at": None,
        "source": "github",
        "stale": False,
        "error_summary": None,
        # Kept so anything still reading the older name keeps working.
        "current_version": installed,
    }
    try:
        payload = _fetch_latest_release()
        tag = (payload or {}).get("tag_name") or (payload or {}).get("name")
        if _version_tuple(tag) is None:
            raise ValueError(f"unrecognised release tag {tag!r}")
        # Drafts and prereleases should already be excluded by the endpoint;
        # honour the flags anyway rather than trusting that silently.
        if (payload or {}).get("draft") or (payload or {}).get("prerelease"):
            raise LookupError("no normal release published")
        html_url = (payload or {}).get("html_url")
        result["latest_version"] = str(tag).strip()
        # Strictly newer, and nothing else. A development build legitimately
        # ahead of the newest published release is CURRENT, not a failure and
        # not an update -- which is the whole reason this is a three-way state
        # and not a boolean.
        result["update_available"] = _version_is_newer(tag, installed)
        if _is_official_release_url(html_url):
            result["release_url"] = html_url
        if result["update_available"]:
            result["asset"] = _select_platform_asset((payload or {}).get("assets"))
            result["asset_url"] = (result["asset"] or {}).get("url")
        result["status"] = UPDATE_AVAILABLE if result["update_available"] else UPDATE_CURRENT
    except Exception as exc:
        # Offline is the normal case for a lot of installations; it is not an
        # error worth a stack trace on every check.
        log.info("Update check unavailable: %s", type(exc).__name__)
        result["ok"] = False
        result["status"] = UPDATE_UNABLE
        # A short name, never a stack trace or a response body: this is shown to
        # an operator and travels in support dumps.
        result["error_summary"] = type(exc).__name__
        result["error"] = type(exc).__name__
        with _update_check_lock:
            previous = _update_check_cache["result"]
        if previous and previous.get("latest_version"):
            # A previous good answer is more useful than nothing; it is marked
            # stale so the UI can say so rather than implying it is current.
            # The last good answer is more useful than nothing, but it is
            # marked stale so the UI can say when it was taken rather than imply
            # it has just been confirmed. Its own status is kept: a remembered
            # CURRENT stays CURRENT-but-stale, and never becomes UNABLE.
            stale = dict(previous)
            stale.update({"ok": False, "stale": True,
                          "error": result["error_summary"],
                          "error_summary": result["error_summary"],
                          "installed_version": installed,
                          "current_version": installed})
            return stale
        return result

    result["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with _update_check_lock:
        _update_check_cache["checked_at"] = time.time()
        _update_check_cache["result"] = dict(result)
    return result


@app.route("/api/update_check", methods=["GET"])
def api_update_check():
    """Report whether a newer official release exists. Never blocks on GitHub.

    Nothing is downloaded or installed here, and no page waits on this: Settings
    renders first and fills this row in when the answer arrives.
    """
    force = str(request.args.get("force", "")).strip().lower() in ("1", "true", "yes")
    result = _update_check(force=force)
    result.update(_update_alert_state(result))
    return jsonify(result)


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
                scan_data = _read_scan_results()
                for key in ("devices", "encoders", "decoders"):
                    arr = scan_data.get(key)
                    if not isinstance(arr, list):
                        continue
                    for device in arr:
                        if (device or {}).get("ip") in changed_ips:
                            device["password"] = target_pwd
                _save_scan_results(scan_data)
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
            scan_data = _read_scan_results()
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
                _save_scan_results(scan_data)
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
        # The firmware folder is the operator's choice, made in Settings. Until
        # they make it there is no folder to list -- falling back to the working
        # directory made a fresh install appear to arrive with firmware in it,
        # chosen by wherever the executable happened to be started from.
        fw_path_str = app.config.get('FIRMWARE_PATH', '').strip()
        if not fw_path_str:
            return jsonify({"ok": True, "files": [{"name": "Select Firmware", "size": 0, "mtime": 0}]})

        fw_dir = Path(fw_path_str)
        if not fw_dir.is_absolute():
            fw_dir = CWD / fw_path_str

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
        # Open the folder the operator chose, and only that one. The guessed
        # defaults this replaced -- ./firmware, ./ui/firmware and one developer
        # machine's absolute path -- meant a fresh install could open a folder
        # nobody had selected, and shipped that developer path in the binary.
        fw_path_str = app.config.get('FIRMWARE_PATH', '').strip()
        if not fw_path_str:
            return jsonify({
                "ok": False,
                "error": "No firmware folder is selected. Choose one in Settings > Firmware Path.",
            }), 400

        fw_dir = Path(fw_path_str)
        if not fw_dir.is_absolute():
            fw_dir = CWD / fw_path_str
        fw_dir = fw_dir.resolve()

        if not (fw_dir.exists() and fw_dir.is_dir()):
            # The operator chose this path, so naming it back is useful rather
            # than a disclosure -- unlike the list of guesses this replaced.
            log.error("[FIRMWARE_FOLDER] Selected firmware folder does not exist")
            return jsonify({
                "ok": False,
                "error": f"The selected firmware folder does not exist: {fw_dir}",
            }), 400

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
                # Argument list, not a shell string: the firmware folder is
                # operator-supplied, and one containing & or " was interpreted by
                # cmd rather than passed to explorer. The sibling macOS and Linux
                # branches below already pass a list.
                subprocess.Popen(["explorer", f"/select,{fw_dir}"],
                                 **_windows_hidden_subprocess_kwargs())
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

@app.route("/api/lldp_topology", methods=["GET"])
def api_lldp_topology():
    """Switch grouping for the current inventory, plus the acknowledgement flag.

    Read-only and derived: it reads the discovered-device store and transmits
    nothing to any device. `/api/cache` carries the same object, so Device Info
    does not call this on its render path; it exists for anything that wants the
    topology without the whole inventory.
    """
    topology = _lldp_topology(_load_cache())
    state = _read_topology_ack()
    return jsonify({"ok": True,
                    "topology": topology,
                    "acknowledged": bool(state.get("acknowledged")),
                    "daisy_acknowledged": _daisy_warning_acknowledged(topology, state)})


@app.route("/api/lldp_topology/acknowledge", methods=["POST"])
def api_lldp_topology_acknowledge():
    """Record that the operator has seen the multi-switch warning.

    Acknowledgement is functional state with its own store. It is never inferred
    from whether the banner is on screen, and nothing but clearing the discovered
    units resets it -- not another scan, not another subnet, not a third switch
    appearing, not a page reload.
    """
    data = request.get_json(silent=True) or {}
    acknowledged = bool(data.get("acknowledged", True))
    # Two distinct advisories, two distinct flags. They describe different risks --
    # an uplink between switches, and a link shared with devices in front of you --
    # so dismissing one must never silence the other.
    scope = str(data.get("scope") or "multi_switch").strip().lower()
    if scope not in ("multi_switch", "daisy_chain"):
        return jsonify({"ok": False, "error": "scope must be multi_switch or daisy_chain"}), 400
    state = _read_topology_ack()
    if scope == "daisy_chain":
        state["daisy_acknowledged"] = acknowledged
        state["daisy_acknowledged_at"] = time.time() if acknowledged else None
        # Recorded at dismissal so a device that becomes chained later is still
        # worth saying once, while an ordinary rescan is not.
        state["daisy_signature"] = _daisy_signature(_lldp_topology(_load_cache())) if acknowledged else ""
    else:
        state["acknowledged"] = acknowledged
        state["acknowledged_at"] = time.time() if acknowledged else None
    _write_topology_ack(state)
    return jsonify({"ok": True, "scope": scope, "acknowledged": acknowledged})


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
            scan_data = _read_scan_results()
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
                _save_scan_results(scan_data)
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
            scan_data = _read_scan_results()
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
                _save_scan_results(scan_data)
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
            scan_data = _read_scan_results()
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
                _save_scan_results(scan_data)
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

    with _cache_io_lock:
        units = _load_cache() or []
        changed = False
        for unit in units:
            changed = apply_updates(unit) or changed
        if changed:
            _save_cache(units)

    if SCAN_RESULTS.exists():
        try:
            # Held across the read and the write for the same reason as the
            # cache above: separately, two concurrent polls each applied their
            # update to their own copy and the later save discarded the earlier.
            with _scan_results_lock:
                scan_data = _read_scan_results()
                scan_changed = False
                for key in ("devices", "encoders", "decoders"):
                    arr = scan_data.get(key)
                    if isinstance(arr, list):
                        for device in arr:
                            scan_changed = apply_updates(device) or scan_changed
                if scan_changed:
                    _save_scan_results(scan_data)
        except Exception as e:
            log.warning("[POLL] Failed to update cached poll details: %s", e)

@app.route("/api/hostname", methods=["POST"])
@_audited("hostname")
def api_hostname():
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    hostname = (data.get("hostname") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400
    if not hostname:
        return jsonify({"ok": False, "error": "hostname required"}), 400
    # DNS bounds the whole name at 253 characters and each label at 63. The
    # pattern accepted any length, so an arbitrarily long name was sent to the
    # device to be rejected -- or accepted -- there.
    if len(hostname) > 253 or any(len(label) > 63 for label in hostname.split(".")):
        return jsonify({"ok": False, "error": "hostname is too long"}), 400
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
@_audited("codec")
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

# ---------------- standalone USB extender discovery API ----------------
# Standalone route transactions (get_route_state / pair_route / unpair_route) are
# deliberately NOT exposed here.  They stay unreachable from the product until the
# AT-OMNI-311/324 hardware validation listed in PROJECT.md is complete.
def _usb_endpoint_ownership(mac):
    """Return (classification, parent) for a discovered USB endpoint MAC.

    Control ownership follows association, not the discovery protocol. An
    endpoint that has not been proven standalone is never given a standalone
    command.
    """
    context = _usb_parent_context()
    parent = context["index"].get(_norm_usb_mac(mac))
    if parent and parent.get("parent_ip"):
        return "INTEGRATED", parent
    return ("STANDALONE" if context["complete"] else "UNCONFIRMED"), None


_USB_EXTENDER_COMMAND_ERRORS = {
    "not_found": "The requested standalone extender is not known.",
    "unconfirmed_endpoint": ("This USB endpoint has not been associated yet. Run a scan from Device Info so its "
                             "parent device can be identified before sending standalone extender commands."),
    "interface_unknown": "The extender has no known network interface. Re-run discovery on the correct interface first.",
    "interface_unavailable": "The selected network interface is unavailable on this computer.",
    "timeout": "The extender did not respond in time.",
    "protocol_error": "The extender returned an invalid protocol response.",
}


def _usb_extender_network(data):
    interface_ip = str((data or {}).get("interface_ip") or "").strip()
    mask = str((data or {}).get("subnet_mask") or "").strip()
    try:
        ipaddress.IPv4Network(f"{interface_ip}/{mask}", strict=False)
    except Exception:
        return None, None
    return interface_ip, mask


# ---- association between UDP-discovered USB endpoints and OmniStream parents ----
# The normal scan already records each USB-capable unit's firmware-reported
# `usb_mac` and `usb_type` in the device cache.  That is the authoritative
# mapping, so correlation is an exact normalized-MAC lookup against it and costs
# no network I/O.  Never correlate on IP proximity, MAC similarity, address
# suffixes, or hostname patterns.
_USB_CAPABLE_MODELS = ("hw-omni-e4521", "hw-omni-d4521", "hw-omni-e4511", "hw-omni-d4511", "4521", "4511")


def _is_usb_capable_unit(unit):
    model = (unit.get("model") or "").lower()
    return any(m in model for m in _USB_CAPABLE_MODELS)


# Runtime USB association learned from the authoritative usb_icron endpoint.
# Keyed by parent device IP so a device that changes USB MAC replaces its own
# entry. Populated wherever the application already reads usb_icron -- the USB
# Matrix build and the background harvest -- so no extra device traffic is
# introduced. This is a cache of observations, never a source of truth on its
# own: every consumer re-derives association from current state.
USB_ASSOCIATIONS = CWD / "usb_associations.json"
_usb_association_lock = threading.RLock()
_USB_ASSOCIATION = {}


def _usb_association_key(parent_mac, parent_ip):
    """Stable identity for a parent device.

    Prefers the hardware MAC, as device identity must not depend on IP alone.
    Falls back to the address only while the MAC is still unknown.
    """
    mac = _norm_usb_mac(parent_mac)
    if len(mac) == 12:
        return f"mac:{mac}"
    parent_ip = str(parent_ip or "").strip()
    return f"ip:{parent_ip}" if parent_ip else ""


def _load_usb_associations():
    """Restore learned associations so they survive restarts and cache rebuilds."""
    try:
        if not USB_ASSOCIATIONS.exists():
            return
        data = json.loads(USB_ASSOCIATIONS.read_text(encoding="utf-8"))
        entries = data.get("associations") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return
        with _usb_association_lock:
            for key, entry in entries.items():
                if isinstance(entry, dict) and len(_norm_usb_mac(entry.get("usb_mac"))) == 12:
                    _USB_ASSOCIATION[key] = entry
        log.info("[USB] Restored %d learned USB association(s)", len(_USB_ASSOCIATION))
    except Exception as e:
        log.warning("[USB] Could not restore USB associations: %s", e)


def _save_usb_associations():
    # The whole write runs under the lock: several harvest threads record
    # associations concurrently, and an unserialized replace collides.
    tmp = _atomic_tmp_path(USB_ASSOCIATIONS)
    try:
        with _usb_association_lock:
            payload = {"associations": {k: dict(v) for k, v in _USB_ASSOCIATION.items()}}
            USB_ASSOCIATIONS.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, USB_ASSOCIATIONS)
    except Exception as e:
        log.info("[USB] Could not persist USB associations: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _record_usb_association(parent_ip, usb_mac, usb_type=None, usb_ip=None, parent_mac=None,
                            icron_revision=None):
    """Record one authoritative usb_icron observation for a parent device.

    Keyed by the parent's hardware MAC when known so the association follows the
    physical device across address changes and cache rebuilds. Persisted so a
    normal scan, a cache clear, or a restart cannot lose it.
    """
    parent_ip = str(parent_ip or "").strip()
    mac = _norm_usb_mac(usb_mac)
    key = _usb_association_key(parent_mac, parent_ip)
    if not key or len(mac) != 12:
        return False
    role = (usb_type or "").strip().upper()
    with _usb_association_lock:
        previous = _USB_ASSOCIATION.get(key) or {}
        entry = {
            "parent_ip": parent_ip or previous.get("parent_ip", ""),
            "parent_mac": _norm_usb_mac(parent_mac) or previous.get("parent_mac", ""),
            "usb_mac": mac,
            "usb_type": role if role in ("LEX", "REX") else previous.get("usb_type", ""),
            "usb_ip": str(usb_ip or previous.get("usb_ip") or "").strip(),
            # The Icron module's own firmware, which every usb_icron reply
            # carries and which used to be discarded here. An endpoint that
            # does not answer the UDP Basic Query has no other source for it,
            # so the field stayed empty and Configure showed N/A.
            #
            # Same precedence as every field above: a fresh known value wins,
            # otherwise the remembered one survives. A read that failed or
            # omitted the field therefore cannot erase a version already
            # verified.
            "icron_revision": str(icron_revision or previous.get("icron_revision") or "").strip(),
            "learned_at": time.time(),
        }
        # Every observation used to rewrite the whole file, with an fsync, under
        # this lock -- measured at 64 writes a minute with two pages open, for a
        # record that differed only by its own timestamp. Persist only when
        # something an operator would care about actually changed.
        significant = lambda record: {k: v for k, v in record.items() if k != "learned_at"}
        changed = significant(entry) != significant(previous)
        _USB_ASSOCIATION[key] = entry
        # Once the hardware MAC is known, drop the provisional address-keyed entry.
        if key.startswith("mac:") and parent_ip:
            changed = _USB_ASSOCIATION.pop(f"ip:{parent_ip}", None) is not None or changed
    if changed:
        _save_usb_associations()
    return True


def _usb_association_for(unit):
    """Learned association for a cached unit, matched by stable identity.

    An address-keyed entry is only accepted when it cannot belong to a different
    physical device, so metadata is never carried over to a unit that merely
    inherited an IP address.
    """
    unit_mac = _norm_usb_mac(unit.get("mac"))
    unit_ip = str(unit.get("ip") or "").strip()
    with _usb_association_lock:
        if len(unit_mac) == 12:
            entry = _USB_ASSOCIATION.get(f"mac:{unit_mac}")
            if entry:
                return dict(entry)
        if unit_ip:
            entry = _USB_ASSOCIATION.get(f"ip:{unit_ip}")
            if entry:
                stored_mac = _norm_usb_mac(entry.get("parent_mac"))
                if not stored_mac or not unit_mac or stored_mac == unit_mac:
                    return dict(entry)
    return {}


def _preserve_usb_association_fields(units):
    """Re-apply learned USB association metadata onto rebuilt unit records.

    A normal scan reports no `usb_mac`/`usb_type`, so a rebuilt record would drop
    association that was learned elsewhere. This restores those additive fields
    from the persisted store, matched by stable device identity. It is pure
    in-memory work with no device traffic, so /api/scan timing is unaffected.
    Authoritative values already present on the record are never overwritten.
    """
    restored = 0
    for unit in units or []:
        if not isinstance(unit, dict) or not _is_usb_capable_unit(unit):
            continue
        entry = _usb_association_for(unit)
        if not entry:
            continue
        if len(_norm_usb_mac(unit.get("usb_mac"))) != 12 and entry.get("usb_mac"):
            unit["usb_mac"] = entry["usb_mac"]
            restored += 1
        if not (unit.get("usb_type") or "").strip() and entry.get("usb_type"):
            unit["usb_type"] = entry["usb_type"]
    if restored:
        log.info("[USB] Preserved %d learned USB association(s) across cache refresh", restored)
    return restored


# Restore learned associations now that the helpers above are defined, so a
# restart starts with the association it already had.
_load_usb_associations()


def _usb_parent_context(devices=None):
    """The single USB endpoint association pipeline.

    Derives, from current runtime state, a map of normalized USB MAC to the
    OmniStream parent that owns it. Both Configure > USB and the USB Matrix
    inventory consume this; neither determines ownership on its own.

    Association is assembled from two data-driven sources, in this order:

    1. ``usb_mac`` / ``usb_type`` recorded on the cached device by the normal
       scan and cache verification.
    2. The runtime usb_icron observations captured wherever the application
       already queries usb_icron (the USB Matrix build and the background
       harvest), keyed by parent device IP.

    Because it is recomputed on every call, parent information that arrives
    later automatically resolves an endpoint that was UNCONFIRMED, with no USB
    rescan required.

    ``pending`` lists USB-capable units that still have no association. While it
    is non-empty an uncorrelated endpoint cannot be proven standalone, so
    classification stays UNCONFIRMED and standalone control is withheld.
    """
    index, pending = {}, []
    try:
        units = devices if devices is not None else _load_cache()
    except Exception as exc:
        log.info("USB parent index unavailable: %s", type(exc).__name__)
        return {"index": index, "pending": pending, "complete": False}
    for unit in units or []:
        parent_ip = str(unit.get("ip") or "").strip()
        # Learned association, matched by stable device identity (MAC first).
        runtime = _usb_association_for(unit)
        usb_mac = _norm_usb_mac(unit.get("usb_mac")) or _norm_usb_mac(runtime.get("usb_mac"))
        role = (unit.get("usb_type") or runtime.get("usb_type") or "").strip().upper()
        if len(usb_mac) != 12:
            if _is_usb_capable_unit(unit) and parent_ip:
                pending.append({"parent_ip": parent_ip,
                                "hostname": unit.get("hostname") or "",
                                "mac": unit.get("mac") or "",
                                "model": (unit.get("model") or "").upper()})
            continue
        index[usb_mac] = {
            "parent_ip": parent_ip,
            # Without this every consumer silently fell back to address-keyed
            # identity: the association written after an integrated network
            # change was keyed by address and inherited by whatever took it next.
            "parent_mac": _norm_usb_mac(unit.get("mac")),
            "parent_hostname": unit.get("hostname") or "",
            "parent_model": (unit.get("model") or "").upper(),
            "parent_role": role if role in ("LEX", "REX") else "",
            "parent_usb_ip": str(runtime.get("usb_ip") or unit.get("usb_ip") or "").strip(),
            # The parent's own OmniStream firmware, which is what an operator
            # means by the firmware of an integrated USB endpoint's unit.
            "parent_firmware": str(unit.get("firmware") or unit.get("version") or "").strip(),
            # The Icron module version, learned from the parent's usb_icron
            # reply. A different product from the parent above, and reported
            # separately -- 2.1.2 against 2.0.9 on the bench.
            "icron_revision": str(runtime.get("icron_revision") or "").strip(),
            "association_source": "device_cache" if _norm_usb_mac(unit.get("usb_mac")) else "usb_icron_runtime",
        }
    return {"index": index, "pending": pending, "complete": not pending}


def _usb_parent_index(devices=None):
    """Return {normalized USB MAC: parent OmniStream device summary}."""
    return _usb_parent_context(devices)["index"]


def _refresh_usb_parent_map(timeout=2.0, force=False):
    """Learn USB MAC/role/firmware from the authoritative usb_icron endpoint.

    ``usb_mac``/``usb_type`` are recorded opportunistically by cache
    verification, so a unit that has not been verified yet has no association
    and its USB endpoint would otherwise look standalone. This fills the gap
    from the same usb_icron configuration the USB Matrix uses and writes it back
    into the device cache.

    ``force`` is an explicit operator Scan, which is a request to refresh rather
    than a background top-up. Without it this returned immediately unless
    ``pending`` was non-empty, and ``pending`` only ever holds units with no
    association at all -- so a device stopped being looked at the moment it
    became known, and nothing a later scan could learn ever reached it. An
    endpoint whose firmware had not been read when it was first seen stayed N/A
    for good, and Clear Units was the only way to make the application ask
    again. It still runs on the background executor, so discovery stays
    responsive.
    """
    context = _usb_parent_context()
    pending = list(context["pending"])
    targets = {entry["parent_ip"]: entry.get("mac", "") for entry in pending if entry.get("parent_ip")}
    if force:
        # Every USB-capable parent, not only the ones with something missing.
        #
        # Selecting only incomplete records was the obvious economy and it was
        # wrong: once a field had been learned the parent was never asked again,
        # so a value that CHANGED on the device could never reach the
        # application. An explicit Scan means "converge on what the hardware says
        # now", which includes replacing a value we already hold. The merge rule
        # in _record_usb_association keeps that safe -- a fresh known value wins,
        # a failed or silent read leaves the previous one alone.
        #
        # Ordinary polling keeps the pending-only behaviour above, so this is a
        # sweep the operator asked for and never a per-poll one.
        for entry in (context["index"] or {}).values():
            parent_ip = entry.get("parent_ip")
            if parent_ip:
                targets.setdefault(parent_ip, entry.get("parent_mac", ""))
    if not targets:
        return {"checked": 0, "learned": 0}
    user, pwd = app.config['USERNAME'], app.config['PASSWORD']
    ws_port, ws_path = app.config['WS_PORT'], app.config['WS_PATH']
    parent_macs = targets

    def learn(ip):
        try:
            cfg = _usb_get_config(ip, user, pwd, ws_port, ws_path, min(app.config['TIMEOUT'], timeout))
        except Exception as exc:
            log.info("USB parent map: usb_icron unavailable for %s: %s", ip, type(exc).__name__)
            return None
        mac = cfg.get("macaddress") or ""
        if len(_norm_usb_mac(mac)) != 12:
            return None
        role = (cfg.get("type") or "").strip().upper()
        # Record into the runtime association immediately so classification
        # improves even if the cache write-back is unavailable.
        _record_usb_association(ip, mac, role, cfg.get("ipaddress"), parent_macs.get(ip),
                                icron_revision=cfg.get("revision"))
        updates = {"usb_mac": mac}
        if role in ("LEX", "REX"):
            updates["usb_type"] = role
        try:
            _update_poll_detail_cache(ip, updates)
        except Exception as exc:
            log.info("USB parent map: cache write-back failed for %s: %s", ip, type(exc).__name__)
        return ip

    addresses = sorted(targets)
    learned = 0
    with ThreadPoolExecutor(max_workers=min(8, len(addresses))) as pool:
        for result in pool.map(learn, addresses):
            if result:
                learned += 1
    log.info("USB parent map refresh: %d asked, %d learned%s",
             len(addresses), learned, " (operator scan)" if force else "")
    return {"checked": len(addresses), "learned": learned}


# The protocol names a standalone endpoint from its role byte -- AT-OMNI-311 for
# a host, AT-OMNI-324 for a device -- and that string is the internal identity:
# role lookups, the bench diagnostics and the pairing family check all compare
# against it, and rewriting it would break them.
#
# What the operator sees is a different question. These units are sold as
# HW-OMNI, so one helper maps the internal identity to the displayed one, and
# every surface goes through it. The alternative -- replacing the string at each
# of a dozen render sites -- is how a protocol constant ends up half-renamed.
USB_DISPLAY_MODELS = {
    omni_usb_extender.HOST_MODEL: "HW-OMNI-311",
    omni_usb_extender.DEVICE_MODEL: "HW-OMNI-324",
}


def _display_model(internal):
    """The name to show for a protocol/internal device identity."""
    text = str(internal or "").strip()
    return USB_DISPLAY_MODELS.get(text, text)


def _display_hostname(device):
    """What to show in a standalone endpoint's Hostname column.

    The AT-OMNI-311/324 API exposes no verified hostname, so this column has
    always held the application's own generated label rather than anything the
    hardware reports -- which is why normalising the label is a presentation
    change and not a claim about the device. A name the operator has actually
    assigned wins over it and is never overwritten.
    """
    for key in ("friendly_name", "display_name", "hostname"):
        assigned = str((device or {}).get(key) or "").strip()
        if assigned:
            return assigned
    return _display_model((device or {}).get("device_type"))


# `network_relation` is an internal enum consumed by the source-binding logic:
# LOCAL means the endpoint shares the selected interface's subnet, OFF_NET means
# it does not, UNKNOWN means no interface information exists. OFF_NET is not a
# fault -- a routed endpoint is reachable, manageable and routable -- so the
# operator is shown what the relation means rather than the enum that encodes it.
USB_NETWORK_RELATION_LABELS = {"LOCAL": "Local", "OFF_NET": "Routed", "UNKNOWN": "Unknown"}


def _usb_extender_view(state=None, parents=None, context=None):
    """Classify each discovered USB endpoint and attach its control ownership.

    The UDP Advanced Query only reports a LEX/REX code, which the protocol layer
    names AT-OMNI-311 / AT-OMNI-324. That is a USB role, not a product identity:
    the Icron endpoint inside an E4521 or D4511 reports the very same codes. So
    when the USB MAC is authoritatively associated with an OmniStream parent, the
    parent wins for both presentation and control ownership, and the UDP-reported
    model is never shown.

    Enrichment only: the provider's canonical MAC identity and its own discovered
    fields are never overwritten.
    """
    state = state if state is not None else _usb_extenders.state()
    if parents is not None:
        context = {"index": parents, "pending": [], "complete": True}
    elif context is None:
        context = _usb_parent_context()
    index, complete = context["index"], context["complete"]
    devices = []
    # Discovery is not the only way an endpoint becomes known. A USB-capable
    # parent found by normal OmniStream discovery tells us its Icron endpoint
    # exists, its MAC and its address, over routed WebSocket -- no UDP broadcast
    # required. Synthesise a record for any such endpoint the UDP provider has
    # not seen, so an endpoint behind a routed parent is not missing from one
    # surface while present in another.
    records = list(state.get("devices") or [])
    known = {_norm_usb_mac(r.get("mac")) for r in records}
    for usb_mac, entry in sorted(index.items()):
        if usb_mac in known:
            continue
        netcfg = _usb_net_config_for(usb_mac) or {}
        address = netcfg.get("ipaddress") or entry.get("parent_usb_ip") or ""
        records.append({
            "mac": omni_usb_extender.normalize_mac(usb_mac),
            "ip": address,
            # Never discovered over UDP, so it carries no UDP-reported role and
            # no UDP liveness. Both come from the parent instead.
            "discovery_source": "parent_derived",
            "device_type": "",
            "subnet_mask": netcfg.get("subnetmask", ""),
            "gateway": netcfg.get("gateway", ""),
        })
    for record in records:
        device = dict(record)
        parent = index.get(_norm_usb_mac(device.get("mac")))
        device["usb_ip"] = device.get("ip") or ""
        # What the UDP layer reported, kept for diagnostics but never presented
        # as the product model.
        device["udp_role_code"] = device.get("device_type_code")
        device["udp_reported_type"] = device.get("device_type") or ""
        if parent:
            role = parent.get("parent_role") or ""
            device.update(parent)
            device["classification"] = "INTEGRATED"
            device["classification_reason"] = (
                f"USB MAC matches {parent.get('parent_model') or 'an OmniStream unit'} at "
                f"{parent.get('parent_ip')} (source: {parent.get('association_source')})")
            device["integrated"] = True
            device["display_model"] = parent.get("parent_model") or "USB endpoint"
            device["usb_role"] = {"LEX": "USB Host / LEX", "REX": "USB Device / REX"}.get(role, "USB")
            # Control ownership follows the parent, not the discovery protocol.
            device["identify_via"] = "parent"
            # Integrated USB network configuration is available through the
            # parent's own proven `net` API (icron interface).
            device["network_config_supported"] = True
            device["network_via"] = "parent"
            device["discovery_source"] = device.get("discovery_source") or "udp_discovery"
            if device.get("discovery_source") == "parent_derived":
                # Liveness for an endpoint that discovery never saw comes from the
                # parent observation, exactly as it does for a discovered one.
                fresh, _seen, _peers = _parent_is_live(parent.get("parent_ip"))
                device["online"] = fresh
                device["stale"] = not fresh
                device["liveness_source"] = "usb_icron" if fresh else ""
                device["liveness_sources"] = ["usb_icron"] if fresh else []
                device["manageable"] = fresh
            # Firmware for an integrated endpoint is the parent OmniStream
            # firmware the normal scan already recorded. The UDP-reported value is
            # the Icron module revision, a different fact, so it keeps its own key.
            # The endpoint's own version, from whichever source actually has
            # it: the UDP Basic Query when the module answers, otherwise the
            # parent's usb_icron reply, which reports the same field for the
            # module inside it. Never the parent's OmniStream firmware.
            icron_from_parent = str(parent.get("icron_revision") or "").strip()
            device["icron_revision"] = device.get("product_revision", "") or icron_from_parent
            device["firmware"] = parent.get("parent_firmware") or device.get("product_revision", "")
            device["firmware_source"] = "omnistream" if parent.get("parent_firmware") else "udp_basic_query"
            # Two different products, two different versions. The parent
            # OmniStream unit and the Icron USB module inside it are versioned
            # separately -- measured on hardware, the parent reported 2.1.2 while
            # its Icron endpoint reported 2.0.9. Anything describing the USB
            # endpoint must use the endpoint's own version, and where that has not
            # been read it stays empty: substituting the parent's would state a
            # version this module does not have.
            device["usb_firmware"] = device.get("product_revision", "") or icron_from_parent
            device["usb_firmware_source"] = ("udp_basic_query" if device.get("product_revision")
                                             else ("usb_icron" if icron_from_parent else ""))
            # Parent `net` is authoritative for an integrated endpoint. The UDP
            # Query mode byte is not meaningful here and must never win.
            netcfg = _usb_net_config_for(device.get("mac"))
            if netcfg:
                device["network_mode_raw"] = netcfg["mode_raw"]
                device["network_mode"] = (netcfg["mode_raw"].upper() if netcfg["mode_known"]
                                          else f"Unknown ({netcfg['mode_raw']})")
                device["usb_ip"] = netcfg["ipaddress"] or device.get("usb_ip", "")
                device["subnet_mask"] = netcfg["subnetmask"] or device.get("subnet_mask", "")
                device["gateway"] = netcfg["gateway"] or ""
                device["network_config_source"] = "parent_net"
                device["network_config_last_read"] = netcfg["network_config_last_read"]
                device["network_config_fresh"] = (not netcfg.get("network_config_stale")) and (
                    time.time() - netcfg["network_config_last_read"]) <= USB_NET_CONFIG_TTL * 3
            else:
                # No authoritative read yet: do not present the UDP byte as the
                # integrated mode, and do not invent one.
                device["network_mode"] = ""
                device["network_mode_raw"] = ""
                device["network_config_source"] = ""
                device["network_config_fresh"] = False
            # /api/reboot is an established parent operation, so Reboot is offered
            # for an integrated endpoint and dispatched to the parent.
            device["reboot_supported"] = True
            device["reboot_via"] = "parent"
            # usb_icron owns pairing for an integrated endpoint. UDP Advanced
            # Query pairing must not be presented as authoritative here.
            device["pairing_source"] = "usb_icron"
            device.pop("paired_macs", None)
            device["pairing_state_fresh"] = False
            # Liveness for an integrated endpoint comes from the usb_icron read
            # the application already performs against its parent; no duplicate
            # UDP traffic is generated for it.
            parent_live, parent_seen, parent_peers = _parent_is_live(parent.get("parent_ip"))
            device["parent_paired_count"] = parent_peers
            # Liveness authority is decided here, by a fixed precedence, and not
            # by whichever provider happened to write the record last. Both
            # providers can attest at once -- the parent answers usb_icron and the
            # endpoint answers its own UDP Query -- and previously the field simply
            # held whichever ran most recently, so an idle endpoint alternated
            # between Parent and UDP with nothing about it actually changing.
            udp_live = bool(device.get("live_online"))
            attesting = ([("usb_icron", parent_seen)] if parent_live else []) +                         ([("udp_query", device.get("live_seen"))] if udp_live else [])
            device["liveness_sources"] = [name for name, _ts in attesting]
            if parent_live:
                # The parent owns this endpoint, so it is the authority whenever it
                # is current, regardless of what else also answered.
                device["online"] = True
                device["stale"] = False
                device["live_seen"] = parent_seen
                device["liveness_source"] = "usb_icron"
                device["manageable"] = True
            elif udp_live:
                # The parent has not been read recently but the endpoint itself
                # answered. That is a real observation and it is named honestly.
                device["liveness_source"] = "udp_query"
            else:
                device["liveness_source"] = ""
        else:
            device["parent_ip"] = device["parent_hostname"] = device["parent_model"] = device["parent_role"] = ""
            device["integrated"] = False
            device["pairing_source"] = "advanced_query"
            if complete:
                # Every USB-capable unit's endpoint is known, so an endpoint that
                # matches none of them is genuinely standalone.
                device["classification"] = "STANDALONE"
                device["classification_reason"] = (
                    "USB MAC matches no OmniStream unit and every USB-capable unit is associated")
                device["display_model"] = _display_model(device.get("device_type")) or "Unknown"
                device["display_hostname"] = _display_hostname(device)
                device["usb_role"] = {omni_usb_extender.HOST_MODEL: "USB Host / LEX",
                                      omni_usb_extender.DEVICE_MODEL: "USB Device / REX"}.get(device.get("device_type"), "")
                device["identify_via"] = "extender"
                device["network_config_supported"] = True
                device["network_via"] = "extender"
                # Standalone extenders remain UDP-authoritative for network state.
                device["network_config_source"] = "standalone_udp"
                device["network_config_fresh"] = bool(device.get("online"))
                # Read live from Basic Device Information / Full Configuration.
                device["firmware"] = device.get("product_revision", "")
                device["firmware_source"] = "udp_basic_query" if device.get("product_revision") else ""
                # A standalone unit is the USB endpoint, so its firmware is the
                # USB firmware; there is no separate parent to confuse it with.
                device["usb_firmware"] = device.get("product_revision", "")
                device["usb_firmware_source"] = device["firmware_source"]
                device["reboot_supported"] = True
                device["reboot_via"] = "extender"
            else:
                # Association is still incomplete, so this endpoint cannot be
                # proven standalone. Fail closed: withhold standalone control
                # rather than risk aiming it at an integrated endpoint.
                device["classification"] = "UNCONFIRMED"
                device["classification_reason"] = (
                    f"USB MAC matches no OmniStream unit, but {len(context['pending'])} USB-capable unit(s) "
                    "have no association yet, so standalone cannot be proven")
                device["display_model"] = "USB endpoint (unclassified)"
                device["display_hostname"] = _display_hostname(device)
                device["usb_role"] = {omni_usb_extender.HOST_MODEL: "USB Host / LEX",
                                      omni_usb_extender.DEVICE_MODEL: "USB Device / REX"}.get(device.get("device_type"), "")
                device["identify_via"] = ""
                device["network_config_supported"] = False
                device["network_via"] = ""
                device["reboot_supported"] = False
                device["reboot_via"] = ""
        # Every surface renders the same derived object. The internal enum stays
        # in the JSON for tests, logs and the binding logic that consumes it; the
        # label is what an operator reads.
        device["network_relation_label"] = USB_NETWORK_RELATION_LABELS.get(
            device.get("network_relation") or "UNKNOWN", "Unknown")
        summary = _usb_link_summary(device)
        device["link_label"] = summary["label"]
        device["link_detail"] = summary["detail"]
        device["link_summary_state"] = summary["state"]
        device["link_peer_count"] = summary["peer_count"]
        device["link_linked_count"] = summary["linked_count"]
        # Which provider established each field, so no single "source" is read as
        # authority over all of them.
        device["field_authority"] = {
            "liveness": device.get("liveness_source", ""),
            "pairing": device.get("pairing_source", ""),
            "network": device.get("network_config_source", ""),
            "link": device.get("link_source", ""),
            "firmware": device.get("firmware_source", ""),
            # The USB module's firmware has its own provider: the endpoint's
            # UDP reply when it answers, otherwise the parent's usb_icron
            # block. Never the parent's own OmniStream firmware.
            "usb_firmware": device.get("usb_firmware_source", ""),
        }
        devices.append(device)
    result = dict(state)
    result["devices"] = devices
    result["online_count"] = len([d for d in devices if d.get("online")])
    result["parent_map_complete"] = complete
    result["parent_map_pending"] = list(context["pending"])
    return result


# ---------------- shared USB live-state manager ----------------
# One backend mechanism serves the USB Matrix, Configure > USB, and the Device
# Info count. Pages ask for current state on their own cadence; this decides
# whether another physical query is actually warranted, so two open pages do not
# multiply device traffic.
#
# Standalone endpoints are refreshed with a single targeted UDP Query to the
# address on record. Never a broadcast, never a range scan, and never an Advanced
# Query -- proving a unit answers does not require reading its pairing table.
#
# Integrated endpoints are not queried over UDP at all: their liveness comes from
# the usb_icron read the application already performs, so no duplicate traffic is
# introduced.
USB_LIVE_MIN_INTERVAL = 4.0        # do not re-query a standalone endpoint faster than this
USB_LIVE_PARENT_TTL = 45.0         # a usb_icron success keeps its endpoint live this long
# Pairing state runs on its own, slower cadence: an Advanced Query is the heavier
# read and liveness does not need it. Endpoints already fresh are skipped, so
# repeated page polls never repeat the physical traffic.
USB_PAIRING_MIN_INTERVAL = 20.0
_usb_live_lock = threading.RLock()
_usb_live_state = {"last_run": 0.0, "in_flight": False}
_usb_pairing_state = {"last_run": 0.0, "in_flight": False}
_USB_PARENT_LIVE = {}


def _record_parent_live(parent_ip, paired_count=None):
    """Note that a parent answered usb_icron, which proves its USB endpoint.

    The authoritative usb_icron peer count is captured at the same time so
    Configure can show current pairing information without a second read.
    """
    parent_ip = str(parent_ip or "").strip()
    if parent_ip:
        with _usb_live_lock:
            previous = _USB_PARENT_LIVE.get(parent_ip) or {}
            _USB_PARENT_LIVE[parent_ip] = {
                "seen": time.time(),
                "paired_count": previous.get("paired_count") if paired_count is None else paired_count,
            }


def _parent_is_live(parent_ip, ttl=USB_LIVE_PARENT_TTL):
    """Return (live, seen_timestamp, usb_icron paired count or None)."""
    with _usb_live_lock:
        entry = _USB_PARENT_LIVE.get(str(parent_ip or "").strip()) or {}
    seen = entry.get("seen")
    return (bool(seen) and (time.time() - seen) <= ttl), seen, entry.get("paired_count")


def _usb_live_refresh(force=False):
    """Refresh standalone liveness if it is due.

    In-flight suppression plus a minimum interval mean concurrent page polls
    collapse into at most one physical sweep. Returns immediately when another
    refresh is already running.
    """
    now = time.time()
    with _usb_live_lock:
        if _usb_live_state["in_flight"]:
            return {"status": "in_flight"}
        if not force and (now - _usb_live_state["last_run"]) < USB_LIVE_MIN_INTERVAL:
            return {"status": "throttled"}
        _usb_live_state["in_flight"] = True
    try:
        context = _usb_parent_context()
        targets = []
        for device in (_usb_extenders.state().get("devices") or []):
            mac = _norm_usb_mac(device.get("mac"))
            if mac in context["index"]:
                continue                      # integrated: liveness comes from usb_icron
            if device.get("ip"):
                targets.append(device.get("mac"))
        before = {m: bool((_usb_extenders.device(m) or {}).get("online")) for m in targets}
        outcome = _usb_extenders.refresh_liveness(targets) if targets else {"checked": 0, "online": 0}
        # An endpoint that just came back online should have its pairing state
        # re-read rather than waiting out the slow cadence.
        returned = [m for m in targets
                    if not before.get(m) and bool((_usb_extenders.device(m) or {}).get("online"))]
        if returned:
            with _usb_live_lock:
                _usb_pairing_state["last_run"] = 0.0
        with _usb_live_lock:
            _usb_live_state["last_run"] = time.time()
        return {"status": "refreshed", **outcome}
    except Exception as exc:
        log.info("USB live refresh failed: %s", type(exc).__name__)
        return {"status": "error", "error": type(exc).__name__}
    finally:
        with _usb_live_lock:
            _usb_live_state["in_flight"] = False


USB_LINK_MIN_INTERVAL = 30.0
_usb_link_state = {"in_flight": False, "last_run": 0.0}


def _usb_link_refresh(force=False, macs=None):
    """Link Status sweep, on its own slow cadence, for every USB endpoint.

    Link Status is a third kind of freshness. It is not read on every Matrix poll:
    liveness answers "does it answer", pairing answers "what is configured", and
    this answers "is the extender link up now".

    Integrated endpoints are included. Their *control* belongs to the parent --
    pairing through usb_icron, network through the parent net API, reboot through
    the parent -- but they are Icron endpoints with their own MAC and address, and
    hardware testing confirmed they answer Link Status (0x030F) over directed
    unicast UDP with the same seven-slot per-peer array. Read authority and
    control ownership are different things: a read the device answers is not made
    unavailable by the fact that writes go somewhere else.
    """
    now = time.time()
    with _usb_live_lock:
        if _usb_link_state["in_flight"]:
            return {"status": "in_flight"}
        if not force and (now - _usb_link_state["last_run"]) < USB_LINK_MIN_INTERVAL:
            return {"status": "throttled"}
        _usb_link_state["in_flight"] = True
    try:
        if macs is None:
            # Liveness for an integrated endpoint is derived from its parent, so
            # the derived view is the only place that knows it is up. Selecting
            # from the raw service store would skip every integrated endpoint,
            # because nothing marks those online there.
            known = {_norm_usb_mac(d.get("mac")) for d in (_usb_extenders.state().get("devices") or [])}
            macs = [d.get("mac") for d in (_usb_extender_view().get("devices") or [])
                    if d.get("online") and _norm_usb_mac(d.get("mac")) in known]
        outcome = _usb_extenders.refresh_link_status(macs) if macs else {"checked": 0, "refreshed": 0}
        with _usb_live_lock:
            _usb_link_state["last_run"] = time.time()
        return {"status": "refreshed", **outcome}
    except Exception as exc:
        log.info("USB link refresh failed: %s", type(exc).__name__)
        return {"status": "error", "error": type(exc).__name__}
    finally:
        with _usb_live_lock:
            _usb_link_state["in_flight"] = False


def _usb_pairing_refresh(force=False, macs=None):
    """Advanced Query refresh for standalone endpoints, on its own slow cadence.

    Separate from ``_usb_live_refresh`` on purpose: liveness is a cheap Query at
    the page cadence, pairing state is the heavier read and runs far less often.
    In-flight suppression plus a minimum interval collapse concurrent page polls,
    and the service skips any endpoint already fresh.
    """
    now = time.time()
    with _usb_live_lock:
        if _usb_pairing_state["in_flight"]:
            return {"status": "in_flight"}
        if not force and (now - _usb_pairing_state["last_run"]) < USB_PAIRING_MIN_INTERVAL:
            return {"status": "throttled"}
        _usb_pairing_state["in_flight"] = True
    try:
        if macs is None:
            context = _usb_parent_context()
            devices = _usb_extenders.state().get("devices") or []
            macs = [d.get("mac") for d in devices
                    if _norm_usb_mac(d.get("mac")) not in context["index"] and d.get("online")]
            # An integrated endpoint is not polled in general: usb_icron owns its
            # pairing. A UDP-created mixed route is the exception, because
            # usb_icron does not observe it and Advanced Query is the only thing
            # that can keep it current. Limited to endpoints already known to
            # carry such a peer, so no new traffic is added elsewhere.
            macs += [d.get("mac") for d in devices
                     if _norm_usb_mac(d.get("mac")) in context["index"] and (d.get("paired_macs") or [])]
        outcome = _usb_extenders.refresh_pairing(macs) if macs else {"checked": 0, "refreshed": 0}
        with _usb_live_lock:
            _usb_pairing_state["last_run"] = time.time()
        return {"status": "refreshed", **outcome}
    except Exception as exc:
        log.info("USB pairing refresh failed: %s", type(exc).__name__)
        return {"status": "error", "error": type(exc).__name__}
    finally:
        with _usb_live_lock:
            _usb_pairing_state["in_flight"] = False


# Internal capability enums stay in the JSON, tests and logs; these are what the
# operator actually reads. Backend enum names never reach normal UI text.
USB_CELL_TEXT = {
    "SUPPORTED_USB_ICRON": ("Available", "Route through the OmniStream USB pairing."),
    "SUPPORTED_STANDALONE_UDP": ("Available", "Route between standalone USB extenders."),
    "CONTROL_ONLY_VERIFIED": ("Mixed route — USB data not yet verified",
                              "Control-plane pairing verified; route remains disabled pending USB transport validation."),
    "IDENTITY_UNAVAILABLE": ("USB endpoint identity unavailable",
                             "This endpoint's USB MAC has not been established, so a route cannot be addressed to it."),
    "SUPPORTED_MIXED_VERIFIED": ("Available",
                                 "Mixed route between families. USB transport physically verified."),
    "SUPPORTED_MIXED_EXPERIMENTAL": ("Mixed USB route — data transport not yet validated",
                                     "Pairing control has been bench verified. Enable this route to perform USB "
                                     "peripheral validation."),
    "UNSUPPORTED_MIXED": ("Not routable", "This combination has not been validated."),
    "OFFLINE": ("Device offline", "The endpoint has not responded to the live check."),
    "UNCONFIRMED": ("USB ownership not confirmed", "Run a scan from Device Info so the endpoint can be associated."),
    "NOT_ELIGIBLE": ("Not routable between these endpoints", "The endpoint is outside the selected interface subnet."),
    "NETWORK_MISMATCH": ("Different USB subnet",
                         "These USB endpoints are on different IP subnets. A USB route is not carried "
                         "across a router, so this pairing is not available. Management of both "
                         "endpoints is unaffected."),
    "NETWORK_RELATION_UNKNOWN": ("USB network information unavailable",
                                 "The USB address or subnet mask of one endpoint has not been read, so "
                                 "this route cannot be shown to be legal."),
    "CONFLICT": ("Already paired to another host", "Remove the existing route before creating this one."),
    "STALE": ("Pairing state stale", "The pairing table has not been re-read recently."),
}


def _usb_cell_text(state, fallback=""):
    label, detail = USB_CELL_TEXT.get(state, ("Not routable", fallback))
    return {"label": label, "detail": detail or fallback}


def _ipv4_sort_key(value):
    """Numeric IPv4 ordering. String ordering would put .141 before .32."""
    try:
        return int(ipaddress.IPv4Address(str(value or "").strip()))
    except Exception:
        return 1 << 32          # unparseable addresses sort last, deterministically


def _usb_matrix_sort_key(entry):
    """Deterministic Matrix axis order, independent of any completion order.

    Integrated units first (preserving the established Matrix convention that
    OmniStream units lead), then numeric IPv4, then canonical MAC as the final
    tie-break so the order can never depend on dict or thread ordering.
    """
    return (0 if (entry.get("kind") or "integrated") == "integrated" else 1,
            _ipv4_sort_key(entry.get("ip")),
            _norm_usb_mac(entry.get("mac")))


# ---------------- USB Matrix route capability ----------------
# Every combination is classified independently from what the hardware actually
# proved on the bench. Nothing is inferred from one family to another.
#
#   E4521 <-> D4511  SUPPORTED_USB_ICRON        established product behaviour
#   311   <-> 324    SUPPORTED_STANDALONE_UDP   Pair and Unpair validated on hardware
#   E4521 <-> 324    SUPPORTED_MIXED_EXPERIMENTAL  Pair/Unpair verified; USB data plane unverified
#   311   <-> D4511  SUPPORTED_MIXED_VERIFIED      Pair/Unpair verified; USB data plane verified
#
# A mixed combination is routable so its USB data plane can be validated
# physically. A successful Pair never implies data-plane success -- 311 to D4511
# moved out of "experimental" only when an operator ran USB peripherals across it
# in the five-peer topology, not when Pair started working.
USB_ROUTE_CAPABILITY = {
    ("integrated", "integrated"): {"state": "SUPPORTED_USB_ICRON", "control_path": "usb_icron", "enabled": True,
                                   "note": "Established E4521/D4511 usb_icron routing."},
    ("standalone", "standalone"): {"state": "SUPPORTED_STANDALONE_UDP", "control_path": "standalone_udp", "enabled": True,
                                   "note": "HW-OMNI-311/324 Pair and Unpair validated against hardware."},
    ("integrated", "standalone"): {"state": "SUPPORTED_MIXED_EXPERIMENTAL", "control_path": "standalone_udp",
                                   "enabled": True, "data_plane_verified": False,
                                   "note": "E4521 to HW-OMNI-324: Pair and Unpair verified on hardware; USB data "
                                           "transport not yet validated."},
    ("standalone", "integrated"): {"state": "SUPPORTED_MIXED_VERIFIED", "control_path": "standalone_udp",
                                   "enabled": True,
                                   "note": "HW-OMNI-311 to D4511: Pair and Unpair verified on hardware, and USB "
                                           "peripheral transport physically verified by the operator."},
}

# Control plane means Pair/Unpair were verified against hardware. Data plane means
# a USB peripheral was observed working across the route. Three combinations now
# carry data-plane confirmation from physical tests; E4521 to AT-OMNI-324 awaits
# the operator's peripheral test and must never claim it because Pair worked.
# Physical USB peripheral transport, verified by an operator with real hardware.
# A verified control plane never sets an entry here; only a physical test does.
#
#   integrated -> integrated  E4521 -> D4511, the established path
#   standalone -> standalone  311 -> 324, verified in the five-peer topology
#   standalone -> integrated  311 -> D4511, verified in the same topology
#   integrated -> standalone  E4521 -> 324, control plane only, not yet tested
USB_ROUTE_DATA_PLANE_VERIFIED = {
    ("integrated", "integrated"): True,
    ("standalone", "standalone"): True,
    ("standalone", "integrated"): True,
    ("integrated", "standalone"): False,
}


def _usb_endpoint_network(entry):
    """A USB endpoint's own address and mask -- the network the USB link lives on.

    For an integrated endpoint this is the Icron interface as the parent `net`
    API reports it, never the parent's management address. An E4521 managed on
    192.168.100.x whose Icron endpoint sits on 192.168.200.0/24 belongs, for
    pairing purposes, to 192.168.200.0/24.

    Order of authority: whatever the derived view stamped on this entry (the same
    object every surface renders), then the parent Icron configuration, then the
    endpoint's own reported mask. An empty mask is returned as empty, never as an
    assumed /24 -- callers must be able to tell "different network" from "cannot
    say", because those have different consequences.
    """
    entry = entry or {}
    mac = _canonical_usb_mac(entry)
    netcfg = _usb_net_config_for(mac) or {}
    address = (entry.get("endpoint_ip") or netcfg.get("ipaddress")
               or entry.get("usb_ip") or entry.get("ip") or "")
    mask = (entry.get("endpoint_mask") or netcfg.get("subnetmask")
            or entry.get("device_subnet_mask") or entry.get("subnet_mask") or "")
    return str(address).strip(), str(mask).strip()


def _usb_attach_endpoint_networks(entries, view=None):
    """Stamp each Matrix axis entry with its endpoint's address and mask.

    The defect this exists for: neither axis builder carried a mask, so every
    eligibility question answered "cannot say" -- and the gate, which refused only
    on a definite mismatch, let a cross-subnet cell render as available. The mask
    was known all along; it simply never reached the entry.

    Doing it once here rather than inside the per-cell capability keeps an N x M
    grid from rebuilding the view for every intersection.
    """
    view = view if view is not None else _usb_extender_view()
    by_mac = {}
    for device in view.get("devices") or []:
        identity = _norm_usb_mac(device.get("mac"))
        if not identity:
            continue
        # The device's own mask first. `subnet_mask` on a standalone record is
        # the mask of the interface it was discovered from, so taking it first
        # made the cell and the route gate answer from different networks.
        by_mac[identity] = (str(device.get("usb_ip") or device.get("ip") or "").strip(),
                            str(device.get("device_subnet_mask") or device.get("subnet_mask") or "").strip())
    for entry in entries or []:
        address, mask = by_mac.get(_norm_usb_mac(_canonical_usb_mac(entry)), ("", ""))
        if address:
            entry["endpoint_ip"] = address
        if mask:
            entry["endpoint_mask"] = mask
    return entries




def _usb_route_network_check(lex_entry, rex_entry):
    """Fail-closed eligibility for creating or moving a USB route.

    Management reachability and route eligibility are different questions. A
    routed endpoint is reachable, manageable and visible, and is still not a
    legal USB peer for something on another subnet -- the USB link is not routed.

    So this returns a decision, not a hint:

        ("OK", None)                       proven to share a subnet
        ("NETWORK_MISMATCH", detail)       proven not to
        ("NETWORK_RELATION_UNKNOWN", d)    not proven either way

    The third case refuses. Anything less would authorise a route on the strength
    of missing information.
    """
    lex_ip, lex_mask = _usb_endpoint_network(lex_entry)
    rex_ip, rex_mask = _usb_endpoint_network(rex_entry)
    detail = {"lex_usb_ip": lex_ip or None, "lex_subnet_mask": lex_mask or None,
              "rex_usb_ip": rex_ip or None, "rex_subnet_mask": rex_mask or None}
    same = omni_usb_extender.endpoints_same_network(lex_ip, lex_mask, rex_ip, rex_mask)
    if same is True:
        return "OK", detail
    if same is False:
        return "NETWORK_MISMATCH", detail
    return "NETWORK_RELATION_UNKNOWN", detail


def _canonical_usb_mac(entry):
    """The canonical USB endpoint MAC for a Matrix axis entry, or "" if unknown.

    This is the routing identity. It is deliberately separate from `usb_key`,
    which is a display/index key and is an OmniStream control IP for an
    integrated unit. An address must never be able to stand in for an identity:
    IP is transport metadata, MAC is who the endpoint is.
    """
    if not isinstance(entry, dict):
        return ""
    raw = entry.get("usb_mac") or entry.get("mac") or ""
    try:
        return omni_usb_extender.normalize_mac(raw)
    except Exception:
        return ""


def _usb_route_capability(lex_entry, rex_entry):
    """Capability for one Matrix cell, derived from both endpoint classifications.

    Carries both the internal ``state`` enum (for JSON, tests and logging) and the
    concise ``label``/``detail`` the UI shows, so no enum name reaches the screen.
    """
    lex_kind = lex_entry.get("kind") or "integrated"
    rex_kind = rex_entry.get("kind") or "integrated"
    base = dict(USB_ROUTE_CAPABILITY.get((lex_kind, rex_kind),
                                         {"state": "UNSUPPORTED_MIXED", "control_path": "", "enabled": False,
                                          "note": "This combination has not been validated."}))
    base.setdefault("data_plane_verified", USB_ROUTE_DATA_PLANE_VERIFIED.get((lex_kind, rex_kind), False))
    base["control_plane_verified"] = bool(base.get("enabled"))
    base["mixed"] = lex_kind != rex_kind
    # Endpoint conditions override capability, always failing closed.
    for entry, label in ((lex_entry, "LEX"), (rex_entry, "REX")):
        if entry.get("classification") == "UNCONFIRMED":
            return _with_cell_text({**base, "state": "UNCONFIRMED", "enabled": False,
                                    "note": f"The {label} endpoint is not associated yet. Run a scan from Device Info."})
        if entry.get("kind") == "standalone" and not entry.get("online", True):
            return _with_cell_text({**base, "state": "OFFLINE", "enabled": False,
                                    "note": f"The {label} endpoint has not responded to the live check."})
    if base["control_path"] == "standalone_udp":
        # This path transmits MACs, so it may only be offered when both canonical
        # identities are known. Without this the cell would look routable and the
        # click would have to invent an identity from whatever key it had.
        for entry, label in ((lex_entry, "LEX"), (rex_entry, "REX")):
            if not _canonical_usb_mac(entry):
                log.info("USB route cell disabled: %s endpoint %r has no canonical USB MAC",
                         label, entry.get("usb_key") or entry.get("ip") or "?")
                return _with_cell_text({**base, "state": "IDENTITY_UNAVAILABLE", "enabled": False,
                                        "note": f"The {label} endpoint's USB MAC is not known, so it cannot be routed."})
        for entry, label in ((lex_entry, "LEX"), (rex_entry, "REX")):
            if entry.get("kind") == "standalone" and entry.get("pairing_eligible") is False:
                return _with_cell_text({**base, "state": "NOT_ELIGIBLE", "enabled": False,
                                        "note": f"The {label} endpoint has no usable address."})
        # The two USB endpoints must share an IP network with each other. This is
        # computed from their own addresses and masks: OmniSuite may manage them
        # from a third network, and its own subnet says nothing about whether the
        # pair can talk. An indeterminate answer does not disable the cell.
    # Same-subnet is a property of the two USB endpoints, not of the transport
    # that would carry the command, so it is checked for every combination --
    # E4521 to D4511 over usb_icron included. A USB link is not routed.
    verdict, network_detail = _usb_route_network_check(lex_entry, rex_entry)
    if verdict == "NETWORK_RELATION_UNKNOWN":
        # Not proven compatible. A cell that cannot be shown to be legal is not
        # offered; the endpoints stay visible, their intersection does not become
        # clickable.
        return _with_cell_text({**base, "state": "NETWORK_RELATION_UNKNOWN", "enabled": False,
                                "network": network_detail,
                                "note": "The USB network of one endpoint is not known, so a route "
                                        "cannot be shown to be legal."})
    if verdict == "NETWORK_MISMATCH":
        return _with_cell_text({**base, "state": "NETWORK_MISMATCH", "enabled": False,
                                "network": network_detail,
                                "note": "These two USB endpoints are on different IP networks."})
    return _with_cell_text(base)


def _with_cell_text(capability):
    capability.update(_usb_cell_text(capability.get("state"), capability.get("note", "")))
    return capability


def _usb_matrix_axes(view=None):
    """LEX/HOST and REX/DEVICE axes for the Matrix, both families together.

    Integrated entries keep their existing control-IP `usb_key` so the proven
    usb_icron routing path is untouched; standalone entries are keyed by MAC.
    `usb_key` is a display/index key only. Every entry additionally carries an
    explicit `usb_mac`, which is the sole routing identity.
    """
    view = view if view is not None else _usb_extender_view()
    lex, rex = [], []
    for device in view.get("devices") or []:
        if device.get("classification") != "STANDALONE":
            continue
        entry = {
            "kind": "standalone",
            "usb_key": device.get("mac", ""),
            # Routing identity, always explicit and never inferred from usb_key.
            "usb_mac": device.get("mac", ""),
            "ip": device.get("ip", ""),
            "mac": device.get("mac", ""),
            # Both are rendered -- `model` in the REX row label, `host` under the
            # LEX column address. The identity comparisons below use
            # `device_type` directly and are unaffected.
            "model": _display_model(device.get("device_type")),
            "host": _display_hostname(device),
            "usb_ip": device.get("ip", ""),
            # Firmware read live from Basic/Full Configuration. `revision` is kept
            # for any existing consumer; `firmware` is the name that says what it
            # actually is.
            "revision": device.get("product_revision", ""),
            "firmware": device.get("product_revision", ""),
            "firmware_source": "udp_basic_query" if device.get("product_revision") else "",
            "protocol": "IP",
            "online": bool(device.get("online")),
            # Live link, kept strictly separate from the configured pairing above.
            "link_state": device.get("link_state") or "UNKNOWN",
            "link_peer_macs": list(device.get("link_peer_macs") or []),
            "link_states": [dict(st) for st in (device.get("link_states") or [])],
            "link_state_fresh": bool(device.get("link_state_fresh")),
            "link_state_retained": bool(device.get("link_state_retained")),
            "link_source": device.get("link_source", ""),
            "link_age": device.get("link_age"),
            "classification": device.get("classification"),
            "pairing_eligible": bool(device.get("pairing_eligible")),
            "pairing_state_fresh": bool(device.get("pairing_state_fresh")),
            "pairing_age": device.get("pairing_age"),
            "paired_macs": list(device.get("paired_macs") or []),
            "host_port": "",
            "host_port_configurable": False,
            "filter": "",
            "filter_configurable": False,
        }
        if device.get("device_type") == omni_usb_extender.HOST_MODEL:
            entry["type"] = "LEX"
            lex.append(entry)
        elif device.get("device_type") == omni_usb_extender.DEVICE_MODEL:
            entry["type"] = "REX"
            rex.append(entry)
    return lex, rex


# Device Filtering, on the evidence gathered on hardware:
#   * `usbfiltering` is a field of the OmniStream `usb_icron` configuration
#     object, read and written over the parent's WebSocket API.
#   * Three standalone units (two AT-OMNI-324, one AT-OMNI-311, firmware 1.9.4)
#     accept no TCP connection on 80, 443, 8080 or 3000, so that API does not
#     exist on them.
#   * Their Full Configuration responses are byte-identical in every unattributed
#     field, across both roles, so nothing there encodes a per-unit policy.
#   * No filtering command appears in the verified UDP opcode set.
# None of that proves the hardware cannot filter, so the capability is reported
# as not established rather than as unsupported.
USB_STANDALONE_FILTER_REASON = (
    "Device filtering is an OmniStream usb_icron setting applied through the parent unit. "
    "A standalone HW-OMNI-311/324 exposes no such API and no filtering command is "
    "established in its UDP protocol, so this is unknown rather than unavailable.")


def _usb_link_summary(entry):
    """Summarise Link Status for one endpoint from its per-peer states.

    A host may hold several peers and they need not agree, so a single byte can
    never speak for all of them: five peers with one down is Partial, not Linked.
    A read that did not answer is never "Not linked" -- a timeout and a confirmed
    absence of link are different facts -- but a state verified moments ago is not
    thrown away either. While it is inside the retain window it is still shown,
    marked as aging.
    """
    entry = entry or {}
    known = bool(entry.get("link_state_fresh") or entry.get("link_state_retained"))
    aging = bool(entry.get("link_state_retained")) and not entry.get("link_state_fresh")
    states = list(entry.get("link_states") or [])
    peers = list(entry.get("link_peer_macs") or [])
    configured = list(entry.get("paired_macs") or [])
    summary = {"label": "Unknown", "detail": "", "state": "UNKNOWN",
               "known": known, "aging": aging, "peer_count": len(states),
               "linked_count": 0}
    if not known:
        # Say why it is unknown when we know why; silence here reads as a bug.
        summary["detail"] = "No Link Status response" if entry.get("link_read_failed") else ""
        return summary
    if not states and not peers:
        # The device answered and reported no peer slot in use. That is a
        # configuration fact, not a failed physical link.
        if configured:
            summary["detail"] = "Configured peers not reported by Link Status"
            return summary
        summary.update({"label": "Not paired", "state": "NO_PEER"})
        return summary
    linked = [st for st in states if st.get("state") == "LINKED"]
    not_linked = [st for st in states if st.get("state") == "NOT_LINKED"]
    summary["linked_count"] = len(linked)
    total = len(states)
    if total and len(linked) == total:
        summary.update({"label": "Linked", "state": "LINKED"})
        summary["detail"] = f"{total} peers" if total > 1 else ""
    elif linked:
        summary.update({"label": "Partial", "state": "PARTIAL",
                        "detail": f"{len(linked)}/{total} linked"})
    elif not_linked:
        summary.update({"label": "Not linked", "state": "NOT_LINKED"})
        summary["detail"] = f"0/{total} linked" if total > 1 else ""
    else:
        summary["detail"] = "Link Status reported an unrecognised state"
    if aging and summary["state"] != "UNKNOWN":
        age = entry.get("link_age")
        seen = f"{int(age)} s ago" if isinstance(age, (int, float)) else "a moment ago"
        summary["detail"] = (summary["detail"] + " · seen " + seen) if summary["detail"] else ("Seen " + seen)
    return summary


def _usb_link_label(entry):
    """Concise link text.  A timeout is never reported as 'not linked'."""
    return _usb_link_summary(entry)["label"]


def _usb_inventory(integrated_lex=(), integrated_rex=(), view=None):
    """One row per physical USB endpoint, keyed by canonical USB MAC.

    Integrated and standalone endpoints are merged here rather than concatenated
    by the client: an endpoint that exists in both collections is one device, and
    the integrated record is listed first so association wins for product
    identity. Each row states its own capabilities, so a standalone unit never
    inherits an integrated-only control.

    The integrated rows are passed in from the `usb_icron` reads the Matrix build
    already performs; no additional device traffic is introduced here.
    """
    view = view if view is not None else _usb_extender_view()
    integrated_lex, integrated_rex = list(integrated_lex), list(integrated_rex)
    axis_lex, axis_rex = _usb_matrix_axes(view)
    # The one derived liveness calculation, the same object the Matrix cell and
    # the route gate consume. The usb_icron rows carry no `online` of their own,
    # so without this every integrated row would render as offline while the cell
    # beside it was enabled.
    derived = {_norm_usb_mac(d.get("mac")): d for d in (view.get("devices") or [])}
    rows = {"LEX": {}, "REX": {}}
    for role, entries in (("LEX", integrated_lex + axis_lex), ("REX", integrated_rex + axis_rex)):
        for entry in entries:
            mac = _norm_usb_mac(entry.get("usb_mac") or entry.get("mac"))
            if not mac:
                # Without a canonical identity a row cannot be deduplicated, so it
                # is dropped rather than risking a duplicate under a display key.
                log.info("USB inventory row without a canonical MAC skipped: %r", entry.get("usb_key"))
                continue
            if mac in rows[role]:
                continue                      # already represented; one endpoint, one row
            rows[role][mac] = _usb_inventory_row(entry, role, derived.get(mac))
    return ([rows["LEX"][mac] for mac in sorted(rows["LEX"], key=lambda m: _usb_matrix_sort_key(rows["LEX"][m]))],
            [rows["REX"][mac] for mac in sorted(rows["REX"], key=lambda m: _usb_matrix_sort_key(rows["REX"][m]))])


def _usb_inventory_row(entry, role, derived=None):
    """One inventory row, with capability flags decided by ownership.

    ``derived`` is this endpoint's entry in the shared view. Liveness, link state
    and pairing freshness are taken from it so the inventory cannot disagree with
    the route cell rendered beside it.
    """
    derived = derived or {}
    integrated = (entry.get("kind") or "integrated") == "integrated"
    # This is a USB inventory row, so Firmware is the USB endpoint's own version.
    # For an integrated endpoint that is the Icron module, which is versioned
    # separately from the OmniStream unit around it; the parent's firmware stays
    # on the device inventory and is never borrowed to fill a blank here.
    firmware = (derived.get("usb_firmware") or entry.get("usb_firmware")
                or entry.get("revision") or "")
    row = {
        "kind": entry.get("kind") or "integrated",
        "usb_key": entry.get("usb_key") or entry.get("ip", ""),
        "usb_mac": _canonical_usb_mac(entry),
        "mac": _canonical_usb_mac(entry),
        "device_ip": entry.get("ip", ""),
        "ip": entry.get("ip", ""),
        "usb_ip": entry.get("usb_ip", ""),
        "name": entry.get("host") or entry.get("model") or "",
        "host": entry.get("host") or entry.get("model") or "",
        "model": entry.get("model") or entry.get("display_model") or "",
        "type": entry.get("type") or role,
        "usb_role": "USB Host / LEX" if role == "LEX" else "USB Device / REX",
        "protocol": entry.get("protocol") or "IP",
        "firmware": firmware,
        "revision": firmware,
        "firmware_source": (derived.get("usb_firmware_source") or entry.get("usb_firmware_source")
                            or ("udp_basic_query" if firmware else "")),
        # Kept alongside, clearly named, for anything that wants the unit rather
        # than its USB endpoint.
        # Only when it really is the OmniStream unit's firmware: `firmware`
        # falls back to the Icron revision when the parent's is unknown, and a
        # field named for the parent must not carry the module's version.
        "parent_firmware": (derived.get("firmware")
                            if integrated and derived.get("firmware_source") == "omnistream" else ""),
        "classification": entry.get("classification") or ("INTEGRATED" if integrated else "STANDALONE"),
        "online": bool(derived.get("online", entry.get("online"))),
        "liveness_source": derived.get("liveness_source", ""),
        # Configured pairing.
        "paired_macs": list(entry.get("paired_macs") or derived.get("paired_macs") or []),
        "pairing_state_fresh": bool(entry.get("pairing_state_fresh") or derived.get("pairing_state_fresh")),
        "pairing_age": entry.get("pairing_age", derived.get("pairing_age")),
        # Current link, independent of the above.
        "link_state": derived.get("link_state") or entry.get("link_state") or "UNKNOWN",
        "link_peer_macs": list(derived.get("link_peer_macs") or entry.get("link_peer_macs") or []),
        "link_states": [dict(st) for st in (derived.get("link_states") or entry.get("link_states") or [])],
        "link_state_fresh": bool(derived.get("link_state_fresh") or entry.get("link_state_fresh")),
        "link_state_retained": bool(derived.get("link_state_retained") or entry.get("link_state_retained")),
        "link_source": derived.get("link_source") or entry.get("link_source") or "",
        "link_age": derived.get("link_age", entry.get("link_age")),
    }
    summary = _usb_link_summary({
        "link_states": row["link_states"],
        "link_peer_macs": row["link_peer_macs"],
        "link_state_fresh": row["link_state_fresh"],
        "link_state_retained": row["link_state_retained"],
        "link_age": row["link_age"],
        "link_read_failed": derived.get("link_read_failed") or entry.get("link_read_failed"),
        "paired_macs": row["paired_macs"],
    })
    row["link_label"] = summary["label"]
    row["link_detail"] = summary["detail"]
    row["link_summary_state"] = summary["state"]
    if integrated:
        # The established OmniStream controls stay exactly as they are.
        row.update({
            "host_port": entry.get("host_port", ""),
            "host_port_configurable": role == "LEX",
            "filter": entry.get("filter", ""),
            "filter_configurable": True,
            "type_configurable": True,
        })
    else:
        # Nothing in the AT-OMNI-311/324 API establishes these, so they are shown
        # as unavailable rather than offered as controls that cannot work.
        #
        # Device Filtering deliberately does not say "Not supported". It is an
        # OmniStream `usb_icron` configuration field, delivered over the parent's
        # WebSocket API; a standalone unit has no parent and exposes no HTTP or
        # WebSocket listener, and no UDP command for it appears in the verified
        # opcode set. That is strong evidence, but it is still absence of
        # evidence, and absence of evidence is not a capability statement.
        row.update({
            "host_port": "Fixed / N/A" if role == "LEX" else "N/A",
            "host_port_configurable": False,
            "filter": "Not established",
            "filter_capability": "NOT_ESTABLISHED",
            "filter_reason": USB_STANDALONE_FILTER_REASON,
            "filter_configurable": False,
            "type_configurable": False,
        })
    return row


def _usb_standalone_inventory(exclude_macs=None, view=None):
    """Standalone AT-OMNI-311/324 rows for the USB Matrix inventory tables.

    Inventory display only. These rows are deliberately kept out of the `lex` and
    `rex` arrays that build the routing grid, so no route cell is created and
    existing E4521/D4511 routing is untouched. An endpoint that correlates to an
    OmniStream parent is omitted because it already has a lex/rex row.
    """
    exclude_macs = exclude_macs or set()
    standalone_lex, standalone_rex = [], []
    try:
        # The same derived view Configure > USB renders, so liveness is computed
        # once. There is no second online/stale calculation.
        view = view if view is not None else _usb_extender_view()
        for device in (view.get("devices") or []):
            if device.get("classification") != "STANDALONE" or _norm_usb_mac(device.get("mac")) in exclude_macs:
                continue
            device_type = device.get("device_type", "")
            if device.get("pairing_state_fresh"):
                peer_count, peers_label = len(device.get("paired_macs") or []), None
            elif device.get("advanced_query_at"):
                peer_count, peers_label = None, "Stale"
            else:
                peer_count, peers_label = None, "Not read"
            row = {
                "ip": device.get("ip", ""),
                "host": _display_hostname(device) or device.get("product") or "",
                "usb_ip": device.get("ip", ""),
                "mac": device.get("mac", ""),
                "revision": device.get("product_revision", ""),
                "protocol": "IP",
                "standalone": True,
                "device_type": device_type,
                # Carried straight through from the shared derivation.
                "online": bool(device.get("online")),
                "stale": bool(device.get("stale")),
                "last_seen": device.get("last_seen"),
                "network_mode": device.get("network_mode", ""),
                # The host-side connector is fixed on a standalone unit and device
                # filtering is not established by the AT-OMNI-311/324 API, so
                # neither is offered as a control.
                "host_port": "",
                "host_port_configurable": False,
                "filter": "",
                "filter_configurable": False,
                # Peers are authoritative only from a fresh Advanced Query.
                "pairing_state_fresh": bool(device.get("pairing_state_fresh")),
                "peer_count": peer_count,
                "peers_label": peers_label,
                # Standalone routing stays hardware gated.
                "routable": False,
            }
            if device_type == omni_usb_extender.HOST_MODEL:
                row["type"] = "LEX"
                standalone_lex.append(row)
            elif device_type == omni_usb_extender.DEVICE_MODEL:
                row["type"] = "REX"
                standalone_rex.append(row)
    except Exception as exc:
        log.info("Standalone USB inventory unavailable: %s", type(exc).__name__)
        return {"standalone_lex": [], "standalone_rex": [], "online_ttl": None}
    return {"standalone_lex": standalone_lex, "standalone_rex": standalone_rex,
            "online_ttl": view.get("online_ttl")}


@app.route("/api/usb_extenders", methods=["GET"])
def api_usb_extenders():
    # Same shared live-state mechanism the Matrix uses; the manager decides
    # whether another physical query is actually warranted.
    if request.args.get("live", "1") not in ("0", "false", "no"):
        _usb_live_refresh()
        # Configure > USB shows pairing information, so it also asks for pairing
        # freshness. The manager skips endpoints already fresh and enforces its
        # own slower cadence, so this does not become an Advanced Query per poll.
        # Link Status has its own slower cadence and its own in-flight guard, and
        # it is what keeps the Link column known while a page simply sits open.
        _usb_link_refresh()
        if request.args.get("pairing", "0") not in ("0", "false", "no"):
            _usb_pairing_refresh()
            # Authoritative Icron configuration, on its own TTL and deduplicated,
            # so an external change made in the parent web UI is picked up without
            # a rediscovery or a restart.
            _usb_extenders._executor.submit(_refresh_icron_network_config)
    return jsonify({"ok": True, **_usb_extender_view()})


@app.route("/api/usb_extenders/clear", methods=["POST"])
def api_usb_extender_clear():
    """Forget discovered USB inventory.  Sends nothing to any device."""
    removed = _usb_extenders.clear_devices()
    return jsonify({"ok": True, "cleared": removed})


@app.route("/api/usb_extenders/discover", methods=["POST"])
def api_usb_extender_discover():
    """Unified background USB discovery for the Device Info Scan button.

    Returns as soon as the work is dispatched.  Local broadcast and any range
    scan are independent, so one being busy or invalid never suppresses the
    other, and neither is ever awaited by the OmniStream scan.
    """
    data = request.get_json(silent=True) or {}
    interface_ip, mask = _usb_extender_network(data)
    if not interface_ip:
        return jsonify({"ok": False, "error": "A valid interface_ip and subnet_mask are required."}), 400
    started, issues = [], []
    # Learn any missing USB MAC association in the background so discovered
    # endpoints can be classified. Never on the /api/scan path, never on a poll.
    try:
        # Association first, then authoritative Icron network configuration.
        # Both run on the background executor so /api/scan never waits for them.
        def _enrich():
            # force: the operator pressed Scan, which means "make what you show
            # me match the hardware now", not "top up anything still unknown".
            _refresh_usb_parent_map(force=True)
            _refresh_icron_network_config(force=True)
            # Optional standalone reads. Each is independent and bounded, none of
            # them gates discovery, and none of them is on the /api/scan path.
            try:
                context = _usb_parent_context()
                devices = _usb_extenders.state().get("devices") or []
                known = [d for d in devices if _norm_usb_mac(d.get("mac")) not in context["index"]]
                # Directed poll of *every* known standalone endpoint, including
                # ones currently offline: a device that stopped answering may have
                # simply moved, and its record is the only address we have to try.
                if known:
                    _usb_extenders.refresh_liveness([d.get("mac") for d in known])
                online = [d.get("mac") for d in known if d.get("online")]
                if online:
                    _usb_extenders.refresh_full_configuration(online)
                    _usb_link_refresh(force=True, macs=online)
            except Exception as exc:
                log.info("Standalone USB enrichment skipped: %s", type(exc).__name__)
        _usb_extenders._executor.submit(_enrich)
    except Exception as exc:
        log.info("USB enrichment could not be dispatched: %s", type(exc).__name__)
    try:
        _usb_extenders.start_local_discovery(interface_ip, mask)
        started.append("local")
    except omni_usb_extender.ScanBusyError:
        issues.append({"scope": "local", "error": "Local USB discovery is already running."})
    except OSError:
        log.info("USB extender local broadcast setup failed for %s", interface_ip)
        issues.append({"scope": "local", "error": "Local USB discovery could not start on the selected interface."})

    # An unknown device on a routed network cannot answer a broadcast, so the
    # only way to find one is to address candidates directly. Both the ad-hoc
    # Targets field and the operator's saved USB discovery networks feed the same
    # bounded, directed range scanner.
    expressions = []
    targets_expression = str(data.get("targets") or "").strip()
    if targets_expression:
        expressions.extend(re.split(r"[\s,]+", targets_expression))
    configured = list(_usb_extenders.state().get("ranges") or [])
    expressions.extend(configured)
    target_count = 0
    if expressions:
        try:
            # Reuse the bounded parser; range expansion never happens in the UI.
            targets = omni_usb_extender.parse_ranges([e for e in expressions if e])
            target_count = len(targets)
            if targets:
                _usb_extenders.start_range_scan(interface_ip, mask, targets=targets)
                started.append("targets" if targets_expression else "ranges")
        except omni_usb_extender.ScanBusyError:
            issues.append({"scope": "targets", "error": "A USB range scan is already running."})
        except (omni_usb_extender.ProtocolError, ValueError) as exc:
            issues.append({"scope": "targets", "error": f"USB discovery skipped for these targets: {exc}"})
    return jsonify({"ok": bool(started), "status": "started" if started else "not_started",
                    "started": started, "issues": issues, "target_count": target_count,
                    "configured_ranges": configured,
                    "maximum_hosts": omni_usb_extender.MAX_RANGE_HOSTS})


@app.route("/api/usb_extenders/traffic", methods=["GET"])
def api_usb_extender_traffic():
    """Read-only UDP traffic tally, by opcode, since the process started.

    Diagnostic. It transmits nothing itself, and exists so a claim about polling
    cost can be checked against what the process actually sent.
    """
    counts = _usb_extenders.transmit_counts()
    elapsed = max(1e-6, time.time() - counts["since"])
    rates = {name: round(value * 60.0 / elapsed, 2)
             for name, value in counts.items() if name not in ("since",)}
    return jsonify({"ok": True, "counts": counts, "per_minute": rates,
                    "elapsed_seconds": round(elapsed, 1),
                    "endpoints_known": len(_usb_extenders.state().get("devices") or [])})


@app.route("/api/usb_extenders/ranges", methods=["GET", "POST"])
def api_usb_extender_ranges():
    if request.method == "GET":
        return jsonify({"ok": True, "ranges": _usb_extenders.state()["ranges"], "maximum_hosts": omni_usb_extender.MAX_RANGE_HOSTS})
    data = request.get_json(silent=True) or {}; ranges = data.get("ranges")
    if not isinstance(ranges, list) or not all(isinstance(value, str) for value in ranges):
        return jsonify({"ok": False, "error": "ranges must be a list of IPv4 ranges"}), 400
    try:
        saved = _usb_extenders.set_ranges(ranges)
    except omni_usb_extender.ProtocolError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "ranges": saved, "maximum_hosts": omni_usb_extender.MAX_RANGE_HOSTS})


@app.route("/api/usb_extenders/discover_local", methods=["POST"])
def api_usb_extender_discover_local():
    data = request.get_json(silent=True) or {}; interface_ip, mask = _usb_extender_network(data)
    if not interface_ip:
        return jsonify({"ok": False, "error": "A valid interface_ip and subnet_mask are required."}), 400
    try:
        result = _usb_extenders.discover_local(interface_ip, mask)
        return jsonify({"ok": True, **result})
    except OSError:
        log.info("USB extender local broadcast setup failed for %s", interface_ip)
        return jsonify({"ok": False, "error": "Local USB discovery could not start on the selected interface."}), 400


@app.route("/api/usb_extenders/discover_ip", methods=["POST"])
def api_usb_extender_discover_ip():
    data = request.get_json(silent=True) or {}; interface_ip, mask = _usb_extender_network(data)
    if not interface_ip:
        return jsonify({"ok": False, "error": "A valid interface_ip and subnet_mask are required."}), 400
    result = _usb_extenders.discover_ip(data.get("ip", ""), interface_ip, mask)
    if result["status"] == "invalid_address":
        return jsonify({"ok": False, "status": "invalid_address", "error": "A valid IPv4 address is required."}), 400
    if result["status"] == "interface_unavailable":
        return jsonify({"ok": False, "status": result["status"], "error": "The selected network interface is unavailable."}), 400
    return jsonify({"ok": result["status"] == "found", **result})


@app.route("/api/usb_extenders/scan_ranges", methods=["POST"])
def api_usb_extender_scan_ranges():
    data = request.get_json(silent=True) or {}; interface_ip, mask = _usb_extender_network(data)
    if not interface_ip:
        return jsonify({"ok": False, "error": "A valid interface_ip and subnet_mask are required."}), 400
    ranges = data.get("ranges")
    if ranges is not None and (not isinstance(ranges, list) or not all(isinstance(value, str) for value in ranges)):
        return jsonify({"ok": False, "error": "ranges must be a list of IPv4 ranges"}), 400
    try:
        # Expand once and hand the targets to the worker so the range set is not
        # parsed twice for a single scan.
        targets = omni_usb_extender.parse_ranges(ranges if ranges is not None else _usb_extenders.state()["ranges"])
        _usb_extenders.start_range_scan(interface_ip, mask, targets=targets)
    except omni_usb_extender.ScanBusyError:
        return jsonify({"ok": False, "status": "busy", "error": "A USB range scan is already running."}), 409
    except omni_usb_extender.ProtocolError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "status": "started", "target_count": len(targets), "maximum_hosts": omni_usb_extender.MAX_RANGE_HOSTS})

@app.route("/api/usb_extenders/network", methods=["POST"])
def api_usb_extender_network_config():
    data = request.get_json(silent=True) or {}; interface_ip, interface_mask = _usb_extender_network(data)
    mac = str(data.get("mac") or "").strip(); mode = str(data.get("mode") or "").strip().lower()
    if not mac:
        return jsonify({"ok": False, "error": "An extender MAC is required."}), 400
    # An integrated E4521/D4511 USB endpoint responds to discovery, but nothing
    # establishes that its USB-side address may be reconfigured with the
    # standalone AT-OMNI-311/324 network command. Refuse rather than assume.
    classification, parent = _usb_endpoint_ownership(mac)
    if classification == "UNCONFIRMED":
        return jsonify({"ok": False, "status": "unconfirmed_endpoint",
                        "error": _USB_EXTENDER_COMMAND_ERRORS["unconfirmed_endpoint"]}), 409
    if parent and parent.get("parent_ip"):
        # Integrated endpoint: configured through the parent's own proven network
        # API, never with the standalone AT-OMNI-311/324 IP commands. The parent
        # is resolved here from the USB MAC, so a client cannot nominate one.
        return _api_integrated_network_config(mac, parent, data)
    # Standalone endpoints are configured over UDP, which does need a real source
    # interface; the parent-backed path above does not.
    if not interface_ip:
        return jsonify({"ok": False, "error": "A valid interface_ip and subnet_mask are required."}), 400
    result = _usb_extenders.configure_network(mac, mode, interface_ip, interface_mask, str(data.get("address") or ""), str(data.get("netmask") or ""), str(data.get("gateway") or ""))
    status = result.get("status")
    if status == "invalid_request":
        return jsonify({"ok": False, "status": status, "error": "Network configuration values are invalid."}), 400
    if status == "not_found":
        return jsonify({"ok": False, "status": status, "error": "The requested standalone extender is not known."}), 404
    if status in ("timeout", "protocol_error", "command_rejected", "interface_unavailable"):
        message = {"timeout": "The extender did not respond in time.",
                   "protocol_error": "The extender returned an invalid protocol response.",
                   "command_rejected": "The extender rejected the network command.",
                   "interface_unavailable": "The selected network interface is unavailable on this computer."}[status]
        return jsonify({"ok": False, "status": status, "error": message}), 502
    return jsonify({"ok": True, **result})


def _api_integrated_network_config(mac, parent, data):
    """Apply and verify an integrated USB network change via the parent API."""
    parent_ip = parent["parent_ip"]
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in ICRON_NETWORK_MODES:
        return jsonify({"ok": False, "status": "invalid_request",
                        "error": "mode must be one of " + ", ".join(ICRON_NETWORK_MODES) + "."}), 400
    # The USB MAC must actually belong to the resolved parent's icron interface.
    try:
        current = _omnistream_icron_network_get(parent_ip)
    except Exception as exc:
        log.info("Integrated USB network read failed for %s: %s", parent_ip, type(exc).__name__)
        return jsonify({"ok": False, "status": "parent_unreachable", "parent_ip": parent_ip,
                        "error": "Could not read the network configuration from " + parent_ip + "."}), 502
    if not current:
        return jsonify({"ok": False, "status": "unsupported_for_integrated", "parent_ip": parent_ip,
                        "error": "This parent device does not expose an integrated USB network interface."}), 409
    if _norm_usb_mac(current.get("macaddress")) != _norm_usb_mac(mac):
        log.warning("Integrated USB network refused: %s does not belong to %s", mac, parent_ip)
        return jsonify({"ok": False, "status": "mac_mismatch", "parent_ip": parent_ip,
                        "error": "That USB MAC does not belong to the resolved parent device."}), 409
    try:
        outcome = _omnistream_icron_network_set(parent_ip, mode,
                                                data.get("address"), data.get("netmask"), data.get("gateway"))
    except ValueError as exc:
        return jsonify({"ok": False, "status": "invalid_request", "error": str(exc)}), 400
    except Exception as exc:
        log.info("Integrated USB network write failed for %s: %s", parent_ip, type(exc).__name__)
        return jsonify({"ok": False, "status": "parent_unreachable", "parent_ip": parent_ip,
                        "error": parent_ip + " did not accept the network change."}), 502
    if not outcome["accepted"]:
        return jsonify({"ok": False, "status": "command_rejected", "parent_ip": parent_ip,
                        "error": parent_ip + " rejected the network change."}), 502
    # An ACK is not verification: read the parent back.
    verified, applied = False, None
    try:
        applied = _omnistream_icron_network_get(parent_ip)
        verified = bool(applied) and applied.get("mode") == mode
    except Exception as exc:
        log.info("Integrated USB network read-back failed for %s: %s", parent_ip, type(exc).__name__)
    if applied:
        # The USB address may have changed; identity stays the canonical MAC, so
        # the existing record is updated rather than a second one created.
        _record_usb_association(parent_ip, applied.get("macaddress"), None, applied.get("ipaddress"),
                                parent.get("parent_mac"))
        # _omnistream_icron_network_get already ingested the read-back through the
        # canonical path, so Configure reflects the change on its next render.
    return jsonify({
        "ok": True,
        "status": "configuration_verified" if verified else "command_accepted_unverified",
        "network_via": "parent",
        "parent_ip": parent_ip,
        "parent_hostname": parent.get("parent_hostname", ""),
        "requested_mode": mode,
        "applied": applied,
        "verified": verified,
    })


@app.route("/api/usb_extenders/network_config", methods=["GET"])
def api_usb_extender_read_network_config():
    """Current network configuration for one USB endpoint, read on demand.

    Read on demand rather than during polling, so the Configure modal can show
    authoritative values without adding traffic to the live-state loop.
    """
    mac = str(request.args.get("mac") or "").strip()
    if not mac:
        return jsonify({"ok": False, "error": "A USB MAC is required."}), 400
    try:
        mac = omni_usb_extender.normalize_mac(mac)
    except omni_usb_extender.ProtocolError:
        return jsonify({"ok": False, "error": "A valid USB MAC is required."}), 400
    classification, parent = _usb_endpoint_ownership(mac)
    if classification == "UNCONFIRMED":
        return jsonify({"ok": False, "status": "unconfirmed_endpoint",
                        "error": _USB_EXTENDER_COMMAND_ERRORS["unconfirmed_endpoint"]}), 409
    if parent and parent.get("parent_ip"):
        try:
            current = _omnistream_icron_network_get(parent["parent_ip"])
        except Exception as exc:
            return jsonify({"ok": False, "status": "parent_unreachable",
                            "error": "Could not read " + parent["parent_ip"] + ": " + type(exc).__name__}), 502
        if not current:
            return jsonify({"ok": False, "status": "unsupported_for_integrated",
                            "error": "This parent device does not expose an integrated USB network interface."}), 409
        current.pop("raw", None)
        return jsonify({"ok": True, "kind": "integrated", "mac": mac,
                        "parent_ip": parent["parent_ip"], "parent_hostname": parent.get("parent_hostname", ""),
                        **current})
    record = _usb_extenders.device(mac)
    if not record:
        return jsonify({"ok": False, "status": "not_found", "error": _USB_EXTENDER_COMMAND_ERRORS["not_found"]}), 404
    return jsonify({"ok": True, "kind": "standalone", "mac": mac,
                    "mode": (record.get("network_mode") or "").lower(),
                    "ipaddress": record.get("ip", ""),
                    "subnetmask": record.get("device_subnet_mask") or record.get("subnet_mask", ""),
                    "gateway": record.get("device_gateway") or record.get("gateway", ""),
                    "macaddress": record.get("mac", ""),
                    "modes": ["dhcp", "static"],
                    "source": "standalone_udp_query"})


@app.route("/api/usb_extenders/identify", methods=["POST"])
def api_usb_extender_identify():
    data = request.get_json(silent=True) or {}; mac = str(data.get("mac") or "").strip(); action = str(data.get("action") or "identify").strip().lower()
    if not mac:
        return jsonify({"ok": False, "error": "An extender MAC is required."}), 400
    if action not in ("identify", "on", "off"):
        return jsonify({"ok": False, "error": "action must be identify, on, or off."}), 400

    # Identify must light up the physical device. An endpoint that belongs to an
    # E4521/D4511 is identified through its parent's existing OmniStream identify;
    # only a true standalone AT-OMNI-311/324 gets the UDP extender Blink.
    classification, parent = _usb_endpoint_ownership(mac)
    if classification == "UNCONFIRMED":
        return jsonify({"ok": False, "status": "unconfirmed_endpoint",
                        "error": _USB_EXTENDER_COMMAND_ERRORS["unconfirmed_endpoint"]}), 409
    if parent and parent.get("parent_ip"):
        if not _usb_extenders.device(mac):
            return jsonify({"ok": False, "status": "not_found", "error": _USB_EXTENDER_COMMAND_ERRORS["not_found"]}), 404
        if action != "identify":
            # The OmniStream identify is a one-shot method; there is no discrete
            # on/off, and the standalone UDP Blink must not be aimed at a USB IP
            # that belongs to an E4521/D4511.
            return jsonify({"ok": False, "status": "unsupported_for_integrated", "identify_via": "parent",
                            "error": "Discrete blink on/off is not available for an integrated E4521/D4511 USB endpoint. Use Identify."}), 409
        try:
            ok, response = _omnistream_identify(parent["parent_ip"])
        except Exception as exc:
            log.info("Parent identify failed for %s via %s: %s", mac, parent["parent_ip"], type(exc).__name__)
            return jsonify({"ok": False, "status": "parent_unreachable", "identify_via": "parent",
                            "parent_ip": parent["parent_ip"],
                            "error": f"The parent device {parent['parent_ip']} did not accept the identify command."}), 502
        if not ok:
            return jsonify({"ok": False, "status": "parent_rejected", "identify_via": "parent",
                            "parent_ip": parent["parent_ip"], "response": response,
                            "error": f"The parent device {parent['parent_ip']} rejected the identify command."}), 502
        return jsonify({"ok": True, "status": "command_accepted", "command_accepted": True,
                        "identify_via": "parent", "parent_ip": parent["parent_ip"], "response": response})

    if action == "on": result = _usb_extenders.blink(mac, True)
    elif action == "off": result = _usb_extenders.blink(mac, False)
    elif action == "identify": result = _usb_extenders.identify(mac, data.get("duration", 5))
    else: return jsonify({"ok": False, "error": "action must be identify, on, or off."}), 400
    if result.get("status") == "invalid_request": return jsonify({"ok": False, "status": "invalid_request", "error": "Identify duration must be between 0 and 30 seconds."}), 400
    if not result.get("command_accepted"):
        status = result.get("status")
        code = 404 if status == "not_found" else (409 if status == "interface_unknown" else 502)
        return jsonify({"ok": False, "status": status, "identify_via": "extender", "error": _USB_EXTENDER_COMMAND_ERRORS.get(status, "The extender did not accept the identify command.")}), code
    return jsonify({"ok": True, "identify_via": "extender", **result})


@app.route("/api/usb_extenders/reboot", methods=["POST"])
def api_usb_extender_reboot():
    data = request.get_json(silent=True) or {}; mac = str(data.get("mac") or "").strip()
    if not mac: return jsonify({"ok": False, "error": "An extender MAC is required."}), 400
    # Never send a standalone extender reboot to a USB identity that belongs to an
    # E4521/D4511; rebooting that endpoint is the parent device's concern.
    classification, parent = _usb_endpoint_ownership(mac)
    if classification == "UNCONFIRMED":
        return jsonify({"ok": False, "status": "unconfirmed_endpoint",
                        "error": _USB_EXTENDER_COMMAND_ERRORS["unconfirmed_endpoint"]}), 409
    if parent and parent.get("parent_ip"):
        # Reuse the established parent reboot rather than sending the standalone
        # extender reboot to an integrated USB endpoint.
        if not _usb_extenders.device(mac):
            return jsonify({"ok": False, "status": "not_found", "error": _USB_EXTENDER_COMMAND_ERRORS["not_found"]}), 404
        outcome = _omnistream_reboot(parent["parent_ip"])
        if not outcome.get("ok"):
            return jsonify({"ok": False, "status": "parent_reboot_failed", "reboot_via": "parent",
                            "parent_ip": parent["parent_ip"],
                            "error": f"The parent device {parent['parent_ip']} did not accept the reboot command."}), 502
        return jsonify({"ok": True, "status": "command_accepted", "command_accepted": True,
                        "reboot_via": "parent", "parent_ip": parent["parent_ip"],
                        "parent_hostname": parent.get("parent_hostname", ""),
                        "response": outcome.get("response")})
    result = _usb_extenders.reboot(mac)
    if not result.get("command_accepted"):
        status = result.get("status")
        code = 404 if status == "not_found" else (409 if status == "interface_unknown" else 502)
        return jsonify({"ok": False, "status": status, "error": _USB_EXTENDER_COMMAND_ERRORS.get(status, "The extender did not accept the reboot command.")}), code
    return jsonify({"ok": True, **result})

# ---------------- production standalone USB routing ----------------
# Serves every combination the UDP transaction owns: AT-OMNI-311 <-> AT-OMNI-324,
# and the two mixed combinations whose Pair and Unpair were verified on hardware.
# E4521 <-> D4511 is refused here because it belongs to the established usb_icron
# path. Mixed routes are enabled so their USB data plane can be validated
# physically; that validation is NOT implied by a successful Pair.
#
# Hardware-derived verification policy: on the bench both endpoints agreed on the
# very first read-back (attempt 1) with ACK latency of 1.5-2.8 ms and no
# measurable propagation delay. A small bounded retry is kept purely as margin.
USB_ROUTE_VERIFY_ATTEMPTS = 3
USB_ROUTE_VERIFY_DELAY = 0.15


def _standalone_route_request():
    """Resolve a Matrix route request to two validated standalone endpoints.

    Ownership and eligibility are resolved from the server's own inventory; a
    client cannot nominate an arbitrary address or bypass classification.
    """
    data = _bench_request_data()
    if not data:
        return None, None, (jsonify({"ok": False, "error": "A JSON body with lex_mac and rex_mac is required."}), 400)
    lex_mac, lex_error = _bench_mac_field(data, "lex_mac")
    rex_mac, rex_error = _bench_mac_field(data, "rex_mac")
    problems = [m for m in (lex_error, rex_error) if m]
    if problems:
        return None, None, (jsonify({"ok": False, "error": " ".join(problems)}), 400)
    if lex_mac == rex_mac:
        return None, None, (jsonify({"ok": False, "error": "lex_mac and rex_mac must differ."}), 400)
    kinds = {}
    # One derived view for both endpoints: the same object the Matrix renders, so
    # a cell that is enabled on screen is not refused here for a different reason.
    live_view = {_norm_usb_mac(d.get("mac")): d for d in (_usb_extender_view().get("devices") or [])}
    for label, mac, expected in (("lex_mac", lex_mac, omni_usb_extender.HOST_MODEL),
                                 ("rex_mac", rex_mac, omni_usb_extender.DEVICE_MODEL)):
        record = _usb_extenders.device(mac)
        if not record:
            return None, None, (jsonify({
                "ok": False, "status": "unknown_endpoint", "mac": mac,
                "error": f"{label} {mac} is not a known USB endpoint."}), 404)
        classification, parent = _usb_endpoint_ownership(mac)
        if classification == "UNCONFIRMED":
            return None, None, (jsonify({
                "ok": False, "status": "unconfirmed_endpoint", "mac": mac,
                "error": _USB_EXTENDER_COMMAND_ERRORS["unconfirmed_endpoint"]}), 409)
        # Both families report their USB role the same way over Advanced Query,
        # which is what makes one transaction serve every UDP-controlled route.
        if record.get("device_type") != expected:
            return None, None, (jsonify({
                "ok": False, "status": "wrong_device_type", "mac": mac,
                "error": (f"{label} must be a {_display_model(expected)}; "
                          f"{mac} reports {_display_model(record.get('device_type')) or 'unknown'}.")}), 409)
        if not record.get("pairing_eligible"):
            return None, None, (jsonify({
                "ok": False, "status": "not_eligible", "mac": mac,
                "error": f"{mac} is not pairing eligible on the interface it was discovered from."}), 409)
        # Liveness comes from the derived view, not from the raw UDP record: an
        # integrated endpoint is never UDP-polled, so its UDP record is always
        # cold and reading it here would refuse every integrated endpoint.
        derived = live_view.get(_norm_usb_mac(mac)) or {}
        if not derived.get("online", record.get("online")):
            return None, None, (jsonify({
                "ok": False, "status": "offline", "mac": mac,
                "liveness_source": derived.get("liveness_source", "udp_query"),
                "error": f"{mac} is not responding."}), 409)
        kinds[label] = "integrated" if classification == "INTEGRATED" else "standalone"

    # The capability table decides which combinations may be routed at all, so an
    # integrated-to-integrated pair never reaches the UDP transaction: that route
    # belongs to the established usb_icron path.
    # Same rule the cell uses, so an enabled cell and the endpoint behind it
    # cannot disagree: the endpoints' own networks decide, not the controller's.
    # The endpoints' own networks decide, taken from the derived view: an
    # integrated endpoint's Icron address and mask come from the parent `net`
    # API, and its raw UDP record carries neither. Reading the record alone is
    # what let a cross-subnet route through.
    entries = {}
    for label, mac in (("lex_mac", lex_mac), ("rex_mac", rex_mac)):
        record = _usb_extenders.device(mac) or {}
        derived = live_view.get(_norm_usb_mac(mac)) or {}
        entries[label] = {"usb_mac": mac,
                          "usb_ip": derived.get("usb_ip") or record.get("ip", ""),
                          "device_subnet_mask": record.get("device_subnet_mask", ""),
                          "subnet_mask": (record.get("device_subnet_mask")
                                          or derived.get("subnet_mask")
                                          or record.get("subnet_mask", ""))}
    verdict, network_detail = _usb_route_network_check(entries["lex_mac"], entries["rex_mac"])
    if verdict != "OK":
        # Checked here, before anything is transmitted and before any existing
        # route is released, so a refused reassignment leaves the working route
        # exactly as it was.
        return None, None, (jsonify({
            "ok": False, "status": verdict.lower(), "state": verdict, "network": network_detail,
            "error": ("These two USB endpoints are on different IP subnets. A USB route is not "
                      "carried across a router." if verdict == "NETWORK_MISMATCH" else
                      "The USB address or subnet mask of one endpoint has not been read, so this "
                      "route cannot be shown to be legal.")}), 409)
    capability = USB_ROUTE_CAPABILITY.get((kinds["lex_mac"], kinds["rex_mac"]), {})
    if capability.get("control_path") != "standalone_udp" or not capability.get("enabled"):
        return None, None, (jsonify({
            "ok": False, "status": "not_supported",
            "combination": f"{kinds['lex_mac']} to {kinds['rex_mac']}",
            "error": ("This combination is not routed through the USB extender transaction. "
                      "E4521 to D4511 routing uses the established OmniStream USB pairing.")}), 409)
    return lex_mac, rex_mac, None


def _standalone_route_response(outcome, operation):
    verification = outcome.get("verification") or {}
    status = outcome.get("status")
    body = {"ok": status == "VERIFIED_SUCCESS", "operation": operation, "status": status,
            "reason": outcome.get("reason"), "commands": outcome.get("command"),
            "verification_attempts": outcome.get("verification_attempts"),
            "routed": verification.get("routed"), "owner": verification.get("owner"),
            "rollback": outcome.get("rollback"),
            # Present when the request moved an endpoint off another host.
            "reassigned_from": outcome.get("reassigned_from")}
    if status == "VERIFIED_SUCCESS":
        return jsonify(body)
    if status in ("ALREADY_ROUTED", "ALREADY_UNROUTED"):
        body["ok"] = True
        return jsonify(body)
    if status == "CONFLICT":
        body["owner_mac"] = outcome.get("owner_mac")
        body["error"] = (f"That endpoint is already owned by {outcome.get('owner_mac')} and could "
                         "not be reassigned.")
        return jsonify(body), 409
    if status == "REASSIGN_RELEASE_FAILED":
        body["owner_mac"] = outcome.get("owner_mac")
        body["error"] = ("The endpoint could not be released from its current host, so the route "
                         "was not changed.")
        return jsonify(body), 502
    if status in ("FAILED_ROLLED_BACK", "FAILED_ROLLBACK_UNVERIFIED"):
        body["reassigned_from"] = outcome.get("reassigned_from")
        body["previous_owner_intact"] = outcome.get("previous_owner_intact")
        body["error"] = ("The new route could not be established. "
                         + ("The previous route was restored."
                            if status == "FAILED_ROLLED_BACK"
                            else "The previous route could not be confirmed restored."))
        return jsonify(body), 502
    if status == "PEER_LIMIT":
        body["error"] = (f"This {_display_model(omni_usb_extender.HOST_MODEL)} already has its "
                         f"maximum of {outcome.get('peer_limit')} peers.")
        return jsonify(body), 409
    body["error"] = {"UNVERIFIABLE": "The endpoints could not be read, so the route was not changed.",
                     "INCONSISTENT": "The two endpoints disagree; refusing to act on an inconsistent route.",
                     "NOT_ELIGIBLE": "An endpoint is not pairing eligible.",
                     "COMMAND_TIMEOUT": "An endpoint did not respond to the command.",
                     "REJECTED": "An endpoint rejected the command.",
                     "COMMAND_ACCEPTED_UNVERIFIED": "The command was accepted but the route could not be verified.",
                     "VERIFICATION_FAILED": "The command was accepted but the endpoints do not show the expected route.",
                     }.get(status, "The route operation did not complete.")
    return jsonify(body), 502


@app.route("/api/usb_route/resolve", methods=["POST"])
def api_usb_route_resolve():
    """Resolve a route request without touching any device.

    Sends nothing. It runs the same gate the mutating endpoints run, then reports
    what the server independently resolved for both endpoints, so identity and
    provider selection can be confirmed before a route is created.
    """
    lex_mac, rex_mac, error = _standalone_route_request()
    if error:
        return error

    # The one derived view, so this reports exactly what the gate judged and what
    # the Matrix renders -- not a second opinion assembled from raw records.
    derived = {_norm_usb_mac(d.get("mac")): d for d in (_usb_extender_view().get("devices") or [])}

    def describe(mac, role):
        record = _usb_extenders.device(mac) or {}
        view = derived.get(_norm_usb_mac(mac)) or {}
        classification, parent = _usb_endpoint_ownership(mac)
        return {
            "role": role,
            "usb_mac": mac,
            "ip": view.get("ip") or record.get("ip", ""),
            # The UDP-reported type is a USB role, not a product; the associated
            # parent supplies the product identity for an integrated endpoint.
            "model": view.get("display_model") or _display_model(record.get("device_type")),
            "udp_reported_type": record.get("device_type", ""),
            "usb_role": "USB Host / LEX" if role == "LEX" else "USB Device / REX",
            "classification": classification,
            "kind": "integrated" if classification == "INTEGRATED" else "standalone",
            "parent_ip": (parent or {}).get("parent_ip", ""),
            "parent_hostname": view.get("parent_hostname", ""),
            "online": bool(view.get("online", record.get("online"))),
            "liveness_source": view.get("liveness_source", ""),
            "pairing_eligible": bool(record.get("pairing_eligible")),
            # The endpoint's own USB network, which is what decides eligibility.
            "usb_ip": view.get("usb_ip") or record.get("ip", ""),
            "subnet_mask": view.get("subnet_mask") or record.get("device_subnet_mask", ""),
        }

    lex, rex = describe(lex_mac, "LEX"), describe(rex_mac, "REX")

    def as_entry(described, mac):
        return {"kind": described["kind"], "classification": described["classification"],
                "usb_mac": mac, "online": described["online"],
                "pairing_eligible": described["pairing_eligible"],
                "endpoint_ip": described["usb_ip"], "endpoint_mask": described["subnet_mask"]}

    capability = _usb_route_capability(as_entry(lex, lex_mac), as_entry(rex, rex_mac))
    return jsonify({"ok": True, "lex": lex, "rex": rex,
                    "combination": f"{lex['kind']} to {rex['kind']}",
                    "mixed": lex["kind"] != rex["kind"],
                    "capability": capability,
                    "provider": capability.get("control_path"),
                    "transaction": "dual_endpoint_udp"})


@app.route("/api/usb_route/pair", methods=["POST"])
@_audited("usb_route_pair")
@_with_usb_route_lock
def api_usb_route_pair():
    """Create a USB extender route (standalone, or a bench-verified mixed pair)."""
    lex_mac, rex_mac, error = _standalone_route_request()
    if error:
        return error
    outcome = _usb_extenders.pair_route(lex_mac, rex_mac,
                                        verify_attempts=USB_ROUTE_VERIFY_ATTEMPTS,
                                        verify_delay=USB_ROUTE_VERIFY_DELAY)
    log.info("[USB ROUTE] pair %s -> %s: %s", lex_mac, rex_mac, outcome.get("status"))
    # The transaction already re-read both endpoints, so pairing state is current;
    # reset the cadence so the next page poll renders it without waiting.
    with _usb_live_lock:
        _usb_pairing_state["last_run"] = time.time()
    return _standalone_route_response(outcome, "pair")


@app.route("/api/usb_route/unpair", methods=["POST"])
@_audited("usb_route_unpair")
@_with_usb_route_lock
def api_usb_route_unpair():
    """Remove a USB extender route (standalone, or a bench-verified mixed pair)."""
    lex_mac, rex_mac, error = _standalone_route_request()
    if error:
        return error
    outcome = _usb_extenders.unpair_route(lex_mac, rex_mac,
                                          verify_attempts=USB_ROUTE_VERIFY_ATTEMPTS,
                                          verify_delay=USB_ROUTE_VERIFY_DELAY)
    log.info("[USB ROUTE] unpair %s -> %s: %s", lex_mac, rex_mac, outcome.get("status"))
    with _usb_live_lock:
        _usb_pairing_state["last_run"] = time.time()
    return _standalone_route_response(outcome, "unpair")


# ---------------- AT-OMNI-311/324 routing bench diagnostics ----------------
# Bench validation only. These endpoints exist so the standalone Pair/Unpair
# direction and Advanced Query propagation can be measured against physical
# hardware before any Matrix routing is enabled. They are NOT production routing:
# the USB Matrix exposes no route cell for standalone units, nothing calls these
# automatically, and every mutating call requires an explicit confirmation token.
#
# Deliberately absent: Force Pair (opcode unconfirmed), Unpair All, and any
# mixed-family combination (E4521 to OMNI-324, OMNI-311 to D4511).
USB_BENCH_VERIFY_ATTEMPTS = 6      # tune during bench testing
USB_BENCH_VERIFY_DELAY = 0.5       # seconds between read-back attempts
USB_BENCH_MAX_ATTEMPTS = 20
USB_BENCH_MAX_DELAY = 2.0


def _bench_endpoint_summary(mac):
    """Sanitized identity plus a fresh authoritative Advanced Query read."""
    device = _usb_extenders.device(mac) or {}
    read = _usb_extenders.read_pairing(mac)
    return {
        "mac": device.get("mac") or omni_usb_extender.normalize_mac(mac) if mac else "",
        "ip": device.get("ip", ""),
        "device_type": device.get("device_type", ""),
        "network_relation": device.get("network_relation", ""),
        "advanced_query": {
            "status": read.get("status"),
            "reason": read.get("reason"),
            "device_type": read.get("device_type"),
            "paired_macs": read.get("paired_macs"),
            "authoritative": bool(read.get("authoritative")),
        },
    }


def _bench_request_data():
    """Read a bench request body without depending on the Content-Type header.

    These endpoints are driven from command-line tools, where it is easy to post
    a JSON body without `Content-Type: application/json`. Flask's `get_json`
    ignores such a body, which previously left every field empty and produced a
    misleading "invalid MAC" error. Parse the raw body as JSON regardless of the
    declared type, and accept a form-encoded body as well. Validation of the
    values themselves is unchanged and still fails closed.
    """
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    data = request.get_json(force=True, silent=True)
    if isinstance(data, dict):
        return data
    if request.form:
        return request.form.to_dict()
    return {}


def _bench_mac_field(data, field):
    """Normalize one MAC field, distinguishing missing from malformed.

    Uses the application's single canonical MAC normalizer; there is no second
    MAC parser here. Accepts any form that normalizer accepts (colon, hyphen,
    dotted, or bare hex, in either case).
    """
    raw = data.get(field)
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None, f"{field} is required."
    try:
        return omni_usb_extender.normalize_mac(text), None
    except omni_usb_extender.ProtocolError:
        return None, f"{field} {text!r} is not a valid MAC address."


def _bench_resolve(data):
    """Validate a bench request: both endpoints must be proven standalone.

    Returns (host_mac, device_mac, error_response). Enforces AT-OMNI-311 as the
    host and AT-OMNI-324 as the device so no mixed-family pair can be attempted.
    """
    if not data:
        return None, None, (jsonify({
            "ok": False, "status": "invalid_request",
            "error": ("No request body was received. Send a JSON object with host_mac and device_mac, "
                      "ideally with Content-Type: application/json.")}), 400)
    host_mac, host_error = _bench_mac_field(data, "host_mac")
    device_mac, device_error = _bench_mac_field(data, "device_mac")
    problems = [message for message in (host_error, device_error) if message]
    if problems:
        return None, None, (jsonify({"ok": False, "status": "invalid_request", "error": " ".join(problems)}), 400)
    if host_mac == device_mac:
        return None, None, (jsonify({"ok": False, "error": "host_mac and device_mac must differ."}), 400)
    for label, mac, expected in (("host_mac", host_mac, omni_usb_extender.HOST_MODEL),
                                 ("device_mac", device_mac, omni_usb_extender.DEVICE_MODEL)):
        record = _usb_extenders.device(mac)
        if not record:
            return None, None, (jsonify({"ok": False, "error": f"{label} {mac} has not been discovered."}), 404)
        classification, parent = _usb_endpoint_ownership(mac)
        if classification != "STANDALONE":
            return None, None, (jsonify({
                "ok": False, "status": "not_standalone", "mac": mac, "classification": classification,
                "parent_ip": (parent or {}).get("parent_ip", ""),
                "error": (f"{label} {mac} is not a confirmed standalone extender. Bench routing is limited to "
                          "standalone HW-OMNI-311/324 pairs.")}), 409)
        if record.get("device_type") != expected:
            return None, None, (jsonify({
                "ok": False, "status": "wrong_device_type", "mac": mac,
                "error": (f"{label} must be a {_display_model(expected)}; "
                          f"{mac} reports {_display_model(record.get('device_type')) or 'unknown'}.")}), 409)
    return host_mac, device_mac, None


def _bench_attempts(data):
    """Resolve bounded read-back retry settings.

    An explicit 0 is honoured (immediate single read, no delay) rather than being
    treated as "unset", so a bench run can ask for no retry at all.
    """
    raw_attempts, raw_delay = data.get("verify_attempts"), data.get("verify_delay")
    try:
        attempts = USB_BENCH_VERIFY_ATTEMPTS if raw_attempts is None else int(raw_attempts)
    except (TypeError, ValueError):
        attempts = USB_BENCH_VERIFY_ATTEMPTS
    try:
        delay = USB_BENCH_VERIFY_DELAY if raw_delay is None else float(raw_delay)
    except (TypeError, ValueError):
        delay = USB_BENCH_VERIFY_DELAY
    return (max(1, min(USB_BENCH_MAX_ATTEMPTS, attempts)),
            max(0.0, min(USB_BENCH_MAX_DELAY, delay)))


@app.route("/api/diagnostics/usb_endpoints", methods=["GET"])
def api_diagnostics_usb_endpoints():
    """Read-only classification table for every discovered USB endpoint.

    Sends nothing to any device: it renders the shared association pipeline's
    current result so an operator can see exactly why each endpoint resolved the
    way it did, and which units still block classification.
    """
    context = _usb_parent_context()
    view = _usb_extender_view(context=context)
    rows = []
    for device in view.get("devices") or []:
        rows.append({
            "usb_mac": device.get("mac", ""),
            "usb_ip": device.get("usb_ip", ""),
            "udp_reported_type": device.get("udp_reported_type", ""),
            "udp_role_code": device.get("udp_role_code"),
            "classification": device.get("classification", ""),
            "parent_model": device.get("parent_model", ""),
            "parent_ip": device.get("parent_ip", ""),
            "parent_hostname": device.get("parent_hostname", ""),
            "role": device.get("parent_role") or {"AT-OMNI-311": "LEX", "AT-OMNI-324": "REX"}.get(device.get("device_type"), ""),
            "control_owner": {"INTEGRATED": "parent_omnistream", "STANDALONE": "standalone_udp"}.get(
                device.get("classification"), "none"),
            "pairing_source": device.get("pairing_source", ""),
            "association_source": device.get("association_source", ""),
            "reason": device.get("classification_reason", ""),
            "online": bool(device.get("online")),
        })
    rows.sort(key=lambda row: (row["classification"], row["usb_mac"]))
    counts = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    return jsonify({
        "ok": True,
        "endpoints": rows,
        "counts": counts,
        "association_complete": context["complete"],
        "unassociated_units": context["pending"],
    })


@app.route("/api/diagnostics/usb_route/state", methods=["POST"])
def api_bench_usb_route_state():
    """Read-only bench diagnostic. Sends only Query/Advanced Query."""
    data = _bench_request_data()
    host_mac, device_mac, error = _bench_resolve(data)
    if error:
        return error
    began = time.time()
    host = _bench_endpoint_summary(host_mac)
    device = _bench_endpoint_summary(device_mac)
    state = _usb_extenders.get_route_state(host_mac, device_mac)
    return jsonify({
        "ok": True, "operation": "route_state", "host": host, "device": device,
        "route_state": {"status": state.get("status"), "routed": state.get("routed"),
                        "owner": state.get("owner"), "reason": state.get("reason"),
                        "endpoint": state.get("endpoint"),
                        "host_lists_device": state.get("host_lists_device"),
                        "device_claims_host": state.get("device_claims_host")},
        "elapsed_seconds": round(time.time() - began, 3),
    })


def _bench_route_operation(operation, confirm_token):
    # Same body parsing as the read-only diagnostic. The confirmation gate below
    # is unchanged and just as strict; only how the body is read was fixed.
    data = _bench_request_data()
    if str(data.get("confirm") or "") != confirm_token:
        return jsonify({"ok": False, "status": "confirmation_required",
                        "error": f'This bench operation changes physical pairing. Resend with "confirm": "{confirm_token}".'}), 400
    host_mac, device_mac, error = _bench_resolve(data)
    if error:
        return error
    attempts, delay = _bench_attempts(data)
    began = time.time()
    pre_host = _bench_endpoint_summary(host_mac)
    pre_device = _bench_endpoint_summary(device_mac)
    runner = _usb_extenders.pair_route if operation == "pair" else _usb_extenders.unpair_route
    result = runner(host_mac, device_mac, verify_attempts=attempts, verify_delay=delay)
    post_host = _bench_endpoint_summary(host_mac)
    post_device = _bench_endpoint_summary(device_mac)
    verification = result.get("verification") or {}
    log.info("[USB BENCH] %s %s -> %s: %s (attempts=%s)", operation, host_mac, device_mac,
             result.get("status"), result.get("verification_attempts"))
    return jsonify({
        "ok": result.get("status") == "VERIFIED_SUCCESS",
        "operation": operation,
        "status": result.get("status"),
        "reason": result.get("reason"),
        "pre": {"host": pre_host, "device": pre_device},
        "command": result.get("command"),
        "post": {"host": post_host, "device": post_device},
        "verification": {"status": verification.get("status"), "routed": verification.get("routed"),
                         "owner": verification.get("owner"),
                         "host_lists_device": verification.get("host_lists_device"),
                         "device_claims_host": verification.get("device_claims_host")},
        "verification_attempts": result.get("verification_attempts"),
        "verify_settings": {"attempts": attempts, "delay_seconds": delay},
        "elapsed_seconds": round(time.time() - began, 3),
    })


@app.route("/api/diagnostics/usb_route/pair", methods=["POST"])
@_audited("bench_usb_route_pair")
@_with_usb_route_lock
def api_bench_usb_route_pair():
    """Bench Pair. Normal Pair opcode only; never Force Pair."""
    return _bench_route_operation("pair", "PAIR")


@app.route("/api/diagnostics/usb_route/unpair", methods=["POST"])
@_audited("bench_usb_route_unpair")
@_with_usb_route_lock
def api_bench_usb_route_unpair():
    """Bench Unpair. Specific Unpair opcode only; never Unpair All."""
    return _bench_route_operation("unpair", "UNPAIR")


# ---------------- scan ----------------
# The USB side has always bounded its range expansion; this one materialised
# whatever it was given, so a /8 built 16 million strings and a malformed range
# like 10.0.0.1-99999999 built a hundred million. Same ceiling, same parser.
MAX_SCAN_HOSTS = omni_usb_extender.MAX_RANGE_HOSTS


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
                # Sized arithmetically before anything is materialised, so an
                # over-wide prefix is refused rather than expanded.
                if net.num_addresses > MAX_SCAN_HOSTS + 2:
                    raise ValueError(f"{net} covers {net.num_addresses} addresses; "
                                     f"the maximum is {MAX_SCAN_HOSTS}")
                out.extend(str(h) for h in net.hosts()); continue
            except ValueError:
                raise
            except Exception: pass
        m = re.match(r"^(\d+\.\d+\.\d+)\.(\d+)-(\d+)$", p)
        if m:
            base,a,b = m.group(1), int(m.group(2)), int(m.group(3))
            # The octets were never checked, so 10.0.0.1-99999999 built a
            # hundred million strings that are not addresses.
            if not (0 <= a <= 255 and 0 <= b <= 255):
                raise ValueError(f"{p} is not a valid address range")
            lo,hi = (a,b) if a<=b else (b,a)
            out += [f"{base}.{i}" for i in range(lo,hi+1)]; continue
        try:
            ipaddress.ip_address(p); out.append(p)
        except Exception: pass
    seen=set(); ret=[]
    for ip in out:
        if ip not in seen: seen.add(ip); ret.append(ip)
    if len(ret) > MAX_SCAN_HOSTS:
        raise ValueError(f"{len(ret)} addresses requested; the maximum is {MAX_SCAN_HOSTS}")
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

    # LLDP neighbour, from the same conversation the scan is already having with
    # this device. Switch topology is derived from the inventory, so it must be
    # recorded when the inventory is built; adding a poll of its own for it would
    # be traffic the scan has already paid for.
    lldp_fields = {}
    try:
        lldp_resp = _ws_send_recv(url,
            {"id": "lldp-get", "username": user, "password": pwd, "config_get": "lldp"},
            timeout=min(timeout, 1.5))
        if lldp_resp and not lldp_resp.get("error"):
            lldp_fields = _lldp_chassis_from_config(lldp_resp.get("config") or {})
    except Exception:
        # A device that does not answer LLDP simply has none. It is never an
        # error, and it never counts as a second switch.
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
        **lldp_fields,
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
    try:
        ips = _expand_targets(targets)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
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

    # Merge with cache, identity first: a fresh answer decides where a device is.
    existing = _load_cache()
    merged = []
    for u in _reconcile_discovered_units(existing, units):
        mac = (u.get("mac") or "").lower()
        if not u.get("timezone"):
            # Identity first, and an address match only for a record that has no
            # identity to match on. The disjunction below used to let a record
            # that merely shared an address win over the one with the same MAC,
            # and it copies a whole systeminfo blob.
            matched = next((old for old in existing
                            if mac and (old.get("mac") or "").lower() == mac), None)
            if matched is None and not mac:
                matched = next((old for old in existing if old.get("ip") == u.get("ip")), None)
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
    
    # A scan reports no usb_mac/usb_type, so restore learned association onto the
    # rebuilt records before persisting. Pure in-memory; no device traffic.
    _preserve_usb_association_fields(merged)
    _save_cache(merged); _write_csv(merged)

    # An explicit Scan is the operator saying "make what you show me match the
    # hardware now", so the USB side is re-read as well.
    #
    # It never was before: /api/scan rebuilt the device cache and stopped, and
    # the only caller of the parent map was startup and the USB discover
    # endpoint -- which itself only looked at units that had no association yet.
    # An endpoint that was already known therefore never got asked again, so a
    # fact it had not supplied the first time (its Icron firmware, its network
    # mode, a changed USB address) stayed missing until the operator used Clear
    # Units, which is exactly what Clear Units is not for.
    #
    # Dispatched on the background executor so the scan response still returns
    # as soon as the device sweep is done, and bounded the same way the
    # background refresh is.
    def _enrich_usb_after_scan():
        try:
            _refresh_usb_parent_map(force=True)
        except Exception as exc:
            log.info("[SCAN] USB parent enrichment failed: %s", type(exc).__name__)
        try:
            _refresh_icron_network_config(force=True)
        except Exception as exc:
            log.info("[SCAN] Icron network refresh failed: %s", type(exc).__name__)

    try:
        _usb_extenders._executor.submit(_enrich_usb_after_scan)
    except Exception as exc:
        log.info("[SCAN] could not schedule USB enrichment: %s", type(exc).__name__)
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
        _save_scan_results(scan_results)
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
        data = _read_scan_results()
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
        log.debug(f"[API/STATE] Loaded {len(units)} units from cache for matrix")
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
            # Whether this decoder's picture is a Multiview composition rather
            # than a routed encoder. Derived from the video input the scan
            # already reads, so the matrix learns this for no extra device
            # traffic at all.
            "multiview_name": omni_multiview.active_multiview_name(
                d.get("video_input") or src.get("video_input")),
            "multiview_active": bool(omni_multiview.active_multiview_name(
                d.get("video_input") or src.get("video_input"))),
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
    
    log.debug(f"[API/STATE] After merge: {len(enc_mapped)} encoders, {len(dec_mapped)} decoders")
    if enc_mapped:
        log.debug(f"[API/STATE] Sample encoder: {enc_mapped[0]}")
    if dec_mapped:
        log.debug(f"[API/STATE] Sample decoder before overlay: {dec_mapped[0]}")

    # Overlay live matrix state (if available) to keep UI in sync after routing
    routes = {}
    if HAS_MATRIX:
        try:
            mstate = omni_matrix_logic.list_state()
            log.debug(f"[API/STATE] Matrix logic list_state returned: encoders={len(mstate.get('encoders') or [])}, decoders={len(mstate.get('decoders') or [])}, routes={len(mstate.get('routes') or {})}")
        except Exception as e:
            log.warning(f"[API/STATE] list_state failed: {e}")
            mstate = None
        if mstate:
            routes = mstate.get("routes") or routes
            log.debug(f"[API/STATE] Routes from list_state: {routes}")
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
                log.debug(f"[API/STATE] Overlaying live decoder {d.get('ip')}: {live}")
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
                # The overlay above replaces `video_input` with what the device
                # last reported, and the Multiview verdict was derived from the
                # cached one. Leaving it there made the matrix say a decoder was
                # not in Multiview while naming a Multiview as its video input --
                # measured on 192.168.100.32 after a route and a Show. Derived
                # again here, from the value that actually survived, because a
                # derived field must never outlive what it was derived from.
                name = omni_multiview.active_multiview_name(
                    dec_mapped[i].get("video_input"))
                dec_mapped[i]["multiview_name"] = name
                dec_mapped[i]["multiview_active"] = bool(name)

    if dec_mapped:
        log.debug(f"[API/STATE] Sample decoder after overlay: {dec_mapped[0]}")

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
    # Named fields, not the whole body: a verbatim dump logs whatever a caller
    # sends, including a credential if one is ever added to this request.
    log.info("[ROUTE] request decoder=%s encoder=%s mode=%s",
             data.get("decoder"), data.get("encoder"), data.get("mode"))
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

    # ---- a decoder that is currently compositing a Multiview --------------
    #
    # Routing a conventional source to it is allowed, but it has to stop
    # compositing first, and the caller has to have said so. Without
    # `exit_multiview` the request is refused rather than silently changing
    # what is on screen in a way the matrix did not describe.
    multiview_exit = None
    try:
        mv_state, _mv_error = _decoder_state(decoder)
    except Exception:
        mv_state = None
    active_multiview = None
    if mv_state is not None:
        active_multiview = omni_multiview.active_multiview_name(
            ((mv_state.get("hdmi_output") or {}).get("video") or {}).get("input"))
    if active_multiview:
        if not data.get("exit_multiview"):
            return jsonify({
                "ok": False,
                "status": "MULTIVIEW ACTIVE",
                "multiview": active_multiview,
                "error": ("%s is currently showing the Multiview %s. Routing a "
                          "source here will exit Multiview."
                          % (decoder, active_multiview)),
            }), 409
        ok, steps, released, message = _exit_active_multiview(
            decoder, active_multiview, mv_state)
        multiview_exit = {"multiview": active_multiview, "steps": steps,
                          "released": released, "ok": ok}
        if not ok:
            log.warning("[ROUTE] could not leave Multiview %s on %s: %s",
                        active_multiview, decoder, message)
            return jsonify({"ok": False, "status": "FAILED — MULTIVIEW NOT EXITED",
                            "error": message, "multiview": multiview_exit}), 200
        log.info("[ROUTE] left Multiview %s on %s, released %s",
                 active_multiview, decoder, released or "nothing")

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
        # If the decoder was taken out of Multiview to make room for this route,
        # the route failing is not the end of the operation: put the Multiview
        # back rather than leave the display on nothing.
        body = {"ok": False, "error": message, "status": "FAILED"}
        if multiview_exit:
            restored, restore_detail = _restore_multiview_after_failed_route(
                decoder, multiview_exit["multiview"])
            multiview_exit["rollback"] = {"restored": restored,
                                          "detail": restore_detail}
            body["multiview_exit"] = multiview_exit
            body["status"] = ("FAILED — ROLLED BACK" if restored
                              else "FAILED — ROLLBACK INCOMPLETE")
            log.warning("[ROUTE] route to %s failed after leaving Multiview %s; "
                        "restore %s", decoder, multiview_exit["multiview"],
                        "verified" if restored else "INCOMPLETE")
        return jsonify(body), 200

    # Note: Decoder inputs will be fetched by the polling system (every 5 seconds)
    # No need to fetch them here - route response returns immediately

    # ---- the route failed after the Multiview had been left ---------------
    #
    # Leaving Multiview and routing are one operation for the operator, so a
    # half-completed one is put back rather than left as a blank display.
    if multiview_exit and not ok:
        restored, message = _restore_multiview_after_failed_route(
            decoder, multiview_exit["multiview"])
        multiview_exit["rollback"] = {"restored": restored, "detail": message}
        log.warning("[ROUTE] route to %s failed after leaving Multiview %s; "
                    "restore %s", decoder, multiview_exit["multiview"],
                    "verified" if restored else "INCOMPLETE: %s" % message)

    dec_payload = {"ip": decoder}
    try:
        fields = _ws_get_decoder_inputs(decoder, decoder_user, used_decoder_pwd or decoder_pref_pwd, app.config['WS_PORT'], app.config['WS_PATH'], timeout=4, attempts=1, delay=0)
        if fields:
            # The same derivation the matrix state uses, so the row the page
            # redraws after a route is as truthful as the one it loaded.
            name = omni_multiview.active_multiview_name(fields.get("video_input"))
            fields["multiview_name"] = name
            fields["multiview_active"] = bool(name)
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
    status = "VERIFIED" if ok else "FAILED"
    if multiview_exit and not ok:
        status = ("FAILED — ROLLED BACK"
                  if (multiview_exit.get("rollback") or {}).get("restored")
                  else "FAILED — ROLLBACK INCOMPLETE")
    body = {"ok": bool(ok), "decoder": dec_payload, "status": status}
    if multiview_exit:
        body["multiview_exit"] = multiview_exit
    return jsonify(body)


def _restore_multiview_after_failed_route(ip, name):
    """Put a Multiview back on the display after a route failed to replace it.

    Best effort and reported as such. The saved object was never touched, so
    this is the ordinary recall path -- the same reconciliation any recall
    performs, which is what makes it safe to run against whatever state the
    failed route left behind.
    """
    try:
        state, error = _decoder_state(ip)
        if state is None:
            return False, error or "the decoder could not be read"
        if not any(str(o.get("name") or "") == name for o in state["multiview"]):
            return False, "%s is no longer on the decoder" % name
        ok, message = _mv_set(ip, "hdmi_output",
                              {"name": "hdmi_output1", "video": {"input": name}})
        if not ok:
            return False, message
        good, detail = _verify_mutation({
            "device": ip, "node": "hdmi_output", "target": "hdmi_output1",
            "config": {"name": "hdmi_output1", "video": {"input": name}}})
        # Leaving the Multiview recorded the fallback input the display moved
        # to. Putting it back has to record that too, or the A/V Matrix goes on
        # showing the conventional route that was rolled back -- measured on
        # 192.168.100.32, where the cache still read ip_input1 while the decoder
        # was demonstrably compositing again. The device is the truth; the cache
        # has to be told when the device changes back.
        if good:
            _remember_decoder_display(ip, name)
        return good, detail
    except Exception as exc:
        return False, str(exc)


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

    log.debug("[API/CACHE] Returning %d units from cache", len(units_with_password_source))
    # Switch topology is derived from these very units and rides along on the
    # response, so the banner and the minority highlighting need no request of
    # their own and generate no device traffic.
    topology = _lldp_topology(units_with_password_source)
    ack_state = _read_topology_ack()
    return jsonify({"ok": True, "units": units_with_password_source,
                    "count": len(units_with_password_source), "source": "cache",
                    "lldp_topology": topology,
                    "topology_warning_acknowledged": bool(ack_state.get("acknowledged")),
                    "daisy_warning_acknowledged": _daisy_warning_acknowledged(topology, ack_state)})

@app.route("/api/clear_units", methods=["POST"])
def api_clear_units_dup():
    try:
        if CACHE.exists(): CACHE.unlink()
        # Also clear unified scan results so UI cache empties
        if SCAN_RESULTS.exists(): SCAN_RESULTS.unlink()
        if CSV_VIEW.exists(): CSV_VIEW.unlink()
    except Exception: pass
    # This is the one operation that ends the inventory's life, so it is the one
    # operation that resets the multi-switch acknowledgement. Scanning again,
    # scanning another network, finding more devices or refreshing LLDP all leave
    # it alone: the operator acknowledged the topology for this inventory, and
    # that inventory still exists.
    _topology_ack_reset()
    # Device Info Clear Units is the single clear action, so it also forgets the
    # standalone USB inventory. Inventory only: no packet is sent, and no device
    # configuration, pairing, or power state is touched. Configured USB ranges are
    # kept. Cleared devices return after the next successful discovery.
    usb_cleared = 0
    try:
        usb_cleared = _usb_extenders.clear_devices()
    except Exception as exc:
        log.warning("USB extender inventory clear failed: %s", type(exc).__name__)
    return jsonify({"ok": True, "usb_cleared": usb_cleared})

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
                scan_data = _read_scan_results()
                # Update devices, encoders, and decoders
                scan_data["devices"] = units
                scan_data["encoders"] = [u for u in units if u.get("role") == "encoder"]
                scan_data["decoders"] = [u for u in units if u.get("role") == "decoder"]
                scan_data["stats"]["devices_merged"] = len(units)
                scan_data["stats"]["encoders_found"] = len(scan_data["encoders"])
                scan_data["stats"]["decoders_found"] = len(scan_data["decoders"])
                _save_scan_results(scan_data)
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

# The USB worksheet is the operator's view of Configure > USB, and nothing else.
# Diagnostics, sources, peers and link state belong in the support dump; putting
# them here would turn a clean inventory into a second troubleshooting export.
USB_SHEET_NAME = "USB"
USB_SHEET_COLUMNS = ("Model", "USB Role", "Device IP", "Hostname", "USB IP",
                     "USB MAC", "Firmware", "Network Mode", "Network")


def _usb_worksheet_rows():
    """One row per canonical USB endpoint, from the one inventory the UI uses.

    Deliberately not rebuilt for Excel: reconstructing it here is how a physical
    endpoint ends up listed twice, once from standalone discovery and once from
    its parent.
    """
    view = _usb_extender_view()
    rows = []
    for device in view.get("devices") or []:
        integrated = device.get("classification") == "INTEGRATED"
        rows.append({
            "Model": device.get("display_model") or "",
            "USB Role": device.get("usb_role") or "",
            # Device IP is what Configure shows in that column: the parent's
            # management address for an integrated endpoint, the unit's own for a
            # standalone one.
            "Device IP": (device.get("parent_ip") or "") if integrated else (device.get("ip") or ""),
            # The same Hostname cell Configure renders, so the workbook and the
            # screen cannot disagree: the protocol identity is not a hostname.
            "Hostname": (device.get("parent_hostname") or "") if integrated else (device.get("display_hostname") or ""),
            "USB IP": device.get("usb_ip") or device.get("ip") or "",
            "USB MAC": device.get("mac") or "",
            # The USB worksheet describes USB endpoints, so this is the Icron
            # module's version. The parent unit's firmware is on the Devices
            # sheet, where it belongs, and is never substituted here.
            "Firmware": device.get("usb_firmware") or "N/A",
            "Network Mode": device.get("network_mode") or "",
            "Network": device.get("network_relation_label") or "",
        })
    # Deterministic order on the server, addresses numerically: the operator gets
    # the same workbook twice running, and never thread completion order.
    return sorted(rows, key=lambda row: (row["USB Role"], _ip_sort_key(row["USB IP"]), row["USB MAC"]))


def _inventory_workbook():
    """Build the inventory workbook, or return None if openpyxl is unavailable.

    Imported here rather than at module scope so a build without the package
    degrades to the CSV download that has always existed, instead of failing to
    start.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        log.info("openpyxl is unavailable; inventory download falls back to CSV")
        return None

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F2937")
    header_align = Alignment(vertical="center")

    def add_sheet(sheet, columns, rows):
        sheet.append(list(columns))
        for cell in sheet[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
        for row in rows:
            # Addresses and MACs are identifiers, never numbers: Excel would
            # otherwise reformat or truncate them.
            sheet.append([str(row.get(column, "") if isinstance(row, dict) else row) for column in columns])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(1, len(rows)) + 1}"
        for index, column in enumerate(columns, start=1):
            widest = max([len(str(column))] + [len(str((row.get(column, "") if isinstance(row, dict) else ""))) for row in rows])
            sheet.column_dimensions[get_column_letter(index)].width = min(46, max(11, widest + 2))

    workbook = Workbook()
    devices_sheet = workbook.active
    devices_sheet.title = "Devices"
    device_columns = list(INVENTORY_COLUMNS)
    device_rows = [dict(zip(device_columns, _inventory_row(unit))) for unit in _units_for_export()]
    add_sheet(devices_sheet, device_columns, device_rows)
    add_sheet(workbook.create_sheet(USB_SHEET_NAME), USB_SHEET_COLUMNS, _usb_worksheet_rows())
    return workbook


@app.route("/api/download_inventory", methods=["GET"])
def api_download_inventory():
    """Inventory workbook: a Devices sheet and a USB sheet.

    The CSV endpoints are untouched, so anything that consumed them still works.
    """
    workbook = _inventory_workbook()
    if workbook is None:
        return _stream_csv_from_units(_units_for_export())
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Response(
        buffer.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=omnisuite-inventory-{stamp}.xlsx"},
    )


# ---------------- device debug log ----------------
#
# Observed on the bench across every generation present -- at-omni-111,
# at-omni-121, hw-omni-e4111, hw-omni-e4111-wp, hw-omni-d4111, hw-omni-d4511 and
# hw-omni-e4521 -- the WebSocket method answers:
#
#   {"error": false, "id": "get_debug_info-method",
#    "reply": {"path": "/debug/<hostname>-debug.vdf"}}
#
# It does not return the log. It builds a bundle on the device and hands back a
# path, which is then fetched over plain HTTP from that same device. The file's
# Last-Modified is the moment of the call, so each request regenerates it; the
# bundles measured were 9.9-12.3 MB and began with the OpenSSL `Salted__`
# magic, meaning the manufacturer encrypts them. OmniSuite therefore streams the
# bytes through untouched and cannot -- and must not try to -- inspect them.
#
# Baselined before and after on a bench encoder: no configuration field changed
# and uptime advanced only by the elapsed time, so the call is observational.
# Fan speed and die temperature moved while the bundle was built, which is the
# device doing work rather than a state change.
DEVICE_LOG_TIMEOUT = 25.0
DEVICE_LOG_FETCH_TIMEOUT = 120.0
DEVICE_LOG_CHUNK = 64 * 1024
# Generous against the 9.9-12.3 MB observed, and still a bound: a device that
# streamed forever would otherwise hold a worker and the operator's browser.
DEVICE_LOG_MAX_BYTES = 256 * 1024 * 1024
# Windows refuses these names with any extension, so a device called `con` would
# produce a file that cannot be written on one of the three platforms.
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul",
                     *(f"com{i}" for i in range(1, 10)),
                     *(f"lpt{i}" for i in range(1, 10))}


def _safe_device_log_path(path):
    """Validate the path the device handed back before fetching it.

    The device supplies this string, so it is untrusted input that is about to
    become a URL. Anything that could retarget the request -- a scheme, a host,
    a protocol-relative `//`, a parent-directory segment, a backslash, a control
    character -- is refused rather than sanitised, because a path we had to
    repair is not a path we understood.
    """
    text = str(path or "")
    if not text or not text.startswith("/") or text.startswith("//"):
        return None
    if len(text) > 512 or "\\" in text or ".." in text:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    if re.search(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text.lstrip("/")):
        return None
    if not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+", text):
        return None
    return text


def _sanitize_filename_part(value, limit=48):
    """One component of a download filename, safe on Windows, macOS and Linux."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    text = re.sub(r"-{2,}", "-", text)[:limit].strip("-._")
    return text


def _device_log_filename(hostname, ip, stamp=None):
    """hostname_ip_timestamp, with a usable name whatever the device is called.

    The device's own path is never used for this. It is attacker-influenced and
    it names a file on the device, not a file on the operator's machine.
    """
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    host = _sanitize_filename_part(hostname)
    if host.lower() in _WINDOWS_RESERVED:
        host = f"{host}-device"
    address = _sanitize_filename_part(ip, limit=39)
    parts = [part for part in (host, address, stamp) if part]
    return f"{'_'.join(parts) or 'device-log'}.vdf"


def _device_log_location(ip, user, pwd, timeout=DEVICE_LOG_TIMEOUT):
    """Ask the device to build its debug bundle and say where it put it.

    Uses the same primary-then-fallback credential behaviour as discovery, so a
    unit still on the fallback password produces a log rather than an error.
    """
    payload = {"id": "get_debug_info-method", "username": user,
               "method": {"get_debug_info": {}}}
    resp = _ws_send_recv_with_fallback(ip, payload, timeout,
                                       app.config["WS_PORT"], app.config["WS_PATH"],
                                       primary_pwd=pwd)
    if not isinstance(resp, dict):
        raise ValueError("the device did not return a debug-info reply")
    if resp.get("error"):
        raise ValueError("the device reported an error building its debug log")
    path = _safe_device_log_path(((resp.get("reply") or {}) if isinstance(resp.get("reply"), dict) else {}).get("path"))
    if not path:
        raise ValueError("the device did not return a usable debug-log path")
    return path


@app.route("/api/device_log", methods=["GET"])
@_audited("device_log_download")
def api_device_log():
    """Stream one device's debug bundle to the operator's browser.

    Strictly operator initiated. Nothing in discovery, polling, startup, page
    rendering, the support dump or inventory generation calls this, and it is
    not on any cached path: each request builds a fresh bundle on the device.

    Credentials never leave the server. The browser sends an address; the
    application looks up the credentials it already authenticated that device
    with and uses them here.
    """
    ip = (request.args.get("ip") or "").strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({"ok": False, "error": "A device address is required."}), 400

    cache_map = {d.get("ip"): d for d in (_load_cache() or []) if d.get("ip")}
    if ip not in cache_map:
        return jsonify({"ok": False, "error": f"{ip} is not a discovered device."}), 404
    user, pwd, device = _device_credentials(ip, cache_map)

    try:
        path = _device_log_location(ip, user, pwd)
    except Exception as exc:
        # A device that is powered down or on another network is an upstream
        # condition, not a fault in this application.
        log.info("Device log unavailable for %s: %s", ip, type(exc).__name__)
        return jsonify({"ok": False, "status": "unavailable",
                        "error": f"{ip} did not return a debug log."}), 502

    url = f"http://{ip}{path}"
    try:
        upstream = requests.get(url, stream=True, timeout=DEVICE_LOG_FETCH_TIMEOUT)
        upstream.raise_for_status()
    except Exception as exc:
        log.info("Device log fetch failed for %s: %s", ip, type(exc).__name__)
        return jsonify({"ok": False, "status": "unavailable",
                        "error": f"{ip} did not deliver its debug log."}), 502

    filename = _device_log_filename(device.get("hostname"), ip)

    def stream():
        # Streamed rather than buffered: the bundles measured were 9.9-12.3 MB,
        # and holding one per concurrent operator in server memory is a cost
        # with no purpose.
        total = 0
        try:
            for chunk in upstream.iter_content(chunk_size=DEVICE_LOG_CHUNK):
                if not chunk:
                    continue
                total += len(chunk)
                if total > DEVICE_LOG_MAX_BYTES:
                    log.warning("Device log from %s exceeded the size bound; truncating", ip)
                    break
                yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    length = upstream.headers.get("Content-Length")
    headers = {
        # A filename built entirely from our own data, so nothing the device
        # says can steer where the browser writes.
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if length and length.isdigit():
        headers["Content-Length"] = length
    return Response(stream(), mimetype="application/octet-stream", headers=headers)


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

# Support-dump schema. Bump it when the shape changes so a dump can be read
# without guessing which build produced it.
#   1  units, config and summary only (implicit; dumps before this field)
#   2  adds usb_endpoints, usb_routes, usb_discovery and lldp_topology
#   3  adds host_network: host firewall profile state, application identity, the
#      listening address actually bound, the selected interface, and the
#      transports OmniSuite uses
TS_DUMP_SCHEMA = 3


def _ts_unknown(value, blank=None):
    """One convention for "we do not know": null. Never an invented value."""
    if value in (None, "", [], {}):
        return blank
    return value


def _ts_usb_endpoints(view):
    """Every canonical USB endpoint, with the authority behind each fact.

    Read-only: it consumes the derived view the pages already consume and sends
    nothing to any device.
    """
    rows = []
    raw = {_norm_usb_mac(d.get("mac")): d for d in (_usb_extenders.state().get("devices") or [])}
    for device in view.get("devices") or []:
        identity = _norm_usb_mac(device.get("mac"))
        record = raw.get(identity, {})
        integrated = device.get("classification") == "INTEGRATED"
        summary = _usb_link_summary(device)
        rows.append({
            "usb_mac": _ts_unknown(device.get("mac")),
            "model": _ts_unknown(device.get("display_model")),
            "usb_role": _ts_unknown(device.get("usb_role")),
            "classification": _ts_unknown(device.get("classification")),
            "integrated": integrated,
            "device_ip": _ts_unknown(device.get("parent_ip") if integrated else device.get("ip")),
            # What the operator saw on screen, plus what the protocol actually
            # said. A support dump needs both: the displayed name is how the
            # report will be described, and `udp_reported_type` is the evidence.
            "hostname": _ts_unknown(device.get("parent_hostname") if integrated else device.get("display_hostname")),
            "udp_reported_type": _ts_unknown(device.get("device_type")),
            "usb_ip": _ts_unknown(device.get("usb_ip") or device.get("ip")),
            "firmware": _ts_unknown(device.get("firmware")),
            "usb_firmware": _ts_unknown(device.get("usb_firmware")),
            "usb_firmware_source": _ts_unknown(device.get("usb_firmware_source")),
            "icron_revision": _ts_unknown(device.get("icron_revision") or device.get("product_revision")),
            "network_mode": _ts_unknown(device.get("network_mode")),
            "network_relation": _ts_unknown(device.get("network_relation")),
            "network_relation_label": _ts_unknown(device.get("network_relation_label")),
            "online": bool(device.get("online")),
            "stale": bool(device.get("stale")),
            "last_seen": _ts_unknown(device.get("last_seen")),
            "live_seen": _ts_unknown(device.get("live_seen")),
            "parent_ip": _ts_unknown(device.get("parent_ip")),
            "parent_hostname": _ts_unknown(device.get("parent_hostname")),
            "parent_model": _ts_unknown(device.get("parent_model")),
            "parent_mac": _ts_unknown(device.get("parent_mac")),
            "association_source": _ts_unknown(device.get("association_source")),
            "discovery_source": _ts_unknown(device.get("discovery_source")),
            # Configured pairing, from whichever provider owns it.
            # The view removes paired_macs for an integrated endpoint on purpose:
            # usb_icron owns that fact. Restoring it from the UDP store here put
            # Advanced Query peers on a row whose field_authority says usb_icron.
            "paired_macs": list(device.get("paired_macs") or []),
            "peer_count": len(device.get("paired_macs") or []),
            "udp_paired_macs": list(record.get("paired_macs") or []),
            "parent_paired_count": _ts_unknown(device.get("parent_paired_count")),
            "pairing_state_fresh": bool(device.get("pairing_state_fresh")),
            "pairing_last_read": _ts_unknown(device.get("pairing_last_read")),
            # Current link, on its own clock.
            "link_state": _ts_unknown(device.get("link_state")),
            "link_label": summary["label"],
            "link_detail": _ts_unknown(summary["detail"]),
            "link_states": [dict(state) for state in (device.get("link_states") or [])],
            "link_peer_macs": list(device.get("link_peer_macs") or []),
            "link_last_read": _ts_unknown(device.get("link_last_read")),
            "link_last_attempt": _ts_unknown(device.get("link_last_attempt")),
            "link_consecutive_failures": record.get("link_consecutive_failures", 0),
            "link_state_fresh": bool(device.get("link_state_fresh")),
            "link_state_retained": bool(device.get("link_state_retained")),
            "link_age_seconds": _ts_unknown(device.get("link_age")),
            "link_read_failed": _ts_unknown(device.get("link_read_failed")),
            # Which provider established each fact.
            "field_authority": dict(device.get("field_authority") or {}),
            "liveness_sources": list(device.get("liveness_sources") or []),
        })
    return sorted(rows, key=lambda row: str(row.get("usb_mac") or ""))


def _ts_usb_routes(view):
    """Configured USB relationships, with the evidence behind each combination.

    Built from the pairing tables the endpoints themselves report, so this is
    hardware state -- never a rendering of what a page happens to be showing.
    """
    by_mac = {_norm_usb_mac(d.get("mac")): d for d in (view.get("devices") or [])}
    raw = {_norm_usb_mac(d.get("mac")): d for d in (_usb_extenders.state().get("devices") or [])}

    def identity(mac):
        device = by_mac.get(_norm_usb_mac(mac)) or {}
        return {
            "usb_mac": device.get("mac") or mac,
            "model": _ts_unknown(device.get("display_model")),
            "usb_ip": _ts_unknown(device.get("usb_ip") or device.get("ip")),
            "device_ip": _ts_unknown(device.get("parent_ip") or device.get("ip")),
            "hostname": _ts_unknown(device.get("parent_hostname")),
            "known": _norm_usb_mac(mac) in by_mac,
        }

    routes, seen = [], set()
    for device in view.get("devices") or []:
        if device.get("usb_role") != "USB Host / LEX":
            continue
        host = _norm_usb_mac(device.get("mac"))
        peers = device.get("paired_macs") or (raw.get(host) or {}).get("paired_macs") or []
        for peer in peers:
            key = (host, _norm_usb_mac(peer))
            if key in seen:
                continue
            seen.add(key)
            rex = by_mac.get(_norm_usb_mac(peer)) or {}
            lex_kind = "integrated" if device.get("classification") == "INTEGRATED" else "standalone"
            rex_kind = "integrated" if rex.get("classification") == "INTEGRATED" else "standalone"
            capability = USB_ROUTE_CAPABILITY.get((lex_kind, rex_kind), {})
            link = next((state for state in (device.get("link_states") or [])
                         if _norm_usb_mac(state.get("mac")) == _norm_usb_mac(peer)), None)
            routes.append({
                "lex": identity(device.get("mac")),
                "rex": identity(peer),
                "provider": capability.get("control_path", "unknown"),
                "configured": True,
                "pairing_source": _ts_unknown(device.get("pairing_source")),
                "link_state": (link or {}).get("state"),
                "link_source": _ts_unknown(device.get("link_source")),
                "control_plane_verified": bool(capability.get("enabled")),
                "data_plane_verified": bool(USB_ROUTE_DATA_PLANE_VERIFIED.get((lex_kind, rex_kind), False)),
                "capability_state": capability.get("state", "UNKNOWN"),
            })
    return sorted(routes, key=lambda route: (str(route["lex"]["usb_mac"]), str(route["rex"]["usb_mac"])))


def _ts_usb_discovery():
    """A concise picture of how USB endpoints are being found and polled.

    Deliberately a summary. A support dump that carried every packet would be
    unreadable and would answer no question a summary does not.
    """
    prefs = _read_scan_preferences()
    state = _usb_extenders.state()
    traffic = _usb_extenders.transmit_counts()
    elapsed = max(1e-6, time.time() - traffic.get("since", time.time()))
    endpoints = []
    for device in state.get("devices") or []:
        endpoints.append({
            "usb_mac": device.get("mac"),
            "ip": device.get("ip"),
            "interface_ip": _ts_unknown(device.get("interface_ip")),
            "discovery_source": _ts_unknown(device.get("discovery_source")),
            "network_relation": _ts_unknown(device.get("network_relation")),
            "last_seen": _ts_unknown(device.get("last_seen")),
            "live_seen": _ts_unknown(device.get("live_seen")),
            "live_offline": bool(device.get("live_offline")),
            "pairing_last_read": _ts_unknown(device.get("pairing_last_read")),
            "link_last_read": _ts_unknown(device.get("link_last_read")),
            "link_last_attempt": _ts_unknown(device.get("link_last_attempt")),
            "link_consecutive_failures": device.get("link_consecutive_failures", 0),
        })
    return {
        "scan_interface_ip": _ts_unknown(prefs.get("interface_ip")),
        "scan_subnet_mask": _ts_unknown(prefs.get("subnet_mask")),
        "scan_targets": _ts_unknown(prefs.get("scan_targets")),
        "usb_directed_ranges": list(state.get("ranges") or []),
        "maximum_range_hosts": omni_usb_extender.MAX_RANGE_HOSTS,
        "endpoint_count": len(endpoints),
        "endpoints": sorted(endpoints, key=lambda row: str(row.get("usb_mac") or "")),
        "freshness_policy": {
            "live_ttl_seconds": omni_usb_extender.LIVE_TTL,
            "pairing_ttl_seconds": omni_usb_extender.PAIRING_TTL,
            "link_ttl_seconds": omni_usb_extender.LINK_TTL,
            "link_refresh_age_seconds": omni_usb_extender.LINK_REFRESH_AGE,
            "link_retain_ttl_seconds": omni_usb_extender.LINK_RETAIN_TTL,
            "link_max_misses": omni_usb_extender.LINK_MAX_MISSES,
            "parent_live_ttl_seconds": USB_LIVE_PARENT_TTL,
        },
        "udp_traffic_since_start": {
            "elapsed_seconds": round(elapsed, 1),
            "per_minute": {name: round(value * 60.0 / elapsed, 2)
                           for name, value in traffic.items() if name != "since"},
        },
    }


def _ts_lldp_topology(units):
    """Enough LLDP detail to reconstruct why the topology was classified as it was.

    Both halves are present for every device: what it actually reported, and what
    that was resolved to. A support engineer disagreeing with the conclusion can
    see the input that produced it.
    """
    topology = _lldp_topology(units)
    devices = []
    for address, record in (topology.get("devices") or {}).items():
        switch = record.get("resolved_upstream_switch") or {}
        devices.append({
            "ip": address,
            "mac": record.get("mac"),
            "hostname": record.get("hostname"),
            "model": record.get("model"),
            "role": record.get("role"),
            "immediate_neighbor": record.get("immediate_neighbor"),
            "resolved_upstream_switch": _ts_unknown(switch),
            "resolution": record.get("resolution"),
            "is_daisy_chained": bool(record.get("is_daisy_chained")),
            "daisy_chain_via_device": record.get("daisy_chain_via_device"),
            "daisy_chain_via_ip": record.get("daisy_chain_via_ip"),
            "daisy_chain_via_mac": record.get("daisy_chain_via_mac"),
            "daisy_chain_hops": record.get("daisy_chain_hops") or [],
            "is_minority_switch": address in (topology.get("minority_ips") or []),
        })
    acknowledgement = _read_topology_ack()
    return {
        "switch_count": topology.get("switch_count"),
        "multi_switch": bool(topology.get("multi_switch")),
        "tied": bool(topology.get("tied")),
        "dominant_chassis_id": _ts_unknown(topology.get("dominant_chassis_id")),
        "groups": topology.get("groups") or [],
        "minority_ips": topology.get("minority_ips") or [],
        "devices_with_lldp": topology.get("devices_with_lldp"),
        "devices_without_lldp": topology.get("devices_without_lldp"),
        "daisy_chained": bool(topology.get("daisy_chained")),
        "devices_daisy_chained": topology.get("devices_daisy_chained") or [],
        "daisy_chained_multi_stream": topology.get("daisy_chained_multi_stream") or [],
        # Stated explicitly so nobody reads the advisory as a measurement: no
        # device reports its actual stream bitrate to OmniSuite.
        "bitrate_data_available": bool(topology.get("bitrate_data_available")),
        "max_chain_hops": LLDP_MAX_CHAIN_HOPS,
        "acknowledgements": {
            "multi_switch": bool(acknowledgement.get("acknowledged")),
            "multi_switch_at": _ts_unknown(acknowledgement.get("acknowledged_at")),
            "daisy_chain": bool(acknowledgement.get("daisy_acknowledged")),
            "daisy_chain_at": _ts_unknown(acknowledgement.get("daisy_acknowledged_at")),
            "daisy_chain_signature": _ts_unknown(acknowledgement.get("daisy_signature")),
        },
        "devices": sorted(devices, key=lambda row: _ip_sort_key(row["ip"])),
    }


# A support dump has to be able to say what this host looks like to the network,
# because "discovery found nothing" and "the host dropped the packet" are
# indistinguishable from inside the application. Everything below is read-only and
# states context; none of it is a diagnosis. A firewall profile being ON is the
# ordinary state of a Windows machine and is never reported as an error or as a
# cause.
FIREWALL_QUERY_TIMEOUT = 4.0


def _firewall_query_output():
    """Raw text of the platform's read-only firewall profile query.

    The single process boundary, isolated so tests can replace it: run_tests.py
    fences both hardware transports but not subprocess, so without one named
    function to stub, a unit test would really shell out.

    `show allprofiles state` reads and cannot write, the call is never elevated,
    and a short timeout means a dump can never hang on it.
    """
    if platform.system().lower() != "windows":
        raise OSError("firewall profile state is only queried on Windows")
    completed = subprocess.run(
        ["netsh", "advfirewall", "show", "allprofiles", "state"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
        timeout=FIREWALL_QUERY_TIMEOUT, check=False,
        **_windows_hidden_subprocess_kwargs())
    if completed.returncode != 0:
        raise OSError(f"netsh exited {completed.returncode}")
    return completed.stdout or ""


def _parse_firewall_profiles(text):
    """Profile name to state, from the query's text.

    Separate from the query so it can be exercised on any platform without the
    test having to pretend the host is Windows. A localised or restructured
    output simply yields nothing, which the caller reports as unavailable rather
    than as "all profiles off".
    """
    profiles, current = {}, None
    for line in str(text or "").splitlines():
        heading = re.match(r"(?i)^\s*(domain|private|public)\s+profile\s+settings\s*:", line)
        if heading:
            current = heading.group(1).capitalize()
            profiles.setdefault(current, {"enabled": None, "reported": None})
            continue
        reading = re.match(r"(?i)^\s*state\s+(\S+)\s*$", line)
        if reading and current:
            word = reading.group(1).strip()
            # The raw word is kept beside the boolean: a value never observed is
            # reported as unknown rather than folded into "off".
            profiles[current] = {"enabled": {"ON": True, "OFF": False}.get(word.upper()),
                                 "reported": word}
    return profiles


def _firewall_profile_states():
    """Domain/Private/Public profile state, as diagnostic context only.

    Returns rather than raises on every failure -- another platform, a localised
    netsh whose headings do not match, a timeout, a missing executable -- so one
    unanswerable question can never cost the dump its other sections.
    "Unavailable" is stated explicitly with the reason, and a profile whose state
    word this parser has never seen stays null rather than being guessed at.

    Only profile state is read. Firewall *rules* are deliberately not dumped: they
    are a large and sensitive surface, and they answer no question this section
    exists to answer.
    """
    result = {
        "available": False,
        "source": "netsh advfirewall show allprofiles state",
        "profiles": {},
        "unavailable_reason": None,
        "note": "Profile state only, read without elevation and without changing "
                "anything. A firewall being enabled is normal and is not an error; "
                "no firewall rules are read or reported.",
    }
    if platform.system().lower() != "windows":
        result["unavailable_reason"] = f"not applicable on {platform.system() or 'this platform'}"
        return result
    try:
        text = _firewall_query_output()
    except Exception as exc:
        result["unavailable_reason"] = type(exc).__name__
        return result
    profiles = _parse_firewall_profiles(text)
    result["profiles"] = profiles
    result["available"] = bool(profiles)
    if not profiles:
        result["unavailable_reason"] = "no profile state recognised in the query output"
    return result


def _ts_host_network():
    """Host-side network context: what this application is, where it listens, what
    it talks to, and what the host firewall profiles currently say.

    Static and already-collected values only. Nothing here probes a device or the
    network, nothing is changed, and nothing is concluded: the section exists so a
    support engineer can see what would have to be allowed, not so the application
    can claim that something is being blocked.
    """
    prefs = _read_scan_preferences()
    bind = dict(_RUNTIME_BIND)
    ws_port = app.config.get("WS_PORT", 80)
    executable = str(getattr(sys, "executable", "") or "")
    return {
        "note": "Diagnostic context, not a diagnosis. OmniSuite performs no reachability "
                "test for this section and states no cause for anything.",
        "platform": {
            "system": platform.system() or None,
            "release": platform.release() or None,
            "version": platform.version() or None,
            "machine": platform.machine() or None,
            "python": platform.python_version(),
        },
        "application": {
            # Which image the host firewall is asked about is the whole question: a
            # frozen build appears as OmniSuite.exe, a source run as python.exe.
            "frozen": bool(FROZEN),
            "executable": executable or None,
            "script_dir": str(SCRIPT_DIR),
            "asset_dir": str(ASSET_DIR),
            "data_dir": str(DATA_DIR),
            "module": os.path.basename(__file__),
        },
        "listening": {
            "host": bind.get("host"),
            "port": bind.get("port"),
            "preferred_port": PORT,
            "bound": bool(bind.get("port")),
            "note": "The address main() actually bound. preferred_port is only the "
                    "first candidate; an occupied port moves the selection upward.",
        },
        "selected_interface": {
            "interface_ip": _ts_unknown(prefs.get("interface_ip")),
            "subnet_mask": _ts_unknown(prefs.get("subnet_mask")),
            "scan_expression": _ts_unknown(prefs.get("scan_expression")),
            "scan_targets": _ts_unknown(prefs.get("scan_targets")),
            "note": "The adapter already chosen on Device Info, read from the stored "
                    "scan preference. No adapter enumeration is performed for the dump.",
        },
        # Static product facts rather than a probe: this is what OmniSuite sends, so
        # it is what would have to be allowed.
        "transports": [
            {"name": "HW-OMNI-311/324 discovery",
             "protocol": "UDP", "port": omni_usb_extender.UDP_PORT,
             "direction": "outbound local broadcast, replies inbound",
             "purpose": "find unknown standalone USB extenders on the attached L2 "
                        "network; broadcast does not cross a router"},
            {"name": "HW-OMNI-311/324 query and control",
             "protocol": "UDP", "port": omni_usb_extender.UDP_PORT,
             "direction": "outbound directed unicast, replies inbound",
             "purpose": "poll, configure and route known USB extenders; unicast is "
                        "routed and reaches other subnets"},
            {"name": "OmniStream device API",
             "protocol": "WebSocket over HTTP/HTTPS",
             "port": ws_port,
             "path": app.config.get("WS_PATH", "/wsapp/"),
             "direction": "outbound to each device",
             "purpose": "encoder and decoder discovery, configuration, usb_icron "
                        "pairing and firmware upload"},
            {"name": "OmniSuite web interface",
             "protocol": "HTTP", "port": bind.get("port"),
             "direction": "inbound from this computer's own browser",
             "purpose": "the OmniSuite user interface"},
            {"name": "Release check",
             "protocol": "HTTPS", "port": 443, "host": "api.github.com",
             "direction": "outbound",
             "purpose": "whether a newer OmniSuite release exists; optional, and the "
                        "application works normally with it unreachable"},
        ],
        "firewall_profiles": _firewall_profile_states(),
    }


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
    # The pre-existing sanitiser drops empty values, which is right for the device
    # settings it was written for and wrong here: "we do not know this" is exactly
    # what a support engineer needs to see. So the sections below are attached
    # after sanitising, with their own explicit nulls. They carry no credentials --
    # every field is an address, an identity, a timestamp or a derived state.
    payload = _ts_settings_only(payload)
    try:
        view = _usb_extender_view()
        payload["usb_endpoints"] = _ts_usb_endpoints(view)
        payload["usb_routes"] = _ts_usb_routes(view)
        payload["usb_discovery"] = _ts_usb_discovery()
    except Exception as exc:
        log.info("Support dump USB section unavailable: %s", type(exc).__name__)
        payload["usb_endpoints_error"] = type(exc).__name__
    try:
        payload["lldp_topology"] = _ts_lldp_topology(cache_units)
    except Exception as exc:
        log.info("Support dump LLDP section unavailable: %s", type(exc).__name__)
        payload["lldp_topology_error"] = type(exc).__name__
    try:
        # Attached after the sanitiser for the same reason as the sections above:
        # its nulls are the answer. "The firewall profiles could not be read" is
        # exactly what a support engineer needs to see, and a sanitiser that strips
        # empty values would delete it.
        payload["host_network"] = _ts_host_network()
    except Exception as exc:
        log.info("Support dump host network section unavailable: %s", type(exc).__name__)
        payload["host_network_error"] = type(exc).__name__
    payload["schema_version"] = TS_DUMP_SCHEMA
    body = json.dumps(payload, indent=2, sort_keys=True)
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
        log.debug(f"[POLL] Polling {ip}...")
        status = omni_matrix_logic.poll_unit_status(ip)
        log.debug(f"[POLL] {ip} status: {status}")
        
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
                    log.debug(f"[POLL] {ip} fresh firmware version from device: '{fresh_version}'")
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
                log.info("[POLL] %s firmware changed: %r -> %r", ip, cached_version, fresh_version)
            else:
                log.debug(f"[POLL] {ip} version unchanged: '{fresh_version}'")
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
        
        log.debug(f"[POLL] {ip} final version: '{status.get('fw', '')}'")

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
                    log.debug(f"[POLL] {ip} linkspeed: {link_speed}")
                else:
                    log.debug(f"[POLL] {ip} net-get returned no linkspeed field")
            else:
                log.debug(f"[POLL] {ip} net-get error or empty response: {net_resp}")
        except Exception as e:
            log.debug(f"[POLL] {ip} net-get failed: {e}")

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
                    _record_usb_association(ip, usb_cfg.get("macaddress"), role, usb_cfg.get("ipaddress"), status.get("mac"))
                    _record_parent_live(ip)
                    paired = usb_cfg.get("paired_devices") or {}
                    usb_data = {
                        "role": role,
                        "mac": usb_cfg.get("macaddress", ""),
                        "usb_ip": usb_cfg.get("ipaddress", ""),
                        "host_port": usb_cfg.get("usbhostport-current") or usb_cfg.get("usbhostport", ""),
                        # `found_devices` is the endpoint's USB-over-IP neighbour
                        # table: every OTHER Icron endpoint it can see on its own
                        # USB subnet. Measured on the bench across eight
                        # endpoints, it never contains the endpoint itself, it
                        # contains LEX and REX alike, and it is identical for
                        # every endpoint on a segment -- 8 on one bench subnet,
                        # 2 on the other. It is a fact about the network, not
                        # about this device, and it can be SMALLER than the
                        # paired count when a peer is on another subnet: one
                        # bench LEX reported 2 found against 3 paired. Reported
                        # here because it is real, and deliberately not shown
                        # next to a device row, where it read as a property of
                        # that device.
                        "found_count": len(usb_cfg.get("found_devices") or {}),
                        # These two ARE facts about this endpoint, and come from
                        # the same reply, so they cost nothing.
                        "paired_count": len(paired),
                        "linked_count": sum(1 for entry in paired.values()
                                            if isinstance(entry, dict) and entry.get("linked")),
                    }
                    log.debug(f"[POLL] {ip} USB info: {usb_data}")
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
    
    log.debug(f"[POLL_DECODERS] Polling {len(decoder_ips)} decoders in parallel...")
    
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
                log.debug(f"[POLL_DECODERS] {ip}: {fields}")
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
            log.debug(f"[POLL_DECODERS] Updated {updated_count} decoders, saved to cache")
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

# /api/reset was removed. It invoked the device factory_reset method from an
# unauthenticated POST, and nothing in the current UI called it -- the only
# references left were in ui/archive/, which is superseded. A destructive
# device operation with no caller is not worth keeping reachable.

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
        # Contained, not merely joined. Path.__truediv__ lets an absolute name
        # replace the base outright, and "../" walks out of it.
        try:
            inside = file_path.is_relative_to(fw_dir.resolve())
        except (OSError, ValueError):
            inside = False
        if not inside:
            log.warning("[UPGRADE] rejected firmware path outside the firmware folder")
            return jsonify({"ok": False, "error": "file not found"}), 400
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


def _omnistream_identify(ip):
    """Send the OmniStream identify/blink method to a device.

    Shared by /api/blink and by USB Identify when the endpoint belongs to an
    E4521/D4511 parent, so both paths use one implementation.  Returns the same
    (ok, payload) content /api/blink has always produced.
    """
    user = app.config['USERNAME']
    pwd = app.config['PASSWORD']
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']

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
        return (not r.get("error")), r
    except Exception:
        return True, resp


@app.route("/api/blink", methods=["POST"])
@_audited("blink")
def api_blink():
    """Send identify/blink command to device"""
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip: return jsonify({"ok": False, "error": "ip required"}), 400

    try:
        ok, response = _omnistream_identify(ip)
        return jsonify({"ok": ok, "response": response})
    except (OSError, websocket.WebSocketException) as e:
        # The device did not answer. That is an upstream condition, reported as
        # one, rather than a 500 that reads as a fault in this application.
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        log.exception("blink error")
        return jsonify({"ok": False, "error": str(e)}), 500

# ---------------- integrated Icron USB network (parent OmniStream API) ----------------
# Established from the device's own web application (artifacts/device-*-atlona.js,
# NetworkController + SocketService) and confirmed against live E4521 and D4511
# hardware. The USB 1 / Icron interface is an entry in the parent's `net` config:
#
#   read : config_get "net"  -> list of interfaces; the USB endpoint is type "icron"
#   write: config_set {"name": "net", "config": [ <that interface object> ]}
#
# The UI offers exactly these modes for an icron interface (vm.icronmodes), and
# ipaddress/subnetmask/gateway are editable only when the mode is "static".
ICRON_NETWORK_MODES = ("broadcast", "dhcp", "static", "disabled")
ICRON_STATIC_FIELDS = ("ipaddress", "subnetmask", "gateway")


def _omnistream_net_interfaces(ip, timeout=None):
    """Read the parent's `net` configuration (a list of interface objects)."""
    url = _ws_url(ip, app.config['WS_PORT'], app.config['WS_PATH'])
    response = _ws_send_recv(url, {
        "id": "net-get",
        "username": app.config['USERNAME'],
        "password": app.config['PASSWORD'],
        "config_get": "net",
    }, timeout=timeout or app.config['TIMEOUT'])
    if not response or response.get("error"):
        raise RuntimeError("the parent device did not return its network configuration")
    config = response.get("config")
    return config if isinstance(config, list) else []


def _icron_interface(interfaces):
    """The USB endpoint entry, identified by its interface type."""
    for entry in interfaces or []:
        if isinstance(entry, dict) and (entry.get("type") == "icron" or entry.get("name") == "icron"):
            return entry
    return None


def _omnistream_icron_network_get(ip, timeout=None):
    """Authoritative integrated USB network configuration from the parent."""
    interface = _icron_interface(_omnistream_net_interfaces(ip, timeout))
    if interface is None:
        return None
    # Route through the single canonical ingest so a read here updates the same
    # state discovery and Configure consume.
    stored = _ingest_icron_network_config(ip, None, interface) or {}
    return {
        "name": interface.get("name", "icron"),
        "mode": stored.get("mode", (interface.get("dhcpmode") or "").strip().lower()),
        "mode_raw": stored.get("mode_raw", interface.get("dhcpmode") or ""),
        "mode_known": stored.get("mode_known", True),
        "ipaddress": interface.get("ipaddress", ""),
        "subnetmask": interface.get("subnetmask", ""),
        "gateway": interface.get("gateway", ""),
        "macaddress": interface.get("macaddress", ""),
        "modes": list(ICRON_NETWORK_MODES),
        "source": "parent_net_config",
        "raw": interface,
    }


def _omnistream_icron_network_set(ip, mode, address=None, netmask=None, gateway=None, timeout=None):
    """Apply an integrated USB network change through the parent's own API.

    Sends the icron interface object back exactly as the device's web UI does:
    the whole entry, mutated, wrapped in a one-element list under `net`. The
    standalone AT-OMNI-311/324 IP commands are never used for an integrated
    endpoint.
    """
    mode = (mode or "").strip().lower()
    if mode not in ICRON_NETWORK_MODES:
        raise ValueError(f"mode must be one of {', '.join(ICRON_NETWORK_MODES)}")
    interfaces = _omnistream_net_interfaces(ip, timeout)
    interface = _icron_interface(interfaces)
    if interface is None:
        raise RuntimeError("this device does not expose an integrated USB network interface")
    payload = dict(interface)
    payload["dhcpmode"] = mode
    if mode == "static":
        values = {"ipaddress": address, "subnetmask": netmask, "gateway": gateway}
        for key, value in values.items():
            text = str(value or "").strip()
            try:
                ipaddress.IPv4Address(text)
            except Exception:
                raise ValueError(f"{key} must be a valid IPv4 address")
            payload[key] = text
        network = ipaddress.IPv4Network(f"{payload['ipaddress']}/{payload['subnetmask']}", strict=False)
        if str(network.netmask) != payload["subnetmask"]:
            raise ValueError("subnetmask is not a valid IPv4 netmask")
        if ipaddress.IPv4Address(payload["gateway"]) not in network:
            raise ValueError("gateway must be inside the same subnet as the address")
    url = _ws_url(ip, app.config['WS_PORT'], app.config['WS_PATH'])
    response = _ws_send_recv(url, {
        "id": "net-set",
        "username": app.config['USERNAME'],
        "password": app.config['PASSWORD'],
        "config_set": {"name": "net", "config": [payload]},
    }, timeout=timeout or app.config['TIMEOUT'])
    accepted = bool(response) and not response.get("error")
    return {"accepted": accepted, "response": response, "sent_mode": mode,
            "sent": {k: payload.get(k) for k in ICRON_STATIC_FIELDS} if mode == "static" else {}}


# ---------------- integrated Icron network configuration state ----------------
# Authority rule: for an INTEGRATED endpoint the parent's `net` config (the entry
# with type == "icron") is authoritative for network mode/address/mask/gateway.
# The standalone UDP Query also carries a network-mode byte, but that byte is not
# meaningful for an Icron endpoint -- it reported DHCP for a device the parent
# reported as static -- so it must never win for an integrated endpoint. For a
# STANDALONE extender the UDP value remains authoritative.
#
# One ingest function serves every path (discovery enrichment, Configure refresh,
# post-write read-back) so no path can interpret a mode differently.
USB_NET_CONFIG_TTL = 30.0          # authoritative re-read cadence
USB_NET_CONFIG_TIMEOUT = 3.0
USB_NET_CONFIG_WORKERS = 6
_usb_net_lock = threading.RLock()
_usb_net_config = {}               # normalized USB MAC -> canonical network config
_usb_net_state = {"in_flight": set()}


def _ingest_icron_network_config(parent_ip, parent_mac, icron):
    """Canonical ingest of one parent `net` icron entry. Returns the stored record.

    The raw ``dhcpmode`` is preserved exactly; an unrecognised value is kept and
    surfaced as unknown rather than being normalised into a valid-looking mode.
    """
    if not isinstance(icron, dict):
        return None
    usb_mac = _norm_usb_mac(icron.get("macaddress"))
    if len(usb_mac) != 12:
        return None
    raw_mode = str(icron.get("dhcpmode") or "").strip()
    record = {
        "usb_mac": usb_mac,
        "parent_ip": str(parent_ip or "").strip(),
        "parent_mac": _norm_usb_mac(parent_mac),
        "mode": raw_mode.lower(),
        "mode_raw": raw_mode,
        "mode_known": raw_mode.lower() in ICRON_NETWORK_MODES,
        "ipaddress": icron.get("ipaddress", ""),
        "subnetmask": icron.get("subnetmask", ""),
        "gateway": icron.get("gateway", ""),
        "macaddress": icron.get("macaddress", ""),
        "network_config_source": "parent_net",
        "network_config_last_read": time.time(),
    }
    with _usb_net_lock:
        _usb_net_config[usb_mac] = record
    return record


def _usb_net_config_for(usb_mac):
    with _usb_net_lock:
        entry = _usb_net_config.get(_norm_usb_mac(usb_mac))
        return dict(entry) if entry else None


def _mark_usb_net_config_stale(usb_mac):
    """A failed parent read keeps the last known values but stops calling them fresh."""
    with _usb_net_lock:
        entry = _usb_net_config.get(_norm_usb_mac(usb_mac))
        if entry:
            entry["network_config_stale"] = True


def _refresh_icron_network_config(parent_ips=None, force=False):
    """Read authoritative Icron configuration for USB-capable parents.

    Bounded and deduplicated: a parent already read within the TTL is skipped
    unless forced, and a read already in flight is never started twice. One
    parent failing never affects the others.
    """
    context = _usb_parent_context()
    if parent_ips is None:
        parent_ips = sorted({entry.get("parent_ip") for entry in context["index"].values()
                             if entry.get("parent_ip")})
    parent_mac_by_ip = {entry.get("parent_ip"): entry.get("parent_mac", "")
                        for entry in context["index"].values()}
    usb_mac_by_ip = {entry.get("parent_ip"): mac for mac, entry in context["index"].items()}
    now = time.time()
    due = []
    with _usb_net_lock:
        for parent_ip in parent_ips:
            if not parent_ip or parent_ip in _usb_net_state["in_flight"]:
                continue
            existing = _usb_net_config.get(usb_mac_by_ip.get(parent_ip, ""))
            if not force and existing and (now - existing.get("network_config_last_read", 0)) < USB_NET_CONFIG_TTL:
                continue
            due.append(parent_ip)
        _usb_net_state["in_flight"].update(due)
    if not due:
        return {"checked": 0, "updated": 0, "skipped_fresh": True}

    def read_one(parent_ip):
        try:
            icron = _icron_interface(_omnistream_net_interfaces(parent_ip, USB_NET_CONFIG_TIMEOUT))
            if icron is None:
                return None
            return _ingest_icron_network_config(parent_ip, parent_mac_by_ip.get(parent_ip), icron)
        except Exception as exc:
            log.info("Icron network read failed for %s: %s", parent_ip, type(exc).__name__)
            stale_mac = usb_mac_by_ip.get(parent_ip)
            if stale_mac:
                _mark_usb_net_config_stale(stale_mac)
            return None

    updated = 0
    try:
        with ThreadPoolExecutor(max_workers=min(USB_NET_CONFIG_WORKERS, len(due)),
                                thread_name_prefix="usb-netcfg") as pool:
            for result in pool.map(read_one, due):
                if result:
                    updated += 1
    finally:
        with _usb_net_lock:
            _usb_net_state["in_flight"].difference_update(due)
    log.info("Icron network refresh: %d parent(s) read, %d updated", len(due), updated)
    return {"checked": len(due), "updated": updated, "skipped_fresh": False}


def _omnistream_reboot(ip, cache_map=None):
    """The established OmniStream reboot method.

    This is the implementation /api/reboot has always used, lifted to module
    level so Device Info and an integrated USB endpoint's Reboot share one
    proven operation instead of a second implementation.
    """
    ws_port = app.config['WS_PORT']
    ws_path = app.config['WS_PATH']
    timeout = app.config['TIMEOUT']
    if cache_map is None:
        cache_map = {d.get("ip"): d for d in (_load_cache() or []) if d.get("ip")}
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

@app.route("/api/reboot", methods=["POST"])
@_audited("reboot")
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
        return _omnistream_reboot(ip, cache_map)
    
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
        # Shared live-state refresh: throttled and in-flight suppressed, so two
        # open pages do not multiply device traffic.
        _usb_live_refresh()
        # Standalone route state comes from Advanced Query, so it is refreshed on
        # the slow pairing cadence rather than on every Matrix poll.
        _usb_pairing_refresh()
        # The same shared Link Status sweep Configure asks for. Both surfaces call
        # the one manager, which decides whether a physical read is warranted, so
        # having both pages open does not double the traffic.
        _usb_link_refresh()
        # Load from cache first (for testing with 100 units)
        all_devices = _load_cache()
        if not all_devices:
            # Fall back to scan results
            if SCAN_RESULTS.exists():
                try:
                    data = _read_scan_results()
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
        
        log.debug("[API/USB_STATE] Found %d USB-capable devices from %d total", len(units), len(all_devices))
        
        if not units:
            # No OmniStream USB units, but standalone extenders may still exist.
            standalone = _usb_standalone_inventory()
            axis_lex, axis_rex = _usb_matrix_axes()
            capabilities = {r["usb_key"]: {l["usb_key"]: _usb_route_capability(l, r) for l in axis_lex}
                            for r in axis_rex}
            standalone_routes = {r["usb_key"]: {"active": (r.get("paired_macs") or [None])[0],
                                                "available": [], "fresh": r.get("pairing_state_fresh", False)}
                                 for r in axis_rex}
            inventory_lex, inventory_rex = _usb_inventory()
            return jsonify({"ok": True, "lex": [], "rex": [], "pairings": {},
                            "inventory_lex": inventory_lex, "inventory_rex": inventory_rex,
                            "matrix_lex": axis_lex, "matrix_rex": axis_rex,
                            "capabilities": capabilities, "standalone_routes": standalone_routes,
                            **standalone})
        
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

                # The USB Matrix build is the authoritative usb_icron read the
                # rest of the application already performs. Feed the shared
                # association pipeline from it so every consumer benefits.
                _record_usb_association(ip, usb_cfg.get("macaddress"), role, usb_cfg.get("ipaddress"), u.get("mac"),
                                        icron_revision=usb_cfg.get("revision"))
                _record_parent_live(ip, len(usb_cfg.get("paired_devices") or {}))

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
                # Every unreachable parent, on every five-second poll: with five
                # offline devices that is sixty records a minute saying the same
                # thing. Whether an endpoint is answering is already carried on
                # the device record as `online` and `liveness_source`, and shown
                # in the UI, which is the authoritative place for it.
                log.debug("USB query failed for %s: %s", ip, e)
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
            # Identity first. These entries persist across address changes, so
            # resolving by address put the route on whichever unit had since
            # inherited it.
            mac_key = _norm_usb_mac(info.get("macaddress") or mac)
            if mac_key and mac_key in lex_by_mac:
                return lex_by_mac[mac_key]
            for candidate in candidates:
                if candidate in lex_by_control_ip:
                    return candidate
                if candidate in lex_by_usb_ip:
                    return lex_by_usb_ip[candidate]
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
            log.debug("Device %s paired_devices: %s", rex_ip, paired_devices)
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
        
        # Standalone AT-OMNI-311/324 inventory is reported separately from `lex`
        # and `rex`. The matrix grid is built from those two arrays, so keeping
        # standalone units out of them leaves existing routing untouched and adds
        # no route cells. Any UDP record that correlates to an E4521/D4511 parent
        # is omitted here because that endpoint already has a lex/rex row.
        # One derived view for both the enrichment below and the inventory rows.
        usb_view = _usb_extender_view()
        standalone = _usb_standalone_inventory(
            exclude_macs={_norm_usb_mac(u.get("mac")) for u in lex_units + rex_units if u.get("mac")}, view=usb_view)
        standalone_lex, standalone_rex = standalone["standalone_lex"], standalone["standalone_rex"]
        try:
            # Enrich existing rows with the USB-side address the UDP provider saw,
            # without overwriting anything firmware already reported.
            udp_by_mac = {_norm_usb_mac(d.get("mac")): d for d in (usb_view.get("devices") or [])}
            for unit in lex_units + rex_units:
                seen = udp_by_mac.get(_norm_usb_mac(unit.get("mac")))
                if seen:
                    unit["usb_discovered"] = True
                    unit["usb_online"] = bool(seen.get("online"))
                    if not unit.get("usb_ip"):
                        unit["usb_ip"] = seen.get("ip", "")
        except Exception as exc:
            log.info("USB endpoint enrichment unavailable: %s", type(exc).__name__)

        # Matrix axes: integrated units keep their existing control-IP key so the
        # proven usb_icron path is untouched; standalone units join the axes so
        # they can be routed where the hardware gate passed.
        firmware_by_ip = {u.get("ip"): (u.get("firmware") or u.get("version") or "")
                          for u in units if u.get("ip")}
        for unit, role in [(u, "LEX") for u in lex_units] + [(u, "REX") for u in rex_units]:
            unit.setdefault("kind", "integrated")
            # For an integrated endpoint the parent's OmniStream firmware is the
            # established source; the usb_icron `revision` is an Icron module
            # revision and is a different fact, so it keeps its own field.
            unit["icron_revision"] = unit.get("revision", "")
            unit["firmware"] = firmware_by_ip.get(unit.get("ip"), "") or unit.get("revision", "")
            unit["firmware_source"] = "omnistream" if firmware_by_ip.get(unit.get("ip")) else "usb_icron"
            # usb_key stays the control IP: the usb_icron path is addressed by IP
            # and is not being changed. usb_mac is the separate routing identity,
            # taken from the parent's own usb_icron `macaddress`.
            unit.setdefault("usb_key", unit.get("ip", ""))
            unit["usb_mac"] = _canonical_usb_mac(unit)
            unit.setdefault("classification", "INTEGRATED")
            unit.setdefault("type", role)
        axis_lex, axis_rex = _usb_matrix_axes(usb_view)
        # Deterministic order: the same membership always renders identically,
        # whatever order the usb_icron queries completed in.
        matrix_lex = sorted(lex_units + axis_lex, key=_usb_matrix_sort_key)
        matrix_rex = sorted(rex_units + axis_rex, key=_usb_matrix_sort_key)
        # Neither axis builder carries the endpoint's own network, so stamp it on
        # here, once, for both families. Without it every eligibility question
        # answers "cannot say" and a cross-subnet cell renders as available.
        _usb_attach_endpoint_networks(matrix_lex, usb_view)
        _usb_attach_endpoint_networks(matrix_rex, usb_view)
        # The same-subnet gate is fail-closed, so an endpoint whose mask has not
        # been read renders as "USB network information unavailable" and cannot
        # be routed. _usb_net_config is in-memory only, and nothing on this page
        # ever filled it: it was populated by discovery and by Configure > USB
        # (which asks with pairing=1), so after a restart an operator who opened
        # only the USB Matrix saw every cell permanently disabled.
        #
        # Asked for only when something is actually missing, so this converges to
        # no reads at all once the masks are known -- it is not another poll. The
        # refresh is itself TTL-bounded, deduplicated and in-flight guarded, and
        # runs on the background executor so this request never waits for it.
        if any(not entry.get("endpoint_mask") for entry in matrix_lex + matrix_rex):
            try:
                _usb_extenders._executor.submit(_refresh_icron_network_config)
            except Exception as exc:
                log.info("Could not schedule an Icron network read: %s", type(exc).__name__)
        lex_units.sort(key=_usb_matrix_sort_key)
        rex_units.sort(key=_usb_matrix_sort_key)
        capabilities = {}
        for rex_entry in matrix_rex:
            row = {}
            for lex_entry in matrix_lex:
                row[lex_entry["usb_key"]] = _usb_route_capability(lex_entry, rex_entry)
            capabilities[rex_entry["usb_key"]] = row
        # Standalone route state comes from the discovery cache with an explicit
        # freshness flag; it is display only and never authorizes a mutation.
        standalone_routes = {}
        for rex_entry in axis_rex:
            owner = (rex_entry.get("paired_macs") or [None])[0]
            standalone_routes[rex_entry["usb_key"]] = {
                "active": owner if owner else None,
                "available": [],
                "fresh": rex_entry.get("pairing_state_fresh", False),
                "pairing_age": rex_entry.get("pairing_age"),
            }
        # A mixed route lands on an integrated REX, and usb_icron does not observe
        # a pairing created over UDP, so that row would otherwise render as having
        # no route at all. Report the UDP-observed owner for those rows too, and
        # only when one was actually read: absence here never means "no route",
        # because usb_icron remains the owner of integrated pairing presentation.
        for unit in rex_units:
            record = _usb_extenders.device(unit.get("usb_mac") or "") or {}
            owner = (record.get("paired_macs") or [None])[0]
            # Only when a peer was actually observed. An empty or missing table is
            # not reported as "no route" here, because usb_icron owns that
            # statement for an integrated row; freshness is passed through so a
            # stale reading is shown as stale rather than as current.
            if owner:
                standalone_routes.setdefault(unit["usb_key"], {
                    "active": owner,
                    "available": [],
                    "fresh": bool(record.get("pairing_state_fresh")),
                    "pairing_age": record.get("pairing_age"),
                    "source": "udp_advanced_query",
                })

        # One row per physical endpoint. `lex`/`rex` and `standalone_lex`/
        # `standalone_rex` are still returned for existing consumers, but the
        # inventory tables render from this and this alone, so an endpoint that
        # appears in both collections can no longer produce two rows.
        inventory_lex, inventory_rex = _usb_inventory(lex_units, rex_units, view=usb_view)
        return jsonify({
            "ok": True,
            "lex": lex_units,
            "rex": rex_units,
            "inventory_lex": inventory_lex,
            "inventory_rex": inventory_rex,
            "matrix_lex": matrix_lex,
            "matrix_rex": matrix_rex,
            "capabilities": capabilities,
            "standalone_routes": standalone_routes,
            "standalone_lex": standalone_lex,
            "standalone_rex": standalone_rex,
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
        # Identity only. The dict key is the peer's MAC, so an address match
        # adds nothing and can drop a different device that happens to hold that
        # address -- or, in the read-back, confirm a write that never happened.
        if _norm_usb_mac(mac) == peer_mac_key or (peer_mac_key and info_mac == peer_mac_key):
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


@app.route("/api/usb_pair", methods=["POST"])
@_audited("usb_pair")
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
            return jsonify({"ok": False, "error": "Could not get LEX MAC address"}), 502
        
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
            return jsonify({"ok": False, "error": "Could not get REX MAC address"}), 502
        # Same-subnet eligibility, proven from the two USB endpoints' own networks,
        # before anything is written and before any existing pairing is released.
        # A USB link is not carried across a router, and the UI is not the safety
        # boundary: this endpoint has to establish it independently.
        lex_endpoint = {"usb_mac": lex_mac, "endpoint_ip": lex_cfg.get("ipaddress", ""),
                        "endpoint_mask": lex_cfg.get("subnetmask", "")}
        rex_endpoint = {"usb_mac": rex_mac, "endpoint_ip": rex_cfg.get("ipaddress", ""),
                        "endpoint_mask": rex_cfg.get("subnetmask", "")}
        verdict, network_detail = _usb_route_network_check(lex_endpoint, rex_endpoint)
        if verdict != "OK":
            log.info("USB pair refused (%s): LEX %s REX %s", verdict,
                     network_detail.get("lex_usb_ip"), network_detail.get("rex_usb_ip"))
            return jsonify({
                "ok": False, "status": verdict.lower(), "state": verdict, "network": network_detail,
                "error": ("These two USB endpoints are on different IP subnets. A USB route is not "
                          "carried across a router." if verdict == "NETWORK_MISMATCH" else
                          "The USB address or subnet mask of one endpoint has not been read, so this "
                          "route cannot be shown to be legal.")}), 409

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
        
    except (OSError, websocket.WebSocketException) as e:
        # The endpoint did not answer. An offline device is an upstream
        # condition, not a fault in this application, and reporting it as 500
        # put a routine "the unit is unplugged" into the same bucket as a bug.
        log.info("usb_pair: device unreachable: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        log.exception("usb_pair error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_unpair", methods=["POST"])
@_audited("usb_unpair")
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
            return jsonify({"ok": False, "error": "Could not get LEX MAC address"}), 502
        
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
            return jsonify({"ok": False, "error": "Could not get REX MAC address"}), 502
        
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
        
    except (OSError, websocket.WebSocketException) as e:
        # The endpoint did not answer. An offline device is an upstream
        # condition, not a fault in this application, and reporting it as 500
        # put a routine "the unit is unplugged" into the same bucket as a bug.
        log.info("usb_unpair: device unreachable: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        log.exception("usb_unpair error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_set_port", methods=["POST"])
@_audited("usb_set_port")
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
@_audited("usb_set_type")
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
        
    except (OSError, websocket.WebSocketException) as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        log.exception("usb_set_type error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/usb_set_filter", methods=["POST"])
@_audited("usb_set_filter")
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
                # The password that happened to work is not the browser's
                # business: it goes into the page, into any log of the response
                # and into anything that captures traffic. Nothing consumes it.
                return jsonify({"ok": True, "enable": enable})
            except Exception as e:
                last_error = str(e)
                continue

        # Offline/auth issues are expected in mixed network states; return non-500.
        return jsonify({"ok": False, "enable": False, "error": last_error}), 200
    except Exception as e:
        log.exception("get_thumbnail_status error")
        return jsonify({"ok": False, "enable": False, "error": str(e)}), 200

def _select_bind_port(preferred_port: int, bind_host: str) -> int:
    """The preferred port if it is free, otherwise the next one that is.

    8080 first, then 8081, 8082 and upward. Defined at module level rather than
    inside main() so the behaviour can be tested: nested, the only way to check
    that a busy 8080 yields 8081 rather than something arbitrary was to start
    the application and look.
    """
    candidates = [preferred_port]
    candidates.extend(p for p in range(preferred_port + 1, preferred_port + 101))
    for candidate in candidates:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((bind_host, candidate))
            return candidate
        except OSError:
            continue
        finally:
            try:
                probe.close()
            except Exception:
                pass
    # Last resort: let the OS choose, rather than refusing to start.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((bind_host, 0))
        return int(probe.getsockname()[1])
    finally:
        try:
            probe.close()
        except Exception:
            pass


def main():
    host = os.getenv("OMNI_HOST", "127.0.0.1")

    selected_port = _select_bind_port(PORT, host)
    # Recorded for the support dump: the port the operator's browser and the host
    # firewall actually see is this one, not PORT.
    _RUNTIME_BIND["host"] = host
    _RUNTIME_BIND["port"] = selected_port
    if selected_port != PORT:
        log.warning("Port %s is unavailable/blocked; using fallback port %s", PORT, selected_port)

    # The read-only startup refreshes begin here, not at import: importing this
    # module used to probe every address in whatever cache OMNI_DATA_DIR resolved
    # to, which put a plain unit-test run on real hardware.
    start_background_startup_tasks()
    log.info("Serving on http://%s:%s/", host, selected_port)

    def run_server():
        # Never debug=True: that serves Werkzeug's interactive console, which is
        # remote code execution behind a console-printed PIN, on any unhandled
        # exception.
        app.run(host=host, port=selected_port, debug=False, use_reloader=False, threaded=True)
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


# ==========================================================================
# Multiview
# ==========================================================================
# Multiview is decoder-only, additive, and lazily loaded: nothing here runs
# during a normal scan. `omni_multiview` owns every layout, scaler and
# allocation decision; this section only reads devices, writes them, and
# verifies what it wrote.
#
# The one rule that shapes all of it: the decoder accepts invalid values and
# still answers `error: false`. Transport success is not configuration success,
# so every mutation is followed by a semantic read-back.

# Stage names used by the show-on-display transaction. They are reported to
# the operator, so they read as the action rather than as the device field.
STAGE_OUTPUT_RESOLUTION = "output_resolution"
STAGE_SAP_SHOW = "sap_input"
STAGE_AUDIO_INPUT = "audio_ip_input"
STAGE_AUDIO_SELECT = "audio_input"
STAGE_SHOW = "show_on_display"

# A Multiview always drives the display at the one canvas this release builds.
MULTIVIEW_OUTPUT_RESOLUTION = omni_multiview.ACTIVE_CANVAS

# How long a freshly selected input is given to lock before its status is taken
# as the answer. A bounded wait on one operation, never a poll.
MULTIVIEW_INPUT_SETTLE = 6.0

# And how long each individual window is given. A window locks within a second
# of its input being pointed in the ordinary case; this is the bound past which
# one is reported as not showing.
MULTIVIEW_WINDOW_SETTLE = 10.0

MULTIVIEW_META = DATA_DIR / "multiview_meta.json"

_multiview_meta_lock = threading.RLock()
_MULTIVIEW_META = {}

MULTIVIEW_GROUPS = DATA_DIR / "multiview_groups.json"

_multiview_groups_lock = threading.RLock()
_MULTIVIEW_GROUPS = {}

# One group operation at a time. A group Show writes several decoders and the
# encoders they share; two of them interleaving would each plan against the
# other's half-applied state.
_multiview_group_lock = threading.RLock()


def _load_multiview_groups():
    """Read the saved groups. A missing or unreadable file means no groups."""
    global _MULTIVIEW_GROUPS
    try:
        with open(MULTIVIEW_GROUPS, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        payload = {}
    except Exception as exc:
        log.info("[MULTIVIEW] Could not read saved groups: %s", exc)
        payload = {}
    groups = payload.get("groups") if isinstance(payload, dict) else None
    with _multiview_groups_lock:
        _MULTIVIEW_GROUPS = {str(k): dict(v) for k, v in (groups or {}).items()
                             if isinstance(v, dict)}
    if _MULTIVIEW_GROUPS:
        log.info("[MULTIVIEW] Restored %d decoder group(s)", len(_MULTIVIEW_GROUPS))
    return _MULTIVIEW_GROUPS


def _save_multiview_groups():
    tmp = _atomic_tmp_path(MULTIVIEW_GROUPS)
    try:
        with _multiview_groups_lock:
            payload = {"groups": {k: dict(v) for k, v in _MULTIVIEW_GROUPS.items()}}
            MULTIVIEW_GROUPS.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, MULTIVIEW_GROUPS)
    except Exception as exc:
        log.info("[MULTIVIEW] Could not persist groups: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# A member is recorded by MAC where one is known, because an address can be
# reassigned and a group that follows the address would silently come to mean a
# different television. The address is kept too, as the way to reach it.
def _group_member_record(unit):
    return {"ip": unit.get("ip"),
            "mac": (unit.get("mac") or "").lower(),
            "hostname": unit.get("hostname") or unit.get("host") or ""}


def _resolve_group_member(member, devices=None):
    """The device a stored member refers to now, or None if it is not here."""
    units = devices if devices is not None else _load_cache()
    mac = (member.get("mac") or "").lower()
    if mac:
        for unit in units:
            if (unit.get("mac") or "").lower() == mac:
                return unit
    ip = member.get("ip")
    for unit in units:
        if unit.get("ip") == ip:
            return unit
    return None


def _group_view(group, devices=None):
    """A group as the page shows it: who is in it, and whether they are here."""
    units = devices if devices is not None else _load_cache()
    members = []
    for member in group.get("members") or ():
        unit = _resolve_group_member(member, units)
        members.append({
            "ip": (unit or {}).get("ip") or member.get("ip"),
            "mac": member.get("mac") or "",
            "hostname": (unit or {}).get("hostname") or member.get("hostname") or "",
            "discovered": unit is not None,
        })
    return {"id": group.get("id"), "name": group.get("name"),
            "members": members, "updated": group.get("updated"),
            "multiview": group.get("multiview") or None}


def _find_group(group_id):
    with _multiview_groups_lock:
        group = _MULTIVIEW_GROUPS.get(str(group_id))
        return dict(group) if group else None


# A capability answer is stable for the life of a device's firmware, so it is
# cached and never polled. Only a definite answer is cached; an unreachable
# device stays unknown so it is asked again rather than being written off.
_multiview_capability_lock = threading.RLock()
_MULTIVIEW_CAPABILITY = {}
MULTIVIEW_CAPABILITY_TTL = 3600.0
# A source that does not answer this costs one short connect instead of six
# WebSocket timeouts. Matches the preflight the scan already uses.
MULTIVIEW_PREFLIGHT_TIMEOUT = 0.6

# One Apply or Delete at a time, process-wide. Two transactions interleaving
# across the same encoder would each snapshot the other's half-applied state,
# and a rollback would then restore the wrong values.
_multiview_apply_lock = threading.RLock()


def _multiview_meta_key(device):
    """Identity for persisted Multiview metadata.

    Keyed on the hardware MAC where one is known so the record follows the
    device across address changes, exactly as USB associations do. An address
    is only a fallback for a device that has never reported a MAC.
    """
    identity = _device_identity(device)
    if identity:
        return "mac:" + identity
    address = str((device or {}).get("ip") or "").strip()
    return "ip:" + address if address else ""


def _load_multiview_meta():
    """Restore Multiview layout metadata so a restart does not lose it.

    The device stores geometry only; the layout name an operator chose exists
    nowhere else, so losing this file would turn every Multiview into Custom.
    """
    try:
        if not MULTIVIEW_META.exists():
            return
        data = json.loads(MULTIVIEW_META.read_text(encoding="utf-8"))
        entries = data.get("decoders") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return
        with _multiview_meta_lock:
            for key, entry in entries.items():
                if isinstance(entry, dict):
                    _MULTIVIEW_META[key] = entry
        log.info("[MULTIVIEW] Restored metadata for %d decoder(s)", len(_MULTIVIEW_META))
    except Exception as e:
        log.warning("[MULTIVIEW] Could not restore metadata: %s", e)


def _save_multiview_meta():
    tmp = _atomic_tmp_path(MULTIVIEW_META)
    try:
        with _multiview_meta_lock:
            payload = {"decoders": {k: dict(v) for k, v in _MULTIVIEW_META.items()}}
            MULTIVIEW_META.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, MULTIVIEW_META)
    except Exception as e:
        log.info("[MULTIVIEW] Could not persist metadata: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _multiview_meta_for(device):
    key = _multiview_meta_key(device)
    if not key:
        return {}
    with _multiview_meta_lock:
        return dict((_MULTIVIEW_META.get(key) or {}).get("multiviews") or {})


def _record_multiview_meta(device, object_name, record):
    key = _multiview_meta_key(device)
    if not key or not object_name:
        return
    with _multiview_meta_lock:
        entry = _MULTIVIEW_META.setdefault(key, {})
        entry["decoder_ip"] = device.get("ip")
        entry["decoder_hostname"] = device.get("hostname")
        # Which standard layout this came from is a fact about where the object
        # came from, not about its current contents, so it survives being saved
        # over. Without this, showing an installed layout turned it into an
        # ordinary preset and Install Standard Layouts would create a duplicate
        # of it -- measured on the bench, where a shown layout stopped being
        # recognised as one of the eleven.
        previous = (entry.setdefault("multiviews", {}) or {}).get(object_name) or {}
        for inherited in ("standard_layout", "standard"):
            if inherited in previous and inherited not in record:
                record[inherited] = previous[inherited]
        entry["multiviews"][object_name] = record
    _save_multiview_meta()


def _forget_multiview_meta(device, object_name):
    key = _multiview_meta_key(device)
    if not key:
        return
    with _multiview_meta_lock:
        entry = _MULTIVIEW_META.get(key) or {}
        if (entry.get("multiviews") or {}).pop(object_name, None) is None:
            return
    _save_multiview_meta()


def _known_multiviews(exclude_decoder_ip=None):
    """Every Multiview OmniSuite knows about, for scaler-conflict detection.

    This is what makes a conflict detectable at all. It is also why the conflict
    is reported as "known" rather than "all": a Multiview built in the device's
    own web UI is invisible here, which is precisely the unknown-external case.
    """
    records = []
    with _multiview_meta_lock:
        for entry in _MULTIVIEW_META.values():
            for object_name, record in (entry.get("multiviews") or {}).items():
                records.append({
                    "decoder_ip": entry.get("decoder_ip"),
                    "decoder_hostname": entry.get("decoder_hostname"),
                    "object_name": object_name,
                    "windows": record.get("windows") or [],
                    "current": entry.get("decoder_ip") == exclude_decoder_ip,
                })
    return records


def _managed_multiviews(device, present_names):
    """The Multiviews OmniSuite manages on this decoder, as the canvas rule sees them.

    A record counts only while the object is still on the device, so metadata
    left behind by something deleted elsewhere does not go on reserving a canvas.
    Objects OmniSuite did not create are absent from this list on purpose: they
    neither block a canvas nor are ever at risk of being overwritten.
    """
    managed = []
    for name, record in (_multiview_meta_for(device) or {}).items():
        if name not in present_names:
            continue
        managed.append({
            "object_name": name,
            "friendly_name": record.get("friendly_name") or name,
            "canvas": record.get("requested_canvas") or record.get("canvas") or "",
            "layout": record.get("layout"),
        })
    return managed


def _multiview_credentials(ip):
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    return (device,
            device.get("username") or app.config['USERNAME'],
            device.get("password") or app.config['PASSWORD'],
            app.config['WS_PORT'], app.config['WS_PATH'], app.config['TIMEOUT'])


# How many times a READ may be attempted, and how long to wait between them.
# Short on purpose: this sits inside an operation an operator is waiting for, and
# a device that has not answered in about a second and a half is not busy, it is
# away. Three attempts cost at most ~0.45s of waiting on top of the timeouts.
MULTIVIEW_READ_ATTEMPTS = 3
MULTIVIEW_RETRY_DELAYS = (0.15, 0.30)

# Records, per operation, which attempt succeeded -- so a diagnostic can say "the
# first read missed and the second worked" without the operator seeing anything.
_multiview_attempts_lock = threading.RLock()
_MULTIVIEW_ATTEMPTS = {}


def _note_attempt(ip, node, attempt, outcome, detail=""):
    with _multiview_attempts_lock:
        entries = _MULTIVIEW_ATTEMPTS.setdefault(threading.get_ident(), [])
        entries.append({"device": ip, "node": node, "attempt": attempt,
                        "outcome": outcome, "detail": detail[:160],
                        "at": time.time()})
        del entries[:-200]


def _attempt_log(clear=False):
    """What this thread has tried. Used by diagnostics, never by the operator."""
    with _multiview_attempts_lock:
        entries = list(_MULTIVIEW_ATTEMPTS.get(threading.get_ident()) or [])
        if clear:
            _MULTIVIEW_ATTEMPTS.pop(threading.get_ident(), None)
    return entries


def _retried_reads(entries=None):
    """The reads that needed more than one attempt, for the engineering panel."""
    entries = entries if entries is not None else _attempt_log()
    return [e for e in entries if e["outcome"] == "ok" and e["attempt"] > 1]


def _mv_get(ip, node, user=None, pwd=None, timeout=None):
    """One `config_get`, with the shared fallback-password behaviour.

    An unreachable device is reported as `__unreachable__` rather than as a
    device that said "no", because the two mean different things: one is unknown
    and must be retried, the other is a definite capability answer.
    """
    device, default_user, default_pwd, ws_port, ws_path, default_timeout = \
        _multiview_credentials(ip)
    last = "no attempt was made"
    for attempt in range(1, MULTIVIEW_READ_ATTEMPTS + 1):
        try:
            answer = _ws_send_recv_with_fallback(
                ip, {"id": f"{node}-get", "username": user or default_user,
                     "config_get": node},
                timeout or default_timeout, ws_port, ws_path, pwd or default_pwd)
            if isinstance(answer, dict):
                # A device that ANSWERED is finished with, whatever it said. An
                # error in the reply is the device's opinion, not a lost packet,
                # and asking again would only produce the same opinion.
                _note_attempt(ip, node, attempt, "ok")
                return answer
            last = "malformed response"
        except Exception as exc:
            last = str(exc)
        _note_attempt(ip, node, attempt, "failed", last)
        # Reads change nothing and each one opens its own connection, so the
        # next attempt starts clean. Nothing is reused, so nothing is stale.
        if attempt < MULTIVIEW_READ_ATTEMPTS:
            time.sleep(MULTIVIEW_RETRY_DELAYS[
                min(attempt - 1, len(MULTIVIEW_RETRY_DELAYS) - 1)])
    log.info("[MULTIVIEW] %s %s did not answer in %d attempt(s): %s",
             ip, node, MULTIVIEW_READ_ATTEMPTS, last)
    return {"__unreachable__": True, "error": last,
            "__attempts__": MULTIVIEW_READ_ATTEMPTS}


def _mv_config(ip, node, **kwargs):
    """The `config` list from a read, or [] when the device did not answer."""
    answer = _mv_get(ip, node, **kwargs)
    config = answer.get("config")
    return config if isinstance(config, list) else []


def _write_landed(ip, node, config):
    """Does the device already hold what this config_set was asking for?

    The only safe question after a write whose answer was lost. `config_set`
    merges by object name, so reading the object back and comparing the fields
    the write named says whether it arrived.
    """
    target = str(config.get("name") or "")
    items = _read_nodes([{"device": ip, "node": node, "target": target,
                          "config": config}])
    current = (items or {}).get((ip, node))
    if current is None:
        return None                      # could not read: still unknown
    actual = current.get(target)
    if actual is None:
        return False
    return not _diff_expected(config, actual)


def _mv_set(ip, node, config):
    """One `config_set`. Returns (ok, message). Never treated as verification.

    On a transport failure the device is read before anything is repeated: a
    write whose reply was lost may have arrived. Only when the read proves it
    did not is one retry allowed, and a config_set is safe to repeat because
    writing the same value twice is the same value.
    """
    device, user, pwd, ws_port, ws_path, timeout = _multiview_credentials(ip)

    def attempt():
        try:
            answer = _ws_send_recv_with_fallback(
                ip, {"id": f"{node}-set", "username": user,
                     "config_set": {"name": node, "config": [config]}},
                timeout, ws_port, ws_path, pwd)
        except Exception as exc:
            return None, str(exc)        # transport: the outcome is unknown
        if not isinstance(answer, dict):
            return None, "malformed response"
        if answer.get("error"):
            # The device answered and refused. That is a decision, not a miss.
            return False, str(answer.get("error_message") or answer.get("error"))
        return True, ""

    ok, message = attempt()
    if ok is not None:
        _note_attempt(ip, node, 1, "ok" if ok else "refused", message)
        return ok, message

    # Unknown outcome. Find out what the device actually holds.
    _note_attempt(ip, node, 1, "failed", message)
    landed = _write_landed(ip, node, config)
    if landed is True:
        _note_attempt(ip, node, 1, "ok", "the write had already arrived")
        return True, ""
    if landed is None:
        return False, message            # cannot even read: do not guess

    time.sleep(MULTIVIEW_RETRY_DELAYS[0])
    ok, retry_message = attempt()
    _note_attempt(ip, node, 2, "ok" if ok else "failed", retry_message)
    if ok is True:
        return True, ""
    if ok is False:
        return False, retry_message
    return False, retry_message or message


def _mv_method(ip, method, options):
    """One `method` call -- the only way to create or delete a named object.

    Never repeated. `add_multiview` twice is two objects and
    `del_multiview_subframe` twice removes something nobody asked about, so a
    lost reply is answered by reading the device rather than by trying again.
    The caller verifies semantically either way; this only avoids turning one
    instruction into two.
    """
    device, user, pwd, ws_port, ws_path, timeout = _multiview_credentials(ip)
    try:
        answer = _ws_send_recv_with_fallback(
            ip, {"id": f"{method}-method", "username": user,
                 "method": {method: options}},
            timeout, ws_port, ws_path, pwd)
    except Exception as e:
        _note_attempt(ip, method, 1, "failed", str(e))
        return False, str(e)
    if not isinstance(answer, dict):
        return False, "malformed response"
    if answer.get("error"):
        return False, str(answer.get("error_message") or answer.get("error"))
    return True, ""


def _multiview_capability(ip, refresh=False):
    """Whether this decoder supports Multiview, cached and never polled."""
    now = time.time()
    if not refresh:
        with _multiview_capability_lock:
            cached = _MULTIVIEW_CAPABILITY.get(ip)
        if cached and now - cached["at"] < MULTIVIEW_CAPABILITY_TTL:
            return cached["supported"], cached["reason"], True
    answer = _mv_get(ip, "multiview")
    supported, reason = omni_multiview.multiview_capable(answer)
    if supported is not None:
        with _multiview_capability_lock:
            _MULTIVIEW_CAPABILITY[ip] = {"supported": supported, "reason": reason,
                                         "at": now}
    return supported, reason, False


@app.route("/api/multiview/layouts", methods=["GET"])
def api_multiview_layouts():
    """The canonical layout geometry, computed once and consumed by the UI.

    The preview the operator drags sources onto is drawn from this, and the same
    numbers are written to the decoder, so the picture and the hardware cannot
    drift apart.
    """
    return jsonify({
        "ok": True,
        "layouts": omni_multiview.layout_geometry_catalog(),
        "canvases": [
            {"id": p["id"], "width": p["width"], "height": p["height"]}
            for p in omni_multiview.CANVAS_PRESETS if p["exposed"]
        ],
        "anchors": list(omni_multiview.ANCHORS),
        "max_subframes": omni_multiview.MAX_SUBFRAMES,
        # The one canvas this release plans, applies and shows.
        # The operator notice is acknowledged per release, so the page has
        # to know which release it is. It rides with the layout
        # catalogue because that is already the first thing loaded.
        "version": _app_version(),
        "canvas": omni_multiview.ACTIVE_CANVAS,
        "output_resolution": omni_multiview.ACTIVE_CANVAS,
        "window_ip_inputs": list(omni_multiview.WINDOW_IP_INPUTS),
        "main_windows": {name: omni_multiview.main_window_cell(name)
                         for name in omni_multiview.LAYOUT_ORDER},
        "budgets": {"source": omni_multiview.SOURCE_VIDEO_BUDGET,
                    "decoder": omni_multiview.DECODER_MULTIVIEW_BUDGET},
    })


def _reachability(addresses):
    """One short TCP probe per address, concurrently.

    Not on the scan path and not a poll: the Multiview page asks once when it
    loads, and the answer decides whether a device is offered at all. A serial
    version of this was what made an unreachable source cost 27.5 seconds, so
    it is done in a pool with the same 0.6s bound the planner already uses.
    """
    addresses = sorted({a for a in addresses if a})
    if not addresses:
        return {}

    def probe(ip):
        return ip, _tcp_probe(ip, [app.config.get("WS_PORT", 80), 80],
                              timeout=MULTIVIEW_PREFLIGHT_TIMEOUT)

    if len(addresses) == 1:
        ip, up = probe(addresses[0])
        return {ip: up}
    with ThreadPoolExecutor(max_workers=min(16, len(addresses))) as pool:
        return dict(pool.map(probe, addresses))


@app.route("/api/multiview/decoders", methods=["GET"])
def api_multiview_decoders():
    """Decoders that can actually be given a Multiview.

    A decoder that does not answer is not a choice, so it is not offered as one.
    A decoder that answers but is barred by Video Wall or Fast Switching *is*
    offered, disabled, with the reason -- the operator can turn those off, and
    would never guess that was the problem from an empty list.

    `probe=1` asks the devices whose capability is not yet known. Without it the
    list is answered purely from cache, so opening the page repeatedly costs
    nothing.
    """
    probe = request.args.get("probe") in ("1", "true", "yes")
    candidates = [d for d in _load_cache()
                  if str(d.get("role") or d.get("type") or "").lower() == "decoder"]
    live = _reachability([d.get("ip") for d in candidates]) if probe else {}

    decoders, probed = [], 0
    for device in candidates:
        ip = device.get("ip")
        reachable = live.get(ip) if probe else None
        with _multiview_capability_lock:
            cached = _MULTIVIEW_CAPABILITY.get(ip)
        supported = cached["supported"] if cached else None
        reason = cached["reason"] if cached else ""
        if supported is None and probe and ip and reachable is not False:
            supported, reason, _was_cached = _multiview_capability(ip)
            probed += 1
        classification = omni_multiview.classify_decoder(
            device, reachable=reachable, multiview_supported=supported)
        decoders.append({
            "ip": ip,
            "mac": device.get("mac") or "",
            "hostname": device.get("hostname") or ip,
            "model": device.get("model") or "",
            "multiview_supported": supported,
            "reachable": reachable,
            "status": classification["status"],
            "status_label": omni_multiview.SOURCE_STATUS_LABELS.get(
                classification["status"], classification["status"]),
            "reason": classification["reason"] or reason,
            "detail": classification["detail"],
        })
    decoders.sort(key=lambda d: _ip_sort_key(d.get("ip")))
    offered = [d for d in decoders if d["status"] != omni_multiview.SOURCE_INELIGIBLE
               or d["reachable"] is not False]

    # Saved groups are targets too, and they belong in the same list the
    # operator already uses to choose what they are working on. Read from the
    # stored groups only -- no device is touched to answer this.
    units = _load_cache()
    with _multiview_groups_lock:
        stored = [dict(g) for g in _MULTIVIEW_GROUPS.values()]
    stored.sort(key=lambda g: (g.get("name") or "").lower())
    groups = [_group_view(group, units) for group in stored]

    return jsonify({"ok": True, "decoders": offered, "probed": probed,
                    "groups": groups,
                    "hidden": len(decoders) - len(offered)})


# The still image every OmniStream encoder publishes when its thumbnail
# generator is running. Encoder 1 only: `vc2_encoder2` carries no thumbnail
# fields at all, and `thumbnail2.jpg` is a placeholder on every device measured.
# Encoder 2 takes the same physical input as Encoder 1 -- that is a Phase 5 rule
# -- so Encoder 1's thumbnail is a picture of what the Multiview window will
# show, which is exactly what the operator is trying to identify.
MULTIVIEW_PREVIEW_PATH = "/thumbnail/thumbnail1.jpg"

PREVIEW_AVAILABLE = "available"
PREVIEW_DISABLED = "disabled"
PREVIEW_UNAVAILABLE = "unavailable"


def _preview_state(ip, device):
    """Whether this encoder is currently generating a thumbnail.

    Measured, and the reason this is a device read rather than a guess: with the
    thumbnail disabled the device still serves HTTP 200 and a placeholder JPEG,
    so the browser sees a successful image load either way. The only thing that
    distinguishes them is the encoder's own `thumbnail.enable`.
    """
    if not omni_multiview.is_eligible_source(device)[0]:
        # An excluded model is not offered as a source, so it is not previewed.
        return {"status": PREVIEW_UNAVAILABLE,
                "reason": "This model is not offered as a Multiview source."}

    encoders = _mv_config(ip, "vc2")
    if not encoders:
        return {"status": PREVIEW_UNAVAILABLE,
                "reason": "The encoder did not answer."}

    first = next((e for e in encoders
                  if str(e.get("name") or "") == "vc2_encoder1"), None)
    if first is None:
        return {"status": PREVIEW_UNAVAILABLE,
                "reason": "This encoder has no Encoder 1 to preview."}

    thumbnail = first.get("thumbnail") or {}
    if "enable" not in thumbnail:
        return {"status": PREVIEW_UNAVAILABLE,
                "reason": "This encoder does not support preview."}
    if not thumbnail.get("enable"):
        return {"status": PREVIEW_DISABLED,
                "reason": "Preview is turned off on this encoder.",
                "width": thumbnail.get("width"),
                "height": thumbnail.get("height")}
    return {"status": PREVIEW_AVAILABLE, "reason": "",
            "width": thumbnail.get("width"),
            "height": thumbnail.get("height"),
            "framerate": thumbnail.get("framerate")}


@app.route("/api/multiview/preview", methods=["GET"])
def api_multiview_preview():
    """Can this source be previewed, and from where?

    Called when the operator hovers a source tile and not before -- there is no
    preview work on page load, on the source listing or on the Scan path. It
    reads one node from one device and writes nothing.

    The response carries the URL the browser should load. It is built here so
    the page never assembles a device URL of its own, and it carries no
    credential: the thumbnail is published unauthenticated on port 80, which is
    how the Matrix page has always loaded it.

    A preview that cannot be shown is never an eligibility failure. A source
    that is perfectly routable but whose preview is off is still routable, and
    this endpoint says nothing about that either way.
    """
    ip = str(request.args.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "ip required"}), 400

    device = {d.get("ip"): d for d in _load_cache()}.get(ip)
    if device is None:
        return jsonify({"ok": False, "status": PREVIEW_UNAVAILABLE,
                        "reason": "OmniSuite has not discovered this device."}), 404
    if str(device.get("role") or device.get("type") or "").lower() != "encoder":
        return jsonify({"ok": False, "status": PREVIEW_UNAVAILABLE,
                        "reason": "Only an encoder has a preview."}), 400

    state = _preview_state(ip, device)
    body = {
        "ok": True,
        "ip": ip,
        "hostname": device.get("hostname") or ip,
        "model": device.get("model") or "",
        "status": state["status"],
        "reason": state.get("reason", ""),
        "width": state.get("width"),
        "height": state.get("height"),
        "framerate": state.get("framerate"),
    }
    if state["status"] == PREVIEW_AVAILABLE:
        body["url"] = "http://%s%s" % (ip, MULTIVIEW_PREVIEW_PATH)
    return jsonify(body)


def _encoder1_bitrates(addresses):
    """Each encoder's current Encoder 1 bitrate, read concurrently.

    One `config_get` per source, and only where a caller is deciding
    eligibility. A device that does not answer is simply absent from the
    result: unknown is not a refusal.
    """
    addresses = sorted({ip for ip in addresses if ip})
    if not addresses:
        return {}

    def read(ip):
        entry = next((e for e in _mv_config(ip, "vc2")
                      if str(e.get("name") or "")
                      == omni_multiview.encoder_object_name(1)), None)
        value = (entry or {}).get("bitrate")
        return ip, value if isinstance(value, (int, float)) else None

    if len(addresses) == 1:
        ip, value = read(addresses[0])
        return {ip: value} if value is not None else {}
    with ThreadPoolExecutor(max_workers=min(8, len(addresses))) as pool:
        return {ip: value for ip, value in pool.map(read, addresses)
                if value is not None}


@app.route("/api/multiview/sources", methods=["GET"])
def api_multiview_sources():
    """Encoders that can feed a Multiview window, each with its own state.

    Answered from the existing discovery cache plus one short reachability probe
    per encoder -- no device configuration is read, so the source list costs a
    round trip and nothing else. Session detail is loaded later, only for a
    source actually used.

    Three states rather than a single list: an offline encoder is not a choice,
    an encoder whose Session 2 has no multicast is a choice the operator can
    make work, and those two need different words.
    """
    probe = request.args.get("probe") not in ("0", "false", "no")
    devices = list(_load_cache())
    encoders = [d for d in devices
                if str(d.get("role") or d.get("type") or "").lower() == "encoder"]
    live = _reachability([d.get("ip") for d in encoders]) if probe else {}

    # A source whose primary stream is using the whole budget has nothing left
    # to carry a window, and the operator should learn that from the list rather
    # than from a refusal after they have assigned it. The scan does not record
    # Encoder 1's bitrate, so it is read here -- at the moment eligibility is
    # deliberately being evaluated, for the reachable encoders only, and
    # concurrently. Nothing about this runs on the scan path or while idle.
    # `probe=0` is the deliberately cheap call: it answers from the cache and
    # touches nothing, so it reads no bitrates either.
    headroom = _encoder1_bitrates(
        [d.get("ip") for d in encoders if live.get(d.get("ip"))]
        if probe else [])

    sources, excluded = [], []
    for device in encoders:
        ip = device.get("ip")
        classification = omni_multiview.classify_source(
            device, reachable=live.get(ip) if probe else None,
            encoder1_bitrate=headroom.get(ip))
        # The scan records an encoder's session multicast under
        # `sessionN_video_mcast`. `ipN_addr` is the decoder-side field -- the
        # address a decoder *listens* to -- and reading it here left every tile
        # showing "not set".
        entry = {
            "ip": ip,
            "mac": device.get("mac") or "",
            "hostname": device.get("hostname") or ip,
            "model": device.get("model") or "",
            "codec": device.get("codec") or "",
            "reachable": live.get(ip) if probe else None,
            "status": classification["status"],
            "status_label": omni_multiview.SOURCE_STATUS_LABELS.get(
                classification["status"], classification["status"]),
            "reason": classification["reason"],
            "detail": classification["detail"],
            "action": classification["action"],
            "encoder1_bitrate": headroom.get(ip),
            "headroom": omni_multiview.multiview_headroom(headroom.get(ip))[0],
            "session1": device.get("session1_video_mcast") or "",
            "session1_port": device.get("session1_video_port") or "",
            "session1_audio": device.get("session1_audio_mcast") or "",
            "session2": device.get("session2_video_mcast") or "",
            "session2_port": device.get("session2_video_port") or "",
        }
        # A conventional A/V Matrix route is a different question from a
        # Multiview window, and the page must not answer it by reusing the
        # Multiview verdict: an encoder whose primary stream uses the whole
        # budget cannot feed a window and is still a perfectly good ordinary
        # source. Decided here, by the one rule, so the page holds none of it.
        normal = omni_multiview.normal_route_eligibility(
            device, reachable=live.get(ip) if probe else None)
        entry["normal_route"] = normal["status"]
        entry["normal_route_reason"] = normal["reason"]
        if classification["status"] == omni_multiview.SOURCE_INELIGIBLE:
            excluded.append(entry)
        else:
            sources.append(entry)
    sources.sort(key=lambda d: (d["status"] != omni_multiview.SOURCE_READY,
                                _ip_sort_key(d.get("ip"))))
    excluded.sort(key=lambda d: _ip_sort_key(d.get("ip")))
    return jsonify({"ok": True, "sources": sources, "excluded": excluded,
                    "ready": sum(1 for s in sources
                                 if s["status"] == omni_multiview.SOURCE_READY)})


def _decoder_state(ip):
    """One lazy read of everything the Multiview page needs from a decoder."""
    multiview = _mv_get(ip, "multiview")
    supported, reason = omni_multiview.multiview_capable(multiview)
    if supported is not True:
        return None, (reason or "This decoder does not expose Multiview.")
    hdmi = _mv_config(ip, "hdmi_output")
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    return {
        "ip": ip,
        # The Fast Switching interlock is decided from the model identity, so
        # the planner is given it rather than left to guess from the address.
        "model": device.get("model") or "",
        "hostname": device.get("hostname") or ip,
        "multiview": multiview.get("config") or [],
        "ip_input": _mv_config(ip, "ip_input"),
        "hdmi_output": hdmi[0] if hdmi else {},
    }, ""


def _showable(obj):
    """Can this Multiview object be put on the display by this release?

    Returns the two fields the page needs: whether Show is possible and, when
    it is not, the sentence to put in front of the operator. The canvas rule is
    the one that bites in practice -- a 4K object saved by an earlier release is
    still on the decoder and still listed, and clicking Show on it produced a
    409 with nothing on screen to explain it.
    """
    width, height = obj.get("width"), obj.get("height")
    if omni_multiview.output_resolution_for_canvas(
            width, height) != MULTIVIEW_OUTPUT_RESOLUTION:
        return {"showable": False,
                "not_showable_reason":
                    "This is a %sx%s Multiview. This release shows %s "
                    "Multiviews only." % (width, height,
                                          MULTIVIEW_OUTPUT_RESOLUTION)}
    return {"showable": True, "not_showable_reason": ""}


def _install_standard_layouts(ip):
    """Put the standard layout library on one decoder. Returns a report.

    The whole point of this operation is what it does NOT do. It writes one
    Multiview object per missing layout and nothing else: no encoder input, no
    scaler, no bitrate, no Session 2, no decoder subscription and no display
    change. A layout is a shape, and a shape reserves nothing.

    Idempotent by layout identity, not by name. A preset an operator renamed is
    still that standard layout and is left alone; an object OmniSuite did not
    create is never overwritten, whatever it is called.
    """
    state, error = _decoder_state(ip)
    if state is None:
        return None, error or "the decoder could not be read"

    devices = _load_cache() or []
    device = {d.get("ip"): d for d in devices}.get(ip, {})
    present = {str(o.get("name") or "") for o in (state.get("multiview") or ())}
    stored = _multiview_meta_for(device) or {}

    # Which standard layouts this decoder already has. Identity first: a record
    # that names the layout it was installed from, whose object is still there.
    installed_layout = {}
    for object_name, record in stored.items():
        if object_name not in present:
            continue
        layout = record.get("standard_layout") or (
            record.get("layout") if record.get("standard") else None)
        if layout:
            installed_layout.setdefault(layout, object_name)

    existing, installed, skipped, conflicts = [], [], [], []
    for layout in omni_multiview.STANDARD_LAYOUTS:
        friendly = omni_multiview.LAYOUTS[layout]["label"]
        known = installed_layout.get(layout)
        if known:
            existing.append({"layout": layout, "friendly_name": friendly,
                             "object_name": known})
            continue
        # The name this layout naturally takes. Asked for WITHOUT collision
        # avoidance on purpose: if something else already has it, installing a
        # near-duplicate under a numbered name would be worse than saying so.
        natural, config, windows = omni_multiview.standard_layout_object(layout)
        object_name = natural
        if not object_name:
            skipped.append({"layout": layout, "friendly_name": friendly,
                            "reason": "this release does not run that layout"})
            continue
        if object_name in present:
            # The name is taken by something OmniSuite did not install. It is
            # somebody's work; it is reported, never replaced.
            conflicts.append({"layout": layout, "friendly_name": friendly,
                              "object_name": object_name,
                              "reason": "%s already exists on this decoder and "
                                        "was not created by OmniSuite."
                                        % object_name})
            continue
        ok, message = _mv_method(ip, "add_multiview", dict(config))
        landed = any(str(o.get("name") or "") == object_name
                     for o in _mv_config(ip, "multiview"))
        if not (ok and landed):
            skipped.append({"layout": layout, "friendly_name": friendly,
                            "object_name": object_name,
                            "reason": message or "the decoder did not create it"})
            continue
        present.add(object_name)
        _record_multiview_meta(device, object_name, {
            "layout": layout,
            "standard_layout": layout,
            "standard": True,
            "friendly_name": friendly,
            "canvas": "%sx%s" % (config["width"], config["height"]),
            "requested_canvas": omni_multiview.ACTIVE_CANVAS,
            "updated": time.time(),
            # No source, no input, no multicast: there is nothing to record
            # about resources a layout does not use.
            "windows": [{"cell": w["cell"],
                         "label": omni_multiview.subframe_label(
                             w["cell"], w["width"], w["height"]),
                         "x": w["x"], "y": w["y"], "anchor": w["anchor"],
                         "width": w["width"], "height": w["height"],
                         "is_main": w["cell"] == omni_multiview.main_window_cell(layout),
                         } for w in windows],
        })
        installed.append({"layout": layout, "friendly_name": friendly,
                          "object_name": object_name})

    log.info("[MULTIVIEW] standard layouts on %s: %d installed, %d already "
             "present, %d skipped, %d conflict(s)",
             ip, len(installed), len(existing), len(skipped), len(conflicts))
    return {"decoder": ip,
            "hostname": device.get("hostname") or ip,
            "available": len(omni_multiview.STANDARD_LAYOUTS),
            "installed": installed, "existing": existing,
            "skipped": skipped, "conflicts": conflicts}, ""


@app.route("/api/multiview/layouts/install", methods=["POST"])
def api_multiview_layouts_install():
    """Install the standard layout library, and change no display anywhere.

    Accepts a single decoder or a group. For a group every member gets the same
    library, which is what makes a group able to show a common layout later --
    and still nothing is shown and no encoder is touched.
    """
    payload = request.get_json(silent=True) or {}
    group_id = (payload.get("group") or "").strip()
    if group_id:
        group = _find_group(group_id)
        if group is None:
            return jsonify({"ok": False, "error": "No such group."}), 404
        targets = [m.get("ip") for m in (group.get("members") or ()) if m.get("ip")]
        label = group.get("name")
    else:
        ip = (payload.get("decoder") or "").strip()
        if not ip:
            return jsonify({"ok": False, "error": "decoder ip required"}), 400
        targets, label = [ip], ip

    reports, problems = [], []
    for target in targets:
        report, error = _install_standard_layouts(target)
        if report is None:
            problems.append({"decoder": target, "error": error})
            continue
        reports.append(report)

    installed = sum(len(r["installed"]) for r in reports)
    existing = sum(len(r["existing"]) for r in reports)
    conflicts = [c for r in reports for c in r["conflicts"]]
    skipped = [s for r in reports for s in r["skipped"]]
    total = len(omni_multiview.STANDARD_LAYOUTS)

    if not reports:
        return jsonify({"ok": False, "status": "FAILED",
                        "error": (problems[0]["error"] if problems
                                  else "nothing to install"),
                        "problems": problems}), 502

    if len(targets) == 1:
        message = ("%d standard layouts available \u2014 %d installed, %d already "
                   "existed." % (total, installed, existing))
    else:
        message = ("%d standard layouts on each of %d decoders \u2014 %d "
                   "installed, %d already existed."
                   % (total, len(reports), installed, existing))
    if conflicts:
        message += (" %d name(s) already in use by something OmniSuite did not "
                    "create, and left alone." % len(conflicts))

    return jsonify({
        "ok": True, "status": "VERIFIED", "target": label,
        "available": total, "installed": installed, "existing": existing,
        "conflicts": conflicts, "skipped": skipped,
        "problems": problems, "decoders": reports,
        "message": message,
        # Said plainly, because it is the promise the operation makes.
        "display_changed": False, "encoder_writes": 0,
    })


@app.route("/api/multiview/state", methods=["GET"])
def api_multiview_state():
    """The selected decoder's Multiview state. Three reads, on demand only."""
    ip = (request.args.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "decoder ip required"}), 400
    state, error = _decoder_state(ip)
    if state is None:
        supported, _reason, _cached = _multiview_capability(ip)
        status = 200 if supported is False else 502
        return jsonify({"ok": False, "error": error, "supported": supported}), status

    devices = list(_load_cache())
    device = {d.get("ip"): d for d in devices}.get(ip, {})
    stored = _multiview_meta_for(device)
    hdmi = state["hdmi_output"] or {}
    selected = str((hdmi.get("video") or {}).get("input") or "")

    views = []
    for obj in state["multiview"]:
        name = str(obj.get("name") or "")
        subframes = obj.get("subframes") or []
        reconciled = omni_multiview.reconcile_layout(
            stored.get(name), int(obj.get("width") or 0), int(obj.get("height") or 0),
            subframes)
        views.append({
            "name": name,
            "width": obj.get("width"),
            "height": obj.get("height"),
            "slice_info": obj.get("slice_info"),
            "layout": reconciled["layout"],
            "layout_label": (omni_multiview.LAYOUTS[reconciled["layout"]]["label"]
                             if reconciled["layout"] else "Custom / Unknown"),
            "layout_source": reconciled["source"],
            "layout_diverged": bool(reconciled.get("diverged")),
            "stored_layout": reconciled.get("stored_layout"),
            "canvas": reconciled.get("canvas"),
            "selected_on_output": name == selected,
            # The same verdict the show endpoint reaches, reported here so the
            # page can say why instead of offering an action that 409s. One
            # copy of the rule, and the page holds none of it.
            **_showable(obj),
            "subframes": [_subframe_view(s, state, stored.get(name), devices)
                          for s in subframes],
        })
    views.sort(key=lambda v: v["name"].lower())

    present = {v["name"] for v in views}
    managed = _managed_multiviews(device, present)
    # A decoder holds as many saved Multiviews as the operator wants, so the
    # canvas is never "taken". The list stays in the response because the page
    # still states which canvas it builds, but nothing gates on availability.
    canvases = [{"id": omni_multiview.ACTIVE_CANVAS,
                 "width": 1920, "height": 1080, "available": True,
                 "used_by": None, "used_by_label": None, "reason": ""}]

    return jsonify({
        "ok": True,
        "decoder": {"ip": ip, "hostname": device.get("hostname") or ip,
                    "model": device.get("model") or "", "mac": device.get("mac") or ""},
        # Video Wall and Fast Switching bar Multiview outright, so the page is
        # told before it offers any of it rather than after an attempt fails.
        "interlocks": omni_multiview.interlocks(device.get("model"), hdmi),
        "canvas": omni_multiview.ACTIVE_CANVAS,
        "multiviews": views,
        "canvases": canvases,
        "can_create": True,
        "managed": managed,
        "hdmi_output": {
            "video_input": selected,
            "audio_input": (hdmi.get("audio") or {}).get("input") or "",
            "aux_input": (hdmi.get("aux") or {}).get("input") or "",
            "available_inputs": (hdmi.get("video") or {}).get("available_inputs") or [],
            "sap_enabled": bool((hdmi.get("sap_input") or {}).get("enabled")),
            "sap_session": (hdmi.get("sap_input") or {}).get("session") or "",
            "output_status": (hdmi.get("video") or {}).get("output") or {},
            "output_resolution": ((hdmi.get("video") or {}).get("output") or {})
                                 .get("resolution") or "",
            "input_status": (hdmi.get("video") or {}).get("status") or {},
            "video_wall": omni_multiview.video_wall_enabled(hdmi),
            "fast_switching": omni_multiview.fast_switching_enabled(hdmi),
        },
        "ip_inputs": omni_multiview.classify_ip_inputs(
            state["ip_input"], hdmi, state["multiview"]),
        # What is on the display right now, as opposed to what is selected in
        # the page or saved on the decoder. Derived from the same read; no
        # extra request, no timer.
        "display_output": _display_output_view(state, devices),
    })


def _resolve_subscription(address, port, devices=None):
    """Which encoder session a decoder input is currently listening to.

    Matched on the address *and* the port, because that pair is the stream --
    an address alone can belong to two sessions on different ports. Encoders
    only: a decoder records the multicast it listens to in the same field, so
    searching every device resolves a window's source to another decoder
    subscribed to the same stream.

    Returns a source dict, or None when nothing discovered carries it. None is
    an answer the page shows, not a reason to show the window as empty.
    """
    if not address:
        return None
    wanted = omni_multiview.stream_identity(address, port)
    for device in (devices if devices is not None else _load_cache()):
        if not omni_multiview.is_eligible_source(device)[0]:
            continue
        for index, session in ((1, "session1"), (2, "session2")):
            candidate = omni_multiview.stream_identity(
                device.get("%s_video_mcast" % session),
                device.get("%s_video_port" % session) or port)
            if candidate and candidate == wanted:
                return {"ip": device.get("ip"),
                        "hostname": device.get("hostname") or device.get("ip"),
                        "model": device.get("model") or "",
                        "session": session, "encoder_index": index,
                        "resolved_from": "subscription"}
        # Older cache records, kept so a device discovered before the session
        # fields existed still resolves. Address only; there is no port to pair.
        for legacy in ("ip1_addr", "ip3_addr"):
            if device.get(legacy) and str(device[legacy]) == str(address):
                return {"ip": device.get("ip"),
                        "hostname": device.get("hostname") or device.get("ip"),
                        "model": device.get("model") or "",
                        "session": "", "encoder_index": None,
                        "resolved_from": "subscription"}
    return None


def _display_output_view(state, devices=None):
    """What the decoder is ACTUALLY putting on its display.

    Read from the decoder's own current output selection and nothing else. A
    saved Multiview, a remembered browser selection and the last button pressed
    all describe intent; only `hdmi_output.video.input` describes the display.

    Three answers, because they call for three different presentations:

      multiview   the output is compositing a Multiview, named
      source      the output is an ip_input carrying a stream we can name
      none        nothing is selected, or what is selected carries nothing

    A stream that is arriving but belongs to no discovered encoder is still
    `source` -- something is plainly on the screen -- with no source named. It
    is never reported as `none`, and `none` is never dressed up as live.

    Derived entirely from the decoder state the caller has already read. This
    adds no device read and no polling.
    """
    hdmi = (state or {}).get("hdmi_output") or {}
    video_input = str((hdmi.get("video") or {}).get("input") or "")
    audio_input = str((hdmi.get("audio") or {}).get("input") or "")
    multiview = omni_multiview.active_multiview_name(video_input)
    if multiview:
        return {"state": "multiview", "multiview": multiview,
                "video_input": video_input, "audio_input": audio_input,
                "video": None, "audio": None, "live": True}

    devices = devices if devices is not None else _load_cache()
    inputs = {str(entry.get("name") or ""): entry
              for entry in ((state or {}).get("ip_input") or ())}

    def leg(name):
        entry = inputs.get(name) or {}
        address = (entry.get("multicast") or {}).get("address") or ""
        port = entry.get("port")
        enabled = bool(entry.get("enabled"))
        carrying = bool(name and address and enabled)
        return {"ip_input": name, "multicast": address, "port": port,
                "enabled": enabled, "carrying": carrying,
                "source": _resolve_subscription(address, port, devices)
                          if carrying else None}

    video, audio = leg(video_input), leg(audio_input)
    return {"state": "source" if video["carrying"] else "none",
            "multiview": "",
            "video_input": video_input, "audio_input": audio_input,
            "video": video, "audio": audio,
            "live": video["carrying"]}


def _subframe_view(subframe, state, stored, devices=None):
    """One window as the page shows it, from what the decoder is subscribed to.

    The distinction this exists to keep: a saved Multiview describes what was
    wanted, and the decoder's inputs say what is actually arriving. After a
    recall the canvas must show the second, because that is what is on the
    screen -- and when a live subscription cannot be resolved to a known
    encoder it is shown as an unknown source rather than erased, since
    something is plainly playing in that window.
    """
    name = str(subframe.get("name") or "")
    cell, width, height = omni_multiview.parse_subframe_label(name)
    ip_input = str(subframe.get("input") or "")

    entry = next((i for i in (state.get("ip_input") or ())
                  if str(i.get("name") or "") == ip_input), {})
    address = (entry.get("multicast") or {}).get("address") or ""
    port = entry.get("port")
    subscribed = bool(ip_input and address and entry.get("enabled"))

    stored_window = next((w for w in ((stored or {}).get("windows") or [])
                          if w.get("cell") == cell), None)
    video = subframe.get("video") or {}
    active = bool((video.get("input") or {}).get("active"))

    source, origin = None, "none"
    if address:
        source = _resolve_subscription(address, port, devices)
        origin = "subscription" if source else "unknown"
    if source is None and not address and stored_window and stored_window.get("source_ip"):
        # Nothing is arriving, so the only thing to show is what was saved --
        # marked as such, because it is not what the decoder is doing.
        source = {"ip": stored_window.get("source_ip"),
                  "hostname": stored_window.get("source_hostname")
                              or stored_window.get("source_ip"),
                  "model": stored_window.get("source_model") or "",
                  "session": stored_window.get("session") or "",
                  "encoder_index": stored_window.get("encoder_index"),
                  "resolved_from": "saved"}
        origin = "saved"

    return {
        "name": name, "cell": cell,
        "width": width, "height": height,
        "x": subframe.get("x"), "y": subframe.get("y"),
        "anchor": subframe.get("anchor"), "priority": subframe.get("priority"),
        "ip_input": ip_input,
        "multicast": address,
        "multicast_port": port,
        "stream": ("%s:%s" % (address, port)) if address else "",
        "ip_input_enabled": bool(entry.get("enabled")),
        "packets": ((entry.get("status") or {}).get("packets")),
        "subscribed": subscribed,
        "source": source,
        "source_origin": origin,
        "encoder_index": (source or {}).get("encoder_index")
                         or (stored_window or {}).get("encoder_index"),
        "saved_source_ip": (stored_window or {}).get("source_ip"),
        "diverged": bool(stored_window and source
                         and stored_window.get("source_ip")
                         and source.get("ip")
                         and stored_window["source_ip"] != source["ip"]),
        "input_active": active,
        "output_active": bool((video.get("output") or {}).get("active")),
        "health": ("live" if active else
                   "no signal" if subscribed else
                   "not subscribed"),
    }


def _encoder_state(ip):
    """Read one source encoder's vc2, sessions and input, or report it absent.

    A short TCP preflight first, the same one the scan uses. Without it an
    unreachable encoder costs six WebSocket timeouts -- three nodes, each retried
    with the fallback password -- which measured 27.5 seconds on the bench and
    left the page looking like it had simply stopped. Failing in 0.4s turns that
    into an answer the operator can act on.
    """
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    absent = {"device": device, "vc2": [], "sessions": [],
              "input_resolution": "", "reachable": False}
    if not _tcp_probe(ip, [app.config.get("WS_PORT", 80), 80],
                      timeout=MULTIVIEW_PREFLIGHT_TIMEOUT):
        log.info("[MULTIVIEW] source %s did not answer a TCP preflight", ip)
        return ip, absent
    vc2 = _mv_config(ip, "vc2")
    if not vc2:
        # It answered TCP but not the API: still absent for planning purposes,
        # and there is no point asking it two more questions.
        return ip, absent
    sessions = _mv_config(ip, "sessions")
    hdmi_input = _mv_config(ip, "hdmi_input")
    # An encoder reports its input under `video.resolution`, with `active` saying
    # whether anything is connected. That is not the shape a decoder uses for its
    # output, and a disconnected input can still report the last size it saw.
    resolution = ""
    if hdmi_input:
        video = hdmi_input[0].get("video") or {}
        size = video.get("resolution") or {}
        if video.get("active") and size.get("width") and size.get("height"):
            resolution = "%sx%s" % (size.get("width"), size.get("height"))
    return ip, {"device": device, "vc2": vc2, "sessions": sessions,
                "input_resolution": resolution,
                "reachable": bool(vc2 and sessions)}


def _gather_encoder_states(source_ips):
    """Read every assigned source concurrently.

    Only the encoders a window is assigned to are contacted, and only when a plan
    is being built -- dragging a tile reads nothing. Concurrently because one slow
    source used to delay every other one behind it.
    """
    addresses = sorted(set(filter(None, source_ips)))
    if not addresses:
        return {}
    if len(addresses) == 1:
        ip, state = _encoder_state(addresses[0])
        return {ip: state}
    with ThreadPoolExecutor(max_workers=min(8, len(addresses))) as pool:
        return dict(pool.map(_encoder_state, addresses))


def _unreachable_sources(plan):
    """The windows whose source could not be read, as the operator sees them."""
    out = []
    for window in (plan or {}).get("windows") or ():
        source = window.get("source") or {}
        if source.get("ip") and source.get("reachable") is False:
            out.append({"ip": source["ip"],
                        "hostname": source.get("hostname") or source["ip"],
                        "window": window.get("cell"),
                        "attempts": MULTIVIEW_READ_ATTEMPTS})
    return out


def _plan_refusal(plan, operation):
    """Turn a refused plan into (body, status) that says which kind it is.

    Three outcomes, three meanings:

      a source did not answer   -> 503, try again when it is back
      a live conflict           -> 409, something else has to change first
      anything else             -> 400, this cannot be built as asked
    """
    unreachable = _unreachable_sources(plan)
    # A refused Save is refused for a preset reason, and quoting an execution
    # error at an operator who was only trying to store a layout would name a
    # problem that is not in their way.
    reasons = list((plan.get("preset_errors") if operation == "save"
                    else plan.get("errors")) or [])
    if unreachable:
        names = ", ".join(sorted({entry["hostname"] for entry in unreachable}))
        return {
            "ok": False,
            "status": "DEVICE UNREACHABLE",
            "classification": "device_unreachable",
            "operation": operation,
            "unreachable": unreachable,
            "attempts": MULTIVIEW_READ_ATTEMPTS,
            "writes": 0,
            "plan": plan,
            "error": ("%s did not answer after %d attempts, so %s cannot be "
                      "prepared. No device was changed."
                      % (names, MULTIVIEW_READ_ATTEMPTS,
                         "this Multiview" if len(unreachable) > 1
                         else "window " + str(unreachable[0]["window"]))),
        }, 503
    if plan.get("conflicts"):
        return {"ok": False, "status": "conflict", "classification": "conflict",
                "operation": operation, "writes": 0, "plan": plan,
                "error": "; ".join(plan["conflicts"])}, 409
    return {"ok": False, "status": "invalid", "classification": "invalid",
            "operation": operation, "writes": 0, "plan": plan,
            "error": "; ".join(reasons) or "This Multiview cannot be built as asked."}, 400


def _build_plan(payload):
    """Read everything the plan depends on, then hand it to the planner."""
    ip = str(payload.get("decoder") or payload.get("ip") or "").strip()
    if not ip:
        return None, ({"ok": False, "error": "decoder ip required"}, 400)
    state, error = _decoder_state(ip)
    if state is None:
        return None, ({"ok": False, "error": error}, 502)
    assignments = payload.get("assignments") or {}
    encoder_states = _gather_encoder_states(assignments.values())
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    managed = _managed_multiviews(
        device, {str(o.get("name") or "") for o in (state.get("multiview") or [])})
    object_name = payload.get("object_name")
    plan = omni_multiview.plan_multiview(
        {"layout": payload.get("layout"),
         "canvas": payload.get("canvas") or omni_multiview.ACTIVE_CANVAS,
         "assignments": assignments, "name": payload.get("name"),
         "object_name": object_name,
         "update_existing": bool(payload.get("update_existing")),
         "owned_inputs": _owned_pool_inputs(device, state)},
        state, encoder_states, _known_multiviews(exclude_decoder_ip=ip),
        managed=managed,
        # Nothing records a claim any more: ownership is `owned_inputs`,
        # from _owned_pool_inputs. See the note on _releasable_pool_inputs.
        claimed_inputs=())
    plan["decoder"] = {"ip": ip, "hostname": state.get("hostname") or ip,
                       "model": state.get("model") or ""}
    return (plan, state, encoder_states), None


@app.route("/api/multiview/plan", methods=["POST"])
def api_multiview_plan():
    """What Apply would do, with no mutation whatsoever.

    This is what the confirmation summary is built from, so the operator is shown
    the same plan that will be executed rather than a description of it.
    """
    built, failure = _build_plan(request.get_json(silent=True) or {})
    if failure:
        body, status = failure
        return jsonify(body), status
    plan, _state, _encoders = built
    return jsonify({"ok": True, "plan": plan})


# --------------------------------------------------------------------------
# Verified apply
# --------------------------------------------------------------------------

def _snapshot_fields(entries):
    """Capture every field the transaction may modify, before it modifies any.

    Read per device and node rather than per field so one read serves several
    fields, and so the snapshot is a coherent picture of that node.
    """
    snapshot = {}
    for entry in entries:
        key = (entry["device"], entry["node"])
        if key in snapshot:
            continue
        snapshot[key] = {str(item.get("name") or ""): copy.deepcopy(item)
                         for item in _mv_config(entry["device"], entry["node"])}
    return snapshot


def _already_applied(mutation):
    """Does the device already hold what this mutation would write?

    The same comparison `_verify_mutation` makes afterwards, made beforehand, so
    a transaction can skip the writes that would change nothing. A device that
    cannot be read is treated as not satisfying anything: the write is attempted
    and its own verification decides.
    """
    try:
        current = {str(item.get("name") or ""): item
                   for item in _mv_config(mutation["device"], mutation["node"])}
    except Exception:
        return False
    actual = current.get(mutation.get("target"))
    if actual is None:
        return False
    if mutation.get("method"):
        # A method call creates or removes something; there is no "already
        # written" reading of it that is safe to infer from a config read.
        return False
    return not _diff_expected(mutation.get("config") or {}, actual)


def _merge_config(base, extra):
    """Deep-merge two config dicts, so a group's end state can be judged at once."""
    merged = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _count_fields(config):
    """Leaf fields in a config, excluding the object's own name."""
    total = 0
    for key, value in (config or {}).items():
        if key == "name":
            continue
        if isinstance(value, dict):
            total += _count_fields(value)
        elif isinstance(value, list):
            total += 1
        else:
            total += 1
    return total


def _read_nodes(mutations):
    """One read per (device, node) this transaction touches.

    A device that cannot be read yields None, which is treated as satisfying
    nothing: the writes are attempted and their own verification decides.
    """
    current = {}
    for mutation in mutations:
        key = (mutation["device"], mutation["node"])
        if key in current:
            continue
        try:
            current[key] = {str(item.get("name") or ""): item
                            for item in _mv_config(mutation["device"],
                                                   mutation["node"])}
        except Exception:
            current[key] = None
    return current


def _transaction_diff(mutations):
    """Split a planned transaction into the writes that are actually needed.

    Returns (needed, skipped, stats). `stats` answers the question a diagnostic
    should be able to answer about any Multiview transaction: how many fields
    were planned, how many the devices already held, and how many had to be
    written.
    """
    order = {id(m): index for index, m in enumerate(mutations)}
    groups = {}
    for mutation in mutations:
        # A method call creates or removes something; there is no "already
        # written" reading of a config that could stand in for it.
        key = ("method", id(mutation)) if mutation.get("method") else (
            mutation["device"], mutation["node"], mutation.get("target"))
        groups.setdefault(key, []).append(mutation)

    current = _read_nodes([m for m in mutations if not m.get("method")])
    needed, skipped = [], []
    planned_fields, correct_fields = 0, 0

    for key, group in groups.items():
        if key[0] == "method":
            needed.extend(group)
            continue
        device, node, target = key
        desired = {}
        for mutation in group:
            desired = _merge_config(desired, mutation.get("config") or {})
        fields = _count_fields(desired)
        planned_fields += fields

        items = current.get((device, node))
        actual = (items or {}).get(target)
        differences = (["unreadable"] if items is None else
                       ["missing"] if actual is None else
                       _diff_expected(desired, actual))
        if differences:
            needed.extend(group)
            correct_fields += max(0, fields - len(differences))
        else:
            skipped.extend(group)
            correct_fields += fields

    needed.sort(key=lambda m: order[id(m)])
    skipped.sort(key=lambda m: order[id(m)])
    return needed, skipped, {
        "planned_fields": planned_fields,
        "already_correct": correct_fields,
        "writes_required": len(needed),
        "planned_mutations": len(mutations),
        "skipped": [{"step": m["description"], "stage": m["stage"]}
                    for m in skipped],
    }


def _verify_mutation(mutation):
    """Read the device back and check it actually holds what we asked for.

    The decoder accepts a nonexistent input, a 0x0 canvas and a bogus delete flag
    and answers `error: false` to all three, so this is the only thing that
    distinguishes a write that worked from one that was ignored.
    """
    current = {str(item.get("name") or ""): item
               for item in _mv_config(mutation["device"], mutation["node"])}
    actual = current.get(mutation["target"])
    if actual is None:
        return False, f"{mutation['target']} is not present on {mutation['device']}"
    if mutation.get("method") == "del_multiview_subframe":
        # A removal is verified by absence; there are no fields left to compare.
        remaining = [str(s.get("name") or "") for s in (actual.get("subframes") or [])]
        if mutation["subframe"] in remaining:
            return False, f"subframe {mutation['subframe']!r} is still present"
        return True, ""
    differences = _diff_expected(mutation["config"], actual)
    if differences:
        return False, "; ".join(differences)
    return True, ""


def _diff_expected(expected, actual, path=""):
    """Every place the device disagrees with what we asked for.

    Only the fields the mutation named are compared. The device adds read-only
    status everywhere, and comparing whole objects would report those as
    failures.
    """
    differences = []
    for key, wanted in (expected or {}).items():
        here = f"{path}.{key}" if path else key
        if key == "name":
            continue
        got = (actual or {}).get(key)
        if isinstance(wanted, dict):
            differences.extend(_diff_expected(wanted, got if isinstance(got, dict) else {}, here))
        elif isinstance(wanted, list):
            if key == "subframes":
                differences.extend(_diff_subframes(wanted, got or []))
            elif list(wanted) != list(got or []):
                differences.append(f"{here}: wanted {wanted!r}, device has {got!r}")
        else:
            if str(got) != str(wanted):
                differences.append(f"{here}: wanted {wanted!r}, device has {got!r}")
    return differences


def _diff_subframes(expected, actual):
    """Subframes are matched by name, not by position."""
    differences = []
    by_name = {str(item.get("name") or ""): item for item in actual}
    for wanted in expected:
        name = str(wanted.get("name") or "")
        got = by_name.get(name)
        if got is None:
            differences.append(f"subframe {name!r} is missing")
            continue
        differences.extend(_diff_expected(wanted, got, f"subframe[{name}]"))
    return differences


def _restore_snapshot(snapshot, applied):
    """Put back only the fields this transaction changed, then read them back.

    Restoration is best-effort by nature -- the devices offer no transaction --
    so the result says explicitly whether each restore was verified rather than
    assuming it worked.
    """
    results = []
    for mutation in reversed(applied):
        key = (mutation["device"], mutation["node"])
        original = (snapshot.get(key) or {}).get(mutation["target"])
        if mutation.get("method") == "del_multiview_subframe":
            was = next((s for s in ((original or {}).get("subframes") or [])
                        if str(s.get("name") or "") == mutation["subframe"]), None)
            if was is None:
                results.append({"step": mutation["description"], "action": "none",
                                "verified": False,
                                "error": "the removed subframe was not captured"})
                continue
            # Restoring the multiview object itself merges the captured subframe
            # list back in, so by the time this runs the window is usually
            # already there. Adding it again would breach the four-subframe cap
            # and report a rollback failure that had not happened.
            present = [str(s.get("name") or "") for s in
                       next((o.get("subframes") or [] for o in
                             _mv_config(mutation["device"], "multiview")
                             if o.get("name") == mutation["target"]), [])]
            if mutation["subframe"] in present:
                results.append({"step": mutation["description"],
                                "action": "already restored", "verified": True,
                                "error": ""})
                continue
            ok, message = _mv_method(mutation["device"], "add_multiview_subframe", {
                "multiview": mutation["target"], "name": was.get("name"),
                "x": was.get("x", 0), "y": was.get("y", 0),
                "anchor": was.get("anchor", "top left"),
                "priority": was.get("priority", 1), "input": was.get("input", "")})
            restored_now = [str(s.get("name") or "") for s in
                            next((o.get("subframes") or [] for o in
                                  _mv_config(mutation["device"], "multiview")
                                  if o.get("name") == mutation["target"]), [])]
            verified = ok and mutation["subframe"] in restored_now
            results.append({"step": mutation["description"], "action": "re-added",
                            "verified": verified,
                            "error": "" if verified else (message or "not restored")})
            continue
        if original is None:
            if mutation.get("method") == "add_multiview":
                ok, message = _mv_method(mutation["device"], "del_multiview",
                                         {"name": mutation["target"]})
                present = any(str(o.get("name") or "") == mutation["target"]
                              for o in _mv_config(mutation["device"], "multiview"))
                results.append({"step": mutation["description"], "action": "removed",
                                "verified": ok and not present,
                                "error": "" if ok else message})
            else:
                results.append({"step": mutation["description"], "action": "none",
                                "verified": False,
                                "error": "nothing was captured for this field"})
            continue
        config = _restore_config(mutation, original)
        ok, message = _mv_set(mutation["device"], mutation["node"], config)
        verified = False
        detail = message
        if ok:
            verified, detail = _verify_mutation({**mutation, "config": config})
        results.append({"step": mutation["description"], "action": "restored",
                        "verified": verified, "error": "" if verified else detail})
    return results


# The only subframe fields a device accepts. Everything else a subframe reports
# -- `video.hdcp`, `video.input.active`, `video.output.active` -- describes the
# stream arriving, not anything that was configured.
SUBFRAME_SETTABLE = ("name", "x", "y", "anchor", "priority", "input")


def _restore_config(mutation, original):
    """The smallest write that puts the captured value back.

    Only settable fields are carried over. The captured subframe list also holds
    read-only status, and copying that into the restore made it part of what the
    read-back then compared: a rollback captured while a window was dark records
    `video.hdcp: none`, and once the picture is back the device reports `2.2`,
    so a restore that worked perfectly reported itself as unverified. Measured
    on the bench -- twice, identically -- as "ROLLBACK INCOMPLETE" against a
    decoder that had in fact been restored exactly.
    """
    config = {"name": mutation["target"]}
    for key in (mutation.get("config") or {}):
        if key == "name" or key not in original:
            continue
        value = copy.deepcopy(original[key])
        if key == "subframes" and isinstance(value, list):
            value = [{k: v for k, v in (item or {}).items()
                      if k in SUBFRAME_SETTABLE} for item in value]
        config[key] = value
    return config


def _owned_pool_inputs(device, state, exclude=None):
    """Pool inputs OmniSuite may reconfigure, because it put them there.

    A pool input is OmniSuite's to reuse when some Multiview it manages on this
    decoder references it -- that object was created here, so the input under it
    was configured here. Everything else in the pool is judged on what it is
    carrying right now, which is the only thing that makes it a live resource.

    A saved Multiview's metadata referencing an input is deliberately not enough
    on its own: a decoder holds many saved Multiviews and only one is active, so
    treating every saved reference as a reservation would let the first saved
    layout lock the pool against all the others.
    """
    managed = set(_multiview_meta_for(device) or {})
    owned = set()
    for obj in state.get("multiview") or ():
        name = str(obj.get("name") or "")
        if name not in managed or (exclude and name == exclude):
            continue
        for subframe in obj.get("subframes") or ():
            target = str(subframe.get("input") or "")
            if target in omni_multiview.WINDOW_IP_INPUTS:
                owned.add(target)
    return owned


def _saved_assignments(device, object_name):
    """The window -> source map OmniSuite recorded when the Multiview was saved."""
    record = (_multiview_meta_for(device) or {}).get(object_name) or {}
    return ({w["cell"]: w["source_ip"] for w in (record.get("windows") or [])
             if w.get("cell") and w.get("source_ip")}, record)


def _log_failed_transaction(operation, decoder, name, plan, failures,
                            applied, rollback=None, classification=""):
    """One traceable line for a transaction that did not finish.

    Deliberately one line: a failure someone is looking at in a screenshot
    should be findable without reconstructing it from six of them.
    """
    last = (failures or [{}])[-1]
    sources = sorted({(w.get("source") or {}).get("ip")
                      for w in (plan or {}).get("windows") or ()
                      if (w.get("source") or {}).get("ip")})
    attempts = len([entry for entry in _attempt_log()
                    if entry["device"] == last.get("device", "")]) or None
    restored = None
    if rollback:
        restored = all(entry.get("verified", entry.get("restored"))
                       for entry in rollback)
    log.warning(
        "[MULTIVIEW] %s failed | op=%s decoder=%s multiview=%s layout=%s "
        "sources=%s stage=%s device=%s attempts=%s written=%d class=%s "
        "rollback=%s | %s",
        operation, uuid.uuid4().hex[:8], decoder, name,
        (plan or {}).get("layout"), ",".join(sources) or "-",
        last.get("stage") or "-", last.get("device") or decoder,
        attempts or "-", len(applied or []), classification or "-",
        "not needed" if not rollback else
        ("verified" if restored else "INCOMPLETE"),
        str(last.get("error") or "")[:200])


def _apply_saved_plan(plan, decoder_ip):
    """Write a planned Multiview to a decoder, verified, with rollback.

    Returns (body, http_status). Shared by Apply, by Copy and by a copy to a
    group, so all three have the same transaction rather than three versions of
    one that drift apart.
    """
    mutations = plan.get("mutations") or []
    device = {d.get("ip"): d for d in _load_cache()}.get(decoder_ip, {})

    with _multiview_apply_lock:
        snapshot = _snapshot_fields([
            entry for entry in (plan.get("snapshot") or [])
            if entry["device"] == decoder_ip and entry["node"] == "multiview"])
        applied, verified, failures = [], [], []

        for mutation in mutations:
            if mutation.get("method") in ("add_multiview", "del_multiview_subframe"):
                ok, message = _mv_method(mutation["device"], mutation["method"],
                                         dict(mutation["config"]))
            else:
                ok, message = _mv_set(mutation["device"], mutation["node"],
                                      mutation["config"])
            if not ok:
                failures.append({"step": mutation["description"],
                                 "stage": mutation["stage"], "error": message})
                break
            applied.append(mutation)
            # Transport success is not configuration success. Read it back.
            good, detail = _verify_mutation(mutation)
            verified.append({"step": mutation["description"], "stage": mutation["stage"],
                             "verified": good, "detail": detail})
            if not good:
                failures.append({"step": mutation["description"],
                                 "stage": mutation["stage"],
                                 "error": f"the device accepted the write but does "
                                          f"not hold it: {detail}"})
                break

        if failures:
            rollback = _restore_snapshot(snapshot, applied)
            complete = all(entry["verified"] for entry in rollback) if rollback else True
            _log_failed_transaction("save", decoder_ip, plan.get("object_name"),
                                    plan, failures, applied, rollback,
                                    "write_or_readback")
            return {
                "ok": False,
                "status": "FAILED — ROLLED BACK" if complete
                          else "FAILED — ROLLBACK INCOMPLETE",
                "plan": plan, "applied": [m["description"] for m in applied],
                "verified": verified, "failures": failures, "rollback": rollback,
            }, 200

        _record_multiview_meta(device, plan["object_name"], {
            "layout": plan["layout"],
            "friendly_name": plan["friendly_name"],
            "canvas": f"{plan['canvas']['width']}x{plan['canvas']['height']}",
            "requested_canvas": "%sx%s" % (plan["requested_canvas"]["width"],
                                           plan["requested_canvas"]["height"]),
            "updated": time.time(),
            "windows": [{
                "cell": w["cell"], "label": w["label"],
                "x": w["x"], "y": w["y"], "anchor": w["anchor"],
                "width": w["width"], "height": w["height"],
                "scaler_format": w["scaler_format"],
                "encoder_index": w["encoder_index"],
                "session": w["session"],
                "ip_input": (w.get("ip_input") or {}).get("ip_input"),
                "source_ip": (w.get("source") or {}).get("ip"),
                "source_hostname": (w.get("source") or {}).get("hostname"),
                "source_model": (w.get("source") or {}).get("model"),
                "multicast": (w.get("multicast") or {}).get("address"),
                "multicast_port": (w.get("multicast") or {}).get("port"),
                "is_main": w.get("is_main", False),
            } for w in plan["windows"] if w.get("source")],
        })
        log.info("[MULTIVIEW] %s saved on %s (%d verified change(s))",
                 plan["object_name"], decoder_ip, len(verified))
        return {"ok": True, "status": "VERIFIED", "plan": plan,
                "applied": [m["description"] for m in applied],
                "verified": verified,
                "message": "Saved. The display is unchanged; use Show on "
                           "Display to put it on screen."}, 200


@app.route("/api/multiview/apply", methods=["POST"])
def api_multiview_apply():
    """Save a Multiview: store its configuration, and change nothing else.

    A decoder holds many saved Multiviews and only one of them is on the output,
    so saving cannot claim live resources -- two saved layouts will routinely
    want the same Encoder 2 at different sizes, and making them coexist is not
    possible even in principle. Saving therefore writes the Multiview object and
    its metadata; every encoder, session and decoder input is prepared at Show,
    against the state that exists then.

    The plan is still built from freshly read device state, so what is stored is
    checked against real sources rather than trusted from the page.
    """
    payload = request.get_json(silent=True) or {}
    built, failure = _build_plan(payload)
    if failure:
        body, status = failure
        return jsonify(body), status
    plan, state, encoder_states = built

    # Save asks the PRESET question, not the execution question. A source that
    # is offline, has no second encoder or has no Session 2 destination cannot
    # be prepared right now, and none of that is a reason to refuse to remember
    # that the operator wants it in that window. Scaler conflicts are the same:
    # two saved layouts wanting one encoder at two sizes is normal, and only
    # showing one of them is a decision.
    #
    # Everything those checks protect is still enforced, at Show, against the
    # state that exists then -- which is the only state that can be protected.
    if not plan.get("preset_ok"):
        body, status = _plan_refusal(plan, "save")
        return jsonify(body), status
    body, status = _apply_saved_plan(plan, plan["decoder"]["ip"])
    return jsonify(body), status




def _recall_plan(ip, name, state):
    """Re-plan a saved Multiview against the state the devices are in NOW.

    Nothing about the encoders is assumed to have survived since the Multiview
    was saved: another saved layout may have been recalled in between and left
    Encoder 2 at a different size, a different bitrate, or pointed somewhere
    else entirely. So every source is read again and the whole requirement is
    recomputed, which is what makes recall a transition rather than a replay.

    Returns (plan, state, encoder_states, record, failure), where `failure` is
    (message, http status) when the recall cannot be planned at all.
    """
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    assignments, record = _saved_assignments(device, name)
    # "No assignments" and "no record" are different things. A layout with
    # nothing assigned is a perfectly good Multiview -- the decoder composites
    # it and keeps its output active -- so only the second is a refusal.
    if not record:
        return None, None, None, None, (
            f"OmniSuite has no record of what feeds {name}, so it cannot be "
            f"prepared. Open it and save it again.", 409)
    assignments = assignments or {}

    encoder_states = _gather_encoder_states(assignments.values())
    plan = omni_multiview.plan_multiview(
        {"layout": record.get("layout"), "canvas": omni_multiview.ACTIVE_CANVAS,
         "assignments": assignments, "name": record.get("friendly_name"),
         "object_name": name, "update_existing": True,
         "owned_inputs": _owned_pool_inputs(device, state)},
        state, encoder_states, _known_multiviews(exclude_decoder_ip=ip),
        managed=_managed_multiviews(
            device, {str(o.get("name") or "") for o in (state.get("multiview") or [])}),
        claimed_inputs=())
    plan["decoder"] = {"ip": ip, "hostname": state.get("hostname") or ip,
                       "model": state.get("model") or ""}
    return plan, state, encoder_states, record, None


def _rebind_subframes(ip, name, plan, state):
    """Point the object's subframes at the inputs this recall actually allocated.

    The allocation is a function of the saved window-to-source map and each
    source's current Session 2 destination. A source that has been given a new
    destination since the Multiview was saved therefore lands on a different
    input, and the object on the device has to be told.
    """
    existing = next((o for o in state.get("multiview") or ()
                     if str(o.get("name") or "") == name), {})
    current = {str(s.get("name") or ""): str(s.get("input") or "")
               for s in (existing.get("subframes") or [])}
    wanted = {w["label"]: (w.get("ip_input") or {}).get("ip_input") or ""
              for w in plan["windows"]}
    changed = {label: value for label, value in wanted.items()
               if current.get(label) != value}
    if not changed:
        return None
    return {
        "stage": omni_multiview.STAGE_MULTIVIEW, "device": ip, "node": "multiview",
        "target": name,
        "description": "Bind %s to %s" % (
            name, ", ".join("%s -> %s" % (k.split(" (")[0], v)
                            for k, v in sorted(changed.items()))),
        "config": {"name": name, "subframes": [
            {"name": label, "input": value} for label, value in sorted(changed.items())]},
    }


def _releasable_pool_inputs(ip, device, state, required, owned=None):
    """Pool inputs OmniSuite owns that the configuration being activated does not need.

    Cleanup is decided from what the ACTIVE configuration requires, never from
    what some other saved Multiview's metadata mentions: a saved object is a
    description and reserves nothing. An input carrying an HDMI role, or one
    carrying something OmniSuite did not configure, is still never touched.
    """
    inputs = {str(i.get("name") or ""): i for i in (state.get("ip_input") or ())}
    hdmi = state.get("hdmi_output") or {}
    roles = {item["name"]: item for item in omni_multiview.classify_ip_inputs(
        state.get("ip_input"), hdmi, ())}
    # Ownership is passed in when the object that proves it has already been
    # removed -- a delete forgets the metadata, and reading it afterwards would
    # make every input the deleted Multiview configured look like somebody
    # else's and leak it permanently.
    owned = _owned_pool_inputs(device, state) if owned is None else set(owned)
    release, kept = [], []
    for name in omni_multiview.WINDOW_IP_INPUTS:
        entry = inputs.get(name)
        if entry is None or not entry.get("enabled"):
            continue
        if name in (required or ()):
            kept.append((name, "still required by this Multiview"))
            continue
        role = roles.get(name) or {}
        other = [r for r in (role.get("roles") or []) if not r.startswith("Multiview")]
        if other:
            kept.append((name, omni_multiview._describe_roles(other)))
            continue
        if name not in owned:
            kept.append((name, "OmniSuite did not configure it"))
            continue
        release.append(name)
    return release, kept


def _show_multiview_on(ip, name):
    """Recall a saved Multiview: prepare everything it needs, then display it.

    This is where a Multiview becomes real. Saving stored a description; recall
    reads the devices as they are now, works out the difference, and closes it:

        Encoder 2's input, scaler and bitrate           (per source)
        Encoder 1's bitrate, only if the budget forces it
        Session 2 assigned, transmitting, announcing nothing
        the decoder inputs the distinct streams need
        the subframes rebound if the allocation moved
        the pool inputs this configuration no longer needs, released
        output resolution -> 1920x1080
        SAP Input off
        the audio input carrying the main window's Session 1 audio
        the Multiview selected for video, that input selected for audio

    and then the one check that is not a read-back of our own write: the
    decoder's Input status. Every step is snapshotted and rolled back together.

    Returns (body, http_status) so a group operation can walk several
    decoders and look at each answer. The single-decoder endpoint below is
    a wrapper around this.
    """

    # The cheap refusals come first, and none of them needs OmniSuite's own
    # records: whether the decoder will take a Multiview at all, and whether
    # this object is one this release knows how to drive.
    state, error = _decoder_state(ip)
    if state is None:
        return {"ok": False, "error": error}, 502
    target = next((o for o in state["multiview"]
                   if str(o.get("name") or "") == name), None)
    if target is None:
        return {"ok": False, "error": f"{name} is not on this decoder"}, 404

    hdmi = state["hdmi_output"] or {}
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})

    blocked = omni_multiview.interlocks(device.get("model"), hdmi)
    if blocked:
        return {"ok": False, "status": "REFUSED", "interlocks": blocked,
                        "error": " ".join(e["reason"] for e in blocked)}, 409

    if omni_multiview.output_resolution_for_canvas(
            target.get("width"), target.get("height")) != MULTIVIEW_OUTPUT_RESOLUTION:
        return {"ok": False, "status": "REFUSED",
                        "error": f"{name} is a {target.get('width')}x"
                                 f"{target.get('height')} Multiview. This release "
                                 f"shows {MULTIVIEW_OUTPUT_RESOLUTION} Multiviews "
                                 f"only."}, 409

    plan, state, encoder_states, record, failure = _recall_plan(ip, name, state)
    if failure:
        message, status = failure
        return {"ok": False, "error": message}, status
    if not plan.get("ok") or plan.get("conflicts"):
        # A source that did not answer is not an invalid request, and the
        # operator needs to be able to tell the two apart.
        return _plan_refusal(plan, "show")

    required = {(w.get("ip_input") or {}).get("ip_input")
                for w in plan["windows"] if w.get("ip_input")}
    release, kept_pairs = _releasable_pool_inputs(ip, device, state, required)
    kept = [{"ip_input": n, "reason": r} for n, r in kept_pairs]
    audio = _show_audio_plan_from_plan(ip, plan, state)

    with _multiview_apply_lock:
        snapshot = _snapshot_fields(
            list(plan.get("snapshot") or [])
            + [{"device": ip, "node": "hdmi_output", "name": "hdmi_output1", "field": "*"}]
            + [{"device": ip, "node": "ip_input", "name": n, "field": "*"}
               for n in sorted(set(release) | ({audio["ip_input"]}
                                               if audio.get("write_ip_input") else set()))])
        applied, steps = [], []

        mutations = list(plan.get("activation") or [])
        rebind = _rebind_subframes(ip, name, plan, state)
        if rebind:
            mutations.append(rebind)
        for name_to_release in release:
            mutations.append({
                "stage": omni_multiview.STAGE_IP_INPUT_DISABLE, "device": ip,
                "node": "ip_input", "target": name_to_release,
                "description": "Release %s, which this Multiview does not use"
                               % name_to_release,
                "config": {"name": name_to_release, "enabled": False}})

        current_resolution = ((hdmi.get("video") or {}).get("output") or {}).get("resolution")
        if current_resolution != MULTIVIEW_OUTPUT_RESOLUTION:
            mutations.append({
                "stage": STAGE_OUTPUT_RESOLUTION, "device": ip, "node": "hdmi_output",
                "target": "hdmi_output1",
                "description": "Set the display output to %s" % MULTIVIEW_OUTPUT_RESOLUTION,
                "config": {"name": "hdmi_output1",
                           "video": {"output": {"resolution": MULTIVIEW_OUTPUT_RESOLUTION}}}})
        if (hdmi.get("sap_input") or {}).get("enabled"):
            mutations.append({
                "stage": STAGE_SAP_SHOW, "device": ip, "node": "hdmi_output",
                "target": "hdmi_output1",
                "description": "Turn off automatic source selection (SAP)",
                "config": {"name": "hdmi_output1", "sap_input": {"enabled": False}}})
        if audio.get("write_ip_input"):
            mutations.append({
                "stage": STAGE_AUDIO_INPUT, "device": ip, "node": "ip_input",
                "target": audio["ip_input"],
                "description": "Point %s at %s:%s for the main window's audio"
                               % (audio["ip_input"], audio["address"], audio["port"]),
                "config": {"name": audio["ip_input"], "enabled": True,
                           "port": audio["port"],
                           "multicast": {"address": audio["address"]}}})
        if str((hdmi.get("video") or {}).get("input") or "") != name:
            mutations.append({
                "stage": STAGE_SHOW, "device": ip, "node": "hdmi_output",
                "target": "hdmi_output1",
                "description": f"Show {name} on the display",
                "config": {"name": "hdmi_output1", "video": {"input": name}}})
        if audio.get("select") and audio["ip_input"] != (hdmi.get("audio") or {}).get("input"):
            mutations.append({
                "stage": STAGE_AUDIO_SELECT, "device": ip, "node": "hdmi_output",
                "target": "hdmi_output1",
                "description": "Take the display's audio from %s (%s Session 1)"
                               % (audio["ip_input"], audio.get("source_hostname")),
                "config": {"name": "hdmi_output1", "audio": {"input": audio["ip_input"]}}})

        # Read what the devices hold, and write only what differs.
        #
        # Encoder configuration is shared: Encoder 1 may be feeding decoders
        # that have nothing to do with this Multiview, and writing a value a
        # device already holds is still a write it acts on. A source that is
        # already prepared correctly must cost zero writes.
        mutations, skipped, write_plan = _transaction_diff(mutations)
        # One exception to writing only differences: a window that was black
        # last time. Its fields already match, so the diff would skip it and the
        # operator's second attempt would do nothing at all. Take that input
        # down and bring it back instead.
        stuck = _previously_unlocked(ip, name)
        if stuck:
            mutations = _force_relock(plan, mutations, stuck)
            log.info("[MULTIVIEW] %s: re-establishing %s, which did not lock "
                     "last time", name, ", ".join(sorted(stuck)))
        already_shown = not mutations
        for mutation in mutations:
            if mutation.get("method") in ("add_multiview", "del_multiview_subframe"):
                ok, message = _mv_method(mutation["device"], mutation["method"],
                                         dict(mutation["config"]))
            else:
                ok, message = _mv_set(mutation["device"], mutation["node"],
                                      mutation["config"])
            good, detail = (False, message)
            if ok:
                applied.append(mutation)
                good, detail = _verify_mutation(mutation)
            steps.append({"step": mutation["description"], "stage": mutation["stage"],
                          "verified": good, "error": "" if good else detail})
            if not good:
                rollback = _restore_snapshot(snapshot, applied)
                complete = all(e["verified"] for e in rollback) if rollback else True
                log.warning("[MULTIVIEW] recall of %s failed on %s: %s", name, ip, detail)
                return {
                    "ok": False,
                    "status": "FAILED — ROLLED BACK" if complete
                              else "FAILED — ROLLBACK INCOMPLETE",
                    "plan": plan, "audio": audio, "steps": steps,
                    "released": release, "kept": kept, "rollback": rollback}, 200

        # Every write held. That is still not proof that a picture exists.
        status, settled = _await_input_status(ip)
        if not settled:
            diagnostics = _show_diagnostics(ip, name, audio)
            rollback = _restore_snapshot(snapshot, applied)
            complete = all(e["verified"] for e in rollback) if rollback else True
            log.warning("[MULTIVIEW] %s on %s reports no active video after a "
                        "fully verified recall", name, ip)
            return {
                "ok": False,
                "status": "NO ACTIVE VIDEO — ROLLED BACK" if complete
                          else "NO ACTIVE VIDEO — ROLLBACK INCOMPLETE",
                "error": "Every change was accepted and verified, but the decoder "
                         "reports no active video on this Multiview. The picture "
                         "is not reaching it.",
                "input_status": status, "diagnostics": diagnostics, "plan": plan,
                "audio": audio, "steps": steps, "rollback": rollback}, 200

    # The composite is live. Each window is a separate question, because one can
    # sit black while the others carry the picture.
    windows, unlocked = _await_window_lock(ip, name)
    # Remembered so the next attempt re-establishes this window instead of
    # concluding, correctly but uselessly, that there is nothing to write.
    _remember_unlocked(ip, name, unlocked)
    after = (_mv_config(ip, "hdmi_output") or [{}])[0]
    output = (after.get("video") or {}).get("output") or {}
    if unlocked:
        log.warning("[MULTIVIEW] %s is shown on %s but %d window(s) are not "
                    "locked: %s", name, ip, len(unlocked),
                    ", ".join(w["subframe"] for w in unlocked))
    else:
        log.info("[MULTIVIEW] %s is now shown on %s at %s (%d write(s) of %d "
                 "planned; %d of %d field(s) already correct)", name, ip,
                 output.get("resolution"), len(steps),
                 write_plan["planned_mutations"], write_plan["already_correct"],
                 write_plan["planned_fields"])
    # The A/V Matrix renders from the cache, so it has to be told that this
    # decoder's picture is now a composition rather than a routed source.
    _remember_decoder_display(ip, name, (after.get("audio") or {}).get("input"))
    return {"ok": True,
                    "status": "VERIFIED" if not unlocked
                              else "VERIFIED — WINDOW NOT LOCKED",
                    "windows": windows,
                    "unlocked_windows": unlocked,
                    "diagnostics": _show_diagnostics(ip, name, audio) if unlocked else None,
                    "steps": steps,
                    "already_shown": already_shown,
                    "plan": plan,
                    "released": release, "kept": kept,
                    "writes": dict(write_plan,
                                   writes_performed=len(steps)),
                    "output_resolution": output.get("resolution"),
                    # What the sink actually negotiated. It depends on the
                    # attached display's EDID, so it is reported but never used
                    # as a pass condition.
                    "negotiated": (output.get("status") or {}).get("resolution"),
                    "input_status": status,
                    "audio": audio,
                    "sap_enabled": (after.get("sap_input") or {}).get("enabled"),
                    "audio_input": (after.get("audio") or {}).get("input")}, 200


@app.route("/api/multiview/show", methods=["POST"])
def api_multiview_show():
    """Recall a saved Multiview onto this decoder's display."""
    payload = request.get_json(silent=True) or {}
    ip = str(payload.get("decoder") or payload.get("ip") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not ip or not name:
        return jsonify({"ok": False,
                        "error": "decoder ip and multiview name required"}), 400
    body, status = _show_multiview_on(ip, name)
    return jsonify(body), status



def _show_audio_plan_from_plan(ip, plan, state):
    """Where the display's audio comes from once this Multiview is shown.

    Multiview is video-only, so the audio follows the main window's source over
    that source's ordinary Session 1 path -- never a second stream invented for
    the purpose, and never Session 2.

    The main window is named by the plan, which recomputed it from the layout, so
    recalling a different Multiview moves the audio with it rather than leaving
    it on whatever was playing before.
    """
    plan_audio = plan.get("audio") or {}
    result = {"followed": False, "reason": plan_audio.get("reason", ""),
              "ip_input": "", "select": False, "write_ip_input": False,
              "main_window": plan_audio.get("main_window"),
              "source_ip": plan_audio.get("source_ip") or "",
              "source_hostname": plan_audio.get("source_hostname") or "",
              "session": "session1",
              "address": plan_audio.get("address") or "",
              "port": plan_audio.get("port")}
    if not plan_audio.get("available"):
        result["reason"] = result["reason"] or (
            "The main window has no usable source, so the display's audio is "
            "left as it is.")
        return result
    if not plan_audio.get("enabled"):
        result["reason"] = ("%s Session 1 audio is not being transmitted, so the "
                            "display's audio is left alone."
                            % (result["source_hostname"] or result["source_ip"]))
        return result

    inputs = state.get("ip_input") or []
    address, port = result["address"], result["port"]
    existing = next((str(e.get("name") or "") for e in inputs
                     if (e.get("multicast") or {}).get("address") == address
                     and str(e.get("port")) == str(port) and e.get("enabled")), "")
    if existing:
        result.update({"followed": True, "ip_input": existing, "select": True,
                       "write_ip_input": False})
        return result

    hdmi = state["hdmi_output"] or {}
    current = str((hdmi.get("audio") or {}).get("input") or "")
    referenced = {str(s.get("input") or "")
                  for o in (state.get("multiview") or [])
                  for s in (o.get("subframes") or [])}
    if not current.startswith("ip_input"):
        result["reason"] = ("The display's audio is on %s rather than an input "
                            "OmniSuite can repoint." % (current or "nothing"))
        return result
    if (current in omni_multiview.WINDOW_IP_INPUTS
            or current == str((hdmi.get("video") or {}).get("input") or "")
            or current == str((hdmi.get("aux") or {}).get("input") or "")
            or current in referenced):
        result["reason"] = ("%s is doing something else as well, so OmniSuite will "
                            "not repoint it for Multiview audio." % current)
        return result
    result.update({"followed": True, "ip_input": current, "select": True,
                   "write_ip_input": True})
    return result


# Windows last seen failing to lock, per decoder and Multiview. Small, in
# memory, and cleared the moment they come good: this is not state about the
# configuration, it is a note that the picture did not arrive.
_multiview_unlocked_lock = threading.RLock()
_MULTIVIEW_UNLOCKED = {}


def _remember_unlocked(ip, name, windows):
    key = (ip, name)
    with _multiview_unlocked_lock:
        if windows:
            _MULTIVIEW_UNLOCKED[key] = {str(w.get("ip_input") or "")
                                        for w in windows
                                        if w.get("ip_input")}
        else:
            _MULTIVIEW_UNLOCKED.pop(key, None)


def _previously_unlocked(ip, name):
    """Decoder inputs whose window was black the last time this was shown."""
    with _multiview_unlocked_lock:
        return set(_MULTIVIEW_UNLOCKED.get((ip, name)) or ())


def _force_relock(plan, needed, inputs):
    """Re-establish these decoder inputs instead of skipping them.

    The case this exists for is the one where NOTHING would otherwise be
    written: every field already holds the wanted value, the planner produces no
    mutation at all, and a window that is black stays black. So the pair is
    built from the plan's own windows -- which name the input, the stream and the
    port whether or not anything needed changing -- rather than from a mutation
    list that may be empty.
    """
    if not inputs:
        return needed
    out = list(needed)
    for window in (plan or {}).get("windows") or ():
        binding = window.get("ip_input") or {}
        target = binding.get("ip_input")
        if not target or target not in inputs:
            continue
        multicast = window.get("multicast") or {}
        address = binding.get("address") or multicast.get("address")
        port = binding.get("port") or multicast.get("port")
        if not address:
            continue
        device = (plan.get("decoder") or {}).get("ip")
        out = [entry for entry in out
               if not (entry.get("node") == "ip_input"
                       and entry.get("target") == target)]
        out.extend([
            {"stage": omni_multiview.STAGE_IP_INPUT, "device": device,
             "node": "ip_input", "target": target,
             "description": "Take %s down, because its window did not lock "
                            "last time" % target,
             "config": {"name": target, "enabled": False}},
            {"stage": omni_multiview.STAGE_IP_INPUT, "device": device,
             "node": "ip_input", "target": target,
             "description": "Point %s at %s:%s again" % (target, address, port),
             "config": {"name": target, "enabled": True, "port": port,
                        "multicast": {"address": address}}},
        ])
    return out


def _await_window_lock(ip, name, timeout=MULTIVIEW_WINDOW_SETTLE, interval=1.0):
    """Which of a Multiview's windows are actually showing their stream.

    The decoder's composite Input status can read active while one window sits
    black, because the other windows are carrying it. A window whose input is
    receiving packets but whose subframe never reports `video.input.active` is
    a black rectangle on someone's display, and nothing else in the transaction
    would notice.

    Reported rather than rolled back: the rest of the picture is live, and
    taking it away to punish one late window helps nobody. The operator is told
    which window, and the diagnostics carry the chain behind it.
    """
    deadline = time.time() + timeout
    windows = []
    while True:
        target = next((o for o in _mv_config(ip, "multiview")
                       if str(o.get("name") or "") == name), {})
        windows = [{
            "subframe": str(s.get("name") or ""),
            "ip_input": str(s.get("input") or ""),
            "active": bool(((s.get("video") or {}).get("input") or {}).get("active")),
        } for s in (target.get("subframes") or []) if s.get("input")]
        if all(w["active"] for w in windows):
            return windows, []
        if time.time() >= deadline:
            return windows, [w for w in windows if not w["active"]]
        time.sleep(interval)


def _await_input_status(ip, timeout=MULTIVIEW_INPUT_SETTLE, interval=0.5):
    """The decoder's HDMI Input status once it has had a moment to settle.

    A freshly selected input takes a beat to lock, so a single immediate read
    would report every successful show as a failure. This is a bounded settle
    wait on one operation, not a poll: nothing calls it unless a Multiview has
    just been shown.
    """
    deadline = time.time() + timeout
    status = {}
    while True:
        hdmi = (_mv_config(ip, "hdmi_output") or [{}])[0]
        video = (hdmi.get("video") or {}).get("status") or {}
        resolution = video.get("resolution") or {}
        status = {
            "active": bool(video.get("active")),
            "width": resolution.get("width"), "height": resolution.get("height"),
            "framerate": video.get("framerate"),
            "colorspace": video.get("colorspace"),
            "label": ("%sx%s" % (resolution.get("width"), resolution.get("height"))
                      if video.get("active") else "No active video"),
        }
        if status["active"]:
            return status, True
        if time.time() >= deadline:
            return status, False
        time.sleep(interval)


def _show_diagnostics(ip, name, audio):
    """The whole chain, captured while it is still broken.

    Called only when a fully verified show produced no picture, and before any
    rollback, so the state that explains the failure still exists.
    """
    hdmi = (_mv_config(ip, "hdmi_output") or [{}])[0]
    inputs = {str(i.get("name") or ""): i for i in _mv_config(ip, "ip_input")}
    multiview = next((o for o in _mv_config(ip, "multiview")
                      if str(o.get("name") or "") == name), {})
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    record = (_multiview_meta_for(device) or {}).get(name) or {}

    windows = []
    for subframe in multiview.get("subframes") or []:
        input_name = str(subframe.get("input") or "")
        entry = inputs.get(input_name) or {}
        stored = next((w for w in (record.get("windows") or [])
                       if w.get("label") == str(subframe.get("name") or "")), {})
        window = {
            "subframe": subframe.get("name"),
            "ip_input": input_name,
            "ip_input_enabled": entry.get("enabled"),
            "ip_input_address": (entry.get("multicast") or {}).get("address"),
            "ip_input_port": entry.get("port"),
            "packets": ((entry.get("status") or {}).get("packets")),
            "subframe_input_active": ((subframe.get("video") or {}).get("input") or {}).get("active"),
            "subframe_output_active": ((subframe.get("video") or {}).get("output") or {}).get("active"),
            "source_ip": stored.get("source_ip"),
        }
        if stored.get("source_ip"):
            _address, source = _encoder_state(stored["source_ip"])
            encoders = {str(e.get("name") or ""): e for e in (source.get("vc2") or [])}
            sessions = {str(s.get("name") or ""): s for s in (source.get("sessions") or [])}
            encoder2 = encoders.get("vc2_encoder2") or {}
            session2 = sessions.get("session2") or {}
            window["source"] = {
                "reachable": source.get("reachable"),
                "encoder1_input": (encoders.get("vc2_encoder1") or {}).get("input"),
                "encoder1_bitrate": (encoders.get("vc2_encoder1") or {}).get("bitrate"),
                "encoder2_input": encoder2.get("input"),
                "encoder2_bitrate": encoder2.get("bitrate"),
                "encoder2_scaler": encoder2.get("scaler"),
                "session2_encoder": (session2.get("video") or {}).get("encoder"),
                "session2_enabled": (((session2.get("video") or {}).get("stream") or {})
                                     .get("enabled")),
                "session2_address": (((session2.get("video") or {}).get("stream") or {})
                                     .get("destination_address")),
                "session2_port": (((session2.get("video") or {}).get("stream") or {})
                                  .get("destination_port")),
                "session2_sap": (session2.get("sap") or {}).get("enabled"),
                "hdmi_input_active": source.get("input_resolution") or "",
            }
        windows.append(window)

    return {
        "captured": time.time(),
        "decoder": {
            "ip": ip,
            "video_input": (hdmi.get("video") or {}).get("input"),
            "audio_input": (hdmi.get("audio") or {}).get("input"),
            "aux_input": (hdmi.get("aux") or {}).get("input"),
            "output_resolution": ((hdmi.get("video") or {}).get("output") or {}).get("resolution"),
            "input_status": (hdmi.get("video") or {}).get("status"),
            "output_status": (((hdmi.get("video") or {}).get("output") or {})
                              .get("status")),
            "sap_enabled": (hdmi.get("sap_input") or {}).get("enabled"),
            "sap_session": (hdmi.get("sap_input") or {}).get("session"),
            "video_wall": omni_multiview.video_wall_enabled(hdmi),
            "fast_switching": omni_multiview.fast_switching_enabled(hdmi),
            "multiview": {"name": multiview.get("name"),
                          "width": multiview.get("width"),
                          "height": multiview.get("height"),
                          "subframes": len(multiview.get("subframes") or [])},
            "pool": {n: {"enabled": (inputs.get(n) or {}).get("enabled"),
                         "address": ((inputs.get(n) or {}).get("multicast") or {}).get("address"),
                         "port": (inputs.get(n) or {}).get("port"),
                         "packets": ((inputs.get(n) or {}).get("status") or {}).get("packets")}
                     for n in omni_multiview.WINDOW_IP_INPUTS},
        },
        "audio": audio,
        "windows": windows,
    }


@app.route("/api/multiview/switch", methods=["POST"])
def api_multiview_switch():
    """Change one window of the Multiview that is on the display, now.

    An active Multiview is a switching surface, not a form. Dragging a source
    onto a live window is the operation, and it is expected to take effect --
    so this does the smallest verified transaction that gets there rather than
    tearing the Multiview down and rebuilding it:

        prepare that source's Encoder 2 and Session 2 if they need it
        point the window's decoder input at the stream, or move the window to
            the input that already carries it
        rebind just that subframe
        release a pool input nothing needs any more
        follow the audio if the window that changed owns it
        verify the window locks, and that the others still hold

    Every other window is left exactly as it is. A change that succeeds becomes
    the saved preset, because a display showing one thing while its own preset
    restores another is a trap; a change that fails is rolled back and the
    preset is left alone.
    """
    payload = request.get_json(silent=True) or {}
    ip = str(payload.get("decoder") or payload.get("ip") or "").strip()
    name = str(payload.get("name") or "").strip()
    cell = str(payload.get("cell") or "").strip()
    source_ip = str(payload.get("source") or "").strip()
    if not ip or not name or not cell:
        return jsonify({"ok": False,
                        "error": "decoder, multiview name and window required"}), 400

    state, error = _decoder_state(ip)
    if state is None:
        return jsonify({"ok": False, "error": error}), 502
    target = next((o for o in state["multiview"]
                   if str(o.get("name") or "") == name), None)
    if target is None:
        return jsonify({"ok": False, "error": f"{name} is not on this decoder"}), 404

    hdmi = state["hdmi_output"] or {}
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})

    # This endpoint exists for the Multiview that is on screen. An inactive one
    # is edited and saved, which is a different operation with a different
    # contract, and conflating them is what this phase set out to stop.
    if str((hdmi.get("video") or {}).get("input") or "") != name:
        return jsonify({"ok": False, "status": "NOT ACTIVE",
                        "error": f"{name} is not on the display. Change it and "
                                 f"save it, then show it."}), 409

    blocked = omni_multiview.interlocks(device.get("model"), hdmi)
    if blocked:
        return jsonify({"ok": False, "status": "REFUSED", "interlocks": blocked,
                        "error": " ".join(e["reason"] for e in blocked)}), 409

    assignments, record = _saved_assignments(device, name)
    if not record:
        return jsonify({"ok": False,
                        "error": f"OmniSuite has no record of {name}, so one of "
                                 f"its windows cannot be switched."}), 409
    if cell not in {w.get("cell") for w in (record.get("windows") or [])} \
            and cell not in assignments:
        layout = record.get("layout")
        cells = [c[0] for c in omni_multiview.LAYOUTS.get(layout, {}).get("cells", ())]
        if cell not in cells:
            return jsonify({"ok": False,
                            "error": f"{name} has no window called {cell}."}), 400

    wanted = dict(assignments)
    if source_ip:
        wanted[cell] = source_ip
    else:
        wanted.pop(cell, None)

    # Plan the whole Multiview as it would be after the change, so every rule --
    # the scaler conflict, the two-window limit, the budgets, the pool -- is
    # applied to the result rather than to the one window in isolation.
    encoder_states = _gather_encoder_states(wanted.values())
    after = omni_multiview.plan_multiview(
        {"layout": record.get("layout"), "canvas": omni_multiview.ACTIVE_CANVAS,
         "assignments": wanted, "name": record.get("friendly_name"),
         "object_name": name, "update_existing": True,
         "owned_inputs": _owned_pool_inputs(device, state)},
        state, encoder_states, _known_multiviews(exclude_decoder_ip=ip),
        managed=_managed_multiviews(
            device, {str(o.get("name") or "") for o in (state.get("multiview") or [])}),
        claimed_inputs=())
    after["decoder"] = {"ip": ip, "hostname": state.get("hostname") or ip,
                        "model": state.get("model") or ""}
    if not after.get("ok") or after.get("conflicts"):
        body, status = _plan_refusal(after, "switch")
        return jsonify(body), status

    changed = next((w for w in after["windows"] if w["cell"] == cell), None)
    if changed is None:
        return jsonify({"ok": False, "error": f"{cell} is not a window of this "
                                              f"layout."}), 400

    # Everything the new configuration needs that the devices do not already
    # hold, and nothing else.
    #
    # This used to keep only the changed source's mutations and the changed
    # window's decoder input, on the assumption that a switch touches one
    # window. It does not: taking a source off one window can un-share a stream,
    # and the re-plan then moves a *different* window to another pool input.
    # That input's mutation was dropped, so the subframe was rebound to an input
    # left disabled and pointing at the old stream, and the window went black --
    # measured on the bench, in the one switch of 36 that had to reshuffle.
    #
    # Filtering by "is this already true?" is still the smallest transaction,
    # because a window nobody disturbed already matches and produces no write,
    # and it does not depend on predicting which resources a re-plan will move.
    required = {(w.get("ip_input") or {}).get("ip_input")
                for w in after["windows"] if w.get("ip_input")}
    activation = list(after.get("activation") or [])
    rebind = _rebind_subframes(ip, name, after, state)
    release, kept_pairs = _releasable_pool_inputs(ip, device, state, required)
    kept = [{"ip_input": n, "reason": r} for n, r in kept_pairs]
    audio = _show_audio_plan_from_plan(ip, after, state)
    audio_moves = (audio.get("write_ip_input")
                   or (audio.get("select")
                       and audio["ip_input"] != (hdmi.get("audio") or {}).get("input")))

    before_windows = {str(s.get("name") or ""):
                      bool(((s.get("video") or {}).get("input") or {}).get("active"))
                      for s in (target.get("subframes") or [])}

    with _multiview_apply_lock:
        snapshot = _snapshot_fields(
            [e for e in (after.get("snapshot") or []) if e["device"] == source_ip]
            + [{"device": ip, "node": "multiview", "name": name, "field": "*"}]
            + [{"device": ip, "node": "ip_input", "name": n, "field": "*"}
               for n in sorted(set(release) | required
                               | ({audio["ip_input"]} if audio.get("write_ip_input")
                                  else set()))
               if n]
            + ([{"device": ip, "node": "hdmi_output", "name": "hdmi_output1",
                 "field": "*"}] if audio_moves else []))

        mutations = list(activation)
        if rebind:
            mutations.append(rebind)
        for target_input in release:
            mutations.append({
                "stage": omni_multiview.STAGE_IP_INPUT_DISABLE, "device": ip,
                "node": "ip_input", "target": target_input,
                "description": "Release %s, which this Multiview no longer uses"
                               % target_input,
                "config": {"name": target_input, "enabled": False}})
        if audio.get("write_ip_input"):
            mutations.append({
                "stage": STAGE_AUDIO_INPUT, "device": ip, "node": "ip_input",
                "target": audio["ip_input"],
                "description": "Point %s at %s:%s for the main window's audio"
                               % (audio["ip_input"], audio["address"], audio["port"]),
                "config": {"name": audio["ip_input"], "enabled": True,
                           "port": audio["port"],
                           "multicast": {"address": audio["address"]}}})
        if audio.get("select") and audio["ip_input"] != (hdmi.get("audio") or {}).get("input"):
            mutations.append({
                "stage": STAGE_AUDIO_SELECT, "device": ip, "node": "hdmi_output",
                "target": "hdmi_output1",
                "description": "Take the display's audio from %s (%s Session 1)"
                               % (audio["ip_input"], audio.get("source_hostname")),
                "config": {"name": "hdmi_output1",
                           "audio": {"input": audio["ip_input"]}}})

        order = {stage: index for index, stage
                 in enumerate(omni_multiview.STAGE_ORDER)}
        mutations.sort(key=lambda m: order.get(m["stage"], len(omni_multiview.STAGE_ORDER)))
        # The same rule as Show: a field the device already holds is not written.
        mutations, skipped, write_plan = _transaction_diff(mutations)

        applied, steps = [], []
        for mutation in mutations:
            if mutation.get("method") in ("add_multiview", "del_multiview_subframe"):
                ok, message = _mv_method(mutation["device"], mutation["method"],
                                         dict(mutation["config"]))
            else:
                ok, message = _mv_set(mutation["device"], mutation["node"],
                                      mutation["config"])
            good, detail = (False, message)
            if ok:
                applied.append(mutation)
                good, detail = _verify_mutation(mutation)
            steps.append({"step": mutation["description"], "stage": mutation["stage"],
                          "verified": good, "error": "" if good else detail})
            if not good:
                rollback = _restore_snapshot(snapshot, applied)
                complete = all(e["verified"] for e in rollback) if rollback else True
                log.warning("[MULTIVIEW] switching %s of %s on %s failed: %s",
                            cell, name, ip, detail)
                return jsonify({
                    "ok": False,
                    "status": "FAILED — ROLLED BACK" if complete
                              else "FAILED — ROLLBACK INCOMPLETE",
                    "error": detail, "cell": cell, "plan": after,
                    "steps": steps, "rollback": rollback,
                    # The preset is untouched, so the page can put the window
                    # back to what is actually on the decoder.
                    "restored_source": assignments.get(cell) or None}), 200

        windows, unlocked = _await_window_lock(ip, name)
        _remember_unlocked(ip, name, unlocked)

    # The window that changed has to have taken; the ones that did not change
    # have to still be showing what they were.
    now = {w["subframe"]: w["active"] for w in windows}
    regressions = [label for label, was in before_windows.items()
                   if was and not now.get(label, False)
                   and label != changed["label"]]

    # A switch that lit the window it was asked about but darkened another has
    # not succeeded. The operator changed one window and lost a different one,
    # which is worse than the switch simply not working, so it is undone and
    # reported rather than recorded as the new preset.
    failed_window = changed["label"] in [w["subframe"] for w in unlocked]
    if failed_window or regressions:
        diagnostics = _show_diagnostics(ip, name, audio)
        with _multiview_apply_lock:
            rollback = _restore_snapshot(snapshot, applied)
        complete = all(e["verified"] for e in rollback) if rollback else True
        if failed_window:
            log.warning("[MULTIVIEW] %s of %s on %s never locked", cell, name, ip)
            explanation = ("The window was configured and verified, but never "
                           "showed its stream. The change has been undone.")
        else:
            log.warning("[MULTIVIEW] switching %s of %s on %s darkened %s",
                        cell, name, ip, ", ".join(regressions))
            explanation = ("The window switched, but %s stopped showing video. "
                           "The change has been undone."
                           % ", ".join(regressions))
        return jsonify({
            "ok": False,
            "status": "NO ACTIVE VIDEO — ROLLED BACK" if complete
                      else "NO ACTIVE VIDEO — ROLLBACK INCOMPLETE",
            "error": explanation,
            "cell": cell, "plan": after, "steps": steps, "windows": windows,
            "regressed_windows": regressions,
            "diagnostics": diagnostics, "rollback": rollback,
            "restored_source": assignments.get(cell) or None}), 200

    # It took. The preset becomes what the display is actually doing.
    _record_multiview_meta(device, name, {
        "layout": after["layout"],
        "friendly_name": after["friendly_name"],
        "canvas": f"{after['canvas']['width']}x{after['canvas']['height']}",
        "requested_canvas": "%sx%s" % (after["requested_canvas"]["width"],
                                       after["requested_canvas"]["height"]),
        "updated": time.time(),
        "windows": [{
            "cell": w["cell"], "label": w["label"],
            "x": w["x"], "y": w["y"], "anchor": w["anchor"],
            "width": w["width"], "height": w["height"],
            "scaler_format": w["scaler_format"],
            "encoder_index": w["encoder_index"], "session": w["session"],
            "ip_input": (w.get("ip_input") or {}).get("ip_input"),
            "source_ip": (w.get("source") or {}).get("ip"),
            "source_hostname": (w.get("source") or {}).get("hostname"),
            "source_model": (w.get("source") or {}).get("model"),
            "multicast": (w.get("multicast") or {}).get("address"),
            "multicast_port": (w.get("multicast") or {}).get("port"),
            "is_main": w.get("is_main", False),
        } for w in after["windows"] if w.get("source")],
    })

    status, settled = _await_input_status(ip)
    log.info("[MULTIVIEW] %s of %s on %s is now %s (%d write(s) of %d planned; "
             "%d of %d field(s) already correct)",
             cell, name, ip, source_ip or "cleared", len(steps),
             write_plan["planned_mutations"], write_plan["already_correct"],
             write_plan["planned_fields"])
    return jsonify({
        "ok": True,
        "status": "VERIFIED" if (settled and not unlocked and not regressions)
                  else "VERIFIED — WINDOW NOT LOCKED",
        "cell": cell, "source": source_ip or None,
        "plan": after, "steps": steps,
        "windows": windows, "unlocked_windows": unlocked,
        "regressed_windows": regressions,
        "released": release, "kept": kept, "audio": audio,
        "writes": dict(write_plan, writes_performed=len(steps)),
        "input_status": status,
        "saved": True,
    })


def _remember_decoder_display(ip, video_input, audio_input=None):
    """Record what a decoder is now displaying, for the A/V Matrix to read.

    The matrix renders from the discovery cache. Without this, taking a decoder
    into or out of Multiview would be invisible there until the next scan, and
    the alternative -- polling the decoder so the matrix can notice -- is exactly
    what the cache exists to avoid.
    """
    try:
        units = _load_cache() or []
        for unit in units:
            if unit.get("ip") != ip:
                continue
            unit["video_input"] = video_input
            if audio_input is not None:
                unit["audio_input"] = audio_input
            _save_cache(units)
            if HAS_MATRIX and ip in omni_matrix_logic._decoders:
                omni_matrix_logic._decoders[ip]["video_input"] = video_input
                if audio_input is not None:
                    omni_matrix_logic._decoders[ip]["audio_input"] = audio_input
            return
    except Exception as exc:
        log.info("[MULTIVIEW] could not record the display state of %s: %s", ip, exc)


def _exit_active_multiview(ip, name, state, fallback=""):
    """Take the display off a Multiview and release what it was using.

    This is NOT deletion. The saved object stays exactly where it is and can be
    recalled later; what changes is that the decoder stops compositing it. The
    A/V Matrix needs this when an operator routes a conventional source to a
    decoder that happens to be showing a Multiview.

    Returns (ok, steps, released, error).
    """
    hdmi = state["hdmi_output"] or {}
    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    owned_before = _owned_pool_inputs(device, state)

    available = [i for i in ((hdmi.get("video") or {}).get("available_inputs") or [])
                 if i and i != name]
    if fallback and fallback not in available:
        return False, [], [], "%s is not an available video input" % fallback
    if not fallback:
        fallback = next((i for i in available if i.startswith("ip_input")),
                        available[0] if available else "")
    if not fallback:
        return False, [], [], (
            "The display is on this Multiview and the decoder offers no other "
            "video input to move it to.")

    steps = []
    ok, message = _mv_set(ip, "hdmi_output",
                          {"name": "hdmi_output1", "video": {"input": fallback}})
    good, detail = (False, message)
    if ok:
        good, detail = _verify_mutation({
            "device": ip, "node": "hdmi_output", "target": "hdmi_output1",
            "config": {"name": "hdmi_output1", "video": {"input": fallback}}})
    steps.append({"step": "Move the display off %s to %s" % (name, fallback),
                  "verified": good, "error": "" if good else detail})
    if not good:
        return False, steps, [], detail
    _remember_decoder_display(ip, fallback)

    # Release only what the configuration now on the display does not need,
    # and only inputs OmniSuite put there -- established before the move, from
    # the object that proves it configured them.
    after, _error = _decoder_state(ip)
    released = []
    if after is not None:
        release, _kept = _releasable_pool_inputs(ip, device, after, set(),
                                                 owned=owned_before)
        for target in release:
            ok, message = _mv_set(ip, "ip_input",
                                  {"name": target, "enabled": False})
            verified = False
            if ok:
                verified, message = _verify_mutation({
                    "device": ip, "node": "ip_input", "target": target,
                    "config": {"name": target, "enabled": False}})
            steps.append({"step": "Release %s" % target, "verified": verified,
                          "error": "" if verified else message})
            if verified:
                released.append(target)
    return True, steps, released, ""


def _saved_definition(ip, name):
    """A saved Multiview as something that can be recreated elsewhere.

    Layout and the source in each window, and nothing about resources: no
    ip_input, no scaler size, no bitrate. Those belong to one decoder at one
    moment, and carrying them to another decoder would be carrying a guess.
    """
    state, error = _decoder_state(ip)
    if state is None:
        return None, error
    target = next((o for o in (state.get("multiview") or ())
                   if str(o.get("name") or "") == name), None)
    if target is None:
        return None, "%s has no Multiview called %s" % (ip, name)

    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    # _saved_assignments returns (assignments, record); the record is what the
    # subframe view and the layout reconciliation both read.
    _assignments, stored = _saved_assignments(device, name)
    subframes = target.get("subframes") or []
    reconciled = omni_multiview.reconcile_layout(
        stored, int(target.get("width") or 0), int(target.get("height") or 0),
        subframes)

    devices = _load_cache()
    assignments, windows = {}, []
    for subframe in subframes:
        view = _subframe_view(subframe, state, stored, devices)
        cell = view.get("cell")
        if not cell:
            continue
        source = view.get("source") or {}
        windows.append({"cell": cell,
                        "source_ip": source.get("ip") or "",
                        "source_hostname": source.get("hostname") or "",
                        "origin": view.get("origin")})
        if source.get("ip"):
            assignments[cell] = source["ip"]

    return {
        "name": name,
        "friendly_name": (stored or {}).get("friendly_name") or name,
        "width": target.get("width"),
        "height": target.get("height"),
        "layout": reconciled.get("layout"),
        "layout_label": (omni_multiview.LAYOUTS[reconciled["layout"]]["label"]
                         if reconciled.get("layout") else "Custom / Unknown"),
        "canvas": "%sx%s" % (target.get("width"), target.get("height")),
        "assignments": assignments,
        "windows": windows,
    }, ""


def _only_unreachable_sources(plan):
    """True when a plan's only complaint is that a source did not answer. (§7)

    A saved Multiview names sources; it does not hold them. Copying one to
    another decoder is paperwork, and refusing it because an encoder is off this
    afternoon would throw away an assignment the operator made deliberately.
    Returns (lenient, notes) -- `notes` is what to tell them instead.
    """
    errors = list(plan.get("errors") or ())
    if not errors:
        return True, []
    unreachable = {}
    for window in (plan.get("windows") or ()):
        source = window.get("source") or {}
        if source.get("ip") and source.get("reachable") is False:
            unreachable[source.get("hostname") or source["ip"]] = window.get("cell")
    if not unreachable:
        return False, []
    notes = []
    for error in errors:
        owner = next((name for name in unreachable if name and name in error), None)
        if owner is None:
            return False, []             # something else is wrong; refuse
        notes.append("%s did not answer, so its window is saved but not "
                     "prepared. It is set up when the Multiview is shown."
                     % owner)
    return True, notes


def _copy_preflight(target_ip, definition):
    """Can this decoder hold this Multiview at all? (§7)

    Returns (problems, warnings). A problem refuses the copy; a warning is
    something the operator should know but which does not make the saved
    definition wrong.
    """
    problems, warnings = [], []
    devices = {d.get("ip"): d for d in _load_cache()}
    if target_ip not in devices:
        return ["%s is not a discovered device." % target_ip], []

    state, error = _decoder_state(target_ip)
    if state is None:
        return ["%s did not answer: %s" % (target_ip, error)], []

    hostname = state.get("hostname") or target_ip
    if state.get("multiview") is None:
        return ["%s does not support Multiview." % hostname], []

    supported, reason, _cached = _multiview_capability(target_ip)
    if supported is False:
        problems.append("%s cannot run Multiview: %s"
                        % (hostname, reason or "unsupported"))

    # Video Wall and Fast Switching, in the operator's words, from the one
    # place that decides it.
    for blocked in omni_multiview.interlocks(state.get("model") or "",
                                             state.get("hdmi_output") or {}):
        problems.append("%s: %s" % (hostname, blocked["reason"]))

    layout = (definition or {}).get("layout")
    if not layout:
        problems.append("%s does not match a known layout, so it cannot be "
                        "recreated on another decoder."
                        % (definition or {}).get("name"))
    elif layout not in omni_multiview.LAYOUTS:
        problems.append("Layout %s is not available in this release." % layout)

    # The composited canvas is not the display resolution: a 2x2 composites at
    # 1920x1088 and is shown on a 1920x1080 output. The question is whether this
    # release can drive it, which is the same question Show already answers.
    verdict = _showable({"width": (definition or {}).get("width"),
                         "height": (definition or {}).get("height")})
    if not verdict["showable"]:
        problems.append(verdict["not_showable_reason"])

    # A source that is offline today is still the right source to have saved,
    # so this is a warning and the window assignment is kept.
    for window in (definition or {}).get("windows") or ():
        source_ip = window.get("source_ip")
        if not source_ip:
            continue
        if source_ip not in devices:
            warnings.append("%s is not currently discovered. The window is kept, "
                            "and prepared when the Multiview is shown." % source_ip)
        elif not _tcp_probe(source_ip, (80,), timeout=0.4):
            warnings.append("%s is not answering. The window is kept, and "
                            "prepared when the Multiview is shown."
                            % (devices[source_ip].get("hostname") or source_ip))
    return problems, warnings


@app.route("/api/multiview/groups/plan", methods=["POST"])
def api_multiview_groups_plan():
    """What a group operation would do, with no mutation whatsoever. (§12)

    This is the conflict check on its own, so the page can show the operator
    why a group cannot be synchronised before they ask for it to be.
    """
    payload = request.get_json(silent=True) or {}
    group = _find_group(payload.get("group"))
    if group is None:
        return jsonify({"ok": False, "error": "No such group."}), 404
    definition, error = _saved_definition(
        str(payload.get("source_decoder") or "").strip(),
        str(payload.get("name") or "").strip())
    if definition is None:
        return jsonify({"ok": False, "error": error}), 404

    members, _problems = _group_members_state(group)
    _plans, report = _plan_group(group, definition, members)
    report["group"] = {"id": group.get("id"), "name": group.get("name")}
    report["multiview"] = definition["name"]
    status = 200 if report["ok"] else 409
    return jsonify(report), status


@app.route("/api/multiview/groups/copy", methods=["POST"])
def api_multiview_groups_copy():
    """Save one Multiview definition onto every member. (§10)

    No display changes. This is the operation an operator runs while the room
    is in use.
    """
    payload = request.get_json(silent=True) or {}
    group = _find_group(payload.get("group"))
    if group is None:
        return jsonify({"ok": False, "error": "No such group."}), 404
    source_ip = str(payload.get("source_decoder") or "").strip()
    name = str(payload.get("name") or "").strip()
    definition, error = _saved_definition(source_ip, name)
    if definition is None:
        return jsonify({"ok": False, "error": error}), 404

    with _multiview_group_lock:
        members, _problems = _group_members_state(group)
        plans, report = _plan_group(group, definition, members)
        if not report["ok"]:
            report["group"] = {"id": group.get("id"), "name": group.get("name")}
            report["status"] = "REFUSED"
            return jsonify(report), 409

        saved, failures = [], []
        for member, plan in plans:
            body, _status = _apply_saved_plan(plan, member["ip"])
            if body.get("ok"):
                saved.append({"decoder": member["ip"],
                              "hostname": member["hostname"],
                              "name": plan.get("object_name")})
            else:
                failures.append({"decoder": member["ip"],
                                 "hostname": member["hostname"],
                                 "error": body.get("error")
                                          or (body.get("failures") or [{}])[-1]
                                          .get("error") or body.get("status")})

    # Deliberately NOT recorded as the group's intended Multiview. That field is
    # what SYNCHRONIZED and DRIFTED are measured against, and a copy changes no
    # display -- treating it as intent would report every member as drifted for
    # not showing something nobody asked to be shown.
    ok = not failures
    log.info("[MULTIVIEW] group %s: %s saved to %d of %d member(s)",
             group.get("name"), definition["name"], len(saved), len(plans))
    return jsonify({
        "ok": ok,
        "status": "VERIFIED" if ok else "PARTIAL",
        "group": {"id": group.get("id"), "name": group.get("name")},
        "saved": saved, "failures": failures,
        "warnings": report.get("warnings") or [],
        "shown": False,
        "message": ("Saved on every decoder in the group. No display changed — "
                    "use Show on Group to put it on screen."
                    if ok else
                    "Saved on some of the group. Nothing was shown."),
    }), (200 if ok else 502)


def _member_display_snapshot(ip, state):
    """What this decoder is showing now, so a group failure can put it back."""
    hdmi = (state or {}).get("hdmi_output") or {}
    return {"ip": ip,
            "video_input": ((hdmi.get("video") or {}).get("input")) or "",
            "audio_input": ((hdmi.get("audio") or {}).get("input")) or ""}


def _restore_member_display(snapshot):
    """Put one member back on what it was showing. Verified."""
    wanted = snapshot.get("video_input")
    if not wanted:
        return False, "nothing was recorded for this decoder"
    config = {"name": "hdmi_output1", "video": {"input": wanted}}
    if snapshot.get("audio_input"):
        config["audio"] = {"input": snapshot["audio_input"]}
    ok, message = _mv_set(snapshot["ip"], "hdmi_output", config)
    if not ok:
        return False, message
    return _verify_mutation({"device": snapshot["ip"], "node": "hdmi_output",
                             "target": "hdmi_output1", "config": config})


@app.route("/api/multiview/groups/show", methods=["POST"])
def api_multiview_groups_show():
    """Put one Multiview on every screen in the group. (§10/§15)

    Plan the whole group, refuse the whole group, or apply it member by member
    and verify each one. A failure stops and puts back what was already changed.
    """
    payload = request.get_json(silent=True) or {}
    group = _find_group(payload.get("group"))
    if group is None:
        return jsonify({"ok": False, "error": "No such group."}), 404
    name = str(payload.get("name") or "").strip()
    source_ip = str(payload.get("source_decoder") or "").strip()
    definition, error = _saved_definition(source_ip, name) if source_ip else (
        None, "source_decoder is required")
    if definition is None:
        return jsonify({"ok": False, "error": error}), 404

    # A live change made in group context (§16): one window is replaced, and the
    # whole group is re-planned around it before anything is written. It is the
    # same operation as showing the group, because that is what it has to be --
    # the shared encoder has to be judged across every member either way.
    cell = str(payload.get("cell") or "").strip()
    replacement = str(payload.get("source") or "").strip()
    if cell:
        assignments = dict(definition["assignments"])
        if replacement:
            assignments[cell] = replacement
        else:
            assignments.pop(cell, None)
        if not assignments:
            return jsonify({"ok": False,
                            "error": "That would leave the Multiview with no "
                                     "sources."}), 400
        definition = dict(definition, assignments=assignments)

    with _multiview_group_lock:
        members, _problems = _group_members_state(group)
        plans, report = _plan_group(group, definition, members)
        if not report["ok"]:
            report["group"] = {"id": group.get("id"), "name": group.get("name")}
            report["status"] = "REFUSED"
            # Nothing has been written. That is the point of planning first.
            report["writes"] = 0
            return jsonify(report), 409

        # Everything each member was showing, before the first write.
        before = [_member_display_snapshot(m["ip"], m["state"]) for m, _p in plans]

        shown, failures, changed = [], [], []
        for member, plan in plans:
            body, _status = _apply_saved_plan(plan, member["ip"])
            if not body.get("ok"):
                failures.append({"decoder": member["ip"],
                                 "hostname": member["hostname"],
                                 "stage": "save",
                                 "error": body.get("status") or "save failed"})
                break
            result, _status = _show_multiview_on(member["ip"],
                                                 plan.get("object_name") or name)
            changed.append(member["ip"])
            if result.get("ok"):
                shown.append({"decoder": member["ip"],
                              "hostname": member["hostname"],
                              "name": plan.get("object_name") or name,
                              "status": result.get("status")})
            else:
                failures.append({"decoder": member["ip"],
                                 "hostname": member["hostname"],
                                 "stage": "show",
                                 "error": result.get("error")
                                          or result.get("status")})
                break

        rollback = []
        if failures:
            for snapshot in before:
                if snapshot["ip"] not in changed:
                    continue
                restored, detail = _restore_member_display(snapshot)
                rollback.append({"decoder": snapshot["ip"],
                                 "restored": bool(restored), "detail": detail})

    if failures:
        complete = all(entry["restored"] for entry in rollback) if rollback else True
        status = ("FAILED — GROUP ROLLED BACK" if complete
                  else "FAILED — GROUP ROLLBACK INCOMPLETE")
        log.warning("[MULTIVIEW] group %s show failed on %s: %s",
                    group.get("name"), failures[-1]["hostname"],
                    failures[-1]["error"])
        return jsonify({
            "ok": False, "status": status,
            "group": {"id": group.get("id"), "name": group.get("name")},
            "shown": shown, "failures": failures, "rollback": rollback,
            "message": ("The group was put back the way it was."
                        if complete else
                        "Some decoders could not be put back. Check them before "
                        "using the group again."),
        }), 200

    with _multiview_groups_lock:
        record = _MULTIVIEW_GROUPS.get(group["id"])
        if record is not None:
            record["multiview"] = {"name": definition["name"],
                                   "layout": definition["layout"],
                                   "assignments": definition["assignments"],
                                   "updated": time.time()}
    _save_multiview_groups()

    log.info("[MULTIVIEW] group %s is showing %s on %d decoder(s)",
             group.get("name"), definition["name"], len(shown))
    return jsonify({
        "ok": True, "status": "VERIFIED",
        "group": {"id": group.get("id"), "name": group.get("name")},
        "shown": shown,
        "warnings": report.get("warnings") or [],
        "shared_sources": report.get("shared_sources") or [],
        "message": "Every decoder in the group is showing %s." % definition["name"],
    })


@app.route("/api/multiview/groups/state", methods=["GET"])
def api_multiview_groups_state():
    """Is this group still synchronised? (§17)

    Read from the decoders when the operator asks, and never on a timer. A
    member that was routed away from the A/V Matrix, rebooted, or changed by
    somebody else is DRIFTED -- reported, not corrected, because OmniSuite does
    not know that the operator did not mean it.
    """
    group = _find_group(request.args.get("group"))
    if group is None:
        return jsonify({"ok": False, "error": "No such group."}), 404
    intended = (group.get("multiview") or {}).get("name")

    members, _problems = _group_members_state(group)
    rows, states = [], []
    for member in members:
        if not member.get("online"):
            state = "OFFLINE"
            showing = None
        else:
            hdmi = (member["state"] or {}).get("hdmi_output") or {}
            showing = omni_multiview.active_multiview_name(
                (hdmi.get("video") or {}).get("input"))
            if not intended:
                state = "UNKNOWN"
            elif showing == intended:
                state = "SYNCHRONIZED"
            else:
                state = "DRIFTED"
        states.append(state)
        rows.append({"ip": member["ip"], "hostname": member["hostname"],
                     "state": state, "showing": showing,
                     "expected": intended})

    if not rows:
        overall = "EMPTY"
    elif all(s == "SYNCHRONIZED" for s in states):
        overall = "SYNCHRONIZED"
    elif any(s == "DRIFTED" for s in states):
        overall = "DRIFTED"
    elif any(s == "OFFLINE" for s in states):
        overall = "OFFLINE"
    else:
        overall = "UNKNOWN"

    return jsonify({"ok": True, "group": {"id": group.get("id"),
                                          "name": group.get("name")},
                    "state": overall, "expected": intended, "members": rows})


# ---------------------------------------------------------------------------
# Decoder groups
# ---------------------------------------------------------------------------

@app.route("/api/multiview/groups", methods=["GET"])
def api_multiview_groups():
    """Every saved group. Reads nothing from any device."""
    devices = _load_cache()
    with _multiview_groups_lock:
        groups = [dict(g) for g in _MULTIVIEW_GROUPS.values()]
    groups.sort(key=lambda g: (g.get("name") or "").lower())
    return jsonify({"ok": True,
                    "groups": [_group_view(g, devices) for g in groups]})


@app.route("/api/multiview/groups/save", methods=["POST"])
def api_multiview_groups_save():
    """Create or update one group. Membership only -- no device is touched."""
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    group_id = str(payload.get("id") or "").strip()
    member_ips = [str(ip).strip() for ip in (payload.get("members") or []) if ip]

    if not name:
        return jsonify({"ok": False, "error": "A group needs a name."}), 400
    if len(name) > 64:
        return jsonify({"ok": False, "error": "That name is too long."}), 400

    devices = {d.get("ip"): d for d in _load_cache()}
    unknown = [ip for ip in member_ips if ip not in devices]
    if unknown:
        return jsonify({"ok": False,
                        "error": "Not discovered: %s" % ", ".join(unknown)}), 400

    # A name collision between groups is confusing rather than dangerous, but
    # two groups called "Sports Bar" help nobody.
    with _multiview_groups_lock:
        for existing_id, existing in _MULTIVIEW_GROUPS.items():
            if existing_id == group_id:
                continue
            if (existing.get("name") or "").strip().lower() == name.lower():
                return jsonify({"ok": False,
                                "error": "There is already a group called %s."
                                         % name}), 409

        if not group_id:
            group_id = uuid.uuid4().hex[:12]
        record = dict(_MULTIVIEW_GROUPS.get(group_id) or {})
        record.update({
            "id": group_id,
            "name": name,
            "members": [_group_member_record(devices[ip]) for ip in member_ips],
            "updated": time.time(),
        })
        _MULTIVIEW_GROUPS[group_id] = record
    _save_multiview_groups()
    log.info("[MULTIVIEW] group %s saved with %d member(s)", name, len(member_ips))
    return jsonify({"ok": True, "group": _group_view(record)})


@app.route("/api/multiview/groups/delete", methods=["POST"])
def api_multiview_groups_delete():
    """Forget a group. The decoders and their Multiviews are left alone."""
    payload = request.get_json(silent=True) or {}
    group_id = str(payload.get("id") or "").strip()
    with _multiview_groups_lock:
        removed = _MULTIVIEW_GROUPS.pop(group_id, None)
    if removed is None:
        return jsonify({"ok": False, "error": "No such group."}), 404
    _save_multiview_groups()
    log.info("[MULTIVIEW] group %s deleted; no decoder was changed",
             removed.get("name"))
    return jsonify({"ok": True, "deleted": removed.get("name"),
                    "message": "The group is gone. Its decoders and their saved "
                               "Multiviews are unchanged."})


def _group_members_state(group):
    """Read every member once. Returns (members, problems).

    `members` carries the decoder state each later stage needs, so the group is
    read once rather than once per check.
    """
    devices = _load_cache()
    members, problems = [], []
    for stored in group.get("members") or ():
        unit = _resolve_group_member(stored, devices)
        if unit is None:
            problems.append("%s is not currently discovered."
                            % (stored.get("hostname") or stored.get("ip")))
            members.append({"ip": stored.get("ip"),
                            "hostname": stored.get("hostname") or stored.get("ip"),
                            "state": None, "online": False})
            continue
        ip = unit.get("ip")
        state, error = _decoder_state(ip)
        hostname = (state or {}).get("hostname") or unit.get("hostname") or ip
        if state is None:
            problems.append("%s did not answer: %s" % (hostname, error))
        members.append({"ip": ip, "hostname": hostname, "state": state,
                        "online": state is not None, "unit": unit})
    return members, problems


def _scaler_conflicts(plans):
    """§12: one Encoder 2 cannot produce two sizes at once.

    `plans` is [(member, plan)]. Returns a list of operator-facing conflicts,
    each naming the source and every decoder and window that disagrees about it.
    """
    wanted = {}
    for member, plan in plans:
        for window in (plan.get("windows") or ()):
            source = window.get("source") or {}
            source_ip = source.get("ip")
            if not source_ip or not window.get("scaler_format"):
                continue
            wanted.setdefault(source_ip, {}).setdefault(
                window["scaler_format"], []).append({
                    "decoder": member["ip"],
                    "hostname": member["hostname"],
                    "cell": window.get("cell"),
                    "window": window.get("label") or window.get("cell"),
                })

    conflicts = []
    for source_ip, sizes in sorted(wanted.items()):
        if len(sizes) < 2:
            continue
        source_name = next(
            (w["source"].get("hostname") for _m, p in plans
             for w in (p.get("windows") or ())
             if (w.get("source") or {}).get("ip") == source_ip
             and (w.get("source") or {}).get("hostname")), source_ip)
        parts = []
        for size, users in sorted(sizes.items()):
            who = ", ".join("%s %s" % (u["hostname"], _pretty_cell(u["cell"]))
                            for u in users)
            parts.append("%s for %s" % (size, who))
        conflicts.append({
            "source_ip": source_ip,
            "source": source_name,
            "sizes": sorted(sizes),
            "detail": ("%s can only send one picture size at a time, and this "
                       "group asks it for %s. Give it the same window size on "
                       "every decoder in the group."
                       % (source_name, " and ".join(parts))),
            "windows": [u for users in sizes.values() for u in users],
        })
    return conflicts


def _pretty_cell(cell):
    return str(cell or "").replace("_", " ")


def _plan_group(group, definition, members=None):
    """Build one plan for every member, and judge the set. (§12)

    Returns (plans, report). `report["ok"]` is the answer to "may this group
    operation proceed", and nothing has been written either way.
    """
    members = members or []
    problems, warnings = [], []
    plans = []

    for member in members:
        if not member.get("online"):
            problems.append("%s is not available." % member["hostname"])
            continue
        member_problems, member_warnings = _copy_preflight(member["ip"], definition)
        problems.extend(member_problems)
        warnings.extend(member_warnings)
        if member_problems:
            continue
        built, failure = _build_plan({
            "decoder": member["ip"],
            "layout": definition["layout"],
            "canvas": omni_multiview.ACTIVE_CANVAS,
            "assignments": definition["assignments"],
            "name": definition.get("friendly_name") or definition["name"],
            "object_name": definition["name"],
            "update_existing": True,
        })
        if failure:
            body, _status = failure
            problems.append("%s: %s" % (member["hostname"],
                                        body.get("error") or "could not be planned"))
            continue
        plan, _state, _encoders = built
        if not plan.get("ok"):
            lenient, notes = _only_unreachable_sources(plan)
            if not lenient:
                problems.extend(
                    "%s: %s" % (member["hostname"], reason)
                    for reason in (plan.get("errors") or ["cannot be applied"]))
                continue
            warnings.extend("%s: %s" % (member["hostname"], note)
                            for note in notes)
        if plan.get("conflicts"):
            problems.extend("%s: %s" % (member["hostname"], reason)
                            for reason in plan["conflicts"])
            continue
        plans.append((member, plan))

    # The group-wide judgement: everything above was per decoder.
    conflicts = _scaler_conflicts(plans) if plans else []

    # Source bandwidth is charged once for a shared Encoder 2 stream (§13): the
    # decoders subscribe to the same multicast, so counting it per decoder would
    # refuse layouts that are perfectly affordable.
    source_load = {}
    for _member, plan in plans:
        for window in (plan.get("windows") or ()):
            source = window.get("source") or {}
            if source.get("ip") and window.get("bitrate") is not None:
                source_load[source["ip"]] = max(
                    source_load.get(source["ip"], 0), window["bitrate"])

    report = {
        "ok": not problems and not conflicts and bool(plans),
        "problems": problems,
        "warnings": warnings,
        "conflicts": conflicts,
        "members": [{"ip": m["ip"], "hostname": m["hostname"],
                     "online": m.get("online", False)} for m in members],
        "planned": [m["ip"] for m, _p in plans],
        "shared_sources": [
            {"source_ip": ip, "bitrate": load,
             "note": "one Encoder 2 stream, subscribed to by every decoder that "
                     "shows it"}
            for ip, load in sorted(source_load.items())],
    }
    if not plans and not problems:
        report["problems"] = ["This group has no decoders to configure."]
        report["ok"] = False
    return plans, report


@app.route("/api/multiview/copy", methods=["POST"])
def api_multiview_copy():
    """Copy a saved Multiview definition to another decoder. (§6)

    This saves; it does not show. The target display is untouched and so is the
    source decoder -- the operator gets a preset on the target that they can
    show whenever they choose.
    """
    payload = request.get_json(silent=True) or {}
    source_ip = str(payload.get("source_decoder") or "").strip()
    target_ip = str(payload.get("target_decoder") or "").strip()
    name = str(payload.get("name") or "").strip()
    on_conflict = str(payload.get("on_conflict") or "").strip().lower()

    if not source_ip or not target_ip or not name:
        return jsonify({"ok": False,
                        "error": "source_decoder, target_decoder and name are "
                                 "required"}), 400
    if source_ip == target_ip:
        return jsonify({"ok": False,
                        "error": "That is the decoder it is already on."}), 400

    definition, error = _saved_definition(source_ip, name)
    if definition is None:
        return jsonify({"ok": False, "error": error}), 404

    problems, warnings = _copy_preflight(target_ip, definition)
    if problems:
        return jsonify({"ok": False, "status": "INCOMPATIBLE",
                        "problems": problems, "warnings": warnings,
                        "error": problems[0]}), 409

    target_state, read_error = _decoder_state(target_ip)
    if target_state is None:
        return jsonify({"ok": False, "error": read_error}), 502
    existing = {str(o.get("name") or "")
                for o in (target_state.get("multiview") or ())}
    target_host = target_state.get("hostname") or target_ip

    # An existing Multiview of the same name is never silently replaced.
    object_name = name
    friendly = definition.get("friendly_name") or name
    update_existing = False
    if name in existing:
        if on_conflict == "replace":
            update_existing = True
        elif on_conflict == "rename":
            object_name = omni_multiview.device_object_name(friendly, existing)
            friendly = object_name
        else:
            return jsonify({
                "ok": False, "status": "NAME IN USE",
                "existing": sorted(existing),
                "suggested_name": omni_multiview.device_object_name(
                    friendly, existing),
                "error": "%s already has a Multiview called %s."
                         % (target_host, name),
            }), 409

    built, failure = _build_plan({
        "decoder": target_ip,
        "layout": definition["layout"],
        "canvas": omni_multiview.ACTIVE_CANVAS,
        "assignments": definition["assignments"],
        "name": friendly,
        "object_name": object_name,
        "update_existing": update_existing,
    })
    if failure:
        body, status = failure
        return jsonify(body), status
    plan, state, _encoders = built
    if not plan.get("ok"):
        lenient, notes = _only_unreachable_sources(plan)
        if not lenient:
            body, status = _plan_refusal(plan, "copy")
            body["warnings"] = warnings
            return jsonify(body), status
        warnings.extend(notes)
    if plan.get("conflicts"):
        body, status = _plan_refusal(plan, "copy")
        body["warnings"] = warnings
        return jsonify(body), status

    result, status = _apply_saved_plan(plan, target_ip)
    result["warnings"] = warnings
    result["copied_from"] = {"decoder": source_ip, "name": name}
    result["target"] = {"decoder": target_ip, "hostname": target_host,
                        "name": plan.get("object_name") or object_name}
    # Nothing was shown. Say so plainly: "copied" must not read as "applied".
    result["shown"] = False
    return jsonify(result), status


@app.route("/api/multiview/delete", methods=["POST"])
def api_multiview_delete():
    """Delete one saved Multiview, leaving every other one alone.

    A decoder holds many saved Multiviews and only one is active, so deleting an
    inactive one is purely a metadata-and-object removal: it must not disturb the
    display or release a single input, because the inputs belong to whatever is
    on screen, not to the object being removed.

    Deleting the *active* one moves the output off it first, verified, and only
    then releases the pool inputs -- and even then by asking what the
    configuration now on the output still requires, never by replaying what the
    deleted object happened to reference.

    `del_multiview` is the only deletion mechanism the device has; `config_set`
    omission and a `delete: true` flag are both accepted and silently ignored.
    """
    payload = request.get_json(silent=True) or {}
    ip = str(payload.get("decoder") or payload.get("ip") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not ip or not name:
        return jsonify({"ok": False, "error": "decoder ip and multiview name required"}), 400

    state, error = _decoder_state(ip)
    if state is None:
        return jsonify({"ok": False, "error": error}), 502
    if not any(str(o.get("name") or "") == name for o in state["multiview"]):
        return jsonify({"ok": False, "error": f"{name} is not on this decoder"}), 404

    hdmi = state["hdmi_output"] or {}
    was_active = str((hdmi.get("video") or {}).get("input") or "") == name
    # Established while the object still exists, because it is the object that
    # proves OmniSuite configured the inputs underneath it.
    owned_before = _owned_pool_inputs(
        {d.get("ip"): d for d in _load_cache()}.get(ip, {}), state)
    steps = []
    with _multiview_apply_lock:
        if was_active:
            # Never leave the output pointing at an object that is about to
            # stop existing.
            fallback = str(payload.get("fallback_input") or "").strip()
            available = [i for i in ((hdmi.get("video") or {}).get("available_inputs") or [])
                         if i and i != name]
            if fallback and fallback not in available:
                return jsonify({"ok": False,
                                "error": f"{fallback} is not an available video input"}), 400
            if not fallback:
                fallback = next((i for i in available if i.startswith("ip_input")),
                                available[0] if available else "")
            if not fallback:
                return jsonify({"ok": False, "error":
                                "The output is on this Multiview and the decoder offers "
                                "no other video input to fall back to."}), 409
            ok, message = _mv_set(ip, "hdmi_output",
                                  {"name": "hdmi_output1", "video": {"input": fallback}})
            good = False
            if ok:
                good, message = _verify_mutation(
                    {"device": ip, "node": "hdmi_output", "target": "hdmi_output1",
                     "config": {"name": "hdmi_output1", "video": {"input": fallback}}})
            steps.append({"step": f"Select {fallback} on the HDMI output",
                          "verified": good, "error": "" if good else message})
            if not good:
                return jsonify({"ok": False, "status": "FAILED",
                                "error": "Could not move the HDMI output off this "
                                         "Multiview, so it was not deleted.",
                                "steps": steps}), 200

        ok, message = _mv_method(ip, "del_multiview", {"name": name})
        remaining = [str(o.get("name") or "") for o in _mv_config(ip, "multiview")]
        gone = name not in remaining
        steps.append({"step": f"Delete {name}", "verified": ok and gone,
                      "error": "" if (ok and gone) else (message or "the object is still present")})
        if not (ok and gone):
            return jsonify({"ok": False, "status": "FAILED", "steps": steps,
                            "error": message or f"{name} is still present after deletion"}), 200

    device = {d.get("ip"): d for d in _load_cache()}.get(ip, {})
    _forget_multiview_meta(device, name)

    # Cleanup is decided from what is on the output NOW. Deleting an inactive
    # Multiview releases nothing: the enabled inputs belong to whatever is
    # displayed, and a saved object never reserved them in the first place.
    reclamation = {"reclaimed": [], "kept": [], "skipped": not was_active}
    if was_active:
        after = _decoder_state(ip)[0] or {}
        reclaimed, kept = _releasable_pool_inputs(ip, device, after, required=(),
                                                  owned=owned_before)
        done = []
        for target in reclaimed:
            ok, message = _mv_set(ip, "ip_input", {"name": target, "enabled": False})
            verified = False
            if ok:
                verified, message = _verify_mutation({
                    "device": ip, "node": "ip_input", "target": target,
                    "config": {"name": target, "enabled": False}})
            done.append({"ip_input": target, "verified": verified,
                         "error": "" if verified else message})
            if not verified:
                log.info("[MULTIVIEW] could not release %s on %s: %s", target, ip, message)
        reclamation = {"reclaimed": done,
                       "kept": [{"ip_input": n, "reason": r} for n, r in kept],
                       "skipped": False}
    log.info("[MULTIVIEW] %s deleted from %s (%s)", name, ip,
             "was active" if was_active else "was not active")
    # Shared encoder streams are deliberately left alone. Proving no other
    # decoder still needs one is not possible from here, and a conservative
    # leftover stream is cheaper than an outage somewhere else.
    return jsonify({"ok": True, "status": "VERIFIED", "steps": steps,
                    "was_active": was_active,
                    "remaining": remaining, "reclamation": reclamation})


# Restore Multiview metadata now that the helpers above are defined. The device
# stores geometry only, so the layout an operator chose lives here and nowhere
# else; without this a restart would show every Multiview as Custom.
_load_multiview_meta()
_load_multiview_groups()


if __name__ == "__main__":
    main()
