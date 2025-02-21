# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

"""
Catalyst SD-WAN Lab - Automation Tool for managing Cisco Catalyst SD-WAN labs inside Cisco Modelling Labs

catalyst_sdwan_lab.__main__
This module implements the command line top-level parser and task dispatcher
"""


from catalyst_sdwan_lab.cli import cli


def main() -> int:
    cli()
    return 0


if __name__ == "__main__":
    main()
