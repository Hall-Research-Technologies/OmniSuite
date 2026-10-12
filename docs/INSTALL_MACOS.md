# Installing OmniSuite on macOS

OmniSuite for macOS is not signed with an Apple Developer ID and is not notarized.
macOS therefore blocks it the first time you open it. That is expected, and this
page shows how to approve it safely. The same instructions are inside the download
as `READ-ME-FIRST-macOS.txt`.

## Before you approve anything

Only approve OmniSuite if you downloaded it from the **official project releases
page**:

<https://github.com/Hall-Research-Technologies/OmniSuite/releases>

* The file name should start with `OmniSuite-`. A file that came from anywhere else
  should be deleted, not approved.
* Every release lists SHA-256 checksums in `SHA256SUMS.txt`. In Terminal, in the folder
  with both files, run `shasum -a 256 <your zip file>` and compare the result with the
  matching line.
* Approving an application tells macOS to trust it. Do that only for software whose
  source you have verified.

## First launch

1. Double-click the zip in Finder to unpack it. Use Finder (or `ditto -x -k`): other
   tools can flatten the links inside the app and leave it unable to start.
2. Move `OmniSuite.app` to your Applications folder if you like.
3. Open `OmniSuite.app`. macOS says it cannot verify the app. Click **Done**. Do not
   click Move to Trash.
4. Open **System Settings -> Privacy & Security** and scroll to the **Security**
   section. It says "OmniSuite" was blocked and shows an **Open Anyway** button. Click
   it, enter your password, then click **Open**.
   The button is only available for a while after the blocked attempt. If it is not
   there, open the app again and look again.

On macOS 14 (Sonoma) and earlier you can also Control-click the app and choose **Open**.
Later versions of macOS removed that shortcut for unsigned apps, so use Open Anyway.

You approve each downloaded copy once. A new download is a new application to macOS and
may ask again.

## Local Network access

The first time OmniSuite looks for devices, macOS asks whether it may find and connect to
devices on your local network. Choose **Allow**.

If you chose Don't Allow, or the question was still open, scans report that they could
not reach the network instead of finding nothing, and OmniSuite shows a notice with a
**Retry Scan** button. To fix it, open **System Settings -> Privacy & Security -> Local
Network**, switch on **OmniSuite**, and scan again. On a Mac you are using directly the
notice also offers **Open System Settings**. If scans still fail afterwards, quit
OmniSuite completely and reopen it.

OmniSuite never changes these settings itself, and never resets them.

## What not to do

* Do not turn off Gatekeeper.
* Do not run commands that remove the quarantine flag from the download.
* Do not approve a copy you cannot trace to the official releases page.

## Future improvement

Apple Developer ID signing and notarization would remove the Open Anyway step and keep
Local Network permission across updates, because macOS would recognise each release as
the same application. It needs an Apple Developer account and a signing identity in the
release pipeline, which is an infrastructure decision rather than a code change, so it
is not done yet. Until it is, each new download may ask again.

## For maintainers

* The macOS zip carries `READ-ME-FIRST-macOS.txt` beside `OmniSuite.app`. It is added to
  the archive by `tools/build_release.py`, and the smoke test fails the macOS build if it
  is missing or no longer mentions Open Anyway, Privacy & Security and Local Network.
* Release notes for a release that includes macOS builds should link to this page and
  say, in one sentence, that macOS will ask you to approve the app under System Settings
  -> Privacy & Security -> Open Anyway.
* Whether Open Anyway appears where these instructions say, and whether the Open System
  Settings button opens the right pane, can only be confirmed on a real Mac. See
  [MACOS_LOCAL_NETWORK.md](MACOS_LOCAL_NETWORK.md).
