"""Data-only plugin control contracts. No imports, activation or service changes."""
import ast
import hashlib
import json
import math
import re
import os
import stat
from pathlib import Path

LIMIT = 65536
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$')
HOOKS = {'on_loaded': 1, 'on_ready': 2, 'on_unload': 2,
         'on_wifi_update': 3, 'on_handshake': 5, 'on_ui_setup': 2,
         'on_ui_update': 2, 'on_internet_available': 2}


def read_data(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('expected regular file')
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('data exceeds 64 KiB limit')
    return raw


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_options(schema, values):
    if not isinstance(values, dict) or len(values) > 128:
        raise ValueError('configuration must be an object with at most 128 options')
    for key, value in values.items():
        if key not in schema:
            raise ValueError('unknown configuration option')
        spec = schema[key]
        kind = spec.get('type')
        valid = {'boolean': type(value) is bool, 'string': isinstance(value, str),
                 'number': type(value) in (int, float), 'enum': isinstance(value, str),
                 'path': isinstance(value, str)}.get(kind, False)
        if not valid:
            raise ValueError('configuration type mismatch')
        if isinstance(value, str) and (len(value) > spec.get('max_length', 1024) or '\x00' in value):
            raise ValueError('configuration string exceeds bounds')
        if kind == 'number' and (not math.isfinite(value) or not spec.get('min', -1e12) <= value <= spec.get('max', 1e12)):
            raise ValueError('configuration number outside bounds')
        if kind == 'enum' and value not in spec.get('choices', []):
            raise ValueError('configuration enum outside choices')
        if kind == 'path':
            base = Path(spec.get('root', '/captures/plugins')).resolve()
            target = Path(value)
            if not target.is_absolute() or not target.resolve().is_relative_to(base) or '..' in target.parts:
                raise ValueError('configuration path outside allowed root')
    return values


def compatibility(pack):
    """Versioned static contract, never a claim that arbitrary code is safe."""
    if pack.adapter == 'redux':
        return {'state': 'unverified', 'contract': 1, 'reason': 'Native manifest; activation not verified'}
    if pack.adapter != 'pwnagotchi-shim-v1':
        return {'state': 'incompatible', 'contract': 1, 'reason': 'Unsupported adapter contract'}
    try:
        path = Path(pack.root) / pack.entrypoint
        if not pack.entrypoint or path.is_symlink() or not path.resolve().is_relative_to(Path(pack.root).resolve()):
            raise ValueError('unsafe entrypoint')
        raw = read_data(path)
        if hashlib.sha256(raw).hexdigest() != pack.sha256:
            raise ValueError('adapter source digest mismatch')
        tree = ast.parse(raw)
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        for node in imports:
            modules = [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
            if any(module not in ('redux.compat', 'logging', 'math', 'time') for module in modules):
                raise ValueError('imports require explicit adaptation review')
        plugin_import = any(isinstance(node, ast.ImportFrom) and node.module == 'redux.compat'
                            and any(alias.name == 'Plugin' and alias.asname is None for alias in node.names) for node in imports)
        plugin_class = any(isinstance(node, ast.ClassDef) and any(isinstance(base, ast.Name) and base.id == 'Plugin' for base in node.bases) for node in tree.body)
        if not plugin_import or not plugin_class:
            raise ValueError('explicit Redux shim base class required')
        hooks = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('on_')]
        for node in hooks:
            if (node.name not in HOOKS or isinstance(node, ast.AsyncFunctionDef)
                    or len(node.args.args) != HOOKS[node.name] or node.args.vararg or node.args.kwarg or node.args.kwonlyargs):
                raise ValueError('unsupported hook or signature')
        # Calls into arbitrary agent/UI APIs need explicit adaptation review.
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ('agent', 'ui'):
                if node.value.id == 'agent' or node.attr not in ('set', 'get', 'add_element', 'remove_element'):
                    raise ValueError('unsupported agent/UI API')
        return {'state': 'adapted', 'contract': 1, 'reason': 'Digest-pinned static shim hooks; execution and hardware unverified'}
    except (OSError, ValueError, SyntaxError, RecursionError):
        return {'state': 'incompatible', 'contract': 1, 'reason': 'Adapter digest, hooks or API contract failed'}
