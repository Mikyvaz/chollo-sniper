#!/usr/bin/env python3
"""
Test de Verificación Oficial para BLOQUE 5:
- 5.1: Pipeline de imágenes de bajo consumo (RAM -> JPEG <=640x640 Q=78, <60KB, mime_type="image/jpeg").
- 5.2: Enrutamiento inteligente de modelos (Puerta 0.5 a coste 0€, Puerta 1 Lite con OpenRouter fallback, Puerta 2 Flash vs Flash-Lite).
"""

import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image

from core_engine import (
    CompressedImageBytes,
    compress_image_to_jpeg_640,
    download_item_images,
    prepare_image_for_ai,
    select_multimodal_model
)
from fraud_signals import evaluate_fraud_risk


class TestBloque5(unittest.TestCase):

    def test_5_1_image_compression_and_specs(self):
        """Verifica que una imagen de 1200x1200 se comprime en RAM a <=640x640 JPEG Q=78 y <60KB."""
        # 1. Crear imagen original grande de 1200x1200 con contenido realista (gradiente/patrón)
        raw_img = Image.new("RGB", (1200, 1200))
        # Rellenar con degradado suave simulando una foto real
        pixels = []
        for y in range(1200):
            row_val = int(y / 6)
            for x in range(1200):
                pixels.append((int(x / 6), row_val, int((x + y) / 12)))
        raw_img.putdata(pixels)

        buf = io.BytesIO()
        raw_img.save(buf, format="JPEG", quality=95)
        original_bytes = buf.getvalue()

        self.assertGreater(len(original_bytes), 60 * 1024, "La imagen original debe pesar >60KB")

        # 2. Comprimir en memoria
        compressed = compress_image_to_jpeg_640(original_bytes, max_dim=640, quality=78)

        # 3. Validar especificaciones
        self.assertIsInstance(compressed, bytes, "Debe ser instancia de bytes")
        self.assertIsInstance(compressed, CompressedImageBytes, "Debe ser CompressedImageBytes")
        self.assertLess(len(compressed), 60 * 1024, f"Debe pesar <60KB, pesa {len(compressed)/1024:.1f}KB")
        self.assertEqual(compressed.mime_type, "image/jpeg")
        self.assertEqual(compressed["mime_type"], "image/jpeg")

        # 4. Verificar dimensiones reales y formato reabriendo con PIL
        with Image.open(io.BytesIO(compressed)) as reopened:
            self.assertEqual(reopened.format, "JPEG")
            self.assertLessEqual(reopened.width, 640)
            self.assertLessEqual(reopened.height, 640)

    def test_5_1_download_item_images_pipeline(self):
        """Verifica que download_item_images entrega las imágenes ya optimizadas en memoria."""
        raw_img = Image.new("RGBA", (1000, 1500), color=(10, 100, 200, 255))
        buf = io.BytesIO()
        raw_img.save(buf, format="PNG")
        mock_raw_bytes = buf.getvalue()

        with patch("core_engine._fetch_single_image", return_value=mock_raw_bytes):
            results = download_item_images(["https://images.example.com/item1.png"], max_images=1)
            self.assertEqual(len(results), 1)
            comp = results[0]
            self.assertIsInstance(comp, bytes)
            self.assertLess(len(comp), 60 * 1024)

            with Image.open(io.BytesIO(comp)) as reopened:
                self.assertEqual(reopened.format, "JPEG")
                self.assertLessEqual(max(reopened.width, reopened.height), 640)

    def test_5_1_compression_fallback_on_corrupted(self):
        """Si los bytes están corruptos, debe hacer fallback seguro sin excepción."""
        corrupted = b"NOT_A_VALID_IMAGE_BYTES_123456"
        result = compress_image_to_jpeg_640(corrupted)
        self.assertIsInstance(result, bytes)
        self.assertEqual(bytes(result), corrupted)

    def test_5_2_smart_model_routing(self):
        """Verifica la asignación económica de modelos (Lite vs Flash) según precio y lote (TASK-01 2026)."""
        # A) Producto simple < 120€ -> gemini-3.5-flash-lite
        g_model, o_model = select_multimodal_model(
            price=45.0,
            title="Disco SSD NVMe Samsung 980 Pro 2TB",
            desc="En perfecto estado, 100% de vida útil",
            target={"id": "nvme_ssd", "name": "NVMe SSD"}
        )
        self.assertEqual(g_model, "gemini-3.5-flash-lite")
        self.assertEqual(o_model, "google/gemini-flash-lite")

        # B) Artículo de ticket alto (>= 220€) -> gemini-3.8-flash
        g_model, o_model = select_multimodal_model(
            price=240.0,
            title="Tarjeta Gráfica RTX 3060 12GB",
            desc="Caja original y cables",
            target={"id": "gpu_rtx", "name": "GPU RTX"}
        )
        self.assertEqual(g_model, "gemini-3.8-flash")
        self.assertEqual(o_model, "google/gemini-flash")

        # C) Lote complejo con múltiples extras (>= 120€) -> gemini-3.8-flash
        g_model, o_model = select_multimodal_model(
            price=130.0,
            title="Lote con 4 juegos de Nintendo DS",
            desc="Incluye Mario Kart y Pokémon",
            target={"id": "games", "name": "Videojuegos"}
        )
        self.assertEqual(g_model, "gemini-3.8-flash")
        self.assertEqual(o_model, "google/gemini-flash")

        # D) Consola con pantalla (< 120€ sin lote) -> se mantiene en gemini-3.5-flash-lite (sin sobrecoste)
        g_model, o_model = select_multimodal_model(
            price=60.0,
            title="Consola Nintendo 3DS Azul",
            desc="Funciona pantalla y 3D",
            target={"id": "3ds_family", "name": "Familia 3DS"}
        )
        self.assertEqual(g_model, "gemini-3.5-flash-lite")
        self.assertEqual(o_model, "google/gemini-flash-lite")

        # E) Presupuesto degradado -> fuerza gemini-3.5-flash-lite incluso en ticket alto
        g_model, o_model = select_multimodal_model(
            price=200.0,
            title="Consola PS5 con mandos",
            desc="Pack completo",
            target={"id": "ps5", "name": "PS5"},
            budget_status="DEGRADED"
        )
        self.assertEqual(g_model, "gemini-3.5-flash-lite")
        self.assertEqual(o_model, "google/gemini-flash-lite")

    def test_5_2_puerta_0_5_deterministic_scam_abort(self):
        """Verifica que Puerta 0.5 entrega should_block=True en estafas para corte a coste 0.00$."""
        # Anuncio con número de teléfono camuflado
        fraud = evaluate_fraud_risk(
            title="Nintendo Switch OLED chollo",
            description="Escríbeme por wasap al 6 1 2 - 3 4 5 - 6 7 8 antes de comprar",
            price=60.0
        )
        self.assertTrue(fraud.get("should_block"), "Debe marcar should_block=True")
        self.assertTrue(fraud.get("is_fraud"), "Debe marcar is_fraud=True")
        self.assertGreaterEqual(fraud.get("risk", 0), 4, "Riesgo debe ser >= 4")


if __name__ == "__main__":
    unittest.main()
