# OmniSuite development rules

## Preserve the working application

- This is an existing working application. Preserve existing behavior.
- Prefer additive changes over refactoring working code.
- Do not replace working subsystems simply to make architecture cleaner.
- Do not modify unrelated functionality.
- Do not break encoder discovery.
- Do not break decoder discovery.
- Do not slow existing encoder/decoder discovery.
- Do not serialize new discovery operations behind existing discovery.
- New device discovery should run concurrently where safe.
- Long-running or broad scans must run in the background and must not block normal discovery or UI rendering.
- Do not change existing E4521/D4511 USB routing behavior unless specifically instructed.
- Do not change existing USB matrix behavior unless specifically instructed.
- Preserve existing APIs, routes, and persisted fields unless a change is explicitly required.
- Database or persistence changes must be additive and backward compatible whenever practical.
- Reuse existing UI components and design patterns.
- Reuse existing services and models only when doing so does not introduce regression risk.
- Keep protocol and network logic out of UI components.
- Validate network responses before changing application state.
- Device identity must not depend solely on IP address when a stable hardware identifier such as a MAC address is available.
- Add tests for new behavior; existing tests must continue to pass.
- Never silently swallow network or protocol errors; log safe diagnostic context and return useful failures.
- Avoid excessive polling and network traffic.
- Do not perform unnecessary architectural rewrites.
- Do not automatically change the host computer's NIC or IP configuration.
- Do not assume undocumented device behavior. Mark hardware-dependent behavior for validation.

## USB extender guardrails

- AT-OMNI-311/324 support is a distinct binary-UDP protocol integration, not an extension of the OmniStream WebSocket protocol.
- Keep automatic local USB discovery independent of the existing `/api/scan` path. Encoder and decoder results must be available without waiting for USB discovery.
- Put configurable range scans in a bounded-concurrency background worker; never add them to startup or a blocking normal-discovery request.
- Preserve MAC identity across IP changes and retain discovery reachability separately from manageability and pairing eligibility.
- Do not combine AT-OMNI-311/324 controls with `/api/usb_pair`, `/api/usb_unpair`, or `usb_icron` until a single, hardware-validated unified routing design is approved.
- A command acknowledgement is not a verified route. Never report routing success from an ACK alone; require a fresh read-back that agrees, and report the accepted-but-unverified case distinctly.
- Routing decisions must consume state read during the current operation. Cached pairing data may be displayed as history but must never be substituted for a failed authoritative read.
- Any targeted operation against a known MAC must verify the responding MAC. Never update, revive, or address a record using a reply from a different device.
- Network eligibility fails closed: when real interface information is unavailable, classify as unknown and deny pairing eligibility rather than inferring a network from the device's own address.
- Bound address-range input arithmetically before expanding it. Never materialize an address space in order to discover that it is too large.
- Keep an opcode or command unsupported and untransmittable until a specification confirms its value; do not infer one from adjacent values.
- Device Info owns discovery and clearing. Do not add a second discovery console, interface picker, or clear action elsewhere; other pages view and configure what discovery already found.
- Dispatch a device action by ownership. A USB endpoint belonging to an E4521/D4511 is acted on through its parent's existing control path; only a true standalone unit receives a standalone extender command.
- Correlate a USB endpoint to its parent solely by exact normalized MAC against recorded device state. Never infer association from IP proximity, MAC similarity, address suffixes, or hostname patterns.
- Present a field as read-only `N/A`/`Not supported` when the device API does not establish it. Do not fabricate a configurable control to fill a column.
- One physical endpoint appears once in an inventory. Correlate and enrich an existing row rather than adding a second row from another provider.
- A discovery protocol's reported type is not a product identity. When authoritative association exists, it wins for both presentation and control ownership; keep the protocol-reported value for diagnostics only.
- Classification fails closed. Until association is proven complete, treat an uncorrelated endpoint as unconfirmed and withhold device-specific commands rather than assuming it is standalone.
- Derive a value once. When two pages show the same state, they must consume the same derived object, not recompute it independently.
- Hardware-validation access belongs behind an explicit diagnostic endpoint with a confirmation token, never behind normal product controls.
- Tests must deep-copy fixtures they hand to server code; background tasks mutate cached device dicts in place.
- Ownership is decided in exactly one server-side pipeline that every page and endpoint consumes. No page determines device ownership on its own.
- Derive association from current state on every call so late-arriving information resolves an unknown automatically, without requiring the user to rescan.
- Learn authoritative data wherever the application already queries it rather than adding a second device round trip for the same fact.
- Correction requests are general. Fix the class of defect for every current and future device; a per-device special case is never the fix.
- Prove genericity with synthetic fixtures whose addresses, MACs, and hostnames share nothing with the reported examples, and assert no live value appears in production code.
- A refresh must not erase additive metadata learned outside it. Preserve learned fields onto rebuilt records, matched by stable hardware identity, and let new authoritative data win.
- Persist learned state that is expensive to relearn, so a restart, a cache clear, or a rebuild does not lose it.
- Every write to a shared JSON file must be atomic and serialized: unique temp file, fsync, os.replace, under one lock. Never truncate a file in place.
- A malformed state file must quarantine and degrade to empty, never crash discovery or propagate corrupt data.
- Verify a protocol's propagation model on hardware before assuming it. A command may update only the device it is addressed to; test both endpoints rather than inferring that a peer was told.
- When a multi-step device transaction fails partway, roll back the steps that succeeded rather than leaving a half-applied state.
- Classify each route combination independently from its own evidence. Success in one family never implies another.
- Separate control-plane from data-plane verification, and keep them in separate tables. Agreement between control-plane tables is not proof that the data plane works, and enabling a route so it can be tested must never upgrade the claim made about it.
- Re-verify through the production endpoint, not only the bench diagnostic, before calling a path validated. The shipping path is the one that has to be proven.
- Reuse an existing proven device operation for a new surface. If no such operation exists, disable the control and say why; never invent one to fill a gap in the UI.
- Before any experimental hardware mutation, capture the original state, use only endpoints with nothing in use, and verify restoration before continuing.
- Order every list a UI renders deterministically on the server. Never let thread, dict, or response completion order reach the screen, and sort addresses numerically rather than as strings.
- Separate structure from live state. A state-only refresh updates existing DOM in place; rebuild only when membership or identity actually changes.
- One shared backend live-state mechanism serves every page, with in-flight suppression and a minimum interval, so extra open pages never multiply device traffic.
- Prove liveness with the cheapest sufficient query, and take it from an existing read where the application already performs one rather than adding a second round trip.
- Tolerate a dropped datagram before declaring a device offline, and treat a determined-offline result as authoritative over an older sighting.
- Production UI never uses native alert/confirm/prompt. Reuse the application's existing dialog structure, and never overload Cancel to mean a second choice.
- Never put detailed explanations in a native `title`; the browser renders them as one very wide tooltip. Use a bounded, viewport-clamped styled tooltip.
- Internal enum names belong in JSON, tests and logs, never in user-facing text. Map each state to a concise human label.
- Give each kind of freshness its own timestamp and cadence. Do not let a cheap liveness check imply that expensive state was re-read.
- Refresh expensive state on its own slower cadence, skip anything already fresh, and reset the cadence immediately after an operation that already read it.
- Integrated 45xx Icron network configuration uses the parent OmniStream `net` API (the `icron` interface), never the standalone UDP IP commands.
- Resolve the parent server-side from the USB MAC before any integrated USB mutation, and verify the MAC belongs to that parent.
- A frontend render change needs a runtime smoke test, not only a syntax check; `node --check` cannot catch an undefined helper.
- Define one escaping helper per module at module scope. A helper defined inside a function is invisible to other scopes and will fail only at runtime.
- When a capability looks absent, check whether it is nested inside a broader configuration object before concluding the product cannot do it.
- Where a device and a cache disagree, read the device and make the application agree with it. Never reconfigure hardware so it matches what the software already believed.
- Give each device family its own authority for a field. A value that is meaningful in one protocol may be meaningless in another; when no authoritative read has succeeded, show nothing rather than a value known to be unreliable.
- Preserve a device's raw value alongside the interpreted one, and surface an unrecognised value as unknown rather than normalising it into something that looks valid.
- Route every path that ingests the same device state through one ingest function, so discovery, a manual refresh, and a post-write read-back cannot interpret it differently.
- Tests must never be able to mutate real hardware. Stub the transport in the shared base class, allow reads, and make any mutating command fail the test; assert it at the call and again in cleanup, because service code that converts errors into results will otherwise absorb the assertion.
- Widening a gate widens what reaches the transport behind it. Re-check what the tests on the far side of that gate will now do.
- Identity and display key are different fields. Never overload one key so it means an address on one row and a hardware identity on another, and never send a display key where an identity is expected.
- A permissive normalizer is an attack surface on identity. If it strips separators it will happily convert an address into a plausible identifier, so reject the other format explicitly.
- A control that is enabled on screen and the endpoint behind it must consume the same derived state. If the UI and the gate judge liveness, ownership or capability from different sources, the user sees a clickable control that always fails.
- When a status code doubles as a semantic answer, say which it is. A 404 meaning "this endpoint is unknown to me" reads as a missing route; separate malformed input (400) from an unknown-but-well-formed identity (404).
- Never fall back from a failed new path to an older one that addresses devices by a different key. Report the failure instead; a retry through an incompatible path can reach the wrong hardware.
- Verify a device operation with the same source the operation itself used. A view built for presentation may deliberately omit or transform the field, and using it to verify produces a false negative that triggers an unnecessary rollback.
- Two views of one device state may converge on different timescales. Before concluding that one cannot see the other's writes, read it again later; "blind" and "late" call for different designs.
- Select route state by control path, not by row family, once one row can be routed by more than one provider.
- Assert the frontend's request contract in a test that actually dispatches the click and inspects the request, and pin the endpoint path against the server's route table so the two cannot drift apart.
- Test scenarios that share a rendered DOM or a request log must run sequentially; an async check whose setup runs at declaration time will be trampled by the next one.
- Successful live device data overrides persisted standalone configuration; persisted values are fallback and history only.
- Standalone Atlona model identification uses the physically verified Atlona mapping, not generic Icron assumptions, and never the product ID, hostname, address or MAC pattern.
- Configured pairing and current link status are separate concepts with separate fields and separate freshness. A configured peer is not a live link, and a timeout is not a confirmed absence of one.
- An unknown protocol field stays unknown. Name a value only once hardware has shown it; fold everything else into an explicit unknown rather than the nearest known state.
- The Matrix USB inventory is one row per canonical USB MAC. Merge on identity server-side; a client-side filter is defence in depth, never the fix.
- A row states its own capabilities. A standalone device must not inherit an integrated-only control just because it shares a table.
- Before renaming a shared column, audit what it holds for each family. Rename only when the values are semantically the same thing, and keep a genuinely different fact in its own field.
- A parser change needs exact captured-hardware fixtures, asserted by length before use, and a malformed-input suite beside them.
- When two captures disagree with a working implementation, suspect the labels before the code, and re-derive identity from a response that carries it self-evidently.
- Measure a remedy before adopting it. A retry, a longer timeout or a wider bound that does not move the number is traffic without benefit.
- Distinguish a degraded device from a defect in the software. Verify at a layer below the application before changing code to accommodate it.
- Broadcast finds unknown devices on the attached L2 network; it is never how known devices stay visible. Poll what is already known by address, with directed unicast, which routes.
- Every surface must draw its endpoint population from one logical inventory. A device present in one view and absent from another is a discovery-source bug, not a display bug.
- Opening a page must never be what repairs backend state. The backend maintains truth on its own schedule; pages display it.
- Bind a source address only when the local interface is on the destination's network. Forcing one otherwise makes a routed endpoint unreachable, so let IP routing choose.
- Compatibility between two devices is computed from their own addresses and masks, never from the controller's subnet, and never from an assumed /24.
- Keep the mask of the interface a device was discovered from separate from the mask the device reports for itself; conflating them corrupts every relation derived from either.
- A transaction whose goal is already half-met should converge toward that goal rather than refuse, provided the disagreement is only between the two endpoints and both reads are authoritative.
- Command only the endpoint that is missing the intended state. A device may reject a command re-asserting what it already holds, and an endpoint the transaction did not change must never be rolled back.
- A backend capability the UI never exposes is unfinished. If an operator cannot see or set it, it does not exist for them.
- Finding an unknown device requires something to address: an attached network to broadcast on, or a configured network to scan. Say so plainly rather than implying discovery is universal.
- Re-probe a known device that has gone quiet before treating it as gone; its recorded address is the only lead available, and it may simply have moved.
- Search only fields the user can see. A diagnostic value that is deliberately never displayed must never be searchable, or the results look arbitrary.
- Every parser in a module should fail the same way. A stray ValueError escaping where callers expect the module's own error type surfaces as a 500 on malformed user input.
- Persist a user's choice by a stable identity, never by list position, and bind the save handler as soon as the control exists rather than after its saved value loads.
- A diagnostic flag must never gate the action that would set it. Enabling a control and claiming its outcome is proven are different statements.
- Measure each device family's capacity separately even when they share a platform, and claim only what was observed simultaneously.
- Capacity for peers and capacity for downstream devices are different quantities; never let one imply the other.
- When a device stops answering under sustained traffic, pace the operations and let the gate refuse; do not retry into a device that is not listening.
- A control states the state the user wants, not the operation the protocol needs. Reconcile the hardware to the request; never make the operator perform the intermediate steps.
- Before removing a working relationship to create a new one, capture what exists and be able to put it back, and report separately whether the restoration was verified.
- Show a change as pending until a verified read-back settles it; an intermediate state must never be displayed as a completed user action.
- Refresh a value before the freshness window that governs it expires. When the re-read interval equals or exceeds the TTL, there is always a window in which good state reads as unknown, and that gap is a design defect rather than a device fault.
- Tolerate a bounded number of missed reads before discarding a verified value, and present the retained value as ageing rather than replacing it with unknown or discarding it silently.
- Give each fact its own recorded provider. A single source field written by whichever refresh ran last will alternate on its own and be read as the endpoint changing.
- Decide authority by fixed precedence, not by write order, when two providers can attest to the same fact at once.
- Control ownership does not restrict reads. A device that answers a safe query still answers it when its writes belong to another API.
- Summarise a multi-peer state from every peer, never from one byte standing in for all of them.
- Absence of evidence is not a capability statement. Say a capability is not established, with the evidence, rather than reporting it as unsupported.
- Two inputs that describe the same search space are one input. If the second exists only because the first is not remembered, persist the first.
- Order asynchronous reads against the writes they race. A response that was already in flight when state changed describes a world that no longer exists and must be discarded, not applied.
- LLDP multi-switch warning acknowledgement belongs to the lifetime of the discovered-device inventory. Never reset it on Scan/Discover. Reset it only when the discovered-device inventory is explicitly cleared.
- An acknowledgement is functional state with its own store. Never infer it from whether the element it silences happens to be visible.
- A dismissible notice and the condition it describes are separate. Dismissing the notice must never remove the indication of the condition.
- Advisory information is not an error. Give it its own semantic state rather than borrowing the danger treatment, and never describe a supported configuration as a fault.
- Absent data is not a distinct value. Compare only records that carry the field, and never let a missing reading create a second group.
- A dominant group needs strictly the most members. On a tie, designate nothing rather than picking one arbitrarily.
- A fresh discovery of an existing canonical hardware MAC updates that device's mutable live address and state in place. IP address is not device identity, and clearing the inventory must never be required merely because a device changed address.
- LLDP upstream-switch resolution must not discard physical path information. If a device is reached through another known endpoint, retain and expose the daisy-chain relationship even when grouping the device under the resolved upstream switch.
- When merging cached and fresh records, iterate the fresh ones first. A dedupe check that runs cache-first silently discards the newer observation it was meant to protect.
- Audit every secondary structure keyed by address whenever identity is keyed by something else. The stale row usually survives in the index, not in the record.
- A layout is presentation. Never fork the behaviour layer per template; one set of logic, several stylesheets.
- Read a persisted UI preference synchronously before first paint, and treat the server copy as the authority that reconciles afterwards.
- Two advisories about different risks need two acknowledgements. Record what an acknowledgement covered, so a genuinely new instance can be surfaced once without re-notifying for the same one.
- State a bandwidth risk from topology, never a bandwidth condition, unless the devices actually report throughput. Say plainly, in the data, that no measurement exists.
- A diagnostic export may omit bulk, never omit that something is unknown. Sanitisers that strip empty values must not be applied to sections whose nulls are the answer.
- Routed management does not imply routed USB pairing eligibility. Every USB route creation or reassignment must prove same-subnet compatibility from the actual USB/Icron endpoint IP and subnet mask before any mutating operation.
- The USB inventory and export report USB/Icron firmware for integrated endpoints. Parent OmniStream firmware belongs to the parent device inventory and must never be substituted for a missing USB firmware value.
- For a safety gate, unproven is refused. Distinguish "incompatible" from "cannot say", and let only a positive proof authorise a mutation; an existing configuration is never torn down because telemetry went incomplete.
- Check eligibility before releasing anything. A destructive step taken ahead of validation turns a refusal into an outage.
- Never assume a prefix length or compare address octets. Derive the network from the mask each endpoint reports, and require each endpoint to fall inside the other's network.
- When a derived value the UI needs is computed per cell, stamp it onto the axis once. A grid that rebuilds shared state per intersection is both slow and prone to being skipped entirely.
- Two controls that appear to choose the same thing are one control. Remove the duplicate rather than renaming around it.
- A custom colour must be validated as a colour before it reaches a custom property, and must be applied wherever a palette class could redeclare the same token.
- Compute a readable foreground for any colour a user chooses; keep their colour and decide only what sits on top of it.
- Layouts may change structure and density, but there is one ownership path for primary navigation and page content. A layout must never duplicate navigation or relocate page nodes; style what the server rendered where it rendered it.
- All layout-specific surfaces consume semantic theme tokens. Layout CSS must not hard-code a dark or light application shell.
- A custom property resolves where it is declared. An alias defined once on the root freezes at that palette's values, so every appearance mode must restate the aliases it changes, not only the variables they were derived from.
- Paint the document element as well as the body, or a strip of the viewport keeps the previous palette.
- When a control is removed, stop honouring the state it wrote. Ignore it on read and drop it on the next save, in every store that holds it, so nothing keeps acting on a setting the operator can no longer see.
- Offering a user N independent choices to reach one outcome is not customisation. Ship curated sets and keep the underlying tokens internal.
- Derive related surfaces from a chosen colour rather than painting everything with it; a single flat fill destroys the separation the layout depends on.

