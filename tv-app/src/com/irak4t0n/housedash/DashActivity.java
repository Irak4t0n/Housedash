package com.irak4t0n.housedash;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.WindowManager;
import android.webkit.WebView;

/** The same page as an app, for a Projectivy tile. ◀ ▶ OK drive the wire, BACK closes a popup
 *  or leaves the app, MENU opens the URL setting. */
public class DashActivity extends Activity {
    private WebView web;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = Dash.make(this, null);
        setContentView(web);
        Dash.fullscreen(web);
    }

    @Override protected void onResume() {
        super.onResume();
        if (web != null) web.loadUrl(Dash.tvUrl(this));
    }

    @Override public boolean onKeyDown(int code, KeyEvent e) {
        if (code == KeyEvent.KEYCODE_MENU) {
            startActivity(new Intent(this, SettingsActivity.class));
            return true;
        }
        String name = Dash.keyName(code);
        if (name != null && web != null) {
            final boolean isBack = "back".equals(name);
            Dash.sendKey(web, name, new Runnable() { public void run() { if (isBack) finish(); } });
            return true;
        }
        return super.onKeyDown(code, e);
    }

    @Override protected void onDestroy() {
        if (web != null) { web.destroy(); web = null; }
        super.onDestroy();
    }
}
