"""
Send to plate — Home Assistant add-on.

Two paths:
  * a sliced .gcode / .gx goes straight to the printer
  * a model (.stl / .3mf / .obj / .step) is sliced here first

Served through HA Ingress, so Home Assistant handles authentication.
"""

import os
import re
import tempfile
from flask import Flask, request, jsonify, render_template_string

from printer import FlashForgePrinter, PrinterError
import slicer

app = Flask(__name__)

MAX_MB = int(os.environ.get("MAX_UPLOAD_MB", "512"))
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

PRINTER_HOST = os.environ.get("PRINTER_HOST", "")
PRINTER_SERIAL = os.environ.get("PRINTER_SERIAL") or None
PRINTER_CHECKCODE = os.environ.get("PRINTER_CHECKCODE") or None

DEFAULTS = {
    "material": os.environ.get("DEFAULT_MATERIAL", "pla"),
    "quality": os.environ.get("DEFAULT_QUALITY", "standard"),
    "infill": int(os.environ.get("DEFAULT_INFILL", "15")),
}

GCODE_EXT = {".gcode", ".gx"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._+-]")

jobs = slicer.JobRunner()


def get_printer():
    if not PRINTER_HOST:
        raise PrinterError("No printer address set. Open the add-on's Configuration tab.")
    return FlashForgePrinter(PRINTER_HOST, PRINTER_SERIAL, PRINTER_CHECKCODE)


def clean_filename(name):
    name = os.path.basename(name or "")
    return SAFE_NAME.sub("_", name)[:96] or "untitled"


@app.errorhandler(413)
def too_big(_):
    return jsonify({"ok": False,
                    "error": f"That file is over the {MAX_MB} MB limit."}), 413


@app.get("/")
def index():
    return render_template_string(
        PAGE,
        host=PRINTER_HOST or "not configured",
        maxmb=MAX_MB,
        can_slice=slicer.available(),
        defaults=DEFAULTS,
    )


@app.get("/api/status")
def status():
    try:
        p = get_printer()
        mode = "http" if p.http_available() else "tcp"
        detail = p.detail() if mode == "http" else None
        return jsonify({"ok": True, "mode": mode, "detail": detail,
                        "slicing": slicer.available()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc),
                        "slicing": slicer.available()}), 502


@app.post("/api/slice")
def start_slice():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file in the request."}), 400

    f = request.files["file"]
    name = clean_filename(f.filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in slicer.MODEL_EXT:
        return jsonify({"ok": False,
                        "error": f"{ext or 'That file'} isn't a model. "
                                 "Send an STL, 3MF, OBJ or STEP."}), 400

    workdir = tempfile.mkdtemp(prefix="model-")
    model_path = os.path.join(workdir, name)
    f.save(model_path)

    if os.path.getsize(model_path) == 0:
        return jsonify({"ok": False, "error": "That file is empty."}), 400

    try:
        infill = max(0, min(100, int(request.form.get("infill", DEFAULTS["infill"]))))
    except ValueError:
        infill = DEFAULTS["infill"]

    job_id = jobs.start(
        model_path, name,
        quality=request.form.get("quality", DEFAULTS["quality"]),
        material=request.form.get("material", DEFAULTS["material"]),
        infill=infill,
        supports=request.form.get("supports") == "1",
        brim=request.form.get("brim") == "1",
    )
    return jsonify({"ok": True, "job": job_id})


@app.get("/api/slice/<job_id>")
def slice_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "That job has expired."}), 404
    return jsonify({
        "ok": True,
        "state": job["state"],
        "name": job["name"],
        "error": job["error"],
        "estimates": job["estimates"],
    })


@app.post("/api/send/<job_id>")
def send_job(job_id):
    job = jobs.get(job_id)
    if not job or job["state"] != "done" or not job["gcode"]:
        return jsonify({"ok": False, "error": "Nothing sliced to send."}), 400

    with open(job["gcode"], "rb") as fh:
        payload = fh.read()

    name = os.path.basename(job["gcode"])
    print_now = request.form.get("print_now") == "1"
    level_first = request.form.get("level_first") == "1"

    try:
        result = get_printer().upload(name, payload, print_now, level_first)
    except PrinterError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify({"ok": True, "file": name, "bytes": len(payload),
                    "started": print_now,
                    "transport": result.get("transport", "http")})


