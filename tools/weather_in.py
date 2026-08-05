"""Weather information tool — interface with the wttr.in weather service.

Provides comprehensive weather data including current conditions, forecasts,
moon phases, and weather metrics through the wttr.in API. The tool supports
multiple output formats (JSON, plain text, one-line, Prometheus metrics)
and location resolution by city name, coordinates, airport code, or IP/domain.

wttr.in is a free, rate-limited public service. No API key is required for
basic usage, but high-volume queries should use a self-hosted instance.

Available tools:
- weather_current: Get current weather for a location (JSON or formatted output)
- weather_forecast: Get weather forecast for a location (text or JSON)
- weather_oneline: Get one-line weather summary using format codes 1-4 or custom format
- weather_moon: Get moon phase information for a date
- weather_prometheus: Get weather data as Prometheus metrics
- weather_help: Get help information about wttr.in query syntax

Usage examples:
    # Current weather in JSON format
    result = weather_current(location="London", format="j1")

    # One-line weather summary
    result = weather_oneline(location="London", format="3")

    # Custom one-line format
    result = weather_oneline(location="Paris", format="%l: %c %t")

    # Prometheus metrics
    result = weather_prometheus(location="London")

    # Current weather with Celsius preference
    result = weather_current(location="London", format="j1", unit_system="c")

    # Forecast with Fahrenheit preference
    result = weather_forecast(location="Mountain View, CA", unit_system="f")
"""

import json
import logging
import os
import urllib.parse
from typing import Dict, Any, Optional, List

import httpx

logger = logging.getLogger(__name__)

# wttr.in base URL. Can be overridden via HERMES_WTTR_BASE_URL env var
# to point at wttr.is or a self-hosted instance.
_WTTR_BASE_URL = os.environ.get("HERMES_WTTR_BASE_URL", "https://wttr.in")

# Default timeout for wttr.in requests
_WTTR_TIMEOUT_SECONDS = 30

# Default unit system preference: "c" (Celsius/metric), "f" (Fahrenheit/USCS), or "both"
_DEFAULT_UNIT_SYSTEM = "both"


