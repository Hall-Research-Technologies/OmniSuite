# OmniSuite Multiview — Phase 1 Discovery

**Status:** Phase 1 discovery complete; Phase 2 implemented the Multiview page
against it. Sections A–R are the Phase 1 record and are preserved as written;
§S records what implementation corrected or added.
**Hardware under test:** OmniStream 4xxx (`hw-omni-d4511` decoder, `hw-omni-e4111` encoder), firmware 2.1.2.
**Method:** live API probing of the bench, plus extraction of the device's *own* web
application, which is the authoritative definition of the layout model.

Every statement below is marked with how it was established:

| Mark | Meaning |
|---|---|
| **[LIVE]** | Proven by a request/response against the bench hardware. |
| **[FW]** | Read out of the device's own firmware web application (`/js/atlona.js`). |
| **[GUIDE]** | From the Atlona *OmniStream Multiview Application Guide* (May 2023, OmniStream 2.0). |
| **[INFER]** | Reasoned conclusion. Flagged wherever it is not directly proven. |

---

## A. Purpose, scope, and what this phase deliberately did not do

The goal was to understand the real device API well enough that Phase 2 is written
against verified behaviour rather than assumption.

**Done:** protocol discovery, configuration mapping, constraint validation, layout and
window matrices, encoder/scaler analysis, multicast strategy, data-loading classification,
UI and transaction *design*, and one controlled live end-to-end proof.

**Deliberately not done:** no OmniSuite Multiview UI, no new page, no production code
changes. The scan architecture, route architecture, WebSocket transport, USB transport,
USB routing, LLDP, appearance, update handling and firmware handling were not modified.
No page polling was introduced.

### A.1 Assumptions in the brief that the hardware contradicted

Four load-bearing assumptions did not survive contact with the device. Each is detailed
in its own section; they are collected here because they change the Phase 2 design.

1. **There is no `layout` field in the API.** The 11 layouts are a *client-side* concept.
   The device stores only explicit subframe geometry. (§I)
2. **Subframes have no width or height.** Window size is not a multiview property at all —
   it is the resolution of the incoming stream, set on the *encoder's* scaler. (§F, §N)
3. **The proposed ip_input mapping (1/2/3/4/6/7) is not what the bench uses.** (§P)
4. **2560x1440 is a first-class canvas resolution in the device's own UI** — the brief
   asked whether live evidence supported it. It does, with one real gap. (§M)

---

## B. Authorization, safety boundaries, and test isolation

**Authorized and used:** inspection and temporary configuration of decoder
`192.168.100.32` and encoder `192.168.100.142`.

**Mutations made, and their disposition:**

| # | Device | Change | Restored? |
|---|---|---|---|
| 1 | .142 | `session2.video.stream.enabled` → true | Yes → `false` |
| 2 | .32 | `ip_input2` enabled, pointed at the Session 2 stream | Yes → disabled, address cleared |
| 3 | .32 | created `multiviewOmniSuiteTest` | **Retained deliberately — see §B.1** |
| 4 | .32 | `hdmi_output1.sap_input.enabled` → false | Yes → `true`, session restored |
| 5 | .32 | `hdmi_output1.video.input` → the multiview | Yes → `ip_input1` |

A full pre-mutation snapshot was taken before any write. After restore, a field-by-field
diff against that snapshot showed **zero unintended differences** across HDMI output
video/audio/aux inputs, SAP input state and session, ip_input 1/2/3 enable state and
addressing, encoder session 1/2 enable/encoder/destination, and both vc2 scaler blocks.

Read-only probing of the other eight bench devices caused no writes.

Transient probe objects (`multiviewOmniSuiteProbe`, `multiviewApiProbe`, and the
name/limit/canvas validation objects) were all deleted; §G.3 documents how.

### B.1 One object intentionally left on the bench

`multiviewOmniSuiteTest` remains on decoder **192.168.100.32**:

- canvas 3840x2160, two subframes (`subframe1` → `ip_input1`, `subframe2` → `ip_input2`)
- it is **not** selected on the HDMI output, and `ip_input2` is disabled, so it is inert
- it is retained so Phase 2 can develop and test the *read* path against a real multiview
  without needing to mutate hardware first

Delete it with the call in §G.3 when it is no longer wanted.

### B.2 Rules for automated tests (binding on Phase 2)

Automated tests must make **zero** real hardware traffic: no WebSocket connections, UDP
packets, TCP connections or HTTP requests to devices, and no device mutations. Any
Multiview tests must use synthetic/test-net addresses and mocked responses. **No
`192.168.100.x` address may appear in an automated test as a reachable target.** The
existing process-wide hardware fences must remain effective.

The live work in this phase was manual and run outside the automated suite; nothing in
this document adds a test that touches hardware.

---

## C. Bench inventory

Probed read-only. The `multiview` column is the result of `config_get multiview`.

| IP | Model | Role | `multiview` node | ip_input | vc2 | sessions |
|---|---|---|---|---|---|---|
| .32 | `hw-omni-d4511` | Decoder | supported | 32 | – | – |
| .151 | `hw-omni-d4511` | Decoder | supported | 32 | – | – |
| .155 | `hw-omni-d4111` | Decoder | supported | 32 | – | – |
| .161 | `hw-omni-d4111` | Decoder | supported | 32 | – | – |
| .205 | `at-omni-121` | Decoder | supported | **16** | – | – |
| .142 | `hw-omni-e4111` | Encoder | *not supported* | – | 2 | 6 |
| .141 | `hw-omni-e4521` | Encoder | *not supported* | – | 2 | 6 |
| .143 | `hw-omni-e4521` | Encoder | *not supported* | – | 2 | 6 |
| .218 | `at-omni-111` | Encoder | *not supported* | – | 2 | 6 |
| .145 | `hw-omni-e4111` | Encoder | unreachable (WS timeout) | – | – | – |

**[LIVE]** Three consequences for Phase 2:

- **Multiview is a decoder-only feature.** Every encoder returns `Config node not found`.
- **ip_input count is model-dependent** — 32 on 4xxx, 16 on the 2.0-era `at-omni-121`.
  Do not hardcode 32; use the length of the returned array.
- `.145` is unreachable over WebSocket yet is still the active source feeding `.32`
  (`session1@hw-omni-e4111-08412`). Multicast delivery does not require the encoder to be
  manageable, so **a working source is not evidence that its encoder is reachable.**
  Phase 2 must tolerate a source device that cannot be queried.

---

## D. Evidence sources, ranked by authority

1. **The live device's responses** — final authority.
2. **The device's own web application** (`http://<ip>/js/atlona.js`) — the code the vendor
   ships to drive this exact firmware. It is where the 11 layouts, the canvas snapping
   maths and the scaler format lists actually live. Nothing else documents them.
3. **The Atlona Multiview Application Guide** — written for OmniStream **2.0**
   (AT-OMNI-111/121), May 2023. Useful corroboration, **not** authoritative for 4xxx.

### D.1 Retrieving the firmware web app

The JS assets are served **gzip-only**. A plain request 404s; the same path with
`Accept-Encoding: gzip` returns the bundle (so does appending `.gz`). The CSS is served
uncompressed, which is why a naive fetch looks half-broken.

```
GET http://<device>/js/atlona.js      Accept-Encoding: gzip     → ~223 KB
```

The decoder bundle contains `MultiviewController`; the encoder bundle contains the scaler
format tables. Both were needed.

### D.2 Where the guide and the hardware agree — and where they do not

The guide's numbers corroborate the 4xxx firmware in two independent, non-obvious places,
which is strong evidence the layout maths is unchanged across generations:

- the guide states 4-split main content is **2880x1584** and that this "is greater than
  supported by the Encoder 2 scaler". The algorithm recovered from 4xxx firmware produces
  exactly 2880x1584 for `4-split` at 4K, and 2880x1584 appears only in the Encoder-1 list.
- the guide gives Encoder 2 as 1920x1080 or 960x528. Both are in the 4xxx HD scaler list,
  and 960x528 is exactly the computed 4-split side-window size.

Neither number was taken from the guide — both were derived from firmware and *then* found
to match. That is the main reason to trust the matrices in §K–§M.

They **disagree** on ip_input numbering: the guide uses `ip_input1` / `ip_input12` /
`ip_input13`; the bench uses 1/3/5. Neither matches the brief's proposed 1/2/3/4/6/7. The
numbering is a site convention, not an API rule. (§P)

---

## E. Transport and request grammar

**[LIVE][FW]** Unchanged from what OmniSuite already speaks — `ws://<ip>:80/wsapp/`, one
JSON request, one JSON response. Phase 2 must reuse the existing primitives
(`_ws_url`, `_ws_send_recv`, `_ws_send_recv_with_fallback`) rather than add a transport.

There are **three** request verbs. The third is the one Multiview needs and the one
OmniSuite barely uses today:

| Verb | Shape | Used for |
|---|---|---|
| `config_get` | `{"config_get": "<node>"}` | read a node |
| `config_set` | `{"config_set": {"name": "<node>", "config": [ ... ]}}` | **merge** into existing objects |
| `method` | `{"method": {"<method_name>": { ...args }}}` | create/delete objects, and other actions |

Every request also carries `id`, `username`, `password`.

### E.1 `config_set` merges; it never replaces the collection

Sending a one-element `config` array updates that named object and leaves every other
object untouched. This is why an object **cannot** be removed by omitting it from a write —
deletion requires a `method`. This was established the hard way: a leftover probe object
survived repeated `config_set` attempts, including ones carrying `delete: true` and
`remove: true`, which the device accepted with `error: false` and then ignored.

**A successful response does not mean the device did what you asked.** See §H.2.

### E.2 There is no introspection

`config_get method`, `config_get methods`, `method list_methods` and `method help` all fail
(`Config node not found` / `Method not found`). The method registry can only be learned
from the firmware web app — which is precisely why §D.1 matters.

OmniSuite already contains one `method` call (`_ws_delete_logo`), so the pattern is proven
in the existing codebase and needs no new plumbing.

---

## F. The multiview object model

**[LIVE]** As the device actually stores it:

```json
{
  "name": "multiviewOmniSuiteTest",
  "width": 3840,
  "height": 2160,
  "background": { "color": { "red": 0, "green": 0, "blue": 0 } },
  "slice_info": { "width": {"min": 32, "max": 32}, "height": {"min": 8, "max": 8} },
  "subframes": [
    {
      "name": "subframe1",
      "x": 0, "y": 0,
      "anchor": "top left",
      "input": "ip_input1",
      "priority": 1,
      "cropper": {"enable": false, "first_line": 0, "first_pixel": 0,
                  "last_line": 0, "last_pixel": 0},
      "repeat":  {"enable": false, "timeout": 0},
      "stream":  {},
      "video":   {"hdcp": "none",
                  "input":  {"active": false},
                  "output": {"active": false}}
    }
  ]
}
```

### F.1 The single most important structural fact

**A subframe has no width and no height.** The stored fields are exactly `name`, `x`, `y`,
`anchor`, `input`, `priority`, `cropper`, `repeat`, `stream`, `video`.

A subframe is a *placement*, not a rectangle. The size of the window on screen is the
resolution of the stream arriving on its `ip_input`, which is determined by the **encoder's
scaler**. The decoder does not scale multiview sources.

This is the architectural centre of the feature: **to change a window's size you must
reconfigure the encoder that produces it.** Everything in §N and §O follows from this.

### F.2 Fields OmniSuite writes vs fields it only reads

| Write | Read-only (device-maintained) |
|---|---|
| `name`, `width`, `height` | `slice_info` |
| `background.color` | `video.input.active`, `video.input.resolution` |
| subframe `name`, `x`, `y`, `anchor`, `input`, `priority` | `video.output.active` |
| subframe `cropper`, `repeat` | `stream` |

`video.input.active` / `video.output.active` are the live per-window status and are how the
device UI reports whether a tile is actually receiving and displaying video. During the live
proof both subframes read `input.active = true, output.active = true`.

---

## G. Multiview CRUD

**[FW]** Four methods, recovered from the firmware's `MultiviewController`, all confirmed
live.

### G.1 Create

```json
{"id": "add_multiview-method",
 "method": {"add_multiview": {
   "name": "multiviewExample",
   "width": 3840, "height": 2160,
   "subframes": [
     {"name": "top_left (1920x1080)", "input": "ip_input1",
      "anchor": "top left", "x": 0, "y": 0, "priority": 1}
   ]}}}
```

Duplicate names are rejected: `Multiview multiviewExample already exists`. **[LIVE]**

The firmware UI sends its whole view-model here, including `layout` and `resolution`. The
device **silently discards both** — see §I.

`config_set` will also create a missing object, but `add_multiview` is preferred: it is what
the vendor UI uses, and it is the only path that reports a duplicate name as an error.

### G.2 Update

```json
{"id": "multiview-set",
 "config_set": {"name": "multiview",
   "config": [{"name": "multiviewExample",
               "subframes": [{"name": "subframe2", "anchor": "bottom right"}]}]}}
```

Merges by object name and by subframe name. Partial subframe updates work — the example
changes only `anchor` and leaves x/y/input/priority alone.

### G.3 Delete

```json
{"id": "del_multiview-method",  "method": {"del_multiview": {"name": "multiviewExample"}}}
```

Subframes individually:

```json
{"method": {"add_multiview_subframe": {"multiview": "multiviewExample", "name": "subframeX",
                                       "anchor": "center", "x": 0, "y": 0,
                                       "priority": 2, "input": "ip_input3"}}}
{"method": {"del_multiview_subframe": {"multiview": "multiviewExample", "name": "subframeX"}}}
```

All four verified live. `del_multiview` is what finally removed the stuck probe object, and
is the call to use on `multiviewOmniSuiteTest` (§B.1).

---

## H. Constraints — what the device enforces, and what it does not

**[LIVE]** Each row below was probed directly against `.32`.

### H.1 Enforced by the device

| Rule | Error message |
|---|---|
| Name must start with `multiview` | `Multiview name should start with "multiview"` |
| Name cannot be exactly `multiview` | `Multiview name cannot be "multiview"` |
| Name is case-sensitive (`MultiviewB` rejected) | as above |
| Max **4** subframes | `Multiview is limited to 4 subframes` |
| Canvas width ≤ **3840** | `Multiview max width is 3840 px` |
| `anchor` must be one of five values | `Invalid anchor position` |
| Duplicate multiview name | `Multiview <name> already exists` |
| `x` snapped to multiples of 32, `y` to multiples of 8 | silent; device UI reports "snapped to slice boundaries" |

The brief's suggested test name `OmniSuite-Test-2x2` is rejected by the prefix rule. Any
OmniSuite-generated name must begin `multiview`. Spaces, underscores and non-ASCII after
the prefix are all accepted (`multiview 1`, `multiview_x` verified).

**Anchor enum — all five accepted live:** `top left`, `top right`, `bottom left`,
`bottom right`, **`center`**. `centre`, `middle`, `center center` and `""` are rejected.

> An earlier probe in this investigation reported `center` as rejected. That was wrong.
> The firmware's own `vm.anchors` lists five values, and retesting confirmed `center`
> stores correctly. Phase 2 should offer all five.

### H.2 NOT enforced — OmniSuite must validate these itself

These matter most, because the device accepts nonsense and returns `error: false`:

| Input | Device behaviour | Why it matters |
|---|---|---|
| `input: "nonsense"` | **accepted** | A typo'd or stale input name yields a black window and no error. OmniSuite must validate against the device's real `ip_input` names. |
| Canvas `1920x1085` | **accepted** | Height is not validated at all. |
| Canvas `0x0` | **accepted** | Produces an unusable object. |
| Canvas `32x8` | **accepted** | Likewise. |
| Unknown field `layout` | **silently dropped** | Never trust a write-back you did not read back. |
| Per-object `delete: true` | **silently ignored**, `error: false` | Looks like a successful delete. Is not. (§E.1) |
| Subframes overlapping / off-canvas | accepted | Only `priority` decides z-order. |

**Canvas height has no device-side maximum**; only width is checked. OmniSuite must enforce
sane canvas bounds itself, and must **read back and verify** after every apply (§R.4).

---

## I. The 11 layouts

**[FW]** The brief asked for the 11 layout *API identifiers*, warning not to assume the
visible UI label equals the API value. The finding is stronger than that:

> **There is no layout field in the API at all.**

`add_multiview` is sent the `layout` string by the vendor's own UI and the device drops it.
`config_get multiview` never returns it. The firmware UI does not recover it on reload
either — `on_get_config` recomputes only `resolution`, never `layout`.

The 11 layouts are **client-side templates that generate subframe geometry**. They live in
`vm.layout_configs` in the web app and nowhere else. Their exact identifiers:

| # | API identifier | Grid (cells w×h) | Subframe keys |
|---|---|---|---|
| 1 | `2x2` | 2 × 2 | `top_left`, `top_right`, `bottom_left`, `bottom_right` |
| 2 | `side-by-side` | 2 × 4 | `left`, `right` |
| 3 | `pip-top-left` | 3 × 3 | `main`, `top_left` |
| 4 | `pip-top-right` | 3 × 3 | `main`, `top_right` |
| 5 | `pip-bottom-left` | 3 × 3 | `main`, `bottom_left` |
| 6 | `pip-bottom-right` | 3 × 3 | `main`, `bottom_right` |
| 7 | `1+3-horizontal-bottom` | 6 × 3 | `main`, `bottom_left`, `bottom_middle`, `bottom_right` |
| 8 | `1+3-horizontal-top` | 6 × 3 | `main`, `top_left`, `top_middle`, `top_right` |
| 9 | `1+3-vertical-right` | 3 × 6 | `main`, `top_right`, `middle_right`, `bottom_right` |
| 10 | `1+3-vertical-left` | 3 × 6 | `main`, `top_left`, `middle_left`, `bottom_left` |
| 11 | `4-split` | 4 × 8 | `main`, `top_right`, `middle_right`, `bottom_right` |

Plus a 12th pseudo-layout, **`custom`**, meaning "leave x/y to the user".

Note these keys *are* the subframe names the vendor UI writes — a subframe is called
`top_left (1920x1080)`, not `subframe1`, when created through the layout picker.

### I.1 Consequence: layout is write-once and must be stored by OmniSuite

Because the device does not persist `layout`, a multiview read back from a device is just
geometry. OmniSuite has three options:

1. **Store the chosen layout in OmniSuite's own state**, keyed by device + multiview name.
   Exact and cheap.
2. **Infer** the layout by matching geometry against the generated matrices. Workable — the
   matrices in §K–§M are mutually distinct — but it cannot represent `custom` and breaks
   after any manual edit.
3. Treat every loaded multiview as `custom`. This is what the vendor UI does. Honest, but
   it loses the layout picker on reload.

**Recommended: (1) with (2) as a fallback** for multiviews OmniSuite did not create, so a
multiview built in the device's own web UI still shows a sensible layout name rather than
"custom". OmniSuite state is a cache, never the source of truth: geometry always wins.

### I.2 The subframe-name convention

**[FW]** The vendor UI encodes the intended window size into the subframe *name*:

```
"top_left (1920x1080)"
```

`compute_subframe_size()` returns that parenthesised string purely to append to the name.
It is a **human hint with no functional effect** — the device does not parse it. It exists
because, per §F.1, there is nowhere else to record intended size.

OmniSuite should adopt the same convention: it keeps the two UIs legible to each other and
gives Phase 2 a cheap layout-inference signal for §I.1 option 2.

---

## J. Canvas snapping

**[FW]** A requested canvas is not always the canvas you get. The device UI rounds the
canvas so it divides evenly into the layout grid:

```
granularity_w = grid_w * 32      (slice width)
granularity_h = grid_h * 8       (slice height)

canvas_w = (requested_w == 3840) ? round_down(requested_w, granularity_w)
                                 : round_up  (requested_w, granularity_w)
canvas_h = (requested_h == 2160) ? round_down(requested_h, granularity_h)
                                 : round_up  (requested_h, granularity_h)
```

Round **down** at exactly 3840/2160 (never exceed a 4K panel), round **up** otherwise.

Subframe geometry is then a proportional division of the *snapped* canvas:

```
x = canvas_w * cell.x / grid_w   (+ x_offset)
y = canvas_h * cell.y / grid_h   (+ y_offset)
w = canvas_w * cell.w / grid_w        <- intended window size, NOT stored on the device
h = canvas_h * cell.h / grid_h
```

The PiP layouts carry a ±32 px `x_offset`/`y_offset` inset and their own `anchor`; that is
the only place the templates use an anchor other than `top left`.

Phase 2 must implement this exactly — several layouts do **not** land on the requested
canvas, and the UI must show the user the real number. `slice_info` is returned per
multiview (32/8 on this hardware) and should be read rather than hardcoded.

The matrices below were produced by porting this algorithm from firmware; §D.2 explains why
the result can be trusted. Every x is a multiple of 32 and every y a multiple of 8, so no
generated layout is ever silently snapped.

---

## K. Window matrix — canvas 3840x2160

⚠ marks a canvas that snapped away from the request. "Encoder" is which encoder can
produce that window size (§N).

