# OmniSuite V1.1.0

A Multiview release. A decoder can now show several encoders at once, as a
saved, editable layout — and the page that builds it tells you what the decoder
is actually displaying rather than what you last asked for.

Everything from V1.0.7 is unchanged and carried forward.

---

## Highlights

- **Multiview.** Build a multi-window picture on a decoder from several
  encoders, with eleven standard layouts installable in one action.
- **Presets that do not have to be finished.** Save a layout with every window
  filled, some of them filled, or none at all, and come back to it later.
- **Show on Display saves first.** Whatever is on the canvas is what gets saved
  and what gets shown. You never have to press Save first.
- **Display output.** A small view of what the decoder is really putting on the
  screen, and a drop target for putting an ordinary source back on it.
- **Groups.** Show one Multiview across several decoders together, or not at
  all.
- **Nothing is trusted.** Every change is read back from the device, and a
  change that cannot be confirmed is undone.

---

## Multiview

- **Eleven standard layouts** — 2×2, side by side, four PiP corners, four 1+3
  arrangements and 4-split — installed on a decoder, or on every decoder in a
  group, in one action. Installing them configures no source and changes no
  display.
- **Saved presets.** A Multiview is stored on the decoder and can be recalled,
  copied to another decoder, renamed or deleted. Selecting one opens it for
  editing; it does not put it on screen.
- **Empty and partial layouts.** Windows can be left empty. An empty window
  uses no stream, no decoder input and no bandwidth, and can be filled later.
- **Live switching.** While a Multiview is on the display, dragging a source
  into a window changes the picture immediately. Clearing a window leaves the
  others alone.
- **A source list you can work with.** Filter by name, model or address, and
  scroll the list without losing sight of the canvas you are dropping onto.
- **Source previews.** Hover a source to see what is on it before you use it.
- **Bandwidth planning.** An encoder's two streams share one 900 Mb/s budget.
  OmniSuite works out what is spare and sizes the Multiview stream to fit.
- **Encoder 1 is never reduced.** It is read to see what is available and is
  never lowered to make room. A source with too little left is reported as
  needing configuration rather than quietly degraded.
- **Shared sources.** Two displays can show the same source when they need it
  at the same size. If another display needs it at a different size, the new
  Multiview is refused with the reason rather than resizing a picture somebody
  is watching.
- **Audio follows the main window**, over that source's ordinary Session 1
  audio.

---

## Routing and display output

- **Display output** shows what the decoder is actually displaying: a Multiview
  by name, an ordinary source with its picture, or nothing at all — read from
  the decoder, not inferred from the page.
- **Drag a source onto it** to route that source normally. If a Multiview is on
  the display it is exited first, and the saved Multiview is kept.
- **The A/V Matrix knows about Multiview.** A decoder showing one is marked as
  such, and routing a source to it asks before replacing the picture.
- **Ordinary routing is unchanged.** A conventional route carries the encoder's
  Session 1 audio and video, exactly as before.

---

## Reliability and safety

- **Read-back on everything.** A command that reports success is not treated as
  success; the device is read again and a change that cannot be confirmed is
  undone.
- **Rollback.** If a route fails after a Multiview has been exited, the
  Multiview is put back rather than leaving the display blank.
- **Saved work survives.** If a preset saves but cannot be shown — a source is
  off, another display needs it at a different size — the preset is kept and
  OmniSuite says why it could not be shown.
- **Protected features stay protected.** OmniSuite does not turn off Video Wall
  or Fast Switching to make Multiview possible.
- **No new polling.** The Multiview page makes no periodic device requests.

---

## Documentation

- The **User Guide** covers Multiview end to end: what it uses on the encoder
  and the decoder, the bandwidth rules, shared sources, presets, groups, and
  what to do when a Multiview will not start.
- **Settings** now states who publishes OmniSuite and links to the licence,
  which the application serves from the build it was made from.
- Third-party components and their licences are listed in
  `THIRD_PARTY_NOTICES.md`, which ships with the application.

---

## Known limitations

- A decoder occasionally leaves one Multiview window black after a layout
  change, even though the configuration is correct and the stream is arriving.
  OmniSuite detects this and reports which window rather than reporting
  success; showing another Multiview and returning clears it. This is decoder
  firmware behaviour and **is not fixed by this release**.
- A decoder with **Video Wall** enabled cannot run Multiview. **Fast Switching**
  prevents it on the affected 1xx family. Both are reported with the reason.
- Multiview runs at **1920×1080** in this release.

---

## Upgrading

Replace the previous installation with this one. Devices, routes and settings
are held on the equipment and in your own configuration, not in the
application, so nothing is migrated.

The Multiview notice and the Save confirmation are scoped to the release that
last acknowledged them, so both appear once on first use of V1.1.0.
