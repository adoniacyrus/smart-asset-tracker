/**
 * Smart Asset Location Tracker with Dynamic Geofencing and SOS Alert
 * Phase 3 - ESP32 Firmware Implementation with Secure Cloud HTTPS
 * Target: ESP32 DevKit (38-pin, NodeMCU-32S / ESP32-WROOM-32)
 *
 * Hardware Pin Mapping (Authoritative - DO NOT MODIFY):
 * - GPS TX        : GPIO 16 (ESP32 UART2 RX) - Connected to NEO-6M TX
 * - GPS RX        : GPIO 17 (ESP32 UART2 TX) - Connected to NEO-6M RX
 * - SOS Button    : GPIO 18 (INPUT_PULLUP, button connected between GPIO18 and GND)
 * - Buzzer Driver : GPIO 25 (OUTPUT, active HIGH)
 * - Red LED       : GPIO 26 (OUTPUT, active HIGH)
 * - Green LED     : GPIO 27 (OUTPUT, active HIGH)
 */

#include <Arduino.h>
#include <HardwareSerial.h>
#include <TinyGPSPlus.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <math.h>
#include <time.h>
#include "esp_sntp.h"
#include "esp_wifi.h"
#include "driver/gpio.h"
#include "secrets.h"
#include "ca_cert.h"

// =============================================================================
// Authoritative Hardware Pin Definitions
// =============================================================================
#define PIN_GPS_RX      16  // Connected to NEO-6M TX
#define PIN_GPS_TX      17  // Connected to NEO-6M RX
#define PIN_SOS_BUTTON  18  // Physical SOS Push Button (INPUT_PULLUP, active LOW)
#define PIN_BUZZER      25  // Buzzer Driver Output
#define PIN_LED_RED     26  // Red Alert LED
#define PIN_LED_GREEN   27  // Green Normal Status LED

// =============================================================================
// Timing Configurations (Milliseconds)
// =============================================================================
const unsigned long CONFIG_POLL_INTERVAL   = 4000;  // Poll backend config every 4s
const unsigned long TELEMETRY_INTERVAL     = 5000;  // Regular telemetry cadence (5s)
const unsigned long DIAGNOSTICS_INTERVAL   = 5000;  // Serial diagnostics report cadence (5s)
const unsigned long BUTTON_DEBOUNCE_DELAY  = 50;    // 50ms debouncing
const unsigned long WIFI_RETRY_INTERVAL    = 10000; // Wi-Fi reconnect retry interval (10s)
const unsigned long SOS_FLUSH_INTERVAL     = 3000;  // Retry unacknowledged SOS every 3s
const uint32_t      GPS_BAUD               = 9600;  // NEO-6M default baud rate
const uint32_t      HTTPS_TIMEOUT_MS       = 15000; // 15s timeout for cloud HTTPS calls

// =============================================================================
// Hardware Peripherals & Storage Instances
// =============================================================================
HardwareSerial gpsSerial(2);
TinyGPSPlus gps;
Preferences preferences;

// =============================================================================
// Active State Variables
// =============================================================================
// Location & Target Coordinates (Amal Jyothi College of Engineering default)
double currentLat = 9.528700;
double currentLon = 76.822600;
bool hasValidCoordinates = true;
int pendingTargetIdToAck = 0;       // target_id awaiting telemetry confirmation

// Geofence Parameters (Amal Jyothi Campus, Koovappally, Kerala)
double geofenceCenterLat = 9.528700;
double geofenceCenterLon = 76.822600;
double geofenceRadiusMeters = 500.0;
bool geofenceActive = true;
long lastProcessedConfigVersion = -1;

// Geofence Calculations & Physical Outputs
double currentDistanceMeters = 0.0;
bool isInsideGeofence = true;
bool ledGreenState = true;
bool ledRedState = false;
bool buzzerState = false;

// SOS State Management & NVS Persistence
bool sosActive = false;             // Active hardware SOS latch
bool pendingSosUpload = false;      // Unacknowledged SOS event pending upload
unsigned long sosEventCount = 0;
unsigned long lastSOSTriggerTime = 0;

// Button Debounce & Active-LOW Variables
const int BUTTON_RESTING_STATE = HIGH;
const int BUTTON_ACTIVE_STATE  = LOW;
int lastButtonReading          = HIGH;
int debouncedButtonState       = HIGH;
unsigned long lastDebounceTime = 0;

// Non-blocking Timers & Network Status
unsigned long lastConfigPollTime = 0;
unsigned long lastTelemetryTime  = 0;
unsigned long lastDiagnosticsTime = 0;
unsigned long lastWifiRetryTime  = 0;
unsigned long lastSosFlushTime   = 0;
bool wifiManuallyDisabled        = false;
bool timeSynchronized            = false;

