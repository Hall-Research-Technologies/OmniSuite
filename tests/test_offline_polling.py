"""A crowd of units that do not answer must not stall polling, multiply traffic or flood the log.

Measured on the real server, with 250 discovered units that never answered (refused connections, TCP
that accepts and says nothing, an upgrade never followed by a reply, and addresses that drop every
packet):

    /api/poll           every silent unit cost a `ping.exe` plus up to five bounded reads (several seconds
                        each: a refused connect on Windows takes about 2 s whatever timeout is asked),
                        and the Device Info page asked each one in turn, so a cycle took tens of minutes
                        and the units that DID answer were refreshed once an hour
    /api/poll_decoders  every decoder, two at a time, 4 s a read: minutes per request
    verification sweep  three attempts with pauses at every silent unit
    look-again          every silent unit re-probed on every state read after ten seconds, for ever

Quiet units are now looked at on an exponential back-off with a cap, one unit is never polled by two
callers at once, answering units go first, a request returns what it has when its budget is spent, and
idle polling logs nothing. The transport here is the WebSocket opener and the process spawner, replaced
beside the fence, and every assertion is proved by mutation (see the report).
"""
import copy
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_liveness                                          # noqa: E402
import omni_matrix_logic                                      # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv                 # noqa: E402


def unit(i, role="decoder"):
    """Synthetic: documentation addresses and invented hardware ids, nothing like the bench."""
    return {"ip": "203.0.113.%d" % (i + 1), "mac": "02:77:%02x:%02x:00:01" % (i >> 8, i & 255),
            "hostname": "quiet-%03d" % i, "role": role, "type": role.capitalize(),
            "model": "hw-omni-d4511" if role == "decoder" else "hw-omni-e4521"}


