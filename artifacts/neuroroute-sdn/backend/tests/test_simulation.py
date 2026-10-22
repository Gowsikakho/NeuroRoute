from __future__ import annotations

import math
import unittest

from neuroroute_api.preprocessing import dense_timing_indicator, tanh_normalize
from neuroroute_api.controller import SimulatedOpenFlowController
from neuroroute_api.queue_agent import QueueRateQLearningAgent
from neuroroute_api.queue_model import QUEUE_CAPACITY_BYTES, OutputPortQueue, Packet
from neuroroute_api.simulation import AVAILABLE_ALGORITHMS, SimulationManager
from neuroroute_api.topologies import TOPOLOGIES


class TopologyTests(unittest.TestCase):
    def test_expected_topology_sizes(self) -> None:
        self.assertEqual(TOPOLOGIES["nsfnet"].node_count, 14)
        self.assertEqual(len(TOPOLOGIES["nsfnet"].edges), 21)
        self.assertEqual(TOPOLOGIES["abilene"].node_count, 11)
        self.assertEqual(TOPOLOGIES["demo-7"].node_count, 7)

    def test_node_and_link_ids_are_consistent(self) -> None:
        for topology in TOPOLOGIES.values():
            data = topology.as_dict()
            node_ids = {node["id"] for node in data["nodes"]}
            self.assertEqual(len(node_ids), topology.node_count)
            for link in data["links"]:
                self.assertIn(link["source"], node_ids)
                self.assertIn(link["target"], node_ids)


class SimulationTests(unittest.TestCase):
    def test_short_static_experiment_returns_finite_metrics(self) -> None:
        result = SimulationManager.run_offline("demo-7", "static-dijkstra", 10, 7)
        self.assertGreaterEqual(result["meanLatencyMs"], 0)
        self.assertGreater(result["throughputMbps"], 0)
        self.assertGreaterEqual(result["packetLossPercent"], 0)
        self.assertTrue(math.isfinite(result["meanQueueOccupancy"]))

    def test_all_marked_available_algorithms_run(self) -> None:
        for algorithm in AVAILABLE_ALGORITHMS:
            with self.subTest(algorithm=algorithm):
                result = SimulationManager.run_offline("demo-7", algorithm, 10, 11)
                self.assertEqual(result["algorithm"], algorithm)
                self.assertGreaterEqual(result["throughputMbps"], 0)

    def test_queue_rate_agent_stays_within_valid_split(self) -> None:
        agent = QueueRateQLearningAgent(seed=19)
        for _ in range(12):
            rate = agent.choose_rate(25.0, 40.0)
            self.assertGreaterEqual(rate, 0.0)
            self.assertLessEqual(rate, 1.0)

    def test_seeded_run_has_repeatable_metric_output(self) -> None:
        first = SimulationManager.run_offline("demo-7", "static-dijkstra", 10, 2026)
        second = SimulationManager.run_offline("demo-7", "static-dijkstra", 10, 2026)
        self.assertEqual(first, second)

    def test_failure_is_scheduled_and_active_flows_reroute(self) -> None:
        runner = SimulationManager(persist=False)
        runner._configure("demo-7", "static-dijkstra", 10, 91, 35, 50)
        flow = runner._flows[0]
        first_hop = tuple(flow.path[:2])
        edge = next(
            (left, right)
            for left, right in TOPOLOGIES["demo-7"].edges
            if {f"s{left}", f"s{right}"} == set(first_hop)
        )
        link_id = f"s{edge[0]}-s{edge[1]}"

        runner._configure(
            "demo-7",
            "static-dijkstra",
            10,
            91,
            35,
            50,
            failure_schedule={
                "linkId": link_id,
                "downSeconds": 2,
                "upSeconds": 5,
            },
        )
        assert runner._environment is not None
        runner._environment.run(until=10.00001)

        self.assertEqual([event["status"] for event in runner._link_events], ["down", "up"])
        self.assertGreater(runner._reroute_count, 0)


class QueueModelTests(unittest.TestCase):
    def test_ls_and_ts_have_independent_byte_capacity(self) -> None:
        port = OutputPortQueue("s0", "s1", 10)
        ls_packet = Packet("ls-1", "ls", QUEUE_CAPACITY_BYTES, 0, ["s0", "s1"])
        ts_packet = Packet("ts-1", "ts", QUEUE_CAPACITY_BYTES, 0, ["s0", "s1"])

        self.assertTrue(port.enqueue(ls_packet))
        self.assertFalse(port.enqueue(ls_packet))
        self.assertTrue(port.enqueue(ts_packet))
        self.assertEqual(port.occupancy_bytes, {"ls": QUEUE_CAPACITY_BYTES, "ts": QUEUE_CAPACITY_BYTES})

    def test_controller_rate_install_changes_service_share(self) -> None:
        controller = SimulatedOpenFlowController()
        controller.reset(["s0", "s1"])

        controller.install_queue_rate("s0", 0.25)

        self.assertEqual(controller.queue_rate("s0", 0.62), 0.25)
        self.assertEqual(controller.queue_rate("s1", 0.62), 0.62)
        self.assertEqual(controller.state([], 0.4)["switches"][0]["queues"]["lsRatePercent"], 25)

    def test_transmit_rate_uses_each_poll_interval_not_total_run_time(self) -> None:
        port = OutputPortQueue("s0", "s1", 10)
        port.interval_tx_bytes = 125_000
        first = port.sample_and_reset(1, 1_000, 1)
        port.interval_tx_bytes = 125_000
        second = port.sample_and_reset(1, 2_000, 2)

        self.assertEqual(first["transmitRateMbps"], 1.0)
        self.assertEqual(second["transmitRateMbps"], 1.0)


class PreprocessingTests(unittest.TestCase):
    def test_dense_timing_indicator_and_tanh_normalization(self) -> None:
        matrix = dense_timing_indicator([10.0, 11.0, 12.0], 1.0, 4)
        self.assertEqual(matrix, [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]])
        normalized = tanh_normalize([0.0, 1.0, 2.0], 1.0, 1.0)
        self.assertAlmostEqual(normalized[1], 0.0)
        self.assertLess(normalized[0], 0.0)
        self.assertGreater(normalized[2], 0.0)


if __name__ == "__main__":
    unittest.main()
