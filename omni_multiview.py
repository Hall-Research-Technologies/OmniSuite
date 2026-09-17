"""OmniStream Multiview layout geometry, scaler selection and resource planning.

This module is the single canonical layout engine. The UI renders the preview
from the geometry computed here and the apply transaction writes the geometry
computed here, so the picture on screen and the configuration sent to hardware
cannot disagree.

Nothing in this module touches the network, Flask, or persisted state. It is
pure computation over values the caller has already read, which is what makes
the whole of it testable without hardware.

Everything here is derived from the Phase 1 live findings in
`docs/MULTIVIEW_DISCOVERY.md`. Three of those findings shape the design:

- The device has no `layout` field. Layouts are an OmniSuite abstraction that
  generates explicit subframe geometry; the decoder stores geometry only.
- A subframe has no width or height. A window's size is the resolution of the
  stream arriving on its ip_input, which is set by the *encoder's* scaler.
- `vc2_encoder2` tops out at 1920x1080 and has no "disable" option, so a window
  larger than that must come from `vc2_encoder1`.
"""
from __future__ import annotations

import json
import re

# Slice granularity reported by the decoder in each multiview's `slice_info`.
# These are the fallback values; a caller holding a real `slice_info` should
# pass it rather than assume, because the device is the authority.
SLICE_WIDTH = 32
SLICE_HEIGHT = 8

# The decoder refuses a canvas wider than this. Height is NOT validated by the
# device at all -- 1920x1085 and 0x0 are both accepted -- so the ceiling below
# is ours, not the device's.
MAX_CANVAS_WIDTH = 3840
MAX_CANVAS_HEIGHT = 2160
MAX_SUBFRAMES = 4

ANCHORS = ("top left", "top right", "bottom left", "bottom right", "center")

# Canvas presets. `exposed` is what the UI offers; the table carries 2560x1440
# so that enabling it later is a flag change rather than a redesign. It stays
# unexposed because six of its window sizes have no encoder scaler format
# (see MULTIVIEW_DISCOVERY.md section M).
CANVAS_PRESETS = (
    {"id": "1920x1080", "width": 1920, "height": 1080, "exposed": True},
    {"id": "3840x2160", "width": 3840, "height": 2160, "exposed": False,
     "note": "Not offered: a 4K canvas needs Encoder 1 for nine of the eleven "
             "layouts, which retunes the source's primary stream."},
    {"id": "2560x1440", "width": 2560, "height": 1440, "exposed": False,
     "note": "Not offered: 1+3-horizontal windows require 896x480, "
             "which no encoder scaler format provides."},
)

# The one canvas the active product path may plan, apply or show. This is not a
# presentation choice: `plan_multiview` refuses anything else outright, so a
# 4K or 1440p plan cannot be constructed even by calling the planner directly
# with a canvas the UI does not offer.
#
# The 4K geometry, its scaler table and the output-resolution mapping all stay
# in this module so the capability can be brought back, but nothing in the
# active path reaches them.
ACTIVE_CANVAS = "1920x1080"

EXPOSED_CANVASES = tuple(p["id"] for p in CANVAS_PRESETS if p["exposed"])


# The decoder's own HDMI Output -> Resolution list, from its web application.
# `input` and `auto` are deliberately not used for a Multiview: the canvas, not
# the incoming signal, decides what the output must be.
DECODER_OUTPUT_RESOLUTIONS = (
    "input", "auto", "4096x2160", "3840x2160", "1920x1200", "1920x1080",
    "1680x1050", "1600x900", "1400x1050", "1440x900", "1280x1024", "1280x800",
    "1280x768", "1280x720", "1024x768")


def output_resolution_for_canvas(width, height):
    """The decoder output resolution a Multiview canvas requires.

    A canvas is frequently snapped away from its preset -- 1920x1088, 3840x2112 --
    so the preset is recovered from the width, which is unambiguous between the
    two exposed sizes. Returns None for a canvas with no exposed preset, and the
    caller then leaves the output alone rather than guessing.
    """
    for preset in CANVAS_PRESETS:
        if not preset["exposed"]:
            continue
        if int(width or 0) == preset["width"]:
            resolution = "%dx%d" % (preset["width"], preset["height"])
            return resolution if resolution in DECODER_OUTPUT_RESOLUTIONS else None
    return None


def canvas_preset(canvas_id):
    for preset in CANVAS_PRESETS:
        if preset["id"] == canvas_id:
            return preset
    return None


# --------------------------------------------------------------------------
# Layout templates
# --------------------------------------------------------------------------
# Each cell is expressed in grid units. `anchor`, `x_offset` and `y_offset`
# appear only on the PiP insets. Transcribed from the decoder firmware's own
# `vm.layout_configs`, which is the only place these exist.

LAYOUTS = {
    "2x2": {
        "label": "2x2",
        "grid": (2, 2),
        "cells": (
            ("top_left", 0, 0, 1, 1, None, 0, 0),
            ("top_right", 1, 0, 1, 1, None, 0, 0),
            ("bottom_left", 0, 1, 1, 1, None, 0, 0),
            ("bottom_right", 1, 1, 1, 1, None, 0, 0),
        ),
    },
    "side-by-side": {
        "label": "Side-by-Side",
        "grid": (2, 4),
        "cells": (
            ("left", 0, 1, 1, 2, None, 0, 0),
            ("right", 1, 1, 1, 2, None, 0, 0),
        ),
    },
    "pip-top-left": {
        "label": "PiP — Top Left",
        "grid": (3, 3),
        "cells": (
            ("main", 0, 0, 3, 3, None, 0, 0),
            ("top_left", 0, 0, 1, 1, "top left", 32, 32),
        ),
    },
    "pip-top-right": {
        "label": "PiP — Top Right",
        "grid": (3, 3),
        "cells": (
            ("main", 0, 0, 3, 3, None, 0, 0),
            ("top_right", 3, 0, 1, 1, "top right", -32, 32),
        ),
    },
    "pip-bottom-left": {
        "label": "PiP — Bottom Left",
        "grid": (3, 3),
        "cells": (
            ("main", 0, 0, 3, 3, None, 0, 0),
            ("bottom_left", 0, 3, 1, 1, "bottom left", 32, -32),
        ),
    },
    "pip-bottom-right": {
        "label": "PiP — Bottom Right",
        "grid": (3, 3),
        "cells": (
            ("main", 0, 0, 3, 3, None, 0, 0),
            ("bottom_right", 3, 3, 1, 1, "bottom right", -32, -32),
        ),
    },
    "1+3-horizontal-bottom": {
        "label": "1+3 — Horizontal Bottom",
        "grid": (6, 3),
        "cells": (
            ("main", 1, 0, 4, 2, None, 0, 0),
            ("bottom_left", 0, 2, 2, 1, None, 0, 0),
            ("bottom_middle", 2, 2, 2, 1, None, 0, 0),
            ("bottom_right", 4, 2, 2, 1, None, 0, 0),
        ),
    },
    "1+3-horizontal-top": {
        "label": "1+3 — Horizontal Top",
        "grid": (6, 3),
        "cells": (
            ("main", 1, 1, 4, 2, None, 0, 0),
            ("top_left", 0, 0, 2, 1, None, 0, 0),
            ("top_middle", 2, 0, 2, 1, None, 0, 0),
            ("top_right", 4, 0, 2, 1, None, 0, 0),
        ),
    },
    "1+3-vertical-right": {
        "label": "1+3 — Vertical Right",
        "grid": (3, 6),
        "cells": (
            ("main", 0, 1, 2, 4, None, 0, 0),
            ("top_right", 2, 0, 1, 2, None, 0, 0),
            ("middle_right", 2, 2, 1, 2, None, 0, 0),
            ("bottom_right", 2, 4, 1, 2, None, 0, 0),
        ),
    },
    "1+3-vertical-left": {
        "label": "1+3 — Vertical Left",
        "grid": (3, 6),
        "cells": (
            ("main", 1, 1, 2, 4, None, 0, 0),
            ("top_left", 0, 0, 1, 2, None, 0, 0),
            ("middle_left", 0, 2, 1, 2, None, 0, 0),
            ("bottom_left", 0, 4, 1, 2, None, 0, 0),
        ),
    },
    "4-split": {
        "label": "4-Split",
        "grid": (4, 8),
        "cells": (
            ("main", 0, 1, 3, 6, None, 0, 0),
            ("top_right", 3, 1, 1, 2, None, 0, 0),
            ("middle_right", 3, 3, 1, 2, None, 0, 0),
            ("bottom_right", 3, 5, 1, 2, None, 0, 0),
        ),
    },
}

# The order the UI offers them in.
LAYOUT_ORDER = (
    "2x2", "side-by-side",
    "pip-top-left", "pip-top-right", "pip-bottom-left", "pip-bottom-right",
    "1+3-horizontal-bottom", "1+3-horizontal-top",
    "1+3-vertical-right", "1+3-vertical-left",
    "4-split",
)


# --------------------------------------------------------------------------
# Encoder scaler formats
# --------------------------------------------------------------------------
# From the encoder firmware's own tables. Encoder 1 gets 4K + HD + "disable";
# Encoder 2 gets HD only and has no "disable" -- it is always scaling.

SCALER_4K_FORMATS = ("3840x2160", "2880x1584", "2592x1440", "2560x1440", "1920x1104")
SCALER_HD_FORMATS = ("1920x1080", "1920x1072", "1792x960", "1728x960", "1440x816",
                     "1280x736", "1280x720", "960x544", "960x528", "864x480",
                     "640x368", "640x360", "480x272")

ENCODER1_FORMATS = SCALER_4K_FORMATS + SCALER_HD_FORMATS
ENCODER2_FORMATS = SCALER_HD_FORMATS

# Encoder 2's ceiling, and the threshold the encoder-selection rule turns on.
ENCODER2_MAX_WIDTH = 1920
ENCODER2_MAX_HEIGHT = 1080

# Bitrates live with the rest of the bandwidth model further down; see
# SOURCE_VIDEO_BUDGET and `allocate_bitrates`. The device's own limits were
# measured, not assumed.


def round_down(value, multiple):
    return value - value % multiple if value % multiple else value


def round_up(value, multiple):
    return value - value % multiple + multiple if value % multiple else value


def snap_dimension(value, multiple, cap):
    """Round `value` to a multiple of `multiple`, respecting the 4K ceiling.

    Down at exactly the cap so a 4K panel is never exceeded; up everywhere else,
    because a canvas smaller than asked for would letterbox the display.

    Named rather than inlined so the direction can be asserted directly: every
    layout granularity happens to divide 3840 evenly, so through the eleven
    layouts the choice at the cap is unobservable, and a test that went through
    them could not tell the two directions apart.
    """
    return round_down(value, multiple) if value == cap else round_up(value, multiple)


def compute_canvas(layout_name, canvas_width, canvas_height,
                   slice_width=SLICE_WIDTH, slice_height=SLICE_HEIGHT):
    """The canvas the device will actually use, which is not always the request.

    The canvas is snapped so it divides evenly into the layout grid.
    """
    layout = LAYOUTS[layout_name]
    grid_w, grid_h = layout["grid"]
    return (snap_dimension(canvas_width, grid_w * slice_width, MAX_CANVAS_WIDTH),
            snap_dimension(canvas_height, grid_h * slice_height, MAX_CANVAS_HEIGHT))


