package jo.edu.ras.schoolbell;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** The weekly alarm list, persisted so it survives app kills and reboots. */
final class BellAlarmStore {
    static final class Alarm {
        int id;
        int weekday;      // java.util.Calendar: SUNDAY=1 .. SATURDAY=7
        int hour;
        int minute;
        String title;
        String body;
    }

    private static final String PREFS = "bell_alarms";
    private static final String KEY = "alarms";

    private BellAlarmStore() {}

    static List<Alarm> load(Context ctx) {
        List<Alarm> out = new ArrayList<>();
        String raw = prefs(ctx).getString(KEY, "[]");
        try {
            JSONArray arr = new JSONArray(raw);
            for (int i = 0; i < arr.length(); i++) {
                out.add(fromJson(arr.getJSONObject(i)));
            }
        } catch (JSONException e) {
            // corrupted store: treat as empty, the app re-sends on next launch
        }
        return out;
    }

    static Alarm find(Context ctx, int id) {
        for (Alarm a : load(ctx)) {
            if (a.id == id) return a;
        }
        return null;
    }

    static void save(Context ctx, JSONArray alarms) {
        prefs(ctx).edit().putString(KEY, alarms.toString()).apply();
    }

    static Alarm fromJson(JSONObject o) throws JSONException {
        Alarm a = new Alarm();
        a.id = o.getInt("id");
        a.weekday = o.getInt("weekday");
        a.hour = o.getInt("hour");
        a.minute = o.getInt("minute");
        a.title = o.optString("title", "");
        a.body = o.optString("body", "");
        return a;
    }

    private static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
