package com.ecell.incidentassistant;

import android.app.Activity;
import android.content.ContentValues;
import android.graphics.Canvas;
import android.graphics.pdf.PdfDocument;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/**
 * Saves a submitted report on the QIDK itself, in Download/IncidentReports (MediaStore: no storage permission).
 * The page calls window.IncidentApp.saveText(name, text) for the JSON and savePdf(name, html) for the PDF;
 * the result goes back to it as window.appSaved(message, ok). A WebView ignores window.print() and blob
 * downloads, which is why the page's own Print / Download buttons did nothing on the board.
 *
 * The PDF: the report card's HTML is laid out in a second WebView at A4 width (behind the visible one, so it
 * is drawn) and painted page by page into an android.graphics.pdf.PdfDocument.
 */
class SaveBridge {
    static final String FOLDER = "IncidentReports";
    static final int PAGE_CSS_PX = 794;              // A4 width at 96 CSS px per inch
    static final int PAGE_W_PT = 595, PAGE_H_PT = 842;

    final Activity act;
    final WebView page;
    final FrameLayout root;
    final String baseUrl;

    SaveBridge(Activity act, WebView page, FrameLayout root, String baseUrl) {
        this.act = act;
        this.page = page;
        this.root = root;
        this.baseUrl = baseUrl;
    }

    @JavascriptInterface
    public void saveText(String name, String text) {
        try {
            done("Saved to " + write(clean(name, ".json"), "application/json",
                    text.getBytes(StandardCharsets.UTF_8)), true);
        } catch (Exception e) {
            done("Could not save the JSON: " + e.getMessage(), false);
        }
    }

    @JavascriptInterface
    public void savePdf(String name, String html) {
        act.runOnUiThread(() -> renderPdf(clean(name, ".pdf"), html));
    }

    void renderPdf(String name, String html) {
        float d = act.getResources().getDisplayMetrics().density;
        int w = Math.round(PAGE_CSS_PX * d);
        int h0 = Math.round(PAGE_CSS_PX * 1.414f * d);
        WebView v = new WebView(act);
        v.getSettings().setJavaScriptEnabled(false);
        root.addView(v, 0, new FrameLayout.LayoutParams(w, h0));     // index 0: under the visible page
        v.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String u) {
                view.postDelayed(() -> {
                    try {
                        done("Saved to " + writePdf(view, name, w, d), true);
                    } catch (Exception e) {
                        done("Could not save the PDF: " + e.getMessage(), false);
                    } finally {
                        root.removeView(view);
                        view.destroy();
                    }
                }, 700);                                              // let fonts and styles settle
            }
        });
        v.loadDataWithBaseURL(baseUrl, html, "text/html", "utf-8", null);
    }

    String writePdf(WebView v, String name, int w, float d) throws IOException {
        int h = Math.max(1, Math.round(v.getContentHeight() * d));
        v.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY));
        v.layout(0, 0, w, h);
        float scale = PAGE_W_PT / (float) w;
        int pageHpx = Math.round(PAGE_H_PT / scale);
        PdfDocument pdf = new PdfDocument();
        int n = 1;
        for (int top = 0; top < h; top += pageHpx, n++) {
            PdfDocument.Page p = pdf.startPage(new PdfDocument.PageInfo.Builder(PAGE_W_PT, PAGE_H_PT, n).create());
            Canvas c = p.getCanvas();
            c.scale(scale, scale);
            c.translate(0, -top);
            v.draw(c);
            pdf.finishPage(p);
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        pdf.writeTo(out);
        pdf.close();
        return write(name, "application/pdf", out.toByteArray());
    }

    /** Download/IncidentReports/<name> on Android 10+, the app's own Downloads folder on Android 9. */
    String write(String name, String mime, byte[] data) throws IOException {
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues cv = new ContentValues();
            cv.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
            cv.put(MediaStore.MediaColumns.MIME_TYPE, mime);
            cv.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/" + FOLDER);
            Uri uri = act.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
            if (uri == null) throw new IOException("the media store refused " + name);
            try (OutputStream os = act.getContentResolver().openOutputStream(uri)) {
                if (os == null) throw new IOException("no output stream for " + name);
                os.write(data);
            }
            return "Download/" + FOLDER + "/" + name;
        }
        File dir = new File(act.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), FOLDER);
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("cannot create " + dir);
        File f = new File(dir, name);
        try (FileOutputStream os = new FileOutputStream(f)) {
            os.write(data);
        }
        return f.getPath();
    }

    static String clean(String name, String ext) {
        String n = name == null ? "" : name.replaceAll("[^A-Za-z0-9._-]", "_");
        if (n.isEmpty()) n = "report";
        return n.endsWith(ext) ? n : n + ext;
    }

    void done(String msg, boolean ok) {
        act.runOnUiThread(() -> {
            Toast.makeText(act, msg, Toast.LENGTH_LONG).show();
            page.evaluateJavascript("window.appSaved && window.appSaved(" + JSONObject.quote(msg) + "," + ok + ")",
                    null);
        });
    }
}
