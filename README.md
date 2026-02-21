# uPyPiStargate

MicroPython firmware for a Raspberry Pi Pico (or Pico W) that reproduces
the **Stargate SG-1 Milky Way gate dialing animation** using 9 LEDs and a
reed switch (or button).

Two Pico W boards can optionally be linked over **Bluetooth LE** so that
**either** gate can initiate a dial — whichever reed switch is triggered first
plays the dialing animation while the other plays the incoming animation.

---

## Hardware

### Single gate (Pico or Pico W)

| Component | Qty | Notes |
|-----------|-----|-------|
| Raspberry Pi Pico / Pico W | 1 | Pico W required for wireless |
| 5 mm LED (blue or white) | 9 | One per chevron |
| 330 Ω resistor | 9 | Current limiter (adjust for LED colour) |
| Reed switch **or** momentary button | 1 | Trigger input |
| Breadboard / PCB | 1 | |

### Wiring

```
Pico 3V3 (pin 36) ──────────────────────── [not used for LEDs]
Pico GND  (pin 38) ──┬─────────────────── common ground
                     │
GPIO 2  ──[330Ω]──[LED 0]── GND   (top / master chevron)
GPIO 3  ──[330Ω]──[LED 1]── GND   (upper-right)
GPIO 4  ──[330Ω]──[LED 2]── GND   (right)
GPIO 5  ──[330Ω]──[LED 3]── GND   (lower-right)
GPIO 6  ──[330Ω]──[LED 4]── GND   (lower-right-of-bottom)
GPIO 7  ──[330Ω]──[LED 5]── GND   (bottom)
GPIO 8  ──[330Ω]──[LED 6]── GND   (lower-left)
GPIO 9  ──[330Ω]──[LED 7]── GND   (left)
GPIO 10 ──[330Ω]──[LED 8]── GND   (upper-left)

GPIO 15 ──[reed switch / button]── GND
         (internal pull-up enabled; switch shorts pin to GND when active)
```

Chevron positions on the ring (viewed from front):

```
         [ 0 ]         ← top / master chevron (always locks last)
      [8]     [1]
   [7]           [2]
   [6]           [3]
      [5]     [4]
```

You can reorder the physical wiring however suits your model.
Just update `LED_PINS` and `LOCK_ORDER` in `config.py`.

---

## Animation sequences

### Dialing (triggered gate)
1. **Gate rotation** – a dim scan-light sweeps clockwise through the unlocked
   chevrons, simulating the inner ring spinning to find the next glyph.
2. **Chevron lock** – the target chevron flashes 3× then locks solid.
   The 7th (master) chevron uses 5 flashes for extra drama.
3. **Kawoosh** – rapid chaotic flashing across all LEDs simulates the
   initial unstable vortex.
4. **Stable wormhole** – all locked LEDs breathe slowly in and out.
5. **Close** – smooth fade-out after the configured timeout (default 38 s).
   A second reed-switch trigger force-closes the wormhole early.

### Incoming wormhole (remote gate)
All seven chevrons lock rapidly in sequence, then kawoosh and stable
wormhole play as normal.

---

## Installation

### 1. Flash MicroPython

Download the latest MicroPython UF2 for your board from
<https://micropython.org/download/> and flash it in the usual way
(hold BOOTSEL while connecting USB).

### 2. Copy files to the Pico

Use **Thonny**, **mpremote**, or **rshell** to copy all four files:

```bash
# mpremote example
mpremote cp config.py animation.py ble_comm.py main.py :
```

### 3. Configure

Edit `config.py` on the Pico (or before copying) to match your wiring.
Key settings:

```python
LED_PINS    = [2, 3, 4, 5, 6, 7, 8, 9, 10]  # GPIO pins for the 9 LEDs
LOCK_ORDER  = [3, 5, 7, 2, 6, 8, 0]          # Lock sequence (last = master)
TRIGGER_PIN = 15                               # Reed switch / button pin
MODE        = 'standalone'                     # 'standalone' | 'primary' | 'secondary'
```

### 4. Run

`main.py` runs automatically on boot. Reset the Pico (or power-cycle it)
to start. The startup sweep confirms the LEDs are wired correctly.

---

## Two-gate BLE setup (Pico W only)

Requires two **Raspberry Pi Pico W** boards, each with the full firmware.
The BLE roles are fixed at flash time, but **both reed switches work
identically** — either gate can initiate a dial at any time.

### Primary gate
```python
# config.py on the Primary Pico W
MODE     = 'primary'
BLE_NAME = 'Stargate'
```
Acts as BLE peripheral (GATT server). Advertises as `Stargate-Pri` and waits
for Secondary to connect. Power this one on first.

### Secondary gate
```python
# config.py on the Secondary Pico W
MODE     = 'secondary'
BLE_NAME = 'Stargate'
```
Acts as BLE central (GATT client). Scans for `Stargate-Pri` on startup and
maintains a persistent connection. If the link drops (e.g. Primary reboots),
Secondary automatically reconnects every `BLE_RECONNECT_S` seconds while idle.

### Flow — triggered from either gate

**Gate A reed switch pressed:**
1. Gate A plays the full dialing animation (rotation → chevron locks → kawoosh).
2. Gate A signals Gate B over BLE (`OPEN` command).
3. Gate B plays the rapid incoming animation.
4. Both gates hold the stable wormhole (breathing LEDs).
5. After `WORMHOLE_TIMEOUT` seconds Gate A signals `CLOSE`; both fade out.

A second press on either reed switch force-closes the wormhole early.

---

## Customisation

| Setting | Effect |
|---------|--------|
| `LOCK_ORDER` | Change which LEDs lock in which order |
| `ROTATION_TIME_MIN/MAX` | Speed of simulated gate rotation |
| `KAWOOSH_DURATION` | Length of the vortex flash |
| `WORMHOLE_TIMEOUT` | How long the stable wormhole lasts |
| `WORMHOLE_PULSE_PERIOD` | Speed of the breathing effect |
| `WORMHOLE_MIN/MAX_BRIGHT` | Depth of the breathing effect |
| `PWM_FREQ` | LED PWM frequency (1000 Hz default) |

---

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| LEDs don't light | Check GND path; verify `LED_PINS` match wiring |
| Trigger fires instantly on boot | Switch wired to 3.3 V instead of GND; set `TRIGGER_ACTIVE_LOW = False` |
| Secondary can't find Primary | Ensure both boards are Pico W; power Primary first so it is advertising |
| `ImportError: no module named 'bluetooth'` | Board is a plain Pico (no wireless); set `MODE = 'standalone'` |
| Animation is too slow / fast | Adjust `ROTATION_TIME_*` and `LOCK_FLASH_*_MS` in `config.py` |
