# OmniSuite V1.0.4 Release Notes

## Highlights

OmniSuite V1.0.4 focuses on USB Matrix reliability, cache accuracy, and safer live Matrix operation. USB pairing now performs serialized, verified route changes, cross-subnet pairing is blocked consistently, and Matrix pages avoid overwriting active controls while polling refreshes device state.

## What's New Since V1.0.3

- USB Matrix route changes now unpair stale LEX relationships before pairing the selected LEX.
- USB pairing read/write verification now checks both LEX and REX state.
- Rapid USB route selections are serialized to avoid lost pairings during quick operator changes.
- Same-/24 USB pairing is enforced in the backend and UI.
- USB Matrix now keeps active dropdowns and text fields stable while background refreshes are running.
- Matrix and Configure pages now defer rerenders while live controls, modals, codec selectors, and video wall editors are active.
- Codec compatibility checks now block incompatible encoder-to-decoder routes.
- Cached unit loading now merges `units_cache.json` with `scan_results.json` and infers encoder/decoder roles from available fields.
- Cache writes are now atomic to reduce the chance of corrupt local state after interrupted saves.

## Fixes

- Fixed stale USB pairing feedback after route changes.
- Fixed cross-subnet USB pair attempts being allowed after a warning.
- Fixed rapid 1-2-3 style USB selections dropping intended routes.
- Fixed matrix role classification when cached units do not carry a simple `role` value.
- Fixed stale disconnected status on Device Manager startup when polling is enabled and about to refresh the rows.
- Fixed decoder `input` resolution being available when Fast Switching or Video Wall makes that setting invalid.
- Fixed acknowledged decoder setting writes being reported as failed solely because the verification read timed out.

## Build And Automation

The cross-platform workflow builds packaged artifacts for:

- Windows
- Linux
- macOS Intel
- macOS Apple Silicon

Pushing tag `V1.0.4` triggers the release build and GitHub Release publishing path.

