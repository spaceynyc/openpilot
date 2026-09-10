#!/usr/bin/env python3
"""Copy candidate source changes into an existing, isolated baseline checkout.

Preserves that checkout's rebuilt native artifacts. Never redirects imports into
another source tree. The emitted hashes are consumed by the provenance check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

BASE = "1c35e376e9e427431c83dd199ad7d1bdb0044975"


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("destination", type=Path)
  args = parser.parse_args()
  source = Path(__file__).resolve().parents[2]
  dest = args.destination.resolve()
  if dest == source or not (dest / ".git").exists():
    parser.error("Destination must be a separate existing baseline checkout")
  paths = set(subprocess.check_output(["git", "diff", "--name-only", BASE], cwd=source, text=True).splitlines())
  paths.update(subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=source, text=True).splitlines())
  manifest = {}
  for relative in sorted(paths):
    original, target = source / relative, dest / relative
    if not original.exists():
      target.unlink(missing_ok=True)
      manifest[relative] = None
      continue
    if original.suffix in (".so", ".a", ".o"):
      raise ValueError(f"Refusing to copy native artifact: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, target)
    manifest[relative] = hashlib.sha256(original.read_bytes()).hexdigest()
  (dest / ".insight-source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
  print(f"Synchronized {len(paths)} candidate source paths; native artifacts preserved")


if __name__ == "__main__":
  main()
