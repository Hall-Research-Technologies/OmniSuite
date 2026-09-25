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

---

# Part 2 — What was built, and what the plan above got wrong

Everything above was written before any of it existed. This part records what
the implementation actually does, where it departed from the plan, and what the
bench and the mutation harness measured. Where the two disagree, this part is
the truth.

## K resolved — HTTPS

All four options in K were considered; three shipped and one was rejected.

| Plan | Outcome |
| --- | --- |
| 1. Operator-provided certificate and key | **Shipped.** `Settings > Remote Access > Use my own files`, two paths. Needs only the standard library. |
| 2. Locally generated self-signed | **Shipped.** `cryptography` was declared as a dependency for this, after measuring the cost. |
| 3. Reverse proxy | **Not shipped.** No proxy support was added, so nothing claims it. |
| 4. HTTP-only | **Retained, off by default, and it cannot happen by accident.** |

`cryptography` was already present transitively, through pypdf's optional
extra, but a transitive presence is not a dependency. Measured: it adds 11.2 MB
to a 33.5 MB executable, 9.9 MB of that the Rust extension. It was declared
anyway, because without generation an installer with no certificate of their
own stays on plain HTTP, which is the outcome the whole section exists to
avoid.

**HTTPS is the default and there is no automatic downgrade.** If the
certificate is missing, unreadable, encrypted, or does not match its key,
remote access does not start: the reason is reported and nothing listens.
Plain HTTP requires `accept_insecure` in the request body, so a caller that
merely omits a field cannot reach it.

**There is no HTTP listener at all while HTTPS is active.** A redirect from
HTTP to HTTPS has to accept the request before it can protect it, and a sign-in
posted to that listener has already been sent in clear. The strict reading of
§13 is the one implemented: nothing answers on plaintext.

`omni_tls.describe()` returns subject, issuer, fingerprint, expiry and whether
the certificate is self-signed. **It never returns key material**, and there is
no endpoint that can. The generated key is written with `os.open` and mode
`0o600`; an existing key has its mode narrowed on load. Neither file is in the
package: a clean installation contains no certificate and no key.

Minimum protocol version is TLS 1.2.

## The Secure cookie

`secure=request.is_secure`, so the flag follows the transport rather than a
setting. `HttpOnly`, `SameSite=Lax`, the opaque server-side session and the
`X-OmniSuite-CSRF` header are all unchanged — HTTPS was added beside the
authentication design, not in place of any part of it.

## Concurrency — O.4 resolved

Two mechanisms, deliberately separate.

**Resource locks** (`omni_locks.py`) serialize operations that touch the same
hardware. A lock is per affected resource — `decoder:<identity>`, `usb:<mac>` —
never one global application lock. An operation touching several resources
acquires them in sorted order, which is what makes deadlock impossible rather
than unlikely, and releases everything it holds if any acquisition fails. The
wait is bounded; exceeding it is `ResourceBusy`, which the API reports as 409
with the resource named.

**The rule is last successful authorized operation wins.** A lock is held for
the duration of one operation and no longer. There is no ownership, no claim
and no lease, because a browser that closed must never be able to leave a
classroom display unusable.

**The state cache** (`omni_statecache.py`) is about reads, not writes. It
already had a TTL and single-flight; this phase added a generation counter and
an epoch so an in-flight read cannot publish a value that a writer has since
invalidated. Two gaps were found by stress tests written for this section:

1. A read that began before an invalidation would store its result afterwards,
   so a writer's invalidation could be silently undone. Fixed with a per-key
   generation counter compared at publication time.
2. A *global* `invalidate()` could not discard an in-flight read for a key that
   had never been individually invalidated. Confirmed by experiment before it
   was fixed — the stale value survived. Fixed with an epoch counter.

A discarded result is still returned to the caller that asked for it; it is
simply not cached. The statistics distinguish `computed` from `discarded`, so
the behaviour is observable rather than asserted.

Device traffic does not scale with browser count. Re-measured after all of the
above: A/V 1 client 0 reads and 10 clients 0 reads; USB 4 and 4; Multiview 6
and 6. Ratio 1.0x.

## The audit log — and a denial of service found in it

