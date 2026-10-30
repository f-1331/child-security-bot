package uz.childsecurity.app;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Offline SQLite Database Helper for Child Security Tracker.
 * Reliably stores GPS coordinates, battery stats, and geofence events locally
 * when internet is disconnected (Offline-First architecture).
 */
public class OfflineDbHelper extends SQLiteOpenHelper {

    private static final String TAG = "OfflineDbHelper";
    private static final String DATABASE_NAME = "child_security_offline.db";
    private static final int DATABASE_VERSION = 1;

    public static final String TABLE_LOCATIONS = "offline_locations";
    public static final String COL_ID = "id";
    public static final String COL_CHILD_ID = "child_id";
    public static final String COL_LATITUDE = "latitude";
    public static final String COL_LONGITUDE = "longitude";
    public static final String COL_ACCURACY = "accuracy";
    public static final String COL_SPEED_KMH = "speed_kmh";
    public static final String COL_BATTERY = "battery_level";
    public static final String COL_IS_CHARGING = "is_charging";
    public static final String COL_SOUND_MODE = "sound_mode";
    public static final String COL_IS_SOS = "is_sos_active";
    public static final String COL_RECORDED_AT = "recorded_at"; // ISO string or epoch ms
    public static final String COL_SYNCED = "is_synced";

    public static final String TABLE_EVENTS = "offline_events";
    public static final String COL_EVENT_TYPE = "event_type";
    public static final String COL_EVENT_PAYLOAD = "payload";

    private static OfflineDbHelper instance;

    public static synchronized OfflineDbHelper getInstance(Context context) {
        if (instance == null) {
            instance = new OfflineDbHelper(context.getApplicationContext());
        }
        return instance;
    }

    private OfflineDbHelper(Context context) {
        super(context, DATABASE_NAME, null, DATABASE_VERSION);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        String createLocationsTable = "CREATE TABLE " + TABLE_LOCATIONS + " ("
                + COL_ID + " INTEGER PRIMARY KEY AUTOINCREMENT, "
                + COL_CHILD_ID + " TEXT, "
                + COL_LATITUDE + " REAL, "
                + COL_LONGITUDE + " REAL, "
                + COL_ACCURACY + " REAL, "
                + COL_SPEED_KMH + " INTEGER, "
                + COL_BATTERY + " INTEGER, "
                + COL_IS_CHARGING + " INTEGER, "
                + COL_SOUND_MODE + " TEXT, "
                + COL_IS_SOS + " INTEGER, "
                + COL_RECORDED_AT + " INTEGER, "
                + COL_SYNCED + " INTEGER DEFAULT 0"
                + ");";

        String createEventsTable = "CREATE TABLE " + TABLE_EVENTS + " ("
                + COL_ID + " INTEGER PRIMARY KEY AUTOINCREMENT, "
                + COL_CHILD_ID + " TEXT, "
                + COL_EVENT_TYPE + " TEXT, "
                + COL_EVENT_PAYLOAD + " TEXT, "
                + COL_RECORDED_AT + " INTEGER, "
                + COL_SYNCED + " INTEGER DEFAULT 0"
                + ");";

        db.execSQL(createLocationsTable);
        db.execSQL(createEventsTable);
        Log.i(TAG, "Offline SQLite tables initialized successfully.");
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        db.execSQL("DROP TABLE IF EXISTS " + TABLE_LOCATIONS);
        db.execSQL("DROP TABLE IF EXISTS " + TABLE_EVENTS);
        onCreate(db);
    }

    /**
     * Store a GPS waypoint locally when offline or in poor network conditions
     */
    public synchronized long insertLocation(String childId, double lat, double lon, float accuracy,
                                            int speedKmh, int battery, boolean isCharging,
                                            String soundMode, boolean isSos, long recordedAtMs) {
        SQLiteDatabase db = this.getWritableDatabase();
        ContentValues cv = new ContentValues();
        cv.put(COL_CHILD_ID, childId);
        cv.put(COL_LATITUDE, lat);
        cv.put(COL_LONGITUDE, lon);
        cv.put(COL_ACCURACY, accuracy);
        cv.put(COL_SPEED_KMH, speedKmh);
        cv.put(COL_BATTERY, battery);
        cv.put(COL_IS_CHARGING, isCharging ? 1 : 0);
        cv.put(COL_SOUND_MODE, soundMode);
        cv.put(COL_IS_SOS, isSos ? 1 : 0);
        cv.put(COL_RECORDED_AT, recordedAtMs);
        cv.put(COL_SYNCED, 0);

        long rowId = db.insert(TABLE_LOCATIONS, null, cv);
        // Keep maximum 3,000 offline points to prevent unlimited DB bloat
        pruneOldRecords(db);
        return rowId;
    }

    private void pruneOldRecords(SQLiteDatabase db) {
        try {
            db.execSQL("DELETE FROM " + TABLE_LOCATIONS + " WHERE " + COL_ID + " NOT IN ("
                    + "SELECT " + COL_ID + " FROM " + TABLE_LOCATIONS + " ORDER BY " + COL_ID + " DESC LIMIT 3000);");
        } catch (Exception ignored) {}
    }

