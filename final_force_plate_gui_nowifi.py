import sys
import csv
import io
import serial
import serial.tools.list_ports
import time
from collections import deque

import numpy as np
from PyQt6 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg

# -----------------------------
# Configuration
# -----------------------------
SERIAL_PORT   = None          # Set to e.g. "COM3" or "/dev/ttyUSB0", or None to auto-detect
BAUD_RATE     = 9600
PLOT_WINDOW   = 10.0          # seconds shown in plots
INVOLVED_SIDE = "left"        # "left" or "right"
RTS_THRESHOLD = 90.0          # LSI % return-to-sport criterion


# -----------------------------
# Helpers
# -----------------------------
def auto_detect_port() -> str | None:
    """Return the first likely Arduino/ESP32 serial port, or None."""
    candidates = serial.tools.list_ports.comports()
    keywords   = ("usb", "usbserial", "usbmodem", "ch340", "cp210",
                   "arduino", "esp32", "ttyusb", "ttyacm")
    for p in candidates:
        desc = (p.description + p.device).lower()
        if any(k in desc for k in keywords):
            return p.device
    return candidates[0].device if candidates else None


def compute_lsi(left: float, right: float) -> float:
    high = max(left, right)
    low  = min(left, right)
    if high == 0:
        return 0.0
    return (low / high) * 100.0


def rts_status(lsi_val: float) -> tuple[str, str]:
    if lsi_val >= RTS_THRESHOLD:
        return "RTS CLEARED ✓", "#1D9E75"
    gap = RTS_THRESHOLD - lsi_val
    if gap <= 5:
        return f"Near threshold (−{gap:.1f}%)", "#BA7517"
    return f"Below threshold (−{gap:.1f}%)", "#D85A30"


# -----------------------------
# Serial reader thread
# -----------------------------
class SerialReader(QtCore.QThread):
    # Emits (sample_count, left_kg, right_kg)
    sample_received = QtCore.pyqtSignal(int, float, float)
    status          = QtCore.pyqtSignal(str)

    def __init__(self, port=None, baud=BAUD_RATE, parent=None):
        super().__init__(parent)
        self.port     = port
        self.baud     = baud
        self._running = True
        self._ser     = None
        self._count   = 0

    def run(self):
        port = self.port or auto_detect_port()
        if not port:
            self.status.emit("No serial port found. Check USB connection.")
            return

        self.status.emit(f"Opening {port} @ {self.baud} baud...")
        try:
            self._ser = serial.Serial(port, self.baud, timeout=1.0)
            self.status.emit(f"Connected - {port}")
        except serial.SerialException as e:
            self.status.emit(f"Serial error: {e}")
            return

        while self._running:
            try:
                line = self._ser.readline().decode("utf-8", errors="ignore").strip()
            except serial.SerialException as e:
                self.status.emit(f"Read error: {e}")
                break

            if not line:
                continue

            if "|" not in line:
                continue

            parts = line.split("|")
            if len(parts) != 2:
                continue

            try:
                left_kg = float(parts[0].strip())
                right_kg = float(parts[1].strip())
            except ValueError:
                continue

            self._count += 1
            self.sample_received.emit(self._count, left_kg, right_kg)

        if self._ser and self._ser.is_open:
            self._ser.close()

    def stop(self):
        self._running = False
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass


