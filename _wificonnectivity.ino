#include <WiFi.h>
#include <WiFiUDP.h>
#include "HX711.h"

// HX711 Config
#define LOADCELL_DOUT_PIN 6
#define LOADCELL_SCK_PIN  5

#define VREF_MV             20.0
#define ADC_FULL_SCALE      8388608.0
#define MEDIAN_SIZE         3
#define EMA_ALPHA           0.4
#define CALIBRATION_FACTOR  16.894  // kgs/mV
HX711 scale;

// WiFi Config 
const char* ssid       = "DRB351"; // network name (SSID)
const char* password   = "bmethernet"; // password 
const char* udpAddress = "10.23.37.138";  // IP of your receiving PC
const int   udpPort    = 4210;

WiFiUDP udp;

// Filtering variables 
float medianBuf[MEDIAN_SIZE];
int   medianIndex = 0;
bool  medianFull  = false;

float emaValue  = 0.0;
bool  emaSeeded = false;

// Stats tracking 
long sampleCount = 0;
float minVal =  999999;
float maxVal = -999999;

// Median filter 

float computeMedian() {
  float sorted[MEDIAN_SIZE];
  int n = medianFull ? MEDIAN_SIZE : medianIndex;

  for (int i = 0; i < n; i++) sorted[i] = medianBuf[i];

   for (int i = 1; i < n; i++) {
    float key = sorted[i];
    int j = i - 1;
    while (j >= 0 && sorted[j] > key) {
      sorted[j + 1] = sorted[j];
      j--;
    }
    sorted[j + 1] = key;
  }

  return sorted[n / 2];
}

// Setup 
void setup() {
  Serial.begin(9600);

   // Connect to WiFi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nConnected!");
  Serial.println(WiFi.localIP());

  udp.begin(udpPort);

  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.tare();

  Serial.println("HX711 ready");
}

// Loop 
void loop() {
  long  rawADC = scale.read();
  float rawMV = (rawADC / ADC_FULL_SCALE) * VREF_MV;
 
  medianBuf[medianIndex] = rawMV;
  medianIndex++;
  if (medianIndex >= MEDIAN_SIZE) { medianIndex = 0; medianFull = true; }
  float medianMV = computeMedian();

  if (!emaSeeded) { emaValue = medianMV; emaSeeded = true;
  }else{
  emaValue = EMA_ALPHA * medianMV + (1.0 - EMA_ALPHA) * emaValue;
  }

  float kgs = emaValue * CALIBRATION_FACTOR;

// tracking 
sampleCount++;

  if (kgs < minVal) minVal = kgs;
  if (kgs > maxVal) maxVal = kgs;

  float rangeVal = maxVal - minVal;

  // Build UDP message 
  // Format: count,raw,min,max,range
  String msg = String(sampleCount) + "," +
               String(kgs, 2) + "," +
               String(minVal, 2) + "," +
               String(maxVal, 2) + "," +
               String(rangeVal, 2);


     // Send over UDP
    udp.beginPacket(udpAddress, udpPort);
    udp.print(msg);
    udp.endPacket();

    // log locally 
    Serial.println(msg);

    delay(200);
  }
