"""
alu.py — The Hack ALU (Arithmetic Logic Unit), modeled in software.

This mirrors the ALU chip built in Nand2Tetris project 2: a purely
combinational 16-bit unit controlled by six 1-bit "control bits". It
computes one of 18 predefined functions of its inputs x and y and
reports two status flags:

    zr  out == 0
    ng  out < 0   (sign bit set, i.e. two's-complement negative)

All arithmetic is 16-bit two's-complement: values wrap modulo 2^16,
so e.g. 0xFFFF represents -1.

Inside the Hack CPU the ALU inputs are always wired as:
    x = D register
    y = A register (a-bit = 0)  or  RAM[A] / "M"  (a-bit = 1)

The six control bits appear in the C-instruction's comp field, in
this order (c1..c6):

    zx  zero the x input
    nx  negate (bitwise NOT) the x input   (applied after zx)
    zy  zero the y input
    ny  negate (bitwise NOT) the y input   (applied after zy)
    f   1 -> out = x + y,  0 -> out = x & y   (after the transforms above)
    no  negate (bitwise NOT) the output    (applied last)
"""

MASK16 = 0xFFFF


def to_signed(value):
    """Interpret a 16-bit pattern as a signed two's-complement number."""
    value &= MASK16
    return value - 0x10000 if value & 0x8000 else value


def alu(x, y, zx, nx, zy, ny, f, no):
    """
    Compute the Hack ALU function.

    x, y   : 16-bit input values (ints; only the low 16 bits are used)
    zx..no : 0/1 control bits (see module docstring)

    Returns (out, zr, ng) where out is a 16-bit int and zr/ng are bools.
    """
    x &= MASK16
    y &= MASK16
    if zx:
        x = 0
    if nx:
        x = ~x & MASK16          # ~x == -x-1 in two's complement
    if zy:
        y = 0
    if ny:
        y = ~y & MASK16
    out = (x + y) if f else (x & y)
    if no:
        out = ~out
    out &= MASK16
    return out, out == 0, bool(out & 0x8000)


# comp mnemonic -> (a-bit, c1..c6 control bits)
# a = 0 means the ALU's y input is the A register;
# a = 1 means it is Memory[A] ("M").  The six control bits are zx nx zy ny f no.
COMP_BITS = {
    # ---- a = 0: y is the A register ----
    "0":    (0, "101010"),
    "1":    (0, "111111"),
    "-1":   (0, "111010"),
    "D":    (0, "001100"),
    "A":    (0, "110000"),
    "!D":   (0, "001101"),
    "!A":   (0, "110001"),
    "-D":   (0, "001111"),
    "-A":   (0, "110011"),
    "D+1":  (0, "011111"),
    "A+1":  (0, "110111"),
    "D-1":  (0, "001110"),
    "A-1":  (0, "110010"),
    "D+A":  (0, "000010"),
    "D-A":  (0, "010011"),
    "A-D":  (0, "000111"),
    "D&A":  (0, "000000"),
    "D|A":  (0, "010101"),
    # ---- a = 1: y is M (RAM[A]) ----
    "M":    (1, "110000"),
    "!M":   (1, "110001"),
    "-M":   (1, "110011"),
    "M+1":  (1, "110111"),
    "M-1":  (1, "110010"),
    "D+M":  (1, "000010"),
    "D-M":  (1, "010011"),
    "M-D":  (1, "000111"),
    "D&M":  (1, "000000"),
    "D|M":  (1, "010101"),
}


def comp_to_control(comp_mnemonic):
    """Return (a, zx, nx, zy, ny, f, no) for a comp mnemonic.

    Raises ValueError for an unknown mnemonic.
    """
    try:
        a_bit, bits = COMP_BITS[comp_mnemonic]
    except KeyError:
        raise ValueError(f"unknown comp mnemonic: {comp_mnemonic!r}") from None
    return (a_bit, int(bits[0]), int(bits[1]), int(bits[2]),
            int(bits[3]), int(bits[4]), int(bits[5]))


if __name__ == "__main__":
    # Self-test: the control bits must reproduce each mnemonic's meaning
    # on a spread of inputs, including negatives and boundary patterns.
    expected = {
        "0":    lambda x, y: 0,
        "1":    lambda x, y: 1,
        "-1":   lambda x, y: -1,
        "D":    lambda x, y: x,
        "A":    lambda x, y: y,
        "!D":   lambda x, y: ~x,
        "!A":   lambda x, y: ~y,
        "-D":   lambda x, y: -x,
        "-A":   lambda x, y: -y,
        "D+1":  lambda x, y: x + 1,
        "A+1":  lambda x, y: y + 1,
        "D-1":  lambda x, y: x - 1,
        "A-1":  lambda x, y: y - 1,
        "D+A":  lambda x, y: x + y,
        "D-A":  lambda x, y: x - y,
        "A-D":  lambda x, y: y - x,
        "D&A":  lambda x, y: x & y,
        "D|A":  lambda x, y: x | y,
    }
    samples = [(53, 7), (0, 0), (0xFFFF, 1), (0x8000, 0xFFFF), (0x1234, 0x4321)]
    for mnemonic, fn in expected.items():
        for x, y in samples:
            out, zr, ng = alu(x, y, *comp_to_control(mnemonic)[1:])
            want = fn(x, y) & MASK16
            assert out == want, (
                f"{mnemonic}({x:#06x},{y:#06x}) -> {out:#06x}, want {want:#06x}")
            # Flags must be consistent with the output itself.
            assert zr == (out == 0) and ng == bool(out & 0x8000)
    print("alu.py self-test passed: all 18 ALU functions verified on 5 input pairs.")
