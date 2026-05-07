---
description: Add, edit, or remove Obsidian LaTeX Suite snippets and keep the reference doc in sync
---

Edit the LaTeX Suite snippet configuration for: $ARGUMENTS

## Files

- **Plugin config:** `/Users/namitdeb/Documents/Obsidian/.obsidian/plugins/obsidian-latex-suite/data.json`
  - The `"snippets"` key holds a **JavaScript string** (`export default [...]`), not raw JSON.
    Edit the JS inside this string value. Mind JSON string escaping: newlines → `\n`, backslashes → `\\`, quotes → `\"`.
  - The `"snippetVariables"` key holds regex group definitions used as `${GREEK}`, `${SYMBOL}`, etc.
  - Options flag reference: `m` = math mode, `t` = text mode, `A` = auto-fire, `r` = regex trigger, `w` = word boundary, `v` = visual selection, `M` = block math only, `n` = inline math only.

- **Reference doc:** `/Users/namitdeb/Documents/Obsidian/_latex-snippets.md`
  - Markdown cheat-sheet: `##` category headers, 3-column tables (`Trigger | Name | Renders as`).

- **Trigger conflict index:** `/Users/namitdeb/Documents/Obsidian/latex-trigger-index.txt`
  - Sorted list of all auto-firing triggers (options containing `A`). Used for conflict detection.
  - Tab-only snippets (no `A` in options) are not listed — they can't fire unintentionally.

## Steps

1. **Read all three files** before making any changes.

2. **Conflict check (for add or modify operations only):**
   - Read `trigger-index.txt`.
   - Check if the new trigger is a substring of any existing entry, or if any existing entry is a substring of the new trigger.
   - If a conflict exists, report it and ask before proceeding. Do not silently insert a conflicting trigger.
   - Regex triggers (those with `r` in options) need only be checked against other regex triggers with overlapping match domains — use judgment.

3. **Apply the change:**
   - **Add snippet:** insert into the correct category block in the JS string in `data.json`; add a row to the matching table in `_latex-snippets.md`; insert the trigger into `trigger-index.txt` in sorted position (if auto-firing).
   - **Modify snippet:** update `data.json` and the corresponding row in `_latex-snippets.md`; update `trigger-index.txt` if the trigger changed.
   - **Remove snippet:** delete from all three files.
   - **Settings change** (`tabout`, `autofractions`, etc.): edit the relevant top-level key in `data.json` only — no changes to the other two files.

4. **Validate JSON:** after editing `data.json`, confirm the outer JSON is still valid. The JS string inside is not automatically validated.

5. **Report** what changed in each file — one sentence per file.
