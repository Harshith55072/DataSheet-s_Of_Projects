#!/usr/bin/env python3
"""
GGUF Multi-Model Ensemble Benchmark Pipeline  —  v3 (Weighted Voting)
======================================================================
Key upgrade over v2: models vote with WEIGHTS based on their proven
per-section accuracy from a previous grader run.

Why this matters
────────────────
Flat voting lets 3 mediocre models outvote 1 strong model even when the
strong model is right. Weighted voting fixes this: a model with 97%
Coding accuracy dominates Coding questions; a model with 60% Reasoning
accuracy barely contributes to Reasoning questions.

Two ways to set weights
───────────────────────
  MODE A — Automatic (recommended after your first grader run):
    Point "grader_reports" at each model's grader .md report.
    Weights are parsed automatically from the Section Accuracy table.

  MODE B — Manual:
    Set "manual_weights" in each model's config block.
    Format: {"Factual": 0.98, "Reasoning": 0.75, "Coding": 0.97}
    Use 0.0–1.0 (accuracy as a fraction). If None, defaults to 0.5.

Section classification
──────────────────────
  Q001–Q100  →  Factual
  Q101–Q200  →  Reasoning
  Q201–Q300  →  Coding

Usage:
    python run_benchmark_ensemble_v3.py
"""

# ═══════════════════════════════════════════════════════════════════════════
CONFIG = {
    # ── Models ────────────────────────────────────────────────────────────
    "models": [
        {
            "name":          "ModelA",
            "path":          r"C:\Users\Lenovo\Documents\programing\NeuralDocker-Selective\models\gemma-2b-aps-it-Q4_K_M.gguf",
            "prompt_format": "chatml",

            # Option A: point at this model's grader report .md
            "grader_report": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\Final_data\Graded_answer\gemma-2b-aps-it-Q4_K_M_report.md",

            # Option B: set manually (used if grader_report is None)
            # "manual_weights": {"Factual": 0.95, "Reasoning": 0.79, "Coding": 0.85},
            "manual_weights": None,
        },
        {
            "name":          "ModelB",
            "path":          r"C:\Users\Lenovo\Documents\programing\NeuralDocker-Selective\models\Llama-3.2-1B-Instruct-Q4_K_M.gguf",
            "prompt_format": "chatml",
            "grader_report": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\Final_data\Graded_answer\Llama-3.2-1B-Instruct-Q4_K_M_report.md",
            "manual_weights": None,
        },
        {
            "name":          "ModelC",
            "path":          r"C:\Users\Lenovo\Documents\programing\NeuralDocker-Selective\models\qwen2.5-1.5b-instruct-q4_k_m.gguf",
            "prompt_format": "chatml",
            "grader_report": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\Final_data\Graded_answer\qwen2.5-1.5b-instruct-q4_k_m_report.md",
            "manual_weights": None,
        },
        {
            "name":          "ModelD",
            "path":          r"C:\Users\Lenovo\Documents\programing\NeuralDocker-Selective\models\unsloth.Q4_K_M.gguf",
            "prompt_format": "chatml",
            "grader_report": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\Final_data\Graded_answer\unsloth.Q4_K_M_report.md",
            "manual_weights": None,
        },
    ],

    # ── Paths ─────────────────────────────────────────────────────────────
    "questions_path": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\questions.md",
    "output_path":    r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\NeuralDocker_Selective_DataSheet\Final_data\ensemble_ANSWERS.md",

    # ── GPU ───────────────────────────────────────────────────────────────
    "n_gpu_layers": 20,

    # ── Context & generation ──────────────────────────────────────────────
    "n_ctx":          2048,
    "max_tokens":     512,
    "temperature":    0.0,
    "repeat_penalty": 1.1,
    "n_batch":        512,

    # ── System prompts ────────────────────────────────────────────────────
    "answer_system_prompt": (
        "You are a helpful and knowledgeable assistant. "
        "Answer the question accurately and concisely."
    ),
    "review_system_prompt": (
        "You are an expert evaluator. You will be shown a question and an answer.\n"
        "Rate the answer's accuracy and quality on a scale from 1 to 5:\n"
        "  1 = completely wrong or useless\n"
        "  2 = mostly wrong with minor correct parts\n"
        "  3 = partially correct\n"
        "  4 = mostly correct with minor issues\n"
        "  5 = accurate, complete, and well-explained\n"
        "Respond with ONLY a single integer (1–5). Nothing else."
    ),

    # ── Reviewer max tokens ───────────────────────────────────────────────
    "review_max_tokens": 8,

    # ── Progress ──────────────────────────────────────────────────────────
    "checkpoint_every": 5,

    # ── Filter ────────────────────────────────────────────────────────────
    "question_filter": None,
}
# ═══════════════════════════════════════════════════════════════════════════

