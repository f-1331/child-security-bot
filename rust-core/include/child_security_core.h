#ifndef CHILD_SECURITY_CORE_H
#define CHILD_SECURITY_CORE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Fast Haversine Distance in meters
double rust_c_haversine_distance(double lat1, double lon1, double lat2, double lon2);

// Bearing direction in degrees (0-360)
double rust_c_calculate_bearing(double lat1, double lon1, double lat2, double lon2);

// Geofence point-in-circle check
bool rust_c_is_inside_geofence(double child_lat, double child_lon, double fence_lat, double fence_lon, double radius);

// Clock tampering and monotonic time validator
bool rust_c_check_clock_tampered(int64_t current_wall_ms, int64_t last_saved_wall_ms, int64_t current_mono_ms, int64_t last_saved_mono_ms, int64_t tolerance_ms);

// Bedtime schedule check (offline without timezone/network)
bool rust_c_is_bedtime(uint32_t current_hour, uint32_t current_min, uint32_t start_hour, uint32_t start_min, uint32_t end_hour, uint32_t end_min);

#ifdef __cplusplus
}
#endif

#endif // CHILD_SECURITY_CORE_H
