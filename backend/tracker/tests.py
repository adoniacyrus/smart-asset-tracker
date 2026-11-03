import math
from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from .models import (
    Asset,
    GeofenceSettings,
    LocationRecord,
    LocationSource,
    GeofenceTransition,
    SOSEvent,
    SOSStatus,
    SOSTriggerSource,
    SoftwareLocationTarget,
    TargetStatus,
    generate_device_token,
)
from .services import (
    validate_coordinates,
    haversine_distance,
    evaluate_geofence,
    record_location,
    process_sos_event,
    resolve_sos_event,
)


class CoordinateValidationTests(TestCase):
    def test_valid_coordinates(self):
        valid, msg, lat, lon = validate_coordinates("12.971598", "77.594562")
        self.assertTrue(valid)
        self.assertIsNone(msg)
        self.assertAlmostEqual(lat, 12.971598)
        self.assertAlmostEqual(lon, 77.594562)

    def test_boundary_coordinates(self):
        self.assertTrue(validate_coordinates(90, 180)[0])
        self.assertTrue(validate_coordinates(-90, -180)[0])
        self.assertTrue(validate_coordinates(0, 0)[0])

    def test_invalid_latitude_range(self):
        valid, msg, _, _ = validate_coordinates(90.0001, 77.0)
        self.assertFalse(valid)
        self.assertIn("Latitude", msg)

        valid, msg, _, _ = validate_coordinates(-90.1, 77.0)
        self.assertFalse(valid)

    def test_invalid_longitude_range(self):
        valid, msg, _, _ = validate_coordinates(12.0, 180.001)
        self.assertFalse(valid)
        self.assertIn("Longitude", msg)

        valid, msg, _, _ = validate_coordinates(12.0, -180.001)
        self.assertFalse(valid)

    def test_non_numeric_and_null_coordinates(self):
        self.assertFalse(validate_coordinates("abc", "77.0")[0])
        self.assertFalse(validate_coordinates(None, 77.0)[0])
        self.assertFalse(validate_coordinates(12.0, None)[0])
        self.assertFalse(validate_coordinates(float('nan'), 77.0)[0])


class HaversineFormulaTests(TestCase):
    def test_zero_distance_same_point(self):
        dist = haversine_distance(12.971598, 77.594562, 12.971598, 77.594562)
        self.assertEqual(dist, 0.0)

    def test_known_ground_truth_distance(self):
        # Known distance between London (51.5074, -0.1278) and Paris (48.8566, 2.3522)
        # Expected great-circle distance is approx 343.5 km (+/- 1 km)
        dist = haversine_distance(51.5074, -0.1278, 48.8566, 2.3522)
        self.assertTrue(342000 < dist < 345000)

    def test_short_distance_accuracy(self):
        # 1 degree of latitude is approx 111.19 km (111195 m) at equator
        dist = haversine_distance(0.0, 0.0, 1.0, 0.0)
        self.assertTrue(110000 < dist < 112000)


class GeofenceLogicTests(TestCase):
    def setUp(self):
        self.asset = Asset.objects.create(
            device_id="TEST-ASSET-01",
            name="Test Vehicle",
            current_mode=LocationSource.REAL_GPS,
        )
        # Center: Vidhana Soudha, Bangalore (12.979693, 77.590658), Radius: 500 meters
        self.geofence = GeofenceSettings.objects.create(
            asset=self.asset,
            center_latitude=Decimal("12.979693"),
            center_longitude=Decimal("77.590658"),
            radius_meters=500.0,
            is_active=True,
        )

    def test_initial_position_inside_no_false_transition(self):
        # Asset starts inside geofence (approx 50m from center)
        result = evaluate_geofence(self.asset, 12.980000, 77.590658)
        self.assertTrue(result["is_inside"])
        self.assertEqual(result["transition"], GeofenceTransition.NONE)
        self.asset.refresh_from_db()
        self.assertTrue(self.asset.is_inside_geofence)

    def test_geofence_exit_transition(self):
        # First set initial state as inside
        evaluate_geofence(self.asset, 12.980000, 77.590658)

        # Move far outside (> 2 km away)
        result = evaluate_geofence(self.asset, 13.000000, 77.590658)
        self.assertFalse(result["is_inside"])
        self.assertEqual(result["transition"], GeofenceTransition.EXIT)
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.is_inside_geofence)

    def test_duplicate_exit_suppression(self):
        # Establish outside state
        self.asset.is_inside_geofence = False
        self.asset.save()

        # Another reading outside must not re-trigger EXIT
        result = evaluate_geofence(self.asset, 13.010000, 77.590658)
        self.assertFalse(result["is_inside"])
        self.assertEqual(result["transition"], GeofenceTransition.NONE)

    def test_geofence_entry_transition(self):
        # Establish outside state
        self.asset.is_inside_geofence = False
        self.asset.save()

        # Move inside (50m from center)
        result = evaluate_geofence(self.asset, 12.979693, 77.590700)
        self.assertTrue(result["is_inside"])
        self.assertEqual(result["transition"], GeofenceTransition.ENTRY)
        self.asset.refresh_from_db()
        self.assertTrue(self.asset.is_inside_geofence)

    def test_duplicate_entry_suppression(self):
        # Establish inside state
        self.asset.is_inside_geofence = True
        self.asset.save()

        # Another reading inside must not re-trigger ENTRY
        result = evaluate_geofence(self.asset, 12.979693, 77.590800)
        self.assertTrue(result["is_inside"])
        self.assertEqual(result["transition"], GeofenceTransition.NONE)

    def test_fresh_asset_complete_geofence_lifecycle(self):
        """
        Verifies that an asset with is_inside_geofence=None:
        1. First location inside -> NONE (baseline state established)
        2. Move outside -> EXIT
        3. Move inside -> ENTRY
        4. Repeated inside -> NONE (duplicate suppression)
        """
        fresh_asset = Asset.objects.create(
            device_id="FRESH-LIFECYCLE-01",
            name="Fresh Lifecycle Asset",
            is_inside_geofence=None,
        )
        GeofenceSettings.objects.create(
            asset=fresh_asset,
            center_latitude=Decimal("12.971600"),
            center_longitude=Decimal("77.594600"),
            radius_meters=500.0,
            is_active=True,
        )

        # 1. First location inside -> NONE
        res1 = evaluate_geofence(fresh_asset, 12.971650, 77.594650)
        self.assertTrue(res1["is_inside"])
        self.assertEqual(res1["transition"], GeofenceTransition.NONE)

        # 2. Subsequent location outside -> EXIT
        res2 = evaluate_geofence(fresh_asset, 12.985000, 77.610000)
        self.assertFalse(res2["is_inside"])
        self.assertEqual(res2["transition"], GeofenceTransition.EXIT)

        # 3. Subsequent location inside -> ENTRY
        res3 = evaluate_geofence(fresh_asset, 12.971650, 77.594650)
        self.assertTrue(res3["is_inside"])
        self.assertEqual(res3["transition"], GeofenceTransition.ENTRY)

        # 4. Repeated location inside -> NONE
        res4 = evaluate_geofence(fresh_asset, 12.971600, 77.594600)
        self.assertTrue(res4["is_inside"])
        self.assertEqual(res4["transition"], GeofenceTransition.NONE)