# =====================================================================
# Shared data store
# =====================================================================
class DataStore:
    def __init__(self):
        self.reset()

    def reset(self):
        self.times    = deque()
        self.raw_L    = deque()
        self.raw_R    = deque()
        self.sum_buf  = deque()
        self.lsi_buf  = deque()

        # Full history for CSV export and replay (never pruned)
        self.history: list[tuple[float, float, float, float, float]] = []
        # (t_rel, raw_L, raw_R, total, lsi)

        self.t0               = None
        self.session_peak_L   = 0.0
        self.session_peak_R   = 0.0
        self.session_peak_sum = 0.0
        self.session_peak_lsi = 0.0

        self.last_count = 0
        self.last_raw_L = 0.0
        self.last_raw_R = 0.0
        self.last_sum   = 0.0
        self.last_lsi   = 0.0

        self.peak_sum_L = 0.0
        self.peak_sum_R = 0.0

    def ingest(self, count: int, raw_L: float, raw_R: float):
        t = time.time()
        if self.t0 is None:
            self.t0 = t
        t_rel = t - self.t0

        lsi_val = compute_lsi(raw_L, raw_R)
        total   = raw_L + raw_R

        self.times.append(t_rel)
        self.raw_L.append(raw_L)
        self.raw_R.append(raw_R)
        self.sum_buf.append(total)
        self.lsi_buf.append(lsi_val)

        # Save full history row
        self.history.append((t_rel, raw_L, raw_R, total, lsi_val))

        # Prune rolling window
        cutoff = t_rel - PLOT_WINDOW
        while self.times and self.times[0] < cutoff:
            for buf in (self.times, self.raw_L, self.raw_R,
                        self.sum_buf, self.lsi_buf):
                buf.popleft()

        if raw_L   > self.session_peak_L:   self.session_peak_L   = raw_L
        if raw_R   > self.session_peak_R:   self.session_peak_R   = raw_R
        if total   > self.session_peak_sum: self.session_peak_sum = total
        if total > self.session_peak_sum:
         self.session_peak_sum = total
         self.peak_sum_L = raw_L  
         self.peak_sum_R = raw_R


        self.last_count = count
        self.last_raw_L = raw_L
        self.last_raw_R = raw_R
        self.last_sum   = total
        self.last_lsi   = lsi_val

    def to_csv_bytes(self) -> bytes:
        """Return full session history as UTF-8 CSV bytes."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["time_s", "left_kg", "right_kg", "total_kg", "lsi_pct"])
        for row in self.history:
            writer.writerow([f"{v:.4f}" for v in row])
        return buf.getvalue().encode("utf-8")

@property
def peak_lsi(self) -> float:
    return compute_lsi(self.peak_sum_L, self.peak_sum_R)

# =====================================================================
# Clinician Dashboard
# =====================================================================
class ClinicianDashboard(QtWidgets.QWidget):
    def __init__(self, store: DataStore, parent=None):
        super().__init__(parent)
        self.store      = store
        self._show_left  = True
        self._show_right = True
        self._setup_ui()

    def _setup_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        hdr = QtWidgets.QLabel("Clinician Dashboard — Force Plate Monitor")
        hdr.setStyleSheet(
            "font-size: 20px; font-weight: 700; color: #e8edf5; margin-bottom: 2px;")
        root.addWidget(hdr)

        # Row 1 — peak cards
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(10)
        self.m_pk_L   = self._card("Peak Force — Left",  "0.0 kg", "#4a90d9", big=True)
        self.m_pk_R   = self._card("Peak Force — Right", "0.0 kg", "#bd10e0", big=True)
        self.m_pk_sum = self._card("Peak Total Force",   "0.0 kg", "#f5a623", big=True)
        self.m_pk_lsi = self._card("Peak LSI",           "0.0 %",  "#7ed321", big=True)
        for w in [self.m_pk_L, self.m_pk_R, self.m_pk_sum, self.m_pk_lsi]:
            row1.addWidget(w)
        root.addLayout(row1)

        # Row 2 — live cards
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(8)
        self.m_live_L   = self._card("Live Load — Left",  "0.0 kg", "#4a90d9", big=False)
        self.m_live_R   = self._card("Live Load — Right", "0.0 kg", "#bd10e0", big=False)
        self.m_live_sum = self._card("Live Total",        "0.0 kg", "#f5a623", big=False)
        self.m_live_lsi = self._card("Live LSI",          "0.0 %",  "#7ed321", big=False)
        self.m_rts      = self._card("RTS Status",        "—",      "#e8edf5", big=False)
        for w in [self.m_live_L, self.m_live_R, self.m_live_sum,
                  self.m_live_lsi, self.m_rts]:
            row2.addWidget(w)
        root.addLayout(row2)

        # Toggle row
        toggle_row = QtWidgets.QHBoxLayout()
        toggle_row.setSpacing(6)
        lbl = QtWidgets.QLabel("Force graph display:")
        lbl.setStyleSheet("font-size: 12px; color: #8899aa;")
        toggle_row.addWidget(lbl)
        self.btn_left  = self._toggle_btn("Left",  "#4a90d9", True)
        self.btn_right = self._toggle_btn("Right", "#bd10e0", True)
        self.btn_left.clicked.connect(self._on_toggle_left)
        self.btn_right.clicked.connect(self._on_toggle_right)
        toggle_row.addWidget(self.btn_left)
        toggle_row.addWidget(self.btn_right)
        toggle_row.addStretch()
        root.addLayout(toggle_row)

        # Graphs
        pw = pg.GraphicsLayoutWidget()
        pw.setBackground("#141b24")

        # Plot 0 — Individual limb forces
        self.plot_force = pw.addPlot(row=0, col=0, title="Individual Limb Force (kg)")
        self.plot_force.showGrid(x=True, y=True)
        self.plot_force.setLabel("left", "Force (kg)")
        self.plot_force.setLabel("bottom", "Time (s)")
        self.plot_force.setYRange(0, 1, padding=0.05)
        self.plot_force.setXRange(0, PLOT_WINDOW, padding=0.02)
        self.plot_force.addLegend(offset=(10, 10))
        self.curve_L = self.plot_force.plot(pen=pg.mkPen("#4a90d9", width=2), name="Left")
        self.curve_R = self.plot_force.plot(pen=pg.mkPen("#bd10e0", width=2), name="Right")
        self.pk_line_L = pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen("#4a90d9", style=QtCore.Qt.PenStyle.DashLine),
            label="Peak L", labelOpts={"position": 0.97, "color": "#4a90d9"})
        self.pk_line_R = pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen("#bd10e0", style=QtCore.Qt.PenStyle.DashLine),
            label="Peak R", labelOpts={"position": 0.92, "color": "#bd10e0"})
        self.plot_force.addItem(self.pk_line_L)
        self.plot_force.addItem(self.pk_line_R)

        # Plot 1 — Total force
        pw.nextRow()
        self.plot_sum = pw.addPlot(
            row=1, col=0, title="Total Combined Force (kg)  — Left + Right")
        self.plot_sum.showGrid(x=True, y=True)
        self.plot_sum.setLabel("left", "Force (kg)")
        self.plot_sum.setLabel("bottom", "Time (s)")
        self.plot_sum.setYRange(0, 1, padding=0.05)
        self.plot_sum.setXRange(0, PLOT_WINDOW, padding=0.02)
        self.curve_sum = self.plot_sum.plot(pen=pg.mkPen("#f5a623", width=2))
        self.pk_line_sum = pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen("#f5a623", style=QtCore.Qt.PenStyle.DashLine),
            label="Peak Total", labelOpts={"position": 0.97, "color": "#f5a623"})
        self.plot_sum.addItem(self.pk_line_sum)

        # Plot 2 — LSI
        pw.nextRow()
        self.plot_lsi = pw.addPlot(
            row=2, col=0,
            title=f"Limb Symmetry Index (%)  — RTS ≥ {RTS_THRESHOLD:.0f}%  |  "
                  f"Formula: (xL − xR) / max(xL − xR) × 100")
        self.plot_lsi.showGrid(x=True, y=True)
        self.plot_lsi.setLabel("left", "LSI (%)")
        self.plot_lsi.setLabel("bottom", "Time (s)")
        self.plot_lsi.setYRange(0, 110, padding=0.02)
        self.plot_lsi.setXRange(0, PLOT_WINDOW, padding=0.02)
        self.curve_lsi = self.plot_lsi.plot(pen=pg.mkPen("#7ed321", width=2))
        rts_line = pg.InfiniteLine(
            pos=RTS_THRESHOLD, angle=0,
            pen=pg.mkPen("#1D9E75", style=QtCore.Qt.PenStyle.DashLine, width=2),
            label=f"RTS {RTS_THRESHOLD:.0f}%",
            labelOpts={"position": 0.95, "color": "#1D9E75"})
        self.plot_lsi.addItem(rts_line)
        self.plot_lsi.addItem(pg.InfiniteLine(
            pos=100, angle=0,
            pen=pg.mkPen("w", style=QtCore.Qt.PenStyle.DotLine, width=1),
            label="100% symmetry",
            labelOpts={"position": 0.05, "color": "w"}))

        root.addWidget(pw, 1)

        self.status_bar = QtWidgets.QLabel("Status: disconnected")
        self.status_bar.setStyleSheet("font-size: 12px; color: gray;")
        root.addWidget(self.status_bar)

    # ------------------------------------------------------------------
    def _card(self, label, init, accent="#e8edf5", big=True):
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            f"QFrame {{ background: #1a2130; border: 2px solid {accent}55; "
            f"border-radius: 8px; }}")
        vl = QtWidgets.QVBoxLayout(frame)
        pad = (12, 12, 12, 12) if big else (8, 6, 8, 6)
        vl.setContentsMargins(*pad)
        vl.setSpacing(2)
        lbl = QtWidgets.QLabel(label)
        lbl.setStyleSheet(
            f"font-size: {'12' if big else '10'}px; color: #8899aa; border: none;")
        val = QtWidgets.QLabel(init)
        val.setObjectName("value")
        val.setStyleSheet(
            f"font-size: {'26' if big else '15'}px; font-weight: 700; "
            f"color: {accent}; border: none;")
        vl.addWidget(lbl)
        vl.addWidget(val)
        return frame

    @staticmethod
    def _set_card(frame, text, color=None):
        import re
        lbl = frame.findChild(QtWidgets.QLabel, "value")
        lbl.setText(text)
        if color:
            lbl.setStyleSheet(
                re.sub(r'color:\s*[^;]+', f'color: {color}', lbl.styleSheet()))

    def _toggle_btn(self, text, color, active):
        btn = QtWidgets.QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setFixedHeight(28)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {'#1e2a38' if active else '#141b24'};
                color: {color}; border: 1px solid {color};
                border-radius: 4px; padding: 0 12px;
                font-size: 12px; font-weight: 600;
            }}
            QPushButton:checked {{ background: {color}33; }}
        """)
        return btn

    def _on_toggle_left(self):
        self._show_left = self.btn_left.isChecked()
        self.curve_L.setVisible(self._show_left)
        self.pk_line_L.setVisible(self._show_left)

    def _on_toggle_right(self):
        self._show_right = self.btn_right.isChecked()
        self.curve_R.setVisible(self._show_right)
        self.pk_line_R.setVisible(self._show_right)

    def refresh(self):
        s = self.store
        if not s.times:
            return

        t       = np.array(s.times,   dtype=float)
        raw_l   = np.array(s.raw_L,   dtype=float)
        raw_r   = np.array(s.raw_R,   dtype=float)
        sums    = np.array(s.sum_buf, dtype=float)
        lsi_arr = np.array(s.lsi_buf, dtype=float)

        x_min = max(0.0, t[-1] - PLOT_WINDOW)
        x_max = t[-1] if t[-1] > PLOT_WINDOW else PLOT_WINDOW

        self.curve_L.setData(t, raw_l)
        self.curve_R.setData(t, raw_r)
        self.curve_sum.setData(t, sums)
        self.curve_lsi.setData(t, lsi_arr)

        for plot in [self.plot_force, self.plot_sum, self.plot_lsi]:
            plot.setXRange(max(0.0, x_min), x_max, padding=0.02)

        if len(raw_l):
            y_max = max(np.max(raw_l), np.max(raw_r), 1.0) * 1.1
            self.plot_force.setYRange(0, y_max, padding=0)
        if len(sums):
            self.plot_sum.setYRange(0, max(np.max(sums), 1.0) * 1.1, padding=0)

        if s.session_peak_L   > 0: self.pk_line_L.setValue(s.session_peak_L)
        if s.session_peak_R   > 0: self.pk_line_R.setValue(s.session_peak_R)
        if s.session_peak_sum > 0: self.pk_line_sum.setValue(s.session_peak_sum)

        rts_text, rts_color = rts_status(s.last_lsi)

        self._set_card(self.m_pk_L,   f"{s.session_peak_L:.1f} kg")
        self._set_card(self.m_pk_R,   f"{s.session_peak_R:.1f} kg")
        self._set_card(self.m_pk_sum, f"{s.session_peak_sum:.1f} kg")
        self._set_card(self.m_pk_lsi, f"{s.session_peak_lsi:.1f} %")
        self._set_card(self.m_live_L,   f"{s.last_raw_L:.1f} kg")
        self._set_card(self.m_live_R,   f"{s.last_raw_R:.1f} kg")
        self._set_card(self.m_live_sum, f"{s.last_sum:.1f} kg")
        self._set_card(self.m_live_lsi, f"{s.last_lsi:.1f} %")
        self._set_card(self.m_rts,      rts_text, rts_color)

    def set_status(self, msg: str):
        self.status_bar.setText(f"Status: {msg}")


