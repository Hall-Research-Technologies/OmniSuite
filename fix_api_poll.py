#!/usr/bin/env python3
"""Fix api_poll to return ok:false when device is unreachable."""

with open('OmniMatrix_upgrade_server_v7_6y.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the unreachable device response
old_response = '''    if not _probe(ip):
        return jsonify({"ok": True, "aliveOnly": True})'''

new_response = '''    if not _probe(ip):
        return jsonify({"ok": False, "error": "Device unreachable"})'''

content = content.replace(old_response, new_response)

with open('OmniMatrix_upgrade_server_v7_6y.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Fixed api_poll to return ok:false for unreachable devices")
