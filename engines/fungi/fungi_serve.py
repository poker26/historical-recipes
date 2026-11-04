"""Грибной движок «Что растёт» — бесплатная замена Kindwise (пилот 2026-08-24:
top-1 76.5%, top-5 95.7% на 2460 полевых фото / 938 видов).

BioCLIP-2 (MIT) + kNN по /work/fungi_index.npz. Обслуживает POST /identify:
тело = сырой JPEG, заголовок X-Engine-Token = общий секрет (fleet-IP публичный).
Ответ: {"engine": "bioclip-fungi", "candidates": [{"latin", "score"}, ...]}.

Трафик крошечный (единицы фото/день) → stdlib ThreadingHTTPServer без фреймворков.
Скор вида = среднее top-3 косинусов его референсов (как в пилоте). Запуск:
  docker run -d --name fungi-engine --restart unless-stopped -p 8977:8977 \
    -v /opt/id-shadow:/work -e FUNGI_ENGINE_TOKEN=... fungi-engine \
    python /work/fungi_serve.py
"""
import io
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch
import open_clip
from PIL import Image

WORK = "/work"
TOKEN = os.environ.get("FUNGI_ENGINE_TOKEN", "")
torch.set_num_threads(int(os.environ.get("NTHREADS", "4")))

print("loading index...", flush=True)
_d = np.load(f"{WORK}/fungi_index.npz", allow_pickle=True)
SLUGS = list(_d["slugs"])
MAT = _d["mat"].astype(np.float32)
# слаг → «Genus species» для моста к монографам (\_latin_key лоуэркейсит сам)
def _latin(slug: str) -> str:
    parts = slug.split("_")
    return (parts[0].capitalize() + " " + " ".join(parts[1:])) if len(parts) > 1 else slug.capitalize()

# Ключ индекса теряет дефис («crista_castrensis»), а без дефиса латынь не совпадёт с
# карточкой атласа. Настоящие имена берём из списков видов, по которым собраны индексы.
_REAL_LATIN: dict[str, str] = {}
for _u in ("fungi_universe.json", "moss_universe.json"):
    try:
        for _name in json.load(open(f"{WORK}/{_u}")):
            _REAL_LATIN.setdefault(re.sub(r"[^a-z]+", "_", _name.lower()).strip("_"), _name)
    except Exception as _e:
        print(f"{_u}: NOT loaded ({_e})", flush=True)
_latin_from_slug = _latin


def _latin(slug: str) -> str:
    real = _REAL_LATIN.get(slug)
    return real[:1].upper() + real[1:] if real else _latin_from_slug(slug)


print(f"index: {len(SLUGS)} vectors / {len(set(SLUGS))} species", flush=True)

# Классификатор ЦАРСТВА на том же эмбеддинге (логрегрессия, обучена на 9968
# грибных + 20288 растительных референсных векторах; замер на отложенных РЕАЛЬНЫХ
# фото 2026-08-25: на границе 0.5 — 94.7% грибов и 94.7% растений верно).
# Он решает, КАКОЙ движок показывать, вместо хрупкой лестницы порогов уверенности.
try:
    _k = np.load(f"{WORK}/kingdom_clf.npz")
    KW, KB = _k["w"].astype(np.float32), float(_k["b"][0])
    print("kingdom classifier: loaded", flush=True)
except Exception as _e:            # движок обязан работать и без классификатора
    KW, KB = None, 0.0
    print(f"kingdom classifier: NOT loaded ({_e})", flush=True)
# Мхи и печёночники (пилот 26.10.2026): отдельный индекс и классификатор «мох или
# нет» на том же эмбеддинге. Pl@ntNet мхов не знает, поэтому бэкенд при высоком
# p_moss показывает ответ мохового индекса. Движок обязан работать и без них.
# Атлас заводил часть мхов под старыми именами; их снимки сливаются с видом под
# принятым именем, чтобы в пятёрке не стоял один вид дважды.
MOSS_MERGE = {"hypnum_triquetrum": "hylocomiadelphus_triquetrus",
              "hypnum_cuspidatum": "calliergonella_cuspidata",
              "bryum_caespiticium": "ptychostomum_imbricatulum",
              "mnium_cuspidatum": "plagiomnium_cuspidatum",
              "neckera_crispa": "exsertotheca_crispa",
              "orthotrichum_affine": "lewinskya_affinis",
              "sphagnum_acutifolium": "sphagnum_capillifolium",
              "andreaea_petrophila": "andreaea_rupestris"}
try:
    _m = np.load(f"{WORK}/moss_index.npz", allow_pickle=True)
    MOSS_SLUGS = [MOSS_MERGE.get(s, s) for s in _m["slugs"]]
    MOSS_MAT = _m["mat"].astype(np.float32)
    _mc = np.load(f"{WORK}/moss_clf.npz")
    MW, MB = _mc["w"].astype(np.float32), float(_mc["b"][0])
    print(f"moss index: {len(MOSS_SLUGS)} vectors / {len(set(MOSS_SLUGS))} species", flush=True)
except Exception as _e:
    MOSS_SLUGS, MOSS_MAT, MW, MB = [], None, None, 0.0
    print(f"moss index: NOT loaded ({_e})", flush=True)
print("loading BioCLIP-2...", flush=True)
MODEL, _, PREPROCESS = open_clip.create_model_and_transforms("hf-hub:imageomics/bioclip-2")
MODEL.eval()
print("ready", flush=True)


def _rank(slugs, mat, v, limit):
    agg: dict[str, list[float]] = {}
    for sl, sc in zip(slugs, mat @ v):
        agg.setdefault(sl, []).append(float(sc))
    ranked = sorted(((float(np.mean(sorted(vv)[-3:])), k) for k, vv in agg.items()), reverse=True)
    return [{"latin": _latin(k), "score": round(s, 4)} for s, k in ranked[:limit]]


def identify(jpeg: bytes, limit: int = 5) -> dict:
    im = PREPROCESS(Image.open(io.BytesIO(jpeg)).convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        v = MODEL.encode_image(im)
        v = (v / v.norm(dim=-1, keepdim=True)).squeeze(0).numpy().astype(np.float32)
    out = {"candidates": _rank(SLUGS, MAT, v, limit),
           "p_fungus": None if KW is None else round(float(1.0 / (1.0 + np.exp(-(v @ KW + KB)))), 4)}
    if MOSS_MAT is not None:
        out["p_moss"] = round(float(1.0 / (1.0 + np.exp(-(v @ MW + MB)))), 4)
        out["moss_candidates"] = _rank(MOSS_SLUGS, MOSS_MAT, v, limit)
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # компактный лог
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _reply(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"ok": True, "species": len(set(SLUGS)),
                              "kingdom_clf": KW is not None,
                              "moss_species": len(set(MOSS_SLUGS))})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/identify":
            self._reply(404, {"error": "not found"}); return
        if TOKEN and self.headers.get("X-Engine-Token") != TOKEN:
            self._reply(403, {"error": "bad token"}); return
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > 15_000_000:
            self._reply(422, {"error": "bad body size"}); return
        jpeg = self.rfile.read(n)
        try:
            t0 = time.time()
            out = identify(jpeg)
            self._reply(200, {"engine": "bioclip-fungi", **out,
                              "took_ms": int((time.time() - t0) * 1000)})
        except Exception as e:
            self._reply(500, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8977), Handler).serve_forever()
