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
Frame-level reading of a zstd file written by `pyarrow.CompressedOutputStream`, for the one case
the stream decoder cannot handle: a file whose last frame was never finished (the writer crashed,
or it is the recorder's still-open current hour).

`pyarrow.CompressedInputStream` raises "Truncated compressed stream" on such a tail and drops the
bytes it had decoded inside that `read` call, so it cannot say which lines were complete. This
module walks the zstd frame and block headers (RFC 8878 section 3.1) instead, returning where the
whole frames end, and rebuilds the truncated tail frame's whole blocks as a complete frame (its
last whole block marked last, the checksum flag cleared) that the stream decoder then reads
exactly: a flush ends a block, so every flushed line is recovered.

Invariant: `scan` never guesses -- anything that is not a zstd or skippable frame raises
`CorruptZstd`, so a damaged file is reported, never silently read short. The one tail it names
instead is a run of zero bytes up to the end of the file after the last whole frame: what a
filesystem leaves after a power loss when the file's size reached disk before its data. It is
reported like any truncated tail (nothing in it was ever a line), so the repair keeps every whole
frame instead of setting the hour's file aside as corrupt.
"""

from typing import NamedTuple


_ZSTD_MAGIC = 0xFD2FB528
_SKIPPABLE_MASK = 0xFFFFFFF0
_SKIPPABLE_MAGIC = 0x184D2A50
_BLOCK_HEADER_BYTES = 3
_BLOCK_RLE = 1
_BLOCK_RESERVED = 3
_CHECKSUM_FLAG = 0b100
_RESERVED_FLAG = 0b1000
_CHECKSUM_BYTES = 4
_DICT_ID_BYTES = (0, 1, 2, 4)


class CorruptZstd(ValueError):
    """Bytes that are not a zstd stream, or a truncated tail this module cannot rebuild."""


class FrameScan(NamedTuple):
    """
    `complete_end`: bytes `[0, complete_end)` are whole frames. `tail_start`: where a truncated
    last frame begins (None when the file ends on a frame boundary). `tail_blocks_end` /
    `tail_last_block`: the end of that frame's last whole block and that block's header offset
    (None when not even one block is whole).
    """

    complete_end: int
    tail_start: int | None
    tail_blocks_end: int | None
    tail_last_block: int | None


def _le(data: memoryview, start: int, size: int) -> int:
    return int.from_bytes(data[start : start + size], "little")


def _header_size(data: memoryview, start: int) -> int | None:
    """Return the frame header's size (magic included), or None when it is not all present."""
    if len(data) - start < 5:
        return None
    descriptor = data[start + 4]
    if descriptor & _RESERVED_FLAG:
        raise CorruptZstd(f"zstd frame at byte {start} sets the reserved descriptor bit")
    single_segment = (descriptor >> 5) & 1
    content_size_bytes = (single_segment, 2, 4, 8)[descriptor >> 6]
    size = 5 + (1 - single_segment) + _DICT_ID_BYTES[descriptor & 0b11] + content_size_bytes
    return size if start + size <= len(data) else None


def _walk_blocks(
    data: memoryview, start: int, header: int
) -> tuple[int | None, int | None, int | None]:
    """Return (frame end or None when truncated, last whole block's header, its end)."""
    checksum = _CHECKSUM_BYTES if data[start + 4] & _CHECKSUM_FLAG else 0
    position, last_block, blocks_end = start + header, None, None
    while position + _BLOCK_HEADER_BYTES <= len(data):
        block = _le(data, position, _BLOCK_HEADER_BYTES)
        kind = (block >> 1) & 0b11
        if kind == _BLOCK_RESERVED:
            raise CorruptZstd(f"reserved zstd block type at byte {position}")
        end = position + _BLOCK_HEADER_BYTES + (1 if kind == _BLOCK_RLE else block >> 3)
        if end > len(data):
            break
        last_block, blocks_end = position, end
        if block & 1:
            frame_end = end + checksum
            return (frame_end if frame_end <= len(data) else None), last_block, blocks_end
        position = end
    return None, last_block, blocks_end


def _skippable_end(data: memoryview, start: int) -> int | None:
    if len(data) - start < 8:
        return None
    end = start + 8 + _le(data, start + 4, 4)
    return end if end <= len(data) else None


def _frame_end(data: memoryview, start: int) -> tuple[int | None, FrameScan]:
    """Return the next frame's end, or None plus the scan describing this truncated tail."""
    truncated = FrameScan(start, start, None, None)
    if len(data) - start < 4:
        return None, truncated
    magic = _le(data, start, 4)
    if magic == 0 and not any(data[start:]):  # a power loss's zero-filled tail
        return None, truncated
    if magic & _SKIPPABLE_MASK == _SKIPPABLE_MAGIC:
        return _skippable_end(data, start), truncated
    if magic != _ZSTD_MAGIC:
        raise CorruptZstd(f"no zstd frame at byte {start} (magic {magic:#010x})")
    header = _header_size(data, start)
    if header is None:
        return None, truncated
    end, last_block, blocks_end = _walk_blocks(data, start, header)
    return end, FrameScan(start, start, blocks_end, last_block)


def scan(data: memoryview) -> FrameScan:
    """Walk every frame of a zstd file's bytes: where the whole frames end, and the tail."""
    position = 0
    while position < len(data):
        end, truncated = _frame_end(data, position)
        if end is None:
            return truncated
        position = end
    return FrameScan(position, None, None, None)


def rebuild_tail(data: memoryview, found: FrameScan) -> bytes:
    """
    Return the truncated tail frame's whole blocks as one complete frame (empty when it has
    none). Raises `CorruptZstd` for a tail frame that declares its content size, which a shorter
    rebuilt frame would contradict -- `pyarrow`'s streaming compressor never writes one.
    """
    if found.tail_start is None or found.tail_blocks_end is None or found.tail_last_block is None:
        return b""
    descriptor = data[found.tail_start + 4]
    if descriptor >> 6 or (descriptor >> 5) & 1:
        raise CorruptZstd(f"truncated zstd frame at byte {found.tail_start} declares its size")
    frame = bytearray(data[found.tail_start : found.tail_blocks_end])
    frame[4] &= ~_CHECKSUM_FLAG & 0xFF
    frame[found.tail_last_block - found.tail_start] |= 1
    return bytes(frame)
