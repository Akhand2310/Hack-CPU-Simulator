"""
hack_cpu.py — Cycle-accurate simulator of the Hack CPU (Nand2Tetris).

The Hack CPU (book figure 5.9) is wired as:

    * A register  -- feeds the ALU's y input when a-bit == 0, holds the
                     jump target for C-instructions, receives the 15-bit
                     constant of A-instructions, and addresses RAM for
                     M operations.
    * D register  -- always the ALU's x input.
    * ALU         -- out = f(D, A or M); flags zr (out == 0) and
                     ng (out < 0) drive the jump logic.
    * PC          -- next ROM address: increment, or jump-load on the
                     clock edge.

Clock semantics implemented by step():
    1. Combinational logic settles using the CURRENT register values:
       decode ROM[PC], compute the ALU, decide the jump.
    2. On the clock edge, all destinations latch simultaneously:
       A (dest bit d1 or A-instruction load), D (d2), RAM[A] (d3,
       addressed by the OLD A value), and PC (increment or jump load).
"""

from alu import alu, MASK16

RAM_SIZE = 24577          # addresses 0..24576 (RAM16K + screen + keyboard)


class HackCPU:
    """A cycle-accurate software model of the Hack CPU + memories."""

    def __init__(self, program):
        """program: list of 16-bit ints (the assembled .hack contents)."""
        if len(program) > 32768:
            raise ValueError("program too large for ROM (max 32768 instructions)")
        self.rom = list(program)
        # ---- state registers (latched on each clock edge) ----
        self.pc = 0
        self.a = 0
        self.d = 0
        self.ram = [0] * RAM_SIZE
        # ---- signals of the most recent cycle (for waveforms) ----
        self.instruction = 0
        self.alu_out = 0
        self.alu_zr = False
        self.alu_ng = False
        self.write_m = False
        self.out_m = 0
        self.address_m = 0
        self.cycles_run = 0

    # ------------------------------------------------------------------
    # Memory helpers
    # ------------------------------------------------------------------
    def _read_m(self, address):
        if not 0 <= address < RAM_SIZE:
            raise IndexError(f"RAM address {address} out of range 0..{RAM_SIZE - 1}")
        return self.ram[address]

    def _write_m(self, address, value):
        if not 0 <= address < RAM_SIZE:
            raise IndexError(f"RAM address {address} out of range 0..{RAM_SIZE - 1}")
        self.ram[address] = value & MASK16

    # ------------------------------------------------------------------
    # Combinational decode of the instruction at ROM[pc]
    # ------------------------------------------------------------------
    def decode(self):
        """Decode the instruction the PC currently points at.

        Returns a dict of decode fields computed from *current* state,
        before any register latches.  Raises IndexError if the PC has
        run past the end of the loaded program.
        """
        pc = self.pc
        if not 0 <= pc < len(self.rom):
            raise IndexError(
                f"PC={pc} is outside the loaded program (0..{len(self.rom) - 1}). "
                "Did the program run off the end?  Use --cycles to bound execution "
                "or end your program with an infinite loop (e.g. @END / 0;JMP).")
        instruction = self.rom[pc]

        if not (instruction & 0x8000):
            # ---- A-instruction: 0 vvvvvvvvvvvvvvv ----
            return {
                "kind": "A",
                "instruction": instruction,
                "a_load_value": instruction & 0x7FFF,
            }

        # ---- C-instruction: 111 a cccccc ddd jjj ----
        a_bit = (instruction >> 12) & 1
        comp_bits = f"{(instruction >> 6) & 0b111111:06b}"
        dest_bits = (instruction >> 3) & 0b111
        jump_bits = instruction & 0b111

        # Map the raw comp field back to its mnemonic via the single
        # source of truth in alu.py, then get its control bits.
        try:
            mnemonic = _COMP_FIELD_TO_MNEMONIC[(a_bit, comp_bits)]
        except KeyError:
            raise ValueError(
                f"undefined comp field a={a_bit} c={comp_bits} at PC={pc} "
                f"(instruction {instruction:#018b})") from None
        a, zx, nx, zy, ny, f, no = comp_to_control(mnemonic)

        x = self.d                                        # x input: D register
        y = self.a if a == 0 else self._read_m(self.a)    # y input: A or M
        out, zr, ng = alu(x, y, zx, nx, zy, ny, f, no)

        # Jump decision (j1=JLT, j2=JEQ, j3=JGT): jump if any requested
        # condition matches the ALU flags; JMP (111) always jumps.
        jump = bool(
            (jump_bits & 0b001 and not ng and not zr)     # JGT
            or (jump_bits & 0b010 and zr)                 # JEQ
            or (jump_bits & 0b100 and ng)                 # JLT
            or jump_bits == 0b111                         # JMP
        )

        return {
            "kind": "C",
            "instruction": instruction,
            "mnemonic": mnemonic,
            "dest_bits": dest_bits,
            "jump": jump,
            "out": out,
            "zr": zr,
            "ng": ng,
        }

    # ------------------------------------------------------------------
    # One clock cycle
    # ------------------------------------------------------------------
    def step(self):
        """Execute one full clock cycle (settle combinational, latch)."""
        decoded = self.decode()
        self.instruction = decoded["instruction"]

        if decoded["kind"] == "A":
            # A-instruction: A loads the 15-bit constant; PC increments.
            self.address_m = self.a    # A's old value is what RAM saw this cycle
            self.a = decoded["a_load_value"]
            self.write_m = False
            self.out_m = 0
            self.alu_out = 0
            self.alu_zr = self.alu_ng = False
            self.pc = (self.pc + 1) & MASK16
        else:
            out = decoded["out"]
            old_a = self.a             # RAM is addressed by the OLD A value
            d1 = (decoded["dest_bits"] >> 2) & 1   # dest A
            d2 = (decoded["dest_bits"] >> 1) & 1   # dest D
            d3 = decoded["dest_bits"] & 1          # dest M

            self.alu_out = out
            self.alu_zr = decoded["zr"]
            self.alu_ng = decoded["ng"]
            self.write_m = bool(d3)
            self.address_m = old_a
            self.out_m = out

            # All destinations latch on the same clock edge:
            if d1:
                self.a = out
            if d2:
                self.d = out
            if d3:
                self._write_m(old_a, out)

            # PC: a jump load overrides the increment (load=True, inc=False).
            if decoded["jump"]:
                self.pc = self.a
            else:
                self.pc = (self.pc + 1) & MASK16

        self.cycles_run += 1
        return self.take_snapshot()

    def run(self, max_cycles):
        """Run up to max_cycles steps; stop early at a reset (PC == 0).

        A well-formed Hack program ends by jumping to itself or to ROM[0];
        treating PC == 0 as "program finished/idling" keeps infinite
        programs from consuming all --cycles in the waveform.
        """
        snapshots = []
        for _ in range(max_cycles):
            snapshots.append(self.step())
            if self.pc == 0:
                break
        return snapshots

    # ------------------------------------------------------------------
    # Signal exposure (consumed by the VCD writer)
    # ------------------------------------------------------------------
    def take_snapshot(self):
        """Current values of every signal worth showing on a waveform.

        clk is the post-edge clock level: 1 right after the rising edge.
        The VCD writer reconstructs both edges from its own time model.
        """
        return {
            "cycle": self.cycles_run,
            "clk": 1,
            "pc": self.pc,
            "a": self.a,
            "d": self.d,
            "alu": self.alu_out,
            "zr": self.alu_zr,
            "ng": self.alu_ng,
            "instruction": self.instruction,
            "address_m": self.address_m,
            "out_m": self.out_m,
            "write_m": self.write_m,
            "ram0": self.ram[0],          # SP — handy to watch
        }


