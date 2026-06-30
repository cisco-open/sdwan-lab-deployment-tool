[![Tests](https://github.com/cisco-open/sdwan-lab-deployment-tool/actions/workflows/test.yml/badge.svg)](https://github.com/cisco-open/sdwan-lab-deployment-tool/actions/workflows/test.yml)
![Python Support](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-informational "Python Support: 3.11, 3.12, 3.13, 3.14")

# Catalyst SD-WAN Lab Deployment Tool for Cisco Modeling Labs

This tool automates [Cisco Catalyst SD-WAN](https://www.cisco.com/site/us/en/solutions/networking/sdwan/index.html) lab deployment inside [Cisco Modeling Labs (CML)](https://www.cisco.com/c/en/us/products/cloud-systems-management/modeling-labs/index.html).

It deploys a complete SD-WAN control plane (Manager, Validator, Controller) and lets you add SD-WAN and SD-Routing edges on demand — all fully automated.

## Requirements

- Python 3.11 or newer
- CML 2.7 or newer
- Cisco Catalyst SD-WAN Manager 20.15 or newer

## Installing

The recommended way to install is via [uv](https://docs.astral.sh/uv/):

```sh
uv tool install catalyst-sdwan-lab
```

Or using pip in a virtual environment:

```sh
python3 -m venv venv
source venv/bin/activate
pip install catalyst-sdwan-lab
```

Verify the installation:

```sh
csdwan --version
```

Both `sdwan-lab` and `csdwan` are available as CLI entry points.

> After upgrading to a new version, run `csdwan setup` once before running other tasks.

## Usage

```
csdwan [OPTIONS] COMMAND [ARGS]...
```

### Global Options

| Option | Env var | Description |
|---|---|---|
| `--cml, -c` | `CML_IP` | CML hostname or IP |
| `--user, -u` | `CML_USER` | CML username |
| `--password, -p` | `CML_PASSWORD` | CML password |
| `--verbose, -v` | | Show INFO-level output |
| `--debug` | | Show DEBUG-level output including HTTP requests |
| `--version` | | Show version and exit |

### Environment Variables

All credentials and lab settings can be provided via environment variables. The easiest approach is a shell file you source before running any task:

```sh
# lab.env
export CML_IP='cml.example.com'
export CML_USER='admin'
export CML_PASSWORD='your-password'
export MANAGER_PORT=2000          # PATty mode; omit for direct IP mode
export MANAGER_USER='sdwan'
export MANAGER_PASSWORD='your-manager-password'
export LAB_NAME='sdwan-lab'
```

```sh
source lab.env
csdwan deploy 20.15.1
```

Available environment variables:

| Variable | Used by |
|---|---|
| `CML_IP` | All commands |
| `CML_USER` | All commands |
| `CML_PASSWORD` | All commands |
| `LAB_NAME` | `deploy`, `add`, `backup`, `restore`, `delete` |
| `MANAGER_IP` | `deploy`, `restore` (direct mode) |
| `MANAGER_PORT` | `deploy`, `restore` (PATty mode) |
| `MANAGER_MASK` | `deploy`, `restore` (direct mode) |
| `MANAGER_GATEWAY` | `deploy`, `restore` (direct mode) |
| `MANAGER_USER` | `deploy`, `add`, `backup`, `restore` |
| `MANAGER_PASSWORD` | `deploy`, `add`, `backup`, `restore` |
| `PROXY_IP` | `deploy`, `restore` |
| `PROXY_PORT` | `deploy`, `restore` |
| `NO_PROXY` | `deploy`, `restore` |

---

## Commands

### `setup`

Prepares CML for SD-WAN lab deployment. Creates the required node definitions. Run once after install and after tool upgrades.

```sh
csdwan setup
```

---

### `images`

Manages SD-WAN software images in CML.

#### `images list`

Lists installed SD-WAN software versions per node type.

```sh
csdwan images list
```

#### `images upload`

Uploads `.qcow2` image files from a directory to CML and creates image definitions.

```sh
csdwan images upload              # searches current directory
csdwan images upload --dir /path/to/images
```

#### `images delete`

Deletes image definitions and files for the specified version(s).

```sh
csdwan images delete 20.12.1
csdwan images delete 20.12.1 20.9.4 --dry-run
```

---

### `deploy`

Deploys a complete Catalyst SD-WAN lab in CML. This task:

1. Creates the CML topology with INET and MPLS underlay networks, Manager, Validator, Controller, and a Gateway router.
2. Waits for all nodes to boot and configures the SD-WAN control plane (certificates, onboarding, etc.).
3. Imports basic feature templates and configuration groups so you can immediately start adding edges.

```
csdwan deploy [OPTIONS] <version>
```

**Positional arguments:**

| Argument | Description |
|---|---|
| `version` | SD-WAN software version, e.g. `20.15.1` |

**Options:**

| Option | Env var | Description |
|---|---|---|
| `--manager-ip` | `MANAGER_IP` | Manager IP address (direct mode) |
| `--manager-mask` | `MANAGER_MASK` | Subnet mask, e.g. `/24` (direct mode) |
| `--manager-gateway` | `MANAGER_GATEWAY` | Default gateway (direct mode) |
| `--manager-port` | `MANAGER_PORT` | PATty external port; enables PATty mode |
| `--manager-user` | `MANAGER_USER` | Manager username (default: `admin`) |
| `--manager-pass` | `MANAGER_PASSWORD` | Manager password |
| `--lab` | `LAB_NAME` | CML lab name |
| `--ip-type` | | Overlay IP type: `v4` (default), `v6`, or `dual` |
| `--bridge` | | Custom CML bridge for Manager (default: System Bridge) |
| `--dns` | | DNS server for lab nodes |
| `--retry` | | Resume Manager onboarding without recreating the lab |
| `--serial-file` | | Custom `.viptela` serial file |
| `--pki` | | Certificate signing mode: `enterprise` (default) or `cisco` (requires 20.18.2+) |
| `--proxy-ip` | `PROXY_IP` | HTTP proxy hostname or IP for Manager's outbound connections |
| `--proxy-port` | `PROXY_PORT` | HTTP proxy port (default: `80`) |
| `--no-proxy` | `NO_PROXY` | Additional no-proxy entries; RFC1918 ranges are always excluded |

**Manager connectivity modes:**

*Direct mode* — Manager is reachable on its own IP address (requires `--manager-ip`, `--manager-mask`, `--manager-gateway`):

```sh
csdwan deploy 20.15.1 \
  --manager-ip 10.0.0.10 --manager-mask /24 --manager-gateway 10.0.0.254 \
  --lab my-lab
```

*PATty mode* — Manager is accessed via CML's PATty port mapping (requires `--manager-port`):

```sh
csdwan deploy 20.15.1 --manager-port 2000 --lab my-lab
```

---

### `add`

Adds and automatically onboards SD-WAN devices to an existing lab. The command detects the lab's IP type (v4/v6/dual) and uses it automatically.

```
csdwan add [OPTIONS] <count> <device-type> <version>
```

**Positional arguments:**

| Argument | Description |
|---|---|
| `count` | Number of devices to add |
| `device-type` | `manager(s)`, `controller(s)`, `validator(s)`, `edge(s)`, `sdrouting` |
| `version` | SD-WAN software version |

**Options:**

| Option | Env var | Description |
|---|---|---|
| `--lab` | `LAB_NAME` | CML lab name |
| `--manager-user` | `MANAGER_USER` | Manager username (default: `admin`) |
| `--manager-pass` | `MANAGER_PASSWORD` | Manager password |
| `--persona` | | Manager cluster persona: `compute-and-data` (default), `compute`, `data` — only for `manager(s)` |
| `--cpus` | | Override CPU count for each added node |
| `--ram` | | Override RAM in MB for each added node |

**Examples:**

```sh
csdwan add 2 managers 20.15.1
csdwan add 2 managers 20.15.1 --persona data
csdwan add 1 validator 20.15.1
csdwan add 2 controllers 20.15.1
csdwan add 3 edges 20.15.1
csdwan add 2 sdrouting 17.15.1
```

Adding managers deploys additional Manager nodes, sets up the cluster interface, enrolls them into the existing Manager cluster, and signs their certificates. The cluster switch is created automatically in the CML topology.

---

### `backup`

Backs up a running SD-WAN lab to a zip archive (or directory). Captures:

- CML topology with running configs extracted via SSH from all nodes
- SD-WAN Manager configuration (templates, config groups, policies, feature profiles) via Sastre
- Network hierarchy (MRF regions)
- Cluster configuration including node personas

The lab must be running. Configs are extracted live over SSH — shut-down nodes are skipped with a warning.

```
csdwan backup [OPTIONS]
```

| Option | Env var | Description |
|---|---|---|
| `--lab` | `LAB_NAME` | CML lab name |
| `--manager-user` | `MANAGER_USER` | Manager username (default: `admin`) |
| `--manager-pass` | `MANAGER_PASSWORD` | Manager password |
| `--output, -o` | | Output path (default: `<lab>-<YYYYMMDD>.zip`) |
| `--directory, -d` | | Save as unpacked directory instead of zip |

**Examples:**

```sh
csdwan backup --lab my-lab --manager-pass secret
csdwan backup --lab my-lab --manager-pass secret -o /backups/my-lab.zip
csdwan backup --lab my-lab --manager-pass secret --directory -o /backups/my-lab
```

> **Note:** If the lab was deployed with a custom serial file, restore requires the same serial file to re-authorise edge devices.

---

### `restore`

Restores a Catalyst SD-WAN lab from a backup archive. Recreates the CML lab, boots the control plane, restores Manager configuration via Sastre, then onboards edges using fresh bootstrap configs from Manager.

```
csdwan restore [OPTIONS] <backup>
```

**Positional arguments:**

| Argument | Description |
|---|---|
| `backup` | Path to backup zip file or directory |

**Options:**

| Option | Env var | Description |
|---|---|---|
| `--lab` | `LAB_NAME` | CML lab name |
| `--manager-ip` | `MANAGER_IP` | Manager IP address (direct mode) |
| `--manager-port` | `MANAGER_PORT` | PATty external port; enables PATty mode |
| `--manager-mask` | `MANAGER_MASK` | Manager subnet mask (direct mode) |
| `--manager-gateway` | `MANAGER_GATEWAY` | Manager default gateway (direct mode) |
| `--manager-user` | `MANAGER_USER` | Manager username (default: `admin`) |
| `--manager-pass` | `MANAGER_PASSWORD` | Manager password |
| `--serial-file` | | Custom `.viptela` serial file (required if backup used one) |
| `--contr-version` | | Override control plane image version |
| `--edge-version` | | Override edge image version |
| `--delete-existing` | | Delete existing lab with the same name before restoring |
| `--retry` | | Resume from Manager boot, skipping lab import |
| `--pki` | | Certificate signing mode: `enterprise` (default) or `cisco` (requires 20.18.2+) |
| `--proxy-ip` | `PROXY_IP` | HTTP proxy hostname or IP for Manager's outbound connections |
| `--proxy-port` | `PROXY_PORT` | HTTP proxy port (default: `80`) |
| `--no-proxy` | `NO_PROXY` | Additional no-proxy entries; RFC1918 ranges are always excluded |

**Examples:**

```sh
csdwan restore my-lab-20240601.zip --manager-port 2000
csdwan restore my-lab-20240601.zip --delete-existing --manager-port 2000
csdwan restore /backups/my-lab --manager-ip 10.0.0.10 --manager-mask /24 --manager-gateway 10.0.0.254
```

---

### Cisco PKI

By default, control-plane certificates are signed by the included Enterprise CA. Cisco PKI uses Cisco's cloud signing infrastructure instead and requires Manager 20.18.2 or later.

To use Cisco PKI, pass `--pki cisco` to `deploy` or `restore`. If Manager needs a proxy to reach Cisco's cloud, also pass `--proxy-ip`.

**Authentication flow:**

On first use, the tool initiates a device code flow and prints an activation URL:

```
Cisco account registration required. Open this URL in your browser:
  https://id.cisco.com/activate?user_code=XXXXXXXX
```

Open the URL, log in with your Cisco.com account, and approve the request. The tool polls automatically and continues once confirmed.

**Example:**

```sh
csdwan deploy 26.1.1 --manager-port 2000 --lab my-lab --pki cisco --proxy-ip proxy.example.com
```

```sh
csdwan restore my-lab-20240601.zip --manager-port 2000 --pki cisco --proxy-ip proxy.example.com
```

**Notes:**
- Cisco PKI requires **Virtual Account Administrator** access to the organization name you are deploying. Always use `--serial-file` to provide a serial file from the organization to which you have VA admin access — the org name is read from the serial file and must match your Cisco.com account's VA.
- Cisco limits the number of certificates that can be issued per Smart Account. For lab use, **Enterprise PKI (the default) is recommended** — it has no such limit and does not require internet connectivity.
- RFC1918 ranges (`10.*`, `172.*`, `192.168.*`) are always excluded from the proxy automatically.
- Use `--no-proxy` to add extra no-proxy entries on top of the built-in exclusions.
- The `add` command detects the PKI mode from Manager automatically — no flag needed.

---

### `delete`

Deletes a CML lab and all its data. This operation is irreversible.

```
csdwan delete [OPTIONS]
```

| Option | Env var | Description |
|---|---|---|
| `--lab` | `LAB_NAME` | CML lab name |
| `--force, -f` | | Skip confirmation prompt |

---

### `sign`

Signs a Certificate Signing Request (CSR) using the SD-WAN Lab Deployment Tool Root CA.

```
csdwan sign [OPTIONS] <csr-file>
```

| Argument/Option | Description |
|---|---|
| `csr-file` | Path to the CSR file (`.pem` or `.txt`) |
| `--output, -o` | Write signed certificate to a file instead of stdout |

---

## Limitations and Scale

Per CML lab:

- 1 SD-WAN Manager (cluster not supported)
- 8 SD-WAN Validators
- 12 SD-WAN Controllers
- 20 SD-WAN Edges
- 10 SD-Routing Edges

The full topology requires at least 9 nodes and is not supported on CML Free.

## Offline Installation

To install in an air-gapped environment, on a machine with internet access download the package and all dependencies:

```sh
pip download catalyst-sdwan-lab -d ./catalyst_sdwan_lab_packages
```

Transfer the `catalyst_sdwan_lab_packages` folder to the air-gapped machine, then install:

```sh
python3 -m venv venv
source venv/bin/activate
pip install --no-index --find-links=/path/to/catalyst_sdwan_lab_packages catalyst-sdwan-lab
```

## Windows (WSL)

This tool requires Linux or macOS. On Windows, use [WSL](https://learn.microsoft.com/en-us/windows/wsl/install).

Make sure hardware virtualization is enabled in BIOS or your VM configuration. On Windows Server, enable the WSL feature first:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
```

Then install WSL with the default (Ubuntu) distribution:

```powershell
wsl --install
```

After restarting Windows, follow the standard [installation steps](#installing) from inside the WSL terminal.

## FAQ

**Q: My devices' consoles stopped working after creating custom configuration groups.**

A: Always include `platform console serial` in a CLI add-on feature parcel. A WAN Edge reboot is required after adding it.

**Q: Can I SSH directly to the Manager?**

A: Yes in direct IP mode. In PATty mode, only the HTTPS port is mapped.

## Authors

Tomasz Zarski (tzarski@cisco.com)

## License

BSD-3-Clause

## Acknowledgments

- Marcelo Reis and [Sastre](https://github.com/CiscoDevNet/sastre)
- Inigo Alonso
- Lars Granberg