---
description: Update GRD to latest version with changelog display
argument-hint: ""
allowed-tools: Bash, Read, AskUserQuestion
---

<purpose>
Self-update GRD plugin to the latest version. Detects install method, checks for updates,
displays changelog, backs up local modifications, and pulls latest code. Supports both
git-clone and manual installations.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md
Plugin root: ${CLAUDE_PLUGIN_ROOT}
</context>

<process>

## Step 1: DETECT INSTALL METHOD AND CURRENT VERSION

```bash
cd "${CLAUDE_PLUGIN_ROOT}"
```

Read VERSION file for current version:
```bash
cat "${CLAUDE_PLUGIN_ROOT}/VERSION"
```

Check if installed via git:
```bash
cd "${CLAUDE_PLUGIN_ROOT}" && git remote -v 2>/dev/null
```

**If no git remote:** Error — manual installation detected.
```
GRD was installed manually (no git remote detected).

To enable auto-update:
  cd ${CLAUDE_PLUGIN_ROOT}
  git init
  git remote add origin https://github.com/ca1773130n/GRD.git
  git fetch origin
  git reset --mixed origin/main

Then re-run /grd:update.
```

## Step 2: CHECK FOR UPDATES

```bash
cd "${CLAUDE_PLUGIN_ROOT}" && git fetch origin 2>&1
```

Count commits behind:
```bash
cd "${CLAUDE_PLUGIN_ROOT}" && git rev-list HEAD..origin/main --count 2>/dev/null
```

**If 0 commits behind:**
```
GRD is up to date (v{current_version}).
```
Exit.

## Step 3: DISPLAY CHANGELOG

Fetch remote CHANGELOG.md:
```bash
cd "${CLAUDE_PLUGIN_ROOT}" && git show origin/main:CHANGELOG.md 2>/dev/null
```

Display changes between current and latest version. Parse CHANGELOG.md to show only
entries newer than current version.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD UPDATE AVAILABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Current: v{current_version}
  Latest:  v{remote_version}
  Commits: {N} new commits

## What's New

{changelog entries between current and latest}
```

## Step 4: DETECT LOCAL MODIFICATIONS

Run manifest detection:
```bash
cd "${CLAUDE_PLUGIN_ROOT}" && node bin/grd-manifest.js detect
```

Parse JSON output. If modifications exist:
```
## Local Modifications Detected

  {N} file(s) modified from original:

  - {file1}
  - {file2}

  These will be backed up before updating.
```

## Step 5: CONFIRM UPDATE

Use AskUserQuestion:
- header: "Update"
- question: "Update GRD from v{current} to v{latest}?"
- options:
  - "Update now" — Back up modifications and pull latest
  - "Cancel" — Stay on current version

**If "Cancel":** Exit.

## Step 6: BACKUP MODIFICATIONS

If modifications were detected in Step 4:
```bash
cd "${CLAUDE_PLUGIN_ROOT}" && node bin/grd-manifest.js save-patches
```

Display backup summary:
```
Backed up {N} modified file(s) to grd-local-patches/
```

## Step 7: PULL UPDATE

```bash
cd "${CLAUDE_PLUGIN_ROOT}" && git pull origin main
```

**If merge conflict:**
```
Update failed due to merge conflicts.

Your local modifications have been saved to:
  ${CLAUDE_PLUGIN_ROOT}/grd-local-patches/

To resolve:
  1. cd ${CLAUDE_PLUGIN_ROOT}
  2. git merge --abort
  3. git reset --hard origin/main
  4. /grd:reapply-patches
```

## Step 8: REGENERATE MANIFEST

```bash
cd "${CLAUDE_PLUGIN_ROOT}" && node bin/grd-manifest.js generate
```

## Step 9: DISPLAY RESULT

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD UPDATED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  v{old_version} → v{new_version}
  {N} files updated
```

If patches were saved:
```
  Local modifications backed up to grd-local-patches/

  Run /grd:reapply-patches to restore your customizations.
```

</process>

<output>
Updated plugin files via git pull. Manifest regenerated.
Optional: grd-local-patches/ created with user modifications.
</output>

<error_handling>
- **No git remote**: Suggest manual git init + remote add
- **Fetch fails (network)**: Display error, suggest retry
- **Merge conflicts**: Abort merge, point to reapply-patches
- **Manifest missing**: Generate one before detecting modifications
</error_handling>

<success_criteria>
- [ ] Current version detected from VERSION file
- [ ] Git remote verified
- [ ] Remote changes fetched and counted
- [ ] Changelog displayed for relevant versions
- [ ] Local modifications detected and reported
- [ ] User confirms before proceeding
- [ ] Modified files backed up to grd-local-patches/
- [ ] git pull executed successfully
- [ ] New manifest generated
- [ ] User informed of reapply-patches if needed
</success_criteria>