UNITS = [unit(i, "decoder" if i % 4 else "encoder") for i in range(60)]
IPS = [u["ip"] for u in UNITS]
DECODER_IPS = [u["ip"] for u in UNITS if u["role"] == "decoder"]
ENCODER_IPS = [u["ip"] for u in UNITS if u["role"] == "encoder"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW"):
            patcher = mock.patch.object(srv, name, Path(self.folder.name) / getattr(srv, name).name)
            patcher.start()
            self.addCleanup(patcher.stop)
        srv.CACHE.write_text(json.dumps(copy.deepcopy(UNITS)), encoding="utf-8")
        self.live = srv._liveness
        self.live.forget()
        self.addCleanup(self.live.forget)
        self.addCleanup(srv.await_startup_tasks, 20)
        previous = omni_liveness._probe_override
        self.addCleanup(omni_liveness.set_probe_override, previous)
        omni_liveness.set_probe_override(lambda ip: False)
        with srv._poll_inflight_lock:
            srv._poll_inflight.clear()
        self.addCleanup(self._drain_inflight)

        self.lock = threading.Lock()
        self.calls = []                  # (kind, ip) at the transport
        self.live_now = 0
        self.max_live = 0
        self.delay = 0.0
        self.answers = False
        self.ping_ok = False             # the connectivity check passes although the management API is silent
        self.release = threading.Event()
        self.release.set()
        self.client = srv.app.test_client()

        self.patch(srv, "_ws_send_recv", self.ws_send_recv)
        self.patch(srv, "_ws_get_decoder_inputs", self.decoder_inputs)
        self.patch(srv, "_ws_get_encoder_output_settings", self.encoder_settings)
        self.patch(omni_matrix_logic, "poll_unit_status", self.unit_status)

    def patch(self, obj, name, value):
        patcher = mock.patch.object(obj, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drain_inflight(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with srv._poll_inflight_lock:
                if not srv._poll_inflight:
                    return
            time.sleep(0.05)

    # -- the transport ---------------------------------------------------------------------------
    def _enter(self, kind, ip):
        with self.lock:
            self.calls.append((kind, ip))
            self.live_now += 1
            self.max_live = max(self.max_live, self.live_now)

    def _leave(self):
        with self.lock:
            self.live_now -= 1

    def _work(self):
        self.release.wait(20)
        if self.delay:
            time.sleep(self.delay)

    def ws_send_recv(self, url, payload, timeout=None):
        ip = url.split("//", 1)[1].split(":", 1)[0]
        self._enter("ws:" + str(payload.get("config_get")), ip)
        try:
            self._work()
            if not self.answers:
                return None
            return {"id": payload.get("id"), "config": {"hostname": "h-" + ip, "firmwareversion": "1.0"}}
        finally:
            self._leave()

    def decoder_inputs(self, ip, *args, **kwargs):
        self._enter("decoder_inputs", ip)
        try:
            self._work()
            return {"ip1_addr": "239.9.9.9", "ip1_port": 5000, "hostname": "h"} if self.answers else {}
        finally:
            self._leave()

    def encoder_settings(self, ip, *args, **kwargs):
        self._enter("encoder_settings", ip)
        try:
            self._work()
            return {"sessions": []}
        finally:
            self._leave()

    def unit_status(self, ip):
        self._enter("status", ip)
        try:
            self._work()
            return ({"status": "connected", "ip": ip, "role": "decoder", "model": "hw-omni-d4511"}
                    if (self.answers or self.ping_ok) else {"status": "disconnected", "ip": ip})
        finally:
            self._leave()

    # -- helpers ---------------------------------------------------------------------------------
    def quiet(self, ips, misses=6):
        """Record `ips` as seen not answering `misses` times in a row, just now."""
        for ip in ips:
            for _ in range(misses):
                self.live.record(ip, False, "seed")

    def kinds(self, prefix=""):
        return [c for c in self.calls if c[0].startswith(prefix)]

    def post(self, path, **body):
        reply = self.client.post(path, json=body)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True)[:200])
        return reply.get_json()


class ScheduleTests(unittest.TestCase):
    def test_nothing_waits_until_the_miss_limit(self):
        for misses in range(omni_liveness.MISS_LIMIT):
            self.assertEqual(omni_liveness.backoff_seconds(misses), 0.0)

    def test_the_schedule_doubles_and_is_capped(self):
        steps = [omni_liveness.backoff_seconds(omni_liveness.MISS_LIMIT + k) for k in range(8)]
        self.assertEqual(steps[:4], [10.0, 20.0, 40.0, 80.0])
        self.assertEqual(max(steps), omni_liveness.BACKOFF_CAP_SECONDS)
        self.assertEqual(steps, sorted(steps))

    def test_junk_never_raises(self):
        for junk in (None, "x", [], {}):
            self.assertEqual(omni_liveness.backoff_seconds(junk), 0.0)

    def test_misses_are_counted_and_an_answer_clears_them(self):
        clock = Clock()
        live = omni_liveness.UnitLiveness(lambda ip: False, now=clock)
        for _ in range(4):
            live.record("a", False)
        self.assertEqual(live.misses("a"), 4)
        live.record("a", True)
        self.assertEqual(live.misses("a"), 0)
        self.assertTrue(live.due("a"))

    def test_due_follows_the_schedule_and_force_overrides_it(self):
        clock = Clock()
        live = omni_liveness.UnitLiveness(lambda ip: False, now=clock)
        self.assertTrue(live.due("never-seen"))
        live.record("a", False)
        self.assertTrue(live.due("a"), "one miss is not gone")
        live.record("a", False)           # second miss: now waits 10 s, stretched 0.75-1.25 per unit
        self.assertFalse(live.due("a"))
        clock.t += 7.0
        self.assertFalse(live.due("a"))
        clock.t += 6.0
        self.assertTrue(live.due("a"))
        self.assertTrue(live.due("a", force=True))
        live.record("a", False)           # third: 20 s (15-25)
        clock.t += 12
        self.assertFalse(live.due("a"))
        self.assertTrue(live.due("a", force=True))
        clock.t += 15
        self.assertTrue(live.due("a"))

    def test_the_jitter_is_bounded_deterministic_and_spreads_a_crowd(self):
        factors = [omni_liveness.jitter("203.0.113.%d" % i) for i in range(1, 200)]
        self.assertTrue(all(0.75 <= f < 1.25 for f in factors))
        self.assertEqual(omni_liveness.jitter("x"), omni_liveness.jitter("x"))
        self.assertGreater(len({round(f, 2) for f in factors}), 20)

    def test_a_crowd_that_went_quiet_together_does_not_fall_due_together(self):
        clock = Clock()
        live = omni_liveness.UnitLiveness(lambda ip: False, now=clock)
        ips = ["203.0.113.%d" % i for i in range(1, 200)]
        for ip in ips:
            for _ in range(7):
                live.record(ip, False)        # waits 120 s (90-150)
        clock.t += 120
        due = sum(1 for ip in ips if live.due(ip))
        self.assertTrue(0 < due < len(ips), "the whole crowd fell due at once: %d of %d" % (due, len(ips)))

    def test_the_look_again_batch_follows_the_same_schedule(self):
        clock = Clock()
        live = omni_liveness.UnitLiveness(lambda ip: False, now=clock)
        for _ in range(5):
            live.record("a", False)       # waits 80 s
        clock.t += 30
        self.assertEqual(live.stale_offline(10.0), [], "a long-silent unit was looked at after ten seconds")
        clock.t += 75
        self.assertEqual(live.stale_offline(10.0), ["a"])

    def test_a_record_seeded_without_misses_keeps_the_old_behaviour(self):
        clock = Clock()
        live = omni_liveness.UnitLiveness(lambda ip: False, now=clock)
        live._seen["a"] = (False, clock.t - 30, "seed")
        self.assertEqual(live.stale_offline(10.0), ["a"])
        self.assertTrue(live.due("a"))

    def test_forget_drops_the_count(self):
        live = omni_liveness.UnitLiveness(lambda ip: False)
        live.record("a", False)
        live.forget("a")
        self.assertEqual(live.misses("a"), 0)
        live.record("b", False)
        live.forget()
        self.assertEqual(live.misses("b"), 0)


class TransitionLogTests(Base):
    def capture(self):
        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())
        handler = Handler(logging.INFO)
        srv.log.addHandler(handler)
        previous = srv.log.level
        srv.log.setLevel(logging.INFO)
        self.addCleanup(srv.log.removeHandler, handler)
        self.addCleanup(srv.log.setLevel, previous)
        return records

    def test_a_unit_going_quiet_is_logged_once_and_then_not_again(self):
        records = self.capture()
        for _ in range(4):
            self.post("/api/poll", ip=IPS[0], force=True)
        lines = [r for r in records if "[LIVENESS]" in r]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("not answering", lines[0])

    def test_a_unit_coming_back_is_logged_once(self):
        self.quiet(IPS[:1])
        records = self.capture()
        self.answers = True
        self.post("/api/poll", ip=IPS[0], force=True)
        self.post("/api/poll", ip=IPS[0], force=True)
        lines = [r for r in records if "[LIVENESS]" in r]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("answering again", lines[0])

    def test_steady_state_polling_of_a_healthy_unit_logs_nothing_here(self):
        records = self.capture()
        self.answers = True
        for _ in range(3):
            self.post("/api/poll", ip=IPS[0])
        self.assertEqual([r for r in records if "[LIVENESS]" in r], [])


