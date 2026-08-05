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
- weather_set_preference: Set a persistent unit_system preference (c/f/both)
- weather_get_preference: Get the current persistent unit_system preference

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

    # Set persistent default preferences (survive across requests):
    #   hermes config set weather.unit_system=f
    #   hermes config set weather.graphics=m
    #   hermes config set weather.detail=l
    # Then all subsequent weather calls will use those defaults.
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

# Default graphics size preference: "s" (small), "m" (medium), "l" (large)
_DEFAULT_GRAPHICS = "m"

# Default detail level preference: "s" (small), "m" (medium), "l" (large)
_DEFAULT_DETAIL = "m"


def _get_current_location() -> str:
    """Get the user's current location string for weather lookups.

    Attempts to use the location_services module (hermes-location-services
    tool) to get precise coordinates via CoreLocationCLI on macOS, or
    IP-based geolocation as a fallback. If the location module is not
    available or returns an error, falls back to empty string (which
    wttr.in interprets as auto-detect by IP).

    Returns:
        A location string suitable for wttr.in, or empty string for
        IP-based auto-detection.
    """
    try:
        # Try to import location_services module
        from tools.location_services import _get_location_cached

        location = _get_location_cached()
        if location and location.get("precise_coords"):
            # Use precise GPS coordinates for best weather accuracy
            coords = location["precise_coords"]
            return coords
        elif location and location.get("address"):
            return location["address"]
    except (ImportError, Exception) as e:
        logger.debug(f"Location services module not available or error: {e}")

    # Fallback: return empty string for wttr.in IP auto-detection
    return ""


def _get_persistent_unit_system() -> str:
    """Get the persistent default unit system from config.yaml.

    Reads the 'weather.unit_system' value from the Hermes config.
    Falls back to 'both' if not set or if config is unavailable.

    Returns:
        'c' for Celsius-only, 'f' for Fahrenheit-only, or 'both' (default).
    """
    try:
        from hermes_cli.config import cfg_get, load_config_readonly
        config = load_config_readonly()
        value = cfg_get(config, "weather", "unit_system", default="both")
        if value in ("c", "f", "both", "s"):
            return value
        return "both"
    except Exception:
        return "both"


def _resolve_unit_system(unit_system: Optional[str]) -> str:
    """Resolve the effective unit_system, falling back to persistent config.

    Args:
        unit_system: Per-request unit_system parameter ('c', 'f', 'both', 's').
                     If None or empty, falls back to persistent config default.

    Returns:
        The resolved unit_system value ('c', 'f', 'both', or 's').
    """
    if unit_system and unit_system in ("c", "f", "both", "s"):
        return unit_system
    return _get_persistent_unit_system()


def _get_persistent_graphics() -> str:
    """Get the persistent graphics size preference from config.yaml.

    Reads the 'weather.graphics' value from the Hermes config.
    Falls back to 'm' (medium) if not set or if config is unavailable.

    Returns:
        's' for small, 'm' for medium (default), or 'l' for large.
    """
    try:
        from hermes_cli.config import cfg_get, load_config_readonly
        config = load_config_readonly()
        value = cfg_get(config, "weather", "graphics", default="m")
        if value in ("s", "m", "l", "small", "medium", "large"):
            # Normalize long names to short
            if value == "small":
                return "s"
            elif value == "medium":
                return "m"
            elif value == "large":
                return "l"
            return value
        return "m"
    except Exception:
        return "m"


def _get_persistent_detail() -> str:
    """Get the persistent detail level preference from config.yaml.

    Reads the 'weather.detail' value from the Hermes config.
    Falls back to 'm' (medium) if not set or if config is unavailable.

    Returns:
        's' for small, 'm' for medium (default), or 'l' for large.
    """
    try:
        from hermes_cli.config import cfg_get, load_config_readonly
        config = load_config_readonly()
        value = cfg_get(config, "weather", "detail", default="m")
        if value in ("s", "m", "l", "small", "medium", "large"):
            # Normalize long names to short
            if value == "small":
                return "s"
            elif value == "medium":
                return "m"
            elif value == "large":
                return "l"
            return value
        return "m"
    except Exception:
        return "m"


