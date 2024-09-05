# Copyright 2024 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

#If you want to use external ip address for your Manager use this section.
export CML_IP='10.0.0.1'
export CML_USER='user1'
export CML_PASSWORD='2ajG$I2?8k'
export MANAGER_IP='10.0.0.10'
export MANAGER_USER='sdwan'
export MANAGER_PASSWORD='2ajG$I2?8k'
export MANAGER_MASK='/24'
export MANAGER_GATEWAY='10.0.0.254'
export LAB_NAME='sdwan'

#If you want to use PAT then use the following example.

export CML_IP='10.0.0.1'
export CML_USER='user1'
export CML_PASSWORD='2ajG$I2?8k'
export MANAGER_IP='pat:2001'
export MANAGER_USER='sdwan'
export MANAGER_PASSWORD='2ajG$I2?8k'
export LAB_NAME='sdwan-PAT'
