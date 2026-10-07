--[[----------------------------------------------------------------------------
ApplyManifest.lua - Library > Plug-in Extras > "Rescue: Apply manifest…"

1. pick the manifest JSON
2. options dialog: Dry run (default ON), develop suggestions on/off
3. plan (read-only); dry run reports and stops
4. real run: confirm (with the XMP auto-write warning), then write in
   batches with a cancellable progress bar
5. summary dialog; full log in <manifest>.apply-log.txt next to the manifest
------------------------------------------------------------------------------]]

local LrBinding = import 'LrBinding'
local LrDialogs = import 'LrDialogs'
local LrFileUtils = import 'LrFileUtils'
local LrFunctionContext = import 'LrFunctionContext'
local LrPathUtils = import 'LrPathUtils'
local LrProgressScope = import 'LrProgressScope'
local LrTasks = import 'LrTasks'
local LrView = import 'LrView'

-- Load a module from the plugin folder. Lightroom's require normally returns
-- the module table; loadfile is the fallback if it does not.
local function loadModule(name)
  local ok, mod = pcall(require, name)
  if ok and type(mod) == 'table' then return mod end
  local chunk, err = loadfile(LrPathUtils.child(_PLUGIN.path, name .. '.lua'))
  if not chunk then error('Cannot load ' .. name .. '.lua: ' .. tostring(err)) end
  return chunk()
end

local json = loadModule('json')
local U = loadModule('RescueUtil')
local Log = loadModule('RescueLog')
local Engine = loadModule('RescueEngine')

local DIALOG_TITLE = 'Rescue: Apply manifest'

local XMP_WARNING = 'If Catalog Settings > Metadata > "Automatically write changes into XMP" is on, '
  .. 'Lightroom will also write these keywords/ratings/titles into XMP sidecars and INTO JPEG, TIFF, '
  .. 'PSD and DNG files. Turn it off first if originals must stay untouched. '
  .. 'Back up the catalog (.lrcat) before a real run: Edit > Undo cannot reliably reverse a run.'

local function logPathFor(manifestPath)
  local dir = LrPathUtils.parent(manifestPath)
  local base = LrPathUtils.removeExtension(LrPathUtils.leafName(manifestPath))
  return LrPathUtils.child(dir, base .. '.apply-log.txt')
end

local function optionsDialog(context, manifestPath)
  local props = LrBinding.makePropertyTable(context)
  props.dryRun = true
  props.doDevelop = true
  props.xmpOff = false
  local f = LrView.osFactory()
  local contents = f:column {
    bind_to_object = props,
    spacing = f:control_spacing(),
    f:static_text { title = 'Manifest:', font = '<system/bold>' },
    f:static_text { title = manifestPath, width_in_chars = 70, height_in_lines = 2 },
    f:separator { fill_horizontal = 1 },
    f:checkbox {
      title = 'Dry run: only report what would change (writes nothing)',
      value = LrView.bind('dryRun'),
    },
    f:checkbox {
      title = 'Develop suggestions: create a "Rescue edit" virtual copy per photo (masters are never edited)',
      value = LrView.bind('doDevelop'),
    },
    f:static_text {
      title = 'Graduated filters in the manifest are not applied in this version; they are listed in the log.',
      width_in_chars = 70, height_in_lines = 2,
    },
    f:separator { fill_horizontal = 1 },
    f:static_text { title = XMP_WARNING, width_in_chars = 70, height_in_lines = 5 },
    f:checkbox {
      title = 'I have turned OFF "Automatically write changes into XMP" and backed up the catalog '
        .. '(required for a real run)',
      value = LrView.bind('xmpOff'),
    },
  }
  local result = LrDialogs.presentModalDialog {
    title = DIALOG_TITLE,
    contents = contents,
    actionVerb = 'Continue',
  }
  if result ~= 'ok' then return nil end
  return { dryRun = props.dryRun == true, doDevelop = props.doDevelop == true, xmpOff = props.xmpOff == true }
end

LrFunctionContext.postAsyncTaskWithContext('RescueApplyManifest', function(context)
  LrDialogs.attachErrorDialogToFunctionContext(context)

  local files = LrDialogs.runOpenPanel {
    title = 'Choose a lightroom-rescue manifest',
    prompt = 'Choose',
    canChooseFiles = true,
    canChooseDirectories = false,
    allowsMultipleSelection = false,
    fileTypes = 'json',
  }
  if not files or not files[1] then return end
  local manifestPath = files[1]

  local options = optionsDialog(context, manifestPath)
  if not options then return end
  -- The SDK cannot read the XMP auto-write setting, so a real run requires
  -- an explicit acknowledgement instead of a warning that is easy to skip.
  if not options.dryRun and not options.xmpOff then
    LrDialogs.message(DIALOG_TITLE, 'A real run needs the confirmation checkbox: turn OFF Catalog Settings > '
      .. 'Metadata > "Automatically write changes into XMP", back up the catalog, then tick the box. '
      .. 'Nothing was written.', 'warning')
    return
  end

  local okRead, text = LrTasks.pcall(LrFileUtils.readFile, manifestPath)
  if not okRead or type(text) ~= 'string' then
    LrDialogs.message(DIALOG_TITLE, 'Could not read the manifest:\n' .. tostring(text), 'critical')
    return
  end
  local okJson, manifest = pcall(json.decode, text)
  text = nil
  if not okJson then
    LrDialogs.message(DIALOG_TITLE, 'The manifest is not valid JSON:\n' .. tostring(manifest), 'critical')
    return
  end

  local logPath = logPathFor(manifestPath)
  local log = Log.open(logPath)
  context:addCleanupHandler(function() log:close() end)
  log:info('==================================================================')
  log:info('Rescue: Apply manifest %s (plugin %s)', manifestPath, 'RescueApply 1.0.0')
  log:info('Options: dryRun=%s develop=%s; user confirmed XMP auto-write OFF and catalog backed up: %s',
    options.dryRun, options.doDevelop, options.xmpOff)

  local progress = LrProgressScope {
    title = options.dryRun and 'Rescue: dry run' or 'Rescue: applying manifest',
    functionContext = context,
  }
  progress:setCancelable(true)

  local okRun, result = LrTasks.pcall(Engine.run, {
    manifest = manifest,
    dryRun = options.dryRun,
    doDevelop = options.doDevelop,
    log = log,
    util = U,
    json = json,
    progress = progress,
    plugin = _PLUGIN,
    confirm = function(planText)
      local answer = LrDialogs.confirm(
        'Apply these changes to the catalog?',
        planText .. '\n\n' .. XMP_WARNING,
        'Apply', 'Cancel')
      return answer == 'ok'
    end,
  })
  progress:done()

  if not okRun then
    log:error('Unexpected error: %s', tostring(result))
    log:close()
    LrDialogs.message(DIALOG_TITLE, 'The run stopped with an error:\n' .. tostring(result)
      .. '\n\nLog: ' .. logPath, 'critical')
    return
  end

  local footer = '\n\nLog: ' .. logPath
  if log.openError then
    footer = '\n\nWARNING: could not write the log file (' .. tostring(log.openError) .. ')'
  elseif log.warnings + log.errors > 0 then
    footer = footer .. '\n(' .. log.warnings .. ' warnings, ' .. log.errors .. ' errors in the log)'
  end
  log:close()
  LrDialogs.message(DIALOG_TITLE, result.text .. footer, result.ok and 'info' or 'warning')
end)
