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


# ── Onboard status LED ─────────────────────────────────────────
# Pico W: LED is on the CYW43 chip, accessed as Pin('LED').
# Pico:   LED is GP25.  Pin('LED') also works on recent MicroPython.

class StatusLED:
    """Non-blocking blink/solid for the onboard LED.

    States
    ------
    solid       – ready (standalone) or BLE peer connected, gate idle
    blink_slow  – BLE mode, searching for / not yet connected to peer
                  Asymmetric: 700 ms on / 300 ms off → visibly "mostly on"
    blink_fast  – gate animation in progress (dialing or incoming)
                  Even: 100 ms on / 100 ms off
    off         – explicit off

    Call update() on every main-loop tick AND from anim.tick_fn so the
    LED stays alive during blocking animation loops.
    """

    def __init__(self):
        try:
            self._pin = machine.Pin('LED', machine.Pin.OUT)
        except (TypeError, ValueError):
            self._pin = machine.Pin(25, machine.Pin.OUT)
        self._pin.off()
        self._on_ms  = 0    # 0 = steady state (solid or off)
        self._off_ms = 0
        self._lit    = False
        self._last   = 0

    def solid(self) -> None:
        self._on_ms = 0
        self._pin.on()
        self._lit   = True

    def off(self) -> None:
        self._on_ms = 0
        self._pin.off()
        self._lit   = False

    def blink_slow(self) -> None:
        """700 ms on / 300 ms off – "searching for peer"."""
        self._on_ms  = 700
        self._off_ms = 300
        self._lit    = True
        self._pin.on()
        self._last   = utime.ticks_ms()

    def blink_fast(self) -> None:
        """100 ms on / 100 ms off – "gate activity"."""
        self._on_ms  = 100
        self._off_ms = 100
        self._lit    = True
        self._pin.on()
        self._last   = utime.ticks_ms()

    def update(self) -> None:
        """Drive the blink timer.  Safe to call from animation tick_fn."""
        if self._on_ms == 0:
            return
        now      = utime.ticks_ms()
        half     = self._on_ms if self._lit else self._off_ms
        if utime.ticks_diff(now, self._last) >= half:
            self._lit = not self._lit
            self._pin.value(self._lit)
            self._last = now


# ── Helpers ────────────────────────────────────────────────────

def _trigger_active(pin: machine.Pin) -> bool:
    """Normalise raw pin value to True = "trigger is pressed"."""
    return (pin.value() == 0) if config.TRIGGER_ACTIVE_LOW else (pin.value() == 1)


def _ble_connected(ble) -> bool:
    """Return True if the BLE link to the peer is established."""
    return ble is not None and ble.is_connected()


# ── Gate sequences (same logic for both BLE roles) ─────────────

def _restore_led(status_led: StatusLED, ble) -> None:
    """Return the status LED to its appropriate idle state."""
    if config.MODE == 'standalone' or _ble_connected(ble):
        status_led.solid()
    else:
        status_led.blink_slow()


def run_dialing(anim: GateAnimator, ble, lock_order: list,
                trigger_pin: machine.Pin, status_led: StatusLED) -> None:
    """Local gate dials; remote gate (if any) plays incoming animation.

    The wormhole stays open while the reed switch is active (mini on it),
    then closes WORMHOLE_CLOSE_DELAY_S seconds after the switch releases.
    WORMHOLE_MIN_OPEN_S ensures it stays open long enough for the kawoosh
    to settle before the release timer can start.
    """
    status_led.blink_fast()
    print('[gate] Dialing …')
    anim.dialing_sequence(lock_order)

    if ble is not None:
        ble.signal_open()

    keep_open = lambda: _trigger_active(trigger_pin)
    print('[gate] Wormhole open – will close after reed switch releases')
    anim.stable_wormhole(lock_order, config.WORMHOLE_TIMEOUT, keep_open)

    if ble is not None:
        ble.signal_close()

    anim.wormhole_close(lock_order)
    print('[gate] Wormhole closed')
    _restore_led(status_led, ble)


