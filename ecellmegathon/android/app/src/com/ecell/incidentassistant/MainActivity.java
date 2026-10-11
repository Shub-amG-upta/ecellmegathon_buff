package com.ecell.incidentassistant;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

/**
 * The Incident Assistant UI (ui/ in the repo, served by app/server.py on the laptop) full-screen on the QIDK.
 * The board reaches the laptop through `adb reverse tcp:8000 tcp:8000`, so the page is http://127.0.0.1:8000.
 * Push-to-talk uses the board's microphone: the app asks for RECORD_AUDIO and grants audio capture to that
 * page only. Another address can be passed with: am start -n ... --es url http://127.0.0.1:PORT/
 */
public class MainActivity extends Activity {
    static final String DEFAULT_URL = "http://127.0.0.1:8000/";
    static final int MIC_REQUEST = 1;

    WebView web;
    String url = DEFAULT_URL;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        String extra = getIntent().getStringExtra("url");
        if (extra != null && !extra.isEmpty()) url = extra;

        WebView.enableSlowWholeDocumentDraw();          // the PDF draws a whole off-screen page (SaveBridge)
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);   // the assistant's reply plays without a tap
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);       // always the laptop's current UI
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) showOffline(String.valueOf(err.getDescription()));
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                return !"127.0.0.1".equals(u.getHost()) && !"localhost".equals(u.getHost());
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                String host = request.getOrigin() == null ? "" : request.getOrigin().getHost();
                boolean ours = "127.0.0.1".equals(host) || "localhost".equals(host);
                boolean micOnly = true;
                for (String r : request.getResources()) {
                    if (!PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)) micOnly = false;
                }
                if (ours && micOnly && hasMic()) {
                    request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
                } else {
                    request.deny();
                }
            }
        });
        // Android 15+ draws apps edge to edge: pad a container (a WebView ignores its own padding) so the
        // page stays clear of the status and navigation bars.
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF0F1115);
        root.addView(web, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            // The on-screen keyboard counts too, so the text box and Send stay visible above it.
            android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsets.CONSUMED;
        });
        setContentView(root);
        // Save PDF / Save JSON from the report view, into Download/IncidentReports on the board. Only our own
        // pages are ever loaded (shouldOverrideUrlLoading above), so only they see this bridge.
        web.addJavascriptInterface(new SaveBridge(this, web, root, url), "IncidentApp");
        if (!hasMic()) requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, MIC_REQUEST);
        web.loadUrl(url);
    }

    boolean hasMic() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code == MIC_REQUEST) web.reload();
    }

    void showOffline(String why) {
        String html = "<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
            + "<style>body{font:18px system-ui,sans-serif;background:#0f1115;color:#e8eaf0;padding:28px;line-height:1.5}"
            + "code{background:#222630;padding:2px 6px;border-radius:4px}button{font:inherit;padding:12px 22px;"
            + "border-radius:10px;border:0;background:#7b93ff;color:#0f1115;margin-top:16px}</style></head><body>"
            + "<h2>Can't reach the assistant</h2><p>" + escape(url) + " did not answer (" + escape(why) + ").</p>"
            + "<p>On the laptop: start <code>python -m app.server</code>, then run "
            + "<code>adb reverse tcp:8000 tcp:8000</code>.</p>"
            + "<button onclick=\"location.href='" + escape(url) + "'\">Retry</button></body></html>";
        web.loadDataWithBaseURL(url, html, "text/html", "utf-8", null);
    }

    static String escape(String t) {
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;")
                .replace("\"", "&quot;");
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
