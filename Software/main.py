import time
import machine
import neopixel
import random
import json
import sys
import select
import micropython
import _thread
from array import array

# --- Palette ---

# --- Curve helpers ---
def lerp_curve(curve, x):
    if x <= curve[0][0]:
        return curve[0][1]
    if x >= curve[-1][0]:
        return curve[-1][1]
    for i in range(len(curve) - 1):
        x0, y0 = curve[i]
        x1, y1 = curve[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return curve[-1][1]

LUT_SIZE = 101
LUT_STEP = 10
# Fixed-point scale: multiply float values by this to get integers
FP_SCALE = 10000

def bake_lut(curve):
    return [lerp_curve(curve, i * LUT_STEP) for i in range(LUT_SIZE)]

def bake_lut_fp(curve):
    """Bake LUT as fixed-point integers (x FP_SCALE)"""
    return array('l', [int(lerp_curve(curve, i * LUT_STEP) * FP_SCALE) for i in range(LUT_SIZE)])

def bake_lut_int(curve):
    """Bake LUT as plain integers (for drain, flare_heat)"""
    return array('l', [int(lerp_curve(curve, i * LUT_STEP)) for i in range(LUT_SIZE)])

def parse_curve(val):
    points = []
    for pair in val.split(";"):
        xy = pair.split(",")
        if len(xy) == 2:
            points.append((float(xy[0]), float(xy[1])))
    points.sort(key=lambda p: p[0])
    return points

def format_curve(curve):
    return ";".join(str(x) + "," + str(y) for x, y in curve)

# --- Palette LUT (built after curves are defined) ---
def build_palette_lut():
    lut = []
    for i in range(LUT_SIZE):
        x = i * LUT_STEP
        r = int(lerp_curve(curves["palette_r"], x))
        g = int(lerp_curve(curves["palette_g"], x))
        b = int(lerp_curve(curves["palette_b"], x))
        if r < 0: r = 0
        elif r > 255: r = 255
        if g < 0: g = 0
        elif g > 255: g = 255
        if b < 0: b = 0
        elif b > 255: b = 255
        lut.append((r, g, b))
    return lut

# --- Hardware ---
NUM_PIXELS = 300
pixels = neopixel.NeoPixel(machine.Pin(16), NUM_PIXELS)

# --- State arrays (signed 32-bit integers) ---
current = array('l', [0] * NUM_PIXELS)
last = array('l', [0] * NUM_PIXELS)
heat_source = array('l', [0] * NUM_PIXELS)
# Double-buffered random arrays (core 1 fills one while core 0 reads the other)
rand_chance_a = array('l', [0] * NUM_PIXELS)
rand_flare_a = array('l', [0] * NUM_PIXELS)
rand_chance_b = array('l', [0] * NUM_PIXELS)
rand_flare_b = array('l', [0] * NUM_PIXELS)

# --- Parameters ---
params = {
    "diffusion_neighbours": 10,
    "diffusion_offset": 10,
    "target_fps": 30,
    "brightness": 100,
}

curves = {
    "decay": [(0, 0.0), (333, 0.02), (666, 0.12), (1000, 0.61)],
    "diffusion": [(0, 0.44), (333, 0.43), (666, 0.35), (1000, 0.14)],
    "drain": [(0, 40), (333, 200), (666, 990), (1000, 2800)],
    "flare_heat": [(0, 3100), (333, 13200), (666, 31400), (1000, 27000)],
    "flare_chance": [(0, 0.00171), (333, 0.00049), (666, 0.00046), (1000, 0.00096)],
    "palette_r": [(0, 0), (333, 217), (666, 254), (1000, 255)],
    "palette_g": [(0, 0), (333, 30), (666, 148), (1000, 193)],
    "palette_b": [(0, 0), (333, 0), (666, 12), (1000, 40)],
}

# LUTs are built after load_settings()

def rebuild_palette_flat():
    global palette_lut
    palette_lut = build_palette_lut()
    for i in range(LUT_SIZE):
        r, g, b = palette_lut[i]
        palette_flat[i * 3] = g  # NeoPixel is GRB
        palette_flat[i * 3 + 1] = r
        palette_flat[i * 3 + 2] = b

def update_curve(name, points):
    curves[name] = points
    luts[name] = bake_lut(points)
    if name.startswith("palette_"):
        rebuild_palette_flat()
        return
    # Rebuild integer LUTs
    global decay_lut_fp, diffusion_lut_fp, drain_lut_int, flare_heat_lut_int, chance_lut_fp
    decay_lut_fp = bake_lut_fp(curves["decay"])
    diffusion_lut_fp = bake_lut_fp(curves["diffusion"])
    drain_lut_int = bake_lut_int(curves["drain"])
    flare_heat_lut_int = bake_lut_int(curves["flare_heat"])
    chance_lut_fp = bake_lut_fp(curves["flare_chance"])

# --- Settings persistence ---
SETTINGS_FILE = "settings.json"

def _settings_data():
    return {
        "params": params,
        "curves": {k: list(v) for k, v in curves.items()},
    }

def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(_settings_data(), f)

NUM_PRESET_SLOTS = 3

def preset_save(slot, name):
    data = _settings_data()
    data["name"] = name
    with open("preset%d.json" % slot, "w") as f:
        json.dump(data, f)

def preset_load(slot):
    with open("preset%d.json" % slot, "r") as f:
        data = json.load(f)
    for k, v in data.get("params", {}).items():
        if k in params:
            params[k] = v
    for k, v in data.get("curves", {}).items():
        if k in curves:
            update_curve(k, [tuple(p) for p in v])
    save_settings()

def preset_list():
    for i in range(1, NUM_PRESET_SLOTS + 1):
        try:
            with open("preset%d.json" % i, "r") as f:
                data = json.load(f)
            print("  preset:%d=%s" % (i, data.get("name", "")))
        except (OSError, ValueError):
            print("  preset:%d=" % i)

def preset_delete(slot):
    try:
        import os
        os.remove("preset%d.json" % slot)
    except OSError:
        pass

def load_settings():
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        for k, v in data.get("params", {}).items():
            if k in params:
                params[k] = v
        for k, v in data.get("curves", {}).items():
            if k in curves:
                curves[k] = [tuple(p) for p in v]
    except (OSError, ValueError):
        pass

load_settings()

# Build all LUTs after settings are loaded
palette_lut = build_palette_lut()
luts = {k: bake_lut(v) for k, v in curves.items()}
decay_lut_fp = bake_lut_fp(curves["decay"])
diffusion_lut_fp = bake_lut_fp(curves["diffusion"])
drain_lut_int = bake_lut_int(curves["drain"])
flare_heat_lut_int = bake_lut_int(curves["flare_heat"])
chance_lut_fp = bake_lut_fp(curves["flare_chance"])

# --- Serial handling ---
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)
serial_buf = ""

