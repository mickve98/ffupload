# Send to plate — v2

Slice models and send them to a FlashForge Adventurer 5M, from your phone,
through Home Assistant.

The slicing is done by **PrusaSlicer's command line engine**, bundled into the
add-on. Same engine as the desktop app, so the gcode is the real thing — this
is not a homegrown slicer.

## What changed from v1

- Drop in an `.stl`, `.3mf`, `.obj` or `.step` and it gets sliced here
- Layer height, filament, infill, supports and brim as on-screen controls
- Print time and filament weight shown before you commit
- Already-sliced `.gcode` / `.gx` still goes straight through, as before

The base image moves from Alpine to Debian, because that's where PrusaSlicer
is packaged. First build takes longer than v1 — several minutes.

## Read this before the first sliced print

**Watch the first print start.** The profile in `profiles/ad5m.ini` sets a
220 × 220 × 220 mm bed, a 0.4 mm nozzle, and start gcode that homes, heats,
and draws a prime line up the left edge of the plate.

Those settings are a sensible baseline, but they have not been verified
against your specific machine and firmware. Wrong start gcode is the one way
this can damage something — a nozzle driven into the plate. So for the first
one, stay next to the printer and be ready to hit stop.

If you already have a slicer profile that works on this printer, copy its
start and end gcode into `profiles/ad5m.ini`, replacing the `start_gcode` and
`end_gcode` lines. That's the safest possible setup, since it's known-good on
your hardware. Newlines in that file are written as `\n`.

## Tuning the profile

`profiles/ad5m.ini` is a plain PrusaSlicer config. Anything in it can be
edited: speeds, accelerations, retraction, cooling, wall count. The add-on
overrides only layer height, temperatures, infill, supports and brim per job;
everything else comes from the file.

After editing, restart the add-on.

## Material presets

| | Nozzle | Bed | Notes |
|---|---|---|---|
| PLA | 210 °C | 60 °C | Fan full |
| PETG | 240 °C | 80 °C | Fan 50%, longer retraction |
| TPU | 225 °C | 45 °C | Slowed right down, short retraction |

## Where things go

Sliced files land in the printer's file list, so your existing entities keep
working:

- `select.3d_printer_mick_mel_file_list`
- `button.3d_printer_mick_mel_print_file`

## Limits

- Slicing runs on your Home Assistant hardware. A dense model at 0.12 mm can
  take a few minutes and will use a CPU core while it does.
- Sliced results are kept for the last 8 jobs, then deleted.
- STEP files need PrusaSlicer 2.6 or newer; on older builds, convert to STL
  first.
- No 3D preview of the sliced result — you get the estimates, not a layer
  view. Check the first layer on the printer.
