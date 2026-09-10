**StarPilot + NRDR Insight/EPS + James's radar/BLoTv2 + Konik — integration plan**

Prepared September 10, 2026. Planning only; no implementation, remote branch changes, installation, or device-setting changes were performed for this request.

Build a new StarPilot-based branch in `spaceynyc/openpilot`. Keep StarPilot responsible for the driving model, longitudinal planner, user interface, and release runtime. Add an isolated Insight steering package from NRDR, James's Insight-capable Bosch-A radar integration and BLoTv2 supervisor, and reliable Konik identity/reporting. Each addition should have its own commit and a clear way to disable or revert it.

**Use these verified source snapshots.**

| Role | Repository / branch | Pinned commit | Commit time, UTC |
|---|---|---|---|
| Base | `firestar5683/StarPilot` / `StarPilot` | `1c35e376e9e427431c83dd199ad7d1bdb0044975` | Sep 9, 02:15 |
| Steering and Konik donor | `nrdr/openpilot` / `nrdr-nightly` | `d2b2df077e896a12c061134fb464af2735ed88f3` | Sep 10, 03:55 |
| BLoTv2 and Insight radar donor | `JamesL787/openpilot` / `ns-bosch-radar-testing` | `d8604bd2036d327b92b967d6b1fcc757fd3a18bf` | Sep 6, 20:36 |
| Previous custom implementation reference | `spaceynyc/openpilot` / `starpilot-insight-konik` | `0d47bded6ec936583fb947edf6bb4f51eb9628b9` | Jul 22, 16:27 |

