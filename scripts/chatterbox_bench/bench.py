#!/usr/bin/env python3
"""Chatterbox-Turbo/Nano benchmark for Switchboard's TTS use case.

Answers three questions with numbers (and WAVs to listen to):
  1. Does it run on this GPU at all, and how much VRAM does it take?
  2. How fast? Real-time factor per chunk, and time to the *first* chunk
     (what a listener waits for under sentence-level streaming).
  3. Would streaming stall? Simulates a ~1,200-char answer through the
     Gateway's own chunker (twice: cold, then warm) and checks each chunk is
     ready before the previous one finishes playing. Also breaks the time
     down by stage (T3 / S3Gen / CPU watermark) to show where it goes.

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


# Where the time goes: Chatterbox-Turbo is T3 (autoregressive speech-token
# model) -> S3Gen (token-to-waveform: conv/flow decoder) -> a CPU watermark pass.
STAGES = {"t3": 0.0, "s3gen": 0.0, "watermark": 0.0}


def instrument(model):
    def wrap(obj, name, key):
        original = getattr(obj, name)

        def timed(*args, **kwargs):
            sync()
            t = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                sync()
                STAGES[key] += time.perf_counter() - t

        setattr(obj, name, timed)

    wrap(model.t3, "inference_turbo", "t3")
    wrap(model.s3gen, "inference", "s3gen")
    wrap(model.watermarker, "apply_watermark", "watermark")


def generate(model, text):
    """Returns (audio, wall_seconds, per-stage seconds)."""
    torch.manual_seed(0)
    for key in STAGES:
        STAGES[key] = 0.0
    sync()
    t = time.perf_counter()
    wav = model.generate(text)
    sync()
    elapsed = time.perf_counter() - t
    return wav.detach().float().cpu().numpy().reshape(-1), elapsed, dict(STAGES)


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

    instrument(model)
    log("\n== warm-up ==")
    generate(model, "This is a short warm up sentence.")

    log("\n== per-chunk speed (audio seconds produced per compute second; need > 1 to keep up) ==")
    log("cold = first time this text/shape is seen; warm = best of the repeats (shape solutions cached)")
    log(f"{'sample':16s} {'chars':>5s} {'audio_s':>8s} {'cold_s':>7s} {'warm_s':>7s} {'RTFcold':>8s} {'RTFwarm':>8s} | "
        f"{'t3':>5s} {'s3gen':>6s} {'wmark':>6s}  note")
    rows = []
    for name, text in SAMPLES.items():
        runs = [generate(model, text) for _ in range(max(2, args.reps))]
        audio, cold = runs[0][0], runs[0][1]
        warm_run = min(runs[1:], key=lambda r: r[1])
        warm, stages = warm_run[1], warm_run[2]
        audio_s = len(audio) / model.sr
        note = suspicious(text, audio_s)
        save_wav(os.path.join(args.out, f"{args.model}_{name}.wav"), audio, model.sr)
        rows.append({"sample": name, "chars": len(text), "audio_s": round(audio_s, 2),
                     "cold_s": round(cold, 3), "warm_s": round(warm, 3),
                     "rtf_cold": round(audio_s / cold, 1), "rtf_warm": round(audio_s / warm, 1),
                     "stages_warm_s": {k: round(v, 3) for k, v in stages.items()}, "note": note})
        log(f"{name:16s} {len(text):5d} {audio_s:8.2f} {cold:7.2f} {warm:7.2f} {audio_s / cold:8.1f} {audio_s / warm:8.1f} | "
            f"{stages['t3']:5.2f} {stages['s3gen']:6.2f} {stages['watermark']:6.2f}  {note}")
    results["samples"] = rows

    chunks = split_for_speech(ANSWER)

    def simulate(label):
        start = time.perf_counter()
        done_at, durs, totals = [], [], {k: 0.0 for k in STAGES}
        for chunk in chunks:
            audio, _, stages = generate(model, chunk)
            done_at.append(time.perf_counter() - start)
            durs.append(len(audio) / model.sr)
            for k in totals:
                totals[k] += stages[k]
        ttfa = done_at[0]
        played, slack = 0.0, []
        for k in range(1, len(chunks)):
            played += durs[k - 1]
            slack.append(ttfa + played - done_at[k])  # chunk k must be ready when chunk k-1 ends
        sim = {
            "pass": label,
            "chunks": len(chunks),
            "answer_chars": len(ANSWER),
            "audio_total_s": round(sum(durs), 1),
            "time_to_first_audio_s": round(ttfa, 2),
            "all_synthesized_s": round(done_at[-1], 1),
            "throughput_x_realtime": round(sum(durs) / done_at[-1], 2),
            "min_slack_s": round(min(slack), 1) if slack else None,
            "stage_totals_s": {k: round(v, 1) for k, v in totals.items()},
        }
        log(json.dumps(sim, indent=2))
        if slack:
            log(f"VERDICT ({label}): " + ("keeps ahead of playback" if min(slack) > 0
                                          else f"WOULD STALL (min slack {min(slack):.1f}s)"))
        return sim

    log("\n== streaming simulation: ~1,200-char answer through the Gateway's chunker ==")
    log("-- pass 1 (cold: chunk lengths this process has not synthesized yet) --")
    cold_sim = simulate("cold")
    log("-- pass 2 (warm: identical chunks again) --")
    warm_sim = simulate("warm")
    results["streaming_sim_cold"] = cold_sim
    results["streaming_sim_warm"] = warm_sim

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
