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
  version = "0.6.2";
  platforms = {
    x86_64-linux = {
      asset = "linux_amd64";
      hash = "sha256-eT3IfJemVF/XPbgsNVgwZkTZ5pFAEPJjbRRwz1UnJAU=";
    };
    aarch64-linux = {
      asset = "linux_arm64";
      hash = "sha256-j1KL2Q/D0XOgoQYJ28GlABrgAvRP11whS152Cv8/BRQ=";
    };
    x86_64-darwin = {
      asset = "darwin_amd64";
      hash = "sha256-GTcLDpRRjVTULZiL54Mf1vXRAGfjSEBCp0Kr5EAqiKk=";
    };
    aarch64-darwin = {
      asset = "darwin_arm64";
      hash = "sha256-wmPBOXbriLN6KuOpW3t2STq/FjC4GVCbINa0gazZ+Lc=";
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
