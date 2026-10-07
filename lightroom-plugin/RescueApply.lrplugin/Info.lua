--[[----------------------------------------------------------------------------
Info.lua - Lightroom Rescue: Apply manifest

Applies a JSON manifest of decisions (keywords, ratings, flags, labels,
title/caption, collections, develop suggestions on a NEW virtual copy) produced
by the lightroom-rescue Python pipeline.

Minimum SDK 6.0: pickStatus/returnExisting (4.0), createVirtualCopies (5.0),
applyDevelopSettings (6.0).
------------------------------------------------------------------------------]]

return {
  LrSdkVersion = 13.0,
  LrSdkMinimumVersion = 6.0,

  LrToolkitIdentifier = 'com.scknurr.lightroomrescue.apply',
  LrPluginName = 'Lightroom Rescue: Apply',
  LrPluginInfoUrl = 'https://github.com/scknurr/lightroom',

  -- No enabledWhen: the command works on the whole catalog, not the selection.
  LrLibraryMenuItems = {
    {
      title = 'Rescue: Apply manifest…',
      file = 'ApplyManifest.lua',
    },
  },

  -- Plugin-private photo fields used to tag our virtual copies and the
  -- masters a run changed (idempotency + audit).
  LrMetadataProvider = 'RescueMetadata.lua',

  VERSION = { major = 1, minor = 0, revision = 0, build = 1 },
}