def _fetch_weather(url: str) -> Dict[str, Any]:
    """Fetch weather data from wttr.in.

    Args:
        url: The fully constructed wttr.in URL.

    Returns:
        Dictionary with either:
        - {"status": "ok", "content": <decoded text>, "raw": <raw bytes if binary>,
           "content_type": <content-type>, "url": <final url>}
        - {"status": "error", "error": <error message>}
    """
    headers = {
        "User-Agent": "curl/7.68.0",
        "Accept": "*/*",
    }

    try:
        with httpx.Client(timeout=_WTTR_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
    except httpx.TimeoutException:
        return {"status": "error", "error": f"Request to wttr.in timed out after {_WTTR_TIMEOUT_SECONDS}s"}
    except httpx.RequestError as e:
        return {"status": "error", "error": f"Network error fetching weather data: {e}"}
    except Exception as e:
        return {"status": "error", "error": f"Unexpected error: {e}"}

    if response.status_code != 200:
        body = response.text[:500] if response.text else response.reason_phrase
        return {
            "status": "error",
            "error": f"wttr.in returned HTTP {response.status_code}: {body}"
        }

    content_type = response.headers.get("content-type", "")
    raw_content = response.content

    # Determine if content is text or binary (PNG images)
    is_binary = "image" in content_type or url.endswith(".png")

    if is_binary:
        return {
            "status": "ok",
            "content": f"[PNG image data: {len(raw_content)} bytes]",
            "raw": list(raw_content),  # Convert bytes to list for JSON serialization
            "content_type": content_type,
            "url": str(response.url),
        }
    else:
        text = raw_content.decode("utf-8", errors="replace")
        return {
            "status": "ok",
            "content": text,
            "content_type": content_type,
            "url": str(response.url),
        }


def _build_url(location: str, fmt: Optional[str] = None, lang: Optional[str] = None,
               unit: Optional[str] = None, extra_params: Optional[str] = None) -> str:
    """Build a wttr.in URL with the given parameters.

    Args:
        location: Location name, coordinate (lat,lon), airport code, @domain,
                  "Moon" or "Moon@YYYY-MM-DD" for lunar data, or empty for auto-detect.
        fmt: Output format. For JSON use "j1" or "j2". For one-line use "1"-"4"
             or custom %-notation string. For Prometheus use "p1". For text use None.
        lang: Language code (e.g., "fr", "de", "es").
        unit: Unit system as a bare wttr.in flag: "m" for metric, "M" for metric
              with m/s, "u" for USCS, "s" for scientific. Empty for default.
        extra_params: Additional query parameters as a string.

    Returns:
        Fully constructed wttr.in URL.
    """
    parts: list = []
    if fmt:
        parts.append(f"format={urllib.parse.quote(fmt, safe='')}")
    if lang:
        parts.append(f"lang={urllib.parse.quote(lang, safe='')}")
    if unit:
        # wttr.in unit params are bare flags, not key=value (e.g., ?m not ?u=m)
        parts.append(unit)
    if extra_params:
        parts.append(extra_params)

    query_string = "&".join(parts)

    if location:
        url = f"{_WTTR_BASE_URL}/{urllib.parse.quote(location, safe='@,-')}"
    else:
        url = _WTTR_BASE_URL

    if query_string:
        url = f"{url}?{query_string}"

    return url


def _resolve_unit_params(unit_system: str, unit: Optional[str] = None) -> str:
    """Resolve unit system preference and explicit unit parameter to wttr.in unit.

    Args:
        unit_system: "c" for metric/Celsius, "f" for USCS/Fahrenheit, "both" for default.
        unit: Explicit unit override (takes priority over unit_system).

    Returns:
        wttr.in unit parameter value ("m", "M", "u", "s") or empty string.
    """
    if unit:
        return unit
    if unit_system == "c":
        return "m"  # metric
    elif unit_system == "f":
        return "u"  # USCS
    elif unit_system == "s":
        return "s"  # scientific
    return ""  # wttr.in default


def _filter_json_units(data: Dict[str, Any], unit_system: str) -> Dict[str, Any]:
    """Filter JSON weather data to remove unwanted temperature units.

    When unit_system is "c", removes all *F fields.
    When unit_system is "f", removes all *C fields.
    When unit_system is "both" or anything else, leaves all fields intact.

    Args:
        data: Parsed JSON weather data from wttr.in.
        unit_system: "c", "f", or "both".

    Returns:
        Filtered data dictionary.
    """
    if unit_system == "both":
        return data

    def _filter_dict(d: Any) -> Any:
        if isinstance(d, dict):
            return {k: _filter_dict(v) for k, v in d.items()
                    if not _should_remove_key(k, unit_system)}
        elif isinstance(d, list):
            return [_filter_dict(item) for item in d]
        else:
            return d

    return _filter_dict(data)


def _should_remove_key(key: str, unit_system: str) -> bool:
    """Check if a key should be removed based on unit system preference."""
    if unit_system == "c":
        # Remove Fahrenheit fields like temp_F, FeelsLikeF, etc.
        return key.endswith("F") and not key.endswith("Fahrenheit")
    elif unit_system == "f":
        # Remove Celsius fields like temp_C, FeelsLikeC, etc.
        return key.endswith("C") and not key.endswith("Celsius")
    return False


def weather_current(args, **kwargs) -> str:
    """Get current weather conditions for a location.

    Returns comprehensive weather data including temperature, humidity, wind,
    precipitation, and forecast data in JSON format.
    """
    location = args.get("location", "")
    fmt = args.get("format", "j1")
    lang = args.get("language", "") or None
    unit_system = args.get("unit_system", _DEFAULT_UNIT_SYSTEM)
    explicit_unit = args.get("unit", "") or None
    extra_params = args.get("extra_params", "") or None

    wttr_unit = _resolve_unit_params(unit_system, explicit_unit)

    url = _build_url(location, fmt=fmt, lang=lang, unit=wttr_unit or None, extra_params=extra_params)
    result = _fetch_weather(url)

    if result["status"] == "error":
        return json.dumps(result)

    # If JSON format, parse and return structured data
    if fmt in ("j1", "j2"):
        try:
            parsed = json.loads(result["content"])
            # Filter units if unit_system is not "both"
            if unit_system != "both":
                parsed = _filter_json_units(parsed, unit_system)
            return json.dumps({"status": "ok", "data": parsed, "url": result["url"]})
        except json.JSONDecodeError as e:
            return json.dumps({
                "status": "error",
                "error": f"Failed to parse JSON response: {e}",
                "raw_content": result["content"][:2000]
            })

    # Return raw text content
    return json.dumps({
        "status": "ok",
        "content": result["content"],
        "format": fmt,
        "location": location or "auto-detected (by IP)",
        "unit_system": unit_system,
        "url": result["url"]
    })


def weather_forecast(args, **kwargs) -> str:
    """Get weather forecast for a location.

    Returns forecast data either as formatted text (default) or as JSON
    when format='j1' or 'j2' is specified.
    """
    location = args.get("location", "")
    fmt = args.get("format", "") or None
    lang = args.get("language", "") or None
    unit_system = args.get("unit_system", _DEFAULT_UNIT_SYSTEM)
    explicit_unit = args.get("unit", "") or None
    extra_params = args.get("extra_params", "") or None

    # Build extra params for forecast days if requested
    days = args.get("days")
    if days is not None:
        if extra_params:
            extra_params = f"{extra_params}&days={days}"
        else:
            extra_params = f"days={days}"

    wttr_unit = _resolve_unit_params(unit_system, explicit_unit)

    url = _build_url(location, fmt=fmt, lang=lang, unit=wttr_unit or None, extra_params=extra_params)
    result = _fetch_weather(url)

    if result["status"] == "error":
        return json.dumps(result)

    # If JSON format, parse and return structured data
    if fmt in ("j1", "j2"):
        try:
            parsed = json.loads(result["content"])
            # Filter units if unit_system is not "both"
            if unit_system != "both":
                parsed = _filter_json_units(parsed, unit_system)
            return json.dumps({"status": "ok", "data": parsed, "url": result["url"]})
        except json.JSONDecodeError as e:
            return json.dumps({
                "status": "error",
                "error": f"Failed to parse JSON response: {e}",
                "raw_content": result["content"][:2000]
            })

    return json.dumps({
        "status": "ok",
        "content": result["content"],
        "location": location or "auto-detected (by IP)",
        "unit_system": unit_system,
        "url": result["url"]
    })


def weather_oneline(args, **kwargs) -> str:
    """Get one-line weather output for a location.

    Uses wttr.in's format parameter. Preconfigured formats 1-4 or custom
    %-notation strings. Supports multiple locations separated by colon.
    The unit_system parameter controls which unit is displayed: 'c' for
    metric (Celsius), 'f' for USCS (Fahrenheit), 'both' for default.
    """
    location = args.get("location", "")
    fmt = args.get("format", "3")
    lang = args.get("language", "") or None
    unit_system = args.get("unit_system", _DEFAULT_UNIT_SYSTEM)
    explicit_unit = args.get("unit", "") or None

    wttr_unit = _resolve_unit_params(unit_system, explicit_unit)

    url = _build_url(location, fmt=fmt, lang=lang, unit=wttr_unit or None)
    result = _fetch_weather(url)

    if result["status"] == "error":
        return json.dumps(result)

    return json.dumps({
        "status": "ok",
        "content": result["content"].strip(),
        "format": fmt,
        "location": location or "auto-detected (by IP)",
        "unit_system": unit_system,
        "url": result["url"]
    })


def weather_moon(args, **kwargs) -> str:
    """Get moon phase information for a specific date.

    Queries wttr.in's Moon endpoint. Date format is YYYY-MM-DD.
    """
    date = args.get("date", "")
    lang = args.get("language", "") or None
    fmt = args.get("format", "") or None
    unit_system = args.get("unit_system", _DEFAULT_UNIT_SYSTEM)
    explicit_unit = args.get("unit", "") or None

    if date:
        location = f"Moon@{date}"
    else:
        location = "Moon"

    wttr_unit = _resolve_unit_params(unit_system, explicit_unit)

    url = _build_url(location, fmt=fmt, lang=lang, unit=wttr_unit or None)
    result = _fetch_weather(url)

    if result["status"] == "error":
        return json.dumps(result)

    return json.dumps({
        "status": "ok",
        "content": result["content"],
        "location": location,
        "date": date or "current date",
        "unit_system": unit_system,
        "url": result["url"]
    })


def weather_prometheus(args, **kwargs) -> str:
    """Get weather data as Prometheus metrics.

    Returns Prometheus-formatted metrics including temperature, humidity,
    wind speed, pressure, and other meteorological observations.
    Prometheus output includes both Celsius and Fahrenheit metrics.
    """
    location = args.get("location", "")

    url = _build_url(location, fmt="p1")
    result = _fetch_weather(url)

    if result["status"] == "error":
        return json.dumps(result)

    return json.dumps({
        "status": "ok",
        "content": result["content"],
        "location": location or "auto-detected (by IP)",
        "format": "prometheus",
        "unit_system": "both",
        "url": result["url"]
    })


def weather_help(args, **kwargs) -> str:
    """Get help information about wttr.in query syntax.

    Returns the wttr.in help page content which documents all supported
    location formats, query parameters, and output formats.
    """
    url = f"{_WTTR_BASE_URL}/:help"

    try:
        with httpx.Client(timeout=_WTTR_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(url, headers={
                "User-Agent": "curl/7.68.0",
                "Accept": "*/*",
            })
    except Exception as e:
        return json.dumps({"status": "error", "error": f"Network error: {e}"})

    if response.status_code != 200:
        return json.dumps({"status": "error", "error": f"wttr.in help returned HTTP {response.status_code}"})

    return json.dumps({
        "status": "ok",
        "content": response.text,
        "url": url,
        "note": (
            "wttr.in supports: locations (city names, coordinates, airport codes, "
            "@domain, IP), formats (j1/j2 JSON, 1-4 one-line, custom % notation, "
            "v2/v2d/v2n data-rich, p1 Prometheus, .png for images), units "
            "(m=metric/Celsius, M=metric with m/s, u=USCS/Fahrenheit, s=scientific), "
            "and languages (lang=XX). Use wttr.is as a reliable fallback domain. "
            "Use unit_system='c' for Celsius-only or unit_system='f' for Fahrenheit-only."
        )
    })


def check_weather_requirements() -> bool:
    """Check if weather tool requirements are met.

    The wttr.in weather tool requires network access to wttr.in or an
    alternative weather service URL. No API key is required for the public
    wttr.in service.
    """
    try:
        url = f"{_WTTR_BASE_URL}/London?format=3"
        with httpx.Client(timeout=10) as client:
            response = client.get(url, headers={
                "User-Agent": "curl/7.68.0",
                "Accept": "*/*",
            })
        return response.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

WEATHER_CURRENT_SCHEMA = {
    "name": "weather_current",
    "description": (
        "Get current weather conditions for a location using the wttr.in service. "
        "Returns comprehensive weather data (temperature, humidity, wind, "
        "precipitation, forecast) in JSON format by default. Supports locations "
        "by city name, coordinates, airport code, @domain, or IP address. "
        "No API key required for the public wttr.in service. "
        "Use unit_system='c' for Celsius-only, 'f' for Fahrenheit-only, "
        "or 'both' (default) for both units."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "Location to query. City name (e.g., 'London'), coordinates "
                    "(e.g., '43.7,-79.41'), 3-letter IATA airport code (e.g., 'muc'), "
                    "@domain (e.g., '@github.com'), or leave empty to auto-detect "
                    "by IP address."
                ),
                "default": ""
            },
            "format": {
                "type": "string",
                "description": (
                    "Output format: 'j1' for full JSON (default), 'j2' for compact "
                    "JSON (no hourly data). Other formats return formatted text output."
                ),
                "default": "j1"
            },
            "language": {
                "type": "string",
                "description": "Language code for localized output (e.g., 'fr', 'de', 'es', 'ja')."
            },
            "unit_system": {
                "type": "string",
                "description": (
                    "Temperature unit preference: 'c' for metric/Celsius only, "
                    "'f' for USCS/Fahrenheit only, 'both' (default) for both units. "
                    "In JSON mode, this filters which temperature fields are returned. "
                    "In text mode, it controls the wttr.in unit parameter."
                ),
                "default": "both",
                "enum": ["c", "f", "both"]
            },
            "unit": {
                "type": "string",
                "description": "Explicit wttr.in unit parameter override ('m', 'M', 'u', 's'). Takes priority over unit_system."
            },
            "extra_params": {
                "type": "string",
                "description": "Additional raw wttr.in query parameters (e.g., 'lang=de&period=60')"
            }
        },
    }
}

