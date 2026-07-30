#!/usr/bin/env python3
"""
Omni Matrix (WS-only) v4.6

What's new in v4.6
- Decoder polling now updates the page **in the background** (no full reload).
- /poll returns JSON; client JS updates matrix bubbles + decoder table cells live.
- Polling pauses briefly during a route click to avoid clobbering user actions.
- Kept all prior features (WS-only, ping pre-check, cumulative scan, Clear, creds box, legend half-size, etc.).
"""

import argparse
import html
import ipaddress
import json
import platform
import socket
import ssl
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, request, jsonify
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

try:
    from websocket import create_connection
except Exception:  # pragma: no cover
    create_connection = None

console = Console()

# -------------------- Helpers --------------------

def detect_local_ipv4() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return None

def default_cidr_24() -> str:
    ip = detect_local_ipv4() or "192.168.1.1"
    parts = ip.split(".")
    if len(parts) != 4:
        return "192.168.1.0/24"
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"

def parse_targets(arg: Optional[str]) -> List[str]:
    if not arg:
        arg = default_cidr_24()
    if arg.startswith("@"):
        with open(arg[1:], "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    if "/" in arg:
        net = ipaddress.ip_network(arg, strict=False)
        return [str(ip) for ip in net.hosts()]
    return [x.strip() for x in arg.replace(",", " ").split() if x.strip()]

def is_alive_ping(ip: str, timeout_ms: int) -> bool:
    sysname = platform.system().lower()
    try:
        if "windows" in sysname:
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            sec = max(1, int(round(timeout_ms / 1000.0)))
            cmd = ["ping", "-c", "1", "-W", str(sec), ip]
            startupinfo = None
            creationflags = 0
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        return res.returncode == 0
    except Exception:
        return False

def tcp_port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False

def fast_filter_live_hosts(
    ips: List[str],
    ping_timeout_ms: int,
    ws_port: int,
    tcp_timeout: float,
    threads: int,
    use_tcp_fallback: bool,
) -> List[str]:
    live: List[str] = []
    def check(ip: str) -> Optional[str]:
        if is_alive_ping(ip, ping_timeout_ms):
            return ip
        if use_tcp_fallback and tcp_port_open(ip, ws_port, tcp_timeout):
            return ip
        return None
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(check, ip): ip for ip in ips}
        for f in as_completed(futs):
            ok = f.result()
            if ok:
                live.append(ok)
    return live

def discover_encoder_session1(
    sessions: List[Dict[str, Any]]
) -> Tuple[Optional[str], Optional[int], Optional[str], Optional[int]]:
    if not sessions:
        return None, None, None, None
    s = next((x for x in sessions if (x.get("name") or "").lower() == "session1"), sessions[0])
    v = ((s.get("video") or {}).get("stream") or {})
    a = ((s.get("audio") or {}).get("stream") or {})
    return (v.get("destination_address"), v.get("destination_port"),
            a.get("destination_address"), a.get("destination_port"))

def need_update_ip_input(cur: Dict[str, Any], want_addr: str, want_port: int) -> bool:
    if not cur:
        return True
    cur_addr = ((cur.get("multicast") or {}).get("address")) or ""
    cur_port = cur.get("port")
    return (cur_addr != (want_addr or "")) or (cur_port != want_port)

def build_ip_input_entry(
    name: str, number: int, interface: str, mcast: str, port: int, enabled: bool = True
) -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "interface": interface,
        "multicast": {"address": mcast, "filter": {"addresses": [], "mode": "exclude"}, "tempAddress": ""},
        "name": name,
        "port": port,
        "status": {},
        "number": number,
    }