import re
import sys
import time
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict


def check_dependencies():
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

check_dependencies()
from llama_cpp import Llama
from tqdm import tqdm


# ─── Section classifier ──────────────────────────────────────────────────────

def get_section(qid: str) -> str:
    n = int(qid[1:])
    if n <= 100: return "Factual"
    if n <= 200: return "Reasoning"
    return "Coding"


# ─── Weight loader ───────────────────────────────────────────────────────────

def parse_weights_from_report(report_path: str) -> dict:
    """
    Parse per-section accuracy from a grader report .md.
    Returns {"Factual": 0.95, "Reasoning": 0.79, "Coding": 0.85}
    Looks for lines like: | Factual (Q001–Q100) | 100 | 95 | 5 | 95.00% |
    """
    text = Path(report_path).read_text(encoding="utf-8")
    weights = {}
    for sec in ("Factual", "Reasoning", "Coding"):
        # Match the section row in the accuracy table
        pat = re.compile(
            rf'\|\s*{sec}\s*\([^)]+\)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*([\d.]+)%',
            re.IGNORECASE
        )
        m = pat.search(text)
        if m:
            weights[sec] = float(m.group(1)) / 100.0
        else:
            weights[sec] = 0.5   # safe default if not found
            print(f"   ⚠️  Could not parse {sec} accuracy from {report_path}. Using 0.5.")
    return weights


def load_model_weights(model_cfg: dict) -> dict:
    """Return section weights for a model, from report or manual config."""
    if model_cfg.get("grader_report"):
        p = Path(model_cfg["grader_report"])
        if p.exists():
            w = parse_weights_from_report(str(p))
            print(f"   {model_cfg['name']}: weights from grader report  "
                  f"F={w['Factual']:.2f}  R={w['Reasoning']:.2f}  C={w['Coding']:.2f}")
            return w
        else:
            print(f"   ⚠️  Grader report not found for {model_cfg['name']}: {p}")

    if model_cfg.get("manual_weights"):
        w = model_cfg["manual_weights"]
        # Normalise keys just in case
        out = {
            "Factual":   float(w.get("Factual",   0.5)),
            "Reasoning": float(w.get("Reasoning", 0.5)),
            "Coding":    float(w.get("Coding",    0.5)),
        }
        print(f"   {model_cfg['name']}: manual weights  "
              f"F={out['Factual']:.2f}  R={out['Reasoning']:.2f}  C={out['Coding']:.2f}")
        return out

    print(f"   {model_cfg['name']}: no weights found — using 0.5 for all sections.")
    return {"Factual": 0.5, "Reasoning": 0.5, "Coding": 0.5}


# ─── Prompt formatting ───────────────────────────────────────────────────────

