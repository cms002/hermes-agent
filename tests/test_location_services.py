"""Tests for the location_services tool."""

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

# Ensure the tools directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.location_services as location_module


# ---------------------------------------------------------------------------
# _run_command
# ---------------------------------------------------------------------------

class TestRunCommand:
    def test_run_command_success(self):
        result = location_module._run_command(["echo", "hello"])
        assert result == "hello"

    def test_run_command_timeout(self):
        result = location_module._run_command(["sleep", "100"], timeout=1)
        assert result == ""

    def test_run_command_missing_binary(self):
        result = location_module._run_command(["nonexistent_cmd_xyz"])
        assert result == ""


# ---------------------------------------------------------------------------
# _get_ip_address
# ---------------------------------------------------------------------------

class TestGetIpAddress:
    @patch("tools.location_services.requests.get")
    def test_get_ip_address_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "1.2.3.4"
        mock_get.return_value = mock_response
        assert location_module._get_ip_address() == "1.2.3.4"

    @patch("tools.location_services.requests.get")
    def test_get_ip_address_fallback_on_failure(self, mock_get):
        """If first service fails, try the next one."""
        mock_response_ok = MagicMock()
        mock_response_ok.status_code = 200
        mock_response_ok.text = "5.6.7.8"
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500
        mock_response_fail.text = ""
        mock_get.side_effect = [
            MagicMock(status_code=500, text=""),
            mock_response_ok,
            MagicMock(status_code=500, text=""),
        ]
        assert location_module._get_ip_address() == "5.6.7.8"

    @patch("tools.location_services.requests.get")
    def test_get_ip_address_all_fail(self, mock_get):
        mock_get.side_effect = Exception("Connection error")
        result = location_module._get_ip_address()
        assert result == ""


# ---------------------------------------------------------------------------
# _get_ip_location
# ---------------------------------------------------------------------------

class TestGetIpLocation:
    @patch("tools.location_services.requests.get")
    def test_get_ip_location_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "city": "Seattle",
            "region": "WA",
            "country": "US",
            "org": "Amazon.com",
            "loc": "47.6062,-122.3321",
            "timezone": "America/Los_Angeles",
        }
        mock_get.return_value = mock_response
        result = location_module._get_ip_location("1.2.3.4")
        assert result["city"] == "Seattle"
        assert result["loc"] == "47.6062,-122.3321"

    @patch("tools.location_services.requests.get")
    def test_get_ip_location_failure(self, mock_get):
        mock_get.side_effect = Exception("Connection error")
        assert location_module._get_ip_location("1.2.3.4") == {}

    @patch("tools.location_services.requests.get")
    def test_get_ip_location_empty_ip(self, mock_get):
        assert location_module._get_ip_location("") == {}


# ---------------------------------------------------------------------------
# _reverse_geocode
# ---------------------------------------------------------------------------

class TestReverseGeocode:
    @patch("tools.location_services.requests.get")
    def test_reverse_geocode_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "display_name": "123 Main St, Seattle, WA 98101",
            "name": "Space Needle",
            "type": "observation_tower",
            "class": "tourism",
            "address": {
                "house_number": "123",
                "road": "Main St",
                "city": "Seattle",
                "state": "WA",
                "country": "United States",
                "country_code": "us",
                "postcode": "98101",
            },
        }
        mock_get.return_value = mock_response
        result = location_module._reverse_geocode("47.6062", "-122.3321")
        assert result["display_name"] == "123 Main St, Seattle, WA 98101"
        assert result["address"]["city"] == "Seattle"

    @patch("tools.location_services.requests.get")
    def test_reverse_geocode_failure(self, mock_get):
        mock_get.side_effect = Exception("Connection error")
        assert location_module._reverse_geocode("0", "0") == {}


# ---------------------------------------------------------------------------
# _detect_location
# ---------------------------------------------------------------------------