| Layout (API id) | Canvas actual | Subframe | x | y | anchor | Window | Encoder |
|---|---|---|---|---|---|---|---|
| `2x2` | 3840x2160 | `top_left` | 0 | 0 | top left | 1920x1080 | 1 or 2 |
|  |  | `top_right` | 1920 | 0 | top left | 1920x1080 | 1 or 2 |
|  |  | `bottom_left` | 0 | 1080 | top left | 1920x1080 | 1 or 2 |
|  |  | `bottom_right` | 1920 | 1080 | top left | 1920x1080 | 1 or 2 |
| `side-by-side` | 3840x2144 ⚠ | `left` | 0 | 536 | top left | 1920x1072 | 1 or 2 |
|  |  | `right` | 1920 | 536 | top left | 1920x1072 | 1 or 2 |
| `pip-top-left` | 3840x2160 | `main` | 0 | 0 | top left | 3840x2160 | **1 only** |
|  |  | `top_left` | 32 | 32 | top left | 1280x720 | 1 or 2 |
| `pip-top-right` | 3840x2160 | `main` | 0 | 0 | top left | 3840x2160 | **1 only** |
|  |  | `top_right` | 3808 | 32 | top right | 1280x720 | 1 or 2 |
| `pip-bottom-left` | 3840x2160 | `main` | 0 | 0 | top left | 3840x2160 | **1 only** |
|  |  | `bottom_left` | 32 | 2128 | bottom left | 1280x720 | 1 or 2 |
| `pip-bottom-right` | 3840x2160 | `main` | 0 | 0 | top left | 3840x2160 | **1 only** |
|  |  | `bottom_right` | 3808 | 2128 | bottom right | 1280x720 | 1 or 2 |
| `1+3-horizontal-bottom` | 3840x2160 | `main` | 640 | 0 | top left | 2560x1440 | **1 only** |
|  |  | `bottom_left` | 0 | 1440 | top left | 1280x720 | 1 or 2 |
|  |  | `bottom_middle` | 1280 | 1440 | top left | 1280x720 | 1 or 2 |
|  |  | `bottom_right` | 2560 | 1440 | top left | 1280x720 | 1 or 2 |
| `1+3-horizontal-top` | 3840x2160 | `main` | 640 | 720 | top left | 2560x1440 | **1 only** |
|  |  | `top_left` | 0 | 0 | top left | 1280x720 | 1 or 2 |
|  |  | `top_middle` | 1280 | 0 | top left | 1280x720 | 1 or 2 |
|  |  | `top_right` | 2560 | 0 | top left | 1280x720 | 1 or 2 |
| `1+3-vertical-right` | 3840x2160 | `main` | 0 | 360 | top left | 2560x1440 | **1 only** |
|  |  | `top_right` | 2560 | 0 | top left | 1280x720 | 1 or 2 |
|  |  | `middle_right` | 2560 | 720 | top left | 1280x720 | 1 or 2 |
|  |  | `bottom_right` | 2560 | 1440 | top left | 1280x720 | 1 or 2 |
| `1+3-vertical-left` | 3840x2160 | `main` | 1280 | 360 | top left | 2560x1440 | **1 only** |
|  |  | `top_left` | 0 | 0 | top left | 1280x720 | 1 or 2 |
|  |  | `middle_left` | 0 | 720 | top left | 1280x720 | 1 or 2 |
|  |  | `bottom_left` | 0 | 1440 | top left | 1280x720 | 1 or 2 |
| `4-split` | 3840x2112 ⚠ | `main` | 0 | 264 | top left | 2880x1584 | **1 only** |
|  |  | `top_right` | 2880 | 264 | top left | 960x528 | 1 or 2 |
|  |  | `middle_right` | 2880 | 792 | top left | 960x528 | 1 or 2 |
|  |  | `bottom_right` | 2880 | 1320 | top left | 960x528 | 1 or 2 |

**Every window size here has a matching encoder scaler format.** 4K is the best-supported
canvas, which is expected — the scaler tables were evidently designed around it.

Nine of the eleven layouts need Encoder 1 for their main window — every layout except `2x2` and `side-by-side`. See §O.

---

## L. Window matrix — canvas 1920x1080

Per the brief, "1080" is interpreted as **1920x1080**.

| Layout (API id) | Canvas actual | Subframe | x | y | anchor | Window | Encoder |
|---|---|---|---|---|---|---|---|
| `2x2` | 1920x1088 ⚠ | `top_left` | 0 | 0 | top left | 960x544 | 1 or 2 |
|  |  | `top_right` | 960 | 0 | top left | 960x544 | 1 or 2 |
|  |  | `bottom_left` | 0 | 544 | top left | 960x544 | 1 or 2 |
|  |  | `bottom_right` | 960 | 544 | top left | 960x544 | 1 or 2 |
| `side-by-side` | 1920x1088 ⚠ | `left` | 0 | 272 | top left | 960x544 | 1 or 2 |
|  |  | `right` | 960 | 272 | top left | 960x544 | 1 or 2 |
| `pip-top-left` | 1920x1080 | `main` | 0 | 0 | top left | 1920x1080 | 1 or 2 |
|  |  | `top_left` | 32 | 32 | top left | 640x360 | 1 or 2 |
| `pip-top-right` | 1920x1080 | `main` | 0 | 0 | top left | 1920x1080 | 1 or 2 |
|  |  | `top_right` | 1888 | 32 | top right | 640x360 | 1 or 2 |
| `pip-bottom-left` | 1920x1080 | `main` | 0 | 0 | top left | 1920x1080 | 1 or 2 |
|  |  | `bottom_left` | 32 | 1048 | bottom left | 640x360 | 1 or 2 |
| `pip-bottom-right` | 1920x1080 | `main` | 0 | 0 | top left | 1920x1080 | 1 or 2 |
|  |  | `bottom_right` | 1888 | 1048 | bottom right | 640x360 | 1 or 2 |
| `1+3-horizontal-bottom` | 1920x1080 | `main` | 320 | 0 | top left | 1280x720 | 1 or 2 |
|  |  | `bottom_left` | 0 | 720 | top left | 640x360 | 1 or 2 |
|  |  | `bottom_middle` | 640 | 720 | top left | 640x360 | 1 or 2 |
|  |  | `bottom_right` | 1280 | 720 | top left | 640x360 | 1 or 2 |
| `1+3-horizontal-top` | 1920x1080 | `main` | 320 | 360 | top left | 1280x720 | 1 or 2 |
|  |  | `top_left` | 0 | 0 | top left | 640x360 | 1 or 2 |
|  |  | `top_middle` | 640 | 0 | top left | 640x360 | 1 or 2 |
|  |  | `top_right` | 1280 | 0 | top left | 640x360 | 1 or 2 |
| `1+3-vertical-right` | 1920x1104 ⚠ | `main` | 0 | 184 | top left | 1280x736 | 1 or 2 |
|  |  | `top_right` | 1280 | 0 | top left | 640x368 | 1 or 2 |
|  |  | `middle_right` | 1280 | 368 | top left | 640x368 | 1 or 2 |
|  |  | `bottom_right` | 1280 | 736 | top left | 640x368 | 1 or 2 |
| `1+3-vertical-left` | 1920x1104 ⚠ | `main` | 640 | 184 | top left | 1280x736 | 1 or 2 |
|  |  | `top_left` | 0 | 0 | top left | 640x368 | 1 or 2 |
|  |  | `middle_left` | 0 | 368 | top left | 640x368 | 1 or 2 |
|  |  | `bottom_left` | 0 | 736 | top left | 640x368 | 1 or 2 |
| `4-split` | 1920x1088 ⚠ | `main` | 0 | 136 | top left | 1440x816 | 1 or 2 |
|  |  | `top_right` | 1440 | 136 | top left | 480x272 | 1 or 2 |
|  |  | `middle_right` | 1440 | 408 | top left | 480x272 | 1 or 2 |
|  |  | `bottom_right` | 1440 | 680 | top left | 480x272 | 1 or 2 |

**Every window size has a matching scaler format, and every one fits Encoder 2.** At a
1080p canvas the Encoder-2 constraint disappears entirely — no window exceeds 1920x1080.
That makes 1920x1080 the *least* constrained canvas from a scaler standpoint, though it
also means a 4K display is driven below its native resolution.

Note that **five of eleven layouts snap the canvas** at 1080p (to 1088 or 1104), versus two
at 4K. The UI must surface the actual canvas.

---

## M. Canvas 2560x1440, and the "1440 vs 1080" discrepancy

The brief said to interpret "1080" as 1920x1080 and **not** to implement 2560x1440 unless
live device/API evidence showed it was the intended requirement, and to report the
discrepancy explicitly. Here is the evidence, which cuts both ways.

**Evidence that 2560x1440 is intended and real:**

- **[FW]** The decoder's own multiview page offers exactly three canvas presets:
  `vm.resolutions = ["3840x2160", "2560x1440", "1920x1080"]`. 2560x1440 is a first-class
  choice in the vendor UI, not an exotic value.
- **[LIVE]** `add_multiview` with 2560x1440 is accepted and stored verbatim.
- **[FW]** The encoder's 4K scaler list contains `2592x1440` and `2560x1440` — values that
  exist *only* to serve 2560-wide multiview canvases. Nothing else in the product needs
  2592x1440.

**Evidence that it is less well supported than the other two:**

- Six of the 44 window sizes at this canvas have **no matching encoder scaler format**:

  | Layout | Subframes | Window needed | In scaler list? |
  |---|---|---|---|
  | `1+3-horizontal-bottom` | `bottom_left`, `bottom_middle`, `bottom_right` | 896x480 | **no** |
  | `1+3-horizontal-top` | `top_left`, `top_middle`, `top_right` | 896x480 | **no** |

  The scaler list has `864x480` but not `896x480`. At 3840x2160 and 1920x1080 every single
  window size is covered. This looks like an oversight in the vendor's own tables rather
  than a deliberate restriction, but the effect is the same: those two layouts cannot be
  driven correctly at a 2560x1440 canvas.
- Nine of eleven layouts snap the canvas away from 2560x1440 (to 2592x1440 or 2688x1440) —
  a worse hit rate than either other preset.

**Recommendation.** Follow the brief: implement **3840x2160 and 1920x1080** in Phase 2, and
treat **2560x1440 as a known, documented gap** rather than a silent omission. The live
evidence does establish that the device supports the canvas, so this is a scoping decision,
not a capability limit — but shipping it would mean shipping two layouts that cannot be
correctly sourced. If 2560x1440 is later required, `1+3-horizontal-*` must either be
disabled for that canvas or accept a 864x480 source with a 32 px horizontal gap.

Full matrix for 2560x1440 is retained below for completeness.

| Layout (API id) | Canvas actual | Subframe | x | y | anchor | Window | Encoder |
|---|---|---|---|---|---|---|---|
| `2x2` | 2560x1440 | `top_left` | 0 | 0 | top left | 1280x720 | 1 or 2 |
|  |  | `top_right` | 1280 | 0 | top left | 1280x720 | 1 or 2 |
|  |  | `bottom_left` | 0 | 720 | top left | 1280x720 | 1 or 2 |
|  |  | `bottom_right` | 1280 | 720 | top left | 1280x720 | 1 or 2 |
| `side-by-side` | 2560x1440 | `left` | 0 | 360 | top left | 1280x720 | 1 or 2 |
|  |  | `right` | 1280 | 360 | top left | 1280x720 | 1 or 2 |
| `pip-top-left` | 2592x1440 ⚠ | `main` | 0 | 0 | top left | 2592x1440 | **1 only** |
|  |  | `top_left` | 32 | 32 | top left | 864x480 | 1 or 2 |
| `pip-top-right` | 2592x1440 ⚠ | `main` | 0 | 0 | top left | 2592x1440 | **1 only** |
|  |  | `top_right` | 2560 | 32 | top right | 864x480 | 1 or 2 |
| `pip-bottom-left` | 2592x1440 ⚠ | `main` | 0 | 0 | top left | 2592x1440 | **1 only** |
|  |  | `bottom_left` | 32 | 1408 | bottom left | 864x480 | 1 or 2 |
| `pip-bottom-right` | 2592x1440 ⚠ | `main` | 0 | 0 | top left | 2592x1440 | **1 only** |
|  |  | `bottom_right` | 2560 | 1408 | bottom right | 864x480 | 1 or 2 |
| `1+3-horizontal-bottom` | 2688x1440 ⚠ | `main` | 448 | 0 | top left | 1792x960 | 1 or 2 |
|  |  | `bottom_left` | 0 | 960 | top left | 896x480 | **none** |
|  |  | `bottom_middle` | 896 | 960 | top left | 896x480 | **none** |
|  |  | `bottom_right` | 1792 | 960 | top left | 896x480 | **none** |
| `1+3-horizontal-top` | 2688x1440 ⚠ | `main` | 448 | 480 | top left | 1792x960 | 1 or 2 |
|  |  | `top_left` | 0 | 0 | top left | 896x480 | **none** |
|  |  | `top_middle` | 896 | 0 | top left | 896x480 | **none** |
|  |  | `top_right` | 1792 | 0 | top left | 896x480 | **none** |
| `1+3-vertical-right` | 2592x1440 ⚠ | `main` | 0 | 240 | top left | 1728x960 | 1 or 2 |
|  |  | `top_right` | 1728 | 0 | top left | 864x480 | 1 or 2 |
|  |  | `middle_right` | 1728 | 480 | top left | 864x480 | 1 or 2 |
|  |  | `bottom_right` | 1728 | 960 | top left | 864x480 | 1 or 2 |
| `1+3-vertical-left` | 2592x1440 ⚠ | `main` | 864 | 240 | top left | 1728x960 | 1 or 2 |
|  |  | `top_left` | 0 | 0 | top left | 864x480 | 1 or 2 |
|  |  | `middle_left` | 0 | 480 | top left | 864x480 | 1 or 2 |
|  |  | `bottom_left` | 0 | 960 | top left | 864x480 | 1 or 2 |
| `4-split` | 2560x1472 ⚠ | `main` | 0 | 184 | top left | 1920x1104 | **1 only** |
|  |  | `top_right` | 1920 | 184 | top left | 640x368 | 1 or 2 |
|  |  | `middle_right` | 1920 | 552 | top left | 640x368 | 1 or 2 |
|  |  | `bottom_right` | 1920 | 920 | top left | 640x368 | 1 or 2 |

---

## N. The encoder: vc2, scalers, and sessions

**[LIVE]** Every bench encoder exposes exactly **2** `vc2` encoders and **6** `sessions`.

Bench state of `.142`:

| | `vc2_encoder1` | `vc2_encoder2` |
|---|---|---|
| bitrate | 700 | 150 |
| input | `hdmi_input1` | `hdmi_input1` |
| scaler | `{enable: false, width: 0, height: 0}` | `{enable: true, width: 1920, height: 1080}` |
| `thumbnail` key | present | **absent** |
| subsampling | 422 | 422 |

Both encoders read the **same physical HDMI input**. They are two encodes of one source,
not two sources.

### N.1 The scaler format tables

**[FW]** From the encoder's own web app — this is the definitive answer to "what
resolutions does the scaler accept":

```js
scaler_4k_formats = ['3840x2160','2880x1584','2592x1440','2560x1440','1920x1104']
scaler_hd_formats = ['1920x1080','1920x1072','1792x960','1728x960','1440x816','1280x736',
                     '1280x720','960x544','960x528','864x480','640x368','640x360','480x272']

vm.scaler_formats[0] = ['disable', ...scaler_4k_formats, ...scaler_hd_formats]  // Encoder 1
vm.scaler_formats[1] = [...scaler_hd_formats]                                   // Encoder 2
```

Two things to note, both load-bearing:

1. **Encoder 1 gets 4K + HD + `disable` (18 options). Encoder 2 gets HD only (13).**
   Encoder 2's ceiling is 1920x1080.
2. **Encoder 2 has no `disable` option.** Encoder 2 is always scaling. There is no
   pass-through mode on the second encoder.

These lists are not general-purpose resolutions — they are exactly the window sizes the
eleven layouts generate across the three canvas presets. The scaler tables were designed
backwards from the layout matrices, which is why §K and §L line up perfectly and why the
one gap in §M stands out as an oversight.

A format not in the list can still be written as raw `width`/`height` (the UI calls this
`custom`), but nothing guarantees the hardware honours it, and it was not tested.

### N.2 Sessions

**[LIVE]** Six sessions; sessions 1 and 2 are the ones that matter.

| | session1 | session2 |
|---|---|---|
| `video.encoder` | `vc2_encoder1` | `vc2_encoder2` |
| video stream | `239.70.132.254:1000`, enabled | `239.100.132.254:1000`, disabled |
| audio stream | `239.80.132.254:1000`, enabled | `239.69.132.254:5004`, disabled |

**[GUIDE]** The guide's advice — Session 1 video on encoder1, Session 2 video on encoder2,
audio only on Session 1 — matches the bench defaults exactly. Audio comes from one session;
duplicating it wastes bandwidth and gains nothing.

---

## O. The shared Encoder-2 scaler conflict

This is a real architectural constraint and Phase 2 must handle it explicitly rather than
hide it.

### O.1 The problem

Per §F.1, a multiview window's size is set by the **source encoder's scaler**. Per §N, an
encoder has two encoders sharing one HDMI input, and the scaler is a property of the
*encoder*, not of the *session* or the *stream*.

Therefore:

> **Every decoder consuming a given encoder's Session 2 gets the same window size.**

If decoder A wants that source as a 960x528 tile in a 4-split, and decoder B wants the same
source as a 1920x1080 tile in a 2x2, they are in direct conflict. Setting the scaler for one
silently changes the other. The device reports no error — the other decoder's window simply
changes size, and its layout breaks.

This scales badly: one encoder can feed many decoders, so a single scaler write is a
**fan-out mutation with no warning**.

### O.2 Made worse by Encoder 1's role

The natural workaround — "use Encoder 1 for the big window, Encoder 2 for small ones" — is
constrained from both ends:

- **Encoder 1 is usually the full-resolution feed** other decoders rely on. Its scaler is
  `disable` on the bench, i.e. native pass-through. Enabling a scaler on Encoder 1 to serve
  a multiview would degrade every non-multiview consumer of that source.
- **Encoder 2 cannot exceed 1920x1080** and cannot be disabled (§N.1).

Per §K, **nine of eleven layouts at a 4K canvas need a main window larger than 1920x1080**
(2560x1440, 2880x1584 or 3840x2160). Those *must* come from Encoder 1. Only the small tiles
can use Encoder 2.

So at 4K the allocation is essentially forced: main window → Encoder 1, side tiles →
Encoder 2. And since Encoder 1 normally carries the native feed, using a source as a
multiview *main* window at 4K conflicts with that source's ordinary use.

At a **1920x1080 canvas this problem disappears** — every window fits Encoder 2 (§L).

### O.3 What Phase 2 must do

Not solvable in software; it is a hardware topology constraint. It can be made visible and
safe:

1. **Model scaler ownership explicitly.** Track, per encoder, which multiviews on which
   decoders depend on `vc2_encoder1` and `vc2_encoder2`.
2. **Detect conflicts before applying.** If a requested window size differs from the
   scaler's current value *and* another known multiview depends on that encoder, warn
   naming the affected decoders and multiviews. Never silently retune a shared scaler.
3. **Prefer Encoder 2** for any window ≤1920x1080, reserving Encoder 1 for windows that
   genuinely require it. This keeps the native feed intact wherever possible.
4. **Surface the assignment in the UI**, per window: which encoder, which session, what
   scaler value. The user must be able to see why a change is risky.
5. **Treat a window >1920x1080 as requiring Encoder 1**, and say so in the UI rather than
   failing at apply time.

OmniSuite can only reason about conflicts among devices it knows. A third party — or the
device's own web UI — can change a scaler at any time. Point 2 is a warning, not a lock,
and the UI should not imply otherwise.

---

## P. Decoder wiring: ip_input, HDMI output, SAP input

### P.1 ip_input

**[LIVE]** 32 on 4xxx, 16 on `at-omni-121` (§C). Minimal write:

```json
{"config_set": {"name": "ip_input", "config": [
  {"name": "ip_input2", "enabled": true, "port": 1000,
   "multicast": {"address": "239.100.132.254"}}]}}
```

Stored shape also carries `interface` (`eth1`), `multicast.filter` and read-only
`status.packets` — a useful liveness signal (it incremented to 5702 during the proof).

**Bench mapping, which contradicts both the brief and the guide:**

| ip_input | Bench role |
|---|---|
| `ip_input1` | Session 1 **video** |
| `ip_input3` | Session 1 **audio** |
| `ip_input5` | aux |

The brief proposed 1/2/3/4/6/7; the guide uses 1/12/13. The bench uses 1/3/5. **The
numbering is a site convention, not an API rule.** Phase 2 must *discover* the mapping by
reading `ip_input` and `hdmi_output`, never assume it. In particular the brief's expectation
of `ip_input7` for aux is wrong here — it is `ip_input5`.

For allocation, pick the lowest-numbered disabled ip_input rather than a fixed slot.

### P.2 HDMI output

**[LIVE]** `hdmi_output` has one object, `hdmi_output1`, with keys `audio, aux, mcu, name,
output, sap_input, scrambling, standby, video`.

```json
"video": {"available_inputs": ["generator","ip_input1","ip_input3","multiviewOmniSuiteTest"],
          "input": "ip_input1"},
"audio": {"available_inputs": ["generator","ip_input1","ip_input3"], "input": "ip_input3"},
"aux":   {"available_inputs": ["ip_input1","ip_input3"], "input": "ip_input5"}
```

Key behaviours, all verified live:

- **`available_inputs` = enabled ip_inputs + defined multiviews + `generator`.** An ip_input
  must be enabled before it is selectable. This is the list to populate the UI from.
