# chat_ai_model_data

Data pipeline for the small "audience chat simulator" model (regular chat spam + rare superchat questions).

## Folder structure
```
raw/                 real + seed raw chat data (jsonl)
processed/           cleaned/expanded jsonl ready for tokenizer training
superchat/           seed superchat examples
scripts/             data prep / expansion scripts
```

## Format
JSONL, one object per line:
```json
{"mode": "chat", "text": "LMAOOO no way", "persona": 4}
{"mode": "superchat", "text": "have you ever tried speedrunning celeste?", "amount_tier": "mid"}
```
Why JSONL and not .md: training data needs to be easy to stream line-by-line and shuffle,
and each example needs metadata (mode/persona/tier) attached. Markdown is for docs, not
training corpora.

## Getting real data (recommended, do this manually)
Download from Hugging Face (CC-BY-SA-4.0, usernames already stripped):
- https://huggingface.co/datasets/lparkourer10/twitch_chat/resolve/main/data/train-00000-of-00001.parquet
- https://huggingface.co/datasets/lparkourer10/twitch_chat/resolve/main/data/validation-00000-of-00001.parquet

Save both into `raw/`. It's a parquet file with a `message` column of raw Twitch chat text.
You'll need `pandas` + `pyarrow` to load it:
```python
import pandas as pd
df = pd.read_parquet("raw/train-00000-of-00001.parquet")
df["message"].head()
```

## Synthetic seed + expansion (what's already here)
- `raw/seed_chat.jsonl` — ~40 hand-written example chat comments across personas
- `superchat/seed_superchats.jsonl` — 20 hand-written superchat questions across amount tiers
- `scripts/expand_dataset.py` — slot-fills templates to generate thousands more synthetic
  examples from the seeds, since real superchat-labeled data is scarce. Run:
```bash
python scripts/expand_dataset.py --chat_n 5000 --superchat_n 1500
```
This writes `processed/chat_expanded.jsonl` and `processed/superchat_expanded.jsonl`.

## Next steps
1. Download the real Twitch dataset into `raw/`, convert to the same jsonl format
   (mode="chat", no persona needed yet — can assign randomly like the synthetic set does).
2. Run the expansion script for the synthetic supplement, especially for superchat.
3. Combine real + synthetic into one `processed/train.jsonl` before tokenizer training.
4. Note: synthetic examples are a bootstrap, not a substitute — the ratio of real:synthetic
   in your final chat set should be as real-heavy as possible; keep synthetic mostly for
   the superchat class where real data is thin.