def run_incoming(anim: GateAnimator, ble, lock_order: list,
                 status_led: StatusLED) -> None:
    """Remote gate dialed us – play the receiving animation.

    Closes instantly when the source sends a BLE CLOSE signal: the tick_fn
    wrapper sets stop_flag the moment wormhole_closed flips True, so the
    stable_wormhole loop exits on the very next 20 ms tick.
    """
    status_led.blink_fast()
    print('[gate] Incoming wormhole!')
    anim.incoming_wormhole(lock_order)

    # Wire a close-detector into tick_fn for the duration of stable_wormhole.
    if ble is not None:
        _prev_tick = anim.tick_fn
        def _tick():
            _prev_tick()
            if ble.wormhole_closed:
                anim.stop_flag = True
        anim.tick_fn = _tick

    print('[gate] Wormhole open – waiting for source to close')
    anim.stable_wormhole(lock_order, config.WORMHOLE_TIMEOUT)

    if ble is not None:
        anim.tick_fn = _prev_tick   # restore original tick_fn

    anim.wormhole_close(lock_order)
    print('[gate] Wormhole closed')
    _restore_led(status_led, ble)


# ── Entry point ────────────────────────────────────────────────

def main() -> None:
    print('=== uPyPiStargate ===')
    print('Mode:', config.MODE)

    # ── Hardware init ──────────────────────────────────────────
    leds       = StargateLEDs(config.LED_PINS, config.PWM_FREQ)
    anim       = GateAnimator(leds, config)
    status_led = StatusLED()

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
            # Kick off a background scan immediately – does not block.
            ble.start_connect()

    # ── Wire status LED into animation tick loop ───────────────
    anim.tick_fn = status_led.update

    # ── Startup visual ─────────────────────────────────────────
    anim.startup_sequence()

    # ── Initial status LED ─────────────────────────────────────
    if config.MODE == 'standalone':
        status_led.solid()
    else:
        status_led.blink_slow()   # will go solid once BLE peer connects

    # ── Main loop ──────────────────────────────────────────────
    lock_order     = config.LOCK_ORDER
    gate_busy      = False
    prev_active    = False
    debounce_end   = 0
    last_reconnect = utime.ticks_ms()
    reconnect_ms   = int(config.BLE_RECONNECT_S * 1000)
    was_connected  = False

    print('[main] Ready – trigger the reed switch to dial')

    while True:
        now = utime.ticks_ms()

        # ── Status LED ─────────────────────────────────────────
        connected = _ble_connected(ble)
        if config.MODE != 'standalone':
            if connected and not was_connected:
                status_led.solid()
                print('[main] BLE peer connected')
            elif not connected and was_connected:
                status_led.blink_slow()
                print('[main] BLE peer lost')
        was_connected = connected
        status_led.update()

        # ── Secondary: maintain persistent connection ──────────
        # start_connect() is non-blocking; it only re-initiates a scan
        # when idle (not already scanning/connecting).
        if (config.MODE == 'secondary'
                and ble is not None
                and not gate_busy
                and not ble.is_busy()
                and not ble.is_connected()
                and utime.ticks_diff(now, last_reconnect) >= reconnect_ms):
            ble.start_connect()
            last_reconnect = utime.ticks_ms()

        # ── Check for incoming signal from remote gate ─────────
        if ble is not None and not gate_busy and ble.wormhole_opened:
            ble.wormhole_opened = False
            gate_busy = True
            run_incoming(anim, ble, lock_order, status_led)
            ble.wormhole_closed = False   # clear flag for next round
            gate_busy = False

        # ── Trigger detection (reed switch / button) ───────────
        currently_active = _trigger_active(trigger_pin)

        if currently_active and not prev_active:
            debounce_end = utime.ticks_add(now, config.DEBOUNCE_MS)

        elif currently_active and utime.ticks_diff(now, debounce_end) >= 0:
            if not gate_busy:
                gate_busy = True
                run_dialing(anim, ble, lock_order, trigger_pin, status_led)
                gate_busy = False

            # Push debounce far out so we don't re-fire on the same press.
            debounce_end = utime.ticks_add(now, 2_000_000)

        prev_active = currently_active
        utime.sleep_ms(10)


# ── Run ────────────────────────────────────────────────────────
if __name__ == '__main__':
    main()
