"""Pure fixed-content renderer with stable segment and section anchors."""
from .content import PARTS, ContentError


def render(hierarchy, manifest, expanded):
    nodes = {n["id"]: n for n in hierarchy["nodes"]}
    blocks = manifest["blocks"]
    lines, anchors, visible, frontier = [], [], [], []

    def segment(node_id, part, block):
        text = block[part]
        targets = [target for item in block["anchors"] if item["part"] == part for target in item["targets"]]
        start = len(lines) + 1
        if text:
            lines.extend(text.split("\n"))
            end = len(lines)
            lines.append("")
        else:
            end = start - 1
        anchors.append({"anchor_id": node_id + ":" + part, "node_id": node_id, "part": part,
                        "start_line": start, "end_line": end, "targets": targets or [{"node_id": node_id}]})

    def visit(node_id):
        if node_id not in blocks:
            raise ContentError("Expanded state requires unpublished child content.")
        node, block = nodes[node_id], blocks[node_id]
        visible.append(node_id)
        start = len(lines) + 1
        if block["kind"] == "section":
            segment(node_id, "lead_in", block)
            body_start = len(lines) + 1
            if node_id in expanded and node["children"]:
                for child in node["children"]:
                    visit(child)
            else:
                frontier.append(node_id)
                segment(node_id, "synopsis", block)
            anchors.append({"anchor_id": node_id + ":body", "node_id": node_id, "part": "body",
                            "start_line": body_start, "end_line": len(lines), "targets": [{"node_id": node_id}]})
            segment(node_id, "lead_out", block)
        else:
            frontier.append(node_id)
            for part in PARTS[block["kind"]]:
                segment(node_id, part, block)
        anchors.append({"anchor_id": node_id + ":section", "node_id": node_id, "part": "section",
                        "start_line": start, "end_line": len(lines), "targets": [{"node_id": node_id}]})
    visit(hierarchy["root_id"])
    # Keep serialization fixed, including empty boundaries; line numbers are view-local.
    return {"text": "\n".join(lines), "lines": lines, "anchors": anchors,
            "visible": visible, "frontier": frontier}
