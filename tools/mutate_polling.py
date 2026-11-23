"""Deliberate-defect check for the offline-polling tests.

    python tools/mutate_polling.py [mutation-name-fragment]

Each mutation breaks one predicate the fix depends on, runs the product's focused test files, and
requires a failure. The file is restored in a finally; the anchor's line endings are translated to the
file's. Whole output is recorded per mutation (never just the last line).
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OMNI = HERE.parent

OMNI_SRV = "OmniMatrix_upgrade_server_v7_6y.py"
OMNI_TESTS = [["-m", "unittest", "tests.test_offline_polling"], ["-m", "unittest", "tests.test_thumbnail_queue_js"]]

MUT = {
    "omni": [
        ("liveness.due-always-true", "omni_liveness.py", "        if force:\n            return True\n        ip = str(ip or \"\").strip()\n        with self._lock:\n            entry = self._seen.get(ip)\n            misses = self._misses.get(ip, 0)",
         "        return True\n        ip = str(ip or \"\").strip()\n        with self._lock:\n            entry = self._seen.get(ip)\n            misses = self._misses.get(ip, 0)"),
        ("liveness.no-jitter", "omni_liveness.py", "    return 0.75 + 0.5 * ((zlib.crc32(str(key).encode(\"utf-8\", \"replace\")) % 1000) / 1000.0)", "    return 1.0"),
        ("liveness.jitter-ignored-by-due", "omni_liveness.py", "self._now() - entry[1] >= wait * jitter(ip)", "self._now() - entry[1] >= wait"),
        ("liveness.no-exponent", "omni_liveness.py", "BACKOFF_BASE_SECONDS * (2 ** min(over, 10))", "BACKOFF_BASE_SECONDS"),
        ("liveness.no-cap", "omni_liveness.py", "return min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** min(over, 10)))", "return BACKOFF_BASE_SECONDS * (2 ** min(over, 10))"),
        ("liveness.miss-limit-zero", "omni_liveness.py", "MISS_LIMIT = 2", "MISS_LIMIT = 0"),
        ("liveness.misses-never-cleared", "omni_liveness.py", "self._misses[ip] = 0 if reachable else min(self._misses.get(ip, 0) + 1, 1000)", "self._misses[ip] = min(self._misses.get(ip, 0) + 1, 1000)"),
        ("liveness.stale-ignores-backoff", "omni_liveness.py", "and now - at >= max(min_age, backoff_seconds(self._misses.get(ip, 0)) * jitter(ip))]", "and now - at >= min_age]"),
        ("poll.no-backoff-gate", OMNI_SRV, "    if data.get(\"force\") is not True and not _liveness.due(ip):", "    if False:"),
        ("poll.force-is-truthy", OMNI_SRV, "    if data.get(\"force\") is not True and not _liveness.due(ip):", "    if not data.get(\"force\") and not _liveness.due(ip):"),
        ('poll.no-silent-short-circuit', OMNI_SRV, '        silent = ((status.get("status") == "disconnected" and not device_contact_ok)\n                  or (not ws_answered and _liveness.known(ip) is False))', '        silent = False'),
        ('poll.no-ping-silent-clause', OMNI_SRV, '                  or (not ws_answered and _liveness.known(ip) is False))', '                  )'),
                ("poll.ws-silent-recorded-up", OMNI_SRV, '        if ws_answered:\n            _record_poll_outcome(ip, True, "answering again")', '        if True:\n            _record_poll_outcome(ip, True, "answering again")'),
        ('poll.usb-read-when-silent', OMNI_SRV, '            if ws_answered and any(m in model for m in usb_models):', '            if any(m in model for m in usb_models):'),
        ('poll.net-reply-not-counted', OMNI_SRV, '            if net_resp:\n                ws_answered = True\n', ''),
        ('poll.transition-always-logs', OMNI_SRV, '    if (before is False) == (not answered):\n        return\n', ''),
        ('poll.tz-reply-not-counted', OMNI_SRV, '            if timezone_resp:\n                ws_answered = True\n', ''),
        ("batch.no-plan-gate", OMNI_SRV, "            elif not _liveness.due(ip, force):\n                skipped[ip] = \"not answering; looked at again on a back-off schedule\"", "            elif False:\n                skipped[ip] = \"x\""),
        ("batch.no-single-flight", OMNI_SRV, "            if ip in _poll_inflight:\n                skipped[ip] = \"poll in progress\"", "            if False:\n                skipped[ip] = \"poll in progress\""),
        ("batch.no-ordering", OMNI_SRV, "        run.sort(key=rank)\n        _poll_inflight.update(run)", "        _poll_inflight.update(run)"),
        ("batch.unbounded-workers", OMNI_SRV, "        self._ex = ThreadPoolExecutor(max_workers=max(1, int(workers)))", "        self._ex = ThreadPoolExecutor(max_workers=1000)"),
        ("batch.no-budget", OMNI_SRV, "            for future in as_completed(futures, timeout=self._budget):", "            for future in as_completed(futures):"),
        ("batch.claim-never-released", OMNI_SRV, "            try:\n                return fn(item)\n            finally:\n                _poll_release([item])", "            return fn(item)"),
        ("batch.decoder-failure-not-recorded", OMNI_SRV, "                _liveness.record(ip, False, \"poll\")\n                log.debug(f\"[POLL_DECODERS] {ip}: {fields}\")", "                log.debug(f\"[POLL_DECODERS] {ip}: {fields}\")"),
        ("batch.decoder-failure-warns", OMNI_SRV, "                _liveness.record(ip, False, \"poll\")\n                log.debug(f\"[POLL_DECODERS] {ip}: {fields}\")", "                _liveness.record(ip, False, \"poll\")\n                log.warning(f\"[POLL_DECODERS] {ip}: {fields}\")"),
        ("verify.no-gate", OMNI_SRV, "                if not _liveness.due(ip):\n                    return None\n                attempts", "                attempts"),
        ("verify.always-three-attempts", OMNI_SRV, "                attempts = 1 if _liveness.known(ip) is False else 3", "                attempts = 3"),
        ("verify.never-three-attempts", OMNI_SRV, "                attempts = 1 if _liveness.known(ip) is False else 3", "                attempts = 1"),
        ("thumb.no-gate", OMNI_SRV, "    if not _liveness.due(device_ip):\n        return jsonify({\"ok\": False, \"enable\": False, \"error\": \"not answering\"}), 200", "    if False:\n        return jsonify({\"ok\": False, \"enable\": False, \"error\": \"not answering\"}), 200"),
        ("ui.thumb-unbounded", "ui/index.html", "const THUMB_STATUS_CONCURRENCY = 3;", "const THUMB_STATUS_CONCURRENCY = 1000;"),
        ("ui.thumb-no-dedupe", "ui/index.html", "  if (last && Date.now() - last < THUMB_STATUS_MIN_MS) return;", "  if (false) return;"),
    ],
}


def run_tests(product):
    root = OMNI
    sets = OMNI_TESTS
    out, ok = [], True
    for args in sets:
        done = subprocess.run([sys.executable] + args, cwd=str(root), capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace")
        out.append("$ python %s -> exit %d\n%s%s" % (" ".join(args), done.returncode, done.stdout[-3000:], done.stderr[-3000:]))
        ok = ok and done.returncode == 0
    return ok, "\n".join(out)


def main():
    product = "omni"
    root = OMNI
    logdir = Path(os.environ.get("OMNI_MUTATION_LOGS") or HERE.parent.parent / "evidence") / "poll_mutations"
    logdir.mkdir(exist_ok=True)
    base_ok, base_out = run_tests(product)
    (logdir / ("%s_baseline.txt" % product)).write_text(base_out, encoding="utf-8")
    print("baseline", "PASS" if base_ok else "FAIL")
    if not base_ok:
        sys.exit(2)
    survivors = []
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for name, rel, old, new in MUT[product]:
        if only and only not in name:
            continue
        path = root / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        crlf = "\r\n" in text
        anchor = old.replace("\n", "\r\n") if crlf else old
        replacement = new.replace("\n", "\r\n") if crlf else new
        if text.count(anchor) != 1:
            print("ANCHOR-MISS", name, text.count(anchor))
            survivors.append(name + " (anchor)")
            continue
        try:
            path.write_bytes(text.replace(anchor, replacement).encode("utf-8"))
            ok, out = run_tests(product)
        finally:
            path.write_bytes(original)
        (logdir / ("%s_%s.txt" % (product, name))).write_text(out, encoding="utf-8")
        print("%-40s %s" % (name, "SURVIVED" if ok else "caught"))
        if ok:
            survivors.append(name)
    print("survivors:", survivors)


if __name__ == "__main__":
    main()
