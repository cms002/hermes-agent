"""Tests for the weather_in tool module."""

import json
import pytest
from unittest.mock import patch, MagicMock

import tools.weather_in as weather_module


class TestWeatherURLBuilder:
    """Tests for the _build_url helper function."""

    def test_build_url_simple_location(self):
        url = weather_module._build_url("London", fmt="j1")
        assert url == "https://wttr.in/London?format=j1"

    def test_build_url_empty_location(self):
        url = weather_module._build_url("", fmt="j1")
        assert url == "https://wttr.in?format=j1"

    def test_build_url_with_lang_and_unit(self):
        url = weather_module._build_url("Paris", fmt="3", lang="fr", unit="m")
        assert "wttr.in/Paris" in url
        assert "format=3" in url
        assert "lang=fr" in url
        assert "&m" in url

    def test_build_url_moon_with_date(self):
        url = weather_module._build_url("Moon@2026-08-15")
        assert "Moon@2026-08-15" in url or "Moon%402026-08-15" in url

    def test_build_url_prometheus(self):
        url = weather_module._build_url("London", fmt="p1")
        assert "format=p1" in url

    def test_build_url_custom_format(self):
        url = weather_module._build_url("London", fmt="%l: %c %t")
        assert "format=" in url


class TestWeatherCurrent:
    """Tests for the weather_current tool."""

    @patch("tools.weather_in._fetch_weather")
    def test_weather_current_json_success(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": json.dumps({
                "current_condition": [{
                    "temp_C": "24",
                    "temp_F": "75",
                    "weatherDesc": [{"value": "Sunny"}],
                    "humidity": "31",
                }],
                "nearest_area": [{"areaName": [{"value": "London"}]}],
            }),
            "content_type": "application/json",
            "url": "https://wttr.in/London?format=j1",
        }

        result = weather_module.weather_current({"location": "London", "format": "j1"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["data"]["current_condition"][0]["temp_C"] == "24"

    @patch("tools.weather_in._fetch_weather")
    def test_weather_current_error(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "error",
            "error": "Network error: connection refused",
        }

        result = weather_module.weather_current({"location": "London", "format": "j1"})
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Network error" in parsed["error"]

    @patch("tools.weather_in._fetch_weather")
    def test_weather_current_text_mode(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": "Weather report: London\n\nSunny\n24°C",
            "content_type": "text/plain",
            "url": "https://wttr.in/London",
        }

        result = weather_module.weather_current({"location": "London", "format": ""})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "London" in parsed["content"]
        assert "24°C" in parsed["content"]


class TestWeatherOneline:
    """Tests for the weather_oneline tool."""

    @patch("tools.weather_in._fetch_weather")
    def test_weather_oneline_format_3(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": "London: ☀️  +24°C",
            "content_type": "text/plain",
            "url": "https://wttr.in/London?format=3",
        }

        result = weather_module.weather_oneline({"location": "London", "format": "3"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "London" in parsed["content"]
        assert "24°C" in parsed["content"]

    @patch("tools.weather_in._fetch_weather")
    def test_weather_oneline_custom_format(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": "London: Sunny 24°C",
            "content_type": "text/plain",
            "url": "https://wttr.in/London?format=%l:+%c+%t",
        }

        result = weather_module.weather_oneline({
            "location": "London",
            "format": "%l: %c %t"
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "London" in parsed["content"]


class TestWeatherMoon:
    """Tests for the weather_moon tool."""

    @patch("tools.weather_in._fetch_weather")
    def test_weather_moon_with_date(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": "Moon phase: Full Moon",
            "content_type": "text/plain",
            "url": "https://wttr.in/Moon@2026-08-15",
        }

        result = weather_module.weather_moon({"date": "2026-08-15"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "2026-08-15" in parsed["location"]

    @patch("tools.weather_in._fetch_weather")
    def test_weather_moon_current(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": "Moon phase: Waxing Gibbous",
            "content_type": "text/plain",
            "url": "https://wttr.in/Moon",
        }

        result = weather_module.weather_moon({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["location"] == "Moon"
        assert parsed["date"] == "current date"


class TestWeatherPrometheus:
    """Tests for the weather_prometheus tool."""

    @patch("tools.weather_in._fetch_weather")
    def test_weather_prometheus_success(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": '# HELP temperature_celsius Temperature in Celsius\ntemperature_celsius{forecast="current"} 24\n',
            "content_type": "text/plain",
            "url": "https://wttr.in/London?format=p1",
        }

        result = weather_module.weather_prometheus({"location": "London"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "temperature_celsius" in parsed["content"]
        assert parsed["format"] == "prometheus"


class TestWeatherFetch:
    """Tests for the _fetch_weather helper."""

    @patch("tools.weather_in.httpx.Client")
    def test_fetch_weather_success(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b'{"test": "data"}'
        mock_response.url = "https://wttr.in/London?format=j1"
        mock_response.text = '{"test": "data"}'
        mock_response.reason_phrase = "OK"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = weather_module._fetch_weather("https://wttr.in/London?format=j1")
        assert result["status"] == "ok"
        assert "test" in result["content"]

    @patch("tools.weather_in.httpx.Client")
    def test_fetch_weather_http_error(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.reason_phrase = "Not Found"
        mock_response.url = "https://wttr.in/NonExistentPlace"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = weather_module._fetch_weather("https://wttr.in/NonExistentPlace")
        assert result["status"] == "error"
        assert "404" in result["error"]


class TestCheckRequirements:
    """Tests for the check_weather_requirements function."""

    @patch("tools.weather_in.httpx.Client")
    def test_check_requirements_online(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        assert weather_module.check_weather_requirements() is True

    @patch("tools.weather_in.httpx.Client")
    def test_check_requirements_offline(self, mock_client_cls):
        import httpx
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("Connection failed")
        mock_client_cls.return_value = mock_client

        assert weather_module.check_weather_requirements() is False


class TestUnitSystemPreference:
    """Tests for the unit_system preference and _resolve_unit_params helper."""

    def test_resolve_unit_params_both(self):
        assert weather_module._resolve_unit_params("both") == ""

    def test_resolve_unit_params_celsius(self):
        assert weather_module._resolve_unit_params("c") == "m"

    def test_resolve_unit_params_fahrenheit(self):
        assert weather_module._resolve_unit_params("f") == "u"

    def test_resolve_unit_params_scientific(self):
        assert weather_module._resolve_unit_params("s") == "s"

    def test_resolve_unit_params_explicit_unit_overrides(self):
        assert weather_module._resolve_unit_params("both", unit="m") == "m"
        assert weather_module._resolve_unit_params("c", unit="u") == "u"

    def test_resolve_unit_params_empty_string(self):
        assert weather_module._resolve_unit_params("") == ""


class TestJsonUnitFilter:
    """Tests for the _filter_json_units function."""

    def test_filter_json_units_both_keeps_all(self):
        data = {"temp_C": "24", "temp_F": "75", "FeelsLikeC": "24", "FeelsLikeF": "75"}
        result = weather_module._filter_json_units(data, "both")
        assert result == data

    def test_filter_json_units_celsius_removes_fahrenheit(self):
        data = {"temp_C": "24", "temp_F": "75", "FeelsLikeC": "24", "FeelsLikeF": "75"}
        result = weather_module._filter_json_units(data, "c")
        assert "temp_C" in result
        assert "FeelsLikeC" in result
        assert "temp_F" not in result
        assert "FeelsLikeF" not in result

    def test_filter_json_units_fahrenheit_removes_celsius(self):
        data = {"temp_C": "24", "temp_F": "75", "FeelsLikeC": "24", "FeelsLikeF": "75"}
        result = weather_module._filter_json_units(data, "f")
        assert "temp_F" in result
        assert "FeelsLikeF" in result
        assert "temp_C" not in result
        assert "FeelsLikeC" not in result

    def test_filter_json_units_nested_dict(self):
        data = {
            "current_condition": [{
                "temp_C": "24",
                "temp_F": "75",
                "weatherDesc": [{"value": "Sunny"}]
            }]
        }
        result = weather_module._filter_json_units(data, "c")
        assert result["current_condition"][0]["temp_C"] == "24"
        assert "temp_F" not in result["current_condition"][0]
        assert result["current_condition"][0]["weatherDesc"][0]["value"] == "Sunny"

    def test_filter_json_units_preserves_non_temp_fields(self):
        data = {"temp_C": "24", "humidity": "31", "windspeedKmph": "15"}
        result = weather_module._filter_json_units(data, "c")
        assert result["temp_C"] == "24"
        assert result["humidity"] == "31"
        assert result["windspeedKmph"] == "15"

    def test_filter_json_units_in_forecast_data(self):
        data = {
            "weather": [{
                "date": "2026-08-05",
                "maxtempC": "30",
                "maxtempF": "86",
                "mintempC": "20",
                "mintempF": "68",
            }]
        }
        result = weather_module._filter_json_units(data, "f")
        assert result["weather"][0]["maxtempF"] == "86"
        assert result["weather"][0]["mintempF"] == "68"
        assert "maxtempC" not in result["weather"][0]
        assert "mintempC" not in result["weather"][0]


class TestWeatherCurrentWithUnitSystem:
    """Tests for weather_current with unit_system parameter."""

    @patch("tools.weather_in._fetch_weather")
    def test_weather_current_celsius_json(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": json.dumps({
                "current_condition": [{
                    "temp_C": "24",
                    "temp_F": "75",
                    "FeelsLikeC": "24",
                    "FeelsLikeF": "75",
                    "humidity": "31",
                }]
            }),
            "content_type": "application/json",
            "url": "https://wttr.in/London?format=j1&u=m",
        }

        result = weather_module.weather_current({
            "location": "London",
            "format": "j1",
            "unit_system": "c"
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        current = parsed["data"]["current_condition"][0]
        assert current["temp_C"] == "24"
        assert "temp_F" not in current
        assert current["FeelsLikeC"] == "24"
        assert "FeelsLikeF" not in current
        assert current["humidity"] == "31"  # Non-temp fields preserved

    @patch("tools.weather_in._fetch_weather")
    def test_weather_current_fahrenheit_json(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": json.dumps({
                "current_condition": [{
                    "temp_C": "24",
                    "temp_F": "75",
                    "FeelsLikeC": "24",
                    "FeelsLikeF": "75",
                    "humidity": "31",
                }]
            }),
            "content_type": "application/json",
            "url": "https://wttr.in/London?format=j1&u=u",
        }

        result = weather_module.weather_current({
            "location": "London",
            "format": "j1",
            "unit_system": "f"
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        current = parsed["data"]["current_condition"][0]
        assert current["temp_F"] == "75"
        assert "temp_C" not in current
        assert current["FeelsLikeF"] == "75"
        assert "FeelsLikeC" not in current

    @patch("tools.weather_in._fetch_weather")
    def test_weather_current_both_json(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": json.dumps({
                "current_condition": [{
                    "temp_C": "24",
                    "temp_F": "75",
                }]
            }),
            "content_type": "application/json",
            "url": "https://wttr.in/London?format=j1",
        }

        result = weather_module.weather_current({
            "location": "London",
            "format": "j1",
            "unit_system": "both"
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        current = parsed["data"]["current_condition"][0]
        assert current["temp_C"] == "24"
        assert current["temp_F"] == "75"


class TestWeatherOnelineWithUnitSystem:
    """Tests for weather_oneline with unit_system parameter."""

    @patch("tools.weather_in._fetch_weather")
    def test_weather_oneline_celsius(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": "London: ☀️  +24°C",
            "content_type": "text/plain",
            "url": "https://wttr.in/London?format=3&u=m",
        }

        result = weather_module.weather_oneline({
            "location": "London",
            "format": "3",
            "unit_system": "c"
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["unit_system"] == "c"
        assert "24°C" in parsed["content"]

    @patch("tools.weather_in._fetch_weather")
    def test_weather_oneline_fahrenheit(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "ok",
            "content": "London: ☀️  +75°F",
            "content_type": "text/plain",
            "url": "https://wttr.in/London?format=3&u=u",
        }

        result = weather_module.weather_oneline({
            "location": "London",
            "format": "3",
            "unit_system": "f"
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["unit_system"] == "f"
        assert "75°F" in parsed["content"]
