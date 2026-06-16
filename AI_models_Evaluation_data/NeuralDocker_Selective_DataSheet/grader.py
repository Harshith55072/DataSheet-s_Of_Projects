#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║              LLM Benchmark Grader — Local Model Judge           ║
║                                                                  ║
║  Compares a model's answers against a reference answer key       ║
║  using your local GGUF model as the judge (no API needed).       ║
║                                                                  ║
║  Outputs a detailed .md report per model with:                   ║
║    • Overall accuracy                                            ║
║    • Per-section accuracy (Factual / Reasoning / Coding)         ║
║    • Per-question verdict + judge reasoning                      ║
║    • Stats useful for research papers                            ║
╚══════════════════════════════════════════════════════════════════╝

Usage:
    python grader.py

Edit the CONFIG block below before running.
"""

# ═══════════════════════════════════════════════════════════════
# CONFIG — edit this block
# ═══════════════════════════════════════════════════════════════

CONFIG = {
    # ── Judge model (your local GGUF) ────────────────────────────
    # This model will act as the grader/judge
    "judge_model_path": r"C:\Users\Lenovo\Documents\programing\NeuralDocker-Selective\models\qwen2.5-1.5b-instruct-q4_k_m.gguf",

    # Number of GPU layers for the judge model (-1 = all)
    "judge_gpu_layers": -1,

    # Context window for the judge
    "judge_n_ctx": 2048,

    # ── Input files ───────────────────────────────────────────────
    # The reference answer key
    "reference_path": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\answers.md",

    # The model's answer file to grade
    "model_answers_path": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\Final_data\gemma-2b-aps-it-Q4_K_M_ANSWERS.md",
    # ── Output ────────────────────────────────────────────────────
    # Folder where the report .md will be saved
    "output_dir": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\Final_data\Graded_answer\unsloth.Q4_K_M_GRADED_ANSWERS.md",
    # ── Grading behaviour ─────────────────────────────────────────
    # Save progress every N questions (resume on crash)
    "checkpoint_every": 10,

    # Set to None to grade all, or e.g. range(0, 100) for first section only
    "question_filter": None,

    # Prompt format for the judge model
    # Options: "chatml" | "alpaca" | "llama2" | "vicuna" | "plain"
    "judge_prompt_format": "chatml",
}

# ═══════════════════════════════════════════════════════════════
# (nothing below needs editing)
# ═══════════════════════════════════════════════════════════════

import re
import sys
import time
import json
from pathlib import Path
from datetime import datetime


# ─── Dependency check ────────────────────────────────────────────────────────

def check_deps():
    missing = []
    try:
        from llama_cpp import Llama  # noqa
    except ImportError:
        missing.append("llama-cpp-python")
    try:
        from tqdm import tqdm  # noqa
    except ImportError:
        missing.append("tqdm")
    if missing:
        print("❌ Missing packages. Install with:")
        for p in missing:
            print(f"  pip install {p}")
        sys.exit(1)

check_deps()

from llama_cpp import Llama
from tqdm import tqdm


# ─── Prompt formatter ────────────────────────────────────────────────────────

def format_prompt(user_msg: str, system_msg: str, fmt: str) -> str:
    if fmt == "chatml":
        return (
            f"<|im_start|>system\n{system_msg}<|im_end|>\n"
            f"<|im_start|>user\n{user_msg}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
    elif fmt == "alpaca":
        return f"### System:\n{system_msg}\n\n### Instruction:\n{user_msg}\n\n### Response:\n"
    elif fmt == "llama2":
        return f"[INST] <<SYS>>\n{system_msg}\n<</SYS>>\n\n{user_msg} [/INST]"
    elif fmt == "vicuna":
        return f"SYSTEM: {system_msg}\n\nUSER: {user_msg}\nASSISTANT:"
    elif fmt == "plain":
        return f"{system_msg}\n\n{user_msg}\n"
    else:
        raise ValueError(f"Unknown format: {fmt}")


# ─── Parsers ─────────────────────────────────────────────────────────────────

def parse_qa_file(filepath: str) -> dict[str, str]:
    """
    Parse any file with **QXXX.** answer blocks.
    Returns {qid: answer_text}
    """
    text = Path(filepath).read_text(encoding="utf-8")
    pattern = re.compile(
        r'\*\*(Q(\d{3}))\.\*\*\s*(.*?)(?=\*\*Q\d{3}\.\*\*|\Z)',
        re.DOTALL
    )
    result = {}
    for m in pattern.finditer(text):
        qid = m.group(1)
        body = m.group(3).strip()
        # Strip trailing section dividers
        body = re.sub(r'\n---.*$', '', body, flags=re.DOTALL).strip()
        result[qid] = body
    return result


def get_model_name_from_file(filepath: str) -> str:
    """Extract model name from the header of an answers .md file."""
    text = Path(filepath).read_text(encoding="utf-8")
    m = re.search(r'Benchmark Answers\s*[—-]\s*(.+)', text)
    if m:
        return m.group(1).strip()
    return Path(filepath).stem


def load_inference_metrics(model_answers_path: str) -> dict[str, dict]:
    """
    Load per-question timing/token metrics produced by benchmark.py.
    Returns {} gracefully if the companion .metrics.json file is missing.
    """
    metrics_path = Path(model_answers_path).with_suffix(".metrics.json")
    if not metrics_path.exists():
        return {}
    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
        return data.get("questions", {})
    except Exception:
        return {}


# ─── Section classifier ──────────────────────────────────────────────────────

def get_section(qid: str) -> str:
    num = int(qid[1:])
    if num <= 100:
        return "Factual"
    elif num <= 200:
        return "Reasoning"
    else:
        return "Coding"


# ─── Checkpoint ──────────────────────────────────────────────────────────────

def load_checkpoint(checkpoint_path: Path) -> dict:
    if checkpoint_path.exists():
        try:
            return json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_checkpoint(checkpoint_path: Path, data: dict):
    checkpoint_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─── Judge ───────────────────────────────────────────────────────────────────

JUDGE_SYSTEM = """You are a strict but fair academic grader evaluating AI model answers.
Your job: compare a model's answer to the reference answer and decide if the model's answer is CORRECT or INCORRECT.

