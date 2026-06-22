#!/usr/bin/env python3
"""Gate coordinate calibration tool.

Reads all timeline data with GoalGatePosition coordinates and outputs
a human-readable comparison of two width/height models:
  - Goal model: X 0-100 = goal posts (7.32m), Y 0-100 = crossbar (2.44m)
  - Keeper box model: X 0-100 = 6-yard box width (18.32m), Y 0-100 = crossbar (2.44m)

Usage:
    PYTHONPATH=src python tests/calibrate_gate.py

Output:
    tests/gate_calibration.md
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fbw.config import init_config

GOAL_WIDTH = 7.32
GOAL_HEIGHT = 2.44
SIX_YARD_WIDTH = 18.32  # 5.5m + 7.32m + 5.5m

# Posts in 6-yard model
SIX_YARD_LEFT_POST = 5.5 / SIX_YARD_WIDTH * 100   # ~30.0%
SIX_YARD_RIGHT_POST = (5.5 + GOAL_WIDTH) / SIX_YARD_WIDTH * 100  # ~70.0%


def collect_shots(config) -> list[dict]:
    """Collect all shots with gate data from timeline files."""
    shots = []
    timelines_dir = config.paths.raw_timelines_dir
    matches_dir = config.paths.raw_matches_dir

    if not timelines_dir.exists():
        return shots

    for tl_path in sorted(timelines_dir.glob("*.json")):
        match_id = tl_path.stem
        try:
            with open(tl_path) as f:
                tl = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # Get team names
        home_abbr = "???"
        away_abbr = "???"
        match_path = matches_dir / f"{match_id}.json"
        if match_path.exists():
            try:
                with open(match_path) as f:
                    md = json.load(f)
                ht = md.get("HomeTeam") or md.get("Home") or {}
                at = md.get("AwayTeam") or md.get("Away") or {}
                home_abbr = ht.get("Abbreviation", "???")
                away_abbr = at.get("Abbreviation", "???")
            except (json.JSONDecodeError, OSError):
                pass

        for ev in tl.get("Event", []):
            gx = ev.get("GoalGatePositionX")
            gy = ev.get("GoalGatePositionY")
            if gx is None or gy is None:
                continue

            px = ev.get("PositionX")
            py = ev.get("PositionY")
            minute = ev.get("MatchMinute", "")
            etype = ev.get("Type", -1)

            desc = ""
            desc_list = ev.get("EventDescription", [])
            if isinstance(desc_list, list) and desc_list:
                for d in desc_list:
                    if isinstance(d, dict):
                        desc = d.get("Description", "")
                        break

            # Determine if goal
            is_goal = etype == 0

            shots.append({
                "match": f"{home_abbr}-{away_abbr}",
                "match_id": match_id,
                "minute": minute,
                "type": etype,
                "is_goal": is_goal,
                "gx": gx,
                "gy": gy,
                "px": px,
                "py": py,
                "desc": desc[:60],
            })

    return shots


def format_gate(gx: float, gy: float, width_m: float, height_m: float,
                left_post: float = 0, right_post: float = 100) -> dict:
    """Calculate gate position for a given model."""
    x_m = gx / 100 * width_m
    from_centre = abs(x_m - width_m / 2)
    y_m = gy / 100 * height_m

    in_posts = left_post <= gx <= right_post
    under_bar = y_m <= GOAL_HEIGHT

    if gx < left_post:
        side = "wide LEFT"
    elif gx > right_post:
        side = "wide RIGHT"
    elif gx < (left_post + right_post) / 2 - 5:
        side = "left"
    elif gx > (left_post + right_post) / 2 + 5:
        side = "right"
    else:
        side = "centre"

    if y_m <= 0.5:
        height = "ground"
    elif y_m <= 1.0:
        height = "low"
    elif y_m <= 1.8:
        height = "mid"
    elif y_m <= GOAL_HEIGHT:
        height = "high"
    else:
        height = "OVER"

    return {
        "x_m": x_m,
        "from_centre": from_centre,
        "y_m": y_m,
        "in_posts": in_posts,
        "under_bar": under_bar,
        "in_goal": in_posts and under_bar,
        "side": side,
        "height": height,
    }


def generate_report(shots: list[dict]) -> str:
    """Generate markdown calibration report."""
    lines = []
    lines.append("# Gate Coordinate Calibration Report")
    lines.append(f"> Generated from {len(shots)} shots across "
                 f"{len(set(s['match_id'] for s in shots))} matches")
    lines.append(f"> Two models compared: Goal (7.32m) vs 6-yard box (18.32m)")
    lines.append("")

    lines.append("## Models")
    lines.append("")
    lines.append("| Model | X: 0-100 maps to | Posts at | Y: 0-100 maps to |")
    lines.append("|-------|-------------------|---------|-------------------|")
    lines.append(f"| Goal | {GOAL_WIDTH}m (post to post) | X=0, X=100 | {GOAL_HEIGHT}m (crossbar) |")
    lines.append(f"| 6-yard box | {SIX_YARD_WIDTH}m (box width) | "
                 f"X={SIX_YARD_LEFT_POST:.0f}, X={SIX_YARD_RIGHT_POST:.0f} | "
                 f"{GOAL_HEIGHT}m (crossbar) |")
    lines.append("")

    # Group by match
    by_match = {}
    for s in shots:
        key = s["match"]
        if key not in by_match:
            by_match[key] = []
        by_match[key].append(s)

    for match, match_shots in by_match.items():
        lines.append(f"## {match}")
        lines.append("")
        lines.append("| Min | Type | Gate X | Gate Y | Goal model | 6-yard model | Description |")
        lines.append("|-----|------|--------|--------|------------|--------------|-------------|")

        for s in sorted(match_shots, key=lambda x: x["minute"]):
            goal_g = format_gate(s["gx"], s["gy"], GOAL_WIDTH, GOAL_HEIGHT)
            six_g = format_gate(s["gx"], s["gy"], SIX_YARD_WIDTH, GOAL_HEIGHT,
                                SIX_YARD_LEFT_POST, SIX_YARD_RIGHT_POST)

            etype = "GOAL" if s["is_goal"] else "SHOT"
            goal_str = f"{goal_g['side']}, {goal_g['height']} ({goal_g['from_centre']:.1f}m off, {goal_g['y_m']:.2f}m)"
            six_str = f"{six_g['side']}, {six_g['height']} ({six_g['from_centre']:.1f}m off, {six_g['y_m']:.2f}m)"

            lines.append(
                f"| {s['minute']:>7s} | {etype:4s} | {s['gx']:5.1f} | {s['gy']:5.1f} "
                f"| {goal_str} | {six_str} "
                f"| {s['desc'][:40]} |"
            )

        lines.append("")

    # Summary statistics
    lines.append("## Summary")
    lines.append("")
    lines.append("### Goals (confirmed in-goal)")
    lines.append("")
    goals = [s for s in shots if s["is_goal"]]
    lines.append(f"Total goals with gate data: {len(goals)}")
    lines.append("")
    lines.append("| Goal | Gate X | Gate Y | Goal model (off centre) | 6-yard model (off centre) |")
    lines.append("|------|--------|--------|------------------------|--------------------------|")
    for s in goals:
        goal_g = format_gate(s["gx"], s["gy"], GOAL_WIDTH, GOAL_HEIGHT)
        six_g = format_gate(s["gx"], s["gy"], SIX_YARD_WIDTH, GOAL_HEIGHT,
                            SIX_YARD_LEFT_POST, SIX_YARD_RIGHT_POST)
        lines.append(
            f"| {s['match']} {s['minute']} | {s['gx']:5.1f} | {s['gy']:5.1f} "
            f"| {goal_g['side']} {goal_g['from_centre']:.1f}m, {goal_g['y_m']:.2f}m "
            f"| {six_g['side']} {six_g['from_centre']:.1f}m, {six_g['y_m']:.2f}m |"
        )
    lines.append("")

    # X range analysis
    lines.append("### X Range Analysis")
    lines.append("")
    all_gx = [s["gx"] for s in shots]
    goal_gx = [s["gx"] for s in goals]
    lines.append(f"- All shots: X range {min(all_gx):.1f} to {max(all_gx):.1f}")
    lines.append(f"- Goals only: X range {min(goal_gx):.1f} to {max(goal_gx):.1f}")
    lines.append(f"- 6-yard model posts at X=30.0 and X=70.0")
    lines.append(f"- Goals outside 30-70 range: "
                 f"{sum(1 for x in goal_gx if x < 30 or x > 70)}/{len(goal_gx)}")
    lines.append("")

    # Y range analysis
    lines.append("### Y Range Analysis")
    lines.append("")
    all_gy = [s["gy"] for s in shots]
    goal_gy = [s["gy"] for s in goals]
    lines.append(f"- All shots: Y range {min(all_gy):.1f} to {max(all_gy):.1f}")
    lines.append(f"- Goals only: Y range {min(goal_gy):.1f} to {max(goal_gy):.1f}")
    lines.append(f"- At 2.44m scale: Y range maps to {min(all_gy)/100*2.44:.2f}m "
                 f"to {max(all_gy)/100*2.44:.2f}m")
    lines.append("")

    lines.append("---")
    lines.append("*Calibrate by watching more football.*")

    return "\n".join(lines)


def main():
    config = init_config()
    shots = collect_shots(config)

    if not shots:
        print("No timeline data with gate coordinates found.")
        print("Run the daemon and backfill first.")
        return

    report = generate_report(shots)

    out_path = Path(__file__).parent / "gate_calibration.md"
    with open(out_path, "w") as f:
        f.write(report)

    print(f"Wrote {out_path} ({len(shots)} shots from "
          f"{len(set(s['match_id'] for s in shots))} matches)")


if __name__ == "__main__":
    main()
