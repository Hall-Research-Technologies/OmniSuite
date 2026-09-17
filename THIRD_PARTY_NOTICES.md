# Third-party notices

OmniSuite is published by Hall Research Technologies LLC. The components
listed here are **not** owned by Hall Research Technologies LLC; they are
third-party software redistributed inside the packaged OmniSuite builds,
each under its own licence and copyright.

The packaged builds are produced with PyInstaller, which embeds the Python
runtime and the libraries below into the distributed executable. The full
licence text of each component ships inside its own package metadata in
that bundle.

This file is generated from installed package metadata rather than written
by hand. A component whose metadata does not declare a licence is listed as
not declaring one rather than being assigned a guess.

| Component | Version | Declared licence |
|---|---|---|
| blinker | 1.9.0 | MIT License |
| certifi | 2026.5.20 | Mozilla Public License 2.0 (MPL 2.0) |
| charset_normalizer | 3.4.7 | MIT |
| click | 8.4.1 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| et-xmlfile | 2.0.0 | MIT License |
| flask | 3.1.3 | BSD-3-Clause |
| idna | 3.18 | BSD-3-Clause |
| itsdangerous | 2.2.0 | BSD License |
| jinja2 | 3.1.6 | BSD License |
| markdown-it-py | 4.2.0 | MIT License |
| markupsafe | 3.0.3 | BSD-3-Clause |
| openpyxl | 3.1.5 | MIT License |
| Pillow | 12.2.0 | MIT-CMU |
| psutil | 7.2.2 | BSD-3-Clause |
| pygments | 2.20.0 | BSD-2-Clause |
| pyinstaller | 6.20.0 | GNU General Public License v2 (GPLv2) |
| pystray | 0.19.5 | GNU Lesser General Public License v3 (LGPLv3) |
| requests | 2.34.2 | Apache Software License |
| rich | 15.0.0 | MIT License |
| six | 1.17.0 | MIT License |
| urllib3 | 2.7.0 | MIT |
| websocket-client | 1.9.0 | Apache Software License |
| werkzeug | 3.1.8 | BSD-3-Clause |

## Notes on specific components

- **pystray** is LGPL-3.0, and the packaged executable is therefore a
  combined work under that licence. LGPL-3.0 requires that this notice and
  pystray's licence text accompany the distribution, and that a recipient be
  able to modify pystray and rebuild the application against their modified
  copy. OmniSuite does not modify pystray: the packaged builds are produced
  from the unmodified published release, OmniSuite's own source is published,
  and the build is reproducible with `tools/build_release.py`, so a recipient
  who installs a modified pystray and rebuilds obtains the application running
  against their version.
- **PyInstaller** is GPLv2, but its licence carries an explicit bootloader
  exception permitting the bootloader to be linked into, and distributed
  with, applications that are not themselves under GPLv2. OmniSuite relies on
  that exception. PyInstaller is a build tool; no part of it other than the
  bootloader is incorporated into the distributed executable.

Nothing in this file grants any right to OmniSuite itself. OmniSuite's own
terms are in [LICENSE](LICENSE).

The Hall Research Technologies Source-Available Software License applies only
to Hall-owned OmniSuite code and content. It does not, and cannot, alter the
licence of any component listed above. Where the Hall licence and a
component's own licence differ as to that component, the component's licence
governs it.

