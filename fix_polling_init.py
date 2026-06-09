#!/usr/bin/env python3
"""Fix polling initialization to run after DOM is ready."""

with open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Change const to let for pollToggle and pollSwitch
content = content.replace(
    "let pollInterval = null;\nconst pollToggle = $('#poll_units_toggle');\nconst pollSwitch = $('#poll_switch');",
    "let pollInterval = null;\nlet pollToggle = null;\nlet pollSwitch = null;"
)

# Replace the top-level if (pollToggle) block with initPolling function
old_block = """if (pollToggle) {
  pollToggle.addEventListener('change', (e) => {
    if (e.target.checked) {
      pollSwitch.classList.add('on');
      startPolling();
      localStorage.setItem('pollUnits', 'true');
    } else {
      pollSwitch.classList.remove('on');
      stopPolling();
      localStorage.setItem('pollUnits', 'false');
    }
  });
  
  if (localStorage.getItem('pollUnits') === 'true') {
    pollToggle.checked = true;
    pollSwitch.classList.add('on');
    startPolling();
  }
}"""

new_block = """function initPolling() {
  pollToggle = $('#poll_units_toggle');
  pollSwitch = $('#poll_switch');
  if (!pollToggle) return;
  
  pollToggle.addEventListener('change', (e) => {
    if (e.target.checked) {
      pollSwitch.classList.add('on');
      startPolling();
      localStorage.setItem('pollUnits', 'true');
    } else {
      pollSwitch.classList.remove('on');
      stopPolling();
      localStorage.setItem('pollUnits', 'false');
    }
  });
  
  if (localStorage.getItem('pollUnits') === 'true') {
    pollToggle.checked = true;
    pollSwitch.classList.add('on');
    startPolling();
  }
}"""

content = content.replace(old_block, new_block)

# Add initPolling() call to the Initialize section
content = content.replace(
    "// ===== Initialize =====\nloadAdapters();\nloadFiles();\nloadUnits();\ninitTheme();\ninitConfig();",
    "// ===== Initialize =====\nloadAdapters();\nloadFiles();\nloadUnits();\ninitTheme();\ninitConfig();\ninitPolling();"
)

with open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Fixed polling initialization timing")
