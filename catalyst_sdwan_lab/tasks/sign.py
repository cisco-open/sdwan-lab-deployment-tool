# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd
from typing import Union

from .utils import create_cert, load_certificate_details, setup_logging, track_progress


def main(csr_file_path: str, loglevel: Union[int, str]) -> None:

    # Setup logging
    log = setup_logging(loglevel)

    # Prepare the CA for controllers certificate signing
    track_progress(log, "Loading root CA details...")
    ca_cert, ca_key, ca_chain = load_certificate_details()

    track_progress(log, "Loading csr from file...")
    with open(csr_file_path, "r") as file:
        csr = file.read()

    track_progress(log, "Signing CSR...")
    cert = create_cert(ca_cert.encode(), ca_key.encode(), csr.encode())

    track_progress(log, "Certificate signed: \n")
    print(cert.decode())

    return