@app.post("/api/upload")
def upload():
    """Direct path for files that are already sliced."""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file in the request."}), 400

    f = request.files["file"]
    name = clean_filename(f.filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in GCODE_EXT:
        return jsonify({"ok": False,
                        "error": f"{ext or 'That file'} isn't printable gcode."}), 400

    payload = f.read()
    if not payload:
        return jsonify({"ok": False, "error": "That file is empty."}), 400

    print_now = request.form.get("print_now") == "1"
    level_first = request.form.get("level_first") == "1"

    try:
        result = get_printer().upload(name, payload, print_now, level_first)
    except PrinterError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify({"ok": True, "file": name, "bytes": len(payload),
                    "started": print_now,
                    "transport": result.get("transport", "http")})


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Send to plate</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#0d1117; --plate:#151b23; --edge:#242e3a; --wire:#2f3d4d;
  --hot:#ff9e2c; --live:#5be3a0; --dead:#ff5f56;
  --text:#e6edf3; --mute:#7d8da1;
}
*{box-sizing:border-box}
body{
  margin:0;background:var(--ink);color:var(--text);
  font-family:'Space Grotesk',system-ui,sans-serif;-webkit-font-smoothing:antialiased;
  padding:24px 18px calc(40px + env(safe-area-inset-bottom));
  max-width:560px;margin-inline:auto;min-height:100vh;
}
header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:24px}
h1{font-size:19px;font-weight:700;letter-spacing:-.02em;margin:0}
.addr{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--mute);margin-top:3px}
.pill{display:inline-flex;align-items:center;gap:6px;font-family:'JetBrains Mono',monospace;
  font-size:11px;color:var(--mute);border:1px solid var(--edge);border-radius:99px;padding:5px 10px;flex:none}
.dot{width:6px;height:6px;border-radius:50%;background:var(--mute);transition:background .3s}
.dot.up{background:var(--live);box-shadow:0 0 8px var(--live)}
.dot.down{background:var(--dead)}

.plate{
  position:relative;border:1px solid var(--edge);border-radius:4px;
  background:
    linear-gradient(var(--wire) 1px,transparent 1px) 0 0/28px 28px,
    linear-gradient(90deg,var(--wire) 1px,transparent 1px) 0 0/28px 28px,
    var(--plate);
  background-blend-mode:soft-light;
  aspect-ratio:1/.72;display:grid;place-items:center;text-align:center;
  cursor:pointer;transition:border-color .2s,box-shadow .2s;overflow:hidden;
}
.plate:focus-visible{outline:2px solid var(--hot);outline-offset:3px}
.plate.armed{border-color:var(--hot);box-shadow:inset 0 0 60px rgba(255,158,44,.12)}
.plate.loaded{border-color:var(--live)}
.plate::after{content:'';position:absolute;left:0;bottom:0;width:9px;height:9px;
  border-left:1px solid var(--hot);border-bottom:1px solid var(--hot);opacity:.6}
.bed{position:absolute;bottom:7px;right:9px;font-family:'JetBrains Mono',monospace;
  font-size:9px;letter-spacing:.14em;color:var(--mute);opacity:.55}
.inner{padding:20px;pointer-events:none}
.inner strong{display:block;font-size:15px;font-weight:600;margin-bottom:5px}
.inner span{font-size:12.5px;color:var(--mute)}
.fname{font-family:'JetBrains Mono',monospace;font-size:12.5px;color:var(--live);
  word-break:break-all;line-height:1.5}
.fsize{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--mute);margin-top:6px}
input[type=file]{display:none}

.opts{margin-top:22px}
.opts.hide{display:none}
.field{margin-bottom:18px}
.field > label{display:block;font-size:12px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--mute);margin-bottom:8px}
.seg{display:flex;border:1px solid var(--edge);border-radius:3px;overflow:hidden}
.seg button{flex:1;padding:11px 4px;border:0;background:transparent;color:var(--mute);
  font-family:'Space Grotesk',sans-serif;font-size:13px;font-weight:600;cursor:pointer;
  border-right:1px solid var(--edge);margin:0;transition:background .15s,color .15s}
