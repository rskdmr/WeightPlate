#include "HX711.h" 
#define LOADCELL_DOUT_PIN 6 // Pin D6
#define LOADCELL_SCK_PIN 5 // Pin D5
HX711 scale;
void setup() {
  // put your setup code here, to run once:
  Serial.begin(9600);
  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
}

void loop() {
  // put your main code here, to run repeatedly:
  if (scale.is_ready()) {
      long reading = scale.read();
      Serial.print("HX711 reading: ");
      Serial.println(reading);
    } else {
      Serial.println("HX711 not found.");
    }

}