WEATHER_FORECAST_SCHEMA = {
    "name": "weather_forecast",
    "description": (
        "Get weather forecast for a location using the wttr.in service. "
        "Returns either formatted text forecast (default) or JSON data "
        "(format='j1' or 'j2') with current conditions, hourly, and daily "
        "forecasts including temperature, precipitation, wind, and astronomy data. "
        "Use unit_system='c' for Celsius, 'f' for Fahrenheit, or 'both' (default)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "Location to query. City name (e.g., 'London'), coordinates "
                    "(e.g., '43.7,-79.41'), 3-letter IATA airport code (e.g., 'muc'), "
                    "@domain (e.g., '@github.com'), or leave empty to auto-detect "
                    "by IP address."
                ),
                "default": ""
            },
            "format": {
                "type": "string",
                "description": (
                    "Output format: 'j1' or 'j2' for JSON, empty for default text "
                    "forecast (ANSI terminal display)."
                ),
                "default": ""
            },
            "language": {
                "type": "string",
                "description": "Language code for localized output (e.g., 'fr', 'de', 'es', 'ja')."
            },
            "unit_system": {
                "type": "string",
                "description": (
                    "Temperature unit preference: 'c' for metric/Celsius only, "
                    "'f' for USCS/Fahrenheit only, 'both' (default) for both units. "
                    "In JSON mode, this filters which temperature fields are returned."
                ),
                "default": "both",
                "enum": ["c", "f", "both"]
            },
            "unit": {
                "type": "string",
                "description": "Explicit wttr.in unit parameter override ('m', 'M', 'u', 's'). Takes priority over unit_system."
            },
            "days": {
                "type": "integer",
                "description": "Number of forecast days to return (not always supported by wttr.in).",
                "default": None
            },
            "extra_params": {
                "type": "string",
                "description": "Additional raw wttr.in query parameters."
            }
        },
    }
}

