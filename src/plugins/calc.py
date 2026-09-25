"""A small calculator that only evaluates arithmetic."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable

from src.plugins.common import aliases, tr
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

_CMD = ("احسب", "calc")
_BIN: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}
_UNARY: dict[type, Callable[[float], float]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class CalcError(Exception):
    pass


def _number(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        raise CalcError("bad")
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, int) and abs(node.value) > 10**12:
            raise CalcError("big")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_number(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left = _number(node.left)
        right = _number(node.right)
        if type(node.op) is ast.Pow and (abs(right) > 8 or abs(left) > 10**6):
            raise CalcError("big")
        try:
            value = _BIN[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise CalcError("zero") from exc
        if isinstance(value, float) and not math.isfinite(value):
            raise CalcError("big")
        if abs(value) > 10**12:
            raise CalcError("big")
        return value
    raise CalcError("bad")


def safe_calc(expression: str) -> str:
    cleaned = expression.strip()
    if not cleaned or len(cleaned) > 100:
        raise CalcError("bad")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise CalcError("bad") from exc
    value = _number(tree.body)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


class CalcPlugin(Plugin):
    meta = PluginMeta(
        name="calc",
        description_en="Calculate an arithmetic expression.",
        description_ar="حساب تعبير حسابي.",
        commands=(
            *aliases(
                "Calculate numbers with + - * / % and parentheses.",
                "حساب أرقام مع + - * / % والأقواس.",
                *_CMD,
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        expression = (ctx.args or "").strip()
        if not expression:
            await ctx.reply(tr(ctx, "Send an expression.", "أرسل تعبيراً."))
            return
        try:
            result = safe_calc(expression)
        except CalcError as exc:
            if str(exc) == "zero":
                await ctx.reply(tr(ctx, "Division by zero.", "القسمة على صفر."))
            else:
                await ctx.reply(
                    tr(ctx, "That expression is not allowed.", "هذا التعبير غير مسموح.")
                )
            return
        await ctx.reply(result)


plugin = CalcPlugin()
