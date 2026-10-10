"""Builds deliverables/M1.pdf (2 pages, A4) with matplotlib only.   python3 deliverables/make_m1.py"""
from pathlib import Path

import matplotlib
matplotlib.use("agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch

TEAM = "Team: Shubham Gupta, Sarthak Chaudhari"          # edit and re-run to change
OUT = Path(__file__).resolve().parent / "M1.pdf"

W, H = 8.27, 11.69                    # A4, inches
ML, MR, MT, MB = 0.62, 0.62, 0.55, 0.5
INK, MUTED, ACCENT, RULE = "#111111", "#555555", "#1f4e79", "#c9d3df"
plt.rcParams.update({"font.family": "DejaVu Sans", "pdf.fonttype": 42})


class Page:
    def __init__(self, pdf):
        self.pdf = pdf
        self.fig = plt.figure(figsize=(W, H))
        self.r = self.fig.canvas.get_renderer()
        self.y = H - MT                                   # inches from the bottom

    def _w(self, s, size, weight="normal"):
        t = self.fig.text(0, 0, s, fontsize=size, weight=weight)
        w = t.get_window_extent(self.r).width / self.fig.dpi
        t.remove()
        return w

    @staticmethod
    def _sep(line, k):
        """No space before a word that starts with punctuation (", with" after a bold word)."""
        return "" if k + 1 < len(line) and line[k + 1][0][:1] in ",.;:)!?" else " "

    def _wrap(self, words, size, width, first_indent=0.0):
        lines, cur, x = [], [], first_indent
        for w, bold in words:
            ww = self._w(w + " ", size, "bold" if bold else "normal")
            if cur and x + ww > width:
                lines.append(cur)
                cur, x = [], 0.0
            cur.append((w, bold))
            x += ww
        if cur:
            lines.append(cur)
        return lines

    def rich(self, text, size=8.6, indent=0.0, gap=0.035, color=INK, lead=1.33):
        """text with **bold** spans, wrapped to the column."""
        words, bold = [], False
        for part in text.split("**"):
            words += [(w, bold) for w in part.split()]
            bold = not bold
        width = W - ML - MR - indent
        for line in self._wrap(words, size, width):
            x = ML + indent
            self.y -= size * lead / 72
            for k, (w, b) in enumerate(line):
                self.fig.text(x / W, self.y / H, w, fontsize=size, color=color,
                              weight="bold" if b else "normal", va="baseline")
                x += self._w(w + self._sep(line, k), size, "bold" if b else "normal")
        self.y -= gap

    def bullet(self, text, size=8.6, level=0):
        ind = 0.16 + 0.2 * level
        y0 = self.y
        self.rich(text, size=size, indent=ind, gap=0.02)
        self.fig.text((ML + ind - 0.13) / W, (y0 - size * 1.33 / 72) / H, "•" if level == 0 else "–",
                      fontsize=size, color=ACCENT, va="baseline")

    def h1(self, text, sub=None):
        self.y -= 0.22
        self.fig.text(ML / W, self.y / H, text, fontsize=15.5, weight="bold", color=ACCENT, va="baseline")
        if sub:
            self.y -= 0.21
            self.fig.text(ML / W, self.y / H, sub, fontsize=8.8, color=MUTED, va="baseline")
        self.y -= 0.1
        self.fig.add_artist(plt.Line2D([ML / W, (W - MR) / W], [self.y / H] * 2, color=ACCENT, lw=1.2))
        self.y -= 0.05

    def h2(self, text):
        self.y -= 0.2
        self.fig.text(ML / W, self.y / H, text, fontsize=11, weight="bold", color=ACCENT, va="baseline")
        self.y -= 0.06
        self.fig.add_artist(plt.Line2D([ML / W, (W - MR) / W], [self.y / H] * 2, color=RULE, lw=0.7))
        self.y -= 0.02

    def table(self, rows, cols, size=7.9, header=True):
        """rows of cells; cols = column widths in inches. Cells may hold **bold**."""
        for i, row in enumerate(rows):
            top = self.y
            lowest = top
            x = ML
            for cell, cw in zip(row, cols):
                saved, self.y = self.y, top
                words, bold = [], False
                for part in str(cell).split("**"):
                    words += [(w, bold or (header and i == 0)) for w in part.split()]
                    bold = not bold
                for line in self._wrap(words, size, cw - 0.08):
                    self.y -= size * 1.3 / 72
                    xx = x + 0.04
                    for k, (w, b) in enumerate(line):
                        self.fig.text(xx / W, self.y / H, w, fontsize=size, weight="bold" if b else "normal",
                                      color=ACCENT if header and i == 0 else INK, va="baseline")
                        xx += self._w(w + self._sep(line, k), size, "bold" if b else "normal")
                lowest = min(lowest, self.y)
                self.y = saved
                x += cw
            self.y = lowest - 0.05
            if header and i == 0:
                self.fig.add_artist(plt.Line2D([ML / W, (ML + sum(cols)) / W], [(self.y + 0.02) / H] * 2,
                                               color=ACCENT, lw=0.8))
            else:
                self.fig.add_artist(plt.Line2D([ML / W, (ML + sum(cols)) / W], [(self.y + 0.02) / H] * 2,
                                               color=RULE, lw=0.4))
        self.y -= 0.04

    def box(self, x, y, w, h, title, sub, fc):
        ax = self.fig.add_axes([0, 0, 1, 1], facecolor="none")
        ax.set_axis_off()
        ax.set_xlim(0, W)
        ax.set_ylim(0, H)
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06",
                                    fc=fc, ec=ACCENT, lw=0.9))
        ax.text(x + w / 2, y + h * 0.64, title, ha="center", va="center", fontsize=7.8, weight="bold", color=INK)
        ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center", fontsize=6.4, color=MUTED,
                linespacing=1.15)
        return ax

    def arrow(self, x1, y1, x2, y2, label=None):
        ax = self.fig.add_axes([0, 0, 1, 1], facecolor="none")
        ax.set_axis_off()
        ax.set_xlim(0, W)
        ax.set_ylim(0, H)
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", color=ACCENT, lw=1.0, shrinkA=0, shrinkB=0))
        if label:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.07, label, ha="center", fontsize=6.3, color=MUTED)

    def footer(self, n):
        self.fig.text(0.5, MB / 2 / H, f"M1 · AI Incident Report Assistant · page {n} of 2", ha="center",
                      fontsize=7, color=MUTED)

    def save(self):
        assert self.y > MB, f"page overflow: y={self.y:.2f} in"
        self.pdf.savefig(self.fig)
        plt.close(self.fig)


