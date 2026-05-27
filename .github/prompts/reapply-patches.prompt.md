---
description: Reapply local modifications after a GRD update
argument-hint: ""
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

<purpose>
Restore user-modified files after a GRD update. Reads backed-up files from
grd-local-patches/, compares with newly installed versions, and intelligently
merges or presents conflicts for resolution.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md
Plugin root: ${CLAUDE_PLUGIN_ROOT}
</context>

<process>

## Step 1: CHECK FOR PATCHES

```bash
cd "${CLAUDE_PLUGIN_ROOT}" && node bin/grd-manifest.js load-patches
```

Parse JSON output. If `found` is false:
```
No local patches found.

Patches are created automatically during /grd:update when local modifications exist.
Nothing to reapply.
```
Exit.

## Step 2: DISPLAY PATCH SUMMARY

Read backup metadata from `grd-local-patches/backup-meta.json`.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD PATCH RESTORE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Backed up from: v{from_version}
  Backup date:    {timestamp}
  Files:          {count}
```

## Step 3: ANALYZE EACH PATCH

For each file in the backup:

1. Read the backed-up version (user's modified file) from `grd-local-patches/{file}`
2. Read the newly installed version from `${CLAUDE_PLUGIN_ROOT}/{file}`
3. Compare the two:

**Case A — Identical:** Upstream incorporated the user's change. Mark as SKIP.

**Case B — Different:** User's modification differs from new version. Needs merge.

**Case C — File deleted upstream:** The file no longer exists in new version.

Display analysis table:

```
  | # | File                       | Status   | Action    |
  |---|----------------------------|----------|-----------|
  | 1 | commands/execute-phase.md  | DIFFERS  | Merge     |
  | 2 | agents/grd-executor.md     | SAME     | Skip      |
  | 3 | commands/old-command.md    | DELETED  | Ask user  |
```

## Step 4: PROCESS EACH FILE

For files marked **Merge**:

1. Read both versions fully
2. Identify the user's specific modifications (diff the backed-up version against the original pre-update version if available, otherwise diff against new version)
3. Apply user's modifications to the new version using Edit tool
4. If changes are in non-overlapping regions: apply automatically
5. If changes overlap (conflict): present both versions to user

For conflicts, use AskUserQuestion:
- header: "{filename}"
- question: "Conflict in {file}. Which version to keep?"
- options:
  - "Keep my version" — Use the backed-up (user's) version
  - "Use new version" — Accept the upstream changes
  - "Show diff" — Display both versions for manual resolution

**If "Show diff":** Display side-by-side comparison of the conflicting section, then re-ask.

For files marked **DELETED upstream**:
- Use AskUserQuestion to ask if user wants to keep their version as a custom file

## Step 5: REGENERATE MANIFEST

After all patches are processed:
```bash
cd "${CLAUDE_PLUGIN_ROOT}" && node bin/grd-manifest.js generate
```

## Step 6: CLEANUP

Use AskUserQuestion:
- header: "Cleanup"
- question: "Remove patch backup directory?"
- options:
  - "Yes, clean up (Recommended)" — Delete grd-local-patches/
  - "Keep backups" — Preserve grd-local-patches/ for reference

**If "Yes, clean up":**
```bash
rm -rf "${CLAUDE_PLUGIN_ROOT}/grd-local-patches"
```

## Step 7: DISPLAY RESULT

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PATCHES APPLIED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Applied:  {N} file(s)
  Skipped:  {M} file(s) (already incorporated upstream)
  Conflicts resolved: {K}

  Manifest regenerated with merged hashes.
```

</process>

<output>
Modified files restored. Manifest regenerated with current hashes.
Patch directory optionally cleaned up.
</output>

<error_handling>
- **No patches found**: Inform user, suggest running /grd:update first
- **Backed-up file corrupted**: Skip file, report to user
- **Cannot resolve conflict automatically**: Present to user for manual resolution
- **Manifest generation fails**: Report error, suggest manual `node bin/grd-manifest.js generate`
</error_handling>

<success_criteria>
- [ ] Patch backup found and loaded
- [ ] Summary table displayed with status per file
- [ ] Identical files skipped (upstream incorporated change)
- [ ] Different files merged or presented for resolution
- [ ] Conflicts resolved with user input
- [ ] Manifest regenerated after all patches applied
- [ ] Cleanup offered and executed if chosen
- [ ] Final report displayed
</success_criteria>