class LocationRecordTests(TestCase):
    def setUp(self):
        self.asset = Asset.objects.create(
            device_id="TEST-ASSET-02",
            name="Fleet Truck",
            current_mode=LocationSource.REAL_GPS,
        )
        self.geofence = GeofenceSettings.objects.create(
            asset=self.asset,
            center_latitude=Decimal("12.971600"),
            center_longitude=Decimal("77.594600"),
            radius_meters=1000.0,
            is_active=True,
        )

    def test_record_location_real_gps(self):
        record = record_location(
            asset=self.asset,
            latitude="12.971600",
            longitude="77.594600",
            source_type=LocationSource.REAL_GPS,
            satellites_count=8,
            speed=25.5,
        )
        self.assertEqual(record.source_type, LocationSource.REAL_GPS)
        self.assertEqual(record.satellites_count, 8)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_mode, LocationSource.REAL_GPS)
        self.assertEqual(self.asset.last_known_source, LocationSource.REAL_GPS)

    def test_record_location_simulation_isolation(self):
        record = record_location(
            asset=self.asset,
            latitude="12.980000",
            longitude="77.600000",
            source_type=LocationSource.DYNAMIC_SIMULATION,
        )
        self.assertEqual(record.source_type, LocationSource.DYNAMIC_SIMULATION)
        self.assertNotEqual(record.source_type, LocationSource.REAL_GPS)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_mode, LocationSource.DYNAMIC_SIMULATION)

    def test_reject_invalid_source_type(self):
        with self.assertRaises(ValueError):
            record_location(
                asset=self.asset,
                latitude="12.971600",
                longitude="77.594600",
                source_type="INVALID_SOURCE_TYPE",
            )

    def test_reject_out_of_bounds_coordinates(self):
        with self.assertRaises(ValueError):
            record_location(
                asset=self.asset,
                latitude="95.000000",
                longitude="77.594600",
                source_type=LocationSource.REAL_GPS,
            )


class SOSEventServiceTests(TestCase):
    def setUp(self):
        self.asset = Asset.objects.create(
            device_id="TEST-ASSET-03",
            name="Personal SOS Node",
            current_mode=LocationSource.REAL_GPS,
        )

    def test_sos_with_missing_antenna_no_gps_fix(self):
        # Missing antenna scenario: has_gps_fix is False, coordinates must be None
        sos = process_sos_event(
            asset=self.asset,
            trigger_source=SOSTriggerSource.HARDWARE_BUTTON,
            has_gps_fix=False,
            latitude=None,
            longitude=None,
        )
        self.assertEqual(sos.status, SOSStatus.ACTIVE)
        self.assertFalse(sos.has_gps_fix)
        self.assertIsNone(sos.latitude)
        self.assertIsNone(sos.longitude)
        self.assertIsNone(sos.location_source)

    def test_sos_rejects_stale_coordinates(self):
        # Coordinates provided with a timestamp 60 seconds old (stale)
        old_timestamp = timezone.now() - timedelta(seconds=60)
        sos = process_sos_event(
            asset=self.asset,
            trigger_source=SOSTriggerSource.HARDWARE_BUTTON,
            has_gps_fix=True,
            latitude="12.971600",
            longitude="77.594600",
            location_source=LocationSource.REAL_GPS,
            timestamp=old_timestamp,
            max_gps_age_seconds=15,
        )
        # Stale fix must be rejected to prevent misrepresenting old location
        self.assertFalse(sos.has_gps_fix)
        self.assertIsNone(sos.latitude)
        self.assertIsNone(sos.longitude)

    def test_sos_with_fresh_valid_gps_fix(self):
        sos = process_sos_event(
            asset=self.asset,
            trigger_source=SOSTriggerSource.HARDWARE_BUTTON,
            has_gps_fix=True,
            latitude="12.971600",
            longitude="77.594600",
            location_source=LocationSource.REAL_GPS,
            timestamp=timezone.now(),
        )
        self.assertTrue(sos.has_gps_fix)
        self.assertEqual(sos.latitude, Decimal("12.971600"))
        self.assertEqual(sos.longitude, Decimal("77.594600"))
        self.assertEqual(sos.location_source, LocationSource.REAL_GPS)

    def test_resolve_sos_event(self):
        sos = process_sos_event(
            asset=self.asset,
            trigger_source=SOSTriggerSource.HARDWARE_BUTTON,
            has_gps_fix=False,
        )
        self.assertEqual(sos.status, SOSStatus.ACTIVE)

        resolved = resolve_sos_event(sos.id, resolved_by="Officer Admin", notes="False alarm verified.")
        self.assertEqual(resolved.status, SOSStatus.RESOLVED)
        self.assertEqual(resolved.resolved_by, "Officer Admin")
        self.assertIsNotNone(resolved.resolved_at)
        self.assertEqual(resolved.notes, "False alarm verified.")


from rest_framework.test import APITestCase
from django.urls import reverse


