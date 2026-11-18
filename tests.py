"""
tests.py — Unit and end-to-end tests for the Hack CPU simulator.

Run with:  python -m unittest tests -v   (or simply:  python tests.py)

Covers: the ALU control-bit table, assembler encodings/symbols/errors,
CPU semantics (including the AM=D old-A-address rule and jump flags),
and structural validation of the generated VCD file.
"""

import os
import re
import tempfile
import tkinter as tk
import unittest
from tkinter import messagebox

import gui
from alu import alu, comp_to_control, COMP_BITS
from assembler import assemble_source, AsmError
from hack_cpu import HackCPU
from main import load_hack_file
from vcd_writer import write_vcd


class TestALU(unittest.TestCase):
    def test_all_18_functions(self):
        expected = {
            "0": lambda x, y: 0, "1": lambda x, y: 1, "-1": lambda x, y: -1,
            "D": lambda x, y: x, "A": lambda x, y: y, "!D": lambda x, y: ~x,
            "!A": lambda x, y: ~y, "-D": lambda x, y: -x, "-A": lambda x, y: -y,
            "D+1": lambda x, y: x + 1, "A+1": lambda x, y: y + 1,
            "D-1": lambda x, y: x - 1, "A-1": lambda x, y: y - 1,
            "D+A": lambda x, y: x + y, "D-A": lambda x, y: x - y,
            "A-D": lambda x, y: y - x, "D&A": lambda x, y: x & y,
            "D|A": lambda x, y: x | y,
        }
        samples = [(53, 7), (0, 0), (0xFFFF, 1), (0x8000, 0xFFFF), (0x1234, 0x4321)]
        for mnemonic, fn in expected.items():
            for x, y in samples:
                out, zr, ng = alu(x, y, *comp_to_control(mnemonic)[1:])
                self.assertEqual(out, fn(x, y) & 0xFFFF, msg=f"{mnemonic}({x},{y})")
                self.assertEqual(zr, out == 0)
                self.assertEqual(ng, bool(out & 0x8000))

    def test_flags(self):
        self.assertEqual(alu(0, 0, *comp_to_control("D+A")[1:]), (0, True, False))
        self.assertEqual(alu(5, 7, *comp_to_control("D-A")[1:]), (0xFFFE, False, True))
        # comp "D" outputs the x input; sign bit set => ng.
        self.assertEqual(alu(0x8000, 0, *comp_to_control("D")[1:]),
                         (0x8000, False, True))

    def test_unknown_mnemonic_rejected(self):
        with self.assertRaises(ValueError):
            comp_to_control("D**A")

    def test_all_28_table_entries_are_distinct_encodings(self):
        keys = [(a, bits) for a, bits in COMP_BITS.values()]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(COMP_BITS), 28)   # 18 with a=0 + 10 with a=1


class TestAssembler(unittest.TestCase):
    def test_book_encodings(self):
        code, _ = assemble_source("@10\nD=A\n@R2\nM=D\n")
        self.assertEqual(code, [0b0000000000001010, 0b1110110000010000,
                                0b0000000000000010, 0b1110001100001000])

    def test_labels_and_variables(self):
        code, syms = assemble_source("@sum\nM=0\n(LOOP)\n@LOOP\nD;JGT\n")
        self.assertEqual(syms["LOOP"], 2)      # label -> ROM address of next instr
        self.assertEqual(syms["sum"], 16)      # first variable -> RAM[16]
        self.assertEqual(code[0], 0b0000000000010000)
        self.assertEqual(code[2], 0b0000000000000010)  # @LOOP

    def test_predefined_symbols(self):
        code, syms = assemble_source("@SCREEN\nD=A\n@KBD\nD=D+A\n")
        self.assertEqual(syms["SCREEN"], 16384)
        self.assertEqual(syms["KBD"], 24576)
        self.assertEqual(code[0], 0b0100000000000000)

    def test_comments_and_blank_lines_ignored(self):
        code, _ = assemble_source("// header\n\n@1  // trailing\n\n0;JMP\n")
        self.assertEqual(code, [0b0000000000000001, 0b1110101010000111])

    def test_all_dest_and_jump_mnemonics(self):
        for dest, dbits in [("M", "001"), ("D", "010"), ("MD", "011"),
                            ("A", "100"), ("AM", "101"), ("AD", "110"),
                            ("AMD", "111")]:
            code, _ = assemble_source(f"@1\n{dest}=1\n")
            word = code[1]
            self.assertEqual((word >> 3) & 0b111, int(dbits, 2), msg=dest)
        for jump, jbits in [("JGT", "001"), ("JEQ", "010"), ("JGE", "011"),
                            ("JLT", "100"), ("JNE", "101"), ("JLE", "110"),
                            ("JMP", "111")]:
            code, _ = assemble_source("@1\n0;" + jump + "\n")
            self.assertEqual(code[1] & 0b111, int(jbits, 2), msg=jump)

    def test_errors(self):
        bad_programs = ["D=F00\n", "@\n", "@(BAD\n", "X=Y\n", "0;WAT\n",
                        "(A)\n(A)\n", "@40960\n", "MD;JMP\n"]
        for bad in bad_programs:
            with self.assertRaises(AsmError, msg=bad):
                assemble_source(bad)


