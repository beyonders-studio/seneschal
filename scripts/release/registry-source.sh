#!/usr/bin/env bash
# Builds an app's registry source: its plug-in, with its listing at the version
# apps/<app>/VERSION holds, written by its backend's scripts/export_plugin.py
# and built by the plug-in SDK, which checks it against the plug-in contract.
# A release attaches the archive to its GitHub Release, and initiative-packages
# signs it into the packages our deployments read.
#
# Usage: scripts/release/registry-source.sh <app> <image@sha256:…> <out.tar.gz>
#
# Prints the archive's sha256. Needs the Python workspace synced and Node.

set -euo pipefail

#: The plug-in SDK whose contract the build checks against.
SDK_VERSION="10.1.1"

app="$1"
image="$2"
out="$(realpath -m "$3")"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
backend="$ROOT/apps/$app/backend"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cd "$backend"
uv run --no-sync python scripts/export_plugin.py "$work/plugin.json" --image "$image" >&2
# The listing's pictures are named relative to the backend, where this runs.
npx --yes --package "initiative-plugin-sdk@$SDK_VERSION" \
  initiative-plugin build --manifest "$work/plugin.json" --registry "$work/registry-source" >&2
tar -C "$work/registry-source" -czf "$out" .
sha256sum "$out" | cut -d' ' -f1
