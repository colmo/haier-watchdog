import json
import tempfile
import unittest
from pathlib import Path

from haier_watchdog.diagnose import guess_source, mqtt_changed_params, slim_history_item
from haier_watchdog.event_log import EventLog


class GuessSourceTest(unittest.TestCase):
    def test_recent_app_command_is_high_confidence(self) -> None:
        recent = [
            {
                "commandName": "startProgram",
                "channel": "mobileApp",
                "onOffStatus": "1",
                "local_time": "2026-08-29T10:00:00+02:00",
                "device": {"mobileOs": "ios", "deviceModel": "iPhone16,1"},
            }
        ]
        guess = guess_source(
            operation_source=0,
            last_activity={"channel": "appliance"},
            recent_commands=recent,
            mqtt_params=["onOffStatus"],
        )
        self.assertEqual(guess["label"], "app_hOn")
        self.assertEqual(guess["confidence"], "alta")

    def test_no_cloud_command_looks_local(self) -> None:
        guess = guess_source(
            operation_source=0,
            last_activity={"channel": "appliance", "origin": "dashboard"},
            recent_commands=[],
            mqtt_params=["onOffStatus", "tempIndoor"],
        )
        self.assertEqual(guess["label"], "termostato_o_mando_local")
        self.assertEqual(guess["confidence"], "media")

    def test_operation_source_3_looks_cloud(self) -> None:
        guess = guess_source(
            operation_source=3,
            last_activity={"origin": "dashboard"},
            recent_commands=[],
            mqtt_params=["onOffStatus"],
        )
        self.assertEqual(guess["label"], "nube_programacion_o_asistente")

    def test_mqtt_param_names(self) -> None:
        names = mqtt_changed_params(
            [
                {
                    "payload": {
                        "parameters": [
                            {"parName": "onOffStatus", "parNewVal": "1"},
                            {"parName": "tempOutdoor", "parNewVal": "29"},
                        ]
                    }
                }
            ]
        )
        self.assertEqual(names, ["onOffStatus", "tempOutdoor"])


class SlimHistoryTest(unittest.TestCase):
    def test_drops_pk_and_keeps_device(self) -> None:
        slim = slim_history_item(
            {
                "PK": "user#secret",
                "timestampExecuted": "2026-05-28T14:31:52.2Z",
                "command": {
                    "commandName": "startProgram",
                    "attributes": {"channel": "mobileApp", "origin": "standardProgram"},
                    "parameters": {"onOffStatus": "1", "tempSel": "22"},
                    "device": {"mobileOs": "ios", "deviceModel": "iPhone16,1"},
                },
            },
            "Europe/Madrid",
        )
        self.assertEqual(slim["commandName"], "startProgram")
        self.assertEqual(slim["channel"], "mobileApp")
        self.assertIn("2026-05-28", slim["local_time"] or "")
        self.assertNotIn("PK", slim)


class EventLogTest(unittest.TestCase):
    def test_appends_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            log = EventLog(path, "Europe/Madrid")
            log.write("ac_on", {"event_id": "abc", "guess": {"label": "app_hOn"}})
            log.write("ac_off", {"trigger": "mqtt"})
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            self.assertEqual(first["event"], "ac_on")
            self.assertEqual(first["event_id"], "abc")
            self.assertIn("ts", first)


if __name__ == "__main__":
    unittest.main()