- **A multiview appears only in `video.available_inputs`** — never in `audio` or `aux`.
  **Multiview carries no audio.** Audio must stay on a separate ip_input, and switching the
  video input to a multiview does not change the audio input. This is why the live proof
  kept `audio.input = ip_input3` untouched.
- Selecting a multiview is one write:

```json
{"config_set": {"name": "hdmi_output", "config": [
  {"name": "hdmi_output1", "video": {"input": "multiviewOmniSuiteTest"}}]}}
```

- Note `aux.input` was `ip_input5`, which is **not** in its own `available_inputs` list. The
  device tolerates a selected input that is no longer offered. Phase 2's UI must not assume
  the current value is present in the options list, or it will blank the control.

**[FW]** `output.video.output.wall` — videowall — is mutually exclusive with multiview
("Videowall cannot be enabled with a Multiview"). The bench `hw-omni-d4511` has no `wall`
key at all (its `output` carries `aspect_ratio`, `framerate`, `fsm`, `resolution`,
`status`), so the conflict does not arise on this hardware. Phase 2 should check for the key
before relying on it. **[INFER]** On models that do expose it, multiview and videowall
cannot both be enabled.

### P.3 SAP input

**[LIVE]** `hdmi_output1.sap_input`:

```json
{"enabled": true,
 "session": "session1@hw-omni-e4111-08412",
 "available_sessions": ["session1@hw-omni-e4111-08414", ...]}
```

SAP is an *automatic* input selector: while enabled, it drives the output from an
advertised session. **It must be disabled before a multiview selection will hold**, which is
why the proof disabled it first and restored it afterwards. Session identifiers are
`<session>@<hostname>`, and `available_sessions` is a live discovery list — useful for
naming sources in the UI without contacting each encoder.

---

## Q. Multicast strategy, and source eligibility

### Q.1 Multicast: use the device's own generator

**[LIVE]** Every session already carries generation metadata:

| Session / stream | `destination_generate_default` | Current address |
|---|---|---|
| session1 video | `239.70.0.0/16:1000` | 239.70.132.254:1000 |
| session1 audio | `239.80.0.0/16:1000` | 239.80.132.254:1000 |
| session2 video | `239.100.0.0/16:1000` | 239.100.132.254:1000 |
| session2 audio | `239.69.0.0/16:5004` | 239.69.132.254:5004 |

`destination_generate_applied: true` on all four means the device generated these itself.
The host part (`.132.254`) is derived per device, so addresses are **deterministic and
unique per device per stream** without any central coordination.

**Recommendation: do not build a multicast allocator.** The brief said not to introduce one
unless an existing one could be reused; the correct answer is that **the devices already do
this**, better than OmniSuite could. Phase 2 should:

1. Read `destination_address` from the session and use it. Do not invent addresses.
2. Leave `destination_generate_applied` alone so the device keeps ownership.
3. Only write an explicit address if a user overrides it, and warn that this opts out of
   device generation.
4. Detect collisions read-only: if two enabled sessions on different encoders advertise the
   same address, surface it. **[INFER]** Not observed on the bench — with per-device host
   parts it should not occur — but it is cheap to check and expensive to debug.

**[GUIDE]** The guide suggests odd addresses for Session 1 and even for Session 2. The
device-generated scheme already separates them by /16 block, which is stronger. No reason to
impose the odd/even convention.

The live proof used the device's existing generated Session 2 address unchanged, and it
worked first time.

### Q.2 Source eligibility

A Multiview **target** is a decoder. The test is exact and cheap:

> `config_get multiview` succeeds → the device supports Multiview.
> It fails with `Config node not found` → it does not.

All five bench decoders pass; all five encoders fail (§C). This is one request and needs no
model table, so it stays correct on hardware that does not exist yet. It is, however, a
per-device request — see §R.1 for when to make it.

A Multiview **source** is an encoder session reaching a decoder ip_input. Eligibility rules:

1. The device is an encoder (has `vc2` and `sessions`).
2. **`AT-OMNI-111-WP` is excluded** per the brief. Implement as a model-string exclusion at
   the point the Multiview source list is built — **not** in discovery. Normal discovery
   behaviour for that device must not change; it stays visible, scannable and routable
   everywhere else. No `AT-OMNI-111-WP` is present on this bench, so this rule is
   **[INFER]** and untested against real hardware. The nearest device, `at-omni-111` at
   .218, is the non-WP variant and reports model `at-omni-111`; match must therefore be on
   the full `-WP` suffix, not a `111` substring, or the plain encoder will be excluded too.
3. **[INFER]** A source whose encoder is unreachable (like `.145`) should remain listed if
   it is already wired to an ip_input, but cannot have its scaler changed. The UI should
   show it as read-only rather than hiding it.

---

## R. Phase 2 design

Design only — none of this was built.

### R.1 Scan vs lazy load

The brief requires that discovery performance not materially regress, and prefers lazy
loading unless data is needed for source eligibility or basic page population.

**Nothing new in the scan path.** Multiview needs no additional per-device request during
discovery. Justification: multiview support is a property of *decoders*, which are already
identified by role in the existing scan, and the support probe is only needed when the user
opens the Multiview page for a specific device.

| Data | When | Why |
|---|---|---|
| device role (encoder/decoder), model, hostname, IP | **already in scan** | reuse; no change |
| `config_get multiview` support probe | **lazy** — on opening the page | one request per device, only when needed |
| `multiview` objects | **lazy** — on opening the page | the page's primary content |
| `ip_input` (list + enabled state) | **lazy** — on opening the page | needed for the input picker and count |
| `hdmi_output` (available_inputs, current, sap_input) | **lazy** — on opening the page | needed to show/set the output |
| `hdmi_output.sap_input.available_sessions` | **lazy** — with hdmi_output | free source names, no extra request |
| encoder `vc2` (scalers) | **lazy** — when a window's source is chosen | only needed for the conflict check (§O) |
| encoder `sessions` (multicast, encoder assignment) | **lazy** — with `vc2` | same trigger, same device |
| per-subframe `video.*.active` status | **on demand / explicit refresh** | see below |

Opening the page for one decoder costs **3 requests** (`multiview`, `ip_input`,
`hdmi_output`), plus 2 per encoder whose scaler is actually inspected.

**No page polling**, per the brief. Live status (`video.input.active`, `status.packets`) is
fetched on explicit refresh. **[FW]** The device's own UI uses a push `notification` event
on the WebSocket with a 250 ms debounce; that is the natural Phase 3 upgrade if live status
is wanted, and it needs no polling.

### R.2 The page

Reuse the existing page shell, appearance and settings implementations unchanged.

- **Target selector** — decoders only, filtered by the §Q.2 probe. Unsupported devices
  shown disabled with a reason, not hidden.
- **Multiview list** for the device: name, canvas, subframe count, and whether it is the
  currently selected HDMI output. Add / delete per §G.
- **Editor**, driven by layout:
  - canvas preset (3840x2160, 1920x1080; 2560x1440 deferred per §M) and layout picker
  - **show the snapped canvas** whenever it differs from the request (§J)
  - a proportional preview of the canvas with each window drawn at its computed size,
    labelled with its resolution and assigned encoder
  - per-window: source picker (validated against real ip_input names, §H.2), priority,
    anchor (all five, §H.1), optional cropper/repeat
  - a conflict banner when a window needs a scaler change that affects other decoders (§O.3)
- **Output control** — select the multiview on `hdmi_output1.video.input`, with a clear
  warning that **SAP input must be disabled** (§P.3) and that **audio stays on its own
  ip_input** (§P.2).
- Per existing convention, no native `alert`/`confirm`/`prompt`.

### R.3 Naming

Device-imposed (§H.1): must start with `multiview`, cannot be exactly `multiview`,
case-sensitive. Suggest `multiview<UserLabel>` with the prefix applied automatically and
shown, so the user understands the stored name. Subframes should follow the vendor
convention `<layout_key> (<width>x<height>)` (§I.2).

### R.4 The desired-state apply transaction

The user edits a desired state; OmniSuite computes a minimal ordered plan. **[GUIDE]** The
guide's programming order is sound and matches the constraints found live:

**Plan order:**

1. **Disable** the ip_inputs whose source or size is changing. (Avoids showing torn video
   mid-change.)
2. **Encoder scaler changes** (`vc2`) — only where required, only after the §O.3 conflict
   check has been shown and accepted.
3. **Encoder session changes** (`sessions`) — enable the stream, set encoder assignment.
   Use the device-generated multicast address (§Q.1).
4. **ip_input** — point at the stream, then **re-enable**.
5. **Multiview** — `add_multiview` for new objects, `config_set` for edits,
   `del_multiview` for removals (§G).
6. **HDMI output** — disable `sap_input`, then set `video.input`. Two separate writes; the
   proof showed the order matters.

**Verification is mandatory, not optional.** Per §H.2 the device accepts invalid input and
returns `error: false`, so after each stage OmniSuite must **read back and compare against
the desired state**. A silent no-op is the expected failure mode here, not an exception.

**Rollback:** capture a `BEFORE` snapshot of every node the plan touches (`multiview`,
`ip_input`, `hdmi_output` on the decoder; `vc2`, `sessions` on each encoder) before the
first write. On failure, replay it in reverse. Two honest caveats:

- **[INFER]** Rollback is best-effort. There is no device-side transaction. A failure
  between stages can leave the system in a state neither desired nor original, and a second
  failure during rollback cannot be recovered automatically. The UI must report exactly
  which stage failed and what was restored.
- **Encoder changes have fan-out** (§O.1). Rolling back a scaler restores the previous value
  for *all* consumers, which is correct, but it means a failed apply can briefly disturb
  decoders the user was not editing.

This snapshot/restore/diff pattern is exactly what was used for the live proof in this
phase, and it worked — the diff afterwards showed zero unintended differences (§B).

---

## Live proof (performed once, then reversed)

Picture-in-picture on `.32`, sourced from `.142` Session 2 via `vc2_encoder2` at 1920x1080:

```
ok  encoder session2 video stream enabled
ok  decoder ip_input2 enabled on the Session 2 stream
ok  multiview multiviewOmniSuiteTest created with 2 subframes
ok  decoder hdmi_output1.sap_input.enabled = false
ok  decoder hdmi_output1.video.input = multiviewOmniSuiteTest

multiview present : True | canvas 3840x2160
subframe1: input=ip_input1 anchor=top left     priority=1 in_active=True out_active=True
subframe2: input=ip_input2 anchor=bottom right priority=2 in_active=True out_active=True
hdmi_output video.input : multiviewOmniSuiteTest
offered in available    : True
sap_input.enabled       : False
output status           : 3840x2160 active=True
ip_input1: enabled=True 239.70.133.236:1000 packets=62152
ip_input2: enabled=True 239.100.132.254:1000 packets=5702
encoder session2 video  : enabled=True encoder=vc2_encoder2 239.100.132.254
```

Both subframes active on input and output, output at full 3840x2160, packets flowing on
both streams. This exercised every mechanism Phase 2 depends on: session enable, ip_input
binding, multiview creation, SAP disable and output selection. All five mutations were
reversed (§B).

---

## S. Phase 2 implementation notes, and corrections to this document

Phase 2 built the Multiview page against these findings. Sections A–R are the
Phase 1 record and are left intact; this section records what implementation and
live testing corrected or added.

### S.1 Corrections to Phase 1

| Where | Was | Is |
|---|---|---|
| §K, §O.2 | "seven of eleven layouts at a 4K canvas need Encoder 1" | **nine of eleven** — every layout except `2x2` and `side-by-side`. Corrected in place. The 44-row table in §K was right; the sentence summarising it was not, and an automated test now counts it. |
| §Q.2 | the `AT-OMNI-111-WP` exclusion was **[INFER]**, untested, and Phase 2 implemented it as a `-WP` suffix match | **Corrected in Phase 3.** The suffix rule was wrong: it excluded `HW-OMNI-E4111-WP` (.253), which is a 4xxx wall plate and a perfectly good source. Exactly one model is barred — `AT-OMNI-111-WP` — matched as a whole normalised identity. See §S.5. |

Everything else in §A–§R was borne out. The geometry engine reproduces all 102
documented rows exactly, and the live 4K PiP placed its inset at x=3808 y=2128
anchor `bottom right`, which is the value in §K.

### S.2 Behaviour Phase 1 did not reach

**Switching an existing Multiview to a different layout needs an explicit
prune.** `config_set` merges by subframe name (§E.1), so the previous layout's
windows survive. Since a Multiview is capped at four subframes (§H.1), writing
four new names on top of four old ones is refused outright. The subframes the new
layout does not have must be removed first, with `del_multiview_subframe`.
Verified live: a 2x2 switched to `side-by-side` pruned four windows and wrote
two.

**An encoder reports its input differently from a decoder's output.** An encoder
uses `hdmi_input[0].video.resolution` with `video.active`; a decoder uses
`hdmi_output[0].video.output.status`. They are not the same shape. This matters
because it is the only way to recognise that Encoder 1 is already passing the
wanted size through, and therefore the only way to avoid enabling a scaler on the
shared native feed unnecessarily. A disconnected input can still report the last
size it saw, so `active` is what proves a source is present.

**The scan's own field names differ by role.** An encoder's session multicast is
recorded as `sessionN_video_mcast` / `sessionN_video_port`. `ipN_addr` is the
decoder-side field — the address a decoder *listens* to — so a decoder subscribed
to the same stream matches it. Resolving a window's source must consider encoders
only.

**`method` is a write, and the test fence did not treat it as one.** The
process-wide fence in `tests/test_usb_integration.py` refused `config_set` and
let `method` through. `add_logo`, `delete_logo`, `add_multiview` and
`del_multiview` all travel that way and carry no `config_set` key, so every one
of them could have reached a real bench device from a unit test. The fence now
refuses both, and three tests assert it.

### S.3 The retained Phase 1 object

`multiviewOmniSuiteTest` on **192.168.100.32** is still there, and was used
exactly as intended: it validated the read path, source resolution, and — most
usefully — the honest-layout requirement. It was hand-built in Phase 1 with both
subframes at x=0 y=0, which matches no generated layout, so it reads back as
**Custom / Unknown** rather than being given a name it does not fit. It is inert:
not selected on the output, and `ip_input2` is disabled.

It found one real defect. Its `subframe1` resolved its source to a *decoder*,
because the lookup matched any device recording that multicast and a decoder
records the address it listens to in a similar field. That is §S.2's third item.

### S.4 Live Phase 2 validation

Run through the production endpoints, not a bench script. Three transactions were
applied and reversed on .32 / .142:

| Test | Result |
|---|---|
| 1080p `2x2`, one source | `VERIFIED`, 6 stages, live video on the window (45,707 packets) |
| modify to `side-by-side` | `VERIFIED`, 4 subframes pruned, 2 written, geometry matched §L |
| 4K `pip-bottom-right`, both encoders | `VERIFIED`, 8 stages, Encoder 1 at 3840x2160 and Encoder 2 at 1280x720 |
| delete (object on the output) | `VERIFIED`, output moved to `ip_input1` first, then removed |
| restart and reconcile | metadata restored from disk, layout still `2x2`, geometry still agreed |

After restoration a field-by-field diff against the pre-Phase-2 snapshot — all 32
ip_inputs, HDMI output video/audio/aux, SAP state and session, both vc2 scalers
and bitrates, both sessions — showed **zero differences**. Only
`multiviewOmniSuiteTest` remains.

The 4K PiP is worth noting as the case §O warned about: it enabled a scaler on
Encoder 1, which is the shared native feed. OmniSuite warned before Apply and the
change was real, so the warning is not theoretical.

---

## T. Phase 3 corrections

### T.1 The source exclusion was too broad

Phase 1 recorded the exclusion as **[INFER]** and Phase 2 implemented it as
"model ends with `-WP`". Both readings treated it as a wall-plate rule. It is not.

| Model | Eligible? | Why |
|---|---|---|
| `AT-OMNI-111-WP` | **no** | the older OmniStream 2.0 wall plate, the one model actually barred |
| `HW-OMNI-E4111-WP` | **yes** | a 4xxx wall plate, and an ordinary encoder in every way that matters |
| `AT-OMNI-111` | **yes** | never in question, but a `111` substring rule would have taken it |

**[LIVE]** The 4xxx wall plate at **192.168.100.253** (`hw-omni-e4111-wp-01003`)
reports the same capabilities as any other supported encoder — two `vc2`
encoders (700/150 Mb/s), six sessions, `destination_generate_applied` on both
Session 1 and Session 2 — and in Phase 3 it drove a live Multiview window on .32
at 960x544 with 18,620 packets received. It is eligible on the evidence, not by
assumption.

No `AT-OMNI-111-WP` is physically present on the bench, so that half of the rule
is covered by model tests only. That is stated rather than papered over.

### T.2 Behaviour added in Phase 3

**One OmniSuite Multiview per canvas resolution.** A decoder holds at most one
managed Multiview per exposed canvas. The count includes only what OmniSuite
manages, so `multiviewOmniSuiteTest` — created by hand in Phase 1 — reserves
nothing and is never overwritten, which is the conservative handling §22.G of the
brief asked for. A metadata record stops reserving a canvas as soon as its object
is no longer on the device.

**Saving and showing are separate operations.** Phase 2 folded the output
selection into Apply. `POST /api/multiview/show` now carries it alone, with the
same snapshot, read-back and rollback. Saving changes nothing on screen, which
also makes bench work markedly safer: a Multiview can be built and verified
without disturbing whatever the decoder is currently displaying.

**The canvas of an existing Multiview is read-only.** Moving one to another
resolution would either collide with the Multiview already there or require a
migration nobody has validated. Deleting and recreating is the supported path.

### T.3 Live Phase 3 validation

Driven through the real UI in a browser, against .32 and the wall plate at .253:

| Step | Result |
|---|---|
| open the page | only the decoder picker is present |
| select .32 | Multiview selector appears; `multiviewOmniSuiteTest` listed as Custom / Unknown; creation controls still hidden |
| New Multiview | both canvases offered, neither reserved by the unmanaged object |
| create 1080p `2x2` fed by .253 | `VERIFIED`; wall plate Encoder 2 scaled to 960x544, Session 2 enabled, `ip_input4` receiving 18,620 packets |
| after Save | **"Not currently shown on display"** — Save changed nothing on screen |
| Show on Display | `VERIFIED`; output switched, SAP disabled, audio untouched |
| New Multiview again | `1920x1080 — Already configured`, disabled; 3840x2160 still free |
| change layout to `side-by-side` | `VERIFIED`; four subframes pruned, two written |
| delete | `VERIFIED`; display moved to `ip_input1` first, object absent afterwards |
| after delete | both canvases free again |

Afterwards a 46-field diff against the pre-test snapshot — the decoder's HDMI
output, SAP, every ip_input, and the wall plate's two scalers and two sessions —
showed **zero differences**. Only `multiviewOmniSuiteTest` remains, as before.

---

## U. Phase 4: the full layout matrix, and what it found

Every one of the eleven layouts was created on decoder **192.168.100.32** at both
exposed canvases — 22 live cases — through the production Save transaction, each
verified by independent read-back of the decoder and of every source encoder.

### U.1 Three device constraints Phase 1 did not reach

**[LIVE]** All three were found by running the matrix, not by reading.

**A decoder cannot open one multicast address and port on two ip_inputs.**
Pointing a second input at an address:port already open elsewhere is refused:

```
Unable to open eth1:1000 (ip_input11)
```

Proven directly: same address + same port → refused; different address + same
port → accepted. This is the constraint that decides how ip_inputs must be
allocated (§U.2).

**An ip_input port must be a multiple of four.**

```
Port must be a multiple of four
```

Returned for port 1002. The bench uses 1000 throughout, so nothing here depends
on it, but it bounds any future port handling.

**A refused write can be reported as an authentication failure.** The same
rejected write surfaced as `Invalid username/password` through one path and
`Unable to open eth1:1000 (ip_input10)` through another. The credentials were
correct and unchanged. **Do not trust that error text**; read the device back and
report what it actually holds, which is what made this diagnosable at all.

### U.2 A Phase 2 assumption, corrected

Phase 2 held that an ip_input carrying HDMI audio, aux, or the HDMI video input
must never be reused for a Multiview window — reasoning that reusing it would
take that function away. A test asserted it. **Hardware disproved it twice:**

- A subframe only *references* an ip_input. Nothing about the input changes, so
  the decoder goes on using it for audio or video exactly as before.
- A second input cannot be opened on a stream that is already open, so allocating
  a fresh input for a stream some other input already carries is a write that
  *cannot succeed*.

The allocator now **shares** an input that already carries the exact address and
port, whatever else it is doing, and writes nothing to it. A free input is still
chosen for anything else, and audio and aux are still never repurposed for a
different stream. The old test has been replaced by three that assert the new
behaviour, and the wrong one is described in place so the reasoning is not lost.

This is why the 4K `main` window is repeatedly allocated `ip_input1` in the
matrix below: `ip_input1` already carries `239.70.133.236`, which is exactly the
stream that window wants.

### U.3 One source cannot serve two differently-sized windows

A scaler belongs to the encoder, so two windows drawn from one source through the
same encoder get one size between them. Phase 2 detected this across *decoders*
but not **within a single Multiview**: a 1080p PiP with the same source in both
windows planned `main` at 1920x1080 and its inset at 640x360, emitted one scaler
write, and reported success — leaving `main` silently at the wrong resolution.

