#!/usr/bin/env python3
"""Interactive calibration and debugging tool for Gold Shop scrolling & item coordinates.

Features:
- Automatically detects and docks/activates Roblox window.
- Live scan of current view: tests matching for all 11 shop items, buy buttons, and stock regions.
- Automated calibration sweep: scrolls through deltas (0 to -4800), detects optimal scroll
  positions for each item, and generates calibrated dictionaries for core/runner_shop.py.
- Interactive mode: test custom scroll amounts, reset to top, test buy button detection,
  and inspect annotated screenshots.
- Saves visual debug images with bounding boxes, viewports, and green-button detection.

Usage:
    .\\venv\\Scripts\\python.exe tools/debug_gold_shop.py
    .\\venv\\Scripts\\python.exe tools/debug_gold_shop.py --sweep
    .\\venv\\Scripts\\python.exe tools/debug_gold_shop.py --inspect
    .\\venv\\Scripts\\python.exe tools/debug_gold_shop.py --interactive
"""

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import cv2
import numpy as np

from core import auto_shop, auto_shop_vision, config, mouse, vision
import core.runner_shop as rs
from core import window as wm


DEBUG_DIR = REPO_ROOT / "debug_output" / "gold_shop"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def get_roblox_hwnd(auto_dock: bool = True) -> Optional[int]:
    """Find and optionally dock the Roblox window."""
    manager = wm.get_window_manager()
    hwnd = manager.find_window()
    if not hwnd:
        print("\n[ERROR] Roblox window not found! Please make sure Roblox is open.")
        return None

    if auto_dock:
        print(f"[Info] Found Roblox window (HWND: {hwnd}). Activating and resizing to {config.FIXED_WIN_W}x{config.FIXED_WIN_H}...")
        try:
            manager.activate()
            if hasattr(manager, "resize_client_to"):
                manager.resize_client_to(config.FIXED_WIN_W, config.FIXED_WIN_H)
            time.sleep(0.5)
        except Exception as exc:
            print(f"[Warning] Could not resize window: {exc}")
    return hwnd


