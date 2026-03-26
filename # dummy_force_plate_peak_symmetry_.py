# dummy_force_plate_peak_symmetry.py
import sys
import time
import math
from collections import deque

import numpy as np # pyright: ignore[reportMissingImports]
from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg

# -----------------------------
# Configuration
# -----------------------------
NUM_SENSORS = 4
SAMPLE_RATE = 200  # Hz
PLOT_WINDOW = 5  # seconds of data shown in plots

# sensor layout (for left/right grouping)
SENSOR_POSITIONS = np.array([
    [-0.5, -0.5],  # s1 (left-back)
    [ 0.5, -0.5],  # s2 (right-back)
    [ 0.5,  0.5],  # s3 (right-front)
    [-0.5,  0.5],  # s4 (left-front)
])

# Which sensors count as left / right (indices)
LEFT_INDICES = [0, 3]
RIGHT_INDICES = [1, 2]

# ---------------------------------
# Main Window
# ---------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dummy Force Plate — Peak Force & Symmetry")
        self.resize(1200, 700)

        # buffers sized for PLOT_WINDOW * SAMPLE_RATE
        self.buffer_len = int(SAMPLE_RATE * PLOT_WINDOW)
        self.times = deque(maxlen=self.buffer_len)
        self.sensor_buffers = [deque(maxlen=self.buffer_len) for _ in range(NUM_SENSORS)]
        self.force_buffer = deque(maxlen=self.buffer_len)

        # metrics buffers
        self.peak_force_buffer = deque(maxlen=self.buffer_len)      # rolling peak over window
        self.symmetry_buffer = deque(maxlen=self.buffer_len)

        # session-level peak
        self.session_peak = 0.0

        self.start_time = time.time()

        self._setup_ui()

        # simulate data timer
        self.timer = QtCore.QTimer()
        self.timer.setInterval(int(1000 / SAMPLE_RATE))
        self.timer.timeout.connect(self.generate_dummy_data)
        self.timer.start()

        # plot refresh timer (~30 Hz)
        self.refresh_timer = QtCore.QTimer()
        self.refresh_timer.setInterval(33)
        self.refresh_timer.timeout.connect(self.update_plots)
        self.refresh_timer.start()

    def _setup_ui(self):
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)

        split = QtWidgets.QHBoxLayout()

        # Left: plots (total force, peak force, symmetry)
        plot_widget = pg.GraphicsLayoutWidget()

        # Total force plot
        self.plot_force = plot_widget.addPlot(row=0, col=0, title="Total Force (sum of sensors)")
        self.plot_force.showGrid(x=True, y=True)
        self.force_curve = self.plot_force.plot(pen=pg.mkPen(width=2))
        plot_widget.nextRow()

        # Peak force plot (rolling peak)
        self.plot_peak = plot_widget.addPlot(row=1, col=0, title=f"Peak Force (rolling {PLOT_WINDOW}s)")
        self.plot_peak.showGrid(x=True, y=True)
        self.peak_curve = self.plot_peak.plot(pen=pg.mkPen(style=QtCore.Qt.PenStyle.DashLine))
        # session peak line
        self.session_peak_line = pg.InfiniteLine(pos=self.session_peak, angle=0, pen=pg.mkPen('r', style=QtCore.Qt.PenStyle.DashLine))
        self.plot_peak.addItem(self.session_peak_line)
        self.session_peak_label = pg.TextItem(anchor=(1,1))
        self.plot_peak.addItem(self.session_peak_label)
        plot_widget.nextRow()

        # Symmetry plot (-1 .. 1)
        self.plot_sym = plot_widget.addPlot(row=2, col=0, title="Force Symmetry (Right-Left) / (Right+Left)")
        self.plot_sym.showGrid(x=True, y=True)
        # horizontal zero line
        self.plot_sym.addLine(y=0, pen=pg.mkPen('w', width=1))
        self.sym_curve = self.plot_sym.plot(pen=pg.mkPen(width=2))
        plot_widget.ci.layout.setRowStretch(0, 1)
        plot_widget.ci.layout.setRowStretch(1, 1)
        plot_widget.ci.layout.setRowStretch(2, 1)

        split.addWidget(plot_widget, 3)

        # Right: numeric readouts & simple plate marker
        right_layout = QtWidgets.QVBoxLayout()

        # small plate schematic (for reference)
        self.plate_view = pg.PlotWidget(title="Plate (sensors marked)")
        self.plate_view.setAspectLocked(True)
        self.plate_view.setXRange(-1, 1)
        self.plate_view.setYRange(-1, 1)
        self.plate_view.plot(SENSOR_POSITIONS[:,0], SENSOR_POSITIONS[:,1], pen=None, symbol='o')
        right_layout.addWidget(self.plate_view, stretch=2)

        # numeric labels
        self.total_label = QtWidgets.QLabel("Total Force: 0.0")
        self.peak_label = QtWidgets.QLabel("Rolling Peak: 0.0")
        self.session_peak_label_widget = QtWidgets.QLabel("Session Peak: 0.0")
        self.sym_label = QtWidgets.QLabel("Symmetry: 0.0")

        right_layout.addWidget(self.total_label)
        right_layout.addWidget(self.peak_label)
        right_layout.addWidget(self.session_peak_label_widget)
        right_layout.addWidget(self.sym_label)
        right_layout.addStretch()

        split.addLayout(right_layout, 2)

        layout.addLayout(split)
        self.setCentralWidget(central)

    # -----------------------------
    # Dummy sensor data simulation
    # -----------------------------
    def generate_dummy_data(self):
        t = time.time() - self.start_time

        # base force and simulated movement (sine waves + occasional peak)
        base_force = 500

        # create a periodic "weight shift" and occasional stomp
        s1 = base_force + 120 * math.sin(2.0 * t + 0.1) + 20 * math.sin(7*t)
        s2 = base_force + 120 * math.sin(2.0 * t + 1.0) + 15 * math.sin(5*t)
        s3 = base_force + 120 * math.sin(2.0 * t + 2.0) + 10 * math.sin(9*t)
        s4 = base_force + 120 * math.sin(2.0 * t + 3.0) + 5  * math.sin(4*t)

        # occasional transient peak (simulate step) ~ 1% chance each sample
        if np.random.rand() < 0.01:
            stomp = 2000 * np.random.rand()
            # add stomp more to one side randomly
            if np.random.rand() < 0.5:
                s2 += stomp
                s3 += stomp * 0.6
            else:
                s1 += stomp
                s4 += stomp * 0.6

        sensors = np.array([s1, s2, s3, s4])
        total = float(sensors.sum())

        # compute left/right totals for symmetry
        left_total = float(sensors[LEFT_INDICES].sum())
        right_total = float(sensors[RIGHT_INDICES].sum())
        denom = right_total + left_total
        if denom != 0:
            symmetry = (right_total - left_total) / denom
        else:
            symmetry = 0.0

        # append to buffers
        self.times.append(t)
        for i in range(NUM_SENSORS):
            self.sensor_buffers[i].append(float(sensors[i]))
        self.force_buffer.append(total)
        self.symmetry_buffer.append(symmetry)

        # compute rolling peak over current buffer (peak of force_buffer)
        current_peak = max(self.force_buffer) if self.force_buffer else 0.0
        self.peak_force_buffer.append(current_peak)

        # update session peak
        if current_peak > self.session_peak:
            self.session_peak = current_peak

    # -----------------------------
    # Update plots (UI thread)
    # -----------------------------
    def update_plots(self):
        if not self.times:
            return

        t0 = self.times[0]
        t_rel = np.array(self.times) - t0

        # total force
        self.force_curve.setData(t_rel, np.array(self.force_buffer))

        # rolling peak
        self.peak_curve.setData(t_rel, np.array(self.peak_force_buffer))
        # update session peak line and label
        self.session_peak_line.setValue(self.session_peak)
        self.session_peak_label.setText(f"Session peak: {self.session_peak:.1f}")
        # also update numeric label
        self.total_label.setText(f"Total Force: {self.force_buffer[-1]:.1f}")
        self.peak_label.setText(f"Rolling Peak ({PLOT_WINDOW}s): {self.peak_force_buffer[-1]:.1f}")
        self.session_peak_label_widget.setText(f"Session Peak: {self.session_peak:.1f}")

        # symmetry
        self.sym_curve.setData(t_rel, np.array(self.symmetry_buffer))
        self.sym_label.setText(f"Symmetry: {self.symmetry_buffer[-1]:.3f}")

    def closeEvent(self, ev):
        # nothing special to close for dummy app
        ev.accept()

# -----------------------------
# Run App
# -----------------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()