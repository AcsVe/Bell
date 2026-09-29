package jo.edu.ras.schoolbell;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.net.Uri;
import android.os.Build;

import java.util.Calendar;
import java.util.List;

/**
 * Arms each weekly alert with AlarmManager.setAlarmClock(): exact to the
 * second, exempt from Doze and battery batching (the same API alarm-clock
 * apps use). After an alarm fires it is re-armed for the same time next week,
 * so alerts keep coming indefinitely with no network and no app launch.
 */
final class BellScheduler {
    static final String CHANNEL_ID = "bell_alarm_v1";
    /** Same alert without a channel sound: the app plays the stage's own tone. */
    static final String CHANNEL_CUSTOM_ID = "bell_alarm_custom_v1";
    static final String EXTRA_ID = "bell_id";
    static final String EXTRA_AT = "bell_at";
    /** A device that was off/asleep through an alert doesn't ring it late. */
    static final long LATE_LIMIT_MS = 10 * 60 * 1000L;

    private BellScheduler() {}

    /** Next moment (strictly after now) matching weekday/hour/minute. */
    static long nextTrigger(int weekday, int hour, int minute, long nowMs) {
        Calendar base = Calendar.getInstance();
        base.setTimeInMillis(nowMs);
        for (int d = 0; d <= 7; d++) {
            Calendar c = (Calendar) base.clone();
            c.add(Calendar.DAY_OF_YEAR, d);
            c.set(Calendar.HOUR_OF_DAY, hour);
            c.set(Calendar.MINUTE, minute);
            c.set(Calendar.SECOND, 0);
            c.set(Calendar.MILLISECOND, 0);
            if (c.get(Calendar.DAY_OF_WEEK) == weekday && c.getTimeInMillis() > nowMs) {
                return c.getTimeInMillis();
            }
        }
        return -1;
    }

    static void scheduleAll(Context ctx) {
        ensureChannel(ctx);
        long now = System.currentTimeMillis();
        for (BellAlarmStore.Alarm a : BellAlarmStore.load(ctx)) {
            scheduleOne(ctx, a, now);
        }
    }

    static void scheduleOne(Context ctx, BellAlarmStore.Alarm a, long now) {
        long at = nextTrigger(a.weekday, a.hour, a.minute, now);
        if (at < 0) return;
        AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
        PendingIntent fire = firePendingIntent(ctx, a.id, at);
        try {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()) {
                AlarmManager.AlarmClockInfo info = new AlarmManager.AlarmClockInfo(at, openAppPendingIntent(ctx, a.id));
                am.setAlarmClock(info, fire);
                return;
            }
        } catch (SecurityException e) {
            // exact alarms revoked — fall through to the best inexact option
        }
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, fire);
    }

    static void cancel(Context ctx, List<BellAlarmStore.Alarm> alarms) {
        AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
        for (BellAlarmStore.Alarm a : alarms) {
            am.cancel(firePendingIntent(ctx, a.id, 0));
        }
    }

    static boolean canExact(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true;
        AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
        return am.canScheduleExactAlarms();
    }

    static PendingIntent firePendingIntent(Context ctx, int id, long at) {
        Intent i = new Intent(ctx, BellAlarmReceiver.class);
        i.setAction("jo.edu.ras.schoolbell.ALARM");
        i.putExtra(EXTRA_ID, id);
        i.putExtra(EXTRA_AT, at);
        return PendingIntent.getBroadcast(ctx, id, i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    static PendingIntent openAppPendingIntent(Context ctx, int id) {
        Intent i = new Intent(ctx, MainActivity.class);
        i.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        i.putExtra(EXTRA_ID, id);
        return PendingIntent.getActivity(ctx, 100000 + id, i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    static java.io.File soundDir(Context ctx) {
        java.io.File d = new java.io.File(ctx.getFilesDir(), "sounds");
        if (!d.exists()) d.mkdirs();
        return d;
    }

    /** The custom tone file for an alarm, or null to use the bundled chime. */
    static java.io.File customSound(Context ctx, String name) {
        if (name == null || name.isEmpty()) return null;
        java.io.File f = new java.io.File(soundDir(ctx), new java.io.File(name).getName());
        return f.exists() && f.length() > 0 ? f : null;
    }

    static void ensureChannel(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm.getNotificationChannel(CHANNEL_CUSTOM_ID) == null) {
            NotificationChannel c = new NotificationChannel(CHANNEL_CUSTOM_ID, "تنبيهات الحصص (نغمة المرحلة)",
                    NotificationManager.IMPORTANCE_HIGH);
            c.setDescription("نهاية وبداية الحصص بنغمة المرحلة");
            c.enableVibration(true);
            c.setVibrationPattern(new long[] {0, 400, 200, 400});
            c.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            c.setSound(null, null);
            nm.createNotificationChannel(c);
        }
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return;
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "تنبيهات الحصص",
                NotificationManager.IMPORTANCE_HIGH);
        ch.setDescription("نهاية وبداية الحصص");
        ch.enableVibration(true);
        ch.setVibrationPattern(new long[] {0, 400, 200, 400});
        ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
        AudioAttributes attrs = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build();
        ch.setSound(soundUri(ctx), attrs);
        nm.createNotificationChannel(ch);
    }

    static Uri soundUri(Context ctx) {
        int res = ctx.getResources().getIdentifier("chime", "raw", ctx.getPackageName());
        return Uri.parse("android.resource://" + ctx.getPackageName() + "/" + res);
    }

    static int smallIcon(Context ctx) {
        int res = ctx.getResources().getIdentifier("ic_stat_bell", "drawable", ctx.getPackageName());
        return res != 0 ? res : ctx.getApplicationInfo().icon;
    }
}
