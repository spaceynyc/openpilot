#!/usr/bin/env python3
"""Package a previously verified Insight native overlay for the device installer.

Run from its exact, clean source revision before committing the release package.
The prebuilt marker is written only after every native output is verified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


def digest(data):
  return hashlib.sha256(data).hexdigest()


def write_atomic(path, data, mode=0o644):
  with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
    temporary = Path(stream.name)
    stream.write(data)
    stream.flush()
    os.fsync(stream.fileno())
  temporary.chmod(mode)
  os.replace(temporary, path)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('artifact_directory', type=Path)
  args = parser.parse_args()
  root = Path(__file__).resolve().parents[2]
  artifact = args.artifact_directory.resolve()
  manifest_bytes = (artifact / 'manifest.json').read_bytes()
  manifest = json.loads(manifest_bytes)
  revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
  if revision != manifest['sourceRevision']:
    raise ValueError('Checkout must match the verified artifact source revision')
  if subprocess.check_output(['git', 'diff', 'HEAD', '--name-only'], cwd=root):
    raise ValueError('Tracked source files must be clean before packaging')
  for line in (artifact / 'SHA256SUMS').read_text().splitlines():
    expected, name = line.split()
    if name not in {'manifest.json', 'native-overlay.tar.gz'}:
      raise ValueError('Unexpected checksum target')
    if digest((artifact / name).read_bytes()) != expected:
      raise ValueError(f'Artifact checksum mismatch: {name}')
  source_hashes = {}
  for name, expected in manifest['sourceHashes'].items():
    if name.startswith(('docs/', 'tools/')):
      continue
    actual = digest((root / name).read_bytes()) if (root / name).exists() else None
    if actual != expected:
      raise ValueError(f'Source differs from validated candidate: {name}')
    source_hashes[name] = expected

  outputs = manifest['nativeOutputs']
  required = {'common/params_pyx.so', 'common/transformations/transformations.so',
              'msgq_repo/msgq/ipc_pyx.so', 'system/camerad/camerad',
              'selfdrive/pandad/pandad', 'system/loggerd/loggerd',
              'selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so'}
  if not required <= outputs.keys():
    raise ValueError('Verified overlay is incomplete')
  payload = {}
  with tarfile.open(artifact / 'native-overlay.tar.gz', 'r:gz') as archive:
    members = archive.getmembers()
    expected_names = set(outputs) | {'insight-build-manifest.json'}
    if len(members) != len(expected_names) or {m.name for m in members} != expected_names:
      raise ValueError('Unexpected archive members')
    for member in members:
      if not member.isfile():
        raise ValueError('Only regular files are allowed')
      data = archive.extractfile(member).read()
      if member.name == 'insight-build-manifest.json':
        if data != manifest_bytes:
          raise ValueError('Archive manifest does not match')
        continue
      path = root / member.name
      if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError(f'Expected an existing runtime output: {member.name}')
      expected = outputs[member.name]
      if len(data) != expected['bytes'] or digest(data) != expected['sha256']:
        raise ValueError(f'Native output mismatch: {member.name}')
      if data[:4] != b'\x7fELF' or int.from_bytes(data[18:20], 'little') != 183:
        raise ValueError(f'Expected AArch64 ELF: {member.name}')
      payload[member.name] = data

  (root / 'prebuilt').unlink(missing_ok=True)
  for name, data in payload.items():
    write_atomic(root / name, data, 0o755)
  for name, expected in outputs.items():
    if digest((root / name).read_bytes()) != expected['sha256']:
      raise ValueError(f'Packaged output verification failed: {name}')
  release_manifest = {
    'schema': 1, 'sourceRevision': revision, 'artifactTag': manifest['tag'],
    'artifactManifestSha256': digest(manifest_bytes),
    'status': 'prebuilt-candidate-not-vehicle-validated',
    'build': manifest['build'], 'nativeOutputs': outputs, 'sourceHashes': source_hashes,
  }
  write_atomic(root / 'insight-build-manifest.json', (json.dumps(release_manifest, indent=2, sort_keys=True) + '\n').encode())
  write_atomic(root / 'prebuilt', b'')
  print(f'Packaged and verified {len(payload)} native outputs; prebuilt marker written last')


if __name__ == '__main__':
  main()
