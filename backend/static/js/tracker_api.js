/**
 * tracker_api.js - Centralized API Communication Service
 * Handles fetch calls to Django REST endpoints, CSRF token attachment,
 * and error handling.
 */

const TrackerAPI = (() => {
    const API_BASE = '/api/tracker';

    // Retrieve CSRF token from HTML meta tag
    const getCsrfToken = () => {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    };

    const request = async (endpoint, options = {}) => {
        const headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-CSRFToken': getCsrfToken(),
            ...(options.headers || {})
        };

        const config = {
            ...options,
            credentials: 'same-origin',
            headers
        };

        try {
            const response = await fetch(`${API_BASE}${endpoint}`, config);
            const data = await response.json().catch(() => ({}));

            if (!response.ok) {
                let errorMsg = `HTTP ${response.status}`;
                if (data.details) {
                    errorMsg = data.error ? `${data.error}: ${data.details}` : data.details;
                } else if (data.error) {
                    errorMsg = data.error;
                } else if (data.detail) {
                    errorMsg = data.detail;
                }
                throw new Error(typeof errorMsg === 'object' ? JSON.stringify(errorMsg) : errorMsg);
            }
            return data;
        } catch (error) {
            console.error(`API Error [${endpoint}]:`, error);
            throw error;
        }
    };

    return {
        // Status & Overview
        getStatus: (deviceId = 'ASSET-ESP32-01') => {
            return request(`/status/?device_id=${encodeURIComponent(deviceId)}`, { method: 'GET' });
        },

        // Geofence
        getGeofence: (deviceId = 'ASSET-ESP32-01') => {
            return request(`/geofence/?device_id=${encodeURIComponent(deviceId)}`, { method: 'GET' });
        },

        updateGeofence: (payload) => {
            return request(`/geofence/`, {
                method: 'PUT',
                body: JSON.stringify(payload)
            });
        },

        // Location Telemetry Ingestion
        sendLocation: (payload) => {
            return request(`/location/`, {
                method: 'POST',
                body: JSON.stringify(payload)
            });
        },

        // Location History
        getHistory: (params = {}) => {
            const query = new URLSearchParams(params).toString();
            return request(`/history/?${query}`, { method: 'GET' });
        },

        // SOS Alerts
        triggerSos: (payload, deviceToken = null) => {
            const headers = {};
            if (deviceToken) {
                headers['X-Device-Token'] = deviceToken;
            }
            return request(`/sos/`, {
                method: 'POST',
                headers,
                body: JSON.stringify(payload)
            });
        },

        getActiveSos: (deviceId = '') => {
            const q = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : '';
            return request(`/sos/active/${q}`, { method: 'GET' });
        },

        getSosHistory: (deviceId = '') => {
            const q = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : '';
            return request(`/sos/history/${q}`, { method: 'GET' });
        },

        resolveSos: (sosId, payload) => {
            return request(`/sos/${sosId}/resolve/`, {
                method: 'POST',
                body: JSON.stringify(payload)
            });
        },

        // Operator / Admin Authentication
        loginAdmin: (credentials) => {
            return request(`/auth/login/`, {
                method: 'POST',
                body: JSON.stringify(credentials)
            });
        },

        logoutAdmin: () => {
            return request(`/auth/logout/`, {
                method: 'POST'
            });
        },

        getAuthStatus: () => {
            return request(`/auth/status/`, {
                method: 'GET'
            });
        }
    };
})();