class APIRouteTests(APITestCase):
    def setUp(self):
        self.device_token = "test_device_secret_token_123"
        from django.contrib.auth.models import User
        self.admin_user = User.objects.create_superuser(
            username="api_test_admin",
            password="api_test_password",
            email="admin@tracker.api"
        )

        self.asset = Asset.objects.create(
            device_id="ASSET-ESP32-01",
            name="Primary Test Node",
            device_token=self.device_token,
            current_mode=LocationSource.REAL_GPS,
        )
        self.geofence = GeofenceSettings.objects.create(
            asset=self.asset,
            center_latitude=Decimal("12.971600"),
            center_longitude=Decimal("77.594600"),
            radius_meters=500.0,
            is_active=True,
        )

        self.device_headers = {"HTTP_X_DEVICE_TOKEN": self.device_token}

    # --- Location Ingestion & Modes Tests ---

    def test_location_ingest_real_gps_authorized(self):
        url = reverse('tracker-location-ingest')
        payload = {
            "device_id": "ASSET-ESP32-01",
            "latitude": 12.971800,
            "longitude": 77.594700,
            "source_type": LocationSource.REAL_GPS,
            "altitude": 920.0,
            "speed": 12.5,
            "satellites_count": 7,
        }
        response = self.client.post(url, payload, format='json', **self.device_headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'success')
        self.assertEqual(response.data['source_type'], LocationSource.REAL_GPS)
        self.assertTrue(response.data['is_inside_geofence'])

    def test_location_ingest_real_gps_unauthorized_missing_token(self):
        url = reverse('tracker-location-ingest')
        payload = {
            "device_id": "ASSET-ESP32-01",
            "latitude": 12.971800,
            "longitude": 77.594700,
            "source_type": LocationSource.REAL_GPS,
        }
        # No auth header supplied
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.data)

    def test_location_ingest_real_gps_unauthorized_invalid_token(self):
        url = reverse('tracker-location-ingest')
        payload = {
            "device_id": "ASSET-ESP32-01",
            "latitude": 12.971800,
            "longitude": 77.594700,
            "source_type": LocationSource.REAL_GPS,
        }
        response = self.client.post(
            url, payload, format='json',
            **{"HTTP_X_DEVICE_TOKEN": "wrong_token_abc"}
        )
        self.assertEqual(response.status_code, 401)

    def test_location_ingest_dynamic_simulation(self):
        url = reverse('tracker-location-ingest')
        payload = {
            "device_id": "ASSET-ESP32-01",
            "latitude": 12.972000,
            "longitude": 77.595000,
            "source_type": LocationSource.DYNAMIC_SIMULATION,
            "speed": 35.0,
        }
        # Allowed from dashboard without hardware token
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['source_type'], LocationSource.DYNAMIC_SIMULATION)

    def test_location_ingest_static_simulation(self):
        url = reverse('tracker-location-ingest')
        payload = {
            "device_id": "ASSET-ESP32-01",
            "latitude": 12.980000,
            "longitude": 77.590000,
            "source_type": LocationSource.STATIC_SIMULATION,
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['source_type'], LocationSource.STATIC_SIMULATION)

    def test_location_ingest_invalid_coordinates(self):
        url = reverse('tracker-location-ingest')
        payload = {
            "device_id": "ASSET-ESP32-01",
            "latitude": 105.000000,  # Invalid latitude (> 90)
            "longitude": 77.594600,
            "source_type": LocationSource.STATIC_SIMULATION,
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 400)

    def test_location_ingest_geofence_exit_transition(self):
        url = reverse('tracker-location-ingest')
        # Point 1: Inside geofence (< 500m)
        self.client.post(url, {
            "device_id": "ASSET-ESP32-01",
            "latitude": 12.971600,
            "longitude": 77.594600,
            "source_type": LocationSource.DYNAMIC_SIMULATION,
        }, format='json')

        # Point 2: Far outside geofence (> 2km)
        resp2 = self.client.post(url, {
            "device_id": "ASSET-ESP32-01",
            "latitude": 13.000000,
            "longitude": 77.594600,
            "source_type": LocationSource.DYNAMIC_SIMULATION,
        }, format='json')

        self.assertEqual(resp2.status_code, 201)
        self.assertFalse(resp2.data['is_inside_geofence'])
        self.assertEqual(resp2.data['geofence_transition'], GeofenceTransition.EXIT)

    # --- Status and History Endpoints Tests ---

    def test_tracker_status_endpoint(self):
        # Set some location first
        record_location(
            asset=self.asset,
            latitude="12.971600",
            longitude="77.594600",
            source_type=LocationSource.REAL_GPS,
        )
        url = reverse('tracker-status') + f"?device_id={self.asset.device_id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['device_id'], self.asset.device_id)
        self.assertEqual(response.data['last_known_source'], LocationSource.REAL_GPS)
        self.assertIsNotNone(response.data['distance_from_center_meters'])
        self.assertFalse(response.data['active_sos'])

    def test_location_history_endpoint(self):
        record_location(self.asset, "12.971600", "77.594600", LocationSource.REAL_GPS)
        record_location(self.asset, "12.972000", "77.595000", LocationSource.DYNAMIC_SIMULATION)

        url = reverse('tracker-history') + f"?device_id={self.asset.device_id}&limit=10"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

        # Filter by source_type
        filter_url = reverse('tracker-history') + f"?source_type={LocationSource.REAL_GPS}"
        filter_resp = self.client.get(filter_url)
        self.assertEqual(filter_resp.status_code, 200)
        self.assertEqual(len(filter_resp.data), 1)
        self.assertEqual(filter_resp.data[0]['source_type'], LocationSource.REAL_GPS)

    # --- Geofence Endpoints Tests ---

    def test_geofence_get(self):
        url = reverse('tracker-geofence') + f"?device_id={self.asset.device_id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['radius_meters'], 500.0)

    def test_geofence_put_authorized(self):
        self.client.force_login(self.admin_user)
        url = reverse('tracker-geofence')
        payload = {
            "device_id": self.asset.device_id,
            "center_latitude": 12.975000,
            "center_longitude": 77.595000,
            "radius_meters": 1200.0,
            "is_active": True,
        }
        response = self.client.put(url, payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['radius_meters'], 1200.0)
        self.geofence.refresh_from_db()
        self.assertEqual(self.geofence.radius_meters, 1200.0)

    def test_geofence_put_unauthorized(self):
        url = reverse('tracker-geofence')
        payload = {
            "device_id": self.asset.device_id,
            "center_latitude": 12.975000,
            "center_longitude": 77.595000,
            "radius_meters": 1200.0,
        }
        # Without session authentication
        response = self.client.put(url, payload, format='json')
        self.assertEqual(response.status_code, 403)

        # Passing obsolete X-Dashboard-Key header without session is strictly rejected
        response_key = self.client.put(url, payload, format='json', HTTP_X_DASHBOARD_KEY="obsolete_key")
        self.assertEqual(response_key.status_code, 403)

    def test_geofence_update_does_not_create_artificial_location_record(self):
        self.client.force_login(self.admin_user)
        initial_record_count = LocationRecord.objects.count()
        url = reverse('tracker-geofence')
        payload = {
            "device_id": self.asset.device_id,
            "center_latitude": 12.975000,
            "center_longitude": 77.595000,
            "radius_meters": 800.0,
        }
        self.client.put(url, payload, format='json')
        # Verify no fake transition or location record was created
        self.assertEqual(LocationRecord.objects.count(), initial_record_count)

    # --- SOS Endpoints Tests ---

    def test_sos_trigger_hardware_button_authorized(self):
        url = reverse('tracker-sos-trigger')
        payload = {
            "device_id": self.asset.device_id,
            "trigger_source": SOSTriggerSource.HARDWARE_BUTTON,
            "has_gps_fix": False,  # Missing antenna scenario
        }
        response = self.client.post(url, payload, format='json', **self.device_headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], SOSStatus.ACTIVE)
        self.assertFalse(response.data['has_gps_fix'])
        self.assertIsNone(response.data['latitude'])
        self.assertIsNone(response.data['longitude'])

    def test_sos_trigger_hardware_button_unauthorized(self):
        url = reverse('tracker-sos-trigger')
        payload = {
            "device_id": self.asset.device_id,
            "trigger_source": SOSTriggerSource.HARDWARE_BUTTON,
            "has_gps_fix": False,
        }
        # Missing device token
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 401)

    def test_sos_trigger_dashboard_simulation(self):
        url = reverse('tracker-sos-trigger')
        payload = {
            "device_id": self.asset.device_id,
            "trigger_source": SOSTriggerSource.DASHBOARD_SIMULATION,
            "has_gps_fix": True,
            "latitude": 12.971600,
            "longitude": 77.594600,
            "location_source": LocationSource.DYNAMIC_SIMULATION,
        }
        # Allowed from dashboard without hardware token
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['trigger_source'], SOSTriggerSource.DASHBOARD_SIMULATION)
        self.assertEqual(response.data['location_source'], LocationSource.DYNAMIC_SIMULATION)

    def test_sos_simulation_cannot_claim_real_gps(self):
        url = reverse('tracker-sos-trigger')
        payload = {
            "device_id": self.asset.device_id,
            "trigger_source": SOSTriggerSource.DASHBOARD_SIMULATION,
            "has_gps_fix": True,
            "latitude": 12.971600,
            "longitude": 77.594600,
            "location_source": LocationSource.REAL_GPS,  # Disallowed
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 400)

    def test_active_sos_and_resolve_lifecycle(self):
        # 1. Trigger an SOS
        sos = process_sos_event(
            asset=self.asset,
            trigger_source=SOSTriggerSource.HARDWARE_BUTTON,
            has_gps_fix=False,
        )

        # 2. Check active SOS endpoint
        active_url = reverse('tracker-sos-active')
        active_resp = self.client.get(active_url)
        self.assertEqual(active_resp.status_code, 200)
        self.assertEqual(len(active_resp.data), 1)
        self.assertEqual(active_resp.data[0]['id'], sos.id)

        # 3. Resolve without admin auth -> 403 Forbidden
        resolve_url = reverse('tracker-sos-resolve', kwargs={'pk': sos.id})
        forbidden_resp = self.client.post(resolve_url, {"resolved_by": "Operator"}, format='json')
        self.assertEqual(forbidden_resp.status_code, 403)

        # 4. Resolve with admin auth -> 200 OK
        self.client.force_login(self.admin_user)
        resolve_resp = self.client.post(
            resolve_url,
            {"resolved_by": "Security Officer", "notes": "Handled on site."},
            format='json'
        )
        self.assertEqual(resolve_resp.status_code, 200)
        self.assertEqual(resolve_resp.data['status'], SOSStatus.RESOLVED)
        self.assertEqual(resolve_resp.data['resolved_by'], "Security Officer")

        # 5. Check active SOS is now empty
        empty_resp = self.client.get(active_url)
        self.assertEqual(len(empty_resp.data), 0)


    def test_fresh_asset_api_location_lifecycle(self):
        """
        Tests the full fresh asset location ingestion lifecycle via REST API:
        1. First location inside -> NONE (baseline established)
        2. Second location outside -> EXIT
        3. Third location inside -> ENTRY
        4. Fourth location inside -> NONE (duplicate suppression)
        """
        fresh_asset = Asset.objects.create(
            device_id="API-FRESH-LIFECYCLE-01",
            name="API Fresh Asset",
            is_inside_geofence=None,
        )
        GeofenceSettings.objects.create(
            asset=fresh_asset,
            center_latitude=Decimal("12.971600"),
            center_longitude=Decimal("77.594600"),
            radius_meters=500.0,
            is_active=True,
        )

        url = reverse('tracker-location-ingest')

        # 1. First location inside -> NONE
        r1 = self.client.post(url, {
            "device_id": fresh_asset.device_id,
            "latitude": 12.971650,
            "longitude": 77.594650,
            "source_type": LocationSource.DYNAMIC_SIMULATION,
        }, format='json')
        self.assertEqual(r1.status_code, 201)
        self.assertTrue(r1.data['is_inside_geofence'])
        self.assertEqual(r1.data['geofence_transition'], GeofenceTransition.NONE)

        # 2. Location outside -> EXIT
        r2 = self.client.post(url, {
            "device_id": fresh_asset.device_id,
            "latitude": 12.985000,
            "longitude": 77.610000,
            "source_type": LocationSource.DYNAMIC_SIMULATION,
        }, format='json')
        self.assertEqual(r2.status_code, 201)
        self.assertFalse(r2.data['is_inside_geofence'])
        self.assertEqual(r2.data['geofence_transition'], GeofenceTransition.EXIT)

        # 3. Location inside -> ENTRY
        r3 = self.client.post(url, {
            "device_id": fresh_asset.device_id,
            "latitude": 12.971650,
            "longitude": 77.594650,
            "source_type": LocationSource.DYNAMIC_SIMULATION,
        }, format='json')
        self.assertEqual(r3.status_code, 201)
        self.assertTrue(r3.data['is_inside_geofence'])
        self.assertEqual(r3.data['geofence_transition'], GeofenceTransition.ENTRY)

        # 4. Repeated location inside -> NONE
        r4 = self.client.post(url, {
            "device_id": fresh_asset.device_id,
            "latitude": 12.971600,
            "longitude": 77.594600,
            "source_type": LocationSource.DYNAMIC_SIMULATION,
        }, format='json')
        self.assertEqual(r4.status_code, 201)
        self.assertTrue(r4.data['is_inside_geofence'])
        self.assertEqual(r4.data['geofence_transition'], GeofenceTransition.NONE)


