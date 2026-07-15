# PR #1238 current manual validation record

Implementation artifact key: `a1f7c1f7b6e1e93400a4372b8e228d3908a1a966`

Independent reviewer approval: **Pending**

## Evidence scope and limitations

This record contains only facts reported or shown by the user in the
development conversation. The user-provided screenshots remain conversation
attachments; they were not copied into this repository. Consequently:

- the observations below are not independently reproduced;
- this directory does not contain the source images needed to re-audit them;
- no unlisted GUI scenario is represented as passed; and
- this record is not a substitute for independent reviewer approval.

## User-supplied observations and policy interpretation

| Scenario | Status | Recorded observation | Limitation |
|---|---|---|---|
| Non-uniform H/V scale combined with rotation | User confirmed | After the rotation-composition correction, the user confirmed that the reported deformation was fixed and then instructed that the work be committed. | Conversational confirmation only; no current-SHA reproduction image is stored here. |
| Full detection/OCR/translation/inpainting pipeline: global transform and effect values | User screenshots reviewed | The user supplied before/after screenshots showing that the result block matched the configured global Horizontal Scale, Vertical Scale, Box Slant, Glyph Slant, Gradient enabled state, Shadow radius, and the other effect values visible in those screenshots. | The screenshots are not repository artifacts. This confirms displayed result-block values, not every visual rendering or override branch. |
| Font family and alignment differences | Code/config inspection | Inspection traced the observed differences to the configured Font Family `Keep existing` policy and Alignment `decide by program` policy. | This is a policy interpretation, not a separate user-confirmed pass. The alternative `Always use global setting` / `use global setting` choices were not manually toggled and verified in this record. |

## Pending manual GUI scenarios

All scenarios below remain **Pending** for the current SHA, even where automated
tests may cover related behavior.

| Scenario | Status | Manual acceptance target |
|---|---|---|
| Transform control numeric commit | Pending | Enter and focus-out commit H/V Scale, Box Slant, and Glyph Slant once with the displayed canonical value. |
| Transform drag preview and release | Pending | Drag previews continuously, release creates one final edit, and the box/overlay stays synchronized. |
| Invalid transform input and Escape | Pending | Invalid input restores the prior value; Escape cancels an active preview without changing the model. |
| Pending edit during selection change | Pending | A pending value is committed to the old target before the new block or multi-selection becomes active. |
| Multi-selection transform editing | Pending | Common/mixed values display correctly and one operation updates all selected blocks atomically. |
| Rotation coverage beyond the reported case | Pending | Positive/negative angles, H-only and V-only anisotropy, combined H/V anisotropy, and rotated pivot changes keep a planar quadrilateral. |
| H/V and slant limits | Pending | Minimum/maximum scale and Box/Glyph Slant limits clamp cleanly without jumps or invalid geometry. |
| Shape-control interaction | Pending | Corners, edge handles, resize anchors, rotation handle, and dashed bounds remain attached to transformed geometry. |
| Horizontal and vertical writing | Pending | Both writing modes edit, wrap, resize, rotate, and render with the configured transforms. |
| Multiline and multiple blocks | Pending | Line layout, empty/trailing paragraphs, alignment, selection, and independent block geometry remain stable. |
| Mixed CJK/Latin and native italic | Pending | Mixed scripts, font fallback, native italic, Box Slant, and Glyph Slant remain visually and semantically distinct. |
| CJK IME, cursor, and partial selection | Pending | Preedit/commit, caret placement, hit testing, and partial selection remain correct under transforms. |
| Undo/redo and redo-branch replacement | Pending | Transform, rotation, format, effect, and text edits undo/redo in order and a new edit replaces the redo branch. |
| Copy, paste, and duplicate | Pending | Canonical transform/effect values copy without shared mutable state and remain undoable. |
| Save, close, restart, and reload | Pending | The current SHA persists exact transform, rotation, effect, writing-mode, and text-format values across a real restart. |
| Repeated load/save cycles | Pending | Repeated cycles do not drift geometry, angles, scale, slant, colors, offsets, or effect enablement. |
| Final export | Pending | Export matches the editing scene's text/effects and excludes selection handles, overlays, and evidence guides. |
| Effect rendering combinations | Pending | Stroke, shadow, gradient, opacity, transform, and rotation combinations paint correctly without clipping or stale cache content. |
| Effect override disabled | Pending | With `Effect: decide by program`, existing block effects remain unchanged through the full pipeline. |
| Preserve-styles pipeline | Pending | A matching preserved style wins over global transform/effect settings; fallback behavior is visibly correct when it cannot be matched. |
| Inpaint-only pipeline | Pending | Inpaint-only execution does not alter text style, writing mode, alignment, transform, or effects. |
| Font Family override choices | Pending | `Keep existing` preserves an actual existing block font and `Always use global setting` applies the selected global family. |
| Alignment override choices | Pending | `decide by program` follows automatic alignment and `use global setting` applies left/center/right exactly. |
| Clipping and large-effect bounds | Pending | Extreme shadow/stroke/gradient bounds remain visible without clipping, runaway allocation, or scene artifacts. |
| Cross-binding/platform GUI smoke test | Pending | The supported Qt binding/platform combinations open, edit, transform, save, reload, and export without binding-specific breakage. |

## Review state

No independent reviewer has approved
`a1f7c1f7b6e1e93400a4372b8e228d3908a1a966` in this artifact record. Review of
the exact current bytes and the remaining manual GUI scenarios is pending.
