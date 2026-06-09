#!/usr/bin/env python3
"""Add config file persistence to OmniMatrix server."""

with open('OmniMatrix_upgrade_server_v7_6y.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add config file path after imports
config_setup = '''
# Config file for persistent settings
CONFIG_FILE = CWD / "config.json"

def _load_config():
    """Load configuration from file, fallback to env vars."""
    if CONFIG_FILE.exists():
        try:
            import json
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_config(cfg):
    """Save configuration to file."""
    try:
        import json
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
'''

# Find where to insert (after CWD definition)
insert_pos = content.find('CWD = Path(__file__).parent')
if insert_pos > 0:
    # Find end of that line
    line_end = content.find('\n', insert_pos)
    insert_pos = line_end + 1
    content = content[:insert_pos] + config_setup + '\n' + content[insert_pos:]

# Update app.config initialization to load from file
old_config_init = '''app.config.update({
    'USERNAME': os.getenv("OMNI_USER","admin"),
    'PASSWORD': os.getenv("OMNI_PASS","Atlona"),
    'WS_PORT': int(os.getenv("OMNI_WS_PORT","80")),
    'TIMEOUT': float(os.getenv("OMNI_TIMEOUT","4.5")),
    'UPLOAD_CONCURRENCY': int(os.getenv("OMNI_CONCURRENCY","6")),
    'WS_PATH': "/wsapp/"
})'''

new_config_init = '''cfg = _load_config()
app.config.update({
    'USERNAME': cfg.get('username') or os.getenv("OMNI_USER","admin"),
    'PASSWORD': cfg.get('password') or os.getenv("OMNI_PASS","Atlona"),
    'WS_PORT': cfg.get('ws_port') or int(os.getenv("OMNI_WS_PORT","80")),
    'TIMEOUT': cfg.get('timeout') or float(os.getenv("OMNI_TIMEOUT","4.5")),
    'UPLOAD_CONCURRENCY': cfg.get('concurrency') or int(os.getenv("OMNI_CONCURRENCY","6")),
    'WS_PATH': "/wsapp/"
})'''

content = content.replace(old_config_init, new_config_init)

# Update POST config handler to save to file
old_post = '''    data = request.get_json(silent=True) or {}
    app.config['USERNAME'] = data.get("username","admin")
    app.config['PASSWORD'] = data.get("password","Atlona")
    app.config['WS_PORT'] = int(data.get("ws_port",80))
    app.config['TIMEOUT'] = float(data.get("timeout",4.5))
    app.config['UPLOAD_CONCURRENCY'] = int(data.get("concurrency", 6))
    return jsonify({"ok": True})'''

new_post = '''    data = request.get_json(silent=True) or {}
    app.config['USERNAME'] = data.get("username","admin")
    app.config['PASSWORD'] = data.get("password","Atlona")
    app.config['WS_PORT'] = int(data.get("ws_port",80))
    app.config['TIMEOUT'] = float(data.get("timeout",4.5))
    app.config['UPLOAD_CONCURRENCY'] = int(data.get("concurrency", 6))
    # Save to file
    _save_config({
        "username": app.config['USERNAME'],
        "password": app.config['PASSWORD'],
        "ws_port": app.config['WS_PORT'],
        "timeout": app.config['TIMEOUT'],
        "concurrency": app.config['UPLOAD_CONCURRENCY']
    })
    return jsonify({"ok": True})'''

content = content.replace(old_post, new_post)

with open('OmniMatrix_upgrade_server_v7_6y.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Added persistent config.json file storage")
