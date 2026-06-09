#!/usr/bin/env python3
"""Add debugging to see what's in the table element."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Add logging at the start of pollUnits to inspect the table
old_poll_start = """async function pollUnits() {
  console.log('[POLL] pollUnits executing...');
  const units = Array.from($$('#units_table tbody tr')).map(row => ({"""

new_poll_start = """async function pollUnits() {
  console.log('[POLL] pollUnits executing...');
  const tbody = $('#units_table tbody');
  const trs = tbody ? tbody.querySelectorAll('tr') : [];
  console.log('[POLL] Table tbody:', tbody, 'rows found:', trs.length);
  for (let tr of trs) {
    console.log('[POLL] Row:', tr.innerHTML.substring(0, 50));
  }
  const units = Array.from($$('#units_table tbody tr')).map(row => ({"""

content = content.replace(old_poll_start, new_poll_start)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Added table inspection logging")
