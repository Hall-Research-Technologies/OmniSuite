"""A restricted User's per-window source choice, through the REAL Show engine.

The server half of the Remote User Multiview editor has one job the HTTP layer cannot do for it:
turn "the windows as they stand, with these changes" into the ordinary Show transaction and leave the
administrator's saved layout alone. These tests drive `_user_multiview_compose` against the simulated
decoder and encoders of test_multiview.py (merge-by-name, method-only create/delete, silently ignored
invalid writes). Nothing touches a socket; every address is an RFC 5737 documentation address.

What they pin:

  * the window baseline: the display is the authority for a Multiview that is on it, and a window it
    holds empty is empty even when the saved record once named a source; for one that is not on it the
    saved record decides;
  * the composition is the baseline with only the named windows changed, and the engine's own rules
    apply to it -- same-size reuse is legal, one source in two differently sized windows is refused
    and leaves every device byte-identical, and the Replace the page performs (empty one window,
    fill the other, together) goes through;
  * the saved record is never written, whatever the person chose;
  * a revision the person was not looking at is refused before a single write.
"""
import copy
import unittest

try:
    from . import test_multiview as base
    from .test_multiview_hardware_state import HardwareStateBase, SOURCES
except ImportError:                                  # loaded by path
    import test_multiview as base
    from test_multiview_hardware_state import HardwareStateBase, SOURCES

srv = base.srv
IP = base.DECODER_IP
A, B, C, D = SOURCES


class ComposeBase(HardwareStateBase):
    def windows(self, name):
        state, error = srv._decoder_state(IP)
        self.assertIsNotNone(state, error)
        return srv._user_mv_windows(IP, name, state)

    def by_cell(self, name):
        return {w["cell"]: w for w in self.windows(name)["windows"]}

    def compose(self, name, wanted, base_revision=None):
        revision = self.windows(name)["revision"] if base_revision is None else base_revision
        body, status = srv._user_multiview_compose(IP, name, wanted, revision)
        return status, body

    def saved(self, name):
        device = {d["ip"]: d for d in base._devices()}[IP]
        return copy.deepcopy(srv._multiview_meta_for(device).get(name))

    def pool_binding(self, name, cell):
        return self.subframe(name, cell)["input"]

    def writes_to_decoder(self):
        return list(self.dec.writes)


class BaselineTests(ComposeBase):
    def test_a_layout_that_is_not_on_the_display_is_what_was_saved(self):
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        cells = self.by_cell(name)
        self.assertEqual({c: w["source_ip"] for c, w in cells.items()}, {"left": A, "right": B})
        self.assertEqual({w["origin"] for w in cells.values()}, {"saved"})
        self.assertFalse(self.windows(name)["showing"])

    def test_the_layout_on_the_display_is_read_from_the_display(self):
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        self.show_ok(name)
        cells = self.by_cell(name)
        self.assertEqual({c: w["source_ip"] for c, w in cells.items()}, {"left": A, "right": B})
        self.assertEqual({w["origin"] for w in cells.values()}, {"live"})
        self.assertTrue(self.windows(name)["showing"])

    def test_a_window_the_display_holds_empty_is_empty_even_though_the_record_names_a_source(self):
        # The record is the administrator's definition and a person's Show does not rewrite it,
        # so after a person empties a window the record still names its source. Showing that
        # source again in the page would be the page saying something the display is not.
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        self.show_ok(name)
        status, body = self.compose(name, {"right": ""})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.saved(name)["windows"][1]["source_ip"] if
                         len(self.saved(name)["windows"]) > 1 else B, B)
        right = self.by_cell(name)["right"]
        self.assertEqual((right["source_ip"], right["origin"]), ("", "empty"))

    def test_the_revision_moves_when_a_window_changes_and_not_when_nothing_does(self):
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        self.show_ok(name)
        first = self.windows(name)["revision"]
        self.assertEqual(self.windows(name)["revision"], first)
        status, _body = self.compose(name, {"right": C})
        self.assertEqual(status, 200)
        self.assertNotEqual(self.windows(name)["revision"], first)

    def test_a_layout_the_decoder_does_not_hold_is_none(self):
        self.store("Duo", "side-by-side", {"left": A, "right": B})
        state, _ = srv._decoder_state(IP)
        self.assertIsNone(srv._user_mv_windows(IP, "no-such-object", state))


