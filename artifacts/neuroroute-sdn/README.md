# NeuroRoute-SDN

NeuroRoute-SDN is a Windows-first SDN research simulator with a React dashboard and a Python 3.11 FastAPI backend. The default backend uses SimPy for event-driven traffic and queue simulation, NetworkX for topology/path algorithms, and SQLite/SQLAlchemy for experiment and queue-sample persistence.

## Windows 10/11 quick start

1. Install Python 3.11, Node.js, and pnpm.
2. From the project root, run `pwsh -File artifacts/neuroroute-sdn/scripts/setup.ps1`.
3. Run `pwsh -File artifacts/neuroroute-sdn/scripts/start_dev.ps1`.
4. Open the Vite URL shown in the terminal. The script starts FastAPI on `127.0.0.1:8000` and the dashboard on port `5173`.

Paths used by the Python backend are built with `pathlib`. The simulator does not require Linux, WSL2, Docker, or a VM. The optional model trainer uses `DataLoader(num_workers=0)`, and executable entry points use a `__main__` guard.

## Current simulator capabilities

- NSFNET (14 nodes), Abilene (11 nodes), and a 7-switch demo topology.
- SimPy-driven latency-sensitive Poisson traffic and throughput-sensitive constant-rate traffic.
- Static Dijkstra and seeded ECMP route selection, plus a tabular Q-learning LS/TS service-rate controller.
- Simulated OpenFlow-style flow tables, port counters, queue rates, flow-rule installation, and a WebSocket stream at `/api/ws/simulation`.
- Queue occupancy time-series capture and SQLite persistence.
- Comparative baseline runs saved to SQLite, with CSV and SVG exports from `scripts/run_all.ps1`.
- Research model source for GAT-GRU occupancy forecasting, dueling Double-DQN, and LSTM/Chebyshev-GCN baselines. Install `backend/requirements-ml.txt` when PyTorch and PyTorch Geometric are needed.
- A Ryu REST adapter is provided for optional WSL2 use. It requires a separately configured Mininet/Ryu environment and has not been validated against a particular Ryu/QoS plugin version.

## Simulator model assumptions

- Every directed output port has two independent FIFO queues: latency-sensitive (LS) and throughput-sensitive (TS). Each queue has a 64 KiB byte capacity and a token-bucket service rate. The controller can change the LS/TS service-rate split while a run is active.
- LS traffic consists of 200-byte packets arriving as a Poisson process during alternating exponential on/off bursts. TS traffic is constant bit rate with 1,500-byte packets. Each flow has a deterministic random stream derived from the run seed and flow ID.
- Congestion is sampled once per simulated second. Reactive baselines reroute when a path queue reaches 80% occupancy. The proactive threshold baseline forecasts two polling steps ahead (k = 2) and begins rerouting when that projection reaches the threshold. Every route installation takes effect after a seeded random delay uniformly sampled from 50–200 ms. This is a modeling assumption, not a measured controller timing guarantee.
- Optional link failures are scheduled by simulated time. The selected link is taken down and restored at the configured times; active flows using it compute a route that excludes the failed link, with the same rule-installation delay.
- Each one-second sample includes per-port LS/TS occupancy, queue drops, byte capacities, and transmit rate. Simulation state also reports per-flow delivered/dropped packet counts, mean latency, and loss.
- Fast mode advances simulated time as quickly as possible and is intended for experiments. Real-time mode advances at approximately one simulated second per wall-clock second for live demonstrations.
- Numeric simulation results are deterministic for the same topology, algorithm, duration, rates, failure schedule, and seed. Run IDs and wall-clock timestamps are metadata and are not expected to repeat.

## Research-paper fidelity

The attached brief referenced a paper's Eq. 3, Algorithm 1, and a dashboard page list, but did not include the paper or the page list. The current app therefore does **not** claim exact reproduction of the paper. `preprocessing.py` contains generic timing-indicator and tanh-normalization helpers; their constants and precise definitions must be checked against the paper. The live simulator exposes static Dijkstra, ECMP, and tabular Q-learning. D3QN, LSTM-Q-learning, and Chebyshev-GCN-DQN are listed as unavailable until their paper-specific training/integration work is completed.

To complete an exact reproduction, provide the paper (or its Eq. 3 and Algorithm 1 pages) and the missing dashboard page list.

To install optional model packages on Windows, run `pwsh -File artifacts/neuroroute-sdn/scripts/setup.ps1 -InstallResearchModels`.

After running a simulation long enough to collect queue samples, train the GAT-GRU forecaster from the backend directory with `py -3.11 -m neuroroute_api.ml.train_gat_gru --topology demo-7`. Model weights are generated locally and ignored by Git.

## Experiment exports

Run `pwsh -File artifacts/neuroroute-sdn/scripts/run_all.ps1` from the project root. It compares each enabled algorithm on each built-in topology and writes `experiment-results.csv` plus SVG charts under `artifacts/neuroroute-sdn/generated-artifacts/`. Generated files and the local SQLite database are ignored by Git.

## Tests

From the workspace root, run:

```sh
uv run --project . --directory artifacts/neuroroute-sdn/backend pytest
```

The pytest suite includes a seeded M/M/1 queue check against the theoretical mean delay `1 / (service rate - arrival rate)` with a 5% tolerance.

For the frontend, run `pnpm --filter @workspace/neuroroute-sdn run typecheck` from the project root.
