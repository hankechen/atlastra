"""
VideoChat3-4B viability probe for Apple Silicon (M4 / MPS).

Throwaway spike: answers ONE question — does MCG-NJU/VideoChat3-4B actually run
on this Mac, produce sane scouting text on a real clip, and how slow is it?
It is NOT wired into the app; it just prints a verdict.

    Works on MPS  -> green light for an offline `_tape_notes` batch (kills the Gemini dep)
    MPS op errors -> it auto-retries on CPU (slow but fine for a ~25-player overnight batch)
    Both fail     -> stay on Gemini / rent a cloud GPU for a one-off batch

--- one-time setup (in a throwaway venv, ~8 GB model download on first run) ---
    python3 -m venv .venv-vc3 && source .venv-vc3/bin/activate
    pip install "torch>=2.4" transformers accelerate qwen-vl-utils decord opencv-python-headless
    # NOTE: do NOT install flash-attn — it's CUDA-only and optional; we use eager attention.

--- run ---
    python tools/videochat3_probe.py                       # auto-picks a clip
    python tools/videochat3_probe.py path/to/clip.mp4
    python tools/videochat3_probe.py path/to/clip.mp4 "Custom prompt about the video"

First run downloads the weights to ~/.cache/huggingface (needs ~8 GB free; you have ~119 GB).
The MacBook Air is fanless, so sustained runs thermal-throttle — expect the CPU path to be
minutes per clip. That's acceptable for a precompute batch, not for anything live.
"""
import glob
import os
import sys
import time

# decord has no Apple-Silicon wheels and torchvision 0.28 removed io.read_video, so use
# torchcodec (the FFmpeg-backed decoder both qwen-vl-utils and transformers now prefer).
# Must be set before qwen_vl_utils is imported.
os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "torchcodec")

# A scouting-flavoured prompt so the probe demonstrates the ACTUAL use case, not just
# "describe this video". If the model can produce this, the feature is viable.
DEFAULT_PROMPT = (
    "You are a professional football scout watching a player's highlight clip. "
    "In 4-6 sentences, describe what the tape shows about this player: first touch, "
    "body shape before receiving, off-ball movement, preferred foot, and composure "
    "under pressure. Only describe what is visible; do not invent statistics."
)


def pick_clip(argv):
    if len(argv) > 1 and argv[1].endswith((".mp4", ".mov", ".mkv", ".webm")):
        return argv[1]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = sorted(glob.glob(os.path.join(here, "webapp/frontend/clips/players/*.mp4")))
    if not hits:
        sys.exit("No clip given and none found under webapp/frontend/clips/players/*.mp4")
    return hits[0]


def prompt_from(argv):
    # trailing free-text arg after an optional path -> custom prompt
    tail = [a for a in argv[1:] if not a.endswith((".mp4", ".mov", ".mkv", ".webm"))]
    return tail[0] if tail else DEFAULT_PROMPT


def sample_frames(clip, n=16, outdir=None):
    """Evenly sample n frames from the clip to JPGs and return their paths.

    We decode with OpenCV and hand qwen-vl-utils a *list of frames* rather than a
    video path — torchvision 0.28 removed torchvision.io.read_video and decord has no
    Apple-Silicon wheels, so the frame-list route is the only decoder-free path that
    still exercises the model's video pathway."""
    import cv2
    outdir = outdir or os.path.join(
        os.path.dirname(os.path.abspath(clip)), "_vc3_frames")
    os.makedirs(outdir, exist_ok=True)
    cap = cv2.VideoCapture(clip)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if total <= 0:
        cap.release()
        sys.exit(f"OpenCV read 0 frames from {clip} — is it a valid video?")
    idxs = [int(round(i * (total - 1) / (n - 1))) for i in range(n)] if total >= n \
        else list(range(total))
    paths = []
    for j, fi in enumerate(idxs):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.read()
        if not ok:
            continue
        p = os.path.join(outdir, f"f{j:03d}.jpg")
        cv2.imwrite(p, frame)
        paths.append(p)
    cap.release()
    if not paths:
        sys.exit(f"Extracted 0 frames from {clip}")
    print(f"[probe] sampled {len(paths)} frames from {total} total", flush=True)
    return paths


