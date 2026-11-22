import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from satmend import Action, Assessment, Contact, load_scenario, plan_sessions, scenario_to_dict, simulate


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class SimulatorTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = load_scenario(EXAMPLES / "scenario.json", EXAMPLES / "ground_contacts.csv")

    def test_example_merges_contacts_and_recovers_during_gap(self):
        scenario = self.scenario
        self.assertEqual(scenario.ground_contacts, (Contact(0, 6), Contact(14, 22)))
        self.assertEqual(scenario, load_scenario(EXAMPLES / "scenario.json"))
        actions = plan_sessions(scenario)
        self.assertEqual([action.at_s for action in actions], [0.5, 3.0, 5.0])
        report = simulate(scenario, actions)
        self.assertEqual(report["summary"]["recovered_applications"], 4)
        self.assertEqual(report["summary"]["restored_services"], 5)
        self.assertAlmostEqual(report["summary"]["mean_service_outage_s"], 10.14)
        for target in ("payload-a", "payload-b"):
            app = report["applications"][target]
            self.assertLess(6, app["verified_recovery_s"])
            self.assertLess(app["verified_recovery_s"], 14)
            self.assertLessEqual(14, app["first_ground_receipt_s"])
        self.assertEqual(report["applications"]["telemetry"]["service_available_s"],
                         report["applications"]["payload-b"]["verified_recovery_s"])
        cached = [event for event in report["events"] if event["event"] == "package_cached"]
        self.assertEqual(len(cached), 2)
        paused = next(event for event in report["events"] if event["event"] == "transfer_paused")
        resumed = next(event for event in report["events"] if event["event"] == "transfer_resumed")
        self.assertEqual((paused["time_s"], paused["bytes_remaining"]), (6, 20))
        self.assertEqual((resumed["time_s"], resumed["bytes_remaining"]), (14, 20))

    def test_gateway_blocks_forwarding_while_packages_can_be_staged(self):
        actions = tuple(action for action in plan_sessions(self.scenario) if action.kind == "code")
        report = simulate(self.scenario, actions)
        self.assertEqual(report["summary"]["recovered_applications"], 0)
        self.assertTrue(any(event["event"] == "package_cached" for event in report["events"]))
        self.assertTrue(all(transfer["link"] == "crosslink" for transfer in report["pending_transfers"]))
        self.assertEqual(len(report["pending_transfers"]), 3)

    def test_assessment_and_version_checks(self):
        def assess(component, version, targets):
            return Assessment(validated=version != "v1", verification_passes=False)

        actions = plan_sessions(self.scenario, assess=assess)
        bad_version = Action(1, "code", ("payload-next",), "payload", "v1", "wrong", 120)
        report = simulate(self.scenario, (bad_version, *actions))
        rejected = [event["reason"] for event in report["events"] if event["event"] == "action_rejected"]
        self.assertEqual(rejected, ["incompatible_version", "ground_validation_failed",
                                    "ground_validation_failed"])
        self.assertEqual(report["summary"]["recovered_applications"], 1)
        self.assertTrue(any(event["event"] == "target_verified" and not event["passed"]
                            for event in report["events"]))

    def test_inflight_candidate_deduplication(self):
        relay, first, second = plan_sessions(self.scenario)
        report = simulate(self.scenario, (relay, first, first, second))
        self.assertEqual(len(report["jobs"]), 4)
        duplicate_events = [event for event in report["events"]
                            if event.get("reason") == "candidate_already_in_flight"]
        self.assertEqual(len(duplicate_events), 2)

    def test_window_endpoint_and_deadline(self):
        scenario = replace(self.scenario, deadline_s=2.0, ground_contacts=(Contact(0, 1),),
                           uplink_bytes_per_s=20.0)
        report = simulate(scenario, (Action(0, "state", ("relay",)),))
        relay = report["applications"]["relay"]
        self.assertEqual(relay["verified_recovery_s"], 2.0)
        self.assertIsNone(relay["first_ground_receipt_s"])

    def test_invalid_dependencies_and_code_package(self):
        normalized = scenario_to_dict(self.scenario)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            normalized["applications"][0]["dependencies"] = ["payload-a"]
            path.write_text(json.dumps(normalized))
            with self.assertRaisesRegex(ValueError, "cycle"):
                load_scenario(path)
            normalized["applications"][0]["dependencies"] = []
            normalized["applications"][1]["package_bytes"] = 0
            path.write_text(json.dumps(normalized))
            with self.assertRaisesRegex(ValueError, "must be positive"):
                load_scenario(path)
