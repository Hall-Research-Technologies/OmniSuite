"""Mutations for the A/V Matrix select-all cell under Lock Headers.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must occur
exactly once. Imported by mutate_security.py and appended to MUTATIONS, so
`python tools/mutate_security.py stickycorner.` runs exactly these.
"""
STICKY = ["tests.test_matrix_sticky_offsets"]

STICKY_CORNER_MUTATIONS = [
    ("stickycorner.select-all-pins-sideways", "ui/matrix/matrix.css",
     ".matrix.sticky-enabled th.row-head.select-all-head{ left:auto; }",
     ".matrix.sticky-enabled th.row-head.select-all-head{ top:auto; }",
     "the select-all checkbox would hide under the frozen label as soon as the grid scrolls",
     STICKY),
    ("stickycorner.label-loses-its-class-mapping", "ui/matrix/matrix.js",
     '<tr><th class="row-head select-all-head"><input type="checkbox" id="group-master-checkbox"',
     '<tr><th class="row-head"><input type="checkbox" id="group-master-checkbox"',
     "the select-all cell would stay pinned on top of the label",
     STICKY),
]