def main():
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        from qwen_vl_utils import process_vision_info
    except ImportError as e:
        sys.exit(f"Missing dep ({e.name}). Install into a venv first — see the module docstring.")

    model_id = "MCG-NJU/VideoChat3-4B"
    clip = pick_clip(sys.argv)
    prompt = prompt_from(sys.argv)

    # Prefer MPS (Apple GPU); fall back to CPU. fp16 on MPS (bf16 support there is patchy),
    # fp32 on CPU. eager attention because flash-attn is CUDA-only and sdpa on MPS can be flaky.
    if torch.backends.mps.is_available():
        device, dtype = "mps", torch.float16
    else:
        device, dtype = "cpu", torch.float32
    print(f"[probe] clip   : {clip}")
    print(f"[probe] device : {device}  dtype: {dtype}")
    print(f"[probe] loading {model_id} (first run downloads ~8 GB) ...", flush=True)

    def load_and_run(dev, dt):
        t0 = time.time()
        model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype=dt, trust_remote_code=True,
            attn_implementation="eager",
        ).to(dev)
        model.eval()
        processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        # The I3D-ViT vision tower hardcodes flash_attn_varlen_func (CUDA-only, absent on
        # Apple Silicon). Switch every vision block to the registered "sdpa" path, which
        # uses torch scaled_dot_product_attention and runs on MPS/CPU.
        n = sum(1 for m in model.modules() if hasattr(m, "attn_impl")
                and setattr(m, "attn_impl", "sdpa") is None)
        print(f"[probe] patched attn_impl=sdpa on {n} vision blocks", flush=True)
        load_s = time.time() - t0
        print(f"[probe] loaded in {load_s:.0f}s", flush=True)

        messages = [{
            "role": "user",
            "content": [
                {"type": "video", "video": clip},  # path; torchcodec decodes it
                {"type": "text", "text": prompt},
            ],
        }]
        text = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        images, videos, video_kwargs = process_vision_info(
            messages, image_patch_size=14,
            return_video_kwargs=True, return_video_metadata=True)
        # process_vision_info returns each video as a (frames_tensor, metadata) tuple.
        # transformers 4.57's make_batched_videos can't flatten that shape, so unwrap:
        # frame tensors go in `videos`, metadata goes in the separate `video_metadata` arg.
        video_meta = [v[1] for v in videos] if videos else None
        video_tensors = [v[0] for v in videos] if videos else None
        for k, v in list((video_kwargs or {}).items()):   # flatten any [x] -> x
            if isinstance(v, (list, tuple)) and len(v) == 1:
                video_kwargs[k] = v[0]
        inputs = processor(text=text, images=images, videos=video_tensors,
                           video_metadata=video_meta, do_resize=False,
                           return_tensors="pt", **(video_kwargs or {}))
        inputs = inputs.to(model.device)
        if hasattr(model, "dtype"):
            inputs = inputs.to(model.dtype)

        t1 = time.time()
        with torch.no_grad():
            generated_ids = model.generate(**inputs, max_new_tokens=256, do_sample=False)
        infer_s = time.time() - t1
        trimmed = [out[len(inp):] for inp, out in zip(inputs.input_ids, generated_ids)]
        out = processor.tokenizer.batch_decode(
            trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
        return load_s, infer_s, out.strip()

    import traceback
    try:
        load_s, infer_s, out = load_and_run(device, dtype)
    except Exception as e:  # noqa: BLE001 — MPS can throw NotImplementedError on unsupported ops
        print(f"\n[probe] {device} FAILED: {type(e).__name__}: {str(e)[:200]}", flush=True)
        print("---- full traceback ----\n" + traceback.format_exc() + "------------------------", flush=True)
        if device == "mps":
            print("[probe] retrying on CPU (slow) ...", flush=True)
            try:
                load_s, infer_s, out = load_and_run("cpu", torch.float32)
                device = "cpu"
            except Exception as e2:  # noqa: BLE001
                sys.exit(f"[probe] CPU also FAILED: {type(e2).__name__}: {str(e2)[:200]}")
        else:
            raise

    print("\n" + "=" * 72)
    print(f"VERDICT: ran on {device.upper()}  |  inference {infer_s:.0f}s for one ~7s clip")
    print("=" * 72)
    print(out)
    print("=" * 72)
    if device == "mps":
        print("MPS works -> viable as a live-ish/offline self-hosted pass.")
    else:
        print(f"CPU-only at ~{infer_s:.0f}s/clip -> viable ONLY as an overnight precompute batch.")
    print("Compare this text's quality against a Gemini report before committing.")


if __name__ == "__main__":
    main()
