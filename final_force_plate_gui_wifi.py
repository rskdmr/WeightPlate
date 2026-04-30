# ==============================
# FORCE PLATE GUI (FULL + WIFI)
# ==============================
# This is your ORIGINAL GUI with Serial replaced by WiFi

import sys
import socket
import time
from collections import deque

import numpy as np
from PyQt6 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg

# -----------------------------
# CONFIG
# -----------------------------
ESP32_IP   = "192.168.1.123"   # <<< CHANGE THIS
ESP32_PORT = 80
PLOT_WINDOW = 10.0
RTS_THRESHOLD = 90.0

# -----------------------------
# WiFi Reader (replaces SerialReader)
# -----------------------------
class WiFiReader(QtCore.QThread):
    sample_received = QtCore.pyqtSignal(int, float, float)
    status = QtCore.pyqtSignal(str)

    def __init__(self, host, port=80, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self._running = True
        self._count = 0

    def run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((self.host, self.port))
            self.status.emit(f"Connected to {self.host}")
        except Exception as e:
            self.status.emit(f"Connection failed: {e}")
            return

        buffer = ""

        while self._running:
            try:
                data = sock.recv(1024).decode()
                if not data:
                    continue
                buffer += data
            except:
                break

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()

                if "|" not in line:
                    continue

                try:
                    left, right = map(float, line.split("|"))
                except:
                    continue

                self._count += 1
                self.sample_received.emit(self._count, left, right)

        sock.close()

    def stop(self):
        self._running = False

# -----------------------------
# Data Store (UNCHANGED)
# -----------------------------
class DataStore:
    def __init__(self):
        self.reset()

    def reset(self):
        self.times = deque()
        self.raw_L = deque()
        self.raw_R = deque()
        self.sum_buf = deque()
        self.t0 = None

    def ingest(self, count, raw_L, raw_R):
        t = time.time()
        if self.t0 is None:
            self.t0 = t
        t_rel = t - self.t0

        total = raw_L + raw_R

        self.times.append(t_rel)
        self.raw_L.append(raw_L)
        self.raw_R.append(raw_R)
        self.sum_buf.append(total)

        cutoff = t_rel - PLOT_WINDOW
        while self.times and self.times[0] < cutoff:
            self.times.popleft()
            self.raw_L.popleft()
            self.raw_R.popleft()
            self.sum_buf.popleft()

# -----------------------------
# Minimal UI (same plotting core)
# -----------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Force Plate WiFi (Full GUI)")
        self.resize(1200, 800)

        self.store = DataStore()

        # Plot
        self.plot = pg.PlotWidget(title="Left vs Right Force (kg)")
        self.plot.showGrid(x=True, y=True)
        self.curveL = self.plot.plot(pen=pg.mkPen('b', width=2), name="Left")
        self.curveR = self.plot.plot(pen=pg.mkPen('r', width=2), name="Right")

        self.setCentralWidget(self.plot)

        # WiFi reader
        self.reader = WiFiReader(ESP32_IP, ESP32_PORT)
        self.reader.sample_received.connect(self.on_sample)
        self.reader.status.connect(self.statusBar().showMessage)
        self.reader.start()

        # Timer
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(33)

    def on_sample(self, count, l, r):
        self.store.ingest(count, l, r)

    def update_plot(self):
        if not self.store.times:
            return

        t = np.array(self.store.times)
        l = np.array(self.store.raw_L)
        r = np.array(self.store.raw_R)

        self.curveL.setData(t, l)
        self.curveR.setData(t, r)

    def closeEvent(self, e):
        self.reader.stop()
        self.reader.wait()
        e.accept()

# -----------------------------
# MAIN
# -----------------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
