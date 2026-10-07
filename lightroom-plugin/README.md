# RescueApply: Lightroom Classic plugin

Applies a lightroom-rescue manifest (JSON written by the Python pipeline) inside
Lightroom Classic. It uses the Lua SDK and never writes to the `.lrcat` file directly.

The plugin never deletes photos, never edits original files and never changes a
master's develop settings. Develop suggestions go onto a new virtual copy named
**Rescue edit**. It can also add files that are not in the catalog yet **in
place** (like Import > Add): they are never copied, moved, renamed or converted.

## Install

1. In Lightroom (it can stay open), go to **File > Plug-in Manager… > Add**,
   then choose the folder `lightroom-plugin/RescueApply.lrplugin`.
2. Make sure the plugin shows as **Enabled**. If you change files in the folder
   later, click **Reload Plug-in** (or restart Lightroom).

Requires Lightroom Classic with SDK 6.0 or later; it was written for LrC 13+.

## Before a real run

- **Back up the catalog.** Each batch is one Undo step, and on macOS the steps
  merge, so Edit > Undo cannot reliably reverse a run. The log (below) lists
  every change with its old value.
- **Turn off "Automatically write changes into XMP"** (Catalog Settings >
  Metadata) if originals must stay untouched. With it on, Lightroom itself writes
  keywords, ratings and titles into sidecars and into JPEG/TIFF/PSD/DNG files.
  The SDK cannot read this setting, so a real run refuses to start until you
  tick "I have turned OFF ... and backed up the catalog" in the options dialog.

## Usage

1. **Library > Plug-in Extras > Rescue: Apply manifest…**
2. Choose the manifest `.json`.
3. Options:
   - **Dry run** (on by default): reads only and reports counts and photos not
     found. It opens no write gate and does not change the selection.
   - **Develop suggestions**: create the "Rescue edit" virtual copies.
   - **Import**: add the manifest's `import` files (see below). Ignored when
     the manifest has no `import` block.
4. A real run shows the plan and asks for confirmation. Progress then shows in
   the activity bar at the top left; click its **x** to cancel. Batches that
   already finished stay written; the rest is not started. Re-running the same
   manifest picks up where it stopped.
5. A summary dialog appears at the end. The full log is
   `<manifest name>.apply-log.txt` next to the manifest; each run is appended.

To find everything a run touched, filter the Library on the plugin's metadata
fields: **Rescue last run** (masters whose metadata changed) and **Rescue role**
/ **Rescue copy run** (our virtual copies, and `imported` photos with the run
that imported them).

## Rules applied to each photo

| Field | Rule |
|---|---|
| lookup | `uuid` via `findPhotoByUuid`, then `path` via `findPhotoByPath`. A path match is accepted only if its local id equals `image_id`. `image_id` alone is used only when there is no uuid and the undocumented `getPhotoByLocalId` exists. |
| keywords | `A\|B\|C` hierarchy, created if missing (names match case-insensitively). Leaf keywords are set to export; parent levels are not. Never duplicated. |
| rating | only raised, never lowered |
| pick | set only if the photo is unflagged |
| color_label | set only if the photo has no label: no colour **and** no label text (custom label text that does not match the active label set counts as a label) |
| title / caption | filled only if empty |
| changed since planning | every conditional write re-reads the field inside the write gate. If the user (or sync) changed it after the plan, e.g. raised the rating, typed a title, rejected the photo, the write is skipped and logged as `SKIP`. |
| unreadable state | a field whose current value cannot be read is treated as unknown, never as empty, and is not written. If the flag cannot be read, only keywords (and verified-empty title/caption) are applied. |
| collections | `Heroes/2019` becomes set `Rescue` > set `Heroes` > collection `2019`. Smart collections with the same name are skipped. |
| develop_suggestion | applied to a **new** virtual copy named "Rescue edit", never to the master. Each new copy is tagged `rescueRole=rescue-edit-pending` in its own write gate right after it is created, then `rescue-edit` once developed. Skipped if the master already has a `rescue-edit` copy; a `rescue-edit-pending` copy left by an interrupted run is completed instead. An **untagged** copy named "Rescue edit" is never edited (it may be yours) and also blocks creating another one. Copies made by mistake (wrong selection) are tagged `rescue-orphan`, never touched again, and listed in the summary for manual removal. |
| rejected photos | photos flagged *Rejected* only get keywords, title and caption. Rating, pick, label, collections and copies are left alone. |

