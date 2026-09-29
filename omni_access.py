"""Route classification and the authorization primitives behind remote access.

The point of this module is that **a route with no classification is denied**.

V1.1.0 has no authentication at all: all 112 routes are anonymous, and the only
thing that makes that safe is `app_launcher.py` binding 127.0.0.1. Enabling
remote access removes that one control from every route at once, so the gate
cannot be a decorator applied to the routes someone remembered to decorate --
`/api/list_dir` enumerates the host filesystem and `/api/config` hands back the
device passwords, and neither of those is a route anyone would think to protect
while writing a routing UI.

So the table below is exhaustive and the fence in tests/test_remote_access.py
asserts it stays that way: every rule Flask has registered must appear here, and
anything unclassified is both a test failure and, at runtime, a denial.

Nothing here decides policy on its own. `classify()` answers "who may call
this", and the caller enforces it.
"""
from __future__ import annotations

from enum import Enum


class Access(Enum):
    """Who may call a route.

    Ordered from most open to most restricted so a comparison reads naturally,
    but the gate matches exactly rather than by ordering -- a USER route is not
    "more open" than an ADMIN one, it is a different audience.
    """

    #: No session required: login, health, and the assets a login page needs.
    PUBLIC = "public"
    #: Any authenticated principal, whatever their role.
    AUTHENTICATED = "authenticated"
    #: Authenticated, role=admin.
    ADMIN = "admin"
    #: Admin, and additionally only from the loopback listener. These act on the
    #: host computer rather than on AV devices -- browsing its filesystem,
    #: opening a window on its desktop, or driving the bench diagnostics -- so
    #: they are not reachable from the network even with an Admin session.
    LOCAL_ONLY = "local_only"
    #: Reachable by a restricted routing User, subject to a capability and to
    #: per-device permission checks performed by the caller.
    USER = "user"


#: Capability ids. Strings rather than three booleans so another route-capable
#: family can be represented later without reshaping the permission store.
FEATURE_AV = "av"
FEATURE_USB = "usb"
FEATURE_MULTIVIEW = "multiview"

FEATURES = (FEATURE_AV, FEATURE_USB, FEATURE_MULTIVIEW)