def _resolve_graphics(graphics: Optional[str]) -> str:
    """Resolve the effective graphics size, falling back to persistent config.

    Args:
        graphics: Per-request graphics parameter ('s', 'm', 'l').
                  If None or empty, falls back to persistent config default.

    Returns:
        The resolved graphics value ('s', 'm', or 'l').
    """
    if graphics and graphics in ("s", "m", "l", "small", "medium", "large"):
        # Normalize long names to short
        if graphics == "small":
            return "s"
        elif graphics == "medium":
            return "m"
        elif graphics == "large":
            return "l"
        return graphics
    return _get_persistent_graphics()


def _resolve_detail(detail: Optional[str]) -> str:
    """Resolve the effective detail level, falling back to persistent config.

    Args:
        detail: Per-request detail parameter ('s', 'm', 'l').
                If None or empty, falls back to persistent config default.

    Returns:
        The resolved detail value ('s', 'm', or 'l').
    """
    if detail and detail in ("s", "m", "l", "small", "medium", "large"):
        # Normalize long names to short
        if detail == "small":
            return "s"
        elif detail == "medium":
            return "m"
        elif detail == "large":
            return "l"
        return detail
    return _get_persistent_detail()


def _resolve_text_format(fmt: Optional[str], detail: str, graphics: str = "m") -> Optional[str]:
    """Resolve the output format for text-based queries.

    Maps the detail level to appropriate wttr.in format options:
    - 's' (small): format=1 (minimal one-line) or format=2 (condition + temp)
    - 'm' (medium): default text output (full ANSI forecast table)
    - 'l' (large): default text output (same as medium, but with full ASCII
      weather graphics and all available forecast days — large graphics)

    The graphics preference controls visual rendering (plain text vs ANSI),
    while detail controls how much data wttr.in returns. 'l' detail uses the
    default text format (full ASCII forecast) with no format= override,
    which gives the richest visual output. Only 's' detail uses format=1
    (one-line summary).

    Args:
        fmt: Explicit format parameter from the user (takes priority).
        detail: The resolved detail level ('s', 'm', 'l').
        graphics: The resolved graphics size ('s', 'm', 'l'), used to determine
                  if plain text (T flag) is requested.

    Returns:
        The resolved format string, or None for default text output.
    """
    if fmt:
        return fmt
    # Small detail: use one-line format
    if detail == "s":
        return "1"  # Minimal one-line format
    # Medium and large detail: use default text output (full ASCII forecast)
    # 'l' detail with default format gives the full 3-day ASCII weather table
    return None  # Default: full text/ANSI output


def _resolve_text_options(graphics: str) -> str:
    """Resolve text output options for wttr.in based on graphics preference.

    Maps the graphics size to wttr.in URL flags:
    - 's' (small): T (plain text, no ANSI colors) + d (standard glyphs only)
    - 'm' (medium): default (ANSI colors, standard glyphs)
    - 'l' (large): default (ANSI colors, all glyphs)

    Args:
        graphics: The resolved graphics size ('s', 'm', 'l').

    Returns:
        String of wttr.in flags to append to the URL.
    """
    flags = []
    if graphics == "s":
        flags.append("T")  # Plain text, no ANSI
        flags.append("d")  # Standard glyphs only
    elif graphics == "m":
        pass  # Default ANSI behavior
    elif graphics == "l":
        pass  # Default ANSI with all glyphs
    return "".join(flags)


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
               unit: Optional[str] = None, extra_params: Optional[str] = None,
               text_flags: Optional[str] = None) -> str:
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
        text_flags: Text output flags (e.g., "Td" for plain text + standard glyphs).

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
    if text_flags:
        # Text flags like "Td" are bare flags appended directly
        parts.append(text_flags)
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
    if not location:
        location = _get_current_location()
    fmt = args.get("format", "j1")
    lang = args.get("language", "") or None
    unit_system = _resolve_unit_system(args.get("unit_system"))
    explicit_unit = args.get("unit", "") or None
    extra_params = args.get("extra_params", "") or None
    graphics = _resolve_graphics(args.get("graphics"))
    detail = _resolve_detail(args.get("detail"))

    # Resolve format based on detail level if no explicit format given
    if fmt == "j1":
        # JSON format: detail level doesn't change the format, but text_flags do apply
        pass
    elif not fmt:
        # No explicit format - use detail to determine format
        fmt = _resolve_text_format(None, detail, graphics)

    wttr_unit = _resolve_unit_params(unit_system, explicit_unit)
    text_flags = _resolve_text_options(graphics) if not fmt or fmt.startswith(("1", "2", "3", "4")) else None

    url = _build_url(location, fmt=fmt, lang=lang, unit=wttr_unit or None,
                     extra_params=extra_params, text_flags=text_flags or None)
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
        "graphics": graphics,
        "detail": detail,
        "url": result["url"]
    })


