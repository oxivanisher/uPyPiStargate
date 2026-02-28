# ==============================================================
# Stargate SG-1 Milky Way Gate Controller - Configuration
# ==============================================================
# Flash this onto your Raspberry Pi Pico or Pico W.
# Edit the values below to match your wiring and preferences.

# --- LED Pins ---
# GPIO pins for the 9 chevron LEDs (connect each LED + resistor between pin and GND).
# Resistor value depends on LED colour (GPIO output = 3.3 V):
#   Blue / white  (Vf ≈ 3.0 V) → 33 Ω   gives ~10 mA
#   Red / yellow  (Vf ≈ 2.0 V) → 100 Ω  gives ~13 mA
#
# Physical chevron layout (Milky Way gate, viewed from front):
#
#           [LED 0]          ← top / "master" chevron (always locks last)
#      [8]        [1]
#   [7]               [2]
#   [6]               [3]
#      [5]        [4]
#
# The LOCK_ORDER below controls which LED index lights up at each dial step,
# so you can easily re-map without rewiring.
LED_PINS = [
    2,   # index 0 – top (master chevron)
    3,   # index 1 – upper-right
    4,   # index 2 – right
    5,   # index 3 – lower-right
    6,   # index 4 – lower-left-of-bottom
    7,   # index 5 – bottom
    8,   # index 6 – lower-left
    9,   # index 7 – left
    10,  # index 8 – upper-left
]

# Order in which LED indices are locked during a dial sequence.
# The last entry is always the master chevron (index 0 = top).
# Adjust to match how your physical LEDs are laid out around the gate model.
# Any length works: 7 entries = standard Milky Way dial,
#                   8 entries = Pegasus dial (requires 8 connected LEDs).
# Omit indices for LEDs you have not wired up (they are simply skipped).
LOCK_ORDER = [1, 2, 3, 6, 7, 8, 0]    # indices of chevrons to lock; last = master (top)
# Values are LED *indices* (0–8), not GPIO pin numbers.
# index = GPIO_pin - 2  (e.g. GPIO 9 → index 7, GPIO 10 → index 8)

# --- Trigger Input ---
# Connect a reed switch (or momentary button) between TRIGGER_PIN and GND.
TRIGGER_PIN        = 15   # GPIO pin
TRIGGER_PULL       = 'up' # 'up' → internal pull-up (switch to GND)
                           # 'down' → internal pull-down (switch to 3.3 V)
TRIGGER_ACTIVE_LOW = True  # True for pull-up / normally-open switch
DEBOUNCE_MS        = 50    # Debounce hold time in milliseconds

# --- Animation Timing ---
# All times in seconds unless noted otherwise.

# Gate-rotation phase (between each chevron lock)
ROTATION_TIME_MIN  = 0.8   # Shortest rotation window
ROTATION_TIME_MAX  = 2.0   # Longest  rotation window
ROTATION_SCAN_DIM  = 0.3   # Brightness of the scanning sweep (0.0–1.0)
ROTATION_STEP_MS   = 90    # How often the scan-light advances (ms)

# Chevron lock animation
LOCK_FLASHES       = 3     # Number of flashes before locking solid
LOCK_FLASH_ON_MS   = 70    # Flash on  duration (ms)
LOCK_FLASH_OFF_MS  = 50    # Flash off duration (ms)
LOCK_BRIGHTNESS    = 1.0   # Steady brightness once locked (0.0–1.0)

# Final (master) chevron gets extra drama
FINAL_LOCK_DELAY_S = 1.0   # Pause after previous lock before master chevron locks (no rotation scan)
FINAL_LOCK_FLASHES = 3
FINAL_FLASH_ON_MS  = 90
FINAL_FLASH_OFF_MS = 55

# Kawoosh – initial vortex after all chevrons lock
KAWOOSH_DURATION   = 1.8   # Total vortex duration (s)
KAWOOSH_ON_MS      = 35    # Flash on  in vortex (ms)
KAWOOSH_OFF_MS     = 25    # Flash off in vortex (ms)

# Stable wormhole
WORMHOLE_TIMEOUT       = 300.0 # Hard safety cut-off (s) – fallback if reed switch
                               # signal is lost. Set longer for game use.
WORMHOLE_MIN_OPEN_S    = 10.0  # Minimum open time before release can trigger close.
                               # Should be at least as long as the kawoosh + settle.
WORMHOLE_CLOSE_DELAY_S = 4.0   # Extra seconds to wait after reed switch releases
                               # before the wormhole actually closes.
WORMHOLE_PULSE_PERIOD  = 2.2   # One full breathe cycle (s)
WORMHOLE_MIN_BRIGHT    = 0.35  # Dimmest point of pulse
WORMHOLE_MAX_BRIGHT    = 1.0   # Brightest point of pulse

# Wormhole close animation
CLOSE_DURATION     = 0.6   # Seconds to fade all LEDs out

# Incoming wormhole (destination gate only)
INCOMING_STEP_MS   = 10    # Time between each rapid chevron lock

# PWM
PWM_FREQ = 1000   # Hz – 1 kHz is flicker-free and PWM-efficient

# --- Wireless Mode (Raspberry Pi Pico W only) ---
# 'standalone' – single gate, no wireless required (works on plain Pico too)
# 'primary'    – GATT server; advertises and waits for Secondary to connect.
#                Either gate can trigger a dial – Primary notifies Secondary.
# 'secondary'  – GATT client; connects to Primary on startup and keeps the
#                connection alive. Either gate can trigger a dial – Secondary
#                writes to Primary's characteristic.
#
# In a two-gate setup: flash one Pico W with 'primary' and the other with
# 'secondary'. Both reed switches work identically after that.
MODE     = 'standalone'
BLE_NAME = 'Stargate'           # Base BLE name; '-Pri'/'-Sec' appended automatically
BLE_SCAN_TIMEOUT_S  = 12        # Seconds Secondary spends scanning for Primary
BLE_RECONNECT_S     = 8         # Seconds Secondary waits between reconnect attempts
