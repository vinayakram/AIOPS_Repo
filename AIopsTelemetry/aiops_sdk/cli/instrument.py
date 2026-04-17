"""
CLI: aiops instrument <project_dir>

Scans Python files for LangGraph StateGraph usage and injects
AIopsCallbackHandler into .invoke() / .astream() / .stream() calls.

Usage:
    aiops instrument ./my_langgraph_app
    aiops instrument ./my_langgraph_app --dry-run
    aiops instrument ./my_langgraph_app --app-name my-agent
"""
import ast
import os
import sys
from pathlib import Path
from typing import Optional

import click


IMPORT_LINE = "from aiops_sdk import AIopsCallbackHandler\n"
HANDLER_EXPR = "AIopsCallbackHandler()"

# Call methods we recognise as LangGraph invocation points
INVOKE_METHODS = {"invoke", "astream", "stream", "ainvoke"}


@click.command("instrument")
@click.argument("project_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--app-name", default=None, help="Override AIOPS_APP_NAME in generated init code")
@click.option("--server-url", default="http://localhost:7000", show_default=True)
@click.option("--dry-run", is_flag=True, help="Print changes without writing files")
@click.option("--verbose", "-v", is_flag=True)
def instrument_cmd(project_dir: str, app_name: Optional[str], server_url: str,
                   dry_run: bool, verbose: bool):
    """Inject AIops telemetry into a LangGraph project."""
    root = Path(project_dir).resolve()
    py_files = list(root.rglob("*.py"))
    patched = []

    for py_file in py_files:
        try:
            result = _patch_file(py_file, dry_run=dry_run, verbose=verbose)
            if result:
                patched.append(py_file)
        except Exception as e:
            click.echo(f"  [skip] {py_file.relative_to(root)}: {e}", err=True)

    # Optionally create/update an aiops_init.py in project root
    _write_init_file(root, app_name, server_url, dry_run)

    if patched:
        click.echo(f"\n✓ Instrumented {len(patched)} file(s):")
        for f in patched:
            click.echo(f"  {f.relative_to(root)}")
    else:
        click.echo("No LangGraph invoke calls found — nothing to patch.")

    if dry_run:
        click.echo("\n[dry-run] No files were modified.")
    else:
        click.echo("\nDone. Add this to your app startup:")
        click.echo("  from aiops_init import setup_aiops; setup_aiops()")


# ── AST-based file patcher ────────────────────────────────────────────────────

def _patch_file(path: Path, dry_run: bool, verbose: bool) -> bool:
    """
    Returns True if the file was (or would be) patched.
    Strategy:
      1. Parse AST to find .invoke / .astream / .stream calls
      2. Check if a 'config' kwarg is already present
      3. If not, inject callbacks=[AIopsCallbackHandler()] into config
      4. Also add import if not present
    """
    source = path.read_text(encoding="utf-8")

    if "StateGraph" not in source and "CompiledGraph" not in source:
        if "invoke" not in source and "astream" not in source:
            return False

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    transformer = _CallbackInjector()
    new_tree = transformer.visit(tree)

    if not transformer.modified:
        return False

    # Add import if needed
    new_source = ast.unparse(new_tree)
    if "AIopsCallbackHandler" not in source:
        new_source = IMPORT_LINE + new_source

    if verbose or dry_run:
        click.echo(f"  patch: {path}")

    if not dry_run:
        path.write_text(new_source, encoding="utf-8")

    return True


class _CallbackInjector(ast.NodeTransformer):
    def __init__(self):
        self.modified = False

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)  # recurse first

        # Match <anything>.invoke(...) / .astream(...) / .stream(...)
        if not isinstance(node.func, ast.Attribute):
            return node
        if node.func.attr not in INVOKE_METHODS:
            return node

        # Already has callbacks in config?
        for kw in node.keywords:
            if kw.arg == "config":
                # config is present — try to inject callbacks into it
                node = _inject_into_config(node, kw)
                return node

        # No config kwarg — add config={"callbacks": [AIopsCallbackHandler()]}
        handler_call = ast.Call(
            func=ast.Name(id="AIopsCallbackHandler", ctx=ast.Load()),
            args=[], keywords=[]
        )
        config_dict = ast.Dict(
            keys=[ast.Constant(value="callbacks")],
            values=[ast.List(elts=[handler_call], ctx=ast.Load())],
        )
        node.keywords.append(ast.keyword(arg="config", value=config_dict))
        ast.fix_missing_locations(node)
        self.modified = True
        return node


def _inject_into_config(parent_call: ast.Call, config_kw: ast.keyword) -> ast.Call:
    """
    If config kwarg is a dict literal, add/extend callbacks key.
    Otherwise leave it untouched (too complex to analyse statically).
    """
    cfg = config_kw.value
    if not isinstance(cfg, ast.Dict):
        return parent_call  # complex config — skip

    for i, key in enumerate(cfg.keys):
        if isinstance(key, ast.Constant) and key.value == "callbacks":
            # callbacks key already present — we leave it alone
            return parent_call

    # Add callbacks key
    handler_call = ast.Call(
        func=ast.Name(id="AIopsCallbackHandler", ctx=ast.Load()),
        args=[], keywords=[]
    )
    cfg.keys.append(ast.Constant(value="callbacks"))
    cfg.values.append(ast.List(elts=[handler_call], ctx=ast.Load()))
    ast.fix_missing_locations(cfg)
    return parent_call


def _write_init_file(root: Path, app_name: Optional[str], server_url: str, dry_run: bool):
    """Create aiops_init.py in the project root for one-line startup configuration."""
    init_path = root / "aiops_init.py"
    app_name_str = f'"{app_name}"' if app_name else 'os.getenv("AIOPS_APP_NAME", "my-app")'
    content = f"""\
# Auto-generated by: aiops instrument
# Call setup_aiops() at your application startup.
import os
from aiops_sdk import AIopsClient, AIopsConfig


def setup_aiops():
    AIopsClient.configure(
        server_url=os.getenv("AIOPS_SERVER_URL", "{server_url}"),
        app_name={app_name_str},
        api_key=os.getenv("AIOPS_API_KEY"),
    )
"""
    if not dry_run:
        init_path.write_text(content, encoding="utf-8")
    click.echo(f"  {'[dry-run] would write' if dry_run else 'wrote'}: aiops_init.py")