def weather_forecast(args, **kwargs) -> str:
    """Get weather forecast for a location.

    Returns forecast data either as formatted text (default) or as JSON
    when format='j1' or 'j2' is specified.
    """
    location = args.get("location", "")
    if not location:
        location = _get_current_location()
    fmt = args.get("format", "") or None
    lang = args.get("language", "") or None
    unit_system = _resolve_unit_system(args.get("unit_system"))
    explicit_unit = args.get("unit", "") or None
    extra_params = args.get("extra_params", "") or None
    graphics = _resolve_graphics(args.get("graphics"))
    detail = _resolve_detail(args.get("detail"))

    # Build extra params for forecast days if requested
    days = args.get("days")
    if days is not None:
        if extra_params:
            extra_params = f"{extra_params}&days={days}"
        else:
            extra_params = f"days={days}"

    # If no explicit format given, use detail to determine format
    if not fmt:
        fmt = _resolve_text_format(None, detail, graphics)

    wttr_unit = _resolve_unit_params(unit_system, explicit_unit)
    text_flags = _resolve_text_options(graphics) if not fmt or fmt.startswith(("1", "2", "3", "4")) else None

    url = _build_url(location, fmt=fmt, lang=lang, unit=wttr_unit or None,
                     extra_params=extra_params, text_flags=text_flags or None)
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
        "graphics": graphics,
        "detail": detail,
        "url": result["url"]
    })


def weather_oneline(args, **kwargs) -> str:
    """Get one-line weather output for a location.

    Uses wttr.in's format parameter. Preconfigured formats 1-4 or custom
    %-notation strings. Supports multiple locations separated by colon.
    The unit_system parameter controls which unit is displayed: 'c' for
    metric (Celsius), 'f' for USCS (Fahrenheit), 'both' for default.
    The graphics parameter controls text rendering: 's' for plain text,
    'm' for ANSI colors (default), 'l' for full graphical.
    """
    location = args.get("location", "")
    if not location:
        location = _get_current_location()
    fmt = args.get("format", "3")
    lang = args.get("language", "") or None
    unit_system = _resolve_unit_system(args.get("unit_system"))
    explicit_unit = args.get("unit", "") or None
    graphics = _resolve_graphics(args.get("graphics"))

    wttr_unit = _resolve_unit_params(unit_system, explicit_unit)
    text_flags = _resolve_text_options(graphics) if graphics == "s" else None

    url = _build_url(location, fmt=fmt, lang=lang, unit=wttr_unit or None,
                     text_flags=text_flags or None)
    result = _fetch_weather(url)

    if result["status"] == "error":
        return json.dumps(result)

    return json.dumps({
        "status": "ok",
        "content": result["content"].strip(),
        "format": fmt,
        "location": location or "auto-detected (by IP)",
        "unit_system": unit_system,
        "graphics": graphics,
        "url": result["url"]
    })