def process_serial():
    global serial_buf
    if not poll.poll(0):
        return
    while poll.poll(0):
        serial_buf += sys.stdin.read(1)
    while "\n" in serial_buf or "\r" in serial_buf:
        line, serial_buf = serial_buf.replace("\r", "\n").split("\n", 1)
        line = line.strip()
        if not line:
            continue
        if line == "list":
            for k, v in params.items():
                print("  {} = {}".format(k, v))
            for k, v in curves.items():
                print("  curve:{} = {}".format(k, format_curve(v)))
        elif line == "save":
            save_settings()
            print("  saved ok")
        elif line == "preset_list":
            preset_list()
        elif line.startswith("preset_save:"):
            parts = line[12:].split(":", 1)
            try:
                slot = int(parts[0])
                name = parts[1] if len(parts) > 1 else ""
                if 1 <= slot <= NUM_PRESET_SLOTS:
                    preset_save(slot, name)
                    print("  preset %d saved" % slot)
                else:
                    print("  error: slot must be 1-%d" % NUM_PRESET_SLOTS)
            except (ValueError, IndexError):
                print("  error: preset_save:N:name")
        elif line.startswith("preset_load:"):
            try:
                slot = int(line[12:])
                if 1 <= slot <= NUM_PRESET_SLOTS:
                    preset_load(slot)
                    print("  preset %d loaded" % slot)
                else:
                    print("  error: slot must be 1-%d" % NUM_PRESET_SLOTS)
            except (ValueError, OSError):
                print("  error: preset not found")
        elif line.startswith("preset_delete:"):
            try:
                slot = int(line[14:])
                if 1 <= slot <= NUM_PRESET_SLOTS:
                    preset_delete(slot)
                    print("  preset %d deleted" % slot)
                else:
                    print("  error: slot must be 1-%d" % NUM_PRESET_SLOTS)
            except ValueError:
                print("  error: preset_delete:N")
        elif line == "reset":
            for i in range(NUM_PIXELS):
                current[i] = 0
                last[i] = 0
                heat_source[i] = 0
            print("  reset ok")
        elif "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if key.startswith("curve:"):
                cname = key[6:]
                if cname in curves:
                    try:
                        update_curve(cname, parse_curve(val))
                    except ValueError:
                        print("  error: invalid curve")
                else:
                    print("  error: unknown curve")
            elif key in params:
                try:
                    if "." in val:
                        params[key] = float(val)
                    else:
                        params[key] = int(val)
                except ValueError:
                    print("  error: invalid value")
            else:
                print("  error: unknown param")
        else:
            print("  usage: list | reset | save | param=value | curve:name=x,y;x,y;...")