Now refused as a conflict that names both windows. Different encoders on one
source remain fine, which is exactly how a 4K PiP works.

### U.4 An unreachable source no longer stalls the page

`hw-omni-e4111-08412` lives at **10.1.1.33** and answered neither ICMP nor TCP/80.
The record is not stale — the MAC is unique and the hostname matches.

> **Corrected in Phase 4B: that device was switched off.** Its silence was
> expected and was never evidence of a defect. The behaviour built in response is
> still right — an operator meets an offline encoder for many reasons — but the
> subnet reasoning below was unnecessary. See §V.1.

Reading it cost **27.5 seconds**: three config nodes, each retried with the
fallback password, six WebSocket timeouts in series. During that time the page
showed "Assign at least one source", which was untrue, and Save sat disabled with
no explanation. Sources are now preflighted with the same short TCP probe the
scan uses and read concurrently:

| | before | after |
|---|---|---|
| one reachable source | 0.7s | 0.8s |
| one unreachable source | **27.5s** | **1.6s** |
| four sources, one unreachable | 28.5s | 1.7s |

### U.5 The 22-case matrix

Decoder `192.168.100.32`. Sources: `hw-omni-e4521-00002` (.141),
`hw-omni-e4111-08414` (.142), `hw-omni-e4521-856c` (.143), and
`hw-omni-e4521-85e6` (192.168.200.144) standing in for the unreachable .33 —
marked `*` wherever it was used. One object per canvas, taken through all eleven
layouts in turn, then deleted.

| Canvas | Layout | Win | E1 | E2 | ip_inputs | Scalers | Geometry | Save | Display |
|---|---|---|---|---|---|---|---|---|---|
| 3840x2160 | 2x2 | 4 | 0 | 4 | 2,4,6,7* | 4/4 | match | VERIFIED | tested |
| 3840x2160 | side-by-side | 2 | 0 | 2 | 4,2 | 2/2 | match | VERIFIED | — |
| 3840x2160 | pip-top-left | 2 | 1 | 1 | 1,4 | 2/2 | match | VERIFIED | — |
| 3840x2160 | pip-top-right | 2 | 1 | 1 | 8,4 | 2/2 | match | VERIFIED | — |
| 3840x2160 | pip-bottom-left | 2 | 1 | 1 | 9,6 | 2/2 | match | VERIFIED | — |
| 3840x2160 | pip-bottom-right | 2 | 1 | 1 | 1,7* | 2/2 | match | VERIFIED | tested |
| 3840x2160 | 1+3-horizontal-bottom | 4 | 1 | 3 | 1,4,6,7* | 4/4 | match | VERIFIED | — |
| 3840x2160 | 1+3-horizontal-top | 4 | 1 | 3 | 1,4,6,7* | 4/4 | match | VERIFIED | — |
| 3840x2160 | 1+3-vertical-right | 4 | 1 | 3 | 1,4,6,7* | 4/4 | match | VERIFIED | tested |
| 3840x2160 | 1+3-vertical-left | 4 | 1 | 3 | 1,4,6,7* | 4/4 | match | VERIFIED | — |
| 3840x2160 | 4-split | 4 | 1 | 3 | 1,4,6,7* | 4/4 | match | VERIFIED | — |
| 1920x1080 | 2x2 | 4 | 0 | 4 | 2,4,6,7* | 4/4 | match | VERIFIED | tested |
| 1920x1080 | side-by-side | 2 | 0 | 2 | 4,2 | 2/2 | match | VERIFIED | — |
| 1920x1080 | pip-top-left | 2 | 0 | 2 | 2,4 | 2/2 | match | VERIFIED | — |
| 1920x1080 | pip-top-right | 2 | 0 | 2 | 6,4 | 2/2 | match | VERIFIED | — |
| 1920x1080 | pip-bottom-left | 2 | 0 | 2 | 4,6 | 2/2 | match | VERIFIED | — |
| 1920x1080 | pip-bottom-right | 2 | 0 | 2 | 2,7* | 2/2 | match | VERIFIED | tested |
| 1920x1080 | 1+3-horizontal-bottom | 4 | 0 | 4 | 2,4,6,7* | 4/4 | match | VERIFIED | — |
| 1920x1080 | 1+3-horizontal-top | 4 | 0 | 4 | 2,4,6,7* | 4/4 | match | VERIFIED | — |
| 1920x1080 | 1+3-vertical-right | 4 | 0 | 4 | 2,4,6,7* | 4/4 | match | VERIFIED | tested |
| 1920x1080 | 1+3-vertical-left | 4 | 0 | 4 | 2,4,6,7* | 4/4 | match | VERIFIED | — |
| 1920x1080 | 4-split | 4 | 0 | 4 | 2,4,6,7* | 4/4 | match | VERIFIED | — |

**22 of 22 verified, 0 problems.** Every case was checked against the layout
engine for canvas size, subframe names, x, y and anchor; against the encoder rule
for encoder and session; against the device for scaler value, multicast address,
ip_input allocation and packet flow; and against the baseline for inputs it must
not have touched.

### U.6 What the matrix confirms

**Encoder selection matches the rule exactly.** At 4K, nine of eleven layouts
need one Encoder-1 window — every layout except `2x2` and `side-by-side`, which
is the count §K records. At **1920x1080 not one window in any layout used
Encoder 1**, which is the measured form of the claim that a 1080p canvas leaves
the native feed alone.

**Encoder 1 scalers** were set to exactly the sizes §K predicts: 3840x2160 for a
PiP main, 2560x1440 for the four `1+3` layouts, 2880x1584 for `4-split` — each
read back from the encoder, each with packets flowing on the decoder input.

**Encoder 1 bitrate was never rewritten.** All three primary sources sat at 750,
700 and 700 Mb/s; the rule reduces only above 750, and correctly did nothing.

**Audio and aux were never taken.** `ip_input3` (HDMI audio) and `ip_input5`
(aux) were not allocated in any of the 22 cases, and HDMI audio stayed on
`ip_input3` throughout.

**Pruning holds across every transition.** One object was taken through all
eleven layouts per canvas — 4 → 2 → 2 → 2 → 2 → 2 → 4 → 4 → 4 → 4 → 4 subframes —
and every case was verified by exact subframe name, so a stale window from the
previous layout would have failed it. None did.

**Saving never changed the display.** Six representative cases: after Save the
output was still on its previous input; Show on Display then switched it,
verified, with HDMI audio untouched.

### U.7 A resource the transaction deliberately does not reclaim

After a layout change stops using an ip_input, that input is left enabled and
still subscribed. This is the same conservative stance taken for encoder streams:
OmniSuite cannot prove nothing else wants it. Across 22 cases six inputs
accumulated this way. They are inert, but an operator running many layout changes
will see inputs left subscribed, and reclaiming them is a candidate for a later
phase rather than something to do silently.

---

## V. Phase 4B: corrected pool, output resolution, and the ip_input lifecycle

Phase 4B reran the matrix with a corrected source pool, made the decoder's HDMI
output resolution follow the Multiview canvas, and closed the ip_input lifecycle
question §U.7 left open. A machine reboot interrupted the phase before it began;
nothing had been written, and it restarted from a freshly captured baseline.

### V.1 Correcting the Phase 4 reading of 10.1.1.33

**10.1.1.33 was switched off.** Phase 4 reported it as unreachable and reasoned
about subnets; that reasoning was unnecessary. The device was simply offline, so
its failure to answer was expected and was never evidence of an OmniSuite defect.

The behaviour Phase 4 built in response — preflighting a source rather than
spending six WebSocket timeouts on it, and reading sources concurrently — remains
correct and is kept. An operator will meet an offline encoder whatever the reason,
and 27.5 seconds of apparent silence is the wrong answer to it.

The corrected pool replaces it with the 4xxx wall plate:

| Source | Model | Role in the matrix |
|---|---|---|
| 192.168.100.253 | `hw-omni-e4111-wp` | W1, and both windows of the PiP rotations |
| 192.168.100.141 | `hw-omni-e4521` | W2 |
| 192.168.100.142 | `hw-omni-e4111` | W3 |
| 192.168.100.143 | `hw-omni-e4521` | W4 |

### V.2 The decoder output resolution follows the canvas

**[LIVE]** The field is `hdmi_output[0].video.output.resolution`, a string from the
decoder's own HDMI Output list: `input`, `auto`, `4096x2160`, `3840x2160`,
`1920x1200`, `1920x1080`, and smaller. `input` and `auto` are never used — the
canvas decides the output, not the incoming signal.

A canvas is frequently snapped (1920x1088, 3840x2112), so the preset is recovered
from the **width**, which is unambiguous between the two exposed sizes.

**Setting and status are different things.** `video.output.resolution` is what
OmniSuite writes and verifies. `video.output.status.resolution` is what the sink
negotiated, and on this bench stayed 1920x1080 even with the output set to
3840x2160, because the attached display is 1080p. The status is reported and is
never a pass condition — it is a property of the display, not of the write.

This belongs to **Show on Display**, not to Save. Changing the output resolution
changes the operator's picture, and that is the one thing Save must never do. The
Show transaction now: sets the resolution, verifies it, disables SAP, selects the
Multiview, verifies it — snapshotting all three so any failure restores all three.

Verified live in both directions:

| Transition | Output before | Written | Read back | Active input | Audio |
|---|---|---|---|---|---|
| 1080p → 4K | 1920x1080 | 3840x2160 | **3840x2160** | `multiviewRes4K` | unchanged |
| 4K → 1080p | 3840x2160 | 1920x1080 | **1920x1080** | `multiviewResHD` | unchanged |

Aspect ratio and framerate mode were unchanged by both, confirmed by read-back:
writing `resolution` alone does not disturb the other output fields.

### V.3 The corrected 22-case matrix

All 22 created on `.32` through the production Save transaction and verified by
independent read-back. **22 of 22 verified, 0 problems.**

| Canvas | Layout | Win | E1 | E2 | ip_inputs | Scalers | Geometry | Save |
|---|---|---|---|---|---|---|---|---|
| 3840x2160 | 2x2 | 4 | 0 | 4 | 4,6,7,8 | 4/4 | match | VERIFIED |
| 3840x2160 | side-by-side | 2 | 0 | 2 | 4,6 | 2/2 | match | VERIFIED |
| 3840x2160 | pip-top-left | 2 | 1 | 1 | 10,7 | 2/2 | match | VERIFIED |
| 3840x2160 | pip-top-right | 2 | 1 | 1 | 9,4 | 2/2 | match | VERIFIED |
| 3840x2160 | pip-bottom-left | 2 | 1 | 1 | 10,8 | 2/2 | match | VERIFIED |
| 3840x2160 | pip-bottom-right | 2 | 1 | 1 | 11,4 | 2/2 | match | VERIFIED |
| 3840x2160 | 1+3-horizontal-bottom | 4 | 1 | 3 | 10,6,7,8 | 4/4 | match | VERIFIED |
| 3840x2160 | 1+3-horizontal-top | 4 | 1 | 3 | 10,6,7,8 | 4/4 | match | VERIFIED |
| 3840x2160 | 1+3-vertical-right | 4 | 1 | 3 | 10,6,7,8 | 4/4 | match | VERIFIED |
| 3840x2160 | 1+3-vertical-left | 4 | 1 | 3 | 10,6,7,8 | 4/4 | match | VERIFIED |
| 3840x2160 | 4-split | 4 | 1 | 3 | 10,6,7,8 | 4/4 | match | VERIFIED |
| 1920x1080 | 2x2 | 4 | 0 | 4 | 4,6,7,8 | 4/4 | match | VERIFIED |
| 1920x1080 | side-by-side | 2 | 0 | 2 | 4,6 | 2/2 | match | VERIFIED |
| 1920x1080 | pip-top-left | 2 | 0 | 2 | 4,7 | 2/2 | match | VERIFIED |
| 1920x1080 | pip-top-right | 2 | 0 | 2 | 8,4 | 2/2 | match | VERIFIED |
| 1920x1080 | pip-bottom-left | 2 | 0 | 2 | 4,8 | 2/2 | match | VERIFIED |
| 1920x1080 | pip-bottom-right | 2 | 0 | 2 | 7,4 | 2/2 | match | VERIFIED |
| 1920x1080 | 1+3-horizontal-bottom | 4 | 0 | 4 | 4,6,7,8 | 4/4 | match | VERIFIED |
| 1920x1080 | 1+3-horizontal-top | 4 | 0 | 4 | 4,6,7,8 | 4/4 | match | VERIFIED |
| 1920x1080 | 1+3-vertical-right | 4 | 0 | 4 | 4,6,7,8 | 4/4 | match | VERIFIED |
| 1920x1080 | 1+3-vertical-left | 4 | 0 | 4 | 4,6,7,8 | 4/4 | match | VERIFIED |
| 1920x1080 | 4-split | 4 | 0 | 4 | 4,6,7,8 | 4/4 | match | VERIFIED |

The encoder rule holds exactly as §U.6 recorded: nine of eleven 4K layouts need
one Encoder-1 window, and **no 1080p window in any layout used Encoder 1**.

### V.4 The wall plate across both encoders

`.253` drove **22 windows** and was exercised as both the large and the small
window of every PiP rotation. Nine distinct scaler sizes, each read back from the
encoder with packets arriving at the decoder:

| Encoder | Sizes driven |
|---|---|
| Encoder 1 / Session 1 | 3840x2160, 2880x1584, 2560x1440 |
| Encoder 2 / Session 2 | 1920x1080, 1920x1072, 1440x816, 1280x736, 1280x720, 960x544, 640x360 |

HW-OMNI-E4111-WP is a Multiview source in every respect the product depends on.
`AT-OMNI-111-WP` remains the one excluded model, matched on full normalised
identity.

### V.5 ip_input lifecycle: reclamation, with ownership proven

§U.7 left this open. Ownership is now provable for exactly one case, and
reclamation is limited to it.

**OmniSuite owns an input when it had to switch it on for a Multiview it manages.**
Sharing — using an input that was *already enabled* and carrying the wanted
stream — never confers ownership, because that input may also be the decoder's
video or audio source. A claim survives a later save that merely reuses the same
input, and a released input keeps its address, so the next Multiview that picks it
up is switching it on again and owns it again.

An input is released only when **all** of these hold: OmniSuite claimed it; the
new configuration no longer needs it; no other Multiview on the decoder references
it; it carries no other role; and it still holds the address OmniSuite gave it.
Anything else is left alone and the reason is reported.

Reclamation is cleanup, so it never fails the operation that ran it — a Multiview
that saved correctly is still saved if an input could not be released, and the
failure is reported rather than swallowed.

**[LIVE]** From a clean baseline of two enabled inputs:

| Step | Enabled inputs | Released |
|---|---|---|
| create `2x2` (4 sources) | 1, 4, 6, 7, 8, 10 | — |
| → `side-by-side` | 1, 4, 6, 10 | 7, 8 |
| → `pip-bottom-right` | 1, 4, 6, 10 | — |
| → `1+3-vertical-right` | 1, 4, 6, 7, 8, 10 | — |
| → `4-split` | 1, 4, 6, 7, 8, 10 | — |
| → `pip-top-left` | 1, 4, 6, 10 | 7, 8 |
| delete | **1, 10** (baseline) | 4, 6 |

`ip_input1` and `ip_input10` — the decoder's own inputs — were never touched, and
four consecutive create/change/delete cycles each returned to exactly the baseline
two. The resource-exhaustion question is answered: capacity is stable, not
monotonically decreasing.

### V.6 Two more device behaviours found

**One stream must map to one ip_input, within a plan as well as across it.** A
source assigned to several equally-sized windows — legal, and the obvious way to
build a quad view of one camera — produced one ip_input *per window*, which the
decoder refuses because it cannot open one address and port twice. The allocator
now resolves all windows drawing the same stream to a single input. Four windows
on one stream now produce one input and one scaler write.

**SAP rewrites ip_inputs and then disables itself.** With SAP enabled on the
decoder, enabling it was observed to repoint `ip_input1` from one encoder's
session to the SAP-advertised session's address, then turn itself off and leave
the output on `generator`. This is the decoder managing its own inputs
asynchronously, underneath whatever else is configuring it. It explains input
state that appeared to change on its own between phases, and it is the reason
Show disables SAP before selecting a Multiview. A decoder with SAP enabled may
have Multiview inputs repointed by the device until the Multiview is shown.

### V.7 Restoration

145 fields compared against the Phase 4B baseline — the decoder's HDMI output,
resolution, aspect, framerate mode, SAP, all 32 ip_inputs, and all four encoders'
scalers, bitrates, sessions and destinations. **0 unintended differences.**
`multiviewOmniSuiteTest` is present and unchanged.

One field, `video.output.aspect_ratio`, was found on "fullscreen" rather than
"keep aspect ratio" during restoration and was put back. It was tested and
confirmed that writing `resolution` alone does not change it, so the OmniSuite
writes were not the cause; the bench had a browser session open on the decoder's
own web UI throughout.


---

## W. Phase 5: 1080p only, the Encoder-2 pipeline, bandwidth and decoder health

Phase 5 changes the architecture rather than extending it. Three decisions do
most of the work, and each of them removes a class of failure rather than
detecting it:

1. **One canvas.** 1920x1080 is the only canvas the product plans, applies or
   shows. Every other size is dormant.
2. **One encoder.** Every window comes from Encoder 2 and Session 2, so the
   source's primary stream is never retuned to build a Multiview.
3. **One input per window.** Window N always lands on a reserved decoder input,
   and an input the product cannot account for is refused rather than taken.

### W.1 The canvas is fixed, and that is enforced in the planner

`ACTIVE_CANVAS = "1920x1080"`. `plan_multiview` refuses anything else before it
looks at a layout, a source or a decoder, so a 4K plan cannot be built even by
calling the planner directly with a canvas the UI does not offer. The 4K and
2560x1440 geometry, the 4K scaler table and `encoder_for_window_size` all stay in
`omni_multiview.py` so the capability can be revived as a flag change, and
`ActiveCanvasOnlyTests` asserts that nothing in the active path reaches them.

**Why 4K went dormant.** At a 4K canvas, nine of the eleven layouts have a main
window larger than 1920x1080, which only Encoder 1 can produce -- and Encoder 1
carries the feed other decoders depend on. The 1080p canvas is the one where
that trade-off does not arise.

`output_resolution_for_canvas` now maps only exposed canvases, which is what
makes a 4K object left over from an earlier release unshowable through the
production path instead of silently driving the display to 4K.

### W.2 Every window fits Encoder 2 exactly

**[LIVE]** All eleven layouts at 1920x1080 produce window sizes that are in the
Encoder-2 scaler table verbatim -- no rounding, no nearest match:

| Layout | Canvas | Window sizes |
|---|---|---|
| 2x2 | 1920x1088 | 960x544 x4 |
| Side-by-Side | 1920x1088 | 960x544 x2 |
| PiP (x4) | 1920x1080 | 1920x1080 + 640x360 |
| 1+3 horizontal (x2) | 1920x1080 | 1280x720 + 640x360 x3 |
| 1+3 vertical (x2) | 1920x1104 | 1280x736 + 640x368 x3 |
| 4-Split | 1920x1088 | 1440x816 + 480x272 x3 |

A window Encoder 2 cannot produce is refused by name. It is never promoted to
Encoder 1, because doing so quietly is the behaviour this phase removed.

The compositor canvas is still snapped to the layout grid -- 1920x1088,
1920x1104 -- while the **display** always runs at 1920x1080. Those are two
different numbers and the page now says so in those words.

### W.3 Encoder 2's physical input

**[LIVE]** Two of the four bench sources shipped with `vc2_encoder2.input = ""`,
which the device's web application shows as **Not used**:

| Source | Model | Encoder 2 input as found |
|---|---|---|
| 192.168.100.141 | `hw-omni-e4521` | `hdmi_input1` |
| 192.168.100.142 | `hw-omni-e4111` | `hdmi_input1` |
| 192.168.100.143 | `hw-omni-e4521` | **`""` (Not used)** |
| 192.168.100.253 | `hw-omni-e4111-wp` | **`""` (Not used)** |

Multiview preparation now reads Encoder 1's input and Encoder 2's, and:

* **the same** -- nothing is written;
* **Not used** -- Encoder 2 is pointed at Encoder 1's input, read back, and the
  operator is told before Apply;
* **a different active input** -- refused as a conflict. OmniSuite cannot prove
  that input is free, so it says so and leaves it alone;
* **Encoder 1 itself has no input** -- there is nothing to follow, and that is an
  error rather than a guess.

The write is the first stage of the transaction, ahead of everything that depends
on the encoder producing video, and it is in the snapshot: a later failure puts
the input back to Not used.

#### W.3.1 The controlled A/B, and what it did not show

**[LIVE]** §19 was run on `.143`, a source still in its shipped Not-used state
whose Encoder 2 had never been started, so the A half is the condition the bench
actually presented rather than one manufactured on a running encoder.

| | Encoder 2 input | Packet rate at the decoder | Subframe active | Decoder Input status |
|---|---|---|---|---|
| **A** | `""` (Not used) | 20,453 /s | yes | active, 1920x1088 |
| **B** | `hdmi_input1` | 20,465 /s | yes | active, 1920x1088 |

**The two conditions are indistinguishable over the API.** 426 encoder fields
were compared between them and the only difference was the input field itself.
Packet flow, the subframe's own `video.input.active` and `video.output.active`,
and the decoder's HDMI Input status all read identical.