- A safety fence belongs at the transport, not at the convenient wrapper above it. Stubbing the shared send/receive helper left four mutating paths that opened their own socket, so the guarantee held only for code that happened to route through the helper. Fence the thing that actually reaches the network, and a future bypass is caught by construction rather than by remembering.
- A test that cannot fail proves nothing. Before trusting an assertion, make the defect it names and watch it fail; a fixture that omits the very field under test turns `assertNotEqual` into a tautology.
- Claiming a guard flag before an early return strands it. Every path out of a claimed critical section must release, or the first empty result disables the feature for the life of the process.
- Two individually locked operations do not compose into an atomic one. A read-modify-write must hold one lock across the whole sequence; separately, concurrent writers each apply their change to their own copy and the last save silently discards the rest.
- Registering a handler on an object that is later reconstructed registers nothing. Build the application once; a second construction quietly discards everything attached to the first.
- A device that does not answer is an upstream condition, not a fault in this application. Report it as such, and keep the server-fault status for genuine bugs, or a routine offline unit is indistinguishable from a crash.
- A test that replaces a module-level name must restore it. Test classes run in alphabetical order, so an unrestored stub leaks into unrelated tests and the blast radius moves whenever a class is renamed.
- Measure before changing, and accept the measurement. When profiling does not reproduce a predicted cost, report no change; a cache added on the strength of an unverified prediction can regress the live feedback it was meant to speed up.
- An idle poll must not log at INFO. Steady-state chatter buries the records that matter -- a mutation, a refusal, a device that stopped answering.
- Audit logging names the fields it records; it never dumps the request body. A verbatim dump logs whatever a caller sends, including a credential added to that request later.
- Global navigation and Settings occupy the same screen position on every page. A control the operator's eye has learned to find must not move because the work surface below it changed; shell geometry and page-content geometry are separate concerns.
- A control that appears on several pages has one implementation. Copy the markup into each page and there are as many dialogs to keep in step as there are pages; own it in one module, inject it, and guard the injection so a second call cannot duplicate every id.
- Styling shared markup is part of sharing it. Markup extracted into a module whose CSS stays behind renders unstyled everywhere except the page it came from.
- Primary device identity columns stay visible while a wide table scrolls sideways, and a frozen column's offset is measured from rendered widths rather than assumed. A hardcoded offset is correct in one layout and wrong in the others.
- A mirrored scroll control reports the authoritative container's real dimensions and is hidden when there is nothing to scroll. It must never be driven from an assumed width, and the synchronisation needs a reentrancy guard so the two ends cannot drive each other.
- Automated tests are physically isolated from both hardware transports, and the isolation is enforced by the test entry point rather than left to each test's discipline.
- An icon-only control carries an accessible name. The tooltip is not one.
- Device Info's primary command area stays reachable while a long inventory is traversed. Pin the compact command surfaces only; an advisory panel pinned forever costs the height the operator came for.
- Express every sticky offset in terms of measured, published heights. A file full of unrelated `top: 37px` / `top: 91px` values is correct at one zoom, one font and one layout, and wrong everywhere else.
- Where a sticky header and a frozen column intersect, the corner carries the highest stacking order of the three. Prove it by hit-testing the corner's own centre while scrolled in both directions, not by looking at it.
- Suppressing a focus outline to hide it on mouse click hides it from the keyboard too. Scope the suppression to `:focus-visible` or the control becomes unreachable to anyone navigating by keyboard.
- Redundant CSS is not free. A rule that merely repeats an existing one still participates in the cascade, and a more specific repeat silently changes whatever the original settled.
- Read a colour only after its transition has finished. Sampling a computed background mid-transition reports a value the user never sees, and the reading differs on every run.
- An observer must never be re-armed from inside its own callback without disconnecting the previous one. Re-arming on every delivery makes the observer count double per event, and since each callback typically forces a layout, the cost of one render grows exponentially with how long the page has been open. Observe the element that persists once; replace only the observers whose targets were actually detached, and disconnect before re-observing.
- Discarding a stale response is not the same as not issuing the request. A sequence counter protects correctness; a poll that sweeps hardware also needs single-flight, or a slow read lets the interval stack another sweep on top of the one still running.
- Diagnostics belong in the product, off by default and silent. A thread-starvation report cannot be argued about without long-task, request-concurrency and observer-callback numbers, and adding the instrumentation after the fact changes the thing being measured.
- One authoritative version, read by everything that displays it. A literal copied into a page is correct on the day it is written and wrong from the next release onward.
- Percentages in a `color-mix()` must sum to 100. A mix summing to less multiplies the result's alpha by that sum, so the token is silently translucent and every surface built on it lets content show through.
- Apply the theme to the document element, not only to the body. A script in `<head>` runs before `document.body` exists, so deferring to DOMContentLoaded paints the first frame in the wrong palette.
- When a headless harness reports that nothing is happening, confirm the instrumentation actually ran. An isolated evaluation world cannot see page globals, and a probe that quietly falls back to an empty object reports reassuring zeros.
- Any independently horizontally scrollable application section must expose synchronized top and bottom horizontal scrolling when overflow exists, from one shared implementation rather than a copy per table.
- An optional external service is never a dependency. Bound the request, cache it for hours, keep it off every polling and startup path, fall back to the last good answer marked stale, and make the manual action always available so the feature degrades to a link when the network is gone.
- Compare versions numerically, never as strings: 1.0.10 is newer than 1.0.9, and an unparseable version means "no update", not "update".
- Validate any URL received from an external service against the expected host and path before handing it to a browser, or the endpoint becomes an open redirect.
- Identify a platform artifact confidently or not at all. Offering the wrong build is worse than offering the download page.
- Write files with the line terminator they already use. On Windows, `write_text` translates `\n` to `\r\n`, so a script that normalises and rewrites adds a CR on every pass and the damage compounds silently.
- An explicit operator refresh re-reads everything it covers, not only the records with something missing. Selecting incomplete records means a value that CHANGED on the device can never arrive, because the record stopped looking incomplete the moment it was first filled.
- Clearing the inventory is not a refresh. If emptying a store is the only way to pick up a device's current state, the refresh path is broken and that is the defect to fix.
- A reply's fields are worth keeping even when the caller only wanted one of them. Firmware sat in every usb_icron response and was discarded, leaving a field permanently blank for any endpoint that had no second source.
- Fresh known value beats cached known value beats a missing fresh value. An optional query that fails or omits a field must never erase something already verified, and a field that arrives must never be ignored because a record already existed.
- Investigate the device before changing the code. Querying the parent directly showed the firmware was already being returned, which turned a suspected protocol gap into a two-line data-plumbing fix.
- A product name and a protocol identity are two different facts about the same device. Map between them in one place and route every rendered surface through it; a global search-and-replace takes the identity with it, and the role lookups, family checks and bench guards that compare against that string fail silently rather than loudly.
- A boundary that is not a hardware transport still has to be fenced for the lifetime of the test process, not per test. A daemon thread started inside one test outlives its teardown, by which time the per-test restore has put the real implementation back -- which is how a suite that stubs an external service in `setUp` still reaches the Internet, intermittently, from a thread nobody is looking at.
- Acknowledge a notification against the thing it is about, never with a boolean. Recording the version dismisses that release only: a newer one announces itself, re-checking the same one stays quiet, and a late acknowledgement of an older release cannot un-dismiss a newer one.
- A one-time alert and a standing status are different. The alert stops once it has been seen; the status goes on being true. Conflating them either nags forever or hides the fact after the first glance.
- A build ahead of the newest published release is current, not an error and not an update. That is why the answer needs three states and not a boolean -- and why a failed check must never render as "up to date".
- Retiring a preference means removing its readers, not only its writer. A key that is no longer written still reads as absent, and code that applies state from it keeps applying the absent value -- silently undoing whatever the replacement had just set, with the outcome decided by which handler happened to run last.
- Measure a shell across sections, not only across repeated loads of one page. Every page being pixel-identical to itself says nothing about whether the header is the same height on the next page, and a control that moves when you navigate is the part a user actually notices.
- Ask the device what its API returns before designing around it. `get_debug_info` was assumed to carry a log; it returns a path to a bundle the device builds on demand and serves over plain HTTP, which is a different feature with different failure modes, a different security surface and a different cost.
- A path a device hands back is untrusted input about to become a URL. Refuse a scheme, a host, a protocol-relative prefix, a parent-directory segment, a backslash, a control character or anything outside an explicit character set -- and refuse rather than repair, because a path that had to be sanitised was not understood. Build the URL against the address you already trust.
- A download's filename comes from data the application already holds, never from the remote artefact's own name. Sanitise it for all three platforms in one helper, including the Windows reserved device names, which are refused with any extension.
- A vendor artefact is passed through byte for byte. If it is encrypted, say so plainly rather than implying the application inspected it, and document what the operator is actually sending when they forward it.
- A number on a device's row must be a fact about that device. A value identical for every device on a subnet is a fact about the subnet, and beside one row it reads as a property of that unit; if it earns a place at all, name it where it is shown.
- Keep USB role, configured peer count, live linked count, peer capacity, physical port count and firmware revision as six separate fields. Two of them agreeing on one bench proves nothing, and one standing in for another is how a column comes to mean something nobody can state.
- A semantic role must survive every palette. Distinguish a destructive action by form as well as hue -- measured across ten presets, accent and critical were as close as deltaE 11 -- and never solve it by editing the palette the operator chose.
- Derive control states from the same tokens the presets set, in one shared stylesheet. A literal colour in a hover, selected or pending rule is invisible to the preset system, and it survives every change to it.
- A selected control is filled, not outlined. Measure the selected-versus-unselected difference rather than assuming it: a 20% tint measured deltaE 3.5 against an unselected tab, which is not a difference anyone can act on.
- Do not paint a label in the accent colour on a tinted ground. That pairing measured 1.90:1; the fill, the edge and an underline can all carry the selection, and none of them has to be legible as text.
- `filter: brightness()` is not a hover treatment. It lightens a light control and a dark one alike, so one rule reads as hover in one palette and as washed out in the other. Composite a translucent veil of the text colour instead.
- Hide a control's real input without removing it. `display:none` on the checkbox behind a custom switch takes it out of the tab order and out of the accessibility tree, so the control cannot be reached or announced at all; clip it instead and style the visible part from `:checked` and `:focus-visible`.
- Paint the class and set the state. A selected control that only gains a CSS class shows as selected and announces nothing.
- A synthetic mouse move does not set `:hover` in a headless browser. Force the pseudo-class through the debugging protocol, or a real hover rule reads as absent and gets 'fixed'.
- Compare a control with itself in both states. Measuring a selected tab against a different unselected tab measures their labels; toggle the state on one element and watch that element, its siblings and the shell.
- Bound a source-text assertion at the end of the thing it describes. A slice that runs to the end of the file starts asserting about whatever is added below it later.
- Put the costly answer behind the focus. When a confirmation's Yes starts something large or irreversible, Cancel takes the focus and the primary treatment so the keyboard default and the visual default agree; a dialog that looks like OK is the obvious choice while Enter cancels is worse than either alone.
- A confirmation whose purpose is the sentence it carries must not degrade to a native dialog. If the shared dialog is unavailable, refuse the action: a browser string box does not carry the notice, and the notice was the point.
- A notice that explains a property of the thing is shown every time. Only an acknowledgement of an event is remembered; making the explanation dismissible means the tenth download happens without it.
- Take the single-flight lock before opening the confirmation, not after it closes. Two dialogs stacked on one device each go on to make their own request.
- Move a flow out of a page and into a module when you need to assert how it behaves. Source-text assertions about an inline handler prove the text, not the behaviour; a module can be driven with a scripted dialog and a recording fetch, and then "Cancel makes no request" is a measurement.
- Never name a local variable after the global it is there to avoid. A local `confirm` in the one file that must never call `confirm()` makes the question unanswerable by inspection, and it defeated the check written to answer it.
- Check that a mutation harness can actually see a failure before trusting a clean sweep. A runner that prints only each suite's last line will report every mutation as uncaught to a grep looking for individual failures, which reads exactly like tests that do not work.
- When a test fails intermittently, make it name which of its preconditions broke. An assertion that blames the behaviour under test hides the population changing underneath it, and the next person reads a real race as flakiness.
- If everything is primary, nothing is. Reserve the accent fill for the action a surface exists for and for the principal action of a dialog; a page of utilities has no primary at all. Classify each control by what it does, not by the class it happens to carry.
- A secondary control is a filled surface with a real border, not a ghost outline, and it keeps hover, press and focus. Quiet is not the same as disabled-looking.
- Retire a native dialog by classifying it, never by search-and-replace. A failure or a confirmation of something that happened is a toast; a list the operator must read before acting is a modal; a destructive choice stays a confirmation. Downgrading a required confirmation to a toast removes the decision.
- A modal returns focus to the control that opened it. Without that, dismissing a dialog drops the keyboard at the top of the document and the operator has to tab back to where they were.
- An acknowledgement offers one button. Showing Cancel next to OK asks a question that has no second answer -- and whatever a dialog hides for one caller it must restore for the next.
- Documentation is part of the application. A help page with its own palette is a second product that drifts, and it is the surface most likely to be read on someone else's machine; give it the shared tokens and let it declare only measure, print rules and callouts.
- Two tints of different roles can converge in some palettes. Carry the distinction at full strength somewhere -- a rule, an edge, an icon -- so it survives a preset where the hues sit close.
- A colour probe must understand every syntax the browser emits. `color(srgb 0.98 0.98 0.99)` has components in 0..1 and `rgb(247, 248, 251)` in 0..255; treating the first as the second reports an almost-white as black, and a working light mode reads as 1.10:1 across six elements.
- An operator-enabled poll is still steady state. Fifteen devices on a five-second cadence at INFO is ninety lines a minute, and it buries a mutation or a refusal exactly as an idle poll would. Log the change, not the check.
- Validate a mutation harness before trusting a clean sweep: break something on purpose in every suite it watches and require that it notices. A harness that cannot see one suite reports that suite's tests as worthless, which is indistinguishable from them being worthless.
- An assertion sliced from source must have comments stripped first. An explanatory comment naming the very selector, attribute or call under test will satisfy the assertion with the code gone.
- `performance.memory` without forcing a collection is monotonic by construction. Before reporting a heap trend, collect garbage through the debugging protocol and read again: a soak that showed 10 MB climbing to 30 MB measured flat at 8 MB across forty navigations and a multi-minute dwell once collection was forced.
- Sample instrumentation on a page that carries it. A probe taken wherever a loop happens to end reports "not available" for every cycle, which looks like a clean result and is no result at all.
- A dialog taller than the window must be reachable. Centring it on a backdrop that cannot scroll puts its title above the top of the screen with nothing to scroll; let the backdrop scroll and centre with auto margins so a short dialog is unaffected.
- A runtime configuration file that can hold a credential never belongs in version control, even when the committed values are defaults. The repository copy is usually not the one the application reads, so it buys nothing, and the next password change gets published. Ignore it and track an example with placeholders instead.
- A build must never bundle a folder the operator chooses at runtime. The firmware directory is a Settings path, and adding it to the package produced a 981 MB executable on a machine that happened to have images in it -- 908 MB of them the manufacturer's. CI did not notice because that folder is gitignored, so the defect only existed where someone would actually hit it.
- "PyInstaller completed successfully" is not evidence that anything runs. Start the packaged artifact, wait for its server and ask it for its version, its pages and its assets; a missing data file or an uncollected import builds cleanly and fails only when a person opens it.
- An artifact name is a contract with whatever selects it. The updater matches a download by the tail of its filename, so renaming a release asset without changing that matcher offers a Windows machine the macOS Intel build. Pin the two together with a test that asks each platform what it would download.
- A release artifact's name states its architecture, and names the environment actually tested rather than the family. "windows" does not say x86-64, and "linux" claims every distribution when only one was built and tested.
- Read a log for the run you started, not for the file. An append-only log makes "the last port in the file" the previous run's until the current one writes its own line, and a probe that trusts it reports a working binary as broken.
- Bundling a directory bundles everything inside it. `--add-data ui` shipped fifteen superseded page iterations, because they lived in `ui/archive/`, and a repository that still tracked them made that invisible. Assert the package's contents from its own table of contents, not from the intent of the build command.
- A packaging guard must be tested against a build that actually failed. The first version of this one read the TOC's second field -- the path on the build machine -- and so reported `.venv/` and `build/` as contraband on every legitimate build while the real defect sat in the first field.
- "Is this file used?" is a question about references, not names. `omni_matrix_logic.py` is imported by module name and matches no filename search; `HallwayBlueBlack.png` appears only inside a CSS comment explaining an asset that replaced it. Search for both forms before concluding either way.
- What belongs in Git and what belongs in the executable are two different lists. The README screenshots are published documentation and must not ship inside the binary; the User Guide is an operator-facing runtime page and must.
- A tree that builds on the development machine proves nothing about what was published. Materialise only the files Git would carry, then install, test and build from that copy -- it is the only way to catch source that was never added.

