package me.trainingtracker.app;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;
import com.google.firebase.FirebaseApp;

public class MainActivity extends BridgeActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Ensure Firebase is initialized before Capacitor/plugins load (avoids crash when push plugin runs).
        try {
            FirebaseApp.initializeApp(this);
        } catch (Exception ignored) {}
        super.onCreate(savedInstanceState);
    }
}
