use crate::anti_tamper;
use crate::geofence;

#[no_mangle]
pub extern "C" fn rust_c_haversine_distance(
    lat1: f64,
    lon1: f64,
    lat2: f64,
    lon2: f64,
) -> f64 {
    geofence::haversine_distance_meters(lat1, lon1, lat2, lon2)
}

#[no_mangle]
pub extern "C" fn rust_c_calculate_bearing(
    lat1: f64,
    lon1: f64,
    lat2: f64,
    lon2: f64,
) -> f64 {
    geofence::calculate_bearing_degrees(lat1, lon1, lat2, lon2)
}

#[no_mangle]
pub extern "C" fn rust_c_is_inside_geofence(
    child_lat: f64,
    child_lon: f64,
    fence_lat: f64,
    fence_lon: f64,
    radius: f64,
) -> bool {
    geofence::is_inside_geofence(child_lat, child_lon, fence_lat, fence_lon, radius)
}

#[no_mangle]
pub extern "C" fn rust_c_check_clock_tampered(
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

#[no_mangle]
pub extern "C" fn rust_c_is_bedtime(
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