#: (access, capability) for every registered rule. The capability is None where
#: the route is not a routing-User route.
#:
#: Read the USER entries as the whole of what a restricted account can reach:
#: twelve of 112. Everything else is Admin or Admin-and-local.
ROUTES: dict[str, tuple[Access, str | None]] = {
    # ---- public -------------------------------------------------------
    "/__health": (Access.PUBLIC, None),
    "/license": (Access.PUBLIC, None),
    "/static/<path:filename>": (Access.PUBLIC, None),
    "/ui/<path:filename>": (Access.PUBLIC, None),
    # Every browser asks for this before it has a session, and an icon says
    # nothing about the installation. Public so the sign-in page loads without
    # a failed request, and it serves one file from the build by a fixed path
    # -- there is no parameter here for anything to be smuggled through.
    "/favicon.ico": (Access.PUBLIC, None),
    # The remote login page and the credential check behind it.
    "/login": (Access.PUBLIC, None),
    "/api/auth/login": (Access.PUBLIC, None),
    # A Direct Access link. Public because the token in the path IS the
    # credential -- there is nothing else to present, and requiring a session
    # to redeem it would defeat the feature entirely. The handler refuses every
    # token it cannot resolve to an enabled, non-administrator account, and the
    # session it opens is marked as link-established and barred from the
    # administrative surface by the gate.
    "/access/<token>": (Access.PUBLIC, None),

    # ---- authentication state -----------------------------------------
    "/api/auth/logout": (Access.AUTHENTICATED, None),
    "/api/auth/session": (Access.AUTHENTICATED, None),

    # ---- any authenticated principal ----------------------------------
    # The guide is operator documentation and carries no device state.
    "/help": (Access.AUTHENTICATED, None),
    # Appearance is a per-browser convenience, not a permission.
    # Readable by anyone signed in, because the User Guide is offered at
    # /help and it reads the theme. Writing is administrative: the file is one
    # per installation, so a restricted account saving a preference would be
    # changing the local operator's interface. See METHOD_ROUTES.
    "/api/ui_preferences": (Access.AUTHENTICATED, None),
    "/api/ui_preferences/reset": (Access.ADMIN, None),
    # The update alert and its acknowledgement. Only Settings uses these, and
    # notice_ack.json is likewise one file for the installation -- a restricted
    # account must not be able to dismiss an administrator's alert.
    "/api/notices": (Access.ADMIN, None),
    "/api/notices/ack": (Access.ADMIN, None),

    # ---- the restricted user's own surface ----------------------------
    # The shell and the projection behind it. AUTHENTICATED rather than USER
    # because an administrator opens the same pages to review them, and the
    # projection scopes its answer to whoever is asking rather than relying on
    # the route class to do it.
    "/user": (Access.AUTHENTICATED, None),
    "/api/user/state": (Access.AUTHENTICATED, None),
    # The caller's own Direct Access link, and nobody else's. It takes no user
    # id, so there is no account to aim it at; AUTHENTICATED because an
    # administrator opening the same page gets the same answer about
    # themselves, which is that they have none.
    "/api/user/direct_access": (Access.AUTHENTICATED, None),

    # A/V
    "/api/user/route": (Access.USER, FEATURE_AV),
    # Putting a permitted display into the verified unrouted state. Same
    # feature and same destination check as routing to it.
    "/api/user/unroute": (Access.USER, FEATURE_AV),
    # A still picture of one permitted source, addressed by stable identity.
    # USER rather than AUTHENTICATED because it reaches a device, and the
    # handler checks the account's own source grant on top of the capability --
    # a source this account may not use has no preview, whatever its card said.
    "/api/user/source_preview": (Access.USER, FEATURE_AV),
    # USB. The operator-facing words are computer and peripherals, because a
    # LEX sits with the host and a REX with the peripherals; forcing
    # source-and-destination onto that would misdescribe the hardware.
    "/api/user/usb_state": (Access.USER, FEATURE_USB),
    "/api/user/usb_route": (Access.USER, FEATURE_USB),
    # Multiview operation. Showing an approved preset and switching a source
    # within it; creating, editing, copying, deleting, installing layouts and
    # group administration all stay administrative.
    "/api/user/multiview_state": (Access.USER, FEATURE_MULTIVIEW),
    "/api/user/multiview_show": (Access.USER, FEATURE_MULTIVIEW),
    "/api/user/multiview_switch": (Access.USER, FEATURE_MULTIVIEW),

    # ---- administration of accounts -----------------------------------
    "/admin/users": (Access.ADMIN, None),
    "/api/admin/users": (Access.ADMIN, None),
    "/api/admin/users/<user_id>": (Access.ADMIN, None),
    # Turning a Direct Access link on, off, or replacing it, and reading the
    # current one to hand to somebody. Administrative in both directions: a
    # restricted account may read its own link at /api/user/direct_access and
    # may never create one.
    "/api/admin/users/<user_id>/direct_access": (Access.ADMIN, None),
    "/api/admin/devices": (Access.ADMIN, None),
    # Credential provenance by name -- which of the configured, per-device or
    # fallback credential each unit is actually answering to. Never values.
    "/api/admin/credentials": (Access.ADMIN, None),
    # Friendly device names. Administrative because naming equipment is an
    # administrator's job, and because the response carries the hostname,
    # model and address a restricted account is deliberately never given.
    "/api/admin/aliases": (Access.ADMIN, None),
    # Saved Multiviews on one display, for approving them to an account.
    "/api/admin/multiviews": (Access.ADMIN, None),
    # Certificate management. Administrative, and the handler never
    # returns key material -- only what the certificate says about
    # itself.
    "/api/admin/certificate": (Access.ADMIN, None),
    "/api/admin/audit": (Access.ADMIN, None),
    "/api/remote_access": (Access.ADMIN, None),

    # ---- first-run bootstrap, local host only -------------------------
    # Creating the first administrator is reachable from the machine running
    # OmniSuite and from nowhere else, so a remote client can never make
    # itself one.
    "/api/bootstrap/state": (Access.LOCAL_ONLY, None),
    "/api/bootstrap/admin": (Access.LOCAL_ONLY, None),

    # Configuration backup and disaster restore. LOCAL_ONLY, and that is a
    # security decision rather than a convenience one.
    #
    # The backup contains the device credentials, which `/api/config` already
    # refuses to hand to anything but the local host -- an export reachable
    # remotely would be a second, quieter way to the same secret. Restore is
    # local-only for a second reason as well: the machine's own operator is
    # trusted by the socket rather than by a password, which is what lets a
    # technician who never knew the old administrator's password rebuild a
    # deployment at all. A remote administrator, a restricted User and a
    # Direct Access panel can reach none of these.
    "/api/backup/export": (Access.LOCAL_ONLY, None),
    "/api/backup/inspect": (Access.LOCAL_ONLY, None),
    "/api/backup/restore": (Access.LOCAL_ONLY, None),

    # ---- restricted routing User --------------------------------------
    #
    # Being allowed to CALL an endpoint is not the same as being allowed to SEE
    # everything it returns, and the administrative endpoints were classified
    # here on the first reading of the brief. Measured, each hands a restricted
    # account the administrative object in full:
    #
    #     /api/state               12,087 bytes  serials, multicast, video wall,
    #                                            HDCP, firmware, ip1_addr
    #     /api/usb_state           12,938 bytes  MACs, USB firmware
    #     /api/multiview/decoders   1,350 bytes  MACs
    #     /api/multiview/sources    1,757 bytes  MACs
    #
    # The gate cannot fix that: it decides whether a request proceeds, not what
    # the handler then returns. So the surface is the two endpoints that were
    # written for a restricted account and answer with a projection built from
    # named fields. Everything else is administrative until a projected
    # equivalent exists for it.
    #
    # Phases H and I added the USB and Multiview operating endpoints the same
    # way -- projected, not merely permitted -- and they are declared with the
    # rest of the user surface above rather than a second time here.

    # ---- Admin, and only from the loopback listener --------------------
    # These act on the host computer, not on AV devices.
    "/api/list_dir": (Access.LOCAL_ONLY, None),
    "/api/files": (Access.LOCAL_ONLY, None),
    "/api/open_firmware_folder": (Access.LOCAL_ONLY, None),
    "/api/debug/latest_upload": (Access.LOCAL_ONLY, None),
    "/api/cleanup_uploads": (Access.LOCAL_ONLY, None),
    # Bench hardware-validation endpoints. AGENTS.md already requires these sit
    # behind an explicit diagnostic path with a confirmation token; they must
    # not additionally become remotely reachable.
    "/api/diagnostics/usb_endpoints": (Access.LOCAL_ONLY, None),
    "/api/diagnostics/usb_route/state": (Access.LOCAL_ONLY, None),
    "/api/diagnostics/usb_route/pair": (Access.LOCAL_ONLY, None),
    "/api/diagnostics/usb_route/unpair": (Access.LOCAL_ONLY, None),

    # ---- Admin ---------------------------------------------------------
    "/": (Access.ADMIN, None),
    "/matrix": (Access.ADMIN, None),
    "/matrix/configure": (Access.ADMIN, None),
    "/matrix/multiview": (Access.ADMIN, None),
    "/matrix/usb": (Access.ADMIN, None),

    "/api/adapters": (Access.ADMIN, None),
    "/api/blink": (Access.ADMIN, None),
    # Administrative state. A restricted account reaches the projected
    # equivalents instead; see the note on the User surface above.
    "/api/state": (Access.ADMIN, None),
    "/api/route": (Access.ADMIN, None),
    "/api/unroute": (Access.ADMIN, None),
    "/api/usb_state": (Access.ADMIN, None),
    "/api/usb_route/resolve": (Access.ADMIN, None),
    "/api/usb_route/pair": (Access.ADMIN, None),
    "/api/usb_route/unpair": (Access.ADMIN, None),
    "/api/multiview/state": (Access.ADMIN, None),
    "/api/multiview/decoders": (Access.ADMIN, None),
    "/api/multiview/sources": (Access.ADMIN, None),
    "/api/multiview/preview": (Access.ADMIN, None),
    "/api/multiview/show": (Access.ADMIN, None),
    "/api/multiview/switch": (Access.ADMIN, None),
    "/api/cache": (Access.ADMIN, None),
    "/api/clear": (Access.ADMIN, None),
    "/api/clear_units": (Access.ADMIN, None),
    "/api/codec": (Access.ADMIN, None),
    "/api/config": (Access.ADMIN, None),
    "/api/decoder_input": (Access.ADMIN, None),
    "/api/device_log": (Access.ADMIN, None),
    "/api/download_csv": (Access.ADMIN, None),
    "/api/download_inventory": (Access.ADMIN, None),
    "/api/encoder_encoding": (Access.ADMIN, None),
    "/api/encoder_input": (Access.ADMIN, None),
    "/api/encoder_output": (Access.ADMIN, None),
    "/api/export_csv": (Access.ADMIN, None),
    "/api/get_thumbnail_status": (Access.ADMIN, None),
    "/api/hostname": (Access.ADMIN, None),
    "/api/lldp": (Access.ADMIN, None),
    # Not user authentication, despite the name: this opens a WebSocket to a
    # caller-supplied address using the stored *device* credentials and reports
    # the result. Phase A classified it public on the strength of its name,
    # which would have made it an anonymous credential-use oracle and a probe
    # for whatever the host can reach. Remote user login is /api/auth/login.
    "/api/login": (Access.ADMIN, None),
    "/api/lldp_topology": (Access.ADMIN, None),
    "/api/lldp_topology/acknowledge": (Access.ADMIN, None),
    "/api/ntp_profile": (Access.ADMIN, None),
    "/api/ping": (Access.ADMIN, None),
    "/api/poll": (Access.ADMIN, None),
    "/api/poll_decoders": (Access.ADMIN, None),
    "/api/poll_encoders": (Access.ADMIN, None),
    "/api/reboot": (Access.ADMIN, None),
    "/api/reload_cache": (Access.ADMIN, None),
    "/api/remove_units": (Access.ADMIN, None),
    "/api/scan": (Access.ADMIN, None),
    "/api/scan_preferences": (Access.ADMIN, None),
    "/api/scan_results": (Access.ADMIN, None),
    "/api/set_ntp": (Access.ADMIN, None),
    "/api/set_thumbnail": (Access.ADMIN, None),
    "/api/slate_delete": (Access.ADMIN, None),
    "/api/slate_settings": (Access.ADMIN, None),
    "/api/slate_status": (Access.ADMIN, None),
    "/api/slate_upload": (Access.ADMIN, None),
    "/api/sync_passwords": (Access.ADMIN, None),
    "/api/tcp_probe": (Access.ADMIN, None),
    "/api/ts_export": (Access.ADMIN, None),
    "/api/unit_config/export": (Access.ADMIN, None),
    "/api/unit_config/export_bulk": (Access.ADMIN, None),
    "/api/unit_config/import": (Access.ADMIN, None),
    "/api/update_ack": (Access.ADMIN, None),
    "/api/update_check": (Access.ADMIN, None),
    "/api/upgrade": (Access.ADMIN, None),

    # Multiview administration: preparing the environment a User operates.
    "/api/multiview/apply": (Access.ADMIN, None),
    "/api/multiview/copy": (Access.ADMIN, None),
    "/api/multiview/delete": (Access.ADMIN, None),
    "/api/multiview/layouts": (Access.ADMIN, None),
    "/api/multiview/layouts/install": (Access.ADMIN, None),
    "/api/multiview/plan": (Access.ADMIN, None),
    "/api/multiview/groups": (Access.ADMIN, None),
    "/api/multiview/groups/copy": (Access.ADMIN, None),
    "/api/multiview/groups/delete": (Access.ADMIN, None),
    "/api/multiview/groups/plan": (Access.ADMIN, None),
    "/api/multiview/groups/save": (Access.ADMIN, None),
    "/api/multiview/groups/show": (Access.ADMIN, None),
    "/api/multiview/groups/state": (Access.ADMIN, None),

    # USB administration, as distinct from USB routing.
    "/api/usb_extenders": (Access.ADMIN, None),
    "/api/usb_extenders/clear": (Access.ADMIN, None),
    "/api/usb_extenders/discover": (Access.ADMIN, None),
    "/api/usb_extenders/discover_ip": (Access.ADMIN, None),
    "/api/usb_extenders/discover_local": (Access.ADMIN, None),
    "/api/usb_extenders/identify": (Access.ADMIN, None),
    "/api/usb_extenders/network": (Access.ADMIN, None),
    "/api/usb_extenders/network_config": (Access.ADMIN, None),
    "/api/usb_extenders/ranges": (Access.ADMIN, None),
    "/api/usb_extenders/reboot": (Access.ADMIN, None),
    "/api/usb_extenders/scan_ranges": (Access.ADMIN, None),
    "/api/usb_extenders/traffic": (Access.ADMIN, None),
    "/api/usb_pair": (Access.ADMIN, None),
    "/api/usb_set_filter": (Access.ADMIN, None),
    "/api/usb_set_port": (Access.ADMIN, None),
    "/api/usb_set_type": (Access.ADMIN, None),
    "/api/usb_unpair": (Access.ADMIN, None),
}


