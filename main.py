# ==============================================================
# Stargate SG-1 Milky Way Gate Controller – main entry point
# ==============================================================
# Modes (set in config.py):
#   'standalone'  – single gate, no wireless
#   'primary'     – GATT server; advertises and waits for Secondary
#   'secondary'   – GATT client; connects to Primary on startup
#
# In wireless modes EITHER gate's reed switch/button triggers the
# full dialing sequence on that gate while the other plays the
# incoming animation.  Both triggers work identically.

import machine
import utime
import config
from animation import StargateLEDs, GateAnimator


# ── Optional BLE import (Pico W only) ─────────────────────────
BLE_AVAILABLE  = False
BLEPrimary     = None
BLESecondary   = None

if config.MODE in ('primary', 'secondary'):
    try:
        from ble_comm import BLEPrimary, BLESecondary
        BLE_AVAILABLE = True
        print('[main] BLE module loaded')
    except ImportError:
        print('[main] WARNING: BLE not available – falling back to standalone')


# ── Helpers ────────────────────────────────────────────────────

def _trigger_active(pin: machine.Pin) -> bool:
    """Normalise raw pin value to True = "trigger is pressed"."""
    return (pin.value() == 0) if config.TRIGGER_ACTIVE_LOW else (pin.value() == 1)


# ── Gate sequences (same logic for both BLE roles) ─────────────

def run_dialing(anim: GateAnimator, ble, lock_order: list) -> None:
    """Local gate dials; remote gate (if any) plays incoming animation."""
    print('[gate] Dialing …')
    anim.dialing_sequence(lock_order)

    if ble is not None:
        ble.signal_open()

    print('[gate] Wormhole open for', config.WORMHOLE_TIMEOUT, 's')
    anim.stable_wormhole(lock_order, config.WORMHOLE_TIMEOUT)

    if ble is not None:
        ble.signal_close()

    anim.wormhole_close(lock_order)
    print('[gate] Wormhole closed')


def run_incoming(anim: GateAnimator, lock_order: list) -> None:
    """Remote gate dialed us – play the receiving animation."""
    print('[gate] Incoming wormhole!')
    anim.incoming_wormhole(lock_order)

    print('[gate] Wormhole open for', config.WORMHOLE_TIMEOUT, 's')
    anim.stable_wormhole(lock_order, config.WORMHOLE_TIMEOUT)

    anim.wormhole_close(lock_order)
    print('[gate] Wormhole closed')


# ── Entry point ────────────────────────────────────────────────

def main() -> None:
    print('=== uPyPiStargate ===')
    print('Mode:', config.MODE)

    # ── Hardware init ──────────────────────────────────────────
    leds = StargateLEDs(config.LED_PINS, config.PWM_FREQ)
    anim = GateAnimator(leds, config)

    pull = machine.Pin.PULL_UP if config.TRIGGER_PULL == 'up' \
           else machine.Pin.PULL_DOWN
    trigger_pin = machine.Pin(config.TRIGGER_PIN, machine.Pin.IN, pull)

    # ── BLE init ───────────────────────────────────────────────
    ble = None
    if BLE_AVAILABLE:
        if config.MODE == 'primary':
            ble = BLEPrimary(config.BLE_NAME)
        elif config.MODE == 'secondary':
            ble = BLESecondary(config.BLE_NAME, config.BLE_SCAN_TIMEOUT_S)
            # Attempt first connection now so the link is up before anyone
            # presses the trigger.  Failure here is non-fatal; the main loop
            # will keep retrying.
            ble.try_connect()

    # ── Startup visual ─────────────────────────────────────────
    anim.startup_sequence()

    # ── Main loop ──────────────────────────────────────────────
    lock_order       = config.LOCK_ORDER
    gate_busy        = False
    prev_active      = False
    debounce_end     = 0
    last_reconnect   = utime.ticks_ms()
    reconnect_ms     = int(config.BLE_RECONNECT_S * 1000)

    print('[main] Ready – trigger the reed switch to dial')

    while True:
        now = utime.ticks_ms()

        # ── Secondary: maintain persistent connection ──────────
        # Only attempt reconnection while the gate is idle so the blocking
        # scan doesn't interrupt an animation.
        if (isinstance(ble, BLESecondary)
                and not gate_busy
                and not ble.is_connected()
                and utime.ticks_diff(now, last_reconnect) >= reconnect_ms):
            print('[main] Attempting to reconnect to Primary …')
            ble.try_connect()
            last_reconnect = utime.ticks_ms()

        # ── Check for incoming signal from remote gate ─────────
        if ble is not None and not gate_busy and ble.wormhole_opened:
            ble.wormhole_opened = False
            gate_busy = True
            run_incoming(anim, lock_order)
            # Reset the close flag in case it arrived while we were busy.
            if ble is not None:
                ble.wormhole_closed = False
            gate_busy = False

        # ── Trigger detection (reed switch / button) ───────────
        currently_active = _trigger_active(trigger_pin)

        if currently_active and not prev_active:
            # Falling edge (active-low) – start debounce window.
            debounce_end = utime.ticks_add(now, config.DEBOUNCE_MS)

        elif currently_active and utime.ticks_diff(now, debounce_end) >= 0:
            if not gate_busy:
                gate_busy = True
                run_dialing(anim, ble, lock_order)
                gate_busy = False
            else:
                # Second press while wormhole is open → force close.
                print('[main] Force-closing wormhole')
                anim.stop_flag = True

            # Push debounce far out so we don't re-fire on the same press.
            debounce_end = utime.ticks_add(now, 2_000_000)

        prev_active = currently_active
        utime.sleep_ms(10)


# ── Run ────────────────────────────────────────────────────────
if __name__ == '__main__':
    main()
