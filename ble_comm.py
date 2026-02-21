# ==============================================================
# Stargate SG-1 – BLE communication layer (Pico W only)
# ==============================================================
# Either gate can initiate a wormhole.  The BLE roles are fixed:
#
#   Primary   – GATT server (peripheral).  Always advertises.
#               Signals Secondary via GATT *notifications*.
#
#   Secondary – GATT client (central).  Connects to Primary on
#               startup and keeps the connection alive.
#               Signals Primary via GATT *writes*.
#
# Both classes expose the same public interface:
#   signal_open()       – tell the other gate to start incoming animation
#   signal_close()      – tell the other gate the wormhole has closed
#   is_connected()      – True while the link is up
#   wormhole_opened     – set True by IRQ when the other gate signals OPEN
#   wormhole_closed     – set True by IRQ when the other gate signals CLOSE
#
# Command bytes written / notified over the wire:
#   CMD_OPEN  = b'\x01'
#   CMD_CLOSE = b'\x00'

import bluetooth
import utime
from micropython import const

# ── BLE IRQ event constants ────────────────────────────────────
_IRQ_CENTRAL_CONNECT             = const(1)
_IRQ_CENTRAL_DISCONNECT          = const(2)
_IRQ_GATTS_WRITE                 = const(3)
_IRQ_SCAN_RESULT                 = const(5)
_IRQ_SCAN_DONE                   = const(6)
_IRQ_PERIPHERAL_CONNECT          = const(7)
_IRQ_PERIPHERAL_DISCONNECT       = const(8)
_IRQ_GATTC_SERVICE_RESULT        = const(9)
_IRQ_GATTC_SERVICE_DONE          = const(10)
_IRQ_GATTC_CHARACTERISTIC_RESULT = const(11)
_IRQ_GATTC_CHARACTERISTIC_DONE   = const(12)
_IRQ_GATTC_DESCRIPTOR_RESULT     = const(13)
_IRQ_GATTC_DESCRIPTOR_DONE       = const(14)
_IRQ_GATTC_WRITE_DONE            = const(17)
_IRQ_GATTC_NOTIFY                = const(18)

# ── GATT characteristic property flags ────────────────────────
_FLAG_READ   = const(0x0002)
_FLAG_WRITE  = const(0x0008)
_FLAG_NOTIFY = const(0x0010)

# ── Custom 128-bit service / characteristic UUIDs ──────────────
_SVC_UUID  = bluetooth.UUID('A5E4C3B2-D1F0-4E8A-9C7B-6D2E1F3A5C8E')
_CHAR_UUID = bluetooth.UUID('B6F5D4C3-E2A1-5F9B-0D8C-7E3F2A4B6D9F')
_CCCD_UUID = bluetooth.UUID(0x2902)   # standard Client Characteristic Config

# ── Wire commands ──────────────────────────────────────────────
CMD_OPEN  = b'\x01'
CMD_CLOSE = b'\x00'

# ── Advertisement helpers ──────────────────────────────────────
_ADV_FLAGS   = const(0x01)
_ADV_NAME    = const(0x09)   # Complete Local Name
_ADV_UUID128 = const(0x07)   # Complete list of 128-bit service UUIDs


def _adv_payload(name: str, service_uuid: bluetooth.UUID) -> bytes:
    buf = bytearray()
    buf += bytes([2, _ADV_FLAGS, 0x06])          # LE General Discoverable, BR/EDR off
    n = name.encode()
    buf += bytes([1 + len(n), _ADV_NAME]) + n
    u = bytes(service_uuid)                       # 16 bytes, already little-endian
    buf += bytes([1 + len(u), _ADV_UUID128]) + u
    return bytes(buf)


