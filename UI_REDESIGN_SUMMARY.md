# OmniMatrix Upgrade Manager - ProducerV2 UI Redesign

## Overview
The OmniMatrix Upgrade Manager UI has been completely redesigned to match the ProducerV2 Device Manager aesthetic, maintaining all existing functionality while adding professional modals for configuration and folder browsing.

## UI Components Implemented

### 1. **Header & Theme Toggle**
- Dark/light theme toggle (saved to localStorage)
- Uses ProducerV2 CSS color variables:
  - Light mode: `--bg:#f7f8fb`, `--fg:#0d1117`
  - Dark mode: `--bg:#1d232a`, `--fg:#e9eef5`
- Accent color: `--accent:#1e90ff` (matches ProducerV2)
- Danger color: `--danger:#e5534b`

### 2. **Configuration Modal** ⚙️
Accessible via gear icon (top-right), includes:
- **Password Field** with show/hide toggle (👁️)
- **WS Port** selector (80 or 443)
- **Concurrency** dropdown (1-12 parallel uploads)
- **Firmware Path** with folder browser button (📁)
- Password state is shown as "Default" badge when using default credentials

### 3. **Folder Browser Modal** 📁
- Navigate directory structure via folder browser modal
- Click folder names to drill down
- "Up" button to go to parent directory
- "Select" button to confirm path choice
- Path displayed and editable in input field
- Read-only path display prevents accidental editing

### 4. **Main Panels**

#### Scan Network
- Adapter selector (auto-populated from `api/adapters`)
- Network range input (e.g., `192.168.100.1-254`)
- Scan button with live status indicator
- Clear Units button to reset cache
- Status display shows discovery count

#### Discovered Units Table
- Columns: IP, MAC, Hostname, Type, Model, Version, Actions
- Select button per device (populates "Test Device" field)
- Export/Download CSV buttons for device inventory
- Shows all cached units from previous scans

#### Firmware Update
- Dropdown to select `.vpup2` firmware files
- Refresh button to reload file list
- Concurrency control (1-12 parallel uploads)
- Upgrade button (requires firmware selection and unit selection)
- Status indicator during upload

#### Device Controls (Placeholder UI)
- Test Device IP input field
- **💡 Blink** button (placeholder - API not yet implemented)
- **🎬 Preview** button (placeholder - toggles preview pane, API not yet implemented)
- **🔃 Reboot** button with confirmation (placeholder - API not yet implemented)
- Preview placeholder div that can be toggled to show device stream later

## Backend API Updates

### GET /api/config
Returns current configuration:
```json
{
  "ok": true,
  "username": "admin",
  "password": "",
  "ws_port": 80,
  "timeout": 4.5,
  "concurrency": 6
}
```
Optional query param: `?include_password=1` to retrieve the actual password

### POST /api/config
Updates configuration:
```json
{
  "username": "admin",
  "password": "NewPassword",
  "ws_port": 80,
  "timeout": 4.5,
  "concurrency": 6
}
```

### GET /api/list_dir
Lists directories for folder browser modal.
Query param: `?path=/some/path` (optional, defaults to CWD)

Response:
```json
{
  "ok": true,
  "path": "/current/path",
  "entries": [
    {"name": "subfolder1", "is_dir": true, "path": "/current/path/subfolder1"},
    {"name": "subfolder2", "is_dir": true, "path": "/current/path/subfolder2"}
  ]
}
```

## Styling Features

- **CSS Variables** for consistent theming across light/dark modes
- **Responsive Layout** with flex-based grid (max 1200px centered)
- **Smooth Transitions** on toggle switches and buttons
- **Status Badges** with color coding (idle green, uploading yellow, error red)
- **Professional Modals** with backdrop blur effect
- **Spinner Animation** for in-progress operations
- **Monospace Font** for technical values (IP addresses, MACs)

## Existing Functionality Preserved

✅ Scan network with adapter selection
✅ TCP+WebSocket probing with thread pool
✅ Unit caching and deduplication
✅ Firmware file browsing (.vpup2 files)
✅ Parallel firmware uploads with concurrency control
✅ Device cleanup (delete old uploads before new ones)
✅ CSV export of discovered units
✅ CSV download
✅ Factory reset endpoint (still available but not UI-exposed)

## Placeholder Functions (APIs to be added by user)

The following buttons have placeholder logic that will trigger alerts showing the expected API call format:

1. **Blink** - `POST /api/blink`
   - Expected payload: `{ip: "192.168.100.xx"}`
   - Will use WebSocket system-command for LED blink

2. **Preview** - `GET /api/preview/mjpeg?ip=192.168.100.xx`
   - Expected: MJPEG stream (likely RTSP relay or HTTP stream)
   - UI shows placeholder div ready for `<img src="...">` tag

3. **Reboot** - `POST /api/reboot`
   - Expected payload: `{ip: "192.168.100.xx"}`
   - Will use WebSocket system-command or similar

## File Changes

### Modified Files
- `c:\logs\upgradeManager\ui\index.html` - Complete redesign with modals and placeholders
- `c:\logs\upgradeManager\OmniMatrix_upgrade_server_v7_6y.py` - Added GET support to /api/config and new /api/list_dir endpoint

### New Files
- `c:\logs\upgradeManager\UI_REDESIGN_SUMMARY.md` - This document

## Next Steps

1. **Visual Verification**: Test the UI in browser to verify ProducerV2 parity
2. **Functionality Check**: Verify all existing scan/upload/export features work
3. **API Implementation**: User to provide API specifications for:
   - `/api/blink` - LED test function
   - `/api/preview/mjpeg` - Device preview stream
   - `/api/reboot` - Reboot function
4. **Integration**: Add the three placeholder APIs to backend

## Colors & Styling Reference

**Dark Theme (Default):**
- Background: `#1d232a`
- Card/Panel: `#222a33`
- Panel 2 (inputs): `#262f39`
- Foreground: `#e9eef5`
- Muted: `#9aa7b4`
- Accent: `#1e90ff` (bright blue, matches ProducerV2)
- Danger: `#e5534b` (red)
- Border: `rgba(255,255,255,0.08)`

**Light Theme:**
- Background: `#f7f8fb`
- Card/Panel: `#fff`
- Panel 2 (inputs): `#f2f5f8`
- Foreground: `#0d1117`
- Muted: `#5a6b7a`
- Border: `rgba(0,0,0,.08)`

## Icons Used
- 🔌 Gear icon
- 🔍 Search
- 🗑️ Delete
- 📥 Export
- ⬇️ Download
- 💡 Blink
- 🎬 Preview
- 🔃 Reboot
- 📁 Folder
- 🌙 Moon (theme toggle)
- ⚡ Power (upgrade)
- 📹 Camera (preview placeholder)
