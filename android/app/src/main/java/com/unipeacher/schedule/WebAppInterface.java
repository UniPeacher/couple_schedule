package com.unipeacher.schedule;

import android.content.Context;
import android.os.Build;
import android.webkit.JavascriptInterface;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

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
        int id = (int) (System.currentTimeMillis() % 100000000);
        NotificationHelper.showNotification(mContext, id, title, content);
    }

    @JavascriptInterface
    public void showToast(String message) {
        Toast.makeText(mContext, message, Toast.LENGTH_SHORT).show();
    }
}
