#!/usr/bin/env python3
"""Chatterbox-Turbo/Nano benchmark for Switchboard's TTS use case.

Answers three questions with numbers (and WAVs to listen to):
  1. Does it run on this GPU at all, and how much VRAM does it take?
  2. How fast? Real-time factor per chunk, and time to the *first* chunk
     (what a listener waits for under sentence-level streaming).
  3. Would streaming stall? Simulates a ~1,200-char answer through the
     Gateway's own chunker and checks each chunk is ready before the
     previous one finishes playing.

Outputs: <out>/*.wav (listen for hallucinated words, cut-offs, odd pauses)
and <out>/results.json. Run via run.sh.
"""

import argparse
import json
import os
import re
import sys
import time
import wave

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # the Gateway's real chunker, bind-mounted by run.sh
    from switchboard_text import split_for_speech
except ImportError:
    def split_for_speech(text, **_):
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
        return parts

# Chunk-sized texts like the ones the Gateway would actually send, plus the
# awkward cases that tend to trip TTS models.
SAMPLES = {
    "tiny_1": "Sure.",
    "tiny_2": "Yes, that's right.",
    "first_chunk": "The French Revolution reshaped European politics by challenging monarchy and aristocratic privilege.",
    "mid_chunk": (
        "Its ideas about citizenship and rights spread well beyond France, inspiring reformers "
        "in neighbouring countries while frightening the kings and emperors who ruled them."
    ),
    "long_chunk": (
        "Napoleon later carried many of the revolution's legal reforms across the continent by force of arms, "
        "which meant that people who never supported the revolution still lived under its civil code, "
        "its secular courts, and its centralized administration, long after he had been defeated and exiled."
    ),
    "numbers_abbrev": (
        "Dr. Smith met J. R. R. Tolkien at 3.5 p.m. on March 4th, 1987, in St. Louis, and paid $1,250.75."
    ),
    "paralinguistic": "Oh, that's a good one [chuckle]. Let me think about that for a second.",
}

ANSWER = (
    "The French Revolution reshaped European politics by challenging monarchy and aristocratic privilege. "
    "Its ideas about citizenship and rights spread well beyond France, inspiring reformers in neighbouring "
    "countries while frightening the kings and emperors who ruled them.\n\n"
    "The consequences unfolded in stages. First came the fall of the monarchy and the radical phase of the "
    "revolution, with its committees, its tribunals, and its fear. Then came the Directory, which was weak "
    "and unpopular, and finally Napoleon, who promised order and delivered an empire.\n\n"
    "Napoleon later carried many of the revolution's legal reforms across the continent by force of arms. "
    "People who never supported the revolution still lived under its civil code, its secular courts, and its "
    "centralized administration long after he had been defeated and exiled.\n\n"
    "The reaction was just as important as the revolution itself. The Congress of Vienna tried to restore the "
    "old order, but it could not undo the idea that governments derive their authority from the governed. "
    "That idea resurfaced in 1830, in 1848, and again and again through the nineteenth century, which is why "
    "historians still argue about whether the revolution ended in 1799, in 1815, or not at all."
)


def log(*args):
    print(*args, flush=True)


def sync():
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def save_wav(path, audio, sr):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def generate(model, text):
    torch.manual_seed(0)
    sync()
    t = time.perf_counter()
    wav = model.generate(text)
    sync()
    elapsed = time.perf_counter() - t
    return wav.detach().float().cpu().numpy().reshape(-1), elapsed