// GPS UART Byte Tracking
unsigned long rawGpsBytes = 0;
unsigned long lastGpsByteTime = 0;

// Serial Buffer for Interactive Commands
String serialInputBuffer = "";

// =============================================================================
// Function Prototypes
// =============================================================================
double calculateHaversineDistance(double lat1, double lon1, double lat2, double lon2);
void recalculateGeofence();
void updateHardwareOutputs();
void triggerSOS(const char* sourceDescription);
void clearSOS();
void persistStateToNVS();
void restoreStateFromNVS();
bool syncTime();
void pollDeviceConfig();
bool sendTelemetry(int targetIdToAck, long configVersionToAck);
bool uploadPendingSOSEvent();
bool acknowledgeClearSOS();
void handleSerialCommands();
void reportDiagnostics();
void maintainWiFi();

// =============================================================================
// Haversine Distance Calculation (ESP32 Onboard Execution)
// =============================================================================
double calculateHaversineDistance(double lat1, double lon1, double lat2, double lon2) {
    const double EARTH_RADIUS_METERS = 6371000.0;
    const double DEG_TO_RAD_FACTOR = M_PI / 180.0;

    double dLat = (lat2 - lat1) * DEG_TO_RAD_FACTOR;
    double dLon = (lon2 - lon1) * DEG_TO_RAD_FACTOR;
    double phi1 = lat1 * DEG_TO_RAD_FACTOR;
    double phi2 = lat2 * DEG_TO_RAD_FACTOR;

    double sinDLat = sin(dLat / 2.0);
    double sinDLon = sin(dLon / 2.0);

    double a = sinDLat * sinDLat + cos(phi1) * cos(phi2) * sinDLon * sinDLon;
    if (a < 0.0) a = 0.0;
    if (a > 1.0) a = 1.0;

    double c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a));
    return EARTH_RADIUS_METERS * c;
}

// =============================================================================
// Geofence Re-Evaluation & Output Synchronization
// =============================================================================
void recalculateGeofence() {
    if (hasValidCoordinates && geofenceActive) {
        currentDistanceMeters = calculateHaversineDistance(
            currentLat, currentLon,
            geofenceCenterLat, geofenceCenterLon
        );
        isInsideGeofence = (currentDistanceMeters <= geofenceRadiusMeters);
    } else {
        currentDistanceMeters = 0.0;
        isInsideGeofence = true;
    }

    updateHardwareOutputs();
}

// =============================================================================
// Hardware Truth Table Output Driver
// =============================================================================
/**
 * TRUTH TABLE:
 * 1. SOS Active (Highest Priority):
 *    - Green LED : OFF (LOW)
 *    - Red LED   : ON  (HIGH)
 *    - Buzzer    : ON  (HIGH)
 * 2. Geofence Breach (Outside):
 *    - Green LED : OFF (LOW)
 *    - Red LED   : ON  (HIGH)
 *    - Buzzer    : ON  (HIGH)
 * 3. Normal State (Inside Geofence, No SOS):
 *    - Green LED : ON  (HIGH)
 *    - Red LED   : OFF (LOW)
 *    - Buzzer    : OFF (LOW)
 */
void updateHardwareOutputs() {
    if (sosActive) {
        ledGreenState = false;
        ledRedState   = true;
        buzzerState   = true;
    } else if (!isInsideGeofence) {
        ledGreenState = false;
        ledRedState   = true;
        buzzerState   = true;
    } else {
        ledGreenState = true;
        ledRedState   = false;
        buzzerState   = false;
    }

    digitalWrite(PIN_LED_GREEN, ledGreenState ? HIGH : LOW);
    digitalWrite(PIN_LED_RED,   ledRedState ? HIGH : LOW);
    digitalWrite(PIN_BUZZER,    buzzerState ? HIGH : LOW);
}

// =============================================================================
// NVS Non-Volatile Storage (Preferences) Persistence
// =============================================================================
void persistStateToNVS() {
    preferences.putBool("sos_active", sosActive);
    preferences.putBool("sos_pending", pendingSosUpload);
    preferences.putULong("sos_count", sosEventCount);
    preferences.putDouble("cur_lat", currentLat);
    preferences.putDouble("cur_lon", currentLon);
    preferences.putBool("has_coords", hasValidCoordinates);
    preferences.putLong("cfg_version", lastProcessedConfigVersion);
}

