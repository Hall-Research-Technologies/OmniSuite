# OmniSuite V1.0.5 Release Notes

## Highlights

OmniSuite V1.0.5 republishes the OmniSuite release build so the cross-platform artifacts are generated, and includes the launcher fix added after V1.0.4.

## What's New Since V1.0.4

- Rebuilt from the current `main` branch after the V1.0.4 artifact publish did not produce downloadable packages.
- Launcher buttons now use native Tk buttons on non-Windows platforms, avoiding unsupported themed button styling during startup.

## Fixes

- Retains the V1.0.4 USB Matrix reliability, cache, codec, and decoder setting verification fixes.

## Build And Automation

The cross-platform workflow builds packaged artifacts for:

- Windows
- Linux
- macOS Intel
- macOS Apple Silicon

Pushing tag `V1.0.5` triggers the release build and GitHub Release publishing path.
