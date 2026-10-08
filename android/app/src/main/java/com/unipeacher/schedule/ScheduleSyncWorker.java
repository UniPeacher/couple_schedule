package com.unipeacher.schedule;

import android.content.Context;
import android.content.SharedPreferences;
import android.webkit.CookieManager;

import androidx.annotation.NonNull;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.TimeUnit;

public class ScheduleSyncWorker extends Worker {

    private static final String WORK_NAME = "ScheduleNotificationSyncWork";

    public ScheduleSyncWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    public static void enqueuePeriodicWork(Context context) {
        Constraints constraints = new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build();

        PeriodicWorkRequest syncRequest = new PeriodicWorkRequest.Builder(
                ScheduleSyncWorker.class,
                15, TimeUnit.MINUTES
        )
                .setConstraints(constraints)
                .build();

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                syncRequest
        );
    }

    @NonNull
    @Override
    public Result doWork() {
        Context context = getApplicationContext();
        try {
            SharedPreferences pref = context.getSharedPreferences("schedule_pref", Context.MODE_PRIVATE);
            String serverUrl = pref.getString("server_url", context.getString(R.string.default_server_url));
            if (serverUrl == null || serverUrl.trim().isEmpty()) {
                return Result.success();
            }

            if (!serverUrl.endsWith("/")) {
                serverUrl = serverUrl + "/";
            }
            String pollUrl = serverUrl + "api/notifications/poll";

            URL url = new URL(pollUrl);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(8000);

            // 从 WebView CookieManager 中获取当前已登录用户的 Cookie
            try {
                String cookie = CookieManager.getInstance().getCookie(serverUrl);
                if (cookie != null && !cookie.isEmpty()) {
                    conn.setRequestProperty("Cookie", cookie);
                }
            } catch (Exception ignored) {
            }

            conn.setRequestProperty("User-Agent", "ScheduleAndroidApp/1.1.0");

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
                            NotificationHelper.showNotification(context, nid, title, content);
                        }
                    }
                }
            }
            conn.disconnect();
        } catch (Exception e) {
            return Result.retry();
        }

        return Result.success();
    }
}
