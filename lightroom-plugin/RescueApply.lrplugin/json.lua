--[[----------------------------------------------------------------------------
json.lua - small JSON decoder/encoder for Lua 5.1 (Lightroom Classic plugins)

Written for the lightroom-rescue plugin. MIT License.

Copyright (c) 2026 lightroom-rescue contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Behaviour:
  * json.decode(str) -> value. Raises an error string with line/column on bad
    input (wrap in pcall).
  * JSON null decodes to nil for object values (the key is absent). Inside
    arrays it decodes to the sentinel json.null, so later items keep their
    positions (# and ipairs still work; callers must skip json.null).
  * \uXXXX escapes (including surrogate pairs) become UTF-8; an unpaired
    surrogate becomes U+FFFD so the output is always valid UTF-8.
  * json.encode(value) -> string, for logging. Tables with keys 1..n are
    arrays; other tables are objects with keys sorted for stable output.

Pure Lua 5.1: no goto, no bit ops, no utf8 library.
------------------------------------------------------------------------------]]

local json = {}

-- Placeholder for null items inside arrays.
json.null = setmetatable({}, { __tostring = function() return 'null' end })

local byte, sub, find, char = string.byte, string.sub, string.find, string.char
local floor = math.floor

-------------------------------------------------------------------------------
-- decode

local escapeMap = {
  ['"'] = '"', ['\\'] = '\\', ['/'] = '/',
  b = '\b', f = '\f', n = '\n', r = '\r', t = '\t',
}

local function decodeError(str, idx, msg)
  local line, col = 1, 1
  for i = 1, math.min(idx - 1, #str) do
    if byte(str, i) == 10 then
      line = line + 1
      col = 1
    else
      col = col + 1
    end
  end
  error(string.format('JSON decode error at line %d column %d: %s', line, col, msg), 0)
end

local function codepointToUtf8(n)
  if n <= 0x7F then
    return char(n)
  elseif n <= 0x7FF then
    return char(floor(n / 64) + 192, n % 64 + 128)
  elseif n <= 0xFFFF then
    return char(floor(n / 4096) + 224, floor(n % 4096 / 64) + 128, n % 64 + 128)
  elseif n <= 0x10FFFF then
    return char(floor(n / 262144) + 240, floor(n % 262144 / 4096) + 128,
                floor(n % 4096 / 64) + 128, n % 64 + 128)
  end
  return nil
end

local function skipWhitespace(str, idx)
  local i = find(str, '[^ \t\r\n]', idx)
  return i or (#str + 1)
end

local parseValue -- forward declaration

local function parseString(str, i)
  -- str:sub(i, i) == '"'
  local buf = {}
  local j = i + 1
  local runStart = j
  while true do
    local c = byte(str, j)
    if not c then
      decodeError(str, i, 'unterminated string')
    elseif c == 34 then -- closing quote
      buf[#buf + 1] = sub(str, runStart, j - 1)
      return table.concat(buf), j + 1
    elseif c == 92 then -- backslash
      buf[#buf + 1] = sub(str, runStart, j - 1)
      local e = sub(str, j + 1, j + 1)
      if e == 'u' then
        local hex = sub(str, j + 2, j + 5)
        if not find(hex, '^%x%x%x%x$') then
          decodeError(str, j, 'invalid \\u escape')
        end
        local n = tonumber(hex, 16)
        local advance = 6
        if n >= 0xD800 and n <= 0xDBFF then
          local lowHex = string.match(str, '^\\u(%x%x%x%x)', j + 6)
          if lowHex then
            local m = tonumber(lowHex, 16)
            if m >= 0xDC00 and m <= 0xDFFF then
              n = 0x10000 + (n - 0xD800) * 1024 + (m - 0xDC00)
              advance = 12
            end
          end
        end
        if n >= 0xD800 and n <= 0xDFFF then
          n = 0xFFFD -- unpaired surrogate: not encodable as UTF-8
        end
        buf[#buf + 1] = codepointToUtf8(n) or '?'
        j = j + advance
      else
        local r = escapeMap[e]
        if not r then
          decodeError(str, j, 'invalid escape \\' .. e)
        end
        buf[#buf + 1] = r
        j = j + 2
      end
      runStart = j
    elseif c < 32 then
      decodeError(str, j, 'control character in string')
    else
      -- Jump to the next quote, backslash or control character.
      j = find(str, '[%z\1-\31"\\]', j + 1) or (#str + 1)
    end
  end
end

local function parseNumber(str, i)
  local last = (find(str, '[^%d%.eE%+%-]', i) or (#str + 1)) - 1
  local s = sub(str, i, last)
  if not find(s, '^%-?%d+%.?%d*[eE]?[%+%-]?%d*$') then
    decodeError(str, i, 'invalid number "' .. s .. '"')
  end
  local n = tonumber(s)
  if not n then
    decodeError(str, i, 'invalid number "' .. s .. '"')
  end
  return n, last + 1
end

local function parseArray(str, i)
  local out = {}
  local n = 0
  i = skipWhitespace(str, i + 1)
  if sub(str, i, i) == ']' then
    return out, i + 1
  end
  while true do
    local v
    v, i = parseValue(str, i)
    n = n + 1
    if v == nil then v = json.null end
    out[n] = v
    i = skipWhitespace(str, i)
    local c = sub(str, i, i)
    if c == ']' then
      return out, i + 1
    elseif c ~= ',' then
      decodeError(str, i, "expected ',' or ']' in array")
    end
    i = skipWhitespace(str, i + 1)
  end
end

local function parseObject(str, i)
  local out = {}
  i = skipWhitespace(str, i + 1)
  if sub(str, i, i) == '}' then
    return out, i + 1
  end
  while true do
    if sub(str, i, i) ~= '"' then
      decodeError(str, i, 'expected string key in object')
    end
    local key
    key, i = parseString(str, i)
    i = skipWhitespace(str, i)
    if sub(str, i, i) ~= ':' then
      decodeError(str, i, "expected ':' after object key")
    end
    i = skipWhitespace(str, i + 1)
    local v
    v, i = parseValue(str, i)
    out[key] = v
    i = skipWhitespace(str, i)
    local c = sub(str, i, i)
    if c == '}' then
      return out, i + 1
    elseif c ~= ',' then
      decodeError(str, i, "expected ',' or '}' in object")
    end
    i = skipWhitespace(str, i + 1)
  end
end

parseValue = function(str, i)
  i = skipWhitespace(str, i)
  local c = sub(str, i, i)
  if c == '{' then
    return parseObject(str, i)
  elseif c == '[' then
    return parseArray(str, i)
  elseif c == '"' then
    return parseString(str, i)
  elseif c == '-' or find(c, '^%d$') then
    return parseNumber(str, i)
  elseif sub(str, i, i + 3) == 'true' then
    return true, i + 4
  elseif sub(str, i, i + 4) == 'false' then
    return false, i + 5
  elseif sub(str, i, i + 3) == 'null' then
    return nil, i + 4
  elseif c == '' then
    decodeError(str, i, 'unexpected end of input')
  end
  decodeError(str, i, "unexpected character '" .. c .. "'")
end

function json.decode(str)
  if type(str) ~= 'string' then
    error('json.decode expects a string, got ' .. type(str), 0)
  end
  local i = 1
  if sub(str, 1, 3) == '\239\187\191' then -- UTF-8 BOM
    i = 4
  end
  local value, nextIdx = parseValue(str, i)
  nextIdx = skipWhitespace(str, nextIdx)
  if nextIdx <= #str then
    decodeError(str, nextIdx, 'trailing characters after JSON value')
  end
  return value
end

-------------------------------------------------------------------------------
-- encode (used for log lines)

local function escapeString(s)
  s = string.gsub(s, '[%c"\\]', function(c)
    local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
                  ['\b'] = '\\b', ['\f'] = '\\f' }
    return map[c] or string.format('\\u%04x', byte(c))
  end)
  return '"' .. s .. '"'
end

local function isArray(t)
  local n = 0
  for _ in pairs(t) do n = n + 1 end
  for i = 1, n do
    if t[i] == nil then return false end
  end
  return true, n
end

local function encodeValue(v, depth)
  if depth > 50 then
    error('json.encode: nesting too deep', 0)
  end
  local tv = type(v)
  if v == nil or v == json.null then
    return 'null'
  elseif tv == 'boolean' then
    return v and 'true' or 'false'
  elseif tv == 'number' then
    if v ~= v or v == math.huge or v == -math.huge then
      return 'null'
    end
    if v == floor(v) and math.abs(v) < 1e15 then
      return string.format('%d', v)
    end
    return string.format('%.14g', v)
  elseif tv == 'string' then
    return escapeString(v)
  elseif tv == 'table' then
    local arr, n = isArray(v)
    local parts = {}
    if arr and n > 0 then
      for i = 1, n do parts[i] = encodeValue(v[i], depth + 1) end
      return '[' .. table.concat(parts, ',') .. ']'
    end
    local keys = {}
    for k in pairs(v) do keys[#keys + 1] = tostring(k) end
    table.sort(keys)
    for _, k in ipairs(keys) do
      local val = v[k]
      if val == nil then val = v[tonumber(k)] end
      parts[#parts + 1] = escapeString(k) .. ':' .. encodeValue(val, depth + 1)
    end
    return '{' .. table.concat(parts, ',') .. '}'
  end
  return escapeString(tostring(v))
end

function json.encode(v)
  return encodeValue(v, 0)
end

return json
