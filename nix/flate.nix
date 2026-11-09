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
  version = "0.7.3";
  platforms = {
    x86_64-linux = {
      asset = "linux_amd64";
      hash = "sha256-BdPHlEdkfpUymqRccDXn66w6EglCly54gZ3q3Dvwr+s=";
    };
    aarch64-linux = {
      asset = "linux_arm64";
      hash = "sha256-NFuLZ53kOTQwAwl7eaq+x9BwwF5w7spcQTGTupeqNo8=";
    };
    x86_64-darwin = {
      asset = "darwin_amd64";
      hash = "sha256-/GO6k9SUP1go5yQRlhPRzje1KJc4eqlY9dJj8pG7Xbk=";
    };
    aarch64-darwin = {
      asset = "darwin_arm64";
      hash = "sha256-+0rKZQtV+wINrAOXdZde86t1OkWCvdA03IrVxzrUd1U=";
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