class TestDetectLocation:
    def test_cache_returns_same_result(self):
        """Calling _detect_location twice should return cached result."""
        location_module._reset_location_cache()

        with patch.object(location_module, "_get_ip_address", return_value="1.2.3.4"):
            with patch.object(location_module, "_get_ip_location", return_value={}):
                result1 = location_module._detect_location()
                result2 = location_module._detect_location()
                assert result1 is result2  # Same cached object

    def test_reset_cache(self):
        """_reset_location_cache should clear the cache."""
        location_module._reset_location_cache()
        assert location_module._LOCATION_CACHE is None

    @patch("tools.location_services.platform.system", return_value="Darwin")
    @patch("tools.location_services._get_precise_location_macos", return_value=(None, None, None))
    @patch("tools.location_services._get_ip_address", return_value="")
    def test_detect_location_fallback_when_no_ip(self, _mock_ip, _mock_macos, _mock_system):
        """When everything fails, should return default fallback."""
        location_module._reset_location_cache()
        with patch.object(location_module, "_get_ip_location", return_value={}):
            result = location_module._detect_location()
            assert result["location"] == "Seattle"
            assert result["location_method"] == "default fallback"
            assert result["ip_address"] == "Unable to detect"


# ---------------------------------------------------------------------------
# location_get
# ---------------------------------------------------------------------------

