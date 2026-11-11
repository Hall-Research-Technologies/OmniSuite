"""Reading a JSON request body: one parser for every endpoint.

`get_json(silent=True) or {}` turned a list, a string or a number into
something handlers then called `.get()` on (a 500 with a Python message), and
turned an unparseable body into an empty request -- which for a destructive
endpoint meant "do it". So:

  * an absent body is {} (many endpoints take no parameters), unless `required`,
    which a destructive endpoint sets so that only a request that actually says
    something can act;
  * a body that is not valid JSON, or not a JSON object, is a 400 with a plain
    sentence, whatever the endpoint and whatever Content-Type said;
  * a form or multipart body is not JSON and is left to the handler that reads
    it (it is {} here, or refused when the endpoint requires a body);
  * a field whose value is always text, or always a list, wherever any endpoint
    takes it, must be that, so `.strip()` on a number is a 400 and not a 500.

The module reads a request handed to it and imports no web framework, so the
rules can be tested without one and used by every blueprint-like module.
"""
from __future__ import annotations

import json

# Fields whose value is text wherever any endpoint takes them. Code reads them
# with `.strip()`, so a number, list or object there was a 500 with a Python
# message. Checked once, here, rather than in sixty handlers.
TEXT_FIELDS = ("ip", "mac", "hostname", "name", "username", "password", "fallback_password",
               "file", "address", "action", "mode", "transport", "interface_ip", "subnet_mask",
               "source", "destination", "adapter_id", "layout", "cert_path", "key_path",
               "passphrase", "new_password", "gateway", "type", "decoder", "encoder",
               "target", "filter", "rex", "lex", "device", "version")
# Fields that are always a list when present.
LIST_FIELDS = ("ips", "macs", "members", "ranges", "devices")

_FORMS = ("multipart/form-data", "application/x-www-form-urlencoded")

NEEDS_BODY = "This request needs a JSON object in its body, and nothing was changed."


def require_any(data, keys):
    """A save must carry at least one field it can save, or it is a 400.

    An object with only unrelated keys is well formed but asks for nothing; reading
    it as "save the current settings" wrote files (and, for a toggle, defaulted to
    on) on the strength of a request that said nothing.
    """
    if not any(data.get(key) is not None for key in keys):
        raise BadRequestBody("Nothing to save: the request carries none of %s."
                             % ", ".join(keys))


class BadRequestBody(Exception):
    """The request body cannot be read as the JSON object the endpoint needs."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def json_object(request, required=False):
    """The JSON object in `request`'s body, or BadRequestBody."""
    raw = request.get_data(cache=True)
    if not raw or not raw.strip():
        if required:
            raise BadRequestBody(NEEDS_BODY)
        return {}
    if request.mimetype in _FORMS:
        if required:
            raise BadRequestBody(NEEDS_BODY)
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BadRequestBody("The request body is not valid JSON. Nothing was changed.")
    if not isinstance(value, dict):
        raise BadRequestBody("The request body must be a JSON object. Nothing was changed.")
    for key in TEXT_FIELDS:
        if value.get(key) is not None and not isinstance(value[key], str):
            raise BadRequestBody('The field "%s" must be text. Nothing was changed.' % key)
    for key in LIST_FIELDS:
        if value.get(key) is not None and not isinstance(value[key], list):
            raise BadRequestBody('The field "%s" must be a list. Nothing was changed.' % key)
    return value
