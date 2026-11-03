/**
 * geofence.js - Dynamic Geofence Management UI Controller
 * Manages geofence radius adjustments, center repositioning,
 * and synchronizing with Django backend APIs.
 */

const GeofenceUI = (() => {
    let currentGeofence = null;

    const init = () => {
        const radiusSlider = document.getElementById('inputGeofenceRadius');
        const radiusDisplay = document.getElementById('displayRadiusValue');
        const form = document.getElementById('geofenceForm');
        const btnSetFromAsset = document.getElementById('btnSetCenterFromCurrent');

        // Dynamic slider value readout
        if (radiusSlider && radiusDisplay) {
            radiusSlider.addEventListener('input', (e) => {
                radiusDisplay.textContent = `${e.target.value} m`;
                // Live preview on map if center coordinates exist
                const lat = parseFloat(document.getElementById('inputGeofenceLat').value);
                const lon = parseFloat(document.getElementById('inputGeofenceLon').value);
                const isActive = document.getElementById('switchGeofenceActive').checked;
                if (!isNaN(lat) && !isNaN(lon)) {
                    TrackerMap.updateGeofence(lat, lon, parseFloat(e.target.value), isActive);
                }
            });
        }

        // Set geofence center from asset's last known location
        if (btnSetFromAsset) {
            btnSetFromAsset.addEventListener('click', () => {
                const status = DashboardController.getCurrentStatus();
                if (status && status.last_known_latitude && status.last_known_longitude) {
                    document.getElementById('inputGeofenceLat').value = Number(status.last_known_latitude).toFixed(6);
                    document.getElementById('inputGeofenceLon').value = Number(status.last_known_longitude).toFixed(6);
                    const radius = parseFloat(radiusSlider.value);
                    TrackerMap.updateGeofence(status.last_known_latitude, status.last_known_longitude, radius, true);
                } else {
                    alert('No valid asset location available to use as geofence center.');
                }
            });
        }

        // Form Submission via PUT API
        if (form) {
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const btnSave = document.getElementById('btnSaveGeofence');
                btnSave.disabled = true;
                btnSave.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Saving...';

                const deviceId = DashboardController.getActiveDeviceId();
                const lat = parseFloat(document.getElementById('inputGeofenceLat').value);
                const lon = parseFloat(document.getElementById('inputGeofenceLon').value);
                const radius = parseFloat(document.getElementById('inputGeofenceRadius').value);
                const isActive = document.getElementById('switchGeofenceActive').checked;

                const payload = {
                    device_id: deviceId,
                    center_latitude: lat,
                    center_longitude: lon,
                    radius_meters: radius,
                    is_active: isActive
                };

                try {
                    const result = await TrackerAPI.updateGeofence(payload);
                    TrackerMap.updateGeofence(result.center_latitude, result.center_longitude, result.radius_meters, result.is_active);
                    currentGeofence = result;
                    btnSave.classList.replace('btn-primary', 'btn-success');
                    btnSave.innerHTML = '<i class="bi bi-check-circle me-1"></i> Saved Successfully';
                    setTimeout(() => {
                        btnSave.classList.replace('btn-success', 'btn-primary');
                        btnSave.innerHTML = '<i class="bi bi-save me-1"></i> Update Geofence via API';
                        btnSave.disabled = false;
                    }, 2000);

                    // Refresh dashboard state
                    DashboardController.pollStatus();
                } catch (err) {
                    btnSave.disabled = false;
                    btnSave.innerHTML = '<i class="bi bi-save me-1"></i> Update Geofence via API';

                    // Check for authorization error
                    if (err.message.includes('Forbidden') || err.message.includes('Unauthorized') || err.message.includes('403')) {
                        DashboardController.openAdminLoginModal('Administrative authorization required to update geofence boundary. Please sign in as an Operator or provide Dashboard Key.');
                    } else {
                        alert(`Failed to update geofence: ${err.message}`);
                    }
                }
            });
        }
    };

    const populateForm = (geofence) => {
        if (!geofence) return;
        currentGeofence = geofence;

        const inputLat = document.getElementById('inputGeofenceLat');
        const inputLon = document.getElementById('inputGeofenceLon');
        const inputRadius = document.getElementById('inputGeofenceRadius');
        const radiusDisplay = document.getElementById('displayRadiusValue');
        const switchActive = document.getElementById('switchGeofenceActive');
        const badgeActive = document.getElementById('geofenceActiveBadge');

        if (inputLat) inputLat.value = Number(geofence.center_latitude).toFixed(6);
        if (inputLon) inputLon.value = Number(geofence.center_longitude).toFixed(6);
        if (inputRadius) inputRadius.value = geofence.radius_meters;
        if (radiusDisplay) radiusDisplay.textContent = `${geofence.radius_meters} m`;
        if (switchActive) switchActive.checked = geofence.is_active;

        if (badgeActive) {
            badgeActive.textContent = geofence.is_active ? 'Active' : 'Disabled';
            badgeActive.className = `badge ${geofence.is_active ? 'bg-success' : 'bg-secondary'}`;
        }
    };

    return {
        init,
        populateForm,
        getCurrentGeofence: () => currentGeofence
    };
})();

