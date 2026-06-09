#!/usr/bin/env python3
"""Fix poll endpoint to capture mac and type fields from device response."""

with open('OmniMatrix_upgrade_server_v7_6y.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the unit dict creation in poll endpoint
old_unit = '''        unit = {
            "ip": ip,
            "model": cfg.get("model") or "",
            "version": cfg.get("firmwareversion") or "",
            "hostname": cfg.get("hostname") or "",
            "serialnumber": board.get("serialnumber") or "",
        }'''

new_unit = '''        unit = {
            "ip": ip,
            "model": cfg.get("model") or "",
            "version": cfg.get("firmwareversion") or "",
            "hostname": cfg.get("hostname") or "",
            "serialnumber": board.get("serialnumber") or "",
            "mac": cfg.get("mac") or "",
            "type": cfg.get("type") or (cfg.get("role") if cfg.get("role") != "unknown" else ""),
        }'''

content = content.replace(old_unit, new_unit)

with open('OmniMatrix_upgrade_server_v7_6y.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Updated poll endpoint to capture mac and type fields")