Rules:
- Focus on whether the core answer is factually/logically correct.
- Minor phrasing differences are fine — mark CORRECT.
- Extra elaboration is fine as long as the core is right.
- For coding: the logic and approach must be correct, not necessarily identical syntax.
- For reasoning: the conclusion AND reasoning must be correct.
- If the model says something factually wrong or misses the key point, mark INCORRECT.
- Hallucinated extra content that contradicts the answer = INCORRECT.

Respond in this EXACT format (two lines only):
VERDICT: CORRECT
REASON: <one sentence explaining your decision>

Or:
VERDICT: INCORRECT
REASON: <one sentence explaining what was wrong>"""


def judge_answer(llm: Llama, qid: str, question: str, reference: str, model_answer: str, fmt: str) -> dict:
    """Ask the judge LLM to score one answer. Returns {verdict, reason, raw}."""

    user_msg = f"""Question ID: {qid}

QUESTION:
{question}

REFERENCE ANSWER:
{reference}

MODEL ANSWER:
{model_answer}

Grade the model answer. Respond ONLY with VERDICT and REASON lines."""

    prompt = format_prompt(user_msg, JUDGE_SYSTEM, fmt)

    try:
        result = llm(
            prompt,
            max_tokens=120,
            temperature=0.0,
            repeat_penalty=1.1,
            stop=["<|im_end|>", "[INST]", "###", "\n\nQ:", "User:", "USER:", "\n\n\n"],
            echo=False,
        )
        raw = result["choices"][0]["text"].strip()
    except Exception as e:
        return {"verdict": "ERROR", "reason": str(e), "raw": ""}

    # Parse verdict
    verdict_match = re.search(r'VERDICT:\s*(CORRECT|INCORRECT)', raw, re.IGNORECASE)
    reason_match  = re.search(r'REASON:\s*(.+)', raw, re.IGNORECASE)

    verdict = verdict_match.group(1).upper() if verdict_match else "UNCLEAR"
    reason  = reason_match.group(1).strip()  if reason_match  else raw[:200]

    # Normalise UNCLEAR — try fuzzy match
    if verdict == "UNCLEAR":
        if re.search(r'\bcorrect\b', raw, re.IGNORECASE):
            verdict = "CORRECT"
        elif re.search(r'\bincorrect\b|\bwrong\b', raw, re.IGNORECASE):
            verdict = "INCORRECT"

    return {"verdict": verdict, "reason": reason, "raw": raw}


# ─── Report generator ────────────────────────────────────────────────────────

def build_report(model_name: str, results: dict, reference: dict, model_answers: dict, inference_metrics: dict | None = None) -> str:
    """Build the full markdown report."""

    im = inference_metrics or {}  # per-qid timing/token dict (may be empty)

    # ── Helper: aggregate metrics for a list of (qid, result) items ──────
    def metrics_agg(items):
        ms = [im[qid] for qid, _ in items if qid in im and not im[qid].get("error")]
        if not ms:
            return None
        return {
            "avg_latency":  sum(x["latency_s"]         for x in ms) / len(ms),
            "avg_compl_tok":sum(x["completion_tokens"]  for x in ms) / len(ms),
            "avg_tps":      sum(x["tokens_per_sec"]     for x in ms) / len(ms),
            "total_tokens": sum(x["total_tokens"]       for x in ms),
            "n":            len(ms),
        }

    sections = {"Factual": [], "Reasoning": [], "Coding": []}
    for qid, r in sorted(results.items()):
        sec = get_section(qid)
        sections[sec].append((qid, r))

    def section_stats(items):
        total   = len(items)
        correct = sum(1 for _, r in items if r["verdict"] == "CORRECT")
        errors  = sum(1 for _, r in items if r["verdict"] == "ERROR")
        unclear = sum(1 for _, r in items if r["verdict"] == "UNCLEAR")
        acc     = correct / total * 100 if total else 0
        return total, correct, errors, unclear, acc

    all_items   = [(qid, r) for sec in sections.values() for qid, r in sec]
    tot, cor, err, unc, acc = section_stats(all_items)
    global_metrics = metrics_agg(all_items)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []

    # ── Title ────────────────────────────────────────────────────────────
    lines += [
        f"# Benchmark Evaluation Report",
        f"",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Model** | `{model_name}` |",
        f"| **Evaluated** | {now} |",
        f"| **Total Questions** | {tot} |",
        f"| **Judge Model** | `{Path(CONFIG['judge_model_path']).stem}` |",
        f"",
        f"---",
        f"",
    ]

    # ── Overall accuracy ─────────────────────────────────────────────────
    lines += [
        f"## Overall Accuracy",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| **Overall Accuracy** | **{acc:.2f}%** ({cor}/{tot}) |",
        f"| Correct | {cor} |",
        f"| Incorrect | {tot - cor - err - unc} |",
        f"| Unclear / Parse Error | {unc} |",
        f"| Judge Errors | {err} |",
    ]
    if global_metrics:
        gm = global_metrics
        efficiency_score = acc / gm["avg_latency"] if gm["avg_latency"] > 0 else 0.0
        lines += [
            f"| Avg Latency / Question | {gm['avg_latency']:.2f}s |",
            f"| Avg Completion Tokens / Question | {gm['avg_compl_tok']:.1f} |",
            f"| Avg Throughput | {gm['avg_tps']:.1f} tok/s |",
            f"| Total Tokens Used | {gm['total_tokens']:,} |",
            f"| **Efficiency Score** *(accuracy% / avg_latency)* | **{efficiency_score:.2f}** |",
        ]
    lines += ["", "---", ""]

    # ── Per-section summary ──────────────────────────────────────────────
    has_metrics = bool(im)
    if has_metrics:
        lines += [
            f"## Section Accuracy",
            f"",
            f"| Section | Questions | Correct | Incorrect | Accuracy | Avg Latency | Avg Tokens | Avg tok/s |",
            f"|---------|-----------|---------|-----------|----------|-------------|------------|-----------|",
        ]
    else:
        lines += [
            f"## Section Accuracy",
            f"",
            f"| Section | Questions | Correct | Incorrect | Accuracy |",
            f"|---------|-----------|---------|-----------|----------|",
        ]

    sec_ranges = {"Factual": "Q001-Q100", "Reasoning": "Q101-Q200", "Coding": "Q201-Q300"}
    section_data = {}
    for sec_name, items in sections.items():
        t, c, e, u, a = section_stats(items)
        wrong = t - c - e - u
        sec_range = sec_ranges[sec_name]
        sm = metrics_agg(items)
        if has_metrics and sm:
            lines.append(
                f"| {sec_name} ({sec_range}) | {t} | {c} | {wrong} | {a:.2f}% "
                f"| {sm['avg_latency']:.2f}s | {sm['avg_compl_tok']:.1f} | {sm['avg_tps']:.1f} |"
            )
        else:
            lines.append(f"| {sec_name} ({sec_range}) | {t} | {c} | {wrong} | {a:.2f}% |")
        section_data[sec_name] = {"total": t, "correct": c, "incorrect": wrong, "unclear": u, "errors": e, "accuracy": a}
    lines += ["", "---", ""]

    # ── Research-paper stats ─────────────────────────────────────────────
    lines += [
        f"## Statistics for Research Paper",
        f"",
        f"```",
        f"Model Name      : {model_name}",
        f"Overall Accuracy: {acc:.4f}% ({cor}/{tot})",
        f"",
        f"Section Breakdown:",
    ]
    for sec_name in ["Factual", "Reasoning", "Coding"]:
        d = section_data[sec_name]
        lines.append(
            f"  {sec_name:<12}: {d['accuracy']:.4f}%  "
            f"({d['correct']}/{d['total']} correct, "
            f"{d['incorrect']} incorrect, "
            f"{d['unclear']+d['errors']} unclear/error)"
        )

    if global_metrics:
        gm = global_metrics
        efficiency_score = acc / gm["avg_latency"] if gm["avg_latency"] > 0 else 0.0
        lines += [
            f"",
            f"Inference Performance:",
            f"  Avg Latency/Q   : {gm['avg_latency']:.3f}s",
            f"  Avg Tokens/Q    : {gm['avg_compl_tok']:.1f} completion tokens",
            f"  Avg Throughput  : {gm['avg_tps']:.2f} tok/s",
            f"  Total Tokens    : {gm['total_tokens']:,}",
            f"",
            f"  Efficiency Score: {efficiency_score:.4f}  (accuracy% ÷ avg_latency_s)",
            f"  [Higher = more accuracy per second of compute]",
        ]

        lines += [
            f"",
            f"Per-Section Efficiency:",
        ]
        for sec_name, items in sections.items():
            d = section_data[sec_name]
            sm = metrics_agg(items)
            if sm:
                sec_eff = d["accuracy"] / sm["avg_latency"] if sm["avg_latency"] > 0 else 0.0
                lines.append(
                    f"  {sec_name:<12}: {sec_eff:.4f} eff  "
                    f"(acc={d['accuracy']:.2f}%, avg_lat={sm['avg_latency']:.2f}s, "
                    f"avg_tok={sm['avg_compl_tok']:.1f})"
                )

    incorrect_qids = sorted(qid for qid, r in results.items() if r["verdict"] != "CORRECT")
    lines += [
        f"",
        f"Total Incorrect : {len(incorrect_qids)}",
        f"Incorrect QIDs  : {', '.join(incorrect_qids) if incorrect_qids else 'None'}",
        f"```",
        f"",
        f"---",
        f"",
    ]

    # ── Per-question detail ───────────────────────────────────────────────
    lines += [f"## Per-Question Results", f""]

    for sec_name, items in sections.items():
        t, c, e, u, a = section_stats(items)
        sm = metrics_agg(items)
        sec_header = f"### {sec_name} Questions ({sec_ranges[sec_name]}) — {a:.2f}% ({c}/{t})"
        if sm:
            sec_header += f"  |  avg {sm['avg_latency']:.2f}s · {sm['avg_compl_tok']:.0f} tok/q"
        lines += [sec_header, f""]

        if has_metrics and im:
            lines += [
                f"| QID | Verdict | Latency | Tokens | tok/s | Judge Reasoning |",
                f"|-----|---------|---------|--------|-------|-----------------|",
            ]
            for qid, r in items:
                verdict = r["verdict"]
                emoji   = "✅" if verdict == "CORRECT" else ("⚠️" if verdict in ("UNCLEAR", "ERROR") else "❌")
                reason  = r["reason"].replace("|", "\\|").replace("\n", " ")[:200]
                m       = im.get(qid, {})
                lat     = f"{m.get('latency_s', 0):.2f}s" if m else "—"
                tok     = str(m.get("completion_tokens", "—"))
                tps     = f"{m.get('tokens_per_sec', 0):.1f}" if m else "—"
                lines.append(f"| {qid} | {emoji} {verdict} | {lat} | {tok} | {tps} | {reason} |")
        else:
            lines += [
                f"| QID | Verdict | Judge Reasoning |",
                f"|-----|---------|-----------------|",
            ]
            for qid, r in items:
                verdict = r["verdict"]
                emoji   = "✅" if verdict == "CORRECT" else ("⚠️" if verdict in ("UNCLEAR", "ERROR") else "❌")
                reason  = r["reason"].replace("|", "\\|").replace("\n", " ")[:200]
                lines.append(f"| {qid} | {emoji} {verdict} | {reason} |")
        lines += [""]

    # ── Full answer comparison ────────────────────────────────────────────
    lines += [
        f"---",
        f"",
        f"## Full Answer Comparison (Incorrect / Unclear Only)",
        f"",
        f"> Only showing questions where the model was wrong or unclear.",
        f"",
    ]

    for qid, r in sorted(results.items()):
        if r["verdict"] == "CORRECT":
            continue
        ref_ans   = reference.get(qid, "N/A")
        model_ans = model_answers.get(qid, "N/A")
        verdict   = r["verdict"]
        emoji     = "⚠️" if verdict in ("UNCLEAR", "ERROR") else "❌"

        lines += [
            f"### {emoji} {qid} — {verdict}",
            f"",
            f"**Judge:** {r['reason']}",
            f"",
            f"**Reference Answer:**",
            f"```",
            ref_ans[:600] + ("…" if len(ref_ans) > 600 else ""),
            f"```",
            f"",
            f"**Model Answer:**",
            f"```",
            model_ans[:600] + ("…" if len(model_ans) > 600 else ""),
            f"```",
            f"",
        ]

    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    cfg = CONFIG

    # Validate paths
    for key, label in [("judge_model_path", "Judge model"), ("reference_path", "Reference answers"), ("model_answers_path", "Model answers")]:
        p = Path(cfg[key])
        if not p.exists():
            print(f"❌ {label} not found:\n   {p}")
            sys.exit(1)

    output_dir = Path(cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse files
    print("\n📂 Parsing reference answers …")
    reference = parse_qa_file(cfg["reference_path"])
    print(f"   Found {len(reference)} reference answers.")

    print("📂 Parsing model answers …")
    model_answers = parse_qa_file(cfg["model_answers_path"])
    model_name = get_model_name_from_file(cfg["model_answers_path"])
    print(f"   Found {len(model_answers)} model answers.")
    print(f"   Model: {model_name}")

    # Load per-question inference metrics produced by benchmark.py (optional)
    inference_metrics = load_inference_metrics(cfg["model_answers_path"])
    if inference_metrics:
        print(f"   ✅ Loaded inference metrics for {len(inference_metrics)} questions.")

    # Determine which questions to grade
    all_qids = sorted(set(reference.keys()) & set(model_answers.keys()))
    if cfg["question_filter"] is not None:
        indices = list(cfg["question_filter"])
        all_qids = [all_qids[i] for i in indices if i < len(all_qids)]

    print(f"\n📋 Questions to grade: {len(all_qids)}")
    missing_in_model = set(reference.keys()) - set(model_answers.keys())
    if missing_in_model:
        print(f"   ⚠️  {len(missing_in_model)} questions missing from model output: {sorted(missing_in_model)[:10]}")

    # Checkpoint
    safe_model_name = re.sub(r'[^\w\-.]', '_', model_name)
    checkpoint_path = output_dir / f".checkpoint_{safe_model_name}.json"
    report_path     = output_dir / f"{safe_model_name}_report.md"

    results = load_checkpoint(checkpoint_path)
    already_done = set(results.keys())
    remaining    = [q for q in all_qids if q not in already_done]

    if already_done:
        print(f"♻️  Resuming: {len(already_done)} already graded, {len(remaining)} remaining.")

    if not remaining:
        print("\n✅ All questions already graded. Generating report …")
        report = build_report(model_name, results, reference, model_answers, inference_metrics)
        report_path.write_text(report, encoding="utf-8")
        print(f"   Report saved: {report_path}")
        return

    # Load judge model
    print(f"\n🔄 Loading judge model …")
    print(f"   {Path(cfg['judge_model_path']).name}")
    t0 = time.time()
    try:
        llm = Llama(
            model_path=cfg["judge_model_path"],
            n_gpu_layers=cfg["judge_gpu_layers"],
            n_ctx=cfg["judge_n_ctx"],
            n_batch=512,
            verbose=False,
        )
    except Exception as e:
        print(f"❌ Failed to load judge model: {e}")
        sys.exit(1)
    print(f"   ✅ Loaded in {time.time()-t0:.1f}s")

    # Grade questions
    print(f"\n🧑‍⚖️  Grading {len(remaining)} questions …\n")

    errors = []
    start  = time.time()

    # We need the question texts — parse from questions.md if available, else use reference
    questions_text = {}
    # Try to infer question text from reference (strip the answer part)
    for qid, ref in reference.items():
        questions_text[qid] = f"(See reference for context)"

    for i, qid in enumerate(tqdm(remaining, desc="Grading", unit="q")):
        ref_ans   = reference.get(qid, "")
        model_ans = model_answers.get(qid, "(no answer)")

        verdict = judge_answer(
            llm       = llm,
            qid       = qid,
            question  = questions_text.get(qid, ""),
            reference = ref_ans,
            model_answer = model_ans,
            fmt       = cfg["judge_prompt_format"],
        )

        results[qid] = verdict

        if verdict["verdict"] == "ERROR":
            errors.append(qid)
            tqdm.write(f"  ⚠️  {qid} judge error: {verdict['reason'][:80]}")

        if (i + 1) % cfg["checkpoint_every"] == 0:
            save_checkpoint(checkpoint_path, results)
            tqdm.write(f"  💾 Checkpoint saved ({len(results)}/{len(all_qids)} graded)")

    # Final save
    save_checkpoint(checkpoint_path, results)

    elapsed = time.time() - start
    per_q   = elapsed / len(remaining) if remaining else 0

    print(f"\n{'='*60}")
    print(f"  ✅ Grading complete!")
    print(f"  Questions graded : {len(results)}")
    print(f"  Judge errors     : {len(errors)}")
    print(f"  Time elapsed     : {elapsed:.0f}s ({per_q:.1f}s/question)")
    print(f"{'='*60}")

    # Build and save report
    print(f"\n📝 Building report …")
    report = build_report(model_name, results, reference, model_answers, inference_metrics)
    report_path.write_text(report, encoding="utf-8")

    # Quick summary
    total   = len(results)
    correct = sum(1 for r in results.values() if r["verdict"] == "CORRECT")
    acc     = correct / total * 100 if total else 0

    print(f"\n{'='*60}")
    print(f"  Model   : {model_name}")
    print(f"  Accuracy: {acc:.2f}% ({correct}/{total})")

    for sec_name, (lo, hi) in [("Factual", (1,100)), ("Reasoning", (101,200)), ("Coding", (201,300))]:
        sec_qids = [q for q in results if lo <= int(q[1:]) <= hi]
        sec_cor  = sum(1 for q in sec_qids if results[q]["verdict"] == "CORRECT")
        sec_acc  = sec_cor / len(sec_qids) * 100 if sec_qids else 0
        print(f"  {sec_name:<12}: {sec_acc:.2f}% ({sec_cor}/{len(sec_qids)})")

    print(f"{'='*60}")
    print(f"\n📄 Report saved: {report_path}")

    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print(f"🗑️  Checkpoint cleaned up.")


if __name__ == "__main__":
    main()