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


class TestPersistentUnitSystem:
    """Tests for the persistent unit_system preference."""

    def test_resolve_unit_system_with_explicit_value(self):
        """Explicit unit_system parameter takes priority over persistent config."""
        assert weather_module._resolve_unit_system("c") == "c"
        assert weather_module._resolve_unit_system("f") == "f"
        assert weather_module._resolve_unit_system("both") == "both"

    def test_resolve_unit_system_none_falls_back_to_config(self):
        """When unit_system is None, fall back to persistent config."""
        with patch("tools.weather_in._get_persistent_unit_system", return_value="f"):
            assert weather_module._resolve_unit_system(None) == "f"

    def test_resolve_unit_system_empty_falls_back_to_config(self):
        """When unit_system is empty string, fall back to persistent config."""
        with patch("tools.weather_in._get_persistent_unit_system", return_value="c"):
            assert weather_module._resolve_unit_system("") == "c"

    def test_get_persistent_unit_system_default(self):
        """Default persistent unit_system is 'both' when config is unavailable."""
        with patch("builtins.__import__", side_effect=ImportError("No module")):
            assert weather_module._get_persistent_unit_system() == "both"

    def test_resolve_unit_system_explicit_overrides_persistent(self):
        """Explicit unit_system overrides persistent config."""
        with patch("tools.weather_in._get_persistent_unit_system", return_value="f"):
            # Explicit 'c' should override persistent 'f'
            assert weather_module._resolve_unit_system("c") == "c"

    @patch("tools.weather_in._get_persistent_unit_system", return_value="c")
    @patch("tools.weather_in._fetch_weather")
    def test_weather_current_uses_persistent_config(self, mock_fetch, _mock_persistent):
        """weather_current uses persistent config when no unit_system provided."""
        mock_fetch.return_value = {
            "status": "ok",
            "content": json.dumps({
                "current_condition": [{
                    "temp_C": "24",
                    "temp_F": "75",
                    "humidity": "31",
                }]
            }),
            "content_type": "application/json",
            "url": "https://wttr.in/London?format=j1&m",
        }

        result = weather_module.weather_current({
            "location": "London",
            "format": "j1",
            # unit_system NOT provided - should use persistent config ("c")
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        current = parsed["data"]["current_condition"][0]
        assert current["temp_C"] == "24"
        assert "temp_F" not in current  # Should be filtered out

    @patch("tools.weather_in._get_persistent_unit_system", return_value="f")
    @patch("tools.weather_in._fetch_weather")
    def test_weather_current_per_request_overrides_persistent(self, mock_fetch, _mock_persistent):
        """weather_current per-request unit_system overrides persistent config."""
        mock_fetch.return_value = {
            "status": "ok",
            "content": json.dumps({
                "current_condition": [{
                    "temp_C": "24",
                    "temp_F": "75",
                    "humidity": "31",
                }]
            }),
            "content_type": "application/json",
            "url": "https://wttr.in/London?format=j1&m",
        }

        result = weather_module.weather_current({
            "location": "London",
            "format": "j1",
            "unit_system": "f",
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        current = parsed["data"]["current_condition"][0]
        assert current["temp_F"] == "75"
        assert "temp_C" not in current


class TestWeatherSetPreference:
    """Tests for the set_weather_preference tool."""

    @patch("tools.weather_in._get_persistent_unit_system", return_value="c")
    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_set_preference_celsius(self, mock_load, mock_save, _mock_persistent):
        mock_load.return_value = {"weather": {"unit_system": "f"}}
        result = weather_module.set_weather_preference({"unit_system": "c"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["saved"]["unit_system"] == "c"
        mock_save.assert_called_once()

    @patch("tools.weather_in._get_persistent_unit_system", return_value="f")
    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_set_preference_fahrenheit(self, mock_load, mock_save, _mock_persistent):
        mock_load.return_value = {"weather": {"unit_system": "c"}}
        result = weather_module.set_weather_preference({"unit_system": "f"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["saved"]["unit_system"] == "f"
        mock_save.assert_called_once()

    def test_set_preference_invalid(self):
        result = weather_module.set_weather_preference({"unit_system": "invalid"})
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "unit_system must be one of" in parsed["error"]

    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_set_preference_graphics(self, mock_load, mock_save):
        mock_load.return_value = {"weather": {}}
        result = weather_module.set_weather_preference({"graphics": "s"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["saved"]["graphics"] == "s"
        mock_save.assert_called_once()

    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_set_preference_detail(self, mock_load, mock_save):
        mock_load.return_value = {"weather": {}}
        result = weather_module.set_weather_preference({"detail": "l"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["saved"]["detail"] == "l"
        mock_save.assert_called_once()

    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_set_preference_all_three(self, mock_load, mock_save):
        mock_load.return_value = {"weather": {}}
        result = weather_module.set_weather_preference({"unit_system": "f", "graphics": "l", "detail": "s"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["saved"]["unit_system"] == "f"
        assert parsed["saved"]["graphics"] == "l"
        assert parsed["saved"]["detail"] == "s"
        mock_save.assert_called_once()

    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_set_preference_invalid_graphics(self, mock_load, mock_save):
        mock_load.return_value = {"weather": {}}
        result = weather_module.set_weather_preference({"graphics": "huge"})
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "graphics must be one of" in parsed["error"]

    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_set_preference_invalid_detail(self, mock_load, mock_save):
        mock_load.return_value = {"weather": {}}
        result = weather_module.set_weather_preference({"detail": "tiny"})
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "detail must be one of" in parsed["error"]


class TestWeatherGetPreference:
    """Tests for the get_weather_preference tool."""

    def test_get_preference_default(self):
        with patch("tools.weather_in._get_persistent_unit_system", return_value="both"), \
             patch("tools.weather_in._get_persistent_graphics", return_value="m"), \
             patch("tools.weather_in._get_persistent_detail", return_value="m"):
            result = weather_module.get_weather_preference({})
            parsed = json.loads(result)
            assert parsed["status"] == "ok"
            assert parsed["preferences"]["unit_system"] == "both"
            assert parsed["preferences"]["graphics"] == "m"
            assert parsed["preferences"]["detail"] == "m"

    @patch("tools.weather_in._get_persistent_unit_system", return_value="c")
    @patch("tools.weather_in._get_persistent_graphics", return_value="l")
    @patch("tools.weather_in._get_persistent_detail", return_value="s")
    def test_get_preference_custom(self, _mock_detail, _mock_graphics, _mock_unit):
        result = weather_module.get_weather_preference({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["preferences"]["unit_system"] == "c"
        assert parsed["preferences"]["graphics"] == "l"
        assert parsed["preferences"]["detail"] == "s"


class TestGraphicsDetailPreferences:
    """Tests for the graphics and detail preference resolution functions."""

    @patch("tools.weather_in._get_persistent_graphics", return_value="m")
    def test_resolve_graphics_explicit(self, _mock):
        assert weather_module._resolve_graphics("s") == "s"
        assert weather_module._resolve_graphics("l") == "l"

    @patch("tools.weather_in._get_persistent_graphics", return_value="m")
    def test_resolve_graphics_explicit_overrides_persistent(self, _mock):
        # Persistent is 'm', explicit 'l' should win
        assert weather_module._resolve_graphics("l") == "l"

    @patch("tools.weather_in._get_persistent_graphics", return_value="m")
    def test_resolve_graphics_none_falls_back(self, _mock):
        assert weather_module._resolve_graphics(None) == "m"
        assert weather_module._resolve_graphics("") == "m"

    @patch("tools.weather_in._get_persistent_graphics", return_value="l")
    def test_resolve_graphics_persistent_used(self, _mock):
        assert weather_module._resolve_graphics(None) == "l"

    @patch("tools.weather_in._get_persistent_graphics", return_value="m")
    def test_resolve_graphics_normalizes_long_names(self, _mock):
        assert weather_module._resolve_graphics("small") == "s"
        assert weather_module._resolve_graphics("medium") == "m"
        assert weather_module._resolve_graphics("large") == "l"

    @patch("tools.weather_in._get_persistent_detail", return_value="m")
    def test_resolve_detail_explicit(self, _mock):
        assert weather_module._resolve_detail("s") == "s"
        assert weather_module._resolve_detail("l") == "l"

    @patch("tools.weather_in._get_persistent_detail", return_value="s")
    def test_resolve_detail_persistent_used(self, _mock):
        assert weather_module._resolve_detail(None) == "s"

    @patch("tools.weather_in._get_persistent_detail", return_value="m")
    def test_resolve_detail_normalizes_long_names(self, _mock):
        assert weather_module._resolve_detail("small") == "s"
        assert weather_module._resolve_detail("medium") == "m"
        assert weather_module._resolve_detail("large") == "l"

    def test_resolve_text_format(self):
        # detail 's' -> format "1" (one-line)
        assert weather_module._resolve_text_format(None, "s") == "1"
        # detail 'm' -> None (default text output)
        assert weather_module._resolve_text_format(None, "m") is None
        # detail 'l' -> None (default text output with full ASCII graphics)
        # 'l' detail uses the default text format for richest visual output
        assert weather_module._resolve_text_format(None, "l") is None
        # Explicit format takes priority over detail
        assert weather_module._resolve_text_format("j2", "s") == "j2"
        assert weather_module._resolve_text_format("3", "l") == "3"

    def test_resolve_text_options(self):
        # Small graphics: plain text + standard glyphs
        flags = weather_module._resolve_text_options("s")
        assert "T" in flags
        assert "d" in flags
        # Medium graphics: no extra flags
        assert weather_module._resolve_text_options("m") == ""
        # Large graphics: no extra flags
        assert weather_module._resolve_text_options("l") == ""


class TestWeatherSetup:
    """Tests for the weather_setup tool."""

    @patch("tools.weather_in._get_persistent_unit_system", return_value="both")
    @patch("tools.weather_in._get_persistent_graphics", return_value="m")
    @patch("tools.weather_in._get_persistent_detail", return_value="m")
    @patch("hermes_cli.config.cfg_get", return_value="not_set")
    @patch("hermes_cli.config.load_config_readonly")
    def test_setup_no_prefs_shows_prompt(self, mock_load, mock_cfg_get, _d, _g, _u):
        """When no preferences are set, setup returns a prompt message."""
        mock_load.return_value = {}
        result = weather_module.weather_setup({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["setup_required"] is True
        assert "First time using weather tools" in parsed["message"]
        assert "current_defaults" in parsed

    @patch("tools.weather_in._get_persistent_unit_system", return_value="f")
    @patch("tools.weather_in._get_persistent_graphics", return_value="s")
    @patch("tools.weather_in._get_persistent_detail", return_value="l")
    @patch("hermes_cli.config.cfg_get")
    @patch("hermes_cli.config.load_config_readonly")
    def test_setup_with_prefs_shows_current(self, mock_load, mock_cfg_get, _d, _g, _u):
        """When preferences are already set, setup shows current values."""
        mock_load.return_value = {"weather": {"unit_system": "f", "graphics": "s", "detail": "l"}}
        mock_cfg_get.return_value = "f"  # weather section exists
        # cfg_get is called with config, "weather" — need to handle the default
        def cfg_get_side_effect(cfg, key, default=None):
            if key == "weather":
                return {"unit_system": "f", "graphics": "s", "detail": "l"}
            return default
        mock_cfg_get.side_effect = cfg_get_side_effect

        result = weather_module.weather_setup({})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["setup_required"] is False
        assert parsed["current_preferences"]["unit_system"] == "f"
        assert parsed["current_preferences"]["graphics"] == "s"
        assert parsed["current_preferences"]["detail"] == "l"

    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_setup_with_params_saves_preferences(self, mock_load, mock_save):
        """When params are provided, setup saves them as preferences."""
        mock_load.return_value = {"weather": {}}
        result = weather_module.weather_setup({"unit_system": "c", "graphics": "m", "detail": "l"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "saved" in parsed
        assert parsed["saved"]["unit_system"] == "c"
        assert parsed["saved"]["graphics"] == "m"
        assert parsed["saved"]["detail"] == "l"

    @patch("hermes_cli.config.save_config")
    @patch("hermes_cli.config.load_config")
    def test_setup_with_partial_params_saves_some(self, mock_load, mock_save):
        """When only some params are provided, only those are saved."""
        mock_load.return_value = {"weather": {}}
        result = weather_module.weather_setup({"unit_system": "f"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["saved"]["unit_system"] == "f"
        assert "graphics" not in parsed["saved"]
        assert "detail" not in parsed["saved"]
