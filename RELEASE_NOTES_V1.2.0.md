# OmniSuite V1.2.0

OmniSuite can now be reached from a phone, tablet or laptop on your network —
securely, and only when you turn it on.

Until you do, nothing changes. OmniSuite answers only on the computer it is
running on, exactly as it always has.

---

## What's new

### Remote Access

Turn it on in **Settings › Remote Access**, choose which network adapter other
devices should reach OmniSuite on, and OmniSuite shows you the address people
type into their browser. It is off on every installation until an administrator
switches it on, and updating OmniSuite never switches it on.

### Encrypted connections

Remote Access uses **HTTPS**, so what travels across your network — including
the password somebody types at sign-in — is encrypted.

You have two ways to get the certificate HTTPS needs:

- **Create certificate** — OmniSuite makes one for the address you chose.
  Nothing else is needed. Browsers show a warning the first time, because
  nobody outside your building vouches for it; the connection is encrypted
  either way.
- **Use my own files** — if your organisation issues certificates, point
  OmniSuite at the certificate and its private key. Browsers that already trust
  your organisation show no warning at all.

Settings shows who the certificate is for, who issued it, and when it expires.

### Direct access links

A teacher who has to type a password before putting the document camera on the
projector will, before long, stop bothering. A **direct access link** is a web
address that opens one person's OmniSuite controls without a password, so it
can live on a tablet home screen or a classroom PC bookmark.

It is off for every account until an administrator turns it on, one account at
a time, and it is never available on an administrator account. The link carries
no permissions of its own: everything set for that account still applies, and
applies immediately. Take a feature away, remove a display, disable the account
or replace the link, and the address stops doing that thing at once. A direct
link cannot reach Settings, account management, Activity, firmware,
diagnostics or scanning, however the address is edited.

Anyone who has the link can use the controls assigned to that account, so both
the administrator's screen and the user's own say so in those words.

### Accounts and roles

Two kinds of account:

- An **Administrator** can do everything OmniSuite does — scanning,
  configuration, firmware, Settings, Multiview, USB and managing accounts.
- A **User** can only route, and only the equipment you assign to them. They
  cannot scan, open Settings, touch firmware, see other rooms' equipment, or
  create accounts.

The first administrator is created at the OmniSuite computer itself. There is
no default password to change and no account exists until you make one.

### Per-user permissions

For each User you choose:

- which **features** they get — A/V, USB, Multiview
- which **sources** they may show
- which **displays** they may control
- which **saved Multiview layouts** they may put on which display

Changes take effect immediately. Anyone signed in on an account you change is
signed out and signs in again.

### A simpler screen for everyone else

A User gets a screen built for a phone or tablet, with a tab for each feature
you granted — and no tabs at all if you granted one. Choose a source, choose a
display, done.

- **A/V** — put a source on a display.
- **USB** — give a computer its keyboard, mouse or camera.
- **Multiview** — put an approved multi-window layout on an approved display,
  or change one window of it.

### Friendly device names

Equipment arrives called `at-omni-111-04553`. Give it a name people recognise —
*Teacher PC*, *Document Camera*, *Front Display* — and that is what everyone
sees. The name lives in OmniSuite; nothing is written to the equipment and the
OmniStream hostname is unchanged.

Where two devices share a name, OmniSuite tells them apart using the
**Location** the device itself reports:

> Front Display — Room 101
> Front Display — Room 102

Location and Description are read from your OmniStream units and shown beside
the friendly name when you are assigning equipment. OmniSuite never changes
them. Setting Location on your units is worth the few minutes.

### Activity

**Manage users › Activity** shows what has happened: who signed in, who was
refused, what was routed, and what an administrator changed — newest first,
with a search box and filters. Passwords, sign-in tokens and device credentials
are never recorded. Only administrators can see it.

### Several people at once

Two people changing the same display at the same time is handled properly: the
operations are serialized, the last authorized one wins, and nobody is ever
locked out because somebody else closed their browser.

Extra browsers cost your equipment nothing. Whether one person or twenty-five
have OmniSuite open, the devices are read the same number of times.

---

## Security

- **Remote Access is off by default**, on new installations and on upgrades
  alike. Nothing on the network can reach OmniSuite until an administrator
  turns it on and chooses a network.
- **HTTPS by default.** If the certificate is missing or unreadable, Remote
  Access does not start — OmniSuite will not quietly fall back to an
  unencrypted connection. An unencrypted option exists for a closed test bench
  and has to be confirmed explicitly.
- **No default password and no built-in account.** The first administrator is
  created locally, by you.
- **Passwords are never stored.** Only a one-way hash is kept, and passwords
  never appear in the Activity log or in a support file.
- **Permission is checked on the server**, for every request, at the moment the
  operation happens — not in the browser, and not once at sign-in. An account
  whose permissions change mid-action is refused.
- **A User is given what they were assigned and nothing else.** Equipment they
  were not assigned does not appear in their lists and cannot be reached by
  calling OmniSuite directly.
- **The private key never leaves the computer.** It is written so only your
  Windows account can read it, is never shown on screen, is never returned
  through OmniSuite's own interface, and is never included in a support file.
- **Working at the OmniSuite computer is unchanged.** No sign-in, no account,
  nothing to set up.

---

## Upgrading from V1.1.0

Install over your existing version. Your device inventory, Multiview layouts,
scan settings and appearance are all preserved.

- OmniSuite keeps working exactly as before, locally.
- **Remote Access stays off.** Upgrading never turns it on.
- No account, no certificate and no device name is created for you.
- When you do want Remote Access, Settings walks you through it at the
  OmniSuite computer.

Once you have turned Remote Access on, it stays on across restarts — but two
things deliberately do not come back on their own: everyone has to sign in
again, and a network adapter that is no longer present is not silently replaced
with a different one.

---

## Known limitations

- **A certificate OmniSuite creates for itself makes browsers warn once.** The
  connection is encrypted; the warning is about nobody outside your building
  vouching for the address. Supply a certificate from your organisation to
  remove it.
- **The administrative pages need a desktop-sized window.** Device Info, the
  A/V Matrix, USB, Multiview and Settings are laid out for a screen at least
  1000 pixels wide. The screen a User sees is built for a phone.
- **Location and Description are read from your equipment, not edited in
  OmniSuite.** Set them on the OmniStream unit itself, then scan again.
- **Remote Access is for your own network.** Do not expose OmniSuite to the
  internet or to a guest network.

---

## Documentation

The full User Guide is in **Settings › User Guide**, and covers Remote Access
setup, certificates, accounts and permissions, what each message means, and
troubleshooting.

## Installing on macOS

OmniSuite Plus for macOS is not signed with an Apple Developer ID and is not notarized, so
macOS asks you to approve it the first time: open System Settings -> Privacy & Security and
choose **Open Anyway**. Only approve a download from the official releases page. macOS also
asks for Local Network access the first time you scan; choose Allow. Full steps are in
[docs/INSTALL_MACOS.md](docs/INSTALL_MACOS.md) and in `READ-ME-FIRST-macOS.txt` inside the
zip.
