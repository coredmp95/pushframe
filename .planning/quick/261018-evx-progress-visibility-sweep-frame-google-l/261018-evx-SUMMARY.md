---
quick_id: "261018-evx"
status: "complete"
---
# Summary: progress visibility sweep — frame/Google listing pages + SQS long-poll tick

## Review (A1–A3 du sweep d'affichage 500+ photos)

Tout chemin > 10 s doit montrer de la vie. Trois trous restants, tous corrigés
en opt-in avec rétro-compatibilité byte-identique par défaut :

- **A1** `Aura.get_all_assets(frame_id, progress=None)` — la boucle cursor
  silencieuse (inspect/sync/push/reconcile/google-sync) appelle
  `page_number, cumulative_total` par page drainée. Les verbes interactifs
  branchent `_frame_assets_page_printer()` (cli) →
  `frame assets page N (T total)`.
- **A2** `run_google_sync` passe `progress=` à `enumerate_album` → les mêmes
  `listing page N: M photos (T total)` que google-album (seam déjà en place
  depuis 261018-eh2).
- **A3** `execute_plan(..., on_poll=None)` — le best-effort SQS poll par
  chunk (`wait_time_seconds=5`) est nommé : postfix tqdm sur tty,
  tick stdout quand la barre est désactivée (runs planifiés inclus).

## Vérification

- Rouge-A2/A3 : le test `test_google_sync_listing_page_lines_and_sqs_poll_tick`
  échouait avant le wiring (pas de lignes de listing, pas de tick SQS).
- A1 : le callback est testé en drain (2 pages, `seen == [(1,1),(2,2)]`),
  fake `_FakeAura(**kwargs)` mis à jour.
- Suite complète : `pytest -q -m "not live"` → 819 passed, 8 deselected,
  0 warning, exit 0.

## Commits

- 8f38f42 — seams (get_all_assets progress, gsync listing pages, on_poll).
- 06a7775 — wiring aux verbes interactifs (inspect/reconcile/sync/google-sync).

## Notes

- Le profil d'affichage des logs planifiés gagne des lignes de listing / tick
  SQS (volontaire : le journal nocturne devient lisible).
- À servir par la prochaine release (v5.1.38), comme 261018-eh2.