def weather_moon(args, **kwargs) -> str:
    """Get moon phase information for a specific date.

    Queries wttr.in's Moon endpoint. Date format is YYYY-MM-DD.
    The graphics parameter controls text rendering: 's' for plain text,
    'm' for ANSI (default), 'l' for full graphical.
    """
    date = args.get("date", "")
    lang = args.get("language", "") or None
    fmt = args.get("format", "") or None
    unit_system = _resolve_unit_system(args.get("unit_system"))
    explicit_unit = args.get("unit", "") or None
    graphics = _resolve_graphics(args.get("graphics"))

    if date:
        location = f"Moon@{date}"
    else:
        location = "Moon"

    wttr_unit = _resolve_unit_params(unit_system, explicit_unit)
    text_flags = _resolve_text_options(graphics) if graphics == "s" else None

    url = _build_url(location, fmt=fmt, lang=lang, unit=wttr_unit or None,
                     text_flags=text_flags or None)
    result = _fetch_weather(url)

    if result["status"] == "error":
        return json.dumps(result)

    return json.dumps({
        "status": "ok",
        "content": result["content"],
        "location": location,
        "date": date or "current date",
        "unit_system": unit_system,
        "graphics": graphics,
        "url": result["url"]
    })


def weather_prometheus(args, **kwargs) -> str:
    """Get weather data as Prometheus metrics.

    Returns Prometheus-formatted metrics including temperature, humidity,
    wind speed, pressure, and other meteorological observations.
    Prometheus output includes both Celsius and Fahrenheit metrics.
    """
    location = args.get("location", "")
    if not location:
        location = _get_current_location()

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


def set_weather_preference(args, **kwargs) -> str:
    """Set persistent weather preferences in config.yaml.

    Stores preferences under the 'weather' section in config.yaml.
    Supported preferences:
    - unit_system: 'c' (Celsius), 'f' (Fahrenheit), 'both' (default), 's' (scientific)
    - graphics: 's' (small/plain text), 'm' (medium/ANSI, default), 'l' (large/full graphics)
    - detail: 's' (small/one-line), 'm' (medium/default forecast), 'l' (large/full ASCII forecast)

    Subsequent weather tool calls that don't specify a parameter will use
    this persistent default. Per-request parameters override these settings.
    """
    unit_system = args.get("unit_system")
    graphics = args.get("graphics")
    detail = args.get("detail")

    # Validate provided parameters
    errors = []
    if unit_system is not None and unit_system not in ("c", "f", "both", "s"):
        errors.append(f"unit_system must be one of: c, f, both, s (got '{unit_system}')")
    if graphics is not None and graphics not in ("s", "m", "l", "small", "medium", "large"):
        errors.append(f"graphics must be one of: s, m, l (got '{graphics}')")
    if detail is not None and detail not in ("s", "m", "l", "small", "medium", "large"):
        errors.append(f"detail must be one of: s, m, l (got '{detail}')")

    if errors:
        return json.dumps({
            "status": "error",
            "error": "; ".join(errors)
        })

    try:
        from hermes_cli.config import load_config, save_config
        config = load_config()
        weather_config = config.setdefault("weather", {})
        if unit_system is not None:
            weather_config["unit_system"] = unit_system
        if graphics is not None:
            # Normalize long names
            if graphics == "small":
                weather_config["graphics"] = "s"
            elif graphics == "medium":
                weather_config["graphics"] = "m"
            elif graphics == "large":
                weather_config["graphics"] = "l"
            else:
                weather_config["graphics"] = graphics
        if detail is not None:
            # Normalize long names
            if detail == "small":
                weather_config["detail"] = "s"
            elif detail == "medium":
                weather_config["detail"] = "m"
            elif detail == "large":
                weather_config["detail"] = "l"
            else:
                weather_config["detail"] = detail
        save_config(config)
    except Exception as e:
        return json.dumps({"status": "error", "error": f"Failed to save preference: {e}"})

    saved = {}
    if unit_system is not None:
        saved["unit_system"] = unit_system
    if graphics is not None:
        saved["graphics"] = weather_config.get("graphics")
    if detail is not None:
        saved["detail"] = weather_config.get("detail")

    return json.dumps({
        "status": "ok",
        "message": (
            "Weather preferences updated. These persistent settings will be used "
            "for all weather tool calls that don't specify the parameter. "
            "Per-request parameters override these settings."
        ),
        "saved": saved,
        "unit_system_meaning": "c=Celsius/metric, f=Fahrenheit/USCS, both=both, s=scientific",
        "graphics_meaning": "s=small(plain text), m=medium(ANSI), l=large(full graphics)",
        "detail_meaning": "s=small(one-line format), m=medium(default forecast), l=large(full ASCII forecast table)"
    })


