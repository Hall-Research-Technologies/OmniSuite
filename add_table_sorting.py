#!/usr/bin/env python3
"""Add column sorting to the units table."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Add sorting state variables at the top of the script
sort_state = '''
// Table sorting state
let currentSortCol = null;
let sortAscending = true;

function sortTable(colIndex, dataAttr) {
  const tbody = $('#units_table');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  
  // Toggle sort direction if clicking same column
  if (currentSortCol === colIndex) {
    sortAscending = !sortAscending;
  } else {
    sortAscending = true;
    currentSortCol = colIndex;
  }
  
  rows.sort((a, b) => {
    let aVal = a.cells[colIndex]?.textContent.trim() || '';
    let bVal = b.cells[colIndex]?.textContent.trim() || '';
    
    // Try numeric sort if both look like numbers
    const aNum = parseFloat(aVal);
    const bNum = parseFloat(bVal);
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return sortAscending ? aNum - bNum : bNum - aNum;
    }
    
    // String sort
    return sortAscending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
  });
  
  // Re-append sorted rows
  rows.forEach(row => tbody.appendChild(row));
  
  // Update header indicators
  Array.from(document.querySelectorAll('table th')).forEach((th, idx) => {
    th.style.cursor = idx <= 6 ? 'pointer' : 'default';
    th.style.opacity = (idx === colIndex) ? '1' : '0.6';
    th.textContent = th.textContent.replace(/ [↑↓]/g, '');
    if (idx === colIndex) {
      th.textContent += sortAscending ? ' ↑' : ' ↓';
    }
  });
}
'''

# Find where to insert (after the first script var declarations)
insert_pos = content.find('// Poll Units functionality')
if insert_pos > 0:
    content = content[:insert_pos] + sort_state + '\n' + content[insert_pos:]

# Update the table headers to be clickable
old_headers = '''      <tr>
        <th style="width:32px;"><input type="checkbox" id="select_all"></th>
        <th>IP</th>
        <th>MAC</th>
        <th>Hostname</th>
        <th>Type</th>
        <th>Model</th>
        <th>Version</th>
        <th style="width:60px;">Status</th>
        <th style="width:40px;">Blink</th>
        <th style="width:70px;">Preview</th>
      </tr>'''

new_headers = '''      <tr>
        <th style="width:32px;"><input type="checkbox" id="select_all"></th>
        <th onclick="sortTable(1)">IP</th>
        <th onclick="sortTable(2)">MAC</th>
        <th onclick="sortTable(3)">Hostname</th>
        <th onclick="sortTable(4)">Type</th>
        <th onclick="sortTable(5)">Model</th>
        <th onclick="sortTable(6)">Version</th>
        <th style="width:60px;">Status</th>
        <th style="width:40px;">Blink</th>
        <th style="width:70px;">Preview</th>
      </tr>'''

content = content.replace(old_headers, new_headers)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Added sortable columns to table (IP, MAC, Hostname, Type, Model, Version)")
