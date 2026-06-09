#!/usr/bin/env python3
"""Combine Export and Download CSV buttons into single Export Units button."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the two buttons with one
old_buttons = '''    <button class="btn" id="export_csv_btn">📥 Export CSV</button>
    <button class="btn" id="download_csv_btn">⬇️ Download CSV</button>'''

new_button = '''    <button class="btn" id="export_units_btn">⬇️ Export Units</button>'''

content = content.replace(old_buttons, new_button)

# Replace the event listeners
old_listeners = '''$('#export_csv_btn').addEventListener('click', async () => {
  try {
    await fetch('/api/export_csv', {method: 'POST'});
    alert('CSV exported');
  } catch (e) {
    alert('Export failed: ' + e.message);
  }
});

$('#download_csv_btn').addEventListener('click', () => {
  window.location = '/api/download_csv';
});'''

new_listener = '''$('#export_units_btn').addEventListener('click', async () => {
  try {
    await fetch('/api/export_csv', {method: 'POST'});
    setTimeout(() => {
      window.location = '/api/download_csv';
    }, 100);
  } catch (e) {
    alert('Export failed: ' + e.message);
  }
});'''

content = content.replace(old_listeners, new_listener)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Combined CSV buttons into single 'Export Units' button")