def get_weather_preference(args, **kwargs) -> str:
    """Get all current persistent weather preferences from config.yaml.

    Reads preferences from the 'weather' section in config.yaml.
    Returns defaults if not set.
    """
    unit_system = _get_persistent_unit_system()
    graphics = _get_persistent_graphics()
    detail = _get_persistent_detail()

    return json.dumps({
        "status": "ok",
        "preferences": {
            "unit_system": unit_system,
            "graphics": graphics,
            "detail": detail,
        },
        "descriptions": {
            "unit_system": "c=Celsius-only (metric), f=Fahrenheit-only (USCS), both=both units (default), s=scientific",
            "graphics": "s=small(plain text no ANSI), m=medium(ANSI colors, default), l=large(full graphical output)",
            "detail": "s=small(one-line format), m=medium(default forecast), l=large(full ASCII forecast table)",
        },
        "set_command": (
            "Use set_weather_preference(unit_system='c'|'f'|'both'|'s', "
            "graphics='s'|'m'|'l', detail='s'|'m'|'l') to change. "
            "All parameters are optional — only specified ones will be updated."
        )
    })


def weather_setup(args, **kwargs) -> str:
    """First-run setup or reconfiguration for weather preferences.

    When called, checks if weather preferences have been previously set in
    config.yaml. If not, returns an interactive setup prompt with
    recommendations. If preferences are already set, shows current values
    and suggests commands to change them.

    If 'unit_system', 'graphics', or 'detail' are provided, saves them
    immediately as persistent preferences.
    """
    unit_system = args.get("unit_system")
    graphics = args.get("graphics")
    detail = args.get("detail")

    # If any params provided, save them
    if unit_system or graphics or detail:
        save_result = set_weather_preference(args)
        save_parsed = json.loads(save_result)
        if save_parsed["status"] == "ok":
            return json.dumps({
                "status": "ok",
                "message": "Weather preferences saved successfully!",
                "saved": save_parsed["saved"],
                "next_steps": (
                    "Your preferences are now stored in config.yaml. "
                    "Use weather_current(location='...') to get weather with your "
                    "preferences applied."
                )
            })
        return save_result

    # No params provided — check if preferences exist and show setup guide or current prefs
    try:
        from hermes_cli.config import cfg_get, load_config_readonly
        config = load_config_readonly()
        weather_section = cfg_get(config, "weather", default={})
        has_prefs = isinstance(weather_section, dict) and any(
            k in weather_section for k in ("unit_system", "graphics", "detail")
        )
    except Exception:
        has_prefs = False

    cur_unit = _get_persistent_unit_system()
    cur_graphics = _get_persistent_graphics()
    cur_detail = _get_persistent_detail()

    if not has_prefs:
        return json.dumps({
            "status": "ok",
            "setup_required": True,
            "message": (
                "First time using weather tools! Please set your preferences:\n\n"
                "  unit_system: 'c' (Celsius), 'f' (Fahrenheit), 'both' (default), 's' (scientific)\n"
                "  graphics: 's' (small/plain text), 'm' (medium/ANSI, default), 'l' (large/full graphics)\n"
                "  detail: 's' (small/one-line), 'm' (medium/default forecast), 'l' (large/full ASCII forecast)\n\n"
                "Example:\n"
                "  weather_set_preference(unit_system='f', graphics='m', detail='m')\n\n"
                "Or set individually:\n"
                "  weather_set_preference(unit_system='f')\n"
                "  weather_set_preference(graphics='s')\n"
                "  weather_set_preference(detail='l')"
            ),
            "current_defaults": {
                "unit_system": cur_unit,
                "graphics": cur_graphics,
                "detail": cur_detail,
            },
            "descriptions": {
                "unit_system": "c=Celsius-only (metric), f=Fahrenheit-only (USCS), both=both units (default), s=scientific",
                "graphics": "s=small(plain text no ANSI), m=medium(ANSI colors, default), l=large(full graphical output)",
                "detail": "s=small(one-line format), m=medium(default forecast), l=large(full ASCII forecast table)",
            }
        })

    return json.dumps({
        "status": "ok",
        "setup_required": False,
        "message": "Weather preferences are configured.",
        "current_preferences": {
            "unit_system": cur_unit,
            "graphics": cur_graphics,
            "detail": cur_detail,
        },
        "commands": {
            "change_unit": "weather_set_preference(unit_system='c'|'f'|'both'|'s')",
            "change_graphics": "weather_set_preference(graphics='s'|'m'|'l')",
            "change_detail": "weather_set_preference(detail='s'|'m'|'l')",
            "reset_all": "weather_set_preference(unit_system='both', graphics='m', detail='m')",
        }
    })

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
                    "If not provided, falls back to the persistent default set via "
                    "'hermes config set weather.unit_system=<c|f|both>'. "
                    "In JSON mode, this filters which temperature fields are returned. "
                    "In text mode, it controls the wttr.in unit parameter. "
                    "This parameter overrides the persistent default for this request only."
                ),
                "default": "both",
                "enum": ["c", "f", "both", "s"]
            },
            "unit": {
                "type": "string",
                "description": "Explicit wttr.in unit parameter override ('m', 'M', 'u', 's'). Takes priority over unit_system."
            },
            "graphics": {
                "type": "string",
                "description": (
                    "Graphics/text rendering size: 's' (small, plain text no ANSI), "
                    "'m' (medium, ANSI colors, default), 'l' (large, full graphical output). "
                    "If not provided, falls back to the persistent default set via "
                    "'hermes config set weather.graphics=<s|m|l>'."
                ),
                "default": "m",
                "enum": ["s", "m", "l"]
            },
            "detail": {
                "type": "string",
                "description": (
                    "Amount of information detail: 's' (small, one-line format), "
                    "'m' (medium, default forecast), 'l' (large, full ASCII forecast table). "
                    "If not provided, falls back to the persistent default set via "
                    "'hermes config set weather.detail=<s|m|l>'. "
                    "Ignored when an explicit format is provided."
                ),
                "default": "m",
                "enum": ["s", "m", "l"]
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
                    "If not provided, falls back to the persistent default set via "
                    "'hermes config set weather.unit_system=<c|f|both>'. "
                    "In JSON mode, this filters which temperature fields are returned."
                ),
                "default": "both",
                "enum": ["c", "f", "both", "s"]
            },
            "unit": {
                "type": "string",
                "description": "Explicit wttr.in unit parameter override ('m', 'M', 'u', 's'). Takes priority over unit_system."
            },
            "graphics": {
                "type": "string",
                "description": (
                    "Graphics/text rendering size: 's' (small, plain text), "
                    "'m' (medium, ANSI, default), 'l' (large, full graphics). "
                    "If not provided, falls back to persistent default."
                ),
                "default": "m",
                "enum": ["s", "m", "l"]
            },
            "detail": {
                "type": "string",
                "description": (
                    "Amount of information: 's' (small, one-line), "
                    "'m' (medium, default forecast), 'l' (large, full ASCII forecast table). "
                    "If not provided, falls back to persistent default. "
                    "Ignored when an explicit format is provided."
                ),
                "default": "m",
                "enum": ["s", "m", "l"]
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
                    "'both' (default) for wttr.in's default unit behavior. "
                    "If not provided, falls back to the persistent default set via "
                    "'hermes config set weather.unit_system=<c|f|both|s>'."
                ),
                "default": "both",
                "enum": ["c", "f", "both", "s"]
            },
            "graphics": {
                "type": "string",
                "description": (
                    "Graphics rendering: 's' (small, plain text), "
                    "'m' (medium, ANSI, default), 'l' (large, full graphics). "
                    "If not provided, falls back to persistent default."
                ),
                "default": "m",
                "enum": ["s", "m", "l"]
            },
            "detail": {
                "type": "string",
                "description": "Amount of information detail (ignored for one-line format)",
                "default": "m",
                "enum": ["s", "m", "l"]
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
                "description": (
                    "Temperature unit preference: 'c', 'f', or 'both' (default). "
                    "If not provided, falls back to the persistent default set via "
                    "'hermes config set weather.unit_system=<c|f|both|s>'."
                ),
                "default": "both",
                "enum": ["c", "f", "both", "s"]
            },
            "graphics": {
                "type": "string",
                "description": (
                    "Graphics rendering: 's' (small, plain text), "
                    "'m' (medium, ANSI, default), 'l' (large, full graphics). "
                    "If not provided, falls back to persistent default."
                ),
                "default": "m",
                "enum": ["s", "m", "l"]
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

WEATHER_SET_PREFERENCE_SCHEMA = {
    "name": "weather_set_preference",
    "description": (
        "Set persistent weather preferences in config.yaml. These preferences "
        "are used for all subsequent weather tool calls that don't explicitly "
        "specify the corresponding parameter. Per-request parameters override "
        "these persistent settings for that single call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "unit_system": {
                "type": "string",
                "description": (
                    "The persistent unit preference: 'c' for Celsius/metric, "
                    "'f' for Fahrenheit/USCS, 'both' for both units (default), "
                    "'s' for scientific."
                ),
                "default": "both",
                "enum": ["c", "f", "both", "s"]
            },
            "graphics": {
                "type": "string",
                "description": (
                    "Graphics/text rendering size: 's' (small, plain text), "
                    "'m' (medium, ANSI, default), 'l' (large, full graphics)."
                ),
                "default": "m",
                "enum": ["s", "m", "l"]
            },
            "detail": {
                "type": "string",
                "description": (
                    "Amount of information detail: 's' (small, one-line format), "
                    "'m' (medium, default forecast), 'l' (large, full ASCII forecast table)."
                ),
                "default": "m",
                "enum": ["s", "m", "l"]
            }
        },
    }
}

