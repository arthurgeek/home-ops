# Talos Patching

Machine configs are assembled by [topf](https://postfinance.github.io/topf/) from
`topf.yaml` plus the strategic merge patches in this directory.

<https://www.talos.dev/latest/talos-guides/configuration/patching/>

## Patch Directories

Patches merge in this order, alphabetically within each directory, with later
patches taking precedence:

- `all/`: applied to every node
- `control-plane/`: applied to control-plane nodes
- `worker/`: applied to worker nodes
- `node/${hostname}/`: applied to the node with the specified name

Files ending in `.yaml.tpl` are Go-templated per node; see the
[topf configuration model](https://postfinance.github.io/topf/main/configuration-model/)
for the available template variables.

## Disk Layout

`all/72-volumes.yaml` splits each node's system disk (the 240GB SanDisk):
EPHEMERAL (`/var`: etcd, container images, logs) is capped at 120GB and an
`openebs` user volume, mounted at `/var/mnt/openebs`, grows into the rest
(~118GB) and backs the `openebs-hostpath` storage class. The NVMe is left whole for Ceph.

Talos only applies this when it creates partitions, so a fresh install needs
nothing extra. It never shrinks an existing partition, though: a node
installed before this patch keeps a full-size EPHEMERAL and no `openebs`
volume until EPHEMERAL is wiped once. Do it one node at a time, waiting for
etcd to be healthy again before the next:

```sh
talosctl -n <node-ip> reset --system-labels-to-wipe EPHEMERAL --reboot
talosctl -n <node-ip> get volumestatus u-openebs   # should be ready
talosctl -n <node-ip> etcd status
```

The reset is graceful: the node leaves etcd first, wipes only `/var`, reboots
with the same Talos install and config, and rejoins.
