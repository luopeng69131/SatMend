<div align="center">

# SatMend

**Software Recovery for Satellite Constellations with Intermittent Ground Contact**

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MIT License](https://img.shields.io/badge/License-MIT-3D7458)](LICENSE)

**English · [简体中文](README.zh-CN.md)**

[Quick start](#quick-start) · [Method](#how-satmend-works) · [Simulator](#configure-the-simulator)

</div>

<p align="center"><img src="assets/background.jpg" width="720" alt="Ground maintenance reaches different onboard software versions through intermittent contact and a gateway satellite"></p>
<p align="center"><sub>One maintenance window can serve applications on several satellites through a common gateway.</sub></p>

## Why SatMend?

Satellite constellations can run multiple versions of the same component while depending on services across spacecraft. When an application fails, a repair must reach the right version through a limited contact window and pass verification at the target. A healthy application can also lose service when an upstream dependency fails.

**SatMend coordinates repair preparation on the ground with recovery onboard.** This repository lets you model that process and compare your own maintenance strategies.

- **Discrete-event simulator** for intermittent links, resumable transfers, gateway forwarding, and dependent services.
- **Reference planner** for cross-version repair sessions, separate validation, and delivery as candidates become ready.
- **Scenario inputs and result traces** for configuring experiments and inspecting target recovery, service availability, and ground receipts.

## Quick start

### Get the example

Use Python 3.9 or newer. The example runs with the standard library.

```bash
git clone https://github.com/luopeng69131/SatMend.git
cd SatMend
mkdir -p outputs
```

### Run the simulator

```bash
python3 -m satmend run examples/scenario.json \
  --contacts examples/ground_contacts.csv \
  --output outputs/result.json
python3 -c 'import json; print(json.load(open("outputs/result.json"))["summary"])'
```

In the three-satellite example, the gateway recovers first. Two payload replicas complete onboard verification during the contact gap, and the ground receives their confirmations after contact resumes.

| Example outcome at 22 s | Result |
| --- | ---: |
| Faulty applications recovered | **4 / 4** |
| Affected services restored | **5 / 5** |
| Mean outage across affected services | **10.14 s** |

<details>
<summary><b>Install the satmend command</b></summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
satmend --help
```

</details>

## How SatMend works

<p align="center"><img src="assets/method.jpg" width="720" alt="SatMend framework showing cross-version repair sessions, separate validation, and timely delivery"></p>

1. **Organize repair sessions by component.** Assign related versions to the same ground repair slot so diagnostic context can carry over.
2. **Validate each version separately.** Set the validation time and result for each version, then emit an action when it is ready. State recovery actions carry commands.
3. **Deliver and verify.** Stage candidates and commands at the gateway as contact permits, forward them to compatible targets, and record onboard verification and ground receipts.

The [reference planner](satmend/method.py) models preparation and validation timing. The [simulator](satmend/simulator.py) models delivery, activation, and target verification using configured durations and outcomes.

## Configure the simulator

Start with the [scenario JSON](examples/scenario.json) and the [contact CSV](examples/ground_contacts.csv). Times are in seconds and link rates are in bytes per second.

| Change | Where |
| --- | --- |
| Deadline and ground contacts | `deadline_s`, `ground_contacts`, or `--contacts` |
| Gateway, crosslink availability, and link capacities | `gateway_satellite`, `gateway_app`, `crosslink_contacts`, and `*_bytes_per_s` |
| Application placement, versions, faults, and service dependencies | `applications` |
| Package and control-message sizes, activation and verification times | Application `package_bytes`, `activation_s`, and `verification_s`; scenario `command_bytes` and `receipt_bytes` |
| Ground repair slots and preparation times | `--slots`, `--diagnosis-s`, `--reuse-diagnosis-s`, `--validation-s`, and `--state-recovery-s` |

<details>
<summary><b>Prepare scenario data and model contact windows</b></summary>

```bash
python3 -m satmend prepare examples/scenario.json \
  --contacts examples/ground_contacts.csv \
  --output outputs/scenario.json
```

The optional CSV supplies `start_s,end_s` and replaces the ground calendar in the JSON. The [loader](satmend/scenario.py) checks identifiers, times, capacities, and acyclic dependencies, then sorts and merges contact windows. An omitted crosslink destination has continuous visibility while the gateway forwards. An explicit empty list gives that destination no visibility. A code fault takes a positive `package_bytes`.

Ground transfers can resume from their remaining bytes. Once a package and its command reach the gateway, forwarding can proceed during a ground contact gap. The gateway caches each compatible package and delivers to each target over the shared crosslink. A successful check exactly at the deadline counts as recovered.

</details>

The result JSON includes `summary`, per-target `applications`, individual `jobs`, `pending_transfers`, timestamped `events`, and `planned_actions`. Target recovery, dependent service availability, and ground confirmation have separate timestamps. A service that remains unavailable contributes the full experiment duration to mean outage.

## Use your own planner

Pass your action sequence with `--actions path/to/actions.json`. The `planned_actions` array from an example run is a starting template. To test the reference planner with different per-version costs or verification outcomes, supply an `Assessment` callback.

<details>
<summary><b>Python example</b></summary>

```python
from satmend import Assessment, load_scenario, plan_sessions, simulate

scenario = load_scenario("examples/scenario.json")

def assess(component, version, targets):
    return Assessment(validated=True, verification_passes=True, duration_s=1.5)

actions = plan_sessions(scenario, slots=2, assess=assess)
print(simulate(scenario, actions)["summary"])
```

Code actions specify ready time, targets, component, version, candidate ID, package size, and check outcomes. State actions specify ready time and targets. See the [Action and Assessment definitions](satmend/method.py).

</details>

[MIT License](LICENSE) · [Asset sources](ASSET_SOURCES.md)