void restoreStateFromNVS() {
    sosActive = preferences.getBool("sos_active", false);
    pendingSosUpload = preferences.getBool("sos_pending", false);
    sosEventCount = preferences.getULong("sos_count", 0);

    if (preferences.isKey("cur_lat") && preferences.isKey("cur_lon")) {
        currentLat = preferences.getDouble("cur_lat", currentLat);
        currentLon = preferences.getDouble("cur_lon", currentLon);
        hasValidCoordinates = preferences.getBool("has_coords", true);
    }
    lastProcessedConfigVersion = preferences.getLong("cfg_version", -1);

    Serial.println("[NVS] State restored from Non-Volatile Storage:");
    Serial.printf("      SOS Active: %s | Pending Upload: %s | SOS Count: %lu\n",
                  sosActive ? "YES (Latched)" : "NO",
                  pendingSosUpload ? "YES" : "NO",
                  sosEventCount);
    Serial.printf("      Current Coordinates: %.6f, %.6f (HasCoords: %d)\n",
                  currentLat, currentLon, hasValidCoordinates);
    Serial.printf("      Last Processed Config Version: %ld\n", lastProcessedConfigVersion);
}

// =============================================================================
// SNTP Time Synchronization (Required for TLS Certificate Date Validation)
// =============================================================================
bool syncTime() {
    if (WiFi.status() != WL_CONNECTED) return false;

    Serial.println("[NTP] Initializing SNTP network time synchronization...");
    configTime(0, 0, "pool.ntp.org", "time.google.com", "time.cloudflare.com");

    time_t now = time(nullptr);
    int retries = 0;
    while (now < 1700000000 && retries < 25) { // Ensure clock is in modern epoch
        delay(200);
        now = time(nullptr);
        retries++;
    }

    if (now >= 1700000000) {
        struct tm timeinfo;
        gmtime_r(&now, &timeinfo);
        Serial.printf("[NTP] Time synchronized: %04d-%02d-%02d %02d:%02d:%02d UTC\n",
                      timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
                      timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
        timeSynchronized = true;
        return true;
    } else {
        Serial.println("[NTP] Warning: SNTP time sync still pending. TLS certificate validation will retry.");
        return false;
    }
}

// =============================================================================
// SOS Event Handling
// =============================================================================
void triggerSOS(const char* sourceDescription) {
    sosActive = true;
    pendingSosUpload = true;
    sosEventCount++;
    lastSOSTriggerTime = millis();

    // Immediately persist and drive outputs before any network call
    persistStateToNVS();
    updateHardwareOutputs();

    Serial.println();
    Serial.println("*****************************************************************");
    Serial.printf(" [ALERT] *** EMERGENCY SOS ACTIVATED (%s) ***\n", sourceDescription);
    Serial.printf(" Event ID           : SOS-EVT-%04lu\n", sosEventCount);
    Serial.printf(" Active Position    : Lat: %.6f, Lon: %.6f\n", currentLat, currentLon);
    Serial.printf(" Red LED (GPIO 26)  : ON\n");
    Serial.printf(" Buzzer (GPIO 25)   : ON\n");
    Serial.printf(" Green LED (GPIO 27): OFF\n");
    Serial.printf(" NVS Persisted      : YES (Survives power cycle)\n");
    Serial.println("*****************************************************************");
    Serial.println();

    // Trigger immediate upload attempt if connected
    if (WiFi.status() == WL_CONNECTED && timeSynchronized) {
        uploadPendingSOSEvent();
    }
}

void clearSOS() {
    if (sosActive || pendingSosUpload) {
        sosActive = false;
        pendingSosUpload = false;
        persistStateToNVS();
        recalculateGeofence();

        Serial.println();
        Serial.println(">>> [COMMAND] SOS STATE CLEARED & LATCH RESET <<<");
        Serial.printf(">>> Outputs restored: Green LED: %s | Red LED: %s | Buzzer: %s\n",
                      ledGreenState ? "ON" : "OFF",
                      ledRedState ? "ON" : "OFF",
                      buzzerState ? "ON" : "OFF");
        Serial.println();
    } else {
        Serial.println(">>> [COMMAND] Reset requested, but SOS is not currently active. <<<");
    }
}

// =============================================================================
// Secure HTTPS API Operations (WiFiClientSecure with CA Validation)
// =============================================================================

/**
 * Poll Django Configuration Endpoint over Secure HTTPS
 * GET /api/tracker/device/config/?device_id=ASSET-ESP32-01
 */
void pollDeviceConfig() {
    if (WiFi.status() != WL_CONNECTED || !timeSynchronized) return;

    WiFiClientSecure secureClient;
    secureClient.setCACert(RENDER_CA_CERT);
    secureClient.setHandshakeTimeout(15);
    secureClient.setTimeout(15);

    HTTPClient http;
    http.setTimeout(HTTPS_TIMEOUT_MS);

    String url = String(DJANGO_SERVER_URL) + "/api/tracker/device/config/?device_id=" + String(DEVICE_ID);
    if (!http.begin(secureClient, url)) {
        Serial.println("[HTTPS Config] Failed to initialize secure connection to host.");
        return;
    }

    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.addHeader("User-Agent", "ESP32-SmartTracker/2.0");

    int httpCode = http.GET();
    if (httpCode == HTTP_CODE_OK) {
        String payload = http.getString();
        DynamicJsonDocument doc(2048);
        DeserializationError error = deserializeJson(doc, payload);

        if (error) {
            Serial.printf("[HTTPS Config] JSON deserialization failed: %s\n", error.c_str());
            http.end();
            return;
        }

        long remoteConfigVersion = doc["config_version"] | -1;
        bool clearSosCommand     = doc["clear_sos_command"] | false;

        // 1. Process Remote SOS Clear Command
        if (clearSosCommand) {
            Serial.println("[HTTPS Config] Remote clear_sos_command received from Django!");
            if (sosActive) {
                clearSOS();
            }
            acknowledgeClearSOS();
        }

        // 2. Process Geofence Boundary Configuration
        bool geofenceUpdated = false;
        JsonObject gf = doc["geofence"];
        if (!gf.isNull() && gf["is_active"].as<bool>()) {
            double newCenterLat = gf["center_latitude"].as<double>();
            double newCenterLon = gf["center_longitude"].as<double>();
            double newRadius    = gf["radius_meters"].as<double>();

            if (newCenterLat != geofenceCenterLat ||
                newCenterLon != geofenceCenterLon ||
                newRadius != geofenceRadiusMeters ||
                !geofenceActive) {
                geofenceCenterLat = newCenterLat;
                geofenceCenterLon = newCenterLon;
                geofenceRadiusMeters = newRadius;
                geofenceActive = true;
                geofenceUpdated = true;
                Serial.printf("[HTTPS Config] Geofence updated: Center(%.6f, %.6f), Radius: %.1fm\n",
                              geofenceCenterLat, geofenceCenterLon, geofenceRadiusMeters);
            }
        }

        // 3. Process Software Coordinate Targets
        bool coordinatesUpdated = false;
        int incomingTargetId = 0;
        JsonObject tgt = doc["target_location"];
        if (!tgt.isNull() && tgt["has_target"].as<bool>()) {
            double newLat = tgt["latitude"].as<double>();
            double newLon = tgt["longitude"].as<double>();
            incomingTargetId = tgt["target_id"].as<int>();

            currentLat = newLat;
            currentLon = newLon;
            hasValidCoordinates = true;
            coordinatesUpdated = true;
            pendingTargetIdToAck = incomingTargetId;

            Serial.printf("[HTTPS Config] Staged target #%d received: Lat: %.6f, Lon: %.6f\n",
                          incomingTargetId, currentLat, currentLon);
            persistStateToNVS();
        }

        // 4. Automatically recalculate if coordinates OR geofence boundary parameters changed
        if (geofenceUpdated || coordinatesUpdated) {
            recalculateGeofence();
            Serial.printf("[Geofence Calc] Distance: %.1fm | Inside: %s | Outputs: G:%d R:%d B:%d\n",
                          currentDistanceMeters,
                          isInsideGeofence ? "YES" : "NO (BREACH)",
                          ledGreenState, ledRedState, buzzerState);

            // Immediate telemetry transmission to confirm processing to Django
            sendTelemetry(pendingTargetIdToAck, remoteConfigVersion);
            lastProcessedConfigVersion = remoteConfigVersion;
            pendingTargetIdToAck = 0;
            persistStateToNVS();
        } else if (remoteConfigVersion > lastProcessedConfigVersion) {
            recalculateGeofence();
            sendTelemetry(0, remoteConfigVersion);
            lastProcessedConfigVersion = remoteConfigVersion;
            persistStateToNVS();
        }

    } else {
        if (httpCode < 0) {
            char err_buf[128];
            int err = secureClient.lastError(err_buf, sizeof(err_buf));
            Serial.printf("[HTTPS Config] TLS/Network error: %s (code %d)\n", http.errorToString(httpCode).c_str(), httpCode);
            if (err < 0) Serial.printf("               TLS detail: %s (err %d)\n", err_buf, err);
        } else {
            Serial.printf("[HTTPS Config] Poll failed, HTTP code: %d\n", httpCode);
        }
    }

    http.end();
}

/**
 * Transmit Telemetry & Acknowledge Processing over Secure HTTPS
 * POST /api/tracker/device/telemetry/
 */
bool sendTelemetry(int targetIdToAck, long configVersionToAck) {
    if (WiFi.status() != WL_CONNECTED || !timeSynchronized) return false;

    WiFiClientSecure secureClient;
    secureClient.setCACert(RENDER_CA_CERT);
    secureClient.setHandshakeTimeout(15);
    secureClient.setTimeout(15);

    HTTPClient http;
    http.setTimeout(HTTPS_TIMEOUT_MS);

    String url = String(DJANGO_SERVER_URL) + "/api/tracker/device/telemetry/";
    if (!http.begin(secureClient, url)) {
        Serial.println("[HTTPS Telemetry] Failed to initialize secure connection.");
        return false;
    }

    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.addHeader("User-Agent", "ESP32-SmartTracker/2.0");

    DynamicJsonDocument doc(512);
    doc["device_id"]              = DEVICE_ID;
    doc["latitude"]               = serialized(String(currentLat, 6));
    doc["longitude"]              = serialized(String(currentLon, 6));
    doc["source_type"]            = "SOFTWARE_SIMULATION";
    doc["satellites_count"]       = gps.satellites.isValid() ? gps.satellites.value() : 0;
    doc["esp32_distance_meters"]  = round(currentDistanceMeters * 10.0) / 10.0;
    doc["esp32_is_inside"]        = isInsideGeofence;
    doc["led_green_state"]        = ledGreenState;
    doc["led_red_state"]          = ledRedState;
    doc["buzzer_state"]           = buzzerState;
    doc["sos_active"]             = sosActive;

    if (configVersionToAck >= 0) {
        doc["config_version"] = configVersionToAck;
    }
    if (targetIdToAck > 0) {
        doc["target_id"] = targetIdToAck;
    }

    String requestBody;
    serializeJson(doc, requestBody);

    int httpCode = http.POST(requestBody);
    bool success = (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_CREATED);

    if (success) {
        lastTelemetryTime = millis();
        Serial.printf("[HTTPS Telemetry] Telemetry accepted (HTTP %d). Distance: %.1fm, Inside: %d\n",
                      httpCode, currentDistanceMeters, isInsideGeofence);
        if (targetIdToAck > 0) {
            Serial.printf("[HTTPS Telemetry] Target #%d successfully acknowledged and consumed.\n", targetIdToAck);
        }
    } else {
        if (httpCode < 0) {
            char err_buf[128];
            int err = secureClient.lastError(err_buf, sizeof(err_buf));
            Serial.printf("[HTTPS Telemetry] TLS/Network error: %s (code %d)\n", http.errorToString(httpCode).c_str(), httpCode);
            if (err < 0) Serial.printf("                 TLS detail: %s (err %d)\n", err_buf, err);
        } else {
            String resp = http.getString();
            Serial.printf("[HTTPS Telemetry] Transmission failed (HTTP %d). Response: %s\n", httpCode, resp.c_str());
        }
    }

    http.end();
    return success;
}

/**
 * Upload Pending Emergency SOS Event over Secure HTTPS
 * POST /api/tracker/sos/
 */
bool uploadPendingSOSEvent() {
    if (WiFi.status() != WL_CONNECTED || !timeSynchronized) return false;

    WiFiClientSecure secureClient;
    secureClient.setCACert(RENDER_CA_CERT);
    secureClient.setHandshakeTimeout(15);
    secureClient.setTimeout(15);

    HTTPClient http;
    http.setTimeout(HTTPS_TIMEOUT_MS);

    String url = String(DJANGO_SERVER_URL) + "/api/tracker/sos/";
    if (!http.begin(secureClient, url)) {
        Serial.println("[HTTPS SOS] Failed to initialize secure connection.");
        return false;
    }

    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.addHeader("User-Agent", "ESP32-SmartTracker/2.0");

    DynamicJsonDocument doc(512);
    doc["device_id"]       = DEVICE_ID;
    doc["trigger_source"]  = "HARDWARE_BUTTON";
    doc["has_gps_fix"]     = false;
    doc["latitude"]        = serialized(String(currentLat, 6));
    doc["longitude"]       = serialized(String(currentLon, 6));
    doc["location_source"] = "SOFTWARE_SIMULATION";

    String requestBody;
    serializeJson(doc, requestBody);

    int httpCode = http.POST(requestBody);
    bool success = (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_CREATED);

    if (success) {
        pendingSosUpload = false;
        persistStateToNVS();
        Serial.printf("[HTTPS SOS] Emergency SOS successfully registered in Django backend (HTTP %d).\n", httpCode);
    } else {
        if (httpCode < 0) {
            char err_buf[128];
            int err = secureClient.lastError(err_buf, sizeof(err_buf));
            Serial.printf("[HTTPS SOS] TLS/Network error: %s (code %d)\n", http.errorToString(httpCode).c_str(), httpCode);
            if (err < 0) Serial.printf("           TLS detail: %s (err %d)\n", err_buf, err);
        } else {
            Serial.printf("[HTTPS SOS] SOS transmission failed (HTTP %d). Will retry.\n", httpCode);
        }
    }

    http.end();
    return success;
}

/**
 * Acknowledge Remote SOS Clear Command over Secure HTTPS
 * POST /api/tracker/device/clear-sos-ack/
 */
bool acknowledgeClearSOS() {
    if (WiFi.status() != WL_CONNECTED || !timeSynchronized) return false;

    WiFiClientSecure secureClient;
    secureClient.setCACert(RENDER_CA_CERT);
    secureClient.setHandshakeTimeout(15);
    secureClient.setTimeout(15);

    HTTPClient http;
    http.setTimeout(HTTPS_TIMEOUT_MS);

    String url = String(DJANGO_SERVER_URL) + "/api/tracker/device/clear-sos-ack/";
    if (!http.begin(secureClient, url)) {
        Serial.println("[HTTPS SOS-Ack] Failed to initialize secure connection.");
        return false;
    }

    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.addHeader("User-Agent", "ESP32-SmartTracker/2.0");

    DynamicJsonDocument doc(256);
    doc["device_id"]     = DEVICE_ID;
    doc["clear_sos_ack"] = true;

    String requestBody;
    serializeJson(doc, requestBody);

    int httpCode = http.POST(requestBody);
    bool success = (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_ACCEPTED);

    if (success) {
        Serial.println("[HTTPS SOS-Ack] Remote clear SOS command acknowledged to Django backend.");
    } else {
        Serial.printf("[HTTPS SOS-Ack] Acknowledgement failed (HTTP %d).\n", httpCode);
    }

    http.end();
    return success;
}

// =============================================================================
// Wi-Fi Connection Management & Event Diagnostics
// =============================================================================
void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
    switch (event) {
        case ARDUINO_EVENT_WIFI_STA_START:
            Serial.println("[Wi-Fi Event] Station interface started.");
            break;
        case ARDUINO_EVENT_WIFI_STA_CONNECTED:
            Serial.println("[Wi-Fi Event] Successfully connected to AP.");
            break;
        case ARDUINO_EVENT_WIFI_STA_GOT_IP: {
            String ip = WiFi.localIP().toString();
            Serial.printf("[Wi-Fi Event] IP address acquired: %s\n", ip.c_str());
            syncTime();
            break;
        }
        case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
            Serial.printf("[Wi-Fi Event] Disconnected from AP. Reason code: %d\n", info.wifi_sta_disconnected.reason);
            timeSynchronized = false;
            break;
        default:
            break;
    }
}

