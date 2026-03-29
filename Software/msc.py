# USB Mass Storage Class — read-only Bulk-Only Transport
# Exposes a block device as a USB drive alongside the built-in CDC serial.
#
# IMPORTANT: All buffers passed to submit_xfer MUST be instance attributes
# to prevent garbage collection while USB DMA is still using them.

from micropython import const
import struct
from usb.device.core import Interface

_EP_IN_FLAG = const(1 << 7)

# BOT signatures
_CBW_SIG = const(0x43425355)
_CSW_SIG = const(0x53425355)
_CBW_LEN = const(31)
_CSW_LEN = const(13)

# MSC class requests
_REQ_RESET = const(0xFF)
_REQ_GET_MAX_LUN = const(0xFE)

# Control transfer stage
_STAGE_SETUP = const(1)

# SCSI opcodes
_TEST_UNIT_READY = const(0x00)
_REQUEST_SENSE = const(0x03)
_INQUIRY = const(0x12)
_MODE_SENSE_6 = const(0x1A)
_START_STOP = const(0x1B)
_PREVENT_ALLOW = const(0x1E)
_READ_FMT_CAP = const(0x23)
_READ_CAP_10 = const(0x25)
_READ_10 = const(0x28)
_MODE_SENSE_10 = const(0x5A)