class CompositionTests(ComposeBase):
    def test_only_the_named_window_changes_and_the_others_are_not_written(self):
        name = self.store("Quad", "2x2", {"top_left": A, "top_right": B, "bottom_left": C, "bottom_right": D})
        self.show_ok(name)
        untouched = {cell: self.pool_binding(name, cell)
                     for cell in ("top_left", "bottom_left", "bottom_right")}
        before = len(self.writes_to_decoder())
        status, body = self.compose(name, {"top_right": C})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "VERIFIED", body)
        cells = self.by_cell(name)
        self.assertEqual(cells["top_right"]["source_ip"], C)
        self.assertEqual({c: cells[c]["source_ip"] for c in ("top_left", "bottom_left", "bottom_right")},
                         {"top_left": A, "bottom_left": C, "bottom_right": D})
        # The three windows nobody asked about are bound to the inputs they had.
        self.assertEqual({cell: self.pool_binding(name, cell) for cell in untouched}, untouched)
        written = self.writes_to_decoder()[before:]
        for cell, bound in untouched.items():
            for entry in written:
                if entry[1] == "ip_input":
                    self.assertNotEqual(entry[2][0].get("name"), bound,
                                        "%s's input was written for a change to another window" % cell)

    def test_the_saved_record_is_not_written_by_a_persons_show(self):
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        self.show_ok(name)
        record = self.saved(name)
        status, body = self.compose(name, {"right": C, "left": ""})
        self.assertEqual(status, 200, body)
        after = self.saved(name)
        # The engine keeps its own bookkeeping of which pool inputs it configured; that is the
        # same record Show has always written and is not the layout. The definition is.
        definition = ("layout", "friendly_name", "windows", "canvas", "id")
        self.assertEqual({k: after.get(k) for k in definition}, {k: record.get(k) for k in definition},
                         "the administrator's definition was changed")
        self.assertEqual([(w["cell"], w["source_ip"]) for w in after["windows"]],
                         [("left", A), ("right", B)])

    def test_a_change_that_is_what_the_display_already_holds_is_a_verified_no_op(self):
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        self.show_ok(name)
        before = self.snapshot()
        status, body = self.compose(name, {"right": B})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["already_shown"], body)
        self.assertEqual(self.snapshot(), before)

    def test_choosing_for_a_layout_that_is_not_showing_shows_it_with_the_choice(self):
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        status, body = self.compose(name, {"right": C})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], name)
        cells = self.by_cell(name)
        self.assertEqual((cells["left"]["source_ip"], cells["right"]["source_ip"]), (A, C))

    def test_clearing_a_window_empties_its_subframe_and_releases_nothing_else(self):
        name = self.store("Quad", "2x2", {"top_left": A, "top_right": B, "bottom_left": C, "bottom_right": D})
        self.show_ok(name)
        status, body = self.compose(name, {"bottom_right": ""})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.pool_binding(name, "bottom_right"), "")
        self.assertEqual(self.by_cell(name)["bottom_right"]["origin"], "empty")
        self.assertEqual(self.by_cell(name)["top_left"]["source_ip"], A)

    def test_several_changes_are_one_transaction(self):
        name = self.store("Quad", "2x2", {"top_left": A, "top_right": B, "bottom_left": C, "bottom_right": D})
        self.show_ok(name)
        status, body = self.compose(name, {"top_left": D, "top_right": "", "bottom_left": A})
        self.assertEqual(status, 200, body)
        cells = self.by_cell(name)
        self.assertEqual({c: w["source_ip"] for c, w in cells.items()},
                         {"top_left": D, "top_right": "", "bottom_left": A, "bottom_right": D})


