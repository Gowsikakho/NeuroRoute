from __future__ import annotations

import math
import unittest

from neuroroute_api.preprocessing import dense_timing_indicator, tanh_normalize
from neuroroute_api.queue_agent import QueueRateQLearningAgent
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
