use hmac::{Hmac, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// Validates whether device wall clock has been tampered with by comparing
/// elapsed wall time with monotonic hardware elapsed time
pub fn is_clock_tampered(
    current_wall_ms: i64,
    last_saved_wall_ms: i64,
    current_mono_ms: i64,
    last_saved_mono_ms: i64,
    tolerance_ms: i64,
) -> bool {
    let delta_wall = current_wall_ms - last_saved_wall_ms;
    let delta_mono = current_mono_ms - last_saved_mono_ms;

    // If wall clock moved backward
    if delta_wall < -tolerance_ms {
        return true;
    }

    // Monotonic clock should never jump backward on hardware
    if delta_mono < 0 {
        return true;
    }

    // If wall clock advanced significantly more than monotonic hardware clock (child moved time forward to bypass limit)
    let drift = (delta_wall - delta_mono).abs();
    drift > tolerance_ms
}

/// Checks if current local hour and minute (e.g. 23:15) fall inside bedtime window (e.g. 22:00 to 07:00)
pub fn is_within_bedtime_window(
    current_hour: u32,
    current_min: u32,
    start_hour: u32,
    start_min: u32,
    end_hour: u32,
    end_min: u32,
) -> bool {
    let cur_total = current_hour * 60 + current_min;
    let start_total = start_hour * 60 + start_min;
    let end_total = end_hour * 60 + end_min;

    if start_total <= end_total {
        // Bedtime in same day: e.g. 14:00 to 16:00
        cur_total >= start_total && cur_total < end_total
    } else {
        // Bedtime spans across midnight: e.g. 22:00 to 07:00
        cur_total >= start_total || cur_total < end_total
    }
}

/// Verifies HMAC-SHA256 signature for coin transactions / telemetry commands
pub fn verify_hmac_signature(message: &[u8], signature_hex: &str, secret_key: &[u8]) -> bool {
    let mut mac = match HmacSha256::new_from_slice(secret_key) {
        Ok(m) => m,
        Err(_) => return false,
    };
    mac.update(message);

    let signature_bytes = match hex_to_bytes(signature_hex) {
        Some(b) => b,
        None => return false,
    };

    mac.verify_slice(&signature_bytes).is_ok()
}

fn hex_to_bytes(hex: &str) -> Option<Vec<u8>> {
    if hex.len() % 2 != 0 {
        return None;
    }
    let mut bytes = Vec::with_capacity(hex.len() / 2);
    for i in (0..hex.len()).step_by(2) {
        let byte_str = &hex[i..i + 2];
        let byte = match u8::from_str_radix(byte_str, 16) {
            Ok(b) => b,
            Err(_) => return None,
        };
        bytes.push(byte);
    }
    Some(bytes)
}
