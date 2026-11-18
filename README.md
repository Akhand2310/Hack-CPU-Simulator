# Hack CPU Simulator with VCD Waveform Generation

A Python 3 toolchain (standard library only) that turns a Nand2Tetris
**Hack assembly** program into a **VCD waveform** you can open in GTKWave
— or in the bundled browser viewer — and watch the CPU execute: clock
edges, PC jumps, A/D register loads, ALU results, RAM writes.

```
program.asm ──assembler.py──▶ program.hack ──hack_cpu.py──▶ signals.vcd ──▶ GTKWave / viewer.html
```

## Files

| File | Purpose |
|------|---------|
| `alu.py` | The Hack ALU: 6 control bits, 18 functions, `zr`/`ng` flags. Self-verifying table. |
| `assembler.py` | Two-pass Hack assembler: labels, predefined symbols, variable allocation (RAM[16..]), A/C-instruction encoding. |
| `hack_cpu.py` | Cycle-accurate CPU model: A, D, PC, ALU, ROM, RAM (incl. SCREEN/KBD range). One `step()` = one clock cycle = one instruction. |
| `vcd_writer.py` | Standard-compliant VCD output (IEEE 1364 ch. 18): change-only logging, 10 ns per cycle, rising edge at +5 ns. |
| `main.py` | CLI orchestrator (`--gui` launches the desktop app; no args = GUI, so the packaged exe opens straight into it). |
| `gui.py` | Desktop GUI (Tkinter, standard library): Run/Step/Auto-step, waveform canvas with zoom/pan/hover, VCD export. `--selftest` runs it headless. |
| `viewer.html` | Optional in-browser waveform viewer (no dependencies, reads `.vcd` directly). |
| `build_exe.bat` / `build_exe.sh` | One-command PyInstaller build of a standalone `HackCPUSim` executable. |
| `examples/` | `sum1to10.asm` (end-to-end demo) and `pc_jump_demo.asm` (minimal jump demo). |
| `tests.py` | Unit + end-to-end tests. |

## Usage

```bash
# End to end: assemble -> simulate 300 cycles -> waveform
python main.py examples/sum1to10.asm signals.vcd --cycles 300

# Already have a .hack file? Skip the assembler.
python main.py program.hack signals.vcd --cycles 500

# Run the test suite
python -m unittest tests -v

# Launch the desktop GUI (same pipeline, interactive)
python main.py --gui          # or: python gui.py [program.asm]
```

## The desktop GUI (`HackCPUSim`)

`gui.py` is a self-contained Tkinter app — a miniature GTKWave:

* **Load .asm/.hack...** assembles through the same `assembler.py`
  pipeline as the CLI; **Demo** loads a built-in sum-1..10 program.
* **Run / Step / Auto / Reset** drive the CPU cycle by cycle; the log
  pane prints each step (instruction bits, PC/A/D/ALU, writeM).
* The waveform canvas shows all 12 signals (same set as the VCD),
  with square waves for bits and hexagon bus segments (hex labels)
  for 16-bit signals. Mouse wheel or drag-box to zoom, drag to
  re-slice, hover for an exact value tooltip, click a signal name to
  hide/show its lane, `Esc`/**Fit** to re-fit.
* **Export VCD...** writes the recorded cycles to a `.vcd` that opens
  in GTKWave or `viewer.html`.

## Building a standalone executable

```bash
pip install pyinstaller      # the only dev-time dependency

# Windows:
build_exe.bat
# macOS / Linux:
./build_exe.sh
```

This produces **`dist/HackCPUSim.exe`** (~12 MB, onefile, windowed).
Double-clicking it opens the GUI with the built-in demo — no Python
installation needed on the target machine. Add `--console` to keep a
console window for debugging. (`HackCPUSim.spec` and `build/` are
PyInstaller working files; `dist/` holds the result.)

`--cycles` bounds execution for programs that never terminate. The
simulator also stops early when the PC returns to 0 (the usual
"loop forever at reset" idiom, e.g. `(END) @END 0;JMP` with `END` at 0,
or `@0 / 0;JMP`).

## Viewing the waveform

**GTKWave** (free, all platforms):

```bash
gtkwave signals.vcd
```

In GTKWave: select the `hack_cpu` scope in the top-left pane, then
double-click signals (or select + append) in the bottom-left pane to add
them to the wave view. The cursor reads **cycle = time / 10** (one
10 ns period per instruction, rising edge at +5 ns latches the
registers).

**Browser viewer** (no install): open `viewer.html`, click *Choose file*
(or drag & drop) and load `signals.vcd` — or click *Demo* for a built-in
sample waveform. Hover for cursors, drag horizontally to zoom, use the
zoom buttons, and click signal names to show/hide.

## Signals in the VCD

| Signal | Width | Meaning |
|--------|-------|---------|
| `clk` | 1 | Toggles every half period; registers latch on the rising edge. |
| `pc` | 16 | Program counter (ROM address of the next instruction). |
| `A` | 16 | A register. |
| `D` | 16 | D register. |
| `ALU` | 16 | ALU output of the cycle just executed (0 for A-instructions). |
| `instruction` | 16 | Raw 16-bit instruction executed this cycle. |
| `addressM` | 16 | RAM address used this cycle (old A value). |
| `outM` | 16 | Value driven to RAM[A] (meaningful when `writeM`=1). |
| `zr`, `ng` | 1 | ALU flags: out == 0, out < 0. |
| `writeM` | 1 | RAM write enable (dest M). |
| `ram0` | 16 | RAM[0] (SP) — handy to watch; extend `SIGNALS` in `vcd_writer.py` for more. |

## What to look for in `signals.vcd`

Running `examples/sum1to10.asm`:

1. **Setup** (cycles 1–6): PC marches 0→1→…→5 while `n=10`, `i=0`,
   `sum=0` are written (watch `writeM`/`outM`/`addressM`).
2. **Loop** (cycles 7–161): PC cycles through the loop body; on the
   last iteration `D;JEQ` takes the jump — you can see `pc` skip from
   the compare straight to the epilogue instead of incrementing.
3. **Epilogue + idle** (cycles 162–166): `R0` receives 55, then the CPU
   idles at `@0 / 0;JMP`.

`examples/pc_jump_demo.asm` shows the same idea in miniature: PC visits
0, 1, 2, 3 then **jumps to 6**, skipping instructions 4–5 entirely —
matching the classic "PC visits 1 → 2 → 6" waveform sketch.

## Design notes

- **Single source of truth for the ALU.** `alu.COMP_BITS` maps comp
  mnemonics to control bits; the assembler encodes with it and the CPU
  decodes through a reverse lookup built from it, so the two can never
  disagree. `python alu.py` self-tests all 18 functions.
- **Old-A addressing.** On instructions like `AM=D` the RAM is written
  at the *old* A value while A latches the new one — the hardware latches
  everything on the same edge. Tested explicitly.
- **Change-only VCD logging.** Only signals that actually changed are
  re-emitted at each timestamp (standard VCD practice), with a full dump
  of reset values at time 0. The clock is emitted at every edge so
  viewers show a proper toggle.
- **Cycle semantics.** `step()` = combinational settle (decode + ALU +
  jump decision from current state) followed by one latch edge for
  A/D/RAM/PC simultaneously — mirroring the real clocked CPU.

Everything is standard-library Python 3; no third-party dependencies.