def compute_windows(layout_name, canvas_width, canvas_height,
                    slice_width=SLICE_WIDTH, slice_height=SLICE_HEIGHT):
    """Geometry for every window in a layout, on a snapped canvas.

    `width`/`height` are the *intended source resolution* for the window. They
    are not written to the decoder -- a subframe has no size -- but they decide
    which encoder and scaler format the window needs.
    """
    layout = LAYOUTS[layout_name]
    grid_w, grid_h = layout["grid"]
    total_w, total_h = compute_canvas(layout_name, canvas_width, canvas_height,
                                      slice_width, slice_height)
    windows = []
    for index, (name, cx, cy, cw, ch, anchor, dx, dy) in enumerate(layout["cells"]):
        windows.append({
            "index": index,
            "cell": name,
            "x": total_w * cx // grid_w + dx,
            "y": total_h * cy // grid_h + dy,
            "anchor": anchor or "top left",
            "width": total_w * cw // grid_w,
            "height": total_h * ch // grid_h,
            "priority": index + 1,
        })
    return {"layout": layout_name, "canvas": {"width": total_w, "height": total_h},
            "requested": {"width": canvas_width, "height": canvas_height},
            "snapped": (total_w, total_h) != (canvas_width, canvas_height),
            "windows": windows}


def subframe_label(cell_name, width, height):
    """The decoder's own naming convention: the size lives in the name.

    A subframe has nowhere else to record its intended size, so the vendor UI
    appends it. The device does not parse this; it is a human hint, and it gives
    OmniSuite a cheap signal when inferring a layout later.
    """
    return f"{cell_name} ({width}x{height})"


_LABEL_RE = re.compile(r"^(?P<cell>.+?)\s*\((?P<w>\d+)x(?P<h>\d+)\)\s*$")


def parse_subframe_label(name):
    """Split a `cell (WxH)` subframe name, or return (name, None, None)."""
    match = _LABEL_RE.match(str(name or ""))
    if not match:
        return str(name or ""), None, None
    return match.group("cell"), int(match.group("w")), int(match.group("h"))


def layout_geometry_catalog(canvases=None):
    """Every exposed layout x canvas, precomputed.

    The UI consumes this instead of reimplementing the geometry, which is what
    keeps the on-screen preview and the device write from ever disagreeing.
    """
    canvases = canvases or EXPOSED_CANVASES
    catalog = []
    for layout_name in LAYOUT_ORDER:
        entry = {"id": layout_name, "label": LAYOUTS[layout_name]["label"],
                 "window_count": len(LAYOUTS[layout_name]["cells"]), "canvases": {}}
        for canvas_id in canvases:
            preset = canvas_preset(canvas_id)
            if not preset:
                continue
            geometry = compute_windows(layout_name, preset["width"], preset["height"])
            for window in geometry["windows"]:
                window["encoder"] = required_encoder(window["width"], window["height"])
                window["scaler_format"] = f"{window['width']}x{window['height']}"
                window["scaler_supported"] = scaler_format_supported(
                    window["width"], window["height"], window["encoder"])
            entry["canvases"][canvas_id] = geometry
        catalog.append(entry)
    return catalog


# --------------------------------------------------------------------------
# One OmniSuite Multiview per canvas resolution
# --------------------------------------------------------------------------
# A decoder may hold at most one OmniSuite-managed Multiview per exposed canvas,
# so a second one cannot quietly displace the first. The limit counts only
# Multiviews OmniSuite manages: an object created in the device's own web UI is
# left entirely alone, neither blocking a canvas nor at risk of being
# overwritten, because OmniSuite has no claim on it.
#
# Changing an existing Multiview's layout is not affected -- that updates the
# object already holding the canvas rather than creating another.

def canvas_availability(managed, canvases=None):
    """Which exposed canvases are free for a new Multiview.

    `managed` is an iterable of {"object_name", "canvas"} for the Multiviews
    OmniSuite manages on this decoder, where `canvas` is the requested canvas id
    (the preset the operator chose, not the snapped result).
    """
    taken = {}
    for entry in managed or ():
        canvas_id = str((entry or {}).get("canvas") or "")
        if canvas_id and canvas_id not in taken:
            taken[canvas_id] = entry

    available = []
    for preset in CANVAS_PRESETS:
        if not preset["exposed"] and preset["id"] not in (canvases or ()):
            continue
        holder = taken.get(preset["id"])
        available.append({
            "id": preset["id"],
            "width": preset["width"],
            "height": preset["height"],
            "available": holder is None,
            "used_by": (holder or {}).get("object_name"),
            "used_by_label": (holder or {}).get("friendly_name")
                             or (holder or {}).get("object_name"),
            "reason": "" if holder is None else "Already configured",
        })
    return available


def can_create_multiview(managed, canvases=None):
    """Whether any exposed canvas is still free."""
    return any(entry["available"] for entry in canvas_availability(managed, canvases))

# --------------------------------------------------------------------------
# Layout inference
# --------------------------------------------------------------------------

def infer_layout(canvas_width, canvas_height, subframes, canvases=None):
    """Recover the layout of a multiview read back from a decoder.

    The device does not persist the layout name, so a multiview OmniSuite did
    not create -- or one edited elsewhere -- arrives as bare geometry. Matching
    it against the generated matrices recovers the name when the geometry is
    genuinely one of ours, and returns None when it is not.

    Returns (layout_id, canvas_id) or (None, None).
    """
    canvases = canvases or [p["id"] for p in CANVAS_PRESETS]
    placements = sorted(
        (int(s.get("x") or 0), int(s.get("y") or 0), str(s.get("anchor") or "top left"))
        for s in (subframes or []))
    if not placements:
        return None, None
    for layout_name in LAYOUT_ORDER:
        if len(LAYOUTS[layout_name]["cells"]) != len(placements):
            continue
        for canvas_id in canvases:
            preset = canvas_preset(canvas_id)
            if not preset:
                continue
            geometry = compute_windows(layout_name, preset["width"], preset["height"])
            if (geometry["canvas"]["width"] != canvas_width
                    or geometry["canvas"]["height"] != canvas_height):
                continue
            expected = sorted((w["x"], w["y"], w["anchor"]) for w in geometry["windows"])
            if expected == placements:
                return layout_name, canvas_id
    return None, None


def reconcile_layout(stored, canvas_width, canvas_height, subframes):
    """Decide what layout name to show for a multiview now on the device.

    Stored metadata is a cache, never the authority. It is used only when the
    hardware geometry still matches it; otherwise the layout is re-inferred, and
    failing that the multiview is Custom. Naming a layout whose geometry no
    longer matches would be a lie the operator could act on.
    """
    inferred, inferred_canvas = infer_layout(canvas_width, canvas_height, subframes)
    stored_layout = (stored or {}).get("layout")
    if stored_layout and stored_layout == inferred:
        return {"layout": stored_layout, "canvas": inferred_canvas,
                "source": "stored", "matches_hardware": True}
    if inferred:
        return {"layout": inferred, "canvas": inferred_canvas,
                "source": "inferred", "matches_hardware": True,
                "stored_layout": stored_layout or None,
                "diverged": bool(stored_layout and stored_layout != inferred)}
    return {"layout": None, "canvas": None, "source": "custom",
            "matches_hardware": False, "stored_layout": stored_layout or None,
            "diverged": bool(stored_layout)}


# --------------------------------------------------------------------------
# Encoder / scaler selection
# --------------------------------------------------------------------------

MULTIVIEW_ENCODER_INDEX = 2


def required_encoder(width, height):
    """The encoder every Multiview window uses: Encoder 2, always.

    Encoder 1 carries the source's primary stream, and other decoders depend on
    it. Building a Multiview window is not a reason to retune it, so the active
    product path never does -- neither its scaler nor, unless the bandwidth
    budget leaves no alternative, its bitrate.

    This is only possible because the canvas is fixed at 1920x1080: every window
    of every one of the eleven layouts is then a size Encoder 2 can produce
    exactly. A window Encoder 2 cannot produce is refused by name rather than
    quietly promoted to Encoder 1 -- see `scaler_format_supported`, which the
    planner consults for every window.
    """
    return MULTIVIEW_ENCODER_INDEX


def encoder_for_window_size(width, height):
    """The size-based rule, kept for the dormant 4K path. Not the active rule.

    At a 4K canvas nine of the eleven layouts need a window larger than
    1920x1080, which only Encoder 1 can produce. Nothing in the active workflow
    calls this; `required_encoder` above is what the planner uses.
    """
    if width > ENCODER2_MAX_WIDTH or height > ENCODER2_MAX_HEIGHT:
        return 1
    return 2


def scaler_format_supported(width, height, encoder_index):
    formats = ENCODER1_FORMATS if encoder_index == 1 else ENCODER2_FORMATS
    return f"{width}x{height}" in formats


def session_for_encoder(encoder_index):
    return f"session{encoder_index}"


def encoder_object_name(encoder_index):
    return f"vc2_encoder{encoder_index}"


# --------------------------------------------------------------------------
# Source eligibility
# --------------------------------------------------------------------------
# Exactly one product is excluded from the Multiview source list: the older
# OmniStream 2.0 wall plate, AT-OMNI-111-WP. This is not a wall-plate rule.
# HW-OMNI-E4111-WP is a 4xxx wall plate and is a perfectly good Multiview
# source; an earlier `endswith("-WP")` rule wrongly excluded it.
#
# The exclusion applies to source eligibility only. The device stays fully
# discoverable, visible and routable everywhere else in OmniSuite.

# Models that may not feed a Multiview window, by normalised identity.
EXCLUDED_SOURCE_MODELS = frozenset({"at-omni-111-wp"})

EXCLUDED_SOURCE_REASON = (
    "AT-OMNI-111-WP is not supported as a Multiview source")


def active_multiview_name(video_input):
    """The Multiview currently on a decoder's display, or None.

    A decoder's HDMI video input is either one of its own inputs -- `ip_input1`,
    `ip_input2` and so on -- or the name of a Multiview object it is
    compositing. So anything that is not an ip_input is a Multiview.

    This is the only definition of "currently in Multiview". The Multiview page
    and the A/V Matrix both use it, which is what stops them describing the same
    decoder differently. It reads the decoder's CURRENT output selection and
    nothing else: a saved object, a remembered selection or a piece of metadata
    proves nothing about what is on screen.
    """
    text = str(video_input or "").strip()
    if not text or re.fullmatch(r"ip_input\d+", text):
        return None
    return text


def normalize_model(model):
    """A device's model as a comparable identity.

    Case, whitespace and the separator style vary between the protocol-reported
    value and what is printed on the device, so all three are normalised away.
    `HW-OMNI-E4111-WP`, `hw omni e4111 wp` and `hw_omni_e4111_wp` are one model.
    """
    text = re.sub(r"[\s_]+", "-", str(model or "").strip().lower())
    return re.sub(r"-{2,}", "-", text).strip("-")


def is_excluded_source_model(model):
    """True only for a model on the exclusion list.

    Matched whole, never by suffix or substring. A suffix rule took
    HW-OMNI-E4111-WP with it, and a substring rule on "111" would take the plain
    AT-OMNI-111 -- both of which are eligible sources.
    """
    return normalize_model(model) in EXCLUDED_SOURCE_MODELS


# --------------------------------------------------------------------------
# Source eligibility
# --------------------------------------------------------------------------
# A source list that shows every discovered encoder is a list of things that
# might work. Three states, because they call for three different actions and
# collapsing them hides the one the operator can actually fix:
#
#   READY                   usable now
#   CONFIGURATION REQUIRED  the device is fine, but something on it has to be
#                           set before OmniSuite can use it -- and OmniSuite
#                           will not set it merely because a page was opened
#   INELIGIBLE              offline, wrong model, or no encoder to use
#
# Everything here is decided from state discovery already holds, plus one short
# reachability probe the caller supplies. Nothing in this function talks to a
# device, and nothing is added to the scan.

