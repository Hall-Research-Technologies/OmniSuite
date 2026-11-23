# OmniSuite V1.2.1

OmniSuite V1.2.1 lets people on your network route sources to displays, switch USB, and show Multiview layouts from a phone, tablet or laptop, with accounts and per-person permissions, and only when you turn it on. It also adds Display Groups, backup and restore, firmware availability, a fixed local port, a faster A/V Matrix and many reliability and security improvements.

Until you turn Remote Access on, nothing changes: OmniSuite answers only on the computer it runs on, exactly as before. V1.2.1 is the first release since V1.1.0, and it contains everything described below.

## Highlights

- **Remote Access** (off until you turn it on), with administrator and User accounts, HTTP or HTTPS, and optional passwordless direct links.
- **Remote User screens**: Standard routing, a User Matrix, a USB Matrix and Multiview, each limited to exactly the equipment an administrator assigned.
- **Remote User Multiview**: an administrator saves the layouts and decides who may show them; the person chooses which of their own sources feeds each window.
- **Display Groups**: one source to a named set of displays in one action.
- **Backup and restore** of your accounts, names, groups, Multiview layouts and settings in one encrypted file.
- **A faster, steadier application**: the A/V Matrix draws at once, units that stop answering no longer slow the pages, and extra browsers do not multiply device traffic.

## What's new

### Remote Access

- Turn it on in **Settings > Remote Access**, pick the network adapter other devices should use, and OmniSuite shows the address people open in their browser. It is off on every installation, and updating OmniSuite never turns it on.
- The first administrator is created at the OmniSuite computer. There is no default password and no built-in account.
- A physical Ethernet adapter is preferred over Wi-Fi and over virtual adapters (VPN, Hyper-V, VMware) when you have not chosen one. A choice you have made is never replaced.
- Remote Access comes back by itself after a restart or power cut, on the same adapter. If that adapter is gone, OmniSuite does not pick another: Settings tells you and you choose.
- If the computer's address changes while Remote Access is on, Settings says so in plain words and shows the address that is actually working. Nothing is rebound or reissued behind your back.
- Turning Remote Access on or off updates the top navigation on every open page without refreshing the browser.
- Opening the address in a browser shows the sign-in page.

### HTTP, HTTPS and certificates

- A first-time setup starts on **HTTP**, labelled "not encrypted", and OmniSuite asks you to confirm it when you turn Remote Access on. Use HTTP only on a network you trust.
- **HTTPS** is one choice away and encrypts everything, including the password typed at sign-in. OmniSuite never switches between the two for you. If you choose HTTPS and there is no usable certificate, Remote Access does not start; it never falls back to HTTP. While HTTPS is on, nothing listens on plain HTTP.
- Two ways to get a certificate: **Create one for this network** (OmniSuite makes one for the adapter you chose) or **Use my own files** (a certificate and private key from your organisation).
- A certificate OmniSuite creates is issued to this computer's Windows name by Hall Research for OmniSuite, is valid for the computer name and the chosen adapter's address, and lasts three calendar years. Because it is self-signed, browsers show a warning the first time; the connection is encrypted either way. Settings shows who it is for, when it expires and its fingerprint so you can compare it with the browser's.
- Settings says whether a certificate is **active**, a **custom certificate is active**, or a **certificate is required**. It never says "trusted": whether a browser accepts a certificate is up to the browser.
- A certificate made for one address is not valid on another. If the adapter or its address changes, OmniSuite tells you which two addresses disagree and asks you to create a new certificate rather than quietly reissuing one. A certificate you supplied is never replaced or deleted by OmniSuite.

### Accounts and permissions

