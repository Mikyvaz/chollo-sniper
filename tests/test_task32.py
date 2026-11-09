import ast
import os
import sys

print("=== Running Task 3.2 Empirical Tests (AST Inspection) ===")

bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")
with open(bot_path, "r", encoding="utf-8") as f:
    source = f.read()

tree = ast.parse(source, filename="bot.py")

TARGET_FUNCTIONS = {"call_ai_copywriter", "call_ai_appraiser", "fetch_cex_live"}

class AsyncCallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.current_async_func = None
        self.blocking_direct_calls = []
        self.threaded_calls = []

    def visit_AsyncFunctionDef(self, node):
        old_func = self.current_async_func
        self.current_async_func = node.name
        self.generic_visit(node)
        self.current_async_func = old_func

    def visit_FunctionDef(self, node):
        # Regular sync functions are not part of discord event loop blocking check
        old_func = self.current_async_func
        self.current_async_func = None
        self.generic_visit(node)
        self.current_async_func = old_func

    def visit_Call(self, node):
        if self.current_async_func:
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            # Check if calling targeted blocking functions directly
            if func_name in TARGET_FUNCTIONS:
                self.blocking_direct_calls.append((self.current_async_func, func_name, node.lineno))

            # Check if calling asyncio.to_thread with targeted function
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "to_thread"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
            ):
                if node.args:
                    target_arg = node.args[0]
                    target_name = None
                    if isinstance(target_arg, ast.Name):
                        target_name = target_arg.id
                    elif isinstance(target_arg, ast.Attribute):
                        target_name = target_arg.attr

                    if target_name in TARGET_FUNCTIONS:
                        self.threaded_calls.append((self.current_async_func, target_name, node.lineno))

        self.generic_visit(node)

# To check if asyncio.to_thread is properly awaited, we also verify parent AST nodes
class AwaitCheckVisitor(ast.NodeVisitor):
    def __init__(self):
        self.awaits_count = 0

    def visit_Await(self, node):
        if isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "to_thread"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "asyncio"
                and call.args
            ):
                target = getattr(call.args[0], "id", None) or getattr(call.args[0], "attr", None)
                if target in TARGET_FUNCTIONS:
                    self.awaits_count += 1
        self.generic_visit(node)

visitor = AsyncCallVisitor()
visitor.visit(tree)

await_visitor = AwaitCheckVisitor()
await_visitor.visit(tree)

print(f"  [OK] Blocking direct calls in async def: {visitor.blocking_direct_calls}")
assert len(visitor.blocking_direct_calls) == 0, f"Found unthreaded blocking calls: {visitor.blocking_direct_calls}"

print(f"  [OK] Threaded calls in async def: {visitor.threaded_calls}")
assert len(visitor.threaded_calls) == 4, f"Expected 4 threaded calls, got {len(visitor.threaded_calls)}"

# Verify presence in required functions
funcs_calling = {t[0]: t[1] for t in visitor.threaded_calls}
assert "finalize_listing" in funcs_calling, "finalize_listing must call call_ai_copywriter via to_thread"
assert "slash_tasar" in [t[0] for t in visitor.threaded_calls], "slash_tasar must use to_thread"
assert "on_interaction" in [t[0] for t in visitor.threaded_calls], "on_interaction must use to_thread"

print(f"  [OK] Confirmed {await_visitor.awaits_count} calls are properly wrapped with 'await asyncio.to_thread'")
assert await_visitor.awaits_count == 4, f"Expected 4 awaited to_thread calls, found {await_visitor.awaits_count}"

print("\n🎉 ALL Task 3.2 tests PASSED successfully!")

