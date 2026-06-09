# Poll a unit for status (used by /api/poll)
def poll_unit_status(ip: str) -> dict:
    """Return status info for a given unit IP (encoder or decoder)."""
    from omni_matrix import is_alive_ping, tcp_port_open
    import sys
    
    print(f"[POLL_STATUS] Checking {ip}...", file=sys.stderr)
    
    with _state_lock:
        unit = _encoders.get(ip) or _decoders.get(ip)
    
    print(f"[POLL_STATUS] {ip} found in _encoders/_decoders: {unit is not None}", file=sys.stderr)
    
    # If not found in internal dicts, try loading from cache (handles newly discovered units)
    if not unit:
        try:
            import json
            print(f"[POLL_STATUS] {ip} not in memory, checking cache file: {_cache_file}", file=sys.stderr)
            if _cache_file.exists():
                with open(_cache_file, "r", encoding="utf-8") as f:
                    cached_units = json.load(f)
                if isinstance(cached_units, list):
                    unit = next((u for u in cached_units if u.get("ip") == ip), None)
                    print(f"[POLL_STATUS] {ip} found in cache file: {unit is not None}", file=sys.stderr)
                    if unit:
                        print(f"[POLL_STATUS] {ip} unit from cache: role={unit.get('role')}, model={unit.get('model')}", file=sys.stderr)
            else:
                print(f"[POLL_STATUS] Cache file does not exist: {_cache_file}", file=sys.stderr)
        except Exception as e:
            print(f"[POLL_STATUS] {ip} error loading from cache: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    
    if not unit:
        print(f"[POLL_STATUS] {ip} not found anywhere - returning disconnected", file=sys.stderr)
        return {"status": "disconnected", "ip": ip}
    
    # Check if the unit is actually reachable (ping or TCP)
    ws_port = int(unit.get("ws_port") or 80)
    print(f"[POLL_STATUS] {ip} checking reachability on port {ws_port}...", file=sys.stderr)
    # Try ping first, fallback to TCP if needed
    alive = is_alive_ping(ip, 600) or tcp_port_open(ip, ws_port, 0.6)
    print(f"[POLL_STATUS] {ip} alive: {alive}", file=sys.stderr)
    if not alive:
        print(f"[POLL_STATUS] {ip} not reachable - returning disconnected", file=sys.stderr)
        return {"status": "disconnected", "ip": ip}
    
    result = {
        "status": "connected",
        "ip": ip,
        "role": unit.get("role", "unknown"),
        "hostname": unit.get("hostname") or unit.get("host"),
        "model": unit.get("model"),
        "fw": unit.get("firmwareversion") or unit.get("fw"),
        "serial": unit.get("serialnumber") or unit.get("serial"),
    }
    print(f"[POLL_STATUS] {ip} returning connected status: {result}", file=sys.stderr)
    return result

# omni_matrix_logic.py — adapter to your omni_matrix.py for the web UI
# Supports independent Audio/Video routing (inner/outer indicators).

import importlib.util as _ilu, pathlib as _pl, threading, sys as _sys, types as _types, re as _re
from typing import Any, Dict, Tuple, List
import json, os

# Minimal rich shim for pure-python runs
try:
    import rich  # type: ignore
except Exception:
    _rich = _types.ModuleType("rich")
    class _Console:
        def __init__(self, *a, **k): pass
        def print(self, *a, **k):
            def _s(x):
                if not isinstance(x,str): return str(x)
                return _re.sub(r"\[(?:/?)[^\]]+\]", "", x)
            print(" ".join(_s(v) for v in a))
    _console_mod = _types.ModuleType("rich.console"); _console_mod.Console = _Console
    class _Confirm:
        @staticmethod
        def ask(prompt:str, default:bool=True, **kw)->bool: return default
    _prompt_mod = _types.ModuleType("rich.prompt"); _prompt_mod.Confirm = _Confirm
    _rich.console = _console_mod; _rich.prompt = _prompt_mod
    _sys.modules["rich"] = _rich; _sys.modules["rich.console"] = _console_mod; _sys.modules["rich.prompt"] = _prompt_mod

_here = _pl.Path(__file__).resolve().parent
_user_py = _here / "omni_matrix.py"
if not _user_py.exists():
    raise RuntimeError("omni_matrix.py not found next to script")

_spec = _ilu.spec_from_file_location("user_omni", str(_user_py))
assert _spec and _spec.loader
user = _ilu.module_from_spec(_spec)  # type: ignore
_spec.loader.exec_module(user)       # type: ignore

_state_lock = threading.RLock()
_args: Dict[str, Any] = {}
_ws = None
_encoders: Dict[str, Dict[str, Any]] = {}
_decoders: Dict[str, Dict[str, Any]] = {}
_routes: Dict[str, Dict[str, bool]] = {}   # per-decoder: {enc_ip: True} for "selected", plus flags stored in dec state
_poll = {"enabled": False, "interval": 3}

# --- Cache file logic ---
_cache_file = _here / "units_cache.json"

def _ensure_ws():
    global _ws
    with _state_lock:
        if _ws is None:
            _ws = user.WSClient(
                username=_args.get("username", "admin"),
                password=_args.get("password", "Atlona"),
                timeout=float(_args.get("timeout", 4.0)),
                ws_port=int(_args.get("ws_port", 80)),
                ws_path=_args.get("ws_path", "/wsapp/"),
                debug=bool(_args.get("debug", False)),
            )

def _load_cache():
    import traceback
    if os.path.exists(_cache_file):
        try:
            with open(_cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _units = data
                print(f"[CACHE] Loaded {len(_units)} units from {_cache_file} (list format)")
            _encoders.clear()
            _decoders.clear()
            for u in _units:
                ip = u.get('ip')
                role = (u.get('role') or '').lower()
                if role == 'encoder' and ip:
                    _encoders[ip] = u
                elif role == 'decoder' and ip:
                    _decoders[ip] = u
        except Exception as e:
            print(f"[CACHE][ERROR] Failed to load cache: {e}")
            traceback.print_exc()

def _save_cache():
    try:
        existing = []
        if os.path.exists(_cache_file):
            try:
                with open(_cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    existing = data
            except Exception:
                existing = []

        by_ip = {u.get("ip"): u for u in existing if u.get("ip")}
        for u in list(_encoders.values()) + list(_decoders.values()):
            ip = u.get("ip")
            if not ip:
                continue
            merged = dict(by_ip.get(ip, {}))
            merged.update(u)
            by_ip[ip] = merged

        with open(_cache_file, "w", encoding="utf-8") as f:
            json.dump(list(by_ip.values()), f, indent=2)
    except Exception as e:
        print(f"[CACHE] Failed to save cache: {e}")

def configure(**kw):
    with _state_lock:
        _args.update(kw)
        _args.setdefault("username","admin")
        _args.setdefault("password","Atlona")
        _args.setdefault("ws_port",80)
        _args.setdefault("ws_path","/wsapp/")
        _args.setdefault("timeout",4.0)
        _args.setdefault("ping_timeout",600)
        _args.setdefault("tcp_timeout",0.6)
        _args.setdefault("threads",128)
        _args.setdefault("tcp_fallback",False)
        _args.setdefault("hdmi_index",0)
        _args.setdefault("interface","eth1")
        _args.setdefault("video_input_name","ip_input1")
        _args.setdefault("audio_input_name","ip_input3")
        _args.setdefault("debug",False)
        global _ws; _ws = None

def clear_state():
    global _ws
    with _state_lock:
        _ws = None
        _encoders.clear()
        _decoders.clear()
        _routes.clear()
        _poll.update({"enabled": False, "interval": 3})

def scan(targets_expr: str) -> Tuple[int,int]:
    _ensure_ws()
    ips: List[str] = user.parse_targets(targets_expr)
    live = user.fast_filter_live_hosts(
        ips, int(_args["ping_timeout"]), int(_args["ws_port"]), float(_args["tcp_timeout"]),
        int(_args["threads"]), bool(_args["tcp_fallback"])
    )
    enc, dec, _ = user.scan_targets_ws_only(live, _ws, int(_args["hdmi_index"]), int(_args["threads"]), bool(_args["debug"]))
    with _state_lock:
        for e in enc: _encoders[e["ip"]] = e
        for d in dec: _decoders[d["ip"]] = d
        # Establish current implied routes from device state
        for d in _decoders.values():
            pass  # explicit routes derived on list_state
    _save_cache()
    return (len(_encoders), len(_decoders))

def list_state() -> Dict[str, Any]:
    with _state_lock:
        # Build encoder/decoder tables
        enc = [{
            "ip": e.get("ip"), "mac": e.get("mac"), "host": e.get("hostname") or e.get("name"),
            "model": e.get("model"), "fw": e.get("firmwareversion"), "serial": e.get("serial"),
            "v_mcast": e.get("v_mcast"), "v_port": e.get("v_port"),
            "a_mcast": e.get("a_mcast"), "a_port": e.get("a_port"),
        } for e in _encoders.values()]
        dec = [{
            "ip": d.get("ip"), "mac": d.get("mac"), "host": d.get("hostname") or d.get("name"),
            "model": d.get("model"), "fw": d.get("firmwareversion"), "serial": d.get("serial"),
            "ip1_addr": d.get("ip1_addr"), "ip1_port": d.get("ip1_port"),
            "ip3_addr": d.get("ip3_addr"), "ip3_port": d.get("ip3_port"),
        } for d in _decoders.values()]

        # Compute "selected encoder per decoder" by matching video first (outer ring)
        routes: Dict[str,str] = {}
        for d in dec:
            for e in enc:
                vm = (d.get("ip1_addr")==e.get("v_mcast")) and (int(d.get("ip1_port") or 0)==int(e.get("v_port") or 0))
                if vm: routes[d["ip"]] = e["ip"]; break

        return {"encoders": enc, "decoders": dec, "routes": routes, "poll": dict(_poll)}

def set_route(decoder_ip: str, encoder_ip: str, mode: str = "av", decoder_user: str = None, decoder_pwd: str = None, encoder_user: str = None, encoder_pwd: str = None) -> bool:
    """Route encoder to decoder using their specific credentials.
    If credentials not provided, falls back to default _args configured values.
    Creates temporary WebSocket connections with device-specific passwords.
    """
    # Use device-specific credentials, or fall back to default
    d_user = decoder_user or _args.get("username", "admin")
    d_pwd = decoder_pwd or _args.get("password", "Atlona")
    e_user = encoder_user or _args.get("username", "admin")
    e_pwd = encoder_pwd or _args.get("password", "Atlona")
    
    print(f"[DEBUG set_route] Available encoders: {list(_encoders.keys())}")
    print(f"[DEBUG set_route] Available decoders: {list(_decoders.keys())}")
    print(f"[DEBUG set_route] Requested encoder: {encoder_ip}, decoder: {decoder_ip}")
    print(f"[DEBUG set_route] Using decoder creds: {d_user}/*****, encoder creds: {e_user}/*****")
    
    e = _encoders.get(encoder_ip); d = _decoders.get(decoder_ip)
    if not e or not d:
        print(f"[DEBUG set_route] Encoder or decoder not found. e: {e}, d: {d}")
        return False
    
    m = (mode or "av").lower()
    do_video = (m in ("av","video"))
    do_audio = (m in ("av","audio"))
    
    # Create WebSocket with decoder's specific credentials
    try:
        ws_dec = user.WSClient(
            username=d_user,
            password=d_pwd,
            timeout=float(_args.get("timeout", 4.0)),
            ws_port=int(_args.get("ws_port", 80)),
            ws_path=_args.get("ws_path", "/wsapp/"),
            debug=bool(_args.get("debug", False)),
        )
        print(f"[DEBUG set_route] Created WebSocket for decoder {decoder_ip} with user {d_user}")
    except Exception as ex:
        print(f"[DEBUG set_route] Failed to create decoder WebSocket: {ex}")
        return False
    
    # Set route on decoder with its credentials
    try:
        ok = user.set_decoder_av(
            ws_dec, decoder_ip,
            v_addr=e.get("v_mcast"), v_port=int(e.get("v_port") or 0),
            a_addr=e.get("a_mcast"), a_port=int(e.get("a_port") or 0),
            do_video=do_video, do_audio=do_audio,
            video_input_name=_args["video_input_name"], audio_input_name=_args["audio_input_name"],
            interface=_args["interface"],
        )
        print(f"[DEBUG set_route] set_decoder_av returned: {ok}")
    except Exception as ex:
        print(f"[DEBUG set_route] set_decoder_av failed: {ex}")
        import traceback
        traceback.print_exc()
        return False
    
    # Refresh decoder's details using its credentials
    try:
        _, info = user.classify_device(decoder_ip, ws_dec, int(_args["hdmi_index"]), bool(_args["debug"]))
        if info:
            _decoders[decoder_ip] = info
            print(f"[DEBUG set_route] Updated decoder {decoder_ip} info: {info}")
    except Exception as ex:
        print(f"[DEBUG set_route] classify_device failed: {ex}")
        pass
    
    # Save updated decoder state to cache for persistence across restarts
    _save_cache()
    return bool(ok)

def set_poll(enabled: bool, interval: int) -> None:
    with _state_lock:
        _poll["enabled"] = bool(enabled)
        _poll["interval"] = max(1, int(interval or 3))
