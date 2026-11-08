package com.irak4t0n.housedash;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/** One field: the dashboard URL. Reached from the screensaver's settings gear or MENU in the app. */
public class SettingsActivity extends Activity implements View.OnClickListener {
    private EditText field;

    public void onClick(View v) {
        String u = field.getText().toString().trim();
        if (!u.isEmpty()) Dash.setUrl(this, u);
        finish();
    }

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setBackgroundColor(Color.parseColor("#0b0f14"));
        l.setGravity(Gravity.CENTER);
        int pad = 64; l.setPadding(pad, pad, pad, pad);

        TextView t = new TextView(this);
        t.setText("Dashboard URL");
        t.setTextColor(Color.parseColor("#e6edf3"));
        t.setTextSize(28);
        l.addView(t);

        field = new EditText(this);
        field.setText(Dash.url(this));
        field.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        field.setTextColor(Color.WHITE);
        field.setTextSize(22);
        field.setSingleLine(true);
        l.addView(field);

        Button save = new Button(this);
        save.setText("Save");
        save.setOnClickListener(this);
        l.addView(save);
        setContentView(l);
    }
}
