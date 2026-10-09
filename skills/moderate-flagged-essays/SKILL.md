---
name: moderate-flagged-essays
description: Help a human marker review essays the marker flagged (borderline marks, sample disagreement, unverified quotes, word limits, possible prompt injection, integrity concerns), and record their decisions. Use when the user asks which essays need checking, wants a moderation list, or is reviewing flagged results.
---

# Moderate flagged essays

Flagged essays (`needs_review = yes` in `summary.csv`) need a human decision.
Your job is to make that review fast and well-informed, not to make the decision.

## Steps

1. List the flagged essays from `results/<batch>/summary.csv`. Order them by
   severity: possible prompt injection or integrity concerns first, then
   sample disagreement, unverified evidence, borderline marks, and word limits.
2. For each essay, read `results/<batch>/<id>.json` and summarise:
   - the final mark and band, and each criterion's median and `sample_marks`;
   - what each flag means in practice:
     - **borderline**: within 2 marks below a classification boundary.
       Human judgement decides which side of the boundary it falls.
     - **samples disagree**: the model's judgements varied, so it is less certain.
     - **evidence not found**: the model quoted text that isn't in the essay,
       so treat that criterion's justification with suspicion.
     - **possible prompt injection**: show the snippet. Check whether it is a
       real attempt to manipulate the marker or an innocent phrase.
     - **integrity concerns**: list them. These are for a human to
       investigate under the university's academic-integrity procedure, not
       accusations.
   - the passages of the essay the reviewer should read.
3. Record the human's decision in `results/<batch>/moderation.csv` with columns
   `id,machine_mark,final_mark,moderator,date,reason`. Append rows; never
   edit the machine result JSON.

## Rules

- Never describe a flag as proof of misconduct.
- The human moderator's mark is final. Don't argue for the machine mark;
  explain the evidence for both readings when asked.
