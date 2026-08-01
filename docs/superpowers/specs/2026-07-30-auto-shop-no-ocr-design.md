# Auto Shop no-OCR Design

## Objective

Replace the current per-item image search and stock OCR flow with a fast,
deterministic Gold Shop sweep. The automation must make no OCR calls and must
never issue a purchase click unless it has verified both the shop layout and
the item card that owns the click target.

This design applies to the existing Gold Shop only. Future shops can add their
own layout manifest without changing the Gold Shop rules.

## Confirmed purchase semantics

- A numeric target `N` means: attempt to buy up to `N` units once per safe
  Auto Shop pass. The item remains scheduled until the card shows `Out of
  Stock` or `Max Inventory`. The game may clamp the entered value to the
  remaining stock.
- `Max` means: select the modal's Max action and purchase the remaining stock
  once during the current UTC day.
- Manual purchases are intentionally not subtracted from numeric targets. No
  screen number is read to infer them.
- A closed purchase modal after the macro clicked the final Buy is sufficient
  confirmation that the current purchase attempt was accepted.
- The UI must describe a successful numeric action as executed and still
  scheduled, not as an exact total owned or an exact number confirmed from
  stock.

This deliberately trades exact manual-purchase reconciliation for speed and
predictability. Without reading a game number, it is impossible to calculate
the difference between a daily target and manual purchases.

## Gold Shop layout manifest

The docked game uses the normalized 1152x756 reference viewport already used
by `core.vision`. All card and button rectangles remain in that coordinate
space, then existing coordinate conversion maps clicks to the actual window.

The Gold Shop is treated as six deterministic positions in the normalized
1152x756 viewport. Each due position first resets to Top and then applies one
calibrated wheel delta. A failed identity check never causes additional search
scrolling:

| Position | Scroll from Top | Fully actionable cards |
| --- | ---: | --- |
| Top | 0 | Cursed Boba, Red Flower |
| Row 2 | -120 | Frown Fruit, Delicious Pie |
| Row 3 | -480 | Mana Flask, Trait Crystal |
| Row 4 | -720 | Sprite (Grey), Equipment Reroll |
| Row 5 | -960 | Equipment Lock, Stat Reroll |
| Absolute bottom | -4800 | Stat Lock |

Every entry has a fixed card slot within its verified position. A slot holds:

- the expected item key;
- a bounded identity region;
- the initial Buy region;
- the terminal-label region;
- a requirement that the Buy region is fully inside the list viewport.

The identity region is an RGBA-masked reference made from the stable card
parts: item icon, item name, and fixed border details. It excludes the stock
label, dynamic price, button state, and animated card areas. A literal full
card screenshot is not used because those dynamic parts would make it fail
after an item sells out or changes state.

Existing reference screenshots can supply these masked identity references;
new manual screenshots are optional, not a prerequisite.

## Session and alignment flow

1. Navigate to Areas, Shop, Gold Shop, tilt the camera, press E, and select
   the Gold Shop tab as today.
2. For each position containing a due item, reset the list upward and apply
   exactly the position's mapped wheel delta.
3. Search each item only inside its fixed left or right column. Require the
   derived Buy and terminal regions to be fully visible before processing it.
4. If an expected item is absent, skip it without issuing any additional
   scroll. Continue from a fresh Top reset for the next due position.
5. Stat Lock always uses the absolute-bottom delta. Confirm its identity in
   the left slot and require its Buy region to be fully visible.

Stat Lock has a stricter bottom rule than the other items. Its card identity
alone is insufficient: the list must be at its physical end and its complete
Buy area must be visible. Otherwise the macro stops the Bottom pass without
clicking it.

If a sentinel or expected card does not match after the bounded alignment
attempt, the entire affected position is skipped. The macro never guesses a
slot or reuses coordinates from another list position.

## Per-card purchase flow

For each enabled, identity-confirmed slot:

1. Search the terminal-label region for `Out of Stock` and `Max Inventory`.
   Either label completes the item for the UTC day without opening a modal.
2. Inspect the known Buy rectangle by its green button-face pixel fraction.
   This is a color and geometry check, not a price-template search.
3. If the region is green, click its calculated center and wait briefly for
   the Cancel anchor in the purchase modal.
4. If the modal opens:
   - for `Max`, click the Max control derived from Cancel, then immediately
     click the final Buy control derived from Cancel;
   - for a numeric target, focus the input derived from Cancel, enter the
     number, then click the final Buy control.
5. Treat disappearance of Cancel after the final Buy as success. Complete a
   `Max` target for the day; keep a numeric target pending for the next safe
   pass. Do not reread the card or scan stock.
6. If Cancel remains, click the required far-right point inside Cancel and
   mark the item Failed Today with a Reset Today action. It must not receive
   another automatic Buy click in the same UTC day without a user reset.

A non-green Buy rectangle without a terminal label is not interpreted as Out
of Stock. It can mean insufficient Gold or an unexpected render. The item is
left actionable for a future safe pass, with no purchase recorded and no
dangerous retry in the current shop visit.

## Safety invariants

- No OCR function is imported or called by the Gold Shop runtime path.
- At most one final Buy click is issued for an item in one shop visit. `Max`
  remains limited to one confirmed purchase per UTC period.
- An ambiguous card identity, scroll position, button state, or modal state
  always results in no Buy click.
- The macro resets to Top before every due position and never searches by
  repeatedly scrolling after a failed identity check.
- Buy coordinates are valid only for the current verified slot and are never
  carried across a scroll.
- Disabled Buy, missing modal, and unknown layout states never become an
  Out-of-Stock result by inference.

## Expected performance

The common nine-item run uses at most one Top reset plus one mapped wheel delta
per due position and one modal interaction per purchasable item. It has no
search-scroll loop, so missing items add no repeated delays. Runtime should be
dominated by Roblox's modal animation rather than visual search or OCR.

## Implementation boundaries

- `core/runner_shop.py` owns the fixed position and column manifest. It resets
  once per due position and performs only the mapped scroll before validating
  the current card slots.
- `core/auto_shop_vision.py` owns fixed slot geometry, masked identity checks,
  green-button classification, viewport-change detection, and modal-relative
  geometry.
- `core/auto_shop.py` owns the no-OCR purchase state semantics and UTC reset
  normalization.
- The pywebview settings bridge and Resource UI preserve existing enabled and
  target settings. They only change labels/statuses needed to distinguish
  executed today, terminal labels, blocked, and uncertain actions.

No generic shop abstraction or unrelated runner refactor is included.

## Verification

- Unit tests prove that the runtime flow cannot call stock OCR.
- Offline visual tests cover each top, middle, and bottom slot using the
  supplied Gold Shop screenshots, including terminal labels and dynamic Buy
  prices.
- Alignment tests prove that an unmatched sentinel causes no click.
- A bottom-alignment test proves Stat Lock is not processed until both the
  viewport is stationary at the end and its full Buy region is visible.
- Modal tests cover numeric, Max, successful closing, and non-closing Cancel
  recovery.
- Tests cover a disabled Buy with no terminal label and confirm it never
  becomes an Out-of-Stock completion.
- Run Ruff, the full pytest suite, Python compilation, and `node --check
  ui/app.js` before a live run.
- The first live validation uses one cheap numeric item, then one Max item,
  then a Bottom run containing Stat Lock.