WEATHER_GET_PREFERENCE_SCHEMA = {
    "name": "weather_get_preference",
    "description": (
        "Get all current persistent weather preferences from config.yaml. "
        "Returns the saved unit_system, graphics, and detail preferences "
        "or their defaults if none have been set."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    }
}

WEATHER_SETUP_SCHEMA = {
    "name": "weather_setup",
    "description": (
        "First-run setup or reconfiguration for weather preferences. "
        "If no parameters are provided, returns a setup prompt if preferences "
        "haven't been set, or shows current preferences if they have. "
        "If unit_system, graphics, or detail are provided, saves them as "
        "persistent preferences. "
        "unit_system: 'c' (Celsius), 'f' (Fahrenheit), 'both' (default), 's' (scientific). "
        "graphics: 's' (small/plain text), 'm' (medium/ANSI, default), 'l' (large/full graphics). "
        "detail: 's' (small/one-line), 'm' (medium/default forecast), 'l' (large/full ASCII forecast). "
        "All parameters are optional."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "unit_system": {
                "type": "string",
                "description": "Persistent temperature unit preference: 'c', 'f', 'both' (default), or 's'.",
                "default": None,
                "enum": ["c", "f", "both", "s", None]
            },
            "graphics": {
                "type": "string",
                "description": "Persistent graphics rendering: 's' (small), 'm' (medium), 'l' (large).",
                "default": None,
                "enum": ["s", "m", "l", None]
            },
            "detail": {
                "type": "string",
                "description": "Persistent detail level: 's' (small), 'm' (medium), 'l' (large).",
                "default": None,
                "enum": ["s", "m", "l", None]
            }
        },
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

registry.register(
    name="weather_set_preference",
    toolset="web",
    schema=WEATHER_SET_PREFERENCE_SCHEMA,
    handler=set_weather_preference,
    check_fn=lambda: True,
    requires_env=[],
    emoji="⚙️",
)

registry.register(
    name="weather_get_preference",
    toolset="web",
    schema=WEATHER_GET_PREFERENCE_SCHEMA,
    handler=get_weather_preference,
    check_fn=lambda: True,
    requires_env=[],
    emoji="ⓘ",
)

registry.register(
    name="weather_setup",
    toolset="web",
    schema=WEATHER_SETUP_SCHEMA,
    handler=weather_setup,
    check_fn=lambda: True,
    requires_env=[],
    emoji="🎛",
)
