"""
gui.py — Desktop GUI for the Hack CPU simulator (Tkinter, standard library).

Loads a .asm (assembled with assembler.py) or a raw .hack file, simulates
it with hack_cpu.py, and shows the signals (clk, PC, A, D, ALU, flags,
memory) as timing-diagram waveforms on a Tkinter canvas:

    * Run / Step / Auto-step / Reset controls
    * Zoom (wheel, +/- keys, buttons, drag-box), pan by dragging
    * Hover cursor with a per-signal value tooltip
    * Click a signal name to hide/show its lane
    * Export the recorded cycles to a .vcd file

Time model matches vcd_writer.py: one cycle == 10 ns, rising edge at
+5 ns.  Snapshots are cycle-end (latched) values, so the waveform shows
PC marching and jumping exactly like the exported VCD does.

Run:    python gui.py                (starts with the built-in demo)
        python gui.py program.asm    (or a .hack file)
        python gui.py --selftest     (headless logic self-test)
Build:  see build_exe.bat / build_exe.sh (PyInstaller, onefile windowed).
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from assembler import AsmError, assemble_source
from hack_cpu import HackCPU
from main import load_hack_file          # shared .hack parser (validates input)
from vcd_writer import TIME_UNIT_NS, HALF_PERIOD_NS, write_vcd

# ----------------------------------------------------------------------
# Theme (dark, GTKWave-ish)
# ----------------------------------------------------------------------
BG = "#0f1117"          # canvas / app background
PANEL = "#151926"       # side panels
FG = "#d8dde6"          # normal text
MUTED = "#8a93a6"       # dimmed text
GRID = "#232a3b"        # lane grid lines
ACCENT = "#7aa2f7"      # cursor / selection
BTN = "#1c2233"
BTN_ACTIVE = "#2a3350"

# One colour per signal lane, indexed like GUI_SIGNALS below.
LANE_COLORS = [
    "#e8c268",  # clk
    "#7aa2f7",  # pc
    "#89ddff",  # A
    "#c3e88d",  # D
    "#f78c6c",  # ALU
    "#ff5370",  # instruction
    "#c792ea",  # addressM
    "#82aaff",  # outM
    "#e8c268",  # zr
    "#e8c268",  # ng
    "#f07178",  # writeM
    "#4dd0b1",  # ram0
]

# (display name, snapshot key, width in bits) -- fixed order, top to bottom.
GUI_SIGNALS = [
    ("clk", "clk", 1),
    ("pc", "pc", 16),
    ("A", "a", 16),
    ("D", "d", 16),
    ("ALU", "alu", 16),
    ("instruction", "instruction", 16),
    ("addressM", "address_m", 16),
    ("outM", "out_m", 16),
    ("zr", "zr", 1),
    ("ng", "ng", 1),
    ("writeM", "write_m", 1),
    ("ram0", "ram0", 16),
]

# Program the GUI starts with (the sum 1..10 end-to-end demo).
DEMO_PROGRAM = """\
// Demo: 1 + 2 + ... + 10 -> R0 = 55, then idle at (FIN).
@10
D=A
@n
M=D            // n = 10
@i
M=0            // i = 0
@sum
M=0            // sum = 0
(LOOP)
@i
M=M+1          // i += 1
@i
D=M            // D = i
@sum
M=D+M          // sum += i
@i
D=M
@n
D=D-M          // D = i - n
@END
D;JEQ          // leave when i == n
@LOOP
0;JMP
(END)
@sum
D=M
@R0
M=D            // R0 = 55
(FIN)
@FIN
0;JMP          // idle: PC toggles FIN <-> FIN+1
"""


# ----------------------------------------------------------------------
# Sampling helpers (independent of Tk so --selftest can exercise them)
# ----------------------------------------------------------------------
def signed16(value):
    """16-bit two's-complement value -> signed int (for tooltips)."""
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def format_value(name, value):
    """Human-readable value for a signal (tooltips and the log)."""
    if name == "clk":
        return "rising" if value else "falling"
    if name in ("zr", "ng", "writeM"):
        return "1" if value else "0"
    u = value & 0xFFFF
    s = signed16(u)
    if s == u:
        return str(u)
    return "%d (0x%04X)" % (s, u)


