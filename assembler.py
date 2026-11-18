"""
assembler.py — A two-pass assembler for the Hack assembly language.

Translates .asm text into .hack machine code: one 16-character '0'/'1'
string per line, the same format the Nand2Tetris tools produce.

Instruction formats
-------------------
A-instruction:   @value   or   @symbol      ->  0vvvvvvvvvvvvvvv
C-instruction:   dest=comp;jump   (dest / jump optional)
                 ->  111 a cccccc ddd jjj
Label:           (LABEL)  binds LABEL to the ROM address of the NEXT
                 instruction.  Labels generate no machine code.

Symbols
-------
* Predefined: R0..R15, SP, LCL, ARG, THIS, THAT, SCREEN (16384), KBD (24576).
* Labels, resolved to ROM addresses in pass 1.
* Variables (any other @symbol) are allocated to RAM starting at
  address 16, in order of first use, during pass 2.

Pass 1 scans the program, records every label's ROM address.
Pass 2 translates each instruction to its 16-bit encoding.
"""

import re

from alu import COMP_BITS

# Symbols the hardware defines (Nand2Tetris figure 4.5 / ch. 6).
PREDEFINED_SYMBOLS = {
    "R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5,
    "R6": 6, "R7": 7, "R8": 8, "R9": 9, "R10": 10, "R11": 11,
    "R12": 12, "R13": 13, "R14": 14, "R15": 15,
    "SP": 0, "LCL": 1, "ARG": 2, "THIS": 3, "THAT": 4,
    "SCREEN": 16384, "KBD": 24576,
}

# dest bits d1 d2 d3: d1 = A register, d2 = D register, d3 = M (RAM[A]).
DEST_BITS = {
    "":    "000",
    "M":   "001",
    "D":   "010",
    "MD":  "011",
    "A":   "100",
    "AM":  "101",
    "AD":  "110",
    "AMD": "111",
}

# jump bits j1 j2 j3: j1 = out<0 (JLT), j2 = out==0 (JEQ), j3 = out>0 (JGT).
JUMP_BITS = {
    "":    "000",
    "JGT": "001",
    "JEQ": "010",
    "JGE": "011",
    "JLT": "100",
    "JNE": "101",
    "JLE": "110",
    "JMP": "111",
}

# Labels/symbols: may not start with a digit (Nand2Tetris ch. 6 convention).
LABEL_RE = re.compile(r"^\(([A-Za-z_.$:][A-Za-z0-9_.$:]*)\)$")
SYMBOL_RE = re.compile(r"^[A-Za-z_.$:][A-Za-z0-9_.$:]*$")
NUMBER_RE = re.compile(r"^[0-9]+$")

# dest is optional ("dest="), jump is optional (";jump").
# Order in the alternation matters: AMD before AD/AM/MD before A/D/M.
C_INSTRUCTION_RE = re.compile(
    r"^(?:(?P<dest>AMD|AD|AM|MD|A|D|M)=)?"
    r"(?P<comp>[^=;]+?)"
    r"(?:;(?P<jump>JGT|JEQ|JGE|JLT|JNE|JLE|JMP))?$"
)


class AsmError(ValueError):
    """Assembly error, with the offending source line for context."""

    def __init__(self, lineno, line, message):
        super().__init__(f"line {lineno}: {message}: {line!r}")


def strip_line(raw):
    """Remove a // comment and surrounding whitespace. '' if nothing remains."""
    return raw.split("//", 1)[0].strip()


def parse_c_instruction(code, lineno):
    """Split a C-instruction into (dest, comp, jump); validate comp."""
    match = C_INSTRUCTION_RE.match(code)
    if not match:
        raise AsmError(lineno, code, "not a valid C-instruction")
    dest = match.group("dest") or ""
    comp = match.group("comp").strip()
    jump = match.group("jump") or ""
    if comp not in COMP_BITS:
        raise AsmError(lineno, code, f"unknown comp {comp!r}")
    return dest, comp, jump


