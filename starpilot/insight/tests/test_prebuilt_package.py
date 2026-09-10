"""Check the actual installer checkout, including its release build gate."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope='module')
def release():
  manifest = ROOT / 'insight-build-manifest.json'
  if not manifest.exists():
    pytest.skip('Source checkout has not been packaged as a device release')
  return json.loads(manifest.read_text())


def test_release_contains_verified_candidate_outputs(release):
  assert (ROOT / 'prebuilt').is_file(), 'Installer releases must not compile on-device'
  assert release['build']['architecture'] == 'larch64'
  assert len(release['nativeOutputs']) >= 19
  for name, expected in release['nativeOutputs'].items():
    data = (ROOT / name).read_bytes()
    assert len(data) == expected['bytes'], name
    assert hashlib.sha256(data).hexdigest() == expected['sha256'], name
    assert data[:4] == b'\x7fELF', name
    assert int.from_bytes(data[18:20], 'little') == 183, name
    assert (ROOT / name).stat().st_mode & 0o111, name
  for name, expected in release['sourceHashes'].items():
    if expected is None:
      assert not (ROOT / name).exists(), name
    else:
      assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name


def test_packaged_launch_does_not_invoke_compiler(release, tmp_path):
  script = (ROOT / 'launch_chffrplus.sh').read_text()
  gate = re.search(r'  if \[ ! -f "\$DIR/prebuilt" \]; then\n.*?\n  fi', script, re.DOTALL)
  assert gate
  # Execute the real launch gate. Any attempted compile exits unsuccessfully.
  compiler = tmp_path / 'build.py'
  compiler.write_text('#!/bin/sh\necho COMPILER_WAS_INVOKED\nexit 91\n')
  compiler.chmod(0o755)
  program = 'set -e\nDIR="$1"\nsp_launch_timing() { :; }\n' + gate.group(0)
  result = subprocess.run(['bash', '-c', program, 'launch-gate', str(ROOT)], cwd=tmp_path,
                          capture_output=True, text=True)
  assert result.returncode == 0, result
  assert 'COMPILER_WAS_INVOKED' not in result.stdout
  # Prove this check would have caught the original source-only installation.
  missing_marker = subprocess.run(['bash', '-c', program, 'launch-gate', str(tmp_path)], cwd=tmp_path,
                                  capture_output=True, text=True)
  assert missing_marker.returncode == 91
  assert 'COMPILER_WAS_INVOKED' in missing_marker.stdout
