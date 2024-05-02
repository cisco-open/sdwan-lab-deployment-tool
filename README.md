[![Tests](https://github.com/cisco-open/sdwan-lab-deployment-tool/actions/workflows/test.yml/badge.svg)](https://github.com/netascode/iac-validate/actions/workflows/test.yml)
![Python Support](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12-informational "Python Support: 3.9, 3.10, 3.11, 3.12")

# Catalyst SD-WAN Lab Deployment Tool for Cisco Modeling Labs

This tool automates [Cisco Catalyst SD-WAN](https://www.cisco.com/site/us/en/solutions/networking/sdwan/index.html) lab deployment inside [Cisco Modeling Labs (CML)](https://www.cisco.com/c/en/us/products/cloud-systems-management/modeling-labs/index.html).

The tool will help you automate your CML lab deployments with SD-WAN Manager, Controllers and Validators and up to 20 edges within one lab pod.

## Getting Started

### Prerequisites
Catalyst SD-WAN Lab Deployment Tool requires Linux or macOS system. 
To run is on Windows, please use [Linux on Windows with WSL](/README.md#appendix---wsl-installation) or set up Linux VM/container.

Catalyst SD-WAN Lab Deployment Tool requires Python 3.9 or newer. This can be verified by pasting the following to a terminal window:

    % python3 -c "import sys;assert sys.version_info>(3,9)" && echo "ALL GOOD"

If 'ALL GOOD' is printed it means Python requirements are met. If not, download and install the latest 3.x version at Python.org (https://www.python.org/downloads/).

All the Python prerequisites are automaticaly installed when you install the package. Please refer to the [Installing](#installing) section for details.

This tool requires CML 2.6 or higher.

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
* [Setup](#setup-task): Setup CML to support Catalyst SD-WAN use cases, upload new software images, and create node and image definitions that are required to run Catalyst SD-WAN lab in the CML.
* [Deploy](#deploy-task): Deploy CML topology with two underlay networks (INET, MPLS), one Manager/Validator/Controller, and one Gateway router. Once topology boots up, configure the control components and create basic templates / configuration groups.
* [Add](#add-task): Add and automatically onboard additional SD-WAN nodes (Validators/Controllers/Edges) to existing lab.
* [Backup](#backup-task): Backup the Catalyst SD-WAN Lab runnning in CML, including the CML topology and all its nodes, SD-WAN device states and templates / configuration groups.
* [Restore](#restore-task): Restore the Catalyst SD-WAN Lab from backup, onboard and confgure control components and create basic feature templates / configuration groups. If there are any WAN Edges, automatically onboard the WAN Edges back to the SD-WAN Manager using the configuration from the backup.
* [Delete](#delete-task): Delete currently running lab from CML and remove all lab data.

Task-specific parameters are provided after the task argument.

### Base Parameters

      sdwan-lab -h
      usage: sdwan-lab.py [-h] [-c <cml-ip>] [-u <cml-user>] [-p <cml-password>] [-v] [--version] {setup,deploy,add,backup,restore,delete} ...
      
      Catalyst SD-WAN Lab Deployment Tool - Automation Tool for managing Cisco Catalyst SD-WAN labs inside Cisco Modelling Labs
      
      positional arguments:
        {setup,deploy,add,backup,restore,delete}
                              Task to be performed.
          setup               Setup on-prem CML to use Catalyst SD-WAN Lab automation.
          deploy              Deploy a new Catalyst SD-WAN lab pod.
          add                 Add Catalyst SD-WAN device to running lab pod.
          backup              Backup running Catalyst SD-WAN lab pod.
          restore             Restore Catalyst SD-WAN POD from backup.
          delete              Delete the CML lab and all the lab data.
      
      optional arguments:
        -h, --help            show this help message and exit
        -c <cml-ip>, --cml <cml-ip>
                              CML IP address, can also be defined via CML_IP environment variable. If neither is provided user is prompted for CML IP.
        -u <cml-user>, --user <cml-user>
                              CML username, can also be defined via CML_USER environment variable. If neither is provided user is prompted for CML username.
        -p <cml-password>, --password <cml-password>
                              CML password, can also be defined via CML_PASSWORD environment variable. If neither is provided user is prompted for CML password.
        -v, --verbose         Increase output verbosity.
        --version             show program's version number and exit

Most of the parameters can also be provided via environment variables:
* CML_IP
* CML_USER
* CML_PASSWORD
* MANAGER_IP
* MANAGER_USER
* MANAGER_PASSWORD
* MANAGER_MASK
* MANAGER_GATEWAY
* LAB_NAME

For any of the required arguments, user is prompted for a value if they are not provided via the environment variables or command line arguments.

The easiest way to run the tool is to provide all the lab variables in the rc file and source that file. The example file below contains all the variables required to run all the tasks.

    % cat rc-example.sh
    export CML_IP='10.0.0.1'
    export CML_USER='user1'
    export CML_PASSWORD='password123'
    export MANAGER_IP='10.0.0.10'
    export MANAGER_USER='sdwan'
    export MANAGER_PASSWORD='sdwanlab123'
    export MANAGER_MASK='/24'
    export MANAGER_GATEWAY='10.0.0.254'
    export LAB_NAME='sdwan'
    % source rc-example.sh

Note that if password was not defined, the user will be prompted for a password.

### Task-specific Parameters
Task-specific parameters and options are defined after the task is provided. Each task has its own set of parameters. Check the task documentation to learn more about task-specific parameters.

### Setup Task
This task makes sure your CML is ready to run Catalyst SD-WAN labs. The task will:
* Create node definitions that are required to run Catalyst SD-WAN lab in the CML: Manager, Validator, Controller and Edge
* Look for new SD-WAN software images in the folder where the script was run. If the image is found, upload the image to CML and create image definition for the right node definition: Manager, Validator, Controller and Edge

On each CML server that you want to use, you should run a setup task at least once to create required node and image definitions. You can rerun the setup task each time you want to add a new Catalyst SD-WAN software image to your CML server.

This task have one task-specific argument that allows you to migrate the node and image definitions to new format if you've used SD-WAN Lab 1.x in the past. 

      sdwan-lab setup -h
      usage: sdwan-lab.py setup [-h] [--migrate]
      
      optional arguments:
        -h, --help  show this help message and exit
        --migrate   Migrate node and image definitions from SD-WAN Lab v1.x to v2.x. This task should be run once if CML server was using SD-WAN LAb Tool v1.x in the past.

### Deploy Task
This task:
1. Defines four/five subnets:
   * VPN0 - 172.16.0.0/24
   * INET - 172.16.1.0/24
   * MPLS - 172.16.2.0/24
   * External Connector - in bridge mode, this subnet is defined by task-specific parameters and is used to provide external reachability to SD-WAN Manager.
   * Internet Connector - in NAT mode, this subnet provides Internet connectivity for Internet transport and is same as CML subnet
2. Deploys basic SD-WAN topology with:
   * two underlay networks (INET, MPLS)
   * one Manager
   * one Validator
   * one Controller
   * one Gateway router that connects VPN0 subnet to INET and MPLS subnets
3. Once topology boots up, the task configures the control plane (control components, certificates, etc.) and create basic feature templates / configuration groups that can be used for WAN Edge onboarding. It also attaches Controller to device template.
4. At this point you can start creating your custom topology using [Add Task](#add-task) to automatically onboard additional SD-WAN nodes (Validators/Controllers/Edges).

You should run this task to deploy a new lab with control plane configured and build any WAN Edge topology you like.

This task has several task-specific parameters, including software version that you want to run on the control components.

      sdwan-lab deploy -h
      usage: sdwan-lab.py deploy [-h] [--manager <manager-ip>] [--mmask <manager-mask>] [--mgateway <manager-gateway>] [--muser <manager-user>] [--mpassword <manager-password>] [--bridge <custom-bridge-name>] [--dns <dns-server-ip>]
                                 <software-version>
      
      positional arguments:
        <software-version>    Software version that will be used on SD-WAN Control Components.
      
      optional arguments:
        -h, --help            show this help message and exit
        --manager <manager-ip>
                              SD-WAN Manager IP address, can also be defined via MANAGER_IP environment variable. If neither is provided user is prompted for SD-WAN Manager IP.
        --mmask <manager-mask>
                              Subnet mask for given SD-WAN Manager IP (e.g. /24), can also be defined via MANAGER_MASK environment variable. If neither is provided user is prompted for SD-WAN Manager subnet mask.
        --mgateway <manager-gateway>
                              Gateway IP for given SD-WAN Manager IP, can also be defined via MANAGER_GATEWAY environment variable. If neither is provided user is prompted for Manager gateway IP.
        --muser <manager-user>
                              SD-WAN Manager username, can also be defined via MANAGER_USER environment variable. If neither is provided user is prompted for SD-WAN Manager username.
        --mpassword <manager-password>
                              SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable. If neither is provided user is prompted for SD-WAN Manager password.
        --lab <lab_name>      Set CML Lab name, can also be defined via LAB_NAME environment variable. If not provided, default name "sdwan<number>" will be assigned.
        --bridge <custom-bridge-name>
                              Set custom bridge for SD-WAN Manager external connection. Default is System Bridge
        --dns <dns-server-ip>
                              Set custom DNS server for Internet/VPN0 transport. Default is same as CML DNS
        --retry               If for some reason your script lost connectivity during SD-WAN Manager boot, you can add --retry to continue onboarding the lab that is already in CML.

Time to complete the deployment task depends on:
* SD-WAN software version
* CML resources

### Add Task
This task adds Catalyst SD-WAN nodes (Validators/Controllers/Edges) into existing Catalyst SD-WAN lab. This task will:
1. Add requested number of nodes to the CML topology and boot them with cloud-init configuration
2. Once nodes boot up, automatically onboard them to SD-WAN Manager
3. For Controller/Edge nodes, automatically attach basic device template / configuration group
4. For Validator nodes, automatically update Validator FQDN mapping with new IP addresses

This task has several task-specific parameters.

      sdwan-lab add -h
      usage: sdwan-lab.py add [-h] [--manager <manager-ip>] [--muser <manager-user>] [--mpassword <manager-password>] [--lab <lab_name>] <number-of-devices> <device-type> <software-version>
      
      positional arguments:
        <number-of-devices>   Number of devices to be added.
        <device-type>         Type of device/s to be added (e.g. validator, controller, edge, sdrouting).
        <software-version>    Software version that will be used.
      
      optional arguments:
        -h, --help            show this help message and exit
        --manager <manager-ip>
                              SD-WAN Manager IP address, can also be defined via MANAGER_IP environment variable. If neither is provided user is prompted for SD-WAN Manager IP.
        --muser <manager-user>
                              SD-WAN Manager username, can also be defined via MANAGER_USER environment variable. If neither is provided user is prompted for SD-WAN Manager username.
        --mpassword <manager-password>
                              SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable. If neither is provided user is prompted for SD-WAN Manager password.
        --lab <lab_name>      CML Lab name, can also be defined via LAB_NAME environment variable. If neither is provided user is prompted for lab name.

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

    % sdwan-lab back -h  
      usage: sdwan-lab.py backup [-h] [--manager <manager-ip>] [--muser <manager-user>] [--mpassword <manager-password>] [--lab <lab_name>] [--workdir <directory>]
      
      optional arguments:
        -h, --help            show this help message and exit
        --manager <manager-ip>
                              SD-WAN Manager IP address, can also be defined via MANAGER_IP environment variable. If neither is provided user is prompted for SD-WAN Manager IP.
        --muser <manager-user>
                              SD-WAN Manager username, can also be defined via MANAGER_USER environment variable. If neither is provided user is prompted for SD-WAN Manager username.
        --mpassword <manager-password>
                              SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable. If neither is provided user is prompted for SD-WAN Manager password.
        --lab <lab_name>      CML Lab name, can also be defined via LAB_NAME environment variable. If neither is provided user is prompted for lab name.
        --workdir <directory>
                              Backup destination folder
    
### Restore Task
This task restores Catalyst SD-WAN lab from a backup. This task will:
1. Import the CML topology from the backup and boot the Catalyst SD-WAN control components first.
2. Once control components are up, configure the control plane (control components, certificates, etc.).
3. Restore SD-WAN Manager templates, policies and configuration groups using [Sastre](https://github.com/CiscoDevNet/sastre).
4. Verify if there are any WAN Edges in the topology (SD-WAN and SD-Routing). If yes, then generate the new OTP and automatically reonboard them to SD-WAN Manager.

This task has several task-specific parameters, including working directory from where backup is restored.

    % sdwan-lab restore -h
      usage: sdwan-lab.py restore [-h] [--manager <manager-ip>] [--mmask <manager-mask>] [--mgateway <manager-gateway>] [--muser <manager-user>] [--mpassword <manager-password>] [--lab <lab_name>]
                                  [--workdir <directory>] [--deleteexisting] [--retry]
      
      optional arguments:
        -h, --help            show this help message and exit
        --manager <manager-ip>
                              SD-WAN Manager IP address, can also be defined via MANAGER_IP environment variable. If neither is provided user is prompted for SD-WAN Manager IP.
        --mmask <manager-mask>
                              Subnet mask for given SD-WAN Manager IP (e.g. /24), can also be defined via MANAGER_MASK environment variable. If neither is provided user is prompted for SD-WAN Manager subnet mask.
        --mgateway <manager-gateway>
                              Gateway IP for given SD-WAN Manager IP, can also be defined via MANAGER_GATEWAY environment variable. If neither is provided user is prompted for Manager gateway IP.
        --muser <manager-user>
                              SD-WAN Manager username, can also be defined via MANAGER_USER environment variable. If neither is provided user is prompted for SD-WAN Manager username.
        --mpassword <manager-password>
                              SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable. If neither is provided user is prompted for SD-WAN Manager password.
        --lab <lab_name>      CML Lab name, can also be defined via LAB_NAME environment variable. If neither is provided user is prompted for lab name.
        --workdir <directory>
                              Restore source folder
        --deleteexisting      If there is already lab running with same name and using same SD-WAN Manager IP, delete this lab before restoring. Note the all running lab data will be lost!
        --retry               If for some reason your script lost connectivity during SD-WAN Manager boot, you can add --retry to continue restoring the lab that is already in CML

### Delete Task
This task deletes the CML lab and removes all it's data. Note after this operation, all lab data is lost.

This task has several task-specific parameters.

      sdwan-lab delete -h
      usage: sdwan-lab.py delete [-h] [--lab <lab_name>] [--force]
      
      optional arguments:
        -h, --help        show this help message and exit
        --lab <lab_name>  Lab name
        --force           Delete the lab without asking for confirmation. Note the all lab data will be lost!

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


## Authors
Tomasz Zarski (tzarski@cisco.com)

## License

BSD-3-Clause

## Acknowledgments
- Marcelo Reis and [Sastre](https://github.com/CiscoDevNet/sastre)
- Inigo Alonso
