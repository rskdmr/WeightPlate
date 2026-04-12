#include "HX711.h"

#define LOADCELL_DOUT_PIN   6
#define LOADCELL_SCK_PIN    5
#define VREF_MV             20.0
#define ADC_FULL_SCALE      8388608.0
#define MEDIAN_SIZE         3
#define EMA_ALPHA           0.4
#define CALIBRATION_FACTOR  16.894  // kgs/mV

HX711 scale;

// --- Median filter ---
float medianBuf[MEDIAN_SIZE];
int   medianIndex = 0;
bool  medianFull  = false;

float computeMedian() {
  float sorted[MEDIAN_SIZE];
  int n = medianFull ? MEDIAN_SIZE : medianIndex;
  for (int i = 0; i < n; i++) sorted[i] = medianBuf[i];
  for (int i = 1; i < n; i++) {
    float key = sorted[i];
    int j = i - 1;
    while (j >= 0 && sorted[j] > key) { sorted[j + 1] = sorted[j]; j--; }
    sorted[j + 1] = key;
  }
  return sorted[n / 2];
}

// --- EMA filter ---
float emaValue  = 0.0;
bool  emaSeeded = false;

float computeEMA(float input) {
  if (!emaSeeded) { emaValue = input; emaSeeded = true; }
  else emaValue = EMA_ALPHA * input + (1.0 - EMA_ALPHA) * emaValue;
  return emaValue;
}

void setup() {
  Serial.begin(9600);
  Serial.println("HX711 force logger");
  Serial.println("------------------");
  Serial.print("Calibration factor: ");
  Serial.print(CALIBRATION_FACTOR, 3);
  Serial.println(" kgs/mV");
  Serial.println();

  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.tare();

  Serial.println("Reading:");
}

void loop() {
  long  rawADC    = scale.read();
  float rawMV     = (rawADC / ADC_FULL_SCALE) * VREF_MV;

  medianBuf[medianIndex] = rawMV;
  medianIndex++;
  if (medianIndex >= MEDIAN_SIZE) { medianIndex = 0; medianFull = true; }
  float medianMV = computeMedian();

  if (!emaSeeded) { emaValue = medianMV; emaSeeded = true; }
  else emaValue = EMA_ALPHA * medianMV + (1.0 - EMA_ALPHA) * emaValue;

  float kgs = emaValue * CALIBRATION_FACTOR;

  Serial.print("Reading: ");
  Serial.print(kgs, 3);
  Serial.println(" kgs");
}
