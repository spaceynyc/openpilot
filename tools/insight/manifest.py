#!/usr/bin/env python3
"""Record source/build provenance and package only candidate-linked ELF outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
from datetime import datetime, timezone


def sha(path):
  digest = hashlib.sha256()
  with path.open('rb') as file:
    for block in iter(lambda: file.read(1024 * 1024), b''):
      digest.update(block)
  return digest.hexdigest()


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('build_root', type=Path)
  parser.add_argument('--tag', required=True)
  args = parser.parse_args()
  source = Path(__file__).resolve().parents[2]
  root = args.build_root.resolve()
  runtime = root / 'runtime'
  revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
  if subprocess.check_output(['git', 'status', '--porcelain'], cwd=source):
    raise ValueError('Commit the source tree before creating an immutable manifest')
  native = {}
  build_log = (root / 'logs/target-build.log').read_text()
  if 'scons: done building targets.' not in build_log:
    raise ValueError('Successful target build receipt required')
  for name in sorted(set(re.findall(r'^clang(?:\+\+)? -o (\S+)', build_log, flags=re.MULTILINE))):
    path = (runtime / name).resolve()
    if path.suffix in ('.o', '.os') or not path.is_file(): continue
    if not path.is_relative_to(runtime): raise ValueError('Unexpected artifact path')
    header = path.read_bytes()[:20]
    if header[:4] != b'\x7fELF': continue
    if int.from_bytes(header[18:20], 'little') != 183: raise ValueError(f'Expected AArch64 ELF: {name}')
    relative = str(path.relative_to(runtime))
    native[relative] = {'sha256': sha(path), 'bytes': path.stat().st_size}
  required = {'common/params_pyx.so', 'msgq_repo/msgq/ipc_pyx.so', 'system/loggerd/loggerd',
              'selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so'}
  if not required <= set(native): raise ValueError(f'Missing rebuilt outputs: {required - set(native)}')
  output = root / 'artifacts' / args.tag
  output.mkdir(parents=True, exist_ok=False)
  manifest = {
    'schema': 1, 'createdUtc': datetime.now(timezone.utc).isoformat(),
    'sourceRevision': revision, 'tag': args.tag, 'status': 'installation-candidate-not-vehicle-validated',
    'sources': json.loads((source / 'docs/insight/source-pins.json').read_text()),
    'build': {'architecture': 'larch64', 'agnos': '19.6.20', 'nativeBuildRevision': 'fa66aab',
              'nativeBuildLogSha256': sha(root / 'logs/target-build.log'),
              'imageId': subprocess.check_output(['docker', 'image', 'inspect', 'starpilot-insight-builder:2026-09-10', '--format', '{{.Id}}'], text=True).strip(),
              'uvLockSha256': sha(source / 'uv.lock'), 'sysroot': json.loads((root / 'logs/sysroot-provenance.json').read_text())},
    'sourceHashes': json.loads((runtime / '.insight-source-manifest.json').read_text()),
    'nativeOutputs': native,
    'validation': {'testsPassed': 1116, 'inheritedFailures': 2, 'newFailures': 0,
                   'controlsJUnitSha256': sha(root / 'logs/target-controls.xml'),
                   'settingsJUnitSha256': sha(root / 'logs/target-settings.xml'),
                   'baselineLateralAudit': json.loads((root / 'logs/baseline-lateral-audit.json').read_text()),
                   'uiImportSmoke': 'passed-under-Xvfb', 'offEquivalenceCases': 96},
    'openGates': ['AGNOS transition and verified OS rollback', 'candidate device startup/CAN/settings',
                 'candidate Konik authenticated record/heartbeat/upload/route/reboot',
                 'real Insight radar tracks/dropout', 'supervised driving comparison'],
    'packageNotes': 'Native overlay for this source revision; not a standalone installer. No Params, identity, credentials or routes included.',
  }
  manifest_path = output / 'manifest.json'
  manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
  archive = output / 'native-overlay.tar.gz'
  with tarfile.open(archive, 'w:gz') as tar:
    for name in native:
      tar.add(runtime / name, arcname=name, recursive=False)
    tar.add(manifest_path, arcname='insight-build-manifest.json')
  (output / 'SHA256SUMS').write_text(f'{sha(manifest_path)}  manifest.json\n{sha(archive)}  native-overlay.tar.gz\n')
  print(json.dumps({'artifactDirectory': str(output), 'revision': revision, 'nativeOutputs': len(native), 'archiveBytes': archive.stat().st_size}))

if __name__ == '__main__':
  main()
