# Installer packaging repair — September 10, 2026

The first device installation of `insight` stopped during compilation with
`fatal error: 'eigen3/Eigen/Dense' file not found`. The preceding PWD warning was
not the fatal error. The extracted AGNOS 19.6.20 system image also lacks this
development header.

The source candidate had been published without its `prebuilt` marker and without
the candidate native overlay. This made the installer invoke SCons on the device.
The earlier larch64 build used a development container with Eigen installed, so
its success did not establish that a source-only installer would build on AGNOS.

The repair packages all 19 native outputs from the verified
`insight-candidate-2026.09.10.1` artifact and adds the marker only after verifying
every output. Six outputs differ from the inherited StarPilot binaries; the other
13 are identical. The package includes `insight-build-manifest.json` with the
source revision, build provenance, runtime source hashes and native output hashes.
This is a packaging change; controller settings, model assets and safety behavior
are unchanged.

Validation ran against the actual packaged checkout mounted at `/data/openpilot`:

- 84 Konik, migration, steering and launch-policy tests passed.
- 113 package and longitudinal tests passed, including the 96-case comparison
  against the pinned stable planner using the packaged native solver.
- The real launch-script build gate skipped the compiler with the packaged
  marker. Its negative control invoked the compiler when the marker was absent,
  reproducing the original packaging condition.
- All 19 packaged outputs resolved their shared-library dependencies using the
  target library tree and managed Python environment.
- The lateral and longitudinal settings UI imports passed under Xvfb.
- Fatal Python/static checks and `git diff --check` passed.

The short installer URL remains `installer.comma.ai/spaceynyc/insight`. A device
already stopped before manager startup cannot be assumed to fetch a GitHub branch
update merely by rebooting. It needs the corrected checkout installed or a scoped
repair of its current checkout.

Device startup and the remaining vehicle/cloud/road checks are still pending.
The prior candidate tag and its artifact remain unchanged.
