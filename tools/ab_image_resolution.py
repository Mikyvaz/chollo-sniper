#!/usr/bin/env python3
"""T3.10 — A/B controlado de resolución de imagen (384 px vs 768 px) para Gemini Vision.

Por defecto ejecuta en modo simulación (--dry-run) sin hacer peticiones externas.
Para ejecutar llamadas reales a Gemini API (con aprobación previa), pasar `--live`.
"""
import argparse
import base64
import json
import os
import re
import sys

import core_engine as ce

DEFAULT_PROMPT = (
    "Audita la foto del producto de segunda mano. "
    "Responde SOLO JSON válido con: "
    '{"is_valid_deal": true|false, "visual_notes": "breve estado y desperfectos visibles"}.'
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def collect_sample_images(kits_dir: str = "kits", max_photos: int = 20) -> list[str]:
    """Devuelve hasta `max_photos` rutas de imágenes ordenadas dentro de `kits_dir`."""
    if not kits_dir or not os.path.isdir(kits_dir):
        return []
    found = []
    for root, _dirs, files in os.walk(kits_dir):
        for fname in sorted(files):
            if fname.lower().endswith(IMAGE_EXTS):
                found.append(os.path.join(root, fname))
    found.sort()
    return found[: max(0, int(max_photos))]


def _parse_verdict_json(raw_text: str) -> bool:
    cleaned = re.sub(r"```json|```", "", str(raw_text or "")).strip()
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and "is_valid_deal" in data:
                return bool(data["is_valid_deal"])
        except Exception:
            pass
    return "true" in cleaned.lower()


def _default_gemini_vision_call(prompt: str, img_b64: str, model: str, api_key: str) -> dict:
    """Llamada directa a Gemini REST v1beta sin thinkingConfig (Invariante 1)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
            ]
        }],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 200},
    }
    resp = ce.HTTP_SESSION.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usageMetadata") or {}
    tokens_img, _tokens_text = ce._prompt_tokens_by_modality(usage)
    candidates = data.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return {"tokens_img": int(tokens_img or 0), "is_valid_deal": _parse_verdict_json(text)}


def evaluate_ab_results(records: list[dict]) -> dict:
    """Resume tokens medios, ratio 768/384, concordancia de veredictos y recomendación."""
    n = len(records)
    if n == 0:
        return {
            "count": 0,
            "avg_tokens_384": 0.0,
            "avg_tokens_768": 0.0,
            "token_ratio": None,
            "agreement_count": 0,
            "agreement_pct": 0.0,
            "recommendation": "insufficient_data",
        }
    avg_384 = sum(float(r.get("tokens_384", 0) or 0) for r in records) / n
    avg_768 = sum(float(r.get("tokens_768", 0) or 0) for r in records) / n
    ratio = round(avg_768 / avg_384, 3) if avg_384 > 0 else None
    agree = sum(1 for r in records if bool(r.get("verdict_384")) == bool(r.get("verdict_768")))
    agree_pct = round((agree / n) * 100.0, 1)
    if ratio is not None and ratio <= 1.1:
        rec = "raise_image_max_dim_768"
    else:
        rec = "test_media_resolution_low"
    return {
        "count": n,
        "avg_tokens_384": round(avg_384, 1),
        "avg_tokens_768": round(avg_768, 1),
        "token_ratio": ratio,
        "agreement_count": agree,
        "agreement_pct": agree_pct,
        "recommendation": rec,
    }


def format_ab_report(result: dict) -> str:
    if result.get("dry_run"):
        return (
            f"[DRY-RUN] Fotos seleccionadas: {result.get('photos_found', 0)} "
            f"(límite={result.get('max_photos', 20)}, modelo={result.get('model')}). "
            "Usa --live para ejecutar llamadas reales tras aprobación."
        )
    s = result.get("summary") or {}
    return (
        f"A/B Visión ({s.get('count', 0)} fotos, modelo={result.get('model')}): "
        f"tokens_img 384px={s.get('avg_tokens_384')} vs 768px={s.get('avg_tokens_768')} "
        f"(ratio={s.get('token_ratio')}) | concordancia={s.get('agreement_count')}/{s.get('count')} "
        f"({s.get('agreement_pct')}%) | recomendación={s.get('recommendation')}"
    )


def run_ab(kits_dir: str = "kits", max_photos: int = 20, live: bool = False,
           model: str | None = None, api_key: str | None = None, caller=None) -> dict:
    eff_model = model or getattr(ce, "AI_MODEL", "gemini-3.5-flash-lite")
    photos = collect_sample_images(kits_dir=kits_dir, max_photos=max_photos)
    if not live:
        return {
            "dry_run": True,
            "model": eff_model,
            "kits_dir": kits_dir,
            "max_photos": max_photos,
            "photos_found": len(photos),
            "photos": photos,
            "records": [],
            "summary": evaluate_ab_results([]),
        }

    eff_key = api_key if api_key is not None else getattr(ce, "GEMINI_API_KEY", "")
    if not eff_key and caller is None:
        raise RuntimeError("Falta GEMINI_API_KEY para ejecutar --live.")
    call_fn = caller or _default_gemini_vision_call

    records = []
    for path in photos:
        with open(path, "rb") as fh:
            raw_bytes = fh.read()
        comp_384 = ce.compress_image_to_jpeg_640(raw_bytes, max_dim=384)
        comp_768 = ce.compress_image_to_jpeg_640(raw_bytes, max_dim=768)
        b64_384 = base64.b64encode(bytes(comp_384)).decode("ascii")
        b64_768 = base64.b64encode(bytes(comp_768)).decode("ascii")
        res_384 = call_fn(DEFAULT_PROMPT, b64_384, eff_model, eff_key)
        res_768 = call_fn(DEFAULT_PROMPT, b64_768, eff_model, eff_key)
        records.append({
            "path": path,
            "tokens_384": int(res_384.get("tokens_img", 0) or 0),
            "tokens_768": int(res_768.get("tokens_img", 0) or 0),
            "verdict_384": bool(res_384.get("is_valid_deal")),
            "verdict_768": bool(res_768.get("is_valid_deal")),
        })

    return {
        "dry_run": False,
        "model": eff_model,
        "kits_dir": kits_dir,
        "max_photos": max_photos,
        "photos_found": len(photos),
        "records": records,
        "summary": evaluate_ab_results(records),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A/B controlado 384px vs 768px en Gemini Vision")
    parser.add_argument("--kits-dir", default="kits", help="Directorio con fotos de kits")
    parser.add_argument("--limit", type=int, default=20, help="Máximo de fotos a comparar")
    parser.add_argument("--model", default=None, help="Modelo Gemini (por defecto AI_MODEL)")
    parser.add_argument("--dry-run", action="store_true", help="Simular sin llamadas de red (por defecto)")
    parser.add_argument("--live", action="store_true", help="Ejecutar llamadas reales a Gemini API")
    args = parser.parse_args(argv)
    live = bool(args.live and not args.dry_run)
    result = run_ab(kits_dir=args.kits_dir, max_photos=args.limit, live=live, model=args.model)
    print(format_ab_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
