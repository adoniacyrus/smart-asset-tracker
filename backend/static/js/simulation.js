/**
 * simulation.js - Dynamic & Static Location Simulation Engine
 * Generates realistic route coordinates, manages start/pause/resume/reset lifecycles,
 * handles map-click static coordinate selection, and submits telemetry strictly
 * tagged as DYNAMIC_SIMULATION or STATIC_SIMULATION to Django APIs.
 */

const SimulationController = (() => {
    let simState = 'IDLE'; // 'IDLE' | 'RUNNING' | 'PAUSED'
    let routePoints = [];
    let currentStepIndex = 0;
    let simTimer = null;
    let isRequestPending = false;
    let stepIntervalMs = 1400;

    // --------------------------------------------------------------------------
    // Route Generation (Anchor to Active Geofence)
    // --------------------------------------------------------------------------
    const generateRoute = (centerLat, centerLon, radiusMeters) => {
        // Approximate degrees per meter
        const latDegPerMeter = 1.0 / 111000.0;
        const lonDegPerMeter = 1.0 / (111000.0 * Math.cos((centerLat * Math.PI) / 180.0));

        const r = radiusMeters || 500.0;

        // Key demonstration waypoints:
        // Waypoint 0: Inside center (distance ~50m)
        // Waypoint 1: Moving East inside (distance ~250m)
        // Waypoint 2: Crossing boundary East (distance ~700m > radius) -> EXIT
        // Waypoint 3: North-East outside (distance ~950m > radius)
        // Waypoint 4: North-West outside (distance ~800m > radius)
        // Waypoint 5: Re-entering from West (distance ~300m < radius) -> ENTRY
        // Waypoint 6: Returning to start inside (distance ~50m < radius)
        const waypoints = [
            { dLat: 0.05 * r * latDegPerMeter, dLon: 0.1 * r * lonDegPerMeter, label: 'Start (Inside)' },
            { dLat: 0.25 * r * latDegPerMeter, dLon: 0.5 * r * lonDegPerMeter, label: 'Inside - Heading East' },
            { dLat: 0.35 * r * latDegPerMeter, dLon: 1.4 * r * lonDegPerMeter, label: 'Crossing Boundary (Exit)' },
            { dLat: 1.2 * r * latDegPerMeter,  dLon: 1.5 * r * lonDegPerMeter, label: 'Far Outside (North-East)' },
            { dLat: 1.5 * r * latDegPerMeter,  dLon: 0.2 * r * lonDegPerMeter, label: 'Outside - Turning West' },
            { dLat: 0.6 * r * latDegPerMeter,  dLon: -0.6 * r * lonDegPerMeter, label: 'Outside - Approaching Boundary' },
            { dLat: 0.2 * r * latDegPerMeter,  dLon: -0.3 * r * lonDegPerMeter, label: 'Re-entering Boundary (Entry)' },
            { dLat: 0.05 * r * latDegPerMeter, dLon: 0.1 * r * lonDegPerMeter, label: 'Inside - Route Completed' },
        ];

        // Smoothly interpolate between waypoints (approx 24-28 steps total)
        const interpolated = [];
        const stepsPerSegment = 3;

        for (let i = 0; i < waypoints.length - 1; i++) {
            const start = waypoints[i];
            const end = waypoints[i + 1];

            for (let s = 0; s < stepsPerSegment; s++) {
                const fraction = s / stepsPerSegment;
                const dLat = start.dLat + (end.dLat - start.dLat) * fraction;
                const dLon = start.dLon + (end.dLon - start.dLon) * fraction;

                interpolated.push({
                    lat: Number((centerLat + dLat).toFixed(6)),
                    lon: Number((centerLon + dLon).toFixed(6)),
                    segment: i
                });
            }
        }

        // Add final waypoint
        const last = waypoints[waypoints.length - 1];
        interpolated.push({
            lat: Number((centerLat + last.dLat).toFixed(6)),
            lon: Number((centerLon + last.dLon).toFixed(6)),
            segment: waypoints.length - 1
        });

        return interpolated;
    };

    // --------------------------------------------------------------------------
    // Dynamic Simulation Controls
    // --------------------------------------------------------------------------
    const startSimulation = () => {
        if (simState === 'RUNNING') return;

        const status = DashboardController.getCurrentStatus();
        const gf = status ? status.geofence : null;
        const centerLat = gf ? gf.center_latitude : 12.9716;
        const centerLon = gf ? gf.center_longitude : 77.5946;
        const radius = gf ? gf.radius_meters : 500.0;

        // Generate or retain route
        if (routePoints.length === 0 || currentStepIndex >= routePoints.length) {
            routePoints = generateRoute(centerLat, centerLon, radius);
            currentStepIndex = 0;
        }

        simState = 'RUNNING';
        updateDynamicUiButtons();
        executeNextStep();
        simTimer = setInterval(executeNextStep, stepIntervalMs);
    };

    const pauseSimulation = () => {
        if (simState !== 'RUNNING') return;
        simState = 'PAUSED';
        if (simTimer) {
            clearInterval(simTimer);
            simTimer = null;
        }
        updateDynamicUiButtons();
    };

    const resumeSimulation = () => {
        if (simState !== 'PAUSED') return;
        simState = 'RUNNING';
        updateDynamicUiButtons();
        executeNextStep();
        simTimer = setInterval(executeNextStep, stepIntervalMs);
    };

    const resetSimulation = () => {
        pauseSimulation();
        simState = 'IDLE';
        currentStepIndex = 0;
        isRequestPending = false;

        const progressBar = document.getElementById('simProgressBar');
        const stepText = document.getElementById('simStepText');
        const eventBadge = document.getElementById('simTransitionEventBadge');

        if (progressBar) progressBar.style.width = '0%';
        if (stepText) stepText.textContent = `Step 0 / ${routePoints.length || 24}`;
        if (eventBadge) {
            eventBadge.textContent = 'Awaiting Start';
            eventBadge.className = 'badge bg-secondary';
        }
        updateDynamicUiButtons();
    };

    const executeNextStep = async () => {
        // Prevent overlapping requests
        if (isRequestPending) return;
        if (currentStepIndex >= routePoints.length) {
            // End of route reached
            resetSimulation();
            return;
        }

        const point = routePoints[currentStepIndex];
        const deviceId = DashboardController.getActiveDeviceId();

        isRequestPending = true;

        const payload = {
            device_id: deviceId,
            latitude: point.lat,
            longitude: point.lon,
            source_type: 'DYNAMIC_SIMULATION',
            speed: 36.5,
            satellites_count: 0
        };

        try {
            // Send strictly tagged as DYNAMIC_SIMULATION
            const response = await TrackerAPI.sendLocation(payload);

            // Update Leaflet marker smoothly
            TrackerMap.updateAsset(point.lat, point.lon, 'DYNAMIC_SIMULATION');

            // Update Progress Bar
            const total = routePoints.length;
            const pct = Math.round(((currentStepIndex + 1) / total) * 100);
            const progressBar = document.getElementById('simProgressBar');
            const stepText = document.getElementById('simStepText');
            const eventBadge = document.getElementById('simTransitionEventBadge');

            if (progressBar) progressBar.style.width = `${pct}%`;
            if (stepText) stepText.textContent = `Step ${currentStepIndex + 1} / ${total}`;

            // Authoritative transition display from Django
            if (eventBadge) {
                if (response.geofence_transition === 'EXIT') {
                    eventBadge.textContent = 'BOUNDARY EXITED!';
                    eventBadge.className = 'badge bg-danger fw-bold';
                } else if (response.geofence_transition === 'ENTRY') {
                    eventBadge.textContent = 'BOUNDARY RE-ENTERED!';
                    eventBadge.className = 'badge bg-success fw-bold';
                } else {
                    eventBadge.textContent = response.is_inside_geofence ? 'Inside Geofence' : 'Outside Geofence';
                    eventBadge.className = `badge ${response.is_inside_geofence ? 'bg-success-subtle text-success' : 'bg-warning-subtle text-warning'}`;
                }
            }

            // Sync overview cards and history table
            DashboardController.pollStatus();
            DashboardController.refreshHistory();

            currentStepIndex++;
        } catch (error) {
            console.error('Dynamic simulation step error:', error.message);
        } finally {
            isRequestPending = false;
        }
    };

    const updateDynamicUiButtons = () => {
        const btnStart = document.getElementById('btnSimStart');
        const btnPause = document.getElementById('btnSimPause');
        const btnResume = document.getElementById('btnSimResume');
        const statusBadge = document.getElementById('simStatusBadge');

        if (!btnStart || !btnPause || !btnResume || !statusBadge) return;

        if (simState === 'RUNNING') {
            btnStart.classList.add('d-none');
            btnResume.classList.add('d-none');
            btnPause.classList.remove('d-none');
            btnPause.disabled = false;
            statusBadge.textContent = 'RUNNING';
            statusBadge.className = 'badge bg-success-subtle text-success border border-success';
        } else if (simState === 'PAUSED') {
            btnStart.classList.add('d-none');
            btnPause.classList.add('d-none');
            btnResume.classList.remove('d-none');
            statusBadge.textContent = 'PAUSED';
            statusBadge.className = 'badge bg-warning-subtle text-warning border border-warning';
        } else {
            // IDLE
            btnStart.classList.remove('d-none');
            btnResume.classList.add('d-none');
            btnPause.classList.remove('d-none');
            btnPause.disabled = true;
            statusBadge.textContent = 'STOPPED';
            statusBadge.className = 'badge bg-secondary-subtle text-secondary border border-secondary';
        }
    };

    // --------------------------------------------------------------------------
    // Static Simulation Controls
    // --------------------------------------------------------------------------
    const setStaticCoordinates = (lat, lon) => {
        const inputLat = document.getElementById('inputStaticLat');
        const inputLon = document.getElementById('inputStaticLon');
        if (inputLat) inputLat.value = Number(lat).toFixed(6);
        if (inputLon) inputLon.value = Number(lon).toFixed(6);
    };

    const submitStaticPosition = async () => {
        const inputLat = document.getElementById('inputStaticLat');
        const inputLon = document.getElementById('inputStaticLon');
        const feedback = document.getElementById('staticFeedbackAlert');
        const btnSubmit = document.getElementById('btnSubmitStaticLocation');

        const lat = parseFloat(inputLat.value);
        const lon = parseFloat(inputLon.value);

        if (isNaN(lat) || isNaN(lon)) {
            alert('Please enter or click valid numeric coordinates on the map.');
            return;
        }

        btnSubmit.disabled = true;
        btnSubmit.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Ingesting...';

        const deviceId = DashboardController.getActiveDeviceId();
        const payload = {
            device_id: deviceId,
            latitude: lat,
            longitude: lon,
            source_type: 'STATIC_SIMULATION',
            satellites_count: 0
        };

        try {
            const result = await TrackerAPI.sendLocation(payload);

            // Update Leaflet map with STATIC_SIMULATION styling
            TrackerMap.updateAsset(lat, lon, 'STATIC_SIMULATION');

            // Provide immediate feedback badge
            if (feedback) {
                feedback.classList.remove('d-none', 'alert-danger', 'alert-success', 'alert-warning');
                const transText = result.geofence_transition !== 'NONE'
                    ? ` (Transition: <strong>${result.geofence_transition}</strong>)`
                    : '';
                const boundaryText = result.is_inside_geofence ? 'INSIDE' : 'OUTSIDE';
                const alertClass = result.is_inside_geofence ? 'alert-success' : 'alert-danger';

                feedback.className = `small mt-2 p-2 rounded-2 text-center alert ${alertClass}`;
                feedback.innerHTML = `Position Ingested &bull; Distance: <strong>${result.distance_from_center_meters}m</strong> &bull; State: <strong>${boundaryText}</strong>${transText}`;
            }

            // Sync overview cards and history
            DashboardController.pollStatus();
            DashboardController.refreshHistory();
        } catch (err) {
            if (feedback) {
                feedback.className = 'small mt-2 p-2 rounded-2 text-center alert alert-danger';
                feedback.textContent = `Ingestion error: ${err.message}`;
                feedback.classList.remove('d-none');
            }
        } finally {
            btnSubmit.disabled = false;
            btnSubmit.innerHTML = '<i class="bi bi-send-check me-1"></i> Ingest Static Position';
        }
    };

    // --------------------------------------------------------------------------
    // Initialization & Mode Event Bindings
    // --------------------------------------------------------------------------
    const init = () => {
        // Dynamic Controls
        const btnStart = document.getElementById('btnSimStart');
        const btnPause = document.getElementById('btnSimPause');
        const btnResume = document.getElementById('btnSimResume');
        const btnReset = document.getElementById('btnSimReset');
        const speedSlider = document.getElementById('simSpeedSlider');
        const speedLabel = document.getElementById('simSpeedLabel');

        if (btnStart) btnStart.addEventListener('click', startSimulation);
        if (btnPause) btnPause.addEventListener('click', pauseSimulation);
        if (btnResume) btnResume.addEventListener('click', resumeSimulation);
        if (btnReset) btnReset.addEventListener('click', resetSimulation);

        if (speedSlider && speedLabel) {
            speedSlider.addEventListener('input', (e) => {
                stepIntervalMs = parseInt(e.target.value, 10);
                speedLabel.textContent = `${(stepIntervalMs / 1000).toFixed(1)}s / step`;
                if (simState === 'RUNNING') {
                    // Update timer frequency smoothly
                    clearInterval(simTimer);
                    simTimer = setInterval(executeNextStep, stepIntervalMs);
                }
            });
        }

        // Static Controls
        const btnSubmitStatic = document.getElementById('btnSubmitStaticLocation');
        const btnPresetIn = document.getElementById('btnPresetInside');
        const btnPresetOut = document.getElementById('btnPresetOutside');

        if (btnSubmitStatic) btnSubmitStatic.addEventListener('click', submitStaticPosition);

        // Map Click Listener (when in STATIC_SIMULATION mode)
        TrackerMap.onMapClick((e) => {
            const currentMode = DashboardController.getCurrentMode();
            if (currentMode === 'STATIC_SIMULATION') {
                const lat = Number(e.latlng.lat).toFixed(6);
                const lon = Number(e.latlng.lng).toFixed(6);
                setStaticCoordinates(lat, lon);
            }
        });

        // Preset Inside Geofence (e.g. 50 meters from center)
        if (btnPresetIn) {
            btnPresetIn.addEventListener('click', () => {
                const status = DashboardController.getCurrentStatus();
                const gf = status ? status.geofence : null;
                const cLat = gf ? gf.center_latitude : 12.9716;
                const cLon = gf ? gf.center_longitude : 77.5946;
                // Offset inside (~50m)
                setStaticCoordinates(Number(cLat + 0.0003).toFixed(6), Number(cLon + 0.0003).toFixed(6));
            });
        }

        // Preset Outside Geofence (e.g. 1200 meters from center)
        if (btnPresetOut) {
            btnPresetOut.addEventListener('click', () => {
                const status = DashboardController.getCurrentStatus();
                const gf = status ? status.geofence : null;
                const cLat = gf ? gf.center_latitude : 12.9716;
                const cLon = gf ? gf.center_longitude : 77.5946;
                // Offset outside (~1200m)
                setStaticCoordinates(Number(cLat + 0.0100).toFixed(6), Number(cLon + 0.0100).toFixed(6));
            });
        }
    };

    // Mode switch handler: called when user clicks Real GPS, Dynamic Sim, or Static Sim
    const handleModeChange = (newMode) => {
        // ALWAYS pause and reset dynamic simulation when switching modes!
        if (simState === 'RUNNING' || simState === 'PAUSED') {
            resetSimulation();
        }

        const panelReal = document.getElementById('panelRealGps');
        const panelDynamic = document.getElementById('panelDynamicSim');
        const panelStatic = document.getElementById('panelStaticSim');

        if (panelReal) panelReal.classList.add('d-none');
        if (panelDynamic) panelDynamic.classList.add('d-none');
        if (panelStatic) panelStatic.classList.add('d-none');

        if (newMode === 'REAL_GPS') {
            if (panelReal) panelReal.classList.remove('d-none');
        } else if (newMode === 'DYNAMIC_SIMULATION') {
            if (panelDynamic) panelDynamic.classList.remove('d-none');
        } else if (newMode === 'STATIC_SIMULATION') {
            if (panelStatic) panelStatic.classList.remove('d-none');
        }
    };

    return {
        init,
        startSimulation,
        pauseSimulation,
        resumeSimulation,
        resetSimulation,
        submitStaticPosition,
        handleModeChange
    };
})();

