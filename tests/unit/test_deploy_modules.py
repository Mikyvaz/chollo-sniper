"""Cada módulo local importado por el código de producción debe llegar a los contenedores.

Los servicios montan los .py como volúmenes (docker-compose.yml) y el Dockerfile los copia. Un módulo
nuevo (p. ej. text_rules.py) que se importe pero no esté en ambos hace caer el servicio con
ModuleNotFoundError al reiniciar.
"""
import ast
import os
import re
import unittest

import _harness  # noqa: F401  (debe ir primero)

ROOT = _harness._ROOT


def _root_modules():
    return {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def _closure(entry, local):
    seen, todo = set(), [entry]
    while todo:
        m = todo.pop()
        if m not in seen:
            seen.add(m)
            todo.extend(_local_imports(m, local))
    return seen


def _local_imports(mod, local):
    tree = ast.parse(_read(mod + ".py"))
    found = set()
    for node in ast.walk(tree):  # incluye imports dentro de funciones
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found & local - {mod}


def _compose_services():
    services, cur, in_services = {}, None, False
    for line in _read("docker-compose.yml").splitlines():
        if re.match(r"^services:", line):
            in_services = True
        elif in_services and re.match(r"^\S", line):
            in_services = False
        m = re.match(r"^  ([\w-]+):\s*$", line)
        if in_services and m:
            cur = services.setdefault(m.group(1), set())
        c = re.match(r"^\s+command:.*?([\w]+)\.py", line)
        if cur is not None and c:
            cur.add("@" + c.group(1))
        v = re.match(r"^\s+-\s+\./([\w]+)\.py:/app/\1\.py(?::ro)?\s*$", line)
        if cur is not None and v:
            cur.add(v.group(1))
    return services


def _dockerfile_copied():
    copied = set()
    for line in _read("Dockerfile").splitlines():
        if line.startswith("COPY "):
            copied |= {t[:-3] for t in line.split()[1:-1] if t.endswith(".py")}
    return copied


class TestModulosDesplegados(unittest.TestCase):
    def test_compose_monta_dependencias_locales(self):
        local = _root_modules()
        services = _compose_services()
        self.assertGreaterEqual(len(services), 5)
        for name, items in services.items():
            mounted = {i for i in items if not i.startswith("@")}
            entries = [i[1:] for i in items if i.startswith("@")]
            self.assertTrue(entries, f"{name}: sin command con .py")
            for entry in entries:
                missing = _closure(entry, local) - mounted
                with self.subTest(servicio=name, entrada=entry):
                    self.assertFalse(missing, f"{name}: {entry}.py necesita {sorted(missing)} sin montarlo en docker-compose.yml")

    def test_dockerfile_copia_dependencias_locales(self):
        local = _root_modules()
        copied = _dockerfile_copied()
        for mod in copied:
            missing = _local_imports(mod, local) - copied
            with self.subTest(modulo=mod):
                self.assertFalse(missing, f"{mod}.py importa {sorted(missing)} que el Dockerfile no copia")

    def test_text_rules_desplegado(self):
        self.assertIn("text_rules", _dockerfile_copied())
        for name, items in _compose_services().items():
            self.assertIn("text_rules", items, name)


if __name__ == "__main__":
    unittest.main()
