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
    # The remote login page and the credential check behind it.
    "/login": (Access.PUBLIC, None),
    "/api/auth/login": (Access.PUBLIC, None),

    # ---- authentication state -----------------------------------------
    "/api/auth/logout": (Access.AUTHENTICATED, None),
    "/api/auth/session": (Access.AUTHENTICATED, None),

    # ---- any authenticated principal ----------------------------------
    # The guide is operator documentation and carries no device state.
    "/help": (Access.AUTHENTICATED, None),
    # Appearance is a per-browser convenience, not a permission.
    "/api/ui_preferences": (Access.AUTHENTICATED, None),
    "/api/ui_preferences/reset": (Access.AUTHENTICATED, None),
    "/api/notices": (Access.AUTHENTICATED, None),
    "/api/notices/ack": (Access.AUTHENTICATED, None),

    # ---- the restricted user's own surface ----------------------------
    # The shell and the projection behind it. AUTHENTICATED rather than USER
    # because an administrator opens the same pages to review them, and the
    # projection scopes its answer to whoever is asking rather than relying on
    # the route class to do it.
    "/user": (Access.AUTHENTICATED, None),
    "/api/user/state": (Access.AUTHENTICATED, None),
    "/api/user/route": (Access.USER, FEATURE_AV),

    # ---- administration of accounts -----------------------------------
    "/admin/users": (Access.ADMIN, None),
    "/api/admin/users": (Access.ADMIN, None),
    "/api/admin/users/<user_id>": (Access.ADMIN, None),
    "/api/admin/devices": (Access.ADMIN, None),
    "/api/admin/audit": (Access.ADMIN, None),
    "/api/remote_access": (Access.ADMIN, None),

    # ---- first-run bootstrap, local host only -------------------------
    # Creating the first administrator is reachable from the machine running
    # OmniSuite and from nowhere else, so a remote client can never make
    # itself one.
    "/api/bootstrap/state": (Access.LOCAL_ONLY, None),
    "/api/bootstrap/admin": (Access.LOCAL_ONLY, None),

    # ---- restricted routing User --------------------------------------
    # Every one of these is additionally filtered or checked per device by the
    # caller: a capability alone never authorises a specific source or
    # destination.
    "/api/state": (Access.USER, FEATURE_AV),
    "/api/route": (Access.USER, FEATURE_AV),
    "/api/usb_state": (Access.USER, FEATURE_USB),
    "/api/usb_route/resolve": (Access.USER, FEATURE_USB),
    "/api/usb_route/pair": (Access.USER, FEATURE_USB),
    "/api/usb_route/unpair": (Access.USER, FEATURE_USB),
    "/api/multiview/state": (Access.USER, FEATURE_MULTIVIEW),
    "/api/multiview/decoders": (Access.USER, FEATURE_MULTIVIEW),
    "/api/multiview/sources": (Access.USER, FEATURE_MULTIVIEW),
    "/api/multiview/preview": (Access.USER, FEATURE_MULTIVIEW),
    # Operating an approved preset. Creating, editing, copying, deleting and
    # installing layouts are administration and stay Admin-only -- the boundary
    # follows Multiview's own preset-versus-execution split.
    "/api/multiview/show": (Access.USER, FEATURE_MULTIVIEW),
    "/api/multiview/switch": (Access.USER, FEATURE_MULTIVIEW),

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


def classify(rule: str) -> tuple[Access, str | None]:
    """Who may call `rule`, and under which capability.

    An unknown rule is ADMIN and LOCAL-only in effect -- the most restrictive
    answer available -- because a route nobody classified is a route nobody has
    reasoned about. The fence turns that into a test failure so it is noticed at
    development time rather than relied on at runtime.
    """
    return ROUTES.get(rule, (Access.LOCAL_ONLY, None))


def is_user_route(rule: str) -> bool:
    return classify(rule)[0] is Access.USER


def required_feature(rule: str) -> str | None:
    return classify(rule)[1]
