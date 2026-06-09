#!/usr/bin/env python3
"""Fix status cells to use pill badges."""

import re

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix loadUnits status cell template
old_template = "const statusCellHtml = firstLoad ? '<td class=\"s-error\">Disconnected</td>' : '<td class=\"s-idle\">Idle</td>';"
new_template = """const statusPill = firstLoad ? '<span class="status s-error">Disconnected</span>' : '<span class="status s-idle">Idle</span>';
      const statusCellHtml = '<td>' + statusPill + '</td>';"""
content = content.replace(old_template, new_template)

# Fix pollUnits to set inner HTML with pill badge
old_idle = "statusCell.textContent = 'Idle';\n        statusCell.className = 's-idle';"
new_idle = "statusCell.innerHTML = '<span class=\"status s-idle\">Idle</span>';"
content = content.replace(old_idle, new_idle)

old_disconnected = "statusCell.textContent = 'Disconnected';\n        statusCell.className = 's-error';"
new_disconnected = "statusCell.innerHTML = '<span class=\"status s-error\">Disconnected</span>';"
content = content.replace(old_disconnected, new_disconnected)

# Also fix the catch clause disconnected
old_catch = "      statusCell.textContent = 'Disconnected';\n      statusCell.className = 's-error';"
new_catch = "      statusCell.innerHTML = '<span class=\"status s-error\">Disconnected</span>';"
content = content.replace(old_catch, new_catch)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Updated status cells to use pill badges")