class TestLocationGet:
    @patch("tools.location_services._detect_location")
    def test_location_get_returns_json(self, mock_detect):
        mock_detect.return_value = {
            "location": "Seattle",
            "location_method": "IP geolocation (approximate)",
            "ip_address": "1.2.3.4",
            "city": "Seattle",
            "state": "WA",
            "country": "United States",
        }
        result = location_module.location_get({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["location"]["location"] == "Seattle"

    @patch("tools.location_services._reset_location_cache")
    @patch("tools.location_services._detect_location")
    def test_location_get_refresh_bypasses_cache(self, mock_detect, mock_reset):
        mock_detect.return_value = {"location": "NYC", "location_method": "test"}
        location_module.location_get({"refresh": True})
        mock_reset.assert_called_once()


# ---------------------------------------------------------------------------
# location_get_coords
# ---------------------------------------------------------------------------

class TestLocationGetCoords:
    @patch("tools.location_services._detect_location")
    def test_get_coords_with_precise(self, mock_detect):
        mock_detect.return_value = {
            "precise_coords": "47.6062,-122.3321",
            "ip_coords": "47.6,-122.3",
            "timezone": "America/Los_Angeles",
            "location_method": "CoreLocation (precise GPS)",
        }
        result = location_module.location_get_coords({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["source"] == "precise"
        assert parsed["coords"] == "47.6062,-122.3321"
        assert parsed["latitude"] == "47.6062"
        assert parsed["longitude"] == "-122.3321"
        assert parsed["timezone"] == "America/Los_Angeles"

    @patch("tools.location_services._detect_location")
    def test_get_coords_ip_only(self, mock_detect):
        mock_detect.return_value = {
            "precise_coords": "",
            "ip_coords": "47.6,-122.3",
            "timezone": "",
            "location_method": "IP geolocation (approximate)",
        }
        result = location_module.location_get_coords({})
        parsed = json.loads(result)
        assert parsed["source"] == "ip"
        assert parsed["coords"] == "47.6,-122.3"

    @patch("tools.location_services._detect_location")
    def test_get_coords_no_coords(self, mock_detect):
        mock_detect.return_value = {
            "precise_coords": "",
            "ip_coords": "",
            "timezone": "",
            "location_method": "default fallback",
        }
        result = location_module.location_get_coords({})
        parsed = json.loads(result)
        assert parsed["source"] == "none"


# ---------------------------------------------------------------------------
# location_reverse_geocode
# ---------------------------------------------------------------------------

class TestLocationReverseGeocode:
    @patch("tools.location_services._reverse_geocode")
    def test_reverse_geocode_with_explicit_coords(self, mock_reverse):
        mock_reverse.return_value = {
            "display_name": "Seattle, WA, USA",
            "name": "",
            "type": "",
            "class": "",
            "address": {"city": "Seattle", "state": "WA"},
        }
        result = location_module.location_reverse_geocode({
            "latitude": "47.6062", "longitude": "-122.3321"
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["coordinates"] == "47.6062,-122.3321"
        assert parsed["address"]["display_name"] == "Seattle, WA, USA"

    @patch("tools.location_services._detect_location")
    @patch("tools.location_services._reverse_geocode")
    def test_reverse_geocode_uses_detected_coords(self, mock_reverse, mock_detect):
        mock_detect.return_value = {"precise_coords": "47.6,-122.3"}
        mock_reverse.return_value = {
            "display_name": "Seattle",
            "address": {"city": "Seattle"},
        }
        result = location_module.location_reverse_geocode({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"

    @patch("tools.location_services._reverse_geocode", return_value={})
    def test_reverse_geocode_no_coords(self, mock_reverse):
        result = location_module.location_reverse_geocode({})
        parsed = json.loads(result)
        assert "error" in parsed


# ---------------------------------------------------------------------------
# location_get_ip
# ---------------------------------------------------------------------------

class TestLocationGetIp:
    @patch("tools.location_services._get_ip_location")
    @patch("tools.location_services._get_ip_address")
    def test_get_ip_success(self, mock_ip, mock_loc):
        mock_ip.return_value = "1.2.3.4"
        mock_loc.return_value = {
            "city": "Seattle",
            "org": "Amazon.com",
            "region": "WA",
            "country": "US",
            "loc": "47.6,-122.3",
            "timezone": "America/Los_Angeles",
        }
        result = location_module.location_get_ip({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["ip_address"] == "1.2.3.4"
        assert parsed["isp"] == "Amazon.com"
        assert parsed["city"] == "Seattle"

    @patch("tools.location_services._get_ip_address", return_value="")
    def test_get_ip_no_ip(self, mock_ip):
        result = location_module.location_get_ip({})
        parsed = json.loads(result)
        assert "error" in parsed


# ---------------------------------------------------------------------------
# location_get_timezone
# ---------------------------------------------------------------------------

class TestLocationGetTimezone:
    @patch("tools.location_services._detect_location")
    def test_get_timezone_with_precise(self, mock_detect):
        mock_detect.return_value = {
            "timezone": "America/Los_Angeles",
            "location_method": "CoreLocation (precise GPS)",
        }
        result = location_module.location_get_timezone({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["timezone"] == "America/Los_Angeles"

    @patch("tools.location_services._detect_location")
    def test_get_timezone_from_ip_fallback(self, mock_detect):
        mock_detect.return_value = {
            "timezone": "",
            "ip_address": "1.2.3.4",
            "location_method": "IP geolocation",
        }
        with patch.object(location_module, "_get_ip_location") as mock_ip_loc:
            mock_ip_loc.return_value = {"timezone": "America/New_York"}
            result = location_module.location_get_timezone({})
            parsed = json.loads(result)
            assert parsed["status"] == "ok"
            assert parsed["timezone"] == "America/New_York"

    @patch("tools.location_services._detect_location")
    def test_get_timezone_failure(self, mock_detect):
        mock_detect.return_value = {"timezone": "", "ip_address": "", "location_method": "default"}
        with patch.object(location_module, "_get_ip_location", return_value={}):
            result = location_module.location_get_timezone({})
            parsed = json.loads(result)
            assert "error" in parsed


# ---------------------------------------------------------------------------
# Test detection helpers
# ---------------------------------------------------------------------------

class TestPreciseLocationDetection:
    def test_macos_returns_none_when_cli_missing(self):
        with patch("tools.location_services.shutil.which", return_value=None):
            coords, _, tz = location_module._get_precise_location_macos()
            assert coords is None

    @patch("tools.location_services._run_command")
    @patch("tools.location_services.shutil.which", return_value="/usr/local/bin/CoreLocationCLI")
    def test_macos_parses_output(self, _mock_which, mock_cmd):
        mock_cmd.return_value = "47.6062,-122.3321|America/Los_Angeles"
        coords, _, tz = location_module._get_precise_location_macos()
        assert coords == "47.6062,-122.3321"
        assert tz == "America/Los_Angeles"

    @patch("tools.location_services._run_command", return_value="invalid output")
    @patch("tools.location_services.shutil.which", return_value="/usr/local/bin/CoreLocationCLI")
    def test_macos_invalid_output(self, _mock_which, _mock_cmd):
        coords, _, tz = location_module._get_precise_location_macos()
        assert coords is None

    def test_windows_returns_none_on_fail(self):
        with patch("tools.location_services._run_command", return_value=""):
            coords, _, tz = location_module._get_precise_location_windows()
            assert coords is None

    def test_linux_returns_none_when_no_tools(self):
        with patch("tools.location_services.shutil.which", return_value=None):
            coords, _, tz = location_module._get_precise_location_linux()
            assert coords is None

    @patch("tools.location_services._run_command")
    @patch("tools.location_services.shutil.which", return_value="/usr/bin/whereami")
    def test_linux_with_whereami(self, _mock_which, mock_cmd):
        mock_cmd.return_value = "47.6,-122.3"
        coords, _, tz = location_module._get_precise_location_linux()
        assert coords == "47.6,-122.3"
