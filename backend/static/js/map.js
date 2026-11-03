/**
 * map.js - Leaflet.js Map Controller
 * Reliable, zero-cost tile provider architecture for academic IoT demonstration.
 * Primary Layer: Esri World Street Map (high-definition, permitted for development & academic evaluation, zero API keys).
 * Alternate Layers: OpenStreetMap (France), Esri World Topographic, Esri World Imagery (Satellite).
 * Includes graceful tile error handling and layer switching.
 */

const TrackerMap = (() => {
    let map = null;
    let assetMarker = null;
    let geofenceCircle = null;
    let geofenceCenterMarker = null;

    // Custom HTML marker generators
    const createAssetIcon = (sourceType = 'REAL_GPS') => {
        const isReal = sourceType === 'REAL_GPS';
        const cssClass = isReal ? 'real-gps' : 'simulation';

        const html = `
            <div class="custom-asset-marker">
                <div class="pulsing-beacon ${cssClass}"></div>
                <div class="marker-pin ${cssClass}"></div>
            </div>
        `;

        return L.divIcon({
            html,
            className: 'custom-leaflet-marker',
            iconSize: [36, 36],
            iconAnchor: [18, 18],
            popupAnchor: [0, -18]
        });
    };

    const createCenterIcon = () => {
        const html = `
            <div class="custom-asset-marker">
                <div class="marker-pin center"></div>
            </div>
        `;
        return L.divIcon({
            html,
            className: 'custom-center-marker',
            iconSize: [24, 24],
            iconAnchor: [12, 12],
            popupAnchor: [0, -12]
        });
    };

    const init = (lat = 12.9716, lon = 77.5946, zoom = 15) => {
        if (map) return map;

        // Initialize Leaflet Map
        map = L.map('leafletMap', {
            zoomControl: true,
            attributionControl: true
        }).setView([lat, lon], zoom);

        // 1. Primary Layer: Esri World Street Map
        // Highly reliable, zero 403 blocks, permitted for non-commercial & academic evaluation, no API key required
        const esriStreet = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
            maxZoom: 19,
            updateWhenIdle: true,
            keepBuffer: 2,
            attribution: 'Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ, USGS, TomTom'
        });

        // 2. OpenStreetMap France Layer (Direct OSM rendering, permitted for academic usage)
        const osmFrance = L.tileLayer('https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png', {
            maxZoom: 20,
            subdomains: ['a', 'b', 'c'],
            updateWhenIdle: true,
            keepBuffer: 2,
            attribution: '&copy; OpenStreetMap France | &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>'
        });

        // 3. Esri World Topographic Map
        const esriTopo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {
            maxZoom: 19,
            updateWhenIdle: true,
            keepBuffer: 2,
            attribution: 'Tiles &copy; Esri &mdash; Source: Esri, FAO, NOAA, USGS'
        });

        // 4. Esri World Imagery (High-Resolution Real Satellite View)
        const esriSatellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            maxZoom: 19,
            updateWhenIdle: true,
            keepBuffer: 2,
            attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, GeoEye'
        });

        // Graceful tile loading error handler
        [esriStreet, osmFrance, esriTopo, esriSatellite].forEach(layer => {
            layer.on('tileerror', (error) => {
                console.warn('Map tile failed to load gracefully:', error.coords);
            });
        });

        // Add default primary layer to map
        esriStreet.addTo(map);

        // Add Layer Switcher Control in top-right corner
        const baseMaps = {
            "World Street Map (Esri)": esriStreet,
            "OpenStreetMap (France)": osmFrance,
            "Topographic (Esri)": esriTopo,
            "Satellite Imagery (Esri)": esriSatellite
        };
        L.control.layers(baseMaps, null, { position: 'topright' }).addTo(map);

        return map;
    };

    const updateAsset = (lat, lon, sourceType = 'REAL_GPS', popupInfo = null) => {
        if (!map) return;
        if (lat === null || lon === null || isNaN(lat) || isNaN(lon)) {
            clearAssetMarker();
            showNoLocationNotice('NO GPS FIX: Waiting for ESP32 satellite lock or valid telemetry');
            return;
        }

        hideNoLocationNotice();

        const latLng = [lat, lon];
        const icon = createAssetIcon(sourceType);

        if (!assetMarker) {
            assetMarker = L.marker(latLng, { icon, zIndexOffset: 1000 }).addTo(map);
        } else {
            assetMarker.setLatLng(latLng);
            assetMarker.setIcon(icon);
        }

        const isReal = sourceType === 'REAL_GPS';
        const badgeClass = isReal ? 'bg-success' : 'bg-warning text-dark';
        const infoHtml = popupInfo || `
            <div style="font-size: 0.8rem; line-height: 1.4;">
                <strong class="text-primary"><i class="bi bi-geo-alt-fill me-1"></i> Asset Telemetry</strong><br>
                Source: <span class="badge ${badgeClass}">${sourceType}</span><br>
                Lat: ${Number(lat).toFixed(6)}<br>
                Lon: ${Number(lon).toFixed(6)}
            </div>
        `;
        assetMarker.bindPopup(infoHtml);
    };

    const clearAssetMarker = () => {
        if (assetMarker && map) {
            map.removeLayer(assetMarker);
            assetMarker = null;
        }
    };

    const showNoLocationNotice = (message = 'NO GPS FIX: Awaiting Satellite Lock or Live Telemetry') => {
        const noticeEl = document.getElementById('mapNoLocationNotice');
        if (noticeEl) {
            noticeEl.innerHTML = `<i class="bi bi-geo-alt-slash-fill me-2 text-warning fs-5"></i><span>${message}</span>`;
            noticeEl.classList.remove('d-none');
        }
    };

    const hideNoLocationNotice = () => {
        const noticeEl = document.getElementById('mapNoLocationNotice');
        if (noticeEl) {
            noticeEl.classList.add('d-none');
        }
    };

    const updateGeofence = (centerLat, centerLon, radiusMeters, isActive = true) => {
        if (!map) return;
        if (!centerLat || !centerLon || isNaN(centerLat) || isNaN(centerLon)) return;

        const centerLatLng = [centerLat, centerLon];

        // 1. Center Marker
        const centerIcon = createCenterIcon();
        if (!geofenceCenterMarker) {
            geofenceCenterMarker = L.marker(centerLatLng, { icon: centerIcon }).addTo(map);
            geofenceCenterMarker.bindPopup(`
                <div style="font-size: 0.75rem;">
                    <strong>Geofence Center</strong><br>
                    Radius: ${radiusMeters}m
                </div>
            `);
        } else {
            geofenceCenterMarker.setLatLng(centerLatLng);
        }

        // 2. Circle Boundary
        const circleStyle = {
            color: isActive ? '#2563eb' : '#64748b',
            fillColor: isActive ? '#2563eb' : '#64748b',
            fillOpacity: isActive ? 0.12 : 0.05,
            weight: 2,
            dashArray: isActive ? null : '6, 6'
        };

        if (!geofenceCircle) {
            geofenceCircle = L.circle(centerLatLng, {
                radius: radiusMeters,
                ...circleStyle
            }).addTo(map);
        } else {
            geofenceCircle.setLatLng(centerLatLng);
            geofenceCircle.setRadius(radiusMeters);
            geofenceCircle.setStyle(circleStyle);
        }
    };

    const centerOnAsset = () => {
        if (assetMarker && map) {
            map.panTo(assetMarker.getLatLng());
        } else if (geofenceCircle && map) {
            map.panTo(geofenceCircle.getLatLng());
        }
    };

    const fitGeofence = () => {
        if (geofenceCircle && map) {
            map.fitBounds(geofenceCircle.getBounds(), { padding: [40, 40] });
        }
    };

    const onMapClick = (callback) => {
        if (map) {
            map.on('click', callback);
        }
    };

    return {
        init,
        updateAsset,
        clearAssetMarker,
        showNoLocationNotice,
        hideNoLocationNotice,
        updateGeofence,
        centerOnAsset,
        fitGeofence,
        onMapClick,
        getMap: () => map
    };
})();
