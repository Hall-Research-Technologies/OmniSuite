import copy
import unittest
from unittest.mock import patch

import OmniMatrix_upgrade_server_v7_6y as server


def _hdmi_output_with_wall(unit, total_width, total_height, width, height, x, y):
    return {
        "name": "hdmi_output1",
        "sap_input": {},
        "video": {
            "output": {
                "resolution": "3840x2160",
                "framerate": {"mode": "fixed", "framerate": 60},
                "wall": {
                    "enabled": True,
                    "unit": unit,
                    "physical_size": {
                        "width": total_width,
                        "height": total_height,
                    },
                    "input_selection": {
                        "width": width,
                        "height": height,
                        "x": x,
                        "y": y,
                    },
                    "rotation": 0,
                    "edge_compensation": {
                        "mode": "none",
                        "top": 0,
                        "bottom": 0,
                        "left": 0,
                        "right": 0,
                    },
                },
            },
        },
        "output": {"hdcp": {"support_version": "2.2", "supported_versions": ["none", "2.2"]}},
        "audio": {},
    }


class VideoWallUnitTranslationTests(unittest.TestCase):
    def test_set_physical_units_preserves_display_values_in_payload(self):
        current_output = _hdmi_output_with_wall("inches", 16, 9, 8, 4.5, 0, 0)
        sent_payloads = []

        def fake_ws_send_recv(_url, payload, timeout):
            sent_payloads.append(copy.deepcopy(payload))
            if payload.get("config_get") == "hdmi_output":
                return {"config": [copy.deepcopy(current_output)]}
            if payload.get("config_set", {}).get("name") == "hdmi_output":
                return {"ok": True}
            raise AssertionError(f"Unexpected payload: {payload}")

        with patch.object(server, "_ws_send_recv", side_effect=fake_ws_send_recv), \
             patch.object(server, "_ws_get_decoder_inputs", return_value={"video_wall_unit": "inches"}):
            result = server._ws_set_decoder_input_settings(
                "192.168.200.154",
                "admin",
                "password",
                80,
                "/ws",
                4,
                video_wall_unit="inches",
                video_wall_total_width=16,
                video_wall_total_height=9,
                video_wall_width=8,
                video_wall_height=4.5,
                video_wall_horizontal=8,
                video_wall_vertical=4.5,
            )

        self.assertTrue(result["ok"])
        config_set = next(payload for payload in sent_payloads if "config_set" in payload)
        wall = config_set["config_set"]["config"][0]["video"]["output"]["wall"]
        self.assertEqual(wall["physical_size"], {"width": 16.0, "height": 9.0})
        self.assertEqual(wall["input_selection"], {"width": 8.0, "height": 4.5, "x": 8.0, "y": 4.5})

    def test_get_physical_units_does_not_treat_integers_as_grid_counts(self):
        hdmi_output = _hdmi_output_with_wall("mm", 160, 90, 80, 45, 80, 45)

        def fake_ws_send_recv(_url, payload, timeout):
            if payload.get("config_get") == "ip_input":
                return {
                    "config": [
                        {"name": "ip_input1", "enabled": True, "multicast": {"address": "239.0.0.1"}, "port": 1000},
                        {"name": "ip_input3", "enabled": True, "multicast": {"address": "239.0.0.3"}, "port": 1000},
                    ]
                }
            if payload.get("config_get") == "hdmi_output":
                return {"config": [hdmi_output]}
            raise AssertionError(f"Unexpected payload: {payload}")

        with patch.object(server, "_ws_send_recv", side_effect=fake_ws_send_recv):
            fields = server._ws_get_decoder_inputs("192.168.200.154", "admin", "password", 80, "/ws", 4, attempts=1, delay=0)

        self.assertEqual(fields["video_wall_unit"], "mm")
        self.assertEqual(fields["video_wall_width"], 80)
        self.assertEqual(fields["video_wall_height"], 45)
        self.assertEqual(fields["video_wall_horizontal"], 80)
        self.assertEqual(fields["video_wall_vertical"], 45)
        self.assertEqual(fields["video_wall_grid_width"], 2)
        self.assertEqual(fields["video_wall_grid_height"], 2)
        self.assertEqual(fields["video_wall_grid_x"], 1)
        self.assertEqual(fields["video_wall_grid_y"], 1)

    def test_set_normalizes_legacy_edge_mode_spelling(self):
        current_output = _hdmi_output_with_wall("inches", 16, 9, 8, 4.5, 0, 0)
        sent_payloads = []

        def fake_ws_send_recv(_url, payload, timeout):
            sent_payloads.append(copy.deepcopy(payload))
            if payload.get("config_get") == "hdmi_output":
                return {"config": [copy.deepcopy(current_output)]}
            if payload.get("config_set", {}).get("name") == "hdmi_output":
                return {"ok": True}
            raise AssertionError(f"Unexpected payload: {payload}")

        with patch.object(server, "_ws_send_recv", side_effect=fake_ws_send_recv), \
             patch.object(server, "_ws_get_decoder_inputs", return_value={"video_wall_edge_mode": "bezel compensation"}):
            result = server._ws_set_decoder_input_settings(
                "192.168.200.154",
                "admin",
                "password",
                80,
                "/ws",
                4,
                video_wall_edge_mode="bezel_compensation",
            )

        self.assertTrue(result["ok"])
        config_set = next(payload for payload in sent_payloads if "config_set" in payload)
        wall = config_set["config_set"]["config"][0]["video"]["output"]["wall"]
        self.assertEqual(wall["edge_compensation"]["mode"], "bezel compensation")


if __name__ == "__main__":
    unittest.main()