def page1(pdf):
    p = Page(pdf)
    p.h1("M1: Approach · AI Incident Report Assistant",
         f"Megathon X · Qualcomm Problem Statement 1 · {TEAM}")

    p.h2("1. Problem statement and our understanding")
    p.rich("Reporting an incident today means opening a portal and typing every field. The problem asks for an "
           "assistant where the reporter **just speaks** (\"There was a water leak near Conference Room 2 around "
           "10 AM. Maintenance has already isolated the area.\") and the app fills a structured report: "
           "**speak → understand → extract → review → submit**, with the result shown on screen and spoken back. "
           "**Every AI model must run on the Snapdragon device (Qualcomm QIDK), with no cloud APIs.**")
    p.rich("**What we take from it:**", gap=0.0)
    p.bullet("**It is multi-turn slot filling, not one-shot extraction.** People leave things out, answer follow-up "
             "questions with one word (\"medium\"), and correct themselves (\"actually it was Lab 4\"). The form has "
             "to stay right across the whole conversation.")
    p.bullet("**\"Mandatory fields present?\" is a decision, so code should make it, not the model.** The same "
             "goes for which question to ask next, whether a value was really said, and when submitting is allowed.")
    p.bullet("**On the device, output length is the cost.** On the QIDK's Hexagon NPU, generating tokens took "
             "82–89% of model time in our earlier project (FieldMind, measured on this board). So the model writes "
             "a short tool call, and every sentence the user hears is written by code.")
    p.bullet("**Judged on:** on-device Snapdragon acceleration · natural speech, not fixed commands · speech "
             "recognition accuracy · extraction accuracy · mapping to the right fields.")
    p.rich("**The report form: the problem statement's 10 fields.** Mandatory: incident type, severity, location, "
           "date & time, description. Optional: persons involved, equipment / asset, immediate actions taken, "
           "witnesses, recommended follow-up. 15 incident types (fire, electrical, water leak, gas leak, chemical, "
           "slip/trip, fall from height, struck by, caught in, equipment failure, lift, structural, medical, "
           "security, other).", gap=0.02)

    p.h2("2. Idea")
    p.rich("A **single-agent, fully on-device voice assistant**. Whisper turns speech into text; **one "
           "grammar-constrained LLM call per turn, on the NPU,** turns the utterance into a tool call "
           "(update_report / submit_report / cancel_report / no_op); **plain code** checks it against what the user "
           "actually said, merges it, decides what is still missing and asks for it; a Piper voice speaks the "
           "reply. In one line: **the model extracts, code decides.** It builds on FieldMind, our earlier project "
           "that ran LLMs on this same QIDK's Hexagon NPU with llama.cpp (every layer on the NPU, verified from the "
           "server's own log).")

    p.h2("3. Architecture and pipeline")
    # --- diagram
    top = p.y - 0.12
    bh, bw, gap = 0.62, 1.22, 0.17
    xs = [ML + i * (bw + gap) for i in range(5)]
    specs = [("Speech-to-text", "Whisper base.en\nwhisper.cpp · QIDK CPU", "#eef3f9"),
             ("LLM extraction", "Llama 3.2 3B · pure Q4_0\nllama.cpp Hexagon · NPU", "#dde8f5"),
             ("Gate + merge", "only values the user said\ntime phrases → timestamp", "#f3f3f3"),
             ("Mandatory present?", "code: type → location\n→ time → severity", "#f3f3f3"),
             ("Review + submit", "read-back, edits,\nJSON report on screen", "#f3f3f3")]
    for x, (t, s, fc) in zip(xs, specs):
        p.box(x, top - bh, bw, bh, t, s, fc)
    for i in range(4):
        p.arrow(xs[i] + bw, top - bh / 2, xs[i + 1], top - bh / 2, None)
    ax0 = p.fig.add_axes([0, 0, 1, 1], facecolor="none"); ax0.set_axis_off(); ax0.set_xlim(0, W); ax0.set_ylim(0, H)
    ax0.text(xs[3] + bw + gap / 2, top - bh / 2 + 0.06, "yes", ha="center", fontsize=6.3, color=MUTED)
    # no-branch: ask the missing field via TTS, back to the user
    ty = top - bh - 0.5
    p.box(xs[3], ty - 0.42, bw, 0.42, "Text-to-speech", "Piper voice · QIDK CPU", "#eef3f9")
    p.arrow(xs[3] + bw / 2, top - bh, xs[3] + bw / 2, ty, None)
    ax = p.fig.add_axes([0, 0, 1, 1], facecolor="none"); ax.set_axis_off(); ax.set_xlim(0, W); ax.set_ylim(0, H)
    ax.text(xs[3] + bw / 2 + 0.05, top - bh - 0.27, "no: ask the missing field", fontsize=6.3, color=MUTED)
    p.arrow(xs[3], ty - 0.21, xs[0] + bw / 2, ty - 0.21, None)
    p.arrow(xs[0] + bw / 2, ty - 0.21, xs[0] + bw / 2, top - bh, None)
    ax.text((xs[0] + xs[3]) / 2 + 0.3, ty - 0.14, "the user answers (\"it's medium\") · next turn", ha="center",
            fontsize=6.3, color=MUTED)
    ax.text(xs[4] + bw / 2, top - bh - 0.17, "spoken confirmation", ha="center", fontsize=6.3, color=MUTED)
    p.arrow(xs[4] + bw / 2, top - bh, xs[4] + bw / 2, ty - 0.21, None)
    p.arrow(xs[4] + bw / 2, ty - 0.21, xs[3] + bw, ty - 0.21, None)
    p.y = ty - 0.42 - 0.12
    p.rich("**Each model has a fixed processor**, so no two stages compete for the NPU:", gap=0.02)
    p.table([["stage", "model", "runtime", "runs on"],
             ["Speech-to-text", "Whisper base.en (tiny / small compared)", "whisper.cpp server", "QIDK CPU"],
             ["Extraction", "Llama 3.2 3B Instruct, pure Q4_0 (Qwen3 1.7B as faster backup)",
              "llama.cpp, Hexagon backend, llama-server", "QIDK NPU (HTP0)"],
             ["Text-to-speech", "Piper en_US lessac medium", "sherpa-onnx", "QIDK CPU"],
             ["Dialog logic, UI", "none (plain Python, standard library only)", "HTTP server + web page",
              "laptop now; board screen (stretch)"]],
            [1.15, 2.6, 1.95, 1.33])
    p.footer(1)
    p.save()


