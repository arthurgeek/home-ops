# flate isn't in nixpkgs; repackage the upstream release binaries.
# Renovate bumps `version`; nix/update-release-hashes.sh (run by the
# renovate-lock workflow) refreshes the per-platform hashes to match.
{
  lib,
  stdenvNoCC,
  fetchurl,
}:
let
  # renovate: datasource=github-releases depName=home-operations/flate
  version = "0.8.0";
  platforms = {
    x86_64-linux = {
      asset = "linux_amd64";
      hash = "sha256-dpQPtv8GDgmkDhqDX8nFzSThj90p5OeTgoUoIeCsAcI=";
    };
    aarch64-linux = {
      asset = "linux_arm64";
      hash = "sha256-SuUC5WBU7pxQGZlVVIVsH2+PRLP2/vCHcEstMcJ6ucY=";
    };
    x86_64-darwin = {
      asset = "darwin_amd64";
      hash = "sha256-z3jxbIHfbTmVWKTp24z8MNFr348YZHq9bgTg4ymYLME=";
    };
    aarch64-darwin = {
      asset = "darwin_arm64";
      hash = "sha256-uIHPvvZRCE0Mm5fGwtYBAwIrQ6LK7dwdBv6/k8Bl3X0=";
    };
  };
  platform =
    platforms.${stdenvNoCC.hostPlatform.system}
      or (throw "flate: unsupported system ${stdenvNoCC.hostPlatform.system}");
in
stdenvNoCC.mkDerivation {
  pname = "flate";
  inherit version;

  src = fetchurl {
    url = "https://github.com/home-operations/flate/releases/download/v${version}/flate_${version}_${platform.asset}.tar.gz";
    inherit (platform) hash;
  };

  sourceRoot = ".";
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    install -Dm755 flate "$out/bin/flate"
    runHook postInstall
  '';

  meta = {
    description = "Render and diff Flux GitOps repositories offline";
    homepage = "https://github.com/home-operations/flate";
    license = lib.licenses.agpl3Only;
    mainProgram = "flate";
    platforms = lib.attrNames platforms;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
