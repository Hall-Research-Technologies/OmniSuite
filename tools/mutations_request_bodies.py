"""Mutations for the request-body parser, the identity normaliser, Remote Access commands,
the listener headers and logging.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must
occur exactly once; the harness translates its newlines to the file's own.
Imported by mutate_security.py and appended to MUTATIONS, so
`python tools/mutate_security.py reqbody. identity. remote. headers. log. save.` runs
exactly these.
"""
BODY = ["tests.test_malformed_bodies"]
IDENT = ["tests.test_display_groups", "tests.test_request_hardening"]
REQH = ["tests.test_request_hardening"]

SERVER = "OmniMatrix_upgrade_server_v7_6y.py"
REQUESTS = "omni_requests.py"

REQUEST_BODY_MUTATIONS = [
    ("reqbody.non-object-accepted", REQUESTS,
     "    if not isinstance(value, dict):\n"
     "        raise BadRequestBody(\"The request body must be a JSON object. Nothing was changed.\")",
     "    if False:\n"
     "        raise BadRequestBody(\"The request body must be a JSON object. Nothing was changed.\")",
     "a list, string or number would reach handlers that call .get(), a 500 with a Python message",
     BODY),
    ("reqbody.invalid-json-is-empty", REQUESTS,
     "    except (UnicodeDecodeError, ValueError):\n"
     "        raise BadRequestBody(\"The request body is not valid JSON. Nothing was changed.\")",
     "    except (UnicodeDecodeError, ValueError):\n"
     "        return {}",
     "an unparseable body would be read as an empty request, which Clear Units reads as \"clear\"",
     BODY),
    ("reqbody.required-ignored-when-absent", REQUESTS,
     "    if not raw or not raw.strip():\n"
     "        if required:",
     "    if not raw or not raw.strip():\n"
     "        if False:",
     "a destructive endpoint would act on a request that said nothing",
     BODY),
    ("reqbody.required-ignored-for-forms", REQUESTS,
     "    if request.mimetype in _FORMS:\n"
     "        if required:",
     "    if request.mimetype in _FORMS:\n"
     "        if False:",
     "a form post would clear the inventory",
     BODY),
    ("reqbody.clear-units-needs-no-body", SERVER,
     "    _json_dict(required=True)\n"
     "    protect = remote_access_enabled()",
     "    protect = remote_access_enabled()",
     "Clear Units would act on any request, including a malformed or empty one",
     BODY),
    ("reqbody.text-fields-unchecked", REQUESTS,
     "    for key in TEXT_FIELDS:\n"
     "        if value.get(key) is not None",
     "    for key in ():\n"
     "        if value.get(key) is not None",
     "a number or object where a handler calls .strip() would be a 500 with a Python message",
     BODY),
    ("reqbody.list-fields-unchecked", REQUESTS,
     "    for key in LIST_FIELDS:\n"
     "        if value.get(key) is not None",
     "    for key in ():\n"
     "        if value.get(key) is not None",
     "a number where a handler iterates a list would be a 500 with a Python message",
     BODY),
    ("reqbody.scan-targets-unchecked", SERVER,
     "    if not isinstance(raw_targets, str):",
     "    if False:",
     "{\"targets\": 5} would be a 500 with a Python message",
     BODY),
    ("reqbody.upgrade-reads-body-in-try", SERVER,
     "    data = _json_dict()\n"
     "    try:\n"
     "        file_name = (data.get(\"file\") or \"\").strip()",
     "    try:\n"
     "        data = _json_dict()\n"
     "        file_name = (data.get(\"file\") or \"\").strip()",
     "the broad handler below would turn the refusal of a body into a 500",
     BODY),
    ("identity.ip-converted-to-mac", "omni_users.py",
     "    if _MAC_SPELLINGS.match(text):\n"
     "        compact = re.sub(r\"[:.-]\", \"\", text)\n",
     "    compact = re.sub(r\"[:.-]\", \"\", text)\n"
     "    if len(compact) == 12 and all(c in \"0123456789abcdef\" for c in compact):\n",
     "an address such as 100.100.100.100 would become a plausible MAC that no device owns",
     IDENT),
    ("identity.mixed-separators-accepted", "omni_users.py",
     "([:-])(?:[0-9a-f]{2}\\1){4}",
     "([:-])(?:[0-9a-f]{2}[:-]){4}",
     "a MAC with mixed separators would be accepted as an identity",
     IDENT),
    ("identity.grant-by-address-allowed", "omni_remote_api.py",
     "            if not (isinstance(item, str) and omni_users.is_mac_identity(item)):",
     "            if False:",
     "a grant by address or name would be stored as an identity that can never match",
     IDENT),
    ("routegroup.stale-member-named-by-identity", "omni_remote_api.py",
     "        return NO_LONGER_AVAILABLE\n",
     "        return identity\n",
     "a restricted user would be shown a stale member's hardware address as its name",
     IDENT),
    ("remote.off-by-default-body", "omni_remote_api.py",
     "        if not isinstance(enabled, bool):",
     "        if False:",
     "a body that does not say on or off would turn Remote Access off and end every session",
     REQH),
    ("remote.port-unchecked", "omni_remote_api.py",
     "        if data.get(\"port\") is not None:\n            port_error",
     "        if False:\n            port_error",
     "an out-of-range port would reach the listener and the saved state",
     REQH),
    ("remote.port-floor-lowered", "omni_remote_api.py",
     "REMOTE_MIN_PORT = 1024",
     "REMOTE_MIN_PORT = 1",
     "ports other services own would be accepted",
     REQH),
    ("headers.server-names-library", "omni_remote.py",
     "        def version_string(self):\n            return \"OmniSuite\"",
     "        def version_string(self):\n            return super().version_string()",
     "the Server header would name the web library and the Python version",
     REQH),
    ("headers.refusal-without-frame-denial", "omni_remote.py",
     "                    (\"X-Frame-Options\", \"DENY\"),\n",
     "",
     "the 421 refusal would be frameable",
     REQH),
    ("log.upgrade-command-unredacted", SERVER,
     "json.dumps(omni_users.redact(payload), indent=2))\n    \n    sslopt",
     "json.dumps(payload, indent=2))\n    \n    sslopt",
     "the device password would be written to the log",
     REQH),
    ("log.redactor-forgets-passphrase", "omni_users.py",
     "    \"passphrase\", \"authorization\", \"cookie\", \"credentials\",\n",
     "",
     "a backup passphrase or a cookie in a body would not be redacted",
     REQH),
    ("save.config-empty-object-saves", SERVER,
     "    omni_requests.require_any(data, (\"username\", \"password\"",
     "    (lambda *a: None)(data, (\"username\", \"password\"",
     "a settings save carrying nothing would rewrite the configuration",
     BODY),
    ("save.lldp-ack-defaults-true", SERVER,
     "    if not isinstance(data.get(\"acknowledged\"), bool):",
     "    if False:",
     "a request that says nothing would record an acknowledgement",
     BODY),
]