# --- Pre-fill random buffers ---
_randrange = random.randrange
def fill_randoms(rc, rf, n, fp_scale, lut_size):
    rr = _randrange
    for i in range(n):
        rc[i] = rr(0, fp_scale)
        rf[i] = rr(0, lut_size)

# --- Simulation (viper compiled, integer math, direct memory access) ---
@micropython.viper
def simulate_frame(cur: ptr32, lst: ptr32, hs: ptr32,
                   rc: ptr32, rf: ptr32,
                   chance_fp: ptr32, flare_int: ptr32, drain_int: ptr32,
                   diff_fp: ptr32, decay_fp: ptr32,
                   n: int, neighbours: int, off: int,
                   neighbour_count: int, fp_scale: int,
                   lut_step: int, lut_max: int):
    nm1 = n - 1

    # Flare generation + drain + diffusion
    for i in range(n):
        heat = int(cur[i])
        if heat < 0: heat = 0
        elif heat > 1000: heat = 1000

        hi = heat // lut_step
        if hi > lut_max: hi = lut_max

        if int(rc[i]) < int(chance_fp[hi]):
            hs[i] = int(hs[i]) + int(flare_int[int(rf[i])])

        fuel = int(hs[i])
        if fuel > 0:
            dv = int(drain_int[hi])
            drain = fuel if fuel < dv else dv
            # Don't push heat above 1000
            headroom = 1000 - heat
            if drain > headroom:
                drain = headroom
            if drain > 0:
                hs[i] = fuel - drain
                cur[i] = heat + drain

        prev_heat = int(lst[i])
        if prev_heat > 0:
            di = prev_heat // lut_step
            if di > lut_max: di = lut_max
            total_diff = prev_heat * int(diff_fp[di]) // fp_scale
            # Cap against previous frame value (stable) not current (modified by neighbours)
            if total_diff > prev_heat:
                total_diff = prev_heat
            hpn = total_diff // neighbour_count
            j = -neighbours
            while j <= neighbours:
                if j == 0:
                    cur[i] = int(cur[i]) - total_diff
                else:
                    ni = i + j * off
                    if ni >= 0 and ni < n:
                        cur[ni] = int(cur[ni]) + hpn
                j += 1

    # Clamp between steps to prevent oscillation
    for i in range(n):
        v = int(cur[i])
        if v < 0: cur[i] = 0
        elif v > 1000: cur[i] = 1000

    # Decay + palette + copy to last
    for i in range(n):
        heat = int(cur[i])
        if heat < 0: heat = 0
        elif heat > 1000: heat = 1000

        di = heat // lut_step
        if di > lut_max: di = lut_max
        decay_amt = heat * int(decay_fp[di]) // fp_scale
        # Cap decay so we never remove more than available
        if decay_amt > heat:
            decay_amt = heat
        heat = heat - decay_amt
        cur[i] = heat
        lst[i] = heat

