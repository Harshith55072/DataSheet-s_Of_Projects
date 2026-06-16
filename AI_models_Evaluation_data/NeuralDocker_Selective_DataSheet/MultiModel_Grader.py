#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║         LLM Benchmark Grader  —  v2  (Ensemble-aware)          ║
║                                                                  ║
║  Works with BOTH:                                               ║
║    • Single-model answer files  (original pipeline)             ║
║    • Ensemble checkpoint JSON   (new multi-model pipeline)      ║
║                                                                  ║
║  Extra ensemble stats for research papers:                      ║
║    • Per-model win-rate vs accuracy                             ║
║    • Vote-margin vs accuracy correlation                        ║
║    • Disqualification impact analysis                           ║
║    • Unanimous-vote accuracy vs split-vote accuracy             ║
╚══════════════════════════════════════════════════════════════════╝

Usage:
    python grader.py

Edit the CONFIG block below before running.
"""

# ═══════════════════════════════════════════════════════════════
# CONFIG — edit this block
# ═══════════════════════════════════════════════════════════════

CONFIG = {
    # ── Judge model ───────────────────────────────────────────────
    "judge_model_path":   r"C:\Users\Lenovo\Documents\programing\NeuralDocker-Selective\models\qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "judge_gpu_layers":   -1,
    "judge_n_ctx":        2048,
    "judge_prompt_format": "chatml",   # chatml | alpaca | llama2 | vicuna | plain

    # ── Reference answers ─────────────────────────────────────────
    "reference_path": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\answers.md",

    # ── Input: pick ONE mode ──────────────────────────────────────
    #
    # MODE A — single-model .md answer file (original pipeline):
    #   Set "model_answers_path" to the .md file, leave "ensemble_checkpoint_path" as None.
    #
    # MODE B — ensemble pipeline checkpoint JSON:
    #   Set "ensemble_checkpoint_path" to the .checkpoint.json file,
    #   leave "model_answers_path" as None.
    #   The grader will read per-model answers, vote scores, winner, disqualifications.
    #
    "model_answers_path":r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\Final_data\ensemble_ANSWERS.md",

    

    # ── Output ────────────────────────────────────────────────────
    "output_dir": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\Final_data\Graded_answer\ensemble_GRADED_ANSWERS.md",

    # ── Grading behaviour ─────────────────────────────────────────
    "checkpoint_every": 10,
    "question_filter":  None,   # None = all. range(0,100) = first 100. [0,5,42] = specific.
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
from collections import defaultdict


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
        print("❌ Missing packages:")
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


# ─── Input loaders ───────────────────────────────────────────────────────────

def parse_md_answers(filepath: str) -> dict:
    """Parse a single-model **QXXX.** markdown answer file. Strips HTML comments."""
    text = Path(filepath).read_text(encoding="utf-8")
    # Strip <!-- ... --> comment lines (ensemble metadata)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    pattern = re.compile(
        r'\*\*(Q(\d{3}))\.\*\*\s*(.*?)(?=\*\*Q\d{3}\.\*\*|\Z)',
        re.DOTALL
    )
    result = {}
    for m in pattern.finditer(text):
        qid  = m.group(1)
        body = m.group(3).strip()
        body = re.sub(r'\n---.*$', '', body, flags=re.DOTALL).strip()
        result[qid] = body
    return result


def load_ensemble_checkpoint(filepath: str) -> dict:
    """Load the ensemble pipeline .checkpoint.json. Returns full data dict."""
    return json.loads(Path(filepath).read_text(encoding="utf-8"))


# ─── Section classifier ──────────────────────────────────────────────────────

def get_section(qid: str) -> str:
    num = int(qid[1:])
    if num <= 100:   return "Factual"
    if num <= 200:   return "Reasoning"
    return "Coding"


# ─── Checkpoint ──────────────────────────────────────────────────────────────

def load_checkpoint(p: Path) -> dict:
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_checkpoint(p: Path, data: dict):
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─── Judge ───────────────────────────────────────────────────────────────────

JUDGE_SYSTEM = """You are a strict but fair academic grader evaluating AI model answers.
Compare the model's answer to the reference answer and decide: CORRECT or INCORRECT.

Rules:
- Minor phrasing differences are fine — mark CORRECT.
- Extra elaboration is fine as long as the core is right.
- For coding: logic and approach must be correct, not necessarily identical syntax.
- For reasoning: both conclusion AND reasoning must be correct.
- Factually wrong or missing the key point → INCORRECT.
- Hallucinated content that contradicts the answer → INCORRECT.