WEATHER_ONELINE_SCHEMA = {
    "name": "weather_oneline",
    "description": (
        "Get one-line weather output for a location. Uses wttr.in's format parameter. "
        "Preconfigured formats: 1 (minimal), 2 (condition + temp), 3 (location + condition), "
        "4 (location + all details). Custom formats use %-notation: %c (condition), "
        "%t (temp), %h (humidity), %w (wind), %l (location), %m (moon phase), "
        "%p (precipitation), %P (pressure), %u (UV index), %e (dew point), "
        "%S (sunrise), %s (sunset), %T (current time), %Z (timezone), etc. "
        "Multiple locations can be passed colon-separated for batch queries. "
        "Use unit_system='c' for Celsius, 'f' for Fahrenheit, or 'both' (default)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "Location to query. City name, coordinates, airport code, "
                    "@domain, or empty for auto-detect. Multiple locations "
                    "can be colon-separated (e.g., 'London:Paris:Berlin')."
                ),
                "default": ""
            },
            "format": {
                "type": "string",
                "description": (
                    "Output format: '1', '2', '3', '4' for preconfigured one-line formats, "
                    "or a custom %-notation string like '%l: %c %t\\n'."
                ),
                "default": "3"
            },
            "language": {
                "type": "string",
                "description": "Language code for localized output (e.g., 'fr', 'de', 'es')."
            },
            "unit_system": {
                "type": "string",
                "description": (
                    "Temperature unit preference: 'c' for Celsius, 'f' for Fahrenheit, "
                    "'both' (default) for wttr.in's default unit behavior."
                ),
                "default": "both",
                "enum": ["c", "f", "both"]
            }
        },
    }
}

