#!/usr/bin/env python3
"""Update clear units to temporarily disable polling."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the clear units listener
old_listener = '''$('#clear_units_btn').addEventListener('click', async () => {
  if (!confirm('Clear all cached units?')) return;
  try {
    await fetch('/api/clear_units', {method: 'POST'});
    await loadUnits();
  } catch (e) {
    alert('Clear failed: ' + e.message);
  }
});'''

new_listener = '''$('#clear_units_btn').addEventListener('click', async () => {
  if (!confirm('Clear all cached units?')) return;
  const wasPolling = pollInterval !== null;
  if (wasPolling) stopPolling();
  try {
    await fetch('/api/clear_units', {method: 'POST'});
    await loadUnits();
    if (wasPolling) startPolling();
  } catch (e) {
    alert('Clear failed: ' + e.message);
    if (wasPolling) startPolling();
  }
});'''

content = content.replace(old_listener, new_listener)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Updated clear units to pause/resume polling")
