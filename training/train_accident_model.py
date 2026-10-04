"""Fine-tune R3D-18 to recognise crashes in 2-second CCTV windows (run on a Kaggle GPU).

Kaggle notebook (GPU T4 or P100, Internet ON so the Kinetics weights download):
    !python /kaggle/input/<your-dataset>/train_accident_model.py --data /kaggle/input/<your-dataset> --out /kaggle/working
Download /kaggle/working/accident_r3d18.pt (+ accident_val_report.json) when it ends.

Windows: 16 frames at 8 fps. Positive = window centre within [crash_start - 0.5 s,
crash_end + 1 s]. Negative = normal videos, and crash-video windows at least 3 s away
from the crash (hard negatives). Ambiguous windows are not used. Classes are balanced
every epoch. Validation videos pick the best epoch and the alarm threshold
(most crashes found with <= --max-fa false alarms per hour on validation).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def _import_ac(data: Path):
    for p in (data, Path(__file__).resolve().parent, Path(__file__).resolve().parents[1] / "detection"):
        if (p / "accident_classifier.py").exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import accident_classifier as ac
    return ac


def read_clip(path: Path, ac) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, img = cap.read()
        if not ok:
            break
        if img.shape[:2] != tuple(ac.SIZE):
            img = cv2.resize(img, (ac.SIZE[1], ac.SIZE[0]))
        frames.append(img[:, :, ::-1])
    cap.release()
    return np.stack(frames)


def window_labels(n: int, cs, ce, ac, step: int):
    """(start, label) for windows; label 1 crash, 0 normal; ambiguous windows dropped."""
    out = []
    for s in range(0, n - ac.CLIP_LEN + 1, step):
        if cs is None:
            out.append((s, 0))
            continue
        t0, t1 = s / ac.FPS, (s + ac.CLIP_LEN) / ac.FPS
        mid = (t0 + t1) / 2
        if cs - 0.5 <= mid <= ce + 1.0:
            out.append((s, 1))
        elif t1 < cs - 3.0 or t0 > ce + 3.0:
            out.append((s, 0))
    return out


def evaluate(model, vids, ac, device, max_fa: float):
    """Per-video crash probabilities on validation -> best threshold and its event metrics."""
    import torch
    model.eval()
    scored = []
    with torch.no_grad():
        for v in vids:
            starts = ac.window_starts(len(v["frames"]))
            probs = []
            for i in range(0, len(starts), 32):
                clips = np.stack([v["frames"][s:s + ac.CLIP_LEN] for s in starts[i:i + 32]])
                probs += torch.softmax(model(ac.to_tensor_batch(clips).to(device)), 1)[:, 1].float().cpu().tolist()
            scored.append((v, [(s + ac.CLIP_LEN) / ac.FPS for s in starts], probs))
    hours = sum(len(v["frames"]) / ac.FPS for v in vids) / 3600
    best = None
    for th in [x / 100 for x in range(30, 100, 5)]:
        tp = fp = n_crash = 0
        for v, times, probs in scored:
            ev = ac.events_from_probs(probs, times, th, 2)
            if v["cs"] is None:
                fp += len(ev)
                continue
            n_crash += 1
            hit = [t for t in ev if v["cs"] - 1.0 <= t <= v["ce"] + 10.0]
            tp += 1 if hit else 0
            fp += len(ev) - (1 if hit else 0)
        fa_h = fp / hours if hours else 0.0
        row = {"threshold": th, "crashes_found": tp, "crash_videos": n_crash, "false_alarms": fp,
               "false_alarms_per_hour": round(fa_h, 2)}
        key = (fa_h <= max_fa, tp, -fp, th)
        if best is None or key > best[0]:
            best = (key, row)
    return best[1]


def main() -> None:
    import torch

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=".")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--windows-per-epoch", type=int, default=3000)
    ap.add_argument("--max-fa", type=float, default=2.0, help="validation false alarms per hour allowed")
    ap.add_argument("--no-pretrained", action="store_true", help="tests only")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ac = _import_ac(data)
    rng = np.random.default_rng(a.seed)
    torch.manual_seed(a.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device}", flush=True)

    vids = {"train": [], "val": []}
    with open(data / "manifest.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fr = read_clip(data / r["file"], ac)
            cs = float(r["crash_start_s"]) if r["crash_start_s"] else None
            ce = float(r["crash_end_s"]) if r["crash_end_s"] else None
            vids[r["split"]].append({"file": r["file"], "frames": fr, "cs": cs, "ce": ce})
    train_w = [(i, s, y) for i, v in enumerate(vids["train"]) for s, y in window_labels(len(v["frames"]), v["cs"], v["ce"], ac, 2)]
    pos = [w for w in train_w if w[2] == 1]
    neg = [w for w in train_w if w[2] == 0]
    print(f"train videos {len(vids['train'])}, val videos {len(vids['val'])}; windows: {len(pos)} crash, {len(neg)} normal",
          flush=True)
    if not pos or not neg:
        raise SystemExit("Need both crash and normal training windows.")

    model = ac.build_model(pretrained=not a.no_pretrained).to(device)
    opt = torch.optim.AdamW([{"params": [p for n, p in model.named_parameters() if not n.startswith("fc.")], "lr": a.lr},
                             {"params": model.fc.parameters(), "lr": a.lr * 10}], weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.epochs)
    scaler = torch.amp.GradScaler("cuda", enabled=device == "cuda")
    loss_fn = torch.nn.CrossEntropyLoss()
    best, history = None, []
    for ep in range(1, a.epochs + 1):
        model.train()
        half = a.windows_per_epoch // 2
        batch = [pos[i] for i in rng.integers(0, len(pos), half)] + [neg[i] for i in rng.integers(0, len(neg), half)]
        rng.shuffle(batch)
        t0, tot, correct, n = time.time(), 0.0, 0, 0
        for i in range(0, len(batch), a.batch):
            part = batch[i:i + a.batch]
            clips = np.stack([vids["train"][vi]["frames"][s:s + ac.CLIP_LEN] for vi, s, _ in part])
            if rng.random() < 0.5:
                clips = clips[:, :, :, ::-1]                        # horizontal flip
            clips = np.clip(clips.astype(np.float32) * rng.uniform(0.8, 1.2), 0, 255).astype(np.uint8)
            x = ac.to_tensor_batch(clips, crop="random", rng=rng).to(device)
            y = torch.tensor([lab for _, _, lab in part], device=device)
            with torch.autocast(device_type="cuda", enabled=device == "cuda"):
                logits = model(x)
                loss = loss_fn(logits, y)
            opt.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            tot += float(loss.detach()) * len(part)
            correct += int((logits.argmax(1) == y).sum())
            n += len(part)
        sched.step()
        val = evaluate(model, vids["val"], ac, device, a.max_fa)
        row = {"epoch": ep, "train_loss": round(tot / n, 4), "train_acc": round(correct / n, 3), "seconds": round(time.time() - t0),
               **{f"val_{k}": v for k, v in val.items()}}
        history.append(row)
        print(row, flush=True)
        key = (val["false_alarms_per_hour"] <= a.max_fa, val["crashes_found"], -val["false_alarms"])
        if best is None or key > best[0]:
            best = (key, ep, val)
            torch.save({"state_dict": {k: (v.half() if v.is_floating_point() else v) for k, v in model.state_dict().items()}, "threshold": val["threshold"], "consecutive": 2,
                        "cooldown_s": 60.0, "fps": ac.FPS, "size": ac.SIZE, "clip_len": ac.CLIP_LEN, "epoch": ep,
                        "val": val, "arch": "r3d_18 (Kinetics-400 pretrained)"}, out / "accident_r3d18.pt")
    report = {"best_epoch": best[1], "best_val": best[2], "history": history,
              "train_videos": len(vids["train"]), "val_videos": len(vids["val"]),
              "train_windows": {"crash": len(pos), "normal": len(neg)}}
    (out / "accident_val_report.json").write_text(json.dumps(report, indent=2))
    print("saved", out / "accident_r3d18.pt", "best epoch", best[1], best[2], flush=True)


if __name__ == "__main__":
    main()
