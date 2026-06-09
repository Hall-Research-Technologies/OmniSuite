#!/usr/bin/env python3
"""Fix poll to fetch license and extract mac address."""

import re

with open('OmniMatrix_upgrade_server_v7_6y.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the poll endpoint's try block
old_try = '''    url = _ws_url(ip, ws_port, ws_path)
    try:
        sysinfo = _ws_send_recv(url, {"id":"systeminfo-get","username":user,"password":pwd,"config_get":"systeminfo"}, timeout)
        cfg = (sysinfo or {}).get("config") or {}; board = cfg.get("board") or {}
        unit = {
            "ip": ip,
            "model": cfg.get("model") or "",
            "version": cfg.get("firmwareversion") or "",
            "hostname": cfg.get("hostname") or "",
            "serialnumber": board.get("serialnumber") or "",
            "mac": cfg.get("mac") or "",
            "type": cfg.get("type") or (cfg.get("role") if cfg.get("role") != "unknown" else ""),
        }'''

new_try = '''    url = _ws_url(ip, ws_port, ws_path)
    try:
        sysinfo = _ws_send_recv(url, {"id":"systeminfo-get","username":user,"password":pwd,"config_get":"systeminfo"}, timeout)
        cfg = (sysinfo or {}).get("config") or {}; board = cfg.get("board") or {}
        
        # Extract mac from license config (same as _probe_one does)
        mac = ""
        try:
            license = _ws_send_recv(url, {"id":"license-get","username":user,"password":pwd,"config_get":"license"}, timeout=min(timeout, 1.5))
            if license:
                lic_cfg = (license or {}).get("config") or {}
                mac_hex = (lic_cfg.get("device_id") or "").lower()
                mac_hex = re.sub(r"[^0-9a-f]","", mac_hex)
                mac = ":".join(mac_hex[i:i+2] for i in range(0,12,2)) if len(mac_hex)==12 else ""
        except Exception:
            pass
        
        device_type = cfg.get("type") or ""
        role = ("encoder" if "encoder" in device_type.lower() else ("decoder" if "decoder" in device_type.lower() else "unknown"))
        
        unit = {
            "ip": ip,
            "model": cfg.get("model") or "",
            "version": cfg.get("firmwareversion") or "",
            "hostname": cfg.get("hostname") or "",
            "serialnumber": board.get("serialnumber") or "",
            "mac": mac,
            "type": device_type,
            "role": role,
        }'''

content = content.replace(old_try, new_try)

with open('OmniMatrix_upgrade_server_v7_6y.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Fixed poll endpoint to fetch license and extract mac address")
