"""
main.py — Orchestrator / CLI for the Hack CPU simulator.

Pipeline:  program.asm --[assembler]--> program.hack --[CPU sim]--> output.vcd

Usage:
    python main.py program.asm output.vcd [--cycles N]
    python main.py program.hack output.vcd [--cycles N]
    python main.py --gui                 (Tkinter GUI: waveforms, step, export)

Given a .asm file it assembles first and writes the .hack file next to
the source; given a .hack file it skips straight to simulation.  The
CPU then executes up to --cycles clock cycles, recording one signal
snapshot per cycle, and vcd_writer emits the .vcd waveform file.
"""

import argparse
import os
import re
import sys

from assembler import AsmError, assemble_file
from hack_cpu import HackCPU
from vcd_writer import write_vcd


def load_hack_file(path):
    """Read a .hack file (one 16-bit binary word per line) into ints."""
    program = []
    with open(path, "r", encoding="ascii") as f:
        for lineno, line in enumerate(f, start=1):
            word = line.strip()
            if not word:
                continue
            if not re.fullmatch(r"[01]{16}", word):
                raise ValueError(
                    f"{path}, line {lineno}: expected 16 bits of 0/1, got {word!r}")
            program.append(int(word, 2))
    return program


def run_pipeline(program_path, output_path, max_cycles):
    """assemble (if needed) -> simulate -> write VCD.  Returns output path."""
    # ---- stage 1: get machine code ----
    if program_path.endswith(".asm"):
        machine_code, symbols, hack_path = assemble_file(program_path)
        print(f"Assembled {program_path} -> {hack_path} "
              f"({len(machine_code)} instructions)")
    elif program_path.endswith(".hack"):
        hack_path = program_path
        machine_code = load_hack_file(hack_path)
        print(f"Loaded {hack_path} ({len(machine_code)} instructions)")
    else:
        raise SystemExit(f"Unsupported input {program_path!r}: expected .asm or .hack")

    if not machine_code:
        raise SystemExit("Program is empty - nothing to simulate.")

    # ---- stage 2: simulate ----
    cpu = HackCPU(machine_code)
    try:
        snapshots = cpu.run(max_cycles)
    except (IndexError, ValueError) as exc:
        raise SystemExit(f"Simulation error: {exc}")
    last = snapshots[-1]
    print(f"Simulated {len(snapshots)} cycle(s); "
          f"final PC={last['pc']} A={last['a']} D={last['d']} R0={last['ram0']}")

    # ---- stage 3: write the VCD ----
    if output_path is None:
        output_path = re.sub(r"\.(asm|hack)$", "", program_path) + ".vcd"
    comment = (f"{os.path.basename(program_path)}: {len(machine_code)} instructions, "
               f"{len(snapshots)} cycles simulated")
    write_vcd(output_path, snapshots, comment=comment)
    print(f"Wrote waveform to {output_path}")
    print(f"Open it with:  gtkwave {output_path}")
    print("    ...or drop the file onto viewer.html in a browser.")
    return output_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hack CPU simulator with VCD waveform output (nand2tetris).")
    parser.add_argument("program", nargs="?", default=None,
                        help="input .asm (assembled first) or .hack file")
    parser.add_argument("output", nargs="?", default=None,
                        help="output .vcd path (default: <program>.vcd)")
    parser.add_argument("--cycles", type=int, default=200, metavar="N",
                        help="max clock cycles to simulate (default 200; "
                             "execution also stops early if PC returns to 0)")
    parser.add_argument("--gui", action="store_true",
                        help="launch the interactive Tkinter waveform GUI "
                             "instead of the command-line pipeline")
    args = parser.parse_args(argv)

    # No arguments at all (e.g. double-clicking the packaged exe) -> GUI.
    if args.gui or (argv is None and len(sys.argv) == 1):
        # Import lazily so the headless CLI works without a display (Tk).
        # gui.main() reads sys.argv itself, so a path passed alongside
        # --gui (e.g. "main.py program.asm --gui") loads into the GUI.
        import gui
        gui.main()
        return

    if not args.program:
        parser.error("the following arguments are required: program "
                     "(or use --gui)")
    if not os.path.exists(args.program):
        raise SystemExit(f"Input file not found: {args.program}")
    try:
        run_pipeline(args.program, args.output, args.cycles)
    except (AsmError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}")


if __name__ == "__main__":
    main()