WEATHER_MOON_SCHEMA = {
    "name": "weather_moon",
    "description": (
        "Get moon phase information for a specific date using wttr.in's Moon endpoint. "
        "Returns ANSI display of moon phase and illumination. Date format is YYYY-MM-DD. "
        "If no date is specified, returns current moon phase."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": (
                    "Date in YYYY-MM-DD format (e.g., '2024-12-25' for Christmas). "
                    "If omitted, returns moon phase for the current date."
                )
            },
            "language": {
                "type": "string",
                "description": "Language code for localized output."
            },
            "format": {
                "type": "string",
                "description": "Output format (e.g., 'j1' for JSON)."
            },
            "unit_system": {
                "type": "string",
                "description": "Temperature unit preference: 'c', 'f', or 'both' (default).",
                "default": "both",
                "enum": ["c", "f", "both"]
            }
        },
    }
}

WEATHER_PROMETHEUS_SCHEMA = {
    "name": "weather_prometheus",
    "description": (
        "Get weather data as Prometheus metrics from wttr.in. Returns time-series "
        "metrics including temperature (C/F), feels-like temperature, wind speed, "
        "humidity, pressure, precipitation, UV index, cloud cover, and astronomy "
        "data (sunrise/sunset, moon phase, moon illumination). Suitable for "
        "monitoring and time-series database integration. Prometheus output "
        "includes both Celsius and Fahrenheit metrics."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "Location to query. City name, coordinates, airport code, "
                    "@domain, or empty for auto-detect by IP."
                ),
                "default": ""
            }
        },
    }
}