def format_mac(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = "".join(ch for ch in raw if ch.isalnum()).lower()
    if len(s) != 12:
        return None
    pairs = [s[i:i+2].upper() for i in range(0, 12, 2)]
    return ":".join(pairs)

# -------------------- WS RPC --------------------

class WSClient:
    def __init__(self, username: str, password: str, timeout: float, ws_port: int, ws_path: str, debug: bool = False):
        if create_connection is None:
            raise RuntimeError("Install websocket-client: pip install websocket-client")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.ws_port = ws_port
        self.ws_path = ws_path if ws_path.startswith("/") else f"/{ws_path}"
        self.debug = debug

    def _ws_url(self, ip: str) -> str:
        secure = self.ws_port in (443, 8443)
        scheme = "wss" if secure else "ws"
        netloc = ip if self.ws_port in (80, 443) else f"{ip}:{self.ws_port}"
        return f"{scheme}://{netloc}{self.ws_path}"

    def call(self, ip: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = {"id": payload.get("id", "x"), "username": self.username, "password": self.password, **payload}
        url = self._ws_url(ip)
        sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False} if url.startswith("wss://") else None
        ws = create_connection(url, timeout=self.timeout, enable_multithread=True, sslopt=sslopt)
        try:
            ws.send(json.dumps(body))
            raw = ws.recv()
        finally:
            ws.close()
        if self.debug:
            console.print(f"[green]WS OK[/green] {url}")
        try:
            return json.loads(raw)
        except Exception:
            if self.debug:
                console.print(f"[yellow]WS parse failed[/yellow] {url}: {raw}")
            return {}

    def config_get(self, ip: str, name: str) -> Any:
        r = self.call(ip, {"id": f"{name}-get", "config_get": name})
        return r.get("config")

    def config_set(self, ip: str, name: str, config: Any) -> Any:
        return self.call(ip, {"id": f"{name}-set", "config_set": {"name": name, "config": config}})

# -------------------- Discovery --------------------

def classify_device(ip: str, rpc: WSClient, hdmi_index: int, debug: bool = False) -> Tuple[str, Dict[str, Any]]:
    try:
        sysinfo = rpc.config_get(ip, "systeminfo") or {}
    except Exception as e:
        if debug:
            console.print(f"[yellow]{ip}: systeminfo get failed: {e}[/yellow]")
        return "unknown", {}
    dtype = (sysinfo.get("type") or "").strip().lower()
    board = sysinfo.get("board") or {}

    mac = None
    try:
        lic = rpc.config_get(ip, "license") or {}
        dev_id = lic.get("device_id") if isinstance(lic, dict) else (lic.get("config", {}) or {}).get("device_id")
        mac = format_mac(dev_id)
    except Exception:
        mac = None
    if not mac:
        mac = format_mac(sysinfo.get("mac") or sysinfo.get("MAC") or board.get("mac"))

    info: Dict[str, Any] = {
        "ip": ip,
        "mac": mac,
        "hostname": sysinfo.get("hostname"),
        "firmwareversion": sysinfo.get("firmwareversion"),
        "model": sysinfo.get("model"),
        "serial": board.get("serialnumber"),
        "name": sysinfo.get("name") or ip,
    }
    if dtype == "encoder":
        sessions_cfg = rpc.config_get(ip, "sessions") or []
        sessions = sessions_cfg if isinstance(sessions_cfg, list) else sessions_cfg.get("sessions", [])
        vaddr, vport, aaddr, aport = discover_encoder_session1(sessions)
        info.update({"role": "Encoder", "v_mcast": vaddr, "v_port": vport, "a_mcast": aaddr, "a_port": aport})
    elif dtype == "decoder":
        ip_cfg = rpc.config_get(ip, "ip_input") or []
        ip_list = ip_cfg if isinstance(ip_cfg, list) else ip_cfg.get("ip_input", [])
        ip1 = next((e for e in ip_list if e.get("name") == "ip_input1"), {})
        ip3 = next((e for e in ip_list if e.get("name") == "ip_input3"), {})
        info.update({
            "role": "Decoder",
            "ip1_addr": (ip1.get("multicast") or {}).get("address"),
            "ip1_port": ip1.get("port"),
            "ip3_addr": (ip3.get("multicast") or {}).get("address"),
            "ip3_port": ip3.get("port"),
        })
    else:
        info.update({"role": "Unknown"})
    return dtype, info

def scan_targets_ws_only(ips: List[str], rpc: WSClient, hdmi_index: int, threads: int, debug: bool):
    encoders: List[Dict[str, Any]] = []
    decoders: List[Dict[str, Any]] = []
    unknowns: List[str] = []

    def worker(ip: str):
        return classify_device(ip, rpc, hdmi_index, debug)

    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(worker, ip): ip for ip in ips}
        for f in as_completed(futs):
            try:
                k, d = f.result()
            except Exception as e:
                if debug:
                    console.print(f"[yellow]{futs[f]}: classify exception: {e}[/yellow]")
                k, d = "unknown", {}
            if not d:
                unknowns.append(futs[f])
            elif k == "encoder":
                encoders.append(d)
            elif k == "decoder":
                decoders.append(d)
            else:
                unknowns.append(futs[f])
    return encoders, decoders, unknowns

# -------------------- Rendering (CLI) --------------------

