# Candidate validation — September 10, 2026

Status: built and tested as an **installation candidate**. Not installed on the car;
vehicle startup, Konik delivery and driving behavior are not yet validated.

## Source and build

- Stable base: `1c35e376e9e427431c83dd199ad7d1bdb0044975`.
- Donors and feature boundaries: [source pins](source-pins.json).
- Native build completed from feature revision `fa66aab`, using larch64,
  Python 3.12, the candidate's frozen dependency lock, and Cap'n Proto 1.0.2.
- Subsequent production change `076bc4f` preserves float types when bounding
  settings. It changes Python settings validation; native inputs are unchanged.
- AGNOS 19.6.20 sparse image SHA-256:
  `d744641104991cdf13e1294aeb9b2628656cab46ed7f234f0f04092a9a5ead8d`.
- Expanded system image SHA-256:
  `2516390281514cde7a9ea8cea5f88035cc55a5124aa23626d12ff1f82554c0e8`.
- Both image hashes matched the pinned manifest before target validation.
- The inherited `prebuilt` marker was removed. Candidate native components,
  including schemas, Params/msgq/model bindings, both MPC solvers, camera,
  logger/encoder and panda-facing components, were rebuilt successfully.

## Automated results

| Check | Result |
| --- | --- |
| Target controls/Insight/radar/longitudinal suite | 1,078 passed; 2 inherited failures below; 0 skipped |
| Galaxy catalog and real Flask settings routes | 33 passed |
| Existing Konik server-switch regression cases | 5 passed |
| Device lateral/longitudinal settings imports under Xvfb | Passed |
| Fatal Python/static checks on added control/tooling modules | Passed |
| Source hashes and candidate/native import-path assertions | Passed |
| Stable longcontrol, MPC implementation and panda safety diff | Unchanged |

Total: **1,116 passing tests**, with **two unchanged baseline failures**. The
validation script retains a failing exit code when the broader suite fails; these
cases have not been hidden or marked as passing.

The inherited failures are in `selfdrive/controls/tests/test_latcontrol.py`:

| Test | Pinned stable result = candidate result | Test expects |
| --- | --- | --- |
| `test_bolt_2022_2023_low_speed_center_output_limit` | 0.8768080716037345 at `(0.05, 9.0)` | > 0.98 |
| `test_palisade_center_output_taper_curve` | 0.8222980111882578 at `(0.0, 30.0)` | > 0.87 |

The owning `latcontrol_vehicle_tunes.py`, torque controller and test file are
unchanged from the pinned baseline. The baseline module was loaded separately and
produced the identical failing values. No unrelated Bolt or Palisade tuning change
was introduced to make these assertions pass.

The BLoTv2 OFF comparison uses the exact pinned planner source, checked by SHA-256,
and the real rebuilt native solver. It compares solver acceleration, final desired
acceleration, stop decisions and actual mode across eight scenarios, four distance
settings, ACC, blended and Experimental model-simulation paths. Enabled tests cover
lifecycle resets, headway applied once, actual solver parameters/cost input,
nonfinite input rejection and lead inching then stopping. These are synthetic
regressions, not measured driving improvement.

Steering tests cover explicit stock/PTM/legacy selection, unknown firmware/profile,
base gains versus multipliers, geometry inversion, engagement latch, serialization,
bounded finite output, saturation, override/disengagement, stiction/smoothing,
actual `Controls` controller selection and NNFF override exclusion. Migration tests
use the rebuilt native Params binding and verify the protected rollback record.

## Vehicle and release gates still open

Read-only inspection found the comma four offroad on the pinned NRDR revision and
AGNOS 19.7. The user confirmed PTM firmware. A local migration preview preserves
the exported base PID and 90/90/95 percent P multipliers, manual geometry, smoothing
ON and stiction OFF. The saved CarParams/tune report says stock ACC; the preview
explicitly selects openpilot longitudinal as required by the integration plan.
Radar and BLoTv2 remain OFF. No device settings or installed code were changed.

The following require the installed candidate and are **not claimed complete**:

1. AGNOS compatibility/transition and verified code-plus-OS rollback path.
2. Comma four startup, correct CarParams/controller, settings persistence, and
   absence of new CAN/safety/schema faults.
3. Authenticated Konik device record, Athena heartbeat, completed route segment
   upload, server-side route visibility, and identity continuity after reboot.
4. Real Insight radar tracks, range/velocity, freshness and dropout validation.
5. Supervised low-speed and staged driving comparison of launch/braking/gap/jerk
   and interventions before selecting BLoTv2 or radar as a preferred ON setting.

The local artifact manifest records exact final source revision, toolchain/image
identity, source hashes, native output hashes and these open gates. Keep private
exports and rollback files out of GitHub and build archives.
