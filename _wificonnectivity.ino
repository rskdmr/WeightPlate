#include <WiFi.h>
#include <WiFiUDP.h>
#include "HX711.h"

#define LOADCELL_DOUT_PIN 6
#define LOADCELL_SCK_PIN  5

// WiFi Config 
const char* ssid       = "DRB351"; // network name (SSID)
const char* password   = "bmethernet"; // password 
const char* udpAddress = "10.23.37.138";  // IP of your receiving PC
const int   udpPort    = 4210;

WiFiUDP udp;
HX711 scale;

long minVal;
long maxVal;
int readingCount = 0;

void setup() {
  Serial.begin(9600);

   // Connect to WiFi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected! IP: " + WiFi.localIP().toString());
  udp.begin(udpPort);

// Load Cell setup 
  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  Serial.println("Stabilizing...");
  delay(2000); // let the circuit settle before taring
  scale.tare(20);
  Serial.println("Tared. Monitoring drift...");
 
  // initialize min/max with first reading
  long first = scale.read();
  minVal = first;
  maxVal = first;
}

void loop() {
  if (scale.is_ready()) {
    long raw = scale.read();
    readingCount++;

    if (raw < minVal) minVal = raw;
    if (raw > maxVal) maxVal = raw;
    long range = maxVal - minVal;

     String msg = String(readingCount) + " | " +
                 String(raw)          + " | " +
                 String(minVal)       + " | " +
                 String(maxVal)       + " | " +
                 String(range);

     // Send over UDP
    udp.beginPacket(udpAddress, udpPort);
    udp.print(msg);
    udp.endPacket();

    // log locally 
    Serial.println(msg);

    delay(200);
  }
} 
