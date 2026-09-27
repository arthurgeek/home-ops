#!/usr/bin/env bash
# Refresh the per-platform hashes of every release-binary package in nix/
# for its current `version`, using the package's own fetchurl URL.
set -euo pipefail

for file in "$(dirname "$0")"/*.nix; do
    grep -q '^ *asset = ' "$file" || continue
    version="$(sed -n 's/^  version = "\(.*\)";$/\1/p' "$file")"
    template="$(sed -n 's/^ *url = "\(.*\)";$/\1/p' "$file")"

    for asset in $(sed -n 's/^ *asset = "\(.*\)";$/\1/p' "$file"); do
        url="${template//\$\{version\}/$version}"
        url="${url//\$\{platform.asset\}/$asset}"
        hash="$(nix store prefetch-file --json "$url" | jq -r .hash)"
        # The hash line sits right after its asset line.
        sed -i.bak "/asset = \"${asset}\";/{n;s|hash = \".*\";|hash = \"${hash}\";|;}" "$file"
    done
    rm -f "$file.bak"
done