    /**
     * Get count of unsynced offline records
     */
    public synchronized int getUnsyncedCount() {
        int count = 0;
        SQLiteDatabase db = this.getReadableDatabase();
        Cursor cursor = null;
        try {
            cursor = db.rawQuery("SELECT COUNT(*) FROM " + TABLE_LOCATIONS + " WHERE " + COL_SYNCED + " = 0", null);
            if (cursor.moveToFirst()) {
                count = cursor.getInt(0);
            }
        } catch (Exception e) {
            Log.e(TAG, "Error getting unsynced count: " + e.getMessage());
        } finally {
            if (cursor != null) cursor.close();
        }
        return count;
    }

    /**
     * Retrieve batch of unsynced offline GPS locations formatted for REST API
     */
    public synchronized JSONArray getUnsyncedBatch(int limit) {
        JSONArray array = new JSONArray();
        SQLiteDatabase db = this.getReadableDatabase();
        Cursor cursor = null;
        try {
            cursor = db.query(TABLE_LOCATIONS, null, COL_SYNCED + " = 0", null, null, null, COL_ID + " ASC", String.valueOf(limit));
            if (cursor.moveToFirst()) {
                do {
                    JSONObject obj = new JSONObject();
                    obj.put("db_id", cursor.getLong(cursor.getColumnIndexOrThrow(COL_ID)));
                    obj.put("latitude", cursor.getDouble(cursor.getColumnIndexOrThrow(COL_LATITUDE)));
                    obj.put("longitude", cursor.getDouble(cursor.getColumnIndexOrThrow(COL_LONGITUDE)));
                    obj.put("accuracy", cursor.getFloat(cursor.getColumnIndexOrThrow(COL_ACCURACY)));
                    obj.put("speed", cursor.getInt(cursor.getColumnIndexOrThrow(COL_SPEED_KMH)));
                    obj.put("speed_kmh", cursor.getInt(cursor.getColumnIndexOrThrow(COL_SPEED_KMH)));
                    obj.put("battery_level", cursor.getInt(cursor.getColumnIndexOrThrow(COL_BATTERY)));
                    obj.put("is_charging", cursor.getInt(cursor.getColumnIndexOrThrow(COL_IS_CHARGING)) == 1);
                    obj.put("sound_mode", cursor.getString(cursor.getColumnIndexOrThrow(COL_SOUND_MODE)));
                    obj.put("is_sos_active", cursor.getInt(cursor.getColumnIndexOrThrow(COL_IS_SOS)) == 1);
                    obj.put("recorded_at_epoch_ms", cursor.getLong(cursor.getColumnIndexOrThrow(COL_RECORDED_AT)));
                    obj.put("is_offline_cached", true);
                    array.put(obj);
                } while (cursor.moveToNext());
            }
        } catch (Exception e) {
            Log.e(TAG, "Error fetching unsynced batch: " + e.getMessage());
        } finally {
            if (cursor != null) cursor.close();
        }
        return array;
    }

    /**
     * Mark records as synced or delete them after successful API transmission
     */
    public synchronized void deleteSyncedRecords(long maxId) {
        SQLiteDatabase db = this.getWritableDatabase();
        try {
            db.delete(TABLE_LOCATIONS, COL_ID + " <= ?", new String[]{String.valueOf(maxId)});
            Log.d(TAG, "Pruned synced offline GPS records up to ID: " + maxId);
        } catch (Exception e) {
            Log.e(TAG, "Error deleting synced records: " + e.getMessage());
        }
    }

    /**
     * Retrieve the most recent recorded GPS location from offline database.
     */
    public synchronized JSONObject getLatestLocation() {
        SQLiteDatabase db = this.getReadableDatabase();
        Cursor cursor = null;
        try {
            cursor = db.query(TABLE_LOCATIONS, null, null, null, null, null, COL_ID + " DESC", "1");
            if (cursor.moveToFirst()) {
                JSONObject obj = new JSONObject();
                obj.put("latitude", cursor.getDouble(cursor.getColumnIndexOrThrow(COL_LATITUDE)));
                obj.put("longitude", cursor.getDouble(cursor.getColumnIndexOrThrow(COL_LONGITUDE)));
                obj.put("accuracy", cursor.getFloat(cursor.getColumnIndexOrThrow(COL_ACCURACY)));
                obj.put("speed_kmh", cursor.getInt(cursor.getColumnIndexOrThrow(COL_SPEED_KMH)));
                obj.put("battery_level", cursor.getInt(cursor.getColumnIndexOrThrow(COL_BATTERY)));
                obj.put("recorded_at_epoch_ms", cursor.getLong(cursor.getColumnIndexOrThrow(COL_RECORDED_AT)));
                return obj;
            }
        } catch (Exception e) {
            Log.e(TAG, "Error fetching latest location: " + e.getMessage());
        } finally {
            if (cursor != null) cursor.close();
        }
        return null;
    }
}
