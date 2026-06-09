#!/usr/bin/env python3
"""Add detailed logging to pollUnits to see API responses."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the pollUnits function with detailed logging
old_poll_loop = """    try {
      const res = await fetch('/api/poll', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ip: u.ip})
      });
      const data = await res.json();
      if (data.ok) {
        statusCell.innerHTML = '<span class="status s-idle">Idle</span>';
      } else {
        statusCell.innerHTML = '<span class="status s-error">Disconnected</span>';
      }
    } catch (e) {
      statusCell.innerHTML = '<span class="status s-error">Disconnected</span>';
    }"""

new_poll_loop = """    try {
      const res = await fetch('/api/poll', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ip: u.ip})
      });
      const data = await res.json();
      console.log(`[POLL] ${u.ip}: ok=${data.ok}, response:`, data);
      if (data.ok) {
        console.log(`[POLL] ${u.ip} is IDLE`);
        statusCell.innerHTML = '<span class="status s-idle">Idle</span>';
      } else {
        console.log(`[POLL] ${u.ip} is DISCONNECTED`);
        statusCell.innerHTML = '<span class="status s-error">Disconnected</span>';
      }
    } catch (e) {
      console.error(`[POLL] ${u.ip} fetch error:`, e);
      statusCell.innerHTML = '<span class="status s-error">Disconnected</span>';
    }"""

content = content.replace(old_poll_loop, new_poll_loop)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Added detailed polling response logging")
