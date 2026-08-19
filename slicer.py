"""
Slicing via the PrusaSlicer command line.

PrusaSlicer does the actual work: mesh repair, perimeters, infill, supports,
cooling, retraction. This module only builds the argument list, runs it, and
reads back the estimates PrusaSlicer writes into the gcode footer.
"""

import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid

PROFILE = "/opt/ffupload/profiles/ad5m.ini"
BINARY = shutil.which("prusa-slicer") or shutil.which("prusa-slicer-console") \
    or "/usr/bin/prusa-slicer"

MODEL_EXT = {".stl", ".3mf", ".obj", ".step", ".stp"}

QUALITY = {
    "draft":    {"layer_height": "0.28", "first_layer_height": "0.3"},
    "standard": {"layer_height": "0.2",  "first_layer_height": "0.25"},
    "fine":     {"layer_height": "0.12", "first_layer_height": "0.2"},
}

MATERIAL = {
    "pla": {
        "temperature": "210", "first_layer_temperature": "215",
        "bed_temperature": "60", "first_layer_bed_temperature": "60",
        "fan_always_on": "1", "max_fan_speed": "100",
    },
    "petg": {
        "temperature": "240", "first_layer_temperature": "245",
        "bed_temperature": "80", "first_layer_bed_temperature": "80",
        "fan_always_on": "1", "max_fan_speed": "50",
        "retract_length": "1.2",
    },
    "tpu": {
        "temperature": "225", "first_layer_temperature": "230",
        "bed_temperature": "45", "first_layer_bed_temperature": "45",
        "fan_always_on": "1", "max_fan_speed": "60",
        "retract_length": "0.4", "retract_speed": "20",
        "perimeter_speed": "30", "infill_speed": "35",
        "external_perimeter_speed": "25", "travel_speed": "150",
    },
}


class SliceError(Exception):
    pass


def available():
    return os.path.exists(BINARY)


def _overrides(quality, material, infill, supports, brim):
    opts = {}
    opts.update(QUALITY.get(quality, QUALITY["standard"]))
    opts.update(MATERIAL.get(material, MATERIAL["pla"]))
    opts["fill_density"] = f"{int(infill)}%"
    opts["support_material"] = "1" if supports else "0"
    if brim:
        opts["brim_width"] = "5"
    return opts


def _parse_estimates(gcode_path):
    """PrusaSlicer writes its estimates as comments at the end of the file."""
    out = {"time": None, "filament_g": None, "filament_mm": None}
    try:
        with open(gcode_path, "rb") as f:
            f.seek(max(0, os.path.getsize(gcode_path) - 8192))
            tail = f.read().decode("utf-8", "ignore")
    except OSError:
        return out

    m = re.search(r"estimated printing time.*?=\s*(.+)", tail)
    if m:
        out["time"] = m.group(1).strip()
    m = re.search(r"total filament used \[g\]\s*=\s*([\d.]+)", tail)
    if m:
        out["filament_g"] = float(m.group(1))
    m = re.search(r"filament used \[mm\]\s*=\s*([\d.]+)", tail)
    if m:
        out["filament_mm"] = float(m.group(1))
    return out


def slice_model(model_path, quality="standard", material="pla",
                infill=15, supports=False, brim=False, timeout=1800):
    """Slice a model file and return (gcode_path, estimates)."""
    if not available():
        raise SliceError("The slicing engine isn't installed in this add-on.")

    ext = os.path.splitext(model_path)[1].lower()
    if ext not in MODEL_EXT:
        raise SliceError(f"{ext} isn't a model file. Send an STL, 3MF, OBJ or STEP.")

    workdir = tempfile.mkdtemp(prefix="slice-")
    base = os.path.splitext(os.path.basename(model_path))[0]
    out_path = os.path.join(workdir, base + ".gcode")

    cmd = [BINARY, "--export-gcode", "--load", PROFILE,
           "--output", out_path, "--center", "110,110"]
    for key, value in _overrides(quality, material, infill, supports, brim).items():
        cmd += [f"--{key.replace('_', '-')}", value]
    cmd.append(model_path)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        shutil.rmtree(workdir, ignore_errors=True)
        raise SliceError("Slicing took too long and was stopped.")

    if proc.returncode != 0 or not os.path.exists(out_path):
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"exit code {proc.returncode}"
        shutil.rmtree(workdir, ignore_errors=True)
        raise SliceError(_friendly(message))

    return out_path, _parse_estimates(out_path)


def _friendly(message):
    low = message.lower()
    if "outside" in low or "bed" in low and "fit" in low:
        return "The model doesn't fit on a 220 x 220 x 220 mm bed."
    if "empty" in low or "no extrusions" in low:
        return "Nothing to print — the model came out empty. It may be too small or broken."
    if "not manifold" in low or "repair" in low:
        return "The mesh is broken and couldn't be repaired automatically."
    return message[:300]


# ---------- background jobs ----------

class JobRunner:
    """Slicing takes a while, so it runs off the request thread."""

    def __init__(self, keep=8):
        self._jobs = {}
        self._order = []
        self._lock = threading.Lock()
        self._keep = keep

    def start(self, model_path, original_name, **kwargs):
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id, "state": "slicing", "name": original_name,
                "error": None, "gcode": None, "estimates": None,
            }
            self._order.append(job_id)
            self._prune()

        t = threading.Thread(
            target=self._run, args=(job_id, model_path), kwargs=kwargs, daemon=True)
        t.start()
        return job_id

    def _run(self, job_id, model_path, **kwargs):
        try:
            gcode, estimates = slice_model(model_path, **kwargs)
            self._update(job_id, state="done", gcode=gcode, estimates=estimates)
        except SliceError as exc:
            self._update(job_id, state="failed", error=str(exc))
        except Exception as exc:
            self._update(job_id, state="failed", error=f"Unexpected failure: {exc}")
        finally:
            try:
                shutil.rmtree(os.path.dirname(model_path), ignore_errors=True)
            except Exception:
                pass

    def _update(self, job_id, **fields):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _prune(self):
        while len(self._order) > self._keep:
            old = self._order.pop(0)
            job = self._jobs.pop(old, None)
            if job and job.get("gcode"):
                shutil.rmtree(os.path.dirname(job["gcode"]), ignore_errors=True)
