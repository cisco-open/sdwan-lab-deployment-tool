# Catalyst SD-WAN Lab 2.0.12 [Jun 18, 2024]

- Added CML PATty support
- Bump pyats from 24.2 to 24.5

# Catalyst SD-WAN Lab 2.0.11 [May 13, 2024]

- Added sign task

# Catalyst SD-WAN Lab 2.0.10 [May 10, 2024]

- Added support for Python 3.12
- In setup task, added functionality to covert refplat ISO SD-WAN images to proper format so script can use them
- In setup task, added --list parameter to list all SD-WAN software images available on CML server
- Fixed problem where setup task might fail with "Cannot modify read-only node definition"
- Fixed problem where add task could fail with undescriptive error "IndexError: list index out of range"
- Updated README with installation guide for Windows

# Catalyst SD-WAN Lab 2.0.9 [Apr 26, 2024]

#### Fixes:

- Updated release workflow

# Catalyst SD-WAN Lab 2.0.8 [Apr 25, 2024]

#### Fixes:

- Fixed problem where devices connected on SD-WAN Edge Gi3 LAN interface after getting DHCP IP could not connect to the Internet

# Catalyst SD-WAN Lab 2.0.7 [Apr 24, 2024]

#### Fixes:

- Raised minumum Python version to 3.9
- Added support for CML 2.7.0
- Increased gateway router CPU from 1 to 2
- Added support for backup and restore task when SD-WAN Controller/Validator/Edge are custom admin password (same as SD-WAN Manager)

# Catalyst SD-WAN Lab 2.0.6 [Apr 15, 2024]

#### Fixes:

- Updated required packages

#### Optimizations:

- Added automated testing using flake8, isort, black and mypy

# Catalyst SD-WAN Lab 2.0.5 [Mar 28, 2024]

#### New Features:

- Updated license information

# Catalyst SD-WAN Lab 2.0.4 [Mar 24, 2024]

#### New Features:

- Setup tasks now accepts both "viptela-edge..." and "viptela-bond" images for SD-WAN Validator

#### Fixes:

- Fixed problem where setup task with --migrate flag might remove Gateway image
- Fixed problem where config-group creation might fail in 20.14 and higher

# Catalyst SD-WAN Lab 2.0.3 [Mar 18, 2024]

#### Fixes:

- Fixed problem where setup task might fail with TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'

# Catalyst SD-WAN Lab 2.0.2 [Mar 15, 2024]

#### New Features:

- Added Code of Conduct and Security information
- Updated rc-example.sh README.md

# Catalyst SD-WAN Lab 2.0.1 [Mar 12, 2024]

#### New Features:

- License updates
- Added contribution guidelines

# Catalyst SD-WAN Lab 2.0.0 [Mar 9, 2024]

#### New Features: 

- Added support for configuration groups in all tasks
- For add task, Edges deployed with software >= 17.12 are using configuration groups by default
- Added support for SD-Routing devices in all tasks
- Added support for backup/restore of Multi-Region Fabric regions and subregions
- Added delete task to remove the lab from CML server
- In restore task, by default the SD-WAN Manager IP, mask, gateway, username and passwords set with script will override these values from backup. This makes it easy to restore on different CML server with different addressing.
- Added --deleteexisting option to restore task for restore task to override the existing lab with same name and vmanage IP
- Added --retry option to deploy and restore task. In case your network connection flaps during task execution, you can add --retry to continue onboarding already booted SD-WAN Manager
- Added --migrate option in setup task to allow smooth migration from SD-WAN Lab 1.x.x to 2.x.x

#### Fixes:

- Fixed a problem where restore of WAN Edge could fail due to logs appearing in the bootstrap configuration
- Fixed a problem where restore was not working when SD-WAN Controllers were attached to custom device template

#### Optimizations:

- Added progress status when running without verbose mode
- Migrated from python-viptela to catalystwan library
- Migrated from setup.py to pyproject.toml
- Aligned with Catalyst SD-WAN naming
- Aligned with Catalyst SD-WAN node definitions from CML
- Changed serial files and org-name to new Virtual Account

