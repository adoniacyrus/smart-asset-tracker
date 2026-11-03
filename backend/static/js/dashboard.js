/**
 * dashboard.js - Core Dashboard Application Controller
 * Genuinely connected to Django REST API endpoints.
 * Synchronizes real-time status polling, map markers, overview metrics,
 * dual-theme toggles, location history, geofence, and SOS emergency alerts.
 * Strictly distinguishes REAL_GPS, DYNAMIC_SIMULATION, and STATIC_SIMULATION.
 */

const DashboardController = (() => {
    let activeDeviceId = 'ASSET-ESP32-01';
    let currentMode = 'REAL_GPS';
    let currentStatus = null;
    let pollIntervalId = null;
    let isPolling = false;
    let adminModal = null;
    let hasAutoCentered = false;

    const init = async () => {
        // 1. Initialize Dual Theme
        initTheme();

        // 2. Initialize Leaflet Map with Amal Jyothi College of Engineering coordinates
        TrackerMap.init(9.5287, 76.8226, 16);

        // 3. Initialize Sub-controllers
        GeofenceUI.init();
        SOSController.init();
        SimulationController.init();

        // 4. Bind UI Controls & Admin Auth
        bindEvents();
        initAdminAuth();

        // 5. Initial Data Fetch from Django Backend
        await pollStatus();
        await refreshHistory();

        // 6. Set Polling Interval (every 3.5 seconds)
        pollIntervalId = setInterval(pollStatus, 3500);
    };

    const initTheme = () => {
        const storedTheme = localStorage.getItem('sat_theme') || 'light';
        applyTheme(storedTheme);

        const btnTheme = document.getElementById('btnThemeToggle');
        if (btnTheme) {
            btnTheme.addEventListener('click', () => {
                const current = document.body.getAttribute('data-bs-theme') || 'light';
                const next = current === 'light' ? 'dark' : 'light';
                applyTheme(next);
                localStorage.setItem('sat_theme', next);
            });
        }
    };

    const applyTheme = (theme) => {
        document.body.setAttribute('data-bs-theme', theme);
        if (theme === 'dark') {
            document.body.classList.remove('theme-light');
            document.body.classList.add('theme-dark');
        } else {
            document.body.classList.remove('theme-dark');
            document.body.classList.add('theme-light');
        }

        const icon = document.getElementById('themeToggleIcon');
        const text = document.getElementById('themeToggleText');
        if (icon && text) {
            if (theme === 'dark') {
                icon.className = 'bi bi-sun-fill me-1 text-warning';
                text.textContent = 'Light Mode';
            } else {
                icon.className = 'bi bi-moon-stars me-1 text-primary';
                text.textContent = 'Dark Mode';
            }
        }
    };

    const bindEvents = () => {
        // Map Controls
        const btnCenter = document.getElementById('btnCenterMap');
        if (btnCenter) btnCenter.addEventListener('click', () => TrackerMap.centerOnAsset());

        const btnFit = document.getElementById('btnFitGeofence');
        if (btnFit) btnFit.addEventListener('click', () => TrackerMap.fitGeofence());

        // Refresh History Button
        const btnRefreshHist = document.getElementById('btnRefreshHistory');
        if (btnRefreshHist) {
            btnRefreshHist.addEventListener('click', () => refreshHistory());
        }

        // Mode Switching UI
        const modeButtons = document.querySelectorAll('#modeSelectorGroup button');
        modeButtons.forEach(btn => {
            btn.addEventListener('click', (e) => {
                modeButtons.forEach(b => b.classList.remove('active'));
                const targetBtn = e.currentTarget;
                targetBtn.classList.add('active');
                currentMode = targetBtn.getAttribute('data-mode');
                updateModeUiState(currentMode);
                SimulationController.handleModeChange(currentMode);
            });
        });
    };

    const updateModeUiState = (mode) => {
        const activeModeBadge = document.getElementById('activeModeBadge');
        const activeModePill = document.getElementById('activeModePill');
        const desc = document.getElementById('modeStatusDescription');

        if (activeModePill) activeModePill.textContent = mode;

        if (mode === 'REAL_GPS') {
            if (activeModeBadge) {
                activeModeBadge.textContent = 'MODE: REAL GPS';
                activeModeBadge.className = 'badge border border-primary text-primary px-2 py-1';
            }
            if (desc) desc.textContent = 'Listening for incoming hardware telemetry packets from the ESP32 tracking node.';
        } else if (mode === 'DYNAMIC_SIMULATION') {
            if (activeModeBadge) {
                activeModeBadge.textContent = 'MODE: DYNAMIC SIMULATION';
                activeModeBadge.className = 'badge border border-warning text-warning px-2 py-1';
            }
            if (desc) desc.textContent = 'Generates progressive movement along a test path to evaluate geofence transitions.';
        } else if (mode === 'STATIC_SIMULATION') {
            if (activeModeBadge) {
                activeModeBadge.textContent = 'MODE: STATIC SIMULATION';
                activeModeBadge.className = 'badge border border-info text-info px-2 py-1';
            }
            if (desc) desc.textContent = 'Manual coordinate placement on the map to test specific threshold boundaries.';
        }
    };

    const pollStatus = async () => {
        if (isPolling) return;
        isPolling = true;

        const spin = document.getElementById('pollingSpinIcon');
        const lastPolled = document.getElementById('lastPolledText');
        const apiBadge = document.getElementById('apiConnectionBadge');
        if (spin) spin.classList.add('text-primary');

        const startTime = Date.now();

        try {
            // Fetch genuine live status from Django backend
            const data = await TrackerAPI.getStatus(activeDeviceId);
            currentStatus = data;
            const elapsedMs = Date.now() - startTime;

            // 1. Backend API Connection Status Indicator
            if (apiBadge) {
                apiBadge.className = 'badge bg-success-subtle text-success border border-success me-2';
                apiBadge.innerHTML = '<i class="bi bi-cloud-check-fill me-1"></i> API Connected';
            }
            if (lastPolled) {
                lastPolled.className = 'text-secondary font-monospace small d-none d-md-block me-2';
                lastPolled.innerHTML = `<i class="bi bi-arrow-repeat me-1" id="pollingSpinIcon"></i> Synced: ${new Date().toLocaleTimeString()} <span class="text-secondary" style="font-size: 0.68rem;">(${elapsedMs}ms)</span>`;
            }

            // 2. Hardware ESP32 Online/Offline Status (Authoritative backend heartbeat check)
            const connBadge = document.getElementById('deviceConnectionBadge');
            const antennaWarning = document.getElementById('antennaWarningNote');
            const isDeviceOnline = Boolean(data.hardware_connection && data.hardware_connection.is_online);
            const now = Date.now();
            const lastPacketTime = data.last_known_timestamp ? new Date(data.last_known_timestamp).getTime() : null;
            const packetAgeSec = lastPacketTime ? Math.round((now - lastPacketTime) / 1000) : null;

            if (isDeviceOnline) {
                // Device is actively connected and sending heartbeats / telemetry to backend
                if (connBadge) {
                    connBadge.textContent = 'Hardware Online';
                    connBadge.className = 'badge bg-success';
                }
                if (antennaWarning) {
                    const sourceText = data.last_known_source === 'REAL_GPS'
                        ? 'NEO-6M GPS stream active'
                        : `Telemetry stream active (${data.last_known_source || 'Online'})`;
                    antennaWarning.innerHTML = `<i class="bi bi-broadcast text-success me-1"></i> ${sourceText}`;
                }
            } else if (packetAgeSec !== null && packetAgeSec <= 60) {
                // Device sent telemetry recently but missed heartbeat threshold
                if (connBadge) {
                    connBadge.textContent = 'Hardware Inactive';
                    connBadge.className = 'badge bg-warning text-dark';
                }
                if (antennaWarning) {
                    antennaWarning.innerHTML = `<i class="bi bi-clock-history text-warning me-1"></i> Last seen ${packetAgeSec}s ago`;
                }
            } else {
                // No physical ESP32 connected or heartbeats expired
                if (connBadge) {
                    connBadge.textContent = 'Hardware Idle / Offline';
                    connBadge.className = 'badge bg-secondary';
                }
                if (antennaWarning) {
                    const activeSourceDesc = data.last_known_source ? data.last_known_source : 'No Telemetry';
                    antennaWarning.innerHTML = `<i class="bi bi-info-circle text-secondary me-1"></i> Source: ${activeSourceDesc}`;
                }
            }

            // 3. Overview Metric 1: Telemetry Source
            const metricModeTitle = document.getElementById('metricModeTitle');
            const metricModeDetail = document.getElementById('metricModeDetail');
            if (metricModeTitle) {
                metricModeTitle.textContent = data.last_known_source || data.current_mode || 'NO TELEMETRY';
            }
            if (metricModeDetail) {
                if (data.last_known_source === 'REAL_GPS') {
                    metricModeDetail.textContent = 'ESP32 Hardware UART Stream';
                } else if (data.last_known_source === 'SOFTWARE_SIMULATION') {
                    metricModeDetail.textContent = 'ESP32 Firmware Simulation Stream';
                } else if (data.last_known_source === 'DYNAMIC_SIMULATION') {
                    metricModeDetail.textContent = 'Software Route Simulation';
                } else if (data.last_known_source === 'STATIC_SIMULATION') {
                    metricModeDetail.textContent = 'Manual Coordinate Placement';
                } else {
                    metricModeDetail.textContent = 'Awaiting Telemetry Ingestion';
                }
            }

            // 4. Overview Metric 2: Geofence State & Distance
            const gfBadge = document.getElementById('metricGeofenceBadge');
            const distVal = document.getElementById('metricDistanceVal');
            const gfDetail = document.getElementById('metricGeofenceDetail');

            if (data.last_known_latitude !== null && data.last_known_longitude !== null) {
                if (data.is_inside_geofence === true) {
                    if (gfBadge) {
                        gfBadge.textContent = 'INSIDE BOUNDARY';
                        gfBadge.className = 'badge bg-success';
                    }
                } else if (data.is_inside_geofence === false) {
                    if (gfBadge) {
                        gfBadge.textContent = 'OUTSIDE BOUNDARY';
                        gfBadge.className = 'badge bg-danger';
                    }
                } else {
                    if (gfBadge) {
                        gfBadge.textContent = 'AWAITING EVAL';
                        gfBadge.className = 'badge bg-secondary';
                    }
                }

                if (data.distance_from_center_meters !== null) {
                    if (distVal) distVal.textContent = `${Number(data.distance_from_center_meters).toFixed(1)} m`;
                } else {
                    if (distVal) distVal.textContent = '-- m';
                }
            } else {
                if (gfBadge) {
                    gfBadge.textContent = 'NO COORDINATES';
                    gfBadge.className = 'badge bg-secondary';
                }
                if (distVal) distVal.textContent = '-- m';
            }

            if (data.geofence) {
                if (gfDetail) {
                    const cLat = Number(data.geofence.center_latitude).toFixed(6);
                    const cLon = Number(data.geofence.center_longitude).toFixed(6);
                    gfDetail.innerHTML = `<span class="text-info"><i class="bi bi-crosshair me-1"></i>${cLat}, ${cLon}</span> &bull; Radius: ${data.geofence.radius_meters}m`;
                }
                GeofenceUI.populateForm(data.geofence);
                TrackerMap.updateGeofence(
                    data.geofence.center_latitude,
                    data.geofence.center_longitude,
                    data.geofence.radius_meters,
                    data.geofence.is_active
                );
            }

            // 5. Overview Metric 3: Coordinates Display (Strictly accurate)
            const coordDisplay = document.getElementById('metricCoordinatesDisplay');
            const coordTimestamp = document.getElementById('metricCoordinateTimestamp');

            if (data.last_known_latitude !== null && data.last_known_longitude !== null) {
                if (coordDisplay) {
                    coordDisplay.textContent = `${Number(data.last_known_latitude).toFixed(6)}, ${Number(data.last_known_longitude).toFixed(6)}`;
                    coordDisplay.classList.add('text-success');
                    coordDisplay.classList.remove('text-danger');
                }
                if (coordTimestamp && data.last_known_timestamp) {
                    const timeStr = new Date(data.last_known_timestamp).toLocaleTimeString();
                    coordTimestamp.textContent = `Updated: ${timeStr} (${data.last_known_source})`;
                }

                // Update Leaflet Map with actual backend coordinates
                TrackerMap.updateAsset(
                    data.last_known_latitude,
                    data.last_known_longitude,
                    data.last_known_source || 'REAL_GPS'
                );
            }

            // Auto-center map on geofence bounds on initial telemetry/geofence load
            if (!hasAutoCentered && data.geofence) {
                hasAutoCentered = true;
                TrackerMap.fitGeofence();
            }

            if (data.last_known_latitude === null || data.last_known_longitude === null) {
                // When no valid coordinates exist, display NO GPS FIX
                if (coordDisplay) {
                    coordDisplay.textContent = 'NO GPS FIX';
                    coordDisplay.classList.remove('text-success');
                    coordDisplay.classList.add('text-danger');
                }
                if (coordTimestamp) {
                    coordTimestamp.textContent = 'Antenna detached / Searching satellites';
                }

                // Clear marker and show notice on map
                TrackerMap.clearAssetMarker();
                TrackerMap.showNoLocationNotice('NO GPS FIX: Waiting for ESP32 & NEO-6M satellite fix');
            }

            // 6. Overview Metric 4: NEO-6M Status & Satellites (Never fabricate counts)
            const gpsStatus = document.getElementById('metricGpsStatusTitle');
            const gpsSats = document.getElementById('metricGpsSatellites');

            if (data.last_known_source === 'REAL_GPS') {
                if (data.last_known_latitude !== null && data.last_known_longitude !== null) {
                    if (gpsStatus) {
                        gpsStatus.textContent = 'GPS Fix Acquired';
                        gpsStatus.className = 'card-title fw-bold text-success mb-1';
                    }
                    if (gpsSats) {
                        gpsSats.textContent = '3D Fix Confirmed (Hardware Lock)';
                    }
                } else {
                    if (gpsStatus) {
                        gpsStatus.textContent = 'NO GPS FIX';
                        gpsStatus.className = 'card-title fw-bold text-danger mb-1';
                    }
                    if (gpsSats) {
                        gpsSats.textContent = '0 Satellites tracked (Antenna Idle)';
                    }
                }
            } else if (data.last_known_source === 'SOFTWARE_SIMULATION' || data.last_known_source === 'DYNAMIC_SIMULATION' || data.last_known_source === 'STATIC_SIMULATION') {
                if (gpsStatus) {
                    gpsStatus.textContent = 'Simulation Active';
                    gpsStatus.className = 'card-title fw-bold text-warning mb-1';
                }
                if (gpsSats) {
                    gpsSats.textContent = 'Software Coordinates (NEO-6M Idle)';
                }
            } else {
                if (gpsStatus) {
                    gpsStatus.textContent = 'NO GPS FIX';
                    gpsStatus.className = 'card-title fw-bold text-secondary mb-1';
                }
                if (gpsSats) {
                    gpsSats.textContent = 'Awaiting initial telemetry stream';
                }
            }

            // 7. Emergency SOS Notifications
            SOSController.updateAlertState(data.active_sos, data.active_sos_details);

            // 8. Auto-sync history table
            await refreshHistory();

        } catch (err) {
            console.error('Django Backend API connection error:', err);

            // Update UI indicators to show clear backend disconnect
            if (apiBadge) {
                apiBadge.className = 'badge bg-danger text-white me-2';
                apiBadge.innerHTML = '<i class="bi bi-cloud-slash-fill me-1"></i> API Disconnected';
            }
            if (lastPolled) {
                lastPolled.className = 'text-danger font-monospace small d-none d-md-block me-2';
                lastPolled.innerHTML = `<i class="bi bi-exclamation-triangle-fill me-1"></i> Connection Error (${err.message})`;
            }

            const connBadge = document.getElementById('deviceConnectionBadge');
            if (connBadge) {
                connBadge.textContent = 'Offline (API Error)';
                connBadge.className = 'badge bg-danger';
            }
        } finally {
            isPolling = false;
            if (spin) spin.classList.remove('text-primary');
        }
    };

    const refreshHistory = async () => {
        const tbody = document.getElementById('locationHistoryTableBody');
        if (!tbody) return;

        try {
            // Genuinely fetch from /api/tracker/history/
            const records = await TrackerAPI.getHistory({ device_id: activeDeviceId, limit: 10 });
            if (!records || records.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="text-center text-secondary py-3">No location records in database yet.</td></tr>';
                return;
            }

            // Update Satellite count on Metric Card 4 if latest record has satellite telemetry
            const latest = records[0];
            if (latest && latest.source_type === 'REAL_GPS' && latest.satellites_count > 0) {
                const gpsSats = document.getElementById('metricGpsSatellites');
                if (gpsSats) {
                    gpsSats.textContent = `${latest.satellites_count} Satellites tracked (Hardware UART)`;
                }
            }

            tbody.innerHTML = records.map(r => {
                const date = new Date(r.timestamp).toLocaleTimeString();
                let sourceBadge = `<span class="badge bg-secondary">${r.source_type}</span>`;
                if (r.source_type === 'REAL_GPS') {
                    sourceBadge = '<span class="badge bg-success-subtle text-success border border-success">REAL GPS</span>';
                } else if (r.source_type === 'DYNAMIC_SIMULATION') {
                    sourceBadge = '<span class="badge bg-warning-subtle text-warning border border-warning">DYNAMIC SIM</span>';
                } else if (r.source_type === 'STATIC_SIMULATION') {
                    sourceBadge = '<span class="badge bg-info-subtle text-info border border-info">STATIC SIM</span>';
                }

                const boundaryBadge = r.is_inside_geofence
                    ? '<span class="badge bg-success">INSIDE</span>'
                    : '<span class="badge bg-danger">OUTSIDE</span>';

                let transitionBadge = '<span class="text-secondary">--</span>';
                if (r.geofence_transition === 'ENTRY') {
                    transitionBadge = '<span class="badge bg-info text-dark fw-bold"><i class="bi bi-box-arrow-in-right"></i> ENTRY</span>';
                } else if (r.geofence_transition === 'EXIT') {
                    transitionBadge = '<span class="badge bg-danger fw-bold"><i class="bi bi-box-arrow-right"></i> EXIT</span>';
                }

                return `
                    <tr>
                        <td class="text-secondary">${date}</td>
                        <td>${sourceBadge}</td>
                        <td class="fw-semibold">${Number(r.latitude).toFixed(6)}</td>
                        <td class="fw-semibold">${Number(r.longitude).toFixed(6)}</td>
                        <td>${r.distance_from_center_meters !== null ? Number(r.distance_from_center_meters).toFixed(1) + ' m' : '--'}</td>
                        <td>${boundaryBadge}</td>
                        <td>${transitionBadge}</td>
                    </tr>
                `;
            }).join('');
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-3">Failed to load history from backend: ${err.message}</td></tr>`;
        }
    };

    const initAdminAuth = () => {
        const modalEl = document.getElementById('adminLoginModal');
        if (modalEl) {
            adminModal = new bootstrap.Modal(modalEl);
        }

        const btnOpenLogin = document.getElementById('btnAdminLogin');
        if (btnOpenLogin) {
            btnOpenLogin.addEventListener('click', () => openAdminLoginModal());
        }

        const btnLogout = document.getElementById('btnAdminLogout');
        if (btnLogout) {
            btnLogout.addEventListener('click', async () => {
                try {
                    await TrackerAPI.logoutAdmin();
                    updateAdminAuthUi(false, null);
                } catch (err) {
                    console.error('Logout error:', err);
                }
            });
        }

        // Form 1: Username & Password Login
        const userForm = document.getElementById('adminUserLoginForm');
        if (userForm) {
            userForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const username = document.getElementById('inputAdminUsername').value.trim();
                const password = document.getElementById('inputAdminPassword').value;
                const btnSubmit = document.getElementById('btnSubmitUserLogin');
                btnSubmit.disabled = true;
                btnSubmit.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Verifying...';

                try {
                    const res = await TrackerAPI.loginAdmin({ username, password });
                    updateAdminAuthUi(true, res.user);
                    if (adminModal) adminModal.hide();
                } catch (err) {
                    showAdminLoginAlert(err.message, 'danger');
                } finally {
                    btnSubmit.disabled = false;
                    btnSubmit.innerHTML = '<i class="bi bi-box-arrow-in-right me-1"></i> Sign In as Operator';
                }
            });
        }

        // Initial check of auth status
        checkAdminAuthStatus();
    };

    const checkAdminAuthStatus = async () => {
        try {
            const data = await TrackerAPI.getAuthStatus();
            updateAdminAuthUi(data.is_authenticated, data.user);
        } catch (err) {
            updateAdminAuthUi(false, null);
        }
    };

    const updateAdminAuthUi = (isAuthenticated, username) => {
        const btnLogin = document.getElementById('btnAdminLogin');
        const userBadge = document.getElementById('adminAuthUserBadge');
        const userDisplay = document.getElementById('adminUsernameDisplay');

        if (isAuthenticated) {
            if (btnLogin) btnLogin.classList.add('d-none');
            if (userBadge) {
                userBadge.classList.remove('d-none');
                userBadge.classList.add('d-flex');
            }
            if (userDisplay) userDisplay.innerHTML = `<i class="bi bi-shield-check me-1"></i> Admin: ${username || 'Operator'}`;
        } else {
            if (btnLogin) btnLogin.classList.remove('d-none');
            if (userBadge) {
                userBadge.classList.remove('d-flex');
                userBadge.classList.add('d-none');
            }
        }
    };

    const openAdminLoginModal = (message = null) => {
        if (message) {
            showAdminLoginAlert(message, 'warning');
        } else {
            showAdminLoginAlert('Administrative authorization required for modifying geofence boundaries and resolving emergency SOS alerts.', 'info');
        }
        if (adminModal) adminModal.show();
    };

    const showAdminLoginAlert = (message, type = 'info') => {
        const alertEl = document.getElementById('adminLoginAlert');
        if (alertEl) {
            alertEl.className = `alert alert-${type} py-2 small mb-3`;
            alertEl.textContent = message;
        }
    };

    return {
        init,
        pollStatus,
        refreshHistory,
        openAdminLoginModal,
        getActiveDeviceId: () => activeDeviceId,
        getCurrentStatus: () => currentStatus,
        getCurrentMode: () => currentMode
    };
})();

// Initialize upon DOM readiness
document.addEventListener('DOMContentLoaded', () => {
    DashboardController.init();
});
