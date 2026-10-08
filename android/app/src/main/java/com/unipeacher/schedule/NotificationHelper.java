package com.unipeacher.schedule;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.os.Build;
import androidx.core.app.NotificationCompat;

public class NotificationHelper {
    public static final String CHANNEL_ID = "couple_schedule_channel";
    public static final String CHANNEL_NAME = "情侣日程与留言提醒";

    public static final String KEEPALIVE_CHANNEL_ID = "couple_schedule_keepalive";
    public static final String KEEPALIVE_CHANNEL_NAME = "小窝后台守护通道";

    public static void createNotificationChannel(Context context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager manager = context.getSystemService(NotificationManager.class);
            if (manager == null) return;

            // 1. 高优先级通知渠道（弹窗、响铃、震动）
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    CHANNEL_NAME,
                    NotificationManager.IMPORTANCE_HIGH
            );
            channel.setDescription("用于接收对方新建日程、修改时间及小狗留言提醒");
            channel.enableVibration(true);
            channel.enableLights(true);
            channel.setLightColor(Color.parseColor("#ff9bb2"));
            manager.createNotificationChannel(channel);

            // 2. 低优先级保活渠道（静默常驻，防止系统杀后台，无声音震动）
            NotificationChannel keepaliveChannel = new NotificationChannel(
                    KEEPALIVE_CHANNEL_ID,
                    KEEPALIVE_CHANNEL_NAME,
                    NotificationManager.IMPORTANCE_LOW
            );
            keepaliveChannel.setDescription("用于在后台静默保持二人日程与留言及时触达");
            keepaliveChannel.enableVibration(false);
            keepaliveChannel.setSound(null, null);
            manager.createNotificationChannel(keepaliveChannel);
        }
    }

    public static android.app.Notification buildForegroundNotification(Context context) {
        createNotificationChannel(context);
        Intent intent = new Intent(context, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                context, 9999, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        return new NotificationCompat.Builder(context, KEEPALIVE_CHANNEL_ID)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle("🐾 线条小狗日程守护中")
                .setContentText("正在后台守护二人日程与留言实时提醒")
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setOngoing(true)
                .setContentIntent(pendingIntent)
                .build();
    }

    public static void showNotification(Context context, int id, String title, String content) {
        showNotification(context, id, title, content, "");
    }

    public static void showNotification(Context context, int id, String title, String content, String targetAction) {
        createNotificationChannel(context);

        Intent intent = new Intent(context, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        if (targetAction != null && !targetAction.isEmpty()) {
            intent.putExtra("target_action", targetAction);
        }
        PendingIntent pendingIntent = PendingIntent.getActivity(
                context, id, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(title)
                .setContentText(content)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(content))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setDefaults(NotificationCompat.DEFAULT_ALL)
                .setAutoCancel(true)
                .setContentIntent(pendingIntent);

        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.notify(id, builder.build());
        }
    }
}
