# Remote user access and role-based routing — architecture

Branch: `feature/remote-user-routing`, from the published V1.1.0 baseline `f38f164`.

This is the Phase A architecture pass. It is a proposal against measured facts
about the current application, not a description of shipped behaviour. Nothing
in it is implemented yet except where a later section says so explicitly.

---

## A. What V1.1.0 assumptions are safe only because OmniSuite is local

**The application has no authentication of any kind.** `secret_key`, Flask
sessions and any login decorator are all absent — measured, not assumed:
a grep for `secret_key|flask_login|session[` over the server returns zero hits.

**All 112 routes are anonymous.** Enumerated from Flask's own `url_map` rather
than by grepping for decorators, so nothing registered another way can hide:

| Class | Count |
|---|---|
| Device writes (reconfigure, reboot, upgrade, pair, route) | 30 |
| Mutating, other | 30 |
| Local state writes | 15 |
| Reads (3 of which perform device I/O) | 35 |
| Static assets | 2 |
| **Total** | **112** |

**The single control that makes this safe today is the loopback bind.**
`app_launcher.py:111` defaults `host="127.0.0.1"`. That one default is the
entire security boundary. Enabling remote access removes it from all 112 routes
at once — so authentication is not an addition to the current posture, it is
the *replacement* for the only control that exists.

Four findings were confirmed against a running instance, by direct HTTP
request with no credential:

1. **`GET /api/config?include_password=1` returns both device passwords in
   plaintext.** Confirmed live: an 8-character primary and a 6-character
   fallback were returned to an anonymous caller, along with the host's
   firmware path.