#: Where one rule serves two audiences by method. Checked before ROUTES.
#:
#: Kept deliberately small. A rule that needs an entry here is usually a rule
#: that should have been two rules, and the exception is a route whose read is
#: ordinary and whose write is not.
METHOD_ROUTES: dict[tuple[str, str], tuple[Access, str | None]] = {
    ("POST", "/api/ui_preferences"): (Access.ADMIN, None),
}


def classify(rule: str, method: str | None = None) -> tuple[Access, str | None]:
    """Who may call `rule`, and under which capability.

    An unknown rule is ADMIN and LOCAL-only in effect -- the most restrictive
    answer available -- because a route nobody classified is a route nobody has
    reasoned about. The fence turns that into a test failure so it is noticed at
    development time rather than relied on at runtime.

    `method` narrows the answer where a rule serves a read and a write with
    different audiences. Omitting it gives the rule's own classification, which
    is the less restrictive of the two, so every caller that decides access
    passes it.
    """
    if method:
        specific = METHOD_ROUTES.get((str(method).upper(), rule))
        if specific is not None:
            return specific
    return ROUTES.get(rule, (Access.LOCAL_ONLY, None))


def is_user_route(rule: str) -> bool:
    return classify(rule)[0] is Access.USER


def required_feature(rule: str) -> str | None:
    return classify(rule)[1]