## Multiview

- OmniSuite owns the layout; the decoder owns only geometry. There is no `layout` field in the API -- the device silently discards one -- so the chosen layout is persisted by OmniSuite, keyed by hardware MAC, and used only while the geometry on the device still matches it. Where it does not, re-infer from geometry; where that fails, say Custom. Never report a layout name the hardware no longer matches.
- A subframe has no width or height. A window's size is the resolution arriving on its ip_input, which is set on the *encoder's* scaler, so changing a window's size means reconfiguring a shared encoder rather than the decoder.
- Transport success is not configuration success. The decoder accepts a nonexistent input, a `0x0` canvas and a `delete: true` flag and answers `error: false` to all three. Every mutating Multiview operation is followed by a semantic read-back, and a write that the device accepted but does not hold is a failure.
- `config_set` merges by object name and never replaces a collection, so nothing is removed by omitting it. Objects are created and deleted only through `method` -- `add_multiview`, `del_multiview`, `add_multiview_subframe`, `del_multiview_subframe`.
- Changing an existing Multiview's layout must delete the subframes the new layout does not have, before writing the new ones. A merge leaves the old windows in place, and the device caps a Multiview at four subframes, so four new names on top of four old ones is refused outright.
- `method` is a write and belongs behind the same test fence as `config_set`. It carries no `config_set` key, so a fence watching only that let every `add_multiview` and `del_multiview` reach a real device.
- A vc2 scaler belongs to the encoder, not to the session or the stream, so retuning one changes what every decoder consuming that session sees. Classify the change as safe, a change, a known conflict, or unprovable, warn before Apply, and refuse a known conflict rather than letting the last layout win. OmniSuite can only reason about the Multiviews it knows; never imply it can see external consumers.
- Encoder 2 tops out at 1920x1080 and has no "disable" option -- it is always scaling. A window larger than that must come from Encoder 1, which usually carries the native feed other decoders depend on. At a 1920x1080 canvas every window fits Encoder 2, which is what makes that canvas the least disruptive mode.
- The ip_input mapping is a site convention, not an API rule. The bench uses 1 = Session 1 video, 3 = Session 1 audio, 5 = aux; the vendor guide uses 1/12/13. Discover the roles from the decoder's own state, never assume them, and never reallocate an input that carries audio, aux, or another Multiview.
- Do not build a multicast allocator. Devices already generate deterministic per-device destinations (`destination_generate_default`), so read the session's address and use it. An empty destination is an error to report, never an address to invent.
- Multiview is video-only and decoder-only. It never appears in `audio` or `aux` available inputs, so HDMI audio stays on its own ip_input and is left alone; and `config_get multiview` failing with "Config node not found" is the capability test, cached and never polled.
- SAP Input must be disabled before a Multiview selection on the HDMI output will hold, and the two are separate writes in that order.
- Deleting a Multiview must first move the output off it, verified, or the output is left pointing at an object that no longer exists. Shared encoder streams are deliberately left configured: proving no other decoder still needs one is not possible from here, and a leftover stream is cheaper than an outage elsewhere.
- Multiview stays off the discovery path. Nothing Multiview-related runs during a scan; the page reads three nodes from the selected decoder on demand, and encoder detail only for a source actually used.
- One layout engine drives both the preview and the device write. The page renders from the server's computed geometry and never derives its own, so the picture on screen cannot disagree with the hardware.
- An encoder reports its input under `video.resolution` with `video.active`; a decoder reports its output under `video.output.status`. They are different shapes, and reading the decoder's shape on an encoder makes pass-through impossible to recognise. A disconnected input can still report the last size it saw, so `active` is what proves a source is there.
- The scan records an encoder's session multicast as `sessionN_video_mcast`. `ipN_addr` is the decoder-side field -- the address a decoder listens to -- and a decoder subscribed to the same stream will match it, so resolving a window's source must consider encoders only.
- Exactly one model is barred from being a Multiview source: `AT-OMNI-111-WP`, matched as a whole normalised identity. This is not a wall-plate rule and not a suffix rule. `HW-OMNI-E4111-WP` is a 4xxx wall plate with the same two encoders, six sessions and generated multicast as any other encoder, and is eligible; an `endswith("-WP")` rule wrongly excluded it, and a `111` substring rule would take the plain `AT-OMNI-111` too. The exclusion applies to source eligibility only and must not change how the device is discovered, displayed or routed anywhere else.
- A decoder holds as many saved Multiviews as an operator wants, and exactly one of them is on the output. *(Superseded the one-per-canvas rule in Phase 6: two saved layouts routinely want the same Encoder 2 at different sizes, and making their requirements coexist is impossible rather than merely hard.)* An object created in the device's own web UI is never overwritten, and a metadata record stops counting the moment its object is no longer on the device.
- Saving a Multiview and showing it are separate actions. Saving configures the Multiview and everything feeding it and changes nothing on screen; showing is the one moment the operator's picture changes. Never make someone accept a picture change to store a layout.
- The canvas of an existing Multiview is read-only. Moving one to another resolution would collide with the Multiview already there or need a migration nobody has validated; delete and recreate instead. Changing its *layout* is fine and updates the same object.
- Say what the operator is doing, not what the device field is called. "Show on Display", not "select on output"; "Display is currently showing", not "HDMI output video input". Device field names belong in the engineering detail.
- Progressive disclosure: a step appears only once the one before it is answered. Loading, unreachable and unsupported are three different conditions and none of them may be presented as "no Multiviews configured". Creation is an action, never an entry in a list of things that exist.
- One control family per row. A select, a text input and a button styled by three different rules sit at three different heights, which reads as a mistake; fix the box in one class and give it to every control in the row.
- A decoder cannot open one multicast address and port on two ip_inputs -- "Unable to open eth1:1000". So an input already carrying the wanted stream must be shared, whatever else it is doing, and written to not at all; allocating a fresh input for a stream that is already open is a write that cannot succeed. A subframe only references an input, so sharing takes nothing away from the HDMI video or audio it also serves. Allocate a free input only for a stream nothing is carrying yet, and never repurpose audio or aux for a *different* stream.
- An ip_input port must be a multiple of four.
- A device can report a refused write as `Invalid username/password` when the credentials are fine and the real reason is something else entirely. Never diagnose from that text; read the device back and report what it actually holds.
- One source cannot feed two windows that need different sizes from the same encoder: that is two writes to one scaler, and the losing window sits silently at the wrong resolution. Detect it within a single Multiview, not only across decoders, and refuse it naming both windows. Two *different* encoders on one source are fine -- that is how a 4K PiP works.
- Preflight a device before spending timeouts on it. An unreachable source cost six WebSocket timeouts in series -- 27.5 seconds measured -- because three config reads were each retried with the fallback password; a 0.6s TCP probe makes it 1.6s. Read independent devices concurrently so one slow source cannot delay the rest.
- A primary action is never withdrawn because the form is incomplete. Disable it, keep it in place, and say what is still missing -- an action that vanishes when the design is nearly right is indistinguishable from one that was never there. Pin the action bar so a panel that grows with the plan cannot push it off screen, and keep a destructive action well away from the primary one.
- While a page is waiting on a request, say so. A placeholder that still reads "assign at least one source" once sources are assigned is not merely unhelpful, it is false, and it is what makes a slow read look like a broken page.
- A Multiview canvas decides the decoder's HDMI output resolution, and setting it belongs to showing rather than saving: it changes the operator's picture. Recover the preset from the canvas *width*, because most layouts snap the height. Never use `input` or `auto` for a Multiview -- the canvas decides, not the incoming signal.
- An output's configured resolution and the resolution the sink negotiated are different facts. Verify the setting; report what the display negotiated, and never fail on it, because it is a property of the attached display rather than of the write.
- One stream maps to one ip_input, within a single plan as well as across plans. A source in several equally-sized windows is legal and ordinary, and giving each window its own input produces a write the decoder refuses. Two windows are the same stream only when the multicast address *and* port agree -- never because they name the same encoder, which transmits several sessions.
- Reclaim a device resource from what is **active**, never from what is merely saved. *(Phase 6 replaced the claim-based model: with many saved Multiviews on one decoder, treating every saved reference as a reservation lets the first one lock the pool against all the others.)* Release a pool input when the configuration being displayed no longer needs it, it carries no other role, and OmniSuite put it there -- and establish that ownership before deleting the object that proves it.
- Cleanup never fails the operation that ran it. A Multiview that saved correctly is still saved if an input could not be released; report the failure rather than swallowing it or rolling the save back.
- SAP manages the decoder's inputs on its own: enabling it repoints an ip_input at the advertised session and it may then disable itself, leaving the output on the generator. A decoder with SAP enabled can have Multiview inputs repointed underneath whatever configured them, which is why showing a Multiview disables SAP first.

