--[[----------------------------------------------------------------------------
RescueQueue.lua - unattended runs from a job folder.

Started by Info.lua's LrInitPlugin when Lightroom loads the plugin, and by the
menu item "Rescue: Run queued jobs now". It watches the folder named in
queue-folder.txt (next to this file) and runs each "*.job.json" in name order:

  { "manifest": "/abs/path/02_tags_01.json",
    "dry_run": false,
    "develop": false,
    "import": false,
    "confirmed_backup_and_xmp_off": true }

A real run (dry_run false) happens only when confirmed_backup_and_xmp_off is
true: the same acknowledgement the dialog asks for, given by whoever wrote the
job. Otherwise the job runs as a dry run. Each job is renamed *.running while
it runs, then *.done (or *.failed), and a *.result.txt is written next to it.
The run log is <manifest>.apply-log.txt, as for interactive runs. A job whose
manifest was already applied is harmless to re-run: every write is idempotent.
------------------------------------------------------------------------------]]

local LrFileUtils = import 'LrFileUtils'
local LrFunctionContext = import 'LrFunctionContext'
local LrPathUtils = import 'LrPathUtils'
local LrProgressScope = import 'LrProgressScope'
local LrTasks = import 'LrTasks'

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

local Q = {}
local POLL_SECONDS = 20
local busy = false

local function queueFolder()
  local cfg = LrPathUtils.child(_PLUGIN.path, 'queue-folder.txt')
  if not LrFileUtils.exists(cfg) then return nil end
  local ok, text = LrTasks.pcall(LrFileUtils.readFile, cfg)
  if not ok or type(text) ~= 'string' then return nil end
  local dir = text:gsub('^%s+', ''):gsub('%s+$', '')
  if dir == '' or LrFileUtils.exists(dir) ~= 'directory' then return nil end
  return dir
end

local function writeText(path, text)
  local f = io.open(path, 'w')
  if f then f:write(text) f:close() end
end

local function logPathFor(manifestPath)
  local dir = LrPathUtils.parent(manifestPath)
  local base = LrPathUtils.removeExtension(LrPathUtils.leafName(manifestPath))
  return LrPathUtils.child(dir, base .. '.apply-log.txt')
end

