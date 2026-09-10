# StarPilot Insight integration candidate

Source branch: `spaceynyc/openpilot:starpilot-insight-next`.

This candidate keeps the pinned StarPilot model, longitudinal planner/controller,
acceleration envelope and panda safety implementation. The added packages are
separate commits. Source pins and the original release gates are in
[source-pins.json](source-pins.json) and [integration-plan.md](integration-plan.md).

## Feature controls

| Setting | Default | Scope and behavior |
| --- | --- | --- |
| `InsightSteeringEnabled` | OFF | Explicit Insight PID adapter. Requires a selected EPS profile and recognized TXM-A040 software identifier. |
| `InsightEpsProfile` | 0, unverified | 1 stock; 2 legacy linear-max; 3 PTM. Software part number alone never selects a modification. |
| `InsightBasePid` | empty | Validated exported base gains, separately from the speed-dependent P/I/F multipliers. |
| `NrdrLatStiction` | OFF | Optional NRDR stiction compensation. |
| `HondaTorqueLowPassFilter` | OFF | Steering output smoothing before unchanged Honda command limits. |
| `HondaSteerDeltaLimiter` | OFF | Optional additional output rate limit; driver override and disengagement clear output immediately. |
| `BlotV2` | OFF | James's bounded following-time pad and acceleration-change cost multiplier in actual ACC mode. |
| `BoschARadar` | OFF | Explicit opt-in to James's Bosch-A decoder, including Insight. Stable's existing verified Civic/CR-V `HondaBoschARadar` path remains available. |
| `UseKonikServer` | ON | Shared Konik API/Athena/registration/upload-URL backend. Switching takes a restart. |

Steering settings are captured together in `CarParams.insightTuning` at ignition.
Both controls and the Honda command path consume that serialized snapshot. Values
edited on the device or through Galaxy take effect next ignition. Learned geometry
also holds its selected ratio throughout engagement. Insight's unsupported raw-angle
mode is unavailable; manual and firmware geometry use consistent measured/desired
curvature transforms.

PTM source defaults are P/I/F `0.03 / 0.01 / 0.000012` and a 4096 torque map. Legacy
source defaults are `0.06 / 0.02 / 0.000024`, a speed-dependent feedforward schedule,
and a 3840 torque map. Migration can preserve the separately exported effective
base gains. Existing panda constraints, driver monitoring, driver override and
final Honda command bounds remain in force.

BLoTv2 resets on disabled longitudinal control, disengagement, stale or invalid
inputs, loss of the selected lead, track identity changes and unsupported modes.
Its pad is applied once after StarPilot's dynamic base-headway stage. Its multiplier
only changes the MPC acceleration-change cost. Blended mode and Experimental
model-simulation output bypasses keep StarPilot behavior. The supervisor never
commands acceleration or changes the acceleration envelope.

The radar port retains StarPilot's slow-radar frequency exception. Range-derived
velocity telemetry is diagnostic only. Receiving radar tracks does not restore
factory AEB/FCW/CMBS when openpilot longitudinal control disables those functions.
Real Insight track and dropout validation remains a separate gate.

Optional torque/PIF blending, tune learning, override tolerance, injection testing,
NRDR longitudinal control and unrelated donor vehicle tunes are excluded from this
initial integration. They were not required by the exported settings.

## Inspectable route diagnostics

- `initData.gitCommit` identifies the build through StarPilot's normal logger.
- `CarParams.insightTuning` holds the immutable steering configuration.
- `controlsState.insightSteering` is emitted once per second with fork commit,
  actual controller class, EPS profile, base PID, P/I/F multipliers and geometry mode.
- `controlsState.insightSteerRatio` is the ratio used by both geometry transforms.
- Normal `controlsState.lateralControlState.pidState` reports applied PID output.
- `longitudinalPlan.insightBLoT` reports enabled/active state, actual solver mode,
  model-simulation bypass, reset reason, follow pad, jerk scale and trigger state.
- Radar lead fields report source, track ID, measurement status and shadow
  range-derived velocity. Shadow velocity is never fed into control.

These fields do not establish successful Konik reporting. Server-side authenticated
identity, heartbeat, completed segment upload and route visibility must be verified
independently on the installed candidate, then checked after a reboot.

## Migration and rollback

Run `tools/insight/export_device.py` with the **installed fork's** Python environment.
It only reads an allowlisted configuration and emits no VIN, backend identities,
keys or route locations. Saved CarParams are labeled as saved snapshots; a part
number and saved gains do not identify an EPS firmware variant.

Prepare a local preview with an explicitly confirmed firmware variant:

```sh
python tools/insight/migration.py preview device-export.json --eps-profile ptm --output migration-preview.json
```

The preview preserves base gains and speed multipliers separately, lists any bounded
value adjustments, and explicitly records the NRDR radar preference migration.
Radar and BLoTv2 stay OFF pending vehicle checks. Openpilot longitudinal and Konik
are selected as requested by the integration plan. Review the complete JSON before
applying it. Nothing is written to the device by `preview`.

After reviewing the candidate and with the car offroad, `apply` creates an exclusive
mode-0600 rollback file before writing anything. Custom steering is disabled first,
then enabled only after the complete group is written; values are read back.

```sh
python tools/insight/migration.py apply migration-preview.json --backup migration-backup.json
python tools/insight/migration.py restore migration-backup.json
```

`restore` restores these settings, not the installed code or operating system.
Keep the previous NRDR source revision and its compatible AGNOS image available
separately. The observed device was running AGNOS **19.7**; pinned StarPilot targets
**19.6.20**. A code rollback alone is not a verified OS rollback. Do not treat an
installer branch link as a passed deployment gate.

## Build and validation

The inherited `prebuilt` marker is removed. Required schemas, bindings, solver and
native targets must be rebuilt from this candidate. No NRDR or James native
libraries are imported. StarPilot's pinned model artifacts remain StarPilot assets.

`tools/insight/Dockerfile` pins the Linux/aarch64 base image and supplies Cap'n Proto
1.0.2. Install dependencies from the candidate's frozen `uv.lock` (core, testing and
dev extras) in the isolated build directory. Extract the pinned AGNOS system image
using `tools/laptop_device_build/extract_sysroot_from_agnos.py`, verify both manifest
hashes, and use `tools/insight/build_target.sh` for the larch64 target build.

`tools/insight/sync_validation.py` copies candidate source changes atomically into
an existing isolated baseline checkout. It preserves rebuilt native outputs,
rejects copying native libraries, records source hashes, and supplies the exact
pinned planner source for the OFF-equivalence tests. Do not sync while a build or
test process is reading that tree.

Required automated checks include the Insight test directory, James's supervisor,
Honda Bosch-A decoder and radard tests, StarPilot's longitudinal regression tests,
and Galaxy settings tests. Run them in Linux with the candidate's own native
bindings; enable `INSIGHT_REQUIRE_PROVENANCE=1` and
`SP_DISABLE_HOST_PYTEST_REDIRECT=1`. The provenance test checks source hashes and
resolved import paths, including the native MPC and Params bindings.

Passing automated checks establishes source behavior, not driving comfort or
on-device readiness. The remaining release gates are listed in the original plan;
record their actual status with each candidate manifest.