The physical observation that prompted this requirement -- Encoder 2 set to Not
used produces no video -- is not contradicted by that. The most likely reading is
that the encoder emits its slate, which is a valid stream carrying no picture:
the encoders ship with `slate.mode = "auto"`, a constant-bitrate intra codec
fills the same packet rate either way, and a person looking at the display sees
nothing while every field an application can read says the chain is healthy.

**That is the argument for configuring the input rather than detecting the
fault.** There is no field to detect it with. A preventive write costs one
`config_set` on a source that needed it anyway; the alternative is a Multiview
that verifies clean and shows a blank window.

### W.4 Session 2, and why it must not announce itself

Every window's video is `vc2_encoder2` -> `session2` -> a reserved decoder input.
Preparation verifies, per source: Encoder 2's input, its scaler, its bitrate,
Session 2's encoder assignment, that its video stream is enabled, that its
destination is the device's own generated address, and that **SAP announcement is
off**.

**[LIVE]** §20 enabled Session 2's announcement on `.142` with the decoder's own
SAP input enabled, and watched `.32` for 30 seconds:

* the session was advertised -- `session2@hw-omni-e4111-08414` appeared in the
  decoder's `sap_input.available_sessions`;
* the decoder did **not** adopt it. `sap_input.session` stayed on the session it
  was configured for, no ip_input was repointed, and the output did not move.

**This corrects §V.6.** SAP does not adopt a newly announced session. It applies
the session it is *configured* for -- which is what repointed `ip_input1` in
Phase 4B, where the configured session and what the input carried disagreed. The
production rule is unchanged and is now better justified: an announced Session 2
is a session an operator or a later configuration change can select, and a
Multiview whose windows can be selected out from under it is a Multiview that
breaks for reasons nobody can trace. Keeping it silent removes the possibility
rather than relying on nobody exercising it.

### W.5 The reserved decoder input pool

```
Window 1 -> ip_input2      Window 3 -> ip_input6
Window 2 -> ip_input4      Window 4 -> ip_input8
```

Odd inputs are left to the decoder's own roles; the bench uses 1 for the primary
video, 3 for its audio and 5 for aux. Two rules bend the mapping and only these
two:

* **One stream, one input.** A source in several windows -- a quad view of one
  camera -- resolves to the reserved input of the first of those windows, because
  the decoder cannot open one multicast address and port on two inputs.
* **A stream already open elsewhere.** If an enabled input outside the pool
  already carries the wanted address and port, the plan is refused rather than
  half-applied.

**The allocator and the reclaimer must agree.** This was found live. The
allocator was willing to take a pool input that another Multiview referenced,
while `reclaimable_inputs` correctly refused to release one -- so every
create/delete cycle leaked an input, and the other Multiview quietly gained our
stream in one of its windows. The rule is now one rule: an input another
Multiview references is neither taken nor released.

**[LIVE]** The refusal, on `.32`, where the retained Phase-4 object points
`subframe2` at `ip_input2`:

```
HTTP 400
Window 1 is reserved to ip_input2, but it is used by the Multiview
multiviewOmniSuiteTest. OmniSuite will not overwrite it.
```

Zero mutations reached any device.

### W.6 Bandwidth

Two budgets, both 900 Mb/s, constraining different things:

| Budget | Covers |
|---|---|
| **Source** | `vc2_encoder1.bitrate + vc2_encoder2.bitrate` on one encoder |
| **Decoder** | the sum of the **unique** streams arriving for one Multiview |

A source in four windows encodes once and arrives once, so it is counted once in
both.

**[LIVE] The device's own limits were measured, not assumed.** `vc2_encoder2`
accepted 20 and 900 Mb/s and refused 10 and 901 with `Invalid bitrate: N Mbps`.
This is the one field in the whole subsystem where the device rejects a bad value
instead of accepting and ignoring it. A single encoder's ceiling being exactly
900 corroborates the 900 Mb/s budget rather than contradicting it.

#### The allocation algorithm

```
floor        = 150 Mb/s for every unique stream        (proven end to end)
remainder    = 900 - 150 x streams
extra(s)     = floor_to_10( remainder x area(s) / total_area )
allocated(s) = min(150 + extra(s), 900)

headroom(s)  = 900 - encoder1_bitrate(s)
  headroom >= allocated  ->  bitrate = allocated,  Encoder 1 untouched
  headroom >= 150        ->  bitrate = headroom,   Encoder 1 untouched
  otherwise              ->  bitrate = 150 and Encoder 1 is reduced to 750
```

`area(s)` is the **largest** window that source feeds, because one encoder serves
all of them.

Giving every stream the floor first and weighting only the remainder is what
makes equal windows equal and a large window favoured without starving a small
one. Flooring each share to a multiple of 10 makes the sum fit the budget by
construction rather than by a final check.

**Encoder 1 is touched only when the floor cannot otherwise fit**, and then only
as far as the floor requires. It is always a planned, displayed, read-back,
rolled-back mutation. Encoder 1's **scaler** is never written at all.

Worked results, from the live matrix:

| Layout | Streams | Allocated | Actual after the source cap | Decoder total |
|---|---|---|---|---|
| 2x2 | 4 | 220 each | 150 / 200 / 200 / 200 | 750 |
| Side-by-Side | 2 | 210 / 210 | 150 / 200 | 350 |
| PiP | 2 | 690 / 210 | 150 / 200 | 350 |
| 1+3 | 4 | 300 / 200 / 200 / 200 | 150 / 190 / 190 / 190 | 720 |
| 4-Split | 4 | 370 / 170 / 170 / 170 | 150 / 170 / 170 / 170 | 660 |

On this bench the source cap binds in every case -- Encoder 1 sits at 700 or 750,
leaving 200 or 150 -- so the area weighting is mostly invisible in the final
numbers. That is the honest outcome and it is reported as such: the weighting is
what decides the allocation, and the source's own budget is what decides how much
of it survives. `.141` at Encoder 1 = 750 lands on exactly the floor, so it is
the one source that shows the cap without triggering an Encoder-1 reduction.

### W.7 The decoder's own health is part of verification

Every write in this subsystem is read back, but a read-back only proves the
device holds what it was told. `hdmi_output[0].video.status` is the decoder's own
**Input status** -- the field the web application shows as `No active video` --
and it is the one check that is not a read-back of our own write.

After a Show, the status is polled for a bounded settle window (`6 s`). If it
does not go active:

* the whole chain is captured **first** -- decoder output, SAP, the four pool
  inputs with their addresses and packet counters, the Multiview object and each
  subframe's own `video.input.active` / `video.output.active`, and for each window
  the source's Encoder 1 input and bitrate, Encoder 2 input, scaler and bitrate,
  Session 2's encoder, enable, destination and SAP, and the source's HDMI input;
* then the Show transaction is rolled back;
* and the result is `NO ACTIVE VIDEO — ROLLED BACK`, not success.

**[LIVE] It fired on real hardware.** On `.161`, a `hw-omni-d4111`, a 2x2 was
saved and shown: every write was accepted and verified, all four pool inputs were
enabled and receiving packets, and **every subframe reported
`video.input.active = false`** with the decoder reporting no active video. The
D4111 did not composite streams the D4511 composites from the same encoders
seconds earlier. That is an unresolved device-family difference, recorded here
rather than explained away; `.161` is not a supported bench target and the
product behaved exactly as intended -- it refused to call it a success and put
the display back.

### W.8 Audio follows the main window

Multiview carries no audio at all, so the display's audio comes from the source
in the **main window**, over that source's ordinary **Session 1** path. No second
audio stream is created on Session 2.

The main window is the largest window, earliest on a tie. That resolves to `main`
for every layout that has one and to Window 1 for 2x2 and Side-by-Side, which are
the two layouts whose windows are all equal.

Applying it belongs to **Show**, never to Save: it changes what the operator
hears. An input already carrying that Session 1 audio is used as it stands;
otherwise the input the display's audio *already* uses is repointed, and only
when OmniSuite can see it is nothing else -- not a Multiview window input, not the
video or aux input, and not referenced by any Multiview. Anything else is
reported and left alone.

### W.9 Video Wall and Fast Switching

Neither is ever turned off automatically. Both are checked before the planner
does any work, so a blocked decoder produces a plan with no mutations in it.

**[LIVE] Video Wall**, on two decoders that already had it enabled, so nothing
had to be manufactured:

| Decoder | Wall | Result |
|---|---|---|
| 192.168.100.151 | enabled | HTTP 400, 0 mutations, nothing created, display and sources untouched |
| 192.168.100.152 | enabled | HTTP 400, 0 mutations, nothing created, display and sources untouched |

**[LIVE] Fast Switching**, the decoder's `video.output.fsm` block, on both
families:

| Decoder | Model | Family | FSM | Result |
|---|---|---|---|---|
| 192.168.100.32 | `hw-omni-d4511` | 4xxx | on | **not blocked** -- all eleven layouts composited with it on |
| 192.168.100.205 | `at-omni-121` | 1xx | on | **blocked**, HTTP 400, 0 mutations, nothing written |

The family comes from the model identity and nothing else: the last segment of
the normalised model, with a leading `d` stripped, four digits starting `4` for
4xxx and three starting `1` for 1xx. An address, a hostname or a serial never
decides it. A family that cannot be established is **refused while Fast Switching
is on** -- a safety gate with no evidence behind it is not a gate.

### W.10 The live matrix

**[LIVE]** Decoder `192.168.100.32`. Sources: `hw-omni-e4521-00002` (.141),
`hw-omni-e4111-08414` (.142), `hw-omni-e4521-856c` (.143),
`hw-omni-e4111-wp-01003` (.253). Each case is Save -> verify -> Show -> verify ->
Delete over the shipping HTTP endpoints. **11 of 11 verified, 0 problems.**

| Layout | Win | Scalers | E2 bitrates | Source E1+E2 | Decoder | ip_inputs | Audio | Input status |
|---|---|---|---|---|---|---|---|---|
| 2x2 | 4 | 960x544 | 150/200/200/200 | 900/900/900/900 | 750 | 2,4,6,8 | .141 S1 | 1920x1088 |
| Side-by-Side | 2 | 960x544 | 150/200 | 900/900 | 350 | 2,4 | .141 S1 | 1920x1088 |
| PiP Top Left | 2 | 1920x1080, 640x360 | 150/200 | 900/900 | 350 | 2,4 | .141 S1 | 1920x1080 |
| PiP Top Right | 2 | 1920x1080, 640x360 | 150/200 | 900/900 | 350 | 2,4 | .141 S1 | 1920x1080 |
| PiP Bottom Left | 2 | 1920x1080, 640x360 | 150/200 | 900/900 | 350 | 2,4 | .141 S1 | 1920x1080 |
| PiP Bottom Right | 2 | 1920x1080, 640x360 | 150/200 | 900/900 | 350 | 2,4 | .141 S1 | 1920x1080 |
| 1+3 Horizontal Bottom | 4 | 1280x720, 640x360 | 150/190/190/190 | 900/890/890/890 | 720 | 2,4,6,8 | .141 S1 | 1920x1080 |
| 1+3 Horizontal Top | 4 | 1280x720, 640x360 | 150/190/190/190 | 900/890/890/890 | 720 | 2,4,6,8 | .141 S1 | 1920x1080 |
| 1+3 Vertical Right | 4 | 1280x736, 640x368 | 150/190/190/190 | 900/890/890/890 | 720 | 2,4,6,8 | .141 S1 | 1920x1104 |
| 1+3 Vertical Left | 4 | 1280x736, 640x368 | 150/190/190/190 | 900/890/890/890 | 720 | 2,4,6,8 | .141 S1 | 1920x1104 |
| 4-Split | 4 | 1440x816, 480x272 | 150/170/170/170 | 900/870/870/870 | 660 | 2,4,6,8 | .141 S1 | 1920x1088 |

Verified independently in every case: the display output read back `1920x1080`
and the sink negotiated 1920x1080; every pool input carried its source's Session 2
destination and was enabled; packet rates of 15,500-20,400 /s on every input;
every subframe's `video.input.active` true; Session 2 assigned to Encoder 2,
enabled and silent on every source; Encoder 2's input equal to Encoder 1's on
every source; Encoder 1's bitrate and scaler unchanged on every source; the audio
input carrying `.141`'s Session 1 audio; Save leaving the display untouched; and
Delete releasing all four pool inputs, each verified.

### W.11 Restoration, and a change this session did not make

**[LIVE]** 393 baseline fields across the decoder and four sources: **0
differences.** `hdmi_output1` compared in full -- 74 settable fields: 0
differences. `multiviewOmniSuiteTest`: 36 fields: 0 differences.

That object had to be rebuilt. At **07:49:21** a client this session did not
drive deleted it, and at **07:50:17** the same client created and showed a
`multiview2x2` of its own. The server log carries the evidence: page requests
answered `304 Not Modified` (a browser with a warm cache, which the automation's
throwaway profile never produces), human-paced gaps between plan requests, and a
Show/Delete sequence no Phase 5 script issues. It is the same concurrent browser
session Phase 4B recorded on this bench. The object was rebuilt field for field
from the §1 baseline and compared back.

This is worth recording for its own sake: **a second client on the same server
can delete an object the first is relying on**, and nothing in the product
prevents that, because nothing can. It is an argument for the read-back-everything
design rather than against it -- every Phase 5 transaction re-reads device state
at the moment it acts rather than trusting what a page believed.

### W.12 What the UI shows now

The canvas selector is gone -- a control that can only hold one value is a label
wearing a select's box. Measured in a browser against live hardware:

* toolbar: Decoder, Multiview, + New Multiview, Layout, Name. No resolution
  control in the DOM at all.
* the canvas states `1920x1080` beside its heading, and the caption separates
  `Display output: 1920x1080` from `Compositor canvas: 1920x1088 — snapped to the
  layout grid`.
* the configuration table is Window / Source / Scaler / Encoder / Bitrate /
  Decoder input, with **Audio: Session 1** under the main window and nowhere else.
* one line of summary: `4 streams to this decoder, 750 of 900 Mb/s`.
* an **Engineering detail** panel, closed by default, carrying the ordered device
  writes, the bandwidth arithmetic per source, the audio plan, and the decoder's
  own output resolution, Input status, Video Wall, Fast Switching, SAP and input
  pool.
* 8 requests on load (3 of them Multiview), 1 per decoder selection,
  **0 in 60 seconds idle**, 0 console errors, 0 failed requests.

### W.13 Cost

| Measurement | HEAD | With Phase 5 |
|---|---|---|
| `/api/scan`, 254 addresses, 11 reachable | 10.395 s (10.361-10.415) | 10.415 s (10.391-10.419) |

Paired runs, same bench, same minute, five each. 20 ms on a 10.4 s operation, with
the ranges overlapping. The scan path contains no Multiview reference and a test
asserts it.


---

## X. Phase 6: many saved Multiviews, recall, shared streams and audio

Phase 6 separates two things Phase 5 had joined: what a Multiview **is**, and
what a Multiview **costs**. A decoder holds as many saved Multiviews as an
operator wants, and only one of them is on the output, so a saved configuration
cannot own an encoder or a decoder input. Everything is decided again at recall.

### X.1 What this supersedes

| Phase 5 rule | Phase 6 |
|---|---|
| one OmniSuite Multiview per exposed canvas | as many as wanted; only one active |
| Save configures the sources and the inputs | Save writes the object; **recall** configures everything |
| window N always uses pool input N | the pool is four slots, handed to **distinct streams** |
| a pool input another Multiview references is a collision | only **live** configuration is a collision |
| reclamation is driven by the saved object | reclamation is driven by the **active** configuration |

The 1920x1080-only canvas, the Encoder-2 pipeline, the Encoder-2 input rule, the
silent Session 2, the two 900 Mb/s budgets and both interlocks are unchanged.

### X.2 Save stores; recall configures

Two saved Multiviews will routinely want the same Encoder 2 at different sizes.
Making their requirements coexist is not merely hard, it is impossible -- one
scaler has one value. So saving writes the Multiview object and its metadata and
nothing else:

```
save        del_multiview_subframe (pruning), add_multiview / config_set
recall      Encoder 2's input, scaler and bitrate; Encoder 1's bitrate where the
            budget forces it; Session 2 and its announcement; the decoder inputs;
            the subframe rebind; the release of pool inputs no longer needed;
            output resolution; SAP; audio; the selection
```

**[LIVE]** Four Multiviews were saved on `.32` and the pool stayed completely
untouched: `ip_input2/4/6/8` all disabled after all four saves. Nothing on the
display changed, and each save left the other three objects byte-identical.

Recall re-reads every source before planning. Nothing from the previous recall
is assumed to have survived, because it usually has not.

### X.3 The pool is four slots, not four window names

```
W1=A W2=B W3=C W4=D   ->  A:ip_input2  B:ip_input4  C:ip_input6  D:ip_input8
W1=A W2=A W3=B W4=C   ->  A:ip_input2  B:ip_input4  C:ip_input6, 8 unused
W1=A W2=A W3=B W4=B   ->  A:ip_input2  B:ip_input4
```

Slots go to **distinct streams in first-use order**, which keeps the result
deterministic and reproducible from the saved configuration alone. Two windows
are the same stream only when the multicast address *and* UDP port agree --
never because they name the same encoder, since one encoder transmits several
sessions.

An input anywhere on the decoder already carrying the wanted stream is used
where it stands, pool or not, because the hardware will not open one address and
port twice.

#### X.3.1 A defect the live recall matrix caught

Recalling Side-by-Side after a quad put **two different sources on one input**.
The incoming layout wanted a stream that the outgoing one had on the very input
the new first window was about to take, so the `already carrying it` lookup
matched an input the same plan had just reassigned. Both windows ended on
`ip_input2` and the Multiview showed one source twice.

The fix is one line of bookkeeping -- an input this plan has already given to a
different stream is not a candidate -- and it is now covered by a property test
over every arrangement of four streams against every three-input starting state.

### X.4 One decoder input drives at most two windows

**[LIVE]** This is the phase's most consequential finding, and nothing in the
API reports it.

| Subframes on one input | Windows that showed |
|---|---|
| 1 | 1 |
| 2 | 2 |
| **3** | **2** |
| **4** | **2** |

With three or four windows on one stream, exactly two showed and the rest stayed
black. The device returned no error, the stream arrived at 16,355 packets/s
throughout, and the decoder's own composite Input status read **active**, because
the other windows were carrying it.

Splitting across two inputs is not a way out: the decoder will not open one
multicast address and port twice. So a layout that asks one source to fill three
or four windows cannot be built, and the planner refuses it by name:

```
hw-omni-e4521-00002 is assigned to 4 windows (top_left, top_right, bottom_left,
bottom_right), and a decoder input drives at most 2. The extra windows would stay
black with no error from the device. Use a different source for bottom_left,
bottom_right, or choose a layout with fewer windows on it.
```

**A saved Multiview does not consume the budget.** An object holding two
subframes on an input while a *different* object was displayed did not prevent
the displayed one from locking its window, and its own subframes read inactive
while it was not on the output. The limit belongs to what is being displayed,
which is exactly the Phase 6 model.

### X.5 Every window is checked, not just the composite

The per-window check exists because of the finding above. The decoder's Input
status is a property of the composite and reads active while one window sits
black, so after a recall each subframe is polled to a bound:

* every window locked -> `VERIFIED`
* one or more did not -> `VERIFIED — WINDOW NOT LOCKED`, naming them, with the
  full chain diagnostics attached

It is **not** rolled back. The rest of the picture is live, and taking it away to
punish one dark window helps nobody; the operator is told which window and why.

### X.6 One encoder, one scaler, two window sizes

**[LIVE]** Built by hand, because the planner refuses to build it: a PiP with
main `1920x1080` and inset `640x360`, both fed from one source's Encoder 2 with
the scaler set to the larger size.

The decoder **accepted it**: 24,327 packets/s, both subframes reporting
`input.active` and `output.active`, Input status active at 1920x1080. No error of
any kind.

And it is still wrong. A subframe has no width or height -- the device reports
none, confirmed by dumping every field of a live subframe -- so a window's size
is the resolution arriving on its input. One stream is one size, so the "inset"
renders 1920x1080 anchored bottom right, covering the main window. The operator
asked for a PiP and would get a full-screen overlay.

**So the answer to §15 is (B): the planner refuses it.** Not because the hardware
rejects it, but because the hardware accepts it and produces the wrong picture
while reporting success, which is the exact failure class this subsystem exists
to prevent. Equal-sized repeated windows remain fine, up to the limit in X.4.

### X.7 Bandwidth counts streams

Four windows showing one camera are **one** stream arriving once and one encode
at the source. The decoder aggregate is keyed on stream identity, not on window
count and not on hostname:

| Layout | Windows | Unique streams | Decoder aggregate |
|---|---|---|---|
| four distinct sources | 4 | 4 | 750 Mb/s |
| one source twice + two others | 4 | 3 | 550 Mb/s |
| two duplicate pairs | 4 | 2 | 350 Mb/s |

Two encoders misconfigured onto one destination are also one stream at the
decoder, and are charged once -- OmniSuite already warns about the clash rather
than budgeting for a stream that does not exist.

### X.8 Audio

**[LIVE]** Each of the four sources in turn as the main window, through the
product's own endpoints:

| Main source | Its Session 1 audio | What `ip_input3` carried | Enabled | HDMI audio | Decoder audio status |
|---|---|---|---|---|---|
| 192.168.100.141 | 239.80.133.236 | 239.80.133.236 | yes | `ip_input3` | active, 16-bit LPCM 48 kHz |
| 192.168.100.142 | 239.80.132.254 | 239.80.132.254 | yes | `ip_input3` | active, 16-bit LPCM 48 kHz |
| 192.168.100.143 | 239.80.133.108 | 239.80.133.108 | yes | `ip_input3` | active, 16-bit LPCM 48 kHz |
| 192.168.100.253 | 239.80.65.179 | 239.80.65.179 | yes | `ip_input3` | active, 24-bit LPCM 48 kHz |

**Four of four correct**, and the audio moved with the main window on every
recall of the matrix rather than staying on the previous Multiview's source.

**What that establishes, stated precisely.** The rule the product applies is
*repoint the input the display's audio already uses*. On this bench that input
is `ip_input3` in every case, which matches the site convention already recorded
(1 = Session 1 video, 3 = Session 1 audio, 5 = aux). `ip_input3` is therefore the
Multiview audio insertion point **on decoders that follow that convention**, and
the mechanism is what makes it portable to one that does not. It is never taken
from the window pool, never Session 2, and an input doing anything else is
reported rather than repointed.

### X.9 The live recall matrix

**[LIVE]** Four objects saved on `.32`, then `A -> B -> C -> D -> A -> D -> B -> A`.

| Saved | Layout | Windows |
|---|---|---|
| A Quad Unique | 2x2 | .141, .142, .143, .253 |
| B Quad Duplicate | 2x2 | .141, .141, .142, .143 |
| C Side by Side | side-by-side | .253, .141 |
| D Presenter PiP | pip-bottom-right | .142 main, .253 inset |

| Step | Streams / windows | Aggregate | Pool | Audio | Input status | Result |
|---|---|---|---|---|---|---|
| 1. A | 4 / 4 | 750 | 2,4,6,8 | .141 | 1920x1088 | VERIFIED |
| 2. B | 3 / 4 | 550 | 2,4,6 | .141 | 1920x1088 | VERIFIED |
| 3. C | 2 / 2 | 350 | 2,4 | .253 | 1920x1088 | **WINDOW NOT LOCKED** |
| 4. D | 2 / 2 | 400 | 2,4 | .142 | 1920x1080 | VERIFIED |
| 5. A | 4 / 4 | 750 | 2,4,6,8 | .141 | 1920x1088 | VERIFIED |
| 6. D | 2 / 2 | 400 | 4,8 | .142 | 1920x1080 | VERIFIED |
| 7. B | 3 / 4 | 550 | 2,4,6 | .141 | 1920x1088 | VERIFIED |
| 8. A | 4 / 4 | 750 | 2,4,6,8 | .141 | 1920x1088 | VERIFIED |

Verified per transition: Encoder 2's input equals Encoder 1's, the scaler matches
each window, Session 2 is on Encoder 2, transmitting and silent, each source's
Encoder 1 + Encoder 2 is inside 900 Mb/s, every distinct stream has its own input,
no multicast is open twice, no pool input is enabled that the configuration does
not use, the output is 1920x1080, SAP is off, audio carries the main window's
Session 1, and the decoder reports active video.

Note steps 4 and 6 -- the same Multiview, different pool inputs (2,4 then 4,8).
Allocation is a function of the live state, which is what makes it deterministic
without being fixed.

**Step 3, reproducibly.** Recalling C after B leaves the left window dark: the
stream arrives at 20,474 packets/s on the input the subframe names, and the
subframe never reports active, within a 30-second bound. The mechanism is not
established. It is not the two-window limit (one subframe per input here), not a
stale address on a released input, not the gap between recalls, and not cold
encoder start -- each was tested and excluded, and the same B -> C transition run
outside that sequence locks in 3.8 seconds. The product reports it rather than
claiming success, which is what the per-window check in X.5 was added for.

### X.10 Delete

Deleting an **inactive** Multiview removes that object and its metadata and
nothing else: no input is released, because the enabled inputs belong to whatever
is on the output, and the saved object never reserved them. Deleting the
**active** one moves the output off it first, verified, then releases the pool
inputs the configuration now on the output no longer needs -- established from
ownership captured *before* the object that proves it is removed.

### X.11 Restoration

**[LIVE]** 393 baseline fields across the decoder and four sources: **0
differences.** `hdmi_output1` in full, 74 settable fields: 0 differences.
`multiviewOmniSuiteTest`: 36 fields: 0 differences. The retained Phase-4 object
no longer needs any special handling -- under the Phase 6 collision model its
reference to `ip_input2` reserves nothing, so the pool was usable throughout
without touching it.


## Y. Phase 7: the active Multiview is a switching surface

Phase 6 made a saved Multiview a document and a recall the act of making that
document true. Phase 7 addresses what happens **while it is on the display**.

The distinction that drives everything else: an inactive Multiview's canvas is a
form, and the active one's canvas is the display. Dragging a source onto a
window means two different things depending on which one is open, and the page
must never leave that to be inferred.

### Y.1 What this supersedes

| Phase 6 | Phase 7 |
|---|---|
| a drag always edits a preset | a drag on the **active** Multiview switches the display |
| Save is the only way a change reaches a device | a verified live switch reaches the device and **then** updates the preset |
| the canvas shows the saved assignment | the canvas shows the decoder's **live subscriptions** |
| a source is eligible or not | READY / CONFIGURATION REQUIRED / INELIGIBLE |
| every discovered decoder is offered | an unreachable decoder is not a choice |

Everything from Phases 5 and 6 is unchanged: the 1920x1080-only canvas, the
Encoder-2 pipeline, the Encoder-2 input rule, the silent Session 2, the reserved
pool, both 900 Mb/s budgets, the two-windows-per-stream limit, the one-scaler
rule and both interlocks.

### Y.2 A switch is the smallest verified transaction that gets there

Re-running a whole recall to change one window would tear down and rebuild
windows nobody asked about, and every one of those is a visible glitch on a
display someone is watching. So `/api/multiview/switch`:

```
plan       the WHOLE Multiview as it would be after the change, so every rule
           -- the scaler conflict, the two-window limit, both budgets, the pool
           -- is applied to the result rather than to one window in isolation
apply      everything that plan requires which the devices do not ALREADY
           hold -- which is the smallest transaction, because a window nobody
           disturbed already matches and produces no write
rebind     the subframes whose input changed
release    a pool input the Multiview no longer needs
audio      only if the window that changed owns it
verify     the changed window locks, AND every other window still holds
```

A change that succeeds becomes the saved preset, because a display showing one
thing while its own preset restores another is a trap. A change that fails is
rolled back and the preset is left alone -- and so is a change that lit the
window it was asked about but darkened a different one.

That last clause and the "already hold" filter are both there because of §Y.9:
the first version of this endpoint predicted which resources a switch would
touch, and was wrong.

### Y.3 The canvas shows subscriptions, not intent

A saved Multiview says what was wanted. The decoder's `ip_input` entries say
what is actually arriving. After a recall -- or after someone switches a window
from the front panel -- those can disagree, and the canvas must show the second,
because that is what is on the screen.

`_subframe_view` reports a window as one of four origins:

| `source_origin` | means |
|---|---|
| `subscription` | the stream arriving resolves to a known encoder |
| `unknown` | a stream is arriving that no discovered encoder claims |
| `saved` | nothing is arriving; only the preset's record is left |
| `none` | no stream and no record |

`unknown` is deliberately not collapsed into "empty". Something is plainly
playing in that window, and drawing it as an empty drop target would be a lie
the operator can see through.

**Measured:** recall reads each source's session live, so a re-addressed encoder
is subscribed correctly. *Naming* the encoder behind a window is answered from
the discovery cache, because reading every encoder when the page opens is the
cost the lazy architecture exists to avoid. Between re-addressing a source and
the next scan the window is therefore correct and reported as `unknown` -- never
named as the wrong device -- and `saved_source_ip` still records the intent.

### Y.4 Three states, because they call for three different actions

| state | meaning | offered? |
|---|---|---|
| READY | usable now | yes, draggable |
| CONFIGURATION REQUIRED | the device is fine; something on it must be set | yes, shown with the reason, **not** draggable |
| INELIGIBLE | offline, wrong model, or no supported encoder | no, listed under "not eligible" with the reason |

Collapsing these hides the only one an operator can act on. An encoder whose
Session 2 has no multicast address is not broken and is not missing -- it needs
one field set -- and a source list that simply omitted it would send someone
looking for a network fault.

Decoders are filtered the same way with one deliberate exception: a decoder that
answers but is barred by Video Wall or Fast Switching **is** offered, disabled,
with the reason. Those are switches the operator can turn off, and an empty list
would never say so.

**The list is a hint; the transaction is the authority.** Eligibility is computed
from the discovery cache and one reachability probe -- listing sources reads no
device configuration at all, which is what keeps opening the page cheap. So a
source that loses its Session 2 destination *after* the last scan still shows as
READY until the next one.

**[LIVE]** Measured by clearing `session2`'s destination on `192.168.200.144`
and putting it back. The tile stayed READY, exactly as the design implies -- and
the plan, which reads every source live, refused it by name:

> `Encoder hw-omni-e4521-85e6 session2 has no stream destination. Configure it
> on the device (it generates its own default) before using it in a Multiview.`

Nothing can be configured from a stale tile: the refusal happens before any
write. What the cache can do is show a source as usable slightly longer than it
is, and the cost of removing that would be reading every encoder on page load.

### Y.5 The live-switch matrix

**[LIVE]** All eleven layouts on `.32`, sources `.253`, `.141`, `.142`, `.143`.
Each switch was applied while the Multiview was on the display and then verified
against the devices -- seventeen checks for a switch that must take, five for one
that must be refused.

| layout | applied | refused | failures | extra settle needed |
|---|---|---|---|---|
| `2x2` | 4 | 0 | 0 | 0 s |
| `side-by-side` | 3 | 0 | 0 | 0 s |
| `pip-top-left` | 2 | 1 | 0 | 0 s |
| `pip-top-right` | 2 | 1 | 0 | 0 s |
| `pip-bottom-left` | 2 | 1 | 0 | 0 s |
| `pip-bottom-right` | 2 | 1 | 0 | 0 s |
| `1+3-horizontal-bottom` | 2 | 2 | 0 | 0 s |
| `1+3-horizontal-top` | 2 | 1 | 0 | 0 s |
| `1+3-vertical-right` | 2 | 1 | 0 | 0 s |
| `1+3-vertical-left` | 2 | 1 | 0 | 0 s |
| `4-split` | 3 | 1 | 0 | 0 s |

Totals: **36 switches, 26 applied and verified, 10 correctly refused, 0 with
any failing check.**

"Extra settle needed" is time this harness waited *beyond* the product's own
window-lock wait before every populated subframe reported active. It is 0 s
everywhere: no applied switch in the matrix needed longer than the endpoint
already gives it.

The seventeen checks, in the order they are applied:

1. the switch reported VERIFIED
2. the switched window carries the new source's stream (address *and* port)
3. the switched window's input is active
4. the switched window's output is active
5. no other window changed decoder input
6. no other window changed stream
7. no other window lost its picture
8. every source's Encoder 2 takes the same input as Encoder 1
9. every Encoder 2 scaler matches the window it feeds
10. every Session 2 is on Encoder 2, transmitting, SAP off
11. no source exceeds its 900 Mb/s video budget
12. the decoder stays inside its 900 Mb/s budget, charged per unique stream
13. no decoder input carries two different streams
14. no stream is opened on two decoder inputs
15. no pool input is left enabled with nothing using it
16. the display shows this Multiview, composited at its canvas
17. the saved preset now records the switched source

Checks 5, 6 and 7 are the reason the matrix exists. A switch that lights the
window it was asked about while quietly dropping another is invisible unless
every window is re-read every time.

### Y.6 Refusals are results

Putting one source into two windows of **different sizes** cannot work: one
Encoder 2 has one scaler. Phase 5 measured that the decoder accepts the
configuration and composites the wrong picture, so software refuses it before
writing anything.

**[LIVE]** 10 refusals were attempted across the PiP, 1+3 and 4-split layouts --
every layout that has two window sizes. All ten were refused before anything was
written, and in every case the decoder was byte-identical afterwards: no window
repointed, no window darkened, the pool unchanged.

Nine were the scaler rule. The tenth broke a *different* rule first -- the
attempted configuration would have put a third window on one stream, which the
measured two-windows-per-input limit forbids -- and it is worth recording that
these arrive with different HTTP statuses:

| | |
|---|---|
| HTTP 409 | a **conflict** against live configuration (the scaler rule) |
| HTTP 400 | a plan that **cannot be built** at all (the window limit) |

Which one fires depends on which rule the attempted configuration breaks first,
not on which direction the window moved. Both are correct refusals, and both
name the rule and the windows involved.

- 1x `192.168.100.141 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1280x720; bottom_middle needs 640x360. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `192.168.100.141 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1920x1080; top_left needs 640x360. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `192.168.100.142 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1920x1080; bottom_left needs 640x360. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `192.168.100.142 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1920x1080; bottom_right needs 640x360. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `192.168.100.143 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1280x736; top_right needs 640x368. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `192.168.100.253 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1280x720; top_middle needs 640x360. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `192.168.100.253 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1280x736; middle_left needs 640x368. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `192.168.100.253 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1440x816; bottom_right needs 480x272. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `192.168.100.253 is assigned to windows that need different sizes from the same encoder (Encoder 2): main needs 1920x1080; top_right needs 640x360. A scaler belongs to the encoder, so it can only produce one of them. Use a different source for one of these windows.`
- 1x `hw-omni-e4111-08414 is assigned to 3 windows (main, bottom_left, bottom_middle), and a decoder input drives at most 2. The extra windows would stay black with no error from the device. Use a different source for bottom_middle, or choose a layout with fewer windows on it.`

Duplicating a source into two windows of the **same** size is legal and was
exercised in every layout that has two windows of one size. It shares a single
decoder input rather than opening a second one.

### Y.7 Note on the composited canvas

Check 16 compares the decoder's HDMI Input status against the canvas the planner
actually wrote, not against the display's output resolution. Those two are
routinely different and that is not a fault:

| layout | composited canvas | display output |
|---|---|---|
| 2x2, side-by-side, 4-split | 1920x1088 | 1920x1080 |
| 1+3 vertical | 1920x1104 | 1920x1080 |
| PiP (all four) | 1920x1080 | 1920x1080 |

The compositor's canvas is snapped up to the layout grid; the display still runs
at 1920x1080, because the decoder has no such output mode. An earlier version of
this matrix asserted `1920x1080` for both and reported a correct configuration
as a failure in seven of eleven layouts.

### Y.8 Audio, re-verified

**[LIVE]** Phase 6 established that the display's audio follows the main
window's source over its ordinary Session 1 path, inserted by repointing the
input the display's audio already uses. Phase 7 adds the case that did not exist
before -- the main window changing *while* the Multiview is on the display.

Eleven checks on `.32`, all passing:

| | |
|---|---|
| recall points the audio at the main window's source | `ip_input3` -> `239.80.133.236`, active |
| switching a **secondary** window | audio input, address and active state all unchanged |
| switching the **main** window to `.141` | `ip_input3` -> `239.80.133.236`, active |
| switching the **main** window to `.142` | `ip_input3` -> `239.80.132.254`, active |
| switching the **main** window to `.253` | `ip_input3` -> `239.80.65.179`, active |

The insertion point never moved off `ip_input3`, and in each case the source the
product reported was the source it had written.

### Y.9 What the live matrix found

Three defects, none of which any simulator test had caught, and all three found
by the same switch -- the only one of the 36 that had to move a window the
operator had not asked about.

**1. A switch did not prepare a window it moved.** The endpoint applied only the
changed source's mutations and the changed window's decoder input. Taking a
source off one window can *un-share* a stream, and the re-plan then moves the
other window to a free pool input -- whose mutation was dropped. On `.32`
4-split, `bottom_right` was rebound to `ip_input8` while `ip_input8` stayed
disabled and pointing at the old stream, and the window went black.
Reproduced twice, identically. The filter is now "apply what the devices do not
already hold", which does not depend on predicting what a re-plan will move.

**2. A switch that darkened another window reported success.** With the window
it was asked about lit, the endpoint returned `ok` with the status
`VERIFIED - WINDOW NOT LOCKED` and recorded the new preset -- leaving the
operator with the window they asked for and one fewer than they started with. A
regression in any other window now rolls the switch back and fails it.

**3. A clean rollback reported itself incomplete.** The restore captured whole
subframe objects, read-only status included, and then compared them back.
`video.hdcp` reads `none` while a window is dark and `2.2` once the picture
returns, so a rollback taken during a failure could never verify. The decoder
had in fact been restored exactly -- confirmed by reading it for 60 s after,
with nothing dark. Only settable fields are carried into a restore now.

A fourth, milder one came out of the corrected matrix: allocation order could
evict a stream from the input it was already on, so un-sharing moved the
*remaining* window for no reason. It kept its picture, but nothing had asked it
to move. Streams already live on an input now keep it, resolved before any free
slot is handed out; a fresh allocation is unchanged, because in one nothing is
carrying anything yet.

### Y.10 What the page costs

**[LIVE]** Measured in-process against the real devices, counting every
WebSocket frame the server sent:

| phase | device reads | TCP probes | wall clock |
|---|---|---|---|
| layout catalogue | 0 | 0 | instant |
| decoder list, from cache | 0 | 0 | instant |
| decoder list, probing | 8 | 8 | 0.93 s |
| **source list** | **0** | 7 | 1.23 s |
| source list, `probe=0` | 0 | 0 | 0.01 s |
| selecting a decoder | 3 | 0 | 0.37 s |

Listing sources reads **no device configuration**. The 1.23 s is seven
concurrent TCP probes, one of which is an encoder that is switched off; without
them the same list is answered in 10 ms.

**Across every page-load and listing phase: 0 mutations.** Not "none intended" --
every frame was recorded and none of them was a `config_set` or a `method`.

### Y.11 Restoration

**[LIVE]** 393 baseline fields across the decoder and four sources: **0
differences.** 24 writes, all verified, restoring what using and then deleting a
Multiview leaves behind -- the display's output resolution, SAP, the pool
inputs' addresses, the audio insertion point, and each source's Encoder 2 input,
scaler, bitrate, Session 2 stream and announcement.

SAP is re-enabled last, because enabling it repoints decoder inputs at the
session it is configured for and doing it first fights every input write after
it.

### Y.12 Proving the tests can fail

Every Phase 7 rule was broken on purpose and the suite was required to object.
27 mutations in total: 23 against the backend across two passes, and 19 against
the page.

| | |
|---|---|
| backend, first pass | 19 mutations, **16 caught** |
| backend, re-aimed after the fixes in §Y.9 | 8 mutations, **7 caught** |
| frontend | 19 mutations, **19 caught** |

Three first-pass targets stopped existing because the fixes in §Y.9 replaced the
code they were aimed at; they were re-aimed at the replacement and caught there.

The one mutation that can no longer be made is worth recording, because it is
how a piece of dead code was found. Replacing
`claimed_inputs=set(_claimed_inputs(...))` with `set()` could not fail a test --
because it had always *been* `set()`. Nothing anywhere writes the `claimed` key
that `_claimed_inputs` reads, and `_reclaim_inputs`, which was the only other
caller, had no callers at all: Phase 6 replaced claim-based reclamation with
`_owned_pool_inputs` / `_releasable_pool_inputs` and left the old machinery
wired in but inert. Both functions are gone. Behaviour is unchanged by
construction, and the three call sites no longer read as though ownership were
being accounted for somewhere it is not.

Four mutations initially survived against tests that could not reach what they
claimed to cover, and each one was a defect in the test rather than in the code:

- the rollback test refused the *first* write in the transaction, so nothing had
  been applied and an empty rollback looked identical to a correct one;
- the HDCP test set the field before the snapshot was taken, so the snapshot
  captured the same value it would later compare against;
- the regression test darkened a window before the switch began, so the window
  had never been lit and nothing had regressed;
- the unreadable-device test could not reach the filter at all, because a device
  that cannot be read fails while the plan is being built.

Finding those also turned up **11 shadowed tests** -- a block of `LiveSwitchTests`
had been duplicated, and Python keeps the last definition, so eleven improved
tests were being silently replaced by their earlier versions. The duplicate is
removed and `tests/` is now checked for shadowed definitions: there are none.

### Y.13 What the UI shows now

The active canvas carries a banner reading **"LIVE -- changes made on this
canvas are applied immediately"**, and the stage itself is outlined. An inactive
canvas says the opposite in words. Neither is inferred from which buttons are
enabled -- an absence is not a signal, and the Phase 6 page distinguished these
two states only by whether *Show on Display* happened to be offered.

On the active canvas a drop, and the window's clear control, both go through the
confirmed live-switch path. Save is relabelled, because by the time it is
reachable the sources have already been applied.

Each window shows what the decoder is doing with it: the source, the stream, the
decoder input carrying it, and whether it is `live` or subscribed with
`no signal`.


## Z. Phase 7A: source hover preview

A source list that shows six encoders by hostname is a list of names. The
question an operator actually has is "which of these is the podium camera", and
before this the only way to answer it was to route one into a window and look.

### Z.1 The mechanism was already there

OmniSuite has shown encoder thumbnails on the A/V Matrix page since before
Multiview existed, and on Device Info. Nothing new was invented:

| | |
|---|---|
| URL | `http://<ip>/thumbnail/thumbnail1.jpg` |
| transport | plain HTTP on port 80, **no credentials** |
| format | JPEG, `image/jpeg`, 320x180 |
| generator | `vc2_encoder1.thumbnail`, `{enable, width, height, framerate}` |
| existing controls | `POST /api/set_thumbnail`, `POST /api/get_thumbnail_status` |

**[LIVE]** All four bench sources answered on the first read: 200 `image/jpeg`,
320x180, 4663-9925 bytes, 449-502 ms each. Every one has `thumbnail.enable`
true at 320x180; `.141`, `.142` and `.143` also report `framerate: 5`.

### Z.2 The thumbnail belongs to Encoder 1

`vc2_encoder2` carries **no thumbnail fields at all** on any of the four
sources, and `thumbnail2.jpg` returned *the same 6147 bytes on every device* --
a placeholder, not a picture.

This is not a limitation. Phase 5 established that Encoder 2 must take the same
physical input as Encoder 1, so Encoder 1's thumbnail is a picture of exactly
what the Multiview window will show, at a different scale and bitrate.

### Z.3 A disabled preview is invisible to the browser

**[LIVE, controlled A/B on `.253`, snapshotted and restored]**

| | bytes | |
|---|---|---|
| `thumbnail.enable = true` | 4663 | the real picture |
| `thumbnail.enable = false` | **6147** | HTTP 200, `image/jpeg`, a placeholder |
| restored | 4663 | the real picture again |

The device answers **HTTP 200 with a valid JPEG either way** -- and 6147 bytes is
the same placeholder `thumbnail2.jpg` serves. So `<img onerror>` never fires,
and the browser cannot distinguish "preview is off" from "this is what is on
it". §7's two messages are impossible to produce in the page alone.

That is the entire reason `GET /api/multiview/preview` exists: it reads
`vc2_encoder1.thumbnail.enable` and says which of the three states it is. The
image itself is still fetched by the browser, directly, with no proxy.

`vc2_encoder1` was byte-identical to its snapshot afterwards (520 characters of
JSON compared, 0 differences), and the same A/B repeated end-to-end through the
endpoint returned `status: "disabled"`, no image URL, and the reason
"Preview is turned off on this encoder." -- then `available` again after
restoring.

### Z.4 Direct or proxied

Direct. The thumbnail is unauthenticated, OmniSuite is served over HTTP so there
is no mixed-content barrier, and an `<img>` load is not subject to CORS. A proxy
would add server load and a second copy of every frame for no benefit. What is
*not* direct is the enabled state, which is a device configuration read and
therefore goes through OmniSuite, where the credentials already live.

**Security:** the endpoint returns no credential and the image URL carries
nothing but the address and path -- asserted by a test that greps the whole
response for `password`, `token`, `secret` and `credential`.

While reading the existing mechanism, `POST /api/get_thumbnail_status` was found
to return **`used_password`** -- the device password that worked -- to the
browser, in the JSON body. Nothing consumed it. It is removed. It was the only
route in the application that did this.

### Z.5 Lazy, and quiet when idle

**[LIVE]** measured in-process against the real devices, counting every
WebSocket frame:

| phase | device reads | preview reads | time |
|---|---|---|---|
| layout catalogue | 0 | 0 | instant |
| decoder list (cached) | 0 | 0 | instant |
| source list | 0 | 0 | 10 ms |
| selecting a decoder | 3 | 0 | 0.38 s |
| **hovering one source** | 1 | 1 | **0.13 s** |
| hovering all four, one each | 4 | 4 | 0.49 s |
| **60 s idle** | **0** | **0** | -- |

Opening the page fetches no previews. Six sources do not become six image
requests. An idle page makes no preview traffic. **0 device mutations across
every phase.**

The debounce is 300 ms, so sweeping the list opens nothing; a pointer crossing
tiles without leaving them cancels the pending request and only the tile it
rests on is fetched.

There is **no refresh timer**. The A/V Matrix hover preview reloads its image
every 2 s, and copying that would have been the obvious thing to do -- but the
Multiview page is held to "nothing polls", and that rule is enforced by a test
that fails on any `setInterval`. One frame per hover; moving off and back on
fetches a newer one. The generator runs at 5 fps, so a frame is at most a fifth
of a second old when it is taken.

### Z.6 The live test

**[LIVE]** All four sources, 22 checks, 0 failing:

| source | model | status | frame | |
|---|---|---|---|---|
| `.253` | hw-omni-e4111-wp | available | 4663 B | static content |
| `.141` | hw-omni-e4521 | available | 9925 B | static content |
| `.142` | hw-omni-e4111 | available | 8618 B | **8562 B on re-read -- regenerating** |
| `.143` | hw-omni-e4521 | available | 5604 B | static content |

Every frame differed from every other, and none was the 6147-byte placeholder,
so each came from its own encoder. `.142` returned different bytes four seconds
later, which is the generator running; the other three encode identical frames
because the bench content is static, and that is reported rather than failed.

Afterwards, 12 nodes across the four sources (`vc2`, `sessions`, `hdmi_input`)
were **byte-identical**. Previewing changes nothing.

### Z.7 In the browser

**[LIVE]** Driven in a real browser against the real decoder and encoders,
across all four layout templates and both themes -- eight combinations:

| | |
|---|---|
| card opened | 8 of 8 |
| image loaded, natural size | 8 of 8, `320x180` |
| frame aspect | 1.77 (16:9) in all eight |
| inside the viewport | 8 of 8 |
| `pointer-events: none` | 8 of 8 |
| closed on leave, image dropped | 8 of 8 |
| card background, dark | `rgb(34, 42, 51)` |
| card background, light | `rgb(255, 255, 255)` |

The two themes produce different colours from the same stylesheet, which is what
using semantic tokens rather than a private palette buys.

Repositioning was checked at 1280x620: a tile at y=524 would put a 268 px card
at 524-792, off the bottom of the window. It was placed at 344-612 instead --
flipped upward, fully inside the viewport.

Scrolling the source list dismisses the card, deliberately: it is anchored to a
tile, and a tile that has moved is not a thing to point at.


## AA. Phase 7B: shared encoders, write-minimal reconciliation, and the 409

### AA.1 The 409, reproduced and classified

**[LIVE]** Every saved Multiview on `.32` was asked to be shown:

| object | result |
|---|---|
| `multiviewOmniSuiteTest` | **HTTP 409 REFUSED** |
| `multiviewPiPTopLeft` | 200 VERIFIED |
| `multiviewSidebySide` (already active) | 200 VERIFIED |
| `multiviewSidebySide` again, twice | 200 VERIFIED, 200 VERIFIED |

> `multiviewOmniSuiteTest is a 3840x2160 Multiview. This release shows
> 1920x1080 Multiviews only.`

**Classification: A — a legitimate configuration refusal.** It is the Phase-4
test object, saved at 4K by an earlier release, and the 1080p-only rule is
correct to turn it away. Not stale state, not idempotency, not a duplicate
request.

Two other things fell out of the same run:

- **Showing an already-active Multiview was already idempotent** — 200 VERIFIED
  three times in a row, and with write-minimal in place it now writes nothing at
  all. There was never a 409 to fix there.
- **There is no duplicate handler.** `wire()` runs once, `mv_show` has one
  listener, and Show is hidden entirely for a Multiview that is already active.
  The two 409s the operator saw were two clicks on the 4K object.

**Fix:** the state endpoint reports `showable` and `not_showable_reason` per
Multiview, computed by the same function the Show endpoint uses. The page shows
the reason and does not send a request it has been told will be refused. The
refusal itself is unchanged.

### AA.2 What a redundant write costs

**[LIVE]** Topology: Decoder A `.151` watching Encoder X `.142` Session 1 full
screen; Decoder B `.32` running Multiview from the same encoder. Decoder A was
sampled every 0.3 s throughout, recording its Input status, resolution,
framerate and packet counter.

Fifteen writes, each performed on its own. The ones that disturbed Decoder A:

| write | value | Decoder A |
|---|---|---|
| `session1.video.stream.destination_address` | **unchanged** | blacked out at +0.42 s, back at +0.94 s |
| `session1.video.stream.enabled` | **unchanged** | blacked out at +0.56 s, back at +1.05 s |
| `vc2_encoder1.bitrate` 700 -> 650 | changed | blacked out at +0.45 s, back at +1.00 s |
| `vc2_encoder1.bitrate` 650 -> 700 | changed | blacked out at +0.44 s, back at +0.98 s |
| `session2.video.stream.enabled` true -> false | changed | blacked out at +0.45 s |
| `session2.video.stream.enabled` false -> true | changed | blacked out at +0.34 s |

Everything else was silent, including `vc2_encoder1.bitrate` and
`vc2_encoder1.input` rewritten with the values already held, the Encoder 1
scaler, every Encoder 2 field, Encoder 2's input going to "Not used" and back,
a real Encoder 2 scaler change, and SAP on either session.

Two conclusions:

1. **Writing an encoder's `video.stream` restarts that stream**, and a decoder
   watching it relocks — *even when the written value is the one it already
   held*. This is the sharpest argument for write-minimal reconciliation.
2. **Starting or stopping Session 2 disturbs Session 1's subscribers too**, on
   the same encoder. That is a shared-pipeline effect and it is unavoidable
   when Multiview genuinely has to start Session 2.

Multiview never writes Session 1. Its only unavoidable disturbances are a
genuine Encoder 1 bitrate reduction and a genuine Session 2 start.

### AA.3 The A/B: an already-prepared encoder costs nothing

**[LIVE]** A Multiview on Decoder B built from Encoder X, shown four times:

| run | planned | already correct | **writes** | Encoder X changed | Decoder A |
|---|---|---|---|---|---|
| A first Show, real work to do | 7 | 7 of 13 fields | **7** | scaler 1920x1080 -> 960x544 | **no disturbance** |
| B Show again, everything correct | 0 | -- | **0** | 0 | no disturbance |
| C one field wrong (`bitrate` 200 -> 123) | 1 | -- | **1** | 0 | no disturbance |
| D again | 0 | -- | **0** | 0 | no disturbance |

Run C is the rule in one line: one field was wrong, exactly one write was made —
`Set vc2_encoder2 to 200 Mb/s` — and nothing else was touched.

Run A is worth reading carefully: 13 fields were planned and **7 were already
correct**, so Encoder 1's bitrate, Encoder 2's input, Session 2's encoder
assignment, its enable, its destination and its SAP were all left alone. Before
this change every one of them was written on every recall.

**The flash could not be reproduced from a Multiview operation on an
already-correct encoder.** Decoder A was continuously active through all four
runs. What the measurements do establish is the mechanism — see AA.2 — and that
the redundant writes which previously happened on every recall are now gone.

### AA.4 In-window previews

**[LIVE, in a browser, against the bench]** The active Multiview's windows show
their encoders' thumbnails:

| | |
|---|---|
| windows showing a real frame | 2 of 2, `320x180` natural, loaded |
| fit | `object-fit: cover`, window boundaries preserved |
| overlay | source, address, size, `Encoder 2 / Session 2`, `ip_input`, health |
| overlay scrim | present, so the text survives bright and dark video |
| console errors | 0 |

**Refresh, measured from the browser's own network log over 16 s with two
unique sources on the canvas:**

```
6 image requests / 16 s  ->  3 per source  ->  ~1 per unique source per 5 s tick
```

and `0` API requests in the same window: no configuration is re-read, nothing
else is polled. Two windows on one source cost one image, not two.

### AA.5 Dialogs removed, safety kept

**[LIVE]** Show on Display: `0` dialogs before, `0` after, **1** request, status
VERIFIED. A live drop is proven dialog-free by the automated suite, which drives
the real handlers.

**[LIVE]** Live switching through the endpoint, 16 checks, 0 failing:

| case | result |
|---|---|
| enabled -> enabled | VERIFIED, window resolves to the new source, preview available |
| switch to the source already there | **0 planned, 0 written** |
| duplicate source, different window sizes | refused 409, scaler rule, nothing written |
| main window switched | VERIFIED, audio followed to the new source's Session 1, same `ip_input3` |
| secondary window switched | **no audio writes at all** |
| preview disabled on a routed source | reported `disabled`, source still READY |

### AA.6 Restoration

**[LIVE]** 514 fields compared across both decoders and all four encoders: **1
difference, 0 unintended.** The one difference is `multiview13HorizontalTop`, a
Multiview created on the bench while this phase was running and not by it. It
was deliberately left in place — deleting someone else's saved configuration to
make a diff look clean would be worse than reporting it.

One restore step failed first time and is worth recording: putting
`239.100.133.236` back on `ip_input2` was refused with `Unable to open
eth1:1000 (ip_input2)` because `ip_input4` still held that address. It is the
documented rule — a decoder will not open one multicast address and port twice —
and the restore succeeded once the other input had been moved.


## AB. Phase 7C: an explicit bitrate policy, and a page that remembers

### AB.1 What the old algorithm did, and why it was replaced

Every stream was given a 150 Mb/s floor and the rest of the decoder's 900 was
shared out by window area. For two identical 960x544 Side-by-Side windows that
made each one "entitled" to 450 Mb/s, so a window running at 150 was explained
to the operator as a reduction from 450 — a number that came from dividing a
budget, not from anything about the window.

It was arithmetically consistent and operationally meaningless. Phase 7C
replaces it with stated targets.

### AB.2 The policy

```
equal-sized windows   200 Mb/s
the largest window    300 Mb/s
the smaller windows   150 Mb/s

headroom = 900 - current Encoder 1 bitrate
actual   = min(layout target, headroom)
```

Roles are decided from the geometry, never from cell names or window order, so
two rules hold by construction: equal-sized windows always have equal targets,
and no small window can out-target the main one. Both are asserted across all
eleven layouts.

### AB.3 The schema, generated from the planner

| layout | windows | roles | targets (Mb/s) | total |
|---|---|---|---|---|
| `side-by-side` | 2 | equal / equal | 200 / 200 | 400 |
| `2x2` | 4 | equal / equal / equal / equal | 200 / 200 / 200 / 200 | 800 |
| `pip-top-left` | 2 | main / small | 300 / 150 | 450 |
| `pip-top-right` | 2 | main / small | 300 / 150 | 450 |
| `pip-bottom-left` | 2 | main / small | 300 / 150 | 450 |
| `pip-bottom-right` | 2 | main / small | 300 / 150 | 450 |
| `1+3-horizontal-bottom` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |
| `1+3-horizontal-top` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |
| `1+3-vertical-right` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |
| `1+3-vertical-left` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |
| `4-split` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |

### AB.4 Live: the whole schema against the real encoders

**[LIVE]** All eleven layouts planned against the bench, whose Encoder 1 values
are `.141` = 750, `.142` = 700, `.143` = 700, `.253` = 700. 41 checks, 0
failing. Per layout, the decoder aggregate over unique streams:

| layout | windows | unique streams | aggregate |
|---|---|---|---|
| `side-by-side` | 2 | 2 | 350 Mb/s |
| `2x2` | 4 | 4 | 750 Mb/s |
| `pip-top-left` | 2 | 2 | 350 Mb/s |
| `pip-top-right` | 2 | 2 | 350 Mb/s |
| `pip-bottom-left` | 2 | 2 | 350 Mb/s |
| `pip-bottom-right` | 2 | 2 | 350 Mb/s |
| `1+3-horizontal-bottom` | 4 | 4 | 650 Mb/s |
| `1+3-horizontal-top` | 4 | 4 | 650 Mb/s |
| `1+3-vertical-right` | 4 | 4 | 650 Mb/s |
| `1+3-vertical-left` | 4 | 4 | 650 Mb/s |
| `4-split` | 4 | 4 | 650 Mb/s |

Every layout: equal-sized windows shared one target, the aggregate stayed inside
900, and **no plan ever targeted Encoder 1**.

### AB.5 The worked example, on hardware

**[LIVE]** Side-by-Side with `.143` (Encoder 1 = 700) and `.141` (Encoder 1 =
750) — the same 960x544 window twice:

| | `.143` | `.141` |
|---|---|---|
| layout target | 200 | 200 |
| Encoder 1 | 700 | 750 |
| headroom | 200 | 150 |
| **actual Encoder 2** | **200** | **150** |
| Encoder 1 after | **700** | **750** |

The two windows have the same target and different actuals, and neither
Encoder 1 moved. The warning now reads

> Encoder 2 on hw-omni-e4521-00002 is set to 150 Mb/s rather than the 200 Mb/s
> target for this window: Encoder 1 is at 750 Mb/s, leaving 150 of the source's
> 900 Mb/s. Encoder 1 is left alone — it carries the source's primary stream.

### AB.6 Still write-minimal

**[LIVE]** Save, Show, Show again on the same Multiview:

| | planned | already correct | written |
|---|---|---|---|
| first Show | 7 | 4 of 11 fields | 7 |
| second Show | 0 | -- | **0** |

Afterwards the devices held `.143` = 200 and `.141` = 150, and Encoder 1 was
still 700 and 750.

### AB.7 Remembered selection

**[LIVE, in a browser]** Two identifiers in browser storage, nothing else:

| | decoder | Multiview | canvas |
|---|---|---|---|
| chosen | `192.168.100.32` | `multiviewSidebySide` | 2 windows, LIVE |
| after a browser refresh | restored | restored | 2 windows, LIVE |
| after navigating away and back | restored | restored | 2 windows, LIVE |
| remembered decoder not selectable | **cleared** | **cleared** | "Select a decoder to begin." |
| remembered Multiview deleted | **kept** | **cleared** | nothing selected |

0 console errors throughout. The canvas is rebuilt from the decoder's live
subscriptions every time; the stored value only decides which decoder to ask.

### AB.8 Restoration

**[LIVE]** 514 fields compared across both decoders and all four encoders: **1
difference, 0 unintended** — `multiview13HorizontalTop`, created on the bench
while Phase 7B ran and not by this work, deliberately left in place.

## AC. Phase 8: the A/V Matrix knows about an active Multiview

### AC.1 The problem the matrix had

A decoder compositing a Multiview still holds whatever it last watched in
`ip_input1`. The matrix builds its video crosspoints by comparing that
subscription against each encoder's multicast address and port, so it ticked a
crosspoint describing a picture nobody was looking at — and clicking anywhere on
that row wrote a route to a decoder whose display was on a composition, without
saying so.

### AC.2 Deciding whether a decoder is compositing

The only thing that proves it is what the display is actually selecting:

```
hdmi_output1.video.input   matches ip_input<n>   -> an ordinary route
                           anything else          -> that is a Multiview
```

Three things that were considered and rejected as evidence:

| not proof | why |
|---|---|
| saved Multiview objects exist on the decoder | saving is not showing; a decoder can hold several and display none |
| the Multiview page remembers this decoder | that is a UI selection, and survives the decoder being re-routed elsewhere |
| OmniSuite's own metadata says it configured one | it records what was written, not what is on screen now |

`video_input` is already collected by the ordinary scan, so the matrix learns
this for free. Measured: rendering the matrix state with a compositing decoder
present performs **zero** reads of any encoder or decoder.

### AC.3 What the row shows

The row is marked **MULTIVIEW**, names the composition on hover, and draws no
video crosspoint, because none of them is true. The **audio** crosspoint stays,
and that is correct rather than an oversight — Multiview sound comes from one
source over its ordinary audio path. Measured on the bench: with
`multiview13HorizontalTop` on the display, the decoder's audio was genuinely on
`ip_input3` carrying `239.80.132.254`, which is a real encoder the matrix can
name.

### AC.4 Routing to it anyway

Routing a conventional source to a compositing decoder is allowed. It is not
silent.

```
click a crosspoint
  -> warn, once per browser session, with a "do not show this again" option
  -> Cancel: the request is never sent; 0 device mutations
  -> Continue: the request carries exit_multiview, and the server acts
```

The consent travels with the request rather than being remembered on the server,
so a request that does not carry it is refused — **409 MULTIVIEW ACTIVE**. A
route can therefore never take a display off a composition by accident, whatever
the caller is. Measured: the refused route left the decoder byte-identical.

### AC.5 Exit is one transaction

```
1. move hdmi_output1.video.input to an available ip_input   (verified)
2. release the reserved pool inputs this Multiview owned    (verified)
3. apply the requested route                                (verified)
```

Step 2 releases only inputs OmniSuite can prove it configured for that
Multiview, established **before** the display moves — after the move the
evidence is gone. Measured: `ip_input2/4/6/8` released, `ip_input1` and
`ip_input3` left alone.

If step 3 fails, the Multiview is put back rather than leaving the display on
nothing, and the status says which happened:

```
FAILED — ROLLED BACK          the Multiview is showing again, verified
FAILED — ROLLBACK INCOMPLETE  it is not, and the operator is told so
```

### AC.6 Exiting is not deleting

The saved object is untouched. Measured across an exit and a recall: all three
saved Multiviews still present, and the recall reconstructed the composition in
5 planned mutations, 5 written, all verified.

### AC.7 The matrix is truthful immediately

The route response carries the decoder's new `multiview_active` and
`video_input`, so the matrix redraws from the reply. No rescan, no refresh, no
polling added anywhere. The same applies in the other direction: showing a
Multiview records the decoder's new display state, so the matrix shows MULTIVIEW
without waiting for a scan. That was a real defect found by the live run —
before it, recalling a Multiview left the matrix claiming an ordinary route
until the next discovery pass.

### AC.8 The warning's memory