void maintainWiFi() {
    if (wifiManuallyDisabled) return;
    if (WiFi.status() == WL_CONNECTED) {
        if (!timeSynchronized) {
            syncTime();
        }
        return;
    }

    unsigned long currentMillis = millis();
    if (currentMillis - lastWifiRetryTime >= WIFI_RETRY_INTERVAL) {
        lastWifiRetryTime = currentMillis;
        Serial.printf("[Wi-Fi] Reconnecting to '%s' (status=%d)...\n", WIFI_SSID, (int)WiFi.status());
        WiFi.disconnect();
        delay(100);
        WiFi.setMinSecurity(WIFI_AUTH_WPA_PSK);
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }
}

// =============================================================================
// Serial Command Handler
// =============================================================================
void handleSerialCommands() {
    while (Serial.available() > 0) {
        char ch = (char)Serial.read();
        if (ch == '\n' || ch == '\r') {
            if (serialInputBuffer.length() > 0) {
                serialInputBuffer.trim();
                serialInputBuffer.toUpperCase();

                if (serialInputBuffer == "RESET" || serialInputBuffer == "R" ||
                    serialInputBuffer == "CLEAR" || serialInputBuffer == "RESOLVE") {
                    clearSOS();
                } else if (serialInputBuffer == "SOS" || serialInputBuffer == "TEST") {
                    triggerSOS("SERIAL_COMMAND");
                } else if (serialInputBuffer == "STATUS" || serialInputBuffer == "DIAG") {
                    reportDiagnostics();
                } else if (serialInputBuffer == "WIFIOFF") {
                    wifiManuallyDisabled = true;
                    WiFi.disconnect(true, false);
                    Serial.println("[Wi-Fi CMD] Wi-Fi manually disconnected for offline testing.");
                } else if (serialInputBuffer == "WIFION") {
                    wifiManuallyDisabled = false;
                    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
                    Serial.println("[Wi-Fi CMD] Wi-Fi manually re-enabled. Connecting...");
                } else if (serialInputBuffer == "REBOOT" || serialInputBuffer == "RESTART") {
                    Serial.println("[CMD] System rebooting now...");
                    delay(200);
                    ESP.restart();
                } else if (serialInputBuffer == "HELP") {
                    Serial.println();
                    Serial.println("--- Available Serial Monitor Commands ---");
                    Serial.println("  RESET / CLEAR  - Reset active SOS latch");
                    Serial.println("  SOS            - Trigger SOS alert (SERIAL_COMMAND)");
                    Serial.println("  STATUS         - Print detailed subsystem diagnostics");
                    Serial.println("  WIFIOFF        - Simulate network disconnection (offline test)");
                    Serial.println("  WIFION         - Reconnect to Wi-Fi access point");
                    Serial.println("  REBOOT         - Soft-reboot ESP32");
                    Serial.println("  HELP           - Display this help message");
                    Serial.println("-----------------------------------------");
                } else {
                    Serial.printf(">>> Unknown command '%s'. Type 'HELP' or 'RESET'.\n", serialInputBuffer.c_str());
                }
                serialInputBuffer = "";
            }
        } else {
            if (serialInputBuffer.length() < 32) {
                serialInputBuffer += ch;
            }
        }
    }
}

