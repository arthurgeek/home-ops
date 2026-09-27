{
  description = "home-ops dev environment";

  # Inputs are pinned to commit SHAs so `nix flake update` is a no-op; Renovate
  # owns all bumps (see .renovaterc.json5). The trailing comment is the branch
  # Renovate follows, and .github/workflows/renovate-lock.yaml re-locks.
  inputs = {
    # renovate: datasource=git-refs depName=https://github.com/NixOS/nixpkgs
    nixpkgs.url = "github:NixOS/nixpkgs/e94cb152ed51bd6e24eb4a41f1460252beb52cd2"; # nixos-unstable
  };

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      formatter = forAllSystems (pkgs: pkgs.nixfmt);

      devShells = forAllSystems (
        pkgs:
        let
          topf = pkgs.callPackage ./nix/topf.nix { };

          tools = with pkgs; [
            # just runs recipes with the first bash on PATH; the stdenv one
            # lacks builtins like compgen that the recipes use.
            bashInteractive
            age
            cloudflared
            fluxcd
            gh
            gum
            helmfile
            jq
            just
            kubeconform
            kubectl
            kubernetes-helm
            kustomize
            lefthook
            nixfmt
            oxfmt
            sops
            talosctl
            topf
            yq-go
            zizmor
          ];

          # Paths are anchored to the repo root, not wherever the shell was entered.
          shellHook = ''
            root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
            export KUBECONFIG="$root/kubeconfig"
            export SOPS_CONFIG="$root/.sops.yaml"
            export SOPS_AGE_KEY_FILE="$root/age.key"
            export TALOSCONFIG="$root/talos/talosconfig"
            # Install into the repo's own hooks dir even when a global
            # core.hooksPath is set, rather than overwriting the global hooks.
            if [ -z "''${CI:-}" ]; then
              hooks="$(git rev-parse --path-format=absolute --git-common-dir)/hooks"
              GIT_CONFIG_COUNT=1 \
                GIT_CONFIG_KEY_0=core.hooksPath \
                GIT_CONFIG_VALUE_0="$hooks" \
                lefthook install >/dev/null
            fi
          '';
        in
        {
          default = pkgs.mkShellNoCC {
            packages = tools;
            inherit shellHook;
          };

          # Adds the tools only the template step needs; `just template tidy`
          # switches .envrc back to the default shell.
          template = pkgs.mkShellNoCC {
            packages =
              tools
              ++ (with pkgs; [
                python314
                sd
                taplo
                uv
              ]);
            # Use the Nix Python rather than a uv-downloaded one.
            UV_PYTHON_DOWNLOADS = "never";
            inherit shellHook;
          };
        }
      );
    };
}