def draw_debug_overlay(frame_bgr: np.ndarray, detected_items: List[dict], current_scroll: int = 0) -> np.ndarray:
    """Draw bounding boxes for viewports, detected items, buy regions, and status text."""
    vis = frame_bgr.copy()

    # Draw SHOP_LIST_ACTION_VIEWPORT (Yellow)
    vx, vy, vw, vh = rs.SHOP_LIST_ACTION_VIEWPORT
    cv2.rectangle(vis, (vx, vy), (vx + vw, vy + vh), (0, 255, 255), 2)
    cv2.putText(vis, "ACTION_VIEWPORT", (vx, vy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

    # Draw Left and Right slot viewports (Cyan / Magenta)
    lx, ly, lw, lh = rs.SHOP_LIST_SLOT_VIEWPORTS["left"]
    cv2.rectangle(vis, (lx, ly), (lx + lw, ly + lh), (255, 255, 0), 1)
    cv2.putText(vis, "LEFT_COL", (lx + 5, ly + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)

    rx, ry, rw, rh = rs.SHOP_LIST_SLOT_VIEWPORTS["right"]
    cv2.rectangle(vis, (rx, ry), (rx + rw, ry + rh), (255, 0, 255), 1)
    cv2.putText(vis, "RIGHT_COL", (rx + 5, ry + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1)

    # Center point for scrolling
    cx, cy = rs.SHOP_LIST_CENTER
    cv2.drawMarker(vis, (cx, cy), (0, 0, 255), cv2.MARKER_CROSS, 14, 2)
    cv2.putText(vis, f"Scroll Center ({cx},{cy})", (cx + 10, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

    for item_data in detected_items:
        match = item_data["match"]
        name = item_data["name"]
        score = item_data["score"]
        buy_valid = item_data["buy_valid"]
        is_green = item_data["buy_green"]
        is_oos = item_data.get("out_of_stock", False)

        x, y, w, h = match["x"], match["y"], match["w"], match["h"]

        # Item Icon Box: Blue / Light Blue
        cv2.rectangle(vis, (x, y), (x + w, y + h), (255, 128, 0), 2)
        cv2.putText(vis, f"{name} ({score:.2f})", (x, max(15, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 0), 1)

        # Initial Buy Region: Green if enabled and in viewport, Orange if visible but not green, Red if clipped
        bx, by, bw, bh = item_data["buy_region"]
        if is_oos:
            buy_color = (128, 128, 128)  # Gray for Out of Stock
            buy_label = "OUT OF STOCK"
        elif buy_valid and is_green:
            buy_color = (0, 255, 0)      # Bright Green
            buy_label = f"BUY OK ({item_data['green_ratio']*100:.1f}%)"
        elif buy_valid:
            buy_color = (0, 165, 255)    # Orange (not green button)
            buy_label = f"BUY DISABLED ({item_data['green_ratio']*100:.1f}%)"
        else:
            buy_color = (0, 0, 255)      # Red (clipped)
            buy_label = "BUY CLIPPED"

        cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), buy_color, 2)
        cv2.putText(vis, buy_label, (bx, by + bh - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.35, buy_color, 1)

        # Stock status region (Purple)
        sx, sy, sw, sh = item_data["stock_region"]
        cv2.rectangle(vis, (sx, sy), (sx + sw, sy + sh), (200, 0, 200), 1)

    # Header text
    cv2.putText(vis, f"Gold Shop Calibration | Scroll: {current_scroll} | Detected Items: {len(detected_items)}",
                (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return vis


def analyze_frame(hwnd: int, frame_bgr: Optional[np.ndarray] = None, allow_top_clip_map: Optional[dict] = None) -> Tuple[np.ndarray, List[dict]]:
    """Capture (or use provided frame) and analyze all shop items on screen."""
    if frame_bgr is None:
        frame_bgr = vision.capture_game_bgr(hwnd)

    if frame_bgr is None:
        print("[Error] Failed to capture game frame.")
        return np.zeros((756, 1152, 3), dtype=np.uint8), []

    detected_items = []

    for item in auto_shop.AUTO_SHOP_ITEMS:
        item_key = item["key"]
        template = item["template"]
        column = rs.SHOP_ITEM_COLUMNS.get(item_key, "left")
        col_viewport = rs.SHOP_LIST_SLOT_VIEWPORTS.get(column)

        # Try to find the item in its column viewport or globally in list action viewport
        match = None
        try:
            match = vision.find_image(hwnd, template, region=col_viewport)
        except Exception:
            pass

        if match is None:
            # Fallback search within wider action viewport
            try:
                match = vision.find_image(hwnd, template, region=rs.SHOP_LIST_ACTION_VIEWPORT)
            except Exception:
                pass

        if match is not None:
            # Compute regions
            stock_region = auto_shop_vision.stock_status_region_from_item_match(match)
            buy_region = auto_shop_vision.initial_buy_region_from_item_match(match)

            allow_top = allow_top_clip_map.get(item_key, False) if allow_top_clip_map else False
            stock_visible = rs.ShopOps._shop_region_is_visible(stock_region, allow_top_clip=allow_top)
            buy_visible = rs.ShopOps._shop_region_is_visible(buy_region, allow_top_clip=False)

            # Check green ratio of buy button
            try:
                buy_crop = auto_shop_vision.crop_region(frame_bgr, buy_region)
                is_green = auto_shop_vision.buy_button_is_enabled(buy_crop)
                # Compute exact green ratio for inspection
                hsv = cv2.cvtColor(buy_crop, cv2.COLOR_BGR2HSV)
                green_mask = cv2.inRange(hsv, (35, 80, 80), (85, 255, 255))
                green_ratio = float(np.count_nonzero(green_mask)) / float(buy_crop.shape[0] * buy_crop.shape[1])
            except Exception:
                is_green = False
                green_ratio = 0.0

            # Check out of stock
            try:
                oos_match = vision.find_image(
                    hwnd,
                    auto_shop.AUTO_SHOP_UI_TEMPLATES["out_of_stock"],
                    region=stock_region,
                )
                is_oos = oos_match is not None
            except Exception:
                is_oos = False

            detected_items.append({
                "key": item_key,
                "name": item["name"],
                "match": match,
                "score": match.get("score", 1.0),
                "column": column,
                "stock_region": stock_region,
                "buy_region": buy_region,
                "stock_valid": stock_visible,
                "buy_valid": buy_visible,
                "buy_green": is_green,
                "green_ratio": green_ratio,
                "out_of_stock": is_oos,
            })

    return frame_bgr, detected_items


def reset_and_scroll(hwnd: int, mouse_inst: mouse.Mouse, scroll_amount: int, settle_delay: float = 1.0) -> None:
    """Move cursor to shop list center, reset to Top, and apply target wheel delta."""
    x, y = vision.ref_to_screen(hwnd, *rs.SHOP_LIST_CENTER)
    mouse_inst.move_to(x, y)
    mouse_inst.nudge()
    time.sleep(0.05)
    # Scroll up to reset
    mouse_inst.scroll(rs.SHOP_SCROLL_RESET_AMOUNT)
    time.sleep(settle_delay)
    if scroll_amount != 0:
        mouse_inst.scroll(scroll_amount)
        time.sleep(settle_delay)


def inspect_current_screen(hwnd: int) -> None:
    """Analyze and display the current screen without moving mouse or scrolling."""
    print("\n--- [Inspect Current Gold Shop View] ---")
    frame, items = analyze_frame(hwnd)
    vis = draw_debug_overlay(frame, items, current_scroll=0)

    out_path = DEBUG_DIR / "inspect_current.png"
    cv2.imwrite(str(out_path), vis)
    print(f"[Saved] Annotated screenshot saved to: {out_path}")

    print(f"\nFound {len(items)} items on screen:")
    print(f"{'Item Name':<20} | {'Col':<5} | {'Center (cx, cy)':<16} | {'Buy Region':<22} | {'Buy In View':<11} | {'Green %':<8} | {'Status'}")
    print("-" * 105)
    for it in items:
        m = it["match"]
        bx, by, bw, bh = it["buy_region"]
        status = "OUT OF STOCK" if it["out_of_stock"] else ("READY TO BUY" if (it["buy_valid"] and it["buy_green"]) else "CLIPPED/DISABLED")
        print(f"{it['name']:<20} | {it['column']:<5} | ({m['cx']:>4}, {m['cy']:>4})       | ({bx:>3}, {by:>3}, {bw:>3}, {bh:>3})   | {str(it['buy_valid']):<11} | {it['green_ratio']*100:>5.1f}%  | {status}")


def run_full_calibration_sweep(hwnd: int, step_delta: int = 120, max_scroll: int = -5000) -> None:
    """Run an automated sweep from 0 to max_scroll, record all positions, and find optimal calibrations."""
    mouse_inst = mouse.Mouse()
    print("\n====================================================================")
    print("       STARTING AUTOMATED GOLD SHOP CALIBRATION SWEEP")
    print("====================================================================")
    print(f"Scanning scroll positions from 0 down to {max_scroll} in steps of {step_delta}...")

    test_deltas = [0] + list(range(-abs(step_delta), max_scroll - 1, -abs(step_delta)))

    item_observations: Dict[str, List[dict]] = {it["key"]: [] for it in auto_shop.AUTO_SHOP_ITEMS}

    for idx, delta in enumerate(test_deltas):
        print(f"[{idx+1}/{len(test_deltas)}] Testing Scroll Delta: {delta} ...", end="\r", flush=True)
        reset_and_scroll(hwnd, mouse_inst, delta, settle_delay=0.9)

        allow_top_map = {it["key"]: (delta == 0) for it in auto_shop.AUTO_SHOP_ITEMS}
        frame, items = analyze_frame(hwnd, allow_top_clip_map=allow_top_map)

        # Save debug screenshot for positions where items were detected
        if items:
            vis = draw_debug_overlay(frame, items, current_scroll=delta)
            out_img = DEBUG_DIR / f"sweep_{abs(delta):04d}.png"
            cv2.imwrite(str(out_img), vis)

        for it in items:
            m = it["match"]
            item_observations[it["key"]].append({
                "scroll": delta,
                "cx": m["cx"],
                "cy": m["cy"],
                "buy_region": it["buy_region"],
                "buy_in_view": it["buy_valid"],
                "buy_green": it["buy_green"],
                "green_ratio": it["green_ratio"],
                "score": it["score"],
                "out_of_stock": it["out_of_stock"],
            })

    print("\n\n" + "=" * 80)
    print("CALIBRATION SWEEP COMPLETE! ANALYZING OPTIMAL POSITIONS...")
    print("=" * 80)

    calibrated_amounts: Dict[str, int] = {}
    calibrated_details: Dict[str, dict] = {}

    for it in auto_shop.AUTO_SHOP_ITEMS:
        key = it["key"]
        name = it["name"]
        obs = item_observations[key]

        if not obs:
            print(f"[-] {name:<20}: NOT FOUND in any tested scroll position! (Check template or asset)")
            calibrated_amounts[key] = rs.SHOP_ITEM_SCROLL_AMOUNTS.get(key, 0)
            continue

        # Filter observations where Buy region is fully inside action viewport
        valid_obs = [o for o in obs if o["buy_in_view"]]
        if not valid_obs:
            # If none has buy_in_view, take the one closest to viewport center (cy ~ 400)
            best_obs = min(obs, key=lambda o: abs(o["cy"] - 400))
            status_note = "WARNING: Buy button slightly clipped"
        else:
            # Find observation where card center is best positioned vertically (around cy 350-450)
            best_obs = min(valid_obs, key=lambda o: abs(o["cy"] - 400))
            status_note = "OK: Buy button perfectly inside viewport"

        calibrated_amounts[key] = best_obs["scroll"]
        calibrated_details[key] = best_obs
        print(f"[+] {name:<20}: Best Scroll = {best_obs['scroll']:>5} | (cy: {best_obs['cy']:>3}, Green: {best_obs['green_ratio']*100:>5.1f}%) | {status_note}")

    # Group items by identical scroll amount
    scroll_to_items: Dict[int, List[str]] = {}
    for key, scroll in calibrated_amounts.items():
        scroll_to_items.setdefault(scroll, []).append(key)

    # Sort scroll amounts in descending order (0, -120, -480, etc.)
    sorted_scrolls = sorted(scroll_to_items.keys(), reverse=True)
    sweep_positions = [(s, tuple(scroll_to_items[s])) for s in sorted_scrolls]

    print("\n" + "=" * 80)
    print("PROPOSED CONFIGURATION FOR core/runner_shop.py")
    print("=" * 80)

    print("\nSHOP_ITEM_SCROLL_AMOUNTS = {")
    for it in auto_shop.AUTO_SHOP_ITEMS:
        key = it["key"]
        val = calibrated_amounts.get(key, 0)
        print(f'    "{key}": {val},')
    print("}")

    print("\nSHOP_SWEEP_POSITIONS = (")
    for s, group in sweep_positions:
        items_str = ", ".join(f'"{k}"' for k in group)
        if len(group) == 1:
            print(f'    ({s}, ({items_str},)),')
        else:
            print(f'    ({s}, ({items_str})),')
    print(")")

    # Save calibration json
    cal_file = DEBUG_DIR / "calibrated_gold_shop.json"
    with open(cal_file, "w", encoding="utf-8") as f:
        json.dump({
            "SHOP_ITEM_SCROLL_AMOUNTS": calibrated_amounts,
            "SHOP_SWEEP_POSITIONS": sweep_positions,
        }, f, indent=4)
    print(f"\n[Saved] Calibration configuration saved to: {cal_file}")


def interactive_mode(hwnd: int) -> None:
    """Interactive loop for testing custom scrolls and debugging."""
    mouse_inst = mouse.Mouse()
    current_scroll = 0

    print("\n====================================================================")
    print("                 GOLD SHOP INTERACTIVE DEBUGGER")
    print("====================================================================")
    print("Commands:")
    print("  <number>      - Reset to Top and scroll by <number> (e.g. 0, -120, -480, -720, -960)")
    print("  +<number>     - Delta scroll up without resetting (e.g. +120)")
    print("  -<number>     - Delta scroll down without resetting (e.g. -120)")
    print("  r             - Reset to Top (+2400)")
    print("  c             - Capture & Analyze current screen")
    print("  click <name>  - Test clicking Buy button of item (e.g. click sprite_grey)")
    print("  sweep         - Run automated calibration sweep")
    print("  q / quit      - Exit")
    print("====================================================================")

    while True:
        try:
            cmd = input(f"\n[Scroll: {current_scroll}] Enter command > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd:
            continue
        if cmd in ("q", "quit", "exit"):
            break

        if cmd == "r":
            print("[Action] Resetting to Top (+2400)...")
            reset_and_scroll(hwnd, mouse_inst, 0)
            current_scroll = 0
            inspect_current_screen(hwnd)

        elif cmd == "c":
            inspect_current_screen(hwnd)

        elif cmd == "sweep":
            run_full_calibration_sweep(hwnd)

        elif cmd.startswith("click"):
            parts = cmd.split()
            if len(parts) < 2:
                print("Usage: click <item_key> (e.g. click sprite_grey)")
                continue
            item_key = parts[1]
            frame, items = analyze_frame(hwnd)
            target = next((it for it in items if it["key"] == item_key), None)
            if not target:
                print(f"[Error] Item '{item_key}' is not visible on current screen!")
                continue
            bx, by, bw, bh = target["buy_region"]
            cx, cy = bx + bw // 2, by + bh // 2
            print(f"[Action] Clicking initial Buy button for {target['name']} at reference ({cx}, {cy})...")
            sx, sy = vision.ref_to_screen(hwnd, cx, cy)
            mouse_inst.click(sx, sy)
            time.sleep(1.0)
            inspect_current_screen(hwnd)

        elif cmd.startswith("+") or cmd.startswith("-") or cmd.isdigit() or (cmd.startswith("-") and cmd[1:].isdigit()):
            try:
                val = int(cmd)
            except ValueError:
                print(f"[Error] Unknown command: {cmd}")
                continue

            is_absolute = not cmd.startswith("+")
            if is_absolute:
                target_scroll = val
                print(f"[Action] Resetting to Top and scrolling to absolute delta {target_scroll}...")
                reset_and_scroll(hwnd, mouse_inst, target_scroll)
                current_scroll = target_scroll
            else:
                delta = val
                print(f"[Action] Relative scrolling {delta}...")
                x, y = vision.ref_to_screen(hwnd, *rs.SHOP_LIST_CENTER)
                mouse_inst.move_to(x, y)
                mouse_inst.scroll(delta)
                current_scroll += delta
                time.sleep(0.8)

            inspect_current_screen(hwnd)

        else:
            print(f"[Error] Unknown command: '{cmd}'. Type 'q' to quit or a number to scroll.")


def main():
    parser = argparse.ArgumentParser(description="Gold Shop Scroll & Coordinate Calibration Tool")
    parser.add_argument("--sweep", action="store_true", help="Run automated calibration sweep across all scroll positions")
    parser.add_argument("--inspect", action="store_true", help="Inspect and annotate current Gold Shop screen")
    parser.add_argument("--interactive", action="store_true", help="Launch interactive debug console")
    parser.add_argument("--step", type=int, default=120, help="Scroll step size for sweep (default: 120)")
    parser.add_argument("--max-scroll", type=int, default=-4800, help="Maximum negative scroll for sweep (default: -4800)")
    args = parser.parse_args()

    hwnd = get_roblox_hwnd(auto_dock=True)
    if not hwnd:
        print("[!] Make sure Roblox is running with Gold Shop open on screen.")
        sys.exit(1)

    if args.sweep:
        run_full_calibration_sweep(hwnd, step_delta=args.step, max_scroll=args.max_scroll)
    elif args.inspect:
        inspect_current_screen(hwnd)
    elif args.interactive:
        interactive_mode(hwnd)
    else:
        # Default interactive menu
        print("\n=================================================")
        print("          GOLD SHOP DEBUG & CALIBRATION")
        print("=================================================")
        print("1. Inspect current screen (no scroll)")
        print("2. Run automated calibration sweep (0 to -4800)")
        print("3. Interactive scroll & click console")
        print("4. Exit")
        try:
            choice = input("\nSelect option [1-4] (default 3): ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "4"

        if choice == "1":
            inspect_current_screen(hwnd)
        elif choice == "2":
            run_full_calibration_sweep(hwnd, step_delta=args.step, max_scroll=args.max_scroll)
        elif choice == "4":
            print("Exiting.")
        else:
            interactive_mode(hwnd)


if __name__ == "__main__":
    main()