-- Export job: { "export": { "uuids": [...], "folder": "/abs/dir", "long_edge": 2400, "quality": 0.9 } }
-- Renders each photo with its current Lightroom develop settings to <folder>/<uuid>.jpg (sRGB, location
-- metadata removed). Read-only on the catalog. Already-exported files are skipped.
local function runExport(job, base)
  local LrApplication = import 'LrApplication'
  local LrExportSession = import 'LrExportSession'
  local spec = job.export
  local catalog = LrApplication.activeCatalog()
  local folder = spec.folder
  LrFileUtils.createAllDirectories(folder)
  local photos, missing, skipped = {}, {}, 0
  for _, uuid in ipairs(spec.uuids or {}) do
    if LrFileUtils.exists(LrPathUtils.child(folder, uuid .. '.jpg')) then
      skipped = skipped + 1
    else
      local p = catalog:findPhotoByUuid(uuid)
      if p then photos[#photos + 1] = p else missing[#missing + 1] = uuid end
    end
  end
  local done, failed = 0, {}
  if #photos > 0 then
    local session = LrExportSession {
      photosToExport = photos,
      exportSettings = {
        LR_export_destinationType = 'specificFolder',
        LR_export_destinationPathPrefix = folder,
        LR_export_useSubfolder = false,
        LR_collisionHandling = 'rename',
        LR_format = 'JPEG',
        LR_jpeg_quality = spec.quality or 0.9,
        LR_export_colorSpace = 'sRGB',
        LR_size_doConstrain = true,
        LR_size_resizeType = 'longEdge',
        LR_size_maxHeight = spec.long_edge or 2400,
        LR_size_maxWidth = spec.long_edge or 2400,
        LR_size_doNotEnlarge = true,
        LR_size_units = 'pixels',
        LR_size_resolution = 240,
        LR_outputSharpeningOn = true,
        LR_outputSharpeningMedia = 'screen',
        LR_outputSharpeningLevel = 2,
        LR_metadata_keywordOptions = 'flat',
        LR_removeLocationMetadata = true,
        LR_minimizeEmbeddedMetadata = false,
        LR_reimportExportedPhoto = false,
        LR_renamingTokensOn = true,
        LR_tokens = '{{image_name}}',
      },
    }
    for _, rendition in session:renditions() do
      local ok, pathOrMsg = rendition:waitForRender()
      if ok then
        local dest = LrPathUtils.child(folder, rendition.photo:getRawMetadata('uuid') .. '.jpg')
        if pathOrMsg ~= dest then
          if LrFileUtils.exists(dest) then LrFileUtils.delete(dest) end
          LrFileUtils.move(pathOrMsg, dest)
        end
        done = done + 1
      else
        failed[#failed + 1] = rendition.photo:getRawMetadata('uuid') .. ': ' .. tostring(pathOrMsg)
      end
    end
  end
  local text = string.format('Exported %d, already there %d, not found %d, failed %d\n', done, skipped, #missing, #failed)
  if #missing > 0 then text = text .. 'NOT FOUND: ' .. table.concat(missing, ', ') .. '\n' end
  if #failed > 0 then text = text .. 'FAILED:\n' .. table.concat(failed, '\n') .. '\n' end
  return (#failed == 0 and #missing == 0) and 'done' or 'done-with-warnings', text
end

-- Run one job file. Returns true when it ran (successfully or not).
local function runJob(jobPath)
  local base = jobPath:gsub('%.job%.json$', '')
  local running = base .. '.job.running'
  if not LrFileUtils.move(jobPath, running) then return false end
  local okRead, text = LrTasks.pcall(LrFileUtils.readFile, running)
  local okJson, job = false, nil
  if okRead and type(text) == 'string' then okJson, job = pcall(json.decode, text) end
  if okJson and type(job) == 'table' and type(job.export) == 'table' then
    local okE, status, text = LrTasks.pcall(runExport, job, base)
    if not okE then status, text = 'failed', 'error: ' .. tostring(status) end
    writeText(base .. '.result.txt', status:upper() .. '\n' .. (text or '') .. '\n')
    LrFileUtils.move(running, base .. (status == 'failed' and '.job.failed' or '.job.done'))
    return true
  end
  if not okJson or type(job) ~= 'table' or type(job.manifest) ~= 'string' then
    writeText(base .. '.result.txt', 'FAILED: job file is not valid JSON with a "manifest" path\n')
    LrFileUtils.move(running, base .. '.job.failed')
    return true
  end
  local confirmed = job.confirmed_backup_and_xmp_off == true
  local dryRun = job.dry_run ~= false or not confirmed

  local status, summary = 'failed', ''
  LrFunctionContext.callWithContext('RescueQueueJob', function(context)
    local okM, mtext = LrTasks.pcall(LrFileUtils.readFile, job.manifest)
    if not okM or type(mtext) ~= 'string' then summary = 'cannot read manifest ' .. job.manifest return end
    local okD, manifest = pcall(json.decode, mtext)
    mtext = nil
    if not okD then summary = 'manifest is not valid JSON: ' .. tostring(manifest) return end
    local logPath = logPathFor(job.manifest)
    local log = Log.open(logPath)
    context:addCleanupHandler(function() log:close() end)
    if log.openError and not dryRun then summary = 'cannot write log ' .. logPath return end
    log:info('==================================================================')
    log:info('Rescue: queued job %s -> manifest %s (plugin RescueApply 1.0.0)', running, job.manifest)
    log:info('Options: dryRun=%s develop=%s import=%s; job confirmed XMP auto-write OFF and catalog backed up: %s',
      dryRun, job.develop == true, job['import'] == true, confirmed)
    local progress = LrProgressScope {
      title = (dryRun and 'Rescue: dry run ' or 'Rescue: applying ') .. LrPathUtils.leafName(job.manifest),
      functionContext = context,
    }
    progress:setCancelable(true)
    local okRun, result = LrTasks.pcall(Engine.run, {
      manifest = manifest, dryRun = dryRun, doDevelop = job.develop == true, doImport = job['import'] == true,
      log = log, logPath = logPath, util = U, json = json, progress = progress, plugin = _PLUGIN,
      confirm = function(planText)
        log:info('Plan (auto-confirmed by queued job):\n%s', planText)
        return true
      end,
    })
    progress:done()
    if not okRun then
      log:error('Unexpected error: %s', tostring(result))
      summary = 'error: ' .. tostring(result)
      return
    end
    summary = (result.text or '') .. string.format('\n(%d warnings, %d errors in %s)', log.warnings, log.errors, logPath)
    status = result.ok and 'done' or 'done-with-warnings'
  end)
  writeText(base .. '.result.txt', status:upper() .. (dryRun and ' (dry run)' or '') .. '\n' .. summary .. '\n')
  LrFileUtils.move(running, base .. (status == 'failed' and '.job.failed' or '.job.done'))
  return true
end

-- Run every pending job once, in name order.
function Q.runPending()
  if busy then return 0 end
  local dir = queueFolder()
  if not dir then return 0 end
  busy = true
  local n = 0
  local ok, err = LrTasks.pcall(function()
    local jobs = {}
    for path in LrFileUtils.files(dir) do
      if path:match('%.job%.json$') then jobs[#jobs + 1] = path end
    end
    table.sort(jobs)
    for _, path in ipairs(jobs) do
      if runJob(path) then n = n + 1 end
    end
  end)
  busy = false
  if not ok then writeText(LrPathUtils.child(dir, 'queue-error.txt'), tostring(err) .. '\n') end
  return n
end

-- Record that a watcher is running (QueueInit.lua calls this once per Lightroom session).
function Q.markAlive()
  local dir = queueFolder()
  if dir then writeText(LrPathUtils.child(dir, 'queue-alive.txt'), 'watcher started ' .. os.date('%Y-%m-%d %H:%M:%S') .. '\n') end
end

-- Older standalone watcher, kept for compatibility; QueueInit.lua now owns the loop.
function Q.startWatcher()
  LrTasks.startAsyncTask(function()
    local dir = queueFolder()
    if dir then writeText(LrPathUtils.child(dir, 'queue-alive.txt'), 'watcher started ' .. os.date('%Y-%m-%d %H:%M:%S') .. '\n') end
    while true do
      Q.runPending()
      LrTasks.sleep(POLL_SECONDS)
    end
  end, 'RescueQueueWatcher')
end

return Q
