<!-- DRAFT: remove this line when V1.1.1 is released. Not yet validated on every Mac. -->
# OmniSuite V1.1.1

A maintenance release about scanning on macOS. A scan that macOS blocks is now reported as
blocked, with the steps to fix it, instead of looking like a network with nothing on it.

Everything from V1.1.0 is unchanged and carried forward.

---

## Highlights

- **A blocked scan says so.** If the operating system refuses to let OmniSuite reach your network,
  the scan fails with an explanation instead of reporting "0 found", and the devices already in
  your list are left alone.
- **Clear steps on macOS.** A notice under the Scan button explains macOS Local Network access, with
  **Retry Scan** and, on a Mac you are using directly, **Open System Settings**.
- **First launch on macOS, explained.** The download now includes `READ-ME-FIRST-macOS.txt`, and the
  install guide covers approving an app that Apple has not signed.
- **Better messages for bad targets.** A target range that cannot be understood is refused with an
  explanation instead of scanning nothing.

---

## macOS

- macOS asks for **Local Network** access the first time OmniSuite scans. Choose **Allow**. OmniSuite now
  says why it needs it.
- If access was refused, or the question is still open, scans of your local network fail straight
  away and the notice shows how to turn it on: **System Settings → Privacy & Security → Local
  Network → OmniSuite**. Then choose **Retry Scan**. OmniSuite never changes this setting itself.
- OmniSuite for macOS is **not signed or notarized by Apple**, so macOS blocks the first launch. Approve it
  under **System Settings → Privacy & Security → Open Anyway**, and only for a download from the
  official releases page. Full steps: [docs/INSTALL_MACOS.md](docs/INSTALL_MACOS.md). Do not turn off
  Gatekeeper.
- The first-run **Network Access Required** notice now also covers Local Network access. Macs that
  already dismissed the earlier wording will see it once more.

### What has and has not been confirmed

On an Apple Silicon Mac, macOS asks for Local Network access on first launch, and discovery works once
it is allowed. A blocked scan is reported as blocked, with the steps above. We believe a blocked
Local Network permission explains the earlier reports of a local scan finding nothing instantly, but
we have not confirmed that in every environment. The Intel Mac build is built and started in
automated testing and has not been run on a physical Intel Mac. If a scan still finds nothing after
Local Network access is on, please tell us.

---

## Scanning

- A refused scan returns a failure with the reason and technical details (what was tried, how long
  it took, which network adapters were active). Nothing is retried, and no limits are changed.
- On Windows and Linux a refused scan says a firewall or security policy may be blocking OmniSuite.
  Scanning itself works as before.

## Updates

- The update check now looks at the list of releases and offers the newest OmniSuite release by
  version number. If it cannot find one it says "unable to check", never "up to date".
- Applications already installed keep receiving updates from the same place as before.

---

## Upgrading

No data migration. Your devices, settings and credentials are kept. Download the archive for your
platform from the [Releases page](https://github.com/Hall-Research-Technologies/OmniSuite/releases)
and check it against `SHA256SUMS.txt`.

## Known limitations

- The macOS download is not signed with an Apple Developer ID and is not notarized. Each new download
  may be treated as a new application by macOS and may ask for approval and for Local Network access
  again. Signing and notarization are planned.
