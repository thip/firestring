# FireString boot — USB composite device: serial (built-in) + mass storage
# The mass storage drive contains configuration.html so users can just open it.
# The FAT12 image is pre-built on the host (tools/mkfatfs.py) and read from flash,
# so no RAM is used for the filesystem data.
#
# Safety: if boot.py crashes, the board still has serial-only USB.
# To disable MSC for debugging, create a file called "nomsc" on the device.

import os


class FileBlockDev:
    """Read-only block device backed by a file on flash."""

    def __init__(self, path):
        self._f = open(path, "rb")
        self._size = os.stat(path)[6]

    def readblocks(self, n, buf):
        self._f.seek(n * 512)
        self._f.readinto(buf)

    def ioctl(self, op, arg):
        if op == 4:
            return self._size // 512
        if op == 5:
            return 512


# Skip MSC setup if "nomsc" flag file exists
try:
    os.stat("nomsc")
    print("boot: nomsc flag found, skipping MSC")
except OSError:
    try:
        bdev = FileBlockDev("firestring.img")

        import usb.device
        from msc import MSCInterface

        msc_if = MSCInterface(bdev)
        usb.device.get().init(
            msc_if,
            builtin_driver=True,
            manufacturer_str="Fire String",
            product_str="Fire String",
        )
    except Exception as e:
        print("boot: MSC setup failed:", e)

import gc
gc.collect()
