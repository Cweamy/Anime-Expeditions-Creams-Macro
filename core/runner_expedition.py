"""Expedition: the wave/checkpoint/extract loop (color-first engine + template
fallback), difficulty and map selection.

Split out of core/runner.py mechanically -- a mixin providing part of
MacroRunner's behavior (see core/runner.py, which composes the mixins).
Methods here run with MacroRunner's full self: shared state and helpers
(_log, _coords, _checkpoint, _click_found_image, ...) resolve normally.
"""
import threading
import time

from . import camera
from . import keys
from . import paths as walk_paths
from . import stage_select
from . import vision
from . import window as wm
from .runner_constants import *  # noqa: F401,F403 -- the shared constants namespace
from .runner_constants import _exp_green, _exp_green_loose, _exp_red  # underscore names, skipped by *


class ExpeditionOps:
    def _strip_auto_upgrade_for_expedition(self, blocks: list, task: dict) -> list:
        # Auto Upgrade Unit reads the unit's upgrade-cost/affordability UI to
        # decide when to click -- Expedition's version of that panel isn't
        # what it was built against, so it just spins without ever actually
        # upgrading. Rather than have it silently fail on every run, skip
        # the block entirely for Expedition tasks (Pre Start's copy of this
        # same block is skipped the same way -- see _run_prestart_blocks).
        if task.get("mode") != "expedition":
            return blocks
        filtered = [b for b in blocks if b.get("type") != "auto_upgrade_unit"]
        if len(filtered) != len(blocks):
            self._log("[Macro] Skipping Auto Upgrade Unit block(s) -- not reliable on Expedition, ignoring them.")
        return filtered

    def _check_expedition_wave_result(self, hwnd, stop_event: threading.Event) -> str:
        """Expedition doesn't show a Victory popup mid-run. exp_continue is
        every regular wave transition AND the mid-run checkpoint (the
        checkpoint is just another exp_continue, not exp_extract) -- click
        it, then continue_2 (or the checkmarked exp_extract_continue,
        whichever the game actually shows) to move on. exp_extract only
        shows up once, whenever the
        game itself decides the task's "Extract After" boss/checkpoint has
        been cleared -- there's nothing for the macro to count or decline,
        it just accepts it the moment it's seen (click exp_extract, then
        extract, landing on the reward screen -- the same terminal state
        Victory is for Story/Raid).

        A genuinely FAILED run (team/base wiped mid-wave) shows the Defeat
        result screen -- checked here every tick and returned as "loss", so
        _handle_match_result records it and, with repeats left on the task,
        clicks Repeat Stage to immediately re-run the expedition instead of
        the old behavior (nothing ever matched, the run sat there until
        MATCH_RESULT_TIMEOUT gave up ~30 min later, then burned a recovery
        attempt re-entering from the lobby). If Expedition's defeat art
        ever differs from Story/Raid's on some screen/setup, add a crop of
        it to Assets/ui/defeat/ as another variant (Settings > General >
        Image Manager) -- same search name, no code change.

        Returns "win" once extracted, "loss" on a detected defeat, or None
        either while still mid-run (the caller just keeps polling) OR when
        a click's expected follow-up never showed up -- a missing follow-up
        is a macro detection miss, not proof the run was actually lost, so
        it's deliberately NOT reported as "loss" (which would record/
        webhook a Defeat that never happened). None makes the caller treat
        it the same as any other setup failure: recover to the lobby and
        retry, with nothing false recorded."""
        # The SAME "Start Game?" confirmation _play_one_match already
        # clicks once before entering Battle can show up AGAIN mid-run --
        # confirmed from a real stuck report: exp_continue/continue_2
        # advanced to a new wave, then the run just sat there silently for
        # over a minute on an identical "Start Game?" popup that nothing
        # was checking for anymore, since this poll loop only ever watched
        # for exp_continue/exp_extract once past the initial click. One-
        # shot per tick (not the full retried version _play_one_match uses)
        # is enough here -- a missed click just gets caught on the very
        # next poll a moment later.
        start_name, start_match = self._find_start_game_button(hwnd)
        if start_match is not None:
            debug_path = self._debug_save(hwnd, start_name, start_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "{start_name}" again mid-run -- clicking it.{suffix}')
            # Same Z-deselect as the first Start Game click: mid-run this
            # popup can appear right after a Battle place/upgrade block, and
            # a still-selected unit eats the click just as readily here.
            self._keyboard.tap(ord("Z"))
            time.sleep(0.1)
            vision.click_match(self._mouse, hwnd, start_match)
            # Also clicks dead center of the screen once -- a real stuck
            # report showed this same button getting re-found and re-clicked
            # for minutes straight without ever actually going away, which a
            # click landing but not registering (something invisible eating
            # it, or the game just not picking up a single click here) fits
            # better than a detection problem would. A follow-up click
            # somewhere neutral is cheap and harmless if the first one
            # already worked, but gives a real shot at clearing whatever's
            # actually blocking it if it didn't.
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            self._mouse.click(left + self._coords["screen_middle_x"], top + self._coords["screen_middle_y"])
            return None

        # Same idea as the nav_start_game re-check above -- a level-up
        # "Select an upgrade!" reward-card modal (confirmed via a real
        # capture sitting right on top of the extract/continue choice) gets
        # its own dedicated check here too, not just the middle-screen click
        # bundled into the exp_extract branch, since it can show up on ANY
        # tick, not only the one where exp_extract happens to also be found.
        if self._dismiss_reward_card_if_found(hwnd):
            return None

        # A failed run (team/base wiped) ends on the Defeat result screen
        # with Repeat/Leave Stage -- same panel Story/Raid loses land on, so
        # the same "defeat" template finds it. Checked BEFORE the extract/
        # continue searches: once this screen is up none of those buttons
        # exist anymore, and returning "loss" is what lets the task's
        # normal repeat flow (Repeat Stage, see _handle_match_result)
        # re-run the expedition right away. Best-effort like every other
        # optional template -- a missing defeat image just skips the check
        # (and a real failure then falls back to the old slow
        # timeout-and-recover path).
        try:
            defeat_match = vision.find_image(hwnd, "defeat")
        except vision.TemplateNotFound:
            defeat_match = None
        if defeat_match is not None:
            debug_path = self._debug_save(hwnd, "defeat", defeat_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Expedition run failed -- Defeat screen found "
                       f"(score {defeat_match['score']:.2f}).{suffix}")
            return "loss"

        # Checkpoint handling forks by engine here: color-first (the
        # default -- one cheap pixel search plus the mirror-symmetry rule,
        # see the EXP_COLOR_* block) or the original template path below
        # (Settings > Debug > "Expedition Color Detection" off). Everything
        # above this line (start-game re-check, reward card, defeat) is
        # shared -- those are art, not color blobs, and stay template-based
        # under both engines.
        if self._expedition_color_buttons:
            return self._check_expedition_checkpoint_by_color(hwnd, stop_event)

        # exp_extract is a recurring checkpoint choice -- Extract and
        # Continue offered side by side, not a one-shot terminal event (see
        # the counting reasoning in _play_one_match's reset of these two
        # fields). Decline every sighting up to extract_after (click the
        # "exp_extract_continue" choice THIS screen offers), only accept
        # the sighting right after that.
        try:
            extract_match = vision.find_image(hwnd, "exp_extract")
        except vision.TemplateNotFound:
            extract_match = None
        if extract_match is not None:
            self._expedition_extract_count += 1
            debug_path = self._debug_save(hwnd, "exp_extract", extract_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "exp_extract" (occurrence {self._expedition_extract_count}/'
                       f'{self._expedition_extract_accept_at}, score {extract_match["score"]:.2f}).{suffix}')
            # A level-up "Select an upgrade!" reward-card modal can be on
            # screen at the exact same moment as this choice (confirmed via
            # a real capture -- 3 upgrade cards sitting right on top of the
            # Extract/Continue buttons), auto-selecting on its own after
            # ~12s but intercepting/covering whichever button gets clicked
            # next until then. No dedicated template for that modal to gate
            # this on, so it's unconditional instead: a middle-screen click
            # picks whatever card is there if one is, and does nothing
            # harmful if there wasn't one. Settled afterward -- the reward
            # modal's own dismiss animation and the extract/continue button
            # itself both need a beat to actually render, not just the
            # instant this match was found in.
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            self._mouse.click(left + self._coords["screen_middle_x"], top + self._coords["screen_middle_y"])
            time.sleep(0.5)

            if self._expedition_extract_count < self._expedition_extract_accept_at:
                self._log("[Macro] Not the configured sighting yet -- declining (continuing).")
                # _click_and_verify_gone, not the plain single-click
                # _click_found_image -- a laggy game can eat the first click
                # without the button actually going anywhere, and the next
                # poll tick would then just re-find the SAME still-showing
                # exp_extract sighting again without ever having actually
                # advanced past it (reported: the count stops incrementing
                # because it's stuck re-declining the identical checkpoint).
                # Retrying the click until it's confirmed gone is what
                # actually fixes that, not just clicking once and hoping.
                # exp_extract_continue (was named just "continue" -- renamed
                # since a name that generic invited conflicts with
                # continue_2 and any future plain-Continue button): the
                # checkmarked "Continue" CHOICE this extract screen offers
                # beside Extract, i.e. the decline button.
                if not self._click_and_verify_gone(hwnd, stop_event, "exp_extract_continue",
                                                   EXPEDITION_EXTRACT_CONFIRM_TIMEOUT):
                    self._log('[Macro] "exp_extract_continue" never showed up after exp_extract -- '
                               'will retry next poll.')
                    return None
                if not self._click_and_verify_gone(hwnd, stop_event, "continue_2", EXPEDITION_WAVE_TIMEOUT):
                    self._log('[Macro] "continue_2" never showed up after declining exp_extract -- '
                               'will retry next poll.')
                    return None
                self._interruptible_sleep(EXPEDITION_CONTINUE_COOLDOWN, stop_event)
                return None

            self._log(f"[Macro] exp_extract sighting {self._expedition_extract_count}/"
                       f"{self._expedition_extract_accept_at} -- extracting for real.")
            # Double-clicked (not a single click like every other match in
            # this file) -- this specific button has been reported as only
            # sometimes actually registering on the first click.
            vision.double_click_match(self._mouse, hwnd, extract_match)
            try:
                confirm_match = vision.wait_for_image(
                    hwnd, "extract", timeout=EXPEDITION_EXTRACT_CONFIRM_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return None
            if confirm_match is None:
                if not (stop_event is not None and stop_event.is_set()):
                    self._log('[Macro] "extract" never showed up after exp_extract -- will retry next poll '
                               '(exp_extract itself doesn\'t go away, a reward modal may just be covering it).')
                return None
            debug_path = self._debug_save(hwnd, "extract", confirm_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "extract" (score {confirm_match["score"]:.2f}) -- clicking it.{suffix}')
            # Shuffled, not a plain click -- reported (confirmed by testing)
            # that this specific button's click can visually land without
            # actually registering game-side, apparently needing genuine
            # hover-in movement first, not just an absolute jump. Then a
            # real settle wait afterward too, on top of that -- the same
            # class of issue as a click not being given time to register
            # before the very next check runs right on top of it.
            vision.shuffle_click_match(self._mouse, hwnd, confirm_match)
            self._interruptible_sleep(EXTRACT_CONFIRM_SETTLE, stop_event)
            # A SECOND confirmation ("Extraction -- Are you sure you'd like
            # to end this run?", its own separate red Extract/Cancel
            # buttons, a rewards preview) can show up after this click --
            # confirmed from a real capture, stuck exactly here. Optional/
            # best-effort like nav_disband: extract_confirm.png being
            # missing just means this step is silently skipped (treated as
            # if this second modal never happens), not a failure, since not
            # everyone will have added it yet.
            try:
                second_confirm = vision.wait_for_image(
                    hwnd, "extract_confirm", timeout=EXPEDITION_EXTRACT_CONFIRM_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound:
                second_confirm = None
            if second_confirm is not None:
                debug_path = self._debug_save(hwnd, "extract_confirm", second_confirm)
                suffix = f" Debug: {debug_path}" if debug_path else ""
                self._log(f'[Macro] Found "extract_confirm" (score {second_confirm["score"]:.2f}) -- '
                           f'clicking it.{suffix}')
                vision.shuffle_click_match(self._mouse, hwnd, second_confirm)
                self._interruptible_sleep(EXTRACT_CONFIRM_SETTLE, stop_event)
            # Second check before declaring the win: an extract click chain
            # that didn't actually register leaves exp_extract still on
            # screen -- reporting "win" then would record a run that never
            # ended and strand the loop on the reward-read that follows.
            # Still visible = not extracted; None lets the next poll tick
            # take the whole thing from the top.
            try:
                still_up = vision.find_image(hwnd, "exp_extract")
            except vision.TemplateNotFound:
                still_up = None
            if still_up is not None:
                self._log('[Macro] "exp_extract" is still showing after the confirm chain -- the '
                           'extract didn\'t register, will retry next poll.')
                return None
            self._log("[Macro] Extracted -- on the reward screen.")
            return "win"

        try:
            continue_match = vision.find_image(hwnd, "exp_continue")
        except vision.TemplateNotFound:
            continue_match = None
        if continue_match is not None:
            debug_path = self._debug_save(hwnd, "exp_continue", continue_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "exp_continue" (score {continue_match["score"]:.2f}) -- clicking it.{suffix}')
            vision.click_match(self._mouse, hwnd, continue_match)
            # continue_2 is the expected follow-up, but the checkmarked
            # Continue (exp_extract_continue -- same art the extract
            # screen's decline choice uses) can show up here instead
            # depending on the wave -- checking for either one instead of
            # only continue_2 avoids a false "never showed up" stop when
            # the follow-up screen just wasn't the one specifically
            # expected.
            try:
                follow_match, follow_name = vision.wait_for_image_any(
                    hwnd, ("continue_2", "exp_extract_continue"),
                    timeout=EXPEDITION_WAVE_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound:
                follow_match, follow_name = None, None
            if follow_match is None:
                if stop_event is not None and stop_event.is_set():
                    return None
                self._log('[Macro] Neither "continue_2" nor "exp_extract_continue" showed up after '
                           'exp_continue -- will retry next poll.')
                return None
            debug_path = self._debug_save(hwnd, follow_name, follow_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "{follow_name}" (score {follow_match["score"]:.2f}) -- clicking it.{suffix}')
            # Retried until it's confirmed gone, not just clicked once --
            # same laggy-click issue as the exp_extract decline path (a
            # click that doesn't register still leaves this same button on
            # screen, and the very next poll tick just re-finds it, stuck).
            for _ in range(3):
                vision.click_match(self._mouse, hwnd, follow_match)
                time.sleep(1.0)
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    still_match, _ = vision.find_image_any(hwnd, (follow_name,))
                except vision.TemplateNotFound:
                    still_match = None
                if still_match is None:
                    break
                follow_match = still_match
            self._interruptible_sleep(EXPEDITION_CONTINUE_COOLDOWN, stop_event)
            return None

        return None

    def _check_expedition_checkpoint_by_color(self, hwnd, stop_event: threading.Event) -> str:
        """The color-first checkpoint engine (see the EXP_COLOR_* block for
        the two-layouts/mirror-symmetry reasoning). One find_color_run for
        the green Continue face answers, in a few milliseconds, everything
        the template path needed four separate image searches for:

        - nothing found        -> mid-wave, keep polling (return None)
        - Continue centered    -> plain wave transition: click it, chase the
                                  smaller follow-up Continue, brief settle
        - Continue off-center  -> the checkpoint is offering Extract; count
                                  the sighting (debounced -- see
                                  EXP_COLOR_SIGHTING_DEBOUNCE), and either
                                  decline via that same Continue or, at the
                                  accept-at sighting, click the mirrored
                                  Extract spot and confirm (-> "win")

        Same return convention as _check_expedition_wave_result, which this
        runs inside of: "win" once extracted, None otherwise ("loss" never
        comes from here -- the shared defeat check above it owns that)."""
        cont = vision.find_color_run(hwnd, EXP_COLOR_CONTINUE_BAND, _exp_green, EXP_COLOR_CONTINUE_MIN_RUN)
        if cont is None:
            return None  # mid-wave -- nothing up this tick
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        center_x = 576  # the 1152-wide reference space's vertical centerline
        offers_extract = cont["cx"] > center_x + EXP_COLOR_MIRROR_MARGIN

        if offers_extract:
            now = time.time()
            if now - self._exp_last_sighting_at > EXP_COLOR_SIGHTING_DEBOUNCE:
                self._expedition_extract_count += 1
                self._log(f'[Macro] Checkpoint offers Extract (sighting {self._expedition_extract_count}/'
                           f'{self._expedition_extract_accept_at} -- Continue found at x={cont["cx"]}).')
            self._exp_last_sighting_at = now
            # A level-up "Select an upgrade!" reward-card modal can sit on
            # top of this exact choice -- same unconditional middle-click
            # dismissal (harmless when no card is up) + settle the template
            # path uses, then re-find Continue in case anything shifted.
            self._mouse.click(left + self._coords["screen_middle_x"], top + self._coords["screen_middle_y"])
            time.sleep(0.5)
            refound = vision.find_color_run(hwnd, EXP_COLOR_CONTINUE_BAND, _exp_green, EXP_COLOR_CONTINUE_MIN_RUN)
            if refound is not None:
                cont = refound
            if self._expedition_extract_count >= self._expedition_extract_accept_at:
                if self._extract_via_mirrored_button(hwnd, stop_event, left, top, center_x, cont):
                    return "win"
                if stop_event is not None and stop_event.is_set():
                    return None
                # Never stall on a failed extract: continuing costs one more
                # wave and another (immediate -- count already past
                # accept-at) extract chance at the next checkpoint.
                self._log("[Macro] Extract confirm never registered -- continuing this checkpoint instead.")
            else:
                self._log(f'[Macro] Not the configured sighting yet -- declining (continuing).')
        else:
            self._log(f'[Macro] Wave Continue found (x={cont["cx"]}) -- clicking it.')

        self._mouse.click(left + cont["cx"], top + cont["cy"])
        time.sleep(0.5)
        # The smaller follow-up Continue confirms the transition actually
        # advanced -- hunted for the same window the template path gave
        # continue_2 (EXPEDITION_WAVE_TIMEOUT), since the game can take a
        # few seconds to put it up. While hunting, the MAIN Continue still
        # being visible means the click above got eaten (confirmed live: the
        # log showed the same "Wave Continue found" twice with nothing in
        # between) -- re-click it right here instead of blind-waiting out a
        # follow-up that can't appear until the first click lands.
        deadline = time.time() + EXPEDITION_WAVE_TIMEOUT
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            follow = vision.find_color_run(hwnd, EXP_COLOR_FOLLOWUP_BAND, _exp_green_loose,
                                            EXP_COLOR_FOLLOWUP_MIN_RUN)
            if follow is not None:
                self._log(f'[Macro] Follow-up Continue found at ({follow["cx"]}, {follow["cy"]}) -- clicking it.')
                self._mouse.click(left + follow["cx"], top + follow["cy"])
                break
            still = vision.find_color_run(hwnd, EXP_COLOR_CONTINUE_BAND, _exp_green,
                                           EXP_COLOR_CONTINUE_MIN_RUN)
            if still is not None:
                self._mouse.click(left + still["cx"], top + still["cy"])
            time.sleep(0.25)
        self._interruptible_sleep(EXP_COLOR_CONTINUE_SETTLE, stop_event)
        return None

    def _extract_via_mirrored_button(self, hwnd, stop_event: threading.Event, left: int, top: int,
                                       center_x: int, cont: dict) -> bool:
        """Clicks the Extract button -- found by its own red face in the
        bottom band when possible, else at the Continue's position mirrored
        across the centerline (the two share a row, symmetric about center;
        verified 2px apart on a real frame, see the EXP_COLOR_* comment) --
        then hunts the confirm dialog's red button and verifies it actually
        went away. Up to 4 full attempts, tight settles -- re-checking is a
        few ms, so there's no reason to sit on long cooldowns between
        tries. True once the confirm is clicked and gone (the reward screen
        is up), False if it never registered."""
        red_btn = vision.find_color_run(hwnd, EXP_COLOR_CONTINUE_BAND, _exp_red, EXP_COLOR_CONTINUE_MIN_RUN)
        if red_btn is not None:
            ex, ey = red_btn["cx"], red_btn["cy"]
            how = "by color"
        else:
            ex, ey = 2 * center_x - cont["cx"], cont["cy"]
            how = "mirrored from Continue"
        self._log(f'[Macro] Sighting {self._expedition_extract_count}/{self._expedition_extract_accept_at} -- '
                   f'extracting for real (Extract at ({ex}, {ey}), {how}).')
        for attempt in range(1, 5):
            if self._checkpoint(stop_event):
                return False
            self._mouse.click(left + ex, top + ey)
            time.sleep(0.45)
            deadline = time.time() + 2.2
            while time.time() < deadline:
                if self._checkpoint(stop_event):
                    return False
                confirm = vision.find_color_run(hwnd, EXP_COLOR_CONFIRM_BAND, _exp_red,
                                                 EXP_COLOR_CONFIRM_MIN_RUN)
                if confirm is None:
                    # Template backstop for the one band no real capture has
                    # verified yet -- the "extract" confirm crop already on
                    # file finds the same dialog by art if the color band
                    # is off on some setup.
                    try:
                        tmpl = vision.find_image(hwnd, "extract")
                    except vision.TemplateNotFound:
                        tmpl = None
                    if tmpl is not None:
                        confirm = {"cx": tmpl["cx"], "cy": tmpl["cy"]}
                if confirm is not None:
                    self._mouse.click(left + confirm["cx"], top + confirm["cy"])
                    time.sleep(0.45)
                    if vision.find_color_run(hwnd, EXP_COLOR_CONFIRM_BAND, _exp_red,
                                              EXP_COLOR_CONFIRM_MIN_RUN) is None:
                        # Second check, a beat later: the confirm vanishing
                        # only proves the DIALOG closed -- an extract that
                        # didn't actually register leaves the checkpoint's
                        # own Continue/Extract still sitting in the bottom
                        # band (and the confirm can even come back). Only
                        # both being clear counts as extracted; anything
                        # else restarts the attempt instead of reporting a
                        # win that never happened.
                        time.sleep(0.8)
                        checkpoint_up = vision.find_color_run(hwnd, EXP_COLOR_CONTINUE_BAND, _exp_green,
                                                               EXP_COLOR_CONTINUE_MIN_RUN)
                        confirm_back = vision.find_color_run(hwnd, EXP_COLOR_CONFIRM_BAND, _exp_red,
                                                              EXP_COLOR_CONFIRM_MIN_RUN)
                        if checkpoint_up is None and confirm_back is None:
                            self._log("[Macro] Extracted -- on the reward screen.")
                            return True
                        self._log("[Macro] Confirm closed but the checkpoint is still up -- "
                                   "the extract didn't register, retrying.")
                    break  # restart from the Extract click
                time.sleep(0.12)
        return False

    def debug_check_expedition_wave(self, hwnd) -> str:
        """Settings > Debug > "Test Expedition Wave Check" -- runs exactly
        one tick of _check_expedition_wave_result against whatever's on
        screen in Roblox right now, with real clicks and all, but WITHOUT
        needing an actual task/run in progress first. Point of this: tuning
        nav_start_game/exp_continue/exp_extract detection used to mean
        restarting a whole macro run (lobby -> gamemode -> map -> stage ->
        teleport) every single time just to get back to the one screen
        being tested. Navigate to that screen by hand in Roblox instead,
        press this button, read what it found/clicked in the log, repeat
        as many times as needed. Uses a stop_event that's never set (there's
        no real run to interrupt), so a click's own wait-for-follow-up can
        still time out normally, it just can't be cancelled early."""
        wm.show_window(hwnd)
        if not wm.activate_window(hwnd):
            self._log("[Debug] Couldn't confirm Roblox actually took focus -- clicks may not register "
                       "until it does. Continuing anyway.")
        self._log("[Debug] Testing Expedition wave-result check (single tick)...")
        result = self._check_expedition_wave_result(hwnd, threading.Event())
        self._log(f"[Debug] Expedition wave-result check returned: {result!r}")
        return result

    def _select_expedition_difficulty(self, hwnd, stop_event: threading.Event, difficulty: str) -> None:
        # One "+" button that steps the level up by 1 per click, starting
        # from 1 -- see EXPEDITION_DIFFICULTY_CLICK's comment.
        try:
            clicks = max(0, int(difficulty) - 1)
        except (TypeError, ValueError):
            clicks = 0
        if clicks == 0:
            self._log(f'[Macro] Difficulty "{difficulty}" is the default -- no click needed.')
            return
        plus_x, plus_y = self._cxy("expedition_difficulty")
        self._log(f'[Macro] Clicking difficulty "+" {clicks} time(s) at ({plus_x}, {plus_y}) '
                   f'for difficulty {difficulty}.')
        self._set_status(action=f'Setting difficulty {difficulty}...')
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        x, y = left + plus_x, top + plus_y
        for _ in range(clicks):
            if stop_event.is_set():
                return
            self._mouse.click(x, y)
            time.sleep(EXPEDITION_DIFFICULTY_CLICK_DELAY)

    def _select_expedition_map(self, hwnd, stop_event: threading.Event, map_name: str) -> bool:
        image_name = EXPEDITION_MAP_IMAGES.get(map_name)
        if image_name is None:
            self._log(f'[Macro] "{map_name}" is selected by default on the Expedition screen -- no click needed.')
            return True
        self._log(f'[Macro] Looking for Expedition map "{map_name}"...')
        try:
            match = vision.wait_for_image(hwnd, image_name, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't find \"{map_name}\": {exc}")
            return False
        if match is None:
            if not stop_event.is_set():
                self._log(f'[Macro] "{image_name}" not found within {GAMEMODE_CLICK_TIMEOUT:.0f}s -- '
                           f'couldn\'t find the "{map_name}" card, stopping.')
            return False
        debug_path = self._debug_save(hwnd, image_name, match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f'[Macro] Found "{map_name}" (score {match["score"]:.2f}) -- clicking it.{suffix}')
        vision.click_match(self._mouse, hwnd, match)
        return True

