package com.unipeacher.schedule;

import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.IBinder;
import android.webkit.CookieManager;

import androidx.annotation.Nullable;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class ScheduleSyncService extends Service {

    private static final int FOREGROUND_NOTIFICATION_ID = 1001;
    private ScheduledExecutorService scheduler;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationHelper.createNotificationChannel(this);
        startForeground(FOREGROUND_NOTIFICATION_ID, NotificationHelper.buildForegroundNotification(this));
        startPolling();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (scheduler == null || scheduler.isShutdown()) {
            startPolling();
        }
        return START_STICKY;
    }

    private void startPolling() {
        if (scheduler != null && !scheduler.isShutdown()) {
            scheduler.shutdownNow();
        }
        scheduler = Executors.newSingleThreadScheduledExecutor();
        // 每 25 秒轻量检查一次后台通知
        scheduler.scheduleWithFixedDelay(this::checkNotifications, 2, 25, TimeUnit.SECONDS);
    }

    private void checkNotifications() {
        try {
            SharedPreferences pref = getSharedPreferences("schedule_pref", Context.MODE_PRIVATE);
            String serverUrl = pref.getString("server_url", getString(R.string.default_server_url));
            if (serverUrl == null || serverUrl.trim().isEmpty()) {
                return;
            }

            if (!serverUrl.endsWith("/")) {
                serverUrl = serverUrl + "/";
            }
            String authUid = pref.getString("auth_uid", "");
            String pollUrl = serverUrl + "api/notifications/poll" + (authUid.isEmpty() ? "" : ("?uid=" + authUid));

            URL url = new URL(pollUrl);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(6000);
            conn.setReadTimeout(6000);

            try {
                String cookie = CookieManager.getInstance().getCookie(serverUrl);
                if (cookie != null && !cookie.isEmpty()) {
                    conn.setRequestProperty("Cookie", cookie);
                }
            } catch (Exception ignored) {
            }

            conn.setRequestProperty("User-Agent", "ScheduleAndroidApp/1.3.0");

            int code = conn.getResponseCode();
            if (code == 200) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) {
                    sb.append(line);
                }
                reader.close();

                JSONObject res = new JSONObject(sb.toString());
                if (res.optBoolean("ok")) {
                    JSONArray arr = res.optJSONArray("notifications");
                    if (arr != null && arr.length() > 0) {
                        for (int i = 0; i < arr.length(); i++) {
                            JSONObject item = arr.getJSONObject(i);
                            int nid = item.optInt("id", (int) (System.currentTimeMillis() % 100000000));
                            String title = item.optString("title", "🐾 线条小狗日程提醒");
                            String content = item.optString("content", "");
                            String targetAction = item.optString("target_action", "");
                            NotificationHelper.showNotification(getApplicationContext(), nid, title, content, targetAction);
                        }
                    }
                }
            }
            conn.disconnect();
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        if (scheduler != null) {
            scheduler.shutdownNow();
            scheduler = null;
        }
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
