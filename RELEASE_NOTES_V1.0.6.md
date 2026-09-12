# OmniSuite V1.0.6 Release Notes

## Highlights

OmniSuite V1.0.6 fixes Video Wall configuration fidelity for physical units and makes the configurator open with the unit's actual layout state loaded.

## What's New Since V1.0.5

- Matrix page load now performs a one-time live read of decoder Video Wall settings before rendering the configurator.
- Selecting a decoder in the Video Wall configurator refreshes that decoder's current settings once, without enabling an auto-refresh loop.
- The Video Wall Layout picker now opens at the configured wall size and highlights the decoder's current display position.

## Fixes

- Inches and millimeters now write to the unit as physical dimensions and offsets instead of being converted into grid counts.
- Physical-unit readback now preserves whole-number dimensions instead of treating them as raw grid values.
- Edge compensation mode writes normalize legacy `bezel_compensation` input to the device-accepted `bezel compensation` value.

## Build And Automation

The cross-platform workflow builds packaged artifacts for:

- Windows
- Linux
- macOS Intel
- macOS Apple Silicon

Pushing tag `V1.0.6` triggers the release build and GitHub Release publishing path.
