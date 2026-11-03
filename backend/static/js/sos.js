/**
 * sos.js - Emergency SOS Alert Controller
 * Handles active SOS alert notification banners, emergency audio/visual cues,
 * simulation triggers, audit log tables, and operator acknowledgment/resolution modal workflows.
 */

const SOSController = (() => {
    let activeAlert = null;
    let resolveModal = null;

    const init = () => {
        const modalEl = document.getElementById('resolveSosModal');
        if (modalEl) {
            resolveModal = new bootstrap.Modal(modalEl);
        }

        const btnBannerResolve = document.getElementById('btnResolveSosFromBanner');
        if (btnBannerResolve) {
            btnBannerResolve.addEventListener('click', () => {
                if (activeAlert) {
                    openResolveModal(activeAlert.id);
                }
            });
        }

        const resolveForm = document.getElementById('resolveSosForm');
        if (resolveForm) {
            resolveForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const sosId = document.getElementById('modalSosId').value;
                const resolvedBy = document.getElementById('modalResolvedBy').value;
                const notes = document.getElementById('modalSosNotes').value;

                try {
                    await TrackerAPI.resolveSos(sosId, {
                        resolved_by: resolvedBy,
                        notes: notes
                    });
                    if (resolveModal) resolveModal.hide();
                    hideAlertBanner();
                    await DashboardController.pollStatus();
                    await refreshSosHistory();
                } catch (err) {
                    if (err.message.includes('Forbidden') || err.message.includes('Unauthorized') || err.message.includes('403')) {
                        if (resolveModal) resolveModal.hide();
                        DashboardController.openAdminLoginModal('Administrative authorization required to resolve emergency SOS alerts. Please sign in as an Operator.');
                    } else {
                        alert(`Failed to resolve SOS: ${err.message}`);
                    }
                }
            });
        }

        // Dashboard SOS Simulation Triggers
        const triggerHandler = async () => {
            if (!confirm("Simulate an emergency SOS event from the console?")) return;

            const deviceId = DashboardController.getActiveDeviceId();
            const status = DashboardController.getCurrentStatus();

            const payload = {
                device_id: deviceId,
                trigger_source: 'DASHBOARD_SIMULATION',
                has_gps_fix: Boolean(status && status.last_known_latitude),
                latitude: status ? status.last_known_latitude : null,
                longitude: status ? status.last_known_longitude : null,
                location_source: status ? status.last_known_source : null
            };

            try {
                await TrackerAPI.triggerSos(payload);
                await DashboardController.pollStatus();
                await refreshSosHistory();
            } catch (err) {
                alert(`SOS trigger error: ${err.message}`);
            }
        };

        const btnSimulateSos = document.getElementById('btnTriggerDashboardSos');
        if (btnSimulateSos) {
            btnSimulateSos.addEventListener('click', triggerHandler);
        }

        const btnQuickSos = document.getElementById('btnQuickSosTrigger');
        if (btnQuickSos) {
            btnQuickSos.addEventListener('click', triggerHandler);
        }

        const btnRefreshSos = document.getElementById('btnRefreshSosHistory');
        if (btnRefreshSos) {
            btnRefreshSos.addEventListener('click', () => refreshSosHistory());
        }

        // Initial fetch of SOS history table
        refreshSosHistory();
    };

    const updateAlertState = (hasActiveSos, alertDetails) => {
        const banner = document.getElementById('sosAlertBanner');
        const sidebarBadge = document.getElementById('sidebarSosBadge');
        const detailsSpan = document.getElementById('sosBannerDetails');

        if (hasActiveSos && alertDetails) {
            activeAlert = alertDetails;
            if (banner) banner.classList.remove('d-none');
            if (sidebarBadge) sidebarBadge.classList.remove('d-none');

            if (detailsSpan) {
                const fixText = alertDetails.has_gps_fix
                    ? `Location: (${Number(alertDetails.latitude).toFixed(4)}, ${Number(alertDetails.longitude).toFixed(4)})`
                    : `Location: Coordinates Unavailable (NEO-6M Missing Antenna / No Lock)`;
                detailsSpan.innerHTML = `<strong>Source:</strong> ${alertDetails.trigger_source} &bull; ${fixText}`;
            }
        } else {
            hideAlertBanner();
        }
    };

    const hideAlertBanner = () => {
        activeAlert = null;
        const banner = document.getElementById('sosAlertBanner');
        const sidebarBadge = document.getElementById('sidebarSosBadge');
        if (banner) banner.classList.add('d-none');
        if (sidebarBadge) sidebarBadge.classList.add('d-none');
    };

    const openResolveModal = (sosId) => {
        const idInput = document.getElementById('modalSosId');
        if (idInput) idInput.value = sosId;
        if (resolveModal) resolveModal.show();
    };

    const renderSosTable = (records) => {
        const tbody = document.getElementById('sosHistoryTableBody');
        if (!tbody) return;

        if (!records || records.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-secondary py-3">No SOS emergency events recorded.</td></tr>';
            return;
        }

        tbody.innerHTML = records.map(r => {
            const timeStr = new Date(r.timestamp).toLocaleTimeString();
            const dateStr = new Date(r.timestamp).toLocaleDateString();
            const isResolved = r.status === 'RESOLVED';

            const statusBadge = isResolved
                ? '<span class="badge bg-success-subtle text-success border border-success"><i class="bi bi-shield-check me-1"></i> RESOLVED</span>'
                : '<span class="badge bg-danger animate-pulse"><i class="bi bi-exclamation-triangle-fill me-1"></i> ACTIVE ALARM</span>';

            const sourceBadge = r.trigger_source === 'HARDWARE_BUTTON'
                ? '<span class="badge bg-danger-subtle text-danger border border-danger">PHYSICAL BUTTON (GPIO18)</span>'
                : `<span class="badge bg-secondary">${r.trigger_source}</span>`;

            const gpsBadge = r.has_gps_fix
                ? '<span class="badge bg-success">FIX ACQUIRED</span>'
                : '<span class="badge bg-warning text-dark">NO GPS FIX</span>';

            const coordsDisplay = (r.latitude !== null && r.longitude !== null)
                ? `${Number(r.latitude).toFixed(5)}, ${Number(r.longitude).toFixed(5)}`
                : '<span class="text-secondary">Antenna Idle / No Fix</span>';

            const resolvedInfo = isResolved
                ? `<div class="small fw-semibold text-success">${r.resolved_by || 'Operator'}</div><div class="text-secondary" style="font-size: 0.68rem;">${r.notes || 'Incident cleared'}</div>`
                : '<span class="text-danger fw-bold">PENDING RESOLUTION</span>';

            const actionBtn = isResolved
                ? `<span class="text-secondary small"><i class="bi bi-check2-circle text-success"></i> Cleared</span>`
                : `<button class="btn btn-xs btn-outline-danger py-0 px-2 fw-semibold" onclick="SOSController.openResolveModal(${r.id})"><i class="bi bi-check-circle me-1"></i> Resolve</button>`;

            return `
                <tr>
                    <td class="fw-bold">#SOS-${String(r.id).padStart(4, '0')}</td>
                    <td class="text-secondary">${dateStr} ${timeStr}</td>
                    <td class="fw-semibold">${r.device_id || 'ASSET-ESP32-01'}</td>
                    <td>${sourceBadge}</td>
                    <td>${gpsBadge}</td>
                    <td>${coordsDisplay}</td>
                    <td>${statusBadge}</td>
                    <td>${resolvedInfo}</td>
                    <td>${actionBtn}</td>
                </tr>
            `;
        }).join('');
    };

    const refreshSosHistory = async () => {
        const tbody = document.getElementById('sosHistoryTableBody');
        if (!tbody) return;

        try {
            const records = await TrackerAPI.getSosHistory(DashboardController.getActiveDeviceId());
            renderSosTable(records);
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center text-danger py-3">Failed to load SOS logs from backend: ${err.message}</td></tr>`;
        }
    };

    return {
        init,
        updateAlertState,
        hideAlertBanner,
        openResolveModal,
        refreshSosHistory,
        renderSosTable
    };
})();
