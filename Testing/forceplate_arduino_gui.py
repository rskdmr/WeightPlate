import sys
import time
from collections import deque

import numpy as np
import serial
from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg

# -----------------------------
# Configuration
# -----------------------------
PLOT_WINDOW = 5.0   # seconds shown in plots
BAUD_RATE = 9600    # must match Arduino code

# -----------------------------
# Serial reader thread
# -----------------------------
class SerialReader(QtCore.QThread):
    sample_received = QtCore.pyqtSignal(float, float, float, float, float)
    status = QtCore.pyqtSignal(str)

    def __init__(self, port: str, baud: int = BAUD_RATE, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self._running = True
        self.ser = None

    def parse_line(self, raw: str):
        raw = raw.strip()
        if not raw:
            return None

        # Format 1:
        # count|raw|min|max|range
        if "|" in raw:
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) != 5:
                return None

            try:
                reading_count = float(parts[0])
                raw_value = float(parts[1])
                min_value = float(parts[2])
                max_value = float(parts[3])
                range_value = float(parts[4])
                return reading_count, raw_value, min_value, max_value, range_value
            except ValueError:
                return None

        # Format 2:
        # Reading: 12.345 kgs
        if "Reading:" in raw:
            try:
                value_str = raw.split("Reading:", 1)[1].strip().split()[0]
                value = float(value_str)
                return 0.0, value, np.nan, np.nan, np.nan
            except Exception:
                return None

        return None

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(2)  # allow Arduino to reset
            self.ser.reset_input_buffer()
            self.status.emit(f"Connected to {self.port} @ {self.baud}")

            while self._running:
                line = self.ser.readline().decode("utf-8", errors="ignore")
                if not line:
                    continue

                parsed = self.parse_line(line)
                if parsed is None:
                    continue

                reading_count, raw_value, min_value, max_value, range_value = parsed
                self.sample_received.emit(
                    time.time(),
                    raw_value,
                    min_value,
                    max_value,
                    range_value,
                )

        except Exception as e:
            self.status.emit(f"Serial error: {e}")

        finally:
            try:
                if self.ser and self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass

    def stop(self):
        self._running = False
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass


