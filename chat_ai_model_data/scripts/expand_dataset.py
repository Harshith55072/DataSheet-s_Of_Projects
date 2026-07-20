"""
Expands the small hand-written seed files (raw/seed_chat.jsonl, superchat/seed_superchats.jsonl)
into a larger synthetic dataset via template slot-filling.

This is NOT a replacement for real chat data (lparkourer10/twitch_chat) -- it's meant to:
  1. Give you something to train/debug your pipeline on immediately.
  2. Boost the superchat class, since real superchat-labeled data is scarce.

Usage:
    python scripts/expand_dataset.py --chat_out processed/chat_expanded.jsonl \
                                      --superchat_out processed/superchat_expanded.jsonl \
                                      --chat_n 5000 --superchat_n 1500
"""

import json
import random
import argparse

# ---- building blocks for template expansion ----

HYPE_WORDS = ["LMAOOO", "POGGERS", "let's gooo", "W stream", "goated", "based",
              "im dead", "no way", "she's cooking", "clip it", "peak content",
              "certified classic", "chat is this real", "im losing it"]

REACTIONS = ["💀", "😭", "🔥", "😳", "👀", ""]

SUBJECTS = ["that game", "this arc", "the new update", "her reaction", "that clip",
            "the boss fight", "that jumpscare", "the collab", "this build",
            "her setup", "the new outfit", "that comeback"]

SUPERCHAT_TEMPLATES = [
    "what's your favorite {thing} right now?",
    "have you ever tried {activity}?",
    "how long have you been {activity_ing}?",
    "what got you into {topic} in the first place?",
    "any tips for someone just starting {activity_ing}?",
    "what's the hardest {thing} you've dealt with on stream?",
    "do you have a favorite moment from {topic} so far?",
    "if you could collab with anyone who would it be?",
    "what's something you wish more people asked about {topic}?",
    "how do you deal with {challenge} in chat?",
    "what keeps you motivated to keep {activity_ing}?",
    "quick question, what's your favorite {thing} while streaming?",
]

THINGS = ["game to speedrun", "snack", "song to listen to while playing", "boss fight",
          "genre to stream", "way to unwind after a stream"]
ACTIVITIES = ["playing with a controller", "co-op games", "horror games", "speedrunning",
              "modding your setup", "streaming variety games"]
ACTIVITY_ING = ["streaming", "vtubing", "playing this game", "doing collabs", "editing your own clips"]
TOPICS = ["vtubing", "this game series", "streaming", "content creation", "this community"]
CHALLENGES = ["toxic comments", "spam", "trolls", "backseat gaming"]


def gen_chat(n, seed_examples, persona_pool_size=20):
    out = []
    for _ in range(n):
        r = random.random()
        if r < 0.5:
            text = random.choice(HYPE_WORDS)
        elif r < 0.8:
            text = f"{random.choice(HYPE_WORDS)} {random.choice(REACTIONS)}".strip()
        else:
            text = f"wait {random.choice(SUBJECTS)} was insane {random.choice(REACTIONS)}".strip()
        out.append({
            "mode": "chat",
            "text": text,
            "persona": random.randint(1, persona_pool_size)
        })
    # fold in the hand-written seeds too
    out.extend(seed_examples)
    random.shuffle(out)
    return out


def gen_superchat(n):
    out = []
    tiers = ["low", "mid", "high"]
    for _ in range(n):
        tmpl = random.choice(SUPERCHAT_TEMPLATES)
        text = tmpl.format(
            thing=random.choice(THINGS),
            activity=random.choice(ACTIVITIES),
            activity_ing=random.choice(ACTIVITY_ING),
            topic=random.choice(TOPICS),
            challenge=random.choice(CHALLENGES),
        )
        out.append({
            "mode": "superchat",
            "text": text,
            "amount_tier": random.choice(tiers)
        })
    return out


def load_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat_seed", default="raw/seed_chat.jsonl")
    ap.add_argument("--superchat_seed", default="superchat/seed_superchats.jsonl")
    ap.add_argument("--chat_out", default="processed/chat_expanded.jsonl")
    ap.add_argument("--superchat_out", default="processed/superchat_expanded.jsonl")
    ap.add_argument("--chat_n", type=int, default=5000)
    ap.add_argument("--superchat_n", type=int, default=1500)
    args = ap.parse_args()

    chat_seeds = load_jsonl(args.chat_seed)
    superchat_seeds = load_jsonl(args.superchat_seed)

    chat_rows = gen_chat(args.chat_n, chat_seeds)
    superchat_rows = gen_superchat(args.superchat_n) + superchat_seeds

    write_jsonl(args.chat_out, chat_rows)
    write_jsonl(args.superchat_out, superchat_rows)

    print(f"Wrote {len(chat_rows)} chat rows -> {args.chat_out}")
    print(f"Wrote {len(superchat_rows)} superchat rows -> {args.superchat_out}")