- Two kinds of account. An **Administrator** has the full OmniSuite. A **User** can only route, and only the sources, displays, Display Groups, USB and Multiview layouts you assign.
- Permissions are checked by the server at the moment each action happens. Take something away and the next action is refused.
- Equipment a person was not assigned does not appear for them, and no device address is shown to them.
- For each person you choose **Standard routing**, the **User Matrix**, or both.
- **Activity** (administrators only) lists sign-ins, refusals, routes and administrative changes, newest first, with search and filters. Passwords and sign-in tokens are never recorded. It is kept for 30 days and up to 5 MB by default; both can be changed.
- Give equipment names people recognise (Teacher PC, Front Display). Names live in OmniSuite and are never written to the equipment. Where two devices share a name, OmniSuite tells them apart using the Location the device itself reports.

### Remote User routing

- **Standard routing**: pick a source, pick one or more displays or Display Groups, press **Route** (or drag the source onto a display). Choosing a source and then displays is one selection; there is no single or multiple mode.
- **Clear selection** forgets the selection without touching any hardware. **Unroute** stops the selected displays and groups and needs no source. **Audio follows video** carries the source's sound with its picture; turn it off to change only the picture. **Preview** shows or hides source pictures on that browser.
- The **User Matrix** shows sources across the top and displays and groups down the side, with Audio + Video, Video and Audio modes.
- Users see pictures of the sources they were given, taken from the encoder's own thumbnail. Pictures never block routing: a source without one still routes.
- A route to several displays no longer holds up the page: displays that are not answering are skipped, not waited for, and the result says how many took it.
- Routing a source to a display that is showing a Multiview simply does it: the display leaves the Multiview as part of the same verified step, with no extra question, and the saved Multiview is kept. If the route fails, the Multiview is put back.

### Remote User USB

- A User with USB sees the administrator's USB Matrix with only their own computers and USB devices in it. It follows the same rules as the administrator's matrix, including **no USB route across subnets**, and every change is checked by OmniSuite on the server and confirmed by reading both ends again before it is reported as done.

### Remote User Multiview

- An administrator gives a User specific saved Multiview layouts in the account editor (**Multiview layouts they may show**). The person sees only those layouts, each with a drawing, and can press **Show** or open **Choose sources**.
- In **Choose sources** the person picks which of their own sources feeds each window, from a list, by dragging, or by tapping a source and then a window. Nothing changes on the display until they press **Show**, and their choices never change the layout the administrator saved.
- Showing uses exactly the same checked, verified and rolled-back procedure as **Show on Display** for an administrator. The same source can fill several windows of the same size; a source cannot fill windows of different sizes at once, and the person is asked whether to **Replace** the other window or **Cancel**.
- Giving someone a layout does not give them the display or the sources inside it, and giving them a display does not give them its layouts. They cannot design layouts.
- If the display stops supporting Multiview, is switched to a Video Wall or Fast Switching, or is not answering, the layout is greyed out for that person with the reason and comes back by itself when it is available again; the assignment is kept. Taking a layout away takes effect on the person's next action. Renaming a layout or a display changing address does not end an assignment, and deleting a layout and saving another with the same name does.

### Display Groups

- A **Display Group** is a named set of displays that all receive one source. A User picks a source and a group and presses Route; each display is routed and checked in turn, and the result says how many took it (for example "3 of 4 displays routed"). A display that fails does not stop the others, and nothing already routed is undone.
- A group is its own permission, and a group is not a Multiview.

### Direct access links

- An optional passwordless address for one User account, for a wall panel or lectern tablet. It is off for every account until an administrator turns it on, and never available on an administrator account. Only one link is live for an account: regenerating it replaces the old one at once.
- It carries no permissions of its own: whatever you change on the account applies to the link at once. A link can never open the administrative pages.
- Treat the address like a password. If the computer's address changes, copy the link again from the account editor; an old bookmark with the old address will not open.

### Backup and restore

