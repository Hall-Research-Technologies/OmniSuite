#!/usr/bin/env python3
"""Fix initialization to await async functions before starting polling."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the initialization block
old_init = """// ===== Initialize =====
loadAdapters();
loadFiles();
loadUnits();
initTheme();
initConfig();
initPolling();"""

new_init = """// ===== Initialize =====
(async () => {
  await Promise.all([loadAdapters(), loadFiles(), loadUnits()]);
  initTheme();
  initConfig();
  initPolling();
})();"""

content = content.replace(old_init, new_init)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Fixed initialization to await async functions before polling")
