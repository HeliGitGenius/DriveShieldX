# Training module

The four fine-tuned detectors were trained on Kaggle (T4 GPU). This folder makes
that reproducible from the repository instead of from notebook state.

| Script | Purpose |
|---|---|
| `inspect_checkpoint.py` | Provenance: base model, data path, classes, every hyper-parameter and how it differs from Ultralytics defaults, embedded validation metrics |
| `merge_datasets.py` | Build a merged dataset (class-name remapping + duplicate removal), as done for `merged_helmet`, `merged_seatbelt`, `tw_merged` |
| `train.py` | Retrain with the deployed model's own hyper-parameters (+ overrides); writes a candidate + model card; `--promote` installs it and archives the old weight |
| `../evaluation/seed_sweep.py` | N seeds → mean ± s.d. (paper Table 3) |
| `../evaluation/perclass_val.py` | Per-class P/R/mAP on a validation split |

Deployed weights (`models/*.pt`) are never modified unless you pass `--promote`.

## Kaggle recipe

1. Upload the repo (or just `training/` + `models/`) as a Kaggle dataset; attach the image datasets.
2. `!pip install ultralytics==8.4.121`
3. `!python -m training.merge_datasets ...` (if you need to rebuild the merged set)
4. `!python -m training.train --task helmet --data /kaggle/working/merged_helmet/data.yaml`
5. Save the notebook version so `/kaggle/working` (weights + model card) is persisted, then download
   `models/candidates/*.pt` + `.json`.

## Provenance of the deployed models (from `inspect_checkpoint.py`)

| Model | Base | Epochs | Batch | Optimizer | Seed | Ultralytics | Non-default augmentation |
|---|---|---|---|---|---|---|---|
| helmet_yolov8.pt | yolov8s | 100 | 16 | auto | 42 | 8.4.121 | hsv_s .5, degrees 8, shear 4, perspective .0004, mixup .1, cls .7, cos_lr |
| seatbelt_yolov8.pt | yolov8s | 100 | 16 | auto | 42 | 8.4.121 | hsv_s .6, hsv_v .5, degrees 5, scale .4, shear 3, perspective .0003, cls .7, cos_lr |
| twowheeler_best.pt | yolov8n | 100 | 32 | auto | 0 | 8.4.60 | degrees 8, shear 4, perspective .0004, close_mosaic 12 |
| plate_best_v2.pt | yolov8n | 80 | 32 | auto | 0 | 8.4.60 | degrees 10, shear 5, perspective .0005 |

All four: lr0 0.01, momentum 0.937, weight decay 0.0005, imgsz 640, warmup_bias_lr 0.0.
`optimizer: auto` means Ultralytics picks SGD or AdamW itself from the number of iterations.