- **Settings > Backup and Restore** (at the OmniSuite computer) writes accounts, permissions, device names, Display Groups, Multiview layouts, device sign-in details and scan settings to one passphrase-encrypted file. The passphrase cannot be recovered.
- A restore shows what is in the file before it changes anything. The backup is the authority for what it contains: an account, group or name on this computer that the backup does not carry is removed. It either completes or puts everything back; if it cannot put everything back it says so and names the folder that holds the copy it took first. Anyone signed in as an account the restore removes or changes is signed out.
- The certificate and private key, the Activity log and scan results are never backed up or restored.

### Device passwords

- Set a primary password and an optional fallback password in Settings. A mixed installation, where some units use the fallback and others the primary, works everywhere: discovery, Preview, Blink, Device Info, configuration, routing, unroute and Multiview.
- The password that signs in to a unit is remembered for that unit and tried first next time; a password that stops working is detected and the other known password is tried once. The Password column shows the class the unit actually accepts. Device passwords are never sent to a remote browser.

### Firmware availability

- OmniSuite checks what Hall Research has published, shortly after it starts and then every seven days, and shows **Up to Date**, **Available** or **Unknown** (with the reason) for each model, in Settings and beside the version on Device Info. It only reports: nothing is downloaded or installed.

### A fixed local port, if you want one

- By default OmniSuite picks its own local port (8080, or the next free one). In **Settings > Local Application Port** you can ask for a fixed port so the address you bookmark stays the same. If something else is using that port at start-up, the launcher says so and lets you retry, start on a temporary port for that session, or choose another fixed port. It never quietly changes the address.

### A/V Matrix, Device Info and scanning

- The A/V Matrix draws as soon as the inventory is known instead of waiting for every decoder to answer, and changed crosspoints update in place. Matrix pages use the same Audio + Video, Video and Audio choice as the routing page.
- Units that stop answering no longer slow the application: they are looked at again on a growing interval instead of on every refresh, and routing, Settings and scanning stay responsive even with hundreds of silent units.
- Device Info sorts IP addresses, numbers and versions the way a person reads them; column headers work from the keyboard. Model, Version, Time Zone and NTP Server always show the right values. Each unit's uptime is shown.
- Standalone HW-OMNI-311 and HW-OMNI-324 USB extenders appear as their own rows (OmniSuite does not upgrade their firmware).
- The Upgrade Manager is now the **Device Manager**.
- A scan that the operating system blocks now says so, instead of reporting "0 found", and leaves your device list alone. A target range that cannot be understood is refused with an explanation.
- **Clear Units** keeps units that Users or Display Groups depend on while Remote Access is on, and tells you how many and for whom. To remove those too, turn Remote Access off first.

### Multiview

- Saving a Multiview that is on a display saves only; nothing on the display changes until you press **Show on Display**, which applies the whole saved Multiview, audio included.
- Dragging a source onto a window of the live Multiview no longer blanks the page, and a live change takes every window you did not touch from what the display is actually showing.
- Saving, showing, installing layouts or pressing Reload decoder stays on the layout you were working on.
- An illegal Multiview name is refused with the rules and what to change, and what you typed stays in the box.
- Show reports whether every window locked first time or after one automatic recovery, and says plainly if a window is still dark after that.
- A window holding a source of a size that conflicts with another window is refused or asked about, rather than leaving a window silently at the wrong size.

### USB Matrix

- The USB Matrix follows one rule everywhere: both ends of a route must be on the same USB subnet, worked out from each endpoint's own address and mask. A route that cannot be proven valid is refused with the reason, and a working route is never torn down to find out.
- Clicking a cell states the relationship you want and OmniSuite reconciles the hardware to it; every change is confirmed by reading both ends again.

### Settings and appearance

- The dialog is called **Settings**, with the same two layouts, **Classic** and **Compact**, light and dark, and ten colour presets. Controls look the same on every page.
- Settings > Remote Access, Backup and Restore, Firmware and Local Application Port each state their status in plain words.

### Update checking

