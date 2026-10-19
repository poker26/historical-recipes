"""Скачивает веса Indra OCR закреплённой ревизии и сверяет контрольные суммы.

Запускается при сборке образа. Ревизия и суммы те же, что в пробе 19.10.2026
(и в пробе банкнот 16.10.2026): другая ревизия весов даёт другое распознавание,
поэтому молча обновляться нельзя. Несовпадение суммы останавливает сборку.

    python scripts/fetch_indra_weights.py /opt/indra-ocr
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "datalab-to/indra-ocr"
REVISION = "ed345c7f158eddbc7dee0c6a7269afb75a94f7e8"   # метка v3.1.1
FILES = {
    "det.onnx": "46b0050ce4c4692f887a1a9c038480223af1c7a91b3fcced2657d9cf00290dc4",
    "rec.onnx": "e243be069b357e56a0e867a21d7482e498df42fb19678bfb67a25f48b1f06151",
    "order.onnx": "23635dd2324b4ccc00c408af961273a2dedd2e4f0386bc54dba678c41bcf6b46",
    "orient.onnx": "8d7bfce5d08bcad55955f41ca494a7f9d9d3ed5d5b6da1f6f276d5c509590729",
    "meta.json": "ff6ec58f91c93a475ae9505ad21c73c3ecda0b378f1d889dd83408e10832d1bb",
    "LICENSE": "50c88a018a7ee322e89f6b09401b237beb0cd331685879405b3db48993950a5f",
}


def main(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, digest in FILES.items():
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        got = hashlib.sha256(data).hexdigest()
        if got != digest:
            raise SystemExit(f"{name}: sha256 {got} != {digest}")
        (target / name).write_bytes(data)
        print(f"ok {name} {len(data)}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/indra-ocr"))
