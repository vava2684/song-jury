# Third-Party Notices

The code in this repository is MIT-licensed (see [`LICENSE`](LICENSE)).

**No third-party model weights, checkpoints, or lexicons are included here.**
They are fetched by the user at install time from their own official sources,
and each remains under its own license. Nothing below is redistributed by this
project, and none of it is covered by this project's MIT license.

⚠️ If you plan to use song-jury **commercially**, read this list first — at least
one component is non-commercial-only, and that restriction applies to your use of
that component regardless of this project's MIT license.

| Component | License / restriction | How it is obtained |
|---|---|---|
| [SongEval](https://github.com/ASLP-lab/SongEval) | **CC BY-NC-SA — non-commercial only** | `install.sh` / `install.ps1` clones it from the upstream repository |
| [NRC-VAD Lexicon](https://saifmohammad.com/WebPages/nrc-vad.html) (Mohammad 2018, NRC Canada) | **Redistribution prohibited**; free for research use | `setup_nrcvad.py` downloads it from the official URL |
| [Meta Audiobox Aesthetics](https://github.com/facebookresearch/audiobox-aesthetics) | See its own license | installed as a package by the installer |
| [SONICS](https://github.com/awsaf49/sonics) checkpoint (optional) | See its own license | user fetches `sonics-alpha-120s` manually |
| [Demucs](https://github.com/adefossez/demucs) / [MuQ](https://github.com/tencent-ailab/MuQ) / [SingMOS](https://github.com/South-Twilight/SingMOS) | See each project's own license | installed as packages by the installer |

The Gemini API (used for the model-ear pillars) is a third-party service under
Google's terms; you supply your own API key.

See the 授權 and 鳴謝 sections of [`README.md`](README.md) for the full
acknowledgement list.