SOURCE_READY = "ready"
SOURCE_CONFIGURATION_REQUIRED = "configuration_required"
SOURCE_INELIGIBLE = "ineligible"

SOURCE_STATUS_LABELS = {
    SOURCE_READY: "Ready",
    SOURCE_CONFIGURATION_REQUIRED: "Configuration required",
    SOURCE_INELIGIBLE: "Unavailable",
}

# The codecs a Multiview window can be carried by. The scan already records the
# device's own reported codec; an encoder running something else cannot feed a
# vc2 session however healthy it otherwise looks.
MULTIVIEW_CODECS = ("vcx", "vc2")


def _codec_family(codec):
    text = str(codec or "").strip().lower()
    for family in MULTIVIEW_CODECS:
        if text.startswith(family):
            return family
    return ""


def classify_source(device, reachable=None):
    """Whether a discovered encoder can feed a Multiview window, and why not.

    `reachable` is the caller's own liveness answer -- None means it did not
    ask, and the source is then judged on everything else. A device that was
    asked and did not answer is ineligible: a window fed by an encoder that is
    not there is a black rectangle, and offering it is offering a failure.

    Returns {"status", "reason", "detail", "action"} where `action` names the
    thing the operator could do about it, or "" when there is nothing.
    """
    device = device or {}
    role = str(device.get("role") or device.get("type") or "").strip().lower()
    if role != "encoder":
        return {"status": SOURCE_INELIGIBLE, "reason": "not an encoder",
                "detail": "", "action": ""}

    if is_excluded_source_model(device.get("model")):
        return {"status": SOURCE_INELIGIBLE, "reason": EXCLUDED_SOURCE_REASON,
                "detail": "", "action": ""}

    if reachable is False:
        return {"status": SOURCE_INELIGIBLE, "reason": "Offline",
                "detail": "This encoder did not answer, so it cannot feed a window.",
                "action": ""}

    codec = _codec_family(device.get("codec"))
    if not codec:
        return {"status": SOURCE_INELIGIBLE,
                "reason": "No supported encoder",
                "detail": "This device does not report a %s encoder, which is "
                          "what a Multiview window is carried by."
                          % "/".join(c.upper() for c in MULTIVIEW_CODECS),
                "action": ""}

    # Multiview windows are Session 2, and its destination is the device's own
    # to generate. Without one there is nothing for a decoder input to open, and
    # inventing an address is not something this application does.
    address = str(device.get("session2_video_mcast") or "").strip()
    if not address:
        return {"status": SOURCE_CONFIGURATION_REQUIRED,
                "reason": "Multicast configuration required",
                "detail": "Session 2 on this encoder has no multicast "
                          "destination. Configure it on the device -- it "
                          "generates its own default -- before using it in a "
                          "Multiview.",
                "action": "configure_multicast"}

    return {"status": SOURCE_READY, "reason": "", "detail": "", "action": ""}


def is_eligible_source(device):
    """Whether a device may feed a Multiview window, ignoring reachability.

    Kept as the model-identity test the rest of the application uses -- window
    source resolution, the excluded-model rule -- and deliberately separate from
    `classify_source`, which answers the different question of whether it can be
    used *now*.
    """
    device = device or {}
    role = str(device.get("role") or device.get("type") or "").strip().lower()
    if role != "encoder":
        return False, "not an encoder"
    if is_excluded_source_model(device.get("model")):
        return False, EXCLUDED_SOURCE_REASON
    return True, ""


def classify_decoder(device, reachable=None, multiview_supported=None,
                     hdmi_output=None):
    """Whether a decoder can be offered as a Multiview target.

    Offline is not a choice, and a decoder the interlocks bar is offered with
    the reason rather than silently -- the operator can turn Video Wall off,
    and would never guess that was the problem from an empty list.
    """
    device = device or {}
    if reachable is False:
        return {"status": SOURCE_INELIGIBLE, "reason": "Offline",
                "detail": "This decoder did not answer.", "blocked": []}
    if multiview_supported is False:
        return {"status": SOURCE_INELIGIBLE, "reason": "No Multiview support",
                "detail": "This decoder does not expose Multiview.",
                "blocked": []}
    blocked = interlocks(device.get("model"), hdmi_output) if hdmi_output else []
    if blocked:
        return {"status": SOURCE_CONFIGURATION_REQUIRED,
                "reason": blocked[0]["reason"].split(".")[0],
                "detail": " ".join(entry["reason"] for entry in blocked),
                "blocked": blocked}
    return {"status": SOURCE_READY, "reason": "", "detail": "", "blocked": []}


def multiview_capable(response):
    """Read a `config_get multiview` response as a capability answer.

    Success means the node exists and the device supports Multiview. "Config
    node not found" means it does not. A transport failure means neither -- it is
    unknown, and must not be cached as unsupported.
    """
    if not isinstance(response, dict):
        return None, "no response"
    if response.get("__unreachable__"):
        return None, str(response.get("error") or "device did not answer")
    if response.get("error"):
        message = str(response.get("error_message") or response.get("error") or "")
        if "not found" in message.lower():
            return False, message
        return None, message
    if isinstance(response.get("config"), list):
        return True, ""
    return None, "unrecognised response"


# --------------------------------------------------------------------------
# Multiview object naming
# --------------------------------------------------------------------------
# The decoder enforces: the name must start with "multiview", must not be
# exactly "multiview", and is case-sensitive.

NAME_PREFIX = "multiview"


def device_object_name(friendly, existing=()):
    """Turn a friendly layout name into a legal, non-colliding device name.

    The friendly name is what the operator sees and what OmniSuite stores; this
    is only the identifier the device will accept.
    """
    cleaned = re.sub(r"[^0-9A-Za-z]+", "", str(friendly or "")).strip()
    if not cleaned:
        cleaned = "View"
    cleaned = cleaned[0].upper() + cleaned[1:]
    candidate = NAME_PREFIX + cleaned
    taken = set(existing or ())
    if candidate not in taken:
        return candidate
    for suffix in range(2, 1000):
        numbered = candidate + str(suffix)
        if numbered not in taken:
            return numbered
    return candidate + str(len(taken) + 1)


def validate_object_name(name):
    """The device's own rules, applied before we send anything."""
    text = str(name or "")
    if not text.startswith(NAME_PREFIX):
        return False, 'Multiview name must start with "multiview"'
    if text == NAME_PREFIX:
        return False, 'Multiview name cannot be exactly "multiview"'
    return True, ""


# --------------------------------------------------------------------------
# Decoder ip_input allocation
# --------------------------------------------------------------------------

def _ip_input_number(name):
    match = re.search(r"(\d+)$", str(name or ""))
    return int(match.group(1)) if match else 10 ** 6


def classify_ip_inputs(ip_inputs, hdmi_output, multiviews=(), exclude_multiview=None):
    """Work out what each decoder ip_input is currently doing.

    The bench mapping (1 = Session 1 video, 3 = Session 1 audio, 5 = aux) is a
    site convention, not an API rule, and the vendor guide uses different numbers
    again. So nothing here is assumed: every role is read from the decoder's own
    current state.
    """
    hdmi_output = hdmi_output or {}
    video_input = str((hdmi_output.get("video") or {}).get("input") or "")
    audio_input = str((hdmi_output.get("audio") or {}).get("input") or "")
    aux_input = str((hdmi_output.get("aux") or {}).get("input") or "")

    used_by_multiview = {}
    for multiview in multiviews or ():
        if exclude_multiview and multiview.get("name") == exclude_multiview:
            continue
        for subframe in multiview.get("subframes") or ():
            source = str(subframe.get("input") or "")
            if source:
                used_by_multiview.setdefault(source, []).append(multiview.get("name"))

    classified = []
    for entry in ip_inputs or ():
        name = str(entry.get("name") or "")
        multicast = (entry.get("multicast") or {}).get("address") or ""
        roles = []
        if name and name == video_input:
            roles.append("HDMI video")
        if name and name == audio_input:
            roles.append("HDMI audio")
        if name and name == aux_input:
            roles.append("Aux")
        if name in used_by_multiview:
            roles.append("Multiview: " + ", ".join(
                str(n) for n in used_by_multiview[name] if n))
        classified.append({
            "name": name,
            "number": _ip_input_number(name),
            "enabled": bool(entry.get("enabled")),
            "address": multicast,
            "port": entry.get("port"),
            "roles": roles,
            # Audio, aux and inputs owned by another multiview are never
            # reallocated. An enabled input with no known role is someone else's
            # configuration and is also left alone.
            "reserved": bool(roles) or bool(entry.get("enabled")),
        })
    classified.sort(key=lambda item: item["number"])
    return classified


# --------------------------------------------------------------------------
# Encoder 2's physical input
# --------------------------------------------------------------------------
# Observed on the bench: two of four sources shipped with
# `vc2_encoder2.input = ""` -- the web application shows this as "Not used".
# Session 2 can then be assigned, enabled and given a destination, the device
# reports no error, the decoder's ip_input opens, and no video is ever
# produced. Configuring the session is not sufficient; the encoder has to be
# fed.
#
# For a dual-encoder source Encoder 2 encodes the same physical input as
# Encoder 1, so that is what an unused Encoder 2 is pointed at. An Encoder 2
# already on a *different* input is somebody's configuration and is reported
# rather than overwritten.

ENCODER_INPUT_OK = "ok"                    # already on the right input
ENCODER_INPUT_NOT_USED = "not_used"        # "Not used"; must be configured
ENCODER_INPUT_CONFLICT = "conflict"        # a different active input; refuse
ENCODER_INPUT_UNAVAILABLE = "unavailable"  # Encoder 1 has no input either


def classify_encoder_input(encoder1_input, encoder2_input):
    """Whether Encoder 2 is fed, and what to do about it.

    Returns {"status", "current", "wanted", "change_required", "reason"}.
    """
    wanted = str(encoder1_input or "").strip()
    current = str(encoder2_input or "").strip()
    if not wanted:
        return {"status": ENCODER_INPUT_UNAVAILABLE, "current": current,
                "wanted": "", "change_required": False,
                "reason": "Encoder 1 has no input either, so there is nothing "
                          "to follow. Set the source's input on the device."}
    if current == wanted:
        return {"status": ENCODER_INPUT_OK, "current": current, "wanted": wanted,
                "change_required": False, "reason": ""}
    if not current:
        return {"status": ENCODER_INPUT_NOT_USED, "current": "", "wanted": wanted,
                "change_required": True,
                "reason": "Encoder 2 is set to Not used and will be pointed at "
                          "%s, the input Encoder 1 uses." % wanted}
    return {"status": ENCODER_INPUT_CONFLICT, "current": current, "wanted": wanted,
            "change_required": False,
            "reason": "Encoder 2 is already encoding %s while Encoder 1 uses %s. "
                      "OmniSuite cannot prove that input is free, so it is left "
                      "alone. Point Encoder 2 at %s on the device, or use another "
                      "source for this window." % (current, wanted, wanted)}


# --------------------------------------------------------------------------
# The main window
# --------------------------------------------------------------------------
# Multiview is video-only: the compositor carries no audio at all. So the
# decoder's HDMI audio has to come from one of the sources, and the only
# defensible choice is the window the operator is looking at.
#
# Audio comes from that source's *Session 1* path, unchanged. Session 2 exists
# for the window video; adding a second audio stream to it would be a second
# thing to keep in step for no benefit.

