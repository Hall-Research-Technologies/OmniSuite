"""Mutations for the local application port and the certificate wording.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must
occur exactly once; the harness translates its newlines to the file's own.
Imported by mutate_security.py and appended to MUTATIONS, so
`python tools/mutate_security.py localport. certificate.` runs exactly these.
"""
LP = ["tests.test_local_port"]
LPL = ["tests.test_local_port_launcher"]
LPUI = ["tests.test_local_port_ui"]
LPSEC = ["tests.test_local_port", "tests.test_security_review"]
CERTUI = ["tests.test_local_port_ui", "tests.test_certificate_state"]

LOCAL_PORT_MUTATIONS = [
    # ---- nothing is saved before the port it names has been bound ----
    ("localport.persist-before-bind", "omni_local_port.py",
     "            if self.actual_port is None or int(port) != self.actual_port:\n"
     "                raise RuntimeError(\"a port is saved only after it has been bound\")",
     "            if False:\n"
     "                raise RuntimeError(\"a port is saved only after it has been bound\")",
     "a port could be saved as the fixed port before anything had bound it, so a "
     "mistyped port would be remembered and fail on every start",
     LP),
    # ---- the temporary alternate is session-only ----
    ("localport.alternate-overwrites-fixed", "app_launcher.py",
     "            if kind == \"change\":\n"
     "                if not self.supervisor.persist_after_bind(port):",
     "            if kind in (\"change\", \"alternate\"):\n"
     "                if not self.supervisor.persist_after_bind(port):",
     "the emergency alternate port would replace the operator's fixed port, so the "
     "next start would not retry it",
     LPL),
    # ---- a fixed port is bound exactly, never drifted from ----
    ("localport.silent-drift-in-fixed-mode", "app_launcher.py",
     "            self.port = stored[\"port\"]",
     "            self.port = self.find_available_port(stored[\"port\"])",
     "a busy fixed port would quietly become another port and the browser address "
     "the operator relies on would change without a word",
     LPL),
    ("localport.fixed-mode-takes-the-automatic-path", "app_launcher.py",
     "        if self.fixed_mode:\n"
     "            self.begin_attempt(self.port, \"startup\")\n"
     "            return",
     "        if False:\n"
     "            self.begin_attempt(self.port, \"startup\")\n"
     "            return",
     "fixed mode would run the automatic start-up, with its drifting port search",
     LPL),
    # ---- the local-only boundary ----
    ("localport.remote-reaches-the-endpoint", "omni_access.py",
     "    \"/api/local_port/fixed\": (Access.LOCAL_ONLY, None),",
     "    \"/api/local_port/fixed\": (Access.ADMIN, None),",
     "a remote administrator could change the port, and restart the application "
     "under whoever is using it",
     LPSEC),
    ("localport.remote-reaches-the-status", "omni_access.py",
     "    \"/api/local_port\": (Access.LOCAL_ONLY, None),",
     "    \"/api/local_port\": (Access.ADMIN, None),",
     "a remote administrator could read the computer's local port configuration",
     LPSEC),
    # ---- the file can never stop OmniSuite starting ----
    ("localport.corrupt-file-prevents-start", "omni_local_port.py",
     "            except (OSError, ValueError) as exc:\n"
     "                _quarantine(path, type(exc).__name__)\n"
     "                result[\"problem\"] = \"unreadable\"\n"
     "                return result",
     "            except (OSError, ValueError) as exc:\n"
     "                raise",
     "a damaged settings file would stop the launcher starting, and with it the "
     "only place the operator could recover from it",
     LP + LPL),
    ("localport.automatic-writes-state", "app_launcher.py",
     "                    self.supervisor.bound(self.port)\n"
     "                    httpd.serve_forever()",
     "                    self.supervisor.bound(self.port)\n"
     "                    self.supervisor.persist_after_bind(self.port)\n"
     "                    httpd.serve_forever()",
     "ordinary automatic startup would silently turn itself into a fixed port",
     LPL),
    # ---- validation and the advisory/authoritative split ----
    ("localport.range-check-dropped", "omni_local_port.py",
     "    if port < MIN_PORT or port > MAX_PORT:",
     "    if port > MAX_PORT:",
     "a port below 1024 would be accepted",
     LP),
    ("localport.set-fixed-skips-the-probe", "omni_local_port.py",
     "            if port != holder:",
     "            if False:",
     "a port something else holds would be saved and the application restarted into "
     "a conflict",
     LP),
    ("localport.listener-that-shares-its-port-is-not-a-conflict", "omni_local_port.py",
     "    if _accepting(host, port):\n"
     "        raise PortUnavailable(port, True)",
     "    if False:\n"
     "        raise PortUnavailable(port, True)",
     "a second OmniSuite (whose server shares its port) would be silently doubled up "
     "on the fixed port instead of reported",
     LP),
    # ---- the restart ----
    ("localport.restart-releases-after-the-new-process", "app_launcher.py",
     "        self.release_for_restart()\n"
     "        if not self.spawn_replacement():",
     "        started = self.spawn_replacement()\n"
     "        self.release_for_restart()\n"
     "        if not started:",
     "the new process would start while the old one still held the port and the data "
     "folder, creating the conflict it was started to avoid",
     LPL),
    ("localport.restarted-process-is-refused-by-its-predecessor", "app_launcher.py",
     "        deadline = time.monotonic() + (15.0 if os.environ.get(\"OMNI_RESTARTED\") else 0.0)",
     "        deadline = time.monotonic()",
     "a restarted process would be refused by the single-instance guard for the few "
     "milliseconds its predecessor needs to let go",
     LPL),
    ("localport.launcher-callback-failures-are-lost", "app_launcher.py",
     "        self.root.report_callback_exception = self._log_callback_exception" + chr(10),
     "",
     "a failure inside a launcher button would vanish: the packaged launcher has no "
     "console, so nothing would say why the button did nothing",
     LPL),
    # ---- backup scope ----
    ("localport.carried-in-the-backup", "omni_backup.py",
     "    Section(\"topology_notices\", \"topology_ack.json\"),\n)",
     "    Section(\"topology_notices\", \"topology_ack.json\"),\n"
     "    Section(\"local_port\", \"local_port.json\"),\n)",
     "a backup would carry a port that belongs to the computer it was made on",
     LP),
    # ---- the page ----
    ("localport.prefill-uses-a-hard-coded-port", "ui/local-port.js",
     "          value: String(actual),",
     "          value: '8080',",
     "the dialog would suggest a port the application is not using",
     LPUI),
    ("localport.cancel-still-saves", "ui/local-port.js",
     "      if (port === null) { await refresh(); return; }      // Cancel changes nothing\n"
     "      await saveFixed(port);",
     "      await saveFixed(port === null ? current.actual_port : port);",
     "Cancel in the enable dialog would save the port and restart the application",
     LPUI),

    # ---- the certificate wording ----
    ("certificate.label-from-the-selection", "ui/settings.js",
     "      option.textContent = httpsLabel(certificateKind((state || {}).certificate, state));",
     "      option.textContent = httpsLabel((state || {}).transport === 'https' ? 'self_signed' : 'required');",
     "choosing HTTPS would read as an active certificate whether or not one exists",
     CERTUI),
    ("certificate.unusable-pair-reads-as-active", "ui/settings.js",
     "    if (cert.usable === false) return 'required';",
     "",
     "a certificate whose key is missing or does not match would be called active",
     CERTUI),
    ("certificate.supplied-is-judged-against-the-adapter", "ui/settings.js",
     "    if (!supplied && cert.for_address && cert.matches_address === false) {\n      return 'required';",
     "    if (cert.for_address && cert.matches_address === false) {\n      return 'required';",
     "a site's own certificate would be called unusable because OmniSuite does not "
     "know the name it carries",
     CERTUI),
    ("certificate.self-signed-is-called-trusted", "ui/settings.js",
     "      words += ' Your browser may warn because this certificate is self-signed.';",
     "      words += ' This certificate is trusted.';",
     "OmniSuite would tell the operator a self-signed certificate is trusted",
     CERTUI),
    ("certificate.server-stops-checking-the-pair", "omni_remote_api.py",
     "        certificate[\"usable\"] = bool(certificate.get(\"present\")) and not https_error",
     "        certificate[\"usable\"] = bool(certificate.get(\"present\"))",
     "the server would report a certificate as usable without trying to load it",
     CERTUI),
]
