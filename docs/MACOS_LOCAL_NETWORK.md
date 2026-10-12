# macOS Local Network access

Status: **leading explanation, not yet proven.** A build that works on a Mac is
not evidence that the defect is understood. This file records what has been
measured, what is inferred, and what still has to be validated on real hardware.

## The defect

On macOS, scanning the adapter's own subnet finished almost instantly with no
devices, while scans of other ranges could succeed. Every connection error was
swallowed, so a machine that refused every connection looked exactly like a
network with nothing on it.

## Evidence

Measured by reading the shipped archives (no code was run):

| | V1.1.0 (published) | OmniSuite diagnostic | OmniSuite Plus diagnostic |
|---|---|---|---|
| `CFBundleIdentifier` | `OmniSuite` | `OmniSuite` | `OmniSuite` |
| `CFBundleShortVersionString` | `0.0.0` | `0.0.0` | `0.0.0` |
| `NSLocalNetworkUsageDescription` | absent | absent | absent |
| `NSBonjourServices` | absent | absent | absent |
| `Info.plist` otherwise | identical | identical | identical |
| Signature | ad-hoc (`_CodeSignature` present) | ad-hoc | ad-hoc |
| Executable SHA-256 | differs per build | differs per build | differs per build |

* `tools/build_release.py` changed between V1.1.0 and the diagnostic builds only
  in how artifacts are named. There was **no packaging change** that could have
  introduced the permission prompt.
* No build has ever declared a Local Network usage description.
* The application uses no Bonjour or mDNS. It opens direct TCP/WebSocket
  connections and sends UDP broadcasts (USB extender discovery). Both are local
  network access; neither is a Bonjour service, so no service types are declared.

Reported by the tester: the Apple Silicon diagnostic build discovered local
devices, and macOS showed a Local Network prompt when it launched, which was
allowed.

## What can be concluded, and what cannot

Supported by the evidence:

* The prompt did not come from a packaging change. The plists are identical.
* Every build is a different code identity to macOS: each is ad-hoc signed and each
  executable differs. An application whose identity changes can be asked about
  again. That is existing macOS behaviour for unsigned, ad-hoc-signed software and
  explains why a new download can prompt even though earlier ones did not.

Inferred, not measured:

* While a prompt is open, or after Don't Allow, connections to the local subnet fail
  immediately. Reproduced in tests by simulation only.
* That is what the original instant "0 found" scan was: connection attempts refused
  at once by the operating system and then reported as an empty network.

Not established:

* What V1.1.0 did on the reporting machine: whether a prompt appeared, was
  dismissed, or never appeared. This is the single most useful missing fact.
* Whether macOS shows the prompt with no usage description at all (the evidence says
  the prompt appeared for a build that had none), and what text it showed.
* Whether changing the setting takes effect without restarting the application.

## What the product does about it

* **Declaration.** The packaged app now carries `NSLocalNetworkUsageDescription`,
  worded for the product, so the prompt explains itself. The build adds it after
  PyInstaller (which cannot) and signs the bundle again, ad hoc, because editing the
  plist invalidates the signature and Apple Silicon will not run an invalid one.
* **Verification on the artifact.** The smoke test reads the shipped `Info.plist` and
  `codesign` details and fails the build if the description is missing or the
  signature does not verify. The values are printed into the CI log.
* **Diagnostics are kept.** A scan the operating system refuses is reported as a
  failed scan (503 `scan_blocked`) with a `likely_cause`, not as "0 found", and
  leaves the device list untouched. A scan that completes and finds nothing is a
  genuine empty result.
* **Guidance.** On macOS the message says to allow OmniSuite under System Settings >
  Privacy & Security > Local Network and scan again. Windows and Linux get firewall
  wording instead. The User Guide has a "macOS: Local Network access" section.

## What the product does not do

* It never resets, grants or changes a privacy permission, and never runs `tccutil`.
  The operator decides, in System Settings.
* It does not raise file-descriptor limits, retry into a refusing device, or change
  concurrency to work around a permission.
* It does not declare Bonjour services it does not use.

## Not solved by this

A stable permission across updates needs a stable code identity, which means signing
with an Apple Developer ID certificate. That is a business and infrastructure
decision, not a code change, and is deliberately not attempted here. Until then each
new download may prompt again.

Both products share the bundle identifier `OmniSuite`. The build now sets the display
name and bundle name to the product's own (`OmniSuite` or `OmniSuite Plus`) so that
System Settings lists them separately; the identifier, executable and app file name
are unchanged because the launcher and smoke test find the app by them. Whether System
Settings actually lists the Plus build under its display name is part of the validation
below.

## Validation on a real Mac

Do not reset any permission automatically. Use a Mac, and for each product line
(OmniSuite and OmniSuite Plus) on Apple Silicon and Intel where available:

1. **Declaration.** Unzip the diagnostic artifact and run
   `plutil -p OmniSuite.app/Contents/Info.plist`. Confirm
   `NSLocalNetworkUsageDescription` is present and `NSBonjourServices` is not. Run
   `codesign --verify --deep --strict OmniSuite.app` and confirm it prints nothing and
   exits 0.
2. **First launch.** Open the app. Record whether macOS shows a Local Network prompt,
   its exact text (it should be the product's description), and whether it appears
   at launch or at the first scan.
3. **While the prompt is open.** Press Scan on the adapter's own subnet *without*
   answering. Expect "The scan could not reach the network" with the System Settings
   path, not "0 found". Keep the `diagnostics` object.
4. **Allow.** Answer Allow and scan again. Expect devices. Keep `diagnostics`.
5. **Deny, then change.** Turn OmniSuite off under System Settings > Privacy &
   Security > Local Network. Scan: expect the failure message and the device list
   unchanged. Turn it back on and scan: record whether it works at once or only after
   quitting and reopening OmniSuite.
6. **Other subnet.** Scan a range reached through the router while access is off, and
   record whether it succeeds. This tests the "local only" part of the explanation.
7. **A second build.** Download a different diagnostic build of the same product and
   record whether the prompt appears again.
8. **V1.1.0.** If the original machine still has V1.1.0, record what it does on a
   first scan with access off, on, and after Don't Allow.

9. **The notice.** With access off, scan. Confirm the notice appears under the Scan
   button with the product's name, the System Settings path, **Retry Scan** and **Open
   System Settings**, and that Technical details holds the diagnostics. Press **Open
   System Settings** and record whether it opens Privacy & Security, whether it lands on
   Local Network, and what Safari or Chrome asks first. If it is unreliable, the button
   should be removed (the manual path is always shown).
10. **Retry.** Switch access on and press **Retry Scan**; confirm the notice clears and
    devices appear. Scan a genuinely empty range and confirm **no** notice appears.
11. **Gatekeeper.** On a clean Mac, follow `READ-ME-FIRST-macOS.txt` from the zip: record
    whether Open Anyway appears where it says, and how long it stays available.
12. **Names.** Confirm System Settings lists the Plus build as "OmniSuite Plus".

Send back, for steps 3 to 6: the `diagnostics` object, the message shown, and what
System Settings listed.
