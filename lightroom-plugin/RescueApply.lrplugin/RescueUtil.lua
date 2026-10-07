--[[----------------------------------------------------------------------------
RescueUtil.lua - string helpers (pure Lua 5.1; uses LrStringUtils when present
so non-ASCII keyword/collection names compare case-insensitively like
Lightroom does).
------------------------------------------------------------------------------]]

local U = {}

local LrStringUtils
do
  local ok, mod = pcall(function() return import 'LrStringUtils' end)
  if ok then LrStringUtils = mod end
end

function U.trim(s)
  if type(s) ~= 'string' then return s end
  return (string.match(s, '^%s*(.-)%s*$'))
end

function U.lower(s)
  if type(s) ~= 'string' then return s end
  if LrStringUtils then
    local ok, r = pcall(LrStringUtils.lower, s)
    if ok and type(r) == 'string' then return r end
  end
  return string.lower(s)
end

function U.isNonEmptyString(v)
  return type(v) == 'string' and U.trim(v) ~= ''
end

function U.isBlank(v)
  return v == nil or (type(v) == 'string' and U.trim(v) == '')
end

-- Split on a plain separator; trims parts and drops empty ones.
function U.split(s, sep)
  local out = {}
  local start = 1
  while true do
    local i = string.find(s, sep, start, true)
    local part = U.trim(i and string.sub(s, start, i - 1) or string.sub(s, start))
    if part ~= '' then out[#out + 1] = part end
    if not i then break end
    start = i + #sep
  end
  return out
end

function U.clamp(v, lo, hi)
  if v < lo then return lo end
  if v > hi then return hi end
  return v
end

-- Keep run ids safe for undo labels and log lines.
function U.sanitizeId(s)
  return (string.gsub(s, '[^%w%-%._ ]', '_'))
end

return U