// =============================================================================
// Diagnostics Output
// =============================================================================
void reportDiagnostics() {
    unsigned long uptimeSec = millis() / 1000;
    String ipStr = (WiFi.status() == WL_CONNECTED) ? WiFi.localIP().toString() : "DISCONNECTED";

    Serial.println("-----------------------------------------------------------------");
    Serial.printf("[DIAG] Uptime: %lu s | Free Heap: %lu B | Wi-Fi: %s (status=%d)\n",
                  uptimeSec, (unsigned long)ESP.getFreeHeap(), ipStr.c_str(), WiFi.status());
    Serial.printf("       Time Synced  : %s | Backend: %s\n",
                  timeSynchronized ? "YES (SNTP)" : "NO (PENDING)", DJANGO_SERVER_URL);
    Serial.printf("       Coordinates  : Lat: %.6f, Lon: %.6f (Source: SOFTWARE_SIMULATION)\n",
                  currentLat, currentLon);
    Serial.printf("       Geofence     : Center(%.6f, %.6f), Radius: %.1fm\n",
                  geofenceCenterLat, geofenceCenterLon, geofenceRadiusMeters);
    Serial.printf("       Distance     : %.1fm | Boundary State: %s\n",
                  currentDistanceMeters, isInsideGeofence ? "INSIDE" : "OUTSIDE (BREACH)");
    Serial.printf("       Hardware Pins: Green(27):%d | Red(26):%d | Buzzer(25):%d | SOS-Btn(18): raw=%d, debounced=%d\n",
                  ledGreenState, ledRedState, buzzerState, digitalRead(PIN_SOS_BUTTON), debouncedButtonState);
    Serial.printf("       SOS Subsystem: Active:%d | Pending-Upload:%d | EventCount:%lu\n",
                  sosActive, pendingSosUpload, sosEventCount);
    Serial.printf("       Config Sync  : ProcessedVersion:%ld\n", lastProcessedConfigVersion);
    Serial.printf("       GPS UART(2)  : RawBytes:%lu | Fix:%s | Sats:%lu\n",
                  rawGpsBytes, gps.location.isValid() ? "YES" : "NO",
                  gps.satellites.isValid() ? (unsigned long)gps.satellites.value() : 0UL);
    Serial.println("-----------------------------------------------------------------");
}

