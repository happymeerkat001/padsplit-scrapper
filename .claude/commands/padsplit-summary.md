---
description: Run PadSplit Slack summaries (urgent messages + tasks/weather digest)
---

Load `.env` from project root, then run both scripts in order with the project venv:

```bash
set -a && source .env && set +a
./venv/bin/python3 message_summarizer.py
./venv/bin/python3 slack_task_digest.py
```

- `message_summarizer.py` sends latest messages to MiniMax AI, posts urgent summary to `SLACK_WEBHOOK_MESSAGES`.
- `slack_task_digest.py` fetches DFW weather + formats tasks digest, posts to `SLACK_WEBHOOK_TASKS`. Weather only included during 5-9 CT.

Print stdout/stderr verbatim. Do not reformat. Report non-zero exits with exact error output.