# =====================================================================
# Patient Dashboard
# =====================================================================
class PatientDashboard(QtWidgets.QWidget):
    def __init__(self, store: DataStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._setup_ui()

    def _setup_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        hdr = QtWidgets.QLabel("Your Recovery Progress")
        hdr.setStyleSheet("font-size: 26px; font-weight: 700; color: #e8edf5;")
        hdr.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hdr)

        sub = QtWidgets.QLabel(
            f"Return-to-sport target: LSI ≥ {RTS_THRESHOLD:.0f}%  "
            f"({'Left' if INVOLVED_SIDE == 'left' else 'Right'} is your involved limb)")
        sub.setStyleSheet("font-size: 13px; color: #8899aa;")
        sub.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(sub)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(12)
        self.m_pk_L   = self._big_card("Peak Force — Left",  "0.0 kg", "#4a90d9")
        self.m_pk_R   = self._big_card("Peak Force — Right", "0.0 kg", "#bd10e0")
        self.m_pk_sum = self._big_card("Peak Total Force",   "0.0 kg", "#f5a623")
        self.m_pk_lsi = self._big_card("Peak LSI",           "0.0 %",  "#7ed321")
        for w in [self.m_pk_L, self.m_pk_R, self.m_pk_sum, self.m_pk_lsi]:
            row1.addWidget(w)
        root.addLayout(row1)

        gauge_lbl = QtWidgets.QLabel("Live Limb Symmetry Index")
        gauge_lbl.setStyleSheet("font-size: 15px; color: #aabbcc; font-weight: 600;")
        gauge_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(gauge_lbl)

        self.lsi_number = QtWidgets.QLabel("— %")
        self.lsi_number.setStyleSheet(
            "font-size: 52px; font-weight: 700; color: #e8edf5;")
        self.lsi_number.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lsi_number)

        self.gauge_bar = QtWidgets.QProgressBar()
        self.gauge_bar.setRange(0, 100)
        self.gauge_bar.setValue(0)
        self.gauge_bar.setTextVisible(False)
        self.gauge_bar.setFixedHeight(36)
        self.gauge_bar.setStyleSheet("""
            QProgressBar { background: #2a3340; border-radius: 8px; border: none; }
            QProgressBar::chunk {
                border-radius: 8px;
                background: qlineargradient(x1:0, x2:1,
                    stop:0 #e74c3c, stop:0.5 #f39c12, stop:1 #2ecc71);
            }
        """)
        root.addWidget(self.gauge_bar)

        self.gauge_text = QtWidgets.QLabel("Step onto the plate to begin")
        self.gauge_text.setStyleSheet(
            "font-size: 20px; font-weight: 700; color: #aabbcc;")
        self.gauge_text.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.gauge_text)

        self.feedback_label = QtWidgets.QLabel("Step onto the plate to begin")
        self.feedback_label.setStyleSheet(
            "font-size: 15px; color: #aabbcc; padding: 12px; "
            "background: #1e2533; border-radius: 8px;")
        self.feedback_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.feedback_label.setWordWrap(True)
        root.addWidget(self.feedback_label)

        # Force loading bars
        force_group = QtWidgets.QGroupBox("Live Force Loading")
        force_group.setStyleSheet("""
            QGroupBox {
                font-size: 13px; font-weight: 600; color: #aabbcc;
                border: 1px solid #2e3a4e; border-radius: 6px;
                margin-top: 8px; padding-top: 12px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; }
        """)
        fg = QtWidgets.QGridLayout(force_group)
        fg.setSpacing(8)

        def side_lbl(text, color):
            l = QtWidgets.QLabel(text)
            l.setStyleSheet(
                f"font-size: 13px; color: {color}; font-weight: 700; min-width: 55px;")
            l.setAlignment(
                QtCore.Qt.AlignmentFlag.AlignRight |
                QtCore.Qt.AlignmentFlag.AlignVCenter)
            return l

        self.left_bar  = self._force_bar("#4a90d9")
        self.right_bar = self._force_bar("#bd10e0")
        self.left_pct_bar  = self._pct_bar("#4a90d9")
        self.right_pct_bar = self._pct_bar("#bd10e0")

        def val_lbl():
            l = QtWidgets.QLabel("0.0 kg")
            l.setStyleSheet("font-size: 13px; color: #e8edf5; min-width: 65px;")
            return l

        self.left_force_lbl  = val_lbl()
        self.right_force_lbl = val_lbl()
        self.left_pct_lbl    = val_lbl()
        self.right_pct_lbl   = val_lbl()

        force_hdr = QtWidgets.QLabel("Force (kg)")
        pct_hdr   = QtWidgets.QLabel("Load share (%)")
        for h in [force_hdr, pct_hdr]:
            h.setStyleSheet("font-size: 11px; color: #556677;")

        fg.addWidget(force_hdr,                  0, 1)
        fg.addWidget(side_lbl("LEFT",  "#4a90d9"), 1, 0)
        fg.addWidget(self.left_bar,              1, 1)
        fg.addWidget(self.left_force_lbl,        1, 2)
        fg.addWidget(side_lbl("RIGHT", "#bd10e0"), 2, 0)
        fg.addWidget(self.right_bar,             2, 1)
        fg.addWidget(self.right_force_lbl,       2, 2)
        fg.addWidget(pct_hdr,                    3, 1)
        fg.addWidget(self.left_pct_bar,          4, 1)
        fg.addWidget(self.left_pct_lbl,          4, 2)
        fg.addWidget(self.right_pct_bar,         5, 1)
        fg.addWidget(self.right_pct_lbl,         5, 2)
        fg.setColumnStretch(1, 1)
        root.addWidget(force_group)

        root.addStretch()

        self.count_label = QtWidgets.QLabel("Samples: 0")
        self.count_label.setStyleSheet("font-size: 11px; color: #556677;")
        self.count_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        root.addWidget(self.count_label)

    def _big_card(self, label, init, accent):
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            f"QFrame {{ background: #1a2130; border: 2px solid {accent}55; "
            f"border-radius: 10px; }}")
        vl = QtWidgets.QVBoxLayout(frame)
        vl.setContentsMargins(16, 14, 16, 14)
        vl.setSpacing(4)
        lbl = QtWidgets.QLabel(label)
        lbl.setStyleSheet(
            "font-size: 13px; color: #8899aa; font-weight: 500; border: none;")
        lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        val = QtWidgets.QLabel(init)
        val.setObjectName("value")
        val.setStyleSheet(
            f"font-size: 30px; font-weight: 700; color: {accent}; border: none;")
        val.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(lbl)
        vl.addWidget(val)
        return frame

    @staticmethod
    def _set_card(frame, text, color=None):
        import re
        lbl = frame.findChild(QtWidgets.QLabel, "value")
        lbl.setText(text)
        if color:
            lbl.setStyleSheet(
                re.sub(r'color:\s*[^;]+', f'color: {color}', lbl.styleSheet()))

    @staticmethod
    def _force_bar(color):
        b = QtWidgets.QProgressBar()
        b.setRange(0, 1000)
        b.setValue(0)
        b.setTextVisible(False)
        b.setFixedHeight(24)
        b.setStyleSheet(f"""
            QProgressBar {{ background: #2a3340; border-radius: 5px; border: none; }}
            QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}
        """)
        return b

    @staticmethod
    def _pct_bar(color):
        b = QtWidgets.QProgressBar()
        b.setRange(0, 100)
        b.setValue(50)
        b.setTextVisible(False)
        b.setFixedHeight(18)
        b.setStyleSheet(f"""
            QProgressBar {{ background: #2a3340; border-radius: 4px; border: none; }}
            QProgressBar::chunk {{ background: {color}80; border-radius: 4px; }}
        """)
        return b

    def _lsi_feedback(self, lsi_val):
        if lsi_val >= RTS_THRESHOLD:
            return (
                "Return to sport cleared! ✓",
                f"LSI {lsi_val:.1f}% — you've reached the {RTS_THRESHOLD:.0f}% "
                "return-to-sport threshold. Great work!",
                "#1D9E75")
        gap = RTS_THRESHOLD - lsi_val
        if gap <= 5:
            return (
                f"Almost there — {gap:.1f}% to go",
                f"LSI {lsi_val:.1f}% — you're very close to the "
                f"{RTS_THRESHOLD:.0f}% RTS target. Keep pushing!",
                "#BA7517")
        if gap <= 15:
            return (
                f"{gap:.1f}% below RTS target",
                f"LSI {lsi_val:.1f}% — your involved limb is loading well. "
                f"Close the {gap:.1f}% gap with continued rehab.",
                "#BA7517")
        return (
            f"{gap:.1f}% below RTS target",
            f"LSI {lsi_val:.1f}% — your involved limb is loading less than your "
            "uninvolved side. Focus on equal weight-bearing.",
            "#D85A30")

    def refresh(self):
        s = self.store
        if not s.times:
            return

        self._set_card(self.m_pk_L,   f"{s.session_peak_L:.1f} kg")
        self._set_card(self.m_pk_R,   f"{s.session_peak_R:.1f} kg")
        self._set_card(self.m_pk_sum, f"{s.session_peak_sum:.1f} kg")
        self._set_card(self.m_pk_lsi, f"{s.session_peak_lsi:.1f} %")

        gauge_lbl, feedback, color = self._lsi_feedback(s.last_lsi)
        self.lsi_number.setText(f"{s.last_lsi:.1f} %")
        self.lsi_number.setStyleSheet(
            f"font-size: 52px; font-weight: 700; color: {color};")
        self.gauge_bar.setValue(int(min(s.last_lsi, 100)))
        self.gauge_text.setText(gauge_lbl)
        self.gauge_text.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {color};")
        self.feedback_label.setText(feedback)
        self.count_label.setText(f"Samples: {s.last_count}")

        peak = max(s.session_peak_L, s.session_peak_R, 1.0)
        self.left_bar.setMaximum(int(peak * 1.1))
        self.right_bar.setMaximum(int(peak * 1.1))
        self.left_bar.setValue(int(s.last_raw_L))
        self.right_bar.setValue(int(s.last_raw_R))
        self.left_force_lbl.setText(f"{s.last_raw_L:.1f} kg")
        self.right_force_lbl.setText(f"{s.last_raw_R:.1f} kg")

        total = s.last_raw_L + s.last_raw_R
        pct_L, pct_R = (
            (s.last_raw_L / total * 100, s.last_raw_R / total * 100)
            if total > 0 else (50.0, 50.0))
        self.left_pct_bar.setValue(int(pct_L))
        self.right_pct_bar.setValue(int(pct_R))
        self.left_pct_lbl.setText(f"{pct_L:.1f} %")
        self.right_pct_lbl.setText(f"{pct_R:.1f} %")