WEATHER_HELP_SCHEMA = {
    "name": "weather_help",
    "description": (
        "Get help information about wttr.in query syntax and supported options. "
        "Returns documentation on location formats, output formats, units, "
        "languages, and available query parameters."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    }
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry

registry.register(
    name="weather_current",
    toolset="web",
    schema=WEATHER_CURRENT_SCHEMA,
    handler=weather_current,
    check_fn=check_weather_requirements,
    requires_env=[],
    emoji="🌤",
    max_result_size_chars=100_000,
)

registry.register(
    name="weather_forecast",
    toolset="web",
    schema=WEATHER_FORECAST_SCHEMA,
    handler=weather_forecast,
    check_fn=check_weather_requirements,
    requires_env=[],
    emoji="📅",
    max_result_size_chars=100_000,
)

registry.register(
    name="weather_oneline",
    toolset="web",
    schema=WEATHER_ONELINE_SCHEMA,
    handler=weather_oneline,
    check_fn=check_weather_requirements,
    requires_env=[],
    emoji="🌡",
    max_result_size_chars=10_000,
)

registry.register(
    name="weather_moon",
    toolset="web",
    schema=WEATHER_MOON_SCHEMA,
    handler=weather_moon,
    check_fn=check_weather_requirements,
    requires_env=[],
    emoji="🌙",
    max_result_size_chars=10_000,
)

registry.register(
    name="weather_prometheus",
    toolset="web",
    schema=WEATHER_PROMETHEUS_SCHEMA,
    handler=weather_prometheus,
    check_fn=check_weather_requirements,
    requires_env=[],
    emoji="📊",
    max_result_size_chars=10_000,
)

registry.register(
    name="weather_help",
    toolset="web",
    schema=WEATHER_HELP_SCHEMA,
    handler=weather_help,
    check_fn=check_weather_requirements,
    requires_env=[],
    emoji="❔",
)
