--[[----------------------------------------------------------------------------
RunQueue.lua - Library > Plug-in Extras > "Rescue: Run queued jobs now"
Runs every pending job in the queue folder immediately (see RescueQueue.lua).
------------------------------------------------------------------------------]]

local LrDialogs = import 'LrDialogs'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'

LrTasks.startAsyncTask(function()
  local Q = loadfile(LrPathUtils.child(_PLUGIN.path, 'RescueQueue.lua'))()
  local n = Q.runPending()
  LrDialogs.message('Rescue: queued jobs', n .. ' job(s) run. Results are in the queue folder (*.result.txt).', 'info')
end)