# --- Pre-bake palette as flat array: [g0,r0,b0, g1,r1,b1, ...] ---
palette_flat = array('l', [0] * (LUT_SIZE * 3))

# Initial build (palette_lut already populated above)
for _i in range(LUT_SIZE):
    _r, _g, _b = palette_lut[_i]
    palette_flat[_i * 3] = _g
    palette_flat[_i * 3 + 1] = _r
    palette_flat[_i * 3 + 2] = _b

# --- Write pixels (viper, writes directly to NeoPixel buffer) ---
@micropython.viper
def write_pixels(cur: ptr32, pal: ptr32, buf: ptr8, n: int, lut_step: int, lut_max: int, bright: int):
    nm1 = n - 1
    for i in range(n):
        pi = int(cur[i]) // lut_step
        if pi < 0: pi = 0
        elif pi > lut_max: pi = lut_max
        dest = (nm1 - i) * 3
        src = pi * 3
        buf[dest] = int(pal[src]) * bright // 100
        buf[dest + 1] = int(pal[src + 1]) * bright // 100
        buf[dest + 2] = int(pal[src + 2]) * bright // 100

# --- Core 1: random number generation ---
rand_lock = _thread.allocate_lock()
rand_buf_ready = [False]  # mutable container so core 1 can see changes

def core1_random_filler(ready, lock, rc, rf, n, fp_scale, lut_size):
    while True:
        if not ready[0]:
            fill_randoms(rc, rf, n, fp_scale, lut_size)
            lock.acquire()
            ready[0] = True
            lock.release()
        else:
            time.sleep_us(100)

# Fill buffer A first (synchronous, for the first frame)
fill_randoms(rand_chance_a, rand_flare_a, NUM_PIXELS, FP_SCALE, LUT_SIZE)

# Start core 1 filling buffer B
_thread.start_new_thread(core1_random_filler,
    (rand_buf_ready, rand_lock, rand_chance_b, rand_flare_b,
     NUM_PIXELS, FP_SCALE, LUT_SIZE))

# --- Main loop ---
use_a = True  # which buffer core 0 is reading from

while True:
    frame_start = time.ticks_us()
    process_serial()

    neighbours = int(params["diffusion_neighbours"])
    offset = int(params["diffusion_offset"])

    # Pick the buffer to read from
    if use_a:
        rc, rf = rand_chance_a, rand_flare_a
    else:
        rc, rf = rand_chance_b, rand_flare_b

    simulate_frame(
        current, last, heat_source,
        rc, rf,
        chance_lut_fp, flare_heat_lut_int, drain_lut_int,
        diffusion_lut_fp, decay_lut_fp,
        NUM_PIXELS, neighbours, offset, neighbours * 2,
        FP_SCALE, LUT_STEP, LUT_SIZE - 1
    )

    write_pixels(current, palette_flat, pixels.buf, NUM_PIXELS, LUT_STEP, LUT_SIZE - 1, int(params["brightness"]))
    pixels.write()

    # Wait for core 1 to finish filling the other buffer, then swap
    while not rand_buf_ready[0]:
        time.sleep_us(50)
    rand_lock.acquire()
    rand_buf_ready[0] = False
    rand_lock.release()
    use_a = not use_a

    target_fps = int(params["target_fps"])
    if target_fps > 0:
        target_ms = 1000 // target_fps
        elapsed_ms = time.ticks_diff(time.ticks_us(), frame_start) // 1000
        sleep_ms = target_ms - elapsed_ms
        if sleep_ms > 0:
            time.sleep_ms(sleep_ms)
