--[[----------------------------------------------------------------------------
QueueInit.lua - LrInitPlugin: start the job-folder watcher (see RescueQueue.lua)
when Lightroom loads the plugin. Does nothing unless queue-folder.txt names an
existing folder.
------------------------------------------------------------------------------]]

local LrPathUtils = import 'LrPathUtils'

local chunk = loadfile(LrPathUtils.child(_PLUGIN.path, 'RescueQueue.lua'))
if chunk then
  local ok, Q = pcall(chunk)
  if ok and type(Q) == 'table' then Q.startWatcher() end
end
