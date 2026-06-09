#!/usr/bin/env python3
"""Add granular logging to pollUnits to find the issue."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the pollUnits function with more granular logging
old_poll = """async function pollUnits() {
  console.log('[POLL] pollUnits executing...');
  const units = Array.from($$('#units_table tbody tr')).map(row => ({
    ip: row.getAttribute('data-ip'),
    el: row
  }));
  
  for (const u of units) {
    // Skip rows without IP or without a status cell
    if (!u.ip || !u.el || !u.el.cells || !u.el.cells[7]) continue;"""

new_poll = """async function pollUnits() {
  console.log('[POLL] pollUnits executing...');
  const units = Array.from($$('#units_table tbody tr')).map(row => ({
    ip: row.getAttribute('data-ip'),
    el: row
  }));
  console.log(`[POLL] Found ${units.length} rows in table`);
  
  for (const u of units) {
    console.log(`[POLL] Processing unit - ip: ${u.ip}, has cells: ${!!u.el.cells}, cell[7]: ${u.el.cells?.[7]?.innerHTML}`);
    // Skip rows without IP or without a status cell
    if (!u.ip || !u.el || !u.el.cells || !u.el.cells[7]) {
      console.log(`[POLL] Skipping - missing ip(${!!u.ip}) el(${!!u.el}) cells(${!!u.el?.cells}) cell7(${!!u.el?.cells?.[7]})`);
      continue;
    }"""

content = content.replace(old_poll, new_poll)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Added granular pollUnits logging")