class TestCPU(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Same program as examples/sum1to10.asm, assembled.
        with open(os.path.join("examples", "sum1to10.asm")) as f:
            asm = f.read()
        cls.code, _ = assemble_source(asm)

    def test_sum_1_to_10(self):
        cpu = HackCPU(self.code)
        snapshots = cpu.run(154)
        self.assertEqual(cpu.ram[18], 55)          # sum
        self.assertEqual(cpu.ram[17], 10)          # i == n at loop exit
        self.assertEqual(cpu.ram[0], 55)           # R0 = sum
        self.assertEqual(cpu.pc, 26)               # idle at (FIN)
        self.assertEqual(len(snapshots), 154)
        self.assertEqual(snapshots[-1]["instruction"], 0b1110101010000111)  # 0;JMP

    def test_cycle_count_matches_manual_trace(self):
        # Setup 8 instrs + 10 loop passes x14 + epilogue 4 + first idle 2 = 154.
        cpu = HackCPU(self.code)
        snapshots = cpu.run(154)
        self.assertEqual(len(snapshots), 8 + 10 * 14 + 4 + 2)

    def test_am_d_writes_old_a_address(self):
        cpu = HackCPU([0b0000000000000101,   # @5
                       0b1110110000010000,   # D=A
                       0b0000000000010011,   # @19
                       0b1110001100101000,   # AM=D: A<=5, RAM[19]<=5
                       0b0000000000000000,   # @0
                       0b1110101010000111])  # 0;JMP -> PC=0 stops the run
        snap = None
        for _ in range(4):                   # cycles 0..3, up to and incl. AM=D
            snap = cpu.step()
        self.assertEqual(cpu.a, 5)
        self.assertEqual(cpu.ram[19], 5)
        self.assertEqual(snap["address_m"], 19)   # OLD A value
        self.assertTrue(snap["write_m"])

    def test_jump_flags(self):
        # JGT must NOT jump when result is zero, must jump when positive.
        cpu = HackCPU([0b0000000000000000,   # @0
                       0b1110001110010000,   # D=D-1 (comp 001110, dest D) -> -1
                       0b0000000000000111,   # @7
                       0b1110001100000001,   # D;JGT (no jump, -1)
                       0b0000000000000000,   # @0
                       0b1110101010000111,   # 0;JMP -> stop
                       0b0000000000000000,   # 6: (never reached via JGT)
                       0b1110101010000111])  # 7: 0;JMP (idle)
        cpu.run(20)
        self.assertEqual(cpu.d, 0xFFFF)
        self.assertEqual(cpu.pc, 0)

        cpu = HackCPU([0b0000000000000010,   # @2
                       0b1110110000010000,   # D=A (=2)
                       0b0000000000000110,   # @6
                       0b1110001100000001,   # D;JGT (jumps: 2 > 0)
                       0b1110101010000111,   # 4: 0;JMP (skipped)
                       0b1110101010000111,   # 5: (skipped)
                       0b1110101010000111])  # 6: 0;JMP (idle)
        cpu.run(10)
        self.assertEqual(cpu.pc, 6)

    def test_running_off_end_raises(self):
        cpu = HackCPU([0b0000000000000001])   # @1, then nothing
        with self.assertRaises(IndexError):
            cpu.run(5)

    def test_pc_wraps_and_snapshot_fields(self):
        cpu = HackCPU(self.code)
        snap = cpu.step()
        for key in ("cycle", "clk", "pc", "a", "d", "alu", "zr", "ng",
                    "instruction", "address_m", "out_m", "write_m", "ram0"):
            self.assertIn(key, snap)
        self.assertEqual(snap["pc"], 1)       # A-instruction incremented PC
        self.assertEqual(snap["a"], 10)       # ... and loaded A=10


class TestVcdWriter(unittest.TestCase):
    def test_vcd_structure_and_change_only_logging(self):
        snaps = [
            {"cycle": 1, "clk": 1, "pc": 1, "a": 10, "d": 0, "alu": 0,
             "zr": True, "ng": False, "instruction": 0b0000000000001010,
             "address_m": 0, "out_m": 0, "write_m": False, "ram0": 0},
            {"cycle": 2, "clk": 1, "pc": 2, "a": 10, "d": 10, "alu": 10,
             "zr": False, "ng": False, "instruction": 0b1110110000010000,
             "address_m": 10, "out_m": 10, "write_m": False, "ram0": 0},
        ]
        fd, tmp = tempfile.mkstemp(suffix=".vcd")
        os.close(fd)
        try:
            write_vcd(tmp, snaps, comment="test")
            with open(tmp) as f:
                text = f.read()
            # Header structure.
            self.assertIn("$timescale 1ns $end", text)
            self.assertIn("$var wire 1 c clk $end", text)
            self.assertIn("$var wire 16 p pc $end", text)
            self.assertLess(text.index("$scope"), text.index("$enddefinitions"))
            self.assertLess(text.index("$enddefinitions"), text.index("#5"))
            self.assertIn("$comment", text)
            # Timestamps: t0 dump, then 5/10 and 15/20 edge pairs.
            for stamp in ("#0", "#5", "#10", "#15", "#20"):
                self.assertIn(f"\n{stamp}\n", text)
            # Clock toggles every edge.
            self.assertTrue(re.search(r"#5\n1c\n", text))
            self.assertTrue(re.search(r"#10\n0c\n", text))
            # Values logged only on change: A=10 appears exactly once,
            # D appears only at the second edge, initial dump at #0.
            self.assertEqual(text.count("b0000000000001010 a"), 1)
            self.assertTrue(re.search(
                r"#15\n1c\nb0000000000000010 p\nb0000000000001010 d\n", text))
            self.assertTrue(re.search(r"^b0000000000001010 i$", text, re.M))
        finally:
            os.unlink(tmp)

    def test_vcd_from_real_run_is_parseable(self):
        with open(os.path.join("examples", "pc_jump_demo.asm")) as f:
            asm = f.read()
        code, _ = assemble_source(asm)
        cpu = HackCPU(code)
        snaps = cpu.run(9)
        fd, tmp = tempfile.mkstemp(suffix=".vcd")
        os.close(fd)
        try:
            write_vcd(tmp, snaps)
            with open(tmp) as f:
                text = f.read()
            # Every value-change line after $enddefinitions must be well-formed:
            # "#<int>" or "b<16 bits> <id>" or "[01]<id>".
            body = text.split("$enddefinitions $end\n", 1)[1]
            for line in body.splitlines():
                if not line:
                    continue
                if line.startswith("#"):
                    self.assertTrue(line[1:].isdigit(), msg=line)
                elif line.startswith("b"):
                    self.assertRegex(line, r"^b[01]{16} [a-z]$", msg=line)
                else:
                    self.assertRegex(line, r"^[01][a-z]$", msg=line)
            # The jump: PC must reach 6 without ever visiting 4 or 5.
            pcs = [int(m, 2) for m in re.findall(r"^b([01]{16}) p$", text, re.M)]
            self.assertIn(6, pcs)
            self.assertNotIn(4, pcs)
            self.assertNotIn(5, pcs)
        finally:
            os.unlink(tmp)


class TestGuiHackLoading(unittest.TestCase):
    """Headless checks of the GUI's .hack load path (regression: a
    malformed .hack used to raise an uncaught ValueError and crash)."""

    def setUp(self):
        self._errors = []
        self._infos = []
        messagebox.showerror = lambda *a, **k: self._errors.append(a)
        messagebox.showinfo = lambda *a, **k: self._infos.append(a)
        self.root = tk.Tk()
        self.root.withdraw()          # real widgets, never shown
        self.gui = gui.HackSimGUI(self.root)   # starts with the demo

    def tearDown(self):
        self.root.destroy()

    def test_malformed_hack_shows_dialog_not_crash(self):
        fd, path = tempfile.mkstemp(suffix=".hack")
        os.close(fd)
        with open(path, "w", encoding="ascii") as f:
            f.write("notbinary\n0101010101010101\n")
        try:
            self.gui.load_program(path)       # must not raise
        finally:
            os.unlink(path)
        self.assertEqual(len(self._errors), 1)
        self.assertIn("expected 16 bits", self._errors[0][1])
        # The app stays alive and keeps its previous (demo) state.
        self.assertIsNone(self.gui.cpu)
        self.assertEqual(self.gui.snapshots, [])
        self.assertEqual(self.gui.program_name, "demo (sum 1..10)")

    def test_valid_hack_loads(self):
        fd, path = tempfile.mkstemp(suffix=".hack")
        os.close(fd)
        with open(path, "w", encoding="ascii") as f:
            f.write("0000000000001010\n1110110000010000\n")
        try:
            self.gui.load_program(path)
        finally:
            os.unlink(path)
        self.assertEqual(self._errors, [])
        self.assertEqual(self.gui.program, [0b0000000000001010,
                                            0b1110110000010000])
        self.assertEqual(self.gui.program_name, os.path.basename(path))
        # The newly loaded program can actually run.
        self.gui.on_step()
        self.assertEqual(len(self.gui.snapshots), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
