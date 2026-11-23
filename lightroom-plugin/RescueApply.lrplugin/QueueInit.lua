--[[----------------------------------------------------------------------------
QueueInit.lua - LrInitPlugin: watch the job folder (see RescueQueue.lua) while
Lightroom runs. RescueQueue.lua and the engine are re-read before every check,
so an updated plugin takes effect without restarting Lightroom. Does nothing
unless queue-folder.txt names an existing folder.
------------------------------------------------------------------------------]]

local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'

local POLL_SECONDS = 20

LrTasks.startAsyncTask(function()
  local first = true
  while true do
    local chunk = loadfile(LrPathUtils.child(_PLUGIN.path, 'RescueQueue.lua'))
    if chunk then
      local ok, Q = pcall(chunk)
      if ok and type(Q) == 'table' then
        if first and Q.markAlive then Q.markAlive() end
        first = false
        LrTasks.pcall(Q.runPending)
      end
    end
    LrTasks.sleep(POLL_SECONDS)
  end
end, 'RescueQueueWatcher')