### Multiview: the 1080p / Encoder-2 architecture

- The active Multiview product path is **1920x1080 and nothing else**. The planner refuses any other canvas before it looks at a layout, a source or a decoder, so a 4K or 1440p plan cannot be built even by calling it directly. The other geometry, the 4K scaler table and the size-based encoder rule stay in the module as dormant capability; nothing in the active path reaches them, and a test asserts that. Hiding a canvas in CSS while the backend can still plan it is not the same thing.
- Every Multiview window comes from **Encoder 2 and Session 2**. Encoder 1 carries the source's primary stream and other decoders depend on it, so building a window is never a reason to retune it: its scaler is never written at all, and its bitrate only when the source's own budget leaves no alternative. A window size Encoder 2 cannot produce is refused by name, never promoted to Encoder 1.
- A 1080p canvas is what makes that possible: every window of all eleven layouts is a size present in the Encoder-2 scaler table verbatim. The compositor canvas is still snapped to the layout grid (1920x1088, 1920x1104) while the display always runs at 1920x1080 -- two different numbers, and the UI says so in those words.
- **Encoder 2 has to be fed before anything downstream of it means anything.** A device can ship with `vc2_encoder2.input = ""`, which its web application calls "Not used"; the session can then be assigned, enabled and given a destination, and no video is ever produced. Read Encoder 1's input and Encoder 2's: the same means write nothing, Not used means point it at Encoder 1's input and read it back, a different active input is a conflict to report rather than overwrite, and Encoder 1 having no input at all is an error rather than a guess. The write is the first stage of the transaction and is in the snapshot.
- That failure is invisible to the API. With Encoder 2 set to Not used, a controlled A/B measured the same packet rate, the same subframe `video.input.active`, and the same decoder Input status as with it set correctly -- 426 encoder fields compared and only the input field itself differed. The encoder emits its slate, which is a valid stream carrying no picture. Configure the input; there is no field to detect the fault with.
- **Session 2 must not announce itself.** SAP does not adopt a newly announced session -- it applies the session it is *configured* for -- but an announced Session 2 is one an operator or a later change can select, and a Multiview whose windows can be selected out from under it breaks for reasons nobody can trace. Disable it, verify it, and restore it on rollback.
- Multiview window video uses a **reserved decoder input pool**: window 1 to `ip_input2`, 2 to `ip_input4`, 3 to `ip_input6`, 4 to `ip_input8`. The odd inputs are left to the decoder's own roles. Two rules bend the mapping and only two: several windows drawing one stream share the reserved input of the first of them, because a decoder cannot open one address and port twice; and a stream already open on an enabled input outside the pool is refused rather than duplicated.
- **The allocator and the reclaimer must agree about what is owned.** Anything the allocator is willing to take, `reclaimable_inputs` must be willing to give back. An allocator that takes a pool input another Multiview references while the reclaimer refuses to release one leaks an input on every cycle, and quietly puts our stream into somebody else's window.
- Multiview carries no audio, so the display's audio follows the **main window's source over its ordinary Session 1 path**. Never create a second audio stream on Session 2. The main window is the largest window, earliest on a tie -- `main` wherever there is one, Window 1 for 2x2 and Side-by-Side. Applying it belongs to Show, not Save, and an input that is doing anything else is reported rather than repointed.
- Two bandwidth budgets, both **900 Mb/s**: Encoder 1 + Encoder 2 on one source, and the sum of the *unique* streams arriving at one decoder. A source in four windows encodes once and arrives once, so it counts once in both. Give every stream a proven floor first and weight only the remainder by window area; that is what keeps equal windows equal and a large window favoured without starving a small one.
- The device's bitrate limits were measured, not assumed: `vc2_encoder2` accepts 20 to 900 Mb/s and refuses anything outside that with `Invalid bitrate`. It is the one field in this subsystem that rejects a bad value instead of accepting and ignoring it.
- **Video Wall bars Multiview outright, and Fast Switching bars it on a 1xx decoder.** Neither is ever turned off automatically -- both are deliberate operator configuration. Check them before the planner does any work, so a blocked decoder produces a plan with no mutations in it at all. Decide the family from the model identity alone, never an address or a hostname, and refuse a family that cannot be established while the feature is on.
- **A verified write is not a picture.** The decoder's own `video.status` -- its Input status, which reads "No active video" -- is the only check in the transaction that is not a read-back of our own write. Poll it for a bounded settle window after a Show; if it never goes active, capture the entire chain *before* rolling back, and report the operation as a failure however clean every write was.
- A second client on the same server can delete an object the first is relying on, and nothing in the product can prevent that. Re-read device state at the moment of acting rather than trusting what a page believed.