# -----------------------------
# Main window
# -----------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, serial_port: str = None):
        super().__init__()
        self.setWindowTitle("HX711 Live Reading")
        self.resize(1200, 700)

        self.times = deque()
        self.value_buffer = deque()

        self.peak_times = deque()
        self.peak_buffer = deque()

        self.session_peak = 0.0
        self.t0 = None

        self.reader = None
        self.serial_port = serial_port

        self._setup_ui()

        if self.serial_port:
            self.start_serial(self.serial_port)
        else:
            self.statusBar().showMessage("No serial port selected. App is idle.")

        self.refresh_timer = QtCore.QTimer()
        self.refresh_timer.setInterval(33)
        self.refresh_timer.timeout.connect(self.update_plots)
        self.refresh_timer.start()

    def _setup_ui(self):
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)

        split = QtWidgets.QHBoxLayout()
        plot_widget = pg.GraphicsLayoutWidget()

        # Live reading plot
        self.plot_raw = plot_widget.addPlot(row=0, col=0, title="Live Reading")
        self.plot_raw.showGrid(x=True, y=True)
        self.raw_curve = self.plot_raw.plot(pen=pg.mkPen(width=2))
        plot_widget.nextRow()

        # Rolling peak plot
        self.plot_peak = plot_widget.addPlot(
            row=1, col=0, title=f"Rolling Peak (last {PLOT_WINDOW:.0f}s)"
        )
        self.plot_peak.showGrid(x=True, y=True)
        self.peak_curve = self.plot_peak.plot(
            pen=pg.mkPen(width=2, style=QtCore.Qt.PenStyle.DashLine)
        )

        self.session_peak_line = pg.InfiniteLine(
            pos=0,
            angle=0,
            pen=pg.mkPen("r", style=QtCore.Qt.PenStyle.DashLine),
        )
        self.plot_peak.addItem(self.session_peak_line)

        self.session_peak_label = pg.TextItem(anchor=(1, 1))
        self.plot_peak.addItem(self.session_peak_label)

        split.addWidget(plot_widget, 3)

        right_layout = QtWidgets.QVBoxLayout()

        self.raw_label = QtWidgets.QLabel("Reading: 0.000")
        self.rolling_peak_label = QtWidgets.QLabel("Rolling Peak: 0.000")
        self.session_peak_label_widget = QtWidgets.QLabel("Session Peak: 0.000")
        self.min_label = QtWidgets.QLabel("Min: N/A")
        self.max_label = QtWidgets.QLabel("Max: N/A")
        self.range_label = QtWidgets.QLabel("Range: N/A")
        self.status_label = QtWidgets.QLabel("Status: disconnected")

        right_layout.addWidget(self.raw_label)
        right_layout.addWidget(self.rolling_peak_label)
        right_layout.addWidget(self.session_peak_label_widget)
        right_layout.addWidget(self.min_label)
        right_layout.addWidget(self.max_label)
        right_layout.addWidget(self.range_label)
        right_layout.addWidget(self.status_label)
        right_layout.addStretch()

        split.addLayout(right_layout, 2)
        layout.addLayout(split)
        self.setCentralWidget(central)

    def start_serial(self, port: str):
        self.reader = SerialReader(port=port, baud=BAUD_RATE)
        self.reader.sample_received.connect(self.on_sample_received)
        self.reader.status.connect(self.on_status)
        self.reader.start()

    def on_status(self, msg: str):
        self.status_label.setText(f"Status: {msg}")
        self.statusBar().showMessage(msg)

    @QtCore.pyqtSlot(float, float, float, float, float)
    def on_sample_received(self, t, raw_value, min_value, max_value, range_value):
        if self.t0 is None:
            self.t0 = t

        t_rel = t - self.t0

        self.times.append(t_rel)
        self.value_buffer.append(raw_value)

        # Keep only the last PLOT_WINDOW seconds
        while self.times and (t_rel - self.times[0] > PLOT_WINDOW):
            self.times.popleft()
            self.value_buffer.popleft()

        current_peak = max(self.value_buffer) if self.value_buffer else raw_value
        self.peak_times.append(t_rel)
        self.peak_buffer.append(current_peak)

        while self.peak_times and (t_rel - self.peak_times[0] > PLOT_WINDOW):
            self.peak_times.popleft()
            self.peak_buffer.popleft()

        if current_peak > self.session_peak:
            self.session_peak = current_peak

        self.raw_label.setText(f"Reading: {raw_value:.3f}")

        if np.isnan(min_value):
            self.min_label.setText("Min: N/A")
        else:
            self.min_label.setText(f"Min: {min_value:.3f}")

        if np.isnan(max_value):
            self.max_label.setText("Max: N/A")
        else:
            self.max_label.setText(f"Max: {max_value:.3f}")

        if np.isnan(range_value):
            self.range_label.setText("Range: N/A")
        else:
            self.range_label.setText(f"Range: {range_value:.3f}")

    def update_plots(self):
        if not self.times:
            return

        t_arr = np.array(self.times, dtype=float)
        value_arr = np.array(self.value_buffer, dtype=float)
        peak_t_arr = np.array(self.peak_times, dtype=float)
        peak_arr = np.array(self.peak_buffer, dtype=float)

        self.raw_curve.setData(t_arr, value_arr)
        self.peak_curve.setData(peak_t_arr, peak_arr)

        self.session_peak_line.setValue(self.session_peak)

        try:
            x_pos = peak_t_arr[-1]
            self.session_peak_label.setText(f"Session peak: {self.session_peak:.3f}")
            self.session_peak_label.setPos(x_pos, self.session_peak)
        except Exception:
            pass

        self.rolling_peak_label.setText(
            f"Rolling Peak ({PLOT_WINDOW:.0f}s): {peak_arr[-1]:.3f}"
        )
        self.session_peak_label_widget.setText(f"Session Peak: {self.session_peak:.3f}")

    def closeEvent(self, ev):
        if self.reader:
            self.reader.stop()
            self.reader.wait(1000)
        ev.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)

    # Run like:
    # python force_plate_gui.py COM3
    # python force_plate_gui.py /dev/ttyACM0
    serial_port = sys.argv[1] if len(sys.argv) > 1 else None

    win = MainWindow(serial_port=serial_port)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
