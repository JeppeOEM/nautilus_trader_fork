# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
dYdX perpetuals data recorder.

A thin venue layer over the shared ``scripts.common_recorder`` infrastructure:
this package provides only the dYdX-specific config parsing (flat perpetual list,
bar-interval whitelist, ``DydxNetwork`` mapping) and the node-wiring file. The
recording strategy, catalog conversion, hot-reload, and heartbeat/reliability
logic are reused verbatim from ``common_recorder``.
"""