class SinglePollTests(Base):
    def test_a_quiet_unit_inside_its_back_off_costs_nothing(self):
        self.quiet(IPS[:1])
        body = self.post("/api/poll", ip=IPS[0])
        self.assertIs(body["ok"], False)
        self.assertEqual(body.get("skipped"), "backoff")
        self.assertEqual(body["unit"]["status"], "disconnected")
        self.assertEqual(self.calls, [], "a unit seen silent a moment ago was probed again")

    def test_an_operator_forced_poll_looks_anyway(self):
        self.quiet(IPS[:1])
        self.post("/api/poll", ip=IPS[0], force=True)
        self.assertTrue(self.kinds("status"))

    def test_a_force_that_is_not_the_boolean_true_is_not_a_force(self):
        self.quiet(IPS[:1])
        for junk in ("true", 1, "yes"):
            self.post("/api/poll", ip=IPS[0], force=junk)
        self.assertEqual(self.calls, [])

    def test_an_unknown_unit_is_looked_at(self):
        self.post("/api/poll", ip=IPS[0])
        self.assertTrue(self.kinds("status"))

    def test_a_silent_unit_gets_one_read_not_three(self):
        self.post("/api/poll", ip=IPS[0])
        reads = [c for c in self.calls if c[0].startswith("ws:")]
        self.assertEqual([k for k, _ip in reads], ["ws:systeminfo"], reads)

    def test_a_silent_unit_is_recorded_and_backs_off_after_the_miss_limit(self):
        for _ in range(omni_liveness.MISS_LIMIT):
            self.post("/api/poll", ip=IPS[0])
        self.assertIs(self.live.known(IPS[0]), False)
        self.calls.clear()
        body = self.post("/api/poll", ip=IPS[0])
        self.assertEqual(body.get("skipped"), "backoff")
        self.assertEqual(self.calls, [])

    def test_a_unit_that_pings_but_whose_api_is_silent_is_recorded_silent(self):
        self.ping_ok = True
        body = self.post("/api/poll", ip=IPS[0])
        self.assertIs(body["ok"], True, "the reply to this poll must not change")
        self.assertIs(self.live.known(IPS[0]), False)
        reads = [k for k, _ip in self.calls if k.startswith("ws:")]
        self.assertEqual(reads, ["ws:systeminfo", "ws:timezone", "ws:net"], "the first look still tries every read: %r" % reads)

    def test_a_pinging_unit_already_seen_silent_gets_one_read(self):
        self.ping_ok = True
        self.live.record(IPS[0], False, "seed")          # one miss: within the limit, so still polled
        self.post("/api/poll", ip=IPS[0])
        reads = [k for k, _ip in self.calls if k.startswith("ws:")]
        self.assertEqual(reads, ["ws:systeminfo"], reads)

    def _answers_only(self, which):
        def reply(url, payload, timeout=None):
            self._enter("ws:" + str(payload.get("config_get")), "x")
            if payload.get("config_get") != which:
                return None
            return {"id": payload.get("id"), "config": {"timezone": "UTC"} if which == "timezone" else []}
        self.patch(srv, "_ws_send_recv", reply)

    def test_a_reply_to_the_timezone_read_alone_counts_as_answering(self):
        self.ping_ok = True
        self._answers_only("timezone")
        self.post("/api/poll", ip=IPS[0])
        self.assertIs(self.live.known(IPS[0]), True)

    def test_a_reply_to_the_net_read_alone_counts_as_answering(self):
        self.ping_ok = True
        self._answers_only("net")
        self.post("/api/poll", ip=IPS[0])
        self.assertIs(self.live.known(IPS[0]), True)

    def test_a_unit_that_answers_is_recorded_and_clears_its_count(self):
        self.quiet(IPS[:1], misses=1)
        self.answers = True
        body = self.post("/api/poll", ip=IPS[0])
        self.assertIs(body["ok"], True)
        self.assertIs(self.live.known(IPS[0]), True)
        self.assertEqual(self.live.misses(IPS[0]), 0)

    def test_a_unit_whose_reads_answer_while_the_probe_fails_is_still_polled_fully(self):
        """The check that a failed connectivity probe does not hide a unit that answers (existing rule)."""
        self.answers = False

        def reads_answer(url, payload, timeout=None):
            self._enter("ws:" + str(payload.get("config_get")), "x")
            return {"id": payload.get("id"), "config": {"hostname": "h", "firmwareversion": "1.0"}}
        self.patch(srv, "_ws_send_recv", reads_answer)
        body = self.post("/api/poll", ip=IPS[0])
        self.assertIs(body["ok"], True)
        self.assertGreater(len([c for c in self.calls if c[0].startswith("ws:")]), 1)


