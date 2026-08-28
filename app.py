"""
AirGuard Lahore — Clean-Air Route Decision & Telemetry System
Phase 8 Finalization & Stabilization Release

Author: AirGuard System Architecture Team
Version: 8.0.0 (Production Ready)
"""

import math
import requests
import folium
import streamlit as st
from streamlit_folium import st_folium

# ==========================================
# 1. CONSTANTS & BASELINE DATA
# ==========================================
LAHORE_CENTER = [31.5204, 74.3587]

DEMO_SECTOR_DATA = [
    {"Sector": "Gulberg III", "lat": 31.5085, "lon": 74.3536, "AQI": 185, "PM25": 121.5, "Risk": "Unhealthy"},
    {"Sector": "Model Town", "lat": 31.4822, "lon": 74.3218, "AQI": 120, "PM25": 43.2, "Risk": "Unhealthy for Sensitive Groups"},
    {"Sector": "DHA Phase 5", "lat": 31.4697, "lon": 74.4019, "AQI": 95, "PM25": 33.5, "Risk": "Moderate"},
    {"Sector": "Johar Town", "lat": 31.4697, "lon": 74.2728, "AQI": 210, "PM25": 160.0, "Risk": "Very Unhealthy"},
    {"Sector": "Mall Road", "lat": 31.5580, "lon": 74.3260, "AQI": 280, "PM25": 230.1, "Risk": "Very Unhealthy"},
    {"Sector": "Ferozepur Road", "lat": 31.4900, "lon": 74.3350, "AQI": 240, "PM25": 190.0, "Risk": "Very Unhealthy"},
]

LOCATIONS = {
    "Gulberg III": (31.5085, 74.3536),
    "Model Town": (31.4822, 74.3218),
    "DHA Phase 5": (31.4697, 74.4019),
    "Johar Town": (31.4697, 74.2728),
    "Mall Road": (31.5580, 74.3260),
    "Ferozepur Road": (31.4900, 74.3350),
}

TRANSPORT_MODES = {
    "Car (Enclosed / AC Filtered)": {"ve": 0.45, "eta": 0.50, "icon": "🚗"},
    "Motorcycle / Rickshaw (Open-Air)": {"ve": 0.65, "eta": 1.00, "icon": "🛵"},
    "Bicycle / Active Exertion": {"ve": 1.80, "eta": 1.00, "icon": "🚲"},
    "Pedestrian / Walking": {"ve": 1.20, "eta": 1.00, "icon": "🚶"},
}


# ==========================================
# 2. HELPER FUNCTIONS & ROUTING PIPELINE
# ==========================================
def categorize_aqi_risk(aqi_val: float) -> str:
    """Classifies numerical AQI into standard EPA risk categories."""
    if aqi_val <= 50:
        return "Good"
    elif aqi_val <= 100:
        return "Moderate"
    elif aqi_val <= 150:
        return "Unhealthy for Sensitive Groups"
    elif aqi_val <= 200:
        return "Unhealthy"
    elif aqi_val <= 300:
        return "Very Unhealthy"
    else:
        return "Hazardous"


def get_aqi_hex_color(aqi_val: float) -> str:
    """Returns standardized hex color code for AQI visualization."""
    if aqi_val <= 50:
        return "#2ecc71"
    elif aqi_val <= 100:
        return "#f1c40f"
    elif aqi_val <= 150:
        return "#e67e22"
    elif aqi_val <= 200:
        return "#e74c3c"
    elif aqi_val <= 300:
        return "#8e44ad"
    else:
        return "#7e0023"


