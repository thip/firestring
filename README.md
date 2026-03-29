# FireString

An RP2040-based LED fire simulation controller that drives WS2812B NeoPixel strings to recreate the look of a glowing fireplace.

Full writeup: [davidcapper.dev/posts/fire-string](https://davidcapper.dev/posts/fire-string)

Finished units available at [hortus.dev/products/fire-string](https://hortus.dev/products/fire-string).

## How it works

The simulation is built on three physical concepts:

- **Black-body radiation** — pixel temperatures are mapped to a colour gradient from red (cool) through orange/yellow to white (hot).
- **Heat decay** — each pixel loses a proportion of its heat every frame, fading from bright to dark over time.
- **Thermal diffusion** — pixels transfer heat to neighbours proportionally to temperature difference, so heat spreads naturally through the string.

On top of this, a **fuel system** adds gradual brightness ramps (fuel converts to heat via a burn rate), and **temperature-dependent curves** make cool pixels decay slowly and reignite easily while hot pixels shed heat rapidly — mimicking real ember behaviour.

## Firmware

CircuitPython running on the RP2040 with `@micropython.viper` compilation and integer-only maths for performance. Includes a **web-based configurator** (Web Serial API) for real-time parameter tuning and preset management.

## Hardware

Custom 2-layer RP2040 PCB with USB-C, designed in KiCad. Schematic and board layout are in `hardware/`.

## Licence

- **Hardware** — [CC BY-NC-SA 4.0](hardware/LICENSE)
- **Software** — [MIT](Software/LICENSE)
