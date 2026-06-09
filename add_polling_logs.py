#!/usr/bin/env python3
"""Add console logging to debug polling."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Add debug logging to initPolling
old_init = """function initPolling() {
  pollToggle = $('#poll_units_toggle');
  pollSwitch = $('#poll_switch');
  if (!pollToggle) return;"""

new_init = """function initPolling() {
  console.log('[POLL] initPolling called');
  pollToggle = $('#poll_units_toggle');
  pollSwitch = $('#poll_switch');
  console.log('[POLL] pollToggle:', pollToggle, 'pollSwitch:', pollSwitch);
  if (!pollToggle) {
    console.error('[POLL] pollToggle not found!');
    return;
  }"""

content = content.replace(old_init, new_init)

# Add logging to the event listener
old_listener = """  pollToggle.addEventListener('change', (e) => {
    if (e.target.checked) {
      pollSwitch.classList.add('on');
      startPolling();"""

new_listener = """  pollToggle.addEventListener('change', (e) => {
    console.log('[POLL] Toggle changed:', e.target.checked);
    if (e.target.checked) {
      console.log('[POLL] Starting poll...');
      pollSwitch.classList.add('on');
      startPolling();"""

content = content.replace(old_listener, new_listener)

# Add logging to startPolling
old_start = """function startPolling() {
  if (pollInterval) return;
  pollUnits();
  pollInterval = setInterval(pollUnits, 2000);"""

new_start = """function startPolling() {
  console.log('[POLL] startPolling called');
  if (pollInterval) {
    console.log('[POLL] Poll already running');
    return;
  }
  console.log('[POLL] First poll...');
  pollUnits();
  pollInterval = setInterval(pollUnits, 2000);
  console.log('[POLL] Interval started');"""

content = content.replace(old_start, new_start)

# Add logging to pollUnits
old_poll = """async function pollUnits() {
  const units = Array.from($$('#units_table tbody tr')).map(row => ({"""

new_poll = """async function pollUnits() {
  console.log('[POLL] pollUnits executing...');
  const units = Array.from($$('#units_table tbody tr')).map(row => ({"""

content = content.replace(old_poll, new_poll)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Added debug logging for polling")
