"""Mutations for where the keyboard is when the Restore confirmation opens.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must occur
exactly once. Imported by mutate_security.py and appended to MUTATIONS, so
`python tools/mutate_security.py restorefocus.` runs exactly these.
"""
RJS = ["tests.test_restore_js"]

RESTORE_FOCUS_MUTATIONS = [
    ("restorefocus.drops-the-cancel-default", "ui/settings.js",
     "    defaultAction: 'cancel',\n  });\n  if (!go) {",
     "  });\n  if (!go) {",
     "the Restore confirmation would open on its red button and one Enter would replace the accounts",
     RJS),
    ("restorefocus.dialog-ignores-the-default", "ui/index.html",
     "  const cancelIsDefault = options.defaultAction === 'cancel';",
     "  const cancelIsDefault = false;",
     "the page's own dialog would focus OK whatever the caller asked, for every costly question",
     RJS),
]