Develop details:

- **Settings are deltas.** Each value is added to the master's current value
  (`Exposure2012: 0.3` on a master at +1.0 gives the copy +1.3), because the
  copy starts as a clone of the master and the scores come from previews that
  already show the master's edit. For an unedited photo this is the same as
  an absolute value. Set `"settings_mode": "absolute"` inside
  `develop_suggestion` to send final values instead. The log's DEVELOP line
  shows the master's value next to the copy's value for every key.
- **Settings accepted:** Exposure2012 (-5..5), Contrast2012, Highlights2012,
  Shadows2012, Whites2012, Blacks2012, Clarity2012, Texture, Dehaze, Vibrance,
  Saturation, PostCropVignetteAmount (all -100..100) and GrainAmount (0..100),
  clamped after adding. Other keys are dropped
  and logged.
  The 2012-style keys are dropped for photos on process versions older than 2012.
- **Crop:** `left/right/top/bottom` are normalised 0..1 and `angle` maps to
  Lightroom's `CropAngle` (-45..45). The crop is applied only when the photo's
  develop `Orientation` is `AB` (unrotated): crop edges are in sensor
  orientation, and the conversion for rotated photos is not verified yet. Other
  photos get the tonal settings and a "crop skipped" line in the log. The crop
  is also skipped when the master is already cropped (the suggestion was
  measured on the cropped preview, so its coordinates do not fit the full frame).
- **Graduated filters are not applied** in this version. The SDK has no
  documented, confirmed format for creating a gradient mask with given geometry.
  Each skipped filter is written to the log with its values.

## Importing files in place

An optional top-level `import` block lists files on attached drives that are
not in the catalog yet:

```json
"import": {
  "folder_note": "free text, shown in the plan and the log",
  "files": ["/Volumes/Y DRIVE/2019/2019-03-17/IMG_3001.CR2", "..."],
  "collection": true,
  "max_files": 25
}
```

- `files` (required): absolute paths. Relative paths and paths with leading or
  trailing spaces are skipped.
- `collection` (default `true`): add the imported photos to
  `<collection_set>/Imported/<run_id>`, e.g. set `Rescue` > set `Imported` >
  collection `2019-pilot-001`. The sets and collection are created like any
  other manifest collection; a smart collection with that name is not used.
- `max_files` (optional): import at most this many in this run (for a pilot).
  A re-run of the same manifest continues with the rest.
- `photos` may be left out of an import-only manifest. Photo entries are
  resolved **before** the import, so entries for files this run imports are
  reported as not found. Re-run the manifest after the import to apply them.

Each file is added with `catalog:addPhoto(path)`, which is the same as choosing
**Add** in the Import dialog: the file stays where it is. Lightroom reads the
metadata already in the file and its XMP sidecar (keywords, ratings, develop
settings) and builds previews in the background afterwards. Nothing is written
to the files by the import itself. The plugin's own fields (`rescueRole`,
`rescueRunId`) are kept in the catalog.

| Case | What happens |
|---|---|
| already in the catalog (`findPhotoByPath`) | skipped, never touched, checked again inside the write gate |
| missing on disk / a folder | skipped and listed (`Missing on disk` in the plan) |
| same path as a catalogued photo except for upper/lower case or accents (NFC vs NFD Unicode) | skipped as a possible duplicate; the plugin compares paths with case and accents ignored |
| `.jpg`/`.jpeg` with a catalogued raw/DNG of the same name in the same folder | skipped (probably the JPEG half of a RAW+JPEG pair) |
| hidden files (`.DS_Store`, `._IMG_1234.JPG` AppleDouble files on exFAT drives), sidecars and other non-media (`xmp thm lrv aae xml json txt lrcat lrdata db ini dop pp3 cos on1`) | skipped |
| listed twice (case and accents/NFC-NFD ignored) | the second entry is skipped |
| unsupported format / unreadable file | `addPhoto` raises an error; the file is logged as `IMPORT-FAILED` and the batch continues |