def _contains_uuid(adv_data: bytes, target: bluetooth.UUID) -> bool:
    """Return True if the raw advertisement data lists target as a 128-bit UUID."""
    target_bytes = bytes(target)
    i = 0
    while i < len(adv_data):
        length = adv_data[i]
        if length == 0:
            break
        if i + length >= len(adv_data):
            break
        ad_type = adv_data[i + 1]
        if ad_type in (0x06, 0x07) and length == 17:
            if adv_data[i + 2: i + 18] == target_bytes:
                return True
        i += 1 + length
    return False


# ══════════════════════════════════════════════════════════════
# Primary Gate  (GATT server / BLE peripheral)
# ══════════════════════════════════════════════════════════════

class BLEPrimary:
    """GATT server that advertises and accepts a connection from Secondary.

    Outgoing signal  → gatts_notify() pushes CMD_OPEN / CMD_CLOSE.
    Incoming signal  → _IRQ_GATTS_WRITE sets wormhole_opened / wormhole_closed.
    """

    def __init__(self, name: str):
        self.wormhole_opened = False
        self.wormhole_closed = False
        self._conn_handle    = None
        self._name           = name

        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)

        # Characteristic: Secondary can write to it; Primary can notify Secondary.
        services = (
            (_SVC_UUID, (
                (_CHAR_UUID, _FLAG_READ | _FLAG_WRITE | _FLAG_NOTIFY),
            )),
        )
        ((self._char_handle,),) = self._ble.gatts_register_services(services)
        self._ble.gatts_write(self._char_handle, CMD_CLOSE)

        self._advertise()
        print('[BLE Primary] Advertising as', name + '-Pri')

    # ── Public interface ────────────────────────────────────────

    def is_connected(self) -> bool:
        return self._conn_handle is not None

    def signal_open(self) -> None:
        """Notify Secondary to start its incoming animation."""
        if self._conn_handle is not None:
            try:
                self._ble.gatts_notify(self._conn_handle,
                                       self._char_handle, CMD_OPEN)
                print('[BLE Primary] Sent OPEN notification')
            except Exception as e:
                print('[BLE Primary] Notify error:', e)

    def signal_close(self) -> None:
        """Notify Secondary the wormhole has closed."""
        if self._conn_handle is not None:
            try:
                self._ble.gatts_notify(self._conn_handle,
                                       self._char_handle, CMD_CLOSE)
            except Exception:
                pass

    def stop(self) -> None:
        self._ble.gap_advertise(None)
        self._ble.active(False)

    # ── BLE IRQ ─────────────────────────────────────────────────

    def _advertise(self) -> None:
        adv = _adv_payload(self._name + '-Pri', _SVC_UUID)
        self._ble.gap_advertise(100_000, adv_data=adv)   # 100 ms interval

    def _irq(self, event, data) -> None:
        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self._conn_handle = conn_handle
            # Stop advertising – only one Secondary supported at a time.
            self._ble.gap_advertise(None)
            print('[BLE Primary] Secondary connected')

        elif event == _IRQ_CENTRAL_DISCONNECT:
            self._conn_handle = None
            print('[BLE Primary] Secondary disconnected – re-advertising')
            self._advertise()

        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            if value_handle == self._char_handle:
                value = bytes(self._ble.gatts_read(self._char_handle))
                if value == CMD_OPEN:
                    print('[BLE Primary] Received OPEN from Secondary')
                    self.wormhole_opened = True
                    self.wormhole_closed = False
                elif value == CMD_CLOSE:
                    print('[BLE Primary] Received CLOSE from Secondary')
                    self.wormhole_closed = True


# ══════════════════════════════════════════════════════════════
# Secondary Gate  (GATT client / BLE central)
# ══════════════════════════════════════════════════════════════

