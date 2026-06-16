#!/usr/bin/env python3
"""
GGUF Model Benchmark Pipeline
==============================
Loads a .gguf model onto GPU, feeds it every question from questions.md,
and saves the answers to a modelname_answers.md file.

Usage:
    python run_benchmark.py

Then edit the CONFIG block below before running.
"""

# ═══════════════════════════════════════════════════════════════════════════
#  ██████╗ ██████╗ ███╗   ██╗███████╗██╗ ██████╗
# ██╔════╝██╔═══██╗████╗  ██║██╔════╝██║██╔════╝
# ██║     ██║   ██║██╔██╗ ██║█████╗  ██║██║  ███╗
# ██║     ██║   ██║██║╚██╗██║██╔══╝  ██║██║   ██║
# ╚██████╗╚██████╔╝██║ ╚████║██║     ██║╚██████╔╝
#  ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝     ╚═╝ ╚═════╝
# ═══════════════════════════════════════════════════════════════════════════

CONFIG = {
    # Full path to your .gguf model file
    "model_path": r"C:\Users\Lenovo\Documents\programing\NeuralDocker-Selective\models\unsloth.Q4_K_M.gguf",

    # Full path to your questions.md file
    "questions_path": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\questions.md",

    # Full path + filename for the output answers file
    "output_path": r"C:\Users\Lenovo\Documents\programing\data\AI_models_Evaluation_data\Final_data\unsloth.Q4_K_M_ANSWERS.md",

    # ── GPU Settings ──────────────────────────────────────────────────────
    # Number of model layers to offload to GPU.
    # -1 = offload ALL layers (max VRAM usage, fastest)
    #  0 = CPU only
    # 20 = offload 20 layers (use if you get VRAM OOM errors)
    "n_gpu_layers": -1,

    # ── Context & Generation ─────────────────────────────────────────────
    # Context window size (tokens). 2048 is safe; 4096 if your model supports it.
    "n_ctx": 2048,

    # Max tokens the model can generate per answer
    "max_tokens": 512,

    # Temperature (0.0 = deterministic, good for factual benchmarks)
    "temperature": 0.0,

    # Repeat penalty to reduce loops
    "repeat_penalty": 1.1,

    # ── Batch size ────────────────────────────────────────────────────────
    # Tokens processed in parallel during prompt evaluation.
    # Higher = faster but more VRAM. 512 is a safe default.
    "n_batch": 512,

    # ── Prompt format ─────────────────────────────────────────────────────
    # Change this to match your model's expected chat template.
    # Options: "alpaca" | "chatml" | "llama2" | "vicuna" | "plain"
    # nous-capybara uses chatml format
    "prompt_format": "chatml",

    # System message injected before each question
    "system_prompt": "You are a helpful and knowledgeable assistant. Answer the question accurately and concisely.",

    # ── Progress ──────────────────────────────────────────────────────────
    # Save progress every N questions (in case of crash, resume from checkpoint)
    "checkpoint_every": 10,

    # ── Filter (optional) ─────────────────────────────────────────────────
    # Set to None to run all 300. Or e.g. range(0, 100) for first 100 only.
    # Or a list: [0, 1, 2, 42] for specific indices.
    "question_filter": None,
}

# ═══════════════════════════════════════════════════════════════════════════
# (nothing below this line needs to be edited)
# ═══════════════════════════════════════════════════════════════════════════

import re
import sys
import time
import json
from pathlib import Path
from datetime import datetime


# ─── Dependency check ───────────────────────────────────────────────────────

def check_dependencies():
    missing = []
    try:
        from llama_cpp import Llama  # noqa: F401
    except ImportError:
        missing.append("llama-cpp-python")
    try:
        from tqdm import tqdm  # noqa: F401
    except ImportError:
        missing.append("tqdm")

    if missing:
        print("❌ Missing required packages. Install them with:\n")
        for pkg in missing:
            if pkg == "llama-cpp-python":
                print("  # For GPU (CUDA) support:")
                print("  pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121")
                print("\n  # For CPU only:")
                print("  pip install llama-cpp-python")
            else:
                print(f"  pip install {pkg}")
        sys.exit(1)

check_dependencies()

from llama_cpp import Llama
from tqdm import tqdm


# ─── Prompt formatting ──────────────────────────────────────────────────────