The run is:

1. **Plan (dry run and real run, read-only).** Each file is checked with
   `findPhotoByPath` and `LrFileUtils.exists`. The catalog folder (or, if the
   folder is not in the catalog under that spelling, the nearest catalogued
   parent folder and its subfolders) is read once to find case and accent
   variants and RAW+JPEG pairs. The dry run reports how many would be
   imported, are already catalogued and are missing.
2. **Import.** Before each write gate the log gets (and flushes)
   `IMPORT-ATTEMPT<TAB>run_id<TAB>path` for every file in it. 50 `addPhoto`
   calls per write gate, each in its own `pcall`; one bad file does not stop
   the batch.
3. **Check and tag.** After the gate every file is looked up again by path.
   Only a photo whose committed id is new (not in the catalog before the run,
   not already claimed by another listed file) gets
   `IMPORT-OK<TAB>run_id<TAB>id<TAB>path` in the log; a second gate then sets
   **Rescue role** = `imported` and **Rescue copy run** = run_id and adds it to
   the collection. Anything else is logged as `IMPORT-FAILED`,
   `IMPORT-REFUSED` or `IMPORT-UNCERTAIN` and never tagged.
4. **Cancel / re-run.** Cancel works between batches. Re-running the same
   manifest skips everything already imported. It finishes tagging and the
   collection only for photos whose path AND id match an `IMPORT-OK` line of
   this run_id (or that already carry this run's tag). A catalogued file that
   has an `IMPORT-ATTEMPT` line but no matching `IMPORT-OK` (crash between the
   gates, failed gate, refused id) is reported as **uncertain** in the plan and
   the log and never tagged automatically; check and tag those by hand. Other
   catalogued photos are left alone.
5. **Log required.** A real run refuses to start if the log file cannot be
   opened, and the import stops if writing to the log fails.

Import is the last phase of a run, after metadata and develop. `addPhoto`
imports one file per call; expect hours for 20,000 files. The Python side
finds the imported photos with the **Rescue role** field (`imported`) and
**Rescue copy run** (the run_id), or with the collection.

Not verified yet (check in a pilot with `max_files`):
- What `addPhoto` does with a path that is already in the catalog. The plugin
  avoids it with the checks above.
- The exact error message for unsupported formats. It is logged as returned.
- Whether `addPhoto` follows the Import dialog's preferences, such as
  "Don't import suspected duplicates" or how RAW+JPEG pairs are treated when
  both files are in `files`. Expect separate photos.
- Whether a gate full of `addPhoto` calls makes Lightroom slow while previews
  are built.

## Manifest

See `sample-manifest.json`. Fields other than `uuid`/`path`/`image_id` are
optional. JSON `null` counts as "not given"; a `null` item in `photos` is
skipped with a warning and does not shift the `photos[i]` numbers in the log.
Large manifests are parsed in one step without a progress bar (about 4 s per
30 MB in stock Lua; Lightroom may be slower).

## Testing on a pilot

1. Dry run the manifest and read the log.
2. Run it on a manifest containing only 2-3 photos. Check that the "Rescue edit"
   copy looks right: whether the crop is correct and whether `Orientation` is
   reported (see the log's develop notes).
3. For an import, dry run first, then a real run with `"max_files": 25`.
   Check the new photos in `Rescue/Imported/<run_id>`, their **Rescue role**,
   the Folders panel and the log's `IMPORT-OK` / `IMPORT-FAILED` / `IMPORT-UNCERTAIN` lines.
4. Then run the whole year.

## Files

- `Info.lua`: plugin manifest and the menu item
- `ApplyManifest.lua`: menu entry (file picker, options, confirmation, summary)
- `RescueEngine.lua`: lookup, planning, batched writes, virtual copies, in-place import
- `RescueMetadata.lua`: plugin metadata fields (`rescueRole`, `rescueRunId`, `rescueLastRun`)
- `RescueLog.lua`, `RescueUtil.lua`: log file and string helpers
- `json.lua`: pure Lua 5.1 JSON decoder/encoder (MIT)
