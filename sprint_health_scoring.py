import ast
import json

import requests

import sprint_health_config as config


_SAFE_FORMULA_FUNCS = {
    "abs": abs,
    "max": max,
    "min": min,
    "round": round,
}


class _SafeFormulaEvaluator(ast.NodeVisitor):
    def __init__(self, variables: dict[str, float]):
        self.variables = variables

    def visit_Expression(self, node: ast.Expression):
        return self.visit(node.body)

    def visit_Name(self, node: ast.Name):
        if node.id not in self.variables:
            raise ValueError(f"Unknown formula variable: {node.id}")
        return self.variables[node.id]

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Formula supports numbers only.")

    def visit_UnaryOp(self, node: ast.UnaryOp):
        value = self.visit(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.USub):
            return -value
        raise ValueError("Unsupported unary operator in formula.")

    def visit_BinOp(self, node: ast.BinOp):
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise ValueError("Unsupported operator in formula.")

    def visit_Call(self, node: ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Unsupported function call in formula.")
        func = _SAFE_FORMULA_FUNCS.get(node.func.id)
        if func is None:
            raise ValueError(f"Unsupported formula function: {node.func.id}")
        return func(*[self.visit(arg) for arg in node.args])

    def generic_visit(self, node):
        raise ValueError(f"Unsupported formula syntax: {type(node).__name__}")


def _progress_weight(sprint_pct: float | None) -> float:
    if sprint_pct is None:
        return 1.0
    if sprint_pct < 30:
        return sprint_pct / 30
    if sprint_pct < 60:
        return 0.5 + (sprint_pct - 30) / 60
    return 1.0


def _blend(real_score: int, sprint_pct: float | None, neutral: int = 70) -> int:
    return round(neutral + _progress_weight(sprint_pct) * (real_score - neutral))


def _safe_eval_formula(expression: str, variables: dict[str, float]) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
        result = _SafeFormulaEvaluator(variables).visit(tree)
    except Exception as exc:
        raise ValueError(f"Invalid custom formula: {exc}") from exc
    return float(result)


def _build_formula_context(c_score, co_score, cy_score, b_score, bd_nudge: int = 0) -> dict[str, float]:
    weights = config._config_weights()
    return {
        "commitment": float(c_score),
        "carryover": float(co_score),
        "cycle_time": float(cy_score),
        "bug_ratio": float(b_score),
        "burndown": float(bd_nudge),
        "weight_commitment": float(weights["commitment"]),
        "weight_carryover": float(weights["carryover"]),
        "weight_cycle_time": float(weights["cycle_time"]),
        "weight_bug_ratio": float(weights["bug_ratio"]),
        "weighted_commitment": float(c_score * weights["commitment"]),
        "weighted_carryover": float(co_score * weights["carryover"]),
        "weighted_cycle_time": float(cy_score * weights["cycle_time"]),
        "weighted_bug_ratio": float(b_score * weights["bug_ratio"]),
    }


def score_commitment(
    completed: int,
    committed: int,
    sprint_pct: float | None = None,
    is_extended: bool = False,
) -> tuple[int, float]:
    points = config._config_points()
    cfg = config.METRICS_CONFIG["commitment"]
    if committed == 0:
        return points["neutral"], 0.0
    pct = completed / committed * 100
    if cfg["ideal_min_pct"] <= pct <= cfg["ideal_max_pct"]:
        raw = points["excellent"]
    elif pct >= cfg["good_min_pct"]:
        raw = points["good"]
    elif pct >= cfg["warning_min_pct"]:
        raw = points["warning"]
    else:
        raw = points["poor"]
    score = _blend(raw, sprint_pct, points["neutral"])
    if is_extended:
        score = min(score, int(cfg["extended_cap_score"]))
    return score, round(pct, 1)


def score_carryover(
    carried: int,
    total: int,
    sprint_pct: float | None = None,
    is_extended: bool = False,
) -> tuple[int, float]:
    points = config._config_points()
    cfg = config.METRICS_CONFIG["carryover"]
    if total == 0:
        return points["neutral"], 0.0
    pct = carried / total * 100
    if pct < cfg["excellent_lt_pct"]:
        raw = points["excellent"]
    elif pct <= cfg["good_lte_pct"]:
        raw = points["good"]
    elif pct <= cfg["warning_lte_pct"]:
        raw = points["warning"]
    else:
        raw = points["poor"]
    score = _blend(raw, sprint_pct, points["neutral"])
    if is_extended:
        score = max(0, score - int(cfg["extended_penalty"]))
    return score, round(pct, 1)


def score_cycle_time(
    current_avg: float | None,
    prev_avg: float | None,
    sprint_pct: float | None = None,
) -> tuple[int, float | None]:
    points = config._config_points()
    cfg = config.METRICS_CONFIG["cycle_time"]
    if current_avg is None or prev_avg is None or prev_avg == 0:
        return points["neutral"], None
    diff_pct = (current_avg - prev_avg) / prev_avg * 100
    if abs(diff_pct) <= cfg["stable_abs_pct"]:
        raw = points["excellent"]
    elif diff_pct <= cfg["good_increase_pct"]:
        raw = points["good"]
    elif diff_pct <= cfg["warning_increase_pct"]:
        raw = points["warning"]
    else:
        raw = points["poor"]
    return _blend(raw, sprint_pct, points["neutral"]), round(diff_pct, 1)


def score_bug_ratio(
    new_bugs: int,
    total: int,
    sprint_pct: float | None = None,
) -> tuple[int, float]:
    points = config._config_points()
    cfg = config.METRICS_CONFIG["bug_ratio"]
    if total == 0 and new_bugs == 0:
        return points["neutral"], 0.0
    denom = total if total > 0 else 1
    pct = new_bugs / denom * 100
    if pct < cfg["excellent_lt_pct"]:
        raw = points["excellent"]
    elif pct <= cfg["good_lte_pct"]:
        raw = points["good"]
    elif pct <= cfg["warning_lte_pct"]:
        raw = points["warning"]
    else:
        raw = points["poor"]
    return _blend(raw, sprint_pct, points["neutral"]), round(pct, 1)


def score_burndown(bd: dict, sprint_pct: float | None) -> int:
    cfg = config.METRICS_CONFIG["burndown"]
    if not bd:
        return 0
    if bd.get("current_remaining", 0) == 0:
        return int(cfg["done_bonus"])
    if bd.get("on_track"):
        return int(cfg["on_track_bonus"])
    behind = bd.get("behind_by", 0)
    if behind <= cfg["behind_small_max"]:
        return 0
    if behind <= cfg["behind_medium_max"]:
        return int(cfg["behind_medium_penalty"])
    return int(cfg["behind_large_penalty"])


def calc_health_score(c_score, co_score, cy_score, b_score, bd_nudge: int = 0) -> dict:
    cfg = config._config_final_score()
    formula = (cfg.get("custom_formula") or "").strip() or config.DEFAULT_METRICS_CONFIG["final_score"]["custom_formula"]
    context = _build_formula_context(c_score, co_score, cy_score, b_score, bd_nudge)
    raw_value = _safe_eval_formula(formula, context)
    bounded = max(float(cfg.get("min_score", 0)), min(float(cfg.get("max_score", 100)), raw_value))
    final_score = round(bounded) if cfg.get("round_result", True) else bounded
    return {
        "score": int(round(final_score)),
        "raw_score": raw_value,
        "formula": formula,
        "context": context,
        "weighted_breakdown": {
            "commitment": round(context["weighted_commitment"], 1),
            "carryover": round(context["weighted_carryover"], 1),
            "cycle_time": round(context["weighted_cycle_time"], 1),
            "bug_ratio": round(context["weighted_bug_ratio"], 1),
        },
    }


def _extract_response_text(payload: dict) -> str:
    output_text = (payload.get("output_text") or "").strip()
    if output_text:
        return output_text
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"]).strip()
    return ""


def generate_ai_insights(report: dict) -> dict | None:
    cfg = config._config_ai()
    if not cfg.get("enabled"):
        return None
    if not config.OPENAI_API_KEY:
        return {
            "status": "disabled",
            "title": "AI insights unavailable",
            "summary": "Set OPENAI_API_KEY in .env to enable AI recommendations.",
            "actions": [],
        }

    payload = {
        "model": (cfg.get("model") or config.OPENAI_MODEL).strip() or config.OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You analyze sprint health reports. Reply in JSON only with keys "
                            "title, summary, actions. actions must be an array of up to 3 short strings."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(report, ensure_ascii=False),
                    }
                ],
            },
        ],
        "max_output_tokens": int(cfg.get("max_output_tokens", 350)),
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=config.OPENAI_TIMEOUT,
        )
        response.raise_for_status()
        parsed = json.loads(_extract_response_text(response.json()))
        actions = parsed.get("actions") if isinstance(parsed.get("actions"), list) else []
        return {
            "status": "ok",
            "title": str(parsed.get("title") or "AI insight").strip(),
            "summary": str(parsed.get("summary") or "").strip(),
            "actions": [str(item).strip() for item in actions if str(item).strip()][:3],
        }
    except Exception as exc:
        return {
            "status": "error",
            "title": "AI insight failed",
            "summary": f"AI request failed: {exc}",
            "actions": [],
        }


def health_label(score: int) -> tuple[str, str]:
    labels = config.METRICS_CONFIG["labels"]
    if score >= labels["green_min_score"]:
        return ":green_circle:", "Predictable sprint"
    if score >= labels["yellow_min_score"]:
        return ":yellow_circle:", "Some instability"
    if score >= labels["orange_min_score"]:
        return ":orange_circle:", "Execution issues"
    return ":red_circle:", "Sprint breakdown"
