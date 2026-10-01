set quiet
set shell := ['bash', '-euo', 'pipefail', '-c']
set script-interpreter := ['bash', '-euo', 'pipefail']
set default-list
set default-script

[doc('Force Flux to pull in changes from your Git repository')]
[group('kube')]
reconcile:
    flux --namespace flux-system reconcile kustomization flux-system --with-source

[doc('Render every Flux resource offline and validate the manifests')]
[group('kube')]
[working-directory('..')]
validate:
    # Hide the per-resource passes; failures and the summary stay.
    flate test all --path ./kubernetes/flux/cluster 2>&1 | grep -v '^  ✓  '
    mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}/kubeconform"
    # flate can't decrypt SOPS values; the domain placeholder isn't a valid
    # hostname, so swap in one that is before checking schemas. Charts that
    # pick an API version from the cluster's capabilities (app-template's
    # routes) need the Gateway API advertised to render v1.
    flate build all --path ./kubernetes/flux/cluster \
        --api-versions gateway.networking.k8s.io/v1,gateway.networking.k8s.io/v1/HTTPRoute \
        | sed 's/\.\.PLACEHOLDER_SECRET_DOMAIN\.\./example.com/g' \
        | kubeconform -strict -ignore-missing-schemas -summary \
            -cache "${XDG_CACHE_HOME:-$HOME/.cache}/kubeconform" \
            -schema-location default \
            -schema-location 'https://k8s-schemas.home-operations.com/{{{{ .Group }}}}/{{{{ .ResourceKind }}}}_{{{{ .ResourceAPIVersion }}}}.json'

[doc('Report cluster resource right-sizing recommendations from Goldilocks/VPA')]
[group('kube')]
[working-directory('..')]
rightsize *args:
    uv run ./scripts/rightsize.py {{ args }}

[doc('Apply cluster resource right-sizing recommendations to HelmReleases')]
[group('kube')]
[working-directory('..')]
rightsize-apply *args:
    uv run ./scripts/rightsize.py --apply {{ args }}

[doc('Create a right-sizing git branch, commit, and prepare pull request')]
[group('kube')]
[working-directory('..')]
rightsize-pr *args:
    uv run ./scripts/rightsize.py --create-pr {{ args }}
