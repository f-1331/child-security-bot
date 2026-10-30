use crate::kalman::{GpsPoint, StopPoint};
use std::f64::consts::PI;

const EARTH_RADIUS_METERS: f64 = 6_371_000.0;

/// Haversine formula to compute great-circle distance between two GPS coordinates in meters
pub fn haversine_distance_meters(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let d_lat = (lat2 - lat1) * PI / 180.0;
    let d_lon = (lon2 - lon1) * PI / 180.0;

    let r_lat1 = lat1 * PI / 180.0;
    let r_lat2 = lat2 * PI / 180.0;

    let a = (d_lat / 2.0).sin().powi(2)
        + r_lat1.cos() * r_lat2.cos() * (d_lon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());

    EARTH_RADIUS_METERS * c
}

/// Computes compass bearing in degrees (0 - 360) from point 1 to point 2
pub fn calculate_bearing_degrees(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let phi1 = lat1.to_radians();
    let phi2 = lat2.to_radians();
    let delta_lambda = (lon2 - lon1).to_radians();

    let y = delta_lambda.sin() * phi2.cos();
    let x = phi1.cos() * phi2.sin() - phi1.sin() * phi2.cos() * delta_lambda.cos();
    let theta = y.atan2(x);

    (theta.to_degrees() + 360.0) % 360.0
}

/// Tests if a coordinate is within a circular geofence
pub fn is_inside_geofence(
    child_lat: f64,
    child_lon: f64,
    fence_lat: f64,
    fence_lon: f64,
    radius_meters: f64,
) -> bool {
    haversine_distance_meters(child_lat, child_lon, fence_lat, fence_lon) <= radius_meters
}

/// Detects stationary stop points where child remained within `max_radius_meters` for >= `min_duration_ms`
pub fn detect_stop_points(
    points: &[GpsPoint],
    min_duration_ms: i64,
    max_radius_meters: f64,
) -> Vec<StopPoint> {
    let mut stops = Vec::new();
    if points.len() < 2 {
        return stops;
    }

    let mut start_idx = 0;

    while start_idx < points.len() {
        let p_start = &points[start_idx];
        let t_start = match p_start.timestamp_ms {
            Some(t) => t,
            None => {
                start_idx += 1;
                continue;
            }
        };

        let mut end_idx = start_idx + 1;
        let mut last_valid_end = start_idx;

        while end_idx < points.len() {
            let p_curr = &points[end_idx];
            let dist = haversine_distance_meters(
                p_start.latitude,
                p_start.longitude,
                p_curr.latitude,
                p_curr.longitude,
            );

            if dist <= max_radius_meters {
                last_valid_end = end_idx;
                end_idx += 1;
            } else {
                break;
            }
        }

        if last_valid_end > start_idx {
            let p_end = &points[last_valid_end];
            if let Some(t_end) = p_end.timestamp_ms {
                let duration = t_end - t_start;
                if duration >= min_duration_ms {
                    stops.push(StopPoint {
                        latitude: p_start.latitude,
                        longitude: p_start.longitude,
                        duration_minutes: (duration / 60_000) as i32,
                        start_timestamp_ms: t_start,
                        end_timestamp_ms: t_end,
                    });
                    start_idx = last_valid_end + 1;
                    continue;
                }
            }
        }

        start_idx += 1;
    }

    stops
}