| | |
|---|---|
| acknowledged | `sessionStorage` — survives a refresh and navigation between pages |
| suppressed for good | `localStorage` — survives the browser session, and outranks the above |
| storage unavailable | ask; a browser with storage disabled must not silently skip the warning |

Verified in a real browser against the bench: warned once, not warned again
after a refresh or a trip to the Multiview page and back, warned again in a new
session, and never again once suppressed.


## AD. Phase 8B: copying, groups, and a decoder family that says yes and shows nothing

### AD.1 The measurement that shaped the live testing

| decoder | model | interlock | Multiview shows? |
|---|---|---|---|
| .32 | hw-omni-d4511 | none | yes (in use by another operator) |
| .153 | hw-omni-d4511 | none | **yes**, VERIFIED, picture on screen |
| .151, .152, .154 | hw-omni-d4511 | Video Wall ON | refused, correctly |
| .155, .161, .38 | hw-omni-d4111 | none | **no** |

The 4111s are the finding. OmniSuite's capability probe reports them as
Multiview-capable because the `multiview` configuration node is present, and
every stage of a recall against them is accepted and reads back correctly --
encoder scalers, Session 2, the decoder inputs, the object, the display
selection. Then the decoder's own Input status says there is no active video on
the composition, and the transaction rolls back.

So the picture never arrives on this family, and nothing before the final status
check can tell. That final check is the only stage that is not a read-back of
our own write, and it is the only reason this is caught at all.

**Not changed in this phase.** Capability is currently decided by the
configuration node, and rewriting that rule on the strength of one family
observed on one bench would be guesswork of exactly the kind this project avoids.
The behaviour is safe as it stands -- the operation refuses, explains itself and
restores -- and the measurement is recorded here for whoever decides the rule.

### AD.2 What this made possible, and what it did not

A fully synchronised two-screen group could not be demonstrated: exactly one
decoder on the bench is both free of interlocks and able to display a Multiview,
and the other d4511s have Video Wall enabled, which is never turned off to make
a test pass.

What was demonstrated live, on real hardware, is the group transaction's failure
path -- which is the part that matters most:

```
group [ .153 (works), .155 (accepts everything, shows nothing) ]
  -> save to group          both members hold the definition, no display changes
  -> show on group          .153 configured and shown, .155 fails its status check
  -> FAILED — GROUP ROLLED BACK
     .153 restored  verified
     .155 restored  verified
```

Half the room was not left on the new layout, and the result did not claim
success. The synchronised path, group drift and group live switching are covered
by the simulator suite.

### AD.3 Copying

Copy carries layout and window assignments and nothing about resources, so the
target plans its own. Measured on .153: the copy changed no display on either
decoder, Show then reconciled the target's own inputs
(`239.100.132.254` and `239.100.133.108` into its own pool), and deleting the
copy left the original untouched.

## AE. Phase 8C: what may be retried, and a window that will not lock

### AE.1 The reported failure was true

`Encoder hw-omni-e4521-00002 did not answer, so its Session 2 cannot be prepared
for window top_left.` That encoder is 192.168.100.141, and it answered **none**
of five TCP probes on port 80, each timing out at 2 s, while every other device
on the bench answered in under 0.03 s. It is switched off. The sentence was
correct and the operation wrote nothing.

What was wrong was the shape of the answer:

| condition | was | now |
|---|---|---|
| a device did not answer | 400 | **503**, naming the unit and the attempts |
| a live conflict | 409 | 409 |
| a request that cannot be built | 400 | 400 |
| an unknown Multiview | 404 | 404 |

Measured after the change, the same request returns in the same 1.6 s with
`classification=device_unreachable`, `writes=0`, and a sentence naming the
encoder, the window and that nothing was changed.

### AE.2 Every refusal, classified

| operation | result | writes | kind |
|---|---|---|---|
| save using the offline encoder | 503 | 0 | device unreachable |
| show a Multiview that is not there | 404 | 0 | invalid request |
| show with no name | 400 | 0 | invalid request |
| copy onto its own decoder | 400 | 0 | invalid request |
| copy to a decoder with Video Wall on | 409 | 0 | policy refusal |
| copy where the name is taken | 409 | 0 | resource conflict |
| switch a window on an inactive Multiview | 409 | 0 | policy refusal |

Not one of them wrote anything, and not one is worth retrying unchanged.

### AE.3 Why the transport needed no rewrite

`_ws_send_recv` opens a connection per call and closes it in a `finally`. There
is no pool, nothing is reused, and a failed call leaves nothing behind -- so the
stale-socket, reuse-after-close and shared-socket questions are all answered by
construction. Sources were already deduplicated by identity and read
concurrently.

### AE.4 The window that will not lock

After some layout transitions a window stays black. Measured state while it was
black:

```
ip_input4   enabled=True   239.100.133.108   packets=53121   (and rising)
.143        Encoder 2 960x544 @200, Session 2 enabled, destination correct
subframe    right (960x544)   video.input.active = False     for 60+ seconds
```

Everything is configured correctly, the packets are arriving, and there is no
picture. It is intermittent -- about four occurrences in thirty attempts -- and
it appears on transitions where a source's Encoder 2 changes scaler size while
its stream keeps flowing to the same decoder input.

What was tried, on the bench:

| remedy | result |
|---|---|
| wait | still black after 60 s |
| disable and re-enable the decoder input | still black |
| show the same Multiview again | **0 writes, still black** |
| show a different Multiview, then come back | locked in 0.1 s |

The third row is the one that mattered. Write-minimality correctly concluded
that every field already held the wanted value, so the operator's obvious remedy
was guaranteed to do nothing at all. "The configuration matches" is not the same
as "the picture is on the screen".

A window seen failing to lock is now remembered, and the next attempt at that
Multiview re-establishes that window's input instead of skipping it. Narrowly:
that input only, on the decoder only, forgotten as soon as it locks. Whether
that clears the underlying condition every time is **not** proven -- a bare
input bounce did not, in the one observation available -- so the guide also
tells the operator the remedy that did work.

### AE.5 What the guardrail does, and what it does not

Measured on the bench after the change, having provoked the condition:

```
first Show    VERIFIED — WINDOW NOT LOCKED    right (960x544)
second Show   2 writes performed, 0 planned
              [verified] Take ip_input4 down, because its window did not lock last time
              [verified] Point ip_input4 at 239.100.133.108:1000 again
              still black
```

So the no-op is gone: the operator's second attempt now genuinely re-establishes
the window instead of concluding there is nothing to do. **It did not clear this
condition**, which is consistent with the earlier observation that a bare input
bounce does not. Showing a different Multiview and returning still does, in 0.1s.

That is the honest position. The guardrail is kept because it is safe, narrow
and strictly better than writing nothing -- and because a window that is black
for a different reason, one where the input really does need re-establishing,
would be fixed by it. It is not a cure for this fault, the User Guide tells the
operator the remedy that works, and the underlying behaviour belongs with
whoever owns the decoder firmware.

## AF. Phase 8D: a flake with a cause, and a source that was never usable

Two cleanup items, both of which had to be answered before a publication gate
could mean anything.

### AF.1 The flake was not in the test

`MatrixNetworkReadinessTests.test_it_stops_asking_once_every_mask_is_known`
failed occasionally in a full-suite run and never on its own. The temptation is
to call it timing-sensitive and give it a longer wait. It is not, and that would
have hidden the defect while leaving the same work landing somewhere else.

**What the instrumentation found.** Wrapping the startup entry points and
recording thread name and time:

```
  T+ 0.00s  _startup_usb_refresh          Thread-3  enter
  T+ 2.00s  _refresh_usb_parent_map       Thread-3  enter
  T+ 2.00s  _usb_live_refresh             Thread-3  enter
  T+ 2.00s  _refresh_icron_network_config Thread-3  enter
```

`start_background_startup_tasks()` starts three plain `threading.Thread`
daemons. `_startup_usb_refresh` slept two seconds and then called three module
globals. Global lookup happens when the call runs, not when the thread starts,
so each call resolved against whatever the test running at T+2.0s had patched
in.

`ServerTestBase.inline_background()` could not help. It replaces
`service._executor.submit`, and these are not executor submissions. An earlier
trace confirmed it from the other side: **0 submissions** reached the shared USB
executor during a full suite, because the base class installs a per-test service
and inlines that one.

**Why this test is the one that notices.** It replaces
`_refresh_icron_network_config` with a recorder and asserts the recorder is
empty — that a known mask is not re-read on every Matrix poll. A leaked call
into that recorder is indistinguishable from the defect the test exists to
catch. The other two assertions in the test (population changed, mask lost) are
reachable the same way through `_refresh_usb_parent_map`; the one observed was
the third.

**Measured, not argued.** A single shot usually misses, because the vulnerable
window is the last few milliseconds of a 79 ms test. Sweeping the start offset
so the fire lands somewhere inside the test:

| campaign | result |
|---|---|
| aimed sweep, arming from outside a test | **7 of 30** reproduced |
| real sequence (arming test → T+2s → target), as shipped | **4 of 45** reproduced |
| real sequence, with the fix | **0 of 45** |

Every failure carried the same assertion:
`[True] != [] : a known mask must not be re-read on every Matrix poll`.

Runs that did **not** reproduce it, and why they do not contradict the above:
2 full-suite rounds, 25 rounds of the USB module alone (22,275 tests), and 80
repeats of the target after priming with the other suites. The first is simply
the base rate. The second and third both remove the trigger — the module alone
did not land the fire in the window, and priming excluded the target's own
module, where the arming tests live.

### AF.2 The fix is ownership, not patience

- `start_background_startup_tasks()` records the threads it starts in
  `_startup_threads`; `await_startup_tasks(timeout)` waits for them and reports
  whether they finished. Startup still waits for nothing.
- The hard-coded two-second settle became `STARTUP_USB_SETTLE`, so a caller with
  nothing to settle waits for the work rather than the delay.
- `ServerTestBase` registers the wait **last in `setUp`**, so it runs **first in
  teardown**, while the test's own stubs are still installed. The work belongs to
  the test that armed it and is observable by it; anything still running fails
  that test by name.

`StartupTaskIsolationTests` holds the contract, including a test that leaks on
purpose and must be reported. Four mutations of the fix were all caught:

| mutation | caught by |
|---|---|
| the contract stops waiting | `test_a_leak_fails_the_test_that_caused_it`, `test_no_startup_thread_outlives_the_test_that_started_it` |
| `await_startup_tasks` always claims success | `test_a_leak_fails_the_test_that_caused_it` |
| the startup threads go untracked | `test_the_startup_threads_are_waitable` |
| the settle delay returns inside the suite | `test_the_settle_delay_is_not_waited_out_by_the_suite` |

### AF.3 A source that was advertised Ready and then refused

`192.168.100.218` was listed **Ready** and then refused by the planner. Read
live:

```
Encoder 1 : 900 Mb/s, headroom 0
list      : configuration_required — "...using 900 of its 900 Mb/s budget,
            which leaves 0 Mb/s -- less than the 20 Mb/s a Multiview stream
            needs... OmniSuite will not change it."
planner   : refuses, same rule
Encoder 1 afterwards: 900 (unchanged: True)
```

The cause was not a disagreement about policy. The scan cache carries no
Encoder 1 bitrate, so the list had no way to ask the question the planner asks.
A source has **one** 900 Mb/s budget shared by both encoders; if Encoder 1 holds
all of it, nothing is left for a Multiview window.

`multiview_headroom(encoder1_bitrate)` in `omni_multiview.py` is now the single
rule, used by `classify_source` and by the allocator, so the list and the plan
cannot disagree. Boundaries, all asserted:

| Encoder 1 | headroom | usable |
|---|---|---|
| 0 – 880 Mb/s | 900 – 20 | yes |
| 890 Mb/s | 10 | no |
| 900 Mb/s | 0 | no |

The boundary is `ENCODER2_MIN_BITRATE` (20 Mb/s) — the smallest stream the
hardware will run — not a number invented for this rule. A bitrate that could
not be read is **unknown, not no**: the source stays Ready and the planner finds
out before it writes.

**Cost.** One `config_get` per *reachable* encoder, concurrent, only on
`/api/multiview/sources`, and only when that endpoint is actually probing.
`probe=0` answers from the cache and reads nothing. Nothing was added to the
scan path or to idle. The Phase 7A cost guard was rewritten to bound the cost
(`test_listing_sources_reads_one_node_per_encoder_and_no_more`) rather than to
forbid the read, and a second guard proves the unprobed path stays free.

**Encoder 1 is read. It is never written.** `encoder1_target` is always `None`,
and `test_encoder_1_is_never_written_to_make_room` asserts it at the boundary
where the temptation would be greatest.

## AG. Phase 8E: display output, and two fields that disagreed

### AG.1 The panel

The Multiview page could describe the composition an operator had open in
detail, and could not say what the decoder was showing. Those are different
questions, and the only way out of Multiview was the A/V Matrix.

**Display output** sits above the canvas and reports one of three states,
derived from `hdmi_output.video.input` and nothing else:

| state | shown | derived from |
|---|---|---|
| `multiview` | MULTIVIEW · ACTIVE, named | `active_multiview_name(video_input)` |
| `source` | LIVE, with the encoder named | the ip_input's multicast, resolved by `_resolve_subscription` |
| `none` | NO VIDEO | the input is selected but disabled or carrying no address |

It rides on `/api/multiview/state` and is computed from the three reads that
endpoint already performs. Measured: `decoder.reads == ["multiview",
"hdmi_output", "ip_input"]`, unchanged, and zero writes. No timer was added.

A stream arriving from a device nobody has discovered is reported as `source`
with no name. Something is plainly on the screen, and `none` would be a lie.

### AG.2 The drop is the A/V Matrix transaction

`POST /api/route {decoder, encoder, mode:"av", exit_multiview:true}` — the same
request the A/V Matrix sends. The server already owned the exit, the route, the
verification and the rollback; the page adds none of it. A test asserts the
handler contains no device protocol and no Multiview endpoint at all.

**Session 1, and only Session 1.** `set_route` routes `v_mcast`/`a_mcast`, which
`_encoder_session_matrix_fields` derives explicitly from `session1`. Asserted
three ways: the fields are Session 1 and differ from Session 2; the route body
contains no reference to `session2`; and routing a source writes nothing at all
to that encoder, so Encoder 2 is never prepared for a conventional route.

The warning, its session acknowledgement and its permanent preference moved into
`ui/confirm.js` under the keys the A/V Matrix already wrote
(`matrix_multiview_exit_acknowledged`, `matrix_multiview_exit_suppressed`), so
no second preference exists. A test asserts both files still name those keys.

### AG.3 Two eligibilities

The page made a tile draggable only when it was Multiview-ready, so the `.218`
case — Encoder 1 using the whole 900 Mb/s budget — could not be dragged
anywhere, including to the display output, where the reason it was refused does
not apply.

| | Multiview window | conventional route |
|---|---|---|
| encoder | Encoder 2 / Session 2 | Encoder 1 / Session 1 |
| needs a Session 2 multicast | yes | no |
| needs budget headroom | yes | no |
| barred by the Multiview model exclusion | yes | no |

`normal_route_eligibility()` answers the second question, the source list carries
both, and the page holds neither. Live on the bench:

```
192.168.100.218: multiview=configuration_required  normal_route=ready
```

Windows refuse what they cannot carry, at the window rather than at the tile.

### AG.4 A derived field that outlived its source

`/api/state` derives `multiview_active` from the cached `video_input`, and the
live-state overlay then replaces `video_input`. It did not recompute the verdict.
Measured on the bench:

```
video_input      = multiview13HorizontalBottom
multiview_active = False
multiview_name   = None
```

A decoder cannot be showing a Multiview and not showing one. The verdict is
recomputed after the overlay, from the value that survived it.

Fixing it turned three existing tests red, which was the fix working.
`omni_matrix_logic._decoders` is process-global and the overlay reads it, so one
test's decoder had already been deciding what a later test's `video_input` said
— those tests were asserting on the clean `multiview_name` while the
contradictory `video_input` sat beside it. The Multiview test base now isolates
those globals, the same rule Phase 8D wrote for background work.

### AG.5 A rollback that did not say what it had done

Leaving Multiview records the input the display moved to. Putting it back after
a failed route recorded nothing, so the A/V Matrix went on showing a
conventional route that had been rolled back. `_restore_multiview_after_failed_route`
now records the restored display.

Both defects were found by looking at the bench rather than at the tests, and
both are covered by tests that fail without the fix.

### AG.6 Live results, decoder 192.168.100.32

Twenty of twenty checks passed on the first pass (A–T), with the rollback proved
separately because the encoder chosen for the "unreachable" case turned out to
be reachable — a bad premise, not a passing test, and reported as such. With the
route call forced to fail, the real rollback ran against the real decoder and
all six checks passed. The two surfaces then agreed across
`Multiview → route → Show → failed route → rollback`, 4 of 4, and the decoder
was left on the Multiview it was found on.

## AH. Phase 8F: what an empty window really is

### AH.1 The discovery gate

Nothing was implemented until decoder 192.168.100.32 had been asked. Raw object
writes and semantic readback, one variable at a time, with the display
untouched:

| probe | result |
|---|---|
| 4 windows, every one assigned | accepted |
| 4 windows, one with `input: ""` | **accepted, and the empty input survives readback** |
| 1 window only, assigned | accepted — an object may hold fewer windows than its layout |
| 1 window only, `input: ""` | accepted |
| no subframes at all | accepted, geometry retained |
| no `subframes` key | accepted, geometry retained |
| name only | accepted, but the object defaults to **3840x2160** |
| a window pointing at `ip_input99`, which does not exist | **accepted and stored as-is** |

Then, with the display changed deliberately and restored afterwards:

```
show a fully EMPTY 2x2
  display        : multiviewP8FEmpty
  output status  : active=True, resolution 1920x1080
  input status   : active=True, 1920x1088
  every window   : health="not subscribed", subscribed=False
```

and a partial one, through the shipping endpoints:

```
C. apply partial 2x2 (2 assigned, 2 empty)  -> VERIFIED
G. show it                                   -> VERIFIED
     top_left      health=live  packets=61043
     top_right     health=live  packets=3365
     bottom_left   health=not subscribed
     bottom_right  health=not subscribed
D. assign bottom_left live                   -> VERIFIED, others untouched
F. clear top_right live                      -> VERIFIED, others untouched
   the display survived all of it            -> True
```

### AH.2 The decision

**Model A.** An unassigned window is a real subframe with an empty input,
because that is what the hardware itself does. No virtual representation was
invented, and none was needed: the decoder composites an all-empty Multiview and
keeps its output active.

Two device behaviours recorded because they cost time to find:

- An object created with **only a name** defaults to 3840x2160. Geometry is
  always written explicitly.
- The decoder answers **any** rejected method call with
  `Invalid username/password`. An object name that does not start with
  `multiview` produces it; so does a malformed `add_multiview`. It is not an
  authentication problem, and reading it as one sends you a long way in the
  wrong direction.
- The decoder does **not** validate the input a subframe points at. OmniSuite
  must, because nothing downstream will.

### AH.3 What the code already had

`_build_mutations` had documented and implemented the save/activation split
since Phase 7B: saving writes the Multiview object and nothing else. The
planner's **errors** had not been split the same way, so a Save inherited every
execution refusal. That, and the single line `"Assign at least one source before
applying."`, were the whole of what stood between the existing architecture and
the one this phase asked for.

### AH.4 Live results

Section 27, with a Multiview genuinely active on .32 and 192.168.100.142 feeding
its main window at 1280x720:

```
before: encoder2_input hdmi_input1 | scaler 1280x720 | bitrate 200
        session2 enabled=True dest=239.100.132.254:1000 sap=False
  install 11 standard layouts        VERIFIED (9 installed, 2 conflicts left alone)
  save a preset referencing .142     VERIFIED
  edit its assignments               VERIFIED
  save a preset with no sources      VERIFIED
after : identical
display still multiview13HorizontalBottom, all four windows live
```

Section 29 ran the whole operator workflow on .32 and passed 20 of 20, including
showing an empty layout, filling two windows live, clearing one, confirming that
merely reading another preset moved nothing, and finding the partial assignment
intact after a round trip.

Section 28, cross-decoder:

```
.32 uses 192.168.100.142 Encoder 2 at 1280x720
.161 SAVES a preset wanting it at 960x544      -> allowed
.161 SHOWS it                                   -> HTTP 409 conflict
   "Window top_left needs Encoder 2 on hw-omni-e4111-08414 scaled to 960x544,
    but multiview13HorizontalBottom on hw-omni-d4511-085c6 already depends on
    it at a different size."
   .142 scaler unchanged, .32 still showing, all four windows live,
   .161 display untouched
```

The compatible-size half of section 28 could not be completed on the bench: the
second decoder's Show reached the known black-window condition and rolled back.
That run still shows the plan was allowed rather than refused, the shared
encoder was not re-scaled, and the active decoder was undisturbed. Compatible
reuse is covered by simulator tests. **The black-window behaviour is unchanged
and is not claimed to be fixed.**

### AH.5 A defect this phase introduced

Showing an installed standard layout saved over its metadata and lost the marker
recording which of the eleven it came from, so a later install would have
created a duplicate of a layout the decoder already had. Found because bench
cleanup removed 8 of 9 installed layouts and left one behind. The identity now
survives being saved over, and `test_showing_a_standard_layout_does_not_make_it_a_duplicate`
holds it.