// =============================================================================
// Arduino Setup
// =============================================================================
void setup() {
    // 1. Initialize Debug Serial Monitor
    Serial.begin(115200);
    delay(1000);

    Serial.println();
    Serial.println("=================================================================");
    Serial.println(" SMART ASSET TRACKER - FIRMWARE PHASE B (SECURE CLOUD HTTPS)   ");
    Serial.println(" Target: ESP32 DevKit | Backend: Render Cloud (SSL/TLS Verified) ");
    Serial.println("=================================================================");

    // 2. Configure Hardware Output Pins
    pinMode(PIN_LED_GREEN, OUTPUT);
    pinMode(PIN_LED_RED,   OUTPUT);
    pinMode(PIN_BUZZER,    OUTPUT);

    // 3. Configure Physical SOS Button Pin with internal pull-up (Active LOW)
    pinMode(PIN_SOS_BUTTON, INPUT_PULLUP);
    gpio_set_pull_mode((gpio_num_t)PIN_SOS_BUTTON, GPIO_PULLUP_ONLY);
    gpio_pullup_en((gpio_num_t)PIN_SOS_BUTTON);
    gpio_pulldown_dis((gpio_num_t)PIN_SOS_BUTTON);
    delay(50); // Stabilization
    int initRead = digitalRead(PIN_SOS_BUTTON);
    Serial.printf("[BUTTON-INIT] GPIO 18 initial read: %d (Expected: 1/HIGH when released)\n", initRead);
    lastButtonReading    = initRead;
    debouncedButtonState = initRead;

    // 4. Initialize Non-Volatile Storage (Preferences)
    preferences.begin("tracker_nv", false);
    restoreStateFromNVS();

    // 5. Initial Geofence Calculation & Output Driver Update
    recalculateGeofence();

    // 6. Initialize Hardware UART2 for NEO-6M GPS Diagnostics
    gpsSerial.begin(GPS_BAUD, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);

    // 7. Initiate Wi-Fi Connection
    WiFi.onEvent(onWiFiEvent);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
    WiFi.setMinSecurity(WIFI_AUTH_WPA_PSK);
    Serial.printf("[Wi-Fi] Connecting to SSID: '%s'...\n", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    lastWifiRetryTime = millis();

    Serial.println("[Setup] Hardware initialization complete. System running.");
    Serial.println("=================================================================");
    Serial.println();
}

// =============================================================================
// Arduino Main Loop (Non-Blocking Task Architecture)
// =============================================================================
void loop() {
    unsigned long currentMillis = millis();

    // -------------------------------------------------------------------------
    // 1. Ingest Raw NMEA Stream from NEO-6M GPS (UART2)
    // -------------------------------------------------------------------------
    while (gpsSerial.available() > 0) {
        char c = (char)gpsSerial.read();
        rawGpsBytes++;
        lastGpsByteTime = currentMillis;
        gps.encode(c);
    }

    // -------------------------------------------------------------------------
    // 2. Physical SOS Button Debounce & Active-LOW Edge Detection
    // -------------------------------------------------------------------------
    int currentReading = digitalRead(PIN_SOS_BUTTON);
    if (currentReading != lastButtonReading) {
        lastDebounceTime = currentMillis;
        Serial.printf("[BUTTON-RAW] GPIO 18 changed from %d to %d\n", lastButtonReading, currentReading);
    }

    if ((currentMillis - lastDebounceTime) > BUTTON_DEBOUNCE_DELAY) {
        if (currentReading != debouncedButtonState) {
            debouncedButtonState = currentReading;
            Serial.printf("[BUTTON-DEBOUNCED] New stable state: %d\n", debouncedButtonState);

            // Trigger when button transitions from HIGH to LOW (Pressed)
            if (debouncedButtonState == BUTTON_ACTIVE_STATE && !sosActive) {
                triggerSOS("HARDWARE_BUTTON");
            }
        }
    }
    lastButtonReading = currentReading;

    // -------------------------------------------------------------------------
    // 3. Process Serial Commands
    // -------------------------------------------------------------------------
    handleSerialCommands();

    // -------------------------------------------------------------------------
    // 4. Maintain Wi-Fi Connection
    // -------------------------------------------------------------------------
    maintainWiFi();

    // -------------------------------------------------------------------------
    // 5. Flush Pending SOS Events to Backend
    // -------------------------------------------------------------------------
    if (pendingSosUpload && (currentMillis - lastSosFlushTime >= SOS_FLUSH_INTERVAL)) {
        lastSosFlushTime = currentMillis;
        uploadPendingSOSEvent();
    }

    // -------------------------------------------------------------------------
    // 6. Poll Django Device Configuration
    // -------------------------------------------------------------------------
    if (currentMillis - lastConfigPollTime >= CONFIG_POLL_INTERVAL) {
        lastConfigPollTime = currentMillis;
        pollDeviceConfig();
    }

    // -------------------------------------------------------------------------
    // 7. Periodic Telemetry Transmission
    // -------------------------------------------------------------------------
    if (currentMillis - lastTelemetryTime >= TELEMETRY_INTERVAL) {
        lastTelemetryTime = currentMillis;
        sendTelemetry(0, lastProcessedConfigVersion);
    }

    // -------------------------------------------------------------------------
    // 8. Periodic Serial Diagnostics Report
    // -------------------------------------------------------------------------
    if (currentMillis - lastDiagnosticsTime >= DIAGNOSTICS_INTERVAL) {
        lastDiagnosticsTime = currentMillis;
        reportDiagnostics();
    }

    yield();
}