class BLESecondary:
    """GATT client that connects to Primary and keeps the connection alive.

    Outgoing signal  → gattc_write() sends CMD_OPEN / CMD_CLOSE.
    Incoming signal  → _IRQ_GATTC_NOTIFY sets wormhole_opened / wormhole_closed.

    Call try_connect() from the main loop whenever is_connected() is False.
    """

    # Discovery state machine
    _ST_IDLE       = 0
    _ST_SCANNING   = 1
    _ST_CONNECTING = 2
    _ST_DISC_SVC   = 3
    _ST_DISC_CHAR  = 4
    _ST_DISC_DESC  = 5
    _ST_READY      = 6
    _ST_FAILED     = 7

    def __init__(self, name: str, scan_timeout_s: int = 12):
        self.wormhole_opened  = False
        self.wormhole_closed  = False
        self._scan_timeout    = scan_timeout_s
        self._state           = self._ST_IDLE
        self._conn_handle     = None
        self._char_handle     = None   # writable value handle
        self._cccd_handle     = None   # CCCD descriptor handle
        self._svc_start       = None
        self._svc_end         = None

        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)
        print('[BLE Secondary] Ready – call try_connect() to link with Primary')

    # ── Public interface ────────────────────────────────────────

    def is_connected(self) -> bool:
        return self._state == self._ST_READY

    def try_connect(self, timeout_s: int = None) -> bool:
        """Scan for Primary and establish a fully-discovered connection.

        Blocking.  Returns True if the link is ready for use.
        Safe to call repeatedly when is_connected() is False.
        """
        if self._state == self._ST_READY:
            return True

        timeout_s = timeout_s or self._scan_timeout
        self._reset_discovery()

        print('[BLE Secondary] Scanning for Primary …')
        self._state = self._ST_SCANNING
        self._ble.gap_scan(timeout_s * 1000, 30_000, 30_000)

        # ── Phase 1: find Primary in scan results ───────────────
        deadline = utime.ticks_add(utime.ticks_ms(), timeout_s * 1000)
        while self._state == self._ST_SCANNING:
            if utime.ticks_diff(deadline, utime.ticks_ms()) <= 0:
                self._ble.gap_scan(None)
                print('[BLE Secondary] Scan timeout – Primary not found')
                self._state = self._ST_IDLE
                return False
            utime.sleep_ms(50)

        if self._state in (self._ST_IDLE, self._ST_FAILED):
            return False

        # ── Phase 2: connect + discover service/char/CCCD ───────
        deadline = utime.ticks_add(utime.ticks_ms(), 12_000)
        while self._state not in (self._ST_READY, self._ST_FAILED,
                                  self._ST_IDLE):
            if utime.ticks_diff(deadline, utime.ticks_ms()) <= 0:
                print('[BLE Secondary] Discovery timeout')
                self._state = self._ST_FAILED
                return False
            utime.sleep_ms(50)

        result = (self._state == self._ST_READY)
        if result:
            print('[BLE Secondary] Linked to Primary')
        return result

    def signal_open(self) -> bool:
        """Write CMD_OPEN to Primary's characteristic."""
        return self._write(CMD_OPEN)

    def signal_close(self) -> bool:
        """Write CMD_CLOSE to Primary's characteristic."""
        return self._write(CMD_CLOSE)

    def stop(self) -> None:
        self._ble.active(False)

    # ── Internal helpers ────────────────────────────────────────

    def _reset_discovery(self) -> None:
        self._conn_handle = None
        self._char_handle = None
        self._cccd_handle = None
        self._svc_start   = None
        self._svc_end     = None

    def _write(self, cmd: bytes) -> bool:
        if self._state != self._ST_READY or self._char_handle is None:
            return False
        try:
            self._ble.gattc_write(self._conn_handle, self._char_handle, cmd, 1)
            utime.sleep_ms(100)   # allow IRQ to fire
            return True
        except Exception as e:
            print('[BLE Secondary] Write error:', e)
            return False

    # ── BLE IRQ ─────────────────────────────────────────────────

    def _irq(self, event, data) -> None:
        if event == _IRQ_SCAN_RESULT:
            addr_type, addr, adv_type, rssi, adv_data = data
            if _contains_uuid(bytes(adv_data), _SVC_UUID):
                print('[BLE Secondary] Found Primary, RSSI', rssi)
                self._ble.gap_scan(None)
                self._state = self._ST_CONNECTING
                self._ble.gap_connect(addr_type, addr)

        elif event == _IRQ_SCAN_DONE:
            # Fired after gap_scan duration or after gap_scan(None).
            if self._state == self._ST_SCANNING:
                self._state = self._ST_IDLE   # timed out without finding Primary

        elif event == _IRQ_PERIPHERAL_CONNECT:
            conn_handle, _, _ = data
            self._conn_handle = conn_handle
            self._state = self._ST_DISC_SVC
            self._ble.gattc_discover_services(conn_handle)

        elif event == _IRQ_PERIPHERAL_DISCONNECT:
            self._conn_handle = None
            self._char_handle = None
            self._cccd_handle = None
            self._state = self._ST_IDLE
            print('[BLE Secondary] Lost connection to Primary')

        elif event == _IRQ_GATTC_SERVICE_RESULT:
            conn_handle, start_handle, end_handle, uuid = data
            if uuid == _SVC_UUID:
                self._svc_start = start_handle
                self._svc_end   = end_handle

        elif event == _IRQ_GATTC_SERVICE_DONE:
            conn_handle, status = data
            if status == 0 and self._svc_start is not None:
                self._state = self._ST_DISC_CHAR
                self._ble.gattc_discover_characteristics(
                    conn_handle, self._svc_start, self._svc_end)
            else:
                print('[BLE Secondary] Service not found (status', status, ')')
                self._state = self._ST_FAILED

        elif event == _IRQ_GATTC_CHARACTERISTIC_RESULT:
            conn_handle, def_handle, value_handle, properties, uuid = data
            if uuid == _CHAR_UUID:
                self._char_handle  = value_handle
                # Descriptor range starts right after the value handle.
                self._desc_start   = value_handle + 1

        elif event == _IRQ_GATTC_CHARACTERISTIC_DONE:
            conn_handle, status = data
            if status == 0 and self._char_handle is not None:
                self._state = self._ST_DISC_DESC
                # Search for CCCD between value_handle+1 and end of service.
                self._ble.gattc_discover_descriptors(
                    conn_handle,
                    self._desc_start,
                    self._svc_end)
            else:
                print('[BLE Secondary] Characteristic not found (status', status, ')')
                self._state = self._ST_FAILED

        elif event == _IRQ_GATTC_DESCRIPTOR_RESULT:
            conn_handle, dsc_handle, uuid = data
            if uuid == _CCCD_UUID:
                self._cccd_handle = dsc_handle

        elif event == _IRQ_GATTC_DESCRIPTOR_DONE:
            conn_handle, status = data
            if self._cccd_handle is not None:
                # Enable notifications: write 0x0001 to CCCD.
                self._ble.gattc_write(self._conn_handle, self._cccd_handle,
                                      b'\x01\x00', 1)
                self._state = self._ST_READY
                print('[BLE Secondary] Notifications enabled')
            else:
                # No CCCD found (unlikely but safe to continue without notify).
                print('[BLE Secondary] Warning: no CCCD – notifications disabled')
                self._state = self._ST_READY

        elif event == _IRQ_GATTC_NOTIFY:
            # Primary pushed a notification to us.
            conn_handle, value_handle, notify_data = data
            value = bytes(notify_data)
            if value == CMD_OPEN:
                print('[BLE Secondary] Received OPEN notification from Primary')
                self.wormhole_opened = True
                self.wormhole_closed = False
            elif value == CMD_CLOSE:
                print('[BLE Secondary] Received CLOSE notification from Primary')
                self.wormhole_closed = True

        elif event == _IRQ_GATTC_WRITE_DONE:
            conn_handle, value_handle, status = data
            if status != 0:
                print('[BLE Secondary] Write failed (status', status, ')')
