--[[----------------------------------------------------------------------------
RescueEngine.lua - plan and apply a lightroom-rescue manifest.

Phases
  1. validate   manifest -> normalized entries (bad fields dropped + logged)
  2. resolve    findPhotoByUuid -> findPhotoByPath (cross-checked with image_id)
                -> getPhotoByLocalId (undocumented, feature-detected, only when
                the entry has no uuid)
  3. read       batchGetRawMetadata / batchGetFormattedMetadata in chunks
  4. plan       diff manifest vs. current state; walk existing keyword and
                collection trees read-only. A DRY RUN stops here.
  5. structure  create missing keywords / collection sets / collections, one
                write gate per tree level (objects are usable once a gate returns)
  6. metadata   200 photos per withWriteAccessDo; per-photo LrTasks.pcall so
                one bad photo cannot roll back its chunk; cancel between chunks
  7. develop    per master: select it, createVirtualCopies('Rescue edit')
                outside any gate, verify; then one gate per group applies the
                suggestion to the NEW copies and tags them (rescueRole)

Safety rules enforced here
  * nothing is deleted; no API that touches original files is called
  * applyDevelopSettings is only called on photos verified isVirtualCopy == true
    whose master is the manifest photo; masters' develop settings are never
    written. New copies are tagged rescue-edit-pending in their own gate right
    after creation, then rescue-edit once developed; untagged copies are never
    developed. develop_suggestion.settings are DELTAS on the master's values
    unless settings_mode = "absolute"
  * every conditional write (rating, pick, label, title, caption, collection
    add, new copy) re-reads the field at write time; a failed read means
    "unknown", never "empty"
  * ratings only go up; pick only on unflagged photos; colour label, title
    and caption only when empty
  * photos the user REJECTED get keywords/title/caption only: no rating, pick,
    label, collection or virtual copy
  * graduated filters are NOT applied (no confirmed SDK format); logged instead
------------------------------------------------------------------------------]]

local LrApplication = import 'LrApplication'
local LrApplicationView = import 'LrApplicationView'
local LrTasks = import 'LrTasks'

local E = {}

E.COPY_NAME = 'Rescue edit'
E.COPY_ROLE = 'rescue-edit'            -- created AND developed
E.PENDING_ROLE = 'rescue-edit-pending' -- created + tagged, develop not yet applied
E.ORPHAN_ROLE = 'rescue-orphan'        -- made by mistake (wrong selection); never touched again

local METADATA_CHUNK = 200   -- photos per metadata write gate
local DEVELOP_GROUP = 25     -- copies created, then developed in one gate
local READ_CHUNK = 500       -- photos per batchGet* call
local YIELD_EVERY = 100      -- lookups between yields / cancel checks
local GATE_TIMEOUT = 60      -- seconds to wait for catalog write access
local GATE_ATTEMPTS = 3

local VALID_COLORS = { red = true, yellow = true, green = true, blue = true, purple = true }

-- Global develop keys accepted from the manifest, with clamping ranges.
local DEVELOP_RANGES = {
  Exposure2012 = { -5, 5 },
  Contrast2012 = { -100, 100 },
  Highlights2012 = { -100, 100 },
  Shadows2012 = { -100, 100 },
  Whites2012 = { -100, 100 },
  Blacks2012 = { -100, 100 },
  Clarity2012 = { -100, 100 },
  Texture = { -100, 100 },
  Dehaze = { -100, 100 },
  Vibrance = { -100, 100 },
  Saturation = { -100, 100 },
  PostCropVignetteAmount = { -100, 100 },
  GrainAmount = { 0, 100 },
}

-- Keys that only work on process version 2012 (6.7) or later.
local NEEDS_PV2012 = {
  Exposure2012 = true, Contrast2012 = true, Highlights2012 = true, Shadows2012 = true,
  Whites2012 = true, Blacks2012 = true, Clarity2012 = true, Texture = true, Dehaze = true,
}

-------------------------------------------------------------------------------
-- helpers

local function newCounter()
  return setmetatable({}, { __index = function() return 0 end })
end

local function inc(S, key, n)
  S.c[key] = S.c[key] + (n or 1)
end

