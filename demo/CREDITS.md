# Demo credits

The demo video (`patchbrief-demo.mp4`, rendered by `demo/render.py` from `patchbrief-demo-raw.cast`) uses:

| Asset | Source | Licence |
|---|---|---|
| Narration voice: Piper TTS `en_US-ljspeech-medium` | https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/ljspeech/medium (model card: https://huggingface.co/rhasspy/piper-voices/raw/main/en/en_US/ljspeech/medium/MODEL_CARD) | Trained on the LJ Speech dataset (https://keithito.com/LJ-Speech-Dataset/), which the model card lists as **public domain**. |
| TTS engine: `piper-tts` 1.8.0, run locally | https://github.com/OHF-Voice/piper1-gpl | GPL-3.0-or-later. Used as a local tool to generate the audio; no Piper code ships in this repo. |
| Font: DejaVu Sans / DejaVu Sans Mono | https://dejavu-fonts.github.io/ | DejaVu Fonts licence (free, permissive). |
| Terminal footage | Real Patchbrief runs, recorded with asciinema | Ours (MIT, see `LICENSE`). |

No music, sound effects or other third-party audio is used. Narration is loudness-normalised to about -16 LUFS (ffmpeg `loudnorm`, two-pass).

An earlier render used a different Piper voice whose dataset licence is research-only. It was replaced; no file in this repo or the published video uses it.
