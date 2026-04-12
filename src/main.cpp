/*
 Example using the SparkFun HX711 breakout board with a scale By: Nathan Seidle
 
 This is the calibration sketch. Use it to determine the calibration_factor that the main example uses. It also
 outputs the zero_factor useful for projects that have a permanent mass on the scale in between power cycles.

 Setup your scale and start the sketch WITHOUT a weight on the scale
 Once readings are displayed place the weight on the scale
 Press +/- or a/z to adjust the calibration_factor until the output readings match the known weight
 Use this calibration_factor on the example sketch

 This example assumes pounds (lbs). If you prefer kilograms, change the Serial.print(" lbs"); line to kg. The
 calibration factor will be significantly different but it will be linearly related to lbs (1 lbs = 0.453592 kg).

 Your calibration factor may be very positive or very negative. It all depends on the setup of your scale system
 and the direction the sensors deflect from zero state
 This example code uses bogde's excellent library:"https://github.com/bogde/HX711"
 bogde's library is released under a GNU GENERAL PUBLIC LICENSE
 Arduino pin 2 -> HX711 CLK
 3 -> DOUT
 5V -> VCC
 GND -> GND

 Most any pin on the Arduino Uno will be compatible with DOUT/CLK.

 The HX711 board can be powered from 2.7V to 5V so the Arduino 5V power should be fine.

*/

// helloworld 

#include <Arduino.h>
#include "HX711.h"

// Simple IIR Low Pass Filter class
class LowPassFilter {
private:
  float alpha;  // Filter coefficient (0 < alpha <= 1)
  float filtered_value;
  bool initialized;

public:
  // Constructor
  // alpha: smoothing factor (0.01 - 0.1 for strong filtering, 0.3 - 0.5 for light filtering)
  // Higher alpha = less filtering (more responsive)
  // Lower alpha = more filtering (smoother but slower response)
  LowPassFilter(float alpha = 0.1) : alpha(alpha), filtered_value(0), initialized(false) {}

  // Set the smoothing factor
  void setAlpha(float a) {
    alpha = constrain(a, 0.001, 1.0);
  }

  // Update filter with new raw value and return filtered value
  float update(float raw_value) {
    if (!initialized) {
      filtered_value = raw_value;
      initialized = true;
    } else {
      filtered_value = (alpha * raw_value) + ((1.0 - alpha) * filtered_value);
    }
    return filtered_value;
  }

  // Get current filtered value without updating
  float getValue() const {
    return filtered_value;
  }

  // Reset filter
  void reset() {
    initialized = false;
    filtered_value = 0;
  }
};

#define LOADCELL_DOUT_PIN  6 // Pin D6 to DATA
#define LOADCELL_SCK_PIN  5 // Pin D5 to CLK

HX711 scale;
LowPassFilter weightFilter(0.1);  // Create filter with alpha=0.1 (adjust as needed)

float calibration_factor = 4000; //-7050 worked for my 440lb max scale setup

void setup() {
  Serial.begin(9600);
  Serial.println("HX711 calibration sketch");
  Serial.println("Remove all weight from scale");
  Serial.println("After readings begin, place known weight on scale");
  Serial.println("Press + or a to increase calibration factor");
  Serial.println("Press - or z to decrease calibration factor");

  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.set_scale();
  scale.tare(); //Reset the scale to 0

  long zero_factor = scale.read_average(); //Get a baseline reading
  Serial.print("Zero factor: "); //This can be used to remove the need to tare the scale. Useful in permanent scale projects.
  Serial.println(zero_factor);
  
}

void loop() {

  scale.set_scale(calibration_factor); //Adjust to this calibration factor

  float raw_weight = scale.get_units();
  float filtered_weight = weightFilter.update(raw_weight);

  Serial.print("Raw: ");
  Serial.print(raw_weight, 3);
  Serial.print(" | Filtered: ");
  Serial.print(filtered_weight, 3);
  Serial.print(" kgs | Cal Factor: ");
  Serial.print(calibration_factor);
  Serial.println();

  if(Serial.available())
  {
    char temp = Serial.read();
    if(temp == '+' || temp == 'a')
      calibration_factor += 500;
    else if(temp == '-' || temp == 'z')
      calibration_factor -= 500;
  }
}