Every event rewrote the whole file, up to 5000 entries, **while holding the
lock authentication needs**. Anyone who could reach the sign-in page could
produce those events at will, so the log was a way to make the application
stop answering.

Now: the snapshot is taken under the lock and the write happens outside it, and
writes are batched on a flush cadence with security-relevant events
(`AUDIT_IMMEDIATE_EVENTS`) still written at once. The entry count is bounded, so
a flood costs history rather than the disk. Redaction is recursive through
nested objects and lists.

`username` is deliberately **not** redacted. It is not a secret and the view is
unreadable without it. Passwords, hashes, tokens, CSRF tokens, session data and
device credentials are.

## Duplicate friendly names — and an architectural gap

Duplicate names are **not** prohibited: two displays legitimately share a name,
and a global uniqueness rule would be the application overruling the site.

`omni_projection.disambiguate()` appends the device's own hostname, but only
where a label actually repeats **within that account's visible set**. A User who
can see one *Front Display* sees it named exactly as the administrator typed it.
This is a projection, computed server-side from the same derived object both
pages consume.

**The gap, reported rather than papered over:** OmniSuite has no location, room,
site or description field anywhere in its data model. The only human-readable
differentiator available is the hostname, which is an engineering string. A
room field would be the correct answer and does not exist; inventing one from
address ranges or hostname patterns would be fabricating location data, so
nothing does. The User Guide says so in the operator's words.

MAC and IP are not used for this. A hostname is already visible on the
administrative screens; a MAC is not, and exposing one to solve a label problem
would be leaking identity to solve presentation.

## Authorization at the operation boundary — §6

`_current_account()` re-reads the session at the moment an operation is
performed, not when the page was rendered. An account disabled, deleted,
demoted, or whose permissions changed a moment ago is refused immediately;
`resolve_session` re-checks the account on every request rather than trusting
what was true at sign-in. Nothing depends on a browser refreshing itself.

## N — the three fences, plus a fourth

The three fences in N exist. A fourth was added: the **mutation harness**,
`tools/mutate_security.py`. It breaks each security predicate on purpose and
requires the suite to notice. A test that cannot fail proves nothing, and the
only way to know an assertion is load-bearing is to make the defect it names
and watch it fail.

It covers the listener trust model, route classification, the local-only
refusal, the session requirement, CSRF, the Admin check, the feature check, the
source and destination checks, Multiview approval, session expiry and
revocation of a disabled account, the Secure cookie, the plaintext
acknowledgement, and address validation.

The harness records each suite's whole output rather than its summary line,
because a runner that reads only the summary reports every mutation as
uncaught, which is indistinguishable from tests that do not work. It restores
the file in a `finally`, so an interrupted run never leaves a deliberately
defective source behind.

## O revisited

| Item | Now |
| --- | --- |
| 1. HTTPS is the largest open item | **Closed.** Default, with no automatic downgrade. |
| 2. The local bypass is the highest-risk mechanism | **Unchanged and re-proved.** Trust is the socket the request was accepted on (`omni.listener`, a key no HTTP header can produce), never an address a request claims. Four mutations attack it directly. |
| 3. `/api/list_dir` and `/api/files` accept arbitrary paths | **Still true, still out of scope, now LOCAL_ONLY** so it is unreachable from the network. Recorded, not fixed. |
| 4. Concurrency needs service-layer decisions | **Closed** — resource locks and the cache generation counter, above. |
| 5. Device traffic must not scale with browser count | **Closed and re-measured** at 1.0x. |
| 6. The filtered view must not become a second inventory | **Closed.** It is a projection built from named fields, recomputed per request; tests assert the key sets are equal rather than merely contained. |

## What is claimed, and what is not

**Not claimed:** resistance to sophisticated timing analysis. What is asserted,
and tested, is that the obvious differences are absent — an unknown username
and a wrong password produce the same answer and both cost a verification. A
timing measurement in a test suite on a loaded machine would be flaky and would
prove less than reading the code path does, so the test reads the code path.

**Not claimed:** anything about an attacker with physical access to the
OmniSuite computer. The local operator is trusted by design; that is what the
local listener means.

**Not claimed:** protection against an active attacker on first contact with a
self-signed certificate. The User Guide says the browser warning is about who
vouches for the address, not about whether the connection is encrypted, which
is the accurate statement.
