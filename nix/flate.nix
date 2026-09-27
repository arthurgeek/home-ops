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
  version = "0.6.5";
  platforms = {
    x86_64-linux = {
      asset = "linux_amd64";
      hash = "sha256-N+sM2RpnUjQHVDdXQ6phbCY82DZ85ZSr1C7pVeHsAJk=";
    };
    aarch64-linux = {
      asset = "linux_arm64";
      hash = "sha256-f1FIFIkN9KCViHlDrmBr4aivh4YGt70Fde/nVALNONw=";
    };
    x86_64-darwin = {
      asset = "darwin_amd64";
      hash = "sha256-Sx3O+0YTtMAvM3TI/I1AgtQtiT9DA9qhbiTjfVi8y3g=";
    };
    aarch64-darwin = {
      asset = "darwin_arm64";
      hash = "sha256-didL5e+LAW6A6gq/e29hKtx2qJ92E9Ln9+NVCvW54YY=";
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