@st.cache_data(ttl=600)
def fetch_live_sector_aqi(sectors: list) -> tuple:
    """Fetches real-time AQI and PM2.5 metrics from Open-Meteo API."""
    updated_sectors = []
    has_error = False
    for sec in sectors:
        lat, lon = sec["lat"], sec["lon"]
        url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=us_aqi,pm2_5"
        try:
            resp = requests.get(url, timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                current_aqi = data.get("current", {}).get("us_aqi")
                current_pm25 = data.get("current", {}).get("pm2_5")
                if current_aqi is not None and current_pm25 is not None:
                    sec_entry = sec.copy()
                    sec_entry["AQI"] = int(current_aqi)
                    sec_entry["PM25"] = float(current_pm25)
                    sec_entry["Risk"] = categorize_aqi_risk(float(current_aqi))
                    updated_sectors.append(sec_entry)
                else:
                    has_error = True
            else:
                has_error = True
        except Exception:
            has_error = True

    if not has_error and len(updated_sectors) == len(sectors):
        return updated_sectors, True, None
    return sectors, False, "Open-Meteo API unreachable. Active fallback: Offline baseline PM2.5/AQI."


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes Haversine distance in kilometers between two geographic coordinates."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2)
    return R * (2.0 * math.atan2(math.sqrt(max(0.0, a)), math.sqrt(max(0.0, 1.0 - a))))


def fetch_osrm_route_single(start_coords: tuple, end_coords: tuple, via_coords: tuple = None) -> list:
    """Fetches OSRM driving routes with full geometry steps."""
    if via_coords:
        waypoints = f"{start_coords[1]},{start_coords[0]};{via_coords[1]},{via_coords[0]};{end_coords[1]},{end_coords[0]}"
    else:
        waypoints = f"{start_coords[1]},{start_coords[0]};{end_coords[1]},{end_coords[0]}"

    url = (f"http://router.project-osrm.org/route/v1/driving/{waypoints}"
           f"?overview=full&geometries=geojson&steps=true&alternatives=3")
    try:
        resp = requests.get(url, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and len(data.get("routes", [])) > 0:
                return data["routes"]
    except Exception:
        pass
    return []


def fetch_robust_route_pair(start_coords: tuple, end_coords: tuple) -> tuple:
    """
    3-Tier Route Retrieval Engine:
    1. Direct OSRM Alternatives
    2. Waypoint-based Detour Engine
    3. Synthetic Corridor Fallback
    """
    routes = fetch_osrm_route_single(start_coords, end_coords)
    if len(routes) >= 2:
        return routes[:2], "OSRM Direct Alternatives"

    lat1, lon1 = start_coords
    lat2, lon2 = end_coords

    mid_lat = (lat1 + lat2) / 2.0
    mid_lon = (lon1 + lon2) / 2.0

    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    via_lat = mid_lat - (d_lon * 0.25)
    via_lon = mid_lon + (d_lat * 0.25)

    alt_routes = fetch_osrm_route_single(start_coords, end_coords, via_coords=(via_lat, via_lon))

    if routes and alt_routes:
        return [routes[0], alt_routes[0]], "OSRM + Waypoint Detour Engine"
    elif alt_routes and len(alt_routes) >= 2:
        return alt_routes[:2], "OSRM Waypoint Alternatives"

    direct_dist_m = haversine_distance(lat1, lon1, lat2, lon2) * 1000.0
    syn_r1 = {
        "geometry": {"coordinates": [[lon1, lat1], [mid_lon, mid_lat], [lon2, lat2]]},
        "distance": direct_dist_m * 1.1,
        "duration": max(60.0, direct_dist_m / 10.0),
        "legs": []
    }
    syn_r2 = {
        "geometry": {"coordinates": [[lon1, lat1], [via_lon, via_lat], [lon2, lat2]]},
        "distance": direct_dist_m * 1.25,
        "duration": max(75.0, direct_dist_m / 8.5),
        "legs": []
    }
    return [syn_r1, syn_r2], "Synthetic Corridor Engine"


def calculate_route_overlap(coords_a: list, coords_b: list, threshold_km: float = 0.1) -> float:
    """Calculates directional geometric overlap percentage between two coordinate arrays."""
    if not coords_a or not coords_b:
        return 1.0
    sampled_b = coords_b[::3]
    if coords_b[-1] not in sampled_b:
        sampled_b.append(coords_b[-1])
    overlapping_points = 0
    for lon_b, lat_b in sampled_b:
        for lon_a, lat_a in coords_a[::3]:
            if haversine_distance(lat_b, lon_b, lat_a, lon_a) <= threshold_km:
                overlapping_points += 1
                break
    return overlapping_points / max(1, len(sampled_b))


def get_nearest_sector(lat: float, lon: float, sector_data: list) -> dict:
    """Finds nearest spatial monitoring sector for coordinate point."""
    min_dist = float('inf')
    nearest = sector_data[0]
    for sec in sector_data:
        d = haversine_distance(lat, lon, sec["lat"], sec["lon"])
        if d < min_dist:
            min_dist = d
            nearest = sec
    return nearest


def calculate_time_weighted_exposure(route_object: dict, sector_data: list, mode_params: dict) -> tuple:
    """Calculates time-weighted PM2.5 inhalation mass (ug) and route-average AQI."""
    ve = mode_params["ve"]
    eta = mode_params["eta"]
    net_multiplier = ve * eta

    total_burden_ug = 0.0
    total_weighted_aqi = 0.0
    total_duration_sec = route_object.get("duration", 0.0)
    legs = route_object.get("legs", [])

    if legs and any("steps" in leg for leg in legs):
        for leg in legs:
            for step in leg.get("steps", []):
                duration_min = step.get("duration", 0.0) / 60.0
                step_intersections = step.get("intersections", [])

                if step_intersections:
                    pm25_sum = sum(
                        get_nearest_sector(inter["location"][1], inter["location"][0], sector_data)["PM25"]
                        for inter in step_intersections
                    )
                    aqi_sum = sum(
                        get_nearest_sector(inter["location"][1], inter["location"][0], sector_data)["AQI"]
                        for inter in step_intersections
                    )
                    avg_pm25 = pm25_sum / max(1, len(step_intersections))
                    avg_aqi = aqi_sum / max(1, len(step_intersections))
                else:
                    m_loc = step.get("maneuver", {}).get("location", [0, 0])
                    sec = get_nearest_sector(m_loc[1], m_loc[0], sector_data)
                    avg_pm25, avg_aqi = sec["PM25"], sec["AQI"]

                total_burden_ug += avg_pm25 * (duration_min / 60.0) * net_multiplier
                total_weighted_aqi += (avg_aqi * step.get("duration", 0.0))

        avg_aqi = round(total_weighted_aqi / max(1e-6, total_duration_sec), 1) if total_duration_sec > 0 else 0.0
    else:
        coords = route_object.get("geometry", {}).get("coordinates", [])
        samples_count = len(coords)
        time_per_sample_min = (total_duration_sec / 60.0) / max(1, samples_count) if samples_count > 0 else 0.0

        aqi_sum = 0.0
        for lon, lat in coords:
            sec = get_nearest_sector(lat, lon, sector_data)
            total_burden_ug += sec["PM25"] * (time_per_sample_min / 60.0) * net_multiplier
            aqi_sum += sec["AQI"]
        avg_aqi = round(aqi_sum / max(1, samples_count), 1) if samples_count > 0 else 0.0

    return round(total_burden_ug, 2), avg_aqi


def build_segmented_route_polylines(geometry_coords: list, sector_data: list) -> list:
    """Builds sector-mapped polyline segments for map colorization."""
    segments = []
    for i in range(len(geometry_coords) - 1):
        pt1 = geometry_coords[i]
        pt2 = geometry_coords[i + 1]

        mid_lat = (pt1[1] + pt2[1]) / 2.0
        mid_lon = (pt1[0] + pt2[0]) / 2.0

        nearest_sec = get_nearest_sector(mid_lat, mid_lon, sector_data)
        color = get_aqi_hex_color(nearest_sec["AQI"])

        segments.append({
            "coords": [[pt1[1], pt1[0]], [pt2[1], pt2[0]]],
            "color": color,
            "sector": nearest_sec["Sector"],
            "aqi": nearest_sec["AQI"],
            "pm25": nearest_sec["PM25"]
        })
    return segments


# ==========================================
# 3. EXPLAINABILITY & DECISION ENGINE
# ==========================================
def evaluate_clean_air_decision(r1: dict, r2: dict, overlap_ratio: float) -> dict:
    """Phase 7C rule-based evaluation engine for route recommendation."""
    burden_a = r1["burden_ug"]
    burden_b = r2["burden_ug"]
    time_a = r1["duration"]
    time_b = r2["duration"]

    pct_diff = ((burden_a - burden_b) / max(burden_a, 1e-6)) * 100.0
    abs_diff = round(abs(burden_a - burden_b), 2)
    time_penalty_pct = ((time_b - time_a) / max(time_a, 1e-6)) * 100.0

    if abs(pct_diff) < 3.0:
        return {
            "code": "EQUIVALENT",
            "recommended": "Route #1 (Primary)",
            "title": "⚖️ Negligible Air Quality Difference (<3% Burden Delta)",
            "explanation": f"Both routes offer virtually identical pollution exposure (delta of {abs_diff} µg / {abs(pct_diff):.1f}%). Route #1 is recommended as the default path.",
            "tradeoff": f"Travel times are within {abs(time_a - time_b):.1f} mins of each other.",
            "banner_type": "info"
        }

    if overlap_ratio > 0.85:
        return {
            "code": "HIGH_OVERLAP",
            "recommended": "Route #1 (Primary)",
            "title": "⚠️ High Corridor Overlap (>85% Shared Path)",
            "explanation": f"Routes share over {int(overlap_ratio * 100)}% of the same road geometry. Choosing Route #2 offers limited real-world exposure reduction.",
            "tradeoff": "Alternative corridor does not deviate sufficiently from main arterial pollution.",
            "banner_type": "warning"
        }

    if pct_diff > 3.0:
        if time_penalty_pct > 30.0:
            return {
                "code": "TIME_PENALTY",
                "recommended": "Route #1 (Fastest)",
                "title": "⏳ Significant Time Trade-off Required",
                "explanation": f"Route #2 reduces PM₂.₅ exposure by {pct_diff:.1f}% ({abs_diff} µg), but requires **{time_penalty_pct:.1f}% more travel time** (+{time_b - time_a:.1f} mins).",
                "tradeoff": "Choose Route #1 if speed is priority; select Route #2 if minimizing inhalation risk is critical.",
                "banner_type": "warning"
            }
        else:
            return {
                "code": "RECOMMEND_ALT",
                "recommended": "Route #2 (Alternative)",
                "title": "🌱 Clean-Air Recommendation: Route #2 Recommended",
                "explanation": f"Route #2 reduces overall PM₂.₅ inhalation burden by **{pct_diff:.1f}%** (saving **{abs_diff} µg** of PM₂.₅) with acceptable travel time.",
                "tradeoff": f"Extra time required: +{max(0.0, time_b - time_a):.1f} mins across a {r2['distance'] - r1['distance']:+.1f} km distance change.",
                "banner_type": "success"
            }
    else:
        pct_cleaner_a = abs(pct_diff)
        return {
            "code": "RECOMMEND_PRI",
            "recommended": "Route #1 (Primary)",
            "title": "🚗 Primary Route is Cleaner & Faster",
            "explanation": f"Route #1 is **{pct_cleaner_a:.1f}% cleaner** (saving **{abs_diff} µg**) and faster than Route #2.",
            "tradeoff": "Route #1 offers optimal balance of speed and reduced pollution.",
            "banner_type": "info"
        }


def analyze_high_exposure_segments(segments: list, aqi_threshold: int = 150) -> list:
    """Filters route segments exceeding severe pollution threshold."""
    return [seg for seg in segments if seg["aqi"] >= aqi_threshold]


# ==========================================
# 4. MAIN APPLICATION DASHBOARD
# ==========================================
def main():
    st.set_page_config(
        page_title="AirGuard Lahore — Clean-Air Route Decision System",
        page_icon="🌬️",
        layout="wide"
    )

    st.title("🌬️ Clean-Air Route Decision System")
    st.caption("AirGuard Lahore — Phase 8 Final Production Release")

    # ----------------------------------------
    # Sidebar Setup
    # ----------------------------------------
    st.sidebar.header("Navigation Setup")
    loc_names = list(LOCATIONS.keys())
    origin_name = st.sidebar.selectbox("Select Origin", loc_names, index=0)
    dest_name = st.sidebar.selectbox("Select Destination", loc_names, index=2)

    if origin_name == dest_name:
        st.sidebar.error("Origin and Destination must be different!")

    st.sidebar.markdown("---")
    st.sidebar.header("Transport Profile")
    mode_name = st.sidebar.selectbox("Mode of Transport", list(TRANSPORT_MODES.keys()), index=0)
    selected_mode = TRANSPORT_MODES[mode_name]

    st.sidebar.markdown("---")
    st.sidebar.header("AQI Data Engine")
    use_live_aqi = st.sidebar.toggle("Fetch Live Open-Meteo AQI", value=True)

    if use_live_aqi:
        sector_data, is_live_aqi, aqi_err = fetch_live_sector_aqi(DEMO_SECTOR_DATA)
        if is_live_aqi:
            st.sidebar.success("🌐 Connected: Live Open-Meteo AQI")
        else:
            st.sidebar.warning(f"⚠️ {aqi_err}")
    else:
        sector_data = DEMO_SECTOR_DATA
        st.sidebar.info("📌 Using Offline Baseline PM₂.₅/AQI")

    start_coords = LOCATIONS[origin_name]
    end_coords = LOCATIONS[dest_name]

    # ----------------------------------------
    # Execution & Telemetry Pipeline
    # ----------------------------------------
    routes, route_engine_status = fetch_robust_route_pair(start_coords, end_coords)

    route_details = []
    for idx, r in enumerate(routes):
        dist_km = round(r["distance"] / 1000.0, 2)
        duration_min = round(r["duration"] / 60.0, 1)
        burden_ug, avg_aqi = calculate_time_weighted_exposure(r, sector_data, selected_mode)
        segments = build_segmented_route_polylines(r["geometry"]["coordinates"], sector_data)

        route_details.append({
            "id": idx + 1,
            "geometry": r["geometry"],
            "distance": dist_km,
            "duration": duration_min,
            "burden_ug": burden_ug,
            "avg_aqi": avg_aqi,
            "segments": segments,
            "is_primary": idx == 0
        })

    has_alt = len(route_details) > 1
    r1 = route_details[0]
    r2 = route_details[1] if has_alt else r1

    overlap_ratio = calculate_route_overlap(r1["geometry"]["coordinates"], r2["geometry"]["coordinates"]) if has_alt else 1.0
    decision = evaluate_clean_air_decision(r1, r2, overlap_ratio) if has_alt else {
        "code": "SINGLE_ROUTE",
        "recommended": "Route #1",
        "title": "ℹ️ Single Route Available",
        "explanation": "Only one viable road corridor was returned by the routing engine.",
        "tradeoff": "N/A",
        "banner_type": "info"
    }

    # ----------------------------------------
    # Top Telemetry Cards
    # ----------------------------------------
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Primary Route Distance", f"{r1['distance']} km")
        st.metric("Est. Primary Travel Time", f"{r1['duration']} mins")

    with col2:
        st.metric(
            f"Primary Burden ({selected_mode['icon']})",
            f"{r1['burden_ug']} µg",
            help="Time-weighted PM2.5 inhalation mass."
        )
        if has_alt:
            delta_val = round(r2['burden_ug'] - r1['burden_ug'], 2)
            pct_val = ((r1['burden_ug'] - r2['burden_ug']) / max(r1['burden_ug'], 1e-6)) * 100.0
            st.metric(
                "Alternative Burden",
                f"{r2['burden_ug']} µg",
                delta=f"{pct_val:+.1f}% ({delta_val:+.2f} µg)",
                delta_color="inverse",
                help="PM2.5 inhalation mass for alternative route."
            )
        else:
            st.metric("Alternative Burden", "N/A")

    with col3:
        if decision["banner_type"] == "success":
            st.success(f"**{decision['title']}**\n\n{decision['explanation']}")
        elif decision["banner_type"] == "warning":
            st.warning(f"**{decision['title']}**\n\n{decision['explanation']}")
        else:
            st.info(f"**{decision['title']}**\n\n{decision['explanation']}")

    st.markdown("---")

    # ----------------------------------------
    # Route Comparison Matrix (Phase 7A/7B)
    # ----------------------------------------
    st.subheader("📊 Route Comparison Matrix")

    if has_alt:
        burden_a = r1["burden_ug"]
        burden_b = r2["burden_ug"]
        pct_diff = ((burden_a - burden_b) / max(burden_a, 1e-6)) * 100.0

        comp_data = {
            "Metric": [
                "Distance (km)",
                "Travel Time (mins)",
                "Time Weighted Avg AQI",
                "PM₂.₅ Inhalation Burden (µg)",
                "Exposure Difference (%)"
            ],
            "Route #1 (Primary)": [
                f"{r1['distance']} km",
                f"{r1['duration']} mins",
                f"{r1['avg_aqi']}",
                f"{r1['burden_ug']} µg",
                "Baseline (0.0%)"
            ],
            "Route #2 (Alternative)": [
                f"{r2['distance']} km",
                f"{r2['duration']} mins",
                f"{r2['avg_aqi']}",
                f"{r2['burden_ug']} µg",
                f"{-pct_diff:+.1f}% vs Route #1"
            ],
            "Delta / Trade-off": [
                f"{r2['distance'] - r1['distance']:+.2f} km",
                f"{r2['duration'] - r1['duration']:+.1f} mins",
                f"{r2['avg_aqi'] - r1['avg_aqi']:+.1f} AQI",
                f"{r2['burden_ug'] - r1['burden_ug']:+.2f} µg",
                f"{'Cleaner 🟢' if pct_diff > 0 else 'Higher Risk 🔴' if pct_diff < 0 else 'Equivalent 🟡'}"
            ]
        }
        st.table(comp_data)
    else:
        st.info("Side-by-side comparison requires at least two calculated routes.")

    # ----------------------------------------
    # Recommendation & Explainability (Phase 7D)
    # ----------------------------------------
    st.subheader("💡 System Recommendation & Explainability")

    rec_col1, rec_col2 = st.columns([2, 1])

    with rec_col1:
        st.markdown(f"### Recommended Route: **{decision['recommended']}**")
        st.markdown(f"**Decision Summary:** {decision['explanation']}")
        st.markdown(f"**Trade-off Analysis:** {decision['tradeoff']}")
        st.markdown(f"**Corridor Overlap Index:** `{int(overlap_ratio * 100)}%` shared road geometry.")

    with rec_col2:
        st.info(
            "🔬 **Scientific Disclaimer & Methodology**\n\n"
            "• **Inhalation Estimate:** Based on spatial proximity to sector monitors and mode inhalation factors.\n"
            "• **Model Bounds:** This system provides relative risk guidance, **not** direct personal exposure measurements.\n"
            "• **Live Data:** Powered by Open-Meteo & local sector interpolation."
        )

    st.markdown("---")

    # ----------------------------------------
    # High Exposure Hotspot Breakdown (Phase 7E)
    # ----------------------------------------
    st.subheader("⚠️ High-Exposure Hotspot Inspection")

    hotspots_r1 = analyze_high_exposure_segments(r1["segments"], aqi_threshold=150)
    hotspots_r2 = analyze_high_exposure_segments(r2["segments"], aqi_threshold=150) if has_alt else []

    h_col1, h_col2 = st.columns(2)

    with h_col1:
        st.markdown("**Route #1 Hotspots (AQI > 150):**")
        if hotspots_r1:
            unique_sectors_r1 = list({h['sector']: h for h in hotspots_r1}.values())
            for h in unique_sectors_r1:
                st.write(f"- 🔴 **{h['sector']}**: AQI `{h['aqi']}` (PM₂.₅: `{h['pm25']} µg/m³`)")
        else:
            st.write("🟢 No severe hotspots (AQI > 150) detected along Route #1.")

    with h_col2:
        st.markdown("**Route #2 Hotspots (AQI > 150):**")
        if hotspots_r2:
            unique_sectors_r2 = list({h['sector']: h for h in hotspots_r2}.values())
            for h in unique_sectors_r2:
                st.write(f"- 🔴 **{h['sector']}**: AQI `{h['aqi']}` (PM₂.₅: `{h['pm25']} µg/m³`)")
        else:
            st.write("🟢 No severe hotspots (AQI > 150) detected along Route #2.")

    st.markdown("---")

    # ----------------------------------------
    # Map Visualization
    # ----------------------------------------
    st.subheader("🗺️ Segment-Level Air Quality Route Map")
    st.caption(f"Active Router Engine: {route_engine_status}")

    m = folium.Map(location=LAHORE_CENTER, zoom_start=12, tiles="OpenStreetMap")

    for sec in sector_data:
        color = get_aqi_hex_color(sec["AQI"])
        folium.CircleMarker(
            location=[sec["lat"], sec["lon"]],
            radius=12,
            popup=f"<b>{sec['Sector']}</b><br>PM₂.₅: {sec['PM25']} µg/m³<br>AQI: {sec['AQI']} ({sec['Risk']})",
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.4
        ).add_to(m)

    for rd in route_details:
        is_pri = rd["is_primary"]
        weight = 7 if is_pri else 4
        opacity = 0.9 if is_pri else 0.6

        for seg in rd["segments"]:
            folium.PolyLine(
                locations=seg["coords"],
                color=seg["color"],
                weight=weight,
                opacity=opacity,
                popup=f"Route #{rd['id']} Step<br>Sector: {seg['sector']}<br>Local AQI: {seg['aqi']}"
            ).add_to(m)

    folium.Marker(start_coords, popup=f"Origin: {origin_name}", icon=folium.Icon(color="green", icon="play")).add_to(m)
    folium.Marker(end_coords, popup=f"Destination: {dest_name}", icon=folium.Icon(color="flag", icon="stop")).add_to(m)

    st_folium(m, width="100%", height=520)


if __name__ == "__main__":
    main()