def suspicious(text, audio_s):
    """Cheap hallucination/cut-off heuristic on duration vs text length."""
    expected = 0.065 * len(text)  # ~15 chars/s, typical speech
    if audio_s > expected * 2 + 1.5:
        return "audio much LONGER than text (extra/hallucinated speech?)"
    if audio_s < expected * 0.4 and len(text) > 15:
        return "audio much SHORTER than text (cut off?)"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["turbo", "nano"], default="turbo")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--ref", help="reference voice clip (~10s wav) to clone; default voice if omitted")
    ap.add_argument("--reps", type=int, default=2, help="timed repetitions per sample")
    ap.add_argument("--out", default="/out")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    log("== environment ==")
    log(f"torch {torch.__version__} | hip={torch.version.hip} cuda={torch.version.cuda}")
    use_cuda = args.device == "cuda"
    if use_cuda and not torch.cuda.is_available():
        log("ERROR: --device cuda but torch.cuda.is_available() is False")
        log("  (ROCm: check /dev/kfd and /dev/dri are passed through; NVIDIA: --gpus all)")
        sys.exit(1)
    results = {"model": args.model, "device": args.device, "torch": torch.__version__}
    if use_cuda:
        props = torch.cuda.get_device_properties(0)
        results["gpu"] = props.name
        results["vram_total_gb"] = round(props.total_memory / 2**30, 1)
        log(f"GPU: {props.name} | {results['vram_total_gb']} GB | "
            f"free now: {torch.cuda.mem_get_info()[0] / 2**30:.1f} GB")

    log("\n== loading model ==")
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    if use_cuda:
        torch.cuda.reset_peak_memory_stats()
    t = time.perf_counter()
    model = ChatterboxTurboTTS.from_pretrained(device=args.device, nano=(args.model == "nano"))
    results["load_s"] = round(time.perf_counter() - t, 1)
    results["sample_rate"] = model.sr
    log(f"loaded in {results['load_s']}s, sample rate {model.sr} Hz")

    if args.ref:
        t = time.perf_counter()
        model.prepare_conditionals(args.ref)
        results["prepare_voice_s"] = round(time.perf_counter() - t, 2)
        log(f"prepared reference voice {args.ref} in {results['prepare_voice_s']}s "
            f"(this is paid once per persona if cached, per request if not)")

    log("\n== warm-up ==")
    generate(model, "This is a short warm up sentence.")

    log("\n== per-chunk speed (sec of audio produced per sec of compute; need > 1 to keep up) ==")
    log(f"{'sample':16s} {'chars':>5s} {'audio_s':>8s} {'wall_s':>7s} {'RTF':>6s}  note")
    rows = []
    for name, text in SAMPLES.items():
        walls, audio = [], None
        for _ in range(args.reps):
            audio, el = generate(model, text)
            walls.append(el)
        audio_s = len(audio) / model.sr
        wall = min(walls)
        note = suspicious(text, audio_s)
        save_wav(os.path.join(args.out, f"{args.model}_{name}.wav"), audio, model.sr)
        rows.append({"sample": name, "chars": len(text), "audio_s": round(audio_s, 2),
                     "wall_s": round(wall, 3), "rtf": round(audio_s / wall, 1), "note": note})
        log(f"{name:16s} {len(text):5d} {audio_s:8.2f} {wall:7.3f} {audio_s / wall:6.1f}  {note}")
    results["samples"] = rows

    log("\n== streaming simulation: ~1,200-char answer through the Gateway's chunker ==")
    chunks = split_for_speech(ANSWER)
    start = time.perf_counter()
    done_at, durs = [], []
    for chunk in chunks:
        audio, _ = generate(model, chunk)
        done_at.append(time.perf_counter() - start)
        durs.append(len(audio) / model.sr)
    ttfa = done_at[0]
    played, slack = 0.0, []
    for k in range(1, len(chunks)):
        played += durs[k - 1]
        slack.append(ttfa + played - done_at[k])  # chunk k must be ready when chunk k-1 ends
    sim = {
        "chunks": len(chunks),
        "answer_chars": len(ANSWER),
        "audio_total_s": round(sum(durs), 1),
        "time_to_first_audio_s": round(ttfa, 2),
        "all_synthesized_s": round(done_at[-1], 1),
        "min_slack_s": round(min(slack), 1) if slack else None,
    }
    results["streaming_sim"] = sim
    log(json.dumps(sim, indent=2))
    if slack:
        log("VERDICT: " + ("keeps ahead of playback" if min(slack) > 0
                           else f"WOULD STALL (min slack {min(slack):.1f}s)"))

    if use_cuda:
        results["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
        log(f"\npeak VRAM allocated by this process: {results['peak_vram_gb']} GB "
            f"(of {results['vram_total_gb']} GB)")

    with open(os.path.join(args.out, "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    log(f"\nWAVs and results.json written to {args.out}. Listen to the tiny_*, numbers_abbrev and "
        f"paralinguistic samples first -- those are where artifacts show up.")


if __name__ == "__main__":
    main()