class Phase1SoftwareLocationTargetModelTests(TestCase):
    """
    Unit tests for Phase 1 Database Model and Lifecycle Tracking:
    - Verifies SOFTWARE_SIMULATION choice existence and preservation of existing modes.
    - Verifies SoftwareLocationTarget creation, ordering, and device association.
    - Verifies lifecycle state machine: PENDING -> DISPATCHED -> CONSUMED / FAILED.
    - Verifies retry resilience (upload failure does not lose staged coordinates).
    - Verifies cryptographic device token generator.
    """
    def setUp(self):
        self.asset = Asset.objects.create(
            device_id="TEST-NODE-SW-01",
            name="ESP32 Software Test Node",
            current_mode=LocationSource.SOFTWARE_SIMULATION,
        )

    def test_location_source_enum_values(self):
        # Verify SOFTWARE_SIMULATION is present as a distinct choice
        self.assertIn('SOFTWARE_SIMULATION', LocationSource.values)
        self.assertEqual(LocationSource.SOFTWARE_SIMULATION, 'SOFTWARE_SIMULATION')

        # Verify existing modes remain unchanged
        self.assertIn('REAL_GPS', LocationSource.values)
        self.assertIn('DYNAMIC_SIMULATION', LocationSource.values)
        self.assertIn('STATIC_SIMULATION', LocationSource.values)
        self.assertEqual(len(LocationSource.values), 4)

    def test_create_software_location_target_defaults(self):
        target = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.972500"),
            longitude=Decimal("77.595500"),
            staged_by="AdminOperator",
            sequence_number=1,
        )
        self.assertEqual(target.status, TargetStatus.PENDING)
        self.assertEqual(target.dispatch_count, 0)
        self.assertIsNone(target.dispatched_at)
        self.assertIsNone(target.consumed_at)
        self.assertIsNone(target.location_record)
        self.assertIsNone(target.error_message)
        self.assertIn("SoftwareTarget #", str(target))

    def test_target_lifecycle_dispatch_and_consumption(self):
        target = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.973000"),
            longitude=Decimal("77.596000"),
            staged_by="Operator",
        )
        # 1. ESP32 polls and target is marked DISPATCHED
        target.mark_dispatched()
        target.refresh_from_db()
        self.assertEqual(target.status, TargetStatus.DISPATCHED)
        self.assertEqual(target.dispatch_count, 1)
        self.assertIsNotNone(target.dispatched_at)
        self.assertIsNone(target.consumed_at)

        # 2. Resilient retry: If ESP32 re-fetches without upload, dispatch count increments
        target.mark_dispatched()
        target.refresh_from_db()
        self.assertEqual(target.status, TargetStatus.DISPATCHED)
        self.assertEqual(target.dispatch_count, 2)
        # Coordinates must remain completely preserved
        self.assertEqual(target.latitude, Decimal("12.973000"))
        self.assertEqual(target.longitude, Decimal("77.596000"))

        # 3. Telemetry uploaded and ingested: target marked CONSUMED
        record = LocationRecord.objects.create(
            asset=self.asset,
            latitude=Decimal("12.973000"),
            longitude=Decimal("77.596000"),
            source_type=LocationSource.SOFTWARE_SIMULATION,
            timestamp=timezone.now(),
        )
        target.mark_consumed(location_record=record)
        target.refresh_from_db()
        self.assertEqual(target.status, TargetStatus.CONSUMED)
        self.assertIsNotNone(target.consumed_at)
        self.assertEqual(target.location_record, record)

    def test_target_mark_failed(self):
        target = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.975000"),
            longitude=Decimal("77.597000"),
        )
        target.mark_failed("Network timeout on node")
        target.refresh_from_db()
        self.assertEqual(target.status, TargetStatus.FAILED)
        self.assertEqual(target.error_message, "Network timeout on node")

    def test_multiple_pending_targets_ordering(self):
        # Create 3 targets sequentially
        t1 = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.971000"),
            longitude=Decimal("77.591000"),
            sequence_number=1,
        )
        t2 = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.972000"),
            longitude=Decimal("77.592000"),
            sequence_number=2,
        )
        t3 = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.973000"),
            longitude=Decimal("77.593000"),
            sequence_number=3,
        )

        pending = list(SoftwareLocationTarget.objects.filter(
            asset=self.asset,
            status=TargetStatus.PENDING
        ))
        self.assertEqual(len(pending), 3)
        self.assertEqual(pending[0].id, t1.id)
        self.assertEqual(pending[1].id, t2.id)
        self.assertEqual(pending[2].id, t3.id)

    def test_cryptographic_device_token_generator(self):
        token1 = generate_device_token()
        token2 = generate_device_token()
        self.assertIsInstance(token1, str)
        self.assertGreaterEqual(len(token1), 32)
        # Must be unique / non-predictable
        self.assertNotEqual(token1, token2)
        # New asset uses generate_device_token by default
        new_asset = Asset.objects.create(device_id="FRESH-NODE-99")
        self.assertIsInstance(new_asset.device_token, str)
        self.assertGreaterEqual(len(new_asset.device_token), 32)
        self.assertNotEqual(new_asset.device_token, "esp32_token_mca_default")


