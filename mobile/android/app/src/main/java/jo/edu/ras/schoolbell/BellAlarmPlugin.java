package jo.edu.ras.schoolbell;

import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONArray;
import org.json.JSONException;

import java.io.File;
import java.io.FileOutputStream;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** JS API: BellAlarm.setAlarms / clear / status / open*Settings. */
@CapacitorPlugin(name = "BellAlarm")
public class BellAlarmPlugin extends Plugin {

    @PluginMethod
    public void setAlarms(PluginCall call) {
        Context ctx = getContext();
        JSArray alarms = call.getArray("alarms", new JSArray());
        try {
            for (int i = 0; i < alarms.length(); i++) {
                BellAlarmStore.fromJson(alarms.getJSONObject(i));   // validate before replacing
            }
        } catch (JSONException e) {
            call.reject("invalid alarm list: " + e.getMessage());
            return;
        }
        List<BellAlarmStore.Alarm> old = BellAlarmStore.load(ctx);
        BellScheduler.cancel(ctx, old);
        BellAlarmStore.save(ctx, alarms);
        BellScheduler.scheduleAll(ctx);
        JSObject ret = status(ctx);
        call.resolve(ret);
    }

    /** Saves custom tones (base64) under files/sounds; removes ones no longer used. */
    @PluginMethod
    public void setSounds(PluginCall call) {
        File dir = BellScheduler.soundDir(getContext());
        JSArray sounds = call.getArray("sounds", new JSArray());
        Set<String> keep = new HashSet<>();
        try {
            for (int i = 0; i < sounds.length(); i++) {
                org.json.JSONObject o = sounds.getJSONObject(i);
                String name = new File(o.getString("name")).getName();   // no paths
                keep.add(name);
                File f = new File(dir, name);
                if (f.exists() && f.length() > 0) continue;
                byte[] data = android.util.Base64.decode(o.getString("data"), android.util.Base64.DEFAULT);
                File tmp = new File(dir, name + ".tmp");
                try (FileOutputStream out = new FileOutputStream(tmp)) { out.write(data); }
                if (!tmp.renameTo(f)) { tmp.delete(); throw new java.io.IOException("rename failed"); }
            }
        } catch (Exception e) {
            call.reject("could not save tones: " + e.getMessage());
            return;
        }
        File[] old = dir.listFiles();
        if (old != null) for (File f : old) if (!keep.contains(f.getName())) f.delete();
        call.resolve();
    }

    @PluginMethod
    public void clear(PluginCall call) {
        Context ctx = getContext();
        BellScheduler.cancel(ctx, BellAlarmStore.load(ctx));
        BellAlarmStore.save(ctx, new JSONArray());
        call.resolve(status(ctx));
    }

    @PluginMethod
    public void status(PluginCall call) {
        call.resolve(status(getContext()));
    }

    @PluginMethod
    public void openExactAlarmSettings(PluginCall call) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            Intent i = new Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                    Uri.parse("package:" + getContext().getPackageName()));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(i);
        }
        call.resolve();
    }

    @PluginMethod
    public void openBatterySettings(PluginCall call) {
        Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                Uri.parse("package:" + getContext().getPackageName()));
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            getContext().startActivity(i);
        } catch (Exception e) {
            Intent fallback = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
            fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(fallback);
        }
        call.resolve();
    }

    @PluginMethod
    public void testAlarm(PluginCall call) {
        BellAlarmStore.Alarm a = new BellAlarmStore.Alarm();
        a.id = 999999;
        a.title = call.getString("title", "تنبيه تجريبي");
        a.body = call.getString("body", "");
        a.sound = call.getString("sound", "");
        BellAlarmReceiver.show(getContext(), a, null);
        call.resolve();
    }

    static JSObject status(Context ctx) {
        JSObject o = new JSObject();
        o.put("count", BellAlarmStore.load(ctx).size());
        o.put("exact", BellScheduler.canExact(ctx));
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        o.put("notifications", nm.areNotificationsEnabled());
        PowerManager pm = (PowerManager) ctx.getSystemService(Context.POWER_SERVICE);
        o.put("battery", pm.isIgnoringBatteryOptimizations(ctx.getPackageName()));
        return o;
    }
}