def main_window_cell(layout_name):
    """The window that owns audio: the largest one, earliest on a tie.

    For every layout with an obvious large window that is the large window. For
    2x2 and Side-by-Side, where all windows are equal, the tie-break makes it
    Window 1 -- deterministic, and the same window every time for a given
    layout.
    """
    layout = LAYOUTS[layout_name]
    grid_w, grid_h = layout["grid"]
    best, best_area = None, -1
    for name, _cx, _cy, cw, ch, _anchor, _dx, _dy in layout["cells"]:
        area = (cw * ch) / float(grid_w * grid_h)
        if area > best_area:
            best, best_area = name, area
    return best


# --------------------------------------------------------------------------
# The Multiview decoder input pool
# --------------------------------------------------------------------------
# Four slots for Multiview video, not four window identities. They are handed
# out to distinct incoming streams in first-use order, so a layout with repeated
# sources uses fewer of them and the result is still deterministic. The odd
# inputs are left to the decoder's own roles -- the bench uses 1 for the primary
# video, 3 for its audio and 5 for aux -- so a Multiview never competes with
# them.

WINDOW_IP_INPUTS = ("ip_input2", "ip_input4", "ip_input6", "ip_input8")

# The pool is reserved for Multiview, so an input in it that OmniSuite cannot
# account for is a collision to report rather than something to overwrite.
IP_INPUT_COLLISION = "collision"


def window_ip_input(slot_index):
    """The pool input at slot N (0-based), or None beyond the fourth.

    A slot is not a window: `allocate_window_inputs` hands slots to distinct
    streams, so window 3 lands on slot 3 only when the first three windows draw
    three different streams.
    """
    if 0 <= slot_index < len(WINDOW_IP_INPUTS):
        return WINDOW_IP_INPUTS[slot_index]
    return None


def _describe_roles(roles):
    """What an input is doing, in the words the operator would use."""
    described = []
    for role in roles:
        if role.startswith("Multiview:"):
            described.append("used by the Multiview %s" % role.split(":", 1)[1].strip())
        else:
            described.append("the %s input" % role)
    return "it is " + " and ".join(described)


def stream_identity(address, port):
    """What makes two windows the same incoming stream.

    Not the encoder's hostname: one source can transmit several sessions, and a
    window is bound to a session's destination rather than to a device. What the
    decoder opens is a multicast address and a UDP port, and that pair is what it
    refuses to open twice -- so that pair is the identity.
    """
    address = str(address or "").strip()
    if not address:
        return None
    return (address, str(port))


def allocate_window_inputs(requirements, ip_inputs, hdmi_output, multiviews=(),
                           exclude_multiview=None, claimed=(), owned=()):
    """Bind each window to a pool input, allocating by UNIQUE stream.

    `requirements` is an ordered iterable of {"key", "index", "address", "port"}.

    The pool -- ip_input 2, 4, 6, 8 -- is a set of Multiview video slots, not a
    fixed window identity. Slots are handed out in order to *distinct* streams as
    they are first used, so the result is deterministic and reproducible from the
    saved configuration alone:

        W1=A W2=B W3=C W4=D  ->  A:2  B:4  C:6  D:8
        W1=A W2=A W3=B W4=C  ->  A:2  B:4  C:6, and ip_input8 is never touched

    Windows sharing a stream share its input, because the decoder cannot open one
    multicast address and port on two enabled inputs at once. Two windows are the
    same stream only when `stream_identity` agrees -- the destination address and
    port, never the hostname.

    `owned` are the pool inputs the currently active Multiview configuration is
    using, which OmniSuite may reconfigure freely. A saved Multiview that is not
    active owns nothing: its metadata is a description, not a reservation.

    Returns (assignments, errors, collisions).
    """
    hdmi_output = hdmi_output or {}
    classified = {item["name"]: item for item in
                  classify_ip_inputs(ip_inputs, hdmi_output, multiviews,
                                     exclude_multiview=exclude_multiview)}
    reconfigurable = set(claimed or ()) | set(owned or ())

    assignments, errors, collisions = {}, [], []
    placed = {}          # stream identity -> the assignment that opened it
    used = {}            # input name -> the stream this plan put on it

    # Pass one: a stream already arriving on an input keeps that input.
    #
    # This has to be settled before any slot is handed out. Resolving it window
    # by window let an earlier window's stream take, from the free pool, the
    # very input a later window's stream was already sitting on -- so the later
    # window was moved somewhere else for no reason. A live switch that
    # un-shared a stream did exactly that to the window it was not asked about.
    #
    # Each input can anchor only one stream, and the first window to want it
    # wins, so this stays deterministic.
    anchored = {}        # stream identity -> the input already carrying it
    for requirement in requirements:
        stream = stream_identity(requirement.get("address"),
                                 requirement.get("port"))
        if stream is None or stream in anchored:
            continue
        match = next((item for item in classified.values()
                      if item["enabled"]
                      and item["name"] not in anchored.values()
                      and stream_identity(item["address"], item["port"]) == stream),
                     None)
        if match is not None:
            anchored[stream] = match["name"]

    def next_slot():
        for name in WINDOW_IP_INPUTS:
            if name not in used and name not in anchored.values():
                return name
        return None

    for requirement in requirements:
        index = int(requirement.get("index") or 0)
        address = str(requirement.get("address") or "")
        port = requirement.get("port")
        stream = stream_identity(address, port)

        if stream is not None and stream in placed:
            # Same stream as an earlier window. It shares that input; no second
            # slot is consumed and nothing further is written.
            shared = dict(placed[stream])
            shared["shared_with"] = shared["ip_input"]
            assignments[requirement["key"]] = shared
            continue

        # An input anywhere on the decoder that already carries this exact
        # stream is used as it stands, pool or not: the hardware will not open
        # the address twice, and a subframe only references an input.
        #
        # An input this plan has already given to a *different* stream is not a
        # candidate, however. Recalling one Multiview after another routinely
        # asks for a stream that the outgoing configuration had on the very
        # input the incoming one is about to repoint, and matching against the
        # state as read would put two different sources on one input -- which is
        # what a live recall matrix caught doing exactly that.
        existing = (classified.get(anchored[stream])
                    if stream is not None and stream in anchored
                    and anchored[stream] not in used else None)
        if existing is not None:
            assignment = {"ip_input": existing["name"], "reused": True,
                          "already_enabled": True, "address": address,
                          "port": port, "shared_with": None,
                          "outside_pool": existing["name"] not in WINDOW_IP_INPUTS}
            assignments[requirement["key"]] = assignment
            placed[stream] = assignment
            used[existing["name"]] = stream
            continue

        name = next_slot()
        if name is None:
            errors.append(
                "This Multiview needs more than %d distinct streams, which is "
                "more than the %d reserved Multiview inputs."
                % (len(WINDOW_IP_INPUTS), len(WINDOW_IP_INPUTS)))
            continue
        entry = classified.get(name)
        if entry is None:
            errors.append("The decoder does not have %s, so window %d cannot be "
                          "placed." % (name, index + 1))
            continue

        # The pool is reserved for Multiview, but "reserved" is a rule OmniSuite
        # states rather than one the device enforces. What matters is whether the
        # slot is carrying live configuration OmniSuite cannot account for --
        # another Multiview's *saved metadata* referencing it is not that. A
        # saved Multiview is a description of a desired configuration; only what
        # is enabled on the decoder right now is a resource in use.
        if name not in reconfigurable:
            blocking = [role for role in entry["roles"]
                        if not role.startswith("Multiview")]
            if blocking:
                reason = _describe_roles(blocking)
            elif entry["enabled"] and entry["address"]:
                reason = ("it is enabled and carrying %s:%s, which OmniSuite did "
                          "not configure" % (entry["address"], entry["port"]))
            else:
                reason = ""
            if reason:
                collisions.append({"ip_input": name, "window": index + 1,
                                   "reason": reason})
                errors.append(
                    "Window %d needs a Multiview input, but %s is unavailable: "
                    "%s. OmniSuite will not overwrite it."
                    % (index + 1, name, reason))
                continue

        used[name] = stream
        assignment = {
            "ip_input": name, "reused": False,
            "already_enabled": bool(entry["enabled"]),
            "address": address, "port": port, "shared_with": None,
            "outside_pool": False,
        }
        assignments[requirement["key"]] = assignment
        if stream is not None:
            placed[stream] = assignment
    return assignments, errors, collisions


# Measured on the bench: one decoder ip_input drives at most two subframes.
# With three or four windows referencing one input, exactly two showed and the
# rest stayed dark -- no error, no status, the stream arriving at 16,355
# packets/s throughout, and the decoder's own Input status reading active
# because the other windows were carrying it. A saved Multiview that is not on
# the output does not consume the budget; only the one being displayed does.
MAX_WINDOWS_PER_STREAM = 2


def check_stream_window_limit(windows, errors):
    """Refuse a layout that asks one stream to fill more windows than it can.

    The decoder accepts it and silently leaves the extra windows black, so
    nothing downstream would notice. Splitting across two inputs is not a way
    out: the hardware will not open one multicast address and port twice.
    """
    per_stream = {}
    for window in windows:
        source = window.get("source") or {}
        multicast = window.get("multicast") or {}
        stream = stream_identity(multicast.get("address"), multicast.get("port"))
        if stream is None or source.get("error"):
            continue
        per_stream.setdefault(stream, []).append(window)

    for stream, group in sorted(per_stream.items(), key=lambda item: item[0]):
        if len(group) <= MAX_WINDOWS_PER_STREAM:
            continue
        hostname = (group[0].get("source") or {}).get("hostname") or stream[0]
        errors.append(
            "%s is assigned to %d windows (%s), and a decoder input drives at "
            "most %d. The extra windows would stay black with no error from the "
            "device. Use a different source for %s, or choose a layout with "
            "fewer windows on it."
            % (hostname, len(group), ", ".join(w["cell"] for w in group),
               MAX_WINDOWS_PER_STREAM,
               ", ".join(w["cell"] for w in group[MAX_WINDOWS_PER_STREAM:])))


def unique_streams(windows):
    """The distinct streams a Multiview presents to the decoder, in window order.

    Bandwidth, ip_input allocation and the decoder's own load are all counted
    from this and never from the window count: four windows showing one camera
    are one stream arriving once.
    """
    seen, order = set(), []
    for window in windows:
        multicast = window.get("multicast") or {}
        stream = stream_identity(multicast.get("address"), multicast.get("port"))
        if stream is None or stream in seen:
            continue
        seen.add(stream)
        order.append({"stream": stream, "address": multicast.get("address"),
                      "port": multicast.get("port"),
                      "source_ip": (window.get("source") or {}).get("ip"),
                      "window": window.get("cell")})
    return order

# --------------------------------------------------------------------------
# Bandwidth
# --------------------------------------------------------------------------
# Two budgets, both 900 Mb/s, and they constrain different things:
#
#   source   -- Encoder 1 + Encoder 2 on one encoder must fit in 900
#   decoder  -- the sum of the *unique* streams arriving for a Multiview must
#               fit in 900
#
# The same stream in four windows is one stream at the decoder and one encoder
# at the source, so it is counted once in both.
#
# The device's own ceiling was measured rather than assumed: `vc2_encoder2`
# accepts 20 through 900 Mb/s and refuses 10 and 901 with "Invalid bitrate",
# which is the one place in this subsystem where the device does reject a bad
# value rather than silently keeping it.

