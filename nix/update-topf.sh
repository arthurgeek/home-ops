#!/usr/bin/env bash
# Refresh the per-platform hashes in nix/topf.nix for its current `version`.
set -euo pipefail

file="$(dirname "$0")/topf.nix"
version="$(sed -n 's/^  version = "\(.*\)";$/\1/p' "$file")"

for asset in $(sed -n 's/^ *asset = "\(.*\)";$/\1/p' "$file"); do
    url="https://github.com/postfinance/topf/releases/download/v${version}/topf_${asset}.tar.gz"
    hash="$(nix store prefetch-file --json "$url" | jq -r .hash)"
    # The hash line sits right after its asset line.
    sed -i.bak "/asset = \"${asset}\";/{n;s|hash = \".*\";|hash = \"${hash}\";|;}" "$file"
done
rm -f "$file.bak"
