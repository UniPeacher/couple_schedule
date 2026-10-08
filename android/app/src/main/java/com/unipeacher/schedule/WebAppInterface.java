package com.unipeacher.schedule;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.widget.Toast;

public class WebAppInterface {
    private final Context mContext;

    public WebAppInterface(Context context) {
        this.mContext = context;
    }

    @JavascriptInterface
    public boolean isNativeApp() {
        return true;
    }

    @JavascriptInterface
    public void postNotification(String title, String content) {
        postNotification(title, content, "");
    }

    @JavascriptInterface
    public void postNotification(String title, String content, String targetAction) {
        int id = (int) (System.currentTimeMillis() % 100000000);
        NotificationHelper.showNotification(mContext, id, title, content, targetAction);
    }

    @JavascriptInterface
    public void saveAuthUid(String uid) {
        if (uid != null && !uid.trim().isEmpty()) {
            SharedPreferences pref = mContext.getSharedPreferences("schedule_pref", Context.MODE_PRIVATE);
            pref.edit().putString("auth_uid", uid.trim()).apply();
            // 确保 WebView 的 Cookie 立即持久化写入闪存
            try {
                CookieManager.getInstance().flush();
            } catch (Exception ignored) {
            }
        }
    }

    @JavascriptInterface
    public void requestBatteryOptimization() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                PowerManager pm = (PowerManager) mContext.getSystemService(Context.POWER_SERVICE);
                if (pm != null && !pm.isIgnoringBatteryOptimizations(mContext.getPackageName())) {
                    Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                    intent.setData(Uri.parse("package:" + mContext.getPackageName()));
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    mContext.startActivity(intent);
                } else {
                    Toast.makeText(mContext, "已处于电池无限制白名单中 🐾", Toast.LENGTH_SHORT).show();
                }
            } catch (Exception e) {
                try {
                    Intent intent = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    mContext.startActivity(intent);
                } catch (Exception ignored) {
                }
            }
        }
    }

    @JavascriptInterface
    public void showToast(String message) {
        Toast.makeText(mContext, message, Toast.LENGTH_SHORT).show();
    }
}
