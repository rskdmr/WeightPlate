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
PLOT_WINDOW = 5  # seconds shown in plots
BAUD_RATE = 9600  # must match your Arduino code

# -----------------------------
# Serial reader thread
# -----------------------------
class SerialReader(QtCore.QThread):
    sample_received = QtCore.pyqtSignal(float, float)
    status = QtCore.pyqtSignal(str)

    def __init__(self, port: str, baud: int = BAUD_RATE, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self._running = True
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(2)  # allow Arduino to reset
            self.ser.reset_input_buffer()
            self.status.emit(f"Connected to {self.port} @ {self.baud}")

            while self._running:
                raw = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if not raw:
                    continue

                # Expected Arduino line:
                # Reading: 12.3 kgs
                if "Reading:" not in raw:
                    continue

                try:
                    # Pull out the numeric portion
                    # Example: "Reading: 12.3 kgs" -> 12.3
                    value_str = raw.split("Reading:")[1].strip().split()[0]
                    force = float(value_str)
                except Exception:
                    continue

                self.sample_received.emit(time.time(), force)

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


# -----------------------------
# Main window
# -----------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, serial_port: str = None):
        super().__init__()
        self.setWindowTitle("Force Plate — Live Total Force")
        self.resize(1200, 700)

        self.buffer_len = int(200 * PLOT_WINDOW)
        self.times = deque(maxlen=self.buffer_len)
        self.force_buffer = deque(maxlen=self.buffer_len)
        self.peak_force_buffer = deque(maxlen=self.buffer_len)

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

        # Total force plot
        self.plot_force = plot_widget.addPlot(row=0, col=0, title="Total Force")
        self.plot_force.showGrid(x=True, y=True)
        self.force_curve = self.plot_force.plot(pen=pg.mkPen(width=2))
        plot_widget.nextRow()

        # Peak force plot
        self.plot_peak = plot_widget.addPlot(row=1, col=0, title=f"Peak Force (rolling {PLOT_WINDOW}s)")
        self.plot_peak.showGrid(x=True, y=True)
        self.peak_curve = self.plot_peak.plot(pen=pg.mkPen(style=QtCore.Qt.PenStyle.DashLine))
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

        self.total_label = QtWidgets.QLabel("Total Force: 0.0")
        self.peak_label = QtWidgets.QLabel("Rolling Peak: 0.0")
        self.session_peak_label_widget = QtWidgets.QLabel("Session Peak: 0.0")
        self.status_label = QtWidgets.QLabel("Status: disconnected")

        right_layout.addWidget(self.total_label)
        right_layout.addWidget(self.peak_label)
        right_layout.addWidget(self.session_peak_label_widget)
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

    @QtCore.pyqtSlot(float, float)
    def on_sample_received(self, t, force):
        if self.t0 is None:
            self.t0 = t

        t_rel = t - self.t0

        self.times.append(t_rel)
        self.force_buffer.append(force)

        current_peak = max(self.force_buffer) if self.force_buffer else 0.0
        self.peak_force_buffer.append(current_peak)

        if current_peak > self.session_peak:
            self.session_peak = current_peak

    def update_plots(self):
        if not self.times:
            return

        t_rel = np.array(self.times)
        force_arr = np.array(self.force_buffer)
        peak_arr = np.array(self.peak_force_buffer)

        self.force_curve.setData(t_rel, force_arr)
        self.peak_curve.setData(t_rel, peak_arr)

        self.session_peak_line.setValue(self.session_peak)

        try:
            x_pos = t_rel[-1]
            self.session_peak_label.setText(f"Session peak: {self.session_peak:.1f}")
            self.session_peak_label.setPos(x_pos, self.session_peak)
        except Exception:
            pass

        self.total_label.setText(f"Total Force: {force_arr[-1]:.1f}")
        self.peak_label.setText(f"Rolling Peak ({PLOT_WINDOW}s): {peak_arr[-1]:.1f}")
        self.session_peak_label_widget.setText(f"Session Peak: {self.session_peak:.1f}")

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
