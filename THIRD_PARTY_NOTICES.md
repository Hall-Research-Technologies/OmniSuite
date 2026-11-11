# Third-party notices

OmniSuite is published by Hall Research Technologies LLC. The components
listed here are **not** owned by Hall Research Technologies LLC; they are
third-party software redistributed inside the packaged OmniSuite builds,
each under its own licence and copyright.

The packaged builds are produced with PyInstaller, which embeds the Python
runtime and the libraries below into the application. A library's own package
metadata does **not** travel with such a build, so this release carries the
licence texts separately:

* inside the application, in a `third_party_licenses` folder, beside this file and `LICENSE`;
* and **beside the application in the downloaded archive**: `LICENSE`,
  `THIRD_PARTY_NOTICES.md` and `third_party_licenses/` sit next to `OmniSuite.exe`,
  `OmniSuite.app` or the `OmniSuite` folder, where a recipient can read them
  without unpacking the executable.

The texts are copied byte for byte from the installed packages of the locked
build environment by `tools/collect_licenses.py`, and `third_party_licenses/<component>/`
holds every licence file the package itself carries. A component whose package
carries no licence file is named below instead of being given a text OmniSuite
does not have. A test (`tests/test_third_party_notices.py`) fails if a shipped
component is missing from this file or from `third_party_licenses/`.

## What is shipped

Established from the contents of a packaged Windows build (the Python modules in
its archive plus the extension modules and libraries beside them), and from
`requirements-lock.txt`. Versions are those of the locked build environment;
`cryptography` is 48.0.1 on Intel macOS only (see below).

| Component | Version | Declared licence | Licence text |
|---|---|---|---|
| blinker | 1.9.0 | MIT License | `third_party_licenses/blinker/` |
| certifi | 2026.10.23 | MPL-2.0 | `third_party_licenses/certifi/` |
| cffi | 2.1.1 | MIT-0 | `third_party_licenses/cffi/` |
| charset-normalizer | 3.5.2 | MIT | `third_party_licenses/charset-normalizer/` |
| click | 8.5.0 | BSD-3-Clause | `third_party_licenses/click/` |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | `third_party_licenses/cryptography/` |
| et-xmlfile | 2.0.0 | MIT | `third_party_licenses/et-xmlfile/` |
| flask | 3.1.3 | BSD-3-Clause | `third_party_licenses/flask/` |
| idna | 3.20 | BSD-3-Clause | `third_party_licenses/idna/` |
| itsdangerous | 2.2.0 | BSD License | `third_party_licenses/itsdangerous/` |
| jinja2 | 3.1.6 | BSD License | `third_party_licenses/jinja2/` |
| markdown-it-py | 4.2.0 | MIT License | `third_party_licenses/markdown-it-py/` |
| markupsafe | 3.0.4 | BSD-3-Clause | `third_party_licenses/markupsafe/` |
| mdurl | 0.1.2 | MIT License | `third_party_licenses/mdurl/` |
| openpyxl | 3.1.5 | MIT | `third_party_licenses/openpyxl/` |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | `third_party_licenses/packaging/` |
| pillow | 13.0.0 | MIT-CMU | `third_party_licenses/pillow/` |
| psutil | 7.2.2 | BSD-3-Clause | `third_party_licenses/psutil/` |
| pygments | 2.21.0 | BSD-2-Clause | `third_party_licenses/pygments/` |
| pyinstaller | 6.22.3 | GNU General Public License v2 (GPLv2) | `third_party_licenses/pyinstaller/` |
| pyinstaller-hooks-contrib | 2026.9 | Apache Software License; GNU General Public License v2 (GPLv2) | `third_party_licenses/pyinstaller-hooks-contrib/` |
| pystray | 0.19.5 | LGPLv3 | `third_party_licenses/pystray/` |
| requests | 2.34.2 | Apache-2.0 | `third_party_licenses/requests/` |
| rich | 15.0.0 | MIT | `third_party_licenses/rich/` |
| setuptools | 84.2.1 | MIT | `third_party_licenses/setuptools/` |
| six | 1.17.0 | MIT | `third_party_licenses/six/` |
| urllib3 | 2.8.0 | MIT | `third_party_licenses/urllib3/` |
| websocket-client | 1.9.2 | Apache-2.0 | `third_party_licenses/websocket-client/` |
| werkzeug | 3.1.9 | BSD-3-Clause | `third_party_licenses/werkzeug/` |

Notes on this table:

* **cffi** is shipped as its `_cffi_backend` extension. `pycparser`, which cffi
  needs only to build, is in the lock but is not part of the package.
