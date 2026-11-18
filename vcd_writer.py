"""
vcd_writer.py — Write a Value Change Dump (.vcd) file from simulation data.

Produces standard-compliant VCD text (IEEE 1364-2005 ch. 18, the format
GTKWave reads): header with $date/$version/$timescale, one $scope with
$var declarations, then timestamped value changes.  Only signals whose
value changed since the previous timestamp are re-emitted — standard VCD
practice, and it keeps files small.

Time model
----------
The clock toggles every half period; registers change on the rising
edge.  One simulation cycle occupies one 10ns period:
    * 0ns   — initial state (all signals at reset values)
    * +5ns  — rising edge: registers latch this cycle's results (clk=1)
    * +10ns — falling edge: clk=0, data stable until the next edge

So GTKWave's cursor reads "cycle = time / 10".

Signals emitted (fixed order, matching how you'd group them in a viewer):
    clk (1 bit); pc, A, D, ALU, instruction, addressM, outM, ram0
    (16 bit each); zr, ng, writeM (1 bit each).
"""

from datetime import datetime, timezone

TIME_UNIT_NS = 10          # one simulation cycle == 10 ns of VCD time
HALF_PERIOD_NS = 5         # rising edge at +5, falling at +10

# (identifier, name, width) — identifiers are the short unique codes the
# value-change lines reference; single chars keep the VCD compact.
SIGNALS = [
    ("c", "clk", 1),
    ("p", "pc", 16),
    ("a", "A", 16),
    ("d", "D", 16),
    ("u", "ALU", 16),
    ("i", "instruction", 16),
    ("m", "addressM", 16),
    ("o", "outM", 16),
    ("z", "zr", 1),
    ("n", "ng", 1),
    ("w", "writeM", 1),
    ("r", "ram0", 16),
]


def _change_line(value, width, sid):
    """One complete VCD value-change line.

    Scalars (1 bit) are '<value><id>' with NO space (e.g. '1c');
    vectors are 'b<bits> <id>' (e.g. 'b0000000000001010 a').
    """
    if width == 1:
        return f"{1 if value else 0}{sid}"
    return f"b{value & ((1 << width) - 1):0{width}b} {sid}"


def write_vcd(path, snapshots, comment=None):
    """
    Write a .vcd file from per-cycle signal snapshots.

    snapshots: iterable of dicts; each must contain the value keys named
    in SIGNALS: 'clk', 'pc', 'a', 'd', 'alu', 'instruction', 'address_m',
    'out_m', 'zr', 'ng', 'write_m', 'ram0' (the keys HackCPU.take_snapshot
    produces).  The 'cycle' key (1-based) is used only for a comment.
    comment: optional extra $comment text in the header.
    """
    snapshots = list(snapshots)
    lines = []

    # ---------------- header ----------------
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append("$date")
    lines.append(f"    {now}")
    lines.append("$end")
    lines.append("$version")
    lines.append("    Hack CPU Simulator (nand2tetris), vcd_writer.py 1.0")
    lines.append("$end")
    lines.append("$timescale 1ns $end")
    if comment:
        lines.append("$comment")
        lines.append(f"    {comment}")
        lines.append("$end")
    lines.append("$scope module hack_cpu $end")
    for sid, name, width in SIGNALS:
        lines.append(f"$var wire {width} {sid} {name} $end")
    lines.append("$upscope $end")
    lines.append("$enddefinitions $end")

    # ---------------- value changes ----------------
    # prev holds each signal's last emitted value; None means "never
    # emitted", forcing a full dump at time 0 (required by the format).
    prev = {sid: None for sid, _n, _w in SIGNALS}
    t = 0

    def emit_changes(at_time, current):
        """Append '#time' plus one line per changed signal."""
        nonlocal prev
        lines.append(f"#{at_time}")
        for sid, _name, width in SIGNALS:
            value = int(bool(current[sid])) if width == 1 else current[sid]
            if prev[sid] is None or value != prev[sid]:
                lines.append(_change_line(value, width, sid))
        prev = dict(current)

    def emit_edge(at_time, current, clock_level):
        """One clock edge: clk always emitted, other signals on change."""
        nonlocal prev
        with_clk = dict(current)
        with_clk["c"] = clock_level
        lines.append(f"#{at_time}")
        for sid, _name, width in SIGNALS:
            value = int(bool(with_clk[sid])) if width == 1 else with_clk[sid]
            if sid == "c" or prev[sid] is None or value != prev[sid]:
                lines.append(_change_line(value, width, sid))
        prev = dict(with_clk)

    # ---- time 0: reset state of the CPU, before the first edge ----
    initial = {sid: 0 for sid, _n, _w in SIGNALS}
    emit_changes(t, initial)

    for snap in snapshots:
        # Rising edge at t+5ns: registers latch this cycle's results.
        t += HALF_PERIOD_NS
        rising = {
            "c": 1,
            "p": snap["pc"],
            "a": snap["a"],
            "d": snap["d"],
            "u": snap["alu"],
            "i": snap["instruction"],
            "m": snap["address_m"],
            "o": snap["out_m"],
            "z": snap["zr"],
            "n": snap["ng"],
            "w": snap["write_m"],
            "r": snap["ram0"],
        }
        emit_edge(t, rising, clock_level=1)

        # Falling edge at t+5ns more: only the clock changes.
        t += HALF_PERIOD_NS
        lines.append(f"#{t}")
        lines.append("0c")
        prev["c"] = 0

    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    # Self-test: a tiny 2-cycle run must produce parseable VCD structure.
    import tempfile, os, re

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
    write_vcd(tmp, snaps)
    with open(tmp) as f:
        text = f.read()
    # Structural checks a real VCD parser would make.
    assert "$timescale 1ns $end" in text
    assert "$var wire 1 c clk $end" in text
    assert "$var wire 16 p pc $end" in text
    assert text.index("$scope") < text.index("$enddefinitions")
    assert text.index("$enddefinitions") < text.index("#5")
    for stamp in ("#0", "#5", "#10", "#15", "#20"):
        assert f"\n{stamp}\n" in text, f"missing timestamp {stamp}"
    assert re.search(r"^b0000000000001010 i$", text, re.M)   # A-instruction logged
    assert re.search(r"^1c$", text, re.M) and re.search(r"^0c$", text, re.M)
    # Clock must toggle on every edge: 1c at #5, 0c at #10, 1c at #15...
    assert re.search(r"#5\n1c\n", text) and re.search(r"#10\n0c\n", text)
    # D register only emitted when it changes (cycle 1 D=0 already dumped at t0;
    # A stays 10 from cycle 1 to cycle 2, so at #15 it must NOT reappear)
    assert re.search(r"#15\n1c\nb0000000000000010 p\nb0000000000001010 d\n", text)
    assert text.count("b0000000000001010 a") == 1
    os.unlink(tmp)
    print("vcd_writer.py self-test passed: header, vars, edges, change-only logging OK.")
