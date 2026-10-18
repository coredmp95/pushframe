---
quick_id: "261018-evx"
slug: "progress-visibility-sweep-frame-google-l"
status: "in-progress"
---
# Plan: progression visible sur listings frame + poll SQS (revue A1–A3)

## Objectif

Revue d'affichage (2026-10-18) : tout chemin > 10 s doit montrer de la vie.
Restent muets sur 500+ photos :

- **A1** `Aura.get_all_assets(frame_id)` — boucle cursor silencieuse
  (inspect cli:1607, reconcile cli:1675, sync/gsync:655, push cli:1901).
- **A2** `gsync.run_google_sync` — la sienne `enumerate_album` sans
  `progress` (le seam existe depuis 261018-eh2, voir cli:1520).
- **A3** `execute_plan` — le best-effort SQS poll par chunk
  (sync.py:880, `receive_message(wait_time_seconds=5)`) fige le postfix de
  la barre Applying pendant le poll (botte 5 s), sans tick.

## Tâches

### Task 1 — tests rouges
- files: tests/test_execute_plan.py (pas-pages tick via on_wait-like seam),
  tests/test_offline_read_path.py ou nouveau test A1 (callback par page),
  tests/test_gsync_execute.py (page lines imprimées).
- action: `get_all_assets(frame_id, progress=cb)` appelle cb(page_no, count)
  par page ; run_google_sync imprime `listing page N:` ; execute_plan passe
  un `on_sqs_tick` (optionnel, default no-op) pendant le poll.
- verify: pytest ciblé → rouge.

### Task 2 — implémentation
- files: pushframe/aura.py, pushframe/gsync.py, pushframe/sync.py,
  pushframe/cli.py (raccords inspect/reconcile/sync/push : lignes de page au
  chemin interactif uniquement, opt-in).
- verify: `uv run --extra dev pytest -q -m "not live"` → 816+ verts.
