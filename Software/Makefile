# FireString build & flash
# Usage:
#   make deploy       — full flash: nuke + firmware + deps + upload (board in bootloader)
#   make upload       — upload code only (board already running MicroPython)
#   make deps         — install MicroPython library dependencies
#   make flash        — copy UF2 to board in bootloader mode
#   make nuke         — erase all flash (board in bootloader mode)
#   make monitor      — open serial monitor

FIRMWARE_VERSION := 1.27.0
FIRMWARE_DATE := 20251209
FIRMWARE_URL := https://micropython.org/resources/firmware/PIMORONI_TINY2040-$(FIRMWARE_DATE)-v$(FIRMWARE_VERSION).uf2
FIRMWARE_FILE := firmware/PIMORONI_TINY2040-v$(FIRMWARE_VERSION).uf2
NUKE_FILE := firmware/flash_nuke.uf2
NUKE_URL := https://datasheets.raspberrypi.com/soft/flash_nuke.uf2
BOOTLOADER_VOL := /Volumes/RPI-RP2
FAT_IMAGE := firestring.img

# Find the RP2040 MicroPython port dynamically (matches both fresh "Board in FS" and configured "Fire String")
PORT = $(shell mpremote connect list 2>/dev/null | grep -E "Fire String|Board in FS" | awk '{print $$1}')

# Pre-compile main.py for RP2040 (ARM Cortex-M0+) to save RAM at import time
_main.mpy: main.py
	mpy-cross -march=armv6m -o _main.mpy main.py

.PHONY: deploy upload deps flash nuke monitor clean wait-for-bootloader wait-for-micropython

# Full deploy: erase, flash firmware, install deps, upload code
deploy: nuke wait-for-bootloader flash wait-for-micropython deps wipe upload

# Build FAT12 disk image containing configuration.html
$(FAT_IMAGE): configuration.html tools/mkfatfs.py
	python3 tools/mkfatfs.py $(FAT_IMAGE) configuration.html

# Download firmware if not cached
$(FIRMWARE_FILE):
	mkdir -p firmware
	curl -L -o $(FIRMWARE_FILE) $(FIRMWARE_URL)
	@echo "Downloaded $(FIRMWARE_FILE)"

$(NUKE_FILE):
	mkdir -p firmware
	curl -L -o $(NUKE_FILE) $(NUKE_URL)

# Erase all flash (board must be in bootloader mode)
nuke: $(NUKE_FILE)
	@test -d $(BOOTLOADER_VOL) || (echo "Error: board not in bootloader mode ($(BOOTLOADER_VOL) not found)" && exit 1)
	cp $(NUKE_FILE) $(BOOTLOADER_VOL)/
	@echo "Flash erased — board will reboot to bootloader"

# Flash firmware to board in bootloader mode
flash: $(FIRMWARE_FILE)
	@test -d $(BOOTLOADER_VOL) || (echo "Error: board not in bootloader mode ($(BOOTLOADER_VOL) not found)" && exit 1)
	cp $(FIRMWARE_FILE) $(BOOTLOADER_VOL)/
	@echo "Firmware copied — board will reboot into MicroPython"

# Wait for bootloader volume to appear
wait-for-bootloader:
	@echo "Waiting for bootloader..."
	@for i in $$(seq 1 30); do \
		if test -d $(BOOTLOADER_VOL); then \
			echo "Bootloader ready"; \
			sleep 1; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Error: bootloader did not appear after 30s" && exit 1

# Wait for board to reboot and enumerate as a serial device
wait-for-micropython:
	@echo "Waiting for MicroPython..."
	@for i in $$(seq 1 30); do \
		if mpremote connect list 2>/dev/null | grep -qE "Fire String|Board in FS"; then \
			echo "Board ready"; \
			sleep 1; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Error: board did not appear after 30s" && exit 1

# Install MicroPython library dependencies (uses resume to avoid soft reset)
deps:
	@echo "Installing dependencies..."
	mpremote connect $(PORT) resume mip install usb-device
	@echo "Dependencies installed"

# Wipe user data (presets, settings) for a clean flash
wipe:
	@echo "Wiping filesystem..."
	mpremote connect $(PORT) resume exec "import os; [os.remove(f) for f in os.listdir('/') if f != 'lib']"

# Upload application files (uses resume to avoid soft reset triggering boot.py USB reconfig)
upload: $(FAT_IMAGE) _main.mpy
	@echo "Uploading files..."
	mpremote connect $(PORT) resume exec "f=open('main.py','w'); f.write('import _main\n'); f.close()" + cp _main.mpy :_main.mpy + cp msc.py :msc.py + cp $(FAT_IMAGE) :$(FAT_IMAGE) + cp boot.py :boot.py + reset
	@echo "Done — FireString running"

# Serial monitor
monitor:
	mpremote connect $(PORT) repl

clean:
	rm -rf firmware/ $(FAT_IMAGE) _main.mpy