def format_prompt(question: str, system: str, fmt: str) -> str:
    if fmt == "chatml":
        return (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{question}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
    elif fmt == "alpaca":
        return f"### System:\n{system}\n\n### Instruction:\n{question}\n\n### Response:\n"
    elif fmt == "llama2":
        return f"[INST] <<SYS>>\n{system}\n<</SYS>>\n\n{question} [/INST]"
    elif fmt == "vicuna":
        return f"SYSTEM: {system}\n\nUSER: {question}\nASSISTANT:"
    elif fmt == "plain":
        return f"{system}\n\nQ: {question}\nA:"
    else:
        raise ValueError(f"Unknown prompt format: {fmt}")


def format_review_prompt(question: str, answer: str, system: str, fmt: str) -> str:
    body = (
        f"Question:\n{question}\n\n"
        f"Answer to evaluate:\n{answer}\n\n"
        f"Your score (1–5):"
    )
    return format_prompt(body, system, fmt)


# ─── Broken-answer detection ─────────────────────────────────────────────────

_LEAK_PATTERNS = [
    re.compile(r'^-\s*(you are|answer the question|helpful and knowledgeable)', re.I),
    re.compile(r'you are a helpful and knowledgeable assistant', re.I),
    re.compile(r'answer the question accurately', re.I),
    re.compile(r'^\(inference error', re.I),
    re.compile(r'^\(no answer generated\)', re.I),
]

def is_broken(answer: str) -> bool:
    head = answer[:200]
    for pat in _LEAK_PATTERNS:
        if pat.search(head):
            return True
    lines = [l.strip() for l in answer.splitlines() if l.strip()]
    if lines:
        leak = sum(1 for l in lines if re.match(r'^-\s*(you|answer the|helpful)', l, re.I))
        if leak / len(lines) > 0.5:
            return True
    return False


# ─── Inference ───────────────────────────────────────────────────────────────

STOP_TOKENS = ["<|im_end|>", "[INST]", "###", "\n\nQ:", "User:", "USER:", "</s>"]

def run_inference(llm, prompt, max_tokens, temperature, repeat_penalty) -> str:
    result = llm(
        prompt, max_tokens=max_tokens, temperature=temperature,
        repeat_penalty=repeat_penalty, stop=STOP_TOKENS, echo=False,
    )
    text = result["choices"][0]["text"].strip()
    text = re.sub(r'^(Assistant:|ASSISTANT:|A:)\s*', '', text).strip()
    text = text.split("<|im_start|>")[0].strip()
    return text or "(no answer generated)"


def extract_score(raw: str) -> int:
    m = re.search(r'[1-5]', raw)
    return int(m.group()) if m else 3


# ─── Weighted vote tally ─────────────────────────────────────────────────────

def compute_weighted_scores(raw_peer_scores: dict, model_weights: dict,
                             section: str, disqualified: set) -> dict:
    """
    raw_peer_scores : {answerer_name: {reviewer_name: raw_score_1_to_5}}
    model_weights   : {model_name: {"Factual":w, "Reasoning":w, "Coding":w}}
    section         : "Factual" | "Reasoning" | "Coding"
    disqualified    : set of model names whose scores are forced to 0

    Returns {model_name: weighted_total_score}

    Each raw score is multiplied by the REVIEWER's weight for this section.
    Rationale: a reviewer with proven 97% accuracy in Coding is a more
    trustworthy judge of Coding answers than one with 60% accuracy.
    """
    weighted = {}
    for answerer, reviews in raw_peer_scores.items():
        if answerer in disqualified:
            weighted[answerer] = 0.0
            continue
        total = 0.0
        for reviewer, score in reviews.items():
            reviewer_weight = model_weights.get(reviewer, {}).get(section, 0.5)
            total += score * reviewer_weight
        weighted[answerer] = total
    return weighted


def pick_winner(weighted_scores: dict, answers: dict, disqualified: set) -> str:
    eligible = {n: s for n, s in weighted_scores.items() if n not in disqualified}
    if not eligible:
        eligible = weighted_scores   # all broken fallback
    best = max(eligible.values())
    tied = [n for n, s in eligible.items() if s == best]
    if len(tied) == 1:
        return tied[0]
    # Tie-break: longest substantive answer
    return max(tied, key=lambda n: len(answers.get(n, "")))


# ─── Question parser ─────────────────────────────────────────────────────────

def parse_questions(filepath: str) -> list:
    text = Path(filepath).read_text(encoding="utf-8")
    pattern = re.compile(
        r'\*\*(Q(\d{3}))\.\*\*\s*(.*?)(?=\*\*Q\d{3}\.\*\*|\Z)', re.DOTALL
    )
    questions = []
    for m in pattern.finditer(text):
        qid = m.group(1)
        raw = m.group(3).strip()
        raw = re.sub(r'\n---.*$', '', raw, flags=re.DOTALL).strip()
        questions.append({"qid": qid, "text": raw})
    return questions


# ─── Checkpoint ──────────────────────────────────────────────────────────────

CHECKPOINT_JSON_PATH = None

def load_checkpoint_json() -> dict:
    p = Path(CHECKPOINT_JSON_PATH)
    if p.exists():
        try: return json.loads(p.read_text(encoding="utf-8"))
        except Exception: pass
    return {}

def save_checkpoint_json(data: dict):
    Path(CHECKPOINT_JSON_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(CHECKPOINT_JSON_PATH).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ─── Output ──────────────────────────────────────────────────────────────────

def save_output(output_path, results, model_names, vote_totals,
                all_questions, model_weights):
    lines = [
        "# Ensemble Benchmark Answers  (v3 — Weighted Voting)",
        f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Models    : {', '.join(model_names)}",
        "",
        "### Model Weights",
        "",
        "| Model | Factual | Reasoning | Coding |",
        "|-------|---------|-----------|--------|",
    ]
    for name in model_names:
        w = model_weights.get(name, {})
        lines.append(
            f"| {name} | {w.get('Factual',0):.2f} "
            f"| {w.get('Reasoning',0):.2f} "
            f"| {w.get('Coding',0):.2f} |"
        )
    lines += ["", "---", ""]

    for q in all_questions:
        qid = q["qid"]
        if qid not in results: continue
        r        = results[qid]
        winner   = r.get("winner_model", "?")
        best_ans = r.get("winner_answer", "(missing)")
        total_v  = r.get("winner_weighted_score", "?")
        disq     = r.get("disqualified", [])

        lines.append(f"**{qid}.** {best_ans}")
        detail = (
            f"winner: {winner} | weighted_score: {total_v:.3f}"
            + (" | disqualified: " + ",".join(disq) if disq else "")
            + " | "
            + " | ".join(
                f"{m}: {r['raw_scores'].get(m, '?'):.3f}"
                for m in model_names
            )
        )
        lines.append(f"<!-- {detail} -->")
        lines.append("")

    # Vote leaderboard
    win_counts  = defaultdict(int)
    disq_counts = defaultdict(int)
    for r in results.values():
        w = r.get("winner_model")
        if w: win_counts[w] += 1
        for d in r.get("disqualified", []): disq_counts[d] += 1

    lines += [
        "---", "",
        "## Weighted Vote Totals",
        "",
        "| Model | Weighted Score Total | Times Won | Times Disqualified |",
        "|-------|--------------------|-----------|--------------------|",
    ]
    for name in model_names:
        lines.append(
            f"| {name} | {vote_totals.get(name, 0):.2f} "
            f"| {win_counts.get(name, 0)} "
            f"| {disq_counts.get(name, 0)} |"
        )
    lines.append("")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    global CHECKPOINT_JSON_PATH
    cfg = CONFIG

    active_models = [m for m in cfg["models"] if m.get("path")]
    if len(active_models) < 2:
        print("❌ At least 2 model paths required.")
        sys.exit(1)

    model_names = [m["name"] for m in active_models]
    output_path = Path(cfg["output_path"])
    CHECKPOINT_JSON_PATH = str(output_path.with_suffix(".checkpoint.json"))

    for p_key in ("questions_path",):
        if not Path(cfg[p_key]).exists():
            print(f"❌ Not found: {cfg[p_key]}")
            sys.exit(1)
    for m in active_models:
        if not Path(m["path"]).exists():
            print(f"❌ Model not found for '{m['name']}': {m['path']}")
            sys.exit(1)

    print("=" * 68)
    print("  GGUF Multi-Model Ensemble Benchmark  —  v3 (Weighted Voting)")
    print("=" * 68)

    # ── Load weights ─────────────────────────────────────────────────────
    print("\n⚖️  Loading model weights …")
    model_weights = {}
    for m in active_models:
        model_weights[m["name"]] = load_model_weights(m)

    # Print weight matrix
    print("\n   Weight matrix:")
    print(f"   {'Model':<20}  {'Factual':>8}  {'Reasoning':>10}  {'Coding':>8}")
    for name in model_names:
        w = model_weights[name]
        print(f"   {name:<20}  {w['Factual']:>8.3f}  {w['Reasoning']:>10.3f}  {w['Coding']:>8.3f}")

    # ── Parse questions ──────────────────────────────────────────────────
    print("\n📂 Parsing questions …")
    all_questions = parse_questions(cfg["questions_path"])
    if not all_questions:
        print("❌ No questions found.")
        sys.exit(1)
    if cfg["question_filter"] is not None:
        idx           = list(cfg["question_filter"])
        all_questions = [all_questions[i] for i in idx if i < len(all_questions)]
    print(f"   Found {len(all_questions)} questions.")

    # ── Checkpoint ───────────────────────────────────────────────────────
    checkpoint  = load_checkpoint_json()
    results     = checkpoint.get("results", {})
    vote_totals = defaultdict(float, checkpoint.get("vote_totals", {}))

    remaining = [q for q in all_questions if q["qid"] not in results]
    if not remaining:
        print("\n✅ All questions already answered.")
        save_output(str(output_path), results, model_names, vote_totals,
                    all_questions, model_weights)
        sys.exit(0)
    if results:
        print(f"♻️  Resuming: {len(results)} done, {len(remaining)} remaining.")

    # ── Load models ──────────────────────────────────────────────────────
    print(f"\n🔄 Loading {len(active_models)} models …")
    llms = []
    for m in active_models:
        print(f"   {m['name']} ({m['prompt_format']}) …", end=" ", flush=True)
        t0 = time.time()
        try:
            llm = Llama(
                model_path=m["path"],
                n_gpu_layers=cfg["n_gpu_layers"],
                n_ctx=cfg["n_ctx"],
                n_batch=cfg["n_batch"],
                verbose=False,
            )
            llms.append(llm)
            print(f"✅ ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"\n❌ Failed: {e}\n   Try lowering n_gpu_layers.")
            sys.exit(1)

    # ── Pipeline ─────────────────────────────────────────────────────────
    ans_sys   = cfg["answer_system_prompt"]
    rev_sys   = cfg["review_system_prompt"]
    max_tok   = cfg["max_tokens"]
    rev_tok   = cfg["review_max_tokens"]
    temp      = cfg["temperature"]
    rep_pen   = cfg["repeat_penalty"]
    chk_every = cfg["checkpoint_every"]

    print(f"\n🚀 Running weighted ensemble on {len(remaining)} questions …")
    print("   Phase 1: all models answer")
    print("   Phase 2: peer review  (scores × reviewer section weight)")
    print("   Phase 3: weighted tally → pick winner\n")

    total_errors = 0
    start_time   = time.time()

    for i, q in enumerate(tqdm(remaining, desc="Questions", unit="q")):
        qid     = q["qid"]
        text    = q["text"]
        section = get_section(qid)

        # ── Phase 1: Answers ─────────────────────────────────────────────
        answers      = {}
        disqualified = set()

        for m, llm in zip(active_models, llms):
            name   = m["name"]
            prompt = format_prompt(text, ans_sys, m["prompt_format"])
            try:
                ans = run_inference(llm, prompt, max_tok, temp, rep_pen)
            except Exception as e:
                ans = f"(inference error: {e})"
                total_errors += 1

            answers[name] = ans
            if is_broken(ans):
                disqualified.add(name)
                tqdm.write(f"  🚫 {qid} [{name}] disqualified (system-prompt leak)")

        # ── Phase 2: Peer review ─────────────────────────────────────────
        # raw_peer_scores[answerer][reviewer] = raw 1-5 score
        raw_peer_scores = defaultdict(dict)

        for reviewer_m, reviewer_llm in zip(active_models, llms):
            rname = reviewer_m["name"]
            rfmt  = reviewer_m["prompt_format"]

            for aname, answer in answers.items():
                if aname == rname: continue   # no self-review
                if aname in disqualified:
                    raw_peer_scores[aname][rname] = 0
                    continue
                rev_prompt = format_review_prompt(text, answer, rev_sys, rfmt)
                try:
                    raw   = run_inference(reviewer_llm, rev_prompt, rev_tok, 0.0, 1.0)
                    score = extract_score(raw)
                except Exception as e:
                    score = 3
                    total_errors += 1
                raw_peer_scores[aname][rname] = score

        # ── Phase 3: Weighted tally ──────────────────────────────────────
        weighted_scores = compute_weighted_scores(
            raw_peer_scores, model_weights, section, disqualified
        )

        # Also track raw (unweighted) totals for comparison
        raw_totals = {
            name: sum(raw_peer_scores[name].values())
            for name in model_names
        }

        winner = pick_winner(weighted_scores, answers, disqualified)

        for name in model_names:
            vote_totals[name] += weighted_scores.get(name, 0.0)

        results[qid] = {
            "section":                section,
            "answers":                answers,
            "raw_peer_scores":        {k: dict(v) for k, v in raw_peer_scores.items()},
            "raw_scores":             weighted_scores,   # weighted totals
            "raw_unweighted":         raw_totals,
            "disqualified":           list(disqualified),
            "winner_model":           winner,
            "winner_answer":          answers[winner],
            "winner_weighted_score":  weighted_scores.get(winner, 0.0),
        }

        tqdm.write(
            f"  {qid} [{section[0]}]  winner={winner} "
            f"(w={weighted_scores.get(winner,0):.2f})  |  "
            + "  ".join(
                f"{n}:{weighted_scores.get(n,0):.2f}(raw:{raw_totals.get(n,0)})"
                for n in model_names
            )
        )

        if (i + 1) % chk_every == 0:
            save_checkpoint_json({"results": results, "vote_totals": dict(vote_totals)})
            save_output(str(output_path), results, model_names, vote_totals,
                        all_questions, model_weights)
            tqdm.write(f"  💾 Checkpoint ({len(results)}/{len(all_questions)})")

    # ── Final save ───────────────────────────────────────────────────────
    save_checkpoint_json({"results": results, "vote_totals": dict(vote_totals)})
    save_output(str(output_path), results, model_names, vote_totals,
                all_questions, model_weights)

    elapsed = time.time() - start_time
    per_q   = elapsed / len(remaining) if remaining else 0

    win_counts  = defaultdict(int)
    disq_counts = defaultdict(int)
    for r in results.values():
        win_counts[r.get("winner_model")] += 1
        for d in r.get("disqualified", []): disq_counts[d] += 1

    print("\n" + "=" * 68)
    print(f"  ✅ Done!  {len(results)} questions  |  {elapsed:.0f}s ({per_q:.1f}s/q)  |  {total_errors} errors")
    print()
    print(f"  {'Model':<20}  {'W.Score':>9}  {'Wins':>5}  {'Disq':>5}  Weights (F/R/C)")
    print(f"  {'-'*20}  {'-'*9}  {'-'*5}  {'-'*5}  ---------------")
    max_v = max(vote_totals.values()) if vote_totals else 1
    for name in model_names:
        w = model_weights[name]
        print(
            f"  {name:<20}  {vote_totals[name]:>9.2f}  {win_counts[name]:>5}  "
            f"{disq_counts[name]:>5}  "
            f"{w['Factual']:.2f}/{w['Reasoning']:.2f}/{w['Coding']:.2f}"
        )
    print("=" * 68)
    print(f"\n  Output: {output_path}")

    if any(disq_counts.values()):
        worst = max(disq_counts, key=disq_counts.get)
        print(f"\n  ⚠️  {worst} disqualified most often ({disq_counts[worst]}x) — check its prompt_format.")


if __name__ == "__main__":
    main()