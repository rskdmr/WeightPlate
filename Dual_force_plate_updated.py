import sys
import socket
import time
from collections import deque

import numpy as np
from PyQt6 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg

# -----------------------------
# Configuration
# -----------------------------
UDP_BIND_IP   = "0.0.0.0"
UDP_PORT      = 4210
PLOT_WINDOW   = 5.0          # seconds shown in plots
SYM_WINDOW    = 2.0          # seconds averaged for symmetry

# Symmetry formula:
#   LSI (%) = (x_left - x_right) / max(x_left - x_right) * 100
#
#   where max(x_left - x_right) is the rolling session maximum of that
#   difference.  100 % = the largest asymmetry seen this session; the
#   number therefore tracks relative improvement within a session.
#
#   Return-to-sport criterion: LSI >= 90 %
#
# CHANGE: Removed INVOLVED_SIDE — both sides are always real sensor readings
#         from a single Arduino outputting "left_kg|right_kg".
# CHANGE: Removed DUAL_CHANNEL — there is only one format now: "val|val".

# Return-to-sport LSI threshold (%)
RTS_THRESHOLD = 90.0


# -----------------------------
# Helpers
# -----------------------------
def compute_lsi(left: float, right: float, max_diff: float) -> float:
    """
    LSI (%) = (x_left - x_right) / max(x_left - x_right) * 100

    max_diff is the session maximum of (left - right), tracked in DataStore.
    Returns 0 when max_diff == 0 to avoid division by zero.
    Clamps to [0, 100] so the gauge always reads sensibly.
    """
    if max_diff == 0:
        return 0.0
    raw = (left - right) / max_diff * 100.0
    return max(0.0, min(100.0, raw))


def rts_status(lsi_val: float) -> tuple[str, str]:
    """Return (status_text, hex_color) based on LSI vs RTS threshold."""
    if lsi_val >= RTS_THRESHOLD:
        return "RTS CLEARED ✓", "#1D9E75"
    gap = RTS_THRESHOLD - lsi_val
    if gap <= 5:
        return f"Near threshold (−{gap:.1f}%)", "#BA7517"
    return f"Below threshold (−{gap:.1f}%)", "#D85A30"


# -----------------------------
# UDP reader thread
# -----------------------------
class UdpReader(QtCore.QThread):
    # CHANGE: Signal simplified from (int, float, float, float, float, float)
    #         to (float, float) — only left and right kg values are sent now.
    sample_received = QtCore.pyqtSignal(float, float)
    status = QtCore.pyqtSignal(str)

    def __init__(self, bind_ip=UDP_BIND_IP, port=UDP_PORT, parent=None):
        super().__init__(parent)
        self.bind_ip = bind_ip
        self.port = port
        self._running = True
        self.sock = None

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.bind_ip, self.port))
            self.sock.settimeout(1.0)
            self.status.emit(f"Listening on UDP {self.bind_ip}:{self.port}")

            while self._running:
                try:
                    data, _ = self.sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break

                raw = data.decode("utf-8", errors="ignore").strip()
                if not raw:
                    continue

                # CHANGE: Split on "|" instead of "," to match Arduino output
                #         "left_kg|right_kg\n". Removed all DUAL_CHANNEL branching.
                parts = [p.strip() for p in raw.split("|")]

                try:
                    if len(parts) == 2:
                        raw_L = float(parts[0])
                        raw_R = float(parts[1])
                        self.sample_received.emit(raw_L, raw_R)
                    else:
                        continue
                except ValueError:
                    continue

        except Exception as e:
            self.status.emit(f"UDP error: {e}")
        finally:
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass

    def stop(self):
        self._running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