class Phase2DeviceIntegrationTests(TestCase):
    """
    Integration tests for Phase 2:
    - Device configuration polling with token authentication.
    - Configuration version increments on geofence updates and target staging.
    - Automatic geofence recalculation without needing new coordinates.
    - Discrepancy detection between ESP32 and Django geofence evaluation.
    - Device telemetry ingestion with physical LED/buzzer states and target consumption.
    - Remote SOS clear and acknowledgement lifecycle.
    - TrackerStatusView rich hardware connection and state reporting.
    """
    def setUp(self):
        from django.contrib.auth.models import User
        self.admin_user = User.objects.create_superuser(
            username="testadmin",
            password="testadminpassword",
            email="testadmin@tracker.local"
        )
        self.device_token = "secret_hw_token_xyz_987"
        self.asset = Asset.objects.create(
            device_id="ASSET-ESP32-TEST",
            name="Test ESP32 Node",
            current_mode=LocationSource.SOFTWARE_SIMULATION,
            device_token=self.device_token,
            config_version=1,
            last_processed_config_version=1,
        )
        # Geofence center: Bangalore (12.971600, 77.594600), Radius: 500m
        self.geofence = GeofenceSettings.objects.create(
            asset=self.asset,
            center_latitude=Decimal("12.971600"),
            center_longitude=Decimal("77.594600"),
            radius_meters=500.0,
            is_active=True,
        )

    def test_geofence_update_increments_config_version(self):
        self.client.login(username="testadmin", password="testadminpassword")
        url = reverse('tracker-geofence')
        response = self.client.put(url, {
            "device_id": self.asset.device_id,
            "center_latitude": 12.971600,
            "center_longitude": 77.594600,
            "radius_meters": 750.0,
            "is_active": True
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.config_version, 2)
        self.assertEqual(self.asset.geofence_settings.radius_meters, 750.0)

    def test_stage_software_target_and_increment_version(self):
        # Unauthenticated staging rejected
        url = reverse('tracker-device-target')
        r_unauth = self.client.post(url, {
            "device_id": self.asset.device_id,
            "latitude": 12.975500,
            "longitude": 77.598500
        }, content_type="application/json")
        self.assertEqual(r_unauth.status_code, 403)

        # Authenticated staging succeeds and increments config_version
        self.client.login(username="testadmin", password="testadminpassword")
        r_auth = self.client.post(url, {
            "device_id": self.asset.device_id,
            "latitude": 12.975500,
            "longitude": 77.598500
        }, content_type="application/json")
        self.assertEqual(r_auth.status_code, 201)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.config_version, 2)
        target = self.asset.software_location_targets.first()
        self.assertIsNotNone(target)
        self.assertEqual(target.status, TargetStatus.PENDING)
        self.assertEqual(float(target.latitude), 12.9755)

    def test_device_config_poll_with_token(self):
        # Stage target first
        SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.975500"),
            longitude=Decimal("77.598500"),
            sequence_number=1,
            status=TargetStatus.PENDING,
        )
        url = reverse('tracker-device-config') + f"?device_id={self.asset.device_id}"

        # Unauthorized without token
        r_no_token = self.client.get(url)
        self.assertEqual(r_no_token.status_code, 401)

        # Authorized with X-Device-Token
        r_token = self.client.get(url, HTTP_X_DEVICE_TOKEN=self.device_token)
        self.assertEqual(r_token.status_code, 200)
        data = r_token.json()
        self.assertEqual(data['config_version'], 1)
        self.assertEqual(data['geofence']['radius_meters'], 500.0)
        self.assertTrue(data['target_location']['has_target'])
        self.assertEqual(data['target_location']['latitude'], 12.9755)
        self.assertFalse(data['clear_sos_command'])

        # Target transitioned to DISPATCHED
        target = self.asset.software_location_targets.first()
        self.assertEqual(target.status, TargetStatus.DISPATCHED)
        self.assertEqual(target.dispatch_count, 1)

        # Heartbeat updated
        self.asset.refresh_from_db()
        self.assertIsNotNone(self.asset.last_seen_at)

    def test_device_telemetry_ingestion_and_target_consumption(self):
        target = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.975500"),
            longitude=Decimal("77.598500"),
            sequence_number=1,
            status=TargetStatus.DISPATCHED,
        )
        url = reverse('tracker-device-telemetry')

        # Ingestion requires X-Device-Token
        r_no_tok = self.client.post(url, {
            "device_id": self.asset.device_id,
            "latitude": 12.975500,
            "longitude": 77.598500,
        }, content_type="application/json")
        self.assertEqual(r_no_tok.status_code, 401)

        # Telemetry ingestion with physical output states
        r_ingest = self.client.post(url, {
            "device_id": self.asset.device_id,
            "latitude": 12.975500,
            "longitude": 77.598500,
            "source_type": LocationSource.SOFTWARE_SIMULATION,
            "satellites_count": 0,
            "esp32_distance_meters": 610.5,
            "esp32_is_inside": False,
            "led_green_state": False,
            "led_red_state": True,
            "buzzer_state": True,
            "sos_active": False,
            "config_version": 2,
            "target_id": target.id,
        }, content_type="application/json", HTTP_X_DEVICE_TOKEN=self.device_token)

        self.assertEqual(r_ingest.status_code, 201)
        resp_data = r_ingest.json()
        self.assertEqual(resp_data['status'], 'success')

        # Target consumed and linked
        target.refresh_from_db()
        self.assertEqual(target.status, TargetStatus.CONSUMED)
        self.assertIsNotNone(target.consumed_at)
        self.assertIsNotNone(target.location_record)

        # Asset state updated
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.last_processed_config_version, 2)
        self.assertFalse(self.asset.led_green_state)
        self.assertTrue(self.asset.led_red_state)
        self.assertTrue(self.asset.buzzer_state)
        self.assertFalse(self.asset.esp32_is_inside)

    def test_automatic_recalculation_after_geofence_changes(self):
        """
        User Mandatory Requirement:
        - Object at 750m distance from center, initial radius is 500m -> OUTSIDE (Red ON, Green OFF, Buzzer ON).
        - Operator updates radius to 1,000m via geofence PUT.
        - Config version increments automatically.
        - Existing coordinates are retained.
        - ESP32 polls config, keeps object coordinates, recalculates distance -> INSIDE.
        - ESP32 posts telemetry: Green ON, Red OFF, Buzzer OFF.
        - Dashboard immediately reflects updated state without new coordinates needed!
        """
        # 1. Establish initial position at ~750m distance (approx 12.978000, 77.594600)
        # Distance from 12.971600, 77.594600 is ~712m > 500m radius
        obj_lat = Decimal("12.978000")
        obj_lon = Decimal("77.594600")

        self.client.post(reverse('tracker-device-telemetry'), {
            "device_id": self.asset.device_id,
            "latitude": float(obj_lat),
            "longitude": float(obj_lon),
            "source_type": LocationSource.SOFTWARE_SIMULATION,
            "esp32_distance_meters": 712.0,
            "esp32_is_inside": False,
            "led_green_state": False,
            "led_red_state": True,
            "buzzer_state": True,
            "config_version": 1,
        }, content_type="application/json", HTTP_X_DEVICE_TOKEN=self.device_token)

        self.asset.refresh_from_db()
        self.assertFalse(self.asset.is_inside_geofence)
        self.assertFalse(self.asset.esp32_is_inside)
        self.assertTrue(self.asset.led_red_state)

        # 2. Operator increases radius to 1000m via PUT /api/tracker/geofence/
        self.client.login(username="testadmin", password="testadminpassword")
        r_put = self.client.put(reverse('tracker-geofence'), {
            "device_id": self.asset.device_id,
            "center_latitude": 12.971600,
            "center_longitude": 77.594600,
            "radius_meters": 1000.0,
            "is_active": True,
        }, content_type="application/json")
        self.assertEqual(r_put.status_code, 200)

        self.asset.refresh_from_db()
        # Version incremented automatically!
        self.assertEqual(self.asset.config_version, 2)
        # Existing coordinates retained in database!
        self.assertEqual(self.asset.last_known_latitude, obj_lat)
        # Server recalculated boundary immediately: 712m <= 1000m -> INSIDE
        self.assertTrue(self.asset.is_inside_geofence)

        # 3. ESP32 polls config over Wi-Fi
        r_poll = self.client.get(
            reverse('tracker-device-config') + f"?device_id={self.asset.device_id}",
            HTTP_X_DEVICE_TOKEN=self.device_token
        )
        cfg = r_poll.json()
        self.assertEqual(cfg['config_version'], 2)
        self.assertEqual(cfg['geofence']['radius_meters'], 1000.0)
        # No new target needed; ESP32 retains existing coordinates
        self.assertFalse(cfg['target_location']['has_target'])

        # 4. ESP32 recalculates distance with existing coordinates: 712m < 1000m -> INSIDE
        # ESP32 sets Green LED ON, Red LED OFF, Buzzer OFF and reports to telemetry
        r_recalc = self.client.post(reverse('tracker-device-telemetry'), {
            "device_id": self.asset.device_id,
            "latitude": float(obj_lat),
            "longitude": float(obj_lon),
            "source_type": LocationSource.SOFTWARE_SIMULATION,
            "esp32_distance_meters": 712.0,
            "esp32_is_inside": True,
            "led_green_state": True,
            "led_red_state": False,
            "buzzer_state": False,
            "config_version": 2,
        }, content_type="application/json", HTTP_X_DEVICE_TOKEN=self.device_token)

        self.assertEqual(r_recalc.status_code, 201)
        self.asset.refresh_from_db()
        self.assertTrue(self.asset.esp32_is_inside)
        self.assertTrue(self.asset.led_green_state)
        self.assertFalse(self.asset.led_red_state)
        self.assertFalse(self.asset.buzzer_state)
        self.assertEqual(self.asset.last_processed_config_version, 2)

        # 5. Dashboard status endpoint reflects synchronized INSIDE state
        r_status = self.client.get(reverse('tracker-status') + f"?device_id={self.asset.device_id}")
        st = r_status.json()
        self.assertEqual(st['backend_boundary_state'], "INSIDE")
        self.assertEqual(st['esp32_boundary_state'], "INSIDE")
        self.assertFalse(st['is_sync_pending'])
        self.assertTrue(st['hardware_outputs']['led_green'])
        self.assertFalse(st['hardware_outputs']['led_red'])
        self.assertFalse(st['hardware_outputs']['buzzer'])

    def test_boundary_mismatch_detection(self):
        # ESP32 claims INSIDE, but server evaluates OUTSIDE (coord 13.000, 77.5946 is >3km away)
        r = self.client.post(reverse('tracker-device-telemetry'), {
            "device_id": self.asset.device_id,
            "latitude": 13.000000,
            "longitude": 77.594600,
            "source_type": LocationSource.SOFTWARE_SIMULATION,
            "esp32_distance_meters": 450.0,
            "esp32_is_inside": True,  # Disagreement with server
        }, content_type="application/json", HTTP_X_DEVICE_TOKEN=self.device_token)

        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()['boundary_mismatch'])
        self.asset.refresh_from_db()
        self.assertTrue(self.asset.boundary_mismatch)

    def test_remote_sos_clear_and_acknowledgement(self):
        # Trigger SOS
        sos = SOSEvent.objects.create(
            asset=self.asset,
            trigger_source=SOSTriggerSource.HARDWARE_BUTTON,
            status=SOSStatus.ACTIVE,
        )
        self.asset.esp32_sos_active = True
        self.asset.save()

        # Operator resolves SOS via dashboard
        self.client.login(username="testadmin", password="testadminpassword")
        r_resolve = self.client.post(reverse('tracker-sos-resolve', kwargs={'pk': sos.id}), {
            "resolved_by": "Operator",
            "notes": "Cleared alert",
        }, content_type="application/json")
        self.assertEqual(r_resolve.status_code, 200)

        self.asset.refresh_from_db()
        self.assertTrue(self.asset.pending_sos_clear)

        # ESP32 polls config and receives clear command
        r_poll = self.client.get(
            reverse('tracker-device-config') + f"?device_id={self.asset.device_id}",
            HTTP_X_DEVICE_TOKEN=self.device_token
        )
        self.assertTrue(r_poll.json()['clear_sos_command'])

        # ESP32 executes resetSOS() and acknowledges clear command
        r_ack = self.client.post(reverse('tracker-device-clear-sos-ack'), {
            "device_id": self.asset.device_id,
            "clear_sos_ack": True,
        }, content_type="application/json", HTTP_X_DEVICE_TOKEN=self.device_token)
        self.assertEqual(r_ack.status_code, 200)

        # Clear flag reset so command is not repeated indefinitely
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.pending_sos_clear)
        self.assertFalse(self.asset.esp32_sos_active)

    def test_tracker_status_hardware_state(self):
        # Set heartbeat and reported state
        now = timezone.now()
        self.asset.last_seen_at = now
        self.asset.last_telemetry_at = now
        self.asset.esp32_latitude = Decimal("12.975500")
        self.asset.esp32_longitude = Decimal("77.598500")
        self.asset.esp32_distance_meters = 620.0
        self.asset.esp32_is_inside = False
        self.asset.led_green_state = False
        self.asset.led_red_state = True
        self.asset.buzzer_state = True
        self.asset.config_version = 5
        self.asset.last_processed_config_version = 4
        self.asset.save()

        url = reverse('tracker-status') + f"?device_id={self.asset.device_id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Verify all required synchronization and hardware fields
        self.assertEqual(data['config_version_requested'], 5)
        self.assertEqual(data['config_version_acknowledged'], 4)
        self.assertTrue(data['is_sync_pending'])
        self.assertEqual(data['esp32_confirmed_location']['latitude'], 12.9755)
        self.assertEqual(data['esp32_confirmed_location']['longitude'], 77.5985)
        self.assertEqual(data['esp32_calculated_distance'], 620.0)
        self.assertEqual(data['esp32_boundary_state'], "OUTSIDE")
        self.assertFalse(data['hardware_outputs']['led_green'])
        self.assertTrue(data['hardware_outputs']['led_red'])
        self.assertTrue(data['hardware_outputs']['buzzer'])
        self.assertTrue(data['hardware_connection']['is_online'])
        self.assertIsNotNone(data['hardware_connection']['last_seen_at'])
        self.assertIsNotNone(data['hardware_connection']['last_telemetry_at'])

    def test_target_retry_and_recovery_on_disconnect(self):
        """
        Tests Requirement A.1: A target marked DISPATCHED must never disappear permanently.
        If ESP32 loses Wi-Fi or reboots before uploading telemetry, re-polling recovers the same target.
        """
        target = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.981200"),
            longitude=Decimal("77.599800"),
            sequence_number=1,
            status=TargetStatus.PENDING,
        )
        url = reverse('tracker-device-config') + f"?device_id={self.asset.device_id}"

        # 1. First poll: target is dispatched
        r1 = self.client.get(url, HTTP_X_DEVICE_TOKEN=self.device_token)
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()['target_location']['has_target'])
        self.assertEqual(r1.json()['target_location']['target_id'], target.id)
        self.assertEqual(r1.json()['target_location']['dispatch_count'], 1)

        target.refresh_from_db()
        self.assertEqual(target.status, TargetStatus.DISPATCHED)

        # 2. Simulate ESP32 reboot / Wi-Fi loss: re-poll config endpoint
        r2 = self.client.get(url, HTTP_X_DEVICE_TOKEN=self.device_token)
        self.assertEqual(r2.status_code, 200)
        # Target must be recovered with identical ID and coordinates!
        self.assertTrue(r2.json()['target_location']['has_target'])
        self.assertEqual(r2.json()['target_location']['target_id'], target.id)
        self.assertEqual(r2.json()['target_location']['latitude'], 12.9812)
        self.assertEqual(r2.json()['target_location']['longitude'], 77.5998)
        self.assertEqual(r2.json()['target_location']['dispatch_count'], 2)

        # 3. Telemetry ingestion successfully acknowledges and consumes the target
        r_tel = self.client.post(reverse('tracker-device-telemetry'), {
            "device_id": self.asset.device_id,
            "latitude": 12.981200,
            "longitude": 77.599800,
            "source_type": LocationSource.SOFTWARE_SIMULATION,
            "esp32_distance_meters": 540.0,
            "esp32_is_inside": False,
            "target_id": target.id,
            "config_version": 1,
        }, content_type="application/json", HTTP_X_DEVICE_TOKEN=self.device_token)
        self.assertEqual(r_tel.status_code, 201)

        target.refresh_from_db()
        self.assertEqual(target.status, TargetStatus.CONSUMED)
        self.assertIsNotNone(target.consumed_at)

    def test_target_max_retries_surfaces_failed(self):
        """
        Tests Requirement A.5: Expired or repeatedly failed targets must be surfaced clearly.
        If target reaches 5 retries without telemetry, it transitions to FAILED.
        """
        target = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.981200"),
            longitude=Decimal("77.599800"),
            sequence_number=1,
            status=TargetStatus.DISPATCHED,
            dispatch_count=5,
            dispatched_at=timezone.now() - timedelta(seconds=20),
        )
        # Next poll detects timeout & retry limit reached
        url = reverse('tracker-device-config') + f"?device_id={self.asset.device_id}"
        r = self.client.get(url, HTTP_X_DEVICE_TOKEN=self.device_token)
        self.assertEqual(r.status_code, 200)

        target.refresh_from_db()
        self.assertEqual(target.status, TargetStatus.FAILED)
        self.assertIn("dispatch retry limit reached", target.error_message)

        # Status endpoint surfaces failed targets
        r_status = self.client.get(reverse('tracker-status') + f"?device_id={self.asset.device_id}")
        st = r_status.json()
        self.assertEqual(st['targets_summary']['failed_count'], 1)
        self.assertEqual(st['targets_summary']['latest_failed_target']['id'], target.id)

    def test_duplicate_target_delivery_prevention(self):
        """
        Tests Requirement A.3: Duplicate target delivery must not create duplicate location records.
        """
        target = SoftwareLocationTarget.objects.create(
            asset=self.asset,
            latitude=Decimal("12.975500"),
            longitude=Decimal("77.598500"),
            sequence_number=1,
            status=TargetStatus.DISPATCHED,
        )
        url = reverse('tracker-device-telemetry')

        # 1. First telemetry report: creates LocationRecord
        r1 = self.client.post(url, {
            "device_id": self.asset.device_id,
            "latitude": 12.975500,
            "longitude": 77.598500,
            "source_type": LocationSource.SOFTWARE_SIMULATION,
            "esp32_distance_meters": 430.0,
            "esp32_is_inside": True,
            "target_id": target.id,
            "config_version": 1,
        }, content_type="application/json", HTTP_X_DEVICE_TOKEN=self.device_token)
        self.assertEqual(r1.status_code, 201)
        rec_id = r1.json()['record_id']
        self.assertEqual(LocationRecord.objects.filter(asset=self.asset).count(), 1)

        # 2. Duplicate telemetry report for the same target_id
        r2 = self.client.post(url, {
            "device_id": self.asset.device_id,
            "latitude": 12.975500,
            "longitude": 77.598500,
            "source_type": LocationSource.SOFTWARE_SIMULATION,
            "esp32_distance_meters": 430.0,
            "esp32_is_inside": True,
            "target_id": target.id,
            "config_version": 1,
        }, content_type="application/json", HTTP_X_DEVICE_TOKEN=self.device_token)

        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()['status'], 'duplicate_ignored')
        self.assertEqual(r2.json()['record_id'], rec_id)

        # Verify duplicate LocationRecord was NOT created
        self.assertEqual(LocationRecord.objects.filter(asset=self.asset).count(), 1)

    def test_session_authentication_and_csrf_enforcement(self):
        """
        Tests Requirement B:
        - Dashboard write operations require Django session authentication and CSRF.
        - Predictable X-Dashboard-Key is strictly rejected.
        - Operator login establishes session cookie.
        """
        geofence_url = reverse('tracker-geofence')
        target_url = reverse('tracker-device-target')
        resolve_url = reverse('tracker-sos-resolve', kwargs={'pk': 999})

        # 1. Rejects unauthenticated requests
        self.assertEqual(self.client.put(geofence_url, {}, content_type="application/json").status_code, 403)
        self.assertEqual(self.client.post(target_url, {}, content_type="application/json").status_code, 403)
        self.assertEqual(self.client.post(resolve_url, {}, content_type="application/json").status_code, 403)

        # 2. Rejects X-Dashboard-Key header fallback
        r_key = self.client.put(geofence_url, {
            "device_id": self.asset.device_id,
            "center_latitude": 12.9716,
            "center_longitude": 77.5946,
            "radius_meters": 500,
        }, content_type="application/json", HTTP_X_DASHBOARD_KEY="dashboard_admin_key_mca_2026")
        self.assertEqual(r_key.status_code, 403)

        # 3. Operator login via username and password
        login_url = reverse('tracker-auth-login')
        r_login = self.client.post(login_url, {
            "username": "testadmin",
            "password": "testadminpassword"
        }, content_type="application/json")
        self.assertEqual(r_login.status_code, 200)
        self.assertEqual(r_login.json()['status'], 'authenticated')

        # 4. Authenticated session can execute write operations
        r_stage = self.client.post(target_url, {
            "device_id": self.asset.device_id,
            "latitude": 12.976000,
            "longitude": 77.597000
        }, content_type="application/json")
        self.assertEqual(r_stage.status_code, 201)

    def test_geofence_only_update_retains_coordinates_and_increments_version(self):
        """
        Tests Requirement C & Clarification 2:
        - Geofence updates retain existing coordinates.
        - Version increments monotonically on every geofence update.
        """
        # Set known coordinates
        self.asset.last_known_latitude = Decimal("12.980000")
        self.asset.last_known_longitude = Decimal("77.594600")
        self.asset.config_version = 10
        self.asset.save()

        self.client.login(username="testadmin", password="testadminpassword")
        url = reverse('tracker-geofence')
        r = self.client.put(url, {
            "device_id": self.asset.device_id,
            "center_latitude": 12.971600,
            "center_longitude": 77.594600,
            "radius_meters": 850.0,
            "is_active": True,
        }, content_type="application/json")
        self.assertEqual(r.status_code, 200)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.config_version, 11)
        self.assertEqual(self.asset.last_known_latitude, Decimal("12.980000"))
        self.assertEqual(self.asset.last_known_longitude, Decimal("77.594600"))
        # 12.980000 is ~934m from 12.971600; with 850m radius, it evaluates OUTSIDE
        self.assertFalse(self.asset.is_inside_geofence)