SOURCE_VIDEO_BUDGET = 900
DECODER_MULTIVIEW_BUDGET = 900

ENCODER2_MIN_BITRATE = 20
ENCODER2_MAX_BITRATE = 900

# Every 1080p layout was verified end to end at this rate, so it is the rate a
# small window is given and the one a starved source falls back towards.
MULTIVIEW_FLOOR_BITRATE = 150

# ---- Encoder 2 targets, by the role a window plays in its layout ----------
#
# Stated, not derived. The previous allocator shared the decoder's 900 Mb/s out
# by window area, which made two identical Side-by-Side windows "entitled" to
# 450 Mb/s each and turned every real number into a reduction from a figure that
# meant nothing on its own.
#
# Three roles, decided by geometry so no layout has to be listed by name:
#
#   every window the same size   ->  EQUAL   (Side-by-Side, 2x2, and any future
#                                             layout of equal windows)
#   the largest window           ->  MAIN    (PiP main, 1+3 main, 4-split main)
#   the rest                     ->  SMALL   (insets and the 1+3 / 4-split trio)
#
# 900 is a ceiling, not a target: 2x2 asks for 800 of it and 1+3 for 750.
EQUAL_WINDOW_TARGET = 200
MAIN_WINDOW_TARGET = 300
SMALL_WINDOW_TARGET = MULTIVIEW_FLOOR_BITRATE

WINDOW_ROLE_EQUAL = "equal"
WINDOW_ROLE_MAIN = "main"
WINDOW_ROLE_SMALL = "small"

ROLE_TARGETS = {
    WINDOW_ROLE_EQUAL: EQUAL_WINDOW_TARGET,
    WINDOW_ROLE_MAIN: MAIN_WINDOW_TARGET,
    WINDOW_ROLE_SMALL: SMALL_WINDOW_TARGET,
}


def window_roles(sizes):
    """The role of each window, from the sizes alone.

    `sizes` is an ordered iterable of (width, height). Returns a list of roles
    in the same order.

    Deciding by size rather than by cell name is what guarantees the two rules
    that matter: windows of equal size always get equal targets, and a window
    that occupies substantially more of the display never gets less than one
    that occupies less.
    """
    sizes = [(int(w), int(h)) for w, h in sizes]
    if not sizes:
        return []
    distinct = {size for size in sizes}
    if len(distinct) == 1:
        return [WINDOW_ROLE_EQUAL] * len(sizes)
    largest = max(distinct, key=lambda size: size[0] * size[1])
    return [WINDOW_ROLE_MAIN if size == largest else WINDOW_ROLE_SMALL
            for size in sizes]


def window_targets(sizes):
    """The Encoder-2 target for each window, in Mb/s."""
    return [ROLE_TARGETS[role] for role in window_roles(sizes)]

# Bitrates are whole numbers of Mb/s on the device; a step keeps the arithmetic
# legible in the plan the operator reads and keeps the sum below the budget by
# construction.
BITRATE_STEP = 10


