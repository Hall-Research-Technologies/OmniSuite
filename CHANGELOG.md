# Changelog

## V1.0.5 - 2026-08-24

- Rebuilt the OmniSuite release after V1.0.4 artifacts were not created.
- Included the launcher update that uses native Tk buttons on non-Windows platforms to avoid unsupported themed button options.

## V1.0.4 - 2026-08-24

- Hardened USB pairing so route changes unpair stale LEX relationships before pairing the selected LEX, verify both sides, and serialize rapid multi-REX updates.
- Enforced same-/24 USB pairing policy in both backend and UI so cross-subnet pair attempts are rejected instead of warning-and-continuing.
- Improved USB Matrix feedback by preserving active controls during polling, extending verification refreshes, and showing shared LEX usage counts.
- Improved cached matrix state by merging `units_cache.json` with `scan_results.json`, inferring encoder/decoder roles from device fields, and saving cache updates atomically.
- Improved Matrix and Configure pages so background refreshes defer while live controls, modals, codec selectors, or video wall controls are active.
- Added codec compatibility protection so incompatible encoder/decoder routes are blocked in the UI and codec mismatches are clearly surfaced.
- Tightened decoder input handling by hiding `input` resolution while Fast Switching or Video Wall is enabled and rejecting invalid `input` resolution writes on the backend.
- Improved decoder setting verification so acknowledged writes can return requested fields when a follow-up read fails instead of falsely reporting a failed update.
- Improved Device Manager startup status display so polling-enabled sessions show `N/A` instead of stale disconnected status while fresh polling is about to update rows.

## V1.0.3 - 2026-08-21

- Fixed macOS scan persistence for packaged OmniSuite builds.