def render_encoder_streams(encoders: List[Dict[str, Any]]):
    t = Table(title="Encoder Streams (session1)")
    t.add_column("Encoder IP", no_wrap=True)
    t.add_column("Video (mcast:port)", no_wrap=True)
    t.add_column("Audio (mcast:port)", no_wrap=True)
    for e in encoders:
        t.add_row(e.get("ip", ""), f"{e.get('v_mcast')}:{e.get('v_port')}", f"{e.get('a_mcast')}:{e.get('a_port')}")
    console.print(t)

def render_matrix(encoders: List[Dict[str, Any]], decoders: List[Dict[str, Any]]):
    t = Table(title="Omni Video Matrix (Encoders → columns, Decoders → rows)")
    t.add_column("Decoders", no_wrap=False)
    for e in encoders:
        label = f"{e.get('hostname') or ''}\n{e.get('ip')}"
        t.add_column(label, justify="center", no_wrap=True)
    for d in decoders:
        row_label = f"{d.get('hostname') or ''}\n{d.get('ip')}"
        row = [row_label]
        for e in encoders:
            vm = (d.get("ip1_addr") == e.get("v_mcast")) and (d.get("ip1_port") == e.get("v_port"))
            am = (d.get("ip3_addr") == e.get("a_mcast")) and (d.get("ip3_port") == e.get("a_port"))
            row.append("AV" if (vm and am) else ("V" if vm else ("A" if am else ".")))
        t.add_row(*row)
    console.print(t)

def render_device_info(encoders: List[Dict[str, Any]], decoders: List[Dict[str, Any]]):
    t1 = Table(title="Encoders")
    t1.add_column("IP"); t1.add_column("MAC"); t1.add_column("Hostname"); t1.add_column("Model"); t1.add_column("Firmware"); t1.add_column("Serial")
    t1.add_column("Video"); t1.add_column("Audio")
    for e in encoders:
        t1.add_row(e.get("ip",""), e.get("mac","") or "", e.get("hostname","") or "",
                   e.get("model","") or "", e.get("firmwareversion","") or "", e.get("serial","") or "",
                   f"{e.get('v_mcast')}:{e.get('v_port')}", f"{e.get('a_mcast')}:{e.get('a_port')}")
    console.print(t1)

    t2 = Table(title="Decoders")
    t2.add_column("IP"); t2.add_column("MAC"); t2.add_column("Hostname"); t2.add_column("Model"); t2.add_column("Firmware"); t2.add_column("Serial")
    t2.add_column("ip_input1 (Video)"); t2.add_column("ip_input3 (Audio)")
    for d in decoders:
        t2.add_row(d.get("ip",""), d.get("mac","") or "", d.get("hostname","") or "",
                   d.get("model","") or "", d.get("firmwareversion","") or "", d.get("serial","") or "",
                   f"{d.get('ip1_addr')}:{d.get('ip1_port')}", f"{d.get('ip3_addr')}:{d.get('ip3_port')}")
    console.print(t2)

# -------------------- Routing --------------------

def set_decoder_av(
    rpc: WSClient,
    dec_ip: str,
    v_addr: Optional[str],
    v_port: Optional[int],
    a_addr: Optional[str],
    a_port: Optional[int],
    do_video: bool,
    do_audio: bool,
    video_input_name: str = "ip_input1",
    audio_input_name: str = "ip_input3",
    interface: str = "eth1",
) -> bool:
    ip_cfg = rpc.config_get(dec_ip, "ip_input") or []
    ip_list = ip_cfg if isinstance(ip_cfg, list) else ip_cfg.get("ip_input", [])
    lookup = {e.get("name"): e for e in ip_list}

    if do_video and v_addr and v_port:
        if need_update_ip_input(lookup.get(video_input_name), v_addr, v_port):
            entry_v = build_ip_input_entry(video_input_name, 1, interface, v_addr, int(v_port), True)
            rpc.config_set(dec_ip, "ip_input", [entry_v])

    if do_audio and a_addr and a_port:
        if need_update_ip_input(lookup.get(audio_input_name), a_addr, a_port):
            entry_a = build_ip_input_entry(audio_input_name, 3, interface, a_addr, int(a_port), True)
            rpc.config_set(dec_ip, "ip_input", [entry_a])

    verify = rpc.config_get(dec_ip, "ip_input") or []
    def has(name, addr, port):
        return any(
            e.get("name") == name and (e.get("multicast") or {}).get("address") == addr and e.get("port") == port
            for e in (verify if isinstance(verify, list) else verify.get("ip_input", []))
        )
    v_ok = (not do_video) or has(video_input_name, v_addr, v_port)
    a_ok = (not do_audio) or has(audio_input_name, a_addr, a_port)
    return v_ok and a_ok

# -------------------- Web UI --------------------

