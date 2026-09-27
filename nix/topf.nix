# topf isn't in nixpkgs; repackage the upstream release binaries.
# Renovate bumps `version`; nix/update-release-hashes.sh (run by the
# renovate-lock workflow) refreshes the per-platform hashes to match.
{
  lib,
  stdenvNoCC,
  fetchurl,
}:
let
  # renovate: datasource=github-releases depName=postfinance/topf
  version = "0.6.0";
  platforms = {
    x86_64-linux = {
      asset = "linux_amd64";
      hash = "sha256-RY30sl9BgaMe02HAWSGU9+uebnsT5wlvh0VFOv3qrfs=";
    };
    aarch64-linux = {
      asset = "linux_arm64";
      hash = "sha256-fl1L8h8HupG4PEZT62XdPPXuZ6qPpBMcpAOzh5FHY2c=";
    };
    x86_64-darwin = {
      asset = "darwin_amd64";
      hash = "sha256-tMdvtZhcLgxz1qNlpa56Rfl/nWHLUCoBse6C5j2gsc0=";
    };
    aarch64-darwin = {
      asset = "darwin_arm64";
      hash = "sha256-SLIhddYerboMKHQRw0qQ9z26pxb9eSt7KGJdPtv4cOI=";
    };
  };
  platform =
    platforms.${stdenvNoCC.hostPlatform.system}
      or (throw "topf: unsupported system ${stdenvNoCC.hostPlatform.system}");
in
stdenvNoCC.mkDerivation {
  pname = "topf";
  inherit version;

  src = fetchurl {
    url = "https://github.com/postfinance/topf/releases/download/v${version}/topf_${platform.asset}.tar.gz";
    inherit (platform) hash;
  };

  sourceRoot = ".";
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    install -Dm755 topf "$out/bin/topf"
    runHook postInstall
  '';

  meta = {
    description = "Talos Orchestrator by PostFinance";
    homepage = "https://github.com/postfinance/topf";
    mainProgram = "topf";
    platforms = lib.attrNames platforms;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