def format_prompt(question: str, system: str, fmt: str) -> str:
    """Format a question into the model's expected prompt template."""
    if fmt == "chatml":
        return (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{question}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
    elif fmt == "alpaca":
        return (
            f"### System:\n{system}\n\n"
            f"### Instruction:\n{question}\n\n"
            f"### Response:\n"
        )
    elif fmt == "llama2":
        return (
            f"[INST] <<SYS>>\n{system}\n<</SYS>>\n\n"
            f"{question} [/INST]"
        )
    elif fmt == "vicuna":
        return (
            f"SYSTEM: {system}\n\n"
            f"USER: {question}\n"
            f"ASSISTANT:"
        )
    elif fmt == "plain":
        return f"{system}\n\nQ: {question}\nA:"
    else:
        raise ValueError(f"Unknown prompt format: {fmt}. Use: chatml, alpaca, llama2, vicuna, plain")


# ─── Question parser ────────────────────────────────────────────────────────

def parse_questions(filepath: str) -> list[dict]:
    """
    Parse questions.md and return a list of:
      {"qid": "Q001", "text": "What is the chemical symbol for gold?"}
    """
    text = Path(filepath).read_text(encoding="utf-8")
    pattern = re.compile(
        r'\*\*(Q(\d{3}))\.\*\*\s*(.*?)(?=\*\*Q\d{3}\.\*\*|\Z)',
        re.DOTALL
    )
    questions = []
    for match in pattern.finditer(text):
        qid = match.group(1)
        raw_text = match.group(3).strip()
        # Strip trailing section headers (lines starting with ---)
        raw_text = re.sub(r'\n---.*$', '', raw_text, flags=re.DOTALL).strip()
        questions.append({"qid": qid, "text": raw_text})

    return questions


# ─── Checkpoint helpers ─────────────────────────────────────────────────────

def load_checkpoint(output_path: str) -> dict[str, str]:
    """Load already-answered questions from a partial output file."""
    p = Path(output_path)
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    pattern = re.compile(r'\*\*(Q\d{3})\.\*\*\s*(.*?)(?=\*\*Q\d{3}\.\*\*|\Z)', re.DOTALL)
    done = {}
    for match in pattern.finditer(text):
        done[match.group(1)] = match.group(2).strip()
    return done


def save_answers(output_path: str, answers: dict[str, str], model_name: str):
    """Write all collected answers to the output markdown file."""
    lines = [
        f"# Benchmark Answers — {model_name}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Model path: `{CONFIG['model_path']}`",
        "",
        "---",
        ""
    ]
    for qid in sorted(answers.keys()):
        answer = answers[qid].strip()
        lines.append(f"**{qid}.** {answer}")
        lines.append("")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


# ─── Main pipeline ──────────────────────────────────────────────────────────

def main():
    cfg = CONFIG

    # ── Validate paths ──────────────────────────────────────────────────
    model_path = Path(cfg["model_path"])
    questions_path = Path(cfg["questions_path"])
    output_path = Path(cfg["output_path"])

    if not model_path.exists():
        print(f"❌ Model file not found:\n   {model_path}")
        sys.exit(1)
    if not questions_path.exists():
        print(f"❌ Questions file not found:\n   {questions_path}")
        sys.exit(1)

    model_name = model_path.stem   # e.g. "nous-capybara-7b.Q3_K_L"

    print("=" * 60)
    print(f"  GGUF Benchmark Pipeline")
    print("=" * 60)
    print(f"  Model     : {model_name}")
    print(f"  Questions : {questions_path}")
    print(f"  Output    : {output_path}")
    print(f"  GPU layers: {cfg['n_gpu_layers']} ({'all' if cfg['n_gpu_layers'] == -1 else cfg['n_gpu_layers']})")
    print(f"  Format    : {cfg['prompt_format']}")
    print("=" * 60)

    # ── Load questions ──────────────────────────────────────────────────
    print("\n📂 Parsing questions …")
    all_questions = parse_questions(str(questions_path))
    if not all_questions:
        print("❌ No questions found. Make sure the file uses **Q001.** format.")
        sys.exit(1)
    print(f"   Found {len(all_questions)} questions.")

    # Apply filter
    if cfg["question_filter"] is not None:
        indices = list(cfg["question_filter"])
        all_questions = [all_questions[i] for i in indices if i < len(all_questions)]
        print(f"   Filtered to {len(all_questions)} questions.")

    # ── Load checkpoint ─────────────────────────────────────────────────
    already_done = load_checkpoint(str(output_path))
    if already_done:
        print(f"\n♻️  Resuming from checkpoint: {len(already_done)} questions already answered.")
    answers = dict(already_done)

    remaining = [q for q in all_questions if q["qid"] not in already_done]
    if not remaining:
        print("\n✅ All questions already answered. Nothing to do.")
        sys.exit(0)

    # ── Load model ──────────────────────────────────────────────────────
    print(f"\n🔄 Loading model onto GPU (n_gpu_layers={cfg['n_gpu_layers']}) …")
    print("   (This may take 10–60 seconds depending on model size)")
    load_start = time.time()

    try:
        llm = Llama(
            model_path=str(model_path),
            n_gpu_layers=cfg["n_gpu_layers"],
            n_ctx=cfg["n_ctx"],
            n_batch=cfg["n_batch"],
            verbose=False,          # set True for llama.cpp debug output
        )
    except Exception as e:
        print(f"\n❌ Failed to load model: {e}")
        print("\nTroubleshooting:")
        print("  • VRAM OOM → lower n_gpu_layers (e.g. 20, 15, 10)")
        print("  • CUDA error → make sure llama-cpp-python was installed with CUDA support")
        print("  • File error → double-check model_path in CONFIG")
        sys.exit(1)

    load_time = time.time() - load_start
    print(f"   ✅ Model loaded in {load_time:.1f}s")

    # ── Run inference ────────────────────────────────────────────────────
    print(f"\n🚀 Running inference on {len(remaining)} questions …\n")

    errors = []
    start_time = time.time()

    for i, q in enumerate(tqdm(remaining, desc="Answering", unit="q")):
        qid = q["qid"]
        question_text = q["text"]

        prompt = format_prompt(
            question=question_text,
            system=cfg["system_prompt"],
            fmt=cfg["prompt_format"]
        )

        try:
            result = llm(
                prompt,
                max_tokens=cfg["max_tokens"],
                temperature=cfg["temperature"],
                repeat_penalty=cfg["repeat_penalty"],
                stop=["<|im_end|>", "[INST]", "###", "\n\nQ:", "User:", "USER:"],
                echo=False,
            )
            answer = result["choices"][0]["text"].strip()

            # Clean up common artifacts
            answer = re.sub(r'^(Assistant:|ASSISTANT:|A:)\s*', '', answer).strip()
            answer = answer.split("<|im_start|>")[0].strip()   # chatml leak guard

            if not answer:
                answer = "(no answer generated)"

        except Exception as e:
            answer = f"(inference error: {e})"
            errors.append(qid)
            tqdm.write(f"  ⚠️  {qid} failed: {e}")

        answers[qid] = answer

        # Save checkpoint every N questions
        if (i + 1) % cfg["checkpoint_every"] == 0:
            save_answers(str(output_path), answers, model_name)
            tqdm.write(f"  💾 Checkpoint saved ({len(answers)}/{len(all_questions)} done)")

    # ── Final save ───────────────────────────────────────────────────────
    save_answers(str(output_path), answers, model_name)

    elapsed = time.time() - start_time
    per_q   = elapsed / len(remaining) if remaining else 0

    print("\n" + "=" * 60)
    print(f"  ✅ Done!")
    print(f"  Questions answered : {len(answers)}")
    print(f"  Errors             : {len(errors)}")
    print(f"  Time elapsed       : {elapsed:.0f}s ({per_q:.1f}s/question)")
    print(f"  Output saved to    : {output_path}")
    if errors:
        print(f"\n  ⚠️  Failed QIDs: {', '.join(errors)}")
    print("=" * 60)

    # ── Print stats ──────────────────────────────────────────────────────
    print("\n📊 Quick peek at first 5 answers:")
    for q in all_questions[:5]:
        ans = answers.get(q["qid"], "(missing)")
        print(f"  {q['qid']}: {ans[:80]}{'…' if len(ans) > 80 else ''}")

    print(f"\n➡️  Next step: run the grader!")
    print(f"   python grader.py --reference answers.md \\")
    print(f"                    --model-output \"{output_path}\" \\")
    print(f"                    --model-name \"{model_name}\"")


if __name__ == "__main__":
    main()