### Multiview: many saved, one active

- A saved Multiview is a **description**, not a reservation. A decoder holds as many as an operator wants and exactly one is on the output, so saving writes the Multiview object and its metadata and nothing else: no encoder, no session, no decoder input, nothing on the HDMI output. Two saved layouts wanting the same Encoder 2 at different sizes is ordinary, not a conflict.
- **Recall is a transition, not a replay.** Read every source again immediately before showing, recompute the whole requirement, and close the difference transactionally. Nothing the previous recall left behind may be assumed to have survived, because another saved Multiview has usually been through since.
- The four pool inputs are **slots**, handed to distinct streams in first-use order, not four fixed window names. Four distinct streams take 2/4/6/8; a layout with repeated sources uses fewer and leaves the rest alone. The result is deterministic from the saved configuration and the sources' current destinations.
- Two windows are the same stream only when the multicast **address and port** agree. An encoder transmits several sessions, so its hostname establishes nothing.
- **An input a plan has already given to one stream is not a candidate for another.** Matching "an input already carrying this stream" against the state as read will, during a recall, match an input the same plan is about to repoint -- which put two different sources on one input and showed one of them twice.
- **One decoder ip_input drives at most two subframes.** Measured: with three or four windows on one stream, exactly two showed and the rest stayed black, with no error, the stream arriving throughout, and the decoder's composite Input status reading active because the other windows carried it. Splitting across two inputs is not a way out -- the hardware will not open one address and port twice -- so refuse the layout and name the windows that would be dark.
- **A verified composite is not a verified window.** The decoder's Input status describes the whole canvas and reads active while one window sits black. Check each subframe after a recall, name any that did not lock, attach the diagnostics -- and do not roll back: the rest of the picture is live, and removing it to punish one dark window helps nobody.
- Bandwidth counts **unique incoming streams**, never windows. Four windows showing one camera are one stream arriving once and one encode at the source. Two encoders misconfigured onto one destination are also one stream, and are charged once.
- One Encoder 2 has one scaler, so one source cannot fill two differently sized windows. The decoder does not refuse it: it composites happily and gives you a full-size "inset" over the main window, because a subframe has no size of its own and takes the resolution arriving on its input. Refuse it in software; the device reports nothing that would let anything else notice.
- Multiview audio is inserted by repointing **the input the display's audio already uses** to the main window's Session 1 audio. On a decoder following the bench convention that is `ip_input3`, verified across four sources; the mechanism rather than the number is what makes it portable. Never the window pool, never Session 2, and an input doing anything else is reported rather than repointed.
- Cleanup follows the **active** configuration. Deleting an inactive Multiview releases nothing, because the enabled inputs belong to whatever is displayed. Deleting the active one moves the output off first, then releases -- and ownership has to be established before the object that proves it is removed.
- A collision is live configuration, not a saved reference. A pool input carrying an HDMI role, or a stream OmniSuite did not configure, is refused; a pool input that another *saved* Multiview merely mentions is free.

