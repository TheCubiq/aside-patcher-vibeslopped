"""Scope reviewed native UI scenarios to independently advertised patch selections."""

from copy import deepcopy


def suite_for_targets(template: dict, targets: list) -> dict:
    """Keep unsupported test adapters unavailable without blocking other selections."""
    if set(targets) - {"ask-aside", "bookmark-folder-appearance", "chats"}:
        raise ValueError("No runtime scenario adapter for the requested settings patch")
    suite = deepcopy(template)
    suite["targets"] = sorted(targets)
    baseline = next(case for case in suite["cases"] if case["id"] == "baseline-color")
    for step in baseline["steps"]:
        if step.get("selector", {}).get("text") == "Ask Aside":
            step["expected"] = 0 if "ask-aside" in targets else 1
    baseline["steps"].insert(
        0,
        {
            "action": "count",
            "selector": {"class_name": "AsideSectionHeaderButton", "text": "Chats"},
            "expected": 0 if "chats" in targets else 1,
        },
    )
    if "bookmark-folder-appearance" not in targets:
        baseline_ids = {"baseline-color", "light-theme-roundtrip", "folder-expand", "folder-collapse"}
        suite["cases"] = [case for case in suite["cases"] if case["id"] in baseline_ids]
        suite["expected_appearance"] = {}
        suite["absent_appearance"] = []
        suite["restart_checks"] = [
            {
                "action": "property",
                "selector": {"class_name": "Label", "text": "Acceptance Parent"},
                "name": "--EnabledColor",
                "from": "parent-color",
            }
        ]
    return suite
