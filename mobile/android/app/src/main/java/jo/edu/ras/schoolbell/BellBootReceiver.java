package jo.edu.ras.schoolbell;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Alarms are wiped by a reboot, an app update or a clock/time-zone change;
 *  re-arm everything from the saved list (no network needed). */
public class BellBootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        BellScheduler.scheduleAll(ctx);
    }
}