class BatchPollTests(Base):
    def test_quiet_decoders_are_skipped_and_say_why(self):
        quiet, live = DECODER_IPS[:30], DECODER_IPS[30:]
        self.quiet(quiet)
        self.answers = True
        body = self.post("/api/poll_decoders", decoders=DECODER_IPS)
        polled = {ip for kind, ip in self.kinds("decoder_inputs")}
        self.assertEqual(polled, set(live))
        for ip in quiet:
            self.assertIn("error", body["results"][ip])
            self.assertIn("back-off", body["results"][ip]["error"])
        for ip in live:
            self.assertNotIn("error", body["results"][ip])
            self.assertEqual(body["results"][ip]["ip1_addr"], "239.9.9.9")

    def test_quiet_encoders_are_skipped(self):
        self.quiet(ENCODER_IPS)
        self.post("/api/poll_encoders", encoders=ENCODER_IPS)
        self.assertEqual(self.kinds("encoder_settings"), [])

    def test_a_forced_batch_polls_everything(self):
        self.quiet(DECODER_IPS)
        self.post("/api/poll_decoders", decoders=DECODER_IPS, force=True)
        self.assertEqual({ip for _k, ip in self.kinds("decoder_inputs")}, set(DECODER_IPS))

    def test_concurrency_is_bounded(self):
        self.delay = 0.03
        self.post("/api/poll_decoders", decoders=DECODER_IPS)
        self.assertLessEqual(self.max_live, srv.POLL_BATCH_WORKERS)
        self.assertGreater(len(self.kinds("decoder_inputs")), 20)

    def test_a_silent_decoder_is_recorded_and_stops_being_asked(self):
        for _ in range(omni_liveness.MISS_LIMIT):
            self.post("/api/poll_decoders", decoders=DECODER_IPS[:5])
        self.calls.clear()
        self.post("/api/poll_decoders", decoders=DECODER_IPS[:5])
        self.assertEqual(self.calls, [])

    def test_answering_units_are_polled_before_silent_ones(self):
        ordered = []
        self.live.record(DECODER_IPS[7], True, "seed")
        self.quiet([DECODER_IPS[0]], misses=1)           # silent but within the limit: still due
        original = srv._poll_plan

        def spy(ips, force=False):
            run, skipped = original(ips, force)
            ordered.extend(run)
            return run, skipped
        self.patch(srv, "_poll_plan", spy)
        self.post("/api/poll_decoders", decoders=[DECODER_IPS[0], DECODER_IPS[3], DECODER_IPS[7]])
        self.assertEqual(ordered[0], DECODER_IPS[7], "the unit known to answer was not first")
        self.assertEqual(ordered[-1], DECODER_IPS[0], "the silent one was not last")

    def test_a_request_returns_at_its_budget_and_the_stragglers_are_discarded(self):
        self.patch(srv, "POLL_BATCH_BUDGET_SECONDS", 0.3)
        self.patch(srv, "POLL_BATCH_WORKERS", 2)
        self.release.clear()                              # every read hangs
        started = time.perf_counter()
        body = self.post("/api/poll_decoders", decoders=DECODER_IPS[:6])
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 3.0, "the request waited out the silent units (%.1fs)" % elapsed)
        self.assertEqual(body["updated"], 0)
        self.assertLessEqual(len(self.kinds("decoder_inputs")), 2, "the pool is two workers wide")
        self.release.set()                                # stragglers finish...
        self._drain_inflight()
        with srv._poll_inflight_lock:
            self.assertEqual(srv._poll_inflight, set(), "a unit stayed claimed after its poll ended")
        for ip in DECODER_IPS[:6]:                        # ...and what they found was not applied
            self.assertNotIn("hdmi", str(omni_matrix_logic._decoders.get(ip, {})))

    def test_a_unit_being_polled_is_not_polled_by_a_second_caller(self):
        self.release.clear()
        first = threading.Thread(target=lambda: self.client.post(
            "/api/poll_decoders", json={"decoders": DECODER_IPS[:4]}))
        first.start()
        deadline = time.monotonic() + 5
        while len(self.kinds("decoder_inputs")) < 4 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(len(self.kinds("decoder_inputs")), 4)
        body = srv.app.test_client().post("/api/poll_decoders", json={"decoders": DECODER_IPS[:4]}).get_json()
        for ip in DECODER_IPS[:4]:
            self.assertEqual(body["results"][ip], {"error": "poll in progress"})
        self.assertEqual(len(self.kinds("decoder_inputs")), 4, "a second caller probed the same units")
        self.release.set()
        first.join(20)

    def test_a_failed_batch_poll_is_not_a_warning(self):
        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record)
        handler = Handler(logging.INFO)
        srv.log.addHandler(handler)
        self.addCleanup(srv.log.removeHandler, handler)
        previous = srv.log.level
        srv.log.setLevel(logging.INFO)
        self.addCleanup(srv.log.setLevel, previous)
        self.post("/api/poll_decoders", decoders=DECODER_IPS[:10])
        noisy = [r for r in records if r.levelno >= logging.WARNING]
        self.assertEqual(noisy, [], "idle polling wrote %d warning(s)" % len(noisy))


