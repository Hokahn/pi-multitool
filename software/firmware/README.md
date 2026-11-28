# Firmware für den RP2040-Zero

`rp2040_micarray_8ch_16k.uf2` macht aus dem RP2040-Zero ein USB-Mikrofon mit 8 Kanälen
(16 kHz, 32 bit). Es liest die vier I2S-Datenleitungen des Sipeed Mic Array 6+1 gleichzeitig.

## Aufspielen (einmalig, am Windows-PC)

1. Am RP2040-Zero die Taste **BOOT** gedrückt halten und das USB-C-Kabel zum PC einstecken.
2. Taste loslassen. Im Explorer erscheint ein Laufwerk **RPI-RP2**.
3. `rp2040_micarray_8ch_16k.uf2` auf dieses Laufwerk ziehen.
4. Das Laufwerk verschwindet von selbst. In Windows erscheint unter Sound ein neues Mikrofon
   **Pico Microphone**. Fertig.

## Anschluss

| RP2040-Zero | Mic Array |
| --- | --- |
| GP0 | MIC_CK |
| GP1 | MIC_WS |
| GP2 | MIC_D0 |
| GP3 | MIC_D1 |
| GP4 | MIC_D2 |
| GP5 | MIC_D3 |
| GND | GND |

## USB-Kanäle

Kanal 0 = D0 links, 1 = D1 links, 2 = D2 links, 3 = D3 links (Mitte),
4 = D0 rechts, 5 = D1 rechts, 6 = D2 rechts, 7 = leer.
Welches Mikrofon links oder rechts ist, legt die Platine fest. `multitool-test calibrate` findet es heraus.

## Quelle

Gebaut aus https://github.com/denisgav/microphone-array-library-for-pico (MIT-Lizenz),
Commit 01f3e68, Beispiel `usb_microphone_array`, Pico SDK 2.1.1, `PICO_BOARD=waveshare_rp2040_zero`.
Einzige Änderung: fehlendes `#include <stdint.h>` (siehe `patch_stdint.diff`).