StarPilot's documentation identifies `StarPilot` as the stable/default branch and `Dom` as development. The inspected stable source reports version `0.11.2`. James's inspected branch was last merged with an August 28 Dom snapshot; use it as a feature donor so the newer stable planner remains intact. Recheck branch heads at implementation start and record any deliberate update to these pins. [Branch documentation](https://wiki.firestar.link/software/starpilot/), [StarPilot snapshot](https://github.com/firestar5683/StarPilot/tree/1c35e376e9e427431c83dd199ad7d1bdb0044975), [James snapshot](https://github.com/JamesL787/openpilot/tree/d8604bd2036d327b92b967d6b1fcc757fd3a18bf).

**Preserve the current StarPilot baseline.**

Keep its Traffic Mode soft-launch behavior, positive-integrator handling during deceleration, newer transitions between Conditional Experimental Mode and ACC, final low-speed transition blending, lead policies, and far-lead coasting logic. Keep StarPilot's acceleration limits and final actuator constraints. These are directly relevant to the reported launch/brake oscillation and late braking, and replacing the planner wholesale would make their preservation hard to establish. [Longitudinal controller](https://github.com/firestar5683/StarPilot/blob/1c35e376e9e427431c83dd199ad7d1bdb0044975/selfdrive/controls/lib/longcontrol.py), [planner](https://github.com/firestar5683/StarPilot/blob/1c35e376e9e427431c83dd199ad7d1bdb0044975/selfdrive/controls/lib/longitudinal_planner.py).

**Port the following NRDR steering capabilities.**

| Capability | NRDR source | Integration decision |
|---|---|---|
| Insight EPS profile and PID selection | `opendbc_repo/opendbc/sunnypilot/car/honda/interface_ext.py`; Honda `interface.py` | Adapt the Insight-specific behavior into StarPilot's Honda interface. Separate stock, legacy modified, and PTM configurations; choose a modified profile only after establishing the actual installed configuration. |
| Speed-dependent P/I/feedforward scaling, center compensation, rate damping | `openpilot/nrdr/features/lateral/latcontrol_pid.py` | Add an Insight-scoped controller adapter; preserve the existing StarPilot controller for other vehicles. Port only the required dependencies. |
| Manual, learned, and firmware-based steering geometry | `features/lateral/steer_ratio_tuning.py`, `honda_vgr.py`, `hooks/controlsd.py` under `openpilot/nrdr/` | Use one geometry selection consistently for measured and desired curvature. Freeze geometry changes during engagement. Insight TXM-A040 has a firmware profile; NRDR's audited raw-angle curve is currently Clarity-specific and should remain unavailable on Insight. |
| Output smoothing and optional rate limiting | `opendbc_repo/opendbc/sunnypilot/car/honda/controller_features.py` | Extract the steering-only operations and adapt them to StarPilot's Honda output path. Review driver-override handling separately; retain immediate disengagement and existing command limits. |
| Stiction compensation | `openpilot/nrdr/features/lateral/lat_stiction.py` | Include as an optional control, initially OFF, preserving the setting saved in this session. |
| Interpolated torque/PIF and tune learning | `interpolated_torque_pif.py`, `tune_learner.py`, `phase_detector.py` | Optional second-stage steering features. Initially disabled unless the verified current-device configuration specifically requires them. Validate independently from BLoTv2. |
| Parameter consistency and migration | `openpilot/nrdr/params/{specs,defaults,snapshots,profiles}.py` | Port the required lateral keys, bounds, grouped snapshots, and migration behavior into StarPilot's parameter/settings infrastructure. Do not import NRDR's entire defaults initializer. |
| Effective-tune diagnostics | `openpilot/nrdr/features/services/car_tune_report.py` | Adapt reporting of actual controller, gains, geometry, and EPS identifier; add equivalent BLoTv2 state to diagnostics. This NRDR module writes local status Params—it is not, by itself, a Konik upload service. |

The donor now places canonical NRDR modules under `openpilot/nrdr/`; similarly named files under `openpilot/sunnypilot/nrdr/` are compatibility shims. Bring over one implementation. StarPilot and NRDR also differ in controller signatures, `CP`/`CP_SP`, Honda flag placement, Params behavior, and hardware imports. These require adapters, rather than a directory copy.

A concrete tune mismatch needs resolving before installation: inspected StarPilot's Insight baseline has P/I `0.6/0.18`, while NRDR's helper sets its torque-mod PID group to `0.03/0.01`, feedforward `0.000012`, and the Insight torque map to `[0,4096] → [0,4096]`. NRDR also applies standstill/resume and minimum-speed changes through that helper. Those numbers are source defaults, not a measurement of the installed car or proof that they suit its EPS modification. Export the effective current CarParams and relevant tuning settings before selecting the migration profile. Preserve the saved 90% low/standard P multipliers as multipliers; they do not establish the base gains. [NRDR interface helper](https://github.com/nrdr/openpilot/blob/d2b2df077e896a12c061134fb464af2735ed88f3/opendbc_repo/opendbc/sunnypilot/car/honda/interface_ext.py), [steering geometry](https://github.com/nrdr/openpilot/blob/d2b2df077e896a12c061134fb464af2735ed88f3/openpilot/nrdr/features/lateral/steer_ratio_tuning.py).

EPS part-number recognition alone does not establish which modified firmware variant is installed. Firmware flashing tools and firmware changes are outside this integration. Keep the existing panda safety enforcement, driver override, and command limits; any incompatibility must be resolved before enabling the custom controller.

**Take this bounded BLoTv2 package from James.**

| File or integration point | Planned change |
|---|---|
| `selfdrive/controls/lib/blotv2.py` | Port the supervisor, its debounce/reset behavior, and bounded outputs. |
| `selfdrive/controls/lib/longitudinal_lead.py` | Port the small lead-observation/necessity helpers required by the supervisor. |
| StarPilot's current `longitudinal_planner.py` | Add initialization, reset, input validation, following-time adjustment, and the acceleration-change cost multiplier. Keep surrounding stable logic. |
| Params registry, `starpilot/common/assets/device_settings_layout.json`, and longitudinal settings UI | Add `BlotV2` consistently, default OFF during development; expose one clearly named toggle. |
| Donor supervisor tests plus integration tests | Port relevant behavioral tests and add tests against the actual current StarPilot planner. |

James's implementation changes following time and the MPC's acceleration-change penalty; it does not command acceleration directly. It can respond to excessive braking, predicted lead braking, and a lead pulling away. Its current gate is no longer Civic-only. It may improve response, but its launch trigger also makes the car respond more readily, so the reported inch-forward/lunge/brake case must be an explicit test. [Supervisor](https://github.com/JamesL787/openpilot/blob/d8604bd2036d327b92b967d6b1fcc757fd3a18bf/selfdrive/controls/lib/blotv2.py), [integration](https://github.com/JamesL787/openpilot/blob/d8604bd2036d327b92b967d6b1fcc757fd3a18bf/selfdrive/controls/lib/longitudinal_planner.py).

Apply its following-time pad once, at the corresponding final base-headway stage, and audit later planner modifiers so lead buffering is not accidentally counted twice. Confirm the model-lead trajectory inputs remain valid with the selected StarPilot model. Preserve StarPilot's acceleration envelope and urgent-braking constraints.

Mode coverage needs a deliberate decision: the inspected MPC consumes the proposed multiplier in ACC mode, while blended mode uses a fixed acceleration-change cost. Do not claim equivalent BLoTv2 behavior in every mode. First validate openpilot longitudinal in ACC/Chill, then test Experimental/CEM and the tinygrad/model-simulation paths using the actual runtime solver mode. Expanding the supervisor into blended mode would be a separate behavioral change. [MPC weights](https://github.com/firestar5683/StarPilot/blob/1c35e376e9e427431c83dd199ad7d1bdb0044975/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py).

**Make Konik reporting verifiable.**

StarPilot already has selectable Konik API and Athena endpoints, but its current switch logic can clear the Konik ID and call registration. Current NRDR supplies a better durable identity resolver at `openpilot/nrdr/features/services/registration.py` and explicitly selects Konik through `openpilot/nrdr/config/backend_env.sh`. Port those behaviors through StarPilot's existing services, using one consistent backend selection for registration, API calls, Athena, and upload-URL requests. [StarPilot server switching](https://github.com/firestar5683/StarPilot/blob/1c35e376e9e427431c83dd199ad7d1bdb0044975/starpilot/common/connect_server.py), [NRDR identity resolver](https://github.com/nrdr/openpilot/blob/d2b2df077e896a12c061134fb464af2735ed88f3/openpilot/nrdr/features/services/registration.py), [NRDR endpoint policy](https://github.com/nrdr/openpilot/blob/d2b2df077e896a12c061134fb464af2735ed88f3/openpilot/nrdr/config/backend_env.sh).

1. Prefer the existing durable Konik identity, preserve the factory Comma identity separately, and use authenticated validation when migrating another candidate. Do not blindly replace a working Konik identity with the stock ID.
2. Reuse the existing device keys on-device. Keep IDs and keys out of source control, build artifacts, and the public plan.
3. Avoid registration from the routine server-switch/boot synchronization path when a usable identity exists. For necessary first registration, implement and test a true overall deadline with bounded hardware lookup and network attempts.
4. Retain local boot and logging during an outage; retry Konik without changing the selected backend. This is a proposed update to the July branch's automatic Comma fallback, which could silently defeat the requirement to report to Konik. Keep any Comma fallback explicit and visible.
5. Verify the authenticated device record, Athena heartbeat, completed route-segment upload, and route visibility in Konik. Reboot and repeat the identity check. Endpoint strings or a local 'connected' label alone do not pass this gate.
6. Include fork/commit, effective EPS profile, controller, BLoTv2 enabled state, actual solver mode, and policy activity in inspectable route diagnostics using StarPilot-compatible logging/schema extensions. Separate diagnostic metadata from upload success.

The previous `starpilot-insight-konik` branch remains useful for its boot-timeout intent and regression cases, but its older identity-selection assumptions should be replaced with the current NRDR behavior. SunnyLink/Galaxy remote settings are a separate integration surface; verify whichever interface is retained actually reads and writes the new keys. Konik route reporting does not establish SunnyLink compatibility.

**Port James's existing Insight radar integration as a separate feature.**

Correction after checking James's radar source: upstream stable's narrower allowlist is not the donor's support boundary. James already adds `Bus.radar: 'honda_bosch_a_radar'` to `HONDA_INSIGHT`, defines the eligible `HONDA_BOSCH_A` platform group, and opens `radarUnavailable` through the `BoschARadar` toggle independently of the longitudinal-controller selection. His parameterized radar tests explicitly include Insight and assert that the Bosch-A parser is constructed and radar is available. This is an existing integration to port, not missing work to invent. [Insight DBC mapping](https://github.com/JamesL787/openpilot/blob/d8604bd2036d327b92b967d6b1fcc757fd3a18bf/opendbc_repo/opendbc/car/honda/values.py#L309-L318), [interface gate](https://github.com/JamesL787/openpilot/blob/d8604bd2036d327b92b967d6b1fcc757fd3a18bf/opendbc_repo/opendbc/car/honda/interface.py#L48-L59), [Insight-inclusive tests](https://github.com/JamesL787/openpilot/blob/d8604bd2036d327b92b967d6b1fcc757fd3a18bf/opendbc_repo/opendbc/car/honda/tests/test_bosch_a_radar.py#L1253-L1276).

| Donor component | Planned port |
|---|---|
| Honda `values.py` and `interface.py` | Insight radar DBC mapping, Bosch-A eligibility, radar availability gate, and required initialization compatibility. Preserve unrelated current StarPilot vehicle specifications and behavior. |
| `opendbc_repo/opendbc/dbc/honda_bosch_a_radar.dbc` and Honda `radar_interface.py` | The Bosch-A object-bank decoder, track handling, timing, and validity checks as a compatible unit. |
| `selfdrive/controls/radard.py` | The required Bosch-A lead-selection/fusion, low-speed lead checks, stale-track handling, and associated telemetry; preserve newer stable behavior outside that path. |
| `common/params_keys.h`, settings metadata, longitudinal settings UI | Wire `BoschARadar` consistently. Explicitly migrate the relevant NRDR radar preference rather than assuming the differently named key is interchangeable. |
| `opendbc_repo/opendbc/car/honda/tests/test_bosch_a_radar.py` and `selfdrive/controls/tests/test_radard_bosch.py` | Port parser, availability, lead-handling, and regression tests; prove they import the candidate source. |

Keep radar and BLoTv2 in separate commits and toggles so each can be tested independently, then together. Verify actual Insight CAN tracks, lead range/velocity, freshness, lead-source selection, and dropout behavior before enabling radar in the candidate's preferred configuration. The inspected tests establish intended code support; they are not evidence of a road-validated result on this particular Insight. Also preserve the documented distinction between receiving radar tracks and retaining factory collision functions: James's source explicitly states that enabling openpilot longitudinal disables factory AEB/FCW/CMBS on this path. Receiving radar data does not restore those functions.

This keeps NRDR's longitudinal controller, Nidec-specific brake/gas changes, unrelated vehicle tunes, and James's entire older planner out of the initial port. Those are separate behavioral changes with little value for isolating this Insight issue.

**Implement in reviewable stages.**

1. Create an isolated checkout from the pinned stable source. Proposed branch: `starpilot-insight-next`, subject to checking availability. Leave existing branches/default branch intact. Record the current installed revision and export only the required configuration for migration and rollback.
2. Add Konik backend/identity handling and outage tests. Produce an offroad-verifiable build before adding controller changes.
3. Add the Insight profile and core PID/geometry/settings adapter. Verify effective CarParams, runtime controller selection, saved settings, driver override, and output bounds.
4. Add BLoTv2, default OFF. Prove disabled behavior matches the stable longitudinal baseline, then test enabled behavior independently of optional steering experiments.
5. Port James's Insight-capable Bosch-A radar package and verify it independently, then together with BLoTv2. Add effective-tune/BLoTv2/lead-source reporting and settings-interface support. Introduce optional torque/PIF or learning only as separately tested commits.
6. Build native components for the StarPilot target, verify the comma four installation/offroad path, and produce a tagged candidate with an immutable manifest and rollback instructions. Selectively carry future stable updates through the same regression gates.

Do not copy prebuilt NRDR/James native libraries into StarPilot. Rebuild required schemas, bindings, solver components, and target binaries from the candidate's own source/runtime combination.

**Use these completion gates.**

| Gate | Evidence required |
|---|---|
| Source/build provenance | Exact source pins; clean candidate diff; runtime module paths and build hashes show tests exercised the candidate. James's latest commit documents stale host-runtime imports invalidating earlier test results, so this check is substantive. |
| Steering correctness | Stock/recognized-modified/unknown-profile cases; correct runtime PID selection; P/I/F migration; geometry consistency; settings latch/reset behavior; finite bounded outputs; saturation/override/disengagement tests. |
| Longitudinal regression | BLoTv2 OFF matches stable; ON handles disappearing/stale leads, slowing lead, stopped lead, lead pulling away, lead inching then stopping, cut-in, stronger braking, and disengagement/re-engagement without stale supervisor state. |
| Mode behavior | Log actual ACC/blended/model-simulation mode and confirm which supervisor outputs reach the solver. Validate all distance settings without stacking unrelated tuning changes. |
| Insight radar | Verify the candidate's Insight DBC/gate, real track decoding and timing, lead validity and stale/dropout behavior, then compare radar ON/OFF independently of BLoTv2 ON/OFF. |
| Konik | Authenticated identity, heartbeat, upload receipt, route visible server-side, reboot persistence, and no boot hang during outage. |
| On-device startup | Correct platform/firmware recognition, no controller/schema failures, no new CAN/safety faults, and working settings persistence on comma four. |
| Driving behavior | Controlled low-speed testing first, then a staged supervised evaluation. Compare launch acceleration, acceleration changes/jerk, brake onset, minimum gap, stop/start oscillation, and interventions. Route replay alone cannot prove the car will behave comfortably. |

Run meaningful unit/integration/replay checks in a matching Linux/AGNOS environment. macOS syntax/import checks cannot validate Linux native binaries. No test results or driving improvement are claimed by this planning document.

The intended candidate keeps openpilot longitudinal, retains the established Insight PID configuration with stiction OFF, and includes James's radar integration and BLoTv2 with independent toggles. BLoTv2 can become the preferred ON configuration only after those tests demonstrate an improvement in this car's stop-and-go behavior; radar activation likewise requires valid tracks on this car. Completion means a tested build with working Konik reporting and a rollback path, not merely a successful merge.