class MSCInterface(Interface):

    def __init__(self, bdev, block_count=None, block_size=512):
        super().__init__()
        self._bdev = bdev
        self._blksz = block_size
        self._nblk = block_count if block_count is not None else bdev.ioctl(4, 0)

        # Pre-allocate ALL buffers used in USB transfers (prevent GC during DMA)
        self._cbw = bytearray(_CBW_LEN)
        self._csw = bytearray(_CSW_LEN)
        self._buf = bytearray(block_size)  # single sector read buffer

        # Active transfer reference — keeps memoryview alive during DMA
        self._active_xfer = None

        self._ep_in = None
        self._ep_out = None
        self._tag = 0
        self._residue = 0
        self._status = 0
        self._rd_lba = 0
        self._rd_left = 0

        # Pre-build static SCSI responses into _resp-sized buffers
        self._inquiry = bytearray(36)
        self._inquiry[0] = 0x00   # direct access block device
        self._inquiry[1] = 0x80   # removable
        self._inquiry[2] = 0x02   # SPC-2
        self._inquiry[3] = 0x02   # response format
        self._inquiry[4] = 31     # additional length
        self._inquiry[8:16] = b"FireStr "
        self._inquiry[16:32] = b"FireString      "
        self._inquiry[32:36] = b"1.0 "

        self._sense = bytearray(18)
        self._sense[0] = 0x70  # current errors
        self._sense[7] = 10   # additional length

        self._mode6 = bytearray(4)
        self._mode6[0] = 3
        self._mode6[2] = 0x80  # write-protected

        self._mode10 = bytearray(8)
        self._mode10[1] = 6
        self._mode10[3] = 0x80  # write-protected

        self._cap10 = bytearray(8)
        self._fmtcap = bytearray(12)

    # ---- USB descriptor / lifecycle ----

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        desc.interface(itf_num, 2, 0x08, 0x06, 0x50)
        self._ep_out = ep_num
        self._ep_in = ep_num | _EP_IN_FLAG
        desc.endpoint(self._ep_out, "bulk", 64)
        desc.endpoint(self._ep_in, "bulk", 64)

    def num_eps(self):
        return 1

    def on_open(self):
        super().on_open()  # sets _open = True
        self._rx_cbw()

    def on_reset(self):
        super().on_reset()  # sets _open = False
        self._active_xfer = None

    def on_interface_control_xfer(self, stage, request):
        brt, breq = struct.unpack_from("BB", request)
        if (brt >> 5) & 3 != 1:  # not a CLASS request
            return False
        if stage == _STAGE_SETUP:
            if breq == _REQ_RESET:
                self._rx_cbw()
                return True
            if breq == _REQ_GET_MAX_LUN:
                return b"\x00"
        return True

    # ---- Bulk-Only Transport state machine ----

    def _rx_cbw(self):
        self._active_xfer = self._cbw
        self.submit_xfer(self._ep_out, self._cbw, self._on_cbw)

    def _on_cbw(self, ep, res, n):
        if n != _CBW_LEN:
            self._rx_cbw()
            return
        sig, tag, dlen, flags, lun, cblen = struct.unpack_from("<IIIBBB", self._cbw)
        if sig != _CBW_SIG:
            self._rx_cbw()
            return

        self._tag = tag
        self._residue = dlen
        self._status = 0
        self._rd_left = 0

        opcode = self._cbw[15]
        cb = memoryview(self._cbw)[15 : 15 + cblen]
        data_in = (flags & 0x80) != 0

        resp = self._scsi(opcode, cb, dlen)

        if resp is not None and data_in and len(resp) > 0:
            self._residue = dlen - len(resp)
            self._active_xfer = resp  # prevent GC
            self.submit_xfer(self._ep_in, resp, self._on_data_in)
        elif self._rd_left > 0:
            self._next_read_chunk()
        else:
            self._tx_csw()

    def _next_read_chunk(self):
        n = min(self._rd_left, len(self._buf) // self._blksz)
        if n == 0:
            self._tx_csw()
            return
        sz = n * self._blksz
        mv = memoryview(self._buf)[:sz]
        self._bdev.readblocks(self._rd_lba, mv)
        self._rd_lba += n
        self._rd_left -= n
        self._residue -= sz
        self._active_xfer = mv  # prevent GC of memoryview
        self.submit_xfer(self._ep_in, mv, self._on_read_chunk)

    def _on_read_chunk(self, ep, res, n):
        if self._rd_left > 0:
            self._next_read_chunk()
        else:
            self._tx_csw()

    def _on_data_in(self, ep, res, n):
        self._tx_csw()

    def _tx_csw(self):
        struct.pack_into(
            "<IIIB", self._csw, 0, _CSW_SIG, self._tag, max(0, self._residue), self._status
        )
        self._active_xfer = self._csw
        self.submit_xfer(self._ep_in, self._csw, self._on_csw)

    def _on_csw(self, ep, res, n):
        self._rx_cbw()

    # ---- SCSI command handlers ----

    def _scsi(self, op, cb, dlen):
        if op == _TEST_UNIT_READY:
            return None

        if op == _INQUIRY:
            return self._inquiry

        if op == _READ_CAP_10:
            struct.pack_into(">II", self._cap10, 0, self._nblk - 1, self._blksz)
            return self._cap10

        if op == _READ_10:
            lba = struct.unpack_from(">I", cb, 2)[0]
            cnt = struct.unpack_from(">H", cb, 7)[0]
            total = cnt * self._blksz
            if total <= len(self._buf):
                mv = memoryview(self._buf)[:total]
                self._bdev.readblocks(lba, mv)
                self._residue = dlen - total
                return mv
            else:
                self._rd_lba = lba
                self._rd_left = cnt
                return None  # handled via chunked path

        if op == _REQUEST_SENSE:
            return self._sense

        if op == _MODE_SENSE_6:
            return self._mode6

        if op == _MODE_SENSE_10:
            return self._mode10

        if op == _READ_FMT_CAP:
            self._fmtcap[3] = 8  # capacity list length
            struct.pack_into(">I", self._fmtcap, 4, self._nblk)
            self._fmtcap[8] = 0x02  # formatted media
            self._fmtcap[9] = (self._blksz >> 16) & 0xFF
            self._fmtcap[10] = (self._blksz >> 8) & 0xFF
            self._fmtcap[11] = self._blksz & 0xFF
            return self._fmtcap

        if op in (_PREVENT_ALLOW, _START_STOP):
            return None

        # unknown command — fail
        self._status = 1
        return None
