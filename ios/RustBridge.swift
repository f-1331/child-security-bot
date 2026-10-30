import Foundation

/**
 * RustCoreBridge for iOS:
 * Links directly with rust-core C-FFI exports (rust_c_*) or falls back to
 * pure Swift high-speed trigonometric & Kalman filters when dynamic linking is pending.
 */
public final class RustCoreBridge {

    public static let shared = RustCoreBridge()
    private init() {}

    // MARK: - Haversine Distance (Meters)
    public func haversineDistance(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
        #if canImport(ChildSecurityCore)
        return rust_c_haversine_distance(lat1, lon1, lat2, lon2)
        #else
        let r = 6371000.0
        let toRad = Double.pi / 180.0
        let dLat = (lat2 - lat1) * toRad
        let dLon = (lon2 - lon1) * toRad
        let rLat1 = lat1 * toRad
        let rLat2 = lat2 * toRad

        let a = sin(dLat / 2.0) * sin(dLat / 2.0) +
                cos(rLat1) * cos(rLat2) * sin(dLon / 2.0) * sin(dLon / 2.0)
        let c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a))
        return r * c
        #endif
    }

    // MARK: - Compass Bearing Angle (0-360 Degrees)
    public func calculateBearing(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
        #if canImport(ChildSecurityCore)
        return rust_c_calculate_bearing(lat1, lon1, lat2, lon2)
        #else
        let toRad = Double.pi / 180.0
        let phi1 = lat1 * toRad
        let phi2 = lat2 * toRad
        let dLambda = (lon2 - lon1) * toRad

        let y = sin(dLambda) * cos(phi2)
        let x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dLambda)
        let theta = atan2(y, x)
        return ((theta * 180.0 / Double.pi) + 360.0).truncatingRemainder(dividingBy: 360.0)
        #endif
    }

    // MARK: - Anti-Tamper: Monotonic System Clock vs Wall Clock Check
    public func isClockTampered(currentWallMs: Int64, lastSavedWallMs: Int64, currentMonoMs: Int64, lastSavedMonoMs: Int64, toleranceMs: Int64 = 30000) -> Bool {
        #if canImport(ChildSecurityCore)
        return rust_c_check_clock_tampered(currentWallMs, lastSavedWallMs, currentMonoMs, lastSavedMonoMs, toleranceMs)
        #else
        if lastSavedWallMs <= 0 || lastSavedMonoMs <= 0 { return false }
        let wallDelta = currentWallMs - lastSavedWallMs
        let monoDelta = currentMonoMs - lastSavedMonoMs
        if monoDelta >= 5000 && (wallDelta < -toleranceMs || abs(wallDelta - monoDelta) > 1800000) {
            return true
        }
        return false
        #endif
    }

    // MARK: - Offline Bedtime Window Evaluation
    public func isBedtime(currentHour: UInt32, currentMin: UInt32, startHour: UInt32, startMin: UInt32, endHour: UInt32, endMin: UInt32) -> Bool {
        #if canImport(ChildSecurityCore)
        return rust_c_is_bedtime(currentHour, currentMin, startHour, startMin, endHour, endMin)
        #else
        let cur = currentHour * 60 + currentMin
        let start = startHour * 60 + startMin
        let end = endHour * 60 + endMin
        if start <= end {
            return cur >= start && cur < end
        } else {
            return cur >= start || cur < end
        }
        #endif
    }
}