local function slice(list, first, last)
  local out = {}
  for i = first, math.min(last, #list) do out[#out + 1] = list[i] end
  return out
end

local function checkCanceled(S)
  if S.canceled then return true end
  if S.progress and S.progress:isCanceled() then
    S.canceled = true
    S.log:warn('Cancelled by user. Work committed before this point stays; nothing after it was written.')
  end
  return S.canceled
end

local function setPhase(S, caption)
  S.log:info('--- %s', caption)
  S.log:flush()
  if S.progress then
    S.progress:setCaption(caption)
    S.progress:setPortionComplete(0, 1)
  end
end

local function tick(S, done, total)
  if S.progress then S.progress:setPortionComplete(done, math.max(total, 1)) end
end

local function photoLabel(pl)
  local e = pl.entry
  return string.format('[id %s] %s', tostring(pl.id), e.path or e.uuid or ('#' .. e.idx))
end

-- One catalog:withWriteAccessDo with timeout + retry. fn must reset any
-- result tables it fills, because a retried gate runs it again.
-- Returns true only when the gate reported 'executed' (= committed).
local function runGate(S, actionName, fn)
  local lastErr
  for attempt = 1, GATE_ATTEMPTS do
    local ok, status = LrTasks.pcall(function()
      return S.catalog:withWriteAccessDo(actionName, fn, { timeout = GATE_TIMEOUT })
    end)
    if ok and status == 'executed' then
      return true
    end
    if ok then
      lastErr = 'write access not granted (status ' .. tostring(status) .. ')'
      S.log:warn('"%s": %s, attempt %s of %s', actionName, lastErr, attempt, GATE_ATTEMPTS)
    else
      lastErr = tostring(status)
      S.log:error('"%s" raised an error, its changes were rolled back (attempt %s): %s',
        actionName, attempt, lastErr)
      -- Only contention errors are worth retrying.
      if not string.find(string.lower(lastErr), 'write access', 1, true) then
        return false, lastErr
      end
    end
    LrTasks.sleep(2)
  end
  return false, lastErr
end

-- batchGetRawMetadata / batchGetFormattedMetadata with a per-photo fallback.
-- nilFill: keys whose nil batch value is re-read per photo (batch support for
-- them is unverified).
-- required: keys that always have a value (pickStatus, isVirtualCopy,
-- colorNameForLabel); nil after the re-read means the read FAILED.
-- Every row gets row.__failed = { [key] = true } for keys that could not be
-- read, so callers can tell "unknown" from "empty".
local function batchRead(S, kind, photos, keys, nilFill, required)
  local method = kind == 'raw' and 'batchGetRawMetadata' or 'batchGetFormattedMetadata'
  local single = kind == 'raw' and 'getRawMetadata' or 'getFormattedMetadata'
  local ok, res = LrTasks.pcall(function() return S.catalog[method](S.catalog, photos, keys) end)
  if not ok or type(res) ~= 'table' then
    local tag = method .. ':' .. table.concat(keys, ',')
    if not S.loggedFallback[tag] then
      S.loggedFallback[tag] = true
      S.log:warn('%s failed (%s); falling back to per-photo %s', method, tostring(res), single)
    end
    res = {}
  end
  local reread = {}
  for _, k in ipairs(nilFill or {}) do reread[k] = true end
  for _, k in ipairs(required or {}) do reread[k] = true end
  local isRequired = {}
  for _, k in ipairs(required or {}) do isRequired[k] = true end
  for _, p in ipairs(photos) do
    local row = res[p]
    local failed = {}
    if type(row) ~= 'table' then
      row = {}
      for _, k in ipairs(keys) do
        local okk, v = LrTasks.pcall(function() return p[single](p, k) end)
        if okk then row[k] = v else failed[k] = true end
      end
    else
      for k in pairs(reread) do
        if row[k] == nil then
          local okk, v = LrTasks.pcall(function() return p[single](p, k) end)
          if okk then row[k] = v else failed[k] = true end
        end
      end
    end
    for k in pairs(isRequired) do
      if row[k] == nil then failed[k] = true end
    end
    row.__failed = failed
    res[p] = row
  end
  return res
end

local function findNamed(S, list, lowerName)
  if type(list) ~= 'table' then return nil end
  for _, obj in ipairs(list) do
    local ok, name = LrTasks.pcall(function() return obj:getName() end)
    if ok and type(name) == 'string' and S.U.lower(name) == lowerName then
      return obj
    end
  end
  return nil
end

local function objKey(obj)
  if obj == nil then return 'root' end
  local id = obj.localIdentifier
  return id and tostring(id) or tostring(obj)
end

-- Cached child lists (invalidated after each structure gate).
local function childrenOf(S, kind, parent)
  local key = kind .. ':' .. objKey(parent)
  local list = S.childrenCache[key]
  if list then return list end
  local ok, res = LrTasks.pcall(function()
    if kind == 'kw' then
      if parent then return parent:getChildren() end
      return S.catalog:getKeywords()
    elseif kind == 'set' then
      if parent then return parent:getChildCollectionSets() end
      return S.catalog:getChildCollectionSets()
    else -- 'col'
      if parent then return parent:getChildCollections() end
      return S.catalog:getChildCollections()
    end
  end)
  list = (ok and type(res) == 'table') and res or {}
  if not ok then S.log:warn('Could not list %s children: %s', kind, tostring(res)) end
  S.childrenCache[key] = list
  return list
end

-- Lower-cased full path ('rescue|subject|dog') of an existing keyword.
local function keywordPathLower(S, kw)
  local id = kw.localIdentifier
  if id and S.kwPathCache[id] then return S.kwPathCache[id] end
  local parts = {}
  local k = kw
  local guard = 0
  while k and guard < 64 do
    local okn, name = LrTasks.pcall(function() return k:getName() end)
    table.insert(parts, 1, S.U.lower(okn and name or '?'))
    local okp, parent = LrTasks.pcall(function() return k:getParent() end)
    k = okp and parent or nil
    guard = guard + 1
  end
  local path = table.concat(parts, '|')
  if id then S.kwPathCache[id] = path end
  return path
end

-------------------------------------------------------------------------------
-- 1. validate

local function normalizeEntry(S, raw, idx, rootSets)
  local U, log = S.U, S.log
  if type(raw) ~= 'table' or raw == S.json.null then
    log:warn('photos[%s]: not an object, skipped', idx)
    return nil
  end
  local e = { idx = idx, keywords = {}, collections = {} }
  if U.isNonEmptyString(raw.uuid) then e.uuid = U.trim(raw.uuid) end
  if raw.image_id ~= nil then e.imageId = tonumber(raw.image_id) end
  if U.isNonEmptyString(raw.path) then e.path = raw.path end
  local where = 'photos[' .. idx .. '] (' .. (e.path or e.uuid or '?') .. ')'
  if not e.uuid and not e.path and not e.imageId then
    log:warn('%s: no uuid, path or image_id, skipped', where)
    return nil
  end

  if raw.keywords ~= nil then
    if type(raw.keywords) == 'table' then
      local seen = {}
      for _, k in ipairs(raw.keywords) do
        if type(k) == 'string' then
          local parts = U.split(k, '|')
          if #parts > 0 then
            local lowers = {}
            for i, p in ipairs(parts) do lowers[i] = U.lower(p) end
            local key = table.concat(lowers, '|')
            if not seen[key] then
              seen[key] = true
              e.keywords[#e.keywords + 1] = {
                parts = parts, lowers = lowers, key = key, display = table.concat(parts, '|'),
              }
            end
          end
        else
          log:warn('%s: non-string keyword ignored', where)
        end
      end
    else
      log:warn('%s: keywords is not an array, ignored', where)
    end
  end

  if raw.rating ~= nil then
    local r = tonumber(raw.rating)
    if r and r >= 1 and r <= 5 then
      e.rating = math.floor(r)
    elseif not (r and r == 0) then
      log:warn('%s: rating %s out of range 1..5, ignored', where, raw.rating)
    end
  end

  if raw.pick == true then
    e.pick = true
  elseif raw.pick ~= nil and raw.pick ~= false then
    log:warn('%s: pick must be true/false, ignored', where)
  end

  if raw.color_label ~= nil then
    local c = type(raw.color_label) == 'string' and U.lower(U.trim(raw.color_label)) or nil
    if c and VALID_COLORS[c] then
      e.color = c
    elseif c ~= 'none' and c ~= '' then
      log:warn('%s: color_label "%s" not one of red/yellow/green/blue/purple, ignored', where, raw.color_label)
    end
  end

  if U.isNonEmptyString(raw.title) then e.title = U.trim(raw.title) end
  if U.isNonEmptyString(raw.caption) then e.caption = U.trim(raw.caption) end

  if raw.collections ~= nil then
    if type(raw.collections) == 'table' then
      local seen = {}
      for _, cpath in ipairs(raw.collections) do
        local parts = type(cpath) == 'string' and U.split(cpath, '/') or {}
        if #parts == 0 then
          log:warn('%s: invalid collection path %s ignored', where, cpath)
        else
          local setNames = {}
          for _, s in ipairs(rootSets) do setNames[#setNames + 1] = s end
          for i = 1, #parts - 1 do setNames[#setNames + 1] = parts[i] end
          local setLowers = {}
          for i, s in ipairs(setNames) do setLowers[i] = U.lower(s) end
          local name = parts[#parts]
          local setKey = table.concat(setLowers, '/')
          local key = setKey .. '//' .. U.lower(name)
          if not seen[key] then
            seen[key] = true
            e.collections[#e.collections + 1] = {
              setNames = setNames, setLowers = setLowers, setKey = setKey,
              name = name, key = key, display = table.concat(setNames, '/') .. '/' .. name,
            }
          end
        end
      end
    else
      log:warn('%s: collections is not an array, ignored', where)
    end
  end

  if raw.develop_suggestion ~= nil then
    if type(raw.develop_suggestion) == 'table' then
      e.develop = raw.develop_suggestion
    else
      log:warn('%s: develop_suggestion is not an object, ignored', where)
    end
  end
  return e
end

local function validate(S, manifest)
  local U = S.U
  if type(manifest) ~= 'table' then return false, 'Manifest is not a JSON object.' end
  if tonumber(manifest.version) ~= 1 then
    return false, 'Unsupported manifest version ' .. tostring(manifest.version) .. ' (this plugin reads version 1).'
  end
  if not U.isNonEmptyString(manifest.run_id) then return false, 'Manifest has no run_id.' end
  if type(manifest.photos) ~= 'table' then return false, 'Manifest has no photos array.' end
  S.runId = U.sanitizeId(U.trim(manifest.run_id))

  local rootName = U.isNonEmptyString(manifest.collection_set) and manifest.collection_set or 'Rescue'
  local rootSets = U.split(rootName, '/')
  if #rootSets == 0 then rootSets = { 'Rescue' } end
  S.rootSets = rootSets

  S.entries = {}
  for i, raw in ipairs(manifest.photos) do
    local e = normalizeEntry(S, raw, i, rootSets)
    if e then S.entries[#S.entries + 1] = e else inc(S, 'invalidEntries') end
  end
  S.log:info('Manifest run_id=%s, %s photo entries (%s invalid), collection set "%s"',
    S.runId, #S.entries, S.c.invalidEntries, table.concat(rootSets, '/'))
  return true
end

-------------------------------------------------------------------------------
-- 2. resolve

local function resolveOne(S, e)
  local catalog = S.catalog
  if e.uuid then
    local ok, p = LrTasks.pcall(function() return catalog:findPhotoByUuid(e.uuid) end)
    if ok and p then
      if e.imageId and p.localIdentifier ~= e.imageId then
        S.log:warn('uuid %s: localIdentifier %s differs from manifest image_id %s (uuid wins)',
          e.uuid, p.localIdentifier, e.imageId)
        inc(S, 'idMismatch')
      end
      return p, 'uuid'
    elseif not ok then
      S.log:warn('findPhotoByUuid(%s) failed: %s', e.uuid, tostring(p))
    end
  end
  if e.path then
    local ok, p = LrTasks.pcall(function() return catalog:findPhotoByPath(e.path) end)
    if ok and p then
      if e.imageId and p.localIdentifier ~= e.imageId then
        return nil, string.format('path matched catalog photo id %s but manifest image_id is %s; '
          .. 'refusing an ambiguous match', tostring(p.localIdentifier), tostring(e.imageId))
      end
      return p, 'path'
    elseif not ok then
      S.log:warn('findPhotoByPath(%s) failed: %s', e.path, tostring(p))
    end
  end
  if e.imageId and not e.uuid and S.hasLocalIdLookup then
    local ok, p = LrTasks.pcall(function() return catalog:getPhotoByLocalId(e.imageId) end)
    if ok and p then
      if e.path then
        local okp, ppath = LrTasks.pcall(function() return p:getRawMetadata('path') end)
        if okp and ppath ~= e.path then
          return nil, 'image_id matched a photo with a different path (' .. tostring(ppath) .. ')'
        end
      end
      return p, 'localId'
    end
  end
  return nil, 'not in catalog'
end

local function resolvePhotos(S)
  setPhase(S, 'Finding photos')
  do
    local ok, f = pcall(function() return S.catalog.getPhotoByLocalId end)
    S.hasLocalIdLookup = ok and type(f) == 'function'
  end
  S.log:info('catalog:getPhotoByLocalId (undocumented) available: %s', S.hasLocalIdLookup)
  local seen = {}
  local total = #S.entries
  for i, e in ipairs(S.entries) do
    if i % YIELD_EVERY == 0 then
      tick(S, i, total)
      LrTasks.yield()
      if checkCanceled(S) then return end
    end
    local photo, how = resolveOne(S, e)
    if photo then
      local id = photo.localIdentifier
      if seen[id] then
        S.log:warn('photos[%s]: same catalog photo (id %s) as an earlier entry; skipped', e.idx, id)
        inc(S, 'duplicates')
      else
        seen[id] = true
        S.plans[#S.plans + 1] = { entry = e, photo = photo, id = id, how = how }
        inc(S, 'foundBy_' .. how)
      end
    else
      S.notFound[#S.notFound + 1] = { entry = e, reason = how }
      S.log:warn('NOT FOUND photos[%s] uuid=%s image_id=%s path=%s: %s',
        e.idx, e.uuid, e.imageId, e.path, how)
    end
  end
  tick(S, total, total)
  if S.c.foundBy_uuid == 0 and S.c.foundBy_path > 0 then
    S.log:warn('No photo was found by uuid but %s were found by path: Adobe_images.id_global '
      .. 'may not equal the SDK uuid in this catalog. Check the Python export.', S.c.foundBy_path)
  end
end

-------------------------------------------------------------------------------
-- 3. read current state

local function readState(S)
  setPhase(S, 'Reading current metadata')
  local photos = {}
  for i, pl in ipairs(S.plans) do photos[i] = pl.photo end
  local total = #photos
  for first = 1, total, READ_CHUNK do
    if checkCanceled(S) then return end
    local chunk = slice(photos, first, first + READ_CHUNK - 1)
    local raw = batchRead(S, 'raw', chunk,
      { 'rating', 'pickStatus', 'colorNameForLabel', 'isVirtualCopy', 'fileFormat' },
      nil, { 'pickStatus', 'colorNameForLabel', 'isVirtualCopy' })
    local kws = batchRead(S, 'raw', chunk, { 'keywords' }, { 'keywords' })
    -- 'label' = the label TEXT. colorNameForLabel is 'none' whenever that text
    -- does not match the active label set (custom text, other set, imported
    -- XMP), so both must be empty before a label counts as empty.
    local fmt = batchRead(S, 'formatted', chunk, { 'title', 'caption', 'label' })
    for j, p in ipairs(chunk) do
      local pl = S.plans[first + j - 1]
      local r = raw[p] or { __failed = { pickStatus = true, rating = true, colorNameForLabel = true, isVirtualCopy = true } }
      local f = fmt[p] or { __failed = { title = true, caption = true, label = true } }
      local kwSet = {}
      local kwRow = kws[p] or {}
      local kwList = kwRow.keywords
      if type(kwList) == 'table' then
        for _, kw in ipairs(kwList) do kwSet[keywordPathLower(S, kw)] = true end
      end
      local failed = {}
      for k in pairs(r.__failed or {}) do failed[k] = true end
      for k in pairs(f.__failed or {}) do failed[k] = true end
      -- Unknown current keywords: still safe to add (addKeyword is idempotent).
      if (kwRow.__failed or {}).keywords then failed.keywords = true end
      pl.cur = {
        rating = tonumber(r.rating),           -- nil = unrated (or unread, see failed)
        pickStatus = tonumber(r.pickStatus),   -- nil only when the read failed
        color = r.colorNameForLabel,
        labelText = f.label,
        isVC = r.isVirtualCopy == true,
        fileFormat = r.fileFormat,
        title = f.title,
        caption = f.caption,
        kwSet = kwSet,
        failed = failed,
      }
      if next(failed) then
        local names = {}
        for k in pairs(failed) do names[#names + 1] = k end
        table.sort(names)
        pl.cur.readFailedKeys = table.concat(names, ',')
      end
    end
    tick(S, math.min(first + READ_CHUNK - 1, total), total)
    LrTasks.yield()
  end
end

-------------------------------------------------------------------------------
-- 4. plan

-- Collect the keyword / collection-set / collection nodes the plan may need
-- and look each up (read-only). cache[key] = object | false (missing).
-- True when only keywords (and verified-empty title/caption) may be applied:
-- the photo is rejected, or its flag / virtual-copy state could not be read.
local function isProtected(cur)
  return cur.pickStatus == nil or cur.pickStatus == -1 or cur.failed.pickStatus or cur.failed.isVirtualCopy
end

-- Yield + cancel check every YIELD_EVERY iterations; true = cancelled.
local function pace(S, i, total)
  if i % YIELD_EVERY == 0 then
    if total then tick(S, i, total) end
    LrTasks.yield()
    return checkCanceled(S)
  end
  return false
end

local function planStructure(S)
  setPhase(S, 'Reading keyword and collection trees')
  local U = S.U
  for i, pl in ipairs(S.plans) do
    if pace(S, i, #S.plans) then return end
    for _, k in ipairs(pl.entry.keywords) do
      for d = 1, #k.parts do
        local key = table.concat(k.lowers, '|', 1, d)
        local info = S.kwInfo[key]
        if not info then
          info = {
            name = k.parts[d], lower = k.lowers[d], depth = d,
            parentKey = d > 1 and table.concat(k.lowers, '|', 1, d - 1) or nil,
            display = table.concat(k.parts, '|', 1, d),
          }
          S.kwInfo[key] = info
          if d > S.kwMaxDepth then S.kwMaxDepth = d end
        end
        if d == #k.parts then info.isLeaf = true end
      end
    end
    if not isProtected(pl.cur) then
      for _, c in ipairs(pl.entry.collections) do
        for d = 1, #c.setNames do
          local key = table.concat(c.setLowers, '/', 1, d)
          if not S.setInfo[key] then
            S.setInfo[key] = {
              name = c.setNames[d], lower = c.setLowers[d], depth = d,
              parentKey = d > 1 and table.concat(c.setLowers, '/', 1, d - 1) or nil,
              display = table.concat(c.setNames, '/', 1, d),
            }
            if d > S.setMaxDepth then S.setMaxDepth = d end
          end
        end
        if not S.colInfo[c.key] then
          S.colInfo[c.key] = { name = c.name, lower = U.lower(c.name), depth = 1,
                               setKey = c.setKey, display = c.display }
        end
      end
    end
  end

  local function byDepth(infos)
    local list = {}
    for key, info in pairs(infos) do list[#list + 1] = { key = key, info = info } end
    table.sort(list, function(a, b)
      if a.info.depth ~= b.info.depth then return a.info.depth < b.info.depth end
      return a.key < b.key
    end)
    return list
  end

  local kwList, setList = byDepth(S.kwInfo), byDepth(S.setInfo)
  setPhase(S, 'Looking up existing keywords and collections')
  for i, item in ipairs(kwList) do
    if pace(S, i, #kwList) then return end
    local info = item.info
    local parent = nil
    local parentOk = true
    if info.parentKey then
      parent = S.kwCache[info.parentKey]
      parentOk = parent and true or false
    end
    S.kwCache[item.key] = parentOk and (findNamed(S, childrenOf(S, 'kw', parent), info.lower) or false) or false
    if not S.kwCache[item.key] then inc(S, 'plan_keywordsToCreate') end
  end
  for i, item in ipairs(setList) do
    if pace(S, i, #setList) then return end
    local info = item.info
    local parent = nil
    local parentOk = true
    if info.parentKey then
      parent = S.setCache[info.parentKey]
      parentOk = parent and true or false
    end
    S.setCache[item.key] = parentOk and (findNamed(S, childrenOf(S, 'set', parent), info.lower) or false) or false
    if not S.setCache[item.key] then inc(S, 'plan_setsToCreate') end
  end
  local nCol = 0
  for key, info in pairs(S.colInfo) do
    nCol = nCol + 1
    if pace(S, nCol) then return end
    local set = S.setCache[info.setKey]
    local col = set and findNamed(S, childrenOf(S, 'col', set), info.lower) or false
    if col then
      local ok, smart = LrTasks.pcall(function() return col:isSmartCollection() end)
      if ok and smart then
        S.colSmart[key] = true
        S.log:warn('Collection "%s" exists as a SMART collection; photos will not be added to it', info.display)
      end
    else
      inc(S, 'plan_collectionsToCreate')
    end
    S.colCache[key] = col
  end
  S.log:info('Structure: %s keyword nodes needed (%s missing), %s collection sets (%s missing), '
    .. '%s collections (%s missing)',
    #kwList, S.c.plan_keywordsToCreate, #setList, S.c.plan_setsToCreate,
    nCol, S.c.plan_collectionsToCreate)
end

local function collectionMembers(S, key)
  local members = S.colMembers[key]
  if members then return members end
  members = {}
  local col = S.colCache[key]
  if col then
    local ok, photos = LrTasks.pcall(function() return col:getPhotos() end)
    if ok and type(photos) == 'table' then
      for _, p in ipairs(photos) do members[p.localIdentifier] = true end
    end
  end
  S.colMembers[key] = members
  return members
end

-- Looks at the master's virtual copies. Returns one table:
--   { done = copy tagged rescue-edit, pending = copy tagged rescue-edit-pending,
--     named = UNTAGGED copy whose name is "Rescue edit", readFailed = bool }
-- Copies tagged rescue-orphan are ignored. Only tagged copies are ever
-- developed: an untagged "Rescue edit" copy may be the user's own, so it is
-- left alone and blocks creating another one (no duplicates either way).
local function findExistingCopies(S, master)
  local out = {}
  local ok, copies = LrTasks.pcall(function() return master:getRawMetadata('virtualCopies') end)
  if not ok or type(copies) ~= 'table' then out.readFailed = true; return out end
  local wanted = S.U.lower(E.COPY_NAME)
  for _, c in ipairs(copies) do
    local okR, role = LrTasks.pcall(function() return c:getPropertyForPlugin(S.plugin, 'rescueRole') end)
    if not okR then
      out.readFailed = true
    elseif role == E.COPY_ROLE then
      out.done = out.done or c
    elseif role == E.PENDING_ROLE then
      out.pending = out.pending or c
    elseif role == nil or role == '' then
      local okN, name = LrTasks.pcall(function() return c:getFormattedMetadata('copyName') end)
      if okN and type(name) == 'string' and S.U.lower(S.U.trim(name)) == wanted then
        out.named = out.named or c
      end
    end
  end
  return out
end

local function planDevelop(S, pl)
  local d, cur = pl.entry.develop, pl.cur
  local label = photoLabel(pl)
  local function skip(reason)
    pl.develop = { action = 'skip', reason = reason }
    inc(S, 'plan_developSkipped')
    S.log:info('%s: develop suggestion skipped: %s', label, reason)
  end

  if type(d.graduated_filters) == 'table' and #d.graduated_filters > 0 then
    inc(S, 'plan_gradientsSkipped', #d.graduated_filters)
    S.log:info('%s: %s graduated filter(s) NOT applied (no confirmed SDK format for masks): %s',
      label, #d.graduated_filters, S.json.encode(d.graduated_filters))
  end
  if cur.isVC then
    return skip('the manifest photo is itself a virtual copy; Rescue copies are only made from masters')
  end
  if cur.fileFormat == 'VIDEO' then return skip('video') end

  local existing = findExistingCopies(S, pl.photo)
  if existing.done then
    inc(S, 'plan_copiesAlreadyDone')
    pl.develop = { action = 'skip', reason = 'exists' }
    S.log:info('%s: "%s" copy already exists (id %s); not touched', label, E.COPY_NAME, existing.done.localIdentifier)
    return
  end
  if existing.readFailed then
    return skip('could not read the existing virtual copies or their tags; not creating one (avoids duplicates)')
  end
  if existing.named and not existing.pending then
    return skip(string.format('an UNTAGGED virtual copy named "%s" already exists (id %s); it may be your own, '
      .. 'so it is not edited and no second copy is made', E.COPY_NAME, tostring(existing.named.localIdentifier)))
  end

  local okDs, ds = LrTasks.pcall(function() return pl.photo:getDevelopSettings() end)
  if not okDs or type(ds) ~= 'table' then
    return skip('could not read the master\'s develop settings (needed to apply the suggestion as a delta)')
  end
  local pvNum = tonumber(ds.ProcessVersion)
  local orientation = ds.Orientation or ds.orientation
  if orientation ~= nil then inc(S, 'diag_orientationKnown') end
  if pvNum == nil then inc(S, 'diag_pvUnknown') end

  -- Manifest settings are DELTAS on top of the master's current values
  -- (develop_suggestion.settings_mode = "absolute" switches to targets). For
  -- an unedited photo the two are identical, since every accepted key
  -- defaults to 0. The copy starts as a clone of the master, so a delta keeps
  -- the user's own edit and nudges it.
  local mode = d.settings_mode == 'absolute' and 'absolute' or 'delta'
  if d.settings_mode ~= nil and d.settings_mode ~= 'absolute' and d.settings_mode ~= 'delta' then
    S.log:warn('%s: unknown settings_mode "%s"; using delta', label, tostring(d.settings_mode))
  end
  local settings, notes, nSettings, before = {}, {}, 0, {}
  if type(d.settings) == 'table' then
    local keys = {}
    for k in pairs(d.settings) do keys[#keys + 1] = tostring(k) end
    table.sort(keys)
    for _, k in ipairs(keys) do
      local v = d.settings[k]
      local range = DEVELOP_RANGES[k]
      if not range then
        notes[#notes + 1] = 'unknown key ' .. k .. ' dropped'
      elseif type(v) ~= 'number' then
        notes[#notes + 1] = k .. ' is not a number, dropped'
      elseif NEEDS_PV2012[k] and pvNum and pvNum < 6.7 then
        notes[#notes + 1] = k .. ' dropped: process version ' .. tostring(ds.ProcessVersion) .. ' is older than 2012'
      else
        local old = tonumber(ds[k]) or 0
        local target = mode == 'delta' and (old + v) or v
        local cv = S.U.clamp(target, range[1], range[2])
        if cv ~= target then notes[#notes + 1] = k .. ' clamped to ' .. cv end
        settings[k] = cv
        before[k] = old
        nSettings = nSettings + 1
      end
    end
  end

  local masterCropped = (tonumber(ds.CropLeft) or 0) > 0.0001 or (tonumber(ds.CropTop) or 0) > 0.0001
    or (tonumber(ds.CropRight) or 1) < 0.9999 or (tonumber(ds.CropBottom) or 1) < 0.9999
    or math.abs(tonumber(ds.CropAngle) or 0) > 0.0001

  if type(d.crop) == 'table' then
    local c = d.crop
    local l, r, t, b = tonumber(c.left), tonumber(c.right), tonumber(c.top), tonumber(c.bottom)
    local a = tonumber(c.angle) or 0
    if not (l and r and t and b) or l < 0 or r > 1 or t < 0 or b > 1 or r - l < 0.05 or b - t < 0.05 then
      notes[#notes + 1] = 'crop invalid, skipped'
      inc(S, 'plan_cropsSkipped')
    elseif masterCropped then
      -- The suggestion was computed on the already-cropped preview, so its
      -- coordinates are not relative to the full frame; keep the user's crop.
      notes[#notes + 1] = 'crop skipped: the master is already cropped'
      inc(S, 'plan_cropsSkipped')
    elseif orientation ~= 'AB' then
      -- Crop edges are in sensor orientation; conversion for rotated photos is
      -- unverified, so only unrotated ('AB') photos get the crop in v1.
      notes[#notes + 1] = 'crop skipped: orientation ' .. tostring(orientation) .. ' (only unrotated AB supported)'
      inc(S, 'plan_cropsSkipped')
    else
      settings.CropLeft, settings.CropRight, settings.CropTop, settings.CropBottom = l, r, t, b
      settings.CropAngle = S.U.clamp(a, -45, 45)
      settings.CropConstrainAspectRatio = false
      nSettings = nSettings + 1
      inc(S, 'plan_crops')
    end
  end

  if nSettings == 0 then
    return skip('nothing applicable' .. (#notes > 0 and (' (' .. table.concat(notes, '; ') .. ')') or ''))
  end
  if #notes > 0 then S.log:info('%s: develop notes: %s', label, table.concat(notes, '; ')) end

  if existing.pending then
    pl.develop = { action = 'complete', copy = existing.pending, settings = settings, before = before, mode = mode }
    inc(S, 'plan_copiesToComplete')
    S.log:info('%s: "%s" copy (id %s) tagged pending by an interrupted run will be developed',
      label, E.COPY_NAME, existing.pending.localIdentifier)
  else
    pl.develop = { action = 'create', settings = settings, before = before, mode = mode }
    inc(S, 'plan_copiesToCreate')
  end
end

local function isEmptyLabel(c)
  return c == nil or c == '' or (type(c) == 'string' and string.lower(c) == 'none')
end

-- Empty only when the colour name is none AND there is no label text.
local function labelIsEmpty(S, color, labelText)
  return isEmptyLabel(color) and S.U.isBlank(labelText)
end

local function planPhoto(S, pl)
  local e, cur, U = pl.entry, pl.cur, S.U
  pl.addKeywords, pl.addCollections = {}, {}
  local label = photoLabel(pl)

  for _, k in ipairs(e.keywords) do
    if not cur.kwSet[k.key] then
      pl.addKeywords[#pl.addKeywords + 1] = k
      inc(S, 'plan_keywordAssignments')
    end
  end
  local failed = cur.failed
  if cur.readFailedKeys then
    inc(S, 'plan_readFailed')
    S.log:warn('%s: current metadata could not be read (%s); those fields are left alone', label, cur.readFailedKeys)
  end
  if e.title and not failed.title and U.isBlank(cur.title) then pl.title = e.title; inc(S, 'plan_titles') end
  if e.caption and not failed.caption and U.isBlank(cur.caption) then pl.caption = e.caption; inc(S, 'plan_captions') end

  if isProtected(cur) then
    if e.rating or e.pick or e.color or #e.collections > 0 or e.develop then
      if cur.pickStatus == -1 then
        inc(S, 'plan_rejectedProtected')
        S.log:info('%s: REJECTED in Lightroom; rating/pick/label/collections/develop not applied', label)
      else
        S.log:warn('%s: flag or virtual-copy state unknown; only keywords/title/caption applied', label)
      end
    end
    return
  end

  if e.rating and not failed.rating and e.rating > (cur.rating or 0) then
    pl.rating = e.rating
    inc(S, 'plan_ratingsRaised')
  end
  if e.pick and cur.pickStatus == 0 then
    pl.pick = true
    inc(S, 'plan_picks')
  end
  if e.color and not failed.colorNameForLabel and not failed.label then
    if labelIsEmpty(S, cur.color, cur.labelText) then
      pl.color = e.color
      inc(S, 'plan_labels')
    elseif U.lower(tostring(cur.color)) ~= e.color then
      inc(S, 'plan_labelsKept')
    end
  end
  for _, c in ipairs(e.collections) do
    if S.colSmart[c.key] then
      -- logged once in planStructure
    elseif not collectionMembers(S, c.key)[pl.id] then
      pl.addCollections[#pl.addCollections + 1] = c
      inc(S, 'plan_collectionAdds')
    end
  end
  if e.develop and S.doDevelop then planDevelop(S, pl) end
end

local function planAll(S)
  setPhase(S, 'Planning changes')
  local total = #S.plans
  for i, pl in ipairs(S.plans) do
    planPhoto(S, pl)
    if i % YIELD_EVERY == 0 then
      tick(S, i, total)
      LrTasks.yield()
      if checkCanceled(S) then return end
    end
  end
  tick(S, total, total)
end

local function hasMetadataWork(pl)
  return #pl.addKeywords > 0 or #pl.addCollections > 0 or pl.rating or pl.pick or pl.color
    or pl.title or pl.caption
end

-------------------------------------------------------------------------------
-- 5. structure

-- Create the missing nodes of one tree, one write gate per depth level.
local function createMissing(S, spec)
  local maxDepth = spec.maxDepth
  for depth = 1, maxDepth do
    local todo = {}
    for key, info in pairs(spec.infos) do
      if info.depth == depth and spec.cache[key] == false then
        local parent, parentOk = spec.parentOf(info)
        if parentOk then
          todo[#todo + 1] = { key = key, info = info, parent = parent }
        else
          spec.cache[key] = nil
          inc(S, spec.what .. 'Failed')
          S.log:error('%s "%s" not created: its parent could not be created', spec.what, info.display)
        end
      end
    end
    if #todo > 0 then
      if checkCanceled(S) then return end
      local created, errs
      local ok, gateErr = runGate(S, string.format('Rescue %s: create %ss (level %d)', S.runId, spec.what, depth),
        function()
          created, errs = {}, {}
          for _, t in ipairs(todo) do
            local okc, obj = LrTasks.pcall(spec.create, t.info, t.parent)
            if okc and obj then created[t.key] = obj else errs[t.key] = tostring(obj) end
          end
        end)
      S.childrenCache = {}
      for _, t in ipairs(todo) do
        local obj = ok and created and created[t.key] or nil
        if not obj then
          -- returnExisting/canReturnPrior may hand back false/nil; look it up.
          obj = findNamed(S, childrenOf(S, spec.kind, t.parent), t.info.lower)
        end
        if obj then
          spec.cache[t.key] = obj
          inc(S, spec.what .. 'Created')
          S.log:info('Created %s "%s"', spec.what, t.info.display)
        else
          spec.cache[t.key] = nil
          inc(S, spec.what .. 'Failed')
          S.log:error('Could not create %s "%s": %s', spec.what, t.info.display,
            (errs and errs[t.key]) or gateErr or 'unknown error')
        end
      end
      S.log:flush()
    end
  end
end

local function applyStructure(S)
  setPhase(S, 'Creating keywords and collections')
  local catalog = S.catalog
  createMissing(S, {
    what = 'keyword', kind = 'kw', infos = S.kwInfo, cache = S.kwCache, maxDepth = S.kwMaxDepth,
    parentOf = function(info)
      if not info.parentKey then return nil, true end
      local p = S.kwCache[info.parentKey]
      return p, p and true or false
    end,
    -- Leaf keywords export; the 'Rescue' / 'Subject' grouping levels do not.
    create = function(info, parent)
      return catalog:createKeyword(info.name, {}, info.isLeaf == true, parent, true)
    end,
  })
  if checkCanceled(S) then return end
  createMissing(S, {
    what = 'collection set', kind = 'set', infos = S.setInfo, cache = S.setCache, maxDepth = S.setMaxDepth,
    parentOf = function(info)
      if not info.parentKey then return nil, true end
      local p = S.setCache[info.parentKey]
      return p, p and true or false
    end,
    create = function(info, parent)
      return catalog:createCollectionSet(info.name, parent, true)
    end,
  })
  if checkCanceled(S) then return end
  createMissing(S, {
    what = 'collection', kind = 'col', infos = S.colInfo, cache = S.colCache, maxDepth = 1,
    parentOf = function(info)
      local p = S.setCache[info.setKey]
      return p, p and true or false
    end,
    create = function(info, parent)
      return catalog:createCollection(info.name, parent, true)
    end,
  })
end

-------------------------------------------------------------------------------
-- 6. metadata

-- Runs INSIDE a write gate. Never raises: each write is LrTasks.pcall'ed.
local function writePhotoMetadata(S, pl)
  local res = { pl = pl, changes = {}, errors = {}, counts = {} }
  local photo = pl.photo
  local function try(counterKey, desc, f)
    local ok, err = LrTasks.pcall(f)
    if ok then
      res.changes[#res.changes + 1] = desc
      res.counts[counterKey] = (res.counts[counterKey] or 0) + 1
    else
      res.errors[#res.errors + 1] = desc .. ': ' .. tostring(err)
    end
  end
  -- The plan was read before the confirmation dialog, possibly hours ago, and
  -- the UI (or sync / other plug-ins) may have changed the photo since. Every
  -- conditional write re-reads its field right here, inside the gate, and is
  -- skipped when the rule no longer holds or the fresh read fails.
  local function fresh(kind, key)
    local ok, v = LrTasks.pcall(function()
      if kind == 'raw' then return photo:getRawMetadata(key) end
      return photo:getFormattedMetadata(key)
    end)
    return ok, v
  end
  local function skipped(desc, why)
    res.skips[#res.skips + 1] = desc .. ' skipped: ' .. why
  end
  res.skips = {}
  pl.collectionsBlocked = nil  -- reset: a retried gate runs this again

  for _, k in ipairs(pl.addKeywords) do
    local kw = S.kwCache[k.key]
    if kw then
      try('keywordsAssigned', '+kw ' .. k.display, function() photo:addKeyword(kw) end)
    else
      res.errors[#res.errors + 1] = '+kw ' .. k.display .. ': keyword could not be created'
    end
  end

  local needFlag = pl.rating or pl.pick or pl.color or #pl.addCollections > 0
  local flagOk, flagNow = true, nil
  if needFlag then
    flagOk, flagNow = fresh('raw', 'pickStatus')
    flagNow = tonumber(flagNow)
    if not flagOk or flagNow == nil then
      skipped('rating/pick/label/collections', 'flag state could not be re-read')
      pl.collectionsBlocked = true
    elseif flagNow == -1 then
      skipped('rating/pick/label/collections', 'photo was REJECTED since planning')
      pl.collectionsBlocked = true
    end
  end
  local flagUsable = flagOk and flagNow ~= nil and flagNow ~= -1

  if pl.rating and flagUsable then
    local ok, now = fresh('raw', 'rating')
    if not ok then
      skipped('rating', 'could not re-read the current rating')
    elseif (tonumber(now) or 0) < pl.rating then
      try('ratingsRaised', 'rating ' .. tostring(tonumber(now) or 0) .. '->' .. pl.rating,
        function() photo:setRawMetadata('rating', pl.rating) end)
    else
      skipped('rating ' .. pl.rating, 'rating is now ' .. tostring(now))
    end
  end
  if pl.pick and flagUsable then
    if flagNow == 0 then
      try('picksSet', 'flag unflagged->pick', function() photo:setRawMetadata('pickStatus', 1) end)
    else
      skipped('pick', 'photo is now flagged ' .. tostring(flagNow))
    end
  end
  if pl.color and flagUsable then
    local okC, nowC = fresh('raw', 'colorNameForLabel')
    local okL, nowL = fresh('formatted', 'label')
    if not (okC and okL) then
      skipped('label ' .. pl.color, 'could not re-read the current label')
    elseif labelIsEmpty(S, nowC, nowL) then
      try('labelsSet', 'label none->' .. pl.color, function() photo:setRawMetadata('colorNameForLabel', pl.color) end)
    else
      skipped('label ' .. pl.color, 'photo now has label "' .. tostring(nowL or nowC) .. '"')
    end
  end
  if pl.title then
    local ok, now = fresh('formatted', 'title')
    if not ok then
      skipped('title', 'could not re-read the current title')
    elseif S.U.isBlank(now) then
      try('titlesFilled', 'title (was empty)', function() photo:setRawMetadata('title', pl.title) end)
    else
      skipped('title', 'title was filled in since planning')
    end
  end
  if pl.caption then
    local ok, now = fresh('formatted', 'caption')
    if not ok then
      skipped('caption', 'could not re-read the current caption')
    elseif S.U.isBlank(now) then
      try('captionsFilled', 'caption (was empty)', function() photo:setRawMetadata('caption', pl.caption) end)
    else
      skipped('caption', 'caption was filled in since planning')
    end
  end
  if #res.changes > 0 or (#pl.addCollections > 0 and not pl.collectionsBlocked) then
    LrTasks.pcall(function() photo:setPropertyForPlugin(S.plugin, 'rescueLastRun', S.runId) end)
  end
  return res
end

local function applyMetadata(S)
  local todo = {}
  for _, pl in ipairs(S.plans) do
    if hasMetadataWork(pl) then todo[#todo + 1] = pl end
  end
  setPhase(S, string.format('Writing metadata (%d photos)', #todo))
  local nChunks = math.ceil(#todo / METADATA_CHUNK)
  for ci = 1, nChunks do
    if checkCanceled(S) then return end
    local chunk = slice(todo, (ci - 1) * METADATA_CHUNK + 1, ci * METADATA_CHUNK)
    local results, colResults
    local ok, err = runGate(S, string.format('Rescue %s: metadata %d/%d', S.runId, ci, nChunks), function()
      results, colResults = {}, {}
      for _, pl in ipairs(chunk) do
        results[#results + 1] = writePhotoMetadata(S, pl)
      end
      local groups, order = {}, {}
      for _, pl in ipairs(chunk) do
        for _, c in ipairs(pl.collectionsBlocked and {} or pl.addCollections) do
          local col = S.colCache[c.key]
          if col then
            local g = groups[c.key]
            if not g then
              g = { col = col, photos = {}, ids = {}, display = c.display }
              groups[c.key] = g
              order[#order + 1] = c.key
            end
            g.photos[#g.photos + 1] = pl.photo
            g.ids[#g.ids + 1] = pl.id
          end
        end
      end
      for _, key in ipairs(order) do
        local g = groups[key]
        local okA, errA = LrTasks.pcall(function() g.col:addPhotos(g.photos) end)
        colResults[#colResults + 1] = { display = g.display, n = #g.photos, ids = g.ids, ok = okA, err = errA }
      end
    end)
    if ok then
      for _, res in ipairs(results) do
        for k, n in pairs(res.counts) do inc(S, k, n) end
        local pl = res.pl
        if #res.changes > 0 then
          S.log:info('CHANGED %s uuid=%s: %s', photoLabel(pl), pl.entry.uuid, table.concat(res.changes, '; '))
        end
        for _, msg in ipairs(res.skips or {}) do
          inc(S, 'skippedChangedSincePlan')
          S.log:info('SKIP %s: %s', photoLabel(pl), msg)
        end
        for _, c in ipairs(pl.collectionsBlocked and {} or pl.addCollections) do
          if not S.colCache[c.key] then
            S.log:warn('%s: collection "%s" unavailable, not added', photoLabel(pl), c.display)
          end
        end
        for _, msg in ipairs(res.errors) do
          inc(S, 'photoErrors')
          S.log:error('%s: %s', photoLabel(pl), msg)
        end
      end
      for _, cr in ipairs(colResults) do
        if cr.ok then
          inc(S, 'collectionAdds', cr.n)
          S.log:info('COLLECTION "%s" +%s photos (ids %s)', cr.display, cr.n, table.concat(cr.ids, ','))
        else
          inc(S, 'photoErrors', cr.n)
          S.log:error('Adding %s photos to "%s" failed: %s', cr.n, cr.display, tostring(cr.err))
        end
      end
    else
      inc(S, 'chunksFailed')
      S.log:error('Metadata chunk %s/%s (%s photos) NOT written: %s', ci, nChunks, #chunk, tostring(err))
    end
    S.log:flush()
    tick(S, ci, nChunks)
    LrTasks.yield()
  end
end

-------------------------------------------------------------------------------
-- 7. develop (virtual copies)

local function saveUi(S)
  local ui = {}
  LrTasks.pcall(function() ui.module = LrApplicationView.getCurrentModuleName() end)
  LrTasks.pcall(function() ui.sources = S.catalog:getActiveSources() end)
  LrTasks.pcall(function()
    ui.active = S.catalog:getTargetPhoto()
    if ui.active then ui.selected = S.catalog:getTargetPhotos() end
  end)
  return ui
end

local function restoreUi(S, ui)
  if ui.sourcesChanged and ui.sources then
    local ok, err = LrTasks.pcall(function() S.catalog:setActiveSources(ui.sources) end)
    if not ok then S.log:warn('Could not restore active sources: %s', tostring(err)) end
  end
  if ui.active then
    local ok, err = LrTasks.pcall(function() S.catalog:setSelectedPhotos(ui.active, ui.selected or {}) end)
    if not ok then S.log:warn('Could not restore selection: %s', tostring(err)) end
  end
  if ui.module and ui.module ~= 'library' then
    LrTasks.pcall(function() LrApplicationView.switchToModule(ui.module) end)
  end
end

local function prepareLibraryGrid(S)
  LrTasks.pcall(function() LrApplicationView.switchToModule('library') end)
  local ok = LrTasks.pcall(function()
    if type(LrApplicationView.gridView) == 'function' then
      LrApplicationView.gridView()
    else
      LrApplicationView.showView('grid')
    end
  end)
  if not ok then S.log:warn('Could not switch to Library grid view; continuing') end
end

local function selectOnly(S, photo)
  LrTasks.pcall(function() S.catalog:setSelectedPhotos(photo, {}) end)
  for _ = 1, 10 do
    local ok, target, all = LrTasks.pcall(function()
      return S.catalog:getTargetPhoto(), S.catalog:getTargetPhotos()
    end)
    if ok and target and target.localIdentifier == photo.localIdentifier
        and type(all) == 'table' and #all == 1 then
      return true
    end
    LrTasks.sleep(0.05)
  end
  return false
end

-- Returns copy | nil, errorMessage, fatal
local function createCopyFor(S, master)
  local selected = selectOnly(S, master)
  if not selected and not S.ui.sourcesChanged then
    -- The master may be outside the current source: show All Photographs once.
    local ok = LrTasks.pcall(function() S.catalog:setActiveSources(S.catalog.kAllPhotos) end)
    if ok then
      S.ui.sourcesChanged = true
      S.log:info('Switched the active source to All Photographs to select masters')
      selected = selectOnly(S, master)
    end
  end
  if not selected then
    return nil, 'could not select the master alone (hidden by a filter or a collapsed stack?)', false
  end
  -- The master may have been rejected while the run was going.
  local okF, flagNow = LrTasks.pcall(function() return master:getRawMetadata('pickStatus') end)
  if not okF or tonumber(flagNow) == nil or tonumber(flagNow) == -1 then
    return nil, 'master is now REJECTED or its flag could not be re-read; no copy made', false
  end
  -- Last check immediately before creating: the user may have clicked another
  -- photo since selectOnly confirmed the selection.
  local okS, target, all = LrTasks.pcall(function()
    return S.catalog:getTargetPhoto(), S.catalog:getTargetPhotos()
  end)
  if not (okS and target and target.localIdentifier == master.localIdentifier
      and type(all) == 'table' and #all == 1) then
    return nil, 'selection changed just before creating the copy; no copy made', false
  end
  local ok, copies = LrTasks.pcall(function() return S.catalog:createVirtualCopies(E.COPY_NAME) end)
  if not ok then return nil, 'createVirtualCopies failed: ' .. tostring(copies), false end
  if type(copies) ~= 'table' or #copies == 0 then
    return nil, 'createVirtualCopies returned no copy', false
  end

  -- Tags every copy in `list` with `role` in one small gate; returns ok, err.
  local function tagCopies(list, role, what)
    return runGate(S, string.format('Rescue %s: tag %s', S.runId, what), function()
      for _, c in ipairs(list) do
        c:setPropertyForPlugin(S.plugin, 'rescueRole', role)
        c:setPropertyForPlugin(S.plugin, 'rescueRunId', S.runId)
      end
    end)
  end
  local function orphan(list, why)
    local ids = {}
    for _, c in ipairs(list) do
      ids[#ids + 1] = tostring(c.localIdentifier)
      S.orphans[#S.orphans + 1] = c.localIdentifier
    end
    local okT, errT = tagCopies(list, E.ORPHAN_ROLE, 'orphan copies')
    return nil, string.format('%s (copy ids %s, %s); stopping the develop phase. Find them via the '
      .. '"Rescue role" = %s filter and remove them by hand.', why, table.concat(ids, ','),
      okT and ('tagged ' .. E.ORPHAN_ROLE) or ('NOT tagged: ' .. tostring(errT)), E.ORPHAN_ROLE), true
  end

  if #copies > 1 then
    return orphan(copies, 'createVirtualCopies made ' .. #copies .. ' copies instead of one')
  end
  local copy = copies[1]
  local okV, isVC, masterOf = LrTasks.pcall(function()
    return copy:getRawMetadata('isVirtualCopy'), copy:getRawMetadata('masterPhoto')
  end)
  if not okV or isVC ~= true or not masterOf or masterOf.localIdentifier ~= master.localIdentifier then
    return orphan(copies, 'new photo is not a virtual copy of the intended master')
  end
  -- Tag BEFORE any develop write, so a later failure (develop gate, crash,
  -- quit) leaves a copy the next run finds and completes instead of
  -- creating another one. This does not rely on copyName.
  local okT, errT = tagCopies({ copy }, E.PENDING_ROLE, 'new copy')
  if not okT then
    S.orphans[#S.orphans + 1] = copy.localIdentifier
    return nil, string.format('copy id %s was created but could not be tagged (%s); stopping the develop '
      .. 'phase so re-runs cannot duplicate it. It is an untagged "%s" copy: re-runs will skip this photo.',
      tostring(copy.localIdentifier), tostring(errT), E.COPY_NAME), true
  end
  return copy
end

local function applyDevelop(S)
  local todo = {}
  for _, pl in ipairs(S.plans) do
    if pl.develop and (pl.develop.action == 'create' or pl.develop.action == 'complete') then
      todo[#todo + 1] = pl
    end
  end
  if #todo == 0 then return end
  setPhase(S, string.format('Creating "%s" virtual copies (%d)', E.COPY_NAME, #todo))
  S.ui = saveUi(S)
  prepareLibraryGrid(S)
  local nGroups = math.ceil(#todo / DEVELOP_GROUP)
  local stop = false
  for gi = 1, nGroups do
    if stop or checkCanceled(S) then break end
    local group = slice(todo, (gi - 1) * DEVELOP_GROUP + 1, gi * DEVELOP_GROUP)
    local ready = {}
    for _, pl in ipairs(group) do
      if checkCanceled(S) then break end
      local copy, err, fatal
      if pl.develop.action == 'complete' then
        local okF, flagNow = LrTasks.pcall(function() return pl.photo:getRawMetadata('pickStatus') end)
        if okF and tonumber(flagNow) ~= nil and tonumber(flagNow) ~= -1 then
          copy = pl.develop.copy
        else
          err = 'master is now REJECTED or its flag could not be re-read; pending copy id '
            .. tostring(pl.develop.copy.localIdentifier) .. ' left as is'
        end
      else
        copy, err, fatal = createCopyFor(S, pl.photo)
        if copy then
          inc(S, 'copiesCreated')
          S.log:info('COPY created for %s: copy id %s', photoLabel(pl), copy.localIdentifier)
        end
      end
      if copy then
        -- Final guard before any develop write: must be a virtual copy.
        local okV, isVC = LrTasks.pcall(function() return copy:getRawMetadata('isVirtualCopy') end)
        if okV and isVC == true then
          ready[#ready + 1] = { pl = pl, copy = copy }
        else
          inc(S, 'copyFailures')
          S.log:error('%s: photo id %s is not a virtual copy; develop NOT applied', photoLabel(pl), copy.localIdentifier)
        end
      else
        inc(S, 'copyFailures')
        S.log:error('%s: %s', photoLabel(pl), err)
        if fatal then stop = true; break end
      end
    end

    if #ready > 0 then
      local results
      local ok, gateErr = runGate(S, string.format('Rescue %s: develop copies %d/%d', S.runId, gi, nGroups),
        function()
          results = {}
          for _, r in ipairs(ready) do
            local okA, errA = LrTasks.pcall(function()
              r.copy:applyDevelopSettings(r.pl.develop.settings, 'Rescue edit (' .. S.runId .. ')')
            end)
            local okT, errT = true, nil
            if okA then
              -- pending -> done only after a successful develop write; a
              -- copy still tagged pending is completed by the next run.
              okT, errT = LrTasks.pcall(function()
                r.copy:setPropertyForPlugin(S.plugin, 'rescueRole', E.COPY_ROLE)
                r.copy:setPropertyForPlugin(S.plugin, 'rescueRunId', S.runId)
              end)
            end
            results[#results + 1] = { r = r, okA = okA, errA = errA, okT = okT, errT = errT }
          end
        end)
      if ok then
        for _, res in ipairs(results) do
          local pl, copy = res.r.pl, res.r.copy
          if res.okA then
            inc(S, 'copiesDeveloped')
            S.log:info('DEVELOP %s -> copy id %s (%s; master value -> copy value): %s', photoLabel(pl),
              copy.localIdentifier, pl.develop.mode or 'delta',
              S.json.encode({ master = pl.develop.before or {}, copy = pl.develop.settings }))
            if not res.okT then
              S.log:warn('%s: copy id %s developed but still tagged %s (%s); the next run re-applies the '
                .. 'same values to it', photoLabel(pl), copy.localIdentifier, E.PENDING_ROLE, tostring(res.errT))
            end
          else
            inc(S, 'copyFailures')
            S.log:error('%s: applyDevelopSettings on copy id %s failed: %s (copy stays tagged %s; '
              .. 'the next run will retry it)', photoLabel(pl), copy.localIdentifier, tostring(res.errA), E.PENDING_ROLE)
          end
        end
      else
        inc(S, 'chunksFailed')
        S.log:error('Develop group %s/%s NOT written (%s); %s copies exist unedited, tagged %s; '
          .. 'the next run will complete them', gi, nGroups, tostring(gateErr), #ready, E.PENDING_ROLE)
      end
    end
    S.log:flush()
    tick(S, gi, nGroups)
    LrTasks.yield()
  end
  restoreUi(S, S.ui)
end

-------------------------------------------------------------------------------
-- summary

local function summaryText(S, mode)
  local c = S.c
  local L = {}
  local function add(fmt, ...) L[#L + 1] = string.format(fmt, ...) end
  local found = c.foundBy_uuid + c.foundBy_path + c.foundBy_localId
  add('Run %s: %d manifest entries (%d invalid).', S.runId, #S.entries + c.invalidEntries, c.invalidEntries)
  add('Found %d photos (uuid %d, path %d, local id %d). Not found: %d. Duplicates skipped: %d.',
    found, c.foundBy_uuid, c.foundBy_path, c.foundBy_localId, #S.notFound, c.duplicates)
  if mode == 'plan' then
    add('')
    add('Would change:')
    add('  Keywords to create: %d; keyword assignments: %d', c.plan_keywordsToCreate, c.plan_keywordAssignments)
    add('  Ratings raised: %d; picks set: %d; colour labels: %d (kept existing: %d)',
      c.plan_ratingsRaised, c.plan_picks, c.plan_labels, c.plan_labelsKept)
    add('  Titles filled: %d; captions filled: %d', c.plan_titles, c.plan_captions)
    add('  Collection sets to create: %d; collections to create: %d; collection adds: %d',
      c.plan_setsToCreate, c.plan_collectionsToCreate, c.plan_collectionAdds)
    if S.doDevelop then
      add('  "%s" copies to create: %d; to complete: %d; already done: %d; skipped: %d',
        E.COPY_NAME, c.plan_copiesToCreate, c.plan_copiesToComplete, c.plan_copiesAlreadyDone, c.plan_developSkipped)
      add('  Crops: %d; crops skipped: %d; graduated filters NOT applied: %d',
        c.plan_crops, c.plan_cropsSkipped, c.plan_gradientsSkipped)
    else
      add('  Develop suggestions: off')
    end
    add('  Rejected photos protected: %d; photos whose current metadata could not be fully read: %d',
      c.plan_rejectedProtected, c.plan_readFailed)
  else
    add('')
    add('Written:')
    add('  Keywords created: %d; assigned: %d', c.keywordCreated, c.keywordsAssigned)
    add('  Ratings raised: %d; picks set: %d; colour labels: %d', c.ratingsRaised, c.picksSet, c.labelsSet)
    add('  Titles filled: %d; captions filled: %d', c.titlesFilled, c.captionsFilled)
    add('  Collection sets created: %d; collections created: %d; collection adds: %d',
      c['collection setCreated'], c.collectionCreated, c.collectionAdds)
    if S.doDevelop then
      add('  "%s" copies created: %d; developed: %d; failures: %d',
        E.COPY_NAME, c.copiesCreated, c.copiesDeveloped, c.copyFailures)
      add('  Graduated filters NOT applied (logged): %d', c.plan_gradientsSkipped)
    end
    if c.skippedChangedSincePlan > 0 then
      add('  Writes skipped because the photo changed since planning: %d (see SKIP lines in the log)',
        c.skippedChangedSincePlan)
    end
    if #S.orphans > 0 then
      local ids = {}
      for i = 1, math.min(20, #S.orphans) do ids[i] = tostring(S.orphans[i]) end
      add('  STRAY virtual copies to check and remove by hand (%d): ids %s%s', #S.orphans,
        table.concat(ids, ', '), #S.orphans > 20 and ', ...' or '')
    end
    if c.chunksFailed > 0 or c.photoErrors > 0 then
      add('  FAILED write batches: %d; per-photo errors: %d (see log)', c.chunksFailed, c.photoErrors)
    end
  end
  if S.canceled then
    add('')
    add('CANCELLED before the run finished. Re-running the same manifest continues safely.')
  end
  if #S.notFound > 0 then
    add('')
    add('Not found (first %d of %d):', math.min(8, #S.notFound), #S.notFound)
    for i = 1, math.min(8, #S.notFound) do
      local e = S.notFound[i].entry
      add('  %s', tostring(e.path or e.uuid or e.imageId))
    end
  end
  return table.concat(L, '\n')
end

local function plannedChangeCount(S)
  local c = S.c
  return c.plan_keywordAssignments + c.plan_ratingsRaised + c.plan_picks + c.plan_labels
    + c.plan_titles + c.plan_captions + c.plan_collectionAdds + c.plan_copiesToCreate + c.plan_copiesToComplete
end

-------------------------------------------------------------------------------
-- entry point
--
-- opts: manifest (decoded table), dryRun, doDevelop, log, util, json,
--       progress (LrProgressScope), plugin (_PLUGIN),
--       confirm(planText) -> boolean   (called only for real runs)
-- Returns { ok = bool, text = summary, dryRun = bool }

function E.run(opts)
  local S = {
    U = opts.util, json = opts.json, log = opts.log, progress = opts.progress, plugin = opts.plugin,
    catalog = LrApplication.activeCatalog(), dryRun = opts.dryRun, doDevelop = opts.doDevelop,
    c = newCounter(), plans = {}, notFound = {}, loggedFallback = {},
    childrenCache = {}, kwPathCache = {},
    kwInfo = {}, kwCache = {}, kwMaxDepth = 0,
    setInfo = {}, setCache = {}, setMaxDepth = 0,
    colInfo = {}, colCache = {}, colSmart = {}, colMembers = {},
    orphans = {},
  }

  local okV, err = validate(S, opts.manifest)
  if not okV then
    S.log:error('%s', err)
    return { ok = false, text = err }
  end
  S.log:info('Mode: %s; develop suggestions: %s', S.dryRun and 'DRY RUN' or 'APPLY', S.doDevelop and 'on' or 'off')

  resolvePhotos(S)
  if not S.canceled then readState(S) end
  if not S.canceled then planStructure(S) end
  if not S.canceled then planAll(S) end

  local planText = summaryText(S, 'plan')
  S.log:info('PLAN\n%s', planText)
  S.log:flush()

  if S.canceled then
    return { ok = false, text = 'Cancelled while planning. Nothing was written.\n\n' .. planText }
  end
  if S.dryRun then
    return { ok = true, dryRun = true, text = 'DRY RUN - nothing was written.\n\n' .. planText }
  end
  if plannedChangeCount(S) == 0 then
    S.log:info('Nothing to change.')
    return { ok = true, text = 'Nothing to change: the catalog already matches this manifest.\n\n' .. planText }
  end
  if not opts.confirm(planText) then
    S.log:info('User declined at the confirmation prompt. Nothing was written.')
    return { ok = false, text = 'Cancelled. Nothing was written.' }
  end

  applyStructure(S)
  if not S.canceled then applyMetadata(S) end
  if not S.canceled and S.doDevelop then
    local okD, errD = LrTasks.pcall(applyDevelop, S)
    if not okD then
      S.log:error('Develop phase aborted: %s', tostring(errD))
      if S.ui then LrTasks.pcall(restoreUi, S, S.ui) end
    end
  end

  local text = summaryText(S, 'done')
  S.log:info('RESULT\n%s', text)
  S.log:flush()
  return { ok = not S.canceled and S.c.chunksFailed == 0, text = text }
end

return E
