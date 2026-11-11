"""Mutations for Display Group integrity: a group route reaches its members only.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must
occur exactly once; the harness translates its newlines to the file's own.
Imported by mutate_security.py and appended to MUTATIONS, so
`python tools/mutate_security.py displaygroup.` runs exactly these.
"""
DG = ["tests.test_display_group_integrity"]
DGIN = ["tests.test_inventory_identity_ingest"]
DGMV = ["tests.test_multiview_group_identity"]
DGJS = ["tests.test_matrix_selection_js"]

DISPLAY_GROUP_MUTATIONS = [
    # ---- route-time resolution fails closed on an ambiguous record ----
    ("displaygroup.ambiguity-ignored", "omni_remote_api.py",
     "            elif len(found) > 1 or any(owners[a] != {identity} for a in found):",
     "            elif False:",
     "an identity recorded twice, or an address two identities claim, would be "
     "routed to whichever row came last, which can be a display outside the group",
     DG),
    ("displaygroup.two-addresses-allowed", "omni_remote_api.py",
     "            elif len(found) > 1 or any(owners[a] != {identity} for a in found):",
     "            elif any(owners[a] != {identity} for a in found):",
     "one display recorded at two addresses would be routed at an arbitrary one",
     DG),
    ("displaygroup.shared-address-allowed", "omni_remote_api.py",
     "            elif len(found) > 1 or any(owners[a] != {identity} for a in found):",
     "            elif len(found) > 1:",
     "a stale row at an address another display now holds would route that other "
     "display as though it were the member",
     DG),
    ("displaygroup.group-member-ambiguity-ignored", "omni_remote_api.py",
     "                    if \"ambiguous\" in (encoder_why, decoder_why):\n"
     "                        return {\"ok\": False, \"result\": \"ambiguous\", \"name\": name}",
     "                    if False:\n"
     "                        return {\"ok\": False, \"result\": \"ambiguous\", \"name\": name}",
     "the group route would write to an address it could not attribute",
     DG),
    ("displaygroup.group-source-ambiguity-ignored", "omni_remote_api.py",
     "                    if \"ambiguous\" in (encoder_why, decoder_why):\n"
     "                        return {\"ok\": False, \"result\": \"ambiguous\", \"name\": name}",
     "                    if \"ambiguous\" in (decoder_why,):\n"
     "                        return {\"ok\": False, \"result\": \"ambiguous\", \"name\": name}",
     "an ambiguous source would put an arbitrary encoder on every display in the group",
     DG),
    ("displaygroup.single-route-ambiguity-ignored", "omni_remote_api.py",
     "        decoder_ip, decoder_why = resolved[destination_id]\n"
     "        if \"ambiguous\" in (encoder_why, decoder_why):",
     "        decoder_ip, decoder_why = resolved[destination_id]\n"
     "        if False:",
     "a single-display route would resolve an ambiguous record by guessing",
     DG),
    ("displaygroup.unroute-ambiguity-ignored", "omni_remote_api.py",
     "        if why == \"ambiguous\":",
     "        if False:",
     "Stop would clear an arbitrary display when its record is ambiguous",
     DG + ["tests.test_user_operations"]),
    # ---- the intent is recorded before the write ----
    ("displaygroup.intent-not-info", "omni_remote_api.py",
     "        log.info(\"[GROUP-ROUTE] group=%s source=%s mode=%s interface=%s \"",
     "        log.debug(\"[GROUP-ROUTE] group=%s source=%s mode=%s interface=%s \"",
     "a mutation's intended targets would not be in the log at its normal level",
     DG),
    ("displaygroup.intent-after-route", "omni_remote_api.py",
     "        summary = omni_groups.run_sequential(group[\"members\"], route_member)\n"
     "        store.audit(",
     "        summary = omni_groups.run_sequential(group[\"members\"], route_member)\n"
     "        log.info(\"[GROUP-ROUTE] group=%s source=%s mode=%s interface=%s \"\n"
     "                 \"intended=%s\", group[\"id\"], source_id, mode, interface,\n"
     "                 \",\".join(group[\"members\"]))\n"
     "        store.audit(",
     "the log would show what was intended only after the displays had changed",
     DG),
    ("displaygroup.audit-intended-dropped", "omni_remote_api.py",
     "                \"intended\": list(group[\"members\"])})",
     "                \"intended\": []})",
     "the audit entry would not say which displays the request intended",
     DG),
    # ---- ingest keeps identity and address apart ----
    ("displaygroup.ingest-dedupe-not-canonical", "OmniMatrix_upgrade_server_v7_6y.py",
     "        mac = _device_identity(u)\n        if mac and mac in seen:",
     "        mac = (u.get(\"mac\") or \"\").lower()\n        if mac and mac in seen:",
     "a MAC written with dashes and the same MAC with colons would be two devices, "
     "and one hardware identity would sit under two addresses",
     DGIN),
    ("displaygroup.ingest-inherit-by-address", "OmniMatrix_upgrade_server_v7_6y.py",
     "if old.get(\"ip\") == u.get(\"ip\") and not _device_identity(old)), None)",
     "if old.get(\"ip\") == u.get(\"ip\")), None)",
     "a unit whose MAC could not be read would inherit the timezone, system info and "
     "location of whichever known unit used to hold its address",
     DGIN),
    ("displaygroup.ingest-inherit-not-canonical", "OmniMatrix_upgrade_server_v7_6y.py",
     "if mac and _device_identity(old) == mac), None)",
     "if mac and (old.get(\"mac\") or \"\") == mac), None)",
     "a cached unit's learned fields would be lost when its MAC is written differently",
     DGIN),
    ("displaygroup.load-lends-across-identities", "OmniMatrix_upgrade_server_v7_6y.py",
     "                                    base = {}\n",
     "                                    pass\n",
     "a scan row for another device would lend its details to the cached one, "
     "producing one device with two devices' facts",
     DGIN),
    ("displaygroup.load-no-identity-dedupe", "OmniMatrix_upgrade_server_v7_6y.py",
     "                                            or preferred.get(_device_identity(row)) == address]",
     "                                            or True]",
     "one hardware identity could load under two addresses",
     DGIN),
    # ---- a saved Multiview group refers to displays by identity ----
    ("displaygroup.mvgroup-falls-back-to-address", "OmniMatrix_upgrade_server_v7_6y.py",
     "            if _device_identity(unit) == identity:\n                return unit\n        return None\n",
     "            if _device_identity(unit) == identity:\n                return unit\n",
     "a group member whose MAC is missing would resolve to whatever unit holds its old address",
     DGMV),
    ("displaygroup.mvgroup-compare-not-canonical", "OmniMatrix_upgrade_server_v7_6y.py",
     "            if _device_identity(unit) == identity:\n                return unit\n",
     "            if (unit.get(\"mac\") or \"\") == (member.get(\"mac\") or \"\"):\n                return unit\n",
     "a MAC written two ways would fail to match its own group member",
     DGMV),
    ("displaygroup.mvgroup-install-by-stored-address", "OmniMatrix_upgrade_server_v7_6y.py",
     "            unit = _resolve_group_member(stored, current)\n            if unit is not None and unit.get(\"ip\"):\n                targets.append(unit.get(\"ip\"))\n",
     "            unit = _resolve_group_member(stored, current)\n            if stored.get(\"ip\"):\n                targets.append(stored.get(\"ip\"))\n",
     "the layout library would be installed on whatever unit holds a member's stored address",
     DGMV),
    # ---- the matrix page's multi-display selection is identity, not address ----
    ("displaygroup.selection-by-address", "ui/matrix/matrix.js",
     "  return mac.length === 12 ? 'mac:' + mac : 'ip:' + String((d && d.ip) || '');",
     "  return 'ip:' + String((d && d.ip) || '');",
     "a tick would follow an address, so a display that takes over a moved "
     "display's address would be routed as part of the selection",
     DGJS),
    ("displaygroup.selection-not-pruned", "ui/matrix/matrix.js",
     "  Array.from(selectedDecoders).forEach(key => { if (!present.has(key)) selectedDecoders.delete(key); });",
     "  void present;",
     "a unit that arrives later would inherit a tick left by one that is gone",
     DGJS),
    ("displaygroup.selection-not-filtered", "ui/matrix/matrix.js",
     "  return (dec || []).filter(d => wanted.has(decoderKey(d))).map(d => d.ip);",
     "  return (dec || []).map(d => d.ip);",
     "every display would be routed whether or not it was ticked",
     DGJS),
    ("displaygroup.change-records-address", "ui/matrix/matrix.js",
     "      const key = el.getAttribute('data-dec-key');\n"
     "      if (el.checked) selectedDecoders.add(key); else selectedDecoders.delete(key);\n"
     "      syncMasterCheckbox(t);",
     "      const key = el.getAttribute('data-dec-ip');\n"
     "      if (el.checked) selectedDecoders.add(key); else selectedDecoders.delete(key);\n"
     "      syncMasterCheckbox(t);",
     "the tick handler would store an address where an identity is expected",
     DGJS),
]
