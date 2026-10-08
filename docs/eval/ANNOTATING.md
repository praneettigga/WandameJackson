# Annotating real plans for evaluation

Synthetic scores flatter the parser, because the same team wrote the generator. Judges bring unseen
plans, so the numbers worth quoting come from real plans whose ground truth was checked by a person.

## Make a ground-truth pair (about 5 minutes per plan)

1. Start the API and the editor (see the root README), then upload the plan.
2. Choose **Manual reference** and mark a dimension you know: a printed dimension line, or a measured
   wall. Use the longest reliable one; the scale error carries into every metric.
3. Reconstruct. Then correct the result in **Edit**:
   - Delete wrong walls (`Delete`). Draw missing ones with `W`; they snap to existing corners and walls.
   - Drag corners onto the drawing's wall centres. Zoom in: snapping becomes finer as you zoom.
   - Fix doors and windows: delete wrong ones, place missing ones with `D` / `N`, and drag them into position.
   - Clear the **completeness** checks in the review dock, or confirm each one is genuinely in the drawing.
4. Click **↓ GT pair**. It downloads `<project>.png|jpg` and `<project>.scene.json`.
5. Put both files in `services/api/data/gt/` (git-ignored). Optionally rename them to a readable stem,
   keeping the two names matching.

## Evaluate

```bash
cd services/api
python -m eval.run --set real --real-dir data/gt             # our parser
python -m eval.run --set real --real-dir data/gt --ablation  # stage ablations on real plans
python -m eval.run --set real --real-dir data/gt --parser both   # + CubiCasa5K baseline and fusion
```

The parser runs with the calibration stored in each ground-truth scene. Every system therefore gets the same
metres/pixel, and differences come from layout recognition, not from scale.

## Rules that keep it honest

- Annotate from the drawing, not from the reconstruction: anything you leave uncorrected inflates our scores.
- Keep plans you used to tune parameters separate from the plans you report (`data/gt/dev` vs `data/gt/test`).
- Report the number of plans and where they came from next to every result.