- OmniSuite may ask GitHub whether a newer release exists, at start-up and when you press **Check Now**. It offers the newest release for your computer, compares versions as numbers, and never says "up to date" when it could not check. It only reports; you download and install updates yourself.
- On a computer that is not Windows x86-64 or macOS Apple Silicon, the update check tells you a newer release exists and that there is no supported update package for that computer.

## Reliability and security

- Every request is checked on the server, for every person, at the moment it runs. A malformed request is refused with a plain message instead of being treated as an instruction (for example, a Clear Units request that says nothing no longer clears anything).
- Two people changing the same display at the same moment are handled in order; the last authorised change wins and nobody is locked out by someone else closing a browser. Extra browsers do not multiply the number of times your devices are read.
- The restore path, the device-list cache and the saved settings are protected against partial writes and overlapping updates, and a damaged settings file is set aside instead of stopping OmniSuite.
- Device passwords are never sent to a remote browser. The local address answers only to the computer itself. A second copy of OmniSuite on the same data folder stops with a message.
- Text a device reports is displayed as text, not interpreted.

## Upgrading from V1.1.0

Install over your existing copy. Your inventory, Multiview layouts, scan settings and appearance are kept. Remote Access stays off, and no account, certificate or device name is created for you.

## Supported platforms

| Platform | Download | Status |
|---|---|---|
| Windows x86-64 | `OmniSuite-V1.2.1-Windows-x86_64.zip` | Validated with physical OmniStream equipment, and tested with simulated devices. |
| macOS Apple Silicon | `OmniSuite-V1.2.1-macOS-arm64.zip` | Built and checked automatically before release. Not yet validated with physical equipment. |

OmniSuite is built for these two platforms only. There is no macOS Intel or Linux download. The Windows download is not code signed: SmartScreen may ask you to choose More info > Run anyway the first time. On macOS the app carries only an ad hoc signature and is not Developer ID signed or notarised, so macOS blocks the first launch; see `docs/INSTALL_MACOS.md` for the Open Anyway steps, and only approve a download from the official releases page. Each release carries `SHA256SUMS.txt`.

## What OmniSuite contacts on the Internet

OmniSuite does not need an Internet connection. It makes two read-only checks of public websites: it may ask GitHub whether a newer OmniSuite release exists (at start-up, and again when you press Check Now), and it asks Hall Research's public product pages whether newer firmware exists (shortly after start-up and every seven days). Neither sends accounts, passwords, device addresses or settings, neither installs anything, and there is no usage reporting. The User Guide describes both.

## Known limitations

- A certificate OmniSuite creates for itself makes browsers warn the first time you visit. The connection is encrypted; the warning is about nobody outside your building vouching for it. Supply your own certificate to avoid it.
- A certificate created by an earlier build keeps its original name and validity; choose "Create one for this network" to get the current identity (browsers will warn again because the fingerprint changes).
- Memory use grows slowly over a very long run, and routing can feel slightly slower after many days. Closing and reopening OmniSuite returns both to normal.
- The administrative pages (Device Info, A/V Matrix, USB, Multiview, Settings) are laid out for a window at least 1000 pixels wide. The User screen is built for a phone or tablet.
- Location and Description are read from your equipment; set them on the OmniStream unit and scan again.
- On the administrator's Multiview page, layouts are listed under the name the device stores (for example multiviewLecture for a layout you named Lecture); the account editor and Users see the name you typed.
- Remote Access is for your own network. Do not expose OmniSuite to the Internet or a guest network.

## Licences

OmniSuite is source-available under the Hall Research Technologies Source-Available Software License (`LICENSE`). Third-party components keep their own licences; `THIRD_PARTY_NOTICES.md` lists them and `third_party_licenses/` holds their texts, both beside the application in the Windows download. One component, pystray (the tray icon), is licensed under the LGPL-3.0: its complete unmodified source and `REBUILD.md` (how to rebuild OmniSuite with your own copy of it) are in the download too, and section 11A of the licence preserves that right.

## Documentation

The full User Guide is in **Settings > User Guide**.