# ----------------------------------------------------------------------
# Reverse lookup: (a-bit, comp field) -> mnemonic, built once at import
# from the single source of truth in alu.COMP_BITS.  Lets decode()
# translate any raw 7-bit comp field without duplicating the table.
# ----------------------------------------------------------------------
def _build_comp_field_lookup():
    from alu import COMP_BITS
    lookup = {}
    for mnemonic, (a_bit, bits) in COMP_BITS.items():
        key = (a_bit, bits)
        if key in lookup:
            raise AssertionError(f"duplicate comp encoding for {mnemonic}")
        lookup[key] = mnemonic
    return lookup


_COMP_FIELD_TO_MNEMONIC = _build_comp_field_lookup()


def comp_to_control(mnemonic):
    """(a, zx, nx, zy, ny, f, no) for a comp mnemonic (thin re-export)."""
    from alu import comp_to_control as _c
    return _c(mnemonic)


if __name__ == "__main__":
    # Self-test 1: run the classic sum 1..10 program (raw machine code,
    # same program as examples/sum1to10.asm) and check the result.
    program = [
        0b0000000000001010,        #  0: @10
        0b1110110000010000,        #  1: D=A
        0b0000000000010000,        #  2: @16   (n = RAM[16])
        0b1110001100001000,        #  3: M=D       n = 10
        0b0000000000010001,        #  4: @17   (i = RAM[17])
        0b1110101010001000,        #  5: M=0       i = 0
        0b0000000000010010,        #  6: @18   (sum = RAM[18])
        0b1110101010001000,        #  7: M=0       sum = 0
        0b0000000000010001,        #  8: LOOP: @17
        0b1111110111001000,        #  9: M=M+1     i += 1
        0b0000000000010001,        # 10: @17
        0b1111110000010000,        # 11: D=M       D = i
        0b0000000000010010,        # 12: @18
        0b1111000010001000,        # 13: M=D+M     sum += i
        0b0000000000010001,        # 14: @17
        0b1111110000010000,        # 15: D=M       D = i
        0b0000000000010000,        # 16: @16
        0b1111010011010000,        # 17: D=D-M     D = i - n
        0b0000000000010110,        # 18: @22   (END)
        0b1110001100000010,        # 19: D;JEQ     leave loop when i == n
        0b0000000000001000,        # 20: @8    (LOOP)
        0b1110101010000111,        # 21: 0;JMP
        0b0000000000010010,        # 22: END: @18
        0b1111110000010000,        # 23: D=M       D = sum
        0b0000000000000000,        # 24: @0    (R0)
        0b1110001100001000,        # 25: M=D       R0 = 55
        0b0000000000011010,        # 26: FIN: @26
        0b1110101010000111,        # 27: 0;JMP     idle: PC toggles 26 <-> 27
    ]
    cpu = HackCPU(program)
    snapshots = cpu.run(152)
    assert cpu.ram[16] == 10, f"n should stay 10, got {cpu.ram[16]}"
    assert cpu.ram[17] == 10, f"i should end at 10, got {cpu.ram[17]}"
    assert cpu.ram[18] == 55, f"sum should be 55, got {cpu.ram[18]}"
    assert cpu.ram[0] == 55, f"R0 should be 55, got {cpu.ram[0]}"
    assert cpu.pc == 26, f"CPU should idle at FIN (PC=26), got {cpu.pc}"
    assert len(snapshots) == 152, f"expected 152 cycles, got {len(snapshots)}"

    # Self-test 2: AM=D must write RAM[OLD A] while A latches the new value.
    cpu2 = HackCPU([
        0b0000000000000101,        # 0: @5
        0b1110110000010000,        # 1: D=A
        0b0000000000010011,        # 2: @19    (address for AM=D)
        0b1110001100101000,        # 3: AM=D   ; A<=5, RAM[19]<=5
        0b0000000000000000,        # 4: @0
        0b1110101010000111,        # 5: 0;JMP  -> PC=0 (would stop the run)
    ])
    for _ in range(4):
        snap = cpu2.step()         # execute cycles 0..3 (up to and incl. AM=D)
    assert cpu2.a == 5, f"A should be 5 after AM=D, got {cpu2.a}"
    assert cpu2.ram[19] == 5, f"RAM[19] should be 5, got {cpu2.ram[19]}"
    assert snap["address_m"] == 19 and snap["write_m"], \
        "AM=D must write RAM at the OLD A value (19)"
    print("hack_cpu.py self-test passed: sum 1..10 = 55 in 152 cycles; AM=D writes old-A address.")