# =====================================================================
# Port selector dialog
# =====================================================================
class PortDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Serial Port")
        self.setFixedSize(340, 160)
        self.setStyleSheet("background: #1a2130; color: #e8edf5;")

        vl = QtWidgets.QVBoxLayout(self)
        vl.setSpacing(12)
        vl.addWidget(QtWidgets.QLabel("Choose the COM/serial port for your ESP32:"))

        self.combo = QtWidgets.QComboBox()
        ports = serial.tools.list_ports.comports()
        auto  = auto_detect_port()
        for p in ports:
            self.combo.addItem(f"{p.device}  —  {p.description}", p.device)
        if not ports:
            self.combo.addItem("No ports found", None)
        # Pre-select auto-detected
        if auto:
            for i in range(self.combo.count()):
                if self.combo.itemData(i) == auto:
                    self.combo.setCurrentIndex(i)
                    break
        vl.addWidget(self.combo)

        btn_row = QtWidgets.QHBoxLayout()
        ok  = QtWidgets.QPushButton("Connect")
        ok.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(ok)
        vl.addLayout(btn_row)

    def selected_port(self) -> str | None:
        return self.combo.currentData()


# =====================================================================
# Main window
# =====================================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, port: str | None = None):
        super().__init__()
        self.setWindowTitle("Force Plate — Live Monitor")
        self.resize(1440, 960)

        palette = QtGui.QPalette()
        for role, color in [
            (QtGui.QPalette.ColorRole.Window,        "#141b24"),
            (QtGui.QPalette.ColorRole.WindowText,    "#e8edf5"),
            (QtGui.QPalette.ColorRole.Base,          "#1e2533"),
            (QtGui.QPalette.ColorRole.AlternateBase, "#232b38"),
            (QtGui.QPalette.ColorRole.Text,          "#e8edf5"),
        ]:
            palette.setColor(role, QtGui.QColor(color))
        QtWidgets.QApplication.instance().setPalette(palette)

        self.store          = DataStore()
        self._paused        = False
        self._replaying     = False
        self._replay_index  = 0
        self._replay_history: list = []

        # ── Central widget ──────────────────────────────────────────────
        central = QtWidgets.QWidget()
        vl      = QtWidgets.QVBoxLayout(central)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # ── Toolbar ─────────────────────────────────────────────────────
        toolbar = QtWidgets.QWidget()
        toolbar.setFixedHeight(52)
        toolbar.setStyleSheet("background: #0e1520; border-bottom: 1px solid #2a3a50;")
        tb_row = QtWidgets.QHBoxLayout(toolbar)
        tb_row.setContentsMargins(16, 0, 16, 0)
        tb_row.setSpacing(10)

        title_lbl = QtWidgets.QLabel("⬡ FORCE PLATE")
        title_lbl.setStyleSheet(
            "font-size: 14px; font-weight: 800; color: #4a90d9; "
            "letter-spacing: 3px;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()

        self.btn_pause   = self._tb_btn("⏸  Pause",   "#f5a623")
        self.btn_replay  = self._tb_btn("⏮  Replay",  "#4a90d9")
        self.btn_reset   = self._tb_btn("↺  Reset",   "#D85A30")
        self.btn_csv     = self._tb_btn("⬇  Export CSV", "#7ed321")

        self.btn_pause.clicked.connect(self._on_pause)
        self.btn_replay.clicked.connect(self._on_replay)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_csv.clicked.connect(self._on_export_csv)

        for b in [self.btn_pause, self.btn_replay, self.btn_reset, self.btn_csv]:
            tb_row.addWidget(b)

        # Replay speed selector
        speed_lbl = QtWidgets.QLabel("Speed:")
        speed_lbl.setStyleSheet("font-size: 12px; color: #556677;")
        tb_row.addWidget(speed_lbl)
        self.replay_speed = QtWidgets.QComboBox()
        self.replay_speed.addItems(["0.5×", "1×", "2×", "5×", "10×"])
        self.replay_speed.setCurrentIndex(1)
        self.replay_speed.setFixedWidth(72)
        self.replay_speed.setStyleSheet("""
            QComboBox {
                background: #1a2130; color: #e8edf5;
                border: 1px solid #2a3a50; border-radius: 4px;
                padding: 2px 6px; font-size: 12px;
            }
            QComboBox QAbstractItemView {
                background: #1a2130; color: #e8edf5;
                selection-background-color: #2a3a50;
            }
        """)
        tb_row.addWidget(self.replay_speed)

        vl.addWidget(toolbar)

        # ── Tabs ────────────────────────────────────────────────────────
        self.tabs           = QtWidgets.QTabWidget()
        self.clinician_view = ClinicianDashboard(self.store)
        self.patient_view   = PatientDashboard(self.store)
        self.tabs.addTab(self.clinician_view, "Clinician View")
        self.tabs.addTab(self.patient_view,   "Patient View")
        vl.addWidget(self.tabs)

        self.setCentralWidget(central)

        # ── Serial reader ───────────────────────────────────────────────
        self.reader = SerialReader(port=port)
        self.reader.sample_received.connect(self._on_sample)
        self.reader.status.connect(self._on_status)
        self.reader.start()

        # ── Render timer (~30 fps) ──────────────────────────────────────
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()

        # ── Replay timer ────────────────────────────────────────────────
        self._replay_timer = QtCore.QTimer(self)
        self._replay_timer.timeout.connect(self._replay_tick)

    # ------------------------------------------------------------------
    # Toolbar button factory
    # ------------------------------------------------------------------
    @staticmethod
    def _tb_btn(text: str, color: str) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(text)
        btn.setFixedHeight(34)
        btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {color};
                border: 1px solid {color}66;
                border-radius: 5px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover  {{ background: {color}22; border-color: {color}; }}
            QPushButton:pressed {{ background: {color}44; }}
            QPushButton:checked {{ background: {color}33; border-color: {color}; }}
        """)
        return btn

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    @QtCore.pyqtSlot(int, float, float)
    def _on_sample(self, count, left_kg, right_kg):
        if not self._paused and not self._replaying:
            self.store.ingest(count, left_kg, right_kg)

    def _on_status(self, msg: str):
        self.clinician_view.set_status(msg)
        self.statusBar().showMessage(msg)

    def _refresh(self):
        self.clinician_view.refresh()
        self.patient_view.refresh()

    # ── Pause / Resume ──────────────────────────────────────────────────
    def _on_pause(self):
        if self._replaying:
            # Pause/resume replay
            if self._replay_timer.isActive():
                self._replay_timer.stop()
                self.btn_pause.setText("▶  Resume")
                self._on_status("Replay paused")
            else:
                self._start_replay_timer()
                self.btn_pause.setText("⏸  Pause")
                self._on_status("Replay resumed")
            return

        self._paused = not self._paused
        if self._paused:
            self.btn_pause.setText("▶  Resume")
            self._on_status("Live data paused — serial still recording")
        else:
            self.btn_pause.setText("⏸  Pause")
            self._on_status("Live data resumed")

    # ── Reset ────────────────────────────────────────────────────────────
    def _on_reset(self):
        reply = QtWidgets.QMessageBox.question(
            self, "Reset Session",
            "Clear all session data and restart?\n"
            "(Unsaved data will be lost — export CSV first if needed.)",
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No)
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._stop_replay()
        self._paused = False
        self.btn_pause.setText("⏸  Pause")
        self.store.reset()
        self._on_status("Session reset — recording fresh data")

    # ── Export CSV ───────────────────────────────────────────────────────
    def _on_export_csv(self):
        if not self.store.history:
            QtWidgets.QMessageBox.information(
                self, "No Data", "No samples recorded yet.")
            return

        default_name = time.strftime("force_plate_%Y%m%d_%H%M%S.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Session CSV", default_name,
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self.store.to_csv_bytes())
            self._on_status(f"CSV saved → {path}")
            QtWidgets.QMessageBox.information(
                self, "Exported",
                f"Saved {len(self.store.history)} samples to:\n{path}")
        except OSError as e:
            QtWidgets.QMessageBox.critical(self, "Save Failed", str(e))

    # ── Replay ───────────────────────────────────────────────────────────
    def _on_replay(self):
        if self._replaying:
            self._stop_replay()
            return

        if not self.store.history:
            QtWidgets.QMessageBox.information(
                self, "No Data", "No session data to replay.")
            return

        # Snapshot the history, then reset the visible store
        self._replay_history = list(self.store.history)
        self._replay_index   = 0
        self._paused         = False
        self.store.reset()
        self._replaying = True

        self.btn_replay.setText("⏹  Stop Replay")
        self.btn_pause.setText("⏸  Pause")
        self._on_status(
            f"Replaying {len(self._replay_history)} samples…")
        self._start_replay_timer()

    def _start_replay_timer(self):
        speeds = {"0.5×": 0.5, "1×": 1.0, "2×": 2.0, "5×": 5.0, "10×": 10.0}
        multiplier = speeds.get(self.replay_speed.currentText(), 1.0)
        # Base interval: approximate real-time spacing between samples
        # Use the first two samples to estimate, fall back to 50 ms
        if len(self._replay_history) >= 2:
            dt_real = (self._replay_history[1][0] - self._replay_history[0][0])
            interval_ms = max(10, int(dt_real * 1000 / multiplier))
        else:
            interval_ms = int(50 / multiplier)
        self._replay_timer.start(interval_ms)

    def _replay_tick(self):
        if self._replay_index >= len(self._replay_history):
            self._stop_replay()
            self._on_status("Replay complete")
            return
        row = self._replay_history[self._replay_index]
        # row = (t_rel, raw_L, raw_R, total, lsi)
        self.store.ingest(self._replay_index + 1, row[1], row[2])
        self._replay_index += 1

    def _stop_replay(self):
        self._replay_timer.stop()
        self._replaying = False
        self._replay_history = []
        self._replay_index   = 0
        self.btn_replay.setText("⏮  Replay")
        self.btn_pause.setText("⏸  Pause")

    # ------------------------------------------------------------------
    def closeEvent(self, ev):
        self._replay_timer.stop()
        self.reader.stop()
        self.reader.wait(1000)
        ev.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    # Show port picker if no port hard-coded
    port = SERIAL_PORT
    if port is None:
        dlg = PortDialog()
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            port = dlg.selected_port()

    win = MainWindow(port=port)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
