# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

"""
Catalyst SD-WAN Lab Deployment Tool - Automation Tool for managing
Cisco Catalyst SD-WAN labs inside Cisco Modelling Labs
"""
import re
import sys

from catalyst_sdwan_lab.__main__ import main

if __name__ == "__main__":
    sys.argv[0] = re.sub(r"(-script\.pyw?|\.exe)?$", "", sys.argv[0])
    sys.exit(main())
