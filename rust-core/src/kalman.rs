use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GpsPoint {
    pub latitude: f64,
    pub longitude: f64,
    pub accuracy: Option<f64>,
    pub speed: Option<f64>,
    pub timestamp_ms: Option<i64>,
    pub is_offline_cached: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StopPoint {
    pub latitude: f64,
    pub longitude: f64,
    pub duration_minutes: i32,
    pub start_timestamp_ms: i64,
    pub end_timestamp_ms: i64,
}

pub struct KalmanFilter2D {
    decay_var: f64,
    est_lat: f64,
    est_lon: f64,
    variance: f64,
    initialized: bool,
}

impl KalmanFilter2D {
    pub fn new(decay_var: f64) -> Self {
        Self {
            decay_var,
            est_lat: 0.0,
            est_lon: 0.0,
            variance: 5.0,
            initialized: false,
        }
    }

    pub fn filter(&mut self, lat: f64, lon: f64, accuracy: f64) -> (f64, f64) {
        if !self.initialized {
            self.est_lat = lat;
            self.est_lon = lon;
            self.variance = accuracy * accuracy;
            self.initialized = true;
            return (lat, lon);
        }

        // Increase estimate variance by process noise
        self.variance += self.decay_var;

        // Measurement noise variance
        let meas_var = if accuracy > 0.0 { accuracy * accuracy } else { 16.0 };

        // Kalman gain
        let k = self.variance / (self.variance + meas_var);

        // Update state estimates
        self.est_lat += k * (lat - self.est_lat);
        self.est_lon += k * (lon - self.est_lon);

        // Update variance
        self.variance = (1.0 - k) * self.variance;

        (self.est_lat, self.est_lon)
    }
}

/// Batch smooths GPS points using Kalman filtering and anti-drift speed clamping
pub fn smooth_gps_trajectory(points: &[GpsPoint]) -> Vec<GpsPoint> {
    if points.len() < 2 {
        return points.to_vec();
    }

    let mut filter = KalmanFilter2D::new(3.0);
    let mut smoothed: Vec<GpsPoint> = Vec::with_capacity(points.len());

    for pt in points {
        let acc = pt.accuracy.unwrap_or(15.0);

        // Anti-drift: skip massive outliers (accuracy > 70m while stationary)
        if acc > 70.0 && pt.speed.unwrap_or(0.0) < 1.0 {
            continue;
        }

        let (flat, flon) = filter.filter(pt.latitude, pt.longitude, acc);
        smoothed.push(GpsPoint {
            latitude: flat,
            longitude: flon,
            accuracy: pt.accuracy,
            speed: pt.speed,
            timestamp_ms: pt.timestamp_ms,
            is_offline_cached: pt.is_offline_cached,
        });
    }

    if smoothed.is_empty() {
        points.to_vec()
    } else {
        smoothed
    }
}
