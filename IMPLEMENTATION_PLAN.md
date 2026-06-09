# OmniMatrix Upgrade Server - ProducerV2 UI Parity Implementation

## Summary of What's Needed

### 1. **Backend API Endpoints to Add**

#### Password Management
- `GET /api/config` - Returns current configuration (ws_port, concurrency, etc.)
- `GET /api/config?include_password=1` - Returns password (authenticated only)
- `POST /api/config` - Saves configuration with password hashing

#### File Management
- `GET /api/list_dir?path=...` - Server-side folder browser
- `POST /api/download_firmware?file=...` - Download firmware files

#### Device Control (Device-specific, may not apply to OmniMatrix)
- ❌ `POST /api/blink` - LED blink function (NOT APPLICABLE - OmniMatrix doesn't support)
- ❌ `GET /api/preview/mjpeg` - MJPEG preview stream (NOT APPLICABLE - No HDMI preview)
- ❌ `POST /api/image/push` - Push image/logo (NOT APPLICABLE - No image streaming)

---

## 2. **Frontend UI Components to Add**

### Configuration Modal
```
┌─────────────────────────────────────┐
│         Configuration               │
├─────────────────────────────────────┤
│ Password:        [••••••••] [👁️]   │
│ Password Hash:   [hash]      [Default]
│ WS PORT:         [80 ▼]             │
│ Concurrency:     [6 ▼]              │
│ Firmware path:   [path]      [Browse]
├─────────────────────────────────────┤
│                  [Cancel]  [Save]   │
└─────────────────────────────────────┘
```

### Folder Browser Modal
```
┌─────────────────────────────────────┐
│         Select Folder               │
├─────────────────────────────────────┤
│ [Up] [/path/to/folder]   [Select]   │
├─────────────────────────────────────┤
│ ├─ folder1                          │
│ ├─ folder2                          │
│ └─ folder3                          │
└─────────────────────────────────────┘
```

### Header Layout
```
┌────────────────────────────────────────────┐
│  [Title]                        [⚙️ Config] │
├────────────────────────────────────────────┤
│  [Scan] [Clear] [Export CSV] ... [Upload]  │
└────────────────────────────────────────────┘
```

---

## 3. **File Structure Changes**

### Current OmniMatrix Structure
```
upgradeManager/
├── OmniMatrix_upgrade_server_v7_6y.py
├── ui/
│   ├── index.html
│   ├── style.css (embed in HTML)
│   └── script.js (embed in HTML)
└── units_cache.json
```

### New Structure (ProducerV2-style)
```
upgradeManager/
├── OmniMatrix_upgrade_server_v7_6y.py
├── ui/
│   ├── index.html (main UI)
│   ├── firmware/  (↑ configurable via /api/config)
│   │   └── [.vpup2 files]
│   └── localdata/
│       └── config.json (persistent settings)
└── units_cache.json
```

---

## 4. **Configuration Persistence**

### ProducerV2 Approach
- Uses `/api/config` endpoint on backend
- Stores in `localdata/config.json` on server
- Frontend reads with password masked as bullets (••••••••)
- Show real password only when "👁️" clicked + re-fetch from `/api/config?include_password=1`

### Implementation
```python
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        include_password = request.args.get("include_password") in ("1", "true")
        return jsonify({
            "ws_port": app.config['WS_PORT'],
            "concurrency": app.config['UPLOAD_CONCURRENCY'],
            "firmware_path": "/path/to/firmware",
            "password": app.config['PASSWORD'] if include_password else None,
            "password_default": app.config['PASSWORD'] == "Atlona"
        })
    # POST saves to disk
```

---

## 5. **Missing Features (Device Protocol Specific)**

### ❌ **Blink Function**
- **ProducerV2**: `POST /api/blink` sends LED control to device
- **OmniMatrix**: Check if `systeminfo` response includes LED status
  - Current response shows `"leds": {"enabled": true}`
  - **STATUS**: Need to check if devices support blink/LED control via WebSocket
  - **If NO**: Skip this feature, document limitation

### ❌ **Preview URL**
- **ProducerV2**: MJPEG stream from device HDMI
- **OmniMatrix**: No HDMI video output (these are AV encoder/decoder cards)
- **STATUS**: Not applicable, skip this feature

### ✅ **Firmware Update Progress** (Already exists)
- Have upload + upgrade stages
- Can poll `/api/poll` for version changes

---

## 6. **Implementation Priority**

### Phase 1: Backend (Core)
1. Add `/api/config` endpoint (GET/POST)
2. Add `/api/list_dir` endpoint for folder browser
3. Password hashing/comparison logic
4. Config file persistence (`localdata/config.json`)

### Phase 2: UI (Layout & Modals)
1. Import ProducerV2 CSS variables (dark/light themes)
2. Build Configuration Modal
3. Build Folder Browser Modal
4. Update header layout (add gear icon)

### Phase 3: UI (Functionality)
1. Wire up Config modal to `/api/config`
2. Wire up Folder Browser to `/api/list_dir`
3. localStorage integration for UI state
4. Theme toggle (dark/light)

### Phase 4: Polish
1. Error handling/validation
2. Mobile responsiveness
3. Accessibility (aria labels, keyboard nav)
4. Documentation

---

## 7. **What to Copy from ProducerV2**

| Component | File | Usage |
|-----------|------|-------|
| CSS Variables | `index.html` (lines 10-11) | Dark/light theme |
| Modal Styles | `index.html` (lines 30-60) | `.modal`, `.modal-backdrop` |
| Button Styles | `index.html` | `.btn`, `.btn.small`, `.btn:hover` |
| Configuration Modal HTML | `index.html` (lines 200-350) | Full config UI |
| Configuration Logic | `index.html` (lines 351-480) | openCfg(), saveCfg() |
| Folder Browser Modal | `index.html` (lines 351-480) | Browser UI + logic |

---

## 8. **Database/Config File Format**

```json
{
  "ws_port": 80,
  "concurrency": 6,
  "firmware_path": "./ui/firmware",
  "password": "Atlona",
  "password_hash": "sha256:...",
  "updated_at": "2025-12-18T22:30:00Z"
}
```

---

## 9. **API Response Examples**

### GET /api/config
```json
{
  "ws_port": 80,
  "concurrency": 6,
  "firmware_path": "./ui/firmware",
  "password": null,
  "password_default": true,
  "updated_at": "2025-12-18T22:30:00Z"
}
```

### GET /api/list_dir?path=/home/user
```json
{
  "ok": true,
  "path": "/home/user",
  "entries": [
    {"name": "folder1", "is_dir": true},
    {"name": "folder2", "is_dir": true},
    {"name": "firmware.vpup2", "is_dir": false}
  ]
}
```

---

## 10. **Testing Checklist**

- [ ] Config modal opens/closes
- [ ] Password field shows/hides with eye button
- [ ] Password hash displays and matches
- [ ] Config saves to disk
- [ ] Config reloads on page refresh
- [ ] Firmware folder browser works
- [ ] Folder browser shows subdirectories
- [ ] Selected folder path populates text field
- [ ] Multiple config saves work
- [ ] Password change requires validation
- [ ] Dark/light theme toggle persists
- [ ] Mobile responsiveness