def assemble_source(source, source_name="<source>"):
    """Assemble Hack assembly text.

    Returns (machine_code, symbols) where machine_code is a list of
    16-bit ints and symbols maps every symbol/label to its address.
    """
    # Collect non-empty code lines once, keeping original line numbers
    # so error messages point at the real source line.
    lines = []
    for lineno, raw in enumerate(source.splitlines(), start=1):
        code = strip_line(raw)
        if code:
            lines.append((lineno, code))

    symbols = dict(PREDEFINED_SYMBOLS)

    # ---------- Pass 1: labels -> ROM addresses ----------
    rom_addr = 0
    for lineno, code in lines:
        match = LABEL_RE.match(code)
        if match:
            label = match.group(1)
            if label in symbols:
                raise AsmError(lineno, code, f"label {label!r} defined twice")
            symbols[label] = rom_addr
        else:
            rom_addr += 1

    # ---------- Pass 2: translate instructions ----------
    machine_code = []
    next_free_ram = 16          # first RAM address available for variables
    for lineno, code in lines:
        if LABEL_RE.match(code):
            continue            # labels emit nothing
        if code.startswith("@"):
            operand = code[1:].strip()
            if not operand:
                raise AsmError(lineno, code, "empty A-instruction operand")
            if NUMBER_RE.match(operand):
                value = int(operand)
                if value > 0x7FFF:
                    raise AsmError(lineno, code,
                                   "A-instruction constant must fit in 15 bits (0..32767)")
            elif SYMBOL_RE.match(operand):
                if operand in symbols:
                    value = symbols[operand]
                else:
                    value = next_free_ram   # new variable -> allocate RAM
                    symbols[operand] = value
                    next_free_ram += 1
            else:
                raise AsmError(lineno, code, f"invalid symbol {operand!r}")
            machine_code.append(value)  # top bit stays 0 => A-instruction
        else:
            dest, comp, jump = parse_c_instruction(code, lineno)
            a_bit, comp_bits = COMP_BITS[comp]
            word = (
                (0b111 << 13)                     # C-instruction marker
                | (a_bit << 12)
                | (int(comp_bits, 2) << 6)        # c1..c6
                | (int(DEST_BITS[dest], 2) << 3)  # d1 d2 d3
                | int(JUMP_BITS[jump], 2)         # j1 j2 j3
            )
            machine_code.append(word)
    return machine_code, symbols


def assemble_file(input_path, output_path=None):
    """Assemble a .asm file and write the .hack file.

    Returns (machine_code, symbols, hack_path).
    """
    with open(input_path, "r", encoding="utf-8") as f:
        source = f.read()
    machine_code, symbols = assemble_source(source, source_name=input_path)
    if output_path is None:
        output_path = re.sub(r"\.asm$", "", input_path) + ".hack"
    with open(output_path, "w", encoding="ascii") as f:
        for word in machine_code:
            f.write(f"{word:016b}\n")
    return machine_code, symbols, output_path


if __name__ == "__main__":
    # Self-test: canonical encodings from the Nand2Tetris book, labels,
    # variable allocation, and error handling.
    program = """
        // canonical encodings
        @10        // 0000000000001010
        D=A        // 1110110000010000
        @R2        // predefined symbol
        M=D        // 1110001100001000
        @sum       // variable -> RAM[16]
        M=0        // 1110101010001000
(LOOP)             // label at ROM address 6
        @sum
        D=M        // 1111110000100000
        @LOOP
        D;JGT      // 1110001100000001
        @LOOP
        0;JMP      // 1110101010000111
"""
    code, syms = assemble_source(program)
    assert code[0] == 0b0000000000001010, code[0]
    assert code[1] == 0b1110110000010000, code[1]
    assert code[2] == 0b0000000000000010, code[2]      # @R2
    assert code[3] == 0b1110001100001000, code[3]      # M=D
    assert code[4] == 0b0000000000010000, code[4]      # @sum -> RAM[16]
    assert code[5] == 0b1110101010001000, code[5]      # M=0
    assert code[6] == 0b0000000000010000, code[6]      # @sum
    assert code[7] == 0b1111110000010000, code[7]      # D=M (a=1: comp=M)
    assert code[8] == 0b0000000000000110, code[8]      # @LOOP -> 6
    assert code[9] == 0b1110001100000001, code[9]      # D;JGT
    assert code[10] == 0b0000000000000110, code[10]    # @LOOP
    assert code[11] == 0b1110101010000111, code[11]    # 0;JMP
    assert syms["LOOP"] == 6 and syms["sum"] == 16

    # Error cases must raise AsmError with line numbers.
    for bad in ("D=F00\n", "@(BAD\n", "X=Y;JMP\n"):
        try:
            assemble_source(bad)
        except AsmError:
            pass
        else:
            raise AssertionError(f"expected AsmError for {bad!r}")
    print("assembler.py self-test passed: encodings, labels, variables, errors.")
