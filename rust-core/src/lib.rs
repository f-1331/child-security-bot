pub mod anti_tamper;
pub mod ffi;
pub mod geofence;
pub mod kalman;

use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn rust_haversine_distance(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    geofence::haversine_distance_meters(lat1, lon1, lat2, lon2)
}

#[wasm_bindgen]
pub fn rust_calculate_bearing(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    geofence::calculate_bearing_degrees(lat1, lon1, lat2, lon2)
}

#[wasm_bindgen]
pub fn rust_is_inside_geofence(
    child_lat: f64,
    child_lon: f64,
    fence_lat: f64,
    fence_lon: f64,
    radius: f64,
) -> bool {
    geofence::is_inside_geofence(child_lat, child_lon, fence_lat, fence_lon, radius)
}

#[wasm_bindgen]
pub fn rust_smooth_trajectory_json(points_json: &str) -> String {
    let points: Vec<kalman::GpsPoint> = match serde_json::from_str(points_json) {
        Ok(p) => p,
        Err(_) => return points_json.to_string(),
    };

    let smoothed = kalman::smooth_gps_trajectory(&points);
    serde_json::to_string(&smoothed).unwrap_or_else(|_| points_json.to_string())
}

#[wasm_bindgen]
pub fn rust_detect_stop_points_json(
    points_json: &str,
    min_duration_ms: i64,
    max_radius_meters: f64,
) -> String {
    let points: Vec<kalman::GpsPoint> = match serde_json::from_str(points_json) {
        Ok(p) => p,
        Err(_) => return "[]".to_string(),
    };

    let stops = geofence::detect_stop_points(&points, min_duration_ms, max_radius_meters);
    serde_json::to_string(&stops).unwrap_or_else(|_| "[]".to_string())
}

#[wasm_bindgen]
pub fn rust_check_clock_tampered(
    current_wall_ms: i64,
    last_saved_wall_ms: i64,
    current_mono_ms: i64,
    last_saved_mono_ms: i64,
    tolerance_ms: i64,
) -> bool {
    anti_tamper::is_clock_tampered(
        current_wall_ms,
        last_saved_wall_ms,
        current_mono_ms,
        last_saved_mono_ms,
        tolerance_ms,
    )
}

#[wasm_bindgen]
pub fn rust_is_bedtime(
    current_hour: u32,
    current_min: u32,
    start_hour: u32,
    start_min: u32,
    end_hour: u32,
    end_min: u32,
) -> bool {
    anti_tamper::is_within_bedtime_window(
        current_hour,
        current_min,
        start_hour,
        start_min,
        end_hour,
        end_min,
    )
}