Respond in EXACTLY this format (two lines only):
VERDICT: CORRECT
REASON: <one sentence>

Or:
VERDICT: INCORRECT
REASON: <one sentence explaining what was wrong>"""


def judge_answer(llm, qid, reference, model_answer, fmt) -> dict:
    user_msg = (
        f"Question ID: {qid}\n\n"
        f"REFERENCE ANSWER:\n{reference}\n\n"
        f"MODEL ANSWER:\n{model_answer}\n\n"
        f"Grade the model answer. Respond ONLY with VERDICT and REASON lines."
    )
    prompt = format_prompt(user_msg, JUDGE_SYSTEM, fmt)
    try:
        result = llm(
            prompt, max_tokens=120, temperature=0.0, repeat_penalty=1.1,
            stop=["<|im_end|>", "[INST]", "###", "\n\nQ:", "User:", "USER:", "\n\n\n"],
            echo=False,
        )
        raw = result["choices"][0]["text"].strip()
    except Exception as e:
        return {"verdict": "ERROR", "reason": str(e), "raw": ""}

    verdict_m = re.search(r'VERDICT:\s*(CORRECT|INCORRECT)', raw, re.IGNORECASE)
    reason_m  = re.search(r'REASON:\s*(.+)',                 raw, re.IGNORECASE)
    verdict   = verdict_m.group(1).upper() if verdict_m else "UNCLEAR"
    reason    = reason_m.group(1).strip()  if reason_m  else raw[:200]

    if verdict == "UNCLEAR":
        if re.search(r'\bcorrect\b',           raw, re.IGNORECASE): verdict = "CORRECT"
        elif re.search(r'\bincorrect\b|\bwrong\b', raw, re.IGNORECASE): verdict = "INCORRECT"

    return {"verdict": verdict, "reason": reason, "raw": raw}


# ─── Section stats helper ────────────────────────────────────────────────────

def section_stats(items, results):
    total   = len(items)
    correct = sum(1 for q in items if results[q]["verdict"] == "CORRECT")
    errors  = sum(1 for q in items if results[q]["verdict"] == "ERROR")
    unclear = sum(1 for q in items if results[q]["verdict"] == "UNCLEAR")
    acc     = correct / total * 100 if total else 0.0
    return total, correct, errors, unclear, acc


# ─── Report builders ─────────────────────────────────────────────────────────

def _common_header(model_name: str, judge_name: str, tot: int, cor: int,
                   err: int, unc: int, acc: float, sections: dict,
                   results: dict, reference: dict, model_answers: dict) -> list:
    """Shared header + accuracy table + per-section table used by both modes."""
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Benchmark Evaluation Report",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Model** | `{model_name}` |",
        f"| **Evaluated** | {now} |",
        f"| **Total Questions** | {tot} |",
        f"| **Judge Model** | `{judge_name}` |",
        "",
        "---",
        "",
        "## Overall Accuracy",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| **Overall Accuracy** | **{acc:.2f}%** ({cor}/{tot}) |",
        f"| Correct | {cor} |",
        f"| Incorrect | {tot - cor - err - unc} |",
        f"| Unclear / Parse Error | {unc} |",
        f"| Judge Errors | {err} |",
        "",
        "---",
        "",
        "## Section Accuracy",
        "",
        "| Section | Questions | Correct | Incorrect | Accuracy |",
        "|---------|-----------|---------|-----------|----------|",
    ]
    sec_ranges = {"Factual": "Q001–Q100", "Reasoning": "Q101–Q200", "Coding": "Q201–Q300"}
    for sec_name, qids in sections.items():
        t, c, e, u, a = section_stats(qids, results)
        wrong = t - c - e - u
        lines.append(f"| {sec_name} ({sec_ranges[sec_name]}) | {t} | {c} | {wrong} | {a:.2f}% |")
    lines += ["", "---", ""]
    return lines


def _research_block(model_name: str, judge_name: str, acc: float, cor: int,
                    tot: int, sections: dict, results: dict) -> list:
    sec_ranges = {"Factual": (1,100), "Reasoning": (101,200), "Coding": (201,300)}
    lines = [
        "## Statistics for Research Paper",
        "",
        "```",
        f"Model Name      : {model_name}",
        f"Judge Model     : {judge_name}",
        f"Overall Accuracy: {acc:.4f}% ({cor}/{tot})",
        "",
        "Section Breakdown:",
    ]
    for sec_name, qids in sections.items():
        t, c, e, u, a = section_stats(qids, results)
        wrong = t - c - e - u
        lines.append(
            f"  {sec_name:<12}: {a:.4f}%  "
            f"({c}/{t} correct, {wrong} incorrect, {u+e} unclear/error)"
        )
    incorrect_qids = sorted(q for q in results if results[q]["verdict"] != "CORRECT")
    lines += [
        "",
        f"Total Incorrect : {len(incorrect_qids)}",
        f"Incorrect QIDs  : {', '.join(incorrect_qids) if incorrect_qids else 'None'}",
        "```",
        "",
        "---",
        "",
    ]
    return lines


def _per_question_block(sections: dict, results: dict,
                        reference: dict, model_answers: dict) -> list:
    sec_ranges = {"Factual": "Q001–Q100", "Reasoning": "Q101–Q200", "Coding": "Q201–Q300"}
    lines = ["## Per-Question Results", ""]
    for sec_name, qids in sections.items():
        t, c, e, u, a = section_stats(qids, results)
        lines += [
            f"### {sec_name} Questions ({sec_ranges[sec_name]}) — {a:.2f}% ({c}/{t})",
            "",
            "| QID | Verdict | Judge Reasoning |",
            "|-----|---------|-----------------|",
        ]
        for qid in sorted(qids):
            r       = results[qid]
            verdict = r["verdict"]
            emoji   = "✅" if verdict == "CORRECT" else ("⚠️" if verdict in ("UNCLEAR","ERROR") else "❌")
            reason  = r["reason"].replace("|","\\|").replace("\n"," ")[:200]
            lines.append(f"| {qid} | {emoji} {verdict} | {reason} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Full Answer Comparison (Incorrect / Unclear Only)",
        "",
        "> Only showing questions where the model was wrong or unclear.",
        "",
    ]
    for qid in sorted(results):
        r = results[qid]
        if r["verdict"] == "CORRECT":
            continue
        ref_ans   = reference.get(qid, "N/A")
        model_ans = model_answers.get(qid, "N/A")
        verdict   = r["verdict"]
        emoji     = "⚠️" if verdict in ("UNCLEAR","ERROR") else "❌"
        lines += [
            f"### {emoji} {qid} — {verdict}",
            "",
            f"**Judge:** {r['reason']}",
            "",
            "**Reference Answer:**",
            "```",
            ref_ans[:600] + ("…" if len(ref_ans) > 600 else ""),
            "```",
            "",
            "**Model Answer:**",
            "```",
            model_ans[:600] + ("…" if len(model_ans) > 600 else ""),
            "```",
            "",
        ]
    return lines


# ── Single-model report ───────────────────────────────────────────────────────

def build_single_report(model_name, judge_name, results, reference, model_answers) -> str:
    sections = {"Factual": [], "Reasoning": [], "Coding": []}
    for qid in sorted(results):
        sections[get_section(qid)].append(qid)

    all_qids = list(results.keys())
    tot = len(all_qids)
    cor = sum(1 for r in results.values() if r["verdict"] == "CORRECT")
    err = sum(1 for r in results.values() if r["verdict"] == "ERROR")
    unc = sum(1 for r in results.values() if r["verdict"] == "UNCLEAR")
    acc = cor / tot * 100 if tot else 0.0

    lines  = _common_header(model_name, judge_name, tot, cor, err, unc, acc,
                             sections, results, reference, model_answers)
    lines += _research_block(model_name, judge_name, acc, cor, tot, sections, results)
    lines += _per_question_block(sections, results, reference, model_answers)
    return "\n".join(lines)


# ── Ensemble report (adds ensemble-specific analytics) ───────────────────────

def build_ensemble_report(ensemble_name, judge_name, results, reference,
                           ensemble_data, model_names) -> str:
    """
    results        : {qid: {verdict, reason, raw}}  — grader output
    ensemble_data  : raw checkpoint JSON from ensemble pipeline
    model_names    : list of model name strings
    """
    ens_results = ensemble_data.get("results", {})   # raw per-Q ensemble data

    # Build model_answers from the winner answer for the common blocks
    model_answers = {qid: ens_results[qid]["winner_answer"]
                     for qid in ens_results if qid in results}

    sections = {"Factual": [], "Reasoning": [], "Coding": []}
    for qid in sorted(results):
        sections[get_section(qid)].append(qid)

    tot = len(results)
    cor = sum(1 for r in results.values() if r["verdict"] == "CORRECT")
    err = sum(1 for r in results.values() if r["verdict"] == "ERROR")
    unc = sum(1 for r in results.values() if r["verdict"] == "UNCLEAR")
    acc = cor / tot * 100 if tot else 0.0

    lines  = _common_header(ensemble_name, judge_name, tot, cor, err, unc, acc,
                             sections, results, reference, model_answers)
    lines += _research_block(ensemble_name, judge_name, acc, cor, tot, sections, results)

    # ── Ensemble-specific analytics ──────────────────────────────────────
    lines += [
        "## Ensemble Analytics",
        "",
        "> These stats are only available for ensemble runs and are useful for",
        "> characterising the ensemble method in a research paper.",
        "",
    ]

    # 1. Per-model win-rate vs accuracy ──────────────────────────────────
    win_correct   = defaultdict(int)
    win_total     = defaultdict(int)
    disq_total    = defaultdict(int)
    vote_by_correct = {"CORRECT": [], "INCORRECT": []}  # vote margins

    for qid, r in results.items():
        if qid not in ens_results:
            continue
        er      = ens_results[qid]
        winner  = er.get("winner_model", "")
        disq    = er.get("disqualified", [])
        scores  = er.get("scores", {})
        verdict = r["verdict"]

        win_total[winner] += 1
        if verdict == "CORRECT":
            win_correct[winner] += 1

        for d in disq:
            disq_total[d] += 1

        # Vote margin = winner score minus second-highest score
        sorted_scores = sorted(scores.values(), reverse=True)
        margin = (sorted_scores[0] - sorted_scores[1]) if len(sorted_scores) > 1 else 0
        vote_by_correct[verdict if verdict in ("CORRECT","INCORRECT") else "INCORRECT"].append(margin)

    lines += [
        "### Per-Model Win-Rate vs Accuracy",
        "",
        "| Model | Times Won | Accuracy When Winning | Disqualifications |",
        "|-------|-----------|-----------------------|-------------------|",
    ]
    for name in model_names:
        wt  = win_total.get(name, 0)
        wc  = win_correct.get(name, 0)
        wa  = wc / wt * 100 if wt else 0.0
        dq  = disq_total.get(name, 0)
        lines.append(f"| {name} | {wt} | {wa:.2f}% ({wc}/{wt}) | {dq} |")
    lines += [""]

    # 2. Vote-margin vs accuracy ─────────────────────────────────────────
    def avg(lst): return sum(lst)/len(lst) if lst else 0.0

    avg_margin_correct   = avg(vote_by_correct["CORRECT"])
    avg_margin_incorrect = avg(vote_by_correct["INCORRECT"])

    # Unanimous = all reviewers gave max score (15 for 3 reviewers × 5)
    # Approximation: margin >= (n_models-1)*4  means dominant winner
    unanimous, unanimous_correct = 0, 0
    split,     split_correct     = 0, 0
    n = len(model_names)
    unanimous_threshold = (n - 1) * 3   # ≥3 pts per reviewer gap = "dominant"

    for qid, r in results.items():
        if qid not in ens_results:
            continue
        scores = ens_results[qid].get("scores", {})
        sorted_s = sorted(scores.values(), reverse=True)
        margin   = (sorted_s[0] - sorted_s[1]) if len(sorted_s) > 1 else 0
        verdict  = r["verdict"]
        if margin >= unanimous_threshold:
            unanimous += 1
            if verdict == "CORRECT": unanimous_correct += 1
        else:
            split += 1
            if verdict == "CORRECT": split_correct += 1

    ua = unanimous_correct / unanimous * 100 if unanimous else 0.0
    sa = split_correct     / split     * 100 if split     else 0.0

    lines += [
        "### Vote Confidence vs Accuracy",
        "",
        f"Average vote margin when answer was **CORRECT**  : {avg_margin_correct:.2f} pts",
        f"Average vote margin when answer was **INCORRECT**: {avg_margin_incorrect:.2f} pts",
        "",
        "> A higher margin for correct answers suggests the ensemble vote is a",
        "> meaningful confidence signal — useful to argue in a paper.",
        "",
        "| Vote Type | Questions | Correct | Accuracy |",
        "|-----------|-----------|---------|----------|",
        f"| Dominant winner (margin ≥ {unanimous_threshold} pts) | {unanimous} | {unanimous_correct} | {ua:.2f}% |",
        f"| Close vote (margin < {unanimous_threshold} pts) | {split} | {split_correct} | {sa:.2f}% |",
        "",
        "> If dominant-winner accuracy >> close-vote accuracy, the vote margin is",
        "> a reliable confidence proxy — cite this in your methodology section.",
        "",
    ]

    # 3. Disqualification impact ─────────────────────────────────────────
    with_disq, with_disq_correct   = 0, 0
    without_disq, without_disq_correct = 0, 0
    for qid, r in results.items():
        if qid not in ens_results: continue
        disq = ens_results[qid].get("disqualified", [])
        v    = r["verdict"]
        if disq:
            with_disq += 1
            if v == "CORRECT": with_disq_correct += 1
        else:
            without_disq += 1
            if v == "CORRECT": without_disq_correct += 1

    da  = with_disq_correct    / with_disq    * 100 if with_disq    else 0.0
    nda = without_disq_correct / without_disq * 100 if without_disq else 0.0

    lines += [
        "### Disqualification Impact",
        "",
        "| Condition | Questions | Correct | Accuracy |",
        "|-----------|-----------|---------|----------|",
        f"| At least one model disqualified | {with_disq} | {with_disq_correct} | {da:.2f}% |",
        f"| No disqualifications | {without_disq} | {without_disq_correct} | {nda:.2f}% |",
        "",
        "> Questions with disqualifications had fewer valid answers to vote on.",
        "> Lower accuracy here may indicate prompt-format misconfiguration.",
        "",
        "---",
        "",
    ]

    # ── Shared per-question + answer-comparison sections ─────────────────
    lines += _per_question_block(sections, results, reference, model_answers)

    # ── Ensemble per-question voting detail ──────────────────────────────
    lines += [
        "---",
        "",
        "## Per-Question Voting Detail",
        "",
        "| QID | Winner | Votes | " + " | ".join(model_names) + " | Disq | Verdict |",
        "|-----|--------|-------|" + "|".join(["-------"]*len(model_names)) + "|------|---------|",
    ]
    for qid in sorted(results):
        if qid not in ens_results: continue
        er      = ens_results[qid]
        winner  = er.get("winner_model", "?")
        scores  = er.get("scores", {})
        disq    = ",".join(er.get("disqualified", [])) or "—"
        verdict = results[qid]["verdict"]
        emoji   = "✅" if verdict == "CORRECT" else ("⚠️" if verdict in ("UNCLEAR","ERROR") else "❌")
        score_cells = " | ".join(str(scores.get(m,"?")) for m in model_names)
        lines.append(
            f"| {qid} | {winner} | {er.get('winner_votes','?')} | "
            f"{score_cells} | {disq} | {emoji} {verdict} |"
        )
    lines.append("")

    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    cfg = CONFIG

    # ── Determine mode ───────────────────────────────────────────────────
    ensemble_mode = bool(cfg.get("ensemble_checkpoint_path"))
    single_mode   = bool(cfg.get("model_answers_path"))

    if ensemble_mode and single_mode:
        print("❌ Set only ONE of model_answers_path or ensemble_checkpoint_path.")
        sys.exit(1)
    if not ensemble_mode and not single_mode:
        print("❌ Set either model_answers_path or ensemble_checkpoint_path in CONFIG.")
        sys.exit(1)

    # ── Validate paths ───────────────────────────────────────────────────
    for key, label in [
        ("judge_model_path", "Judge model"),
        ("reference_path",   "Reference answers"),
    ]:
        if not Path(cfg[key]).exists():
            print(f"❌ {label} not found:\n   {cfg[key]}")
            sys.exit(1)

    output_dir = Path(cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Load reference ───────────────────────────────────────────────────
    print("\n📂 Parsing reference answers …")
    reference = parse_md_answers(cfg["reference_path"])
    print(f"   Found {len(reference)} reference answers.")

    # ── Load model answers ───────────────────────────────────────────────
    if single_mode:
        print("📂 Parsing single-model answer file …")
        model_answers = parse_md_answers(cfg["model_answers_path"])
        model_name    = Path(cfg["model_answers_path"]).stem
        ensemble_data = None
        model_names   = [model_name]
        print(f"   Found {len(model_answers)} answers.  Model: {model_name}")

    else:  # ensemble mode
        print("📂 Loading ensemble checkpoint JSON …")
        ensemble_data = load_ensemble_checkpoint(cfg["ensemble_checkpoint_path"])
        ens_results   = ensemble_data.get("results", {})
        model_names   = sorted({
            m for r in ens_results.values()
            for m in r.get("answers", {}).keys()
        })
        # Use the winner answer as the "model answer" for grading
        model_answers = {qid: r["winner_answer"] for qid, r in ens_results.items()}
        model_name    = "Ensemble[" + "+".join(model_names) + "]"
        print(f"   Found {len(model_answers)} ensemble answers.")
        print(f"   Models: {', '.join(model_names)}")

    # ── Question list ────────────────────────────────────────────────────
    all_qids = sorted(set(reference.keys()) & set(model_answers.keys()))
    if cfg["question_filter"] is not None:
        indices  = list(cfg["question_filter"])
        all_qids = [all_qids[i] for i in indices if i < len(all_qids)]
    print(f"\n📋 Questions to grade: {len(all_qids)}")

    missing = set(reference.keys()) - set(model_answers.keys())
    if missing:
        print(f"   ⚠️  {len(missing)} reference questions missing from model output: "
              f"{sorted(missing)[:10]}")

    # ── Checkpoint ───────────────────────────────────────────────────────
    safe_name       = re.sub(r'[^\w\-.]', '_', model_name)[:80]
    checkpoint_path = output_dir / f".checkpoint_{safe_name}.json"
    report_path     = output_dir / f"{safe_name}_report.md"

    results      = load_checkpoint(checkpoint_path)
    already_done = set(results.keys())
    remaining    = [q for q in all_qids if q not in already_done]

    if already_done:
        print(f"♻️  Resuming: {len(already_done)} already graded, {len(remaining)} remaining.")

    # ── Load judge ───────────────────────────────────────────────────────
    if remaining:
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
            print(f"❌ Failed to load judge: {e}")
            sys.exit(1)
        print(f"   ✅ Loaded in {time.time()-t0:.1f}s")

        # ── Grade ─────────────────────────────────────────────────────────
        print(f"\n🧑‍⚖️  Grading {len(remaining)} questions …\n")
        errors = []
        start  = time.time()

        for i, qid in enumerate(tqdm(remaining, desc="Grading", unit="q")):
            ref_ans   = reference.get(qid, "")
            model_ans = model_answers.get(qid, "(no answer)")

            verdict = judge_answer(
                llm=llm, qid=qid,
                reference=ref_ans, model_answer=model_ans,
                fmt=cfg["judge_prompt_format"],
            )
            results[qid] = verdict

            if verdict["verdict"] == "ERROR":
                errors.append(qid)
                tqdm.write(f"  ⚠️  {qid} judge error: {verdict['reason'][:80]}")

            if (i + 1) % cfg["checkpoint_every"] == 0:
                save_checkpoint(checkpoint_path, results)
                tqdm.write(f"  💾 Checkpoint ({len(results)}/{len(all_qids)} graded)")

        save_checkpoint(checkpoint_path, results)
        elapsed = time.time() - start
        print(f"\n{'='*60}")
        print(f"  ✅ Grading done!  {len(results)} questions  |  "
              f"{elapsed:.0f}s  ({elapsed/len(remaining) if remaining else 0:.1f}s/q)  |  "
              f"{len(errors)} judge errors")
        print(f"{'='*60}")

    # ── Build report ─────────────────────────────────────────────────────
    print(f"\n📝 Building report …")
    judge_name = Path(cfg["judge_model_path"]).stem

    if single_mode:
        report = build_single_report(
            model_name, judge_name, results, reference, model_answers
        )
    else:
        report = build_ensemble_report(
            model_name, judge_name, results, reference,
            ensemble_data, model_names
        )

    report_path.write_text(report, encoding="utf-8")

    # ── Summary ──────────────────────────────────────────────────────────
    tot = len(results)
    cor = sum(1 for r in results.values() if r["verdict"] == "CORRECT")
    acc = cor / tot * 100 if tot else 0.0

    print(f"\n{'='*60}")
    print(f"  Model   : {model_name}")
    print(f"  Accuracy: {acc:.2f}% ({cor}/{tot})")
    for sec_name, (lo, hi) in [("Factual",(1,100)),("Reasoning",(101,200)),("Coding",(201,300))]:
        sec_q   = [q for q in results if lo <= int(q[1:]) <= hi]
        sec_cor = sum(1 for q in sec_q if results[q]["verdict"] == "CORRECT")
        sec_acc = sec_cor / len(sec_q) * 100 if sec_q else 0.0
        print(f"  {sec_name:<12}: {sec_acc:.2f}% ({sec_cor}/{len(sec_q)})")
    print(f"{'='*60}")
    print(f"\n📄 Report: {report_path}")

    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print("🗑️  Checkpoint cleaned up.")


if __name__ == "__main__":
    main()