# =====================================================================
# Shared data store (thread-safe via Qt signals)
# =====================================================================
class DataStore:
    """Simple in-memory rolling store shared between views."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.times     = deque()
        self.raw_L     = deque()
        self.raw_R     = deque()
        # CHANGE: Removed min_buf, max_buf, range_buf — drift tracking removed.
        self.lsi_buf   = deque()

        self.t0               = None
        # CHANGE: session_peak_raw replaced with session_peak_L and session_peak_R
        #         so both plates are tracked independently in Python.
        # CHANGE: session_peak_range removed — drift tracking removed.
        self.session_peak_L   = float("-inf")
        self.session_peak_R   = float("-inf")
        self.session_peak_lsi = 0.0
        self.session_max_diff = 0.0
        # CHANGE: last_count and last_range removed — no longer received from Arduino.
        self.last_raw_L       = 0.0
        self.last_raw_R       = 0.0
        self.last_lsi         = 0.0

    # CHANGE: Signature simplified from (count, raw_L, min_val, max_val, range_val, raw_R)
    #         to (raw_L, raw_R). All removed parameters were either drift-related
    #         or no longer sent by the Arduino.
    def ingest(self, raw_L, raw_R):
        t = time.time()
        if self.t0 is None:
            self.t0 = t
        t_rel = t - self.t0

        # CHANGE: Removed single-channel synthesis block (DUAL_CHANNEL = False branch).

        diff = raw_L - raw_R
        if diff > self.session_max_diff:
            self.session_max_diff = diff

        lsi_val = compute_lsi(raw_L, raw_R, self.session_max_diff)

        self.times.append(t_rel)
        self.raw_L.append(raw_L)
        self.raw_R.append(raw_R)
        # CHANGE: Removed min_buf, max_buf, range_buf appends.
        self.lsi_buf.append(lsi_val)

        # Prune rolling window
        cutoff = t_rel - PLOT_WINDOW
        while self.times and self.times[0] < cutoff:
            # CHANGE: Removed min_buf, max_buf, range_buf from prune loop.
            for buf in (self.times, self.raw_L, self.raw_R, self.lsi_buf):
                buf.popleft()

        # CHANGE: session_peak_raw split into session_peak_L and session_peak_R.
        # CHANGE: session_peak_range removed.
        if raw_L > self.session_peak_L:
            self.session_peak_L = raw_L
        if raw_R > self.session_peak_R:
            self.session_peak_R = raw_R
        if lsi_val > self.session_peak_lsi:
            self.session_peak_lsi = lsi_val

        # CHANGE: last_count and last_range removed.
        self.last_raw_L = raw_L
        self.last_raw_R = raw_R
        self.last_lsi   = lsi_val


# =====================================================================
# Clinician Dashboard
# =====================================================================
class ClinicianDashboard(QtWidgets.QWidget):
    def __init__(self, store: DataStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._setup_ui()

    def _setup_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        header = QtWidgets.QLabel("Clinician Dashboard — Force Plate Monitor")
        header.setStyleSheet("font-size: 18px; font-weight: 700; margin-bottom: 4px;")
        root.addWidget(header)

        # CHANGE: Row 1 reorganised — Drift Range and old single Peak Load removed.
        #         Peak Force L, Peak Force R, and Peak LSI are now in row 1
        #         as the primary emphasis. Live loads moved to row 2 as secondary.
        metric_row1 = QtWidgets.QHBoxLayout()
        self.m_pk_L   = self._metric_card("Peak Force L",    "0.0 kg", "#4a90d9", big=True)
        self.m_pk_R   = self._metric_card("Peak Force R",    "0.0 kg", "#bd10e0", big=True)
        self.m_pk_lsi = self._metric_card("Session Peak LSI","0 %",    "#7ed321", big=True)
        self.m_rts    = self._metric_card("RTS Status",      "—",      "#e8edf5", big=True)
        for w in [self.m_pk_L, self.m_pk_R, self.m_pk_lsi, self.m_rts]:
            metric_row1.addWidget(w)
        root.addLayout(metric_row1)

        # CHANGE: Row 2 now holds live loads and LSI (smaller, secondary emphasis).
        #         Removed m_range (drift). Removed m_pk_raw (replaced by m_pk_L/R above).
        metric_row2 = QtWidgets.QHBoxLayout()
        self.m_raw        = self._metric_card("Live Load L",    "0.0 kg", "#4a90d9")
        self.m_raw_R      = self._metric_card("Live Load R",    "0.0 kg", "#bd10e0")
        self.m_lsi        = self._metric_card("LSI",            "0 %",    "#7ed321")
        self.m_force_diff = self._metric_card("L − R Diff",     "0.0 kg", "#f5a623")
        self.m_force_pct_L = self._metric_card("Left Load %",   "50 %",   "#4a90d9")
        self.m_force_pct_R = self._metric_card("Right Load %",  "50 %",   "#bd10e0")
        for w in [self.m_raw, self.m_raw_R, self.m_lsi,
                  self.m_force_diff, self.m_force_pct_L, self.m_force_pct_R]:
            metric_row2.addWidget(w)
        root.addLayout(metric_row2)

        # ---- Plots ----
        pw = pg.GraphicsLayoutWidget()

        # Row 0: Load Cell Left
        self.plot_raw = pw.addPlot(row=0, col=0, title="Load Cell (L) — Force")
        self.plot_raw.showGrid(x=True, y=True)
        self.plot_raw.setLabel("left", "Force (kg)")
        # CHANGE: Y minimum set to 0 so axis never goes below zero.
        self.plot_raw.setYRange(0, 1, padding=0.1)
        self.curve_raw = self.plot_raw.plot(pen=pg.mkPen("#4a90d9", width=2), name="Left")
        self.peak_line = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen("r", style=QtCore.Qt.PenStyle.DashLine),
            label="Peak L", labelOpts={"position": 0.02, "color": "r"})
        self.plot_raw.addItem(self.peak_line)

        # Row 1: Load Cell Right
        pw.nextRow()
        self.plot_raw_R = pw.addPlot(row=1, col=0, title="Load Cell (R) — Force")
        self.plot_raw_R.showGrid(x=True, y=True)
        self.plot_raw_R.setLabel("left", "Force (kg)")
        # CHANGE: Y minimum set to 0.
        self.plot_raw_R.setYRange(0, 1, padding=0.1)
        self.curve_raw_R = self.plot_raw_R.plot(
            pen=pg.mkPen("#bd10e0", width=2), name="Right")
        self.peak_line_R = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen("#ff88ff", style=QtCore.Qt.PenStyle.DashLine),
            label="Peak R", labelOpts={"position": 0.02, "color": "#ff88ff"})
        self.plot_raw_R.addItem(self.peak_line_R)

        # Row 2: Left vs Right overlay
        pw.nextRow()
        self.plot_both = pw.addPlot(row=2, col=0, title="Force Comparison — Left vs Right")
        self.plot_both.showGrid(x=True, y=True)
        self.plot_both.setLabel("left", "Force (kg)")
        # CHANGE: Y minimum set to 0.
        self.plot_both.setYRange(0, 1, padding=0.1)
        self.plot_both.addLegend(offset=(10, 10))
        self.curve_both_L = self.plot_both.plot(
            pen=pg.mkPen("#4a90d9", width=2), name="Left")
        self.curve_both_R = self.plot_both.plot(
            pen=pg.mkPen("#bd10e0", width=2), name="Right")

        # CHANGE: Drift Range plot (row 3) removed entirely.

        # Row 3 (was row 4): LSI
        pw.nextRow()
        self.plot_lsi = pw.addPlot(
            row=3, col=0,
            title=f"LSI (%) — RTS threshold: {RTS_THRESHOLD:.0f}%  |  "
                  f"Formula: (xL − xR) / max(xL − xR) × 100")
        self.plot_lsi.showGrid(x=True, y=True)
        # CHANGE: Y minimum explicitly set to 0 (was implicitly bounded by setYRange).
        self.plot_lsi.setYRange(0, 130, padding=0.05)
        self.plot_lsi.setLabel("left", "LSI (%)")
        self.curve_lsi = self.plot_lsi.plot(pen=pg.mkPen("#7ed321", width=2))

        # RTS threshold line (green dashed at 90 %)
        rts_line = pg.InfiniteLine(
            pos=RTS_THRESHOLD, angle=0,
            pen=pg.mkPen("#1D9E75", style=QtCore.Qt.PenStyle.DashLine, width=2),
            label=f"RTS {RTS_THRESHOLD:.0f}%",
            labelOpts={"position": 0.95, "color": "#1D9E75"})
        self.plot_lsi.addItem(rts_line)

        # 100 % reference (perfect symmetry)
        self.plot_lsi.addItem(pg.InfiniteLine(
            pos=100, angle=0,
            pen=pg.mkPen("w", style=QtCore.Qt.PenStyle.DotLine, width=1),
            label="100% symmetry", labelOpts={"position": 0.05, "color": "w"}))

        root.addWidget(pw, 1)

        # ---- Status ----
        self.status_bar = QtWidgets.QLabel("Status: disconnected")
        self.status_bar.setStyleSheet("font-size: 12px; color: gray;")
        root.addWidget(self.status_bar)

    # CHANGE: Added big=False parameter to allow larger font for priority cards.
    def _metric_card(self, label: str, init: str,
                     accent: str = "#e8edf5", big: bool = False) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background: #1e2533; border: 1px solid #2e3a4e; border-radius: 6px; }")
        vl = QtWidgets.QVBoxLayout(frame)
        vl.setContentsMargins(10, 8, 10, 8)
        lbl = QtWidgets.QLabel(label)
        # CHANGE: Big cards use larger label font to emphasise peak metrics.
        lbl.setStyleSheet(f"font-size: {'13' if big else '11'}px; color: #8899aa;")
        val = QtWidgets.QLabel(init)
        val.setObjectName("value")
        # CHANGE: Big cards use 26px value font vs 18px for secondary cards.
        val.setStyleSheet(f"font-size: {'26' if big else '18'}px; font-weight: 700; color: {accent};")
        vl.addWidget(lbl)
        vl.addWidget(val)
        return frame

    def _set_card(self, frame: QtWidgets.QFrame, text: str,
                  color: str | None = None, big: bool = False):
        lbl = frame.findChild(QtWidgets.QLabel, "value")
        lbl.setText(text)
        if color:
            lbl.setStyleSheet(
                f"font-size: {'26' if big else '18'}px; font-weight: 700; color: {color};")

    def refresh(self):
        s = self.store
        if not s.times:
            return

        t       = np.array(s.times,   dtype=float)
        raw_l   = np.array(s.raw_L,   dtype=float)
        raw_r   = np.array(s.raw_R,   dtype=float)
        # CHANGE: rng array removed — drift tracking removed.
        lsi_arr = np.array(s.lsi_buf, dtype=float)

        x_min = max(0, t[-1] - PLOT_WINDOW)
        x_max = t[-1]

        # Update all curves
        self.curve_raw.setData(t, raw_l)
        self.curve_raw_R.setData(t, raw_r)
        self.curve_both_L.setData(t, raw_l)
        self.curve_both_R.setData(t, raw_r)
        # CHANGE: curve_rng.setData removed — drift plot removed.
        self.curve_lsi.setData(t, lsi_arr)

        # CHANGE: plot_rng removed from X range update loop.
        for plot in [self.plot_raw, self.plot_raw_R, self.plot_both, self.plot_lsi]:
            plot.setXRange(x_min, x_max, padding=0.02)

        # CHANGE: Per-plot Y range auto-scales above 0 minimum using session peaks.
        if s.session_peak_L != float("-inf"):
            y_max_L = max(s.session_peak_L * 1.1, 1.0)
            self.plot_raw.setYRange(0, y_max_L, padding=0.05)
        if s.session_peak_R != float("-inf"):
            y_max_R = max(s.session_peak_R * 1.1, 1.0)
            self.plot_raw_R.setYRange(0, y_max_R, padding=0.05)
        if s.session_peak_L != float("-inf") and s.session_peak_R != float("-inf"):
            y_max_both = max(s.session_peak_L, s.session_peak_R) * 1.1
            self.plot_both.setYRange(0, max(y_max_both, 1.0), padding=0.05)

        # CHANGE: Peak tracking now comes from DataStore (session_peak_L / session_peak_R)
        #         instead of local _peak_force_L / _peak_force_R variables.
        if s.session_peak_L != float("-inf"):
            self.peak_line.setValue(s.session_peak_L)
        if s.session_peak_R != float("-inf"):
            self.peak_line_R.setValue(s.session_peak_R)

        # Force load share percentages
        total = s.last_raw_L + s.last_raw_R
        if total > 0:
            pct_L = s.last_raw_L / total * 100.0
            pct_R = s.last_raw_R / total * 100.0
        else:
            pct_L = pct_R = 50.0

        diff = s.last_raw_L - s.last_raw_R
        diff_color = "#f5a623" if abs(diff) < 20 else "#D85A30"

        # RTS card
        rts_text, rts_color = rts_status(s.last_lsi)

        # CHANGE: Row 1 now shows peak values with big=True for larger font.
        self._set_card(self.m_pk_L,   f"{s.session_peak_L:.1f} kg" if s.session_peak_L != float("-inf") else "0.0 kg", big=True)
        self._set_card(self.m_pk_R,   f"{s.session_peak_R:.1f} kg" if s.session_peak_R != float("-inf") else "0.0 kg", big=True)
        self._set_card(self.m_pk_lsi, f"{s.session_peak_lsi:.1f} %", big=True)
        self._set_card(self.m_rts,    rts_text, rts_color, big=True)

        # CHANGE: Row 2 shows live values (smaller). m_range removed.
        self._set_card(self.m_raw,         f"{s.last_raw_L:.1f} kg")
        self._set_card(self.m_raw_R,       f"{s.last_raw_R:.1f} kg")
        self._set_card(self.m_lsi,         f"{s.last_lsi:.1f} %")
        self._set_card(self.m_force_diff,  f"{diff:+.1f} kg", diff_color)
        self._set_card(self.m_force_pct_L, f"{pct_L:.1f} %")
        self._set_card(self.m_force_pct_R, f"{pct_R:.1f} %")

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
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        header = QtWidgets.QLabel("Your Recovery Progress")
        header.setStyleSheet("font-size: 24px; font-weight: 700; color: #e8edf5;")
        header.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(header)

        # CHANGE: Removed INVOLVED_SIDE reference from subtitle.
        subtitle = QtWidgets.QLabel(
            f"Return-to-sport target: LSI ≥ {RTS_THRESHOLD:.0f}%")
        subtitle.setStyleSheet("font-size: 13px; color: #8899aa;")
        subtitle.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subtitle)

        # CHANGE: Peak force cards added at top of patient view for emphasis,
        #         above the LSI gauge.
        peak_row = QtWidgets.QHBoxLayout()
        self.p_pk_L   = self._peak_card("Peak Force LEFT",  "0.0 kg", "#4a90d9")
        self.p_pk_lsi = self._peak_card("Peak LSI",         "0 %",    "#7ed321")
        self.p_pk_R   = self._peak_card("Peak Force RIGHT", "0.0 kg", "#bd10e0")
        for w in [self.p_pk_L, self.p_pk_lsi, self.p_pk_R]:
            peak_row.addWidget(w)
        root.addLayout(peak_row)

        # CHANGE: Live load labels added below peaks, smaller than peak cards.
        live_row = QtWidgets.QHBoxLayout()
        self.p_live_L = self._live_card("Live LEFT",  "0.0 kg", "#4a90d9")
        self.p_live_R = self._live_card("Live RIGHT", "0.0 kg", "#bd10e0")
        for w in [self.p_live_L, self.p_live_R]:
            live_row.addWidget(w)
        root.addLayout(live_row)

        # Big symmetry gauge
        gauge_label = QtWidgets.QLabel("Limb Symmetry Index (LSI)")
        gauge_label.setStyleSheet(
            "font-size: 15px; color: #aabbcc; font-weight: 600;")
        gauge_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(gauge_label)

        self.lsi_number = QtWidgets.QLabel("— %")
        self.lsi_number.setStyleSheet(
            "font-size: 48px; font-weight: 700; color: #e8edf5;")
        self.lsi_number.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lsi_number)

        self.gauge_bar = QtWidgets.QProgressBar()
        self.gauge_bar.setRange(0, 100)
        self.gauge_bar.setValue(0)
        self.gauge_bar.setTextVisible(False)
        self.gauge_bar.setFixedHeight(32)
        self.gauge_bar.setStyleSheet("""
            QProgressBar {
                background: #2a3340;
                border-radius: 8px;
                border: none;
            }
            QProgressBar::chunk {
                border-radius: 8px;
                background: qlineargradient(x1:0, x2:1,
                    stop:0 #e74c3c, stop:0.5 #f39c12, stop:1 #2ecc71);
            }
        """)
        root.addWidget(self.gauge_bar)

        self.gauge_text = QtWidgets.QLabel("Step onto the plate to begin")
        self.gauge_text.setStyleSheet(
            "font-size: 18px; font-weight: 700; color: #aabbcc;")
        self.gauge_text.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.gauge_text)

        # Large feedback message
        self.feedback_label = QtWidgets.QLabel("Step onto the plate to begin")
        self.feedback_label.setStyleSheet(
            "font-size: 16px; color: #aabbcc; padding: 12px; "
            "background: #1e2533; border-radius: 8px;")
        self.feedback_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.feedback_label.setWordWrap(True)
        root.addWidget(self.feedback_label)

        # ---- Force bars (Left / Right) ----
        force_group = QtWidgets.QGroupBox("Live Force Loading")
        force_group.setStyleSheet("""
            QGroupBox {
                font-size: 13px; font-weight: 600; color: #aabbcc;
                border: 1px solid #2e3a4e; border-radius: 6px;
                margin-top: 8px; padding-top: 12px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; }
        """)
        force_layout = QtWidgets.QGridLayout(force_group)
        force_layout.setSpacing(8)

        # Labels
        left_side_lbl  = QtWidgets.QLabel("LEFT")
        right_side_lbl = QtWidgets.QLabel("RIGHT")
        for lbl, color in [(left_side_lbl, "#4a90d9"), (right_side_lbl, "#bd10e0")]:
            lbl.setStyleSheet(
                f"font-size: 13px; color: {color}; font-weight: 700; min-width: 50px;")
            lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight |
                             QtCore.Qt.AlignmentFlag.AlignVCenter)

        # Force bars (raw value, normalised to session peak for bar width)
        self.left_bar = self._make_force_bar("#4a90d9")
        self.right_bar = self._make_force_bar("#bd10e0")

        # Percentage share bars
        self.left_pct_bar  = self._make_pct_bar("#4a90d9")
        self.right_pct_bar = self._make_pct_bar("#bd10e0")

        # Value labels
        self.left_force_lbl  = QtWidgets.QLabel("0.0 kg")
        self.right_force_lbl = QtWidgets.QLabel("0.0 kg")
        self.left_pct_lbl    = QtWidgets.QLabel("50 %")
        self.right_pct_lbl   = QtWidgets.QLabel("50 %")
        for lbl in [self.left_force_lbl, self.right_force_lbl,
                    self.left_pct_lbl, self.right_pct_lbl]:
            lbl.setStyleSheet("font-size: 13px; color: #e8edf5; min-width: 60px;")

        # Row headers
        force_hdr = QtWidgets.QLabel("Force (kg)")
        pct_hdr   = QtWidgets.QLabel("Load share (%)")
        for hdr in [force_hdr, pct_hdr]:
            hdr.setStyleSheet("font-size: 11px; color: #556677;")

        # Grid layout:  col0=side, col1=bar, col2=value
        force_layout.addWidget(force_hdr,            0, 1)
        force_layout.addWidget(left_side_lbl,         1, 0)
        force_layout.addWidget(self.left_bar,          1, 1)
        force_layout.addWidget(self.left_force_lbl,   1, 2)
        force_layout.addWidget(right_side_lbl,         2, 0)
        force_layout.addWidget(self.right_bar,         2, 1)
        force_layout.addWidget(self.right_force_lbl,  2, 2)

        force_layout.addWidget(pct_hdr,               3, 1)
        force_layout.addWidget(self.left_pct_bar,     4, 1)
        force_layout.addWidget(self.left_pct_lbl,     4, 2)
        force_layout.addWidget(self.right_pct_bar,    5, 1)
        force_layout.addWidget(self.right_pct_lbl,    5, 2)

        force_layout.setColumnStretch(1, 1)
        root.addWidget(force_group)

        root.addStretch()

        # CHANGE: count_label removed — sample count no longer sent by Arduino.

    # CHANGE: New helper for large peak cards in patient view.
    @staticmethod
    def _peak_card(label: str, init: str, color: str) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background: #1e2533; border: 1px solid #2e3a4e; border-radius: 8px; }")
        vl = QtWidgets.QVBoxLayout(frame)
        vl.setContentsMargins(14, 12, 14, 12)
        lbl = QtWidgets.QLabel(label)
        lbl.setStyleSheet("font-size: 13px; color: #8899aa; font-weight: 600;")
        lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        val = QtWidgets.QLabel(init)
        val.setObjectName("value")
        val.setStyleSheet(f"font-size: 30px; font-weight: 700; color: {color};")
        val.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(lbl)
        vl.addWidget(val)
        return frame

    # CHANGE: New helper for smaller live load cards in patient view.
    @staticmethod
    def _live_card(label: str, init: str, color: str) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background: #1a2230; border: 1px solid #2e3a4e; border-radius: 6px; }")
        vl = QtWidgets.QVBoxLayout(frame)
        vl.setContentsMargins(10, 8, 10, 8)
        lbl = QtWidgets.QLabel(label)
        lbl.setStyleSheet("font-size: 11px; color: #556677;")
        lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        val = QtWidgets.QLabel(init)
        val.setObjectName("value")
        val.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {color};")
        val.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(lbl)
        vl.addWidget(val)
        return frame

    @staticmethod
    def _set_label(frame: QtWidgets.QFrame, text: str):
        lbl = frame.findChild(QtWidgets.QLabel, "value")
        if lbl:
            lbl.setText(text)

    @staticmethod
    def _make_force_bar(color: str) -> QtWidgets.QProgressBar:
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 1000)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(22)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background: #2a3340; border-radius: 5px; border: none;
            }}
            QProgressBar::chunk {{
                background: {color}; border-radius: 5px;
            }}
        """)
        return bar

    @staticmethod
    def _make_pct_bar(color: str) -> QtWidgets.QProgressBar:
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(50)
        bar.setTextVisible(False)
        bar.setFixedHeight(18)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background: #2a3340; border-radius: 4px; border: none;
            }}
            QProgressBar::chunk {{
                background: {color}80; border-radius: 4px;
            }}
        """)
        return bar

    def _lsi_to_display(self, lsi_val: float):
        """Map LSI % to gauge score (0-100), label, and color for the patient view."""
        score = int(min(lsi_val, 100))
        if lsi_val >= RTS_THRESHOLD:
            return score, "Return to sport cleared!", "#1D9E75"
        gap = RTS_THRESHOLD - lsi_val
        if gap <= 5:
            return score, f"Almost there — {gap:.1f}% to go", "#BA7517"
        if gap <= 15:
            return score, f"{gap:.1f}% below RTS target", "#BA7517"
        return score, f"{gap:.1f}% below RTS target", "#D85A30"

    def _lsi_to_feedback(self, lsi_val: float) -> str:
        if lsi_val >= RTS_THRESHOLD:
            return (f"LSI {lsi_val:.1f}% — you've reached the {RTS_THRESHOLD:.0f}% "
                    f"return-to-sport threshold. Great work!")
        gap = RTS_THRESHOLD - lsi_val
        if gap <= 5:
            return (f"LSI {lsi_val:.1f}% — you're very close to the "
                    f"{RTS_THRESHOLD:.0f}% RTS target. Keep pushing!")
        if gap <= 15:
            return (f"LSI {lsi_val:.1f}% — your involved limb is loading well. "
                    f"Close the {gap:.1f}% gap with continued rehab.")
        return (f"LSI {lsi_val:.1f}% — your involved limb is loading less than your "
                f"uninvolved side. Focus on equal weight-bearing.")

    def refresh(self):
        s = self.store
        if not s.times:
            return

        # LSI gauge
        score, rts_label, color = self._lsi_to_display(s.last_lsi)
        feedback = self._lsi_to_feedback(s.last_lsi)

        self.lsi_number.setText(f"{s.last_lsi:.1f} %")
        self.lsi_number.setStyleSheet(
            f"font-size: 48px; font-weight: 700; color: {color};")
        self.gauge_bar.setValue(score)
        self.gauge_text.setText(rts_label)
        self.gauge_text.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {color};")
        self.feedback_label.setText(feedback)
        # CHANGE: count_label update removed.

        # CHANGE: Peak cards updated from DataStore peaks.
        self._set_label(self.p_pk_L,
            f"{s.session_peak_L:.1f} kg" if s.session_peak_L != float("-inf") else "0.0 kg")
        self._set_label(self.p_pk_R,
            f"{s.session_peak_R:.1f} kg" if s.session_peak_R != float("-inf") else "0.0 kg")
        self._set_label(self.p_pk_lsi, f"{s.session_peak_lsi:.1f} %")

        # CHANGE: Live load cards updated.
        self._set_label(self.p_live_L, f"{s.last_raw_L:.1f} kg")
        self._set_label(self.p_live_R, f"{s.last_raw_R:.1f} kg")

        # Force bars
        # CHANGE: session_peak_raw replaced with max of session_peak_L/R.
        session_max = max(
            s.session_peak_L if s.session_peak_L != float("-inf") else 0.0,
            s.session_peak_R if s.session_peak_R != float("-inf") else 0.0,
            1.0)
        self.left_bar.setMaximum(int(session_max * 1.1))
        self.right_bar.setMaximum(int(session_max * 1.1))
        self.left_bar.setValue(int(max(s.last_raw_L, 0)))
        self.right_bar.setValue(int(max(s.last_raw_R, 0)))
        self.left_force_lbl.setText(f"{s.last_raw_L:.1f} kg")
        self.right_force_lbl.setText(f"{s.last_raw_R:.1f} kg")

        # Load-share percentage bars
        total = s.last_raw_L + s.last_raw_R
        if total > 0:
            pct_L = s.last_raw_L / total * 100.0
            pct_R = s.last_raw_R / total * 100.0
        else:
            pct_L = pct_R = 50.0
        self.left_pct_bar.setValue(int(pct_L))
        self.right_pct_bar.setValue(int(pct_R))
        self.left_pct_lbl.setText(f"{pct_L:.1f} %")
        self.right_pct_lbl.setText(f"{pct_R:.1f} %")


# =====================================================================
# Main window — tabbed
# =====================================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Force Plate — Live Monitor")
        self.resize(1400, 900)

        # Dark palette
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.ColorRole.Window,
                         QtGui.QColor("#141b24"))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText,
                         QtGui.QColor("#e8edf5"))
        palette.setColor(QtGui.QPalette.ColorRole.Base,
                         QtGui.QColor("#1e2533"))
        palette.setColor(QtGui.QPalette.ColorRole.AlternateBase,
                         QtGui.QColor("#232b38"))
        palette.setColor(QtGui.QPalette.ColorRole.Text,
                         QtGui.QColor("#e8edf5"))
        QtWidgets.QApplication.instance().setPalette(palette)

        self.store = DataStore()

        self.tabs = QtWidgets.QTabWidget()
        self.clinician_view = ClinicianDashboard(self.store)
        self.patient_view   = PatientDashboard(self.store)

        self.tabs.addTab(self.clinician_view, "Clinician View")
        self.tabs.addTab(self.patient_view,   "Patient View")
        self.setCentralWidget(self.tabs)

        self.reader = UdpReader()
        self.reader.sample_received.connect(self._on_sample)
        self.reader.status.connect(self._on_status)
        self.reader.start()

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(33)   # ~30 fps
        self.timer.timeout.connect(self._refresh)
        self.timer.start()

    # CHANGE: Slot signature updated from (int, float, float, float, float, float)
    #         to (float, float) to match the new signal.
    @QtCore.pyqtSlot(float, float)
    def _on_sample(self, raw_L, raw_R):
        self.store.ingest(raw_L, raw_R)

    def _on_status(self, msg: str):
        self.clinician_view.set_status(msg)
        self.statusBar().showMessage(msg)

    def _refresh(self):
        self.clinician_view.refresh()
        self.patient_view.refresh()

    def closeEvent(self, ev):
        self.reader.stop()
        self.reader.wait(1000)
        ev.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
