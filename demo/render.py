"""Render an asciinema v2 cast into a captioned MP4 + GIF.

Real terminal output only. Idle gaps longer than GAP_LIMIT seconds (model waits) are
shortened to CUT_TO seconds and labelled on screen "[model wait cut: Ns]".
Caption markers come from OSC 1337;PBCAP=N sequences emitted by demo-steps.sh.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import pyte
from PIL import Image, ImageDraw, ImageFont

COLS, ROWS = 100, 38
W, H = 1280, 800
TERM_H = 690
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
GAP_LIMIT, CUT_TO, FPS = 8.0, 2.0, 10
PIPER = str(Path.home() / ".local/bin/piper")
VOICE = str(Path.home() / ".local/share/piper-voices/en_US-lessac-medium.onnx")

CAPTIONS = {
    0: "Patchbrief: a local OpenMinion agent that drafts patch briefs. It runs locally for $0 and only writes after you approve.",
    1: "Patchbrief reads a local repo or release folder and writes a patch brief: changes, risks, test commands and a release blurb.",
    2: "OpenMinion 0.1.22 on local Ollama, with a 16k-context qwen2.5 1.5B model. Doctor's live turn check passes.",
    3: "Tests cover redaction, path safety, the approval gate, memory, and the release blurb checks.",
    4: "When the write is denied, OpenMinion's approval gate blocks the tool, the turn stops, and no file is created.",
    5: "Once approved, the model calls write_patch_brief. If the blurb fails a check, Patchbrief asks the model once for a new one, and the JSON reports it.",
    6: "The brief cites the real commits and every open issue, and the blurb names real changes without claiming open issues are fixed.",
    7: "Every approved brief is logged to the local history.",
    8: "Our feedback on OpenMinion 0.1.22 is in the README. Repo link below.",
}
COLORS = {
    "default": (220, 220, 220), "black": (40, 40, 40), "red": (240, 90, 90), "green": (110, 210, 110),
    "brown": (220, 190, 90), "yellow": (230, 210, 100), "blue": (110, 150, 240), "magenta": (210, 120, 210),
    "cyan": (100, 200, 210), "white": (235, 235, 235),
}
OSC = re.compile(r"\x1b\]1337;PBCAP=(\d+)\x07")

font = ImageFont.truetype(FONT, 16)
font_b = ImageFont.truetype(FONT_B, 16)
cap_font = ImageFont.truetype(SANS, 24)
cw = font.getbbox("M")[2]
lh = TERM_H // ROWS


def wrap(text: str, fnt, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if fnt.getlength(trial) > width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur]


def draw_caption(img: Image.Image, cap: int | None, banner: str | None) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, TERM_H, W, H], fill=(18, 18, 30))
    if cap is not None:
        for i, ln in enumerate(wrap(CAPTIONS[cap], cap_font, W - 80)[:3]):
            d.text((40, TERM_H + 14 + i * 32), ln, font=cap_font, fill=(255, 255, 255))
    if banner:
        tw = font_b.getlength(banner)
        y = TERM_H - 46
        d.rectangle([W - tw - 40, y, W - 10, y + 34], fill=(200, 60, 60))
        d.text((W - tw - 25, y + 7), banner, font=font_b, fill=(255, 255, 255))


def render_screen(screen: pyte.Screen, cap: int | None, banner: str | None) -> Image.Image:
    img = Image.new("RGB", (W, H), (12, 12, 16))
    d = ImageDraw.Draw(img)
    x0 = (W - COLS * cw) // 2
    for y in range(ROWS):
        row = screen.buffer[y]
        for x in range(COLS):
            ch = row[x]
            if ch.data.strip():
                color = COLORS.get(ch.fg, COLORS["default"])
                d.text((x0 + x * cw, 4 + y * lh), ch.data, font=font_b if ch.bold else font, fill=color)
    draw_caption(img, cap, banner)
    return img


def title_card() -> Image.Image:
    img = Image.new("RGB", (W, H), (12, 12, 16))
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(SANS, 64)
    mid = ImageFont.truetype(SANS, 30)
    d.text((W // 2, 220), "Patchbrief", font=big, fill=(255, 255, 255), anchor="mm")
    d.text((W // 2, 300), "Local OpenMinion 0.1.22 + Ollama release & issue-triage agent", font=mid, fill=(180, 200, 255), anchor="mm")
    d.text((W // 2, 350), "Cortensor Hackathon #5 · real terminal runs, model waits cut and marked", font=ImageFont.truetype(SANS, 22), fill=(160, 160, 160), anchor="mm")
    draw_caption(img, 0, None)
    return img


def tts(text: str, out: Path) -> float:
    subprocess.run([PIPER, "--model", VOICE, "--output_file", str(out)], input=text.encode(), check=True,
                   capture_output=True)
    with wave.open(str(out)) as w:
        return w.getnframes() / w.getframerate()


def main(cast_path: str, out_dir: str) -> None:
    out = Path(out_dir)
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for f in frames_dir.glob("*.png"):
        f.unlink()
    lines = Path(cast_path).read_text().splitlines()
    events = [json.loads(ln) for ln in lines[1:] if ln.strip()]

    # Build an output timeline: (out_time, text, caption, banner_until, banner_text)
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.Stream(screen)
    frames: list[tuple[Path, float]] = []  # (png, duration)
    cuts: list[dict] = []
    cap_starts: dict[int, float] = {}
    voice_dir = out / "voice"
    voice_dir.mkdir(exist_ok=True)

    t_out = 0.0
    idx = 0

    def emit(img: Image.Image, dur: float) -> None:
        nonlocal idx, t_out
        p = frames_dir / f"f{idx:05d}.png"
        img.save(p)
        frames.append((p, dur))
        idx += 1
        t_out += dur

    # Title card, held for its voiceover.
    voice_len = {k: tts(v, voice_dir / f"cap{k}.wav") for k, v in CAPTIONS.items()}
    cap_starts[0] = 0.0
    emit(title_card(), max(4.0, voice_len[0] + 0.6))

    cap: int | None = None
    prev_t, acc, banner_until, banner = 0.0, 0.0, -1.0, None

    def flush() -> None:
        nonlocal acc
        if acc > 0:
            emit(render_screen(screen, cap, banner if t_out < banner_until else None), acc)
            acc = 0.0

    for t, kind, data in events:
        if kind != "o":
            continue
        gap = t - prev_t
        prev_t = t
        if gap > GAP_LIMIT:
            flush()
            cut = round(gap - CUT_TO)
            banner = f"[model wait cut: {cut}s]"
            cuts.append({"at_out_s": round(t_out, 1), "real_gap_s": round(gap, 1), "cut_s": cut})
            emit(render_screen(screen, cap, banner), CUT_TO)
            banner_until = t_out + 2.5
        else:
            acc += gap
            if acc >= 1 / FPS:
                flush()
        for m in OSC.finditer(data):
            flush()
            # Hold the previous caption until its voiceover finishes.
            if cap is not None:
                need = cap_starts[cap] + voice_len[cap] + 0.4 - t_out
                if need > 0:
                    emit(render_screen(screen, cap, None), need)
            cap = int(m.group(1))
            cap_starts[cap] = t_out
        stream.feed(OSC.sub("", data))
    flush()
    # Final hold: last caption's voice + 2s.
    tail = max(2.5, cap_starts.get(cap, 0) + voice_len.get(cap, 0) + 1.0 - t_out)
    emit(render_screen(screen, cap, None), tail)

    # Drop zero-length frames and write the concat list.
    concat = out / "frames.txt"
    with concat.open("w") as fh:
        for p, dur in frames:
            if dur >= 0.01:
                fh.write(f"file '{p.resolve()}'\nduration {dur:.3f}\n")
        fh.write(f"file '{frames[-1][0].resolve()}'\n")

    # Voice track: each caption's speech placed at its start time.
    inputs, filters = [], []
    for i, (k, start) in enumerate(sorted(cap_starts.items(), key=lambda kv: kv[1])):
        inputs += ["-i", str(voice_dir / f"cap{k}.wav")]
        filters.append(f"[{i}:a]adelay={int(start * 1000)}:all=1[a{i}]")
    n = len(filters)
    mix = ";".join(filters) + ";" + "".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,volume=0.6[aout]"
    voice = out / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", mix, "-map", "[aout]",
                    "-ar", "44100", str(voice)], check=True)

    mp4 = out / "patchbrief-demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-i", str(voice), "-af", "apad", "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-t", f"{t_out:.2f}", "-movflags", "+faststart", str(mp4)],
                   check=True)
    gif = out / "patchbrief-demo.gif"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-vf", "fps=4,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse",
                    str(gif)], check=True)
    (out / "cuts.json").write_text(json.dumps({"cuts": cuts, "caption_starts_s": cap_starts}, indent=2))
    print(json.dumps({"duration_s": round(t_out, 1), "cuts": cuts}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
