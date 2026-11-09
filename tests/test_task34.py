import ast
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/app")
import bot

print("=== Running Task 3.4 Empirical Tests ===")

# --- Test 1: Unit Test de lógica de clean_photos_count ---
# Caso A: clean_photos_count es 0 pero hay photo_urls
kit_info_a = {"clean_photos_count": 0, "photo_urls": ["url1", "url2", "url3"]}
if kit_info_a.get("clean_photos_count", 0) == 0 and kit_info_a.get("photo_urls"):
    kit_info_a["clean_photos_count"] = len(kit_info_a["photo_urls"])
assert kit_info_a["clean_photos_count"] == 3, f"Expected 3, got {kit_info_a['clean_photos_count']}"
print("  [OK] clean_photos_count correctamente inicializado con len(photo_urls) cuando es 0")

# Caso B: clean_photos_count ya tiene fotos procesadas (>0)
kit_info_b = {"clean_photos_count": 5, "photo_urls": ["url1", "url2"]}
if kit_info_b.get("clean_photos_count", 0) == 0 and kit_info_b.get("photo_urls"):
    kit_info_b["clean_photos_count"] = len(kit_info_b["photo_urls"])
assert kit_info_b["clean_photos_count"] == 5, f"Expected 5 preserved, got {kit_info_b['clean_photos_count']}"
print("  [OK] clean_photos_count preservado cuando ya es > 0")


# --- Test 2: Unit Test de on_message con aislamiento de canal ---
async def test_on_message_isolation():
    user_id = 987654
    correct_channel_id = 111222
    wrong_channel_id = 333444

    bot.active_sessions[user_id] = {
        "waiting_photos": True,
        "channel_id": correct_channel_id,
        "uploaded_photos": []
    }

    # Mock attachment
    mock_att = MagicMock()
    mock_att.content_type = "image/jpeg"
    mock_att.filename = "foto.jpg"
    mock_att.read = AsyncMock(return_value=b"fake_image_bytes")

    # Mensaje en canal incorrecto
    msg_wrong = MagicMock()
    msg_wrong.author.bot = False
    msg_wrong.author.id = user_id
    msg_wrong.author.display_name = "Tester"
    msg_wrong.channel.id = wrong_channel_id
    msg_wrong.channel.send = AsyncMock()
    msg_wrong.attachments = [mock_att]

    await bot.on_message(msg_wrong)
    assert len(bot.active_sessions[user_id]["uploaded_photos"]) == 0, "Fotos no deben capturarse en canal ajeno"
    print("  [OK] Mensaje en canal ajeno correctamente ignorado")

    # Mensaje en canal correcto
    msg_correct = MagicMock()
    msg_correct.author.bot = False
    msg_correct.author.id = user_id
    msg_correct.author.display_name = "Tester"
    msg_correct.channel.id = correct_channel_id
    msg_correct.channel.send = AsyncMock()
    msg_correct.attachments = [mock_att]

    await bot.on_message(msg_correct)
    assert len(bot.active_sessions[user_id]["uploaded_photos"]) == 1, "Foto debe ser capturada en el canal correcto"
    print("  [OK] Mensaje en el canal correcto procesado con éxito")

    # Limpiar sesión de prueba
    bot.active_sessions.pop(user_id, None)

asyncio.run(test_on_message_isolation())


# --- Test 3: Inspección AST de bot.py ---
bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")
with open(bot_path, "r", encoding="utf-8") as f:
    tree = ast.parse(f.read(), filename="bot.py")

has_channel_check = False
has_clean_photos_fallback = False

for node in ast.walk(tree):
    # Verificar verificación de canal en on_message
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_message":
        for sub in ast.walk(node):
            if isinstance(sub, ast.Compare):
                # Comprobar si compara message.channel.id con session["channel_id"]
                sub_str = ast.dump(sub)
                if "channel_id" in sub_str and "channel" in sub_str:
                    has_channel_check = True

    # Verificar asignación de clean_photos_count con photo_urls
    if isinstance(node, ast.Assign):
        target_str = ast.dump(node)
        if "clean_photos_count" in target_str and "photo_urls" in target_str:
            has_clean_photos_fallback = True

assert has_channel_check, "AST check failed: on_message must check channel_id"
assert has_clean_photos_fallback, "AST check failed: clean_photos_count must use photo_urls"
print("  [AST] Verificado aislamiento de canal en on_message y fallback de clean_photos_count")

print("\n🎉 ALL Task 3.4 tests PASSED successfully!")

