# pystray 0.19.5 - provenance of the included source

This folder-level record accompanies `third_party/pystray-0.19.5/`, which holds the
**unmodified upstream source of pystray 0.19.5**. Nothing in that folder was edited,
renamed, reformatted or re-encoded (the repository marks it `-text` so line endings are
preserved byte for byte). pystray is licensed LGPL-3.0; see `third_party_licenses/pystray/`
for the GPLv3 and LGPLv3 texts and `THIRD_PARTY_NOTICES.md` for how OmniSuite uses it.

## Where it came from

| Item | Value |
|---|---|
| Project | pystray, Copyright (C) 2016-2022 Moses Palmer |
| Version | 0.19.5 |
| Upstream repository | https://github.com/moses-palmer/pystray |
| Source used | the upstream release tag `v0.19.5`, downloaded as `https://github.com/moses-palmer/pystray/archive/refs/tags/v0.19.5.tar.gz` (48,142 bytes) |
| SHA-256 of that archive as downloaded | `2ab6bd15c14b400c9e8271a82c9de8adc0f1a0cacfc14080bdd4fb42bb88ec92` (GitHub regenerates tag archives, so the per-file hashes below are the stable record) |
| Source distribution on PyPI | **none**: PyPI publishes pystray 0.19.5 only as a wheel, so the tag archive is the upstream source |
| PyPI wheel | `pystray-0.19.5-py2.py3-none-any.whl`, 49,068 bytes, SHA-256 `a0c2229d02cf87207297c22d86ffc57c86c227517b038c0d3c59df79295ac617` (from PyPI metadata, and recomputed on the downloaded file: identical) |

## What was checked

1. **The tag source equals the PyPI wheel.** All 13 files of the `pystray` package in the
   wheel are byte-identical (SHA-256) to the 13 files under `lib/pystray/` in the tag source;
   no file is present on only one side.
2. **The pystray that OmniSuite packages is this source.** The released executable's
   embedded Python archive was read, and each of its 13 pystray modules was compared with the
   compiled form of the corresponding file in this source (same Python 3.12 compiler, file
   names ignored): all identical. This proves the packaged pystray was built from these files.
   It does not, by itself, prove anything about other parts of the executable.
3. **The locked installation matches.** The pystray installed from the locked requirement
   (`pystray==0.19.5`) has every file listed in its wheel `RECORD` (21 files, including the 13 package files) matching the recorded hash.

## Files (SHA-256)

| File | Bytes | SHA-256 |
|---|---|---|
| `.gitignore` | 55 | `4545483131a2273be1dfe191ea02808d64d005af05e4d85fdfd31c563ab3bd15` |
| `CHANGES.rst` | 8394 | `d24346e0c2a427e335cb75ddf93b2d8b037e1b9b41a5135d2d39369b6e1d4146` |
| `COPYING` | 35152 | `1e4d0c2eb111b851de12d7b2737195b950fae3620d2a23a474ce8d46e1d2e4b6` |
| `COPYING.LGPL` | 7656 | `e583ee8a64cd2ae744422bf76a1295838acae3698347f3a2af07462ddfaf0d2d` |
| `README.rst` | 294 | `7a37301fafc100f2e322d69aa476b077f2ffc65d908bd5a79464eff9e7a5c603` |
| `docs/conf.py` | 2024 | `6ae68cf3e8a03eb8c62183c6a4a536ecd976273bd071a287af4c55855d8deaea` |
| `docs/faq.rst` | 1892 | `25d4e93f32fa82badbd3142b398fcc569c426950af2e897dfb10bbc76d13666a` |
| `docs/index.rst` | 437 | `0d3c25df01e8108600a0f5aeaed1c14a2420d007791f37531601c3cb1a7a639d` |
| `docs/reference.rst` | 155 | `b51f85301079587e62130a74b660bccad0a3fcc85e25979c5f4069143370fdf3` |
| `docs/usage.rst` | 10057 | `d67b71365b2f9089a6789b2be8bfddceb8060f9a7dcdd2f194821de581bb8df2` |
| `lib/pystray/__init__.py` | 2108 | `c8e2b76aef6862992334eca1cc88b319b70db64dc9a391ea41c55a675c1fc20d` |
| `lib/pystray/_appindicator.py` | 3217 | `538439ba1f43967845690a6d50941407a9d19bee3915664a5e552fbcab755f36` |
| `lib/pystray/_base.py` | 21068 | `d964e3ae1f62238cdc96411491ad8b2844690523f1ac9ef68bfa2d3376f22b7e` |
| `lib/pystray/_darwin.py` | 8899 | `fdc51dfe2bbdce148dc4d111644f3b7c0a437f5ffcfb77fb5acadc36b86bea3a` |
| `lib/pystray/_dummy.py` | 866 | `61cf5fc0f443692d9e29a6368d4b320757fb4a6a0601be5f46bcb9d06ca20f0c` |
| `lib/pystray/_gtk.py` | 2360 | `a38d619ad380bfd0dfe35ed4dcf946e2de2fe09264617fddca8088f2c5426060` |
| `lib/pystray/_info.py` | 776 | `c7974beafe3d59822362f2b71f18d336d4a580826936e6382f2997198de32200` |
| `lib/pystray/_util/__init__.py` | 1589 | `441f5b187d0ce1a6fc1f25f26cf384835688c3763f4dfa2e1753d65737ff84c6` |
| `lib/pystray/_util/gtk.py` | 5519 | `eeaba1b86131fe48a10f1ac1af8fcf29fb57fd49ab65fb1f42ac1c8e45e359b2` |
| `lib/pystray/_util/notify_dbus.py` | 2850 | `7df75ffbc11ba85c372670501154f98c9a8a1c207d938009e3654553982f4835` |
| `lib/pystray/_util/win32.py` | 10087 | `4a3c2d0c81040632f2fb1777644ba8214d58eec1861343a8ce2ca205366d3dff` |
| `lib/pystray/_win32.py` | 13449 | `de4a968ac1cd519291fde5b13716446a8e5bd8805ab4d9e428ea2e3d66f37fb5` |
| `lib/pystray/_xorg.py` | 15236 | `01d5a1e99a9611986aa91add5dce60a30adddd01210d7bbdc4741764a1b74dda` |
| `setup.cfg` | 145 | `ef3164cbbc358b370a18e0945f6c488576ea049d6ae8389a4b1987979b7436d8` |
| `setup.py` | 2948 | `5ef8e915dcc4a0e14c9f9957630269287ba6443be8d2bdb78b8abefd7df77eb8` |
| `tests/__init__.py` | 3703 | `d8ef32a25c9361bcfc67a28f2c279261314216f85fb067713cad81b6b83971f2` |
| `tests/icon_tests.py` | 13448 | `69c587d3f909b28d68a6331ce802b9092e95b25c6e349103eecee33b480f2d93` |
| `tests/menu_descriptor_tests.py` | 6474 | `a8bfcffa0ef8ee031966371ac657d4b3705355389e8e780d6a1eac4400f963cb` |
| `tools/make-release.py` | 6386 | `febcdf0e31471576c959299853b72d02131c1aa4873c5a8742cf386642d1862c` |