* **packaging** and **setuptools** are not in the lock. They arrive with
  PyInstaller and are embedded by it, so their versions are those that were
  installed beside it; **setuptools** also carries vendored libraries, whose
  licence texts are under `third_party_licenses/setuptools/vendored/`.
* **pyinstaller** and **pyinstaller-hooks-contrib** are build tools. What reaches
  the package is PyInstaller's bootloader and runtime modules, and one runtime
  hook from the hooks collection.
* **cryptography** on Intel macOS is 48.0.1, because later releases publish no
  prebuilt wheel for that platform. Its three licence files are identical to those
  of 50.0.2, which are the ones bundled.
* **pillow**'s wheel contains compiled image libraries; its `LICENSE` file is the
  statement that covers them.

### Components that depend on the build platform

The lock was resolved on Windows. `pystray` requires further packages on two
platforms, and pip resolves them at build time, so they are **not pinned**:

| Component | Required by pystray on | Declared licence | Licence text |
|---|---|---|---|
| python-xlib | Linux | LGPL-2.1-or-later (as declared: LGPLv2+) | `third_party_licenses/python-xlib/` |
| pyobjc-framework-Cocoa | macOS | MIT | `third_party_licenses/pyobjc-framework-cocoa/` |
| pyobjc-framework-Quartz | macOS | MIT | `third_party_licenses/pyobjc-framework-quartz/` |
| pyobjc-core | macOS | MIT (as declared) | its package carries no licence file; the PyObjC text is the one in `pyobjc-framework-cocoa` |

OmniSuite has not inspected a Linux or macOS package's contents for these, so
they are listed as possibly present, not as confirmed.

## The Python runtime and what it carries

PyInstaller embeds the Python interpreter that built the application (CPython
3.12), under the Python Software Foundation licence, whose text is in
`third_party_licenses/python/LICENSE.txt`. That interpreter brings its own copies of other
software, each under its own licence and not reproduced separately here:

* **Tcl/Tk**, for the launcher window;
* **OpenSSL 3**, used by Python's `ssl` module, and a copy of which is linked
  into `cryptography`'s wheel (OpenSSL 3 is published under the Apache License
  2.0, a text of which is `third_party_licenses/cryptography/licenses/LICENSE.APACHE`);
* **libffi**, and on Windows the Microsoft Visual C++ runtime, which is
  redistributed under Microsoft's redistribution terms.

## Notes on specific components

- **pystray 0.19.5** (Copyright (C) 2016-2022 Moses Palmer) is licensed under
  the GNU Lesser General Public License version 3 (LGPL-3.0), which is itself
  the GNU General Public License version 3 plus the additional permissions in
  the LGPL text. Both texts are included: `third_party_licenses/pystray/COPYING`
  (GPLv3) and `third_party_licenses/pystray/COPYING.LGPL` (LGPLv3). OmniSuite
  uses pystray for the notification-area (tray) icon, and the packaged
  executable is a combined work with it. pystray is pure Python, included in
  the executable as compiled modules; it is **not modified** by OmniSuite.
  - **Corresponding source.** The complete, unmodified source of pystray 0.19.5
    is included in every release package, beside the executable, in
    `third_party/pystray-0.19.5/`, and in the OmniSuite source repository at the
    same path. Where it came from, its hashes, and what was checked against the
    packaged copy are in `third_party/PYSTRAY_PROVENANCE.md`.
  - **Your rights.** Section 11A of the OmniSuite licence (`LICENSE`) preserves
    your right to modify pystray, to debug such modifications, and to replace
    the pystray in OmniSuite with a modified, interface-compatible version and
    rebuild or recombine OmniSuite with it, for your own permitted use.
  - **How.** `REBUILD.md` (beside the executable, and in the repository) gives
    the exact steps: install the locked dependencies, install your modified copy
    of `third_party/pystray-0.19.5/` over the locked pystray, and run
    `tools/build_release.py`. The result is an application running against
    your pystray. That procedure was carried out and verified before this
    release (a marked test copy of pystray was incorporated into a rebuilt
    executable).
- **PyInstaller** is GPLv2, but its licence carries an explicit bootloader
  exception permitting the bootloader to be linked into, and distributed
  with, applications that are not themselves under GPLv2. OmniSuite relies on
  that exception. The exception's text is in `third_party_licenses/pyinstaller/licenses/COPYING.txt`.
- **certifi** is MPL-2.0 and ships Mozilla's root-certificate bundle.

Nothing in this file grants any right to OmniSuite itself. OmniSuite's own
terms are in [LICENSE](LICENSE).

The Hall Research Technologies Source-Available Software License applies only
to Hall-owned OmniSuite code and content. It does not, and cannot, alter the
licence of any component listed above. Where the Hall licence and a
component's own licence differ as to that component, the component's licence
governs it.
