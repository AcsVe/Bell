package jo.edu.ras.schoolbell;

import android.app.Notification;
import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import java.io.File;
import java.util.concurrent.atomic.AtomicBoolean;

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
        // Re-arm for next week (1 minute ahead so it can't re-match this minute).
        BellScheduler.scheduleOne(ctx, a, Math.max(now, at) + 60_000L);
        if (at == 0L || now - at <= BellScheduler.LATE_LIMIT_MS) {
            show(ctx, a, goAsync());
        }
    }

    /** pending: keeps the receiver alive while a custom tone plays (may be null). */
    static void show(Context ctx, BellAlarmStore.Alarm a, PendingResult pending) {
        BellScheduler.ensureChannel(ctx);
        File custom = BellScheduler.customSound(ctx, a.sound);
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(ctx, custom != null ? BellScheduler.CHANNEL_CUSTOM_ID : BellScheduler.CHANNEL_ID)
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
             .setVibrate(new long[] {0, 400, 200, 400});
            if (custom == null) b.setSound(BellScheduler.soundUri(ctx));
        }
        try {
            nm.notify(a.id, b.build());
        } catch (SecurityException e) {
            // notifications not permitted by the user — nothing we can do here
        }
        // Stage tone; if the file can't be played, the bundled chime instead.
        if (custom == null || !(play(ctx, android.net.Uri.fromFile(custom), pending)
                                || play(ctx, BellScheduler.soundUri(ctx), pending))) {
            if (pending != null) pending.finish();
        }
    }

    /** Plays the stage's tone on the alarm stream (sounds even in silent mode,
     *  like the channel chime). At most ~9 s so the receiver finishes in time. */
    static boolean play(Context ctx, android.net.Uri uri, PendingResult pending) {
        final MediaPlayer mp = new MediaPlayer();
        final AtomicBoolean done = new AtomicBoolean(false);
        final Runnable stop = () -> {
            if (!done.compareAndSet(false, true)) return;
            try { if (mp.isPlaying()) mp.stop(); } catch (Exception ignored) { }
            mp.release();
            if (pending != null) pending.finish();
        };
        try {
            mp.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            mp.setDataSource(ctx, uri);
            mp.setOnCompletionListener(m -> stop.run());
            mp.setOnErrorListener((m, w, e) -> { stop.run(); return true; });
            mp.prepare();
            mp.start();
            new Handler(Looper.getMainLooper()).postDelayed(stop, 9_000L);
            return true;
        } catch (Exception e) {
            mp.release();
            return false;
        }
    }
}
