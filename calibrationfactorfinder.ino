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

HX711 scale;
HX711 scale2;

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

float readFiltered2(const char* label) {
  long  rawADC    = scale2.read();
  float rawMV     = (rawADC / ADC_FULL_SCALE) * VREF_MV;

  medianBuf2[medianIndex2] = rawMV;
  medianIndex2++;
  if (medianIndex2 >= MEDIAN_SIZE) { medianIndex2 = 0; medianFull2 = true; }
  float medianMV   = computeMedian2();
  float filteredMV = computeEMA2(medianMV);

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

  Serial.println(">> Settling (5s)...");
  unsigned long settleStart = millis();
  while (millis() - settleStart < SETTLE_MS) {
    readFiltered("[SETTLING L]");
    readFiltered2("[SETTLING R]");
  }

  Serial.println();
  Serial.println(">> Sampling (10s)...");
  float sampleSum   = 0.0;
  float sampleSum2  = 0.0;
  int   sampleCount = 0;
  unsigned long sampleStart = millis();
  while (millis() - sampleStart < SAMPLE_MS) {
    float filtered  = readFiltered("[SAMPLING L]");
    float filtered2 = readFiltered2("[SAMPLING R]");
    sampleSum  += filtered;
    sampleSum2 += filtered2;
    sampleCount++;
  }

  float meanMV  = sampleSum  / sampleCount;
  float meanMV2 = sampleSum2 / sampleCount;

  Serial.println();
  Serial.println("=== Calibration result ===");
  Serial.print("  Samples collected   : "); Serial.println(sampleCount);
  Serial.print("  Mean filtered mV L  : "); Serial.print(meanMV,  6); Serial.println(" mV");
  Serial.print("  Mean filtered mV R  : "); Serial.print(meanMV2, 6); Serial.println(" mV");
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

  scale2.begin(LOADCELL_DOUT_PIN_2, LOADCELL_SCK_PIN_2);
  scale2.tare();

  Serial.println("Continuous readings active.");
  Serial.println("Send any character over serial to begin calibration.");
  Serial.println();
}

void loop() {
  if (Serial.available() > 0) {
    while (Serial.available()) Serial.read();
    runCalibration();
  }

  readFiltered("[LIVE L]   ");
  readFiltered2("[LIVE R]   ");
}