def sample_at(times, values, t):
    """Signal value at time t (step function: last change at or before t).

    times/values are parallel lists of change times and values; times
    past the end return the last value (VCD signals extend to infinity),
    and times before the first change return 0 (reset value).
    """
    if not times:
        return 0
    lo, hi = 0, len(times)
    while lo < hi:                      # rightmost index with times[i] <= t
        mid = (lo + hi) // 2
        if times[mid] <= t:
            lo = mid + 1
        else:
            hi = mid
    return values[lo - 1] if lo > 0 else 0


# ----------------------------------------------------------------------
# The application
# ----------------------------------------------------------------------
class HackSimGUI:
    """Tkinter front-end: load, simulate, waveforms, VCD export."""

    def __init__(self, root, program_path=None):
        self.root = root
        root.title("Hack CPU Simulator - waveform viewer")
        root.configure(bg=BG)
        root.geometry("1080x680")

        # ---- simulation state ----
        self.program = None          # list of 16-bit ints
        self.program_name = None
        self.cpu = None              # HackCPU, created on first Run/Step
        self.snapshots = []          # one dict per executed cycle
        self.times_by_row = [[] for _ in GUI_SIGNALS]   # change times per lane
        self.values_by_row = [[] for _ in GUI_SIGNALS]
        self.changed = [False] * len(GUI_SIGNALS)
        self.visible = [True] * len(GUI_SIGNALS)
        self.auto_after_id = None
        self.px_per_ns = 2.0
        self.t_start = 0.0
        self._drag_start = None      # x pixel where a zoom drag began
        self._drag_now = None
        self._hover_line = None
        self._tooltip_id = None
        self._redraw_scheduled = False

        self._build_widgets()
        root.bind("<Escape>", lambda e: self.fit_view())
        root.bind("<plus>", lambda e: self.zoom(1.25))
        root.bind("<equal>", lambda e: self.zoom(1.25))
        root.bind("<minus>", lambda e: self.zoom(0.8))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        if program_path:
            self.load_program(program_path)
        else:
            self.load_demo()

    # ------------------------------------------------------------------
    # Widget layout
    # ------------------------------------------------------------------
    def _mkbtn(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, bg=BTN, fg=FG,
                         activebackground=BTN_ACTIVE, activeforeground=FG,
                         relief=tk.FLAT, padx=8)

    def _build_widgets(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(6, 2))

        self.btn_load = self._mkbtn(top, "Load .asm/.hack...", self.on_load)
        self.btn_demo = self._mkbtn(top, "Demo", self.load_demo)
        self.btn_run = self._mkbtn(top, "Run", self.on_run)
        self.btn_step = self._mkbtn(top, "Step >", self.on_step)
        self.btn_auto = self._mkbtn(top, "> Auto", self.on_auto)
        self.btn_reset = self._mkbtn(top, "Reset", self.on_reset)
        self.cycles_var = tk.StringVar(value="200")
        self.cycles_entry = tk.Entry(top, textvariable=self.cycles_var, width=6,
                                     bg=PANEL, fg=FG, insertbackground=FG,
                                     relief=tk.FLAT, justify=tk.RIGHT)
        lbl_cycles = tk.Label(top, text="cycles", bg=BG, fg=MUTED)
        self.btn_export = self._mkbtn(top, "Export VCD...", self.on_export)
        self.btn_fit = self._mkbtn(top, "Fit", self.fit_view)
        self.btn_zin = self._mkbtn(top, "+", lambda: self.zoom(1.25))
        self.btn_zout = self._mkbtn(top, "-", lambda: self.zoom(0.8))

        for w in (self.btn_load, self.btn_demo, self.btn_run, self.btn_step,
                  self.btn_auto, self.btn_reset, self.cycles_entry, lbl_cycles,
                  self.btn_export, self.btn_fit, self.btn_zin, self.btn_zout):
            w.pack(side=tk.LEFT, padx=3, pady=2)

        main = tk.Frame(self.root, bg=BG)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))

        self.sig_list = tk.Listbox(main, width=13, bg=PANEL, fg=FG,
                                   selectbackground=BTN_ACTIVE,
                                   selectforeground=FG, relief=tk.FLAT,
                                   activestyle="none", exportselection=False)
        for name, _key, _w in GUI_SIGNALS:
            self.sig_list.insert(tk.END, name)
        self.sig_list.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        self.sig_list.bind("<Button-1>", self._on_signal_click)

        self.canvas = tk.Canvas(main, bg=BG, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_wheel)      # Windows / mac
        self.canvas.bind("<Button-4>", self._on_wheel)        # Linux up
        self.canvas.bind("<Button-5>", self._on_wheel)        # Linux down
        self.canvas.bind("<Configure>", lambda e: self.schedule_redraw())

        self.log = scrolledtext.ScrolledText(main, width=36, bg=PANEL, fg=FG,
                                             insertbackground=FG,
                                             relief=tk.FLAT,
                                             font=("TkFixedFont", 9),
                                             wrap="word", state=tk.DISABLED)
        self.log.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        self.status = tk.Label(self.root, text="Ready.", bg=BG, fg=MUTED,
                               anchor=tk.W)
        self.status.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 4))

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def log_line(self, text):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def set_status(self, text):
        self.status.configure(text=text)

    def schedule_redraw(self):
        """Coalesce redraw floods (wheel, resize) into one per idle tick."""
        if not self._redraw_scheduled:
            self._redraw_scheduled = True
            self.root.after_idle(self._do_scheduled_redraw)

    def _do_scheduled_redraw(self):
        self._redraw_scheduled = False
        self.redraw()

    # ------------------------------------------------------------------
    # Program loading
    # ------------------------------------------------------------------
    def load_demo(self):
        self.load_source(DEMO_PROGRAM, "demo (sum 1..10)")

    def on_load(self):
        path = filedialog.askopenfilename(
            title="Load Hack program",
            filetypes=[("Hack programs", "*.asm *.hack"), ("All files", "*.*")])
        if path:
            self.load_program(path)

    def load_program(self, path):
        try:
            if path.endswith(".asm"):
                with open(path, "r", encoding="utf-8") as f:
                    self.load_source(f.read(), path)
            elif path.endswith(".hack"):
                self.load_binary(load_hack_file(path), path)
            else:
                messagebox.showerror("Load",
                                     "Unsupported file type (use .asm or .hack).")
        except OSError as exc:
            messagebox.showerror("Load", "Cannot read %s:\n%s" % (path, exc))
        except ValueError as exc:   # malformed .hack (load_hack_file validates)
            messagebox.showerror("Load", str(exc))

    def load_source(self, text, name):
        """Assemble Hack assembly text and load it (GUI equivalent of main.py)."""
        try:
            machine_code, _symbols = assemble_source(text, source_name=name)
        except AsmError as exc:
            messagebox.showerror("Assembly error", str(exc))
            return
        self.load_binary(machine_code, name)

    def load_binary(self, program, name):
        if not program:
            messagebox.showerror("Load", "%s contains no instructions."
                                 % os.path.basename(name))
            return
        self.program = program
        self.program_name = os.path.basename(name)
        self.stop_auto()
        self.cpu = None
        self.snapshots = []
        self.times_by_row = [[] for _ in GUI_SIGNALS]
        self.values_by_row = [[] for _ in GUI_SIGNALS]
        self.visible = [True] * len(GUI_SIGNALS)
        self._recompute_changed()
        self.fit_view()
        self.log_line("Loaded %s: %d instructions." % (self.program_name,
                                                       len(program)))
        self.set_status("%s: %d instructions. Press Run or Step to simulate."
                        % (self.program_name, len(program)))

    # ------------------------------------------------------------------
    # Simulation control
    # ------------------------------------------------------------------
    def _ensure_cpu(self):
        if self.program is None:
            messagebox.showinfo("No program",
                                "Load a .asm/.hack file first (or press Demo).")
            return False
        if self.cpu is None:
            self.cpu = HackCPU(self.program)
            self.snapshots = []
            self.times_by_row = [[] for _ in GUI_SIGNALS]
            self.values_by_row = [[] for _ in GUI_SIGNALS]
            self._recompute_changed()
            self.log_line("Run started: %s" % self.program_name)
        return True

    def _read_cycles(self):
        try:
            n = int(self.cycles_var.get())
            if n <= 0:
                raise ValueError
            return n
        except ValueError:
            messagebox.showerror("Cycles",
                                 "Enter a positive whole number of cycles.")
            return None

    def on_run(self):
        if not self._ensure_cpu():
            return
        n = self._read_cycles()
        if n is None:
            return
        try:
            new_snaps = self.cpu.run(n)
        except (IndexError, ValueError) as exc:
            messagebox.showerror("Simulation error", str(exc))
            return
        self._absorb_snapshots(new_snaps)
        self.log_line("Ran %d cycles (total %d)." % (len(new_snaps),
                                                     len(self.snapshots)))
        if self.snapshots:
            last = self.snapshots[-1]
            self.log_line("  PC=%d  A=%s  D=%s  R0=%d"
                          % (last["pc"], format_value("A", last["a"]),
                             format_value("D", last["d"]), last["ram0"]))
        self.fit_view()
        self.set_status("Total %d cycles simulated." % len(self.snapshots))

    def on_step(self):
        if not self._ensure_cpu():
            return
        try:
            snap = self.cpu.step()
        except (IndexError, ValueError) as exc:
            messagebox.showerror("Simulation error", str(exc))
            return
        self._absorb_snapshots([snap])
        self.log_line("Step %4d: instr=%s PC=%-6d A=%-6d D=%-6d ALU=%-6d "
                      "writeM=%d"
                      % (snap["cycle"], format(snap["instruction"], "016b"),
                         snap["pc"], snap["a"], snap["d"], snap["alu"],
                         int(snap["write_m"])))
        self.set_status("Cycle %d: PC=%d A=%d D=%d ALU=%d"
                        % (snap["cycle"], snap["pc"], snap["a"],
                           snap["d"], snap["alu"]))
        self.schedule_redraw()

    def on_auto(self):
        if self.auto_after_id is not None:
            self.stop_auto()
            return
        if not self._ensure_cpu():
            return
        self.btn_auto.configure(text="Stop", bg="#5a2a35")
        self._auto_tick()

    def stop_auto(self):
        if self.auto_after_id is not None:
            self.root.after_cancel(self.auto_after_id)
            self.auto_after_id = None
        if hasattr(self, "btn_auto"):
            self.btn_auto.configure(text="> Auto", bg=BTN)

    def _auto_tick(self):
        try:
            snap = self.cpu.step()
        except (IndexError, ValueError) as exc:
            self.stop_auto()
            messagebox.showerror("Simulation error", str(exc))
            return
        self._absorb_snapshots([snap])
        self.set_status("Cycle %d: PC=%d A=%d D=%d ALU=%d"
                        % (snap["cycle"], snap["pc"], snap["a"],
                           snap["d"], snap["alu"]))
        self.schedule_redraw()
        self.auto_after_id = self.root.after(150, self._auto_tick)

    def on_reset(self):
        self.stop_auto()
        self.cpu = None
        self.snapshots = []
        self.times_by_row = [[] for _ in GUI_SIGNALS]
        self.values_by_row = [[] for _ in GUI_SIGNALS]
        self._recompute_changed()
        self.fit_view()
        self.log_line("Reset.")
        self.set_status("Reset - press Run/Step to simulate again.")

    # ------------------------------------------------------------------
    # Snapshot bookkeeping
    # ------------------------------------------------------------------
    def _absorb_snapshots(self, snaps):
        """Append cycles: per-lane change-only (time, value) lists."""
        for snap in snaps:
            self.snapshots.append(snap)
            t_rise = (len(self.snapshots) - 1) * TIME_UNIT_NS + HALF_PERIOD_NS
            for row, (_name, key, width) in enumerate(GUI_SIGNALS):
                value = int(bool(snap[key])) if width == 1 else snap[key] & 0xFFFF
                row_values = self.values_by_row[row]
                if not row_values or row_values[-1] != value:   # change-only
                    self.times_by_row[row].append(t_rise)
                    row_values.append(value)
                    self.changed[row] = True

    def _recompute_changed(self):
        self.changed = [len(t) > 0 for t in self.times_by_row]

    # ------------------------------------------------------------------
    # VCD export
    # ------------------------------------------------------------------
    def on_export(self):
        if not self.snapshots:
            messagebox.showinfo("Export",
                                "Nothing recorded yet - run the CPU first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export waveform", defaultextension=".vcd",
            filetypes=[("VCD waveforms", "*.vcd"), ("All files", "*.*")])
        if not path:
            return
        comment = ("%s: %d instructions, %d cycles simulated (exported from GUI)"
                   % (self.program_name, len(self.program), len(self.snapshots)))
        write_vcd(path, self.snapshots, comment=comment)
        self.log_line("Exported %d cycles -> %s" % (len(self.snapshots), path))
        self.set_status("Exported %s - opens in GTKWave or viewer.html." % path)


    # ------------------------------------------------------------------
    # Waveform drawing
    # ------------------------------------------------------------------
    def fit_view(self):
        if not self.snapshots:
            self.px_per_ns = 2.0
            self.t_start = 0.0
        else:
            span_ns = len(self.snapshots) * TIME_UNIT_NS
            width = max(self.canvas.winfo_width(), 100)
            self.px_per_ns = max((width - 4) / span_ns, 0.02)
            self.t_start = 0.0
        self.redraw()

    def zoom(self, factor, centre_time=None):
        """Scale time; keep the given time (default: view centre) fixed."""
        width = max(self.canvas.winfo_width(), 100)
        if centre_time is None:
            centre_time = self.t_start + width / 2.0 / max(self.px_per_ns, 1e-9)
        self.px_per_ns = max(self.px_per_ns * factor, 0.002)
        self.t_start = max(centre_time - width / 2.0 / self.px_per_ns, 0.0)
        self.schedule_redraw()

    def _lane_geometry(self):
        """Shared layout math for redraw() and hit-testing.

        Returns (col_x, row_step, centers) where centers[i] is the y
        centre of visible lane i (None when hidden).

        Cached by (visible-tuple, height). This used to create+delete a
        throwaway canvas text item to measure label width on *every*
        call -- and _on_motion() calls this on every single mouse-move,
        so that create/delete pair was firing dozens of times a second
        while you hovered. Rapid canvas item churn like that is known
        to leave rendering artifacts ("ghosting"/shading) on some Tk
        backends, which matches the shaded smudge appearing on hover
        before anything had even been run. Measuring the label once and
        caching the result removes the churn entirely.
        """
        height = max(self.canvas.winfo_height(), 100)
        cache_key = (tuple(self.visible), height)
        cached = getattr(self, "_geom_cache", None)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        canvas = self.canvas
        pad = 6
        label_w = getattr(self, "_label_w_cache", None)
        row_h = getattr(self, "_row_h_cache", None)
        if label_w is None or row_h is None:
            # measure the widest label with real text extents (once, ever)
            probe = canvas.create_text(0, 0, text="instruction",
                                       font=("TkFixedFont", 9))
            bbox = canvas.bbox(probe)
            canvas.delete(probe)
            label_w = (bbox[2] - bbox[0]) if bbox else 80
            row_h = (bbox[3] - bbox[1]) if bbox else 12
            self._label_w_cache = label_w
            self._row_h_cache = row_h
        col_x = min(pad + label_w + 14, 190)
        row_step = max(18, row_h + 8)
        n_visible = sum(self.visible)
        usable = height - 26                       # leave room for the axis
        if n_visible * row_step > usable and n_visible > 0:
            row_step = max(10, usable / n_visible)
        centers = []
        y = 4 + row_step / 2.0
        for vis in self.visible:
            centers.append(y if vis else None)
            if vis:
                y += row_step
        result = (col_x, row_step, centers)
        self._geom_cache = (cache_key, result)
        return result

    def redraw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 100)
        height = max(canvas.winfo_height(), 100)
        col_x, row_step, centers = self._lane_geometry()

        # ---- lane grid + labels ----
        for row, (name, _key, _w) in enumerate(GUI_SIGNALS):
            if not self.visible[row]:
                continue
            center = centers[row]
            color = LANE_COLORS[row]
            canvas.create_line(col_x, center, width, center, fill=GRID)
            canvas.create_text(6, center, text=name, anchor="w", fill=color,
                               font=("TkFixedFont", 9))
        canvas.create_line(col_x - 7, 0, col_x - 7, height, fill=GRID)

        if not self.snapshots:
            canvas.create_text(width / 2, height / 2, fill=MUTED,
                               justify=tk.CENTER, font=("TkDefaultFont", 11),
                               text="No waveform yet - press Run, Step, or Auto.\n"
                                    "Load .asm/.hack... opens your own program; "
                                    "Demo runs sum 1..10.")
            return

        px = self.px_per_ns
        t_end = self.t_start + (width - col_x) / px

        # ---- waveforms ----
        for row, (name, _key, bits) in enumerate(GUI_SIGNALS):
            if not self.visible[row]:
                continue
            color = LANE_COLORS[row]
            hi = centers[row] - row_step * 0.32
            lo = centers[row] + row_step * 0.32
            if bits == 1:
                self._draw_bit_lane(self.times_by_row[row],
                                    self.values_by_row[row],
                                    color, hi, lo, t_end, px, col_x)
            else:
                self._draw_bus_lane(self.times_by_row[row],
                                    self.values_by_row[row],
                                    color, hi, lo, t_end, px, col_x)

        # ---- time axis (one tick every TIME_UNIT_NS = 1 cycle) ----
        axis_y = height - 12
        canvas.create_line(col_x, axis_y, width, axis_y, fill=GRID)
        first = int(self.t_start // TIME_UNIT_NS)
        last = int(t_end // TIME_UNIT_NS) + 1
        for k in range(max(first, 0), last + 1):
            t = k * TIME_UNIT_NS
            x = col_x + (t - self.t_start) * px
            if x < col_x + 2 or x > width - 2:
                continue
            canvas.create_line(x, axis_y - 3, x, axis_y + 3, fill=GRID)
            if px >= 18:                    # thin labels when zoomed out
                canvas.create_text(x, axis_y - 7, text=str(k), fill=MUTED,
                                   anchor="s", font=("TkFixedFont", 8))
        canvas.create_text(width - 4, axis_y - 7, text="cycle", fill=MUTED,
                           anchor="e", font=("TkFixedFont", 8))

        # ---- live drag-zoom rectangle ----
        if self._drag_start is not None and self._drag_now is not None:
            x0, x1 = sorted((self._drag_start, self._drag_now))
            if x1 - x0 >= 3:
                canvas.create_rectangle(x0, 0, x1, height, outline=ACCENT,
                                        fill="", dash=(3, 3))

    def _draw_bit_lane(self, times, values, color, hi, lo, t_end, px, col_x):
        """1-bit signal as a square wave (diagonal edges like GTKWave)."""
        if not values:
            return
        pts = []

        def x_of(t):
            return col_x + (t - self.t_start) * px

        v = sample_at(times, values, self.t_start)
        pts.append((col_x, hi if v else lo))
        for i, t in enumerate(times):
            if t < self.t_start:
                continue
            if t > t_end:
                break
            x = x_of(t)
            y_new = hi if values[i] else lo
            prev_y = pts[-1][1]
            if y_new != prev_y:
                pts.append((x, prev_y))   # horizontal up to the edge
                pts.append((x, y_new))    # vertical transition
            else:
                pts.append((x, y_new))
        pts.append((col_x + (t_end - self.t_start) * px, pts[-1][1]))

        cleaned = [pts[0]]
        for p in pts[1:]:
            if p != cleaned[-1]:
                cleaned.append(p)
        flat = []
        for x, y in cleaned:
            flat.extend((x, y))
        if len(flat) >= 4:
            self.canvas.create_line(*flat, fill=color, width=1.6,
                                    joinstyle=tk.ROUND, capstyle=tk.BUTT)

    def _draw_bus_lane(self, times, values, color, hi, lo, t_end, px, col_x):
        """Multi-bit signal as value segments in hexagon boxes (hex text)."""
        canvas = self.canvas
        if not values:
            return
        v = sample_at(times, values, self.t_start)
        seg_start = self.t_start
        segs = []                                # (t0, t1, value)
        for i, t in enumerate(times):
            if t <= self.t_start:
                v = values[i]
                seg_start = self.t_start
                continue
            if t >= t_end:
                break
            if values[i] != v:
                segs.append((seg_start, t, v))
                v = values[i]
                seg_start = t
        segs.append((seg_start, t_end, v))

        mid_y = (hi + lo) / 2.0
        for (t0, t1, val) in segs:
            x0 = col_x + (t0 - self.t_start) * px
            x1 = col_x + (t1 - self.t_start) * px
            if x1 - x0 < 0.7:
                continue
            inset = min(6, (x1 - x0) / 2)
            canvas.create_polygon(x0, lo, x0 + inset, hi,
                                  x1 - inset, hi, x1, lo,
                                  outline=color, fill="", width=1.4)
            if x1 - x0 > 34:
                canvas.create_text((x0 + x1) / 2, mid_y,
                                   text="%04X" % (val & 0xFFFF), fill=color,
                                   font=("TkFixedFont", 8))

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def _on_signal_click(self, event):
        """Toggle lane visibility for the clicked signal name."""
        index = self.sig_list.nearest(event.y)
        if 0 <= index < len(GUI_SIGNALS):
            self.visible[index] = not self.visible[index]
            self.redraw()

    def _on_motion(self, event):
        col_x, _row_step, centers = self._lane_geometry()
        if event.x < col_x:
            self._clear_hover()
            return
        row = None
        best_d = None
        for i, c in enumerate(centers):
            if c is None:
                continue
            d = abs(event.y - c)
            if best_d is None or d < best_d:
                row, best_d = i, d
        if row is not None and best_d > self.canvas.winfo_height() / (2 * max(1, sum(self.visible))):
            row = None                            # too far from any lane
        t = self.t_start + (event.x - col_x) / max(self.px_per_ns, 1e-9)
        self._update_hover(event.x, event.y, t, row)

    def _update_hover(self, x, y, t, row):
        canvas = self.canvas
        self._clear_hover()
        if row is None:
            return
        self._hover_line = canvas.create_line(x, 0, x, canvas.winfo_height(),
                                              fill=ACCENT, dash=(2, 2))
        name, key, _w = GUI_SIGNALS[row]
        if self.snapshots:
            cyc = max(0, min(int(t // TIME_UNIT_NS), len(self.snapshots) - 1))
            snap = self.snapshots[cyc]
            value = snap[key]
            text = "t=%.0fns  cycle=%d  %s=%s" % (t, snap["cycle"], name,
                                                  format_value(name, value))
        else:
            text = "t=%.0fns  %s=0" % (t, name)
        self._tooltip_id = canvas.create_text(x + 8, y - 10, text=text,
                                              anchor="sw",
                                              font=("TkFixedFont", 9))
        bbox = canvas.bbox(self._tooltip_id)
        if bbox:
            pad = 3
            rect = canvas.create_rectangle(bbox[0] - pad, bbox[1] - pad,
                                           bbox[2] + pad, bbox[3] + pad,
                                           fill=ACCENT, outline="")
            canvas.tag_lower(rect)

    def _clear_hover(self):
        if self._hover_line is not None:
            self.canvas.delete(self._hover_line)
            self._hover_line = None
        if self._tooltip_id is not None:
            self.canvas.delete(self._tooltip_id)
            self._tooltip_id = None

    def _on_press(self, event):
        if event.x < self._lane_geometry()[0]:
            return
        self._drag_start = event.x
        self._drag_now = event.x

    def _on_drag(self, event):
        if self._drag_start is None:
            return
        self._drag_now = event.x
        if abs(event.x - self._drag_start) >= 3:
            self.schedule_redraw()

    def _on_release(self, event):
        if self._drag_start is None:
            return
        x0, x1 = sorted((self._drag_start, event.x))
        self._drag_start = None
        self._drag_now = None
        if x1 - x0 < 6:
            self.redraw()
            return                              # plain click, not a zoom drag
        col_x = self._lane_geometry()[0]
        t0 = self.t_start + (x0 - col_x) / max(self.px_per_ns, 1e-9)
        t1 = self.t_start + (x1 - col_x) / max(self.px_per_ns, 1e-9)
        width = max(self.canvas.winfo_width(), 100)
        self.px_per_ns = (width - col_x) / max(t1 - t0, 1e-9)
        self.t_start = max(t0, 0.0)
        self.redraw()

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4 or event.delta > 0:
            factor = 1.15
        else:
            factor = 1 / 1.15
        col_x = self._lane_geometry()[0]
        centre_t = self.t_start + (event.x - col_x) / max(self.px_per_ns, 1e-9)
        self.zoom(factor, centre_time=centre_t)

    def _on_close(self):
        self.stop_auto()
        self.root.destroy()


# ----------------------------------------------------------------------
# Headless self-test (python gui.py --selftest): no Tk window needed.
# ----------------------------------------------------------------------
def _selftest():
    code, symbols = assemble_source(DEMO_PROGRAM, source_name="demo")
    assert len(code) == 28, "expected 28 instructions, got %d" % len(code)
    assert symbols["n"] == 16 and symbols["i"] == 17 and symbols["sum"] == 18
    assert symbols["LOOP"] == 8 and symbols["END"] == 22 and symbols["FIN"] == 26

    cpu = HackCPU(code)
    snaps = cpu.run(200)
    assert snaps[-1]["ram0"] == 55, "R0 should be 55, got %r" % snaps[-1]["ram0"]
    assert snaps[-1]["pc"] == 26, "should idle at FIN (26), got %r" % snaps[-1]["pc"]

    # Build per-lane change lists exactly as _absorb_snapshots does.
    times_by_row = [[] for _ in GUI_SIGNALS]
    values_by_row = [[] for _ in GUI_SIGNALS]
    for snap in snaps:
        t_rise = (snap["cycle"] - 1) * TIME_UNIT_NS + HALF_PERIOD_NS
        for row, (_name, key, width) in enumerate(GUI_SIGNALS):
            value = int(bool(snap[key])) if width == 1 else snap[key] & 0xFFFF
            if not values_by_row[row] or values_by_row[row][-1] != value:
                times_by_row[row].append(t_rise)
                values_by_row[row].append(value)

    # sample_at: step-function semantics at boundaries and beyond.
    pc_times = times_by_row[1]
    pc_values = values_by_row[1]
    assert sample_at(pc_times, pc_values, pc_times[0]) == pc_values[0]
    assert sample_at(pc_times, pc_values, pc_times[0] - 1) == 0
    assert sample_at(pc_times, pc_values, 10 ** 9) == pc_values[-1]

    # format_value must cover every lane without raising.
    for name, _key, _w in GUI_SIGNALS:
        for v in (0, 1, 0xFFFF, 0x8000):
            format_value(name, v)

    # VCD export round-trip through the same call the GUI uses.
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        write_vcd(tmp, snaps, comment="gui selftest")
        with open(tmp) as f:
            text = f.read()
        assert "$enddefinitions $end" in text
        assert text.count("\n#") >= len(snaps)
    finally:
        os.unlink(tmp)

    print("gui.py self-test passed: demo assembles, runs (R0=55), "
          "wave data + sampling + VCD export all OK.")


def main():
    if "--selftest" in sys.argv:
        _selftest()
        return
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    root = tk.Tk()
    HackSimGUI(root, program_path=args[0] if args else None)
    root.mainloop()


if __name__ == "__main__":
    main()