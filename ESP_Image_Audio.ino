#include <I2S.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include "esp_camera.h"

#define CAMERA_MODEL_XIAO_ESP32S3 // Has PSRAM
#include "camera_pins.h"

#define SAMPLE_RATE 16000U
#define SAMPLE_BITS 16
#define RECORD_TIME 3  // seconds
#define VOLUME_GAIN 2
#define BUFFER_COUNT 2

// BLE settings
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define AUDIO_CHAR_UUID     "beb5483e-36e1-4688-b7f5-ea07361b26a8"
#define IMAGE_CHAR_UUID     "beb5483e-36e1-4688-b7f5-ea07361b26a9"

BLEServer* pServer = NULL;
BLECharacteristic* pAudioCharacteristic = NULL;
BLECharacteristic* pImageCharacteristic = NULL;
bool deviceConnected = false;

uint32_t record_size = (SAMPLE_RATE * SAMPLE_BITS / 8) * RECORD_TIME;
uint8_t* audio_buffers[BUFFER_COUNT];
int currentBuffer = 0;
bool bufferReady = false;

camera_fb_t* fb = NULL;
bool camera_sign = false;

class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
    };

    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
    }
};

void setup() {
  Serial.begin(115200);
  while (!Serial);

  // Initialize I2S
  I2S.setAllPins(-1, 42, 41, -1, -1);
  if (!I2S.begin(PDM_MONO_MODE, SAMPLE_RATE, SAMPLE_BITS)) {
    Serial.println("Failed to initialize I2S!");
    while (1);
  }

  // Initialize BLE
  BLEDevice::init("ESP32_Audio_Image_Sender");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());
  BLEService *pService = pServer->createService(SERVICE_UUID);
  pAudioCharacteristic = pService->createCharacteristic(
                      AUDIO_CHAR_UUID,
                      BLECharacteristic::PROPERTY_READ   |
                      BLECharacteristic::PROPERTY_WRITE  |
                      BLECharacteristic::PROPERTY_NOTIFY |
                      BLECharacteristic::PROPERTY_INDICATE
                    );
  pImageCharacteristic = pService->createCharacteristic(
                      IMAGE_CHAR_UUID,
                      BLECharacteristic::PROPERTY_READ   |
                      BLECharacteristic::PROPERTY_WRITE  |
                      BLECharacteristic::PROPERTY_NOTIFY |
                      BLECharacteristic::PROPERTY_INDICATE
                    );
  pAudioCharacteristic->addDescriptor(new BLE2902());
  pImageCharacteristic->addDescriptor(new BLE2902());
  pService->start();
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->setMinPreferred(0x06);  
  pAdvertising->setMinPreferred(0x12);
  BLEDevice::startAdvertising();
  Serial.println("BLE device is ready to be connected");

  // Allocate audio buffers
  for (int i = 0; i < BUFFER_COUNT; i++) {
    audio_buffers[i] = (uint8_t*) malloc(record_size);
    if (audio_buffers[i] == NULL) {
      Serial.println("Failed to allocate buffer!");
      while (1);
    }
  }

  // Initialize Camera
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_SVGA;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 10;
  config.fb_count = 1;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x", err);
    return;
  }
  
  camera_sign = true;

  // Start recording
  xTaskCreate(recordTask, "Record Task", 4096, NULL, 1, NULL);
}

void loop() {
  if (deviceConnected && bufferReady) {
    // Send audio
    sendAudioOverBLE(audio_buffers[currentBuffer]);
    bufferReady = false;
    
    // Wait for 20ms
    delay(20);
    
    // Capture and send image
    fb = esp_camera_fb_get();
    if (fb) {
      sendImageOverBLE(fb);
      esp_camera_fb_return(fb);
    } else {
      Serial.println("Camera capture failed");
    }
    
    // Wait for another 20ms
    delay(20);
  }
}

void recordTask(void* parameter) {
  while (1) {
    uint32_t sample_size = 0;
    int bufferToFill = (currentBuffer + 1) % BUFFER_COUNT;

    esp_i2s::i2s_read(esp_i2s::I2S_NUM_0, audio_buffers[bufferToFill], record_size, &sample_size, portMAX_DELAY);

    // Adjust volume
    for (uint32_t i = 0; i < sample_size; i += SAMPLE_BITS / 8) {
      (*(uint16_t *)(audio_buffers[bufferToFill] + i)) <<= VOLUME_GAIN;
    }

    currentBuffer = bufferToFill;
    bufferReady = true;
  }
}

void sendAudioOverBLE(uint8_t* buffer) {
  const int chunkSize = 512;  // Adjust based on your BLE MTU
  for (uint32_t i = 0; i < record_size; i += chunkSize) {
    uint32_t chunk = (record_size - i < chunkSize) ? (record_size - i) : chunkSize;
    pAudioCharacteristic->setValue(buffer + i, chunk);
    pAudioCharacteristic->notify();
    delay(10);  // Give some time for the notification to be sent
  }
}

void sendImageOverBLE(camera_fb_t* fb) {
  if (!fb) {
    Serial.println("Invalid frame buffer");
    return;
  }
  
  const int chunkSize = 512;  // Adjust based on your BLE MTU
  for (uint32_t i = 0; i < fb->len; i += chunkSize) {
    uint32_t chunk = (fb->len - i < chunkSize) ? (fb->len - i) : chunkSize;
    pImageCharacteristic->setValue(fb->buf + i, chunk);
    pImageCharacteristic->notify();
    delay(10);  // Give some time for the notification to be sent
  }
}
