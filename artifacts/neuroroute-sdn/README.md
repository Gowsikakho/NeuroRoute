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

## Research-paper fidelity

The attached brief referenced a paper's Eq. 3, Algorithm 1, and a dashboard page list, but did not include the paper or the page list. The current app therefore does **not** claim exact reproduction of the paper. `preprocessing.py` contains generic timing-indicator and tanh-normalization helpers; their constants and precise definitions must be checked against the paper. The live simulator exposes static Dijkstra, ECMP, and tabular Q-learning. D3QN, LSTM-Q-learning, and Chebyshev-GCN-DQN are listed as unavailable until their paper-specific training/integration work is completed.

To complete an exact reproduction, provide the paper (or its Eq. 3 and Algorithm 1 pages) and the missing dashboard page list.

To install optional model packages on Windows, run `pwsh -File artifacts/neuroroute-sdn/scripts/setup.ps1 -InstallResearchModels`.

After running a simulation long enough to collect queue samples, train the GAT-GRU forecaster from the backend directory with `py -3.11 -m neuroroute_api.ml.train_gat_gru --topology demo-7`. Model weights are generated locally and ignored by Git.

## Experiment exports

Run `pwsh -File artifacts/neuroroute-sdn/scripts/run_all.ps1` from the project root. It compares each enabled algorithm on each built-in topology and writes `experiment-results.csv` plus SVG charts under `artifacts/neuroroute-sdn/generated-artifacts/`. Generated files and the local SQLite database are ignored by Git.

## Tests

From `artifacts/neuroroute-sdn/backend`, run:

```powershell
py -3.11 -m unittest discover -s tests -v
```

For the frontend, run `pnpm --filter @workspace/neuroroute-sdn run typecheck` from the project root.
