#include "HX711.h"

#define LOADCELL_DOUT_PIN   6
#define LOADCELL_SCK_PIN    5
#define VREF_MV             20.0
#define ADC_FULL_SCALE      8388608.0
#define SETTLE_MS           5000    // settle window: 5 seconds
#define SAMPLE_MS           10000   // collection window: 10 seconds
#define MEDIAN_SIZE         3
#define EMA_ALPHA           0.4

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

// --- Read and filter one sample, print with a phase label ---
float readFiltered(const char* label) {
  long  rawADC    = scale.read();
  float rawMV     = (rawADC / ADC_FULL_SCALE) * VREF_MV;

  medianBuf[medianIndex] = rawMV;
  medianIndex++;
  if (medianIndex >= MEDIAN_SIZE) { medianIndex = 0; medianFull = true; }
  float medianMV   = computeMedian();
  float filteredMV = computeEMA(medianMV);

  Serial.print(label);
  Serial.print("  Raw ADC: ");
  Serial.print(rawADC);
  Serial.print("  |  Raw mV: ");
  Serial.print(rawMV, 4);
  Serial.print("  |  Median mV: ");
  Serial.print(medianMV, 4);
  Serial.print("  |  Filtered mV: ");
  Serial.print(filteredMV, 4);
  Serial.println(" mV");

  return filteredMV;
}

void runCalibration() {
  Serial.println();
  Serial.println("=== Calibration started ===");
  Serial.println("Step on the scale now.");
  Serial.println();

  // --- Settle phase: 5 seconds, print but don't accumulate ---
  Serial.println(">> Settling (5s)...");
  unsigned long settleStart = millis();
  while (millis() - settleStart < SETTLE_MS) {
    readFiltered("[SETTLING]");
  }

  // --- Sample phase: 10 seconds, accumulate filtered values ---
  Serial.println();
  Serial.println(">> Sampling (10s)...");
  float sampleSum   = 0.0;
  int   sampleCount = 0;
  unsigned long sampleStart = millis();
  while (millis() - sampleStart < SAMPLE_MS) {
    float filtered = readFiltered("[SAMPLING]");
    sampleSum += filtered;
    sampleCount++;
  }

  float meanMV = sampleSum / sampleCount;

  Serial.println();
  Serial.println("=== Calibration result ===");
  Serial.print("  Samples collected : "); Serial.println(sampleCount);
  Serial.print("  Mean filtered mV  : "); Serial.print(meanMV, 6); Serial.println(" mV");
  Serial.println();
  Serial.println("Note this value alongside your known load in kg.");
  Serial.println("Repeat with a second known load to verify linearity.");
  Serial.println("==========================");
  Serial.println();
  Serial.println("Send any character to run calibration again.");
}

void setup() {
  Serial.begin(9600);
  Serial.println("HX711 calibration logger");
  Serial.println("------------------------");

  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.tare();

  Serial.println("Continuous readings active.");
  Serial.println("Send any character over serial to begin calibration.");
  Serial.println();
}

void loop() {
  // --- Trigger calibration on any serial input ---
  if (Serial.available() > 0) {
    while (Serial.available()) Serial.read();  // flush input
    runCalibration();
  }

  // --- Continuous readings during idle ---
  readFiltered("[LIVE]     ");
}