#!/usr/bin/env python3
"""Fix pollUnits to use correct selector for tbody."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the tbody selector
content = content.replace(
    "const tbody = $('#units_table tbody');",
    "const tbody = $('#units_table');"
)

# Also fix the Array.from selector
content = content.replace(
    "const units = Array.from($$('#units_table tbody tr')).map(row => ({",
    "const units = Array.from($$('#units_table tr')).map(row => ({"
)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Fixed tbody selector in pollUnits")
