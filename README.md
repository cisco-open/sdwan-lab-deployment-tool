[![Tests](https://github.com/cisco-open/sdwan-lab-deployment-tool/actions/workflows/test.yml/badge.svg)](https://github.com/cisco-open/sdwan-lab-deployment-tool/actions/workflows/test.yml)
![Python Support](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-informational "Python Support: 3.9, 3.10, 3.11, 3.12, 3.13")

# Catalyst SD-WAN Lab Deployment Tool for Cisco Modeling Labs

This tool automates [Cisco Catalyst SD-WAN](https://www.cisco.com/site/us/en/solutions/networking/sdwan/index.html) lab deployment inside [Cisco Modeling Labs (CML)](https://www.cisco.com/c/en/us/products/cloud-systems-management/modeling-labs/index.html).

The tool will help you automate your CML lab deployments with SD-WAN Manager, Controllers and Validators and up to 20 SD-WAN edges. You can build as pods as your CML platform can host. Please refer to the [Limitations and scale](#limitations-and-scale) for details.

## Getting Started

### Prerequisites

Catalyst SD-WAN Lab Deployment Tool requires Linux or macOS system.
To run is on Windows, please use [Linux on Windows with WSL](/README.md#appendix---wsl-installation) or set up Linux VM/container.

Catalyst SD-WAN Lab Deployment Tool requires Python 3.9.2 or newer. This can be verified by pasting the following to a terminal window:

    python3 -c "import sys; assert sys.version_info >= (3, 9, 2)" && echo "ALL GOOD"

If 'ALL GOOD' is printed it means Python requirements are met. If not, download and install the supported 3.x version at Python.org (https://www.python.org/downloads/).

All the Python prerequisites are automaticaly installed when you install the package. Please refer to the [Installing](#installing) section for details.

This tool requires CML 2.6 or higher.

Demo of the tool and guide on how to use it can be found on this [youtube video](https://www.youtube.com/watch?v=WxiZ5bxlDk8).

### Installing

The recommended way to install is via pip.

Create a directory to store the virtual environment and runtime files:

      mkdir csdwan
      cd csdwan

Create virtual environment:

      python3 -m venv venv

Activate virtual environment:

     source venv/bin/activate

- Note that the prompt is updated with the virtual environment name (venv), indicating that the virtual environment is active.

Upgrade initial virtual environment packages:

    pip install --upgrade pip setuptools

To install SD-WAN Lab Deployment Tool:

    pip install --upgrade catalyst-sdwan-lab

Verify that SD-WAN Lab tool can run:

    sdwan-lab --version

You can also use the following shortcut to run any lab task:

    csdwan --version

Notes:

- The virtual environment is deactivated by typing 'deactivate' at the command prompt.
- Before running Catalyst SD-WAN Lab Deployment Tool again, make sure to activate the virtual environment back again (source venv/bin/activate).

## Usage

Simmilar to [Sastre](https://github.com/CiscoDevNet/sastre), the command line is structured as a set of base parameters, the task specification followed by task-specific parameters:

      sdwan-lab <base parameters> <task> <task-specific parameters>

Base parameters define global options such as verbosity level, CML credentials, etc.

Task indicates the operation to be performed. The following tasks are currently available:

- [Setup](#setup-task): Setup CML to support Catalyst SD-WAN use cases, upload new software images, and create node and image definitions that are required to run Catalyst SD-WAN lab in the CML.
- [Deploy](#deploy-task): Deploy CML topology with two underlay networks (INET, MPLS), one Manager/Validator/Controller, and one Gateway router. Once topology boots up, configure the control components and create basic templates / configuration groups.
- [Add](#add-task): Add and automatically onboard additional SD-WAN nodes (Validators/Controllers/Edges) to existing lab.
- [Backup](#backup-task): Backup the Catalyst SD-WAN Lab runnning in CML, including the CML topology and all its nodes, SD-WAN device states and templates / configuration groups.
- [Restore](#restore-task): Restore the Catalyst SD-WAN Lab from backup, onboard and confgure control components and create basic feature templates / configuration groups. If there are any WAN Edges, automatically onboard the WAN Edges back to the SD-WAN Manager using the configuration from the backup.
- [Delete](#delete-task): Delete currently running lab from CML and remove all lab data.
- [Sign](#sign-task): Sign Certificate Signing Request (CSR) using SD-WAN Lab Deployment Tool Root CA

Task-specific parameters are provided after the task argument.

### Base Parameters

      sdwan-lab -h

       Usage: sdwan-lab [OPTIONS] COMMAND [ARGS]...

      ╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
      │ --version                          Show the version and exit.                                                                                                                              │
      │ --verbose   -v  <verbosity level>  Verbose mode. Multiple -v options increase the verbosity.                                                                                               │
      │ --cml       -c  <cml-ip>           CML IP address, can also be defined via CML_IP environment variable.                                                                                    │
      │ --user      -u  <cml-user>         CML username, can also be defined via CML_USER environment variable.                                                                                    │
      │ --password  -p  <cml-password>     CML password, can also be defined via CML_PASSWORD environment variable.                                                                                │
      │ --help      -h                     Show this message and exit.                                                                                                                             │
      ╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
      ╭─ Commands ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
      │ add                     Add Catalyst SD-WAN device to running lab pod.                                                                                                                     │
      │ backup                  Backup running Catalyst SD-WAN lab pod.                                                                                                                            │
      │ delete                  Delete the CML lab and all the lab data.                                                                                                                           │
      │ deploy                  Deploy a new Catalyst SD-WAN lab pod.                                                                                                                              │
      │ restore                 Restore Catalyst SD-WAN POD from backup.                                                                                                                           │
      │ setup                   Setup CML to use Catalyst SD-WAN Lab automation.                                                                                                           │
      │ sign                    Sign CSR using the SD-WAN Lab Deployment Tool Root CA.                                                                                                             │
      ╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Most of the parameters can also be provided via environment variables:

- CML_IP
- CML_USER
- CML_PASSWORD
- MANAGER_IP
- MANAGER_USER
- MANAGER_PASSWORD
- MANAGER_MASK
- MANAGER_GATEWAY
- LAB_NAME

For any of the required arguments, user is prompted for a value if they are not provided via the environment variables or command line arguments.

The easiest way to run the tool is to provide all the lab variables in the rc file and source that file. The example file below contains all the variables required to run all the tasks.

    % cat rc-example.sh
    export CML_IP='10.0.0.1'
    export CML_USER='user1'
    export CML_PASSWORD='2ajG$I2?8k'
    export MANAGER_IP='10.0.0.10'
    export MANAGER_USER='sdwan'
    export MANAGER_PASSWORD='2ajG$I2?8k'
    export MANAGER_MASK='/24'
    export MANAGER_GATEWAY='10.0.0.254'
    export LAB_NAME='sdwan'
    % source rc-example.sh

Note that if password was not defined, the user will be prompted for a password. Also please note we recommend not to use admin user as MANAGER_USER. Instead, configure a different user name to always have a backup user. By default, the MANAGER_PASSWORD will be set for both admin user and the MANAGER_USER that you specify.

Note that MANAGER_IP can be:

- an IP address: SD-WAN Manager will be reachable over this IP address. By default the IP address should come from the same subnet as CML IP, unless custom bridge is specified during deploy task.
- a PATty port in format "pat:<outside-port>": SD-WAN Manager will be reachable over CML IP port <outside-port>. Before using this option, PATTy needs to be enabled on the CML server as per [CML documentation](https://developer.cisco.com/docs/modeling-labs/patty-tool-mapping-configuration/).

If you want to use PATty the rc file above will change slighty to the following.

    % cat rc-PATty-example.sh
    export CML_IP='10.0.0.1'
    export CML_USER='user1'
    export CML_PASSWORD='2ajG$I2?8k'
    export MANAGER_IP='pat:2002'
    export MANAGER_USER='sdwan'
    export MANAGER_PASSWORD='2ajG$I2?8k'
    export LAB_NAME='sdwan-PATty'
    % source rc-PATty-example.sh

### Task-specific Parameters

Task-specific parameters and options are defined after the task is provided. Each task has its own set of parameters. Check the task documentation to learn more about task-specific parameters.

### Setup Task

This task makes sure your CML is ready to run Catalyst SD-WAN labs. The task will:

- Create node definitions that are required to run Catalyst SD-WAN lab in the CML: Manager, Validator, Controller and Edge
- Look for new SD-WAN software images in the folder where the script was run. If the image is found, upload the image to CML and create image definition for the right node definition: Manager, Validator, Controller and Edge

On each CML server that you want to use, you should run a setup task at least once to create required node and image definitions. You can rerun the setup task each time you want to add a new Catalyst SD-WAN software image to your CML server.

This task can also delete existing image definitions to clean up old SD-WAN releases from CML server.

      sdwan-lab setup -h

       Usage: sdwan-lab setup [OPTIONS]

      ╭─ Options ────────────────────────────────────────────────────────────────────────────────────────────╮
      │ --delete  -d  <software_versions>  Delete all image definitions for the specified software           │
      │                                    version(s). To specify multiple versions, separate them with a    │
      │                                    comma.                                                            │
      │ --list    -l                       List the available SD-WAN software per node type and exit.        │
      │ --help    -h                       Show this message and exit.                                       │
      ╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯

### Deploy Task

This task:

1. Defines four/five subnets:
   - VPN0 - 172.16.0.0/24
   - INET - 172.16.1.0/24
   - MPLS - 172.16.2.0/24
   - External Connector - in bridge mode, this subnet is defined by task-specific parameters and is used to provide external reachability to SD-WAN Manager.
   - Internet Connector - in NAT mode, this subnet provides Internet connectivity for Internet transport and is same as CML subnet
2. Deploys basic SD-WAN topology with:
   - two underlay networks (INET, MPLS)
   - one Manager
   - one Validator
   - one Controller
   - one Gateway router that connects VPN0 subnet to INET and MPLS subnets
3. Once topology boots up, the task configures the control plane (control components, certificates, etc.) and create basic feature templates / configuration groups that can be used for WAN Edge onboarding. It also attaches Controller to device template.
4. At this point you can start creating your custom topology using [Add Task](#add-task) to automatically onboard additional SD-WAN nodes (Validators/Controllers/Edges).

You should run this task to deploy a new lab with control plane configured and build any WAN Edge topology you like.

This task has several task-specific parameters, including software version that you want to run on the control components.

      (venv) tzarski:~$sdwan-lab deploy -h

       Usage: sdwan-lab deploy [OPTIONS] <software-version>


       positional arguments:
         <software-version>    Software version that will be used on SD-WAN Control Components.

      ╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
      │ --manager        <manager-ip>          SD-WAN Manager IP address, can also be defined via MANAGER_IP environment                                                                           │
      │ --muser          <manager-user>        SD-WAN Manager username, can also be defined via MANAGER_USER environment variable.                                                                 │
      │ --mpassword      <manager-password>    SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable.                                                             │
      │ --mmask          <manager-mask>        Subnet mask for given SD-WAN Manager IP (e.g. /24), can also be defined via MANAGER_MASK environment variable.                                      │
      │ --mgateway       <manager-gateway>     Gateway IP for given SD-WAN Manager IP, can also be defined via MANAGER_GATEWAY environment variable.                                               │
      │ --lab            <lab_name>            Set CML Lab name, can also be defined via LAB_NAME environment variable. If not provided, default name "sdwan<number>" will be assigned.            │
      │ --bridge         <custom-bridge-name>  Set custom bridge for SD-WAN Manager external connection. Default is System Bridge                                                                  │
      │ --dns            <dns-server-ip>       Set custom DNS server for Internet/VPN0 transport. Default is same as CML DNS                                                                       │
      │ --retry                                If for some reason your script lost connectivity during SD-WAN Manager boot, you can add --retry to continue onboarding the lab that is already in  │
      │                                        CML                                                                                                                                                 │
      │ --help       -h                        Show this message and exit.                                                                                                                         │
      ╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Time to complete the deployment task depends on:

- SD-WAN software version
- CML resources

### Add Task

This task adds Catalyst SD-WAN nodes (Validators/Controllers/Edges) into existing Catalyst SD-WAN lab. This task will:

1. Add requested number of nodes to the CML topology and boot them with cloud-init configuration
2. Once nodes boot up, automatically onboard them to SD-WAN Manager
3. For Controller/Edge nodes, automatically attach basic device template / configuration group
4. For Validator nodes, automatically update Validator FQDN mapping with new IP addresses

This task has several task-specific parameters.

      sdwan-lab add -h

       Usage: sdwan-lab add [OPTIONS] <number-of-devices> <device-type> <software-version>


       positional arguments:
         <number-of-devices>   Number of devices to be added.
         <device-type>         Type of device/s to be added (e.g. validator, controller, edge, sdrouting).
         <software-version>    Software version that will be used.

      ╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
      │ --manager        <manager-ip>        SD-WAN Manager IP address, can also be defined via MANAGER_IP environment                                                                             │
      │ --muser          <manager-user>      SD-WAN Manager username, can also be defined via MANAGER_USER environment variable.                                                                   │
      │ --mpassword      <manager-password>  SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable.                                                               │
      │ --lab            <lab_name>          CML Lab name, can also be defined via LAB_NAME environment variable.                                                                                  │
      │ --help       -h                      Show this message and exit.                                                                                                                           │
      ╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Below you will find few examples of add task:

    sdwan-lab add 1 validator 20.9.4 --lab sdwan1
    sdwan-lab add 2 controllers 20.12.2 --lab sdwan2
    sdwan-lab add 5 edges 17.09.03a --lab vsdwan1
    sdwan-lab add 2 sdrouting 17.12.2 --lab vsdwan1

### Backup Task

This task creates a backup of the Catalyst SD-WAN lab running in CML. CML doesn't natively support configuration export from Catalyst SD-WAN nodes, but this script can help you to save your Catalyst SD-WAN configuration. This task will:

1. For Manager, Validator, Controller and WAN Edge nodes (SD-WAN and SD-Routing), create configuration backup.
2. For non-SD-WAN nodes, export the configuration if it's supported by CML.
3. Save the CML topology with exported configuration.
4. Create a backup of SD-WAN Manager templates, policies and configuration groups using [Sastre](https://github.com/CiscoDevNet/sastre).

This task has several task-specific parameters, including working directory where backup is saved.

      sdwan-lab backup -h

       Usage: sdwan-lab backup [OPTIONS]

      ╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
      │ --manager        <manager-ip>        SD-WAN Manager IP address, can also be defined via MANAGER_IP environment                                                                             │
      │ --muser          <manager-user>      SD-WAN Manager username, can also be defined via MANAGER_USER environment variable.                                                                   │
      │ --mpassword      <manager-password>  SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable.                                                               │
      │ --lab            <lab_name>          CML Lab name, can also be defined via LAB_NAME environment variable.                                                                                  │
      │ --workdir        <directory>         Backup destination folder                                                                                                                             │
      │ --help       -h                      Show this message and exit.                                                                                                                           │
      ╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

### Restore Task

This task restores Catalyst SD-WAN lab from a backup. This task will:

1. Import the CML topology from the backup and boot the Catalyst SD-WAN control components first.
2. Once control components are up, configure the control plane (control components, certificates, etc.).
3. Restore SD-WAN Manager templates, policies and configuration groups using [Sastre](https://github.com/CiscoDevNet/sastre).
4. Verify if there are any WAN Edges in the topology (SD-WAN and SD-Routing). If yes, then generate the new OTP and automatically reonboard them to SD-WAN Manager.

This task has several task-specific parameters, including working directory from where backup is restored. You can also overwrite the software version for control components and SD-WAN/SD-Routing Edges (note that specifying version lower than the one in the backup is not supported).

      sdwan-lab restore -h

       Usage: sdwan-lab restore [OPTIONS]

      ╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────╮
      │ --manager             <manager-ip>        SD-WAN Manager IP address, can also be defined via      │
      │                                           MANAGER_IP environment                                  │
      │ --muser               <manager-user>      SD-WAN Manager username, can also be defined via        │
      │                                           MANAGER_USER environment variable.                      │
      │ --mpassword           <manager-password>  SD-WAN Manager password, can also be defined via        │
      │                                           MANAGER_PASSWORD environment variable.                  │
      │ --mmask               <manager-mask>      Subnet mask for given SD-WAN Manager IP (e.g. /24), can │
      │                                           also be defined via MANAGER_MASK environment variable.  │
      │ --mgateway            <manager-gateway>   Gateway IP for given SD-WAN Manager IP, can also be     │
      │                                           defined via MANAGER_GATEWAY environment variable.       │
      │ --lab                 <lab_name>          CML Lab name, can also be defined via LAB_NAME          │
      │                                           environment variable.                                   │
      │ --workdir             <directory>         Restore source folder                                   │
      │ --deleteexisting                          If there is already lab running with same name and      │
      │                                           using same SD-WAN Manager IP, delete this lab before    │
      │                                           restoring. Note the all running lab data will be lost!  │
      │ --retry                                   If for some reason your script lost connectivity during │
      │                                           SD-WAN Manager boot, you can add --retry to continue    │
      │                                           onboarding the lab that is already in CML               │
      │ --contr_version       <contr_version>     Change the controller version when restoring the lab.   │
      │ --edge_version        <edge_version>      Change the SD-WAN edge version when restoring the lab.  │
      │ --help            -h                      Show this message and exit.                             │
      ╰───────────────────────────────────────────────────────────────────────────────────────────────────╯

Below you will find few examples of restore task:

    sdwan-lab restore --workdir backup
    sdwan-lab restore --workdir backup --deleteexisting
    sdwan-lab restore --workdir backup --contr_version 20.16.1
    sdwan-lab restore --workdir backup --contr_version 20.16.1 --edge_version 17.16.01a

### Delete Task

This task deletes the CML lab and removes all it's data. Note after this operation, all lab data is lost.

This task has several task-specific parameters.

      (venv) tzarski:~$sdwan-lab delete -h

       Usage: sdwan-lab delete [OPTIONS]

      ╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
      │ --lab        <lab_name>  CML Lab name, can also be defined via LAB_NAME environment variable.                                                                                              │
      │ --force                  Delete the lab without asking for confirmation. Note the all lab data will be lost!                                                                               │
      │ --help   -h              Show this message and exit.                                                                                                                                       │
      ╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

### Sign Task

This tasks reads the Certificate Signing Request (CSR) from a file and signs it using SD-WAN Lab Deployment Tool Root CA.
At the end, the task prints the signed certificate in standard output.

This task has several task-specific parameters.

      sdwan-lab sign -h

       Usage: sdwan-lab sign [OPTIONS] <csr_file>


       positional arguments:
         <csr_file>  Certificate Signing Request (CSR) File

      ╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
      │ --help  -h    Show this message and exit.                                                                                                                                                  │
      ╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

## Limitations and scale

The tool supports the following scale per CML lab:

- 1 SD-wan Manager instance (Cluster is not yet supported)
- 8 SD-WAN Validators (Documented support from CCO)
- 12 SD-WAN Controllers (Documented support from CCO)
- 20 SD-WAN Edges
- 10 SD-Routing Edges

The tool requires a minimum of 9 nodes to deploy the topology; therefore, it is not supported on CML-Free.

## Appendix - WSL Installation

To install WSL on your Windows VM or Physical machine. Ensure that the HW Virutalization is enabled in the BIOS or VM Defintion.

If its on Windows server you may need to run this command to allow the WSL to function properly

Open PowerShell as Administrator and run:

`Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux`

Install WSL with default distribution (Ubuntu)
Open PowerShell and run

`wsl --install`

Once the installation is finished and you have restarted Windows you are able to continue the installation of this tool as described in the [installation section](README.md#installing) of this document.

You can read more about [Linux on Windows with WSL here](https://learn.microsoft.com/en-us/windows/wsl/install).

## FAQ

Q1: My devices' consoles have stopped working after I created my own configuration groups. How do I recover console access?

A1: Always include the `platform console serial` command in an CLI add-on feature parcel to ensure your consoles work from the CML UI. Note that after adding this command, a WAN Edge reboot is required.

Q2: Can I SSH to my Manager instance directly?

A2: Yes, you can if you are using an external IP. However, if you are using PATty, you cannot, as we only map the HTTPS port.

## Authors

Tomasz Zarski (tzarski@cisco.com)

## License

BSD-3-Clause

## Acknowledgments

- Marcelo Reis and [Sastre](https://github.com/CiscoDevNet/sastre)
- Inigo Alonso
