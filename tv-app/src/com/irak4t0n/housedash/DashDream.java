package com.irak4t0n.housedash;

import android.service.dreams.DreamService;
import android.view.KeyEvent;
import android.webkit.WebView;

/** Screensaver: the dashboard, fullscreen. The remote's ◀ ▶ browse the wire and OK opens a
 *  headline (QR, then the TV browser); ▲ jumps to the Lights card where ◀ ▶ OK toggle lights and
 *  ▼ leaves; BACK or any other key wakes the TV as usual. */
public class DashDream extends DreamService {
    private WebView web;

    @Override public void onAttachedToWindow() {
        super.onAttachedToWindow();
        setInteractive(true);        // keys come to us instead of ending the dream
        setFullscreen(true);
        setScreenBright(true);
        web = Dash.make(this, new Runnable() { public void run() { finish(); } });
        setContentView(web);
        Dash.fullscreen(web);
    }

    @Override public boolean dispatchKeyEvent(KeyEvent e) {
        if (e.getAction() != KeyEvent.ACTION_DOWN) return true;   // swallow ups; downs decide
        String name = Dash.keyName(e.getKeyCode());
        if (name == null || web == null) { finish(); return true; }   // any other key: wake
        Dash.sendKey(web, name, new Runnable() { public void run() { finish(); } });
        return true;
    }

    @Override public void onDreamingStopped() {
        super.onDreamingStopped();
        if (web != null) { web.loadUrl("about:blank"); web.destroy(); web = null; }
    }
}
