package jo.edu.ras.schoolbell;

import android.app.Notification;
import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/** Fires when an alarm goes off: shows the alert (sound + heads-up / full
 *  screen) and re-arms the same alert for next week. */
public class BellAlarmReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        int id = intent.getIntExtra(BellScheduler.EXTRA_ID, -1);
        long at = intent.getLongExtra(BellScheduler.EXTRA_AT, 0L);
        BellAlarmStore.Alarm a = BellAlarmStore.find(ctx, id);
        if (a == null) return;                      // removed since it was armed
        long now = System.currentTimeMillis();
        if (at == 0L || now - at <= BellScheduler.LATE_LIMIT_MS) {
            show(ctx, a);
        }
        // Re-arm for next week (1 minute ahead so it can't re-match this minute).
        BellScheduler.scheduleOne(ctx, a, Math.max(now, at) + 60_000L);
    }

    static void show(Context ctx, BellAlarmStore.Alarm a) {
        BellScheduler.ensureChannel(ctx);
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(ctx, BellScheduler.CHANNEL_ID)
                : new Notification.Builder(ctx);
        b.setSmallIcon(BellScheduler.smallIcon(ctx))
                .setContentTitle(a.title)
                .setContentText(a.body)
                .setStyle(new Notification.BigTextStyle().bigText(a.body))
                .setCategory(Notification.CATEGORY_ALARM)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setAutoCancel(true)
                .setWhen(System.currentTimeMillis())
                .setShowWhen(true)
                .setContentIntent(BellScheduler.openAppPendingIntent(ctx, a.id))
                // Wakes the screen and shows the app's big alert when allowed.
                .setFullScreenIntent(BellScheduler.openAppPendingIntent(ctx, a.id), true);
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            b.setPriority(Notification.PRIORITY_MAX)
             .setSound(BellScheduler.soundUri(ctx))
             .setVibrate(new long[] {0, 400, 200, 400});
        }
        try {
            nm.notify(a.id, b.build());
        } catch (SecurityException e) {
            // notifications not permitted by the user — nothing we can do here
        }
    }
}