.seg button:last-child{border-right:0}
.seg button.on{background:var(--hot);color:#171004}
.seg button:focus-visible{outline:2px solid var(--text);outline-offset:-2px}
.seg small{display:block;font-weight:400;font-size:10px;opacity:.75;margin-top:1px}

.slider{display:flex;align-items:center;gap:14px}
.slider input[type=range]{flex:1;accent-color:var(--hot)}
.slider output{font-family:'JetBrains Mono',monospace;font-size:13px;min-width:42px;text-align:right}

label.row{display:flex;align-items:center;gap:11px;padding:13px 2px;
  border-bottom:1px solid var(--edge);cursor:pointer;font-size:14px}
label.row:first-of-type{border-top:1px solid var(--edge)}
label.row input{accent-color:var(--hot);width:17px;height:17px;flex:none}
label.row em{font-style:normal;color:var(--mute);font-size:12px;display:block;margin-top:2px}

button.go{
  width:100%;margin-top:22px;padding:16px;border:0;border-radius:3px;
  background:var(--hot);color:#171004;font-family:'Space Grotesk',sans-serif;
  font-size:15px;font-weight:700;cursor:pointer;
}
button.go:disabled{background:var(--edge);color:var(--mute);cursor:not-allowed}
button.go:focus-visible{outline:2px solid var(--text);outline-offset:2px}
button.ghost{background:transparent;border:1px solid var(--edge);color:var(--text);margin-top:10px}

.bar{height:2px;background:var(--edge);margin-top:16px;overflow:hidden;display:none}
.bar.on{display:block}
.bar i{display:block;height:100%;width:0;background:var(--hot);transition:width .2s}
.bar.pulse i{width:35%;animation:sweep 1.1s ease-in-out infinite}
@keyframes sweep{0%{margin-left:-35%}100%{margin-left:100%}}

.est{display:none;margin-top:18px;border:1px solid var(--edge);border-radius:3px}
.est.on{display:flex}
.est div{flex:1;padding:14px 12px;border-right:1px solid var(--edge)}
.est div:last-child{border-right:0}
.est dt{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin:0 0 5px}
.est dd{margin:0;font-family:'JetBrains Mono',monospace;font-size:15px;color:var(--live)}

.msg{margin-top:16px;font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.6;
  padding:12px 13px;border-left:2px solid var(--edge);color:var(--mute);display:none;word-break:break-word}
.msg.on{display:block}
.msg.good{border-color:var(--live);color:var(--live)}
.msg.bad{border-color:var(--dead);color:var(--dead)}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>

<header>
  <div>
    <h1>Send to plate</h1>
    <div class="addr">{{ host }}</div>
  </div>
  <div class="pill"><span class="dot" id="dot"></span><span id="stat">checking</span></div>
</header>

<div class="plate" id="plate" tabindex="0" role="button" aria-label="Choose a file">
  <div class="inner" id="inner">
    <strong>Drop a model or gcode</strong>
    <span>{% if can_slice %}.stl, .3mf, .obj, .step{% else %}slicing unavailable{% endif %} &middot; .gcode, .gx</span>
  </div>
  <div class="bed">220 &times; 220</div>
</div>
<input type="file" id="file" accept=".stl,.3mf,.obj,.step,.stp,.gcode,.gx">

<div class="opts hide" id="sliceopts">
  <div class="field">
    <label id="l-quality">Layer height</label>
    <div class="seg" role="group" aria-labelledby="l-quality" data-name="quality">
      <button type="button" data-v="draft">Draft<small>0.28 mm</small></button>
      <button type="button" data-v="standard">Standard<small>0.20 mm</small></button>
      <button type="button" data-v="fine">Fine<small>0.12 mm</small></button>
    </div>
  </div>

  <div class="field">
    <label id="l-material">Filament</label>
    <div class="seg" role="group" aria-labelledby="l-material" data-name="material">
      <button type="button" data-v="pla">PLA<small>210 / 60</small></button>
      <button type="button" data-v="petg">PETG<small>240 / 80</small></button>
      <button type="button" data-v="tpu">TPU<small>225 / 45</small></button>
    </div>
  </div>

  <div class="field">
    <label for="infill">Infill</label>
    <div class="slider">
      <input type="range" id="infill" min="0" max="100" step="5" value="{{ defaults.infill }}">
      <output id="infillout">{{ defaults.infill }}%</output>
    </div>
  </div>

  <label class="row">
    <input type="checkbox" id="supports">
    <span>Add supports
      <em>For overhangs steeper than 55&deg;</em></span>
  </label>
  <label class="row">
    <input type="checkbox" id="brim">
    <span>Add a brim
      <em>Helps tall or small-footprint prints stay put</em></span>
  </label>
</div>

<div class="est" id="est">
  <div><dt>Print time</dt><dd id="esttime">&mdash;</dd></div>
  <div><dt>Filament</dt><dd id="estfil">&mdash;</dd></div>
</div>

<div class="opts" id="sendopts" style="margin-top:20px">
  <label class="row">
    <input type="checkbox" id="printnow">
    <span>Start printing on arrival
      <em>Otherwise it just lands in the file list</em></span>
  </label>
</div>

<button class="go" id="go" disabled>Choose a file first</button>
<button class="go ghost" id="reset" style="display:none">Start over</button>
<div class="bar" id="bar"><i id="fill"></i></div>
<div class="msg" id="msg"></div>

<script>
const BASE = location.pathname.endsWith("/") ? location.pathname : location.pathname + "/";
const MAXMB = {{ maxmb }};
const CAN_SLICE = {{ 'true' if can_slice else 'false' }};
const $ = id => document.getElementById(id);

let picked = null, isModel = false, jobId = null, poll = null;
const choice = {
  quality: {{ defaults.quality|tojson }},
  material: {{ defaults.material|tojson }},
};

const human = b => b < 1024 ? b + " B"
  : b < 1048576 ? (b/1024).toFixed(0) + " KB"
  : (b/1048576).toFixed(1) + " MB";

function say(text, kind){
  const m = $("msg");
  m.textContent = text || "";
  m.className = "msg" + (text ? " on" : "") + (kind ? " " + kind : "");
}

// segmented controls
document.querySelectorAll(".seg").forEach(seg => {
  const name = seg.dataset.name;
  seg.querySelectorAll("button").forEach(b => {
    if(b.dataset.v === choice[name]) b.classList.add("on");
    b.onclick = () => {
      seg.querySelectorAll("button").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      choice[name] = b.dataset.v;
    };
  });
});

$("infill").oninput = e => $("infillout").textContent = e.target.value + "%";

function accept(f){
  const model = /\.(stl|3mf|obj|step|stp)$/i.test(f.name);
  const gcode = /\.(gcode|gx)$/i.test(f.name);
  if(!model && !gcode){
    say("Send a model (.stl, .3mf, .obj, .step) or sliced gcode.", "bad"); return;
  }
  if(model && !CAN_SLICE){
    say("Slicing isn't available in this add-on. Send sliced gcode instead.", "bad"); return;
  }
  if(f.size > MAXMB * 1048576){
    say("That file is over the " + MAXMB + " MB limit.", "bad"); return;
  }

  picked = f; isModel = model; jobId = null;
  $("plate").classList.add("loaded");
  $("inner").innerHTML = '<div class="fname"></div><div class="fsize">' + human(f.size) + '</div>';
  $("inner").querySelector(".fname").textContent = f.name;
  $("sliceopts").classList.toggle("hide", !model);
  $("est").classList.remove("on");
  $("reset").style.display = "none";
  $("go").disabled = false;
  $("go").textContent = model ? "Slice it" : "Send to printer";
  say("");
}

$("plate").onclick = () => $("file").click();
$("plate").onkeydown = e => {
  if(e.key === "Enter" || e.key === " "){ e.preventDefault(); $("file").click(); }
};
$("file").onchange = e => e.target.files[0] && accept(e.target.files[0]);

["dragenter","dragover"].forEach(ev =>
  $("plate").addEventListener(ev, e => { e.preventDefault(); $("plate").classList.add("armed"); }));
["dragleave","drop"].forEach(ev =>
  $("plate").addEventListener(ev, e => { e.preventDefault(); $("plate").classList.remove("armed"); }));
$("plate").addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if(f) accept(f); });