class ThumbnailStatusTests(Base):
    """The Device Info page asks once per encoder whenever it draws the table."""

    def test_a_quiet_encoder_is_answered_without_a_read(self):
        self.quiet(ENCODER_IPS[:1])
        body = self.post("/api/get_thumbnail_status", ip=ENCODER_IPS[0])
        self.assertIs(body["ok"], False)
        self.assertIs(body["enable"], False)
        self.assertEqual(self.calls, [], "a unit seen silent a moment ago was read again")

    def test_an_unknown_encoder_is_still_read(self):
        self.post("/api/get_thumbnail_status", ip=ENCODER_IPS[0])
        self.assertTrue(self.kinds("ws:vc2"))

    def test_a_missing_address_is_still_a_bad_request(self):
        reply = self.client.post("/api/get_thumbnail_status", json={})
        self.assertEqual(reply.status_code, 400)


class VerifySweepTests(Base):
    def run_sweep(self):
        srv._cache_verification_in_progress = False
        srv._verify_cache_in_background()
        self.assertTrue(srv.await_startup_tasks(60))
        deadline = time.monotonic() + 30
        while srv._cache_verification_in_progress and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_quiet_units_are_not_verified_on_every_sweep(self):
        self.quiet(IPS[:40])
        self.run_sweep()
        verified = {ip for kind, ip in self.kinds("ws:systeminfo")}
        self.assertTrue(verified.isdisjoint(IPS[:40]), "a quiet unit was probed by the sweep")
        self.assertEqual(verified, set(IPS[40:]))

    def test_a_unit_already_seen_silent_gets_one_attempt_not_three(self):
        self.quiet(IPS[:1], misses=1)       # silent, but due again: a look is allowed, once
        self.patch(srv.time, "sleep", lambda s: None)
        self.run_sweep()
        mine = [c for c in self.kinds("ws:systeminfo") if c[1] == IPS[0]]
        self.assertEqual(len(mine), 1, mine)

    def test_a_unit_never_seen_still_gets_its_retries(self):
        self.patch(srv.time, "sleep", lambda s: None)
        self.run_sweep()
        mine = [c for c in self.kinds("ws:systeminfo") if c[1] == IPS[1]]
        self.assertEqual(len(mine), 3, "startup connectivity tolerance was removed: %r" % mine)


if __name__ == "__main__":
    unittest.main()
