set quiet
set minimum-version := '1.55.1'
set default-list
set default-script
set shell := ['bash', '-euo', 'pipefail', '-c']
set script-interpreter := ['bash', '-euo', 'pipefail']

[group('bootstrap')]
mod? bootstrap 'bootstrap'

[group('kubernetes')]
mod? kube 'kubernetes'

[group('talos')]
mod? talos 'talos'

[private]
log lvl msg *args:
    gum log -t rfc3339 -s -l "{{ lvl }}" "{{ msg }}" {{ args }}

[doc('Format all files across the repository (YAML, JSON, Markdown, Just)')]
fmt:
    oxfmt .
    just --fmt --unstable
