--[[----------------------------------------------------------------------------
RescueLog.lua - append-only log file next to the manifest.

LrLogger can only write to Adobe's log folder, so this writes with io.open.
Format strings use %s only; every argument goes through tostring().
------------------------------------------------------------------------------]]

local Log = {}
Log.__index = Log

function Log.open(path)
  local f, err = io.open(path, 'a')
  return setmetatable({ path = path, f = f, openError = err, warnings = 0, errors = 0 }, Log)
end

local function format(fmt, ...)
  local n = select('#', ...)
  if n == 0 then return tostring(fmt) end
  local args = {}
  for i = 1, n do
    args[i] = tostring((select(i, ...)))
  end
  local ok, msg = pcall(string.format, fmt, unpack(args, 1, n))
  if ok then return msg end
  return tostring(fmt) .. ' ' .. table.concat(args, ' ')
end

function Log:write(level, fmt, ...)
  if not self.f then return end
  local line = os.date('%Y-%m-%d %H:%M:%S') .. ' ' .. level .. ' ' .. format(fmt, ...) .. '\n'
  self.f:write(line)
end

function Log:info(fmt, ...) self:write('INFO ', fmt, ...) end

function Log:warn(fmt, ...)
  self.warnings = self.warnings + 1
  self:write('WARN ', fmt, ...)
end

function Log:error(fmt, ...)
  self.errors = self.errors + 1
  self:write('ERROR', fmt, ...)
end

function Log:flush()
  if self.f then self.f:flush() end
end

function Log:close()
  if self.f then
    self.f:flush()
    self.f:close()
    self.f = nil
  end
end

return Log
