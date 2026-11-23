"""Nine-segment context frame with a fixed pruning order.

Order: persona → relation → boundaries → corrections → digest → narrative →
evidence → task → crisis.

Hard rule: ``boundaries`` and ``corrections`` are never pruned. If the frame is
still over budget after dropping evidence and narrative, digest is compressed;
persona is trimmed last and only down to ``PERSONA_FLOOR``.
"""

from __future__ import annotations

from .contracts import (
    FRAME_MAX_CHARS,
    Digest,
    Frame,
    FrameSegment,
    SearchResult,
    TurnPlan,
)

SEGMENT_CAPS: dict[str, int] = {
    "persona": 1000,
    "relation": 200,
    "boundaries": 300,
    "corrections": 400,
    "digest": 600,
    "narrative": 300,
    "evidence": 1600,
    "task": 400,
    "crisis": 200,
}
PRUNE_ORDER = ("evidence", "narrative")
PERSONA_FLOOR = 220


def render_evidence(result: SearchResult | None) -> str:
    if result is None:
        return ""
    lines: list[str] = []
    for hit in result.summaries:
        body = hit.content or hit.title
        lines.append(f"[{hit.cite_as}] {hit.title}｜{hit.time_hint}：{body}")
    for hit in result.hits:
        body = hit.content or hit.title
        lines.append(f"[{hit.cite_as}] {hit.title}｜{hit.time_hint}：{body}")
    return "\n".join(lines)


def _segment(name: str, text: str, stable: bool = False) -> FrameSegment:
    cap = SEGMENT_CAPS[name]
    if len(text) > cap:
        text = text[: cap - 1] + "…"
    return FrameSegment(name=name, text=text, stable=stable)


def build_frame(
    *,
    plan: TurnPlan,
    persona: str,
    relation: str,
    boundaries: list[str],
    corrections: list[str],
    digest: Digest | None,
    narrative: str,
    evidence: SearchResult | None,
    crisis_note: str = "",
) -> Frame:
    digest_text = ""
    if digest is not None and digest.state == "ok":
        digest_text = "；".join(f"{s.name}:{s.text}" for s in digest.sections)

    segments = [
        _segment("persona", persona, stable=True),
        _segment("relation", relation, stable=True),
        _segment("boundaries", "；".join(boundaries)),
        _segment("corrections", "；".join(corrections)),
        _segment("digest", digest_text),
        _segment("narrative", narrative),
        _segment("evidence", render_evidence(evidence)),
        _segment("task", f"意图={plan.intent.value}；必须给证据的断言需加 [S#] 引用；无证据时直说不知道。"),
        _segment("crisis", crisis_note),
    ]
    dropped: list[str] = []
    total = sum(len(s.text) for s in segments)
    if total > FRAME_MAX_CHARS:
        for name in PRUNE_ORDER:
            for i, seg in enumerate(segments):
                if seg.name == name and seg.text:
                    dropped.append(name)
                    segments[i] = FrameSegment(name=seg.name, text="", stable=seg.stable)
                    total = sum(len(s.text) for s in segments)
                    if total <= FRAME_MAX_CHARS:
                        break
            if total <= FRAME_MAX_CHARS:
                break
    if total > FRAME_MAX_CHARS:
        for i, seg in enumerate(segments):
            if seg.name == "digest" and len(seg.text) > 120:
                segments[i] = FrameSegment(name="digest", text=seg.text[:118] + "…", stable=False)
        total = sum(len(s.text) for s in segments)
    if total > FRAME_MAX_CHARS:
        for i, seg in enumerate(segments):
            if seg.name == "persona":
                keep = max(PERSONA_FLOOR, len(seg.text) - (total - FRAME_MAX_CHARS))
                segments[i] = FrameSegment(name="persona", text=seg.text[: keep - 1] + "…", stable=True)
        total = sum(len(s.text) for s in segments)
    return Frame(segments=tuple(segments), dropped=tuple(dropped), total_chars=total)
