package com.unipeacher.schedule;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import org.json.JSONObject;

import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

public class PushManager {
    private static final String TAG = "PushManager";
    private static final String NTFY_WS_BASE = "wss://push.unipeacher.email/";
    private static final String TOPIC_PREFIX = "couple_schedule_uni_";

    private static PushManager instance;
    private final Context context;
    private final OkHttpClient client;
    private WebSocket webSocket;
    private boolean isConnecting = false;

    private PushManager(Context context) {
        this.context = context.getApplicationContext();
        this.client = new OkHttpClient.Builder()
                .readTimeout(0, TimeUnit.MILLISECONDS)
                .pingInterval(20, TimeUnit.SECONDS)
                .retryOnConnectionFailure(true)
                .build();
    }

    public static synchronized PushManager getInstance(Context context) {
        if (instance == null) {
            instance = new PushManager(context);
        }
        return instance;
    }

    public synchronized void start() {
        SharedPreferences pref = context.getSharedPreferences("schedule_pref", Context.MODE_PRIVATE);
        String authUid = pref.getString("auth_uid", "");
        if (authUid.isEmpty()) {
            Log.d(TAG, "auth_uid is empty, skipping push websocket connection");
            return;
        }

        if (webSocket != null || isConnecting) {
            return;
        }

        isConnecting = true;
        String topic = TOPIC_PREFIX + authUid;
        String wsUrl = NTFY_WS_BASE + topic + "/ws";

        Log.d(TAG, "Connecting to ntfy push ws: " + wsUrl);

        Request request = new Request.Builder()
                .url(wsUrl)
                .build();

        webSocket = client.newWebSocket(request, new WebSocketListener() {
            @Override
            public void onOpen(WebSocket ws, Response response) {
                isConnecting = false;
                Log.d(TAG, "WebSocket connected successfully to topic: " + topic);
            }

            @Override
            public void onMessage(WebSocket ws, String text) {
                try {
                    JSONObject obj = new JSONObject(text);
                    String event = obj.optString("event");
                    if ("message".equals(event)) {
                        int id = (int) (System.currentTimeMillis() % 100000000);
                        String title = obj.optString("title", "🐾 线条小狗日程提醒");
                        String message = obj.optString("message", "");
                        String click = obj.optString("click", "");
                        String targetAction = "";
                        if (click.startsWith("schedule://open?action=")) {
                            targetAction = click.substring("schedule://open?action=".length());
                        }
                        NotificationHelper.showNotification(context, id, title, message, targetAction);
                    }
                } catch (Exception e) {
                    Log.e(TAG, "Error parsing push message", e);
                }
            }

            @Override
            public void onClosed(WebSocket ws, int code, String reason) {
                isConnecting = false;
                webSocket = null;
                scheduleReconnect();
            }

            @Override
            public void onFailure(WebSocket ws, Throwable t, Response response) {
                Log.e(TAG, "WebSocket failure: " + (t != null ? t.getMessage() : "unknown"), t);
                isConnecting = false;
                webSocket = null;
                scheduleReconnect();
            }
        });
    }

    private void scheduleReconnect() {
        new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(this::start, 5000);
    }
}
