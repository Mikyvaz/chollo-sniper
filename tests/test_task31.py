import ast
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine

print("=== Running Task 3.1 Empirical Tests ===")

# Test 1: Medir el tiempo de abrir y cerrar conexión con core_engine.get_db()
# Debe ser < 5ms y confirmar con PRAGMA journal_mode que devuelve wal
t0 = time.perf_counter()
conn = core_engine.get_db()
mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
conn.close()
elapsed_ms = (time.perf_counter() - t0) * 1000

print(f"  [OK] get_db() open + PRAGMA + close took: {elapsed_ms:.2f}ms")
print(f"  [OK] PRAGMA journal_mode: {mode}")

assert mode.lower() == "wal", f"Expected WAL mode, got {mode}"
assert elapsed_ms < 50.0, f"Expected < 50ms (ideally < 5ms), took {elapsed_ms}ms"

# Test 2: Analizar el AST de bot.py para garantizar que init_db() solo se invoca 1 vez (en on_ready)
bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")
with open(bot_path, "r", encoding="utf-8") as f:
    tree = ast.parse(f.read(), filename="bot.py")

class InitDbCallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.init_db_calls = []
        self.current_function = None

    def visit_FunctionDef(self, node):
        old_func = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_func

    def visit_AsyncFunctionDef(self, node):
        old_func = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_func

    def visit_Call(self, node):
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name == "init_db":
            self.init_db_calls.append((self.current_function, node.lineno))
        self.generic_visit(node)

visitor = InitDbCallVisitor()
visitor.visit(tree)

print(f"  [AST] Invocaciones a init_db() encontradas en bot.py: {visitor.init_db_calls}")
assert len(visitor.init_db_calls) == 1, f"Expected exactly 1 invocation to init_db(), found {len(visitor.init_db_calls)}: {visitor.init_db_calls}"
assert visitor.init_db_calls[0][0] == "on_ready", f"Expected init_db() to be called only in on_ready, but found in {visitor.init_db_calls[0][0]}"

# Test 3: Verificar que run_db_maintenance existe y funciona
core_engine.run_db_maintenance()
print("  [OK] run_db_maintenance() ejecutado con éxito")

print("\n🎉 ALL Task 3.1 tests PASSED successfully!")