2. **`GET /api/list_dir?path=<anything>` enumerates the host filesystem.**
   Confirmed live: 33 directories from `C:\`, the drive list `['C:\', 'D:\']`,
   and then every user profile under `C:\Users`. It takes an arbitrary absolute
   path and is not confined to the firmware directory.
3. **`GET /api/state` discloses the whole deployment.** Confirmed live: 12
   encoders and 19 decoders with addresses, models and hostnames.
4. **`POST /api/open_firmware_folder` opens a file explorer on the host.** A
   physical-presence action reachable by an HTTP request.

Others in the same category, by inspection: `/api/ts_export`,
`/api/download_inventory` and `/api/unit_config/export*` (bulk exfiltration),
`/api/sync_passwords` (writes credentials to devices), `/api/upgrade`,
`/api/reboot`, `/api/slate_upload` (destructive device operations), and
`/api/diagnostics/*` (bench hardware-validation endpoints that AGENTS.md
already requires be kept behind a confirmation token).

> **Consequence for the design.** A model that authenticates the new routes and
> leaves the existing ones as they are would be worthless: an attacker would
> ignore the new UI and call `/api/list_dir` directly. The authorization layer
> must be a **default-deny gate in front of every route**, with access granted
> by explicit classification — not a decorator applied to the routes someone
> remembered.

## B. Persistence and state that must become safe for multiple remote clients

Runtime state is file-based, in `%LOCALAPPDATA%/OmniSuite` (or `OMNI_DATA_DIR`):
`units_cache.json`, `scan_results.json`, `usb_extenders.json`, Multiview
metadata, groups, UI preferences and config.

Three properties were written for a single local operator:

- **`omni_matrix_logic._decoders` / `_encoders` are process-global** and
  `/api/state` overlays them onto the cache. PROJECT.md already records that
  tests must isolate them. With concurrent remote users they become shared
  mutable state across requests.
- **Read-modify-write over a shared JSON file.** AGENTS.md already requires
  atomic replacement under one lock, and records the failure mode: "concurrent
  writers each apply their change to their own copy and the last save silently
  discards the rest." That risk was low with one operator; it is ordinary with
  several.
- **UI preference state is per-browser.** That is correct for preferences and
  must never be extended to permissions (§15).

## C. Endpoints requiring authentication and authorization

Default deny. Every route is Admin-only unless explicitly classified otherwise.

The inventory says a restricted routing User plausibly needs **12** of the 112:

| Route | Method | Purpose |
|---|---|---|
| `/api/state` | GET | filtered device/route state |
| `/api/route` | POST | the A/V route itself |
| `/api/usb_state` | GET | filtered USB endpoints |
| `/api/usb_route/resolve` | POST | resolve a USB pair before acting |
| `/api/usb_route/pair` | POST | USB route |
| `/api/usb_route/unpair` | POST | USB unroute |
| `/api/multiview/state` | GET | what a decoder is showing |
| `/api/multiview/decoders` | GET | permitted decoders |
| `/api/multiview/sources` | GET | permitted sources |
| `/api/multiview/preview` | GET | source thumbnail |
| `/api/multiview/show` | POST | show an approved preset |
| `/api/multiview/switch` | POST | change a source in a live preset |

The other **100** are Admin-only. Several are additionally **local-only** and
should not be reachable remotely even for an Admin, because they act on the
host rather than on AV devices: `/api/list_dir`, `/api/files`,
`/api/open_firmware_folder`, and the `/api/diagnostics/*` bench endpoints.

Note that four of the twelve are reads that must be **filtered**, not merely
permitted: `/api/state` today returns the entire deployment, and a teacher must
receive only their room. Filtering is part of authorization, not presentation.

## D. User / role / permission data model

Server-authoritative, in its own file, separate from the device cache, written
with the atomic-replace-under-one-lock rule AGENTS.md already requires.

```
users.json
  version: 1
  users:
    - id:            stable opaque id (never the username)
      username:      login name, case-insensitive unique
      password_hash: werkzeug scrypt (see J)
      role:          "admin" | "user"
      enabled:       bool
      created_at, password_changed_at
      permissions:
        features:    ["av", "usb", "multiview"]        # capability ids
        sources:     [device identity, ...]
        destinations:[device identity, ...]
        multiview:   {presets: [...], mode: "operate"} # see J
```

Two deliberate choices:

**Capabilities are string ids, not three booleans.** `"av"`, `"usb"`,
`"multiview"` today; a future family adding `"videowall"` or `"rtmp"` is a new
id and a new gate registration, not a schema change. This is the architectural
foresight §7 asks for and costs nothing now. No LumaSuite code is in this
branch.

**Permissions are keyed by device identity, not IP.** AGENTS.md is explicit
that "device identity must not depend solely on IP address when a stable
hardware identifier such as a MAC address is available," and §24 asks the same.
MAC is the key where one is known; the record keeps the last-known hostname and
IP as *display* fields only. A permitted device that is offline is shown as
offline and remains permitted — a permission must never silently transfer to
whatever now answers at that address.

## E. Session model

Server-side sessions with an opaque random token in a cookie. Not a JWT: an
Admin disabling a user must take effect immediately (§18), which a
self-contained token cannot do without a revocation list — which is a session
store with extra steps.

- `secrets.token_urlsafe(32)` session id; the store holds user id, created-at,
  last-seen, and the client address it was issued to.
- Cookie `HttpOnly`, `SameSite=Lax`, `Secure` when served over TLS, and scoped
  to path `/`.
- **Idle expiry** and an **absolute cap**; both configurable, both enforced
  server-side on every request.
- **Revocation on**: logout, account disabled, role or permission change,
  password change (all of that user's other sessions), and Admin delete.
- **Server restart drops all sessions.** The store is in memory. Everyone
  re-authenticates after a restart, which is the safe default and avoids
  persisting session material to disk.
- **Multiple devices per account are allowed** — a teacher with a phone and a
  laptop is the ordinary case — so the store is a set of sessions per user.
- **Session fixation**: the id is regenerated on login.
- **CSRF**: `SameSite=Lax` plus a per-session CSRF token required on every
  state-changing request, checked server-side. `Lax` alone does not cover
  top-level POST navigation.
- **Login throttling**: per-username and per-source-address backoff, with the
  failures recorded in the audit log.

## F. Local-versus-remote trust boundary

This is the part most likely to be got wrong, so it is stated narrowly.

**The only trustworthy signal is the kernel-reported peer address of the TCP
connection.** In Flask that is `request.remote_addr` *when no proxy is in
front*. `X-Forwarded-For`, `X-Real-IP`, `Host`, `Origin` and every other header
are client-supplied and are never consulted for a trust decision (§5, §20).

Three principals:

| Principal | How established | Gets |
|---|---|---|
| **Local host operator** | peer address is loopback **and** the listening socket is the loopback socket | today's full local experience, no login |
| **Authenticated remote Admin** | session cookie, role `admin` | everything except local-only routes (C) |
| **Authenticated remote User** | session cookie, role `user` | only granted capabilities, filtered to granted devices |

Two constraints on the local bypass:

1. It is **not** "remote_addr == 127.0.0.1" alone. If the operator binds to a
   routable address, a remote client cannot make its peer address loopback, but
   the bypass must still be tied to the socket the request arrived on, so that
   a future proxy or port-forward cannot present as local. The check is
   *arrival on the loopback listener*, which is server-side and not spoofable.
2. **If a reverse proxy is deployed (§21), every request's peer address becomes
   the proxy's.** The local bypass must therefore be disabled automatically
   whenever a proxy is configured, or every remote user is promoted to local
   Admin. This is recorded as a hard requirement, not a caveat.

## G. Adapter and binding architecture

**Remote Access defaults to Disabled** (§28). An existing installation that
updates must not become reachable.

`GET /api/adapters` already enumerates active private IPv4 adapters with
address and CIDR, so the Settings UI offers a list rather than a free-text bind
address (§4). `0.0.0.0` is not offered as a default and, if offered at all, is
a deliberate explicit selection with its consequence stated.

**Binding cannot change on a live Flask process.** `app.run()` owns the
listening socket for the life of the call, and the packaged build runs it on a
thread inside the launcher. Pretending otherwise would be a lie in the UI
(§4). Two honest options, and the proposal takes the second:

- *Restart-to-apply*: save the setting, tell the operator plainly that it takes
  effect on restart, and offer a clean restart from the launcher.
- *Dual listener*: keep the loopback server and start a **second** listening
  socket on the selected address when remote access is enabled, and stop it
  when disabled. This preserves "loopback always works" (§4) literally, makes
  the local-versus-remote distinction in F a property of *which socket accepted
  the connection* rather than an inference from an address, and makes
  enable/disable immediate without a restart.

The dual-listener option is preferred because it makes F sound. It requires
serving the same Flask app from two WSGI listeners, which is a change to the
launcher's server startup and must be proven against the packaged executable,
not only in a development run.

## H. Restricted routing UI architecture

A separate, small, touch-first surface — not the Admin UI with buttons hidden
(§10). It answers two questions: *what source, and where*.

- Its own route and its own page, served only to an authenticated principal.
- Reuses the family visual language (tokens, controls, toast, confirm) so it is
  recognisably OmniSuite, but is laid out for a phone and a tablet. The Admin
  UI's `min-width: 1000px` does not apply to it.
- It renders only what the server sent, and the server sends only what the
  account is permitted. There is no client-side filtering of a full inventory,
  because that would mean shipping the full inventory (§15).
- No engineering detail: no multicast addresses, no bitrates, no scaler state.

## I. Server-side authorization enforcement

A `before_request` gate, so a route added later is **denied by default** rather
than silently public. This is the core of §25.

```
request arrives
  -> is the path public? (login, static, health)        -> allow
  -> did it arrive on the loopback listener?            -> local operator, allow
  -> valid session?                                     -> else 401
  -> CSRF valid for state-changing methods?             -> else 403
  -> route classified for this role?                    -> else 403
  -> role=user: resolve the devices in the request body
     against the account's permitted identities         -> else 403
  -> handler
```

The last step is the one that cannot be skipped: feature permission alone is
not enough (§8). `POST /api/route` names a source and a destination, and
**both** must be checked against the account before the routing service is
called. The check is on the resolved device identity, not on a display name.

A route with no classification is denied, and a test asserts that the set of
classified routes equals the set of registered routes — so adding a route
without classifying it fails the suite rather than shipping an open endpoint.

## J. Multiview User boundary

§13 asks for this to be decided deliberately rather than by enabling
everything. Multiview's own architecture already draws the line, in the table
AGENTS.md records: layout, preset, active Multiview, display output.

**A User operates; an Admin prepares.** Granting `multiview` gives:

| Operation | User | Why |
|---|---|---|
| See what a permitted decoder is showing | yes | read of permitted state |
| Show an approved saved preset on a permitted decoder | yes | this is the operating action |
| Change a source in a live preset to a **permitted** source | yes | `/api/multiview/switch`, already a verified transaction |
| Create / edit / delete / copy a preset | **no** | preparing the environment |
| Install layouts | **no** | preparing |
| Prepare encoders, change scalers or bitrates | **no** | shared state affecting other decoders |
| Group administration | **no** | Admin defines groups; a User may show one if granted |

This maps onto the existing split cleanly, which is the argument for it:
AGENTS.md already establishes that saving configures nothing and showing is the
one moment the picture changes. "Operate" is exactly the Show/Switch half.

## K. HTTPS approach

Exposing AV control to a routable network over plain HTTP sends the session
cookie in clear (§21).

Recommended for an on-premises AV utility, in order:

1. **Operator-provided certificate and key**, configured in Settings. Correct
   for a site with its own CA or a real certificate; no PKI is invented.
2. **Locally generated self-signed certificate**, offered as a convenience.
   Honest about the browser warning, and about the fact that a self-signed
   certificate stops passive interception but not an active attacker the first
   time a client trusts it.
3. **Reverse proxy** — documented as supported, with the F consequence: the
   local bypass is disabled whenever a proxy is configured.
4. **HTTP-only**, retained but **explicitly warned** in the UI and disabled by
   default for remote access.

Self-signed certificate generation needs `cryptography`, which is not a current
dependency. If it is not added, option 2 is dropped rather than approximated.
**If HTTPS is not completed in the first implementation pass, remote access
over HTTP must be labelled as not safe for deployment on an untrusted network**
— architected for, and clearly identified, as §21 requires.

## L. Migration and upgrade behaviour

- Remote Access defaults off; no existing install changes behaviour (§28).
- `users.json` absent means no accounts, which is the correct state for a
  local-only installation. It is created when an Admin first enables remote
  access.
- Existing device inventory, Multiview metadata and preferences are untouched;
  nothing in this design rewrites them.
- No account is required to keep using OmniSuite locally (§28).

## M. Rollback if Remote Access is disabled

Disabling stops the remote listener, revokes every session issued on it, and
returns the application to loopback-only. `users.json` is retained, so
re-enabling does not mean recreating accounts. Nothing about the local
experience depends on the remote subsystem being present.

## N. Test strategy

The existing hardware fence (`tests/_fence.py`, installed by the test package)
is preserved unchanged; zero tests may reach the bench.

Three new fences, in the spirit of the existing one — enforced by construction
rather than by discipline:

1. **Route classification completeness.** Every route in `url_map` has a
   classification. A new route with none fails the suite.
2. **Default-deny.** For every registered route, an unauthenticated request
   arriving on the remote listener returns 401/403 — driven from the route
   table, so it cannot fall behind.
3. **No credential in any response or log.** The existing canary approach
   extended to the new endpoints.

Plus the §29 list: role checks, disabled accounts, feature/source/destination
permissions, direct unauthorized API calls, privilege escalation, session
expiry and revocation, CSRF, throttling, concurrency, audit redaction,
persistence across restart, clean start, and disable/re-enable.

Adversarial rather than happy-path: the permission tests must call the API
directly with a crafted body, not drive the UI (§25).

## O. Security risks and open questions

1. **HTTPS is the largest open item.** Authentication over plain HTTP on a
   routable network protects the account but not the session cookie.
2. **The local bypass is the highest-risk mechanism.** It is the one place
   where a mistake grants full Admin anonymously. The dual-listener design in G
   exists to make it a property of the socket rather than an inference.
3. **`/api/list_dir` and `/api/files` should arguably be narrowed regardless**
   of this feature — they accept arbitrary absolute paths, and the folder
   browser only needs to reach the firmware directory. Out of scope here, and
   recorded rather than changed.
4. **Concurrency is a real behaviour change, not a hardening item.** Two users
   routing one decoder, and an Admin scanning while users route, need decisions
   at the service layer, not optimistic UI.
5. **Device traffic must not scale with browser count** (§30). Remote clients
   must consume the host's existing shared state mechanism; ten browsers must
   not mean ten times the device reads. This needs measuring, not asserting.
6. **A restricted User's filtered `/api/state` is a new derived view.**
   AGENTS.md requires derived values be recomputed from their source rather
   than cached forward; the filtered view must not become a second inventory.
