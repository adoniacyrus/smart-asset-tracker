# Smart Asset Location Tracker with Dynamic Geofencing and SOS Alert

An academic IoT project developed for the MCA curriculum, combining an ESP32 hardware tracking node with a robust Django backend, MySQL 8 database, and an interactive Leaflet.js dashboard.

---

## 1. Project Overview

The **Smart Asset Location Tracker** monitors the physical or simulated location of an asset, evaluates distance against configurable dynamic geofence boundaries using the Haversine formula, and manages emergency SOS alerts triggered from physical hardware or the dashboard.

### Core Architecture Highlights
- **Hardware Layer**: ESP32 DevKit (38-pin), NEO-6M GPS module (UART), SOS push button, buzzer, and status LEDs.
- **Backend Layer**: Python 3.11, Django, Django REST Framework, MySQL 8. All geofence boundary calculations and state-tracking logic reside in Django services.
- **Frontend Layer**: Responsive Bootstrap 5 dashboard, Leaflet.js mapping, OpenStreetMap tiles, and AJAX telemetry polling.

---

## 2. Three Location Operating Modes

To facilitate both real-world deployment and thorough laboratory/academic evaluation, the system implements three distinct operating modes:

1. **Mode 1: Real GPS Mode (`REAL_GPS`)**
   - Transmits live NMEA coordinates acquired by the NEO-6M GPS module via ESP32 Wi-Fi HTTP requests.
   - Requires a valid satellite fix (`has_gps_fix: true`).
   - *Note*: If the ceramic patch antenna is absent or unlinked, the system does not substitute simulated data, preserving data integrity.

2. **Mode 2: Dynamic Simulation Mode (`DYNAMIC_SIMULATION`)**
   - Software-generated coordinate movement along predefined or interactive transit routes.
   - Demonstrates real-time geofence entry and exit events with configurable speed, pause, resume, and reset controls.

3. **Mode 3: Static Simulation Mode (`STATIC_SIMULATION`)**
   - Allows an evaluator or operator to pin a specific latitude/longitude on the map or enter manual coordinates.
   - Useful for testing boundary threshold conditions (inside vs. outside geofence).

Every location record stored in MySQL is tagged with its definitive `source_type` (`REAL_GPS`, `DYNAMIC_SIMULATION`, `STATIC_SIMULATION`).

---

## 3. SOS Alert System

- **Physical Trigger**: Push button connected to ESP32 with hardware-safe debouncing.
- **Local Feedback**: Pulsed emergency buzzer audio pattern and high-intensity Red LED alert.
- **Server Telemetry**: Dispatches an instant HTTP POST to `/api/tracker/sos/`.
- **Antenna Independence**: If the NEO-6M has no satellite lock, the SOS event is still recorded with `has_gps_fix: false` and coordinates set to `null` to prevent misleading stale data.
- **Operator Lifecycle**: Operators can review, acknowledge, and resolve active alerts directly from the web dashboard.

---

## 4. Directory Structure

```
IoT-Project/
│
├── .gitignore
├── README.md
│
├── backend/                        # Django REST Backend & Web Dashboard
│   ├── manage.py
│   ├── requirements.txt
│   ├── .env.example
│   ├── config/                     # Core Django configuration
│   ├── tracker/                    # Geofencing, telemetry & SOS app
│   ├── templates/                  # HTML templates (Bootstrap 5)
│   └── static/                     # CSS, JS (Leaflet, Simulation), Icons
│
├── firmware/                       # ESP32 PlatformIO Project (Arduino C++)
│   ├── platformio.ini
│   ├── include/
│   │   ├── config.h
│   │   └── secrets.h.example
│   └── src/
│       └── main.cpp
│
└── documentation/                  # Academic Project Reports & Schematics
    ├── circuit_diagram.png
    ├── api_specification.md
    └── system_architecture.md
```

---

## 5. Security & Academic Integrity

- Database passwords, secret keys, and Wi-Fi credentials are kept strictly out of version control using `.env` and `secrets.h`.
- Real GPS data and simulated telemetry are logically isolated.

