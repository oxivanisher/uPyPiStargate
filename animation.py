# ==============================================================
# Stargate SG-1 Milky Way Gate – LED animation engine
# ==============================================================
# All animation is blocking (no asyncio needed). BLE events are
# handled via IRQ callbacks and communicated through shared flags.

import machine
import utime
import math


# ── Low-level LED layer ────────────────────────────────────────

class StargateLEDs:
    """PWM-based driver for the 9 chevron LEDs.

    All brightness values are floats in [0.0, 1.0].
    """

    def __init__(self, pins: list, pwm_freq: int = 1000):
        self._pwms = []
        for pin_num in pins:
            pwm = machine.PWM(machine.Pin(pin_num))
            pwm.freq(pwm_freq)
            pwm.duty_u16(0)
            self._pwms.append(pwm)
        self.count = len(pins)

    # ── single LED ──────────────────────────────────────────────

    def set(self, index: int, brightness: float) -> None:
        duty = int(max(0.0, min(1.0, brightness)) * 65535)
        self._pwms[index].duty_u16(duty)

    def get(self, index: int) -> float:
        return self._pwms[index].duty_u16() / 65535

    # ── bulk helpers ────────────────────────────────────────────

    def set_all(self, brightness: float) -> None:
        duty = int(max(0.0, min(1.0, brightness)) * 65535)
        for pwm in self._pwms:
            pwm.duty_u16(duty)

    def set_subset(self, indices: list, brightness: float) -> None:
        duty = int(max(0.0, min(1.0, brightness)) * 65535)
        for i in indices:
            self._pwms[i].duty_u16(duty)

    def off(self) -> None:
        self.set_all(0.0)

    # ── timed effects ───────────────────────────────────────────

    def fade_to(self, index: int, target: float, duration_ms: int,
                steps: int = 40) -> None:
        """Smoothly fade a single LED to target brightness."""
        start = self.get(index)
        delta = target - start
        if steps < 1 or duration_ms < 1:
            self.set(index, target)
            return
        step_ms = max(1, duration_ms // steps)
        for i in range(steps + 1):
            self.set(index, start + delta * i / steps)
            utime.sleep_ms(step_ms)

    def fade_all_to(self, target: float, duration_ms: int,
                    steps: int = 40) -> None:
        """Fade all LEDs to a common target brightness."""
        starts = [self.get(i) for i in range(self.count)]
        deltas = [target - s for s in starts]
        if steps < 1 or duration_ms < 1:
            self.set_all(target)
            return
        step_ms = max(1, duration_ms // steps)
        for i in range(steps + 1):
            t = i / steps
            for idx in range(self.count):
                self.set(idx, starts[idx] + deltas[idx] * t)
            utime.sleep_ms(step_ms)


# ── Animation sequences ────────────────────────────────────────

class GateAnimator:
    """High-level Stargate animation controller.

    Parameters
    ----------
    leds : StargateLEDs
    cfg  : config module (imported in main.py and passed in)
    """

    def __init__(self, leds: StargateLEDs, cfg):
        self.leds      = leds
        self.cfg       = cfg
        # stop_flag is checked inside stable_wormhole() every loop tick.
        # Set it to True from outside (e.g. BLE IRQ) to close early.
        self.stop_flag = False
        # Optional callable invoked on every inner-loop tick.
        # Assign e.g. status_led.update so the LED stays alive during
        # blocking animations.  Defaults to a no-op so call sites need
        # no 'if tick_fn' guard and the type stays callable throughout.
        self.tick_fn = lambda: None

    # ── Public sequences ────────────────────────────────────────

    def startup_sequence(self) -> None:
        """Brief "system alive" flash on startup."""
        self.leds.off()
        utime.sleep_ms(300)
        # Sequential sweep then off
        for i in range(self.leds.count):
            self.leds.set(i, 0.6)
            utime.sleep_ms(60)
        utime.sleep_ms(200)
        for i in range(self.leds.count - 1, -1, -1):
            self.leds.set(i, 0.0)
            utime.sleep_ms(40)
        utime.sleep_ms(300)

    def dialing_sequence(self, lock_order: list) -> list:
        """Lock all chevrons in sequence and return the locked list.

        Does NOT play the kawoosh – call kawoosh(locked) afterwards so the
        caller can signal the remote gate between the two phases.

        lock_order  – list of LED indices to lock, in order.
                      The last one is the master (top) chevron.
        """
        self.leds.off()
        locked = []

        scan_dir = 1   # alternates ±1 after each chevron, like the real gate ring
        for step, led_idx in enumerate(lock_order):
            is_final = (step == len(lock_order) - 1)

            # ── Gate rotation ───────────────────────────────────
            rotation_ms = self._random_rotation_ms()
            self._rotation_scan(locked, rotation_ms, scan_dir)
            scan_dir *= -1

            # ── Chevron lock ────────────────────────────────────
            if is_final:
                self._chevron_lock(led_idx,
                                   flashes=self.cfg.FINAL_LOCK_FLASHES,
                                   on_ms=self.cfg.FINAL_FLASH_ON_MS,
                                   off_ms=self.cfg.FINAL_FLASH_OFF_MS)
            else:
                self._chevron_lock(led_idx,
                                   flashes=self.cfg.LOCK_FLASHES,
                                   on_ms=self.cfg.LOCK_FLASH_ON_MS,
                                   off_ms=self.cfg.LOCK_FLASH_OFF_MS)

            locked.append(led_idx)

        return locked

    def incoming_wormhole(self, lock_order: list) -> None:
        """Destination-gate animation: rapid sequential lock then kawoosh."""
        self.leds.off()
        locked = []

        for led_idx in lock_order:
            self._chevron_lock(led_idx, flashes=1,
                               on_ms=self.cfg.INCOMING_STEP_MS,
                               off_ms=self.cfg.INCOMING_STEP_MS // 2)
            locked.append(led_idx)
            utime.sleep_ms(self.cfg.INCOMING_STEP_MS)

        self.kawoosh(locked)

    def stable_wormhole(self, locked: list, timeout_s: float,
                        keep_open_fn=None) -> None:
        """Slow breathing pulse on locked chevrons.

        Closes when any of these happen (first wins):
          1. timeout_s elapses (safety cut-off).
          2. stop_flag is set True externally.
          3. keep_open_fn is provided AND returns False for at least
             WORMHOLE_CLOSE_DELAY_S seconds, AND WORMHOLE_MIN_OPEN_S
             has already elapsed.

        keep_open_fn  – callable() → bool.  Return True while the wormhole
                        should stay open (e.g. reed switch active), False when
                        the trigger has been released.  None = timeout only.
        """
        self.stop_flag = False
        now            = utime.ticks_ms()
        end_ms         = utime.ticks_add(now, int(timeout_s * 1000))
        min_end_ms     = utime.ticks_add(now, int(self.cfg.WORMHOLE_MIN_OPEN_S * 1000))
        close_delay_ms = int(self.cfg.WORMHOLE_CLOSE_DELAY_S * 1000)
        period_ms      = int(self.cfg.WORMHOLE_PULSE_PERIOD * 1000)
        release_ms     = None   # timestamp when keep_open_fn first returned False

        while True:
            now = utime.ticks_ms()

            if self.stop_flag:
                break
            if utime.ticks_diff(end_ms, now) <= 0:
                break

            # ── Reed-switch / keep-open logic ───────────────────
            if keep_open_fn is not None:
                past_min = utime.ticks_diff(now, min_end_ms) >= 0
                if past_min:
                    if keep_open_fn():
                        # Trigger still active – reset any pending close timer.
                        release_ms = None
                    else:
                        # Trigger released – start (or check) the close delay.
                        if release_ms is None:
                            release_ms = now
                        elif utime.ticks_diff(now, release_ms) >= close_delay_ms:
                            break   # delay expired → close

            # ── Sine-based breathing ─────────────────────────────
            phase      = (now % period_ms) / period_ms
            s          = 0.5 - 0.5 * math.cos(2 * math.pi * phase)
            brightness = (self.cfg.WORMHOLE_MIN_BRIGHT
                          + s * (self.cfg.WORMHOLE_MAX_BRIGHT
                                 - self.cfg.WORMHOLE_MIN_BRIGHT))
            for i in locked:
                self.leds.set(i, brightness)

            self.tick_fn()
            utime.sleep_ms(20)

    def wormhole_close(self, locked: list) -> None:
        """Rapid fade-out to close the wormhole."""
        self.leds.fade_all_to(0.0, int(self.cfg.CLOSE_DURATION * 1000),
                              steps=30)
        self.leds.off()

    # ── Private helpers ─────────────────────────────────────────

    def _random_rotation_ms(self) -> int:
        """Return a random rotation duration in milliseconds."""
        # urandom not available on all builds; use ticks as entropy source
        span = self.cfg.ROTATION_TIME_MAX - self.cfg.ROTATION_TIME_MIN
        # Pseudo-random via ticks_ms lower bits
        frac = (utime.ticks_ms() & 0xFF) / 255.0
        return int((self.cfg.ROTATION_TIME_MIN + frac * span) * 1000)

    def _rotation_scan(self, locked: list, duration_ms: int,
                       direction: int = 1) -> None:
        """Animate a dim sweep through unlocked chevrons to simulate gate spin.

        The scan light travels around the full ring of LED positions; locked
        chevrons are skipped, all others (including the next-to-lock target)
        participate in the sweep.  direction alternates ±1 each chevron to
        mimic the real gate ring reversing after every lock.

        locked      – indices already locked (stay bright, skipped by scan)
        duration_ms – how long the scan lasts
        direction   – +1 forward, -1 reverse through the unlocked list
        """
        # All LED positions that aren't yet locked (target included)
        unlocked = [i for i in range(self.leds.count) if i not in locked]

        if not unlocked:
            utime.sleep_ms(duration_ms)
            return

        end_ms   = utime.ticks_add(utime.ticks_ms(), duration_ms)
        # Always start at position 0 (LED 0, master chevron) regardless of
        # direction so it flashes at the top at the beginning of every rotation.
        # For direction=-1 the modulo wraps negative scan_pos correctly in
        # MicroPython (e.g. -1 % 9 == 8), giving a true counter-clockwise sweep.
        scan_pos = 0
        last_ms  = utime.ticks_ms()
        prev_idx = unlocked[0]

        while utime.ticks_diff(end_ms, utime.ticks_ms()) > 0:
            now = utime.ticks_ms()
            if utime.ticks_diff(now, last_ms) >= self.cfg.ROTATION_STEP_MS:
                # Extinguish previous scan LED (but not locked ones)
                if prev_idx not in locked:
                    self.leds.set(prev_idx, 0.0)

                cur_idx = unlocked[scan_pos % len(unlocked)]
                self.leds.set(cur_idx, self.cfg.ROTATION_SCAN_DIM)

                prev_idx  = cur_idx
                scan_pos += direction
                last_ms   = now

            self.tick_fn()
            utime.sleep_ms(5)

        # Clean up scan light
        for i in unlocked:
            if i not in locked:
                self.leds.set(i, 0.0)

    def _chevron_lock(self, index: int, flashes: int,
                      on_ms: int, off_ms: int) -> None:
        """Flash a chevron LED N times then leave it on (locked)."""
        for _ in range(flashes):
            self.leds.set(index, self.cfg.LOCK_BRIGHTNESS)
            utime.sleep_ms(on_ms)
            self.leds.set(index, 0.0)
            utime.sleep_ms(off_ms)
        # Final lock – stay on
        self.leds.set(index, self.cfg.LOCK_BRIGHTNESS)

    def kawoosh(self, locked: list) -> None:
        """Vortex animation: frantic flashing across all LEDs, then settle.

        Phases are proportional to KAWOOSH_DURATION so the animation scales
        correctly regardless of how short or long the duration is set:
          Phase 1 (~57%): chaotic single-LED bounce sweep
          Phase 2 (~25%): all LEDs flash together
          Phase 3 (~18%): settle – break and restore locked chevrons
        """
        duration_ms = int(self.cfg.KAWOOSH_DURATION * 1000)
        end_ms      = utime.ticks_add(utime.ticks_ms(), duration_ms)
        on_ms       = self.cfg.KAWOOSH_ON_MS
        off_ms      = self.cfg.KAWOOSH_OFF_MS
        # Thresholds scale with duration (same proportions as the original 2.8 s)
        p2_ms       = duration_ms * 43 // 100   # ~43% remaining → phase 2 starts
        p3_ms       = duration_ms * 18 // 100   # ~18% remaining → settle / break
        sweep_dir   = 1
        sweep_i     = 0

        while utime.ticks_diff(end_ms, utime.ticks_ms()) > 0:
            remaining = utime.ticks_diff(end_ms, utime.ticks_ms())

            if remaining > p2_ms:
                # Phase 1: chaotic full-ring sweep
                self.leds.set(sweep_i, 1.0)
                utime.sleep_ms(on_ms)
                self.leds.set(sweep_i, 0.0)
                utime.sleep_ms(off_ms)
                sweep_i = (sweep_i + sweep_dir) % self.leds.count
                if sweep_i == 0 or sweep_i == self.leds.count - 1:
                    sweep_dir *= -1  # bounce back and forth
            elif remaining > p3_ms:
                # Phase 2: all flash together
                self.leds.set_all(1.0)
                utime.sleep_ms(on_ms + 10)
                self.leds.set_all(0.0)
                utime.sleep_ms(off_ms + 10)
            else:
                pass
            self.tick_fn()
            if remaining <= p3_ms:
                # Phase 3: settle – locked chevrons fade in
                break

        # Restore locked chevrons to full brightness
        for i in locked:
            self.leds.set(i, self.cfg.LOCK_BRIGHTNESS)
        utime.sleep_ms(150)
