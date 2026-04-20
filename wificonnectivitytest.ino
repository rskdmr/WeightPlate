#include <WiFi.h>
#include <WiFiUDP.h>

// WiFi Config 
const char* ssid       = "USC Guest Wireless";
const char* password   = "";
const char* udpAddress = "10.25.38.131";
const int   udpPort    = 4210;

WiFiUDP udp;

int counter = 0;

void setup() {
  Serial.begin(9600);

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nConnected! IP: " + WiFi.localIP().toString());
  udp.begin(udpPort);
}

void loop() {
  counter++;

  String msg = "Test packet #" + String(counter);

  udp.beginPacket(udpAddress, udpPort);
  udp.print(msg);
  udp.endPacket();

  Serial.println(msg);

  delay(1000);
}