$("reset").onclick = () => {
  picked = null; jobId = null; isModel = false;
  $("file").value = "";
  $("plate").classList.remove("loaded");
  $("inner").innerHTML = '<strong>Drop a model or gcode</strong><span>.stl, .3mf, .obj, .step &middot; .gcode, .gx</span>';
  $("sliceopts").classList.add("hide");
  $("est").classList.remove("on");
  $("reset").style.display = "none";
  $("go").disabled = true;
  $("go").textContent = "Choose a file first";
  say("");
};

function busy(on, label, pulse){
  $("go").disabled = on;
  if(label) $("go").textContent = label;
  $("bar").classList.toggle("on", on);
  $("bar").classList.toggle("pulse", !!pulse);
  if(!on) $("fill").style.width = "0";
}

function post(url, fd, onProgress){
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    if(onProgress) xhr.upload.onprogress = e => {
      if(e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let r = {};
      try { r = JSON.parse(xhr.responseText); } catch(_){}
      (xhr.status === 200 && r.ok) ? resolve(r) : reject(r.error || ("Server returned " + xhr.status));
    };
    xhr.onerror = () => reject("Lost the connection.");
    xhr.send(fd);
  });
}

async function doSlice(){
  const fd = new FormData();
  fd.append("file", picked);
  fd.append("quality", choice.quality);
  fd.append("material", choice.material);
  fd.append("infill", $("infill").value);
  fd.append("supports", $("supports").checked ? "1" : "0");
  fd.append("brim", $("brim").checked ? "1" : "0");

  busy(true, "Uploading");
  say("Sending the model to the slicer.");
  const r = await post(BASE + "api/slice", fd, p => $("fill").style.width = (p*100) + "%");

  jobId = r.job;
  busy(true, "Slicing", true);
  say("Slicing. Larger models can take a minute or two.");

  return new Promise((resolve, reject) => {
    poll = setInterval(async () => {
      let s;
      try { s = await (await fetch(BASE + "api/slice/" + jobId)).json(); }
      catch(_){ return; }
      if(!s.ok || s.state === "failed"){
        clearInterval(poll);
        reject(s.error || "Slicing failed.");
      } else if(s.state === "done"){
        clearInterval(poll);
        resolve(s.estimates || {});
      }
    }, 1500);
  });
}

