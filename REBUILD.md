# Rebuilding OmniSuite with your own copy of pystray

OmniSuite is built from the source in this repository. It uses **pystray**, which is
licensed under the GNU Lesser General Public License version 3 (LGPL-3.0). Section 11A of
the OmniSuite licence (`LICENSE`) preserves your right to modify pystray and to replace the
pystray in OmniSuite with a modified, interface-compatible version and rebuild the
application with it, for your own permitted use. These are the steps. The unmodified pystray
0.19.5 source is in `third_party/pystray-0.19.5/` (beside the executable in every release
package, and in the repository); `third_party/PYSTRAY_PROVENANCE.md` records where it came from.

What you need: Windows 10 or 11 (64-bit), Python 3.12 (64-bit; the project is built with
3.12), Git or the source archive for the release you are rebuilding, and Internet access
for the package installs. The same steps work on macOS and Linux with that platform's
`--suffix` (`arm64`, `x86_64`, `linux`) in the last command.

1. Get the OmniSuite source for the release you have (this repository, at the release tag).
   Open a command prompt in its top folder. If you have a release package instead, copy
   its `third_party` folder over the same folder in the source.
2. Make an isolated Python environment:

       py -3.12 -m venv .venv
       .venv\Scripts\activate

3. Install the locked dependencies and the pinned build tools:

       pip install -r requirements-lock.txt
       pip install pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.9

4. Modify pystray if you wish. Edit the files under `third_party/pystray-0.19.5/lib/pystray/`
   (or substitute your own interface-compatible copy of that folder).
5. Put your pystray in place of the locked one:

       pip install --no-deps --force-reinstall third_party/pystray-0.19.5

6. Rebuild:

       python tools/build_release.py --suffix windows --version V1.2.0

   The application is written to `dist\windows\OmniSuite.exe` and packaged in `release\`.
   It now runs against your pystray. To confirm that your copy is the one in the executable,
   look for your change in its embedded Python archive: run
   `pyi-archive_viewer dist\windows\OmniSuite.exe` (installed with PyInstaller), open
   `PYZ-00.pyz`, and extract the `pystray` modules.

Notes: a rebuild with a modified pystray is a Modified Version of OmniSuite under the
OmniSuite licence. You may keep it inside your own organization (section 5); section 7 still
restricts distributing it, except as section 11A preserves for the LGPL component. A build
you make is not an official release and the in-application update check does not make it one.
