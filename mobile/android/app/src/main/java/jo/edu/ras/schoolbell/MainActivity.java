package jo.edu.ras.schoolbell;

import android.os.Build;
import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(BellAlarmPlugin.class);
        super.onCreate(savedInstanceState);
        // Opened from an alert: show over the lock screen and turn the screen on.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        }
        // Re-arm on every launch in case the system dropped alarms.
        BellScheduler.scheduleAll(this);
    }
}