function showEstimates(e){
  $("esttime").textContent = e.time || "—";
  $("estfil").textContent = e.filament_g ? e.filament_g.toFixed(1) + " g" : "—";
  $("est").classList.add("on");
}

$("go").onclick = async () => {
  if(!picked) return;
  try {
    if(isModel && !jobId){
      const est = await doSlice();
      showEstimates(est);
      busy(false, "Send to printer");
      $("reset").style.display = "block";
      say("Sliced and ready.", "good");
      return;
    }

    const fd = new FormData();
    fd.append("print_now", $("printnow").checked ? "1" : "0");
    fd.append("level_first", "0");

    let r;
    if(jobId){
      busy(true, "Sending", true);
      say("Transferring to the printer.");
      r = await post(BASE + "api/send/" + jobId, fd);
    } else {
      fd.append("file", picked);
      busy(true, "Sending");
      say("Transferring " + human(picked.size) + " to the printer.");
      r = await post(BASE + "api/upload", fd, p => $("fill").style.width = (p*100) + "%");
    }

    busy(false, "Send another");
    $("reset").style.display = "block";
    say(r.started
      ? r.file + " is on the plate and printing."
      : r.file + " is on the printer. Pick it from the file list to start.", "good");

  } catch(err){
    if(poll) clearInterval(poll);
    busy(false, isModel && !jobId ? "Try again" : "Try again");
    say(String(err), "bad");
  }
};

fetch(BASE + "api/status").then(r => r.json()).then(d => {
  if(d.ok){
    $("dot").className = "dot up";
    $("stat").textContent = d.mode === "http" ? "online" : "legacy";
  } else {
    $("dot").className = "dot down";
    $("stat").textContent = "no reply";
    say(d.error, "bad");
  }
}).catch(() => {
  $("dot").className = "dot down";
  $("stat").textContent = "offline";
});
</script>
</body>
</html>"""
