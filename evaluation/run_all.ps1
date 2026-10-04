# DriveShieldX -- run every evaluation that has data on this laptop.
# Usage (PowerShell, from the project root, venv active):
#   powershell -ExecutionPolicy Bypass -File evaluation\run_all.ps1 -Datasets "C:\datasets" -Videos "C:\path\to\triple_riding_video_1.mp4","C:\path\to\seat_belt_video_1.mp4"
param(
  [string]$Datasets = "C:\datasets",
  [string[]]$Videos = @(),
  [int]$NumSeqs = 10,
  [string]$Device = "cpu"
)
$ErrorActionPreference = "Stop"
$py = "python"

Write-Host "== 0. unit tests (gate, fog, accident, registry, dispatch)"
& $py -m pytest tests\test_gate.py tests\test_fog.py tests\test_accident.py tests\test_registry_dispatch.py -q

Write-Host "== 0b. training provenance from the checkpoints"
& $py -m training.inspect_checkpoint models\helmet_yolov8.pt models\seatbelt_yolov8.pt models\twowheeler_best.pt models\plate_best_v2.pt --json evaluation\results\provenance.json

$img = Join-Path $Datasets "DETRAC-Images"
$trn = Join-Path $Datasets "DETRAC-Train-Annotations-XML"
$tst = Join-Path $Datasets "DETRAC-Test-Annotations-XML"
if (Test-Path $img) {
  Write-Host "== 1. cache deployed detections on $NumSeqs UA-DETRAC sequences"
  & $py -m evaluation.cache_detections --images-root $img --ann-roots $trn $tst --num-seqs $NumSeqs --device $Device --out evaluation\cache
  Write-Host "== 2. tracker comparison (ByteTrack / Centroid / DeepSORT / no-tracker ablation)"
  & $py -m evaluation.run_tracking_eval --cache evaluation\cache --images-root $img --ann-roots $trn $tst --out evaluation\results\tracking
} else { Write-Warning "UA-DETRAC images not found at $img -- skipping tracking." }

if ($Videos.Count -gt 0) {
  Write-Host "== 3. disagreement gate on real footage"
  & $py -m evaluation.gate_eval measure --source $Videos --out evaluation\results\gate
  Write-Host "== 3b. violation-level labelling sheet (legacy vs current rules)"
  & $py -m evaluation.violation_eval make-sheet --source $Videos --out evaluation\violation_bench
  Write-Host "== 3c. fog sanity check (scattering model on your own frames)"
  & $py -m evaluation.fog_eval synthetic --frames snapshots --out evaluation\results\fog
  Write-Host "== 4. ANPR labelling sheet"
  & $py -m evaluation.anpr_benchmark make-sheet --source $Videos --out evaluation\anpr_bench
  Write-Host "== 5. throughput"
  foreach ($v in $Videos) {
    & $py -m evaluation.throughput --video $v --frames 200 --device $Device
    & $py -m evaluation.throughput --video $v --frames 200 --device $Device --npr
  }
} else { Write-Warning "No -Videos given -- skipping gate, ANPR sheet and throughput." }

Write-Host "== 6. database metrics"
& $py -m evaluation.db_metrics

Write-Host "== 7. per-class validation (only if the training data.yaml paths exist here)"
& $py -m evaluation.perclass_val --all-from-checkpoints --device $Device

Write-Host "== 8. report"
& $py -m evaluation.make_report
Write-Host "Done -> evaluation\results\RESULTS.md"