def make_app(initial_targets: List[str], rpc: WSClient, args):
    app = Flask(__name__)

    encoders_by_ip: Dict[str, Dict[str, Any]] = {}
    decoders_by_ip: Dict[str, Dict[str, Any]] = {}
    current_subnet: Dict[str, str] = {"value": default_cidr_24()}
    current_mode: Dict[str, str] = {"value": "follow"}  # follow | video | audio
    current_auth: Dict[str, str] = {"username": args.username, "password": args.password}
    polling: Dict[str, Any] = {"enabled": False, "interval": 3}

    if initial_targets:
        live0 = fast_filter_live_hosts(initial_targets, args.ping_timeout, args.ws_port, args.tcp_timeout, args.threads, args.tcp_fallback)
        enc0, dec0, _ = scan_targets_ws_only(live0, rpc, args.hdmi_index, args.threads, args.debug)
        for d in enc0: encoders_by_ip[d["ip"]] = d
        for d in dec0: decoders_by_ip[d["ip"]] = d

    def add_or_update(devs: List[Dict[str, Any]], is_encoder: bool):
        for d in devs:
            if not d.get("ip"): continue
            (encoders_by_ip if is_encoder else decoders_by_ip)[d["ip"]] = d

    def scan_and_add(target_expr: str) -> Tuple[int, int]:
        targets = parse_targets(target_expr)
        live = fast_filter_live_hosts(targets, args.ping_timeout, args.ws_port, args.tcp_timeout, args.threads, args.tcp_fallback)
        enc, dec, _ = scan_targets_ws_only(live, rpc, args.hdmi_index, args.threads, args.debug)
        add_or_update(enc, True); add_or_update(dec, False)
        return len(enc), len(dec)

    def bubble_class_name(d: Dict[str, Any], e: Dict[str, Any]) -> str:
        vm = (d.get("ip1_addr") == e.get("v_mcast")) and (d.get("ip1_port") == e.get("v_port"))
        am = (d.get("ip3_addr") == e.get("a_mcast")) and (d.get("ip3_port") == e.get("a_port"))
        if vm and am: return "dot-both"
        if vm: return "dot-video"
        if am: return "dot-audio"
        return "dot-none"

    def header_cell(e):
        ip = e.get("ip", "?"); host = e.get("hostname") or ""
        return ("<th class='enc-head'>"
                f"<div class='vertical'>"
                f"<a class='ip' href='http://{html.escape(ip)}' target='_blank' rel='noopener'>{html.escape(ip)}</a>"
                f"<span class='host'>{html.escape(host)}</span>"
                f"</div></th>")

    def row_header(d):
        ip = d.get("ip", "?"); host = d.get("hostname") or ""
        return (f"<div class='rowhdr'>"
                f"<a class='ip' href='http://{html.escape(ip)}' target='_blank' rel='noopener'>{html.escape(ip)}</a><br>"
                f"<span class='host'>{html.escape(host)}</span>"
                f"</div>")

    def encoder_rows():
        encs = sorted(encoders_by_ip.values(), key=lambda x: x.get("ip", ""))
        return "".join(
            f"<tr>"
            f"<td>{html.escape(e.get('ip',''))}</td>"
            f"<td>{html.escape(e.get('mac') or '')}</td>"
            f"<td>{html.escape(e.get('hostname') or '')}</td>"
            f"<td>{html.escape(e.get('model') or '')}</td>"
            f"<td>{html.escape(e.get('firmwareversion') or '')}</td>"
            f"<td>{html.escape(e.get('serial') or '')}</td>"
            f"<td>{html.escape(str(e.get('v_mcast')))}:{html.escape(str(e.get('v_port')))}</td>"
            f"<td>{html.escape(str(e.get('a_mcast')))}:{html.escape(str(e.get('a_port')))}</td>"
            f"</tr>" for e in encs)

    def decoder_rows():
        decs = sorted(decoders_by_ip.values(), key=lambda x: x.get("ip", ""))
        return "".join(
            f"<tr id='decrow-{html.escape(d.get('ip',''))}'>"
            f"<td id='dec-ip-{html.escape(d.get('ip',''))}'>{html.escape(d.get('ip',''))}</td>"
            f"<td>{html.escape(d.get('mac') or '')}</td>"
            f"<td>{html.escape(d.get('hostname') or '')}</td>"
            f"<td>{html.escape(d.get('model') or '')}</td>"
            f"<td>{html.escape(d.get('firmwareversion') or '')}</td>"
            f"<td>{html.escape(d.get('serial') or '')}</td>"
            f"<td id='dec-ip1-{html.escape(d.get('ip',''))}'>{html.escape(str(d.get('ip1_addr')))}:{html.escape(str(d.get('ip1_port')))}</td>"
            f"<td id='dec-ip3-{html.escape(d.get('ip',''))}'>{html.escape(str(d.get('ip3_addr')))}:{html.escape(str(d.get('ip3_port')))}</td>"
            f"</tr>" for d in decs)

    def legend_html():
        return ("""
        <div class="legend">
          <span class="lbl">Legend:</span>
          <span class="dotbtn dot-video"><span class="ring"></span><span class="inner"></span></span> Video
          <span class="dotbtn dot-audio"><span class="ring"></span><span class="inner"></span></span> Audio
          <span class="dotbtn dot-both"><span class="ring"></span><span class="inner"></span></span> A/V
        </div>
        """)

    def page(msg: str = "") -> str:
        encs = sorted(encoders_by_ip.values(), key=lambda x: x.get("ip", ""))
        decs = sorted(decoders_by_ip.values(), key=lambda x: x.get("ip", ""))
        headers = "".join(header_cell(e) for e in encs)
        rows = ""
        for d in decs:
            cells = ""
            for e in encs:
                klass = bubble_class_name(d, e)
                cells += ("<td class='cell'>"
                          "<form method='POST' action='/route' onsubmit='window.__pausePoll=Date.now()+3000'>"
                          f"<input type='hidden' name='dec_ip' value='{html.escape(d.get('ip',''))}' />"
                          f"<input type='hidden' name='enc_ip' value='{html.escape(e.get('ip',''))}' />"
                          f"<input type='hidden' name='mode' value='{html.escape(current_mode['value'])}' />"
                          f"<button class='dotbtn {klass}' data-dec='{html.escape(d.get('ip',''))}' data-enc='{html.escape(e.get('ip',''))}' title='Route {html.escape(d.get('ip',''))} → {html.escape(e.get('ip',''))}'>"
                          "<span class='ring'></span><span class='inner'></span>"
                          "</button>"
                          "</form>"
                          "</td>")
            rows += f"<tr><th class='row-head'>{row_header(d)}</th>{cells}</tr>"

        m = current_mode["value"]
        checked = lambda k: "checked" if m == k else ""
        poll_checked = "checked" if polling["enabled"] else ""
        poll_interval = int(polling["interval"]) if isinstance(polling["interval"], (int, float, str)) else 3

        # Preload encoders into a JS object for client-side recompute of bubble states
        enc_json = json.dumps([
            {"ip": e.get("ip"), "v_mcast": e.get("v_mcast"), "v_port": e.get("v_port"),
             "a_mcast": e.get("a_mcast"), "a_port": e.get("a_port")}
            for e in encs
        ])

        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Omni Matrix</title>
        <style>
            body{{font-family:system-ui,Arial;margin:20px}}
            table{{border-collapse:collapse;margin-top:10px;table-layout:auto; width:max-content}}
            th,td{{border:1px solid #777;padding:6px;vertical-align:middle}}
            .bar{{display:flex; gap:14px; align-items:flex-end; flex-wrap:wrap; margin-bottom:8px}}
            .bar form{{display:flex; gap:8px; align-items:flex-end}}
            .hdr{{display:flex; justify-content:flex-start; align-items:flex-end; gap:20px}}
            input[type=text], input[type=number]{{width:140px}}
            .status{{color:green}}
            .cell{{text-align:center; min-width:38px}}

            /* Dot buttons (matrix bubbles) */
            .dotbtn{{position:relative; width:20px; height:20px; background:none; border:none; cursor:pointer; padding:0}}
            .dotbtn .ring{{position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); width:14px; height:14px; border-radius:50%; border:2px solid #000; display:none}}
            .dotbtn .inner{{position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); width:8px; height:8px; border-radius:50%; background:#000; display:none}}
            .dot-video .inner{{display:block}}
            .dot-audio .ring{{display:block}}
            .dot-both .ring,.dot-both .inner{{display:block}}
            .dot-none .ring{{display:block; border-color:#bbb}}

            /* Encoder column headers (vertical) */
            .enc-head{{width:28px;min-width:28px;max-width:28px;padding:2px 2px 2px 4px}}
            .vertical{{writing-mode:vertical-rl; transform:rotate(180deg); white-space:nowrap; text-align:center; line-height:1}}
            .vertical .ip{{display:block; font-size:12px; font-weight:600; padding:2px 0 0 0; margin:0; text-decoration:none}}
            .vertical .ip:link, .vertical .ip:visited{{color:#2246c5}}
            .vertical .host{{display:block; font-size:10px; opacity:.85; padding:2px 0; margin:0}}

            /* Decoder row headers */
            .row-head .rowhdr .ip{{font-size:14px; font-weight:700; text-decoration:none}}
            .row-head .rowhdr .ip:link, .row-head .rowhdr .ip:visited{{color:#2246c5}}
            .row-head .rowhdr .host{{font-size:12px; opacity:.85}}

            fieldset{{border:1px dashed #999; padding:6px}}
            legend{{font-size:12px; color:#333}}

            /* Legend (half-size) */
            .legend{{margin:6px 0 0 0; display:flex; gap:10px; align-items:center; justify-content:flex-start; font-size:11px}}
            .legend .lbl{{font-weight:600; color:#333}}
            .legend .dotbtn{{width:12px; height:12px}}
            .legend .dotbtn .ring{{width:9px; height:9px; border:1.5px solid #000}}
            .legend .dotbtn .inner{{width:5px; height:5px}}

            .modebar{{display:flex; justify-content:flex-start; margin-top:6px; margin-bottom:2px}}
            .pollbar{{display:flex; gap:10px; align-items:center; margin:6px 0}}
        </style>
        </head><body>
        <div class="bar">
          <form method="POST" action="/auth">
            <div style="display:flex; flex-direction:column; gap:4px;">
              <label style="font-weight:600">Credentials</label>
              <div style="display:flex; gap:8px; align-items:center;">
                <label>Username:</label><input type="text" name="username" value="{html.escape(current_auth['username'])}" style="width:140px">
                <label>Password:</label><input type="password" name="password" value="{html.escape(current_auth['password'])}" style="width:140px">
                <button type="submit">Set</button>
              </div>
            </div>
          </form>

          <form method="POST" action="/scan">
            <label>Subnet/IPs</label>
            <input type="text" name="subnet" value="{html.escape(current_subnet['value'])}" />
            <button type="submit">Scan</button>
          </form>

          <form method="POST" action="/clear"><button>Clear</button></form>
        </div>

        <div class="hdr">
          <h3>Matrix</h3>
        </div>

        <div class="modebar">
          <form method="POST" action="/mode" id="modeForm">
            <fieldset>
              <legend>Routing Mode</legend>
              <label><input type="radio" name="mode" value="follow" {checked('follow')} onchange="this.form.submit()"> Audio follows Video</label>
              <label style="margin-left:10px"><input type="radio" name="mode" value="video" {checked('video')} onchange="this.form.submit()"> Video</label>
              <label style="margin-left:10px"><input type="radio" name="mode" value="audio" {checked('audio')} onchange="this.form.submit()"> Audio</label>
            </fieldset>
          </form>
        </div>

        {legend_html()}

        <form method="POST" action="/polling" class="pollbar">
          <label><input type="checkbox" name="enabled" value="1" {'checked' if polling['enabled'] else ''} onchange="document.getElementById('pollSubmit').click()"> Poll decoders</label>
          <label>Interval (sec): <input type="number" min="1" max="300" step="1" name="interval" value="{int(polling['interval']) if isinstance(polling['interval'], (int,float)) else 3}" style="width:70px"></label>
          <button id="pollSubmit" type="submit" style="display:none">Apply</button>
          <span style="font-size:12px; color:#555">(updates ip_input1/ip_input3 live)</span>
        </form>

        <script>
        // Encoder map for client-side recompute of bubble state
        window.__encoders = {enc_json};
        window.__pausePoll = 0;

        function klassFor(dec, enc) {{
            var vm = (dec.ip1_addr === enc.v_mcast) && (dec.ip1_port == enc.v_port);
            var am = (dec.ip3_addr === enc.a_mcast) && (dec.ip3_port == enc.a_port);
            if (vm && am) return 'dot-both';
            if (vm) return 'dot-video';
            if (am) return 'dot-audio';
            return 'dot-none';
        }}

        function updateMatrixAndTable(decoders) {{
            // Update decoder table cells and matrix bubbles
            for (var ip in decoders) {{
                var d = decoders[ip];
                var c1 = document.getElementById('dec-ip1-' + ip);
                var c3 = document.getElementById('dec-ip3-' + ip);
                if (c1) c1.textContent = (d.ip1_addr || '') + ':' + (d.ip1_port || '');
                if (c3) c3.textContent = (d.ip3_addr || '') + ':' + (d.ip3_port || '');

                // Matrix cells: buttons marked with data-dec=ip
                var btns = document.querySelectorAll('button.dotbtn[data-dec=\"' + ip + '\"]');
                btns.forEach(function(btn) {{
                    var encIp = btn.getAttribute('data-enc');
                    var enc = window.__encoders.find(function(e){{ return e.ip === encIp; }});
                    if (!enc) return;
                    var cls = 'dotbtn ' + klassFor(d, enc);
                    btn.className = cls;
                }});
            }}
        }}

        (function() {{
            var enabled = {str(polling["enabled"]).lower()};
            var intervalSec = {int(polling["interval"]) if isinstance(polling["interval"], (int,float)) else 3};
            if (enabled) {{
                setInterval(function() {{
                    if (Date.now() < window.__pausePoll) return; // pause shortly after route click
                    fetch('/poll', {{method: 'POST'}})
                      .then(function(r) {{ return r.json(); }})
                      .then(function(data) {{ updateMatrixAndTable(data.decoders || {{}}); }})
                      .catch(function(e) {{ console.log('poll error', e); }});
                }}, Math.max(1000, intervalSec * 1000));
            }}
        }})();
        </script>

        <p class="status">{msg}</p>

        <table><tr><th class="row-head">Decoders \\ Encoders</th>{headers}</tr>{rows}</table>

        <h3>Encoders</h3>
        <table>
            <tr><th>IP</th><th>MAC</th><th>Hostname</th><th>Model</th><th>Firmware</th><th>Serial</th><th>Video (mcast:port)</th><th>Audio (mcast:port)</th></tr>
            {encoder_rows()}
        </table>

        <h3>Decoders</h3>
        <table>
            <tr><th>IP</th><th>MAC</th><th>Hostname</th><th>Model</th><th>Firmware</th><th>Serial</th><th>ip_input1 (Video)</th><th>ip_input3 (Audio)</th></tr>
            {decoder_rows()}
        </table>
        </body></html>"""

    @app.route("/", methods=["GET"])
    def index():
        return page()

    @app.route("/auth", methods=["POST"])
    def auth():
        u = request.form.get("username", "").strip() or "admin"
        p = request.form.get("password", "").strip() or "Atlona"
        current_auth["username"] = u
        current_auth["password"] = p
        rpc.username = u
        rpc.password = p
        return page("Credentials updated.")

    @app.route("/mode", methods=["POST"])
    def set_mode():
        val = request.form.get("mode", "follow").strip().lower()
        if val not in ("follow","video","audio"):
            val = "follow"
        current_mode["value"] = val
        return page(f"Routing mode set to: {val}")

    @app.route("/polling", methods=["POST"])
    def set_polling():
        en = request.form.get("enabled")
        interval = request.form.get("interval", "3").strip()
        try:
            sec = max(1, min(300, int(float(interval))))
        except Exception:
            sec = 3
        polling["enabled"] = bool(en)
        polling["interval"] = sec
        return page(f"Polling {'enabled' if polling['enabled'] else 'disabled'} (every {sec}s).")

    @app.route("/poll", methods=["POST"])
    def poll():
        # Refresh decoder ip_input subscriptions and return JSON only
        changed = 0
        result: Dict[str, Dict[str, Any]] = {}
        for ip, d in list(decoders_by_ip.items()):
            try:
                ip_cfg = rpc.config_get(ip, "ip_input") or []
                ip_list = ip_cfg if isinstance(ip_cfg, list) else ip_cfg.get("ip_input", [])
                ip1 = next((e for e in ip_list if e.get("name") == "ip_input1"), {})
                ip3 = next((e for e in ip_list if e.get("name") == "ip_input3"), {})
                new_ip1_addr = (ip1.get("multicast") or {}).get("address")
                new_ip1_port = ip1.get("port")
                new_ip3_addr = (ip3.get("multicast") or {}).get("address")
                new_ip3_port = ip3.get("port")
                updated = False
                if (d.get("ip1_addr") != new_ip1_addr or d.get("ip1_port") != new_ip1_port):
                    d["ip1_addr"] = new_ip1_addr; d["ip1_port"] = new_ip1_port; updated = True
                if (d.get("ip3_addr") != new_ip3_addr or d.get("ip3_port") != new_ip3_port):
                    d["ip3_addr"] = new_ip3_addr; d["ip3_port"] = new_ip3_port; updated = True
                if updated:
                    decoders_by_ip[ip] = d
                    changed += 1
                result[ip] = {"ip1_addr": d.get("ip1_addr"), "ip1_port": d.get("ip1_port"),
                              "ip3_addr": d.get("ip3_addr"), "ip3_port": d.get("ip3_port")}
            except Exception as e:
                if args.debug:
                    console.print(f"[yellow]poll {ip}: {e}[/yellow]")
        return jsonify({"updated": changed, "decoders": result})

    @app.route("/scan", methods=["POST"])
    def scan():
        subnet = request.form.get("subnet", "").strip() or current_subnet["value"]
        current_subnet["value"] = subnet
        enc_n, dec_n = scan_and_add(subnet)
        return page(f"Scanned {subnet}: +{enc_n} encoders, +{dec_n} decoders (cumulative).")

    @app.route("/clear", methods=["POST"])
    def clear():
        encoders_by_ip.clear()
        decoders_by_ip.clear()
        return page("Cleared all devices.")

    @app.route("/route", methods=["POST"])
    def route():
        dec_ip = request.form.get("dec_ip")
        enc_ip = request.form.get("enc_ip")
        mode = request.form.get("mode", current_mode["value"]).strip().lower()
        enc = encoders_by_ip.get(enc_ip)
        msg = "Invalid selection"
        if enc and dec_ip and dec_ip in decoders_by_ip:
            do_video = (mode in ("follow","video"))
            do_audio = (mode in ("follow","audio"))
            ok = set_decoder_av(
                rpc, dec_ip,
                v_addr=enc.get("v_mcast"), v_port=int(enc.get("v_port") or 0),
                a_addr=enc.get("a_mcast"), a_port=int(enc.get("a_port") or 0),
                do_video=do_video, do_audio=do_audio,
                video_input_name=args.video_input_name, audio_input_name=args.audio_input_name,
                interface=args.interface,
            )
            # Refresh this decoder after routing for immediate UI correctness
            _, info = classify_device(dec_ip, rpc, args.hdmi_index, args.debug)
            if info: decoders_by_ip[dec_ip] = info
            msg = (f"Routed ({mode}) {dec_ip} → {enc_ip}") if ok else f"Failed routing {dec_ip}"
        return page(msg)

    return app

# -------------------- CLI --------------------

def main():
    ap = argparse.ArgumentParser(description="Omni Matrix (WS-only, web UI + background polling updates)")
    ap.add_argument("--targets", help="Optional: initial subnet/IPs/@file to pre-scan (defaults to host /24 when omitted).")
    ap.add_argument("-u", "--username", default="admin")
    ap.add_argument("-p", "--password", default="Atlona")
    ap.add_argument("--ws-port", type=int, default=80)
    ap.add_argument("--ws-path", default="/wsapp/")
    ap.add_argument("--timeout", type=float, default=4.0, help="WS timeout (sec)")
    ap.add_argument("--ping-timeout", type=int, default=600, help="ICMP ping timeout ms")
    ap.add_argument("--tcp-fallback", action="store_true", help="If ping fails, try TCP connect to WS port")
    ap.add_argument("--tcp-timeout", type=float, default=0.6, help="TCP timeout (sec)")
    ap.add_argument("--threads", type=int, default=128, help="Thread pool size")
    ap.add_argument("--hdmi-index", type=int, default=0)
    ap.add_argument("--video-input-name", default="ip_input1")
    ap.add_argument("--audio-input-name", default="ip_input3")
    ap.add_argument("--interface", default="eth1")
    ap.add_argument("--web", action="store_true", help="Start the web UI")
    ap.add_argument("--web-port", type=int, default=8088)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    global rpc
    rpc = WSClient(args.username, args.password, args.timeout, args.ws_port, args.ws_path, args.debug)

    if args.web:
        initial_targets = parse_targets(args.targets) if args.targets else []
        app = make_app(initial_targets, rpc, args)
        app.run(host="0.0.0.0", port=args.web_port, debug=False)
        return

    targets = parse_targets(args.targets)
    console.print(f"[cyan]Pinging {len(targets)} hosts...[/cyan]")
    live = fast_filter_live_hosts(targets, args.ping_timeout, args.ws_port, args.tcp_timeout, args.threads, args.tcp_fallback)
    if not live:
        console.print("[red]No live hosts responded to ping (or TCP fallback)[/red]")
        sys.exit(1)
    console.print(f"[cyan]{len(live)} alive → classifying over WebSocket...[/cyan]")
    enc, dec, _ = scan_targets_ws_only(live, rpc, args.hdmi_index, args.threads, args.debug)
    if not enc and not dec:
        console.print("[red]No encoders/decoders discovered[/red]")
        sys.exit(1)

    render_encoder_streams(enc)
    if not Confirm.ask("Do these encoder video/audio streams look correct?", default=True):
        console.print("[yellow]Aborted by user[/yellow]"); sys.exit(0)
    render_matrix(enc, dec)
    render_device_info(enc, dec)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted[/red]")