### Multiview: the active one is a switching surface

- An inactive Multiview's canvas is a **form**; the active one's canvas is the **display**. The same drag means two different things, so the page states which it is in words on the canvas itself -- never by which buttons happen to be enabled. An absence is not a signal, and a page that distinguishes live from saved only by whether *Show on Display* is offered is asking the operator to infer it.
- A live switch is the **smallest verified transaction that gets there**, not a recall. Plan the whole Multiview as it would be after the change so every rule still applies to the result, then write only what the new source and the changed window's input need. Every window nobody asked about is left untouched, because each one torn down and rebuilt is a visible glitch on a display someone is watching.
- **Verify the windows that did not change.** A switch that lights the window it was asked about while dropping another is invisible unless every window is re-read afterwards. Report a regressed window and an unlocked window separately: they have different causes.
- A verified live switch **becomes the saved preset**; a failed one is rolled back and the preset is left alone. A display showing one thing while its own preset restores another is a trap, and it is the kind that only appears days later.
- **A switch endpoint is for the Multiview on the display.** Refuse one aimed at an inactive object rather than quietly reinterpreting it as an edit -- those are two operations with two different contracts, and conflating them is how a preset silently becomes live.
- The canvas shows the decoder's **live subscriptions**, not the saved assignment. A stream arriving that no discovered encoder claims is shown as an unknown source with its address -- never as an empty window, because something is plainly playing in it. Nothing is arriving is a third state again, and the preset's record is labelled as such.
- Recall reads each source's session **live**, so a re-addressed encoder is subscribed correctly; *naming* the encoder behind a window is answered from the discovery cache, because reading every encoder on page load is the cost the lazy architecture exists to avoid. Between the two, report the window as unknown rather than naming the wrong device, and keep the saved source as the record of intent.
- Source eligibility is **three states, not two**: READY, CONFIGURATION REQUIRED and INELIGIBLE. Collapsing them hides the only one an operator can act on -- an encoder whose Session 2 has no multicast address needs one field set, and omitting it from the list sends someone looking for a network fault. Show it with the reason and refuse to drag it; hide the ineligible ones but say how many and why.
- **An unreachable decoder is not a choice.** One bounded, concurrent TCP probe when the page loads, never on the scan path and never polled. A decoder that answers but is barred by Video Wall or Fast Switching is still offered, disabled, with the reason -- those are switches the operator can turn off and an empty list would never say so.
- Geometry belongs to Save; resources belong to Recall. A subframe added on the device outside OmniSuite survives a recall and is **reported** as a window that never locked, rather than silently deleted -- deleting it would be OmniSuite overruling a change it did not make. Saving over the object is what prunes it.
- Refusing a switch is a **result**, and it has its own contract: refuse it, say why, and leave the decoder byte-identical. An impossible switch that half-applied is worse than one that succeeded.

### Multiview: source preview

- Multiview source previews are **optional, lazy, read-only visual identification aids**. Preview availability does not determine routing eligibility, and preview interaction must never mutate device configuration. A source whose preview is off, broken or unreachable is still a source.
- Reuse the encoder's existing thumbnail. `http://<ip>/thumbnail/thumbnail1.jpg` is published unauthenticated on port 80 and is what the A/V Matrix page has always shown; there is no second preview system to build.
- **The thumbnail belongs to Encoder 1.** `vc2_encoder2` carries no thumbnail fields at all and `thumbnail2.jpg` is a placeholder on every unit measured. That is not a limitation: Encoder 2 takes the same physical input as Encoder 1, so Encoder 1's thumbnail is a picture of what the Multiview window will show.
- **A disabled thumbnail cannot be detected from the image.** With `thumbnail.enable` false the device still answers HTTP 200 with a placeholder JPEG, so an `<img>` load succeeds either way and the browser cannot tell "off" from "showing something". Read `vc2_encoder1.thumbnail.enable` to distinguish them, and never enable it because somebody hovered.
- Preview work starts on hover and stops when hover ends: a debounce before anything is requested, cancellation when the pointer leaves first, and the image dropped -- not merely hidden -- on close. Nothing is fetched on page load, and an idle page makes no preview traffic at all. No repeating timer; one frame per hover.
- A preview must never get in the way of the thing it is helping with. The card takes no pointer events, a drag dismisses it before anything else happens, and it is repositioned to stay inside the viewport rather than clipped.

### Multiview: shared state, and writing only differences

- **READ -> PLAN -> DIFF -> WRITE ONLY DIFFERENCES -> VERIFY.** Every Multiview transaction reads what the devices hold, plans what they should hold, and writes only the fields that differ. A field that is already correct is not written again. This applies to Encoder 1 bitrate, Encoder 2 bitrate, Encoder 2 input, Encoder 2 scaler, Session 2's encoder assignment, enable, destination and SAP, the decoder's inputs, and the audio subscription.
- **Encoder configuration is shared state.** Encoder 1 may be feeding decoders that have nothing to do with Multiview. A `config_set` is an instruction the device acts on, not a comparison it makes — measured on the bench, writing an encoder's `session1.video.stream` blacks every decoder watching it for about half a second **even when the value written is the one it already had**.
- **Calculating a bitrate is not writing one.** The planner recalculates bandwidth on every Show, recall and switch, and both 900 Mb/s limits are still enforced; the result is compared to the live value and written only if it differs. A fully prepared source costs zero encoder writes.
- **Judge a group of mutations by the state they jointly ask for, not one at a time.** The decoder-input pair "disable ip_inputN while its source changes" then "point ip_inputN at X and enable it" must be kept or dropped together: evaluated separately, the disable looks necessary and the enable looks redundant, which leaves the input switched off with nothing to switch it back on.
- Any transaction should be able to report **planned fields / already correct / writes required / writes performed**. If those numbers are not available, the transaction cannot be reasoned about.

### Multiview: operator notice

- The Multiview page presents an operator notice when it is newly opened, unless the operator has suppressed it **for the running application version**. The suppression key records the version, never a boolean: a release that changes what Multiview touches shows the notice again by itself.
- The notice says what Multiview may configure — Encoder 2's input and scaler, Session 2 and its SAP announcement, decoder subscriptions, the reserved inputs, the display's audio path, and bitrates where the budget forces it — and what OmniSuite will never do on its own: disable Video Wall, disable Fast Switching, touch an encoder because a page was opened, or enable preview generation.
- It is raised once from page start, never from `render()`, and it costs zero requests and zero device mutations. Copy and Save produce the same complete plain text, carrying the version and a timestamp and no credential.

### Multiview: previews in the windows

- A populated Multiview window whose source has preview enabled shows **the encoder's own thumbnail**, with the source information as a readable overlay over it. The picture is the point; the text sits on a scrim so it survives bright and dark video alike.
- **Never display the device's placeholder JPEG.** A disabled thumbnail still answers HTTP 200 with a placeholder, so a window whose source reports `disabled` keeps its ordinary appearance and says "Preview disabled" quietly. Preview availability never determines routing eligibility, and preview is never enabled automatically.
- Visible in-window thumbnails refresh about every five seconds. This is the **one** repeating timer the Multiview page is permitted, and it supersedes the earlier "no preview timer at all" rule for this case only. One timer for the page, one image per **unique** visible source, cache-busted, and stopped when the canvas leaves the screen, the page is hidden, or nothing visible has a preview.

### Multiview: fewer dialogs, same transaction

- **An active Multiview's canvas already says it is LIVE, so a drop on it applies immediately with no confirmation.** Removing the dialog removes none of the safety: the whole Multiview is still replanned, every rule still applies, every write is still read back, and a switch that cannot be verified is rolled back and the preset left alone.
- **Show on Display has no confirmation either.** Choosing a Multiview and pressing Show is the intent.
- Showing a Multiview that is already active and already correct is a **verified no-op**, not a conflict: it returns success having written nothing.
- The page does not offer Show for a Multiview this release cannot display. The server reports `showable` and a reason with each saved Multiview, computed by the same rule the Show endpoint applies, so there is one copy of the rule and the operator gets a sentence instead of a console 409.

### Multiview: Encoder 2 bitrate policy

- Encoder 2 targets are **stated by window role, not derived from window area**. Equal-sized windows get **200 Mb/s**, the largest window in a mixed layout gets **300**, the smaller ones get **150**. Roles come from the geometry, so a layout never has to be listed by name and two windows of one size can never be given different targets.
- **What reaches the device is `min(layout target, 900 - current Encoder 1 bitrate)`.** The target is what the layout asks for; the actual is what the source can spare.
- **Encoder 1 is read and never written.** It carries the source's primary stream and other decoders are watching it — Phase 7B measured that changing its bitrate blacks every one of them for about half a second. A Multiview window at 150 instead of 200 is a far smaller price, so a source with little headroom simply gets a smaller Multiview stream. A source with less than 20 Mb/s spare is refused by name rather than having its primary feed trimmed to fit.
- **900 is a ceiling, not a target.** The heaviest layout (2x2) asks for 800 and 1+3 asks for 750. Do not consume the remainder because it is there.
- Diagnostics must distinguish **target** from **actual** and say which of the two constraints produced the number. "150 Mb/s, target 200, Encoder 1 is at 750" is actionable; "150 rather than the 450 its window size would give it" is not, and was the old wording.
- Bandwidth is still counted per **unique stream**: one source in two windows is one encode and one arrival, charged once.