#### Removals:

- dropped support for vEdges
- dropped support for SD-WAN releases below 20.4/17.4

# SD-WAN Lab 1.1.4 [Jan 24, 2024]

#### New features:

- Added Internet access on controllers VPN 0 transport and Edges Internet transport
- Added option to specify custom DNS server for Internet access
- Added option to specify custom CML bridge for vManage UI access

# SD-WAN Lab 1.1.3 [Dec 11, 2023]

#### New features:

- For deploy and restore tasks, increased wait time for vManage API to 1 hour and root cert installation timeout to 2 minutes.

#### Fixes:

- Fixed problem where deploy task for 20.9.4.1 vManage and 20.9.4 vSmart/vBond overlay was failing with "Requested software image version 20.9.4.1 is not found in CML."
- Fixed problem where Gateway router was not getting DHCP IP on GigabitEthernet4
- Fixed problem where add task fails with ValueError after user changed vSmart system-ip

# SD-WAN Lab 1.1.2 [Nov 22, 2023]

#### Fixes:

- Fixed problem where add task would fail with ValueError: max() arg is an empty sequence

# SD-WAN Lab 1.1.1 [Nov 21, 2023]

#### New features:

- Increase default numbers of WAN Edge ports from 4 to 8
- Increase number of C8000V available serial numbers from 8 to 20
- Added 10 serial numbers for SD-Routing C8000V
- Added DHCP pool on the LAN interface for C8000V device template

#### Fixes:

- Fixed problem where Internet node was not booting during deploy task
- Fixed problem where backup task fails with: "load()" has been removed
- Fixed problem where cEdge console might stop working after rebooting causing backup task to fail

# SD-WAN Lab 1.1.0 [Sep 13, 2023]

#### New features:

- Introduced support for CML>=2.6, dropped support for CML<2.6
- Implemented new "add" task that automatically onboards additional vSmarts/vBonds/vEdges/cEdges to existing topology
- Added new gateway configuration including VRF split and DHCP pools for INET/MPLS transports and DNS resolution for vBond FQDN
- Added external Internet access for Internet transport with NAT external connector
- Added service VPN 1 and service VPN interface ge0/2 (vEdge) and Gi3 (cEdge) to default device templates that allows to do a quick tests without manually modifying the basic template
- Added automatic enablement of data stream when deploying vManage
- Added support for backup/restore of multiple vSmarts and vBonds
- Added 31 ports on all unmanaged switches in default topology which allows to build larger topologies
- When user requests software image that is not available, tool will print the available software images

#### Fixes:

- Provided final fix for encrypting vManage password in cloud-init
- Fixed problem with 20.12 vManage deployment failure
- Fixed problem where backup was created only for first lab if there were multiple labs with the same name and user was not aware of that
- Fixed problem where backup task was failing when lab contained nodes that doesn't support configuration extract
- Fixed logging to make sure when verbose is enabled, it sets logging level INFO only for sdwan_lab and not for other modules like virl2_client or sastre. This makes easier for people to understand what is happening.

# SD-WAN Lab 1.0.3 [Apr 28, 2023]

#### New features:

- Raised the memory requirements for vmanage (16GB->32GB) and cedge (4GB->5GB) nodes
- Enhanced setup task to be able to update the node definition with new parameters when node is already defined in the CML
- Loosen the packages requirements for the tool

#### Fixes:

- Fixed problem where restore task might fail with "TypeError: expected string or bytes-like object"
- Addressed vulnerability CVE-2022-40897
- Fixed problem where mandatory parameters in backup/restore tasks were treated as optional and were leading to crash

# SD-WAN Lab 1.0.2 [Nov 8, 2022]

#### Fixes:

- Fixed problems with backup and restore tasks

# SD-WAN Lab 1.0.1 [Sep 5, 2022]

#### New features:

- Added AIDE stats collection
- Added support for 20.9 software release

#### Fixes:

- Initial tool release.

# SD-WAN Lab 1.0.0 [Aug 1, 2022]

#### New features:

- Initial tool release.
