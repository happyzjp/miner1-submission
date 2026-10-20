"""Sample method generator (development guide section 18.2).

Usage: python generator.py --request /in/request.json --output /out/

Reads the public curriculum request and writes ``problem_specs.jsonl``: one JSON object per line with template parameters.
The validators' trusted auditor renders the parameters into training material; this program only chooses *which* problems
to teach. It must be fully deterministic for one request (the same ``gen_seed`` must always give the same output), must
not touch the network, and must not read anything outside the request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

MAX_ROWS = 1000
FAMILIES_PER_TEMPLATE = 12
WORDS = ["river", "bridge", "garden", "market", "window", "planet", "forest", "school", "island", "castle"]


def make_rng(request: dict) -> random.Random:
    """Seed a private PRNG from the request. Never use Python's ``hash()`` (randomised per process) or global ``random``."""
    return random.Random(int(request_digest(request)[:16], 16))


def request_digest(request: dict) -> str:
    material = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_request(request: object) -> dict:
    """Keep the public sample standalone: no node or training packages are needed to execute it."""
    if not isinstance(request, dict) or request.get("protocol") != "coordinated-a-v1":
        raise ValueError("request must be an object with protocol coordinated-a-v1")
    if not isinstance(request.get("task_id"), str) or not request["task_id"]:
        raise ValueError("task_id must be a non-empty string")
    if request.get("stage") not in ("FOUNDATION", "TRANSFER", "GROWTH"):
        raise ValueError("stage must be FOUNDATION, TRANSFER or GROWTH")
    if type(request.get("gen_seed")) is not int or not 0 <= request["gen_seed"] < 2**64:
        raise ValueError("gen_seed must be an unsigned 64-bit integer")
    if type(request.get("sample_budget")) is not int or request["sample_budget"] <= 0:
        raise ValueError("sample_budget must be a positive integer")
    unit = request.get("curriculum_unit")
    if not isinstance(unit, dict) or any(not isinstance(unit.get(k), str) or not 1 <= len(unit[k]) <= 80
                                         for k in ("subject", "language", "age_group", "modality")):
        raise ValueError("curriculum_unit must contain subject, language, age_group and modality strings of 1-80 characters")
    if unit["modality"] not in {"text", "image"}:
        raise ValueError("modality must be text or image")
    sources = request.get("source_ids")
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or not 1 <= len(s) <= 80 for s in sources):
        raise ValueError("source_ids must contain at least one source string of 1-80 characters")
    if not isinstance(request.get("public_diagnostics", {}), dict):
        raise ValueError("public_diagnostics must be an object")
    return request


def choose_template(unit: dict) -> str:
    """Pick the template that fits the unit's modality and subject."""
    if unit["modality"] == "image":
        return "image_caption_spec"
    return {"math": "arith_expression", "chinese": "reading_passage", "english": "vocab_cloze"}.get(unit["subject"], "proof_step")


def build_params(template: str, rng: random.Random, stage: str) -> dict:
    """Parameters follow the trusted template schemas exactly (ints, short strings, small lists); the validators render them.

    Keep the parameter space large (the auditor rejects a whole stage when more than 5% of the rendered rows are duplicates)
    and image sizes modest (rendered pixels count against the stage pixel budget)."""
    level = {"FOUNDATION": 1, "TRANSFER": 2, "GROWTH": 3}[stage]
    if template == "arith_expression":
        hi = min(9999, 10 ** (level + 1) * 9)  # the template allows |a|, |b| <= 9999
        return {"a": rng.randint(1, hi), "b": rng.randint(1, hi), "op": rng.choice(["+", "-", "*"])}
    if template == "vocab_cloze":
        return {"word": f"{rng.choice(WORDS)}{rng.randint(0, 9999)}", "distractor_seed": rng.randint(0, 2**31)}
    if template == "reading_passage":
        return {"topic": rng.choice(WORDS), "passage_seed": rng.randint(0, 2**31), "question_kind": rng.choice(["word", "detail", "main"])}
    if template == "image_caption_spec":
        return {"width": rng.randint(32, 160), "height": rng.randint(32, 160), "objects": [rng.choice(WORDS) for _ in range(rng.randint(1, 3))],
                "style": rng.choice(["plain", "detailed"])}
    return {"premise_a": f"P{rng.randint(0, 10**6)}", "premise_b": f"Q{rng.randint(0, 10**6)}",
            "rule": rng.choice(["modus_ponens", "transitivity", "substitution", "symmetry", "addition"])}


def generate(request: dict) -> list[dict]:
    request = validate_request(request)
    unit = request["curriculum_unit"]
    rng = make_rng(request)
    template = choose_template(unit)
    count = min(int(request["sample_budget"]), MAX_ROWS)
    prefix = f"{request['stage'].lower()}-{request_digest(request)[:20]}"
    rows = []
    seen_params: set[str] = set()
    for i in range(count):
        for _ in range(100):
            params = build_params(template, rng, request["stage"])
            signature = json.dumps(params, sort_keys=True, separators=(",", ":"))
            if signature not in seen_params:
                seen_params.add(signature)
                break
        else:
            raise ValueError("parameter space exhausted; reduce sample_budget or expand the generator")
        rows.append({
            "problem_id": f"{prefix}-{i:06d}",
            "family_id": f"{template}-f{i % FAMILIES_PER_TEMPLATE:02d}",
            "template_id": template,
            "params": params,
            "source_id": request["source_ids"][i % len(request["source_ids"])],
            "language": unit["language"],
            "age_group": unit["age_group"],
            "modality": unit["modality"],
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        rows = generate(request)
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        out = output / "problem_specs.jsonl"
        with out.open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"generator error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