### Multiview: remembering the operator's selection

- The page remembers the **selected decoder and the selected Multiview**, and nothing else. Two identifiers in browser storage; no device configuration is cached and no credential is stored, and no server or device write happens to remember a view.
- On open, live state is loaded first and the stored names are matched against it. A remembered decoder that is offline, undiscovered or no longer Multiview-capable is **dropped**, not presented as a working selection. A remembered Multiview that has been deleted clears only itself and leaves the decoder in place. Neither is an error.
- The canvas is still populated from the decoder's **current live subscriptions**. The stored value is a bookmark, never a substitute for reading the devices.

### Multiview and the A/V Matrix

- **A decoder showing a Multiview must not be drawn with a video crosspoint.** Its `ip_input1` still holds whatever it last watched, so the matrix would tick a route describing a picture nobody is looking at. The row says MULTIVIEW instead, and says why on hover.
- The **audio** crosspoint on that row stays. Multiview sound comes from one source over its ordinary audio path, so it is true, and hiding it would be the lie.
- Whether a decoder is compositing is decided **only from what its display is currently selecting**. Saved Multiview objects, a remembered page selection and OmniSuite's own metadata are not evidence: none of them says what is on screen now.
- This is derived from a field the ordinary scan already collects. **Do not add Multiview discovery or configuration reads to the scan hot path** to answer it.
- **Normal routing to such a decoder stays available.** It warns first, once per browser session, with a "do not show this again" option; the acknowledgement is session-scoped so a refresh or a trip to another page does not ask again. Cancel performs **zero** device mutations.
- The operator's answer **travels with the route request**. A request that does not carry it is refused, so no caller can take a display off a composition silently.
- Leaving Multiview is one transaction — move the display, release the pool inputs that Multiview owned, then route — and each step is verified. If the route fails, restore the Multiview and say whether the restoration was verified.
- **Exiting a Multiview never deletes it.** The saved object stays and can be shown again.
- The matrix redraws from the route response, and showing a Multiview records the decoder's new display state. **Do not add polling** to keep the matrix honest, and never show the intended state in place of the verified one.

### Multiview: what the decoder is showing outranks what we remembered

- Choosing a decoder opens the Multiview **currently on its display**, populated from live subscriptions. A remembered selection is a bookmark and is used only when nothing is active. Metadata and saved objects never outrank the display.
- A composition that cannot be matched to a saved layout is still shown as what it is. Never substitute an unrelated preset because the live one did not resolve.

### Multiview: creating is not editing

- **+ New Multiview** enters a separate creation state with its own heading, and leaves the selected Multiview untouched. Cancel returns to it exactly as it was. Creating never changes what is on the display.
- The editing area says which of the two is happening and whether there are unsaved changes.

### Multiview: Save means there is something to save

- **Save is disabled unless the persistable preset differs from what is stored** -- name, layout, window assignments. Live readings (packets, health, reachability, previews, engineering figures) must never enable it.
- After a successful save the baseline becomes what is now on the device and Save disables. After a failure the operator's edits are **kept** and Save stays available: a failed transaction changed nothing, so reverting their work would be a second failure.

### Multiview: version-scoped warning suppression

- "Do not ask again" on the Save confirmation records the **application version** it was given against, through the shared `omniSuppression` store. A different version shows the warning again by itself.
- The reason is consent, not tidiness: a warning describes what an operation does to shared equipment, and a release can change that. Never require the operator to clear browser storage, and never let one warning's preference touch another's.

### Multiview: copying to another decoder

- A copy carries the **definition** -- layout, and which source is in which window. It carries no resources: ip_input numbers, scaler sizes and bitrates belong to one decoder at one moment and are worked out again at Show.
- Copying **saves**; it does not show. Neither display changes.
- Never silently overwrite a Multiview of the same name on the target. Ask, or create a distinct name.
- A source that is offline is a **warning**, not a refusal: the window keeps its source. A saved Multiview names sources, it does not hold them.

### Multiview: decoder groups

- A group is decoders meant to show the same Multiview. Membership **persists** in OmniSuite's runtime state and stores no credentials. Members are recorded by MAC where one is known, so a reassigned address does not silently change which television is in the group.
- **Copy to group and Show on group are different operations.** Copying changes no picture; showing changes all of them and asks first.
- **Plan the whole group before writing anything.** Window geometry decides what a source's Encoder 2 must scale to, and one Encoder 2 produces one size -- so the same source at two window sizes across the group is impossible, however reasonable each decoder looks alone. Refuse the whole operation, name the source and the decoders that disagree, and write nothing.
- Decoders that share a source subscribe to the **same** Encoder 2 stream. Charge that source's bandwidth once; evaluate each decoder's input bandwidth separately. Never allocate a second multicast because a second decoder wants the same picture.
- Audio is per decoder: each one repoints its own display audio input at the main window's Session 1 audio. Do not assume every decoder uses the same input number.
- Show on group snapshots, applies in order, verifies each member, and on failure restores every member already changed -- reporting `VERIFIED`, `FAILED — GROUP ROLLED BACK` or `FAILED — GROUP ROLLBACK INCOMPLETE`. Never report success with half the room on the old layout. These are independent network endpoints: say that the atomicity is best effort rather than implying it is not.
- Group-wide live changes happen **only while a visible group context is shown**. A drag that moves six displays must never look like a drag that moves one.
- Drift is **reported, never corrected**. A grouped decoder routed away from the A/V Matrix is DRIFTED, and only the decoder the operator routed leaves Multiview. Do not poll to discover this; read on the actions the operator already takes.

### Warnings have two lifetimes, and both are version-scoped

- **Acknowledging a warning is not the same as suppressing it.** Pressing Continue means "I have read it" and lasts the browser session, surviving a refresh and navigation. Ticking "do not show again" is a preference and lasts the application version. Two records, two keys; neither overwrites the other, and neither touches any other warning's preference.
- Both record the VERSION they were given against, through the shared `omniSuppression` store. A release that changes what an operation does asks again by itself. Never require the operator to clear browser storage.
- A missing preference store, or storage that throws, means **show the warning**. Absence of a record is not consent.

### Operator documentation states what the code does

- The Multiview notice claimed OmniSuite might "adjust Encoder 1 or Encoder 2 bit rates" long after Phase 7C made Encoder 1 read-only. **When a policy changes, the operator-facing text that describes it changes in the same build**, and the promise belongs in the list of things OmniSuite will never do.

### A group is a target, not a second interface

- Persisted groups appear in the same selector as the decoders, in their own labelled section, and are never presented as pseudo-devices. Selecting one scopes everything that follows: the same canvas, layouts, sources, previews, drag and drop, Save and Show.
- The scope is stated, not implied: a visible context bar, and button labels that say how many displays an action changes.
- A group's own state comes from reading every member. One member's live state is **never** reported as the group's.
- The Groups panel is membership management. Choosing what a group shows, and changing it, belongs in the ordinary workflow.

### A stylesheet must not ask for a colour nobody defines

- `var(--x)` with no definition and no fallback resolves to nothing, silently. A dialog whose surface did that had no background at all and the canvas read through its text. Tokens used are checked against tokens defined, and a dialog surface is one of the opaque theme surfaces.

### Reads may be retried. Writes may not, until the device is asked.

- A read changed nothing and opens its own connection, so a transient failure is safe to repeat: **three attempts, short pauses, then a clear "did not answer" naming the unit**. Never a loop, never a background poll.
- A **`config_set` whose reply was lost may have arrived**. Read the device first. If it already holds the wanted value the step is done; only when the read proves otherwise is one retry allowed, and only because writing the same value twice is the same value.
- A **`method` is never repeated.** `add_multiview` twice is two objects. A lost reply is answered by reading the device.
- **A device that answered is finished with, whatever it said.** An error in a reply is a decision, not a miss, and deterministic refusals -- Video Wall, Fast Switching, an ineligible source, a scaler conflict, a bandwidth refusal -- fail fast before any write. Retrying them only delays the sentence the operator needs.

### "Did not answer" is not "invalid request"

- A device that was not there is **503** with the unit named and the attempt count; a live conflict is **409**; a request that cannot be built is **400**. The page shows the structured reason, never the status word.

### A configuration that matches is not a picture on the screen

- Measured on the bench: after some transitions a window stays black although every field reads correct and its packets are arriving. Write-minimality then made the operator's natural remedy -- show it again -- a guaranteed no-op, because there was nothing to write.
- So a window seen failing to lock is **remembered, and re-established on the next attempt** rather than skipped. Narrowly: only that window's decoder input, only on the decoder, and forgotten as soon as it locks.

### The hardware fence belongs to the tests

- **The fence lives in `tests/_fence.py` and is installed by the test package**, so it is present through `run_tests.py`, `python -m unittest`, discovery, an IDE and any mutation harness. It must never again depend on one runner remembering to install it.
- It refuses WebSocket, TCP and UDP to anything that is not loopback, and therefore everything layered on them -- HTTP, thumbnails and the `config_get` / `config_set` / `method` transports. Loopback stays open because a test talking to 127.0.0.1 is talking to itself.
- `tests/test_fence.py` proves it, including by running the suite in a fresh interpreter through `python -m unittest` with no help from `run_tests.py`.
- A test must not write a bench address into a test file, as a target or otherwise; use a documentation range, or RFC 2544's 198.18.0.0/15 when a routable-looking address is needed.

### The display output is a display