def page2(pdf):
    p = Page(pdf)
    p.bullet("**One LLM call per turn, always valid.** The prompt holds fixed instructions (~600 tokens, reusable "
             "across turns), the form so far, the field just asked about, and the utterance. The reply is a tool "
             "call with one-letter keys, constrained by a **JSON schema that llama-server compiles into a "
             "grammar**: enums can only hold allowed values and text fields have a maximum length. (FieldMind: free "
             "JSON from 1–3B models broke in 25–100% of answers; with a grammar, 0.)")
    p.bullet("**Code after the model.** Drop any value whose words are not in the user's speech (numbers must match "
             "exactly: \"Room 3\" never passes for \"Room 2\"); corrections overwrite; time phrases (\"around 10\", "
             "\"quarter past two\", \"8 last night\") become timestamps; ask the first missing mandatory field with a "
             "fixed question; read back and wait for \"submit\"; refuse to submit while a mandatory field is empty. "
             "The description is the user's own words, so it cannot be invented. Follow-up actions come from a "
             "fixed list per incident type. A severity below the usual minimum for the type is questioned at "
             "review, never changed silently.")
    p.bullet("**NPU, proven.** Models are built as pure Q4_0 (published \"Q4_0\" files carry other tensor types); the "
             "board refuses any model file whose hash doesn't match. Every launch is checked from the server's own "
             "log: all layers offloaded to HTP0 and a nonzero HTP0 buffer (Llama 3B: 29/29 layers). Measured on this "
             "board in FieldMind: **986 tok/s prefill, 21 tok/s decode** for Llama 3.2 3B (700-token prompt). "
             "Target: under 6 s per spoken turn end to end (to be measured).")
    p.bullet("**Deployment.** Tier 1: all three models on the QIDK, UI on a laptop over USB (adb). Tier 2 (stretch): "
             "the UI on the board's own screen, plus an airplane-mode demo.")

    p.h2("4. Planned testing methodology")
    p.table([["what we test", "how", "judging criterion"],
             ["Extraction and field mapping",
              "**Text eval set: 65 scripted dialogs, 250 user turns**, a correct tool call for every turn. 52 dialogs "
              "are rewrites of real public incident reports (OSHA accident summaries, Indian news), 13 hand-written. "
              "Traps: negations, corrections, one-word answers, premature submit, noise. **Teacher-forced** (each "
              "turn from the correct form so far): tool accuracy, per-field accuracy, values the model added that "
              "the user never said.", "extraction accuracy; correct mapping"],
             ["The conversation",
              "**Free-running:** each dialog through the real dialog loop from an empty form; the final form against "
              "the correct one; did it end right (submitted / cancelled).", "natural conversational speech"],
             ["Speech recognition",
              "**36 dev turns × 3 speakers**, plus 10 in a noisy room, chosen where STT is likeliest to hurt "
              "(numbers, times, names, accents, \"not\"/\"actually\"). Word error rate and errors on key words; then "
              "**the same turns typed vs spoken** through extraction, to show what STT costs. Whisper tiny / base / "
              "small, with and without a vocabulary hint.", "speech recognition accuracy"],
             ["On-device and latency",
              "Every run gated by the NPU log check; per-stage time from the servers themselves (STT, LLM prefill, "
              "LLM decode, code, TTS); chip temperature before and after.", "on-device Snapdragon acceleration"]],
            [1.45, 4.35, 1.23])
    p.bullet("**Rules for the numbers.** Every figure is labelled BOARD or LAPTOP, estimates are marked as "
             "estimates, and nothing is invented. Each eval runs twice (replies at temperature 0 still vary between "
             "server launches). Tuning uses the dev split only; the 21-dialog test split stays locked until the "
             "decision rules (3B vs 1.7B, Whisper size, vocabulary hint) are written down.")
    p.bullet("**Status at M1.** The dialog core, UI, voice input/output, evaluation harness and board bring-up "
             "scripts are built, with 138 automated tests passing (scorer self-check: 100% on the correct answers). "
             "Next: the full run on the QIDK, then the measured results for the final submission.")
    p.footer(2)
    p.save()


if __name__ == "__main__":
    with PdfPages(OUT) as pdf:
        page1(pdf)
        page2(pdf)
    print(f"wrote {OUT}")
