#!/usr/bin/env python3
"""Add logging to the IIFE to verify it's running and when."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Add logging to IIFE
old_iife = """// ===== Initialize =====
(async () => {
  await Promise.all([loadAdapters(), loadFiles(), loadUnits()]);
  initTheme();
  initConfig();
  initPolling();
})();"""

new_iife = """// ===== Initialize =====
(async () => {
  console.log('[INIT] Starting initialization IIFE...');
  const start = performance.now();
  await Promise.all([loadAdapters(), loadFiles(), loadUnits()]);
  const elapsed = performance.now() - start;
  console.log(`[INIT] Data loading complete in ${elapsed.toFixed(0)}ms`);
  initTheme();
  initConfig();
  console.log('[INIT] About to call initPolling...');
  initPolling();
  console.log('[INIT] initPolling called');
})();"""

content = content.replace(old_iife, new_iife)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Added IIFE initialization logging")