def _floor_to_step(value, step=BITRATE_STEP):
    return int(value // step) * step


def allocate_bitrates(streams):
    """Encoder-2 bitrates for one Multiview: the layout target, capped by the
    source's own headroom.

    `streams` is one entry per UNIQUE source:
    {"source_ip", "target", "encoder1_bitrate"}. A source in several windows
    encodes once, so its target is the highest of the roles it plays -- there is
    one Encoder 2 and one bitrate behind all of them.

        headroom = 900 - current Encoder 1 bitrate
        actual   = min(layout target, headroom)

    **Encoder 1 is read and never written.** It carries the source's primary
    stream and other decoders are watching it; Phase 7B measured that changing
    its bitrate blacks every one of them for about half a second. Giving a
    Multiview window 150 Mb/s instead of 200 is a far smaller price than that,
    so a source with little headroom simply gets a smaller Multiview stream.

    Each allocation carries the arithmetic that produced it, so the UI can say
    "the target is 200, this source has 150 to spare" rather than presenting a
    number with no explanation.
    """
    streams = list(streams or ())
    if not streams:
        return []

    allocations = []
    for stream in streams:
        target = int(stream.get("target") or EQUAL_WINDOW_TARGET)
        encoder1 = stream.get("encoder1_bitrate")
        encoder1 = int(encoder1) if isinstance(encoder1, (int, float)) else None
        headroom = (SOURCE_VIDEO_BUDGET - encoder1) if encoder1 is not None else None

        bitrate, capped_by, error = target, "", ""
        if headroom is not None and headroom < target:
            bitrate = _floor_to_step(headroom)
            if bitrate < ENCODER2_MIN_BITRATE:
                # Nothing usable is left. Encoder 1 is still not touched: a
                # source with no room to spare is a source that cannot be used
                # for Multiview right now, and saying so is more useful than
                # quietly degrading the feed other people are watching.
                bitrate, error = None, (
                    "Encoder 1 on this source is at %d Mb/s, leaving %d of its "
                    "%d Mb/s budget -- less than the %d Mb/s Encoder 2 needs to "
                    "run. Lower Encoder 1 on the device if this source is needed "
                    "for Multiview."
                    % (encoder1, headroom, SOURCE_VIDEO_BUDGET,
                       ENCODER2_MIN_BITRATE))
            else:
                capped_by = ("Encoder 1 is at %d Mb/s, leaving %d of the source's "
                             "%d Mb/s" % (encoder1, headroom, SOURCE_VIDEO_BUDGET))
        if bitrate is not None:
            bitrate = max(min(int(bitrate), ENCODER2_MAX_BITRATE),
                          ENCODER2_MIN_BITRATE)

        allocations.append({
            "source_ip": stream.get("source_ip"),
            "target": target,
            "role": stream.get("role"),
            "area": stream.get("area"),
            "bitrate": bitrate,
            "capped_by": capped_by,
            "encoder1_bitrate": encoder1,
            # Kept as None throughout: Encoder 1 is never a target of this
            # planner. The key remains so nothing downstream has to change.
            "encoder1_target": None,
            "headroom": headroom,
            "error": error,
        })
    return allocations


def decoder_aggregate(allocations):
    """What the decoder is asked to receive, counting each stream once."""
    return sum(int(a.get("bitrate") or 0) for a in allocations
               if a.get("bitrate") is not None)


# --------------------------------------------------------------------------
# Decoder interlocks
# --------------------------------------------------------------------------
# Two decoder features are incompatible with Multiview. Neither is ever turned
# off automatically: both are deliberate operator configuration, and silently
# undoing one to make a Multiview work would be a worse outcome than refusing.

VIDEO_WALL_BLOCKED = "Multiview is unavailable while Video Wall is enabled on this decoder."


def video_wall_enabled(hdmi_output):
    return bool((((hdmi_output or {}).get("video") or {}).get("output") or {})
                .get("wall", {}).get("enabled"))


def fast_switching_enabled(hdmi_output):
    """Fast Switching, which the device exposes as the output's `fsm` block."""
    return bool((((hdmi_output or {}).get("video") or {}).get("output") or {})
                .get("fsm", {}).get("enabled"))


DECODER_FAMILY_4XXX = "4xxx"
DECODER_FAMILY_1XX = "1xx"


def decoder_family(model):
    """'4xxx', '1xx' or None, from the model identity and nothing else.

    An address or a hostname is not a product identity, so neither is consulted.
    A model the table does not recognise returns None, and the caller treats
    that as unproven rather than as either family.
    """
    text = normalize_model(model)
    if not text:
        return None
    segment = text.split("-")[-1].lstrip("d")
    if not segment.isdigit():
        return None
    if len(segment) == 4 and segment[0] == "4":
        return DECODER_FAMILY_4XXX
    if len(segment) == 3 and segment[0] == "1":
        return DECODER_FAMILY_1XX
    return None


def fast_switching_blocks(model, hdmi_output):
    """Whether Fast Switching bars Multiview on this decoder. (blocked, reason)

    A 4xxx decoder composites a Multiview with Fast Switching on; a 1xx decoder
    does not. A family that cannot be established is refused while the feature
    is on, because a safety gate with no evidence behind it is not a gate.
    """
    if not fast_switching_enabled(hdmi_output):
        return False, ""
    family = decoder_family(model)
    if family == DECODER_FAMILY_4XXX:
        return False, ""
    if family == DECODER_FAMILY_1XX:
        return True, ("Multiview is unavailable while Fast Switching is enabled "
                      "on a 1xx decoder. Turn Fast Switching off on the device "
                      "if you want to use Multiview.")
    return True, ("Fast Switching is enabled and OmniSuite cannot establish this "
                  "decoder's family from its model (%s), so it will not risk a "
                  "Multiview on it." % (model or "unknown"))


def interlocks(model, hdmi_output):
    """Every reason this decoder may not be given a Multiview right now."""
    blocked = []
    if video_wall_enabled(hdmi_output):
        blocked.append({"interlock": "video_wall", "reason": VIDEO_WALL_BLOCKED})
    barred, reason = fast_switching_blocks(model, hdmi_output)
    if barred:
        blocked.append({"interlock": "fast_switching", "reason": reason})
    return blocked

# --------------------------------------------------------------------------
# ip_input reclamation
# --------------------------------------------------------------------------
# Phase 4 left this open: inputs OmniSuite allocated accumulated as layouts
# changed, because nothing could prove it was safe to take them back.
#
# Ownership is provable for exactly one case: an input OmniSuite allocated from
# the free pool for a Multiview it manages. The allocator already distinguishes
# that from a *shared* input -- one that was already carrying the wanted stream
# and may also be the HDMI video or audio source -- and a shared input is never
# owned, however it came to be used.
#
# Everything else is left alone. Safety beats tidiness, and an input wrongly
# disabled is a black window on someone's display.

def reclaimable_inputs(claimed, required, multiviews, hdmi_output, ip_inputs,
                       object_name=None):
    """Inputs OmniSuite may safely disable, with the reason it may.

    `claimed`  : {name: address} OmniSuite allocated from the free pool for this
                 Multiview, from its own metadata.
    `required` : names the new configuration still needs.
    Returns (reclaimable, kept) where `kept` explains every one it declined.
    """
    required = set(required or ())
    hdmi_output = hdmi_output or {}
    roles = {item["name"]: item for item in
             classify_ip_inputs(ip_inputs, hdmi_output, multiviews,
                                exclude_multiview=object_name)}
    # Every input any Multiview on the device still points at, including ones
    # OmniSuite does not manage.
    referenced = set()
    for multiview in multiviews or ():
        if object_name and multiview.get("name") == object_name:
            continue
        for subframe in multiview.get("subframes") or ():
            if subframe.get("input"):
                referenced.add(str(subframe["input"]))

    reclaimable, kept = [], []
    for name, address in sorted((claimed or {}).items(),
                                key=lambda item: _ip_input_number(item[0])):
        entry = roles.get(name)
        if name in required:
            kept.append((name, "still required by this Multiview"))
            continue
        if entry is None:
            kept.append((name, "not present on the decoder"))
            continue
        if name in referenced:
            kept.append((name, "another Multiview still uses it"))
            continue
        other_roles = [r for r in entry["roles"] if not r.startswith("Multiview")]
        if other_roles:
            kept.append((name, "it is the " + " and ".join(other_roles)))
            continue
        if address and entry["address"] and entry["address"] != address:
            # Something repointed it since. It is no longer the input we
            # allocated, so the claim no longer applies.
            kept.append((name, "it now carries %s, not %s"
                         % (entry["address"], address)))
            continue
        reclaimable.append(name)
    return reclaimable, kept

# --------------------------------------------------------------------------
# Shared-scaler ownership
# --------------------------------------------------------------------------
# A vc2 scaler belongs to the *encoder*, not to the session or the stream, so
# every decoder consuming that encoder's session sees the same window size.
# Retuning one is a fan-out mutation that the device reports no error for.

SCALER_SAFE = "safe"                       # already correct; nothing to write
SCALER_CHANGE = "change"                   # must change, no known conflict
SCALER_CONFLICT = "conflict"               # another known Multiview wants another size
SCALER_UNKNOWN_EXTERNAL = "unknown_external"  # cannot prove there are no other consumers


def _format_of(scaler):
    scaler = scaler or {}
    if not scaler.get("enable"):
        return None
    return "%sx%s" % (scaler.get("width"), scaler.get("height"))


def classify_scaler(current_scaler, desired_width, desired_height, encoder_index,
                    other_consumers=(), input_resolution=None):
    """Decide whether an encoder's scaler may be written, and how loudly to say so.

    `other_consumers` are the Multiviews OmniSuite already knows about that draw
    on this same encoder. OmniSuite can only reason about what it knows, so a
    change that looks locally safe is still reported as unproven rather than
    silently applied.
    """
    desired = "%dx%d" % (desired_width, desired_height)
    current = _format_of(current_scaler)

    # Encoder 1 passing through natively already produces the wanted size.
    passthrough_ok = (encoder_index == 1 and current is None
                      and input_resolution == desired)
    if current == desired or passthrough_ok:
        return {"status": SCALER_SAFE, "current": current, "desired": desired,
                "change_required": False, "conflicts": [],
                "passthrough": passthrough_ok}

    disagreeing = [c for c in other_consumers
                   if c.get("scaler_format") and c.get("scaler_format") != desired]
    if disagreeing:
        return {"status": SCALER_CONFLICT, "current": current, "desired": desired,
                "change_required": True, "conflicts": disagreeing, "passthrough": False}
    status = SCALER_CHANGE if other_consumers else SCALER_UNKNOWN_EXTERNAL
    return {"status": status, "current": current, "desired": desired,
            "change_required": True, "conflicts": [], "passthrough": False}


# --------------------------------------------------------------------------
# The resource planner
# --------------------------------------------------------------------------
# Every encoder / session / scaler / ip_input / multicast decision is made here
# and nowhere else. The UI renders what this returns; the apply transaction
# executes what this returns.

STAGE_IP_INPUT_DISABLE = "ip_input_disable"
STAGE_ENCODER_INPUT = "encoder_input"
STAGE_ENCODER_SCALER = "encoder_scaler"
STAGE_ENCODER_BITRATE = "encoder_bitrate"
STAGE_SESSION = "session"
STAGE_SESSION_SAP = "session_sap"
STAGE_IP_INPUT = "ip_input"
STAGE_MULTIVIEW_PRUNE = "multiview_prune"
STAGE_MULTIVIEW = "multiview"

# The order Apply executes in. Inputs are taken down first so a window never
# shows torn video mid-change; Encoder 2's physical input comes before anything
# that depends on it producing video; the Multiview object is written last.
#
# There is no HDMI stage here on purpose. Everything the operator can see or
# hear -- the selected input, the output resolution, the audio source -- belongs
# to Show, so that saving a layout can never change the picture.
STAGE_ORDER = (STAGE_IP_INPUT_DISABLE, STAGE_ENCODER_INPUT, STAGE_ENCODER_SCALER,
               STAGE_ENCODER_BITRATE, STAGE_SESSION, STAGE_SESSION_SAP,
               STAGE_IP_INPUT, STAGE_MULTIVIEW_PRUNE, STAGE_MULTIVIEW)


def _session_stream(session, kind="video"):
    return ((session or {}).get(kind) or {}).get("stream") or {}


def _by_name(items):
    return {str(item.get("name") or ""): item for item in (items or [])}


def plan_multiview(desired, decoder_state, encoder_states,
                   known_multiviews=(), slice_info=None, managed=(),
                   claimed_inputs=()):
    """Turn a desired Multiview into a verified, ordered mutation plan.

    Nothing here writes. The result carries everything the UI needs to explain
    the change and everything Apply needs to perform it, so the two can never
    describe different work.

    `desired`     : {layout, canvas, assignments {cell: source_ip}, name,
                     object_name}
    `decoder_state`: {ip, model, hostname, ip_input, hdmi_output, multiview}
    `encoder_states`: {source_ip: {device, vc2, sessions, input_resolution}}
    `known_multiviews`: OmniSuite's record of Multiviews on other decoders, used
                     for scaler-conflict detection.
    `managed`     : the Multiviews OmniSuite manages on *this* decoder.
    `claimed_inputs`: reserved-pool inputs this Multiview already owns, so
                     editing it does not read its own inputs as a collision.
    """
    slice_width = ((slice_info or {}).get("width") or {}).get("min") or SLICE_WIDTH
    slice_height = ((slice_info or {}).get("height") or {}).get("min") or SLICE_HEIGHT

    layout_name = desired.get("layout")
    canvas_id = desired.get("canvas") or ACTIVE_CANVAS
    errors, warnings, conflicts = [], [], []

    if layout_name not in LAYOUTS:
        errors.append("Unknown layout %r." % layout_name)
    # The active product path is 1920x1080 and nothing else. This is enforced
    # here, in the planner, and not merely by what the UI offers: a 4K or 1440p
    # canvas cannot be planned even by calling this function directly.
    if canvas_id != ACTIVE_CANVAS:
        preset = canvas_preset(canvas_id)
        errors.append(
            "Multiview runs at %s in this release. %s is not available.%s"
            % (ACTIVE_CANVAS, canvas_id,
               (" " + preset["note"]) if preset and preset.get("note") else ""))
    if errors:
        return {"ok": False, "errors": errors, "warnings": [], "conflicts": [],
                "windows": [], "mutations": [], "activation": [],
                "unique_streams": [], "snapshot": []}
    preset = canvas_preset(canvas_id)

    hdmi_output = decoder_state.get("hdmi_output") or {}

    # Video Wall and Fast Switching are checked before anything else is worked
    # out, so a blocked decoder produces a plan with no mutations in it at all
    # rather than one that merely refuses to run.
    blocked = interlocks(decoder_state.get("model"), hdmi_output)
    for entry in blocked:
        errors.append(entry["reason"])
    if blocked:
        return {"ok": False, "errors": errors, "warnings": [], "conflicts": [],
                "interlocks": blocked, "windows": [], "mutations": [],
                "activation": [], "unique_streams": [], "snapshot": []}

    geometry = compute_windows(layout_name, preset["width"], preset["height"],
                               slice_width, slice_height)
    if geometry["snapped"]:
        warnings.append(
            "The canvas for %s is %dx%d, not %s: it is snapped so it divides "
            "evenly into the layout grid. The display still runs at %s."
            % (LAYOUTS[layout_name]["label"], geometry["canvas"]["width"],
               geometry["canvas"]["height"], canvas_id, ACTIVE_CANVAS))

    existing_multiviews = list(decoder_state.get("multiview") or [])
    existing_names = {str(m.get("name") or "") for m in existing_multiviews}
    object_name = desired.get("object_name") or device_object_name(
        desired.get("name") or layout_name,
        existing_names if not desired.get("update_existing") else ())
    valid, message = validate_object_name(object_name)
    if not valid:
        errors.append(message)

    updating = object_name in existing_names

    # A decoder holds as many saved Multiviews as the operator wants; only one
    # of them is ever on the output. A saved configuration is a description, not
    # a reservation, so nothing here refuses a second one.
    if updating and not desired.get("update_existing"):
        errors.append(
            "The decoder already has a Multiview called %s. Choose Update to "
            "modify it, or give this one a different name." % object_name)
    if not updating and len(existing_multiviews) and desired.get("update_existing"):
        errors.append("No Multiview called %s exists on this decoder." % object_name)

    assignments = desired.get("assignments") or {}
    main_cell = main_window_cell(layout_name)
    windows, requirements = [], []

    for window in geometry["windows"]:
        cell = window["cell"]
        source_ip = assignments.get(cell)
        encoder_index = required_encoder(window["width"], window["height"])
        entry = {
            "cell": cell,
            "label": subframe_label(cell, window["width"], window["height"]),
            "x": window["x"], "y": window["y"], "anchor": window["anchor"],
            "width": window["width"], "height": window["height"],
            "area": window["width"] * window["height"],
            "priority": window["priority"],
            "index": window["index"],
            "window_number": window["index"] + 1,
            "is_main": cell == main_cell,
            "encoder_index": encoder_index,
            "encoder": encoder_object_name(encoder_index),
            "session": session_for_encoder(encoder_index),
            "scaler_format": "%dx%d" % (window["width"], window["height"]),
            "scaler_supported": scaler_format_supported(
                window["width"], window["height"], encoder_index),
            "reserved_ip_input": window_ip_input(window["index"]),
            "source": None, "ip_input": None, "multicast": None, "scaler": None,
            "encoder_input": None, "bitrate": None,
        }
        if not entry["scaler_supported"]:
            # Never silently promote the window to Encoder 1: that is the whole
            # point of the Encoder-2 rule.
            errors.append(
                "Window %s needs a %s source, which Encoder 2 cannot produce. "
                "This layout is not available at %s."
                % (cell, entry["scaler_format"], canvas_id))
        if source_ip:
            entry["source"] = _plan_source(entry, source_ip, encoder_states,
                                           known_multiviews, errors, warnings,
                                           conflicts, decoder_state)
            if entry["source"] and entry["source"].get("multicast"):
                entry["multicast"] = entry["source"]["multicast"]
                requirements.append({"key": cell, "index": window["index"],
                                     "address": entry["multicast"]["address"],
                                     "port": entry["multicast"]["port"]})
        windows.append(entry)

    if not any(w["source"] for w in windows):
        errors.append("Assign at least one source before applying.")

    _check_intra_plan_scaler_conflicts(windows, conflicts)
    check_stream_window_limit(windows, errors)

    ip_assignments, ip_errors, collisions = allocate_window_inputs(
        requirements, decoder_state.get("ip_input"), hdmi_output,
        existing_multiviews, exclude_multiview=object_name,
        claimed=claimed_inputs, owned=desired.get("owned_inputs") or ())
    errors.extend(ip_errors)
    for window in windows:
        window["ip_input"] = ip_assignments.get(window["cell"])

    bandwidth = _plan_bandwidth(windows, warnings, errors)

    _check_multicast_collisions(windows, decoder_state, warnings)

    audio = _plan_audio(windows, main_cell, encoder_states, warnings)

    mutations, activation, snapshot = _build_mutations(
        desired, object_name, updating, geometry, windows, decoder_state,
        encoder_states, warnings)

    classified = classify_ip_inputs(decoder_state.get("ip_input"), hdmi_output,
                                    existing_multiviews,
                                    exclude_multiview=object_name)

    return {
        "ok": not errors,
        "object_name": object_name,
        "friendly_name": desired.get("name") or LAYOUTS[layout_name]["label"],
        "updating": updating,
        "layout": layout_name,
        "canvas": geometry["canvas"],
        "canvas_id": canvas_id,
        "requested_canvas": geometry["requested"],
        "output_resolution": ACTIVE_CANVAS,
        "snapped": geometry["snapped"],
        "main_window": main_cell,
        "audio": audio,
        "bandwidth": bandwidth,
        "windows": windows,
        "ip_inputs": classified,
        "ip_input_collisions": collisions,
        "interlocks": [],
        "mutations": mutations,
        "activation": activation,
        "unique_streams": unique_streams(windows),
        "snapshot": snapshot,
        "errors": errors,
        "warnings": warnings,
        "conflicts": conflicts,
        "requires_confirmation": bool(mutations or activation),
    }


def _plan_bandwidth(windows, warnings, errors):
    """Give every unique source an Encoder-2 bitrate inside both budgets.

    One source encodes once however many windows it feeds, so the allocation is
    per source and the window that decides it is the largest one that source
    appears in.
    """
    # Keyed on the stream the decoder actually opens -- its multicast address
    # and port -- not on the encoder's hostname. One source transmits several
    # sessions, and it is the destination the decoder refuses to open twice.
    # For Multiview the two coincide, because every window is that source's
    # Session 2, and keying on the stream is what keeps that an observation
    # rather than an assumption.
    largest, order, stream_of = {}, [], {}
    for window in windows:
        source = window.get("source") or {}
        ip = source.get("ip")
        multicast = window.get("multicast") or {}
        stream = stream_identity(multicast.get("address"), multicast.get("port"))
        if not ip or source.get("error") or stream is None:
            continue
        stream_of[ip] = stream
        if ip not in largest:
            largest[ip] = window
            order.append(ip)
        elif window["area"] > largest[ip]["area"]:
            largest[ip] = window

    # The role each window plays, from the geometry of the layout it is in.
    # Equal-sized windows get equal targets by construction; the largest window
    # gets the main target. Nothing here looks at cell names or window order.
    roles = window_roles([(w["width"], w["height"]) for w in windows])
    for window, role in zip(windows, roles):
        window["bitrate_role"] = role
        window["bitrate_target"] = ROLE_TARGETS[role]

    # One Encoder 2 per source, so a source used in two roles is planned at the
    # higher of them -- there is a single physical stream behind both windows.
    target_of = {}
    for window in windows:
        ip = (window.get("source") or {}).get("ip")
        if not ip:
            continue
        target_of[ip] = max(target_of.get(ip, 0), window["bitrate_target"])

    streams = [{"source_ip": ip,
                "target": target_of.get(ip, EQUAL_WINDOW_TARGET),
                "role": largest[ip].get("bitrate_role"),
                "area": largest[ip]["area"],
                "encoder1_bitrate": (largest[ip]["source"] or {}).get("encoder1_bitrate")}
               for ip in order]
    allocations = allocate_bitrates(streams)
    by_source = {a["source_ip"]: a for a in allocations}

    for window in windows:
        source = window.get("source") or {}
        allocation = by_source.get(source.get("ip"))
        if not allocation:
            continue
        window["bitrate"] = allocation["bitrate"]
        window["bitrate_target"] = allocation["target"]
        window["bitrate_headroom"] = allocation.get("headroom")
        window["bitrate_capped_by"] = allocation["capped_by"]
        window["decides_bitrate"] = largest.get(source.get("ip")) is window
        window["encoder1_target"] = None

    for allocation in allocations:
        if allocation.get("error"):
            errors.append("%s cannot be used: %s"
                          % (allocation["source_ip"], allocation["error"]))
        elif allocation.get("capped_by"):
            # Say what the target was and why this source cannot meet it. The
            # old wording blamed "its window size", which was both wrong and
            # impossible to act on.
            warnings.append(
                "Encoder 2 on %s is set to %s Mb/s rather than the %s Mb/s "
                "target for this window: %s. Encoder 1 is left alone -- it "
                "carries the source's primary stream."
                % (allocation["source_ip"], allocation["bitrate"],
                   allocation["target"], allocation["capped_by"]))

    # One bitrate per distinct stream, however many windows show it. Four
    # windows on one camera are one stream arriving once and are counted once.
    counted, aggregate = set(), 0
    for allocation in allocations:
        stream = stream_of.get(allocation["source_ip"])
        if stream is None or stream in counted:
            continue
        counted.add(stream)
        aggregate += int(allocation.get("bitrate") or 0)
    if aggregate > DECODER_MULTIVIEW_BUDGET:
        # The explicit targets cannot reach this (2x2 asks for 800, 1+3 for
        # 750), but the budget and the targets are separate numbers and this is
        # what keeps them connected.
        errors.append(
            "This Multiview would ask the decoder for %d Mb/s across %d unique "
            "stream(s), above its %d Mb/s limit."
            % (aggregate, len(counted), DECODER_MULTIVIEW_BUDGET))

    return {"budget_source": SOURCE_VIDEO_BUDGET,
            "budget_decoder": DECODER_MULTIVIEW_BUDGET,
            "floor": MULTIVIEW_FLOOR_BITRATE,
            "targets": {"equal": EQUAL_WINDOW_TARGET, "main": MAIN_WINDOW_TARGET,
                        "small": SMALL_WINDOW_TARGET},
            "target_total": sum(int(a.get("target") or 0) for a in allocations),
            "unique_streams": len(counted),
            "window_count": len(windows),
            "decoder_aggregate": aggregate,
            "allocations": allocations}


def _plan_audio(windows, main_cell, encoder_states, warnings):
    """Where the decoder's HDMI audio comes from once this Multiview is shown.

    Multiview carries no audio, so it follows the main window's source over that
    source's ordinary Session 1 path. Applying it belongs to Show, not to Save --
    it changes what the operator hears -- so this only describes it.
    """
    window = next((w for w in windows if w["cell"] == main_cell), None)
    source = (window or {}).get("source") or {}
    if not window or not source.get("ip") or source.get("error"):
        return {"main_window": main_cell, "source_ip": None, "session": "session1",
                "available": False,
                "reason": "The main window (%s) has no usable source, so the "
                          "display's audio is left as it is." % main_cell}
    sessions = _by_name((encoder_states.get(source["ip"]) or {}).get("sessions"))
    stream = ((sessions.get("session1") or {}).get("audio") or {}).get("stream") or {}
    address = str(stream.get("destination_address") or "")
    if not address:
        warnings.append(
            "%s Session 1 has no audio destination, so Multiview audio cannot "
            "follow the main window." % source.get("hostname"))
        return {"main_window": main_cell, "source_ip": source["ip"],
                "session": "session1", "available": False,
                "reason": "%s Session 1 has no audio destination."
                          % source.get("hostname")}
    return {"main_window": main_cell,
            "source_ip": source["ip"],
            "source_hostname": source.get("hostname"),
            "session": "session1",
            "available": True,
            "enabled": bool(stream.get("enabled")),
            "address": address,
            "port": stream.get("destination_port"),
            "reason": ""}


def _plan_source(window, source_ip, encoder_states, known_multiviews,
                 errors, warnings, conflicts, decoder_state):
    """Resolve one window's encoder, session, scaler, input and multicast."""
    state = (encoder_states or {}).get(source_ip) or {}
    device = state.get("device") or {}
    source = {
        "ip": source_ip,
        "hostname": device.get("hostname") or source_ip,
        "model": device.get("model") or "",
        "mac": device.get("mac") or "",
        "reachable": bool(state.get("reachable", True)),
    }
    if not state or not state.get("sessions"):
        errors.append(
            "Encoder %s did not answer, so its Session %d cannot be prepared for "
            "window %s." % (source["hostname"], window["encoder_index"], window["cell"]))
        source["error"] = "unreachable"
        return source

    sessions = _by_name(state.get("sessions"))
    encoders = _by_name(state.get("vc2"))
    session = sessions.get(window["session"]) or {}
    encoder = encoders.get(window["encoder"]) or {}
    primary = encoders.get(encoder_object_name(1)) or {}

    if not encoder:
        errors.append(
            "%s has no %s, so it cannot feed a Multiview window."
            % (source["hostname"], window["encoder"]))
        source["error"] = "no encoder 2"
        return source

    stream = _session_stream(session, "video")
    address = str(stream.get("destination_address") or "")
    port = stream.get("destination_port")
    if not address:
        errors.append(
            "Encoder %s %s has no stream destination. Configure it on the device "
            "(it generates its own default) before using it in a Multiview."
            % (source["hostname"], window["session"]))
        source["error"] = "no destination"
        return source
    source["multicast"] = {"address": address, "port": port,
                           "generated": bool(stream.get("destination_generate_applied")),
                           "default": stream.get("destination_generate_default")}
    source["stream_enabled"] = bool(stream.get("enabled"))
    source["session_encoder"] = (session.get("video") or {}).get("encoder")
    source["session_sap"] = bool((session.get("sap") or {}).get("enabled"))
    source["bitrate"] = encoder.get("bitrate")
    source["encoder1_bitrate"] = primary.get("bitrate")
    source["encoder1_input"] = primary.get("input")
    source["input"] = encoder.get("input")

    # Encoder 2 has to be fed before any of the rest of it means anything.
    encoder_input = classify_encoder_input(primary.get("input"), encoder.get("input"))
    window["encoder_input"] = encoder_input
    source["encoder_input"] = encoder_input
    if encoder_input["status"] == ENCODER_INPUT_CONFLICT:
        conflicts.append("Window %s (%s): %s"
                         % (window["cell"], source["hostname"], encoder_input["reason"]))
    elif encoder_input["status"] == ENCODER_INPUT_UNAVAILABLE:
        errors.append("Window %s (%s): %s"
                      % (window["cell"], source["hostname"], encoder_input["reason"]))
    elif encoder_input["status"] == ENCODER_INPUT_NOT_USED:
        warnings.append(
            "Encoder 2 on %s is set to Not used and will be pointed at %s, the "
            "input Encoder 1 already encodes. Without this the session carries "
            "no video at all."
            % (source["hostname"], encoder_input["wanted"]))

    other = _other_consumers(known_multiviews, source_ip, window["encoder_index"],
                             decoder_state.get("ip"))
    scaler = classify_scaler(encoder.get("scaler"), window["width"], window["height"],
                             window["encoder_index"], other,
                             state.get("input_resolution"))
    window["scaler"] = scaler

    if scaler["status"] == SCALER_CONFLICT:
        names = ", ".join("%s on %s" % (c.get("multiview"), c.get("decoder"))
                          for c in scaler["conflicts"])
        conflicts.append(
            "Window %s needs Encoder %d on %s scaled to %s, but %s already "
            "depends on it at a different size. Change the layout or the source "
            "for this window."
            % (window["cell"], window["encoder_index"], source["hostname"],
               scaler["desired"], names))
    elif scaler["status"] == SCALER_CHANGE:
        warnings.append(
            "Encoder 2 on %s will be re-scaled from %s to %s. Every decoder "
            "using Session 2 sees this change; Encoder 1 is untouched."
            % (source["hostname"], scaler["current"] or "pass-through",
               scaler["desired"]))
    elif scaler["status"] == SCALER_UNKNOWN_EXTERNAL:
        warnings.append(
            "Encoder 2 on %s will be re-scaled from %s to %s. OmniSuite knows of "
            "no other Multiview using it, but cannot prove there is no consumer "
            "outside its own state. Encoder 1 is untouched."
            % (source["hostname"], scaler["current"] or "pass-through",
               scaler["desired"]))
    return source


def _check_intra_plan_scaler_conflicts(windows, conflicts):
    """One source cannot feed two windows that need different scaler sizes.

    A scaler belongs to the encoder, so a source driving two windows through the
    same encoder produces one size for both. Asking for two is not a plan that
    can be applied -- it is two writes to one field, the second overwriting the
    first, leaving whichever window lost silently at the wrong resolution. The
    device reports no error, so nothing downstream would notice.

    Every Multiview window now goes through Encoder 2, so this is the only thing
    standing between "one source in two different-sized windows" and a picture
    that is quietly wrong.
    """
    wanted = {}
    for window in windows:
        source = window.get("source") or {}
        if not source.get("ip") or source.get("error"):
            continue
        key = (source["ip"], window["encoder_index"])
        wanted.setdefault(key, {}).setdefault(window["scaler_format"], []).append(
            window["cell"])

    for (source_ip, encoder_index), formats in sorted(wanted.items()):
        if len(formats) < 2:
            continue
        described = "; ".join(
            "%s needs %s" % (", ".join(sorted(cells)), fmt)
            for fmt, cells in sorted(formats.items()))
        conflicts.append(
            "%s is assigned to windows that need different sizes from the same "
            "encoder (Encoder %d): %s. A scaler belongs to the encoder, so it "
            "can only produce one of them. Use a different source for one of "
            "these windows."
            % (source_ip, encoder_index, described))


def _other_consumers(known_multiviews, source_ip, encoder_index, this_decoder):
    """Multiviews OmniSuite already knows about that draw on this same encoder."""
    consumers = []
    for record in known_multiviews or ():
        if record.get("decoder_ip") == this_decoder and record.get("current"):
            continue
        for window in record.get("windows") or ():
            if (window.get("source_ip") == source_ip
                    and window.get("encoder_index") == encoder_index):
                consumers.append({
                    "decoder": record.get("decoder_hostname") or record.get("decoder_ip"),
                    "multiview": record.get("object_name"),
                    "scaler_format": window.get("scaler_format"),
                })
    return consumers


def _check_multicast_collisions(windows, decoder_state, warnings):
    """Two windows drawing different sources from one address is a mistake."""
    seen = {}
    for window in windows:
        multicast = window.get("multicast")
        if not multicast:
            continue
        key = "%s:%s" % (multicast.get("address"), multicast.get("port"))
        if key in seen and seen[key] != (window.get("source") or {}).get("ip"):
            warnings.append(
                "Windows %s and %s both resolve to %s. Check the session "
                "destinations on those encoders." % (seen[key], window["cell"], key))
        seen[key] = (window.get("source") or {}).get("ip")


def _build_mutations(desired, object_name, updating, geometry, windows,
                     decoder_state, encoder_states, warnings):
    """Two ordered sets of writes: what Save does, and what Recall does.

    A decoder holds many saved Multiviews and only one of them is on the output,
    so a saved configuration cannot own live encoder or decoder resources -- two
    saved Multiviews will routinely want the same Encoder 2 at different sizes.
    Saving therefore writes the Multiview object and nothing else, and every
    resource decision is made again, against live state, at Recall.

        save        the subframe prune and the Multiview object
        activation  Encoder 2's input, scaler and bitrate, Encoder 1's bitrate
                    where the budget forces it, Session 2 and its announcement,
                    and the decoder inputs the windows need

    Nothing here touches `hdmi_output`: the display belongs to Show alone.
    """
    decoder_ip = decoder_state.get("ip")
    save, activation, snapshot = [], [], []

    def snap(device_ip, node, name, field):
        entry = {"device": device_ip, "node": node, "name": name, "field": field}
        if entry not in snapshot:
            snapshot.append(entry)

    # Per source, not per window: one Encoder 2 serves every window drawing on it.
    by_source = {}
    for window in windows:
        source = window.get("source") or {}
        if not source.get("ip") or source.get("error"):
            continue
        by_source.setdefault(source["ip"], []).append(window)

    for source_ip, group in by_source.items():
        window = group[0]
        source = window["source"]

        # A. Encoder 2's physical input. Everything after it depends on the
        # encoder actually producing video, so it goes first.
        encoder_input = window.get("encoder_input") or {}
        if encoder_input.get("change_required"):
            activation.append({
                "stage": STAGE_ENCODER_INPUT, "device": source_ip, "node": "vc2",
                "target": window["encoder"],
                "description": "Point %s on %s at %s (it is currently Not used)"
                               % (window["encoder"], source.get("hostname"),
                                  encoder_input["wanted"]),
                "shared": True,
                "config": {"name": window["encoder"], "input": encoder_input["wanted"]},
            })
            snap(source_ip, "vc2", window["encoder"], "input")

        # B. Encoder 2's scaler. Encoder 1's scaler is never written.
        scaler = window.get("scaler") or {}
        if scaler.get("change_required"):
            activation.append({
                "stage": STAGE_ENCODER_SCALER, "device": source_ip, "node": "vc2",
                "target": window["encoder"],
                "description": "Set %s on %s to %s"
                               % (window["encoder"], source.get("hostname"),
                                  scaler["desired"]),
                "shared": True,
                "config": {"name": window["encoder"],
                           "scaler": {"enable": True, "width": window["width"],
                                      "height": window["height"]}},
            })
            snap(source_ip, "vc2", window["encoder"], "scaler")

        # C. bitrates
        bitrate = window.get("bitrate")
        if bitrate is not None and source.get("bitrate") != bitrate:
            activation.append({
                "stage": STAGE_ENCODER_BITRATE, "device": source_ip, "node": "vc2",
                "target": window["encoder"],
                "description": "Set %s on %s to %s Mb/s"
                               % (window["encoder"], source.get("hostname"), bitrate),
                "shared": True,
                "config": {"name": window["encoder"], "bitrate": bitrate},
            })
            snap(source_ip, "vc2", window["encoder"], "bitrate")
        encoder1_target = window.get("encoder1_target")
        if encoder1_target is not None and source.get("encoder1_bitrate") != encoder1_target:
            activation.append({
                "stage": STAGE_ENCODER_BITRATE, "device": source_ip, "node": "vc2",
                "target": encoder_object_name(1),
                "description": "Reduce %s on %s from %s to %s Mb/s to fit the "
                               "source's %s Mb/s budget"
                               % (encoder_object_name(1), source.get("hostname"),
                                  source.get("encoder1_bitrate"), encoder1_target,
                                  SOURCE_VIDEO_BUDGET),
                "shared": True,
                "config": {"name": encoder_object_name(1), "bitrate": encoder1_target},
            })
            snap(source_ip, "vc2", encoder_object_name(1), "bitrate")

        # D. Session 2 assigned to Encoder 2 and transmitting
        session_state = _by_name((encoder_states.get(source_ip) or {}).get("sessions"))
        session = session_state.get(window["session"]) or {}
        stream = _session_stream(session, "video")
        needs_enable = not stream.get("enabled")
        wrong_encoder = (session.get("video") or {}).get("encoder") != window["encoder"]
        if needs_enable or wrong_encoder:
            config = {"name": window["session"], "video": {}}
            if wrong_encoder:
                config["video"]["encoder"] = window["encoder"]
            if needs_enable:
                config["video"]["stream"] = {"enabled": True}
            activation.append({
                "stage": STAGE_SESSION, "device": source_ip, "node": "sessions",
                "target": window["session"],
                "description": "Enable %s video on %s (%s)"
                               % (window["session"], source.get("hostname"),
                                  window["encoder"]),
                "shared": True, "config": config,
            })
            snap(source_ip, "sessions", window["session"], "video")

        # E. Session 2 must not announce itself, or a decoder with SAP enabled
        # can be pointed at it underneath whatever configured it.
        if (session.get("sap") or {}).get("enabled"):
            activation.append({
                "stage": STAGE_SESSION_SAP, "device": source_ip, "node": "sessions",
                "target": window["session"],
                "description": "Turn off SAP announcement for %s on %s so no "
                               "decoder adopts it automatically"
                               % (window["session"], source.get("hostname")),
                "shared": True,
                "config": {"name": window["session"], "sap": {"enabled": False}},
            })
            snap(source_ip, "sessions", window["session"], "sap")

    # F. the decoder inputs, once per distinct input rather than once per window
    bound = {}
    for window in windows:
        allocation = window.get("ip_input") or {}
        name = allocation.get("ip_input")
        multicast = window.get("multicast") or {}
        if not name or not multicast.get("address") or name in bound:
            continue
        bound[name] = True
        if allocation.get("reused") and allocation.get("already_enabled"):
            continue                      # already carrying exactly this stream
        if allocation.get("already_enabled"):
            activation.append({
                "stage": STAGE_IP_INPUT_DISABLE, "device": decoder_ip,
                "node": "ip_input", "target": name,
                "description": "Disable %s while its source changes" % name,
                "config": {"name": name, "enabled": False},
            })
        activation.append({
            "stage": STAGE_IP_INPUT, "device": decoder_ip, "node": "ip_input",
            "target": name,
            "description": "Point %s at %s:%s and enable it"
                           % (name, multicast.get("address"), multicast.get("port")),
            "config": {"name": name, "enabled": True,
                       "port": multicast.get("port"),
                       "multicast": {"address": multicast.get("address")}},
        })
        snap(decoder_ip, "ip_input", name, "*")

    # G. subframes the new layout no longer has.
    #
    # `config_set` merges by subframe name, so switching layout would otherwise
    # leave the previous layout's windows in place -- and since the device caps a
    # multiview at four subframes, adding four new names on top of four old ones
    # is refused outright. They have to go first, and only `del_multiview_subframe`
    # removes one.
    subframe_names = [w["label"] for w in windows]
    if updating:
        existing = next((m for m in (decoder_state.get("multiview") or [])
                         if m.get("name") == object_name), {})
        for stale in (s for s in (existing.get("subframes") or [])
                      if str(s.get("name") or "") not in subframe_names):
            save.append({
                "stage": STAGE_MULTIVIEW_PRUNE, "device": decoder_ip,
                "node": "multiview", "target": object_name,
                "method": "del_multiview_subframe",
                "subframe": str(stale.get("name") or ""),
                "description": "Remove subframe %r, which this layout does not have"
                               % str(stale.get("name") or ""),
                "config": {"multiview": object_name,
                           "name": str(stale.get("name") or "")},
            })

    # H. the Multiview object itself
    subframes = []
    for window in windows:
        allocation = window.get("ip_input") or {}
        subframes.append({
            "name": window["label"],
            "x": window["x"], "y": window["y"], "anchor": window["anchor"],
            "priority": window["priority"],
            "input": allocation.get("ip_input") or "",
        })
    save.append({
        "stage": STAGE_MULTIVIEW, "device": decoder_ip, "node": "multiview",
        "target": object_name,
        "method": None if updating else "add_multiview",
        "description": ("Update %s" if updating else "Create %s") % object_name,
        "config": {"name": object_name,
                   "width": geometry["canvas"]["width"],
                   "height": geometry["canvas"]["height"],
                   "subframes": subframes},
    })
    snap(decoder_ip, "multiview", object_name, "*")

    # A source feeding four identical windows would otherwise emit four identical
    # writes to one encoder field. Collapsed by what they actually do, so a
    # genuinely different write to the same field still survives as its own step.
    save = _dedupe_mutations(save)
    activation = _dedupe_mutations(activation)

    order = {stage: index for index, stage in enumerate(STAGE_ORDER)}
    save.sort(key=lambda m: order.get(m["stage"], len(STAGE_ORDER)))
    activation.sort(key=lambda m: order.get(m["stage"], len(STAGE_ORDER)))
    return save, activation, snapshot


def _dedupe_mutations(mutations):
    seen, unique = set(), []
    for mutation in mutations:
        identity = (mutation["stage"], mutation["device"], mutation["node"],
                    mutation["target"], mutation.get("method"),
                    mutation.get("subframe"),
                    json.dumps(mutation.get("config"), sort_keys=True))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(mutation)
    return unique