class SizeRuleTests(ComposeBase):
    def test_one_source_in_two_windows_of_the_same_size_is_legal_and_shares_one_input(self):
        name = self.store("Quad", "2x2", {"top_left": A, "top_right": B})
        self.show_ok(name)
        status, body = self.compose(name, {"bottom_left": A})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.pool_binding(name, "top_left"), self.pool_binding(name, "bottom_left"),
                         "one stream is one decoder input")
        self.assertEqual(self.by_cell(name)["bottom_left"]["source_ip"], A)

    def test_one_source_in_windows_of_different_sizes_is_refused_and_nothing_is_written(self):
        name = self.store("Presenter", "pip-bottom-right", {"main": A, "bottom_right": B})
        self.show_ok(name)
        before = self.snapshot()
        status, body = self.compose(name, {"bottom_right": A})
        self.assertEqual(status, 409, body)
        self.assertFalse(body["ok"])
        self.assertEqual(self.snapshot(), before, "a refused composition changed a device")

    def test_replace_empties_the_other_window_and_fills_this_one_in_one_step(self):
        # What the page sends after the person chooses Replace: both windows named together.
        name = self.store("Presenter", "pip-bottom-right", {"main": A, "bottom_right": B})
        self.show_ok(name)
        status, body = self.compose(name, {"main": "", "bottom_right": A})
        self.assertEqual(status, 200, body)
        cells = self.by_cell(name)
        self.assertEqual((cells["main"]["source_ip"], cells["bottom_right"]["source_ip"]), ("", A))

    def test_replace_the_other_way_round(self):
        name = self.store("Presenter", "pip-bottom-right", {"main": A, "bottom_right": B})
        self.show_ok(name)
        status, body = self.compose(name, {"bottom_right": "", "main": B})
        self.assertEqual(status, 200, body)
        cells = self.by_cell(name)
        self.assertEqual((cells["main"]["source_ip"], cells["bottom_right"]["source_ip"]), (B, ""))


class StaleOperationTests(ComposeBase):
    def test_a_revision_the_person_was_not_looking_at_is_refused_before_any_write(self):
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        self.show_ok(name)
        seen = self.windows(name)["revision"]
        # Somebody else changes a window after the page read it.
        status, _ = self.compose(name, {"right": C})
        self.assertEqual(status, 200)
        before = self.snapshot()
        status, body = self.compose(name, {"left": D}, base_revision=seen)
        self.assertEqual((status, body.get("reason")), (409, "stale"), body)
        self.assertEqual(self.snapshot(), before)

    def test_a_missing_or_empty_revision_is_stale_not_trusted(self):
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        self.show_ok(name)
        for revision in ("", None, "0" * 16):
            body, status = srv._user_multiview_compose(IP, name, {"left": C}, revision)
            self.assertEqual((status, body.get("reason")), (409, "stale"), revision)

    def test_a_layout_the_decoder_does_not_hold_is_a_404_not_a_write(self):
        self.store("Duo", "side-by-side", {"left": A, "right": B})
        before = self.snapshot()
        body, status = srv._user_multiview_compose(IP, "no-such-object", {"left": A}, "x")
        self.assertEqual(status, 404)
        self.assertEqual(self.snapshot(), before)


class RevisionAndLockTests(ComposeBase):
    def test_the_revision_changes_when_only_the_layout_going_on_the_display_changes(self):
        # Same windows, same sources: the saved layout and the layout on the display agree, so only
        # whether it is showing can tell the two views apart.
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        off = self.windows(name)
        self.show_ok(name)
        on = self.windows(name)
        self.assertEqual([(w["cell"], w["source_ip"]) for w in off["windows"]],
                         [(w["cell"], w["source_ip"]) for w in on["windows"]], "precondition: same windows")
        self.assertNotEqual(off["revision"], on["revision"])

    def test_the_composition_is_read_and_written_under_the_multiview_lock(self):
        # Ownership is asked at the moment each device-touching step runs: the read of the display and
        # the Show itself. A wait that merely happened to take long would prove nothing, because the
        # Show takes the lock for its own writes; only "owned at the read" proves the read is inside.
        from unittest import mock
        name = self.store("Duo", "side-by-side", {"left": A, "right": B})
        self.show_ok(name)
        revision = self.windows(name)["revision"]
        owned = {}
        real_state, real_show = srv._decoder_state, srv._show_multiview_on

        def state(ip):
            owned.setdefault("read", srv._multiview_apply_lock._is_owned())
            return real_state(ip)

        def show(*args, **kwargs):
            owned["show"] = srv._multiview_apply_lock._is_owned()
            return real_show(*args, **kwargs)

        with mock.patch.object(srv, "_decoder_state", state), mock.patch.object(srv, "_show_multiview_on", show):
            body, status = srv._user_multiview_compose(IP, name, {"right": C}, revision)
        self.assertEqual(status, 200, body)
        self.assertEqual(owned, {"read": True, "show": True},
                         "a composition read or wrote without holding the Multiview lock")


if __name__ == "__main__":
    unittest.main()
