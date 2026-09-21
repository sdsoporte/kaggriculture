# Kaggriculture

Competition tooling for the [Kaggle Kaggriculture](https://www.kaggle.com/) environment:
single-farmer agent plumbing — agent loading, episode running, and paired
benchmarking (same seeds, both seats) to cancel first-seat bias.

Phase 1 scope: wheat-only `baseline_v0` ("Walter, fixed") plus the harness.
Reference ladder agents live in `data/reference-agents/`; research notes in
`docs/kaggriculture-research.md`.

## Setup

```bash
python3.14 -m venv .venv
.venv/bin/pip install numpy scipy pettingzoo kaggle
# pygame is broken in this env on purpose: kaggle-environments is installed
# without deps so rendering never loads.
.venv/bin/pip install --no-deps kaggle-environments==1.32.7
.venv/bin/pip install pytest   # only needed for the tests
```

## Run a match

```bash
.venv/bin/python scripts/run_match.py <a.py> <b.py> --seed 42
```

Agent arguments are single-file agents exposing `agent(obs)`, or builtin names
(`pass`, `random`, `starter`).

## Run a paired benchmark

```bash
.venv/bin/python scripts/benchmark.py <a.py> <b.py> --seeds 8
```

Each seed is played in both seat orders; wins/losses/ties and mean margin are
reported from A's perspective.

## Run the tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Layout

- `src/kaggriculture/loading.py` — load a single-file agent via importlib
- `src/kaggriculture/runner.py` — one episode -> {seed, banks, winner, turns}
- `src/kaggriculture/harness.py` — paired benchmark over seeds, both seats
- `src/kaggriculture/agents/baseline.py` — `baseline_v0` policy
