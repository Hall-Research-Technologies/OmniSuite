"""A WebSocket stand-in a test owns, so what it asserts about device reads does not depend on load order.

run_tests.py executes every test file by path without registering it, so a module that does
`import test_usb_integration as base` gets a *second* copy of it, and each copy installs its own fenced
transport into the application at import. Whichever loaded last is the one in `srv._ws_send_recv`, which
is not necessarily the copy whose `ServerTestBase` the test inherits -- and then a test that asserts
"no device was read" asserts against a list nothing writes to, and passes for the wrong reason. (Found
when a test that said the sweep recorded what it found failed in the full run and passed alone.)

A test that cares what reached the transport installs this in `setUp` and reads its own lists. It is
restored on cleanup, and a write fails the test at the call and again at cleanup, because service code that
turns errors into results would otherwise absorb the first.
"""


class RecordingWebSocket:
    """Mixin for a `ServerTestBase` subclass. Call `install_recording_ws()` from `setUp`."""

    def install_recording_ws(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.ws_requested = []          # (url, config node) for every read
        self.ws_writes = []             # (url, what) for every write that was attempted
        original = srv._ws_send_recv

        def stand_in(url, payload, timeout=None, *args, **kwargs):
            payload = payload if isinstance(payload, dict) else {}
            if "config_set" in payload or "method" in payload:
                self.ws_writes.append((url, payload.get("config_set") or payload.get("method")))
                raise AssertionError("a test tried to write to %s" % url)
            node = payload.get("config_get")
            self.ws_requested.append((url, node))
            return self.ws_read(url, node)

        srv._ws_send_recv = stand_in
        self.addCleanup(lambda: setattr(srv, "_ws_send_recv", original))
        self.addCleanup(self.assert_no_ws_writes)

    def assert_no_ws_writes(self):
        if self.ws_writes:
            raise AssertionError("this test tried to write to a device: %r" % (self.ws_writes,))