- **The drop target is drawn as a small 16:9 screen**, not a status card, because it answers "what is on the display" and an operator should read that at a glance. It is deliberately small: it is not a second canvas.
- **Each state gets its own picture, and none of them may claim more than the decoder said.** A conventional source shows that encoder's existing thumbnail. A Multiview is drawn as its own window geometry, taken from the *shown* Multiview's subframes — never from the layout that happens to be open in the editor, which is routinely a different preset. Nothing on the display is drawn as an off screen.
- **The picture is informational; the state is not.** `display_output` comes from decoder readback, and a thumbnail that is disabled, unavailable or slow changes none of it. A stale picture must never survive into a state that no longer has one.
- **The thumbnail is asked for once per source, on demand.** The canvas refresh timer is driven by `windowPreview.visible`, and the display output deliberately never joins it. An idle page makes no periodic device request, and that is measured, not assumed.

### The Multiview notice is a configuration disclosure

- **It is a factual description of what the code does, not a warning.** Every claim in it is measured behaviour. If the behaviour changes, the notice changes with it, and `multiview_ui_test.js` fails until it does.
- **It leads with when anything happens at all**, because after Phase 8F most of it does not: designing, installing, copying and saving a preset configure nothing.
- **It must state, and the tests hold:** Encoder 2 / Session 2 carries Multiview video; Encoder 1 is read and never lowered; one 900 Mb/s budget with a 20 Mb/s minimum for Encoder 2; audio follows the main window over Session 1; the reserved decoder inputs, with only the needed ones used; a 1920x1080 output; the Encoder 2 scaler belongs to the source and is shared; and a preset reserves nothing.
- **It must not tell the operator to invent a multicast address.** OmniSuite uses the address the encoder already has and never generates one; encoders generate their own. An encoder with none is *Configuration required*.
- **Assertions about it are structural where the wording is load-bearing.** Matching a phrase anywhere in the flattened notice passes even when the specific promise has been removed, because similar wording appears elsewhere. Read the entry, not the blob — and keep the tests indifferent to punctuation and ordinary copy edits.

### A layout is not a preset is not an execution

Four things, and collapsing any two of them is how a saved layout comes to change somebody's picture:

| | is | owns |
|---|---|---|
| **Layout** | geometry: how many windows, where, what size | nothing |
| **Preset** | a layout plus zero or more desired source assignments | nothing |
| **Active Multiview** | a preset reconciled against the hardware as it is *now* | encoders, sessions, decoder inputs |
| **Display output** | what the decoder is actually showing | the screen |

- **Creating, installing, copying, editing or saving an inactive preset must never configure Encoder 2.** Not its input, not its scaler, not its bitrate, not Session 2, not the Session 2 multicast, not SAP, not a decoder `ip_input`, not the HDMI output. Those are execution resources and they are prepared at Show and nowhere else. `_build_mutations` returns `save` and `activation` separately for exactly this reason, and `PresetVersusExecutionTests` is a durable fence around it: a regression here breaks a picture on somebody else's screen.
- **A preset does not have to be executable at the moment it is saved.** A source may be offline, may have no Session 2 destination, may have no second encoder at all. Save asks the preset question (`preset_ok`); Show asks the execution question (`ok`). The offline assignment stays in the record, because it is what the operator asked for.
- **What is structural stays structural.** A layout this release does not run, an illegal name, one source needed at two sizes in the same Multiview, one stream asked to fill three windows — none of those can ever execute whatever the hardware does, so they refuse the preset too. The test is "could any state of the world make this work", not "is it inconvenient".
- **Show-time planning is not weakened by any of this.** Reachability, Encoder 2 input, headroom, scaler, Session 2, SAP, duplicate sharing, the two-subframes-per-stream limit, the decoder input pool, both 900 Mb/s budgets, Video Wall, Fast Switching and cross-decoder conflicts are all still evaluated against live state when Show is pressed. A saved configuration is not evidence that activation is possible.

### An empty window is a window

- **The decoder represents an unassigned window as a real subframe with `input: ""`.** Measured on the bench: the write is accepted, the empty input is preserved on readback, an object made entirely of them composites, and the decoder's output stays active at 1920x1080 with every window reporting "not subscribed". OmniSuite therefore stores layouts the way the hardware does (Model A) rather than inventing a virtual representation.
- **An empty window consumes nothing** — no Encoder 2 stream, no Session 2, no multicast, no bandwidth, no decoder input. Show prepares only the unique sources actually assigned.
- **A Multiview with empty windows is still LIVE when it is on the display.** Emptiness is a property of a window, not of the composition, and calling the whole thing INACTIVE because three of four windows are empty would be false.
- **The decoder reports a rejected method call as "Invalid username/password" whatever the real reason.** An illegal object name produces it, and so does a malformed `add_multiview`. Never read that message as an authentication problem without checking the request first.
- **One Encoder 2 has one scaler.** Two decoders may share a source when they want the same size, and a decoder asking for a different size is refused before any write, naming the source, both sizes and the decoder already using it. The already-active decoder is never changed to accommodate the one asking.

### Display output and the canvas answer different questions

- **The Display Output panel says what the decoder is putting on its screen. The canvas says whether the composition you have open is that thing.** They are not two views of one fact, and a page that merges them will tell an operator that a preset they are editing is on air.
- **Display Output is derived from the decoder's own `hdmi_output.video.input`, every time state is read, and from nothing else.** Not the remembered selection, not the saved object, not the last button pressed. It is carried on `/api/multiview/state`, computed from the same three reads that endpoint already performs: no extra request and no timer.
- **LIVE is a claim about the device.** An input that is selected but carrying nothing is `NO VIDEO`, not LIVE. A stream that is arriving but belongs to no discovered encoder is still on the screen and is reported as such, with no source named.
- **A drop on Display Output is the ordinary A/V Matrix route** — `POST /api/route` with `exit_multiview` — and nothing about the teardown, the route, the verification or the rollback is implemented a second time on the Multiview page.
- **A conventional route carries Session 1 video and Session 1 audio.** Never the Encoder 2 / Session 2 stream a Multiview window uses, however conveniently it happens to be running already. Encoder 2 is not prepared, scaled, re-rated or enabled for a conventional route.
- **Multiview eligibility and normal-route eligibility are different questions, and the page holds neither rule.** A window needs Encoder 2, a Session 2 multicast and enough of the 900 Mb/s budget for a second stream; an ordinary route needs the Session 1 stream that is already running. A source can legitimately be CONFIGURATION REQUIRED for Multiview and a perfectly good conventional source, and refusing to route it would be a defect. The server answers both, per source.
- **A group's Display Output is status only.** One drag must never route a room full of displays. Group-wide conventional routing is the A/V Matrix's job, and this phase did not invent one.

### A derived field must not outlive what it was derived from

- **If a value is computed from another, recompute it wherever that other value can change — do not carry the old answer forward.** `/api/state` derives `multiview_active` from the cached `video_input` and then overlays the live `video_input` on top of it. Measured on the bench: one record said `video_input` was a Multiview and `multiview_active` was false, at the same time. A decoder cannot be both.
- **Anything that changes what a decoder displays must record it**, so the A/V Matrix does not go on showing a state the device has left. That includes the rollback path: `_restore_multiview_after_failed_route` puts the Multiview back, and has to say so, or the matrix keeps showing a conventional route that was undone.
- **`omni_matrix_logic._decoders` and `_encoders` are process-global and `/api/state` overlays them onto the cache.** Tests must isolate them, for the same reason background work must not outlive its test: otherwise one test's decoder decides what another test's matrix row says.

### Background work must not outlive the test that started it

- **A test owns everything it sets in motion.** If a test starts, schedules or arms work that runs on another thread, that test waits for it to finish before it ends. Work that lands later lands inside somebody else's test, against somebody else's stubs, and is reported as their failure.
- This is not a theoretical hazard. `start_background_startup_tasks()` starts three plain daemon threads; `_startup_usb_refresh` waited two seconds and then called `_refresh_icron_network_config` **through the module global**, so it resolved against whichever test was running at that moment. It arrived inside `MatrixNetworkReadinessTests` and recorded an Icron read that test never made. Aiming the fire at the target reproduced the failure in 7 of 30 attempts; in a full suite it was a rare flake whose cause was two seconds and several hundred tests away.
- **Patching an executor is not enough.** `inline_background()` reaches executor submissions only. A bare `threading.Thread`, a `threading.Timer` or an `atexit` hook is invisible to it. Production code that starts a thread must record it somewhere a caller can wait on — `_startup_threads` / `await_startup_tasks()` is the pattern — so shutdown and tests both have a way to let the work finish.
- **`ServerTestBase` enforces this rather than documenting it.** The wait is registered last in `setUp`, so it runs first in teardown, with the test's own stubs still installed: the work both belongs to that test and is observable by it. If anything is still running, the test that armed it fails. `StartupTaskIsolationTests` proves the contract, including that a deliberate leak is reported.
- **Never fix a flake by waiting longer, retrying the assertion, reordering the suite, or excluding the test.** Find what crosses the boundary. A timing fix hides the leak and leaves the same work landing somewhere else.
- A test-only delay is not a test-only knob for hiding a race. `STARTUP_USB_SETTLE` exists so the suite waits for the *work* instead of for a settle it has nothing to settle; the wait for the work is what makes it deterministic.

### The User Guide is part of the build

- **The Settings-page User Guide (`ui/user-guide.html`) is a release artefact, not documentation that trails behind the code.** It ships inside the application, it is what an operator reads, and a build whose guide describes behaviour the build does not have is a defect in that build.
- **Every build or checkpoint intended for publication must include a review of the User Guide.** If user-visible behaviour changed, the guide is updated *in the same build*, before it is considered ready to publish. Do not defer it to a later pass and do not rely on anyone remembering to ask.
- If a build genuinely needs no documentation change, say so explicitly in its report — **"User Guide reviewed — no update required."** Silence is not a review.
- Write it for an operator. What the feature does, how to use it, what the messages mean. Not function names, routes, protocol verbs, internal algorithms, test architecture or phase numbers — none of those help the person reading it, and all of them age badly.
- The guide states the version it was built from, substituted at serve time. Never hand-type a version into it.
- Screenshots must match the controls the build actually has. A screenshot that contradicts the current workflow is worse than none. Bench addresses, MAC addresses, serial numbers and hostnames are acceptable in it; credentials, tokens and secrets never are.
