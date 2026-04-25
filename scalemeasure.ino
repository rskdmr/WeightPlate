#include "HX711.h"

#define LOADCELL_DOUT_PIN   6
#define LOADCELL_SCK_PIN    5
#define LOADCELL_DOUT_PIN_2 11
#define LOADCELL_SCK_PIN_2  12
#define VREF_MV             3300.0
#define ADC_FULL_SCALE      8388608.0
#define SETTLE_MS           5000
#define SAMPLE_MS           10000
#define MEDIAN_SIZE         3
#define EMA_ALPHA           0.2
#define CALIBRATION_FACTOR  0.0        // kg/mV for left plate  — fill in
#define CALIBRATION_FACTOR2 9.959421512 // kg/mV for right plate — fill in

HX711 scale;
HX711 scale2;

float baseline  = 283.0;
float baseline2 = 14.7521;

// --- Median filter ---
float medianBuf[MEDIAN_SIZE];
int   medianIndex = 0;
bool  medianFull  = false;

float medianBuf2[MEDIAN_SIZE];
int   medianIndex2 = 0;
bool  medianFull2  = false;

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

float computeMedian2() {
  float sorted[MEDIAN_SIZE];
  int n = medianFull2 ? MEDIAN_SIZE : medianIndex2;
  for (int i = 0; i < n; i++) sorted[i] = medianBuf2[i];
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

float emaValue2  = 0.0;
bool  emaSeeded2 = false;

float computeEMA(float input) {
  if (!emaSeeded) { emaValue = input; emaSeeded = true; }
  else emaValue = EMA_ALPHA * input + (1.0 - EMA_ALPHA) * emaValue;
  return emaValue;
}

float computeEMA2(float input) {
  if (!emaSeeded2) { emaValue2 = input; emaSeeded2 = true; }
  else emaValue2 = EMA_ALPHA * input + (1.0 - EMA_ALPHA) * emaValue2;
  return emaValue2;
}

// --- Read and filter one sample ---
float readFiltered() {
  long  rawADC  = scale.read();
  float rawMV   = (rawADC / ADC_FULL_SCALE) * VREF_MV;
  medianBuf[medianIndex] = rawMV;
  medianIndex++;
  if (medianIndex >= MEDIAN_SIZE) { medianIndex = 0; medianFull = true; }
  return computeEMA(computeMedian());
}

float readFiltered2() {
  long  rawADC  = scale2.read();
  float rawMV   = (rawADC / ADC_FULL_SCALE) * VREF_MV;
  medianBuf2[medianIndex2] = rawMV;
  medianIndex2++;
  if (medianIndex2 >= MEDIAN_SIZE) { medianIndex2 = 0; medianFull2 = true; }
  return computeEMA2(computeMedian2());
}

// --- Capture stable baseline ---
void captureBaseline() {
  Serial.println("Zeroing...");
  for (int i = 0; i < 50; i++) { readFiltered(); readFiltered2(); }
  baseline  = readFiltered();
  baseline2 = readFiltered2();
  Serial.print("Baseline L: "); Serial.print(baseline,  4); Serial.println(" mV");
  Serial.print("Baseline R: "); Serial.print(baseline2, 4); Serial.println(" mV");
}

void setup() {
  Serial.begin(9600);
  Serial.println("HX711 dual force logger");
  Serial.println("-----------------------");

  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.tare();

  scale2.begin(LOADCELL_DOUT_PIN_2, LOADCELL_SCK_PIN_2);
  scale2.tare();

  captureBaseline();
  Serial.println("Reading:");
}

void loop() {
  float kgs  = (readFiltered()  - baseline)  / CALIBRATION_FACTOR;
  float kgs2 = (readFiltered2() - baseline2) / CALIBRATION_FACTOR2;

  Serial.print(kgs,  3);
  Serial.print("|");
  Serial.println(kgs2, 3);
}
