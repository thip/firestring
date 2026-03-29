#!/usr/bin/env python3
"""Build a minimal FAT12 disk image containing specified files."""

import struct
import sys
import os

SECTOR = 512
LABEL = b"Fire String"  # exactly 11 bytes


def build_image(files, out_path):
    # Calculate size
    data_size = sum(os.path.getsize(f) for f in files)
    data_sectors = (data_size + SECTOR - 1) // SECTOR + len(files)  # slack per file
    # Layout: 1 boot + 2 FAT + 2 root_dir + data
    total_sectors = 5 + data_sectors + 4  # +4 breathing room
    fat_entries = total_sectors - 5 + 2  # clusters start at 2
    fat_sectors = max(1, (fat_entries * 3 // 2 + SECTOR - 1) // SECTOR)

    # Recalculate with actual FAT size
    root_dir_sectors = 2  # 32 entries
    reserved = 1
    num_fats = 2
    data_start = reserved + num_fats * fat_sectors + root_dir_sectors
    total_sectors = data_start + data_sectors + 4
    total_sectors = max(total_sectors, 32)  # minimum

    img = bytearray(total_sectors * SECTOR)

    # --- Boot sector ---
    # Jump + NOP
    img[0:3] = b"\xEB\x3C\x90"
    # OEM
    img[3:11] = b"MSDOS5.0"
    # BPB
    struct.pack_into("<H", img, 11, SECTOR)          # bytes per sector
    img[13] = 1                                        # sectors per cluster
    struct.pack_into("<H", img, 14, reserved)          # reserved sectors
    img[16] = num_fats                                 # number of FATs
    struct.pack_into("<H", img, 17, 32)                # root entry count
    struct.pack_into("<H", img, 19, total_sectors)     # total sectors 16
    img[21] = 0xF8                                     # media type (hard disk)
    struct.pack_into("<H", img, 22, fat_sectors)       # sectors per FAT
    struct.pack_into("<H", img, 24, 1)                 # sectors per track
    struct.pack_into("<H", img, 26, 1)                 # number of heads
    # Extended boot record
    img[36] = 0x80                                     # drive number
    img[38] = 0x29                                     # extended boot sig
    struct.pack_into("<I", img, 39, 0xF1AE5721)       # volume serial
    img[43:54] = LABEL                                 # volume label
    img[54:62] = b"FAT12   "                           # filesystem type
    # Boot signature
    img[510] = 0x55
    img[511] = 0xAA

    # --- FAT ---
    fat_off = reserved * SECTOR
    # First two entries: media byte + 0xFFF
    img[fat_off] = 0xF8
    img[fat_off + 1] = 0xFF
    img[fat_off + 2] = 0xFF

    # --- Root directory ---
    root_off = (reserved + num_fats * fat_sectors) * SECTOR

    # Volume label entry
    img[root_off:root_off + 11] = LABEL
    img[root_off + 11] = 0x08  # volume label attribute

    # --- Write files ---
    dir_idx = 1  # next dir entry (0 = volume label)
    cluster = 2  # first data cluster
    data_off = data_start * SECTOR

    for filepath in files:
        filename = os.path.basename(filepath)
        filedata = open(filepath, "rb").read()
        file_clusters = (len(filedata) + SECTOR - 1) // SECTOR

        # Write file data
        img[data_off:data_off + len(filedata)] = filedata

        # 8.3 directory entry
        name, ext = (filename.rsplit(".", 1) + [""])[:2]
        short_name = name[:8].upper().ljust(8) + ext[:3].upper().ljust(3)
        needs_lfn = len(name) > 8 or len(ext) > 3 or name != name.upper() or ext != ext.upper()

        entry_off = root_off + dir_idx * 32

        if needs_lfn:
            # Build LFN entries
            lfn_name = filename.encode("utf-16-le")
            # Pad to multiple of 26 bytes (13 UTF-16 chars per LFN entry)
            chars = [ord(c) for c in filename]
            while len(chars) % 13 != 0:
                if len(chars) == len(filename):
                    chars.append(0x0000)  # null terminator
                else:
                    chars.append(0xFFFF)  # padding

            num_lfn = len(chars) // 13
            # Generate checksum of short name
            cksum = 0
            for b in short_name.encode("ascii"):
                cksum = ((cksum >> 1) + ((cksum & 1) << 7) + b) & 0xFF

            # Short name for LFN: FILENA~1.EXT
            sn = name[:6].upper() + "~1"
            short_name = sn.ljust(8) + ext[:3].upper().ljust(3)

            # Write LFN entries (reverse order)
            for i in range(num_lfn, 0, -1):
                seq = i
                if i == num_lfn:
                    seq |= 0x40  # last LFN entry flag
                c = chars[(i - 1) * 13:(i - 1) * 13 + 13]
                lfn_entry = bytearray(32)
                lfn_entry[0] = seq
                # Chars 1-5 at offsets 1-10
                for j in range(5):
                    struct.pack_into("<H", lfn_entry, 1 + j * 2, c[j])
                lfn_entry[11] = 0x0F  # LFN attribute
                lfn_entry[13] = cksum
                # Chars 6-11 at offsets 14-25
                for j in range(6):
                    struct.pack_into("<H", lfn_entry, 14 + j * 2, c[5 + j])
                # Chars 12-13 at offsets 28-31
                for j in range(2):
                    struct.pack_into("<H", lfn_entry, 28 + j * 2, c[11 + j])

                img[entry_off:entry_off + 32] = lfn_entry
                entry_off += 32
                dir_idx += 1

        # Write 8.3 entry
        img[entry_off:entry_off + 11] = short_name.encode("ascii")
        img[entry_off + 11] = 0x20  # archive attribute
        struct.pack_into("<H", img, entry_off + 26, cluster)  # first cluster
        struct.pack_into("<I", img, entry_off + 28, len(filedata))  # file size
        dir_idx += 1

        # Write FAT chain
        for i in range(file_clusters):
            c = cluster + i
            next_c = cluster + i + 1 if i < file_clusters - 1 else 0xFFF
            # FAT12: each entry is 12 bits
            byte_off = fat_off + (c * 3) // 2
            if c % 2 == 0:
                img[byte_off] = next_c & 0xFF
                img[byte_off + 1] = (img[byte_off + 1] & 0xF0) | ((next_c >> 8) & 0x0F)
            else:
                img[byte_off] = (img[byte_off] & 0x0F) | ((next_c << 4) & 0xF0)
                img[byte_off + 1] = (next_c >> 4) & 0xFF

        cluster += file_clusters
        data_off += file_clusters * SECTOR

    # Copy FAT1 to FAT2
    fat1 = img[fat_off:fat_off + fat_sectors * SECTOR]
    fat2_off = fat_off + fat_sectors * SECTOR
    img[fat2_off:fat2_off + len(fat1)] = fat1

    with open(out_path, "wb") as f:
        f.write(img)
    print(f"Built {out_path}: {total_sectors} sectors ({total_sectors * SECTOR} bytes), {len(files)} file(s)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <output.img> <file> [file...]")
        sys.exit(1)
    build_image(sys.argv[2:], sys.argv[1])
